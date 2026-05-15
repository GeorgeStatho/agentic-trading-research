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
DEFAULT_OPTION_TRAIL_INITIAL_FLOOR_PCT = env_float("OPTION_TRAIL_INITIAL_FLOOR_PCT", 0.10)
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
    "stopped",
}
PENDING_ORDER_CLEAR_STATUSES = {
    "canceled",
    "cancelled",
    "expired",
    "filled",
    "rejected",
}
