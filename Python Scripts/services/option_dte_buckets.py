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


OPTION_DTE_BUCKETS = (
    DteBucket("3_7", "3-7 DTE", 3, 7, target_otm_pct=0.005, force_exit_days_to_expiration=1),
    DteBucket("7_14", "7-14 DTE", 7, 14, target_otm_pct=0.010, force_exit_days_to_expiration=3),
    DteBucket("14_30", "14-30 DTE", 14, 30, target_otm_pct=0.015, force_exit_days_to_expiration=7),
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
    "very_short_term": "3_7",
    "short_term": "7_14",
    "medium_term": "14_30",
}
_DTE_BUCKET_ALIASES = {
    "3-7": "3_7",
    "3 to 7": "3_7",
    "3_7": "3_7",
    "7-14": "7_14",
    "7 to 14": "7_14",
    "7_14": "7_14",
    "14-30": "14_30",
    "14 to 30": "14_30",
    "14_30": "14_30",
    "n/a": "none",
    "na": "none",
    "not_applicable": "none",
    "none": "none",
}


def normalize_target_dte_bucket(value: Any) -> str:
    text = str(value or "").strip().lower()
    normalized = _DTE_BUCKET_ALIASES.get(text, text)
    return normalized if normalized in VALID_OPTION_DTE_BUCKET_KEYS_WITH_NONE else ""


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
