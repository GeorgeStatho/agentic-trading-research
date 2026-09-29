"""Discovery is disabled or shadow; there is deliberately no live mode in Phase 2."""
from dataclasses import asdict, dataclass
import os


@dataclass(frozen=True)
class DiscoveryPolicy:
    mode: str = 'disabled'
    symbols: tuple[str, ...] = ()  # Legacy constructor compatibility; never selects companies.
    rescan_seconds: int = 300
    scan_limit: int = 20
    batch_size: int = 6
    direct_slots: int = 2
    review_slots: int = 2
    lease_seconds: int = 120
    max_attempts: int = 3
    retry_seconds: int = 60
    stale_seconds: int = 604800
    aging_per_hour: float = 1.0
    minimum_evidence_score: float = 0.6

    def __post_init__(self):
        if self.mode not in {'disabled', 'shadow'}:
            raise ValueError('Discovery supports only disabled or shadow mode')
        if min(self.rescan_seconds, self.scan_limit, self.batch_size, self.lease_seconds, self.max_attempts,
               self.retry_seconds, self.stale_seconds) < 1:
            raise ValueError('Discovery limits must be positive')
        if min(self.direct_slots, self.review_slots) < 0 or self.direct_slots + self.review_slots >= self.batch_size:
            raise ValueError('Discovery reservations must leave at least one general slot')
        if not 0 < self.aging_per_hour <= 100 or not 0 <= self.minimum_evidence_score <= 1:
            raise ValueError('Invalid discovery aging or evidence threshold')

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv('DISCOVERY_MODE', 'disabled').lower(),
                   rescan_seconds=int(os.getenv('DISCOVERY_RESCAN_SECONDS', '300')),
                   scan_limit=int(os.getenv('DISCOVERY_SCAN_LIMIT', '20')),
                   batch_size=int(os.getenv('DISCOVERY_BATCH_SIZE', '6')),
                   direct_slots=int(os.getenv('DISCOVERY_DIRECT_SLOTS', '2')),
                   review_slots=int(os.getenv('DISCOVERY_REVIEW_SLOTS', '2')),
                   lease_seconds=int(os.getenv('DISCOVERY_LEASE_SECONDS', '120')),
                   max_attempts=int(os.getenv('DISCOVERY_MAX_ATTEMPTS', '3')),
                   retry_seconds=int(os.getenv('DISCOVERY_RETRY_SECONDS', '60')),
                   stale_seconds=int(os.getenv('DISCOVERY_STALE_SECONDS', '604800')))

    def manifest(self):
        return asdict(self)
