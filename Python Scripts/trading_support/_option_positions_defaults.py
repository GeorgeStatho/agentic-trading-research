from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from services.common import env_float, env_positive_int
from services.config import OptionPositionSettings
from services.option_dte_buckets import OPTION_DTE_BUCKETS


@dataclass(frozen=True)
class OptionExitRule:
    label: str
    min_days_to_expiration: int
    max_days_to_expiration: int
    take_profit_pct: float
    stop_loss_pct: float
    force_exit_days_to_expiration: int | None


@dataclass(frozen=True)
class ExitThresholds:
    dte_rule_label: str
    take_profit_pct: float
    stop_loss_pct: float
    force_exit_days_to_expiration: int | None
    exit_hours_to_expiration: float
    is_default_rule: bool


@dataclass(frozen=True)
class TrailingProfitConfig:
    protection_trigger_pct: float
    initial_floor_pct: float
    dynamic_giveback_start_pct: float
    first_scale_out_trigger_pct: float
    first_scale_out_fraction: float
    second_scale_out_trigger_pct: float
    second_scale_out_fraction: float
    giveback_pct_by_bucket_key: dict[str, float]
    floor_100_pct: float
    floor_150_pct: float
    floor_200_pct: float
    enable_momentum_exit: bool


@dataclass(frozen=True)
class MomentumHistoryConfig:
    enable_after_pnl_pct: float
    window_size: int
    min_samples: int
    bad_count_exit_threshold: int
    consecutive_bad_exit_threshold: int


@dataclass(frozen=True)
class OptionPriceMomentumConfig:
    bad_giveback_threshold: float
    mixed_giveback_threshold: float
    good_profit_threshold: float


@dataclass(frozen=True)
class PendingExitConfig:
    stale_minutes: float
    cancel_on_stale: bool


OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
OPTION_SYMBOL_PATTERN = re.compile(
    r"^(?P<underlying>[A-Z0-9]{1,8})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<contract_type>[CP])(?P<strike>\d{8})$"
)

