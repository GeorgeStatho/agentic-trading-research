from __future__ import annotations

import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from services.core.trading_config import OptionExitRuleConfig
from services.options.option_dte_buckets import OPTION_DTE_BUCKETS


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


OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
OPTION_SYMBOL_PATTERN = re.compile(
    r"^(?P<underlying>[A-Z0-9]{1,8})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<contract_type>[CP])(?P<strike>\d{8})$"
)


def build_option_exit_rules(dte_rules: tuple[OptionExitRuleConfig, ...]) -> tuple[OptionExitRule, ...]:
    """Pair each DTE bucket's day range with its configured exit thresholds."""
    rules_by_key = {rule.key: rule for rule in dte_rules}
    return tuple(
        OptionExitRule(
            label=bucket.label,
            min_days_to_expiration=bucket.min_days,
            max_days_to_expiration=bucket.max_days,
            take_profit_pct=rules_by_key[bucket.key].take_profit_pct,
            stop_loss_pct=rules_by_key[bucket.key].stop_loss_pct,
            force_exit_days_to_expiration=rules_by_key[bucket.key].force_exit_days_to_expiration,
        )
        for bucket in OPTION_DTE_BUCKETS
        if bucket.key in rules_by_key
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
