from pathlib import Path
import os
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from alpaca.data import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest, StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import ContractType, OrderSide, TimeInForce
from alpaca.trading.requests import GetOptionContractsRequest, LimitOrderRequest, MarketOrderRequest
from dotenv import load_dotenv


PYTHON_SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PYTHON_SCRIPTS_DIR.parent
env_path = PROJECT_DIR / ".env"
load_dotenv(env_path)

API_KEY = os.getenv("PUBLIC_KEY")
API_SECRET_KEY: str = os.getenv("PRIVATE_KEY")
OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
OPTION_SYMBOL_PATTERN = re.compile(
    r"^(?P<underlying>[A-Z0-9]{1,8})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<contract_type>[CP])(?P<strike>\d{8})$"
)

DEFAULT_OPTION_TAKE_PROFIT_PCT = float(
    os.getenv("OPTION_POSITION_TAKE_PROFIT_PCT", "25")
)
DEFAULT_OPTION_STOP_LOSS_PCT = float(
    os.getenv("OPTION_POSITION_STOP_LOSS_PCT", "-20")
)
DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION = float(
    os.getenv("OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION", "24")
)
MARKET_TIMEZONE = ZoneInfo("America/New_York")
OPTION_EXPIRATION_MARKET_CLOSE_HOUR = 16


def IntializeTradingClient(api_key: str, secret: str, paper: bool) -> TradingClient:
    # ensure no residual OAuth tokens conflict with key/secret auth
    os.environ.pop("APCA_OAUTH_TOKEN", None)
    os.environ.pop("ALPACA_OAUTH_TOKEN", None)
    trading_client = TradingClient(api_key=api_key, secret_key=secret, oauth_token=None, paper=paper)
    return trading_client


trading_client = IntializeTradingClient(API_KEY, API_SECRET_KEY, True)
option_history_client = OptionHistoricalDataClient(API_KEY, API_SECRET_KEY)
stock_history_client = StockHistoricalDataClient(API_KEY, API_SECRET_KEY)


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mid_price(bid_price: float | None, ask_price: float | None) -> float | None:
    if bid_price is None or ask_price is None:
        return None
    return round((bid_price + ask_price) / 2.0, 4)


def _parse_option_symbol(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    match = OPTION_SYMBOL_PATTERN.match(normalized_symbol)
    if match is None:
        return {
            "symbol": normalized_symbol,
            "underlying_symbol": "",
            "expiration_date": None,
            "contract_type": "",
            "strike": None,
        }

    expiration_date = date(
        year=2000 + int(match.group("yy")),
        month=int(match.group("mm")),
        day=int(match.group("dd")),
    )
    contract_type = "call" if match.group("contract_type") == "C" else "put"

    return {
        "symbol": normalized_symbol,
        "underlying_symbol": match.group("underlying"),
        "expiration_date": expiration_date.isoformat(),
        "contract_type": contract_type,
        "strike": int(match.group("strike")) / 1000.0,
    }


def _get_latest_option_quote(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    fallback = {
        "symbol": normalized_symbol,
        "bid_price": None,
        "ask_price": None,
        "mid_price": None,
        "timestamp": "",
        "error": "",
    }
    if not normalized_symbol:
        fallback["error"] = "Option symbol was missing."
        return fallback

    try:
        quotes = option_history_client.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=normalized_symbol)
        )
        quote = quotes.get(normalized_symbol) if hasattr(quotes, "get") else None
        if quote is None:
            fallback["error"] = f"No option quote was returned for {normalized_symbol}."
            return fallback

        bid_price = _safe_float(getattr(quote, "bid_price", None))
        ask_price = _safe_float(getattr(quote, "ask_price", None))

        return {
            "symbol": normalized_symbol,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "mid_price": _mid_price(bid_price, ask_price),
            "timestamp": str(
                getattr(quote, "timestamp", None)
                or getattr(quote, "ask_timestamp", None)
                or getattr(quote, "bid_timestamp", None)
                or ""
            ),
            "error": "",
        }
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback


