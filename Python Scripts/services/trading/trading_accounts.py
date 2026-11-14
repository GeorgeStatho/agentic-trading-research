"""Trading accounts: production as the read-only primary, plus follower accounts that mirror it.

Production keeps its own execution, exits and bookkeeping; registering it here only lets
followers read what it holds and lets reports put its results next to theirs.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os

LOGGER = logging.getLogger(__name__)

STRATEGIES = ('single_leg', 'debit_spread', 'itm_single', 'longer_dte', 'shares')
PRIMARY_NAME = 'production'
PRIMARY_STRATEGY = 'single_leg'
PRIMARY_EXIT_POLICY = 'bucket_rules'
FOLLOW_PRIMARY = f'follow:{PRIMARY_NAME}'
# Each variant still ends with production's exit; it only adds an earlier exit (stock_stop) or a one-time trim.
STOCK_STOP = 'stock_stop'
PARTIAL_PROFIT = 'partial_profit'
EXIT_POLICIES = (FOLLOW_PRIMARY, STOCK_STOP, PARTIAL_PROFIT)
# Structures that can cost less than production's contract. A single ITM or longer-dated contract costs at
# least production's, which the premium cap already refused, so those never take capped picks.
BLOCKED_PICK_STRATEGIES = ('debit_spread', 'shares')


@dataclass(frozen=True)
class TradingAccount:
    name: str
    api_key: str
    api_secret: str
    role: str  # 'primary' or 'follower'
    strategy: str
    exit_policy: str
    blocked_picks: bool = False  # also trade picks production skipped only for the premium cap


def _follower(name, env, primary):
    prefix = f'ACCOUNT_{name.upper()}_'
    key, secret = (str(env.get(prefix + part) or '').strip() for part in ('PUBLIC_KEY', 'PRIVATE_KEY'))
    strategy = str(env.get(prefix + 'STRATEGY') or name).strip().lower()
    exit_policy = str(env.get(prefix + 'EXIT_POLICY') or FOLLOW_PRIMARY).strip().lower()
    if not key or not secret:
        return None, 'missing keys'
    if strategy not in STRATEGIES or strategy == PRIMARY_STRATEGY:
        return None, f'unsupported strategy {strategy!r}'
    if exit_policy not in EXIT_POLICIES:
        return None, f'unsupported exit policy {exit_policy!r}'
    if key == primary.api_key:
        return None, 'uses the primary account keys'
    blocked = str(env.get(prefix + 'TAKE_BLOCKED') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    if blocked and strategy not in BLOCKED_PICK_STRATEGIES:
        LOGGER.error('Follower account %r: TAKE_BLOCKED ignored for strategy %r', name, strategy)
        blocked = False
    return TradingAccount(name, key, secret, 'follower', strategy, exit_policy, blocked), ''


def load_trading_accounts(env=None) -> list[TradingAccount]:
    """The primary first, then each valid follower listed in TRADING_ACCOUNTS."""
    env = os.environ if env is None else env
    key, secret = (str(env.get(name) or '').strip() for name in ('PUBLIC_KEY', 'PRIVATE_KEY'))
    if not key or not secret:
        return []
    primary = TradingAccount(PRIMARY_NAME, key, secret, 'primary', PRIMARY_STRATEGY, PRIMARY_EXIT_POLICY)
    accounts = [primary]
    for name in dict.fromkeys(part.strip().lower() for part in str(env.get('TRADING_ACCOUNTS') or '').split(',')):
        if not name:
            continue
        if name == PRIMARY_NAME:
            LOGGER.error('Follower account %r ignored: the name is reserved for the primary', name)
            continue
        account, problem = _follower(name, env, primary)
        if account is None:
            LOGGER.error('Follower account %r ignored: %s', name, problem)
            continue
        accounts.append(account)
    return accounts
