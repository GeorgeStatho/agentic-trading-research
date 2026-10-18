"""Catalyst expiry and IV-collapse rungs, with explicit clocks and missing data."""
from contextlib import closing
from datetime import datetime, timezone

from catalyst_models import timestamp
from db_common import get_connection
from services.contract_policy import ContractPolicy
from services.execution_telemetry import number

EXIT_PRIORITY = ('stop_loss', 'thesis_invalidated', 'CATALYST_EXPIRED', 'near_expiration',
                 'VOLATILITY_REGIME_CHANGE', 'trailing_profit_stop', 'momentum_history_failed',
                 'static_take_profit', 'first_scale_out_trigger', 'second_scale_out_trigger')


def evaluate_position_rungs(inputs, *, as_of=None, policy=None):
    policy = policy or ContractPolicy.from_env()
    now = as_of or datetime.now(timezone.utc)
    opened = timestamp(inputs.get('opened_at'))
    age = (now - opened).total_seconds() / 86400 if opened and opened <= now else None
    horizon = number(inputs.get('expected_horizon_days'))
    sessions, resolution = number(inputs.get('sessions_held')), number(inputs.get('p75_resolution_sessions'))
    use_sessions = resolution is not None and resolution > 0 and sessions is not None
    expired = sessions > resolution if use_sessions else age > horizon if age is not None and horizon and horizon > 0 else None
    entry_iv, current_iv, entry_stock, current_stock = [number(inputs.get(key)) for key in
        ('entry_iv', 'current_iv', 'entry_underlying_price', 'current_underlying_price')]
    iv_at, stock_at = timestamp(inputs.get('iv_at')), timestamp(inputs.get('underlying_at'))
    fresh = all(at and 0 <= (now - at).total_seconds() <= 60 for at in (iv_at, stock_at))
    valid = fresh and all(value is not None and value > 0 for value in (entry_iv, current_iv, entry_stock, current_stock))
    collapse = 1 - current_iv / entry_iv if valid else None
    move = abs(current_stock / entry_stock - 1) if valid else None
    vol_exit = collapse >= policy.iv_collapse_fraction and move <= policy.underlying_stagnation_fraction if valid else None
    reasons = []
    if expired:
        reasons.append('CATALYST_EXPIRED')
    if vol_exit:
        reasons.append('VOLATILITY_REGIME_CHANGE')
    # Cached arbiter recommendations are observational, never a new authority to
    # bypass the validator's kill switch, consistency checks or daily exit cap.
    thesis_at = timestamp(inputs.get('thesis_verdict_at'))
    thesis_shadow = inputs.get('thesis_arbiter_action') in {'close', 'close_half'} and thesis_at is not None and 0 <= (now - thesis_at).total_seconds() <= 86400
    return {'policy_version': policy.version, 'mode': policy.exit_mode,
            'active_reasons': reasons if policy.exit_mode == 'enforce' else [],
            'shadow_reasons': (['thesis_invalidated'] if thesis_shadow else []) + reasons,
            'inputs': inputs, 'metrics': {'calendar_days_held': age, 'sessions_held': sessions,
                'expiry_clock': 'observed_sessions' if use_sessions else 'calendar_days_fallback',
                'expiry_threshold': resolution if use_sessions else horizon,
                'catalyst_expired': expired, 'iv_collapse_fraction': collapse,
                'absolute_underlying_move': move, 'volatility_exit': vol_exit},
            'thresholds': {'iv_collapse_fraction': policy.iv_collapse_fraction,
                           'underlying_stagnation_fraction': policy.underlying_stagnation_fraction}}


def fetch_dated_position_iv(position):
    from agent_helpers.market_context.option_chain import (
        OptionChainRequest, _get_alpaca_clients, _normalize_option_snapshot_map, _get_field, _safe_float,
    )
    clients = _get_alpaca_clients()
    if clients is None or OptionChainRequest is None:
        return {}
    chain = clients['option'].get_option_chain(OptionChainRequest(
        underlying_symbol=position['underlying_symbol'], expiration_date=position['expiration_date'],
        strike_price_gte=position['strike'], strike_price_lte=position['strike']))
    snapshot = _normalize_option_snapshot_map(chain).get(position['symbol'])
    return {'iv': _safe_float(_get_field(snapshot, 'implied_volatility')),
            'quote_at': str(_get_field(_get_field(snapshot, 'latest_quote'), 'timestamp') or '')} if snapshot else {}


def build_position_exit_context(option_symbol, parsed, stock_quote, *, as_of=None, db_path=None,
                                iv_fetcher=fetch_dated_position_iv):
    from market_db_modules.bootstrap import initialize_database
    initialize_database(db_path=db_path)
    now = as_of or datetime.now(timezone.utc)
    policy = ContractPolicy.from_env()
    with closing(get_connection(db_path)) as conn, conn:
        thesis = conn.execute("SELECT * FROM position_theses WHERE option_symbol=? AND status='open' ORDER BY id DESC LIMIT 1",
                              (option_symbol,)).fetchone()
        if thesis is None:
            return evaluate_position_rungs({'status': 'missing_entry_thesis'}, as_of=now, policy=policy)
        outcome = conn.execute('''SELECT * FROM position_outcomes WHERE entry_order_id=? AND closed_at IS NULL LIMIT 1''',
                               (thesis['trade_execution_order_id'],)).fetchone()
        verdict = conn.execute('''SELECT * FROM thesis_validation_runs WHERE thesis_id=? AND julianday(run_at)<=julianday(?)
            ORDER BY julianday(run_at) DESC LIMIT 1''', (thesis['id'], now.isoformat())).fetchone()
    inputs = {'status': 'captured', 'thesis_id': thesis['id'], 'candidate_id': thesis['candidate_id'],
              'opened_at': outcome['opened_at'] if outcome else thesis['opened_at'],
              'expected_horizon_days': thesis['expected_horizon_days'],
              'entry_iv': outcome['iv_at_entry'] if outcome else None,
              'entry_underlying_price': outcome['entry_underlying_price'] if outcome else None,
              'current_underlying_price': stock_quote.get('price'), 'underlying_at': stock_quote.get('timestamp'),
              'thesis_arbiter_action': verdict['arbiter_action'] if verdict else None,
              'thesis_verdict_at': verdict['run_at'] if verdict else None,
              'p75_resolution_sessions': None, 'sessions_held': None}
    if inputs['entry_iv'] and policy.exit_mode != 'disabled':
        try:
            current = iv_fetcher({**parsed, 'symbol': option_symbol})
            inputs.update(current_iv=current.get('iv'), iv_at=current.get('quote_at'))
        except Exception as exc:
            inputs['iv_error'] = str(exc)
    return evaluate_position_rungs(inputs, as_of=now, policy=policy)


def shadow_exit_comparison(action, context):
    hard_hold = action.get('reason') in {'data_invalid', 'no_position_qty', 'recent_fill_reconciliation', 'pending_exit_order',
                                       'stop_loss_pending_confirmation', 'stop_loss_underlying_hold'}
    active = set(action.get('coactive_exit_reasons') or [])
    shadow = [] if context.get('mode') == 'disabled' else context.get('shadow_reasons') or []
    ordered = [reason for reason in EXIT_PRIORITY if reason in active or reason in shadow]
    return {**context, 'legacy_or_active_reason': action.get('reason'),
            'would_exit_reason': action.get('reason') if hard_hold else ordered[0] if ordered else None,
            'coactive_shadow_reasons': [] if hard_hold else ordered}
