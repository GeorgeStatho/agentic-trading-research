"""Inputs and answer validation for the catalyst classifier stage."""
from __future__ import annotations

from typing import Any

from catalyst_models import CATALYST_TYPES

BODY_CHAR_LIMIT = 1500
DIRECTIONS = ("positive", "negative", "mixed", "unknown")
CONFIDENCES = ("high", "medium", "low")
LABEL_FIELDS = ("catalyst_id", "catalyst_type", "direction", "confidence")
TYPE_DEFINITIONS = {
    "earnings": "reported or previewed quarterly results",
    "guidance": "raised, cut or reaffirmed company outlook",
    "ma": "acquisition, merger, divestiture or takeover bid",
    "regulatory": "approval, clearance, investigation or rule change by a regulator",
    "legal": "lawsuit, ruling, settlement or fine",
    "product": "product launch, release, recall or technical milestone",
    "customer_contract": "customer win or loss, large order or contract",
    "supply_chain": "supplier, capacity, shortage or logistics news",
    "management": "executive hire, departure or board change",
    "capital_return": "dividend, buyback or special distribution",
    "financing": "equity or debt raise, offering or credit facility",
    "analyst_action": "analyst upgrade, downgrade or price-target change",
    "macro": "economy-wide data or central bank news",
    "geopolitical": "war, sanctions, tariffs or trade policy",
    "industry_readthrough": "news about a peer or the sector that implies an effect on this company",
    "rumor": "unconfirmed report or speculation",
    "other": "none of the above or no clear catalyst",
}


def catalyst_inputs(catalysts: list[dict[str, Any]], *, conn) -> list[dict[str, Any]]:
    # The anchor is the earliest article by event_time, the same one the rules classify.
    inputs = []
    for catalyst in catalysts:
        anchor = conn.execute('''SELECT a.id, a.title, a.summary, c.body FROM news_articles a
            LEFT JOIN news_article_content c ON c.article_id=a.id WHERE a.catalyst_id=?
            ORDER BY a.event_time IS NULL, julianday(a.event_time), a.id LIMIT 1''', (catalyst['catalyst_id'],)).fetchone()
        if anchor is None:
            continue
        inputs.append({'catalyst_id': catalyst['catalyst_id'], 'ticker': catalyst['ticker'], 'title': anchor['title'],
                       'summary': anchor['summary'], 'body': (anchor['body'] or '')[:BODY_CHAR_LIMIT],
                       'anchor_article_id': anchor['id']})
    return inputs


def valid_labels(parsed: Any, batch_ids: set[str]) -> tuple[list[dict[str, str]], int]:
    """The usable labels in a parsed answer, and how many items were dropped. The first label per id wins."""
    items = parsed.get('labels') if isinstance(parsed, dict) else parsed
    items = items if isinstance(items, list) else []
    labels: dict[str, dict[str, str]] = {}
    for item in items:
        usable = (isinstance(item, dict) and all(isinstance(item.get(field), str) for field in LABEL_FIELDS)
                  and item['catalyst_id'] in batch_ids and item['catalyst_id'] not in labels
                  and item['catalyst_type'] in CATALYST_TYPES and item['direction'] in DIRECTIONS
                  and item['confidence'] in CONFIDENCES)
        if usable:
            labels[item['catalyst_id']] = {field: item[field] for field in LABEL_FIELDS}
    return list(labels.values()), len(items) - len(labels)
