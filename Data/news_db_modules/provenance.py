"""Deterministic, conservative provenance clustering at the ingestion boundary."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from db_common import get_connection
from processing.news_normalization import normalize_url
from processing.scoring import confirmation_score, compute_evidence_score
from sources.source_config import get_source_metadata
from catalyst_models import timestamp


SOURCE_TYPES = {
    "primary_filing", "company_release", "government", "court_document",
    "earnings_call", "wire_service", "original_reporting", "analyst_research",
    "aggregator", "commentary",
}
LEGACY_TYPES = {
    "wire": "wire_service", "company_ir": "company_release",
    "financial_news": "original_reporting", "business_news": "original_reporting",
    "research_financial_news": "analyst_research", "analysis_opinion": "commentary",
}
STOP_WORDS = {"a", "an", "the", "and", "to", "of", "in", "for", "on", "as", "by", "with"}
TITLE_SIMILARITY = 0.8


def _identifier(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def _tokens(title):
    return set(re.findall(r"[a-z0-9]+", str(title).lower())) - STOP_WORDS


def _origin(article):
    url = article["source_url"]
    metadata = get_source_metadata(url)
    source_type = metadata.get("provenance_source_type") or metadata.get("source_type")
    source_type = LEGACY_TYPES.get(source_type, source_type)
    if source_type not in SOURCE_TYPES:
        source_type = "aggregator"
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if host == "sec.gov" or host.endswith(".sec.gov"):
        source_type = "primary_filing"
    elif host.endswith(".gov") and source_type not in {"court_document", "primary_filing"}:
        source_type = "government"
    try:
        raw = json.loads(article.get("raw_json") or "{}")
    except (ValueError, TypeError):
        raw = {}
    provenance = raw.get("provenance", {}) if isinstance(raw, dict) else {}
    provenance = provenance if isinstance(provenance, dict) else {}
    supplied_url = str(provenance.get("primary_source_url") or "")
    supplied_parts = urlsplit(supplied_url)
    primary_url = None
    if supplied_parts.scheme in {'http', 'https'} and supplied_parts.hostname:
        primary_metadata = get_source_metadata(supplied_url)
        primary_kind = primary_metadata.get('provenance_source_type')
        if primary_kind in {'primary_filing', 'company_release', 'government', 'court_document', 'earnings_call'} or supplied_parts.hostname.endswith('.gov'):
            primary_url = normalize_url(supplied_url)
    if primary_url:
        return source_type, "url:" + primary_url, primary_url
    if source_type in {"primary_filing", "company_release", "government", "court_document", "earnings_call"}:
        return source_type, "url:" + normalize_url(url), normalize_url(url)
    # Attribution wins over the republisher's domain. Conservative mentions can
    # undercount; they must never manufacture independent confirmation.
    text = " ".join(str(article.get(key) or "") for key in ("source", "summary", "body"))
    text += " " + str(provenance.get("publisher") or "")
    for wire, pattern in (("reuters", r"\breuters\b"), ("ap", r"\bassociated press\b"),
                          ("prnewswire", r"\bpr newswire\b"), ("businesswire", r"\bbusiness wire\b"),
                          ("globenewswire", r"\bglobe\s?newswire\b")):
        if re.search(pattern, text, re.I) or wire in host:
            return "wire_service", "wire:" + wire, None
    if source_type in {"aggregator", "commentary"}:
        return source_type, "unverified", None
    return source_type, "publisher:" + str(metadata.get("domain") or host), None


def update_article_provenance(article_id: int, *, conn) -> None:
    article = dict(conn.execute("""
        SELECT a.*, content.body, content.raw_json FROM news_articles a
        LEFT JOIN news_article_content content ON content.article_id = a.id
        WHERE a.id = ?
    """, (article_id,)).fetchone())
    company = conn.execute("""
        SELECT c.symbol, c.name FROM companies c
        JOIN company_news_articles link ON link.company_id = c.id
        WHERE link.article_id = ? ORDER BY link.id LIMIT 1
    """, (article_id,)).fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'companies'"
    ).fetchone() else None
    ticker = article.get("primary_ticker") or (company["symbol"] if company else None)
    try:
        raw = json.loads(article.get('raw_json') or '{}')
    except (TypeError, ValueError):
        raw = {}
    supplied = raw.get('provenance', {}) if isinstance(raw, dict) else {}
    supplied = supplied if isinstance(supplied, dict) else {}
    published = timestamp(article['published_at'])
    event = timestamp(supplied.get('event_time'))
    when = event if event and published and event <= published else published
    source_type, origin, primary_url = _origin(article)
    catalyst = article.get("catalyst_id")
    # Unlinked articles receive an isolated ID, and are regrouped once a primary
    # company link arrives. A first company link stays primary on later reuse.
    if not article.get("primary_ticker"):
        catalyst = None
    if catalyst is None and ticker:
        prior = conn.execute('''SELECT catalyst_id FROM news_articles WHERE primary_ticker=? AND id!=?
            AND catalyst_id IS NOT NULL AND ((? IS NOT NULL AND primary_source_url=?)
                 OR (content_hash=? AND title=? AND source!=?)) ORDER BY id LIMIT 1''',
            (ticker, article_id, primary_url, primary_url, article.get('content_hash'), article['title'], article['source'])).fetchone()
        if prior:
            catalyst = prior['catalyst_id']
    if catalyst is None and ticker and when:
        candidates = conn.execute("""
            SELECT * FROM news_articles
            WHERE primary_ticker = ? AND id != ? AND catalyst_id IS NOT NULL
              AND ABS(julianday(event_time) - julianday(?)) <= 1
            ORDER BY id
        """, (ticker, article_id, when.isoformat())).fetchall()
        title_tokens = _tokens(article["title"])
        entity_tokens = _tokens(company["name"] if company else "") | _tokens(ticker)
        for candidate in candidates:
            other = _tokens(candidate["title"])
            # Compare against the cluster anchor, preventing rolling 24h chains.
            anchor = conn.execute("""SELECT a.title, COALESCE(c.first_public_time, a.event_time) AS event_time
                FROM news_articles a LEFT JOIN catalysts c ON c.catalyst_id=a.catalyst_id
                WHERE a.catalyst_id = ? ORDER BY a.id LIMIT 1""",
                                  (candidate["catalyst_id"],)).fetchone()
            anchor_time = timestamp(anchor["event_time"])
            other = _tokens(anchor["title"])
            shared = title_tokens & other
            similarity = len(shared) / max(len(title_tokens | other), 1)
            if (anchor_time and abs((when - anchor_time).total_seconds()) <= 86400
                    and shared & entity_tokens and similarity >= TITLE_SIMILARITY):
                catalyst = candidate["catalyst_id"]
                break
    catalyst = catalyst or _identifier("catalyst:", str(article_id))
    root = _identifier("root:", catalyst + "|" + origin)
    old_catalyst = article.get("catalyst_id")
    conn.execute("""
        UPDATE news_articles SET source_type=?, primary_ticker=?, catalyst_id=?, root_event_id=?,
            root_source_id=?, primary_source_url=?, event_time=?,
            first_seen_time=COALESCE(first_seen_time, created_at, ?)
        WHERE id=?
    """, (source_type, ticker, catalyst, root, origin, primary_url,
          when.isoformat() if when else None, datetime.now(timezone.utc).isoformat(), article_id))
    for cluster in {catalyst, old_catalyst} - {None}:
        _refresh_cluster_scores(cluster, conn=conn)
        from .catalysts import refresh_catalyst
        refresh_catalyst(cluster, conn=conn)

    from market_db_modules.company_interest import signal_company_news
    signal_company_news(conn, article_id, at=datetime.now(timezone.utc).isoformat())


def _refresh_cluster_scores(catalyst, *, conn):
    members = conn.execute("SELECT * FROM news_articles WHERE catalyst_id = ? ORDER BY id", (catalyst,)).fetchall()
    # Unattributed aggregators do not add a root to independently known evidence.
    known = {row["root_event_id"] for row in members if row["root_source_id"] != "unverified"}
    count = len(known) or (1 if members else 0)
    confirmation = confirmation_score(count)
    parents = {}
    for row in members:
        root = row["root_event_id"]
        parent = parents.setdefault(root, row["id"])
        evidence = compute_evidence_score(
            recency=row["recency_score"] or 0, source_reputation=row["source_reputation_score"] or 0,
            directness=row["directness_score"] or 0, confirmation=confirmation,
            factuality=row["factuality_score"] or 0,
        )
        conn.execute("""
            UPDATE news_articles SET independent_source_count=?, confirmation_score=?,
                evidence_score=?, syndication_parent_id=? WHERE id=?
        """, (count, confirmation, evidence, parent if parent != row["id"] else None, row["id"]))


def backfill_news_provenance(*, db_path=None) -> dict:
    from .bootstrap import initialize_database
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM news_articles ORDER BY id")]
        for article_id in ids:
            update_article_provenance(article_id, conn=conn)
        distribution = [dict(row) for row in conn.execute("""
            SELECT independent_source_count, COUNT(*) AS article_count
            FROM news_articles GROUP BY independent_source_count ORDER BY independent_source_count
        """)]
    return {"articles_processed": len(ids), "distribution": distribution}
