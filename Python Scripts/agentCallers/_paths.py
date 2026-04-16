from __future__ import annotations

"""Shared runtime bootstrap helpers for the ``agentCallers`` package.

The project still supports a mix of direct script execution and package-style
imports. Centralizing path and environment setup here keeps the downstream
modules focused on business logic instead of Python bootstrapping details.
"""

from pathlib import Path
import sys

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
PYTHON_SCRIPTS_DIR = AGENT_CALLERS_DIR.parent
ROOT_DIR = AGENT_CALLERS_DIR.parents[1]
DATA_DIR = ROOT_DIR / "Data"
LOGS_DIR = DATA_DIR / "logs"
ENV_PATH = ROOT_DIR / ".env"
WEBSCRAPING_DIR = PYTHON_SCRIPTS_DIR / "Webscraping"
WEBSCRAPING_MARKET_DATA_DIR = WEBSCRAPING_DIR / "market_data"


def _append_path(path: Path) -> None:
    """Add a path to ``sys.path`` once while preserving the existing order."""
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)


def add_agent_caller_paths(*extra_paths: Path) -> None:
    """Register the default import paths used by agent callers.

    Usage:
        Call near the top of a script that might be executed directly instead of
        imported as part of the package.
    """
    for path in (AGENT_CALLERS_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR, *extra_paths):
        _append_path(path)


def add_webscraping_paths() -> None:
    """Register the Webscraping folders used by pipeline subprocesses."""
    add_agent_caller_paths(WEBSCRAPING_DIR, WEBSCRAPING_MARKET_DATA_DIR)


def load_project_env() -> None:
    """Load ``Stock-trading-experiment/.env`` when ``python-dotenv`` is present."""
    if load_dotenv is not None:
        load_dotenv(ENV_PATH)


def bootstrap_agent_callers(
    *,
    include_webscraping: bool = False,
    load_env_file: bool = False,
) -> None:
    """Apply the standard runtime setup for agent caller modules.

    Args:
        include_webscraping: Also register the sibling ``Webscraping`` import
            locations used by pipeline runners and market-data builders.
        load_env_file: Load the project ``.env`` file before the module reads
            environment variables.
    """
    if include_webscraping:
        add_webscraping_paths()
    else:
        add_agent_caller_paths()

    if load_env_file:
        load_project_env()
