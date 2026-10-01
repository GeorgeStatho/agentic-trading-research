"""Immutable decision-time economics reports and a read-only shadow-period summary."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json

from db_common import DB_PATH, get_connection
from research_foundation_contracts import timestamp
from .bootstrap import initialize_database
from .foundation_store import canonical, digest, insert_record, load_record, read_record


ECONOMICS_STAGES = ('TRADE_ECONOMICS', 'TRADE_ECONOMICS_SUBMISSION')


def record_candidate_economics_report(report, *, db_path=DB_PATH):
    """Store once under a content identity; the package must belong to the candidate's company."""
    initialize_database(db_path=db_path)
    data = json.loads(canonical({**report, 'created_at': timestamp(report['created_at']),
                                 'valid_until': timestamp(report['valid_until'])}))
    data.pop('report_id', None)
    data['report_id'] = 'economics:' + digest(data)
    with closing(get_connection(db_path)) as conn, conn:
        candidate = conn.execute('SELECT symbol FROM candidates WHERE candidate_id=?', (data['candidate_id'],)).fetchone()
        if candidate is None or candidate['symbol'] != data['symbol']:
            raise ValueError('Economics report does not match a recorded candidate')
        if data.get('package_id'):
            package = read_record(conn, 'evidence_packages', 'package_id', data['package_id'])
            company = conn.execute('SELECT symbol FROM companies WHERE id=?', (package['company_id'],)).fetchone()
            if company is None or company['symbol'] != data['symbol']:
                raise ValueError('Economics report package belongs to another company')
            if timestamp(package['information_cutoff_time']) > data['created_at']:
                raise ValueError('Economics report includes future evidence')
        return insert_record(conn, 'candidate_economics_reports', 'report_id', data, {
            'candidate_id': data['candidate_id'], 'package_id': data.get('package_id'),
            'catalyst_id': (data.get('catalyst') or {}).get('catalyst_id'),
            'selected_option_symbol': data.get('selected_option_symbol'), 'outcome': data['outcome'],
            'policy_version': data['assumptions']['policy']['policy_version'], 'created_at': data['created_at'],
            'valid_until': data['valid_until']})


def get_candidate_economics_report(report_id, *, db_path=DB_PATH):
    return load_record(db_path, 'candidate_economics_reports', 'report_id', report_id)


def _since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat() if days else None


def _window(column, since):
    return (f' AND julianday({column})>=julianday(?)', [since]) if since else ('', [])


def trade_economics_summary(*, days=7, db_path=DB_PATH):
    """Descriptive monitoring only; promotion still needs Part 6 counterfactual reports."""
    since = _since(days)
    clause, values = _window('created_at', since)
    with closing(get_connection(db_path)) as conn:
        reports = []
        for row in conn.execute('SELECT record_json, record_hash FROM candidate_economics_reports WHERE 1=1'
                                + clause + ' ORDER BY created_at', values):
            record = json.loads(row['record_json'])
            if digest(record) != row['record_hash']:
                raise ValueError('Corrupt candidate_economics_reports record hash')
            reports.append(record)
        rejection_clause, rejection_values = _window('r.occurred_at', since)
        rejections = conn.execute(f'''SELECT r.stage, r.reason_code, r.shadow, COUNT(*) AS count,
            SUM(EXISTS(SELECT 1 FROM candidate_snapshots s WHERE s.candidate_id=r.candidate_id
                       AND s.stage='order_submitted')) AS submitted
            FROM candidate_rejections r WHERE r.stage IN (?, ?){rejection_clause}
            GROUP BY r.stage, r.reason_code, r.shadow ORDER BY r.stage, count DESC''',
            (*ECONOMICS_STAGES, *rejection_values)).fetchall()
    selected = [next((row for row in report['contracts'] if row.get('selected')), {}) for report in reports]
    outcomes = [row.get('outcomes') or {} for row in selected]
    expected = [row['expected_net_pnl'] for row in outcomes if row.get('expected_net_pnl') is not None]
    return {
        'window_days': days or None, 'reports': len(reports),
        'outcomes': dict(Counter(report['outcome'] for report in reports).most_common()),
        'coverage': dict(Counter(report['coverage_status'] for report in reports).most_common()),
        'selected_contract_status': dict(Counter(row.get('status', 'no_selected_contract') for row in selected).most_common()),
        'expected_value_available': sum(row.get('expected_value_available') is True for row in outcomes),
        'mean_selected_expected_net_pnl': sum(expected) / len(expected) if expected else None,
        'rejections': [{'stage': row['stage'], 'reason_code': row['reason_code'], 'shadow': bool(row['shadow']),
                        'candidates': row['count'], 'orders_submitted_anyway': row['submitted']}
                       for row in rejections],
        'basis': 'decision-time estimates; not realized outcomes or promotion evidence',
    }
