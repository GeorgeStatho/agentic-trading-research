from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


SERVICES_DIR = Path(__file__).resolve().parent
PYTHON_SCRIPTS_DIR = SERVICES_DIR.parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
LOGS_DIR = DATA_DIR / "logs"
ENV_PATH = ROOT_DIR / ".env"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"


def load_project_env() -> None:
    """Load the checkout's .env without overriding variables already set.

    Only entrypoints call this, before importing modules that read configuration.
    Importing a module must never load .env: tests and other processes would inherit
    the operator's settings.
    """
    load_dotenv(ENV_PATH)
