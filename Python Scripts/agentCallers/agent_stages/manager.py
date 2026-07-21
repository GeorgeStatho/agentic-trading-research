from __future__ import annotations

import json
from pathlib import Path
import sys


AGENT_STAGES_DIR = Path(__file__).resolve().parent
AGENT_CALLERS_DIR = AGENT_STAGES_DIR.parent
PYTHON_SCRIPTS_DIR = AGENT_CALLERS_DIR.parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
for path in (AGENT_CALLERS_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

from _paths import bootstrap_agent_callers, load_project_env


if __name__ == "__main__":
    load_project_env()

bootstrap_agent_callers()

from agent_stages.manager_context import _build_manager_visible_market_context
from agent_stages.manager_prompt import build_manager_prompt
from agent_stages.manager_runner import DEFAULT_MODEL, decide_company_option_position, replay_manager_decision_from_payload
from agent_stages.manager_shared import MANAGER_PROMPT_VERSION, MANAGER_STAGE_VERSION


__all__ = [
    "MANAGER_STAGE_VERSION",
    "MANAGER_PROMPT_VERSION",
    "_build_manager_visible_market_context",
    "build_manager_prompt",
    "decide_company_option_position",
    "replay_manager_decision_from_payload",
]


if __name__ == "__main__":
    print(json.dumps(decide_company_option_position("AAPL"), ensure_ascii=True, indent=2))
