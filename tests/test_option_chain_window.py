"""The manager's option chain covers the contracts it can target, fetched across every page."""
import unittest
from unittest.mock import patch

from agent_helpers.market_context import common, option_chain
from services.option_dte_buckets import OPTION_DTE_BUCKETS


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


class ChainWindowTests(unittest.TestCase):
    def test_default_window_contains_every_targetable_bucket(self):
        self.assertTrue(all(common.CLOSEST_EXPIRATION_GTE <= bucket.min_days
                            and bucket.max_days <= common.FARTHEST_EXPIRATION_LTE for bucket in OPTION_DTE_BUCKETS))

    def test_contract_pages_are_followed_to_the_end(self):
        pages = [{'option_contracts': ['near-1', 'near-2'], 'next_page_token': 'page-2'},
                 {'option_contracts': ['far-1'], 'next_page_token': None}]
        self.assertEqual(fetch(pages), (['near-1', 'near-2', 'far-1'], [None, 'page-2']))

    def test_endless_paging_stops_at_the_cap(self):
        with self.assertLogs(common.LOGGER.name, 'WARNING'):
            contracts, _ = fetch([{'option_contracts': ['contract'], 'next_page_token': 'again'}])
        self.assertEqual(len(contracts), common.MAXIMUM_CONTRACT_PAGES)


if __name__ == '__main__':
    unittest.main()
