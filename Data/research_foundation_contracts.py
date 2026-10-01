"""Versioned, provider-independent records for the evidence research foundation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Literal


SCHEMA_VERSION = "research-foundation-v1"
OBSERVATION_PROTOCOL = "company-evidence-v1"
# A production decision's frozen inputs (Phase 7); never paired with discovery observations.
DECISION_OBSERVATION_PROTOCOL = "decision-observation-v1"


def timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Research timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def require_text(*values: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Required research text is empty")


def nonnegative(*values: float) -> None:
    if any(isinstance(value, bool) or not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Research amounts must be finite and nonnegative")


@dataclass(frozen=True, kw_only=True)
class EvidenceDocument:
    document_id: str
    provider: str
    source_url: str
    publisher: str
    document_type: str
    text: str
    published_at: str
    first_observed_at: str
    ingested_at: str
    supersedes_document_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        require_text(self.document_id, self.provider, self.source_url, self.publisher,
                     self.document_type, self.text)
        times = [timestamp(value) for value in
                 (self.published_at, self.first_observed_at, self.ingested_at)]
        if times != sorted(times):
            raise ValueError("Document publication, observation and ingestion are out of order")
        if self.supersedes_document_id == self.document_id:
            raise ValueError("Document cannot supersede itself")


@dataclass(frozen=True, kw_only=True)
class CompanyFact:
    fact_id: str
    company_id: int
    metric: str
    kind: Literal["actual", "prior_guidance", "guidance", "consensus", "derived"]
    value: float | None
    unit: str
    fiscal_period: str
    period_type: str
    accounting_basis: str
    document_id: str
    source_start: int
    source_end: int
    available_at: str
    extraction_version: str
    currency: str | None = None
    scale: float = 1
    range_high: float | None = None
    unknown_reason: str | None = None
    supersedes_fact_id: str | None = None
    calculation: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        require_text(self.fact_id, self.metric, self.unit, self.fiscal_period,
                     self.period_type, self.accounting_basis, self.document_id,
                     self.extraction_version)
        timestamp(self.available_at)
        if self.kind not in {"actual", "prior_guidance", "guidance", "consensus", "derived"}:
            raise ValueError("Unknown fact kind")
        if self.company_id <= 0 or not 0 <= self.source_start < self.source_end:
            raise ValueError("Invalid company or source span")
        if self.value is None and not self.unknown_reason:
            raise ValueError("Unknown facts need a reason")
        if any(value is not None and (isinstance(value, bool) or not math.isfinite(value))
               for value in (self.value, self.range_high, self.scale)):
            raise ValueError("Fact values must be finite")
        if self.scale <= 0 or (self.range_high is not None and
                              (self.value is None or self.range_high < self.value)):
            raise ValueError("Invalid scale or fact range")
        if self.kind == "derived" and not self.calculation:
            raise ValueError("Derived facts need calculation inputs and formula")
        if self.supersedes_fact_id == self.fact_id:
            raise ValueError("Fact cannot supersede itself")


@dataclass(frozen=True, kw_only=True)
class EvidencePackage:
    package_id: str
    company_id: int
    catalyst_id: str
    event_revision: str
    information_cutoff_time: str
    document_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    supporting_claims: tuple[dict[str, Any], ...] = ()
    opposing_claims: tuple[dict[str, Any], ...] = ()
    gaps: tuple[str, ...] = ()
    coverage_status: Literal["complete", "partial", "unavailable"] = "partial"
    freshness_status: Literal["fresh", "stale", "unknown"] = "unknown"
    scheduled_events: tuple[dict[str, Any], ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        require_text(self.package_id, self.catalyst_id, self.event_revision)
        timestamp(self.information_cutoff_time)
        if self.company_id <= 0 or self.coverage_status not in {"complete", "partial", "unavailable"}:
            raise ValueError("Invalid evidence package")
        if self.freshness_status not in {"fresh", "stale", "unknown"}:
            raise ValueError("Invalid evidence freshness")
        if len(set(self.document_ids)) != len(self.document_ids) or len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("Duplicate evidence references")


@dataclass(frozen=True, kw_only=True)
class DiscoveryQueueItem:
    company_id: int
    catalyst_id: str
    evidence_revision: str
    purpose: Literal["entry", "watchlist", "position_review"]
    origin: str
    created_at: str
    priority_components: dict[str, float] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        require_text(self.catalyst_id, self.evidence_revision, self.origin)
        timestamp(self.created_at)
        if self.company_id <= 0 or self.purpose not in {"entry", "watchlist", "position_review"}:
            raise ValueError("Invalid discovery item")
        nonnegative(*self.priority_components.values())


@dataclass(frozen=True, kw_only=True)
class ResearchSession:
    session_id: str
    queue_id: str
    initial_package_id: str
    started_at: str
    deadline_at: str
    model: str
    prompt_version: str
    status: str = "pending"
    calls_consumed: int = 0
    cost_consumed: float = 0
    final_package_id: str | None = None
    stop_reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        require_text(self.session_id, self.queue_id, self.initial_package_id,
                     self.model, self.prompt_version)
        if timestamp(self.deadline_at) < timestamp(self.started_at):
            raise ValueError("Research deadline precedes start")
        if self.status not in {"pending", "running", "ready", "deferred_missing_evidence",
                               "conflicting_evidence", "budget_exhausted", "superseded", "failed"}:
            raise ValueError("Invalid research status")
        nonnegative(self.calls_consumed, self.cost_consumed)


@dataclass(frozen=True, kw_only=True)
class ResearchObservation:
    company_id: int
    symbol: str
    catalyst_id: str
    event_revision: str
    information_cutoff_time: str
    source_snapshot: dict[str, Any]
    protocol_version: str = OBSERVATION_PROTOCOL
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        require_text(self.symbol, self.catalyst_id, self.event_revision, self.protocol_version)
        timestamp(self.information_cutoff_time)
        if self.company_id <= 0 or self.symbol != self.symbol.strip().upper():
            raise ValueError("Observation needs a company and normalized symbol")
        if not isinstance(self.source_snapshot, dict) or not self.source_snapshot:
            raise ValueError("Observation needs a frozen source snapshot")
