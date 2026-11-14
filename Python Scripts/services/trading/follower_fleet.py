"""Mirror the primary account's entries into follower accounts and close them when it exits.

The primary (production) is read-only here: the fleet only reads what it holds. Each follower
places its own strategy's order on its own paper account and closes it with explicit legs once
the primary's position is gone, so accounts can be shared without closing each other's positions.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timezone
import math
from zoneinfo import ZoneInfo

from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
from market_db_modules import account_positions as store
from services.trading.account_strategies import OPTION_STRATEGIES, OrderPlan, Skip, build_order
from services.core.numbers import safe_float
from services.research.research_contract import utc
from services.core.trade_sides import trade_side
from services.trading.order_values import FILLED, REPLACED, WORKING, order_phase
from services.core.settings import get_settings
from services.trading.trading_accounts import PARTIAL_PROFIT, STOCK_STOP

NEW_YORK = ZoneInfo('America/New_York')
# The manager's chain keeps only near-the-money OTM strikes within $25. Followers need ITM strikes and
# spread short legs, so they take every strike within a quarter of spot either side.
FOLLOWER_CHAIN_LIMIT_PER_TYPE = 30
FOLLOWER_STRIKE_WINDOW_PCT = 0.25


def _reversed(legs):
    return [{**leg, 'side': 'sell' if leg['side'] == 'buy' else 'buy'} for leg in legs]


def _submit(gateway, client, legs, qty, *, opening):
    """Open legs as planned, or close them with the reverse of each leg."""
    if not opening:
        legs = _reversed(legs)
    if len(legs) > 1:
        return gateway.submit_multileg_market_order(
            client, legs=[{key: leg[key] for key in ('symbol', 'side', 'ratio_qty')} for leg in legs],
            qty=qty, opening=opening)
    leg = legs[0]
    if leg['asset'] == 'stock':
        return gateway.submit_stock_market_order(client, symbol=leg['symbol'], qty=qty, side=leg['side'])
    if opening:
        return gateway.submit_option_market_order(client, option_symbol=leg['symbol'], qty=qty)
    return gateway.submit_option_sell_market_order(client, option_symbol=leg['symbol'], qty=qty)


def _price(quote):
    """A quote's mid, else its last price; None when neither is readable."""
    quote = quote or {}
    return safe_float(quote.get('mid_price')) or safe_float(quote.get('price'))


def _fill_price(summary):
    # Multi-leg fills can carry either sign; prices are stored positive.
    return abs(safe_float(summary.get('filled_avg_price')) or 0.0)


def _filled_qty(summary):
    return int(safe_float(summary.get('filled_qty')) or 0)


def _fill_time(summary, now):
    filled_at = str(summary.get('filled_at') or '')
    return filled_at if filled_at and filled_at != 'None' else now.isoformat()


