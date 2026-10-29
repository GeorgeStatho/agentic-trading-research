"""Followers need in-the-money and far strikes that the manager's near-the-money chain never contains."""
from __future__ import annotations

import logging
import unittest
from datetime import date
from unittest.mock import Mock, patch

from agent_helpers.market_context import option_chain
from services.follower_fleet import build_follower_fleet
from services.trading_accounts import FollowerSettings, TradingAccount

STRIKES = (300.0, 385.0, 405.0, 415.0, 440.0, 500.0)
TODAY = date(2026, 10, 29)
EXPIRY = '2026-11-27'  # 29 days out: the 20_30 bucket


def listed(_symbol, *, contract_type, **_window):
    kind = 'call' if contract_type == option_chain.ContractType.CALL else 'put'
    return [{'symbol': f'TSLA261127{kind[0].upper()}{int(strike * 1000):08d}', 'underlying_symbol': 'TSLA',
             'type': kind, 'expiration_date': EXPIRY, 'strike_price': strike} for strike in STRIKES]


class FollowerChainTests(unittest.TestCase):
    def test_follower_chain_keeps_itm_and_far_strikes_in_a_price_scaled_window(self):
        clients = {'option': Mock(get_option_chain=Mock(return_value={}))}
        with patch.object(option_chain, '_get_alpaca_clients', return_value=clients), \
                patch.object(option_chain, '_fetch_option_contracts', side_effect=listed) as fetch:
            chains = option_chain.build_bucket_chains('TSLA', reference_stock_price=400.0, per_type_limit=30,
                                                      today=TODAY, strike_window=100.0, near_reference_only=False)
        window = fetch.call_args.kwargs
        self.assertEqual((window['strike_price_gte'], window['strike_price_lte']), (300.0, 500.0))
        calls = sorted(item['strike_price'] for item in chains['buckets']['20_30'] if item['contract_type'] == 'call')
        # 385 is in the money, 440 is beyond the old flat $25 window; both must be there.
        self.assertEqual(calls, list(STRIKES))

    def test_fleet_asks_for_a_quarter_of_spot_either_side_untrimmed(self):
        accounts = [TradingAccount('production', 'k', 's', 'primary', 'single_leg', 'bucket_rules'),
                    TradingAccount('itm', 'k2', 's2', 'follower', 'itm_single', 'follow:production')]
        fleet = build_follower_fleet(accounts, logger=logging.getLogger('test'), settings=FollowerSettings())
        with patch.object(option_chain, 'build_bucket_chains', return_value={'available': True, 'buckets': {}}) as fetch:
            fleet._chain_fetcher('TSLA', 400.0)
        self.assertEqual((fetch.call_args.kwargs['strike_window'], fetch.call_args.kwargs['near_reference_only']),
                         (100.0, False))


if __name__ == '__main__':
    unittest.main()