_OPTION_POSITION_SETTINGS = OptionPositionSettings.from_env()
DEFAULT_OPTION_TAKE_PROFIT_PCT = _OPTION_POSITION_SETTINGS.take_profit_pct
DEFAULT_OPTION_STOP_LOSS_PCT = _OPTION_POSITION_SETTINGS.stop_loss_pct
DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION = _OPTION_POSITION_SETTINGS.exit_hours_to_expiration
# Number of consecutive manager cycles the broker P/L must stay at or below the
# stop-loss floor before a full exit fires. 1 preserves the legacy behavior of
# selling on the first breach; higher values ride out momentary dips that bounce
# back (option premium is leveraged and spread-noisy, so a small underlying wobble
# can briefly cross the floor and recover).
DEFAULT_OPTION_STOP_LOSS_CONFIRM_CHECKS = env_positive_int(
    "OPTION_POSITION_STOP_LOSS_CONFIRM_CHECKS",
    1,
)
# Minimum adverse move in the UNDERLYING (as a fraction of the entry underlying
# price) required before a broker-P/L stop-loss breach is treated as real. 0.0
# disables the check and keeps the legacy premium-only stop. A value like 0.015
# means "only stop a call once the stock is down >=1.5% from entry (or a put's
# underlying is up >=1.5%)", so IV crush / spread noise on the option premium can
# no longer force an exit while the underlying itself is still fine. When the
# underlying baseline is unavailable or untrusted the premium stop still applies.
DEFAULT_OPTION_STOP_LOSS_UNDERLYING_MOVE_PCT = env_float(
    "OPTION_POSITION_STOP_LOSS_UNDERLYING_MOVE_PCT",
    0.0,
)
# When enabled, exits are submitted as limit orders priced off the live option
# quote (passive at the midpoint to capture spread; marketable at the bid for
# urgent or already-retried exits) instead of crossing the whole spread with a
# market order. Disabled by default so behavior is unchanged until opted in.
DEFAULT_OPTION_EXIT_USE_LIMIT_ORDERS = str(
    os.getenv("OPTION_POSITION_EXIT_USE_LIMIT_ORDERS", "false")
).strip().lower() not in {"0", "false", "no", "off"}
# Liquidity guard: when > 0, a non-urgent exit (scale-out, momentum, trailing
# stop) is held for another cycle if the option's bid/ask spread exceeds this
# fraction of the midpoint, rather than paying up into a blown-out book. Hard
# risk exits (stop-loss, near-expiration) always bypass the guard. 0 disables it.
DEFAULT_OPTION_EXIT_MAX_SPREAD_PCT = env_float("OPTION_POSITION_EXIT_MAX_SPREAD_PCT", 0.0)
# Tick sizes for rounding limit-exit prices to a broker-valid increment. Defaults
# to the OCC standard (a nickel below $3, a dime at/above), which is valid for any
# option class; set both to 0.01 for penny-quoted names to keep full precision.
# Sell limits round DOWN to the tick, so they stay valid and marketable.
DEFAULT_OPTION_EXIT_TICK_SIZE_SMALL = env_float("OPTION_POSITION_EXIT_TICK_SIZE_SMALL", 0.05)
DEFAULT_OPTION_EXIT_TICK_SIZE_LARGE = env_float("OPTION_POSITION_EXIT_TICK_SIZE_LARGE", 0.10)
DEFAULT_OPTION_EXIT_TICK_PRICE_THRESHOLD = env_float("OPTION_POSITION_EXIT_TICK_PRICE_THRESHOLD", 3.00)
# Volatility-scaled exit thresholds: when enabled, the stop-loss, take-profit, and
# underlying-move gate are multiplied by the underlying's ATR relative to a
# baseline, so a fixed -25% stop widens on a high-vol name and tightens on a calm
# one. Disabled by default; falls back to fixed thresholds (scale 1.0) whenever the
# ATR cannot be measured.
DEFAULT_OPTION_VOLATILITY_SCALED_THRESHOLDS = str(
    os.getenv("OPTION_POSITION_VOLATILITY_SCALED_THRESHOLDS", "false")
).strip().lower() not in {"0", "false", "no", "off"}
# The underlying daily-ATR-as-fraction-of-price that maps to a 1.0x scale. A name
# whose ATR is this fraction of its price gets its thresholds unchanged; twice as
# volatile -> ~2x (subject to the min/max clamp).
DEFAULT_OPTION_VOLATILITY_BASELINE_ATR_PCT = env_float("OPTION_POSITION_VOLATILITY_BASELINE_ATR_PCT", 0.02)
DEFAULT_OPTION_VOLATILITY_MIN_SCALE = env_float("OPTION_POSITION_VOLATILITY_MIN_SCALE", 0.5)
DEFAULT_OPTION_VOLATILITY_MAX_SCALE = env_float("OPTION_POSITION_VOLATILITY_MAX_SCALE", 2.0)
DEFAULT_OPTION_VOLATILITY_ATR_PERIOD = env_positive_int("OPTION_POSITION_VOLATILITY_ATR_PERIOD", 14)
DEFAULT_OPTION_VOLATILITY_ATR_LOOKBACK_DAYS = env_positive_int("OPTION_POSITION_VOLATILITY_ATR_LOOKBACK_DAYS", 40)
DEFAULT_OPTION_POSITION_STATE_PATH = Path(
    os.getenv("OPTION_POSITION_STATE_PATH", "/shared/option_position_state.json")
)
DEFAULT_OPTION_POSITION_ENABLE_TRAILING_PROFIT = str(
    os.getenv("OPTION_POSITION_ENABLE_TRAILING_PROFIT", "false")
).strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_OPTION_POSITION_TRAILING_PROFIT_DRY_RUN = str(
    os.getenv("OPTION_POSITION_TRAILING_PROFIT_DRY_RUN", "false")
).strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_OPTION_POSITION_ENABLE_MOMENTUM_EXIT = str(
    os.getenv("OPTION_POSITION_ENABLE_MOMENTUM_EXIT", "false")
).strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT = env_float(
    "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT",
    2.00,
)
DEFAULT_OPTION_MOMENTUM_HISTORY_WINDOW_SIZE = env_positive_int(
    "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE",
    10,
)
DEFAULT_OPTION_MOMENTUM_HISTORY_MIN_SAMPLES = env_positive_int(
    "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES",
    5,
)
DEFAULT_OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD = env_positive_int(
    "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD",
    6,
)
DEFAULT_OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD = env_positive_int(
    "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD",
    3,
)
DEFAULT_OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD = env_float(
    "OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD",
    0.35,
)
DEFAULT_OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD = env_float(
    "OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD",
    0.15,
)
DEFAULT_OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD = env_float(
    "OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD",
    0.10,
)
DEFAULT_OPTION_TRAIL_PROTECTION_TRIGGER_PCT = env_float("OPTION_TRAIL_PROTECTION_TRIGGER_PCT", 0.40)
DEFAULT_OPTION_TRAIL_INITIAL_FLOOR_PCT = env_float("OPTION_TRAIL_INITIAL_FLOOR_PCT", 0.05)
DEFAULT_OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT = env_float(
    "OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT",
    0.75,
)
DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT = env_float(
    "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT",
    0.55,
)
DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION = env_float(
    "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION",
    0.50,
)
DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT = env_float(
    "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT",
    2.00,
)
DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION = env_float(
    "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION",
    0.25,
)
DEFAULT_OPTION_TRAIL_3_7_GIVEBACK_PCT = env_float("OPTION_TRAIL_3_7_GIVEBACK_PCT", 0.25)


