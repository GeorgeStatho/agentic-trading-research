from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

PROJECT_DIR = Path(__file__).resolve().parents[3]
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))
WEBSCRAPING_MARKET_DATA_DIR = PROJECT_DIR / "Python Scripts" / "Webscraping" / "market_data"
if str(WEBSCRAPING_MARKET_DATA_DIR) not in sys.path:
    sys.path.append(str(WEBSCRAPING_MARKET_DATA_DIR))

from _shared import parse_published_at
from _strategist_helpers import (
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    HIGH_CONFIDENCE,
    build_strategist_evidence_sections,
)
from db_helpers import get_all_companies, initialize_news_database

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from yfinance_client import REQUEST_HANDLER
except ImportError:
    REQUEST_HANDLER = None


DEFAULT_SUMMARY_ARTICLE_LIMIT = 20
DEFAULT_FULL_ARTICLE_LIMIT = 5
HISTORICAL_PERIOD_CONFIG: tuple[tuple[str, str, int], ...] = (
    ("1d", "5m", 8),
    ("5d", "1h", 8),
    ("1mo", "1d", 10),
    ("3mo", "1d", 12),
)
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

__all__ = [
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "DEFAULT_SUMMARY_ARTICLE_LIMIT",
    "DEFAULT_FULL_ARTICLE_LIMIT",
    "build_strategist_input",
    "build_strategist_prompt_payload",
]


def _serialize_filters(
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
    summary_article_limit: int,
    full_article_limit: int,
) -> dict[str, str | int | None]:
    return {
        "start_time": start_time.astimezone(timezone.utc).isoformat() if start_time is not None else "",
        "end_time": end_time.astimezone(timezone.utc).isoformat() if end_time is not None else "",
        "max_age_days": max_age_days,
        "macro_confidence": HIGH_CONFIDENCE,
        "sector_confidence": HIGH_CONFIDENCE,
        "industry_confidence": HIGH_CONFIDENCE,
        "company_confidence": HIGH_CONFIDENCE,
        "sector_news_source": "cnbc_rss",
        "summary_article_limit": summary_article_limit,
        "full_article_limit": full_article_limit,
    }


def _serialize_company_scope(company: dict[str, Any]) -> dict[str, Any]:
    company_record = _get_company_market_record(company)
    return {
        "company_id": company["company_id"],
        "symbol": company["symbol"],
        "name": company["name"],
        "industry_id": company["industry_id"],
        "industry_key": company["industry_key"],
        "industry_name": company["industry_name"],
        "sector_id": company["sector_id"],
        "sector_key": company["sector_key"],
        "sector_name": company["sector_name"],
        "rating": company_record.get("rating") or "",
        "market_weight": company_record.get("market_weight"),
        "market_data": _deserialize_company_raw_json(company_record.get("raw_json")),
        "historical_price_data": _build_company_historical_price_data(company["symbol"]),
    }


def _deserialize_company_raw_json(raw_json: Any) -> dict[str, Any]:
    if isinstance(raw_json, dict):
        return raw_json
    if isinstance(raw_json, str):
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _get_company_market_record(company: dict[str, Any]) -> dict[str, Any]:
    company_id = int(company["company_id"])
    company_symbol = str(company["symbol"] or "").strip().upper()

    for candidate in get_all_companies():
        try:
            candidate_id = int(candidate.get("id"))
        except (TypeError, ValueError):
            candidate_id = -1
        if candidate_id == company_id:
            return candidate

    for candidate in get_all_companies():
        symbol = str(candidate.get("symbol") or "").strip().upper()
        if symbol == company_symbol:
            return candidate

    return {}


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed):
        return None
    return parsed


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_timestamp(value: Any) -> str:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except TypeError:
            pass
    return str(value or "")


