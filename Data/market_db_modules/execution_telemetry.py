from __future__ import annotations

from contextlib import closing
import json
from db_common import get_connection, json_text
from .bootstrap import initialize_database
from .telemetry_schema import EXECUTION_COLUMNS


def store_execution_telemetry(order_id, telemetry, *, context=None, execution_version=None, conn=None, db_path=None):
    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local, local:
            return store_execution_telemetry(order_id, telemetry, context=context,
                                             execution_version=execution_version, conn=local)
    values = {key: value for key, value in telemetry.items() if key in EXECUTION_COLUMNS and value is not None}
    if context:
        values.update(candidate_id=context['candidate_id'], pipeline_version_json=json_text(context['pipeline_version']))
    if execution_version:
        values['execution_version_json'] = json_text(execution_version)
    existing = conn.execute('SELECT telemetry_json FROM option_trade_executions WHERE order_id=?', (order_id,)).fetchone()
    prior = json.loads(existing['telemetry_json'] or '{}') if existing else {}
    values['telemetry_json'] = json_text({**prior, **telemetry})
    if telemetry.get('order_status'):
        values['order_status'] = telemetry['order_status']
    if values:
        conn.execute('UPDATE option_trade_executions SET '+', '.join(f'{k}=?' for k in values)+' WHERE order_id=?',
                     (*values.values(), order_id))


def pending_execution_telemetry(*, db_path=None):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        return [dict(row) for row in conn.execute('''SELECT * FROM option_trade_executions
            WHERE fill_observed_at IS NULL OR LOWER(order_status) NOT IN
            ('filled', 'canceled', 'expired', 'rejected', 'replaced', 'not_found') ORDER BY id''')]


def execution_telemetry_report(*, start=None, end=None, db_path=None):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        return [dict(row) for row in conn.execute('''SELECT paper, order_side,
            COUNT(*) AS submitted_orders,
            COUNT(fill_observed_at) AS observed_orders,
            SUM(CASE WHEN fill_observed_at IS NULL THEN 1 ELSE 0 END) AS unobserved_orders,
            SUM(CASE WHEN filled_qty>0 THEN 1 ELSE 0 END) AS orders_with_fills,
            AVG(CASE WHEN filled_qty>=order_qty AND order_qty>0 THEN 1.0 ELSE 0.0 END) AS full_fill_rate,
            AVG(CASE WHEN filled_qty>0 THEN 1.0 ELSE 0.0 END) AS any_fill_rate,
            COUNT(fill_price) AS fill_price_samples, COUNT(slippage_vs_mid) AS slippage_samples,
            AVG(slippage_vs_mid) AS mean_slippage_vs_mid,
            AVG(slippage_vs_arrival) AS mean_slippage_vs_arrival,
            AVG(spread_paid_pct) AS mean_spread_paid_pct,
            SUM(slippage_vs_mid * filled_qty * 100) AS measured_slippage_dollars,
            SUM(option_mid_at_decision * filled_qty * 100) AS decision_premium_dollars,
            SUM(CASE WHEN would_have_filled_later IS NOT NULL THEN 1 ELSE 0 END) AS counterfactual_samples
            , NULL AS missed_trade_opportunity_pnl
            FROM option_trade_executions
            WHERE (? IS NULL OR date(submitted_at)>=?) AND (? IS NULL OR date(submitted_at)<?)
            GROUP BY paper, order_side''', (start, start, end, end))]
