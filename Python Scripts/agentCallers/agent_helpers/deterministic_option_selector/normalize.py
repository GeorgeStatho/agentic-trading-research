from __future__ import annotations

from typing import Any


def _coerce_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_option_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None

    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = int(text)
    except ValueError:
        return None
    return normalized if normalized > 0 else None


def _normalize_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    decision = {
        "bullish": "call",
        "buy_call": "call",
        "buy call": "call",
        "calls": "call",
        "bearish": "put",
        "buy_put": "put",
        "buy put": "put",
        "puts": "put",
        "hold": "neither",
        "avoid": "neither",
        "pass": "neither",
        "no_trade": "neither",
        "no trade": "neither",
        "neutral": "neither",
    }.get(decision, decision)
    return decision if decision in {"call", "put", "neither"} else ""


def _normalize_confidence(value: Any) -> str:
    confidence = str(value or "").strip().lower()
    return confidence if confidence in {"high", "medium", "low"} else ""


def _confidence_rank(confidence: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(_normalize_confidence(confidence), -1)


def _downgrade_confidence(confidence: str, steps: int) -> str:
    normalized_confidence = _normalize_confidence(confidence)
    if not normalized_confidence:
        return confidence
    rank = max(0, _confidence_rank(normalized_confidence) - max(0, int(steps)))
    return ("low", "medium", "high")[rank]


def _normalize_strategist_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    decision = {
        "trade candidate": "trade_candidate",
        "watch list": "watchlist",
        "do not trade": "do_not_trade",
    }.get(decision, decision)
    return decision if decision in {"trade_candidate", "watchlist", "do_not_trade"} else ""


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def _is_selection_eligible_under_confidence_guardrails(
    *,
    decision: str,
    confidence: str,
    strategist_recommendation: dict[str, Any] | None,
) -> tuple[bool, str]:
    if decision not in {"call", "put"}:
        return False, "decision_not_directional"
    if confidence not in {"high", "medium"}:
        return False, "confidence_below_medium"

    strategist_recommendation = strategist_recommendation if isinstance(strategist_recommendation, dict) else {}
    strategist_decision = _normalize_strategist_decision(strategist_recommendation.get("decision"))
    if strategist_decision == "do_not_trade":
        return False, "strategist_do_not_trade"
    return True, ""
