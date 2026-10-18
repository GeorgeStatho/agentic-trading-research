"""Frozen catalyst-gate observations and executed-outcome coverage reports."""
from contextlib import closing
from db_common import get_connection, json_text
from .bootstrap import initialize_database


def record_catalyst_gate_observation(context, gate, *, db_path=None):
    initialize_database(db_path=db_path)
    metrics = gate['metrics']
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''INSERT INTO catalyst_gate_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, stage) DO NOTHING''',
            (context['candidate_id'], context['catalyst_id'], gate['stage'], context['created_at'],
             metrics.get('catalyst_age_minutes'), metrics.get('remaining_information_value'),
             gate['would_allow'], gate['allowed'], gate['enforcement_mode']=='shadow', gate['policy_version'],
             json_text(metrics), json_text(gate['thresholds'])))


def freshness_outcome_report(*, start=None, end=None, db_path=None):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        rows = [dict(row) for row in conn.execute('''WITH cohort AS (
            SELECT *, NTILE(4) OVER (ORDER BY age_minutes, candidate_id) AS age_quartile
            FROM catalyst_gate_observations WHERE stage='FRESHNESS' AND age_minutes IS NOT NULL AND shadow=1
            AND (? IS NULL OR date(observed_at)>=?) AND (? IS NULL OR date(observed_at)<?)
        ), outcomes AS (
            SELECT candidate_id, AVG(realized_pnl_pct) AS realized_pnl_pct FROM manager_decision_history
            WHERE closed_at IS NOT NULL AND realized_pnl_pct IS NOT NULL AND trade_executed=1 GROUP BY candidate_id
        ) SELECT c.age_quartile, COUNT(*) AS candidates, MIN(age_minutes) AS minimum_age_minutes,
            MAX(age_minutes) AS maximum_age_minutes, SUM(CASE WHEN would_allow=0 THEN 1 ELSE 0 END) AS would_reject,
            COUNT(o.realized_pnl_pct) AS resolved_candidates, AVG(o.realized_pnl_pct) AS mean_realized_pnl_pct
            FROM cohort c LEFT JOIN outcomes o ON o.candidate_id=c.candidate_id GROUP BY c.age_quartile''',
            (start, start, end, end))]
        unknown = conn.execute("SELECT COUNT(*) FROM catalyst_gate_observations WHERE stage='FRESHNESS' AND age_minutes IS NULL AND shadow=1 "
                               'AND (? IS NULL OR date(observed_at)>=?) AND (? IS NULL OR date(observed_at)<?)',
                               (start, start, end, end)).fetchone()[0]
    return {'quartiles': rows, 'missing_age_candidates': unknown,
            'outcome_basis': 'executed candidates only; no counterfactual returns for untraded candidates',
            'quartile_basis': 'UTC cohort; original catalyst calendar age; equal-count buckets, ties ordered by candidate ID'}
