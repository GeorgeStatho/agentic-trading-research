#!/usr/bin/env python3
"""Late-bind `db_path` defaults so they follow db_common's current database.

Usage: python3 scripts/codemods/late_bind_db_path.py <dir-or-file>...

- function and class-body field defaults of `DB_PATH` become `None`, and their `Path | str`
  or `Path` annotation becomes optional;
- `get_connection(DB_PATH)` becomes `get_connection()` and `db_path=DB_PATH` keyword
  arguments in calls are dropped;
- `DB_PATH` is dropped from any `from db_common import ...`, top-level or function-local,
  once nothing else in its scope (the enclosing function, else the file) uses it.

`db_common.DB_PATH` is treated like `DB_PATH` throughout.

Edits are spliced into the original text bottom-up from `ast` positions, so formatting,
comments and line endings are kept. Prints each changed file.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


OPTIONAL_ANNOTATIONS = {"Path | str": "Path | str | None", "Path": "Path | None"}


def _is_db_path(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "DB_PATH" and isinstance(node.value, ast.Name) and node.value.id == "db_common"
    return isinstance(node, ast.Name) and node.id == "DB_PATH"


class _Source:
    def __init__(self, text: str):
        self.text = text
        self.line_starts = [0]
        for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n)", text):
            self.line_starts.append(self.line_starts[-1] + len(line))

    def offset(self, lineno: int, col: int) -> int:
        # ast columns are UTF-8 byte offsets.
        start = self.line_starts[lineno - 1]
        line = self.text[start:start + col * 4]
        return start + len(line.encode("utf-8")[:col].decode("utf-8"))

    def start(self, node: ast.AST) -> int:
        return self.offset(node.lineno, node.col_offset)

    def end(self, node: ast.AST) -> int:
        return self.offset(node.end_lineno, node.end_col_offset)


def _drop_from_list(source: _Source, items: list[ast.AST], dropped: set[int]) -> list[tuple[int, int]]:
    """Spans that remove items[i] for i in dropped, with one adjacent comma per run of them.

    Consecutive drops are merged into one span so the edits never overlap.
    """
    spans = []
    index = 0
    while index < len(items):
        if index not in dropped:
            index += 1
            continue
        last = index
        while last + 1 in dropped:
            last += 1
        if index > 0:
            spans.append((source.end(items[index - 1]), source.end(items[last])))
        else:
            spans.append((source.start(items[index]), source.start(items[last + 1])))
        index = last + 1
    return spans


def _transform(text: str) -> str:
    tree = ast.parse(text)
    source = _Source(text)
    edits: list[tuple[int, int, str]] = []
    removed_names: set[int] = set()

    def to_none(value: ast.AST, annotation: ast.AST | None) -> None:
        edits.append((source.start(value), source.end(value), "None"))
        removed_names.add(id(value))
        if annotation is not None:
            replacement = OPTIONAL_ANNOTATIONS.get(ast.get_source_segment(text, annotation))
            if replacement:
                edits.append((source.start(annotation), source.end(annotation), replacement))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            positional = args.posonlyargs + args.args
            for arg, default in zip(positional[len(positional) - len(args.defaults):], args.defaults):
                if _is_db_path(default):
                    to_none(default, arg.annotation)
            for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                if _is_db_path(default):
                    to_none(default, arg.annotation)
        elif isinstance(node, ast.ClassDef):
            for statement in node.body:
                if isinstance(statement, (ast.Assign, ast.AnnAssign)) and _is_db_path(statement.value):
                    to_none(statement.value, getattr(statement, "annotation", None))
        elif isinstance(node, ast.Call):
            items = sorted(node.args + node.keywords, key=lambda item: (item.lineno, item.col_offset))
            func_name = getattr(node.func, "id", getattr(node.func, "attr", None))
            dropped = {
                index for index, item in enumerate(items)
                if (isinstance(item, ast.keyword) and item.arg == "db_path" and _is_db_path(item.value))
                or (func_name == "get_connection" and len(items) == 1 and _is_db_path(item))
            }
            for index in dropped:
                removed_names.add(id(getattr(items[index], "value", items[index])))
            if dropped and len(dropped) == len(items):
                edits.append((source.end(node.func), source.end(node), "()"))
            elif dropped:
                edits += [(*span, "") for span in _drop_from_list(source, items, dropped)]

    def still_used(scope: ast.AST) -> bool:
        return any(
            (isinstance(node, ast.Name) and node.id == "DB_PATH" and id(node) not in removed_names)
            or (isinstance(node, ast.Constant) and node.value == "DB_PATH")
            for node in ast.walk(scope)
        )

    # A function-local import only needs its enclosing function; any other import is
    # checked against the whole file.
    def visit_imports(scope: ast.AST, node: ast.AST) -> None:
        for field in ("body", "orelse", "finalbody", "handlers", "cases"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for statement in block:
                if isinstance(statement, ast.ImportFrom) and statement.module == "db_common":
                    drop_import(scope, statement, block)
                inner = statement if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
                visit_imports(inner, statement)

    def drop_import(scope: ast.AST, statement: ast.ImportFrom, block: list[ast.stmt]) -> None:
        names = statement.names
        dropped = {index for index, alias in enumerate(names) if alias.name == "DB_PATH" and not alias.asname}
        if not dropped or still_used(scope):
            return
        if len(dropped) < len(names):
            edits.extend((*span, "") for span in _drop_from_list(source, names, dropped))
        elif len(block) == 1:
            edits.append((source.start(statement), source.end(statement), "pass"))
        else:
            start = source.line_starts[statement.lineno - 1]
            end = source.line_starts[statement.end_lineno] if statement.end_lineno < len(source.line_starts) else len(text)
            edits.append((start, end, ""))

    visit_imports(tree, tree)

    edits.sort(reverse=True)
    for (start, end, _), (next_start, _, _) in zip(edits[1:], edits):
        if end > next_start:
            raise ValueError(f"overlapping edits at offsets {start}-{end} and {next_start}")
    for start, end, replacement in edits:
        text = text[:start] + replacement + text[end:]
    return text


def _python_files(targets: list[str]):
    for target in map(Path, targets):
        if target.is_dir():
            yield from (path for path in sorted(target.rglob("*.py")) if "__pycache__" not in path.parts)
        else:
            yield target


def main(targets: list[str]) -> int:
    if not targets:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    for path in _python_files(targets):
        with open(path, encoding="utf-8", newline="") as handle:
            original = handle.read()
        updated = _transform(original)
        if updated != original:
            ast.parse(updated)
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