class FollowerFleet:
    def __init__(self, *, accounts, settings, gateway_factory, chain_fetcher, logger, db_path=None, clock=None,
                 background=False):
        accounts = list(accounts)
        self._primary = next((account for account in accounts if account.role == 'primary'), None)
        self._followers = {account.name: account for account in accounts if account.role == 'follower'}
        self._settings = settings
        self._gateway_factory = gateway_factory
        self._chain_fetcher = chain_fetcher
        self._logger = logger
        self._db_path = db_path
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._connections = {}
        # In production, mirroring runs on one worker thread so production's next order never
        # waits on a chain fetch or follower orders. Tests run it inline.
        self._background = ThreadPoolExecutor(max_workers=1, thread_name_prefix='followers') if background else None

    def _connection(self, account):
        """Gateway and client per account, created on first use."""
        if account.name not in self._connections:
            gateway = self._gateway_factory(account)
            self._connections[account.name] = (gateway, gateway.create_client())
        return self._connections[account.name]

    def _chains(self, underlying, spot, accounts):
        if spot is None or not any(account.strategy in OPTION_STRATEGIES for account in accounts):
            return None
        try:
            return self._chain_fetcher(underlying, spot)
        except Exception:
            self._logger.exception('Follower option chain fetch failed for %s', underlying)
            return {'available': False, 'buckets': {}}

    def mirror_entry(self, candidate, execution):
        """Give each follower its strategy's version of an entry the primary just submitted."""
        return self._dispatch(self._mirror, candidate, execution)

    def mirror_blocked(self, candidate, execution, premium_cap):
        """Offer a pick production skipped only for the premium cap to the followers that opted in."""
        return self._dispatch(self._mirror_blocked, candidate, execution, premium_cap)

    def _dispatch(self, work, candidate, *args):
        if self._background is None:
            return work(candidate, *args)
        future = self._background.submit(work, dict(candidate), *(dict(a) if isinstance(a, dict) else a for a in args))
        future.add_done_callback(self._log_failure)

    def _log_failure(self, future):
        if future.exception() is not None:
            self._logger.error('Follower mirroring failed', exc_info=future.exception())

    def _mirror(self, candidate, execution):
        source_order_id = str((execution.get('order') or {}).get('id') or '')
        if not execution.get('submitted') or not source_order_id or self._primary is None or not self._followers:
            return
        self._place(candidate, source_order_id, execution.get('estimated_order_cost'), store.COPY,
                    self._followers.values())

    def _mirror_blocked(self, candidate, execution, premium_cap):
        takers = [account for account in self._followers.values() if account.blocked_picks]
        cap = safe_float(premium_cap)
        if (execution.get('portfolio_guardrail') != 'premium_per_trade_cap' or not takers or self._primary is None
                or cap is None or cap <= 0):
            return
        # No production order exists, so the pick is keyed by its candidate; the prefix keeps it out of copy lookups.
        key = (candidate.get('candidate_context') or {}).get('candidate_id') or (
            f"{candidate.get('selected_option_symbol')}:{self._clock().isoformat()}")
        self._place(candidate, f'blocked:{key}', cap, store.PREMIUM_CAP_BLOCKED, takers, max_capital=cap)

    def _place(self, candidate, source_order_id, budget, entry_kind, accounts, *, max_capital=math.inf):
        underlying = str(candidate.get('symbol') or '').strip().upper()
        spot = _get_reference_stock_price(candidate.get('market_context') or {})
        chains = self._chains(underlying, spot, accounts)
        production_delta = safe_float(((candidate.get('selected_option') or {}).get('greeks') or {}).get('delta'))
        for account in accounts:
            row_id = None
            try:
                plan = build_order(account.strategy, decision=candidate.get('decision'), underlying=underlying,
                                   production_contract=candidate.get('selected_option') or {}, chains=chains,
                                   spot=spot, budget=budget, settings=self._settings)
                if isinstance(plan, OrderPlan) and plan.capital > max_capital:
                    # Sizing takes at least one unit, which could break the very cap that blocked a pick.
                    plan = Skip(account.strategy, 'over_premium_cap')
                row_id = store.record_plan(
                    account, source_account=self._primary.name, source_order_id=source_order_id,
                    source_option_symbol=candidate.get('selected_option_symbol'), underlying=underlying,
                    decision=candidate.get('decision'), budget=budget, plan=plan, spot=spot,
                    source_delta=production_delta, entry_kind=entry_kind, db_path=self._db_path)
                if row_id is None or isinstance(plan, Skip):
                    continue
                gateway, client = self._connection(account)
                order = _submit(gateway, client, [asdict(leg) for leg in plan.legs], plan.qty, opening=True)
            except Exception as exc:
                self._logger.exception('Follower %s could not mirror %s', account.name, source_order_id)
                if row_id is not None:
                    store.mark_failed(row_id, error=str(exc), db_path=self._db_path)
                continue
            try:
                store.mark_entry_order(row_id, order_id=order['id'], db_path=self._db_path)
            except Exception:
                # The order is live at the broker; marking the row failed would hide it.
                self._logger.critical('Follower %s order %s is live but unrecorded on row %s; reconcile by hand',
                                      account.name, order.get('id'), row_id, exc_info=True)

    def manage(self):
        """Resolve follower fills and close followers whose primary position is gone."""
        if self._primary is None or not self._followers:
            return
        try:
            gateway, client = self._connection(self._primary)
            held = {position['option_symbol'] for position in gateway.get_open_option_positions(client)}
        except Exception:
            # Without the primary's holdings an exit cannot be told from a read failure.
            self._logger.exception('Could not read primary holdings; follower exits skipped this tick')
            return
        now = self._clock()
        rows = store.active_positions(db_path=self._db_path)
        closed = store.closed_source_orders({row['source_order_id'] for row in rows}, db_path=self._db_path)
        for row in rows:
            account = self._followers.get(row['account'])
            if account is None:
                continue
            try:
                self._manage_position(account, row, held, closed, now)
            except Exception:
                self._logger.exception('Follower %s could not manage position %s', account.name, row['id'])

    def _order_summary(self, account, order_id):
        gateway, client = self._connection(account)
        return gateway.get_order_status_summary(client, order_id=order_id)

    def _replacement(self, row, summary):
        """A replaced order is followed through its replacement; without one the row waits as it is."""
        if str(summary.get('replaced_by') or '').strip():
            return True
        self._logger.error('Follower position %s: broker reported a replaced order without its replacement', row['id'])
        return False

    def _manage_position(self, account, row, held, closed, now):
        if row['status'] == 'pending_entry':
            if not row['entry_order_id']:
                return
            summary = self._order_summary(account, row['entry_order_id'])
            status, filled = summary.get('status'), _filled_qty(summary)
            phase = order_phase(status)
            if phase == WORKING:
                return
            if phase == REPLACED:
                if self._replacement(row, summary):
                    store.mark_entry_order(row['id'], order_id=summary['replaced_by'], db_path=self._db_path)
                return
            # An order that ended after a partial fill still left a position to manage.
            if phase != FILLED and filled <= 0:
                store.mark_failed(row['id'], error=f'entry order {status}', db_path=self._db_path)
                return
            qty = filled if 0 < filled < row['qty'] else None
            row = {**row, 'status': 'open', 'entry_at': _fill_time(summary, now), 'qty': qty or row['qty'],
                   'entry_fill_price': _fill_price(summary)}
            store.mark_entry_fill(row['id'], fill_price=_fill_price(summary), filled_at=row['entry_at'], qty=qty,
                                  db_path=self._db_path)
        elif row['status'] == 'closing':
            summary = self._order_summary(account, row['exit_order_id'])
            status = summary.get('status')
            phase = order_phase(status)
            if phase == WORKING:
                return
            if phase == REPLACED:
                if self._replacement(row, summary):
                    store.mark_closing(row['id'], order_id=summary['replaced_by'], reason=row['exit_reason'],
                                       db_path=self._db_path)
                return
            # Re-close only what is still held. A partially closed slice drops out of the P&L.
            remaining = 0 if phase == FILLED else row['qty'] - _filled_qty(summary)
            if remaining <= 0:
                store.mark_closed(row['id'], fill_price=_fill_price(summary), closed_at=_fill_time(summary, now),
                                  db_path=self._db_path)
                return
            store.reopen(row['id'], error=f'exit order {status}', qty=remaining, db_path=self._db_path)
            row = {**row, 'status': 'open', 'qty': remaining}

        gateway, client = self._connection(account)
        if row['partial_order_id'] and row['partial_fill_price'] is None:
            row = self._resolve_trim(account, row)
            if row is None:
                return
        if row['source_option_symbol'] in held and not row['source_seen_at']:
            store.mark_source_seen(row['id'], at=now.isoformat(), db_path=self._db_path)
        reason = self._exit_reason(row, held, closed, now) or self._policy_exit(gateway, row)
        if reason is None:
            self._maybe_trim(gateway, client, row)
            return
        try:
            order = _submit(gateway, client, row['legs'], row['qty'], opening=False)
        except Exception:
            self._logger.exception('Follower %s could not close position %s; retrying next tick', account.name, row['id'])
            return
        store.mark_closing(row['id'], order_id=order['id'], reason=reason, db_path=self._db_path)

    def _resolve_trim(self, account, row):
        """Record what the partial exit sold; None while it still works, so nothing else sells meanwhile."""
        summary = self._order_summary(account, row['partial_order_id'])
        phase = order_phase(summary.get('status'))
        if phase == WORKING:
            return None
        if phase == REPLACED:
            if self._replacement(row, summary):
                store.mark_partial_order(row['id'], order_id=summary['replaced_by'], db_path=self._db_path)
            return None
        # An unfilled trim is recorded as an empty slice, so it is not retried.
        sold, price = min(_filled_qty(summary), row['qty'] - 1), _fill_price(summary)
        store.mark_partial_fill(row['id'], qty=max(sold, 0), fill_price=price, db_path=self._db_path)
        return {**row, 'qty': row['qty'] - max(sold, 0), 'partial_qty': max(sold, 0), 'partial_fill_price': price}

    def _policy_exit(self, gateway, row):
        if row['exit_policy'] != STOCK_STOP or row['entry_spot'] is None:
            return None
        price = _price(gateway.get_execution_market_snapshot(option_symbol=None, underlying_symbol=row['underlying'])
                       .get('underlying'))
        return STOCK_STOP if self._stock_stop_hit(row, price) else None

    def _stock_stop_hit(self, row, price):
        side = trade_side(row['decision'])
        if price is None or side is None:
            return False
        # A call (and long shares) loses when the stock falls; a put (and short shares) when it rises.
        return side * (price - row['entry_spot']) <= -self._settings.stock_stop_pct * row['entry_spot']

    def _maybe_trim(self, gateway, client, row):
        """The partial_profit policy sells part of the position once, when it is up enough."""
        if row['exit_policy'] != PARTIAL_PROFIT or row['partial_order_id'] or row['qty'] < 2:
            return
        gain = self._gain_pct(gateway, row)
        if gain is None or gain < self._settings.partial_profit_pct:
            return
        qty = max(1, min(row['qty'] - 1, math.floor(row['qty'] * self._settings.partial_profit_fraction)))
        order = _submit(gateway, client, row['legs'], qty, opening=False)
        store.mark_partial_order(row['id'], order_id=order['id'], db_path=self._db_path)

    @staticmethod
    def _gain_pct(gateway, row):
        """Gain on the entry price, valuing each leg at its mid; None when a quote is missing."""
        value = 0.0
        for leg in row['legs']:
            stock = leg['asset'] == 'stock'
            snapshot = gateway.get_execution_market_snapshot(option_symbol=None if stock else leg['symbol'],
                                                             underlying_symbol=row['underlying'])
            mid = _price(snapshot.get('underlying' if stock else 'option'))
            if mid is None:
                return None
            value += mid if leg['side'] == 'buy' else -mid
        entry = row['entry_fill_price']
        if not entry:
            return None
        short = len(row['legs']) == 1 and row['legs'][0]['asset'] == 'stock' and row['legs'][0]['side'] == 'sell'
        return (-1 if short else 1) * (abs(value) - entry) / entry

    def _exit_reason(self, row, held, closed, now):
        # Not holding the contract alone is no exit: the primary's own order may not have filled
        # yet. It is one once the contract was seen held, or production recorded its close
        # (an entry and exit both inside one tick are never seen held). A blocked pick has no
        # production position to follow, so only the follower's own exits apply to it.
        following = row['entry_kind'] != store.PREMIUM_CAP_BLOCKED
        if following and row['source_option_symbol'] not in held and (
                row['source_seen_at'] or row['source_order_id'] in closed):
            return 'production_exit'
        today = now.astimezone(NEW_YORK).date()
        for leg in row['legs']:
            expiry = leg.get('expiration_date')
            if expiry and (date.fromisoformat(expiry) - today).days <= self._settings.force_exit_dte:
                return 'expiry_guard'
        if row['entry_at'] and (now - utc(row['entry_at'])).total_seconds() >= self._settings.max_hold_days * 86400:
            return 'max_hold'
        return None


