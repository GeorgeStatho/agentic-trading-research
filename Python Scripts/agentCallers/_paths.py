from __future__ import annotations

from pathlib import Path
import sys


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
PYTHON_SCRIPTS_DIR = AGENT_CALLERS_DIR.parent
ROOT_DIR = AGENT_CALLERS_DIR.parents[1]
DATA_DIR = ROOT_DIR / "Data"


def add_agent_caller_paths() -> None:
    for path in (AGENT_CALLERS_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR):
        normalized = str(path)
        if normalized not in sys.path:
            sys.path.append(normalized)
