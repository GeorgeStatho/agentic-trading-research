from __future__ import annotations

import json
import os
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

from agent_stages.strategist_prompt import build_strategist_prompt
from agent_stages.strategist_runner import DEFAULT_MODEL, decide_company_purchase
from agent_stages.strategist_schema import STRATEGIST_RECOMMENDATION_SCHEMA


OLLAMA_HOST = os.getenv(
    "STRATEGIST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)

__all__ = [
    "build_strategist_prompt",
    "decide_company_purchase",
]


if __name__ == "__main__":
    print(json.dumps(decide_company_purchase("AAPL"), ensure_ascii=True, indent=2))
