from __future__ import annotations

import math
from typing import Any


ROLLUP_DIRECTIONS: tuple[str, ...] = ("positive", "negative", "mixed", "neutral")
ROLLUP_MATERIALITY: tuple[str, ...] = ("high", "medium", "low")
ROLLUP_HORIZONS: tuple[str, ...] = ("immediate", "short_term", "medium_term", "unclear")
ROLLUP_EFFECT_TYPES: tuple[str, ...] = ("direct", "indirect")
ROLLUP_RELATIVE_POSITIONING: tuple[str, ...] = (
    "better_than_peers",
    "worse_than_peers",
    "similar",
    "not_applicable",
)
MATERIALITY_WEIGHTS = {"high": 3.0, "medium": 2.0, "low": 1.0}
MAGNITUDE_WEIGHTS = {"major": 3.0, "moderate": 2.25, "modest": 1.5, "minimal": 1.0}
EFFECT_TYPE_WEIGHTS = {"direct": 1.5, "indirect": 1.0}
CONFIDENCE_WEIGHTS = {"high": 1.2, "medium": 1.0, "low": 0.8}


def _round_rollup_metric(value: float) -> float:
    return round(float(value), 4)


def _normalize_reason_key(reason: Any) -> str:
    return " ".join(str(reason or "").strip().lower().split())


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_signal_weight(item: dict[str, Any]) -> float:
    materiality_weight = MATERIALITY_WEIGHTS.get(str(item.get("materiality") or "").strip().lower(), 0.0)
    magnitude_weight = MAGNITUDE_WEIGHTS.get(str(item.get("impact_magnitude") or "").strip().lower(), 0.0)
    effect_type_weight = EFFECT_TYPE_WEIGHTS.get(str(item.get("effect_type") or "").strip().lower(), 1.0)
    confidence_weight = CONFIDENCE_WEIGHTS.get(str(item.get("confidence") or "").strip().lower(), 1.0)
    return materiality_weight * magnitude_weight * effect_type_weight * confidence_weight


def _build_empty_count_map(keys: tuple[str, ...]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _build_empty_weight_map(keys: tuple[str, ...]) -> dict[str, float]:
    return {key: 0.0 for key in keys}


def _pick_dominant_bucket(weighted_totals: dict[str, float], *, default: str) -> str:
    if not weighted_totals:
        return default
    ranked = sorted(weighted_totals.items(), key=lambda item: (item[1], item[0]), reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return default
    if len(ranked) > 1 and math.isclose(ranked[0][1], ranked[1][1], rel_tol=0.0, abs_tol=0.001):
        return default
    return ranked[0][0]


def _build_top_reasons(
    items: list[dict[str, Any]],
    *,
    directions: set[str] | None = None,
    effect_type: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    reasons: dict[str, dict[str, Any]] = {}

    for item in items:
        direction = str(item.get("impact_direction") or "").strip().lower()
        if directions is not None and direction not in directions:
            continue
        current_effect_type = str(item.get("effect_type") or "").strip().lower()
        if effect_type is not None and current_effect_type != effect_type:
            continue

        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue

        key = _normalize_reason_key(reason)
        if not key:
            continue

        weight = _get_signal_weight(item)
        published_at = item.get("published_at") or ""
        entry = reasons.setdefault(
            key,
            {
                "reason": reason,
                "count": 0,
                "weighted_score": 0.0,
                "latest_published_at": "",
            },
        )
        entry["count"] += 1
        entry["weighted_score"] += weight
        if published_at and str(published_at) > str(entry["latest_published_at"]):
            entry["latest_published_at"] = str(published_at)

    ranked_reasons = sorted(
        reasons.values(),
        key=lambda entry: (
            entry["weighted_score"],
            entry["count"],
            entry["latest_published_at"],
            entry["reason"],
        ),
        reverse=True,
    )

    return [
        {
            "reason": entry["reason"],
            "count": int(entry["count"]),
            "weighted_score": _round_rollup_metric(entry["weighted_score"]),
            "latest_published_at": entry["latest_published_at"],
        }
        for entry in ranked_reasons[: max(0, int(limit))]
    ]
