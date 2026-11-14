"""Offline stand-ins for every outside service a trading cycle talks to.

The project's own code runs unchanged; only the third-party boundary is replaced:
the Alpaca SDK clients (broker, option and stock data), yfinance, SEC and the LLM call.
Any real HTTP request that slips past them fails the test instead of reaching the network.
"""
from __future__ import annotations

import json
import math
from contextlib import ExitStack, closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from db_common import get_connection


SYMBOL = "NVDA"
SPOT = 100.0
COMPANY_ID, INDUSTRY_ID, SECTOR_ID = 1, 1, 1
ARTICLE_IDS = (1, 2)


def _fridays(today: date, days: int) -> list[date]:
    first = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    return [first + timedelta(weeks=week) for week in range(days // 7 + 1)]


def _field(request, name):
    return getattr(request, name, None)


class FakeMarket:
    """One underlying's broker account, option chain and price history, deterministic and offline.

    It answers as all three Alpaca SDK clients (trading, option data, stock data).
    """

    def __init__(self, symbol: str = SYMBOL, spot: float = SPOT, buying_power: float = 100_000.0):
        self.symbol, self.spot, self.buying_power = symbol, spot, buying_power
        self.today = datetime.now(timezone.utc).date()
        self.orders: list[SimpleNamespace] = []
        self.contracts = [self._contract(expiry, strike, kind)
                          for expiry in _fridays(self.today, 70)
                          for strike in [spot + step * 2.5 for step in range(-8, 9)]
                          for kind in ("call", "put")]

    # Contracts and quotes ---------------------------------------------------------------
    def _contract(self, expiry: date, strike: float, kind: str) -> dict:
        occ = f"{self.symbol}{expiry:%y%m%d}{kind[0].upper()}{int(round(strike * 1000)):08d}"
        return {"id": occ, "symbol": occ, "underlying_symbol": self.symbol, "type": kind,
                "expiration_date": expiry, "strike_price": strike, "style": "american", "status": "active",
                "tradable": True, "size": 100, "open_interest": 5000, "open_interest_date": self.today,
                "close_price": self._mid(expiry, strike, kind)}

    def _mid(self, expiry: date, strike: float, kind: str) -> float:
        intrinsic = max(0.0, self.spot - strike) if kind == "call" else max(0.0, strike - self.spot)
        days = max(1, (expiry - self.today).days)
        return round(intrinsic + 0.04 * self.spot * math.sqrt(days / 30), 2)

    def _snapshot(self, contract: dict) -> dict:
        mid = self._mid(contract["expiration_date"], contract["strike_price"], contract["type"])
        moneyness = (self.spot - contract["strike_price"]) / 20
        delta = min(0.95, max(0.05, 0.5 + moneyness))
        delta = delta if contract["type"] == "call" else delta - 1
        now = datetime.now(timezone.utc)
        return {"implied_volatility": 0.45,
                "greeks": {"delta": round(delta, 3), "gamma": 0.03, "theta": -0.05, "vega": 0.1, "rho": 0.01},
                "latest_quote": {"bid_price": round(mid - 0.05, 2), "ask_price": round(mid + 0.05, 2),
                                 "bid_size": 50, "ask_size": 50, "timestamp": now},
                "latest_trade": {"price": mid, "timestamp": now}}

    def _matches(self, contract: dict, request) -> bool:
        kind = _field(request, "type")
        if kind is not None and str(getattr(kind, "value", kind)).lower() != contract["type"]:
            return False
        expiry = contract["expiration_date"].isoformat()
        for name, keep in (("expiration_date", expiry.__eq__), ("expiration_date_gte", expiry.__ge__),
                           ("expiration_date_lte", expiry.__le__)):
            bound = _field(request, name)
            if bound and not keep(str(bound)):
                return False
        low, high = _field(request, "strike_price_gte"), _field(request, "strike_price_lte")
        return (low is None or contract["strike_price"] >= float(low)) and \
            (high is None or contract["strike_price"] <= float(high))

    # alpaca TradingClient -----------------------------------------------------------------
    def get_option_contracts(self, request):
        return {"option_contracts": [c for c in self.contracts if self._matches(c, request)], "next_page_token": None}

    def get_account(self):
        return SimpleNamespace(buying_power=str(self.buying_power), equity=str(self.buying_power),
                               cash=str(self.buying_power), status="ACTIVE")

    def get_all_positions(self):
        return []

    def get_clock(self):
        return SimpleNamespace(is_open=True, timestamp=datetime.now(timezone.utc))

    def submit_order(self, order_data):
        order = SimpleNamespace(
            id=f"order-{len(self.orders) + 1}", symbol=order_data.symbol, qty=str(order_data.qty),
            side=order_data.side, type="market", time_in_force=order_data.time_in_force, status="filled",
            asset_class="us_option", submitted_at=datetime.now(timezone.utc), limit_price=None,
            filled_qty=str(order_data.qty), filled_at=datetime.now(timezone.utc), canceled_at=None, failed_at=None,
            expired_at=None, replaced_by=None, replaces=None, updated_at=datetime.now(timezone.utc))
        contract = next((c for c in self.contracts if c["symbol"] == order_data.symbol), None)
        order.filled_avg_price = str(self._snapshot(contract)["latest_quote"]["ask_price"]) if contract else "1.0"
        self.orders.append(order)
        return order

    def get_order_by_id(self, order_id):
        return next(order for order in self.orders if order.id == order_id)

    def get_orders(self, filter=None):
        return []

    # alpaca OptionHistoricalDataClient ---------------------------------------------------
    def get_option_chain(self, request):
        return {c["symbol"]: self._snapshot(c) for c in self.contracts if self._matches(c, request)}

    def get_option_latest_quote(self, request):
        wanted = _field(request, "symbol_or_symbols")
        wanted = {wanted} if isinstance(wanted, str) else set(wanted or ())
        return {c["symbol"]: SimpleNamespace(**self._snapshot(c)["latest_quote"])
                for c in self.contracts if c["symbol"] in wanted}

    # alpaca StockHistoricalDataClient ----------------------------------------------------
    def bars(self, count: int = 400) -> list[SimpleNamespace]:
        start = datetime.now(timezone.utc) - timedelta(days=count)
        return [SimpleNamespace(timestamp=start + timedelta(days=day), open=price - 0.5, high=price + 1,
                                low=price - 1, close=price, volume=1_000_000, vwap=price, trade_count=1000)
                for day in range(count) for price in [self.spot * (0.8 + 0.2 * day / (count - 1))]]

    def get_stock_bars(self, request):
        return {str(_field(request, "symbol_or_symbols")).upper(): self.bars()}

    def get_stock_latest_quote(self, request):
        symbol = _field(request, "symbol_or_symbols")
        return {symbol: SimpleNamespace(bid_price=self.spot - 0.01, ask_price=self.spot + 0.01,
                                        bid_size=100, ask_size=100, timestamp=datetime.now(timezone.utc))}

    def get_stock_latest_trade(self, request):
        return {_field(request, "symbol_or_symbols"): SimpleNamespace(price=self.spot, timestamp=datetime.now(timezone.utc))}

    # yfinance -------------------------------------------------------------------------------
    def ticker(self, symbol, *args, **kwargs):
        market = self
        bars = self.bars()

        class Ticker:
            fast_info = {"lastPrice": market.spot, "previousClose": market.spot * 0.99}
            info = {"symbol": symbol}
            news: list = []

            def history(self, period="1mo", interval="1d", **_):
                frame = pd.DataFrame([{"Open": b.open, "High": b.high, "Low": b.low, "Close": b.close,
                                       "Volume": b.volume} for b in bars],
                                     index=pd.DatetimeIndex([b.timestamp for b in bars], name="Date"))
                return frame

            def get_news(self, *args, **kwargs):
                return []

        return Ticker()


def _offline(name):
    def refuse(*args, **kwargs):
        raise RuntimeError(f"{name} was called during an offline test")
    return refuse


class ScriptedModel:
    """The LLM: answers each stage from a canned reply and records every prompt it was sent."""

    def __init__(self, **replies):
        self.replies = replies
        self.prompts: list[tuple[str, str]] = []
        self.system_prompts: list[tuple[str, str]] = []

    def answer(self, stage):
        def ask(*, system_prompt="", user_prompt="", **_):
            self.prompts.append((stage, user_prompt))
            self.system_prompts.append((stage, system_prompt))
            reply = self.replies[stage]
            return reply(user_prompt) if callable(reply) else reply
        return ask


def strategist_reply(decision="trade_candidate", *, symbol=SYMBOL, company_id=COMPANY_ID, article_ids=ARTICLE_IDS,
                     direction="call", industry_id=INDUSTRY_ID, sector_id=SECTOR_ID):
    """A grounded strategist answer citing the seeded articles; `direction` is call or put."""
    return json.dumps({"recommendation": {
        "decision": decision, "confidence": "high", "evidence_quality": "strong", "setup_quality": "strong",
        "timing_clarity": "clear", "preferred_option_direction": direction,
        "expected_stock_direction": "up" if direction == "call" else "down",
        "time_horizon": "short_term", "why_now": "Guidance was revised", "summary": "Revised guidance",
        "catalyst": [], "thesis": [], "risks": [], "contradictions_present": False, "watchlist_reason": "",
        "grounded_company_symbol": symbol,
        "grounded_entity_ids": {"company_id": company_id, "industry_id": industry_id, "sector_id": sector_id},
        "evidence_claims": [{"claim_type": kind, "claim": "Raised guidance", "evidence_article_ids": list(article_ids)}
                            for kind in ("summary", "why_now")]}})


def grounded_strategist_reply(user_prompt):
    """A trade_candidate grounded on whichever company the prompt names, for cycles over many companies."""
    grounding = json.loads(user_prompt)["grounding"]
    return strategist_reply(symbol=grounding["company"]["symbol"], company_id=grounding["company"]["id"],
                            article_ids=grounding["article_ids"], industry_id=grounding["industry"]["id"],
                            sector_id=grounding["sector"]["id"])


def manager_reply(decision="call"):
    return json.dumps({"recommendation": {
        "decision": decision, "confidence": "high", "target_dte_bucket": "dte_8_21",
        "reason": "Raised guidance with confirming price action."}})


def bearish_news(article_ids=ARTICLE_IDS) -> None:
    """Turn the seeded articles into a guidance cut that the opportunists rated negative."""
    with closing(get_connection()) as conn, conn:
        for article_id in article_ids:
            conn.execute("UPDATE news_articles SET title=?, summary=? WHERE id=?",
                         (f"{SYMBOL} cuts revenue guidance", "Revenue guidance was cut.", article_id))
            conn.execute("UPDATE news_article_content SET body=? WHERE article_id=?",
                         ("The company cut quarterly revenue guidance below expectations.", article_id))
            for table in ("industry_opportunist_impacts", "company_opportunist_impacts"):
                conn.execute(f"UPDATE {table} SET impact_direction='negative', reason='Guidance cut' WHERE article_id=?",
                             (article_id,))


def seed_cycle_inputs() -> None:
    """One ranked sector and industry holding NVDA with fresh high-confidence news, as the collectors leave it."""
    with closing(get_connection()) as conn, conn:
        conn.execute("INSERT INTO sectors(id,sector_key,name) VALUES (?,'technology','Technology')", (SECTOR_ID,))
        conn.execute("INSERT INTO industries(id,sector_id,industry_key,name) VALUES (?,?,'semiconductors','Semiconductors')",
                     (INDUSTRY_ID, SECTOR_ID))
    add_company(COMPANY_ID, SYMBOL, ARTICLE_IDS)
    from market_data.hydration import _record_industry_top_companies_refresh
    # Marks the industry's company list fresh, so ranking does not re-fetch it from Yahoo.
    _record_industry_top_companies_refresh("semiconductors")


def add_company(company_id: int, symbol: str, article_ids, *, confidence: str = "high", market_weight: float = 1.0,
                industry_id: int = INDUSTRY_ID, sector_id: int = SECTOR_ID):
    """A company in an existing industry with its own articles, rated by the opportunists at `confidence`."""
    now = datetime.now(timezone.utc).isoformat()
    with closing(get_connection()) as conn, conn:
        conn.execute("INSERT INTO companies(id,industry_id,symbol,name,market_weight) VALUES (?,?,?,?,?)",
                     (company_id, industry_id, symbol, f"{symbol} Inc.", market_weight))
        for article_id in article_ids:
            conn.execute("""INSERT INTO news_articles(id,source,article_key,title,source_url,published_at,first_seen_time,
                            primary_ticker,summary,source_type) VALUES (?,?,?,?,?,?,?,?,?,'company_release')""",
                         (article_id, "issuer", f"article-{article_id}", f"{symbol} raises revenue guidance",
                          f"https://issuer.test/{article_id}", now, now, symbol, "Revenue guidance was raised."))
            conn.execute("INSERT INTO news_article_content(article_id,body) VALUES (?,?)",
                         (article_id, "The company raised quarterly revenue guidance above expectations."))
            conn.execute("""INSERT INTO us_news_sector_impacts(article_id,sector_id,confidence,reason,created_at)
                            VALUES (?,?,?,'AI demand',?)""", (article_id, sector_id, confidence, now))
            conn.execute("INSERT INTO us_news_article_processing(article_id,model,processed_at) VALUES (?,'fixture',?)",
                         (article_id, now))
            conn.execute("""INSERT INTO industry_opportunist_impacts(article_id,industry_id,confidence,impact_direction,
                            impact_magnitude,reason,created_at) VALUES (?,?,?,'positive','high','AI demand',?)""",
                         (article_id, industry_id, confidence, now))
            conn.execute("INSERT INTO company_news_articles(company_id,article_id) VALUES (?,?)", (company_id, article_id))
            conn.execute("""INSERT INTO company_opportunist_impacts(article_id,company_id,confidence,impact_direction,
                            impact_magnitude,reason,created_at) VALUES (?,?,?,'positive','high','Guidance raised',?)""",
                         (article_id, company_id, confidence, now))


@contextmanager
def offline_services(market: FakeMarket, model: ScriptedModel):
    """Route every outside call of a cycle to `market` and `model`; refuse anything else."""
    import yfinance
    from agent_helpers.market_context import alpaca_base
    from market_data import yfinance_client
    from trading_support import clients

    clients_by_role = {"trading": market, "option": market, "stock": market}
    with ExitStack() as stack:
        enter = stack.enter_context
        enter(mock.patch("requests.Session.request", _offline("requests")))
        enter(mock.patch("curl_cffi.requests.Session.request", _offline("curl_cffi")))
        enter(mock.patch.object(yfinance, "Ticker", market.ticker))
        # The rate limit protects Yahoo; the fake needs none.
        enter(mock.patch.object(yfinance_client.REQUEST_HANDLER, "min_interval_seconds", 0))
        for name in ("Industry", "Sector", "Search", "download"):
            enter(mock.patch.object(yfinance, name, _offline(f"yfinance.{name}")))
        enter(mock.patch.object(alpaca_base, "_ALPACA_CLIENTS", clients_by_role))
        enter(mock.patch.object(clients, "get_option_history_client", lambda: market))
        enter(mock.patch.object(clients, "get_stock_history_client", lambda: market))
        enter(scripted_models(model))
        yield


@contextmanager
def scripted_models(model: ScriptedModel):
    """Answer the strategist and manager from `model`; no model client is created or called."""
    from agent_stages import manager_runner, strategist_runner

    with ExitStack() as stack:
        for runner, stage in ((strategist_runner, "strategist"), (manager_runner, "manager")):
            stack.enter_context(mock.patch.object(runner, "ask_model", model.answer(stage)))
            stack.enter_context(mock.patch.object(runner, "get_default_client", return_value=object()))
        yield
