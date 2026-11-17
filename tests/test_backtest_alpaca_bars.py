"""Alpaca 1-minute bar fetch for backtests: stored once per session, regular hours only, no order endpoint."""
from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from db_common import get_connection
from market_db_modules.backtest_alpaca_bars import fetch_alpaca_minute_bars
from market_db_modules.bootstrap import initialize_database

DATA_DIR = Path(__file__).resolve().parents[1] / "Data"
# Wednesday 10:00 New York; a 24-hour hold covers that session and Thursday's.
DECISION_AT = "2026-03-04T15:00:00+00:00"
AFTER_BOTH_SESSIONS = datetime(2026, 3, 6, 12, tzinfo=timezone.utc)


def bar(at: str, close: float = 400.0) -> SimpleNamespace:
    return SimpleNamespace(timestamp=datetime.fromisoformat(at), open=close, high=close, low=close, close=close,
                           volume=10, trade_count=2, vwap=close)


class FakeClient:
    """Returns the bars given per symbol inside the requested window, like the SDK's BarSet.data."""

    def __init__(self, bars: dict[str, list[SimpleNamespace]], fail: set[str] = frozenset()) -> None:
        self.bars, self.fail, self.requests = bars, fail, []

    def _get(self, request):
        symbol = request.symbol_or_symbols
        self.requests.append(symbol)
        if symbol in self.fail:
            raise RuntimeError("provider down")
        # The SDK's request models hold start and end as naive UTC.
        naive = lambda at: at.astimezone(timezone.utc).replace(tzinfo=None) if at.tzinfo else at
        return SimpleNamespace(data={symbol: [item for item in self.bars.get(symbol, [])
                                              if naive(request.start) <= naive(item.timestamp) <= naive(request.end)]})

    get_stock_bars = get_option_bars = _get


class AlpacaMinuteBarFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "bars.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        self.add_decision("MSFT", "MSFT260417C00400000")
        self.stock = FakeClient({"MSFT": [bar("2026-03-04T13:00:00+00:00"),  # pre-market
                                          bar("2026-03-04T14:30:00+00:00"), bar("2026-03-04T20:59:00+00:00"),
                                          bar("2026-03-04T21:30:00+00:00"),  # after hours
                                          bar("2026-03-05T14:30:00+00:00")],
                                 "AAPL": [bar("2026-03-04T15:00:00+00:00", 200.0)]})
        self.options = FakeClient({"MSFT260417C00400000": [bar("2026-03-04T15:10:00+00:00", 11.0)]})

    def add_decision(self, symbol: str, option_symbol: str, at: str = DECISION_AT) -> None:
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision, "
                         "selected_option_symbol) VALUES (?, ?, 'call', ?)", (symbol, at, option_symbol))

    def fetch(self, **options) -> dict:
        return fetch_alpaca_minute_bars(**{"limit": 10, "max_hold_hours": 24, "stock_client": self.stock,
                                           "option_client": self.options, "now": AFTER_BOTH_SESSIONS,
                                           "db_path": self.db_path, **options})

    def rows(self, sql: str) -> list[tuple]:
        with closing(get_connection(self.db_path)) as conn:
            return [tuple(row) for row in conn.execute(sql)]

    def test_regular_hours_bars_and_one_fetch_row_per_session_are_stored(self) -> None:
        result = self.fetch()
        self.assertEqual(self.rows("SELECT bar_at FROM historical_underlying_bars WHERE timeframe='1m' ORDER BY bar_at"),
                         [("2026-03-04T14:30:00+00:00",), ("2026-03-04T20:59:00+00:00",), ("2026-03-05T14:30:00+00:00",)])
        self.assertEqual(self.rows("SELECT option_symbol, bar_at, close_price FROM historical_option_bars"),
                         [("MSFT260417C00400000", "2026-03-04T15:10:00+00:00", 11.0)])
        self.assertEqual(self.rows("SELECT symbol, session_date, bar_count FROM historical_bar_fetches ORDER BY 1, 2"),
                         [("MSFT", "2026-03-04", 2), ("MSFT", "2026-03-05", 1),
                          ("MSFT260417C00400000", "2026-03-04", 1), ("MSFT260417C00400000", "2026-03-05", 0)])
        self.assertEqual((result["stock_bar_count"], result["option_bar_count"], result["error_count"]), (3, 1, 0))
        self.assertTrue(result["read_only_trading"])

    def test_a_rerun_makes_no_requests(self) -> None:
        self.fetch()
        self.stock.requests.clear(), self.options.requests.clear()
        result = self.fetch()
        self.assertEqual((self.stock.requests, self.options.requests), ([], []))
        self.assertEqual(result["already_cached_count"], 1)

    def test_a_session_that_has_not_finished_is_left_for_later(self) -> None:
        during_thursday = datetime(2026, 3, 5, 18, tzinfo=timezone.utc)
        self.fetch(now=during_thursday)
        self.assertEqual(self.rows("SELECT DISTINCT session_date FROM historical_bar_fetches"), [("2026-03-04",)])
        self.fetch()
        self.assertIn(("2026-03-05",), self.rows("SELECT DISTINCT session_date FROM historical_bar_fetches"))

    def test_a_failed_request_is_counted_retried_later_and_the_batch_continues(self) -> None:
        self.add_decision("AAPL", "AAPL260417C00200000", at="2026-03-04T15:30:00+00:00")
        self.stock.fail = {"MSFT"}
        result = self.fetch()
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(self.rows("SELECT DISTINCT symbol FROM historical_bar_fetches WHERE symbol='MSFT'"), [])
        self.assertIn(("AAPL",), self.rows("SELECT DISTINCT symbol FROM historical_bar_fetches"))
        self.stock.fail = set()
        self.fetch()
        self.assertIn(("MSFT",), self.rows("SELECT DISTINCT symbol FROM historical_bar_fetches"))

    def test_the_limit_bounds_the_decisions_fetched_and_counts_the_rest(self) -> None:
        self.add_decision("AAPL", "AAPL260417C00200000", at="2026-03-04T15:30:00+00:00")
        result = self.fetch(limit=1)
        self.assertEqual((result["fetched_decision_count"], result["remaining_decision_count"]), (1, 1))



