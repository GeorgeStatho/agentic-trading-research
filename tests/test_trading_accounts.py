from __future__ import annotations

import unittest

from services.trading.trading_accounts import FollowerSettings, TradingAccount, load_trading_accounts

PRODUCTION = {'PUBLIC_KEY': 'PROD-KEY', 'PRIVATE_KEY': 'PROD-SECRET'}
PRIMARY = TradingAccount('production', 'PROD-KEY', 'PROD-SECRET', 'primary', 'single_leg', 'bucket_rules')


def follower(name, key, secret, strategy=None):
    return TradingAccount(name, key, secret, 'follower', strategy or name, 'follow:production')


def keys(name, key='k', secret='s'):
    return {f'ACCOUNT_{name.upper()}_PUBLIC_KEY': key, f'ACCOUNT_{name.upper()}_PRIVATE_KEY': secret}


class PrimaryAccountTests(unittest.TestCase):
    def test_production_alone_is_the_read_only_primary(self):
        self.assertEqual(load_trading_accounts(dict(PRODUCTION)), [PRIMARY])
        self.assertEqual(load_trading_accounts({**PRODUCTION, 'TRADING_ACCOUNTS': ' '}), [PRIMARY])

    def test_without_production_keys_there_are_no_accounts(self):
        self.assertEqual(load_trading_accounts({'TRADING_ACCOUNTS': 'shares', **keys('shares')}), [])


class FollowerAccountTests(unittest.TestCase):
    def test_followers_come_after_the_primary_in_listed_order(self):
        env = {**PRODUCTION, 'TRADING_ACCOUNTS': 'shares, Debit_Spread',
               **keys('shares', 'k1', 's1'), **keys('debit_spread', 'k2', 's2')}
        self.assertEqual(load_trading_accounts(env), [PRIMARY, follower('shares', 'k1', 's1'),
                                                      follower('debit_spread', 'k2', 's2')])

    def test_strategy_can_differ_from_the_account_name(self):
        env = {**PRODUCTION, 'TRADING_ACCOUNTS': 'spreads2', **keys('spreads2'),
               'ACCOUNT_SPREADS2_STRATEGY': 'debit_spread'}
        self.assertEqual(load_trading_accounts(env), [PRIMARY, follower('spreads2', 'k', 's', 'debit_spread')])

    def test_two_followers_may_share_one_alpaca_account(self):
        env = {**PRODUCTION, 'TRADING_ACCOUNTS': 'itm_single,longer_dte', **keys('itm_single'), **keys('longer_dte')}
        self.assertEqual([account.name for account in load_trading_accounts(env)],
                         ['production', 'itm_single', 'longer_dte'])

    def test_explicit_follow_production_policy_is_accepted(self):
        env = {**PRODUCTION, 'TRADING_ACCOUNTS': 'shares', **keys('shares'),
               'ACCOUNT_SHARES_EXIT_POLICY': 'follow:production'}
        self.assertEqual(load_trading_accounts(env), [PRIMARY, follower('shares', 'k', 's')])

    def test_exit_policies_beside_follow_production(self):
        for policy in ('stock_stop', 'partial_profit', 'Stock_Stop'):
            env = {**PRODUCTION, 'TRADING_ACCOUNTS': 'shares2', **keys('shares2'),
                   'ACCOUNT_SHARES2_STRATEGY': 'shares', 'ACCOUNT_SHARES2_EXIT_POLICY': policy}
            with self.subTest(policy):
                self.assertEqual(load_trading_accounts(env)[1].exit_policy, policy.lower())

    def test_taking_blocked_picks_is_opted_into_per_account(self):
        base = {**PRODUCTION, 'TRADING_ACCOUNTS': 'shares,debit_spread', **keys('shares'), **keys('debit_spread')}
        self.assertEqual([a.blocked_picks for a in load_trading_accounts(base)[1:]], [False, False])
        on = {**base, 'ACCOUNT_SHARES_TAKE_BLOCKED': 'true', 'ACCOUNT_DEBIT_SPREAD_TAKE_BLOCKED': '1'}
        self.assertEqual([a.blocked_picks for a in load_trading_accounts(on)[1:]], [True, True])

    def test_single_contract_strategies_ignore_the_blocked_setting(self):
        # Their contract is at least production's, so it can never fit under the cap that blocked it.
        env = {**PRODUCTION, 'TRADING_ACCOUNTS': 'itm_single,longer_dte', **keys('itm_single'), **keys('longer_dte'),
               'ACCOUNT_ITM_SINGLE_TAKE_BLOCKED': 'true', 'ACCOUNT_LONGER_DTE_TAKE_BLOCKED': 'true'}
        with self.assertLogs('services.trading.trading_accounts', level='ERROR'):
            accounts = load_trading_accounts(env)
        self.assertEqual([a.blocked_picks for a in accounts[1:]], [False, False])

    def test_invalid_followers_are_dropped(self):
        cases = {
            'missing secret': {'TRADING_ACCOUNTS': 'shares', 'ACCOUNT_SHARES_PUBLIC_KEY': 'k'},
            'production keys reused': {'TRADING_ACCOUNTS': 'shares', **keys('shares', 'PROD-KEY', 'x')},
            'unknown strategy': {'TRADING_ACCOUNTS': 'iron_condor', **keys('iron_condor')},
            'single_leg follower': {'TRADING_ACCOUNTS': 'copy', **keys('copy'), 'ACCOUNT_COPY_STRATEGY': 'single_leg'},
            'unsupported exit policy': {'TRADING_ACCOUNTS': 'shares', **keys('shares'),
                                        'ACCOUNT_SHARES_EXIT_POLICY': 'bucket_rules'},
            'name of the primary': {'TRADING_ACCOUNTS': 'production', **keys('production'),
                                    'ACCOUNT_PRODUCTION_STRATEGY': 'shares'},
        }
        for label, env in cases.items():
            with self.subTest(label):
                self.assertEqual(load_trading_accounts({**PRODUCTION, **env}), [PRIMARY])

    def test_duplicate_names_are_listed_once(self):
        env = {**PRODUCTION, 'TRADING_ACCOUNTS': 'shares,shares', **keys('shares')}
        self.assertEqual(load_trading_accounts(env), [PRIMARY, follower('shares', 'k', 's')])


class FollowerSettingsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(FollowerSettings.from_env({}), FollowerSettings(0.05, 0.70, 1, 30.0, 14))

    def test_reads_env_knobs(self):
        env = {'FOLLOWER_SPREAD_WIDTH_PCT': '0.08', 'FOLLOWER_ITM_MIN_DELTA': '0.8',
               'FOLLOWER_FORCE_EXIT_DTE': '2', 'FOLLOWER_MAX_HOLD_DAYS': '10', 'FOLLOWER_LONGER_DTE_EXTRA_DAYS': '21'}
        self.assertEqual(FollowerSettings.from_env(env), FollowerSettings(0.08, 0.8, 2, 10.0, 21))


if __name__ == '__main__':
    unittest.main()
