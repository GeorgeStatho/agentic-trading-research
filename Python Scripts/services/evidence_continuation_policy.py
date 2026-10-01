"""Thresholds and rollout mode for the deterministic swing-continuation entry gate."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

from services.research_promotion import guard_policy


POLICY_VERSION = "evidence-continuation-v2-provenance"
OVEREXTENSION_LEVELS = {"low", "medium", "high"}
MODES = {"enforce", "shadow"}


@dataclass(frozen=True)
class EvidenceContinuationPolicy:
    enabled: bool = True
    required_evidence_count: int = 2
    required_independent_sources: int = 2
    min_evidence_score: float = 0.65
    require_sector_outperformance: bool = True
    require_stock_relative_strength: bool = True
    min_relative_volume: float = 1.25
    max_overextension: str = "medium"
    allowed_call_entry_quality: frozenset[str] = frozenset({"good", "excellent"})
    allowed_put_entry_quality: frozenset[str] = frozenset({"good", "excellent"})
    fail_closed_on_missing_data: bool = True
    enforcement_mode: str = "shadow"
    policy_version: str = POLICY_VERSION

    @classmethod
    def from_env(cls) -> "EvidenceContinuationPolicy":
        return cls.from_environ(os.environ)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "EvidenceContinuationPolicy":
        def text(name: str, default: str) -> str:
            return str(environ.get(name, default) or "").strip().lower()

        def flag(name: str, default: bool) -> bool:
            return text(name, str(default)) in {"1", "true", "yes", "on"}

        def positive_int(name: str, default: int) -> int:
            try:
                return max(1, int(environ.get(name, str(default))))
            except ValueError:
                return default

        def positive_float(name: str, default: float) -> float:
            try:
                return max(0.0, float(environ.get(name, str(default))))
            except ValueError:
                return default

        def qualities(name: str, default: frozenset[str]) -> frozenset[str]:
            values = {str(value or "").strip().lower() for value in environ.get(name, ",".join(sorted(default))).split(",")}
            return frozenset(value for value in values if value) or default

        max_overextension = text("EVIDENCE_CONTINUATION_MAX_OVEREXTENSION", "medium")
        enforcement_mode = text("EVIDENCE_CONTINUATION_MODE", "shadow")
        policy = cls(
            enabled=flag("EVIDENCE_CONTINUATION_ENABLED", True),
            required_evidence_count=positive_int("EVIDENCE_CONTINUATION_REQUIRED_EVIDENCE_COUNT", 2),
            required_independent_sources=positive_int("EVIDENCE_CONTINUATION_REQUIRED_INDEPENDENT_SOURCES", 2),
            min_evidence_score=positive_float("EVIDENCE_CONTINUATION_MIN_EVIDENCE_SCORE", 0.65),
            require_sector_outperformance=flag("EVIDENCE_CONTINUATION_REQUIRE_SECTOR_OUTPERFORMANCE", True),
            require_stock_relative_strength=flag("EVIDENCE_CONTINUATION_REQUIRE_STOCK_RELATIVE_STRENGTH", True),
            min_relative_volume=positive_float("EVIDENCE_CONTINUATION_MIN_RELATIVE_VOLUME", 1.25),
            max_overextension=max_overextension if max_overextension in OVEREXTENSION_LEVELS else "medium",
            allowed_call_entry_quality=qualities("EVIDENCE_CONTINUATION_ALLOWED_CALL_QUALITIES", frozenset({"good", "excellent"})),
            allowed_put_entry_quality=qualities("EVIDENCE_CONTINUATION_ALLOWED_PUT_QUALITIES", frozenset({"good", "excellent"})),
            fail_closed_on_missing_data=flag("EVIDENCE_CONTINUATION_FAIL_CLOSED_ON_MISSING_DATA", True),
            enforcement_mode=enforcement_mode if enforcement_mode in MODES else "shadow",
        )
        return guard_policy(policy, {"enforcement_mode": "CONTINUATION"})
