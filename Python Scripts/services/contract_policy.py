"""Explicit rollout controls and provisional Part 5 thresholds."""
from dataclasses import dataclass
import math
import os


def mode(name):
    value = os.getenv(name, 'shadow').lower().strip()
    if value not in {'shadow', 'enforce', 'disabled'}:
        raise ValueError(f'{name} must be shadow, enforce or disabled')
    return value


@dataclass(frozen=True)
class ContractPolicy:
    version: str = 'contract-execution-v2'
    liquidity_mode: str = 'shadow'
    scenario_mode: str = 'shadow'
    cluster_mode: str = 'shadow'
    exit_mode: str = 'shadow'
    minimum_quote_size: int = 1
    maximum_quote_age_seconds: float = 5
    maximum_spread_fraction: float = .06
    maximum_cluster_positions: int = 1
    maximum_cluster_exposure_fraction: float = .25
    scenario_iv_change: float = -.05
    scenario_tail_weight: float = .25
    iv_collapse_fraction: float = .30
    underlying_stagnation_fraction: float = .01
    baseline_minimum_fills: int = 30
    entry_attempt_seconds: int = 5
    maximum_underlying_move_atr: float = .5

    def __post_init__(self):
        for value in (self.liquidity_mode, self.scenario_mode, self.cluster_mode, self.exit_mode):
            if value not in {'shadow', 'enforce', 'disabled'}:
                raise ValueError('Invalid Part 5 policy mode')
        for value in (self.minimum_quote_size, self.maximum_quote_age_seconds, self.maximum_spread_fraction,
                      self.maximum_cluster_positions, self.maximum_cluster_exposure_fraction,
                      self.iv_collapse_fraction, self.underlying_stagnation_fraction,
                      self.baseline_minimum_fills, self.entry_attempt_seconds, self.maximum_underlying_move_atr):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Part 5 limits must be finite and positive')
        if not math.isfinite(self.scenario_iv_change) or not math.isfinite(self.scenario_tail_weight) or self.scenario_tail_weight < 0:
            raise ValueError('Invalid scenario assumptions')

    @classmethod
    def from_env(cls):
        return cls(liquidity_mode=mode('V2_LIQUIDITY_MODE'), scenario_mode=mode('V2_SCENARIO_MODE'),
                   cluster_mode=mode('V2_RISK_CLUSTER_MODE'), exit_mode=mode('V2_POSITION_EXIT_MODE'),
                   minimum_quote_size=int(os.getenv('V2_MIN_QUOTE_SIZE', '1')),
                   maximum_spread_fraction=float(os.getenv('V2_MAX_SPREAD_FRACTION', '.06')),
                   maximum_cluster_positions=int(os.getenv('V2_MAX_CLUSTER_POSITIONS', '1')),
                   maximum_cluster_exposure_fraction=float(os.getenv('V2_MAX_CLUSTER_EXPOSURE_FRACTION', '.25')),
                   scenario_iv_change=float(os.getenv('V2_SCENARIO_IV_CHANGE', '-.05')))