class MinuteBarScheduleTests(unittest.TestCase):
    """The scheduled fetch stays off Alpaca while trading needs the rate limit, by Alpaca's own market clock."""
    NOW = datetime.fromisoformat("2026-11-18T19:00:00-05:00")

    def run_with(self, clock, **options) -> tuple[dict, list]:
        from market_db_modules.backtest_alpaca_bars import run_due_minute_bar_fetch
        calls = []
        result = run_due_minute_bar_fetch(**{"now": self.NOW, "enabled": True, "market_clock": lambda: clock,
                                             "fetcher": lambda **kwargs: calls.append(kwargs) or {"fetched_decision_count": 3},
                                             **options})
        return result, calls

    def closed(self, opens_in: timedelta) -> SimpleNamespace:
        return SimpleNamespace(is_open=False, next_open=self.NOW + opens_in)

    def test_it_never_starts_while_the_market_is_open_or_within_half_an_hour_of_the_open(self) -> None:
        for clock in (SimpleNamespace(is_open=True, next_open=self.NOW + timedelta(hours=20)),
                      self.closed(timedelta(minutes=29))):
            with self.subTest(clock=clock):
                result, calls = self.run_with(clock)
                self.assertEqual((result["status"], calls), ("market_hours", []))

    def test_it_runs_while_the_market_is_closed_including_holidays(self) -> None:
        # A weekday-noon holiday: closed, next open tomorrow.
        result, calls = self.run_with(self.closed(timedelta(hours=21)))
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(calls, [{"limit": 100}])

    def test_it_skips_when_the_market_clock_cannot_be_read(self) -> None:
        result, calls = self.run_with(None)
        self.assertEqual((result["status"], calls), ("clock_unavailable", []))

    def test_it_runs_at_most_hourly_and_does_not_ask_the_clock_until_due(self) -> None:
        from market_db_modules.backtest_alpaca_bars import run_due_minute_bar_fetch
        result = run_due_minute_bar_fetch(now=self.NOW, enabled=True, last_run_at=self.NOW - timedelta(minutes=59),
                                          market_clock=lambda: self.fail("asked the clock before due"))
        self.assertEqual(result["status"], "not_due")
        self.assertEqual(self.run_with(self.closed(timedelta(hours=12)), last_run_at=self.NOW - timedelta(hours=1))[0]["status"],
                         "fetched")

    def test_it_can_be_switched_off(self) -> None:
        from market_db_modules.backtest_alpaca_bars import run_due_minute_bar_fetch
        result = run_due_minute_bar_fetch(now=self.NOW, enabled=False, market_clock=lambda: self.fail("asked the clock"),
                                          fetcher=lambda **kwargs: self.fail("fetched while disabled"))
        self.assertEqual(result["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
