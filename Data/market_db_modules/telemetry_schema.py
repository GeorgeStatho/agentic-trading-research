"""Additive columns shared by fresh schemas and legacy-database migrations."""
EXECUTION_COLUMNS = {
    **{name: 'REAL' for name in (
        'underlying_at_decision', 'option_bid_at_decision', 'option_ask_at_decision',
        'option_mid_at_decision', 'submitted_price', 'fill_price', 'underlying_at_fill',
        'slippage_vs_mid', 'slippage_vs_arrival', 'arrival_mid', 'arrival_spread',
        'spread_paid_pct', 'seconds_to_fill', 'underlying_move_while_working',
        'option_mid_move_while_working', 'filled_qty',
    )},
    'fill_timestamp': 'TEXT', 'repricing_count': 'INTEGER DEFAULT 0',
    'cancel_reason': 'TEXT', 'would_have_filled_later': 'INTEGER',
    'candidate_id': 'TEXT', 'pipeline_version_json': 'TEXT', 'execution_version_json': 'TEXT',
    'telemetry_json': 'TEXT', 'fill_observed_at': 'TEXT',
}
LINK_COLUMNS = {'candidate_id': 'TEXT', 'pipeline_version_json': 'TEXT'}


def migrate_telemetry_columns(conn):
    for table, columns in (('option_trade_executions', EXECUTION_COLUMNS),
                           ('manager_decision_history', LINK_COLUMNS), ('position_theses', LINK_COLUMNS),
                           ('position_outcomes', {'benchmark_json': 'TEXT'}),
                           ('candidate_price_observations', {'forward_raw_residual_return': 'REAL'})):
        existing = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
        if existing:
            for name, kind in columns.items():
                if name not in existing:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