def _get_latest_stock_price(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    fallback = {
        "symbol": normalized_symbol,
        "bid_price": None,
        "ask_price": None,
        "mid_price": None,
        "price": None,
        "timestamp": "",
        "error": "",
    }
    if not normalized_symbol:
        fallback["error"] = "Underlying stock symbol was missing."
        return fallback

    try:
        quotes = stock_history_client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=normalized_symbol)
        )
        quote = quotes.get(normalized_symbol) if hasattr(quotes, "get") else None
        if quote is None:
            fallback["error"] = f"No stock quote was returned for {normalized_symbol}."
            return fallback

        bid_price = _safe_float(getattr(quote, "bid_price", None))
        ask_price = _safe_float(getattr(quote, "ask_price", None))
        mid_price = _mid_price(bid_price, ask_price)

        return {
            "symbol": normalized_symbol,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "mid_price": mid_price,
            "price": ask_price if ask_price is not None else bid_price if bid_price is not None else mid_price,
            "timestamp": str(
                getattr(quote, "timestamp", None)
                or getattr(quote, "ask_timestamp", None)
                or getattr(quote, "bid_timestamp", None)
                or ""
            ),
            "error": "",
        }
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback


def _normalize_unrealized_pl_pct(
    position: Any,
    entry_price: float | None,
    mid_price: float | None,
) -> float | None:
    unrealized_plpc = _safe_float(getattr(position, "unrealized_plpc", None))
    if unrealized_plpc is not None:
        return round(unrealized_plpc * 100.0, 4)

    if entry_price is None or entry_price <= 0 or mid_price is None:
        return None

    return round(((mid_price - entry_price) / entry_price) * 100.0, 4)


def _days_to_expiration(expiration_date_text: str | None) -> int | None:
    if not expiration_date_text:
        return None

    try:
        expiration_date = datetime.strptime(expiration_date_text, "%Y-%m-%d").date()
    except ValueError:
        return None

    return (expiration_date - date.today()).days


def _hours_to_expiration(expiration_date_text: str | None) -> float | None:
    if not expiration_date_text:
        return None

    try:
        expiration_date = datetime.strptime(expiration_date_text, "%Y-%m-%d").date()
    except ValueError:
        return None

    expiration_close = datetime(
        year=expiration_date.year,
        month=expiration_date.month,
        day=expiration_date.day,
        hour=OPTION_EXPIRATION_MARKET_CLOSE_HOUR,
        tzinfo=MARKET_TIMEZONE,
    )
    now = datetime.now(MARKET_TIMEZONE)
    return round((expiration_close - now).total_seconds() / 3600.0, 4)


def _is_option_position_symbol(symbol: str, company: str | None = None) -> bool:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return False

    if company:
        normalized_company = str(company).strip().upper()
        return bool(re.match(rf"^{re.escape(normalized_company)}{OPTION_SYMBOL_TEMPLATE}", normalized_symbol))

    return bool(re.search(OPTION_SYMBOL_TEMPLATE, normalized_symbol))


def _deterministic_option_exit_decision(
    *,
    unrealized_pl_pct: float | None,
    hours_to_expiration: float | None,
    take_profit_pct: float,
    stop_loss_pct: float,
    exit_hours_to_expiration: float,
) -> tuple[str, list[str]]:
    reasons: list[str] = []

    if unrealized_pl_pct is not None and unrealized_pl_pct >= take_profit_pct:
        reasons.append(
            f"Take-profit triggered at {unrealized_pl_pct:.2f}% versus target {take_profit_pct:.2f}%."
        )
    if unrealized_pl_pct is not None and unrealized_pl_pct <= stop_loss_pct:
        reasons.append(
            f"Stop-loss triggered at {unrealized_pl_pct:.2f}% versus floor {stop_loss_pct:.2f}%."
        )
    if hours_to_expiration is not None and hours_to_expiration <= exit_hours_to_expiration:
        reasons.append(
            f"Hours to expiration is {hours_to_expiration:.2f}, at or below exit threshold {exit_hours_to_expiration:.2f}."
        )

    return ("sell", reasons) if reasons else ("hold", ["No deterministic sell rule was triggered."])


