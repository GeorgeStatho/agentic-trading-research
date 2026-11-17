"""Shared point-in-time contract for Part 6 features, labels and experiments."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from services.core.numbers import finite_float


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def utc(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


# Provider timestamps (exchange quote times) come from another clock than our cutoffs. The live host's
# clock has run up to ~1 s slow of NTP; a few seconds of skew carries no information for daily horizons.
CLOCK_SKEW_TOLERANCE = timedelta(seconds=5)


def assert_information_cutoff(timestamps, information_cutoff_time):
    """Require both observation and availability timestamps, supplied by adapters."""
    cutoff = utc(information_cutoff_time)
    for value in timestamps:
        if value is None:
            raise ValueError(f'Missing research input timestamp (cutoff {cutoff.isoformat()})')
        if utc(value) > cutoff + CLOCK_SKEW_TOLERANCE:
            raise ValueError(f'Future research input timestamp {value} after cutoff {cutoff.isoformat()}')
    return cutoff.isoformat()


@dataclass(frozen=True)
class ResearchPolicy:
    version: str = 'counterfactual-daily-v1'
    holding_days: float = 5
    stop_loss: float = .30
    take_profit: float = .50
    trailing_drawdown: float = .20
    fee_per_contract_side: float = .65
    entry_quote_age_seconds: float = 900
    maximum_mark_gap_days: float = 4
    minimum_promotion_samples: int = 100
    relative_volume_threshold: float = 1.25
    benchmark_cost_bps: float = 1

    def __post_init__(self):
        values = asdict(self)
        for key, value in values.items():
            if key != 'version' and (finite_float(value) is None or value < 0):
                raise ValueError('Research policy values must be finite and nonnegative')
        if (self.holding_days <= 0 or self.entry_quote_age_seconds <= 0
                or self.maximum_mark_gap_days <= 0 or not 100 <= self.minimum_promotion_samples <= 200
                or not 0 < self.stop_loss < 1 or not 0 < self.trailing_drawdown < 1):
            raise ValueError('Invalid research horizon, risk limit or promotion sample size')


STRATEGIES = ('sentiment_volume', 'sentiment_trend', 'taxonomy_expectancy',
              'strategist_only', 'strategist_manager', 'full_v2', 'shares_drift')
# Families with measured multi-day drift after the first print (docs/PROJECT_DIRECTION.md).
SHARES_DRIFT_FAMILIES = ('earnings_positive', 'earnings_negative', 'analyst')
SHARES_DRIFT_MAX_AGE_HOURS = 24
# Stock on a fixed notional: reported next to the option strategies, but never pairs or competes with them.
UNPAIRED_STRATEGIES = ('shares_drift',)


def direction(value):
    return {'call': 'call', 'bullish': 'call', 'positive': 'call', 'buy': 'call',
            'put': 'put', 'bearish': 'put', 'negative': 'put', 'sell': 'put',
            'neither': 'skip', 'hold': 'skip', 'neutral': 'skip', 'watchlist': 'skip'}.get(value)


def publication_lag_hours(catalyst):
    """Hours from a catalyst's first publication to the system's first sight of it; None when either is missing."""
    seen, public = catalyst.get('first_seen_time'), catalyst.get('first_public_time')
    return (utc(seen) - utc(public)).total_seconds() / 3600 if seen and public else None


def shares_drift_decision(features):
    """The catalyst's own direction for a fresh earnings or analyst catalyst; the follower fleet trades it live."""
    if features.get('catalyst_family') not in SHARES_DRIFT_FAMILIES:
        return 'skip'
    age = finite_float(features.get('catalyst_age_hours'))
    # First sight is not publication: a recap scraped days late is old news however recently it was seen.
    lag = finite_float(features.get('publication_lag_hours'))
    if age is None or lag is None:
        return None
    taxonomy = direction(features.get('catalyst_direction'))
    fresh = 0 <= age < SHARES_DRIFT_MAX_AGE_HOURS and lag < SHARES_DRIFT_MAX_AGE_HOURS and taxonomy in {'call', 'put'}
    return taxonomy if fresh else 'skip'


def strategy_decisions(features, policy):
    """None means unavailable; skip is an observed decision to hold cash."""
    sentiment = direction(features.get('sentiment'))
    strategist = direction(features.get('strategist_direction'))
    manager = direction(features.get('manager_direction'))
    volume, expectancy = finite_float(features.get('relative_volume')), finite_float(features.get('historical_expectancy'))
    trend = direction(features.get('trend'))
    taxonomy = direction(features.get('catalyst_direction'))
    production_allowed = features.get('production_allowed')
    full_v2 = None if production_allowed is None else manager if production_allowed else 'skip'
    return {
        'sentiment_volume': None if sentiment is None or volume is None else sentiment if volume >= policy.relative_volume_threshold else 'skip',
        'sentiment_trend': None if sentiment is None or trend is None else sentiment if sentiment == trend else 'skip',
        'taxonomy_expectancy': None if taxonomy is None or expectancy is None else taxonomy if (
            (taxonomy == 'call' and expectancy > 0) or (taxonomy == 'put' and expectancy < 0)) else 'skip',
        'strategist_only': strategist,
        'strategist_manager': None if strategist is None or manager is None else manager if strategist == manager else 'skip',
        'full_v2': full_v2,
        'shares_drift': shares_drift_decision(features),
    }
