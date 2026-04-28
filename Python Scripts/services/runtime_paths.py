from __future__ import annotations

from pathlib import Path


SERVICES_DIR = Path(__file__).resolve().parent
PYTHON_SCRIPTS_DIR = SERVICES_DIR.parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
LOGS_DIR = DATA_DIR / "logs"
ENV_PATH = ROOT_DIR / ".env"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
