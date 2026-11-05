"""Model knowledge cutoffs: which frozen decisions a model may remember rather than predict.

A decision is eligible for a model only if it was made more than `CONTAMINATION_MARGIN_DAYS` days
after the model's knowledge cutoff, counted in whole UTC days. A model missing from the table has
an unknown cutoff, which callers must refuse unless told otherwise.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CUTOFFS_CSV = Path(__file__).resolve().parents[3] / "config" / "model_knowledge_cutoffs.csv"
CONTAMINATION_MARGIN_DAYS = 30


def cutoff_record(model: str) -> tuple[date, str] | None:
    """`(knowledge_cutoff, source)` for the model id, ignoring case; None if unlisted.

    Exact ids only: a family prefix would give a newer model an older cutoff and admit decisions it may remember.
    """
    with CUTOFFS_CSV.open(newline="", encoding="utf-8") as handle:
        table = {row["model"].strip().casefold(): (date.fromisoformat(row["knowledge_cutoff"]), row["source"])
                 for row in csv.DictReader(handle)}
    return table.get(model.strip().casefold())


def knowledge_cutoff(model: str) -> date | None:
    record = cutoff_record(model)
    return record[0] if record else None


def eligibility(decision_run_at: str, cutoff: date | None) -> str:
    """'eligible', 'pre_knowledge_cutoff' or 'cutoff_unknown' for a decision against a model's known cutoff."""
    if cutoff is None:
        return "cutoff_unknown"
    decided = datetime.fromisoformat(decision_run_at)
    decided_on = (decided.astimezone(timezone.utc) if decided.tzinfo else decided).date()
    return "eligible" if decided_on > cutoff + timedelta(days=CONTAMINATION_MARGIN_DAYS) else "pre_knowledge_cutoff"


def eligible(decision_run_at: str, model: str) -> str:
    return eligibility(decision_run_at, knowledge_cutoff(model))
