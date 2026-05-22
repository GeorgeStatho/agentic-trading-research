from __future__ import annotations

from collections import defaultdict

from strategist_payload_rollup_common import (
    _build_empty_count_map,
    _build_empty_weight_map,
    _build_top_reasons,
    _get_signal_weight,
    _pick_dominant_bucket,
    _round_rollup_metric,
    _safe_int,
    EFFECT_TYPE_WEIGHTS,
    ROLLUP_DIRECTIONS,
    ROLLUP_EFFECT_TYPES,
    ROLLUP_HORIZONS,
    ROLLUP_MATERIALITY,
    ROLLUP_RELATIVE_POSITIONING,
)


def _build_rollup_section(
    items: list[dict[str, Any]],
    *,
    include_relative_positioning: bool = False,
) -> dict[str, Any]:
    direction_counts = _build_empty_count_map(ROLLUP_DIRECTIONS)
    materiality_counts = _build_empty_count_map(ROLLUP_MATERIALITY)
    horizon_counts = _build_empty_count_map(ROLLUP_HORIZONS)
    effect_type_counts = _build_empty_count_map(ROLLUP_EFFECT_TYPES)
    weighted_direction_totals = _build_empty_weight_map(ROLLUP_DIRECTIONS)
    weighted_materiality_totals = _build_empty_weight_map(ROLLUP_MATERIALITY)
    weighted_horizon_totals = _build_empty_weight_map(ROLLUP_HORIZONS)
    relative_positioning_summary = {
        key: {"count": 0, "weighted_score": 0.0}
        for key in ROLLUP_RELATIVE_POSITIONING
    }

    positive_high_materiality_direct_count = 0
    negative_high_materiality_direct_count = 0
    direct_positive_weight = 0.0
    direct_negative_weight = 0.0
    unique_article_ids: set[int] = set()

    for item in items:
        direction = str(item.get("impact_direction") or "").strip().lower()
        materiality = str(item.get("materiality") or "").strip().lower()
        horizon = str(item.get("time_horizon") or "").strip().lower()
        effect_type = str(item.get("effect_type") or "").strip().lower()
        relative_positioning = str(item.get("relative_positioning") or "").strip().lower()
        weight = _get_signal_weight(item)

        article_id = _safe_int(item.get("article_id"))
        if article_id is not None:
            unique_article_ids.add(article_id)

        if direction in direction_counts:
            direction_counts[direction] += 1
            weighted_direction_totals[direction] += weight
        if materiality in materiality_counts:
            materiality_counts[materiality] += 1
            weighted_materiality_totals[materiality] += weight
        if horizon in horizon_counts:
            horizon_counts[horizon] += 1
            weighted_horizon_totals[horizon] += weight
        if effect_type in effect_type_counts:
            effect_type_counts[effect_type] += 1

        if effect_type == "direct" and direction == "positive":
            direct_positive_weight += weight
        if effect_type == "direct" and direction == "negative":
            direct_negative_weight += weight
        if materiality == "high" and effect_type == "direct" and direction == "positive":
            positive_high_materiality_direct_count += 1
        if materiality == "high" and effect_type == "direct" and direction == "negative":
            negative_high_materiality_direct_count += 1

        if include_relative_positioning and relative_positioning in relative_positioning_summary:
            relative_positioning_summary[relative_positioning]["count"] += 1
            relative_positioning_summary[relative_positioning]["weighted_score"] += weight

    contradiction_flags = {
        "has_directional_conflict": weighted_direction_totals["positive"] > 0 and weighted_direction_totals["negative"] > 0,
        "has_high_materiality_conflict": (
            positive_high_materiality_direct_count > 0 and negative_high_materiality_direct_count > 0
        ),
        "has_direct_conflict": direct_positive_weight > 0 and direct_negative_weight > 0,
    }

    rollup = {
        "impact_count": len(items),
        "article_count": len(unique_article_ids),
        "positive_count": direction_counts["positive"],
        "negative_count": direction_counts["negative"],
        "mixed_count": direction_counts["mixed"],
        "neutral_count": direction_counts["neutral"],
        "high_materiality_count": materiality_counts["high"],
        "medium_materiality_count": materiality_counts["medium"],
        "low_materiality_count": materiality_counts["low"],
        "immediate_count": horizon_counts["immediate"],
        "short_term_count": horizon_counts["short_term"],
        "medium_term_count": horizon_counts["medium_term"],
        "unclear_count": horizon_counts["unclear"],
        "direct_count": effect_type_counts["direct"],
        "indirect_count": effect_type_counts["indirect"],
        "high_materiality_direct_count": positive_high_materiality_direct_count + negative_high_materiality_direct_count,
        "direction_counts": direction_counts,
        "materiality_counts": materiality_counts,
        "time_horizon_counts": horizon_counts,
        "effect_type_counts": effect_type_counts,
        "weighted_direction_totals": {
            key: _round_rollup_metric(value) for key, value in weighted_direction_totals.items()
        },
        "weighted_materiality_totals": {
            key: _round_rollup_metric(value) for key, value in weighted_materiality_totals.items()
        },
        "weighted_horizon_totals": {
            key: _round_rollup_metric(value) for key, value in weighted_horizon_totals.items()
        },
        "dominant_direction": _pick_dominant_bucket(weighted_direction_totals, default="unclear"),
        "dominant_time_horizon": _pick_dominant_bucket(weighted_horizon_totals, default="unclear"),
        "dominant_effect_type": _pick_dominant_bucket(
            {
                key: float(effect_type_counts[key]) * EFFECT_TYPE_WEIGHTS.get(key, 1.0)
                for key in ROLLUP_EFFECT_TYPES
            },
            default="unclear",
        ),
        "net_direction_score": _round_rollup_metric(
            weighted_direction_totals["positive"] - weighted_direction_totals["negative"]
        ),
        "contradiction_flags": contradiction_flags,
        "contradictions_present": any(contradiction_flags.values()),
        "top_reasons": _build_top_reasons(items, limit=4),
        "top_positive_reasons": _build_top_reasons(items, directions={"positive"}, limit=3),
        "top_negative_reasons": _build_top_reasons(items, directions={"negative"}, limit=3),
        "top_direct_reasons": _build_top_reasons(items, effect_type="direct", limit=3),
        "top_indirect_reasons": _build_top_reasons(items, effect_type="indirect", limit=3),
    }

    if include_relative_positioning:
        rollup["relative_positioning_summary"] = {
            key: {
                "count": int(value["count"]),
                "weighted_score": _round_rollup_metric(value["weighted_score"]),
            }
            for key, value in relative_positioning_summary.items()
        }

    return rollup


