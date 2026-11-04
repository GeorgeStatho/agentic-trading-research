"""Discovery is disabled or shadow; there is deliberately no live mode in Phase 2."""
from dataclasses import asdict, dataclass
import os


@dataclass(frozen=True)
class DiscoveryPolicy:
    mode: str = 'disabled'
    symbols: tuple[str, ...] = ()  # Legacy constructor compatibility; never selects companies.
    rescan_seconds: int = 300
    scan_limit: int = 20
    batch_size: int = 6  # research sessions and theses per collector cycle
    admission_batch_size: int = 20  # queue items admitted per trading cycle; no model calls
    direct_slots: int = 2
    lease_seconds: int = 120
    max_attempts: int = 3
    retry_seconds: int = 60
    stale_seconds: int = 604800
    aging_per_hour: float = 1.0
    minimum_evidence_score: float = 0.6
    # Market-wide SEC filings may add companies outside the ranking; 0 turns the feed off.
    market_event_limit: int = 0
    market_event_min_price: float = 10.0
    market_event_min_average_volume: float = 500_000

    def __post_init__(self):
        if self.mode not in {'disabled', 'shadow'}:
            raise ValueError('Discovery supports only disabled or shadow mode')
        if min(self.rescan_seconds, self.scan_limit, self.batch_size, self.lease_seconds, self.max_attempts,
               self.retry_seconds, self.stale_seconds) < 1:
            raise ValueError('Discovery limits must be positive')
        if self.admission_batch_size < self.batch_size:
            raise ValueError('The admission batch must be at least the research batch')
        if not 0 <= self.direct_slots < self.batch_size:
            raise ValueError('Discovery reservations must leave at least one general slot')
        if not 0 < self.aging_per_hour <= 100 or not 0 <= self.minimum_evidence_score <= 1:
            raise ValueError('Invalid discovery aging or evidence threshold')
        if min(self.market_event_limit, self.market_event_min_price, self.market_event_min_average_volume) < 0:
            raise ValueError('Market event limits must not be negative')

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv('DISCOVERY_MODE', 'disabled').lower(),
                   rescan_seconds=int(os.getenv('DISCOVERY_RESCAN_SECONDS', '300')),
                   scan_limit=int(os.getenv('DISCOVERY_SCAN_LIMIT', '20')),
                   batch_size=int(os.getenv('DISCOVERY_BATCH_SIZE', '6')),
                   admission_batch_size=int(os.getenv('DISCOVERY_ADMISSION_BATCH_SIZE', '20')),
                   direct_slots=int(os.getenv('DISCOVERY_DIRECT_SLOTS', '2')),
                   lease_seconds=int(os.getenv('DISCOVERY_LEASE_SECONDS', '120')),
                   max_attempts=int(os.getenv('DISCOVERY_MAX_ATTEMPTS', '3')),
                   retry_seconds=int(os.getenv('DISCOVERY_RETRY_SECONDS', '60')),
                   stale_seconds=int(os.getenv('DISCOVERY_STALE_SECONDS', '604800')),
                   market_event_limit=int(os.getenv('DISCOVERY_MARKET_EVENT_LIMIT', '0')),
                   market_event_min_price=float(os.getenv('DISCOVERY_MARKET_EVENT_MIN_PRICE', '10')),
                   market_event_min_average_volume=float(os.getenv('DISCOVERY_MARKET_EVENT_MIN_AVERAGE_VOLUME', '500000')))

    def manifest(self):
        return asdict(self)
