"""Forward cohorts, frozen decision observations and paired incumbent/challenger evaluations."""
from contextlib import closing
from datetime import timedelta
import json

from db_common import get_connection
from research_foundation_contracts import OBSERVATION_PROTOCOL
from services.research_contract import utc
from services.research_promotion import PAIRED_PREFIX, source_hash
from .bootstrap import initialize_database
from .foundation_store import canonical, digest
from .research_observations import bind_candidate_observation
from .research_runs import insert_run

FINAL_EVALUATIONS = ('evaluated', 'abstained', 'unavailable')


def iso(value):
    return utc(value).isoformat()


def _active_cohort(conn, roles, *, base_pipeline_version, challenger_id, policy_hash, at):
    rows = conn.execute(f'''SELECT a.* FROM research_run_acceptance a JOIN research_runs r USING(run_id)
        WHERE a.role IN ({",".join("?" for _ in roles)}) AND a.base_pipeline_version=?
        AND COALESCE(a.challenger_id,'')=? AND a.policy_hash=? AND r.status='open'
        AND julianday(a.window_end)>julianday(?) ORDER BY a.window_start DESC''',
        (*roles, base_pipeline_version, challenger_id or '', policy_hash, at)).fetchall()
    cohorts = {}
    for row in rows:
        cohorts.setdefault(row['cohort_id'], {})[row['role']] = dict(row)
    for cohort_id, members in cohorts.items():
        if set(members) == set(roles):
            first = next(iter(members.values()))
            return {'cohort_id': cohort_id, 'window_start': first['window_start'], 'window_end': first['window_end'],
                    'runs': {role: member['run_id'] for role, member in members.items()}}
    return None


def ensure_cohort(manifests_by_role, *, base_pipeline_version, challenger_id, acceptance, at,
                  observation_protocol=OBSERVATION_PROTOCOL, db_path=None):
    """Return the open cohort for these strategies, opening one (all roles at once) if needed.

    Every run starts before any decision it can enroll, and freezes its acceptance criteria.
    """
    initialize_database(db_path=db_path)
    at = iso(at)
    roles = sorted(manifests_by_role)
    key = {'base_pipeline_version': base_pipeline_version, 'challenger_id': challenger_id,
           'policy_hash': acceptance.policy_hash(), 'at': at}
    with closing(get_connection(db_path)) as conn:
        found = _active_cohort(conn, roles, **key)
    if found:
        return found
    code = source_hash()
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        found = _active_cohort(conn, roles, **key)
        if found:
            return found
        window_end = (utc(at) + timedelta(days=acceptance.window_days)).isoformat()
        cohort_id = 'cohort:' + digest({'base_pipeline_version': base_pipeline_version,
                                        'challenger_id': challenger_id, 'policy_hash': acceptance.policy_hash(),
                                        'window_start': at})
        runs = {}
        for role in roles:
            run = insert_run(conn, manifests_by_role[role], at=at, target_candidates=acceptance.cohort_candidates,
                             observation_protocol=observation_protocol, code=code)
            conn.execute('INSERT INTO research_run_acceptance VALUES (?,?,?,?,?,?,?,?,?)',
                         (run['run_id'], role, cohort_id, base_pipeline_version, challenger_id, at, window_end,
                          acceptance.policy_hash(), canonical(acceptance.assumptions())))
            runs[role] = run['run_id']
    return {'cohort_id': cohort_id, 'window_start': at, 'window_end': window_end, 'runs': runs, 'opened': True}


def cohort_registrations(cohort_id, *, db_path=None):
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('SELECT * FROM research_run_acceptance WHERE cohort_id=?', (cohort_id,)).fetchall()
    result = {}
    for row in rows:
        registration = dict(row)
        registration['policy'] = json.loads(registration.pop('policy_json'))
        if digest(registration['policy']) != registration['policy_hash']:
            raise ValueError('Pre-registered acceptance criteria changed')
        result[registration['role']] = registration
    return result


def record_decision_observation(candidate_id, *, observation_id, cohort_id, status, at, reason=None, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO decision_observations VALUES (?,?,?,?,?,?)',
                     (candidate_id, observation_id, cohort_id, status, reason, iso(at)))


def pending_paired_observations(challenger_id, *, limit, db_path=None):
    """This challenger's recorded observations, oldest first, still lacking a final evaluation.

    Cohorts of an earlier challenger configuration are left alone rather than run with its models.
    """
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(f'''SELECT d.* FROM decision_observations d
            JOIN research_run_acceptance a ON a.cohort_id=d.cohort_id AND a.role='challenger'
            WHERE d.status='recorded' AND a.challenger_id=?
            AND (SELECT COUNT(*) FROM paired_evaluations p WHERE p.observation_id=d.observation_id
                 AND p.status IN ({",".join("?" for _ in FINAL_EVALUATIONS)})) < 2
            ORDER BY d.recorded_at, d.candidate_id LIMIT ?''', (challenger_id, *FINAL_EVALUATIONS, limit)).fetchall()
    return [dict(row) for row in rows]


def paired_evaluation(observation_id, role, *, db_path=None):
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('SELECT * FROM paired_evaluations WHERE observation_id=? AND role=?',
                           (observation_id, role)).fetchone()
    return dict(row) if row else None


def update_paired_evaluation(observation_id, role, *, cohort_id, run_id, status, at, candidate_id=None,
                             reason=None, attempt=False, model_calls=0, latency_seconds=None, db_path=None):
    """Record progress; a final status is never overwritten."""
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute(f'''INSERT INTO paired_evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(observation_id, role) DO UPDATE SET candidate_id=COALESCE(excluded.candidate_id, candidate_id),
            status=excluded.status, reason=excluded.reason, attempts=attempts+excluded.attempts,
            model_calls=model_calls+excluded.model_calls,
            latency_seconds=COALESCE(excluded.latency_seconds, latency_seconds), updated_at=excluded.updated_at
            WHERE paired_evaluations.status NOT IN ({",".join("?" for _ in FINAL_EVALUATIONS)})''',
                     (observation_id, role, cohort_id, run_id, candidate_id, status, reason, int(attempt),
                      model_calls, latency_seconds, iso(at), *FINAL_EVALUATIONS))


