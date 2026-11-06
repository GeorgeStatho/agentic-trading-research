"""Payloads for the read-only report routes, shaped for the dashboard's tables."""
from __future__ import annotations

from market_db_modules.decision_attribution_report import decision_attribution_report

ATTRIBUTION_HORIZONS = (5, 10)


def _groups(by_key: dict) -> list[dict]:
    # Keyed by data values ("weak", "high"); as rows they keep a fixed shape, largest first.
    rows = [{"key": key, "count": group["count"], "graded": group["graded"],
             "hit_rate": group["hit_rate"], "hit_rate_se": group["hit_rate_se"]} for key, group in by_key.items()]
    return sorted(rows, key=lambda row: -row["count"])


def decision_attribution_payload(*, days: int, horizon: int) -> dict:
    report = decision_attribution_report(days=days, horizon=horizon)
    failures = sorted(report["primary_failure_counts"].items(), key=lambda item: -item[1])
    return {
        "days": days, "horizon": horizon, "total": report["total"],
        "by_quality": _groups(report["matrix"]),
        "by_source": _groups(report["by_evidence_source"]),
        "by_self_rating": _groups(report["model_self_rating_calibration"]),
        "primary_failures": [{"key": key, "count": count} for key, count in failures],
    }
