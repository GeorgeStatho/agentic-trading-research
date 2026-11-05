"""Versioned research priors. Pricing verdicts deliberately have no enforce mode."""
from dataclasses import dataclass
import math
import os

HORIZON_DAYS = {'intraday': 1 / 24, '1_3_days': 3, '4_10_days': 5,
                '2_4_weeks': 21, '1_2_months': 45}
RESPONSE_HORIZONS = (1 / 24, 1, 3, 5, 10, 21, 45)


@dataclass(frozen=True)
class PricingPolicy:
    version: str = 'pricing-research-v1'
    mode: str = 'shadow'
    regression_returns: int = 60
    minimum_samples: int = 30
    absorption_threshold: float = 1.25
    move_edge_threshold: float = 1.15
    maximum_atm_distance: float = .05
    maximum_quote_age_seconds: int = 900
    symbol_budget: int = 25

    def __post_init__(self):
        if self.mode != 'shadow':
            raise ValueError('Pricing gates are shadow-only until counterfactual validation')
        if self.minimum_samples < 2 or self.regression_returns < 3 or self.symbol_budget < 1:
            raise ValueError('Invalid pricing sample or collection budget')
        for value in (self.absorption_threshold, self.move_edge_threshold,
                      self.maximum_atm_distance, self.maximum_quote_age_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Pricing thresholds must be finite and positive')

    @classmethod
    def from_env(cls):
        return cls(minimum_samples=int(os.getenv('V2_EXPECTED_MOVE_MIN_SAMPLES', '30')),
                   move_edge_threshold=float(os.getenv('V2_MOVE_EDGE_THRESHOLD', '1.15')),
                   symbol_budget=int(os.getenv('V2_RESEARCH_SYMBOL_BUDGET', '25')))
