"""Rollout controls and declared assumptions for trade-economics reports.

The numeric fields are engineering priors, not calibrated values. Each one is part of the
policy hash, so a promotion proof covers exactly the assumptions it was measured under.
"""
from dataclasses import asdict, dataclass, replace
import math
import os

from services.gates.contract_policy import ContractPolicy
from services.gates.pricing_policy import PricingPolicy
from services.research.research_contract import ResearchPolicy
from services.research.research_promotion import guard_policy


POLICY_VERSION = 'trade-economics-v2'
MODES = {'off', 'shadow', 'enforce'}


@dataclass(frozen=True)
class TradeEconomicsPolicy:
    mode: str = 'shadow'
    minimum_effective_samples: int = PricingPolicy.minimum_samples
    minimum_expected_net_return: float = 0.0
    # The Part 5 scorer prices the same way; both read OPTION_RISK_FREE_RATE and V2_SCENARIO_IV_CHANGE.
    risk_free_rate: float = ContractPolicy.scenario_risk_free_rate
    base_iv_change: float = ContractPolicy.scenario_iv_change
    stress_iv_changes: tuple[float, ...] = (-.10, -.05, 0.0, .05)
    stress_sigma_multiples: tuple[float, ...] = (-2.0, -1.0, 0.0, 1.0, 2.0)
    fee_per_contract_side: float = ResearchPolicy.fee_per_contract_side
    maximum_input_quote_age_seconds: float = PricingPolicy.maximum_quote_age_seconds
    maximum_submission_quote_age_seconds: float = ContractPolicy.maximum_quote_age_seconds
    maximum_underlying_move_atr: float = ContractPolicy.maximum_underlying_move_atr
    report_validity_seconds: float = 900
    maximum_itm_fraction: float = .10
    maximum_contracts: int = 40
    policy_version: str = POLICY_VERSION

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError('TRADE_ECONOMICS_MODE must be off, shadow or enforce')
        for value in (self.minimum_effective_samples, self.maximum_contracts):
            if type(value) is not int or value < 1:
                raise ValueError('Trade-economics counts must be positive integers')
        positive = (self.fee_per_contract_side, self.maximum_input_quote_age_seconds,
                    self.maximum_submission_quote_age_seconds, self.maximum_underlying_move_atr,
                    self.report_validity_seconds, self.maximum_itm_fraction)
        finite = (self.minimum_expected_net_return, self.risk_free_rate, self.base_iv_change,
                  *self.stress_iv_changes, *self.stress_sigma_multiples)
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError('Trade-economics limits must be finite and positive')
        if any(not math.isfinite(value) for value in finite) or not -.05 <= self.risk_free_rate <= .25:
            raise ValueError('Invalid trade-economics assumptions')
        if not self.stress_iv_changes or not self.stress_sigma_multiples:
            raise ValueError('Trade-economics stress grid must not be empty')

    def assumptions(self):
        """The frozen values a report was built under, without the rollout mode."""
        return {key: value for key, value in asdict(self).items() if key != 'mode'}

    @classmethod
    def from_assumptions(cls, assumptions):
        """Rebuild the policy a stored report used; JSON turned its tuples into lists."""
        values = {key: tuple(value) if isinstance(value, list) else value for key, value in assumptions.items()}
        return replace(cls(), **values)

    @classmethod
    def from_env(cls):
        policy = cls(mode=os.getenv('TRADE_ECONOMICS_MODE', 'shadow').strip().lower(),
                     minimum_effective_samples=int(os.getenv('V2_EXPECTED_MOVE_MIN_SAMPLES', '30')),
                     risk_free_rate=float(os.getenv('OPTION_RISK_FREE_RATE', '.04')),
                     base_iv_change=float(os.getenv('V2_SCENARIO_IV_CHANGE', '-.05')),
                     report_validity_seconds=float(os.getenv('TRADE_ECONOMICS_REPORT_VALIDITY_SECONDS', '900')))
        return guard_policy(policy, {'mode': 'TRADE_ECONOMICS'})
