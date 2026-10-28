from __future__ import annotations

import unittest

from services.paper_trader_config import PaperTraderAccount, PaperTraderSettings, load_paper_traders

PRODUCTION = {'PUBLIC_KEY': 'PROD-KEY', 'PRIVATE_KEY': 'PROD-SECRET'}


class LoadPaperTradersTests(unittest.TestCase):
    def test_no_traders_when_unset_or_empty(self):
        self.assertEqual(load_paper_traders(dict(PRODUCTION)), [])
        self.assertEqual(load_paper_traders({**PRODUCTION, 'PAPER_TRADERS': ' '}), [])

    def test_configured_traders_with_keys_in_listed_order(self):
        env = {**PRODUCTION, 'PAPER_TRADERS': 'shares, Debit_Spread',
               'PAPER_TRADER_SHARES_PUBLIC_KEY': 'k1', 'PAPER_TRADER_SHARES_PRIVATE_KEY': 's1',
               'PAPER_TRADER_DEBIT_SPREAD_PUBLIC_KEY': 'k2', 'PAPER_TRADER_DEBIT_SPREAD_PRIVATE_KEY': 's2'}
        self.assertEqual(load_paper_traders(env), [PaperTraderAccount('shares', 'k1', 's1'),
                                                   PaperTraderAccount('debit_spread', 'k2', 's2')])

    def test_two_traders_may_share_one_account(self):
        env = {**PRODUCTION, 'PAPER_TRADERS': 'itm_single,longer_dte',
               'PAPER_TRADER_ITM_SINGLE_PUBLIC_KEY': 'k', 'PAPER_TRADER_ITM_SINGLE_PRIVATE_KEY': 's',
               'PAPER_TRADER_LONGER_DTE_PUBLIC_KEY': 'k', 'PAPER_TRADER_LONGER_DTE_PRIVATE_KEY': 's'}
        self.assertEqual([account.name for account in load_paper_traders(env)], ['itm_single', 'longer_dte'])

    def test_trader_without_both_keys_is_inactive(self):
        env = {**PRODUCTION, 'PAPER_TRADERS': 'shares', 'PAPER_TRADER_SHARES_PUBLIC_KEY': 'k1'}
        self.assertEqual(load_paper_traders(env), [])

    def test_trader_using_production_keys_is_refused(self):
        env = {**PRODUCTION, 'PAPER_TRADERS': 'shares',
               'PAPER_TRADER_SHARES_PUBLIC_KEY': 'PROD-KEY', 'PAPER_TRADER_SHARES_PRIVATE_KEY': 'x'}
        self.assertEqual(load_paper_traders(env), [])

    def test_unknown_and_duplicate_names_are_ignored(self):
        env = {**PRODUCTION, 'PAPER_TRADERS': 'iron_condor,shares,shares',
               'PAPER_TRADER_SHARES_PUBLIC_KEY': 'k', 'PAPER_TRADER_SHARES_PRIVATE_KEY': 's',
               'PAPER_TRADER_IRON_CONDOR_PUBLIC_KEY': 'k', 'PAPER_TRADER_IRON_CONDOR_PRIVATE_KEY': 's'}
        self.assertEqual(load_paper_traders(env), [PaperTraderAccount('shares', 'k', 's')])


class PaperTraderSettingsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(PaperTraderSettings.from_env({}), PaperTraderSettings(0.05, 0.70, 1, 30.0))

    def test_reads_env_knobs(self):
        env = {'PAPER_SPREAD_WIDTH_PCT': '0.08', 'PAPER_ITM_MIN_DELTA': '0.8',
               'PAPER_FORCE_EXIT_DTE': '2', 'PAPER_MAX_HOLD_DAYS': '10'}
        self.assertEqual(PaperTraderSettings.from_env(env), PaperTraderSettings(0.08, 0.8, 2, 10.0))


if __name__ == '__main__':
    unittest.main()
