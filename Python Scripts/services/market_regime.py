from __future__ import annotations

from contextlib import closing
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_helpers.market_context.common import REQUEST_HANDLER, yf
from agent_helpers.market_context.underlying import _extract_bar_close, _extract_bar_timestamp, _fetch_daily_stock_bars
from agent_helpers.market_context.underlying_indicators import (
    _compute_exponential_moving_average,
    _compute_simple_moving_average,
)
from agent_helpers.volatility import compute_realized_volatility
from services.regime_history import refresh_regime_history


LOGGER = logging.getLogger("market_regime")
POLICY_VERSION = "market-regime-v1"


@dataclass(frozen=True)
class MarketRegimeSnapshotPolicy:
    policy_version: str = POLICY_VERSION
    max_market_data_age_seconds: int = 72 * 60 * 60
    max_macro_calendar_age_minutes: int = 24 * 60
    high_volatility_realized_volatility: float = 0.28
    stressed_realized_volatility: float = 0.45
    high_volatility_vix_percentile: float = 80.0
    stressed_vix_percentile: float = 95.0
    stressed_daily_move_pct: float = 3.5
    minimum_trend_strength_pct: float = 1.0
    macro_before_minutes: int = 60
    macro_after_minutes: int = 30

    @classmethod
    def from_env(cls) -> "MarketRegimeSnapshotPolicy":
        def integer(name: str, default: int) -> int:
            try:
                return max(0, int(os.getenv(name, str(default))))
            except ValueError:
                return default

        def number(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except ValueError:
                return default

        return cls(
            max_market_data_age_seconds=integer("MARKET_REGIME_MAX_MARKET_DATA_AGE_SECONDS", 72 * 60 * 60),
            max_macro_calendar_age_minutes=integer("MARKET_REGIME_MAX_MACRO_CALENDAR_AGE_MINUTES", 24 * 60),
            high_volatility_realized_volatility=number("MARKET_REGIME_HIGH_VOLATILITY_RV", 0.28),
            stressed_realized_volatility=number("MARKET_REGIME_STRESSED_RV", 0.45),
            high_volatility_vix_percentile=number("MARKET_REGIME_HIGH_VOLATILITY_VIX_PERCENTILE", 80.0),
            stressed_vix_percentile=number("MARKET_REGIME_STRESSED_VIX_PERCENTILE", 95.0),
            stressed_daily_move_pct=number("MARKET_REGIME_STRESSED_DAILY_MOVE_PCT", 3.5),
            minimum_trend_strength_pct=number("MARKET_REGIME_MIN_TREND_STRENGTH_PCT", 1.0),
            macro_before_minutes=integer("MARKET_REGIME_MACRO_BEFORE_MINUTES", 60),
            macro_after_minutes=integer("MARKET_REGIME_MACRO_AFTER_MINUTES", 30),
        )


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _percentile_rank(values: Iterable[float], value: float | None) -> float | None:
    numbers = [float(item) for item in values if item is not None]
    if value is None or not numbers:
        return None
    return round((sum(item <= value for item in numbers) / len(numbers)) * 100.0, 2)


def _is_high_impact_us_event(event: dict[str, Any]) -> bool:
    name = str(event.get("event_name") or "").lower()
    country = str(event.get("country") or "").lower()
    currency = str(event.get("currency") or "").upper()
    importance = str(event.get("importance") or "").lower()
    if country not in {"united states", "us", "usa", ""} and currency != "USD":
        return False
    if importance not in {"high", "3", "three"}:
        return False
    phrases = ("fomc", "fed", "interest rate", "cpi", "pce", "payroll", "nonfarm", "gdp")
    return any(phrase in name for phrase in phrases)


def _macro_event_window(
    events: list[dict[str, Any]],
    *,
    as_of: datetime,
    policy: MarketRegimeSnapshotPolicy,
) -> tuple[dict[str, Any], list[str]]:
    relevant_events = [event for event in events if _is_high_impact_us_event(event)]
    unknown_time_today = False
    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    active_event: tuple[datetime, dict[str, Any]] | None = None

    for event in relevant_events:
        scheduled_at = _parse_datetime(event.get("scheduled_at_utc"))
        if scheduled_at is None:
            if str(event.get("event_date") or "") == as_of.date().isoformat():
                unknown_time_today = True
            continue
        if scheduled_at < as_of:
            if (as_of - scheduled_at).total_seconds() <= policy.macro_after_minutes * 60:
                active_event = (scheduled_at, event)
        elif (scheduled_at - as_of).total_seconds() <= policy.macro_before_minutes * 60:
            active_event = (scheduled_at, event)
        if scheduled_at >= as_of:
            upcoming.append((scheduled_at, event))

    if active_event is not None:
        scheduled_at, event = active_event
        return (
            {
                "active": True,
                "next_event": str(event.get("event_name") or "High-impact macro event"),
                "scheduled_at": scheduled_at.isoformat(),
                "minutes_until": round((scheduled_at - as_of).total_seconds() / 60.0, 1),
            },
            ["macro_blackout_active"],
        )
    if unknown_time_today:
        return (
            {
                "active": True,
                "next_event": "High-impact US macro event with unknown time",
                "scheduled_at": None,
                "minutes_until": None,
            },
            ["macro_event_time_unknown"],
        )
    if upcoming:
        scheduled_at, event = min(upcoming, key=lambda item: item[0])
        return (
            {
                "active": False,
                "next_event": str(event.get("event_name") or "High-impact macro event"),
                "scheduled_at": scheduled_at.isoformat(),
                "minutes_until": round((scheduled_at - as_of).total_seconds() / 60.0, 1),
            },
            [],
        )
    return ({"active": False, "next_event": None, "scheduled_at": None, "minutes_until": None}, [])


def classify_market_regime(
    *,
    spy_closes: list[float],
    spy_last_timestamp: str | None,
    vix_closes: list[float] | None = None,
    macro_events: list[dict[str, Any]] | None = None,
    macro_refreshed_at: str | None = None,
    as_of: datetime | None = None,
    policy: MarketRegimeSnapshotPolicy | None = None,
) -> dict[str, Any]:
    """Classify a market state from deterministic, dated market and calendar inputs."""
    active_policy = policy or MarketRegimeSnapshotPolicy.from_env()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    valid_closes = [float(close) for close in spy_closes if close is not None and float(close) > 0]
    vix_values = [float(close) for close in (vix_closes or []) if close is not None and float(close) > 0]
    latest_market_at = _parse_datetime(spy_last_timestamp)
    latest_macro_at = _parse_datetime(macro_refreshed_at)
    market_age_seconds = round((now - latest_market_at).total_seconds(), 1) if latest_market_at else None
    macro_age_minutes = round((now - latest_macro_at).total_seconds() / 60.0, 1) if latest_macro_at else None
    macro_window, macro_reason_codes = _macro_event_window(
        macro_events or [], as_of=now, policy=active_policy
    )

    ema_20 = _compute_exponential_moving_average(valid_closes, 20)
    sma_50 = _compute_simple_moving_average(valid_closes, 50)
    prior_ema_20 = _compute_exponential_moving_average(valid_closes[:-5], 20) if len(valid_closes) >= 25 else None
    latest_price = valid_closes[-1] if valid_closes else None
    price_vs_20ema = ((latest_price - ema_20) / ema_20 * 100.0) if latest_price and ema_20 else None
    price_vs_50sma = ((latest_price - sma_50) / sma_50 * 100.0) if latest_price and sma_50 else None
    ema_slope = ((ema_20 - prior_ema_20) / prior_ema_20 * 100.0) if ema_20 and prior_ema_20 else None
    trend_strength = (abs(ema_20 - sma_50) / latest_price * 100.0) if latest_price and ema_20 and sma_50 else None
    realized_volatility = compute_realized_volatility(valid_closes, 20) if len(valid_closes) >= 21 else None
    vix_percentile = _percentile_rank(vix_values[-252:], vix_values[-1] if len(vix_values) >= 252 else None)
    daily_move_pct = ((valid_closes[-1] - valid_closes[-2]) / valid_closes[-2] * 100.0) if len(valid_closes) >= 2 and valid_closes[-2] else None

    reason_codes: list[str] = []
    market_stale = (
        latest_market_at is None
        or market_age_seconds is None
        or market_age_seconds < 0
        or market_age_seconds > active_policy.max_market_data_age_seconds
    )
    macro_stale = (
        latest_macro_at is None
        or macro_age_minutes is None
        or macro_age_minutes < 0
        or macro_age_minutes > active_policy.max_macro_calendar_age_minutes
    )
    if market_stale:
        reason_codes.append("market_data_missing_or_stale")
    if macro_stale:
        reason_codes.append("macro_calendar_missing_or_stale")
    reason_codes.extend(macro_reason_codes)

    signals = {
        "spy_trend": "unknown",
        "spy_price_vs_20ema_pct": _round(price_vs_20ema),
        "spy_price_vs_50sma_pct": _round(price_vs_50sma),
        "trend_strength": _round(trend_strength),
        "spy_realized_volatility_20d": _round(realized_volatility),
        "vix_percentile_1y": _round(vix_percentile, 2),
        "market_breadth": "unavailable",
    }

    if market_stale or macro_stale or len(valid_closes) < 50:
        if len(valid_closes) < 50:
            reason_codes.append("insufficient_spy_history")
        state = "unknown"
    elif macro_window["active"]:
        state = "stressed"
    elif (
        (daily_move_pct is not None and abs(daily_move_pct) >= active_policy.stressed_daily_move_pct)
        or (realized_volatility is not None and realized_volatility >= active_policy.stressed_realized_volatility)
        or (vix_percentile is not None and vix_percentile >= active_policy.stressed_vix_percentile)
    ):
        state = "stressed"
        reason_codes.append("extreme_market_volatility_or_move")
    elif (
        (realized_volatility is not None and realized_volatility >= active_policy.high_volatility_realized_volatility)
        or (vix_percentile is not None and vix_percentile >= active_policy.high_volatility_vix_percentile)
    ):
        state = "high_volatility"
        reason_codes.append("elevated_market_volatility")
    elif (
        price_vs_20ema is not None and price_vs_50sma is not None and ema_slope is not None and trend_strength is not None
        and price_vs_20ema > 0 and price_vs_50sma > 0 and ema_slope > 0 and trend_strength >= active_policy.minimum_trend_strength_pct
    ):
        state = "trend_up"
        signals["spy_trend"] = "bullish"
        reason_codes.append("spy_price_ma_alignment_bullish")
    elif (
        price_vs_20ema is not None and price_vs_50sma is not None and ema_slope is not None and trend_strength is not None
        and price_vs_20ema < 0 and price_vs_50sma < 0 and ema_slope < 0 and trend_strength >= active_policy.minimum_trend_strength_pct
    ):
        state = "trend_down"
        signals["spy_trend"] = "bearish"
        reason_codes.append("spy_price_ma_alignment_bearish")
    else:
        state = "range"
        signals["spy_trend"] = "range"
        reason_codes.append("no_directional_market_trend")

    confidence = "low" if state == "unknown" else "high" if vix_percentile is not None else "medium"
    return {
        "as_of": now.isoformat(),
        "state": state,
        "confidence": confidence,
        "input_freshness": {
            "market_data_age_seconds": market_age_seconds,
            "macro_calendar_age_minutes": macro_age_minutes,
        },
        "signals": signals,
        "macro_event_window": macro_window,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "policy_version": active_policy.policy_version,
    }


def _load_macro_calendar() -> tuple[list[dict[str, Any]], str | None]:
    try:
        from db_common import get_connection
        from news_db_modules.bootstrap import initialize_database as initialize_news_database

        initialize_news_database()
        with closing(get_connection()) as conn, conn:
            rows = conn.execute(
                """
                SELECT event_name, event_date, event_time, country, currency, importance,
                       scheduled_at_utc, calendar_timezone, calendar_refreshed_at
                FROM macro_events
                WHERE scheduled_at_utc IS NOT NULL OR event_date IS NOT NULL
                """
            ).fetchall()
        events = [dict(row) for row in rows]
        refreshed_at = max((str(event.get("calendar_refreshed_at") or "") for event in events), default="")
        return events, refreshed_at or None
    except Exception as exc:  # pragma: no cover - database failures must fail closed, not crash the cycle
        LOGGER.warning("Unable to load macro calendar for market regime: %s", exc)
        return [], None


def _macro_calendar_is_fresh(
    refreshed_at: str | None,
    *,
    as_of: datetime,
    policy: MarketRegimeSnapshotPolicy,
) -> bool:
    refreshed = _parse_datetime(refreshed_at)
    if refreshed is None:
        return False
    age_minutes = (as_of - refreshed).total_seconds() / 60.0
    return 0 <= age_minutes <= policy.max_macro_calendar_age_minutes


def refresh_macro_calendar_for_market_regime() -> dict[str, Any]:
    """Refresh the dated macro-calendar input once before a regime snapshot.

    The live agent cycle usually runs from existing database targets, so it does
    not otherwise execute the standalone macro-calendar ingestion workflow.
    Keep this integration here, at the cycle-level market service, rather than
    refreshing once per company recommendation.
    """
    webscraping_dir = Path(__file__).resolve().parents[1] / "Webscraping"
    normalized_webscraping_dir = str(webscraping_dir)

    try:
        from pipelines.macroNewsPipeline import ingest_macro_and_news

        result = ingest_macro_and_news()
        event_count = int((result or {}).get("macro_events") or 0)
        if event_count < 1:
            return {
                "refreshed": False,
                "macro_event_count": 0,
                "reason_code": "macro_calendar_refresh_returned_no_events",
            }
        return {"refreshed": True, "macro_event_count": event_count}
    except Exception as exc:  # pragma: no cover - a failed refresh must preserve fail-closed classification
        LOGGER.warning("Unable to refresh macro calendar for market regime: %s", exc)
        return {
            "refreshed": False,
            "macro_event_count": 0,
            "reason_code": "macro_calendar_refresh_failed",
        }


def _fetch_closes(symbol: str) -> tuple[list[float], str | None]:
    return refresh_regime_history(symbol)


def build_market_regime_snapshot(
    *,
    as_of: datetime | None = None,
    policy: MarketRegimeSnapshotPolicy | None = None,
    persist: bool = True,
    refresh_macro_calendar: bool = True,
) -> dict[str, Any]:
    """Build exactly one market-wide snapshot for a strategist/manager cycle."""
    active_policy = policy or MarketRegimeSnapshotPolicy.from_env()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    macro_events, macro_refreshed_at = _load_macro_calendar()
    calendar_is_fresh = _macro_calendar_is_fresh(
        macro_refreshed_at,
        as_of=now,
        policy=active_policy,
    )
    if refresh_macro_calendar and not calendar_is_fresh:
        macro_refresh = refresh_macro_calendar_for_market_regime()
        macro_events, macro_refreshed_at = _load_macro_calendar()
    else:
        macro_refresh = {
            "refreshed": False,
            "macro_event_count": len(macro_events),
            "skipped_reason": "macro_calendar_already_fresh" if calendar_is_fresh else "refresh_disabled",
        }
    spy_closes, spy_timestamp = _fetch_closes("SPY")
    vix_closes, vix_timestamp = _fetch_closes("VIX")
    vix_at = _parse_datetime(vix_timestamp)
    vix_fresh = vix_at is not None and 0 <= (now - vix_at).total_seconds() <= active_policy.max_market_data_age_seconds
    if not vix_fresh:
        vix_closes = []
    snapshot = classify_market_regime(
        spy_closes=spy_closes,
        spy_last_timestamp=spy_timestamp,
        vix_closes=vix_closes,
        macro_events=macro_events,
        macro_refreshed_at=macro_refreshed_at,
        as_of=now,
        policy=active_policy,
    )
    snapshot["input_freshness"]["vix_last_timestamp"] = vix_timestamp
    snapshot["input_freshness"]["vix_history_count"] = len(vix_closes)
    if not vix_fresh or len(vix_closes) < 252:
        snapshot["reason_codes"].append("vix_history_missing_stale_or_insufficient")
    snapshot["macro_calendar_refresh"] = macro_refresh
    refresh_reason = macro_refresh.get("reason_code")
    if refresh_reason:
        snapshot["reason_codes"] = list(dict.fromkeys([*snapshot["reason_codes"], refresh_reason]))
    if persist:
        try:
            from market_db_modules.market_regime import record_market_regime_snapshot

            snapshot["market_regime_run_id"] = record_market_regime_snapshot(snapshot)
        except Exception as exc:  # pragma: no cover - fail closed snapshot remains usable if audit storage is unavailable
            LOGGER.exception("Unable to persist market regime snapshot: %s", exc)
            snapshot["reason_codes"] = list(dict.fromkeys([*snapshot["reason_codes"], "regime_persistence_failed"]))
    return snapshot
