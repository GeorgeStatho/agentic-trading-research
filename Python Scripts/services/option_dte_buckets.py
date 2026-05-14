from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


@dataclass(frozen=True)
class DteBucket:
    key: str
    label: str
    min_days: int
    max_days: int
    target_otm_pct: float | None = None
    force_exit_days_to_expiration: int | None = None
    default_take_profit_pct: float | None = None
    default_stop_loss_pct: float | None = None
    mapped_time_horizon: str | None = None


OPTION_DTE_BUCKETS = (
    DteBucket(
    "7_14",
    "7-14 DTE",
    7,
    14,
    target_otm_pct=0.0075,
    force_exit_days_to_expiration=4,
    default_take_profit_pct=35.0,
    default_stop_loss_pct=-25.0,
    mapped_time_horizon="very_short_term",
    ),
    DteBucket(
        "14_30",
        "14-30 DTE",
        14,
        30,
        target_otm_pct=0.0125,
        force_exit_days_to_expiration=9,
        default_take_profit_pct=55.0,
        default_stop_loss_pct=-30.0,
        mapped_time_horizon="short_term",
    ),
    DteBucket(
        "30_45",
        "30-45 DTE",
        30,
        45,
        target_otm_pct=0.020,
        force_exit_days_to_expiration=18,
        default_take_profit_pct=70.0,
        default_stop_loss_pct=-35.0,
        mapped_time_horizon="medium_term",
    ),
    DteBucket(
        "45_60",
        "45-60 DTE",
        45,
        60,
        target_otm_pct=0.025,
        force_exit_days_to_expiration=25,
        default_take_profit_pct=85.0,
        default_stop_loss_pct=-40.0,
        mapped_time_horizon="longer_term",
    ),
)

ORDERED_OPTION_DTE_BUCKET_KEYS = tuple(bucket.key for bucket in OPTION_DTE_BUCKETS)
ORDERED_OPTION_DTE_BUCKET_KEYS_WITH_NONE = (*ORDERED_OPTION_DTE_BUCKET_KEYS, "none")
VALID_OPTION_DTE_BUCKET_KEYS = frozenset(ORDERED_OPTION_DTE_BUCKET_KEYS)
VALID_OPTION_DTE_BUCKET_KEYS_WITH_NONE = frozenset(ORDERED_OPTION_DTE_BUCKET_KEYS_WITH_NONE)
OPTION_DTE_BUCKET_BY_KEY = {bucket.key: bucket for bucket in OPTION_DTE_BUCKETS}
DTE_BUCKET_TO_TARGET_OTM_PCT = {
    bucket.key: bucket.target_otm_pct
    for bucket in OPTION_DTE_BUCKETS
    if bucket.target_otm_pct is not None
}
TIME_HORIZON_TO_DTE_BUCKET = {
    bucket.mapped_time_horizon: bucket.key
    for bucket in OPTION_DTE_BUCKETS
    if bucket.mapped_time_horizon
}
DEFAULT_MANAGER_TARGET_DTE_BUCKET = (
    TIME_HORIZON_TO_DTE_BUCKET.get("medium_term")
    or (OPTION_DTE_BUCKETS[-1].key if OPTION_DTE_BUCKETS else "none")
)

_DTE_BUCKET_ALIASES = {
    "n/a": "none",
    "na": "none",
    "not_applicable": "none",
    "none": "none",
}

for bucket in OPTION_DTE_BUCKETS:
    normalized_label = bucket.label.strip().lower()
    range_dash = f"{bucket.min_days}-{bucket.max_days}"
    range_to = f"{bucket.min_days} to {bucket.max_days}"
    _DTE_BUCKET_ALIASES.update(
        {
            bucket.key.lower(): bucket.key,
            range_dash: bucket.key,
            range_to: bucket.key,
            normalized_label: bucket.key,
            normalized_label.replace(" dte", ""): bucket.key,
        }
    )


def normalize_target_dte_bucket(value: Any) -> str:
    text = str(value or "").strip().lower()
    normalized = _DTE_BUCKET_ALIASES.get(text, text)
    return normalized if normalized in VALID_OPTION_DTE_BUCKET_KEYS_WITH_NONE else ""


def get_all_target_dte_bucket_aliases(*, include_none: bool = True) -> tuple[str, ...]:
    aliases: list[str] = []
    seen: set[str] = set()
    for alias, canonical in _DTE_BUCKET_ALIASES.items():
        if canonical == "none" and not include_none:
            continue
        if alias in seen:
            continue
        aliases.append(alias)
        seen.add(alias)
    return tuple(aliases)


def get_target_dte_bucket_choices_text(*, include_none: bool = True, separator: str = "|") -> str:
    keys = ORDERED_OPTION_DTE_BUCKET_KEYS_WITH_NONE if include_none else ORDERED_OPTION_DTE_BUCKET_KEYS
    return separator.join(keys)


def get_time_horizon_mapping_text() -> str:
    parts = [
        f"{time_horizon} -> {bucket_key}"
        for time_horizon, bucket_key in TIME_HORIZON_TO_DTE_BUCKET.items()
    ]
    return ", ".join(parts)


def get_dte_bucket(bucket: Any) -> DteBucket | None:
    normalized_bucket = normalize_target_dte_bucket(bucket)
    if normalized_bucket in {"", "none"}:
        return None
    return OPTION_DTE_BUCKET_BY_KEY.get(normalized_bucket)


def get_dte_bucket_range(bucket: Any) -> tuple[int, int] | None:
    resolved_bucket = get_dte_bucket(bucket)
    if resolved_bucket is None:
        return None
    return resolved_bucket.min_days, resolved_bucket.max_days


def get_bucket_target_otm_pct(bucket: Any) -> float | None:
    resolved_bucket = get_dte_bucket(bucket)
    if resolved_bucket is None:
        return None
    return resolved_bucket.target_otm_pct


def resolve_dte_bucket_for_days(
    days_to_expiration: int | None,
    *,
    prefer_higher_boundary: bool = False,
) -> DteBucket | None:
    if days_to_expiration is None:
        return None

    buckets = reversed(OPTION_DTE_BUCKETS) if prefer_higher_boundary else OPTION_DTE_BUCKETS
    for bucket in buckets:
        if bucket.min_days <= days_to_expiration <= bucket.max_days:
            return bucket
    return None


def target_dte_bucket_to_expiration_filters(
    target_dte_bucket: Any,
    *,
    today: date | None = None,
) -> tuple[str | None, str | None, str | None]:
    bucket_range = get_dte_bucket_range(target_dte_bucket)
    if bucket_range is None:
        return None, None, None

    current_date = today or date.today()
    min_days, max_days = bucket_range
    return (
        None,
        (current_date + timedelta(days=min_days)).isoformat(),
        (current_date + timedelta(days=max_days)).isoformat(),
    )
