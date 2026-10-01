"""Normalized retrieval records and the optional consensus integration boundary."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SourceDocument:
    provider: str
    url: str
    publisher: str
    document_type: str
    text: str
    published_at: str
    observed_at: str
    event_key: str
    metadata: dict = field(default_factory=dict)
    facts: tuple[dict, ...] = ()
    scheduled_events: tuple[dict, ...] = ()
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectionResult:
    documents: tuple[SourceDocument, ...]
    cursor: dict
    gaps: tuple[str, ...] = ()


class ConsensusAdapter(Protocol):
    """Licensed integrations must return dated, source-backed consensus facts."""
    name: str

    def collect(self, company: dict, cursor: dict) -> CollectionResult: ...