def _empty_historical_snapshot(
    *,
    symbol: str,
    period: str,
    interval: str,
    error: str = "",
) -> dict[str, Any]:
    return {
        "available": False,
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "point_count": 0,
        "first_timestamp": "",
        "last_timestamp": "",
        "first_close": None,
        "last_close": None,
        "absolute_change": None,
        "percent_change": None,
        "period_high": None,
        "period_low": None,
        "total_volume": None,
        "recent_points": [],
        "error": error,
    }


def _summarize_history_frame(
    symbol: str,
    frame: Any,
    *,
    period: str,
    interval: str,
    sample_limit: int,
) -> dict[str, Any]:
    if frame is None or getattr(frame, "empty", True):
        return _empty_historical_snapshot(
            symbol=symbol,
            period=period,
            interval=interval,
            error="No yfinance history rows returned.",
        )

    sample_rows = frame.tail(max(1, int(sample_limit)))
    recent_points: list[dict[str, Any]] = []
    for timestamp, row in sample_rows.iterrows():
        recent_points.append(
            {
                "timestamp": _serialize_timestamp(timestamp),
                "open": _safe_float(row.get("Open")),
                "high": _safe_float(row.get("High")),
                "low": _safe_float(row.get("Low")),
                "close": _safe_float(row.get("Close")),
                "volume": _safe_int(row.get("Volume")),
            }
        )

    first_close = _safe_float(frame["Close"].iloc[0])
    last_close = _safe_float(frame["Close"].iloc[-1])
    absolute_change = None
    percent_change = None
    if first_close is not None and last_close is not None:
        absolute_change = round(last_close - first_close, 6)
        if first_close != 0:
            percent_change = round(((last_close - first_close) / first_close) * 100.0, 4)

    total_volume = None
    if "Volume" in frame:
        raw_volume = frame["Volume"].fillna(0).sum()
        total_volume = _safe_int(raw_volume)

    return {
        "available": True,
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "point_count": int(len(frame.index)),
        "first_timestamp": _serialize_timestamp(frame.index[0]),
        "last_timestamp": _serialize_timestamp(frame.index[-1]),
        "first_close": first_close,
        "last_close": last_close,
        "absolute_change": absolute_change,
        "percent_change": percent_change,
        "period_high": _safe_float(frame["High"].max()) if "High" in frame else None,
        "period_low": _safe_float(frame["Low"].min()) if "Low" in frame else None,
        "total_volume": total_volume,
        "recent_points": recent_points,
        "error": "",
    }


def _build_company_historical_price_data(symbol: Any) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    history_by_period: dict[str, Any] = {}

    if not normalized_symbol:
        for period, interval, _sample_limit in HISTORICAL_PERIOD_CONFIG:
            history_by_period[period] = _empty_historical_snapshot(
                symbol="",
                period=period,
                interval=interval,
                error="Company symbol was missing.",
            )
        return history_by_period

    if yf is None or REQUEST_HANDLER is None:
        error = "yfinance integration is unavailable."
        for period, interval, _sample_limit in HISTORICAL_PERIOD_CONFIG:
            history_by_period[period] = _empty_historical_snapshot(
                symbol=normalized_symbol,
                period=period,
                interval=interval,
                error=error,
            )
        return history_by_period

    try:
        ticker = REQUEST_HANDLER.run(
            yf.Ticker,
            normalized_symbol,
            _context=f"Ticker({normalized_symbol})",
        )
    except Exception as exc:
        error = str(exc)
        for period, interval, _sample_limit in HISTORICAL_PERIOD_CONFIG:
            history_by_period[period] = _empty_historical_snapshot(
                symbol=normalized_symbol,
                period=period,
                interval=interval,
                error=error,
            )
        return history_by_period

    for period, interval, sample_limit in HISTORICAL_PERIOD_CONFIG:
        try:
            history_frame = REQUEST_HANDLER.run(
                lambda current_period=period, current_interval=interval: ticker.history(
                    period=current_period,
                    interval=current_interval,
                    auto_adjust=False,
                    prepost=False,
                ),
                _context=f"history({normalized_symbol},{period},{interval})",
            )
            history_by_period[period] = _summarize_history_frame(
                normalized_symbol,
                history_frame,
                period=period,
                interval=interval,
                sample_limit=sample_limit,
            )
        except Exception as exc:
            history_by_period[period] = _empty_historical_snapshot(
                symbol=normalized_symbol,
                period=period,
                interval=interval,
                error=str(exc),
            )

    return history_by_period


