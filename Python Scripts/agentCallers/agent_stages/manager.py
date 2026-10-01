from __future__ import annotations

import json

from services.settings import load_process_settings


if __name__ == "__main__":
    load_process_settings()


from agent_stages.manager_context import _build_manager_visible_market_context
from agent_stages.manager_prompt import build_manager_prompt
from agent_stages.manager_runner import decide_company_option_position, replay_manager_decision_from_payload
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
