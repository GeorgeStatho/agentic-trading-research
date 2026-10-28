"""Which paper traders mirror production, and the knobs their strategies share."""
from __future__ import annotations

from dataclasses import dataclass

TRADER_NAMES = ('debit_spread', 'itm_single', 'longer_dte', 'shares')


@dataclass(frozen=True)
class PaperTraderAccount:
    name: str
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class PaperTraderSettings:
    spread_width_pct: float = 0.05
    itm_min_delta: float = 0.70
    force_exit_dte: int = 1
    max_hold_days: float = 30.0

    @classmethod
    def from_env(cls, env=None) -> 'PaperTraderSettings':
        raise NotImplementedError


def load_paper_traders(env=None) -> list[PaperTraderAccount]:
    raise NotImplementedError
