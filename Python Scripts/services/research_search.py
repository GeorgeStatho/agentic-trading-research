"""Point-in-time full-text hits over one company's news and evidence archive, and immutable article snapshots."""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from itertools import chain, zip_longest

from db_common import get_connection
from market_db_modules.company_evidence import record_evidence_document
from research_foundation_contracts import EvidenceDocument, timestamp

SEARCH_SNAPSHOT_PROVIDER = 'news_archive'
# An article counts as known from first sight, and only while its text has not changed since the cutoff.
ARTICLE_HITS = '''SELECT a.id FROM news_article_fts JOIN news_articles a ON a.id=news_article_fts.rowid
    WHERE news_article_fts MATCH ?
    AND EXISTS (SELECT 1 FROM company_news_articles l WHERE l.article_id=a.id AND l.company_id=?)
    AND julianday(COALESCE(a.first_seen_time,a.created_at))<=julianday(?)
    AND (a.content_updated_at IS NULL OR julianday(a.content_updated_at)<=julianday(?))
    ORDER BY bm25(news_article_fts) LIMIT ?'''
# Snapshotted articles are excluded so an article is never returned twice.
DOCUMENT_HITS = '''SELECT f.document_id FROM evidence_document_fts f JOIN evidence_documents d USING(document_id)
    WHERE evidence_document_fts MATCH ? AND f.company_id=? AND d.provider<>?
    AND julianday(json_extract(d.record_json,'$.ingested_at'))<=julianday(?)
    ORDER BY bm25(evidence_document_fts) LIMIT ?'''


def search_hits(company_id, expressions, cutoff, *, limit, db_path=None):
    """Interleaved ('article', id) / ('document', id) hits for the first expression that matches anything.

    bm25 scores from the two indexes are not comparable, so results alternate by rank position.
    """
    articles, documents, mode = [], [], expressions[-1][0]
    with closing(get_connection(db_path)) as conn:
        for mode, expression in expressions:
            articles = [('article', row[0]) for row in
                        conn.execute(ARTICLE_HITS, (expression, company_id, cutoff, cutoff, limit + 1))]
            documents = [('document', row[0]) for row in
                         conn.execute(DOCUMENT_HITS, (expression, company_id, SEARCH_SNAPSHOT_PROVIDER, cutoff, limit + 1))]
            if articles or documents:
                break
    ranked = [hit for hit in chain.from_iterable(zip_longest(articles, documents)) if hit]
    return {'mode': mode, 'hits': ranked[:limit], 'more_results_available': len(ranked) > limit}


def _utc(value):
    try:
        return timestamp(value) if value else None
    except ValueError:
        return None


def snapshot_article(article_id, company_id, *, db_path=None):
    """Record the article's current text as an immutable evidence document; return its document_id.

    Article rows are rewritten on re-scrape, so the text is frozen here. The id carries a content hash:
    the same text maps to the same stored document, and changed text gives a new one.
    """
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('''SELECT a.*, c.body FROM news_articles a
            LEFT JOIN news_article_content c ON c.article_id=a.id WHERE a.id=?''', (article_id,)).fetchone()
        text = '\n\n'.join(part or '' for part in (row['title'], row['summary'], row['body']))
        document_id = f'news:{article_id}:{sha256(text.encode()).hexdigest()[:16]}'
        if conn.execute('SELECT 1 FROM evidence_documents WHERE document_id=?', (document_id,)).fetchone():
            return document_id
    first_seen = _utc(row['first_seen_time']) or _utc(row['created_at'])
    published, changed = _utc(row['published_at']), _utc(row['content_updated_at'])
    metadata = {'company_id': company_id, 'article_id': article_id, 'content_updated_at': changed,
                'text_snapshot_at': datetime.now(timezone.utc).isoformat()}
    if published is None or published > first_seen:
        published, metadata['published_at_clamped'] = first_seen, True
    record_evidence_document(EvidenceDocument(
        document_id=document_id, provider=SEARCH_SNAPSHOT_PROVIDER, source_url=row['source_url'] or row['normalized_url'],
        publisher=row['source'], document_type='news_article', text=text, published_at=published,
        first_observed_at=first_seen, ingested_at=max(first_seen, changed or first_seen), metadata=metadata),
        db_path=db_path)
    return document_id