def reserve_challenger_calls(calls, *, limit, at, db_path=None):
    """Reserve before dispatch; a reservation is spent even if the call then fails."""
    day = iso(at)[:10]
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT calls FROM challenger_call_allowances WHERE day=?', (day,)).fetchone()
        if (row['calls'] if row else 0) + calls > limit:
            return False
        conn.execute('''INSERT INTO challenger_call_allowances VALUES (?,?)
            ON CONFLICT(day) DO UPDATE SET calls=calls+excluded.calls''', (day, calls))
    return True


def paired_candidate_id(observation_id, role, run_id):
    return PAIRED_PREFIX + role + ':' + digest({'observation_id': observation_id, 'run_id': run_id})


def create_paired_candidate(observation, *, role, run_id, manifest, at, db_path=None):
    """A research-only candidate bound to the shared observation before any of its snapshots."""
    candidate_id = paired_candidate_id(observation['observation_id'], role, run_id)
    states = observation['source_snapshot']['catalysts']
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM candidates WHERE candidate_id=?', (candidate_id,)).fetchone():
            raise FileExistsError(candidate_id)
        articles = sorted({row['article_id'] for row in observation['source_snapshot'].get('evidence', [])
                           if row.get('article_id') is not None})
        conn.execute('''INSERT INTO candidates
            (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,pipeline_version_json)
            VALUES (?,?,?,?,?,?)''', (candidate_id, observation['symbol'], iso(at),
                                     canonical([state['catalyst_id'] for state in states]),
                                     canonical(articles), canonical(manifest)))
        for index, state in enumerate(states):
            conn.execute('INSERT INTO candidate_catalysts VALUES (?,?,?,?,?)',
                         (candidate_id, state['catalyst_id'], state['catalyst_family'], index == 0, canonical(state)))
        bind_candidate_observation(conn, candidate_id, observation['observation_id'])
    # Snapshot envelopes repeat the context manifest; the full one is stored once, on the candidate row.
    slim_manifest = {'pipeline_version': manifest['pipeline_version'], 'gate_config': manifest.get('gate_config', {})}
    return {'candidate_id': candidate_id, 'symbol': observation['symbol'], 'created_at': iso(at),
            'pipeline_version': slim_manifest, 'catalysts': states, 'catalyst_id': states[0]['catalyst_id'],
            'catalyst_family': states[0]['catalyst_family'], 'observation_id': observation['observation_id'],
            'observation_cutoff': observation['information_cutoff_time']}


def claim_mark_session(session_date, *, at, db_path=None):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        return conn.execute("INSERT OR IGNORE INTO research_mark_sessions VALUES (?,?,'running',NULL)",
                            (session_date, iso(at))).rowcount == 1


def finish_mark_session(session_date, *, status, summary, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('UPDATE research_mark_sessions SET status=?, summary_json=? WHERE session_date=?',
                     (status, canonical(summary), session_date))


def forward_status(*, db_path=None, limit=10):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        return forward_status_from(conn, limit=limit)


def forward_status_from(conn, *, limit=10):
    """Read-only operator view of cohorts, paired coverage, marks and approvals."""
    cohorts = {}
    for row in conn.execute('''SELECT a.*, r.status AS run_status, r.target_candidates,
            (SELECT COUNT(*) FROM research_candidates c WHERE c.run_id=a.run_id) AS enrolled,
            (SELECT COUNT(*) FROM research_shadow_positions p WHERE p.run_id=a.run_id AND p.strategy='full_v2'
             AND p.status IN ('complete','cash')) AS resolved
            FROM research_run_acceptance a JOIN research_runs r USING(run_id)
            ORDER BY a.window_start DESC'''):
        cohort = cohorts.setdefault(row['cohort_id'], {
            'cohort_id': row['cohort_id'], 'base_pipeline_version': row['base_pipeline_version'],
            'challenger_id': row['challenger_id'], 'window_start': row['window_start'],
            'window_end': row['window_end'], 'acceptance_hash': row['policy_hash'], 'runs': {}})
        cohort['runs'][row['role']] = {'run_id': row['run_id'], 'status': row['run_status'],
                                       'enrolled': row['enrolled'], 'resolved': row['resolved'],
                                       'target_candidates': row['target_candidates']}
    observations = {row['status']: row['n'] for row in conn.execute(
        'SELECT status, COUNT(*) n FROM decision_observations GROUP BY status')}
    evaluations = [dict(row) for row in conn.execute('''SELECT role, status, COUNT(*) n,
        SUM(model_calls) model_calls FROM paired_evaluations GROUP BY role, status ORDER BY role, status''')]
    sessions = [dict(row) for row in conn.execute(
        'SELECT * FROM research_mark_sessions ORDER BY session_date DESC LIMIT ?', (limit,))]
    approvals = [json.loads(row['record_json']) for row in conn.execute(
        'SELECT record_json FROM challenger_approvals ORDER BY approved_at DESC LIMIT ?', (limit,))]
    for session in sessions:
        session['summary'] = json.loads(session.pop('summary_json') or 'null')
    return {'cohorts': list(cohorts.values())[:limit], 'decision_observations': observations,
            'paired_evaluations': evaluations, 'mark_sessions': sessions, 'challenger_approvals': approvals,
            'authority': 'research only: no cohort, evaluation or approval places an order'}
