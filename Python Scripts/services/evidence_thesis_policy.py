"""Rollout controls for structured, claim-checked strategist theses."""
from dataclasses import dataclass
import os

from services.research_promotion import guard_policy


# v2: quotes may come from view-only articles' title and summary, which the model also sees.
POLICY_VERSION = "evidence-thesis-v2"
MODES = {"off", "shadow", "enforce"}


@dataclass(frozen=True)
class EvidenceThesisPolicy:
    mode: str = "shadow"
    max_horizon_days: int = 90
    daily_research_thesis_calls: int = 50
    policy_version: str = POLICY_VERSION

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError("EVIDENCE_THESIS_MODE must be off, shadow or enforce")
        for value in (self.max_horizon_days, self.daily_research_thesis_calls):
            if type(value) is not int or value < 1:
                raise ValueError("Evidence thesis limits must be positive integers")

    @property
    def requests_thesis(self):
        return self.mode != "off"

    @classmethod
    def from_env(cls):
        policy = cls(mode=os.getenv("EVIDENCE_THESIS_MODE", "shadow").strip().lower(),
                     daily_research_thesis_calls=int(os.getenv("RESEARCH_DAILY_THESIS_CALLS", "50")))
        return guard_policy(policy, {"mode": "EVIDENCE_THESIS"})
