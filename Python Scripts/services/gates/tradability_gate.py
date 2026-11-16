"""TRADABILITY: before the strategist, would the selector's top pick in the default bucket fit the premium cap?

Production fetches no option chain until the manager stage, so this check makes its own requests through
the manager's Alpaca helpers: one stock quote and trade, one bucket-chain fetch (call and put contracts and
their snapshots), and once per cycle the account's buying power when a percent cap is set. Each side's
top pick is chosen by the deterministic selector exactly as after the manager, then priced the way the
trade executor prices a contract. A failed request, a missing quote or an unknown cap gives an unknown
verdict, never a rejection. Shadow by default. Enforcement needs a pooled proof that at least 95% of its
rejections were hard-blocked later anyway (research_reports.PRECISION_SCORED_BLOCKS); an enforced
rejection then skips the strategist and manager calls for that pick.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from agent_helpers.deterministic_option_selector import _pick_matching_contract
from agent_helpers.market_context.alpaca_base import _get_alpaca_clients
from agent_helpers.market_context.equities import _build_current_stock_price_snapshot
from agent_helpers.market_context.option_chain import build_bucket_chains
from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
from services.core.numbers import safe_float
from services.core.settings import get_settings
from services.core.trading_config import (DEFAULT_MAX_PREMIUM_PER_TRADE_DOLLARS, DEFAULT_MAX_PREMIUM_PER_TRADE_PCT,
                                          WorkerSettings)
from services.options.option_dte_buckets import DEFAULT_MANAGER_TARGET_DTE_BUCKET
from services.options.option_pricing import CONTRACT_MULTIPLIER
from services.options.reference_price import option_reference_price
from services.research.research_promotion import guard_policy, rollout_mode

UNKNOWN_OUTCOMES = ('missing_quote', 'premium_cap_unknown')
FAILURE_TEXT_LIMIT = 300  # characters of a request error kept in the verdict


@dataclass(frozen=True)
class TradabilityPolicy:
    policy_version: str = 'tradability-v1'
    mode: str = 'shadow'
    dte_bucket: str = DEFAULT_MANAGER_TARGET_DTE_BUCKET
    max_premium_per_trade_pct: float = DEFAULT_MAX_PREMIUM_PER_TRADE_PCT
    max_premium_per_trade_dollars: float = DEFAULT_MAX_PREMIUM_PER_TRADE_DOLLARS

    @classmethod
    def from_env(cls) -> 'TradabilityPolicy':
        settings = WorkerSettings.from_env()
        return guard_policy(cls(mode=rollout_mode('TRADABILITY_MODE', ('shadow', 'enforce', 'disabled')),
                                max_premium_per_trade_pct=settings.max_premium_per_trade_pct,
                                max_premium_per_trade_dollars=settings.max_premium_per_trade_dollars),
                            {'mode': 'TRADABILITY'})


def cycle_premium_cap(settings):
    """The cycle's per-trade premium cap, reading buying power once and only for a percent cap; None if unknown."""
    if settings.max_premium_per_trade_pct <= 0:
        return settings.resolve_max_premium_per_trade(0.0)
    try:
        clients = _get_alpaca_clients()
        buying_power = safe_float(getattr(clients['trading'].get_account(), 'buying_power', None)) if clients else None
    except Exception:
        return None
    return None if buying_power is None else settings.resolve_max_premium_per_trade(buying_power)


def _premium(contract):
    return option_reference_price(contract) * CONTRACT_MULTIPLIER


def _cap_outcome(premium, premium_cap):
    if premium <= 0:
        return 'missing_quote'
    if premium_cap is None:
        return 'premium_cap_unknown'
    if 0 < premium_cap < premium:  # a zero cap means uncapped
        return 'over_premium_cap'
    return 'tradable'


def _side(side, contracts, market_context, premium_cap, policy):
    pick, _ = _pick_matching_contract(decision=side, option_contracts=contracts, market_context=market_context,
                                      target_dte_bucket=policy.dte_bucket)
    if pick is None:
        return {'outcome': 'no_eligible_contract'}
    premium = round(_premium(pick), 2)
    return {'outcome': _cap_outcome(premium, premium_cap), 'option_symbol': pick.get('symbol'), 'expiration_date': pick.get('expiration_date'),
            'strike_price': safe_float(pick.get('strike_price')), 'premium': premium if premium > 0 else None}


def _verdict(would_allow, reasons, metrics, policy):
    return {'stage': 'TRADABILITY', 'would_allow': would_allow, 'reason_codes': reasons,
            'allowed': would_allow is not False or policy.mode != 'enforce', 'enforcement_mode': policy.mode,
            'policy_version': policy.policy_version, 'metrics': metrics, 'thresholds': asdict(policy)}


def evaluate_tradability(contracts, *, market_context, premium_cap, policy):
    """Tradable when either side's top pick fits the cap; rejected only when both sides definitely do not."""
    metrics = {'dte_bucket': policy.dte_bucket, 'premium_cap': premium_cap, 'contract_count': len(contracts),
               'reference_stock_price': _get_reference_stock_price(market_context)}
    for side in ('call', 'put'):
        metrics[side] = _side(side, contracts, market_context, premium_cap, policy)
    outcomes = {metrics[side]['outcome'] for side in ('call', 'put')}
    if 'tradable' in outcomes:
        return _verdict(True, ['tradable'], metrics, policy)
    unknown = sorted(outcomes & set(UNKNOWN_OUTCOMES))
    return _verdict(None, unknown, metrics, policy) if unknown else _verdict(False, sorted(outcomes), metrics, policy)


def check_tradability(symbol, *, premium_cap, policy):
    """Fetch the default bucket's chain and judge it. Never raises: a failure is an unknown verdict."""
    try:
        market_context = {'current_stock_price': _build_current_stock_price_snapshot({'symbol': symbol})}
        chains = build_bucket_chains(symbol, reference_stock_price=_get_reference_stock_price(market_context),
                                     per_type_limit=get_settings().manager_context.option_selection_limit_per_type)
    except Exception as exc:
        return _verdict(None, ['chain_request_failed'], {'dte_bucket': policy.dte_bucket, 'premium_cap': premium_cap,
                                                          'failure': f'{type(exc).__name__}: {exc}'[:FAILURE_TEXT_LIMIT]}, policy)
    if not chains.get('available'):
        return _verdict(None, ['chain_unavailable'], {'dte_bucket': policy.dte_bucket, 'premium_cap': premium_cap,
                                                       'failure': str(chains.get('error') or '')[:FAILURE_TEXT_LIMIT]}, policy)
    verdict = evaluate_tradability((chains.get('buckets') or {}).get(policy.dte_bucket) or [],
                                   market_context=market_context, premium_cap=premium_cap, policy=policy)
    verdict.setdefault('metrics', {})['raw_contract_count'] = chains.get('raw_contract_count')
    return verdict
