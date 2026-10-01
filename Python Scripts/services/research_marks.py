"""Closing marks for shadow research positions, polled once per session just before the close.

The entry quote is the one frozen with each decision (Alpaca's chain); marks come from the same
vendor so an entry and its exit are never priced by different providers. The bid near the close
is that session's mark. A session the poll misses stays a gap, and the simulation reports it as
missing daily marks rather than inventing a price. Daily marks cannot validate intraday claims.
"""
from contextlib import closing
from datetime import timedelta
import json
import logging
from zoneinfo import ZoneInfo

from db_common import DB_PATH, get_connection
from market_db_modules.forward_evaluation import claim_mark_session, finish_mark_session
from market_db_modules.research_runs import DECISION_QUOTE_PROVIDER, collect_active_runs
from services.forward_evaluation_policy import ForwardEvaluationPolicy
from services.research_contract import canonical, number, utc

LOGGER = logging.getLogger(__name__)
NEW_YORK = ZoneInfo('America/New_York')
QUOTE_BATCH = 100
SYMBOL_LIMIT = 2000


def pending_mark_symbols(*, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT DISTINCT json_extract(result_json, '$.option_symbol') AS symbol
            FROM research_shadow_positions WHERE status='pending'
            AND json_extract(result_json, '$.option_symbol') IS NOT NULL ORDER BY symbol''').fetchall()
    return [row['symbol'] for row in rows]


def store_marks(quotes, *, at, db_path=DB_PATH):
    stored = 0
    with closing(get_connection(db_path)) as conn, conn:
        for symbol, quote in quotes.items():
            quoted = quote.get('timestamp')
            if not quoted or number(quote.get('bid_price')) is None or number(quote.get('ask_price')) is None:
                continue
            raw = {**quote, 'available_at': utc(at).isoformat(), 'source': 'closing_mark_poll'}
            stored += conn.execute('''INSERT INTO historical_option_quotes
                (option_symbol, quote_at, bid_price, ask_price, provider, raw_json) VALUES (?,?,?,?,?,?)
                ON CONFLICT(option_symbol, quote_at, provider) DO NOTHING''',
                (symbol.upper(), utc(quoted).isoformat(), number(quote['bid_price']), number(quote['ask_price']),
                 DECISION_QUOTE_PROVIDER, canonical(raw))).rowcount
    return stored


def fetch_latest_option_quotes(symbols):
    """Alpaca's latest quote per contract, the same feed the decision chain used."""
    from agent_helpers.market_context.common import _get_alpaca_clients, _get_field
    from alpaca.data.requests import OptionLatestQuoteRequest
    clients = _get_alpaca_clients()
    if clients is None:
        raise RuntimeError('Alpaca option data is unavailable')
    quotes = {}
    for start in range(0, len(symbols), QUOTE_BATCH):
        response = clients['option'].get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=symbols[start:start + QUOTE_BATCH]))
        for symbol, quote in dict(response).items():
            stamp = _get_field(quote, 'timestamp')
            quotes[str(symbol)] = {'bid_price': _get_field(quote, 'bid_price'), 'ask_price': _get_field(quote, 'ask_price'),
                                   'bid_size': _get_field(quote, 'bid_size'), 'ask_size': _get_field(quote, 'ask_size'),
                                   'timestamp': stamp.isoformat() if hasattr(stamp, 'isoformat') else stamp}
    return quotes


def mark_window_opens(close_at, policy):
    return utc(close_at) - timedelta(minutes=policy.mark_lead_minutes)


def collect_research_marks(*, at, close_at, fetch_quotes=fetch_latest_option_quotes, policy=None, db_path=DB_PATH):
    policy = policy or ForwardEvaluationPolicy.from_env()
    if not policy.enabled:
        return {'status': 'disabled'}
    if close_at is None or not mark_window_opens(close_at, policy) <= utc(at) < utc(close_at):
        return {'status': 'not_due'}
    session = utc(close_at).astimezone(NEW_YORK).date().isoformat()
    if not claim_mark_session(session, at=at, db_path=db_path):
        return {'status': 'already_ran', 'session_date': session}
    try:
        # Enroll today's finished decisions first, so their contracts are marked from day one.
        collect_active_runs(as_of=at, db_path=db_path)
        symbols = pending_mark_symbols(db_path=db_path)
        requested = symbols[:SYMBOL_LIMIT]
        quotes = fetch_quotes(requested) if requested else {}
        stored = store_marks(quotes, at=at, db_path=db_path)
        summary = {'symbols': len(symbols), 'requested': len(requested), 'quoted': len(quotes), 'stored': stored,
                   'truncated': len(symbols) - len(requested), 'missing': sorted(set(requested) - set(quotes))[:50]}
        finish_mark_session(session, status='complete', summary=summary, db_path=db_path)
        return {'status': 'complete', 'session_date': session, **summary}
    except Exception as exc:
        LOGGER.exception('Research closing marks failed for %s', session)
        finish_mark_session(session, status='failed', summary={'error': f'{type(exc).__name__}: {exc}'[:300]},
                            db_path=db_path)
        return {'status': 'failed', 'session_date': session, 'error': str(exc)}


def mark_status(*, limit=10, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        rows = [dict(row) for row in conn.execute(
            'SELECT * FROM research_mark_sessions ORDER BY session_date DESC LIMIT ?', (limit,))]
    for row in rows:
        row['summary'] = json.loads(row.pop('summary_json') or 'null')
    return {'sessions': rows, 'pending_symbols': len(pending_mark_symbols(db_path=db_path)),
            'provider': DECISION_QUOTE_PROVIDER}
