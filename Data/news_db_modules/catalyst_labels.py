"""Insert-only model labels for catalysts the rules could not classify."""
from __future__ import annotations

from datetime import datetime, timezone

from db_common import json_text

MAX_LABEL_ATTEMPTS = 3


def record_catalyst_label(catalyst_id, *, label_version, catalyst_type, direction, confidence, anchor_article_id,
                          model, answered_by, labeled_at=None, conn):
    conn.execute('INSERT INTO catalyst_model_labels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                 (catalyst_id, label_version, catalyst_type, direction, confidence, anchor_article_id, model,
                  json_text(answered_by), labeled_at or datetime.now(timezone.utc).isoformat()))


def latest_catalyst_label(catalyst_id, *, as_of=None, conn):
    """The newest usable label (not low confidence, not `other`), else the newest label of any kind."""
    row = conn.execute('SELECT * FROM catalyst_model_labels WHERE catalyst_id=? AND (? IS NULL OR '
                       "julianday(labeled_at)<=julianday(?)) ORDER BY confidence<>'low' AND catalyst_type<>'other' DESC, "
                       'julianday(labeled_at) DESC LIMIT 1', (catalyst_id, as_of, as_of)).fetchone()
    return dict(row) if row else None


def pending_catalysts_for_labeling(limit, label_version, *, conn):
    """Unresolved catalysts never asked at this version, or asked fewer than MAX_LABEL_ATTEMPTS times and
    joined by an article first seen after the latest of those labels."""
    return [dict(row) for row in conn.execute('''SELECT c.* FROM catalysts c
        WHERE c.classification_status='needs_review' AND c.ticker IS NOT NULL
        AND c.catalyst_id NOT LIKE 'catalyst:unknown:%' AND c.catalyst_id NOT LIKE 'catalyst:sector-discovery:%'
        AND EXISTS (SELECT 1 FROM news_articles a WHERE a.catalyst_id=c.catalyst_id)
        AND (NOT EXISTS (SELECT 1 FROM catalyst_model_labels l WHERE l.catalyst_id=c.catalyst_id AND l.label_version=:version)
             OR ((SELECT COUNT(*) FROM catalyst_model_labels l
                  WHERE l.catalyst_id=c.catalyst_id AND l.label_version=:version) < :attempts
                 AND EXISTS (SELECT 1 FROM news_articles a WHERE a.catalyst_id=c.catalyst_id
                     AND julianday(COALESCE(a.first_seen_time, a.created_at)) > (SELECT MAX(julianday(l.labeled_at))
                         FROM catalyst_model_labels l WHERE l.catalyst_id=c.catalyst_id AND l.label_version=:version))))
        ORDER BY julianday(c.first_seen_time) DESC LIMIT :limit''',
        {'version': label_version, 'attempts': MAX_LABEL_ATTEMPTS, 'limit': limit})]


_LATEST_LABELS = '''(SELECT l.* FROM catalyst_model_labels l WHERE l.rowid=(SELECT l2.rowid FROM catalyst_model_labels l2
    WHERE l2.catalyst_id=l.catalyst_id ORDER BY julianday(l2.labeled_at) DESC LIMIT 1))'''


def _counts(conn, query):
    return {row[0]: row[1] for row in conn.execute(query)}


def catalyst_label_status(*, conn):
    """Catalyst counts by status, classification source and type, and latest labels by confidence."""
    return {'by_status': _counts(conn, 'SELECT classification_status, COUNT(*) FROM catalysts GROUP BY 1'),
            'by_classification_source': _counts(conn, "SELECT COALESCE(json_extract(state_json, '$.classification_source'), "
                                                      "'rules'), COUNT(*) FROM catalysts GROUP BY 1"),
            'by_type': _counts(conn, 'SELECT catalyst_type, COUNT(*) FROM catalysts GROUP BY 1'),
            'by_confidence': _counts(conn, f'SELECT confidence, COUNT(*) FROM {_LATEST_LABELS} GROUP BY 1')}


def labeled_catalyst_sample(sample, *, conn):
    """A random sample of labelled catalysts with the anchor headline, for a manual spot-check."""
    return [dict(row) for row in conn.execute(f'''SELECT l.catalyst_id, c.ticker, a.title, l.catalyst_type, l.direction,
        l.confidence, c.catalyst_family, c.classification_status FROM {_LATEST_LABELS} l
        JOIN catalysts c ON c.catalyst_id=l.catalyst_id LEFT JOIN news_articles a ON a.id=l.anchor_article_id
        ORDER BY RANDOM() LIMIT ?''', (sample,))]
