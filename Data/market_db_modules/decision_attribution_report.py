"""Read-only report over stored decision attributions: does evidence quality, or the model's own rating, predict the call?"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from db_common import get_connection
from services.decision_attribution import bootstrap_se
from services.llm_direction_score import DIRECTION_SIGN
from .decision_attribution import list_attributions

GRADED = ('correct', 'wrong', 'flat')
TRADED_OUTCOMES = ('captured', 'lost_despite_direction', 'lost_with_direction', 'pending')


def decision_attribution_report(*, days: int = 0, horizon: int = 5, db_path: Path | str | None = None) -> dict[str, Any]:
    """Counts, hit rates (with bootstrap SEs) and signed returns at `horizon`, over attributions computed in `days`."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat() if days else None
    key = str(horizon)
    decisions = _manager_decisions(db_path)
    rows = [_row(item, key, decisions.get(item['manager_decision_history_id'])) for item in
            list_attributions(since=since, db_path=db_path)]
    by_quality = _group(rows, lambda row: row['quality'])
    return {
        'days': days, 'horizon': horizon, 'total': len(rows),
        'matrix': {quality: {**_hit_rate(group), 'cells': _cells(group)} for quality, group in by_quality.items()},
        'by_evidence_source': {source: _hit_rate(group)
                               for source, group in _group(rows, lambda row: row['source']).items()},
        'model_self_rating_calibration': {rating: _hit_rate(group) for rating, group in
                                          _group(rows, lambda row: row['self_rating']).items() if rating},
        'primary_failure_counts': dict(Counter(row['failure'] for row in rows)),
        'expression': dict(Counter(row['expression'] for row in rows if row['expression'] in TRADED_OUTCOMES)),
        'abstentions': {
            'neither': _moves([row for row in rows if row['sign'] is None]),
            'directional': _moves([row for row in rows if row['sign'] is not None]),
        },
    }


def _manager_decisions(db_path) -> dict[int, str]:
    with closing(get_connection(db_path)) as conn:
        return {row['id']: row['manager_decision'] for row in conn.execute(
            'SELECT id, manager_decision FROM manager_decision_history')}


def _row(item: dict[str, Any], horizon: str, manager_decision: str | None) -> dict[str, Any]:
    record = item['record']
    direction, evidence = record['direction'], record['evidence']
    sign = DIRECTION_SIGN.get(str(manager_decision or '').lower())
    move = (direction.get('horizon_returns') or {}).get(horizon)
    return {
        'quality': item['evidence_quality'], 'source': item['evidence_source'], 'failure': item['primary_failure'],
        'expression': item['expression_outcome'],
        'outcome': (direction.get('horizon_outcomes') or {}).get(horizon, direction['outcome']),
        'sign': sign, 'signed_return': None if sign is None or move is None else sign * move,
        'abs_move': (direction.get('abs_move') or {}).get(horizon),
        'self_rating': ((evidence.get('signals') or {}).get('model_self_rating') or {}).get('evidence_quality'),
    }


def _group(rows, key) -> dict[Any, list[dict[str, Any]]]:
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return dict(groups)


def _hit_rate(rows) -> dict[str, Any]:
    # Sorted so the seeded bootstrap does not depend on row order.
    graded = sorted((1 if row['outcome'] == 'correct' else 0 for row in rows if row['outcome'] in GRADED), reverse=True)
    return {'count': len(rows), 'graded': len(graded), 'hit_rate': mean(graded) if graded else None,
            'hit_rate_se': bootstrap_se(graded)}


def _cells(rows) -> dict[str, dict[str, Any]]:
    return {outcome: {'count': len(group), 'mean_signed_return': _mean(row['signed_return'] for row in group)}
            for outcome, group in _group(rows, lambda row: row['outcome']).items()}


def _moves(rows) -> dict[str, Any]:
    moves = [row['abs_move'] for row in rows if row['abs_move'] is not None]
    return {'count': len(moves), 'mean_abs_move': _mean(moves), 'median_abs_move': median(moves) if moves else None}


def _mean(values) -> float | None:
    values = [value for value in values if value is not None]
    return mean(values) if values else None
