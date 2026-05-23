from __future__ import annotations

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import ContractType
from alpaca.trading.requests import GetOptionContractsRequest

from .clients import get_default_trading_client


def GetCallOptionsForCompany(
    company: str,
    *,
    trading_client_override: TradingClient | None = None,
    expiration_date: str = "2026-05-15",
    limit: int = 10,
):
    """Return a small call-contract sample for one underlying."""
    active_trading_client = trading_client_override or get_default_trading_client()
    return active_trading_client.get_option_contracts(
        GetOptionContractsRequest(
            underlying_symbols=[company],
            type=ContractType.CALL,
            expiration_date=expiration_date,
            limit=limit,
        )
    )
