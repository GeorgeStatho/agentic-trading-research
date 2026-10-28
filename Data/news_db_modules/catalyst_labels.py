"""Insert-only model labels for catalysts the rules could not classify."""
from __future__ import annotations

from datetime import datetime, timezone

from db_common import json_text


def record_catalyst_label(catalyst_id, *, label_version, catalyst_type, direction, confidence, anchor_article_id,
                          model, answered_by, labeled_at=None, conn):
    conn.execute('INSERT INTO catalyst_model_labels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                 (catalyst_id, label_version, catalyst_type, direction, confidence, anchor_article_id, model,
                  json_text(answered_by), labeled_at or datetime.now(timezone.utc).isoformat()))


def latest_catalyst_label(catalyst_id, *, as_of=None, conn):
    row = conn.execute('SELECT * FROM catalyst_model_labels WHERE catalyst_id=? AND (? IS NULL OR '
                       'julianday(labeled_at)<=julianday(?)) ORDER BY julianday(labeled_at) DESC LIMIT 1',
                       (catalyst_id, as_of, as_of)).fetchone()
    return dict(row) if row else None


def pending_catalysts_for_labeling(limit, label_version, *, conn):
    return [dict(row) for row in conn.execute('''SELECT c.* FROM catalysts c
        WHERE c.classification_status='needs_review' AND c.ticker IS NOT NULL
        AND c.catalyst_id NOT LIKE 'catalyst:unknown:%' AND c.catalyst_id NOT LIKE 'catalyst:sector-discovery:%'
        AND NOT EXISTS (SELECT 1 FROM catalyst_model_labels l WHERE l.catalyst_id=c.catalyst_id AND l.label_version=?)
        ORDER BY julianday(c.first_seen_time) DESC LIMIT ?''', (label_version, limit))]
