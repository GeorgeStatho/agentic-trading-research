from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from services.common import safe_float


MomentumStatus = Literal["good", "bad", "unknown"]

MOMENTUM_GOOD: MomentumStatus = "good"
MOMENTUM_BAD: MomentumStatus = "bad"
MOMENTUM_UNKNOWN: MomentumStatus = "unknown"

__all__ = [
    "MOMENTUM_BAD",
    "MOMENTUM_GOOD",
    "MOMENTUM_UNKNOWN",
    "MomentumStatus",
    "OptionMomentumAssessment",
    "evaluate_option_momentum",
    "evaluate_option_momentum_from_snapshot",
]


@dataclass(frozen=True)
class OptionMomentumAssessment:
    """Simple directional momentum assessment for an open option position."""

    status: MomentumStatus
    reasons: list[str]
    contract_type: str
    entry_underlying_price: float | None
    current_underlying_price: float | None
    strike_price: float | None
    moved_in_profitable_direction: bool | None
    moved_toward_strike: bool | None
    price_change_from_entry_pct: float | None
    distance_to_strike_at_entry: float | None
    distance_to_strike_now: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "contract_type": self.contract_type,
            "entry_underlying_price": self.entry_underlying_price,
            "current_underlying_price": self.current_underlying_price,
            "strike_price": self.strike_price,
            "moved_in_profitable_direction": self.moved_in_profitable_direction,
            "moved_toward_strike": self.moved_toward_strike,
            "price_change_from_entry_pct": self.price_change_from_entry_pct,
            "distance_to_strike_at_entry": self.distance_to_strike_at_entry,
            "distance_to_strike_now": self.distance_to_strike_now,
        }


def _normalize_contract_type(contract_type: Any) -> str:
    normalized = str(contract_type or "").strip().lower()
    if normalized in {"call", "c"}:
        return "call"
    if normalized in {"put", "p"}:
        return "put"
    return ""


def _distance_to_strike(
    *,
    contract_type: str,
    underlying_price: float,
    strike_price: float,
) -> float:
    if contract_type == "call":
        return strike_price - underlying_price
    return underlying_price - strike_price


def _build_unknown_assessment(
    *,
    reasons: list[str],
    contract_type: str,
    entry_underlying_price: float | None,
    current_underlying_price: float | None,
    strike_price: float | None,
) -> OptionMomentumAssessment:
    return OptionMomentumAssessment(
        status=MOMENTUM_UNKNOWN,
        reasons=reasons,
        contract_type=contract_type,
        entry_underlying_price=entry_underlying_price,
        current_underlying_price=current_underlying_price,
        strike_price=strike_price,
        moved_in_profitable_direction=None,
        moved_toward_strike=None,
        price_change_from_entry_pct=None,
        distance_to_strike_at_entry=None,
        distance_to_strike_now=None,
    )


def evaluate_option_momentum(
    *,
    contract_type: str,
    entry_underlying_price: Any,
    current_underlying_price: Any,
    strike_price: Any,
) -> OptionMomentumAssessment:
    normalized_contract_type = _normalize_contract_type(contract_type)
    entry_price = safe_float(entry_underlying_price)
    current_price = safe_float(current_underlying_price)
    strike = safe_float(strike_price)

    reasons: list[str] = []
    if not normalized_contract_type:
        reasons.append("Contract type was missing or invalid.")
    if entry_price is None or entry_price <= 0:
        reasons.append("Entry underlying price was missing or invalid.")
    if current_price is None or current_price <= 0:
        reasons.append("Current underlying price was missing or invalid.")
    if strike is None or strike <= 0:
        reasons.append("Strike price was missing or invalid.")

    if reasons:
        return _build_unknown_assessment(
            reasons=reasons,
            contract_type=normalized_contract_type,
            entry_underlying_price=entry_price,
            current_underlying_price=current_price,
            strike_price=strike,
        )

    if normalized_contract_type == "call":
        moved_in_profitable_direction = current_price > entry_price
    else:
        moved_in_profitable_direction = current_price < entry_price

    distance_at_entry = _distance_to_strike(
        contract_type=normalized_contract_type,
        underlying_price=entry_price,
        strike_price=strike,
    )
    distance_now = _distance_to_strike(
        contract_type=normalized_contract_type,
        underlying_price=current_price,
        strike_price=strike,
    )
    # Signed strike distance doubles as a simple moneyness score:
    # positive = OTM, zero = ATM, negative = ITM.
    # Lower values are better because they move toward the strike and then deeper ITM.
    moved_toward_strike = distance_now < distance_at_entry

    price_change_from_entry_pct = round(((current_price - entry_price) / entry_price) * 100.0, 4)
    if normalized_contract_type == "put":
        price_change_from_entry_pct = round(price_change_from_entry_pct * -1.0, 4)

    if moved_in_profitable_direction and moved_toward_strike:
        reasons.append("Underlying moved in the profitable direction and improved its strike distance.")
        status: MomentumStatus = MOMENTUM_GOOD
    elif not moved_in_profitable_direction and not moved_toward_strike:
        reasons.append("Underlying moved against the position and farther from the strike.")
        status = MOMENTUM_BAD
    else:
        reasons.append("Underlying signals were mixed between direction and strike progress.")
        status = MOMENTUM_UNKNOWN

    return OptionMomentumAssessment(
        status=status,
        reasons=reasons,
        contract_type=normalized_contract_type,
        entry_underlying_price=entry_price,
        current_underlying_price=current_price,
        strike_price=strike,
        moved_in_profitable_direction=moved_in_profitable_direction,
        moved_toward_strike=moved_toward_strike,
        price_change_from_entry_pct=price_change_from_entry_pct,
        distance_to_strike_at_entry=round(distance_at_entry, 4),
        distance_to_strike_now=round(distance_now, 4),
    )


def evaluate_option_momentum_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    assessment = evaluate_option_momentum(
        contract_type=snapshot.get("contract_type"),
        entry_underlying_price=snapshot.get("entry_underlying_price"),
        current_underlying_price=snapshot.get("underlying_stock_price"),
        strike_price=snapshot.get("strike"),
    )
    return assessment.to_dict()