def _round_rollup_metric(value: float) -> float:
    return round(float(value), 4)


def _normalize_reason_key(reason: Any) -> str:
    return " ".join(str(reason or "").strip().lower().split())


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


def _serialize_signal(item: dict[str, Any], *, layer: str) -> dict[str, Any]:
    signal = {
        "layer": layer,
        "article_id": item["article_id"],
        "confidence": item.get("confidence") or "",
        "reason": item.get("reason") or "",
        "published_at": item.get("published_at") or "",
        "source": item.get("source") or "",
        "source_url": item.get("source_url") or "",
        "title": item.get("title") or "",
        "summary": item.get("summary") or "",
    }
    if item.get("impact_direction"):
        signal["impact_direction"] = item["impact_direction"]
    if item.get("impact_magnitude"):
        signal["impact_magnitude"] = item["impact_magnitude"]
    if item.get("materiality"):
        signal["materiality"] = item["materiality"]
    if item.get("time_horizon"):
        signal["time_horizon"] = item["time_horizon"]
    if item.get("effect_type"):
        signal["effect_type"] = item["effect_type"]
    if item.get("relative_positioning"):
        signal["relative_positioning"] = item["relative_positioning"]
    if item.get("news_scope"):
        signal["news_scope"] = item["news_scope"]
    if item.get("sector_name"):
        signal["sector_name"] = item["sector_name"]
    if item.get("industry_name"):
        signal["industry_name"] = item["industry_name"]
    if item.get("company_name"):
        signal["company_name"] = item["company_name"]
    return signal