def _build_option_position_snapshot(
    position: Any,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    exit_hours_to_expiration: float,
) -> dict[str, Any]:
    option_symbol = str(getattr(position, "symbol", "") or "").strip().upper()
    parsed_symbol = _parse_option_symbol(option_symbol)
    option_quote = _get_latest_option_quote(option_symbol)
    stock_quote = _get_latest_stock_price(parsed_symbol.get("underlying_symbol"))

    entry_price = _safe_float(getattr(position, "avg_entry_price", None))
    quantity = _safe_float(getattr(position, "qty", None))
    current_bid = option_quote.get("bid_price")
    current_ask = option_quote.get("ask_price")
    mid_price = option_quote.get("mid_price")
    expiration_date_text = parsed_symbol.get("expiration_date")
    days_to_expiration = _days_to_expiration(expiration_date_text)
    hours_to_expiration = _hours_to_expiration(expiration_date_text)
    unrealized_pl_pct = _normalize_unrealized_pl_pct(position, entry_price, mid_price)
    decision, decision_reasons = _deterministic_option_exit_decision(
        unrealized_pl_pct=unrealized_pl_pct,
        hours_to_expiration=hours_to_expiration,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        exit_hours_to_expiration=exit_hours_to_expiration,
    )

    quote_errors = [error for error in (option_quote.get("error"), stock_quote.get("error")) if error]

    return {
        "symbol": option_symbol,
        "underlying_symbol": parsed_symbol.get("underlying_symbol"),
        "contract_type": parsed_symbol.get("contract_type"),
        "entry_price": entry_price,
        "current_bid": current_bid,
        "current_ask": current_ask,
        "mid_price": mid_price,
        "expiration_date": expiration_date_text,
        "strike": parsed_symbol.get("strike"),
        "quantity": quantity,
        "unrealized_pl_pct": unrealized_pl_pct,
        "days_to_expiration": days_to_expiration,
        "hours_to_expiration": hours_to_expiration,
        "underlying_stock_price": stock_quote.get("price"),
        "decision": decision,
        "decision_reasons": decision_reasons,
        "option_quote_timestamp": option_quote.get("timestamp"),
        "underlying_quote_timestamp": stock_quote.get("timestamp"),
        "quote_errors": quote_errors,
        "close_submitted": False,
        "close_order_id": "",
        "close_order_status": "",
        "close_error": "",
    }


# class to help keep track of trades
class StockTrades:
    def __init__(self, company: str, numOfStocks: float, timeInForce: str, limit: bool):
        self.company = company
        self.numOfStocks = 0
        self.timeInForce = timeInForce
        limit = limit

    #########BUY###############

    # Buy Stocks as soon as they are avaiable
    # company must be a company symbol for
    # timeInForce take in 4 options, "Day","FOK","GTC","IOC"
    def ImmediateStockBuy(self, numOfStocks: int = 0):
        if self.timeInForce == "Day":
            time_in_force = TimeInForce.DAY
        elif self.timeInForce == "FOK":
            time_in_force = TimeInForce.FOK
        elif self.timeInForce == "GTC":
            time_in_force = TimeInForce.GTC
        elif self.timeInForce == "IOC":
            time_in_force = TimeInForce.IOC

        market_order_data = MarketOrderRequest(
            symbol=self.company,
            qty=numOfStocks,
            side=OrderSide.BUY,
            time_in_force=time_in_force,
        )

        trading_client.submit_order(order_data=market_order_data)
        self.numOfStocks = self.numOfStocks + numOfStocks

    # buy Stocks when at specific price
    # follows similar parameters to ImmediateStockBuy function,except
    # limit_price and notional are needed at the end of the function
    def StockAtPriceBuy(self, numOfStocks: int = 0, limit_price: int = 0, notional: int = 0):
        if self.timeInForce == "Day":
            time_in_force = TimeInForce.DAY
        elif self.timeInForce == "FOK":
            time_in_force = TimeInForce.FOK
        elif self.timeInForce == "GTC":
            time_in_force = TimeInForce.GTC
        elif self.timeInForce == "IOC":
            time_in_force = TimeInForce.IOC

        limit_order_data = LimitOrderRequest(
            symbol=self.company,
            limit_price=limit_price,
            notional=notional,
            qty=numOfStocks,
            side=OrderSide.BUY,
            time_in_force=time_in_force,
        )

        trading_client.submit_order(order_data=limit_order_data)
        self.numOfStocks = self.numOfStocks + numOfStocks

    ##########BUY############

    ##########SELL###########

    # Exact Same parameters as the Buy functions but it will sell instead
    def ImmediateStockSell(self, numOfStocks: int = 0):
        if self.timeInForce == "Day":
            time_in_force = TimeInForce.DAY
        elif self.timeInForce == "FOK":
            time_in_force = TimeInForce.FOK
        elif self.timeInForce == "GTC":
            time_in_force = TimeInForce.GTC
        elif self.timeInForce == "IOC":
            time_in_force = TimeInForce.IOC

        market_order_data = MarketOrderRequest(
            symbol=self.company,
            qty=numOfStocks,
            side=OrderSide.SELL,
            time_in_force=time_in_force,
        )

        trading_client.submit_order(order_data=market_order_data)
        self.numOfStocks = self.numOfStocks - numOfStocks

    def StockAtPriceSell(self, numOfStocks: int = 0, limit_price: int = 0, notional: int = 0):
        if self.timeInForce == "Day":
            time_in_force = TimeInForce.DAY
        elif self.timeInForce == "FOK":
            time_in_force = TimeInForce.FOK
        elif self.timeInForce == "GTC":
            time_in_force = TimeInForce.GTC
        elif self.timeInForce == "IOC":
            time_in_force = TimeInForce.IOC

        limit_order_data = LimitOrderRequest(
            symbol=self.company,
            limit_price=limit_price,
            notional=notional,
            qty=numOfStocks,
            side=OrderSide.SELL,
            time_in_force=time_in_force,
        )

        trading_client.submit_order(order_data=limit_order_data)
        self.numOfStocks = self.numOfStocks - numOfStocks


