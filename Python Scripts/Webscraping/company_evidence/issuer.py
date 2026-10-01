"""Explicitly configured issuer RSS/Atom feeds; URLs cannot expand the host allowlist."""
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

from research_foundation_contracts import timestamp
from .models import CollectionResult, SourceDocument
from .releases import guidance_facts, release_text, scheduled_events


class IssuerReleaseAdapter:
    name = 'issuer'

    def __init__(self, http, feed_url, policy):
        self.http, self.feed_url, self.policy = http, feed_url, policy

    def collect(self, company, cursor):
        response = self.http.get(self.feed_url, ttl_seconds=self.policy.refresh_seconds)
        root = ET.fromstring(response['body'])
        documents = []
        entries = root.findall('.//item') or root.findall('{http://www.w3.org/2005/Atom}entry')
        for item in entries[:self.policy.filings_per_company]:
            values = {child.tag.split('}')[-1]: child for child in item}
            link = values.get('link')
            url = (link.get('href') or link.text or '').strip() if link is not None else ''
            if not url:
                continue
            body = self.http.get(url, ttl_seconds=self.policy.refresh_seconds)
            published = values.get('pubDate')
            if published is None:
                published = values.get('published')
            publication, precision = self._publication(published, body['observed_at'])
            text = release_text(body['body'])
            facts = guidance_facts(text)
            documents.append(SourceDocument(self.http.provider, url, company['name'], 'issuer_release', text,
                                            publication, body['observed_at'], url,
                                            {'raw_response_id': body['response_id'], 'cik': company['cik'],
                                             'publication_precision': precision}, facts,
                                            scheduled_events(body['body']),
                                            () if facts else ('guidance_not_extracted_from_release',)))
        return CollectionResult(tuple(reversed(documents)), {'feed_response_id': response['response_id']})

    def _publication(self, element, observed):
        if element is not None and element.text:
            try:
                return timestamp(element.text), 'timestamp'
            except ValueError:
                try:
                    return timestamp(parsedate_to_datetime(element.text).isoformat()), 'timestamp'
                except (ValueError, TypeError):
                    pass
        return observed, 'unknown'