def _build_view(
    *,
    layer: str,
    items: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    return {
        "layer": layer,
        "label": label,
        "count": len(items),
        "agent_conclusions": [
            _serialize_signal(item, layer=layer)
            for item in items
        ],
    }


def _upsert_supporting_article(
    articles_by_id: dict[int, dict[str, Any]],
    item: dict[str, Any],
    *,
    layer: str,
    include_agent_signal: bool,
) -> None:
    article_id = int(item["article_id"])
    entry = articles_by_id.setdefault(
        article_id,
        {
            "article_id": article_id,
            "title": item.get("title") or "",
            "summary": item.get("summary") or "",
            "body": item.get("body") or "",
            "source": item.get("source") or "",
            "source_url": item.get("source_url") or "",
            "published_at": item.get("published_at") or "",
            "article_scope": item.get("article_scope") or "",
            "evidence_layers": [],
            "agent_signals": [],
        },
    )

    if layer not in entry["evidence_layers"]:
        entry["evidence_layers"].append(layer)

    if include_agent_signal:
        entry["agent_signals"].append(_serialize_signal(item, layer=layer))

    if not entry.get("summary") and item.get("summary"):
        entry["summary"] = item["summary"]
    if not entry.get("body") and item.get("body"):
        entry["body"] = item["body"]
    if not entry.get("title") and item.get("title"):
        entry["title"] = item["title"]


def _build_supporting_articles(
    evidence: dict[str, Any],
    *,
    summary_article_limit: int,
    full_article_limit: int,
) -> dict[str, list[dict[str, Any]]]:
    articles_by_id: dict[int, dict[str, Any]] = {}

    for item in evidence["macro_impacts"]:
        _upsert_supporting_article(articles_by_id, item, layer="macro_view", include_agent_signal=True)
    for item in evidence["sector_impacts"]:
        _upsert_supporting_article(articles_by_id, item, layer="sector_view", include_agent_signal=True)
    for item in evidence["industry_impacts"]:
        _upsert_supporting_article(articles_by_id, item, layer="industry_view", include_agent_signal=True)
    for item in evidence["company_impacts"]:
        _upsert_supporting_article(articles_by_id, item, layer="company_view", include_agent_signal=True)
    for item in evidence["sector_rss_articles"]:
        _upsert_supporting_article(articles_by_id, item, layer="sector_rss_evidence", include_agent_signal=False)

    def article_priority(article: dict[str, Any]) -> tuple[int, datetime, int]:
        high_confidence_signals = sum(
            1 for signal in article["agent_signals"] if str(signal.get("confidence") or "").lower() == HIGH_CONFIDENCE
        )
        signal_count = len(article["agent_signals"])
        layer_count = len(article["evidence_layers"])
        published_at = parse_published_at(article.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc)
        score = (high_confidence_signals * 100) + (signal_count * 10) + layer_count
        return score, published_at, int(article["article_id"])

    ranked_articles = sorted(
        articles_by_id.values(),
        key=article_priority,
        reverse=True,
    )

    summary_articles = [
        {
            "article_id": article["article_id"],
            "title": article["title"],
            "summary": article["summary"],
            "source": article["source"],
            "source_url": article["source_url"],
            "published_at": article["published_at"],
            "article_scope": article["article_scope"],
            "evidence_layers": article["evidence_layers"],
            "agent_signals": article["agent_signals"],
        }
        for article in ranked_articles[: max(1, int(summary_article_limit))]
    ]

    full_articles = [
        {
            "article_id": article["article_id"],
            "title": article["title"],
            "summary": article["summary"],
            "body": article["body"] or article["summary"],
            "source": article["source"],
            "source_url": article["source_url"],
            "published_at": article["published_at"],
            "article_scope": article["article_scope"],
            "evidence_layers": article["evidence_layers"],
            "agent_signals": article["agent_signals"],
        }
        for article in ranked_articles
        if str(article.get("body") or "").strip()
    ][: max(1, int(full_article_limit))]

    return {
        "article_summaries": summary_articles,
        "full_articles": full_articles,
    }


def build_strategist_input(
    company_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    summary_article_limit: int = DEFAULT_SUMMARY_ARTICLE_LIMIT,
    full_article_limit: int = DEFAULT_FULL_ARTICLE_LIMIT,
) -> dict[str, Any]:
    initialize_news_database()
    evidence = build_strategist_evidence_sections(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    company = evidence["company"]
    opportunist_rollup = _build_opportunist_rollup(evidence)

    return {
        "company": _serialize_company_scope(company),
        "peer_groups": evidence["peer_groups"],
        "filters": _serialize_filters(
            start_time=start_time,
            end_time=end_time,
            max_age_days=max_age_days,
            summary_article_limit=summary_article_limit,
            full_article_limit=full_article_limit,
        ),
        "opportunist_rollup": opportunist_rollup,
        "views": {
            "macro_view": _build_view(
                layer="macro_view",
                items=evidence["macro_impacts"],
                label="High-confidence macro news mapped to the company sector",
            ),
            "sector_view": _build_view(
                layer="sector_view",
                items=evidence["sector_impacts"],
                label="High-confidence sector opportunist impacts",
            ),
            "industry_view": _build_view(
                layer="industry_view",
                items=evidence["industry_impacts"],
                label="High-confidence industry opportunist impacts",
            ),
            "company_view": _build_view(
                layer="company_view",
                items=evidence["company_impacts"],
                label="High-confidence company opportunist impacts",
            ),
        },
        "supporting_articles": _build_supporting_articles(
            evidence,
            summary_article_limit=summary_article_limit,
            full_article_limit=full_article_limit,
        ),
    }


def build_strategist_prompt_payload(
    company_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    summary_article_limit: int = DEFAULT_SUMMARY_ARTICLE_LIMIT,
    full_article_limit: int = DEFAULT_FULL_ARTICLE_LIMIT,
) -> str:
    payload = build_strategist_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
    )
    return json.dumps(payload, ensure_ascii=True, indent=2)


if __name__ == "__main__":
    print(build_strategist_prompt_payload("AAPL"))
