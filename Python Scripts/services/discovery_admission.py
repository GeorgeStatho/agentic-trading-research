"""Research admission for primary events; this result never grants trading permission."""
from datetime import datetime

from catalyst_models import PRIMARY_SOURCE_TYPES


def evaluate_discovery_admission(event, evidence, *, at, policy):
    now = datetime.fromisoformat(at)
    if evidence.get('freshness_at_read') == 'fresh':
        documents = {doc['document_id']: doc for doc in evidence.get('documents', [])}
        for fact in evidence.get('facts', []):
            doc = documents.get(fact.get('document_id'), {})
            if (fact.get('kind') in {'actual', 'guidance'} and fact.get('value') is not None
                    and not fact.get('superseded') and doc.get('document_type') in {'xbrl_facts', 'regulatory_filing', 'issuer_release'}
                    and doc.get('published_at') and fact.get('source_excerpt')):
                age = (now - datetime.fromisoformat(doc['published_at'])).total_seconds()
                if 0 <= age <= policy.stale_seconds:
                    return {'allowed': True, 'reason': 'supported_primary_company_event', 'route': 'primary_evidence', 'shadow': True}
    for article in event['articles']:
        if (str(article.get('primary_ticker') or '').upper() == event['company']['symbol'].upper()
                and article.get('source_type') in PRIMARY_SOURCE_TYPES and article.get('root_source_id') not in {None, '', 'unverified'}
                and article.get('body') and float(article.get('evidence_score') or 0) >= policy.minimum_evidence_score):
            age = (now - datetime.fromisoformat(article['event_time'])).total_seconds()
            if 0 <= age <= policy.stale_seconds:
                return {'allowed': True, 'reason': 'supported_primary_news_event', 'route': 'primary_news', 'shadow': True}
    if event.get('legacy_high_confidence_articles', 0) >= event.get('minimum_legacy_articles', 2):
        return {'allowed': True, 'reason': 'legacy_opportunist_support', 'route': 'sector', 'shadow': True}
    return {'allowed': False, 'reason': 'missing_fresh_primary_evidence_or_legacy_support', 'route': 'deferred', 'shadow': True}
