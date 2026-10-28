"""Turn one production entry into each paper trader's order. Pure: no broker or DB access."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaperLeg:
    symbol: str
    side: str  # 'buy' or 'sell'
    asset: str = 'option'  # 'option' or 'stock'
    ratio_qty: int = 1
    expiration_date: str | None = None


@dataclass(frozen=True)
class PaperOrderPlan:
    trader: str
    legs: tuple[PaperLeg, ...]
    qty: int
    unit_cost: float  # dollars per spread, contract or share at the quoted entry
    multiplier: int  # 100 for options, 1 for shares

    @property
    def capital(self) -> float:
        return self.qty * self.unit_cost


@dataclass(frozen=True)
class Skip:
    trader: str
    reason: str


def build_order(trader, *, decision, underlying, production_contract, chains, spot, budget, settings):
    raise NotImplementedError