def _load_default_option_trailing_giveback_pct_by_bucket_key() -> dict[str, float]:
    defaults: dict[str, float] = {}
    for bucket in OPTION_DTE_BUCKETS:
        env_name = f"OPTION_TRAIL_{bucket.key.upper()}_GIVEBACK_PCT"
        fallback = (
            float(bucket.default_trailing_giveback_pct)
            if bucket.default_trailing_giveback_pct is not None
            else DEFAULT_OPTION_TRAIL_3_7_GIVEBACK_PCT
        )
        defaults[bucket.key] = env_float(env_name, fallback)
    return defaults


DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY = _load_default_option_trailing_giveback_pct_by_bucket_key()
DEFAULT_OPTION_TRAIL_7_14_GIVEBACK_PCT = DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY.get("7_14", 0.35)
DEFAULT_OPTION_TRAIL_14_30_GIVEBACK_PCT = DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY.get("14_30", 0.45)
DEFAULT_OPTION_TRAIL_30_45_GIVEBACK_PCT = DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY.get("30_45", 0.45)
DEFAULT_OPTION_TRAIL_45_60_GIVEBACK_PCT = DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY.get("45_60", 0.45)
DEFAULT_OPTION_TRAIL_100_FLOOR_PCT = env_float("OPTION_TRAIL_100_FLOOR_PCT", 0.60)
DEFAULT_OPTION_TRAIL_150_FLOOR_PCT = env_float("OPTION_TRAIL_150_FLOOR_PCT", 1.00)
DEFAULT_OPTION_TRAIL_200_FLOOR_PCT = env_float("OPTION_TRAIL_200_FLOOR_PCT", 1.40)
DEFAULT_OPTION_PENDING_EXIT_STALE_MINUTES = env_float("OPTION_PENDING_EXIT_STALE_MINUTES", 10.0)
DEFAULT_OPTION_PENDING_EXIT_CANCEL_ON_STALE = str(
    os.getenv("OPTION_PENDING_EXIT_CANCEL_ON_STALE", "true")
).strip().lower() not in {"0", "false", "no", "off"}

_OPTION_EXIT_RULE_SETTINGS_BY_KEY = {
    rule.key: rule for rule in _OPTION_POSITION_SETTINGS.dte_rules
}

OPTION_EXIT_DTE_RULES = tuple(
    OptionExitRule(
        label=bucket.label,
        min_days_to_expiration=bucket.min_days,
        max_days_to_expiration=bucket.max_days,
        take_profit_pct=_OPTION_EXIT_RULE_SETTINGS_BY_KEY[bucket.key].take_profit_pct,
        stop_loss_pct=_OPTION_EXIT_RULE_SETTINGS_BY_KEY[bucket.key].stop_loss_pct,
        force_exit_days_to_expiration=_OPTION_EXIT_RULE_SETTINGS_BY_KEY[
            bucket.key
        ].force_exit_days_to_expiration,
    )
    for bucket in OPTION_DTE_BUCKETS
    if bucket.key in _OPTION_EXIT_RULE_SETTINGS_BY_KEY
)

MARKET_TIMEZONE = ZoneInfo("America/New_York")
OPTION_EXPIRATION_MARKET_CLOSE_HOUR = 16
PENDING_ORDER_OPEN_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "calculated",
    "held",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_new",
    "pending_replace",
    "pending_review",
    "stopped",
    "suspended",
}
PENDING_ORDER_CLEAR_STATUSES = {
    "canceled",
    "cancelled",
    "done_for_day",
    "expired",
    "filled",
    "rejected",
}
