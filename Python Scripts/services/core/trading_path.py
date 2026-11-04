"""The source files that can change a trading decision, found by following imports.

Research evidence (cohorts, gate proofs) is bound to these files' contents. A deploy that only
touches the dashboard, scrapers, reports or scripts leaves the hash unchanged.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
# The dev-mode import roots in pyproject.toml, in sys.path order.
IMPORT_ROOTS = ('.', 'Data', 'Python Scripts', 'Python Scripts/Webscraping', 'Python Scripts/agentCallers')
# Decisions, orders and exits, plus the collector stages whose output the gates read.
ENTRY_MODULES = (
    'decision_pipeline',
    'services.trading.trade_executor',
    'services.options.position_manager',
    'services.discovery.catalyst_classification',
    'services.discovery.catalyst_response_collector',
)
# Non-Python inputs read at run time; imports cannot find them.
DATA_FILES = (
    'config/risk_clusters.csv', 'config/source.json', 'Data/sector_etfs.json', 'Data/catalyst_schema.sql',
    'Data/market_db_modules/pricing_schema.sql', 'Data/market_db_modules/trade_economics_schema.sql',
    'Data/market_db_modules/thesis_event_reviews_schema.sql', 'Data/market_db_modules/research_schema.sql',
    'Data/market_db_modules/forward_evaluation_schema.sql',
)


class _Resolver:
    """Finds a module's file under the import roots; each name is probed once.

    Most imported names are stdlib or third-party. Checking the first name part against each root's
    listing first skips their file probes, which dominate the cost on slow filesystems.
    """

    def __init__(self, roots):
        self.roots = roots
        self.top_level = [{entry.stem if entry.suffix == '.py' else entry.name for entry in root.iterdir()}
                          if root.is_dir() else set() for root in roots]
        self.found = {}

    def __call__(self, name):
        if name not in self.found:
            self.found[name] = self._probe(name.split('.'))
        return self.found[name]

    def _probe(self, parts):
        for root, names in zip(self.roots, self.top_level):
            if parts[0] not in names:
                continue
            base = root.joinpath(*parts)
            for candidate in (base.with_suffix('.py'), base / '__init__.py'):
                if candidate.is_file():
                    return candidate
        return None


def _module_name(path, roots):
    # A file is named by its most specific root, as the importer that found it would name it.
    for root in sorted(roots, key=lambda r: len(r.parts), reverse=True):
        if path.is_relative_to(root):
            parts = list(path.relative_to(root).with_suffix('').parts)
            is_package = parts[-1] == '__init__'
            return '.'.join(parts[:-1] if is_package else parts), is_package
    raise ValueError(f'{path} is outside the import roots')


def _imported_names(path, roots):
    """Every module an import statement in the file could name, including imports inside functions."""
    module, is_package = _module_name(path, roots)
    package = module if is_package else module.rpartition('.')[0]
    package_parts = package.split('.') if package else []
    names = set()
    for node in ast.walk(ast.parse(path.read_bytes())):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # One dot is the file's own package; each further dot goes up one level.
                base = package_parts[:len(package_parts) - node.level + 1]
                prefix = '.'.join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ''
            # `from package import name` may name a submodule or an attribute; try both.
            targets = [prefix] + [f'{prefix}.{alias.name}' for alias in node.names]
        else:
            continue
        for target in filter(None, targets):
            parts = target.split('.')
            names.update('.'.join(parts[:i]) for i in range(1, len(parts) + 1))
    return names


def import_closure(entries, *, root=ROOT):
    """Repo files reachable from the entry modules; third-party and stdlib imports are ignored."""
    roots = tuple(root / name for name in IMPORT_ROOTS)
    resolve = _Resolver(roots)
    seen, pending = set(), [resolve(entry) for entry in entries]
    if None in pending:
        raise ValueError(f'Unknown entry module among {entries}')
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        pending.extend(found for name in _imported_names(path, roots)
                       if (found := resolve(name)) and found not in seen)
    return seen


@lru_cache(maxsize=1)
def trading_path_files():
    """Relative paths, computed once per process; a running process's code cannot change."""
    files = {str(path.relative_to(ROOT)) for path in import_closure(ENTRY_MODULES)}
    return tuple(sorted(files | set(DATA_FILES)))


def trading_path_hashes():
    return {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in trading_path_files()}
