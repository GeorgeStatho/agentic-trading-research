"""Every environment variable the code reads is listed in .env.example.

tests/__init__.py clears exactly the keys .env.example lists before any test runs, so a name
missing from it leaks the operator's shell value into the suite.
"""
import ast
from pathlib import Path
import re
import unittest

from tests import ROOT_DIR, _project_configuration_keys, clear_project_configuration


SOURCE_ROOTS = ("Python Scripts", "Data", "api_support", "api_routes", "scripts")
SOURCE_FILES = ("api.py", "portfolio_history_service.py")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
# Functions that take a variable name (or a tuple of names) and read it from the environment.
ENV_READERS = {
    "getenv",
    "env_flag", "env_float", "env_percentage", "env_positive_int", "env_non_negative_int",
    "env_optional_positive_int",
    "_integer", "_decimal", "_optional_integer", "_optional_temperature", "_price_ceiling", "_first",
    "flag", "text", "positive_int", "positive_float", "qualities", "integer", "number", "mode",
    "_requested_mode", "_env_str", "_get_positive_int_env", "_positive_setting", "_first_setting",
    "_first_configured_env",
}
ENVIRONMENT_MAPPINGS = {"environ", "env"}
# Set by the script itself from its own command-line flag, never by the operator.
NOT_OPERATOR_SETTINGS = {"CHECK_EXTRACTORS_HTML_DIR", "RUN_E2E", "RUN_LIVE_MARKET"}


def _source_paths():
    for root in SOURCE_ROOTS:
        yield from (path for path in (ROOT_DIR / root).rglob("*.py") if "__pycache__" not in path.parts)
    yield from (ROOT_DIR / name for name in SOURCE_FILES)


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _mapping_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _string_literals(nodes):
    """Whole names passed directly or inside a tuple/list; a concatenated suffix is not a name."""
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and ENV_NAME.match(node.value):
            yield node.value
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            yield from _string_literals(node.elts)
        elif isinstance(node, ast.Starred):
            yield from _string_literals([node.value])


def _names_read(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _called_name(node.func)
            reads_mapping = (name == "get" and isinstance(node.func, ast.Attribute)
                             and _mapping_name(node.func.value) in ENVIRONMENT_MAPPINGS)
            if name in ENV_READERS or reads_mapping:
                yield from _string_literals([*node.args, *(keyword.value for keyword in node.keywords)])
        elif isinstance(node, ast.Subscript) and _mapping_name(node.value) in ENVIRONMENT_MAPPINGS:
            yield from _string_literals([node.slice])


def environment_names_read_by_code() -> dict[str, Path]:
    names: dict[str, Path] = {}
    for path in _source_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _names_read(tree):
            names.setdefault(name, path.relative_to(ROOT_DIR))
    return names


class EnvExampleCoverageTests(unittest.TestCase):
    def test_every_environment_variable_read_is_listed_in_env_example(self):
        listed = _project_configuration_keys()
        unlisted = {
            name: str(path) for name, path in environment_names_read_by_code().items()
            if name not in listed and name not in NOT_OPERATOR_SETTINGS
        }
        self.assertEqual({}, unlisted, "List these in .env.example (commented out with their default).")

    def test_follower_account_settings_are_cleared_whatever_the_account_is_called(self):
        environ = {"ACCOUNT_FOO_PUBLIC_KEY": "k", "ACCOUNT_FOO_PRIVATE_KEY": "s", "ACCOUNT_FOO_STRATEGY": "shares",
                   "ACCOUNT_FOO_EXIT_POLICY": "follow:production", "DB_TIMEOUT_SECONDS": "abc", "HOME": "/home/x"}

        clear_project_configuration(environ)

        self.assertEqual({"HOME": "/home/x"}, environ)

    def test_the_scan_finds_settings_read_through_each_kind_of_reader(self):
        names = environment_names_read_by_code()
        for expected in ("DB_PATH", "TRADING_ACCOUNTS", "VERTEX_MAX_ATTEMPTS", "DB_TIMEOUT_SECONDS"):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()
