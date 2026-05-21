from __future__ import annotations

import copy
import json
import sys

from . import apply_deterministic_option_selection


if __name__ == "__main__":
    from agent_helpers.manager import test_market_context

    symbol = str(sys.argv[1] if len(sys.argv) > 1 else "AAPL").strip().upper()
    market_test = test_market_context(symbol)
    market_context = market_test.get("market_context", {})
    base_manager_result = {
        "company": market_test.get("company", {}),
        "context_snapshot": market_test.get("diagnostics", {}),
        "market_context": market_context,
        "selected_option": None,
    }

    for decision in ("call", "put"):
        test_manager_result = copy.deepcopy(base_manager_result)
        test_manager_result["recommendation"] = {
            "decision": decision,
            "confidence": "high",
            "reason": f"Manual test for {decision}",
        }
        selected_result = apply_deterministic_option_selection(test_manager_result)
        print(f"\n=== TEST DECISION: {decision.upper()} ===")
        print(json.dumps(selected_result, ensure_ascii=True, indent=2))
