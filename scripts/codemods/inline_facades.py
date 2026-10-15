#!/usr/bin/env python3
"""Point imports of the persistence facades at the modules that own each name.

Usage: python3 scripts/codemods/inline_facades.py <dir-or-file>...

The facades are `market_db`, `news_db`, `db`, `db_helpers` and `db_helpers.{common,market,news,queries}`,
plus `api_support.context` for the four names it re-exports from `db_helpers`. Each imported name is
followed through the facades' own `from X import name [as alias]` statements until X is not a facade;
a name that does not resolve stops the run. Run this before deleting the facades: it parses them.

Every level-0 `from <facade> import ...`, at any indentation, becomes one `from <owner> import ...` per
owner, keeping the caller's local names (`initialize_market_database` stays bound as an alias). A
statement that shares a line with other code, or whose span holds a comment other than `# noqa...`,
stops the run; the noqa comment is copied to each new statement. Facade files and the directories the
persistence-layering guard skips are not rewritten. Line endings are kept. Prints each changed file.
"""
from __future__ import annotations

import ast
import io
from pathlib import Path
import sys
import tokenize


ROOT_DIR = Path(__file__).resolve().parents[2]
FACADE_PATHS = {
    "market_db": "Data/market_db.py",
    "news_db": "Data/news_db.py",
    "db": "Data/db.py",
    "db_helpers": "Data/db_helpers/__init__.py",
    "db_helpers.common": "Data/db_helpers/common.py",
    "db_helpers.market": "Data/db_helpers/market.py",
    "db_helpers.news": "Data/db_helpers/news.py",
    "db_helpers.queries": "Data/db_helpers/queries.py",
    "api_support.context": "api_support/context.py",
}
# api_support.context is a facade only for these names; everything else it defines stays its own.
CONTEXT_FACADE_NAMES = {"DB_PATH", "get_connection", "initialize_market_database", "list_option_trade_executions"}
SKIPPED_DIRS = {"graft", ".git", "__pycache__", ".venv", "venv", "env", ".superpowers"}
MAX_LINE = 100


class CodemodError(Exception):
    pass


def _absolute_module(facade: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module
    package = facade if FACADE_PATHS[facade].endswith("__init__.py") else facade.rpartition(".")[0]
    for _ in range(node.level - 1):
        package = package.rpartition(".")[0]
    return f"{package}.{node.module}" if node.module else package


def _bindings(facade: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Local name -> (module, source name) for the facade's top-level imports, and its star imports."""
    tree = ast.parse((ROOT_DIR / FACADE_PATHS[facade]).read_bytes())
    names, stars = {}, []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module != "__future__":
            module = _absolute_module(facade, node)
            for alias in node.names:
                if alias.name == "*":
                    stars.append(module)
                else:
                    names[alias.asname or alias.name] = (module, alias.name)
    return names, stars


BINDINGS = {facade: _bindings(facade) for facade in FACADE_PATHS}


def _is_facade(module: str, name: str) -> bool:
    if module == "api_support.context":
        return name in CONTEXT_FACADE_NAMES
    return module in FACADE_PATHS


def resolve(module: str, name: str) -> tuple[str, str] | None:
    """(owner module, name there) for `from module import name`, or None if a facade lacks it."""
    if not _is_facade(module, name):
        return module, name
    names, stars = BINDINGS[module]
    if name in names:
        return resolve(*names[name])
    for star in stars:
        if (owner := resolve(star, name)) is not None:
            return owner
    return None


def _comments(text: str) -> list[tuple[int, str]]:
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    return [(token.start[0], token.string) for token in tokens if token.type == tokenize.COMMENT]


def _render(indent: str, owner: str, items: list[str], noqa: str) -> list[str]:
    comment = f"  {noqa}" if noqa else ""
    line = f"{indent}from {owner} import {', '.join(items)}"
    if len(line + comment) <= MAX_LINE:
        return [line + comment]
    return [f"{indent}from {owner} import ({comment}", *(f"{indent}    {item}," for item in items), f"{indent})"]


def _replacement(path: Path, lines: list[str], comments: list[tuple[int, str]], node: ast.ImportFrom) -> list[str]:
    where = f"{path}:{node.lineno}"
    first, last = lines[node.lineno - 1], lines[node.end_lineno - 1]
    before = first.encode("utf-8")[:node.col_offset].decode("utf-8")
    after = last.encode("utf-8")[node.end_col_offset:].decode("utf-8").strip()
    if before.strip() or (after and not after.startswith("#")):
        raise CodemodError(f"{where}: the import shares a line with other code")
    span_comments = {comment for lineno, comment in comments if node.lineno <= lineno <= node.end_lineno}
    if any(not comment.startswith("# noqa") for comment in span_comments) or len(span_comments) > 1:
        raise CodemodError(f"{where}: the import's span holds a comment other than one # noqa")
    noqa = next(iter(span_comments), "")

    groups: dict[str, list[str]] = {}
    bound: dict[str, tuple[str, str]] = {}
    for alias in node.names:
        if alias.name == "*":
            raise CodemodError(f"{where}: star import of facade {node.module}")
        owner = resolve(node.module, alias.name)
        if owner is None:
            raise CodemodError(f"{where}: {node.module}.{alias.name} does not resolve to an owner")
        local = alias.asname or alias.name
        if bound.setdefault(local, owner) != owner:
            raise CodemodError(f"{where}: {bound[local]} and {owner} would both bind {local}")
        groups.setdefault(owner[0], []).append(owner[1] if owner[1] == local else f"{owner[1]} as {local}")

    newline = last[len(last.rstrip("\r\n")):] or "\n"
    return [line + newline for owner, items in groups.items() for line in _render(before, owner, items, noqa)]


def rewrite(path: Path) -> str | None:
    """The file's new text, or None when it imports no facade."""
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    tree = ast.parse(text)
    targets = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in FACADE_PATHS
        and any(_is_facade(node.module, alias.name) for alias in node.names)
    ]
    if not targets:
        return None
    lines = text.splitlines(keepends=True)
    comments = _comments(text)
    for node in sorted(targets, key=lambda node: node.lineno, reverse=True):
        lines[node.lineno - 1:node.end_lineno] = _replacement(path, lines, comments, node)
    return "".join(lines)


def _python_files(arguments: list[str]) -> list[Path]:
    facade_files = {(ROOT_DIR / relative).resolve() for relative in FACADE_PATHS.values()}
    files = []
    for argument in arguments:
        target = Path(argument).resolve()
        for path in sorted(target.rglob("*.py")) if target.is_dir() else [target]:
            parts = path.relative_to(ROOT_DIR).parts
            if SKIPPED_DIRS & set(parts) or parts[:2] == ("scripts", "codemods") or path in facade_files:
                continue
            files.append(path)
    return files


def main(arguments: list[str]) -> int:
    if not arguments:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    try:
        # Rewrite everything before writing anything, so a refusal leaves the tree untouched.
        changed = {path: text for path in _python_files(arguments) if (text := rewrite(path)) is not None}
    except CodemodError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path, text in changed.items():
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        print(path.relative_to(ROOT_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
