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


@dataclass(frozen=True)
class TradingAccount:
    name: str
    api_key: str
    api_secret: str
    role: str  # 'primary' or 'follower'
    strategy: str
    exit_policy: str


@dataclass(frozen=True)
class FollowerSettings:
    spread_width_pct: float = 0.05
    itm_min_delta: float = 0.70
    force_exit_dte: int = 1
    max_hold_days: float = 30.0

    @classmethod
    def from_env(cls, env=None) -> 'FollowerSettings':
        env = os.environ if env is None else env
        defaults = cls()
        return cls(spread_width_pct=float(env.get('FOLLOWER_SPREAD_WIDTH_PCT', defaults.spread_width_pct)),
                   itm_min_delta=float(env.get('FOLLOWER_ITM_MIN_DELTA', defaults.itm_min_delta)),
                   force_exit_dte=int(env.get('FOLLOWER_FORCE_EXIT_DTE', defaults.force_exit_dte)),
                   max_hold_days=float(env.get('FOLLOWER_MAX_HOLD_DAYS', defaults.max_hold_days)))


def _follower(name, env, primary):
    prefix = f'ACCOUNT_{name.upper()}_'
    key, secret = (str(env.get(prefix + part) or '').strip() for part in ('PUBLIC_KEY', 'PRIVATE_KEY'))
    strategy = str(env.get(prefix + 'STRATEGY') or name).strip().lower()
    exit_policy = str(env.get(prefix + 'EXIT_POLICY') or FOLLOW_PRIMARY).strip().lower()
    if not key or not secret:
        return None, 'missing keys'
    if strategy not in STRATEGIES or strategy == PRIMARY_STRATEGY:
        return None, f'unsupported strategy {strategy!r}'
    if exit_policy != FOLLOW_PRIMARY:
        return None, f'unsupported exit policy {exit_policy!r}'
    if key == primary.api_key:
        return None, 'uses the primary account keys'
    return TradingAccount(name, key, secret, 'follower', strategy, exit_policy), ''


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
