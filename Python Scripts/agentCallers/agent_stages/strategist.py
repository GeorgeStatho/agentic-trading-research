from __future__ import annotations

import json

from agent_stages.strategist_prompt import build_strategist_prompt
from agent_stages.strategist_runner import decide_company_purchase
from agent_stages.strategist_schema import STRATEGIST_RECOMMENDATION_SCHEMA


__all__ = [
    "build_strategist_prompt",
    "decide_company_purchase",
]


if __name__ == "__main__":
    print(json.dumps(decide_company_purchase("AAPL"), ensure_ascii=True, indent=2))
