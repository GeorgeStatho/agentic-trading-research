"""Candidate-universe response storage, point-in-time samples and coverage reports."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
from statistics import mean, median, pstdev

from db_common import get_connection, json_text
from .bootstrap import initialize_database

SECTOR_ETFS = json.loads((Path(__file__).resolve().parents[1] / 'sector_etfs.json').read_text())


def backfill_raw_residuals(conn):
    """Recover the raw residual on rows written before it was stored separately.

    The stored value folds in the catalyst direction, and that direction is frozen on the
    same row's snapshot, so multiplying by it again is exact rather than an estimate. The
    collector only revisits rows with no forward return, so these would never resolve.
    """
    conn.execute("""UPDATE candidate_price_observations
        SET forward_raw_residual_return = forward_residual_return * CASE
            json_extract(snapshot_json, '$.direction') WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 END
        WHERE forward_raw_residual_return IS NULL AND forward_residual_return IS NOT NULL""")


def sector_symbol(state):
    return (SECTOR_ETFS.get(state.get('sector')) or {}).get('ticker')


def candidate_sector_symbol(ticker, *, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        row = conn.execute('''SELECT s.sector_key FROM companies c JOIN industries i ON c.industry_id=i.id
            JOIN sectors s ON i.sector_id=s.id WHERE c.symbol=?''', (ticker,)).fetchone()
    return sector_symbol({'sector': row['sector_key']}) if row else None


def register_response_events(*, as_of, db_path=None):
    """Freeze each classified catalyst once, independently of manager decisions."""
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        rows = conn.execute("SELECT * FROM catalysts WHERE classification_status='classified' AND ticker IS NOT NULL "
                            'AND julianday(first_public_time)<=julianday(?) AND julianday(first_seen_time)<=julianday(?)',
                            (as_of.isoformat(), as_of.isoformat())).fetchall()
        for row in rows:
            state = json.loads(row['state_json'])
            conn.execute('''INSERT INTO catalyst_response_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(catalyst_id) DO NOTHING''',
                (row['catalyst_id'], row['ticker'], sector_symbol(state), row['catalyst_family'], row['direction'],
                 row['first_public_time'], as_of.isoformat(), json_text(state)))


def load_histories(ticker, sector, *, as_of, db_path=None, captured_before=None):
    result = {}
    with closing(get_connection(db_path)) as conn, conn:
        for role, symbol in (('stock', ticker), ('market', 'SPY'), ('sector', sector)):
            rows = conn.execute('''SELECT * FROM historical_underlying_bars WHERE symbol=?
                AND timeframe IN ('1d', '1h') AND julianday(bar_at)<=julianday(?) AND close_price>0
                AND (? IS NULL OR julianday(created_at)<=julianday(?))
                ORDER BY julianday(bar_at)''',
                (symbol, as_of.isoformat(), captured_before, captured_before)).fetchall()
            series = {}
            for row in rows:
                bar = dict(row)
                raw = json.loads(bar.get('raw_json') or '{}')
                bar['available_at'] = raw.get('available_at')
                series.setdefault((bar['timeframe'], bar['provider']), []).append(bar)
            selected = []
            for timeframe in ('1d', '1h'):
                choices = [bars for (frame, _), bars in series.items() if frame == timeframe]
                if choices:
                    # Never splice different providers into one return series.
                    selected.extend(max(choices, key=lambda bars: (bars[-1]['bar_at'], len(bars))))
            result[role] = selected
    return result


def store_history(symbol, bars, *, provider, timeframe, captured_at, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        conn.executemany('''INSERT INTO historical_underlying_bars
            (symbol, bar_at, timeframe, open_price, high_price, low_price, close_price, volume,
             provider, raw_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, bar_at, timeframe, provider) DO NOTHING''',
            [(symbol, bar['bar_at'], timeframe, bar.get('open'), bar.get('high'), bar.get('low'),
              bar['close'], bar.get('volume'), provider, json_text({'available_at': bar.get('available_at')}),
              captured_at.isoformat()) for bar in bars])


def store_response(event, horizon, response, *, as_of, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''INSERT INTO catalyst_responses VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(catalyst_id, horizon_days) DO UPDATE SET status=excluded.status,
            outcome_at=excluded.outcome_at, recorded_at=excluded.recorded_at,
            residual_return=excluded.residual_return, metrics_json=excluded.metrics_json
            WHERE catalyst_responses.status != 'complete' ''',
            (event['catalyst_id'], horizon, response['status'], response.get('outcome_at'), as_of.isoformat(),
             response.get('residual_return'), json_text(response)))


def quantile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def catalyst_return_distribution(family, horizon_days, *, as_of, exclude_catalyst_id=None, db_path=None):
    """Empirical scenarios use the same maturity/availability boundaries as stats."""
    with closing(get_connection(db_path)) as conn, conn:
        rows = conn.execute('''SELECT r.catalyst_id, r.residual_return FROM catalyst_responses r
            JOIN catalyst_response_events e USING(catalyst_id)
            WHERE e.catalyst_family=? AND r.horizon_days=? AND r.status='complete'
            AND r.residual_return IS NOT NULL AND julianday(r.outcome_at)<=julianday(?)
            AND julianday(r.recorded_at)<=julianday(?) AND julianday(e.registered_at)<=julianday(?)
            AND (? IS NULL OR e.catalyst_id!=?) ORDER BY r.catalyst_id''',
            (family, horizon_days, as_of.isoformat(), as_of.isoformat(), as_of.isoformat(),
             exclude_catalyst_id, exclude_catalyst_id)).fetchall()
    return [dict(row) for row in rows]


def catalyst_return_scenarios(family, horizon_days, *, as_of, exclude_catalyst_id=None, db_path=None):
    """Total and residual responses per catalyst, under the distribution's availability rules.

    Rows written before total returns were stored keep a null `stock_return`; the caller
    counts them as missing rather than substituting the residual.
    """
    with closing(get_connection(db_path)) as conn, conn:
        rows = conn.execute('''SELECT r.catalyst_id, e.ticker, e.event_at, r.residual_return,
            json_extract(r.metrics_json, '$.stock_return') AS stock_return FROM catalyst_responses r
            JOIN catalyst_response_events e USING(catalyst_id)
            WHERE e.catalyst_family=? AND r.horizon_days=? AND r.status='complete'
            AND r.residual_return IS NOT NULL AND julianday(r.outcome_at)<=julianday(?)
            AND julianday(r.recorded_at)<=julianday(?) AND julianday(e.registered_at)<=julianday(?)
            AND (? IS NULL OR e.catalyst_id!=?) ORDER BY r.catalyst_id''',
            (family, horizon_days, as_of.isoformat(), as_of.isoformat(), as_of.isoformat(),
             exclude_catalyst_id, exclude_catalyst_id)).fetchall()
    return [dict(row) for row in rows]


def catalyst_stats(family, horizon_days, *, as_of, minimum_samples=30, exclude_catalyst_id=None, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        rows = conn.execute('''SELECT e.direction, r.residual_return, r.metrics_json
            FROM catalyst_response_events e JOIN catalyst_responses r USING(catalyst_id)
            WHERE e.catalyst_family=? AND r.horizon_days=? AND r.status='complete'
            AND r.residual_return IS NOT NULL AND julianday(r.outcome_at)<=julianday(?)
            AND julianday(r.recorded_at)<=julianday(?) AND julianday(e.registered_at)<=julianday(?)
            AND (? IS NULL OR e.catalyst_id!=?)''',
            (family, horizon_days, as_of.isoformat(), as_of.isoformat(), as_of.isoformat(),
             exclude_catalyst_id, exclude_catalyst_id)).fetchall()
    moves = [row['residual_return'] for row in rows]
    directions = [(row['residual_return'], {'positive': 1, 'negative': -1}.get(row['direction'])) for row in rows]
    signed = [move * sign for move, sign in directions if sign]
    metrics = [json.loads(row['metrics_json']) for row in rows]
    favorable = [row['mfe'] for row in metrics if row.get('mfe') is not None]
    adverse = [row['mae'] for row in metrics if row.get('mae') is not None]
    return {'catalyst_family': family, 'horizon_days': horizon_days, 'as_of': as_of.isoformat(),
            'n': len(moves), 'minimum_samples': minimum_samples, 'usable': len(moves) >= minimum_samples,
            'expected_abs_residual_move': mean(abs(move) for move in moves) if moves else None,
            'median_residual_return': median(moves) if moves else None,
            'p25': quantile(moves, .25) if moves else None, 'p75': quantile(moves, .75) if moves else None,
            'residual_std': pstdev(moves) if len(moves) > 1 else None,
            'probability_correct_direction': mean(move > 0 for move in signed) if signed else None,
            'directional_n': len(signed), 'median_MFE': median(favorable) if favorable else None,
            'median_MAE': median(adverse) if adverse else None,
            'sample_basis': 'unique classified catalysts; completed, recorded outcomes only; candidate universe',
            'excursion_basis': 'direction-adjusted residuals at sampled completed closes'}


def record_price_observation(context, gate, *, db_path=None):
    state = (context.get('catalysts') or [{}])[0]
    payload = {'gate': gate, 'ticker': context['symbol'],
               'sector_symbol': candidate_sector_symbol(context['symbol'], db_path=db_path),
               'direction': state.get('direction')}
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''INSERT INTO candidate_price_observations
            (candidate_id, catalyst_id, observed_at, bucket, snapshot_json) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO NOTHING''',
            (context['candidate_id'], context['catalyst_id'], context['created_at'],
             gate['metrics']['bucket'], json_text(payload)))


def pricing_report(*, db_path=None):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        coverage = [dict(row) for row in conn.execute('''SELECT horizon_days, status, COUNT(*) AS catalysts
            FROM catalyst_responses GROUP BY horizon_days, status ORDER BY horizon_days, status''')]
        buckets = [dict(row) for row in conn.execute('''SELECT bucket, COUNT(*) AS candidates,
            COUNT(DISTINCT catalyst_id) AS unique_catalysts, COUNT(forward_residual_return) AS resolved_candidates,
            AVG(forward_residual_return) AS mean_forward_residual_return
            FROM candidate_price_observations GROUP BY bucket''')]
        events = conn.execute('SELECT COUNT(*) FROM catalyst_response_events').fetchone()[0]
        classified = conn.execute("SELECT COUNT(*) FROM catalysts WHERE classification_status='classified'").fetchone()[0]
        total = conn.execute('SELECT COUNT(*) FROM catalysts').fetchone()[0]
        missing_ticker = conn.execute("SELECT COUNT(*) FROM catalysts WHERE classification_status='classified' AND ticker IS NULL").fetchone()[0]
        refreshes = [dict(row) for row in conn.execute('SELECT * FROM pricing_history_refreshes ORDER BY symbol')]
    return {'registered_events': events, 'classified_catalysts': classified, 'all_catalysts': total,
            'classified_without_ticker': missing_ticker, 'needs_classification_review': total - classified,
            'excluded_or_unregistered': total - events, 'response_coverage': coverage,
            'absorption_buckets': buckets, 'history_refreshes': refreshes,
            'forward_basis': '5 calendar days after candidate observation; direction-adjusted residual close return',
            'limitations': 'Candidate-universe selection bias; repeated candidates are correlated. '
                          'Underlying returns are not option counterfactual P&L; thresholds remain provisional.'}
