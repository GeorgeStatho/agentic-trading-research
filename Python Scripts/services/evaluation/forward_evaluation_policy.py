"""Forward evaluation settings: rolling cohorts, an optional challenger and pre-registered acceptance.

Acceptance criteria are frozen into every cohort when it opens. Changing them here only affects
cohorts opened afterwards, so a cohort can never be judged by criteria chosen after its outcomes.
"""
from dataclasses import asdict, dataclass
import os

from services.research.research_contract import fingerprint


MODES = {'off', 'shadow'}


@dataclass(frozen=True)
class ForwardEvaluationPolicy:
    """Cohorts, closing marks and outcome collection. None of it can place an order."""
    mode: str = 'shadow'
    # Marks are polled once per session, this many minutes before the close.
    mark_lead_minutes: int = 10
    policy_version: str = 'forward-evaluation-v1'

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError('FORWARD_EVALUATION_MODE must be off or shadow')
        if not 1 <= self.mark_lead_minutes <= 60:
            raise ValueError('FORWARD_MARK_LEAD_MINUTES must be between 1 and 60')

    @property
    def enabled(self):
        return self.mode == 'shadow'

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv('FORWARD_EVALUATION_MODE', 'shadow').strip().lower(),
                   mark_lead_minutes=int(os.getenv('FORWARD_MARK_LEAD_MINUTES', '10')))


@dataclass(frozen=True)
class ChallengerPolicy:
    """An alternate strategist/manager model pair, run in shadow on the incumbent's frozen inputs.

    An empty model inherits the incumbent's, so a challenger can change one stage only.
    """
    mode: str = 'off'
    strategist_model: str = ''
    manager_model: str = ''
    # Sized for roughly 50 production decisions a day at two calls each; a smaller allowance leaves a
    # backlog, and observations still unevaluated when the cohort seals count against its coverage.
    evaluations_per_cycle: int = 12
    daily_model_calls: int = 120
    policy_version: str = 'challenger-models-v1'

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError('RESEARCH_CHALLENGER_MODE must be off or shadow')
        if self.evaluations_per_cycle < 1 or self.daily_model_calls < 2:
            raise ValueError('A challenger needs at least one evaluation per cycle and two calls a day')
        if self.mode == 'shadow' and not (self.strategist_model or self.manager_model):
            raise ValueError('A shadow challenger needs RESEARCH_CHALLENGER_STRATEGIST_MODEL or _MANAGER_MODEL')

    @property
    def enabled(self):
        return self.mode == 'shadow'

    @property
    def challenger_id(self):
        return 'models:' + fingerprint({'strategist': self.strategist_model, 'manager': self.manager_model})[:16]

    def models(self, incumbent_models):
        return {'strategist': self.strategist_model or incumbent_models['strategist'],
                'manager': self.manager_model or incumbent_models['manager']}

    def manifest(self):
        return {**asdict(self), 'challenger_id': self.challenger_id}

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv('RESEARCH_CHALLENGER_MODE', 'off').strip().lower(),
                   strategist_model=os.getenv('RESEARCH_CHALLENGER_STRATEGIST_MODEL', '').strip(),
                   manager_model=os.getenv('RESEARCH_CHALLENGER_MANAGER_MODEL', '').strip(),
                   evaluations_per_cycle=int(os.getenv('RESEARCH_CHALLENGER_PER_CYCLE', '12')),
                   daily_model_calls=int(os.getenv('RESEARCH_CHALLENGER_DAILY_CALLS', '120')))


@dataclass(frozen=True)
class AcceptancePolicy:
    """Economic acceptance, defined before any outcome in the cohort exists."""
    window_days: int = 60
    cohort_candidates: int = 10000
    minimum_paired_observations: int = 100
    # Observations on the same New York day, or the same ticker within the hold, share a cluster.
    minimum_clusters: int = 30
    minimum_regimes: int = 2
    minimum_coverage: float = .9
    # One-sided: the lower bound of the cluster-bootstrap mean paired difference must exceed zero.
    confidence: float = .9
    bootstrap_resamples: int = 2000
    # The challenger's worst peak-to-trough loss may exceed the incumbent's by at most this factor.
    maximum_drawdown_ratio: float = 1.25
    policy_version: str = 'forward-acceptance-v1'

    def __post_init__(self):
        # Part 6's sample and regime requirements are floors that pre-registration cannot lower.
        if self.minimum_paired_observations < 100 or self.minimum_regimes < 2:
            raise ValueError('Acceptance cannot lower the 100-observation or two-regime floors')
        if not 1 <= self.window_days <= 365 or self.cohort_candidates < self.minimum_paired_observations:
            raise ValueError('The evaluation window or cohort size is out of range')
        if (self.minimum_clusters < 1 or not 0 < self.minimum_coverage <= 1 or not .5 < self.confidence < 1
                or self.bootstrap_resamples < 200 or self.maximum_drawdown_ratio < 1):
            raise ValueError('Invalid acceptance criteria')

    def assumptions(self):
        return asdict(self)

    def policy_hash(self):
        return fingerprint(asdict(self))

    @classmethod
    def from_env(cls):
        return cls(window_days=int(os.getenv('FORWARD_EVALUATION_WINDOW_DAYS', '60')),
                   cohort_candidates=int(os.getenv('FORWARD_COHORT_CANDIDATES', '10000')),
                   minimum_paired_observations=int(os.getenv('FORWARD_MINIMUM_PAIRED_OBSERVATIONS', '100')),
                   minimum_clusters=int(os.getenv('FORWARD_MINIMUM_CLUSTERS', '30')))


def recorded_configuration(read):
    """A research setting that fails validation is recorded; it must never stop the trading cycle."""
    try:
        return read()
    except ValueError as exc:
        return {'configuration_error': str(exc)}


def forward_evaluation_manifest():
    return {'forward_evaluation': recorded_configuration(lambda: asdict(ForwardEvaluationPolicy.from_env())),
            'challenger': recorded_configuration(lambda: ChallengerPolicy.from_env().manifest()),
            'acceptance': recorded_configuration(lambda: AcceptancePolicy.from_env().assumptions())}
