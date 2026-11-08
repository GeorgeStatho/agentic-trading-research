"""Alpaca calls from the API: bounded by a timeout, run together, and portfolio history cached."""
import io
import json
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import portfolio_history_service
from api_support import alpaca
from tests.test_api_contracts import _fake_alpaca


SETTINGS = SimpleNamespace(api_key="key", api_secret="secret", trading_base_url="https://alpaca.test",
                           data_base_url="https://data.alpaca.test")


def _response(*args, **kwargs):
    return io.BytesIO(json.dumps({"equity": [1.0]}).encode())


class AlpacaTimeoutTests(unittest.TestCase):
    def test_trading_and_data_calls_pass_a_timeout(self):
        with mock.patch.object(alpaca, "ALPACA_SETTINGS", SETTINGS), \
                mock.patch.object(alpaca, "urlopen", side_effect=_response) as urlopen:
            alpaca.alpaca_get_json("/v2/account")
            alpaca.alpaca_data_get_json("/v2/stocks/quotes")
        for call in urlopen.call_args_list:
            self.assertGreater(call.kwargs.get("timeout") or 0, 0)


class PortfolioHistoryCacheTests(unittest.TestCase):
    def setUp(self):
        portfolio_history_service._HISTORY_CACHE.clear()
        patcher = mock.patch.object(portfolio_history_service.AlpacaSettings, "from_env", return_value=SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(portfolio_history_service._HISTORY_CACHE.clear)

    def test_history_is_fetched_with_a_timeout_and_reused_within_the_ttl(self):
        with mock.patch.object(portfolio_history_service, "urlopen", side_effect=_response) as urlopen, \
                mock.patch.object(portfolio_history_service.time, "monotonic", return_value=1000.0):
            first = portfolio_history_service.fetch_portfolio_history()
            second = portfolio_history_service.fetch_portfolio_history()
        self.assertEqual(first, second)
        self.assertEqual(urlopen.call_count, 1)
        self.assertGreater(urlopen.call_args.kwargs.get("timeout") or 0, 0)

    def test_history_is_refetched_after_the_ttl(self):
        clock = iter([1000.0, 1000.0 + portfolio_history_service.HISTORY_TTL_SECONDS + 1])
        with mock.patch.object(portfolio_history_service, "urlopen", side_effect=_response) as urlopen, \
                mock.patch.object(portfolio_history_service.time, "monotonic", side_effect=lambda: next(clock)):
            portfolio_history_service.fetch_portfolio_history()
            portfolio_history_service.fetch_portfolio_history()
        self.assertEqual(urlopen.call_count, 2)


class DashboardKpiConcurrencyTests(unittest.TestCase):
    def test_the_five_alpaca_calls_run_at_the_same_time(self):
        # Sequential calls would leave the first one waiting alone until the barrier times out.
        barrier = threading.Barrier(5, timeout=2)

        def waiting_alpaca(path, query=None):
            barrier.wait()
            return _fake_alpaca(path, query)

        def waiting_history():
            barrier.wait()
            return {"timestamp": [], "equity": []}

        with mock.patch("api_support.dashboard.fetch_portfolio_history", side_effect=waiting_history), \
                mock.patch("api_support.dashboard.alpaca_get_json", side_effect=waiting_alpaca):
            payload = _concurrent_kpis(self)
        self.assertEqual(payload["account_equity"], 2379.77)


def _concurrent_kpis(test):
    from api_support.dashboard import build_dashboard_kpis
    from tests.db_fixtures import temporary_database

    temporary_database(test)
    with mock.patch("api_support.dashboard.get_current_rankings", return_value={}):
        return build_dashboard_kpis()


if __name__ == "__main__":
    unittest.main()