##########SELL###########

##########Options########

def CloseOptionPositions(
    company: str | None = None,
    *,
    trading_client_override: TradingClient | None = None,
) -> list[dict[str, Any]]:
    close_results: list[dict[str, Any]] = []
    active_trading_client = trading_client_override or trading_client
    positions = active_trading_client.get_all_positions()

    for position in positions or []:
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        if not _is_option_position_symbol(symbol, company):
            continue

        close_order = active_trading_client.close_position(symbol)
        close_results.append(
            {
                "symbol": symbol,
                "qty": str(getattr(position, "qty", "")),
                "close_order_id": str(getattr(close_order, "id", "")),
                "close_order_status": str(getattr(close_order, "status", "")),
            }
        )

    return close_results


def ManageCurrentOptionPositions(
    company: str | None = None,
    *,
    execute_sales: bool = False,
    take_profit_pct: float = DEFAULT_OPTION_TAKE_PROFIT_PCT,
    stop_loss_pct: float = DEFAULT_OPTION_STOP_LOSS_PCT,
    exit_hours_to_expiration: float = DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION,
    trading_client_override: TradingClient | None = None,
) -> dict[str, Any]:
    position_summaries: list[dict[str, Any]] = []
    active_trading_client = trading_client_override or trading_client
    positions = active_trading_client.get_all_positions()

    for position in positions or []:
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        if not _is_option_position_symbol(symbol, company):
            continue

        summary = _build_option_position_snapshot(
            position,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            exit_hours_to_expiration=exit_hours_to_expiration,
        )

        if execute_sales and summary["decision"] == "sell":
            try:
                close_order = active_trading_client.close_position(symbol)
                summary["close_submitted"] = True
                summary["close_order_id"] = str(getattr(close_order, "id", ""))
                summary["close_order_status"] = str(getattr(close_order, "status", ""))
            except Exception as exc:
                summary["close_error"] = str(exc)

        position_summaries.append(summary)

    return {
        "evaluated_at": datetime.now().isoformat(),
        "company_filter": str(company or "").strip().upper(),
        "execute_sales": execute_sales,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "exit_hours_to_expiration": exit_hours_to_expiration,
        "position_count": len(position_summaries),
        "sell_count": sum(1 for summary in position_summaries if summary.get("decision") == "sell"),
        "close_submitted_count": sum(1 for summary in position_summaries if summary.get("close_submitted")),
        "positions": position_summaries,
    }


def GetCallOptionsForCompany(company: str):
    contracts = trading_client.get_option_contracts(
        GetOptionContractsRequest(
            underlying_symbols=[company],
            type=ContractType.CALL,
            expiration_date="2026-05-15",
            limit=10,
        )
    )
    return contracts


if __name__ == "__main__":
    print(GetCallOptionsForCompany("AAPL"))