def _build_grouped_rollups(
    items: list[dict[str, Any]],
    *,
    subject_id_key: str,
    subject_key_key: str,
    subject_name_key: str,
) -> list[dict[str, Any]]:
    grouped_items: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in items:
        subject_key = str(item.get(subject_key_key) or item.get(subject_name_key) or "").strip()
        if not subject_key:
            continue
        grouped_items[subject_key].append(item)

    grouped_rollups: list[dict[str, Any]] = []
    for subject_key, subject_items in grouped_items.items():
        summary = _build_rollup_section(subject_items)
        grouped_rollups.append(
            {
                subject_id_key: subject_items[0].get(subject_id_key),
                subject_key_key: subject_key,
                subject_name_key: subject_items[0].get(subject_name_key) or subject_key,
                **summary,
            }
        )

    return sorted(
        grouped_rollups,
        key=lambda item: (
            abs(float(item.get("net_direction_score") or 0.0)),
            int(item.get("impact_count") or 0),
            str(item.get(subject_name_key) or ""),
        ),
        reverse=True,
    )


def _build_opportunist_rollup(evidence: dict[str, Any]) -> dict[str, Any]:
    sector_items = evidence.get("sector_impacts", [])
    industry_items = evidence.get("industry_impacts", [])
    company_items = evidence.get("company_impacts", [])

    sector_rollup = _build_rollup_section(sector_items)
    if sector_items:
        sector_rollup.update(
            {
                "sector_id": sector_items[0].get("sector_id"),
                "sector_key": sector_items[0].get("sector_key") or "",
                "sector_name": sector_items[0].get("sector_name") or "",
            }
        )

    industry_rollup = _build_rollup_section(industry_items)
    industry_summaries = _build_grouped_rollups(
        industry_items,
        subject_id_key="industry_id",
        subject_key_key="industry_key",
        subject_name_key="industry_name",
    )
    industry_rollup.update(
        {
            "industry_summaries": industry_summaries,
            "top_supportive_industries": [
                {
                    "industry_id": entry.get("industry_id"),
                    "industry_key": entry.get("industry_key") or "",
                    "industry_name": entry.get("industry_name") or "",
                    "net_direction_score": entry.get("net_direction_score"),
                    "impact_count": entry.get("impact_count"),
                    "dominant_time_horizon": entry.get("dominant_time_horizon") or "unclear",
                }
                for entry in sorted(
                    industry_summaries,
                    key=lambda entry: float(entry.get("net_direction_score") or 0.0),
                    reverse=True,
                )
                if float(entry.get("net_direction_score") or 0.0) > 0
            ][:3],
            "top_risky_industries": [
                {
                    "industry_id": entry.get("industry_id"),
                    "industry_key": entry.get("industry_key") or "",
                    "industry_name": entry.get("industry_name") or "",
                    "net_direction_score": entry.get("net_direction_score"),
                    "impact_count": entry.get("impact_count"),
                    "dominant_time_horizon": entry.get("dominant_time_horizon") or "unclear",
                }
                for entry in sorted(
                    industry_summaries,
                    key=lambda entry: float(entry.get("net_direction_score") or 0.0),
                )
                if float(entry.get("net_direction_score") or 0.0) < 0
            ][:3],
        }
    )

    company_rollup = _build_rollup_section(company_items, include_relative_positioning=True)
    if company_items:
        company_rollup.update(
            {
                "company_id": company_items[0].get("company_id"),
                "symbol": company_items[0].get("symbol") or "",
                "company_name": company_items[0].get("company_name") or "",
            }
        )

    return {
        "sector": sector_rollup,
        "industries": industry_rollup,
        "company": company_rollup,
    }
