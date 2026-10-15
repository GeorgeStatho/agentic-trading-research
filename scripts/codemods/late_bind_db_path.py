#!/usr/bin/env python3
"""Late-bind `db_path` defaults so they follow db_common's current database.

Usage: python3 scripts/codemods/late_bind_db_path.py <dir-or-file>...

- function and class-body field defaults of `DB_PATH` become `None`, and their `Path | str`
  or `Path` annotation becomes optional;
- `get_connection(DB_PATH)` becomes `get_connection()` and `db_path=DB_PATH` keyword
  arguments in calls are dropped;
- `DB_PATH` is dropped from `from db_common import ...` once nothing else uses it.

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


def _drop_from_list(source: _Source, items: list[ast.AST], index: int) -> tuple[int, int]:
    """Span that removes items[index] and one adjacent comma from a comma-separated list."""
    if index > 0:
        return source.end(items[index - 1]), source.end(items[index])
    return source.start(items[index]), source.start(items[index + 1])


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
            for index, item in enumerate(items):
                drop = (isinstance(item, ast.keyword) and item.arg == "db_path" and _is_db_path(item.value)) or (
                    func_name == "get_connection" and len(items) == 1 and _is_db_path(item))
                if not drop:
                    continue
                removed_names.add(id(getattr(item, "value", item)))
                if len(items) == 1:
                    edits.append((source.end(node.func), source.end(node), "()"))
                else:
                    edits.append((*_drop_from_list(source, items, index), ""))

    still_used = any(
        (_is_db_path(node) and id(node) not in removed_names)
        or (isinstance(node, ast.Constant) and node.value == "DB_PATH")
        for node in ast.walk(tree)
    )
    if not still_used:
        for statement in tree.body:
            if not (isinstance(statement, ast.ImportFrom) and statement.module == "db_common"):
                continue
            names = statement.names
            for index, alias in enumerate(names):
                if alias.name != "DB_PATH" or alias.asname:
                    continue
                if len(names) == 1:
                    start = source.line_starts[statement.lineno - 1]
                    end = source.line_starts[statement.end_lineno] if statement.end_lineno < len(source.line_starts) else len(text)
                    edits.append((start, end, ""))
                else:
                    edits.append((*_drop_from_list(source, names, index), ""))

    for start, end, replacement in sorted(edits, reverse=True):
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
