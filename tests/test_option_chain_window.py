"""The manager's option chain covers the contracts it can target, fetched across every page."""
import unittest
from unittest.mock import patch

from agent_helpers.market_context import option_chain


class FakeTradingClient:
    def __init__(self, pages):
        self.pages = pages
        self.page_tokens = []

    def get_option_contracts(self, request):
        self.page_tokens.append(request.page_token)
        return self.pages[min(len(self.page_tokens), len(self.pages)) - 1]


def fetch(pages):
    client = FakeTradingClient(pages)
    with patch.object(option_chain, '_get_alpaca_clients', return_value={'trading': client}):
        contracts = option_chain._fetch_option_contracts(
            'NVDA', contract_type=None, expiration_date=None, expiration_date_gte='2026-10-13',
            expiration_date_lte='2026-11-30', strike_price_gte=None, strike_price_lte=None, limit=2)
    return contracts, client.page_tokens


def default_strike_filters(spot):
    """The strike range requested when the caller gives none; no contracts come back."""
    with patch.object(option_chain, '_get_alpaca_clients', return_value={'trading': None, 'option': None}), \
            patch.object(option_chain, '_fetch_option_contracts', return_value=[]):
        snapshot = option_chain._build_option_market_snapshot(
            'TEST', reference_stock_price=spot, expiration_date=None, expiration_date_gte='2026-11-28',
            expiration_date_lte='2026-12-18', strike_price_gte=None, strike_price_lte=None,
            contract_limit_per_type=7)
    filters = snapshot['selection_filters']
    return filters['effective_strike_price_gte'], filters['effective_strike_price_lte']


class ChainWindowTests(unittest.TestCase):
    def test_contract_pages_are_followed_to_the_end(self):
        pages = [{'option_contracts': ['near-1', 'near-2'], 'next_page_token': 'page-2'},
                 {'option_contracts': ['far-1'], 'next_page_token': None}]
        self.assertEqual(fetch(pages), (['near-1', 'near-2', 'far-1'], [None, 'page-2']))

    def test_strike_window_widens_with_share_price(self):
        # BLK at $1,170 lists strikes $10 apart: +/- $25 holds only two call strikes.
        low, high = default_strike_filters(1170.69)
        self.assertAlmostEqual(low, 1170.69 * 0.92, places=2)
        self.assertAlmostEqual(high, 1170.69 * 1.08, places=2)

    def test_strike_window_stays_25_dollars_for_ordinary_prices(self):
        self.assertEqual(default_strike_filters(120.0), (95.0, 145.0))


class SelectionRefreshTests(unittest.TestCase):
    def test_post_decision_refresh_fetches_a_larger_pool_than_the_prompt(self):
        from agent_stages import manager_runner

        calls = []
        def record(company, **kwargs):
            calls.append(kwargs)
            return {'option_market': {'contract_count': 0}}

        with patch.object(manager_runner, 'build_market_context', side_effect=record):
            manager_runner._refresh_market_context_for_target_dte_bucket(
                company={'symbol': 'BLK'}, market_context={'option_market': {'contract_count': 8}},
                recommendation={'decision': 'call', 'target_dte_bucket': '30_45'},
                option_expiration_date=None, option_expiration_date_gte=None, option_expiration_date_lte=None,
                option_strike_price_gte=None, option_strike_price_lte=None, option_contract_limit_per_type=7)

        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0]['option_contract_limit_per_type'], 30)


if __name__ == '__main__':
    unittest.main()