def build_follower_fleet(accounts, *, logger, settings=None, primary_gateway=None):
    """A fleet when any follower is configured, else None. No client is created until first use.

    Pass production's own gateway as `primary_gateway` so holdings are read exactly the way
    production reads them; followers always trade on paper.
    """
    roles = {account.role for account in accounts}
    if not {'primary', 'follower'} <= roles:
        return None
    settings = settings or get_settings().followers
    if settings is None:
        # Settings logged which FOLLOWER_* value is bad; production starts without followers.
        logger.error('Follower accounts disabled: a FOLLOWER_* setting is invalid')
        return None
    from services.trading.trading_gateway import AlpacaTradingGateway

    def gateway_factory(account):
        if account.role == 'primary' and primary_gateway is not None:
            return primary_gateway
        return AlpacaTradingGateway(paper=True, logger=logger, api_key=account.api_key, api_secret=account.api_secret)

    def chain_fetcher(underlying, spot):
        from agent_helpers.market_context.option_chain import build_bucket_chains
        return build_bucket_chains(underlying, reference_stock_price=spot,
                                   per_type_limit=FOLLOWER_CHAIN_LIMIT_PER_TYPE,
                                   strike_window=spot * FOLLOWER_STRIKE_WINDOW_PCT, near_reference_only=False)

    return FollowerFleet(accounts=accounts, settings=settings, gateway_factory=gateway_factory,
                         chain_fetcher=chain_fetcher, logger=logger, background=True)
