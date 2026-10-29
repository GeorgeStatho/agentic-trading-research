"""Trading accounts: production as the read-only primary, plus follower accounts that mirror it."""
from __future__ import annotations

from dataclasses import dataclass

STRATEGIES = ('single_leg', 'debit_spread', 'itm_single', 'longer_dte', 'shares')


@dataclass(frozen=True)
class TradingAccount:
    name: str
    api_key: str
    api_secret: str
    role: str  # 'primary' or 'follower'
    strategy: str
    exit_policy: str


@dataclass(frozen=True)
class FollowerSettings:
    spread_width_pct: float = 0.05
    itm_min_delta: float = 0.70
    force_exit_dte: int = 1
    max_hold_days: float = 30.0

    @classmethod
    def from_env(cls, env=None) -> 'FollowerSettings':
        raise NotImplementedError


def load_trading_accounts(env=None) -> list[TradingAccount]:
    raise NotImplementedError
