from __future__ import annotations

"""Phase 4 of the LLM thesis validator: the deterministic arbiter.

The only layer allowed to turn an LLM verdict into a broker action, and every
failure path defaults to "do nothing new":

| Rules layer | LLM verdict                          | Action                          |
|-------------|--------------------------------------|---------------------------------|
| EXIT        | anything                             | Exit (LLM not consulted)        |
| HOLD        | invalidated + would_open=no          | Close                           |
| HOLD        | invalidated + would_open=yes/marginal| Close half if >=2, else flag    |
| HOLD        | degraded                             | Flag only (advisory tighten)    |
| HOLD        | intact / unclear / error             | No action                       |

Circuit breakers: a daily cap on LLM-initiated exits and a kill switch env
var read at container startup. Both downgrade to flag-only, never to a trade.
"""

from typing import Any


ARBITER_POLICY_VERSION = "thesis-arbiter-v1"

ACTION_NONE = "none"
ACTION_FLAG = "flag"
ACTION_TIGHTEN = "tighten"
ACTION_CLOSE = "close"
ACTION_CLOSE_HALF = "close_half"


def decide_thesis_arbiter_action(
    *,
    rules_exit_active: bool,
    verdict: dict[str, Any] | None,
    quantity: int,
    llm_exits_today: int,
    max_daily_llm_exits: int,
    kill_switch: bool,
) -> dict[str, Any]:
    """Map (rules verdict, LLM verdict) to a deterministic action."""

    def _result(action: str, reason: str) -> dict[str, Any]:
        return {
            "action": action,
            "reason": reason,
            "policy_version": ARBITER_POLICY_VERSION,
            "rules_exit_active": rules_exit_active,
            "llm_exits_today": llm_exits_today,
            "max_daily_llm_exits": max_daily_llm_exits,
            "kill_switch": kill_switch,
        }

    if rules_exit_active:
        return _result(ACTION_NONE, "rules_exit_precedence")

    if not isinstance(verdict, dict) or verdict.get("consistency") == "error":
        return _result(ACTION_NONE, "llm_error_or_timeout")

    thesis_state = str(verdict.get("thesis_state") or "").strip().lower()
    would_open = str(verdict.get("would_open_today") or "").strip().lower()

    if thesis_state in {"intact", "unclear", ""}:
        return _result(ACTION_NONE, f"thesis_{thesis_state or 'unclear'}")

    if thesis_state == "degraded":
        return _result(ACTION_TIGHTEN, "thesis_degraded_advisory")

    if thesis_state != "invalidated":
        return _result(ACTION_NONE, "unknown_thesis_state")

    proposed_action = ACTION_CLOSE if would_open == "no" else (
        ACTION_CLOSE_HALF if quantity >= 2 else ACTION_FLAG
    )
    if proposed_action == ACTION_FLAG:
        return _result(ACTION_FLAG, "invalidated_single_contract_no_partial")

    if kill_switch:
        return _result(ACTION_FLAG, "kill_switch_active")
    if llm_exits_today >= max(0, int(max_daily_llm_exits)):
        return _result(ACTION_FLAG, "daily_exit_cap_reached")

    reason = "invalidated_would_not_open" if proposed_action == ACTION_CLOSE else "invalidated_would_still_open"
    return _result(proposed_action, reason)
