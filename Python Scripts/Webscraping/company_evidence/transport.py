"""Bounded HTTP, immutable responses and durable accounting shared by adapters."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
from urllib.parse import urlparse

import requests

from market_db_modules.company_collection import (
    cache_response, cached_response, collection_state, decode_response, reserve_provider_request,
    save_collection_state,
)


def utc_now():
    return datetime.now(timezone.utc)


class ProviderUnavailable(RuntimeError):
    pass


class EvidenceHTTP:
    def __init__(self, provider, hosts, policy, db_path, user_agent, *, session=None, clock=utc_now, sleep=time.sleep):
        self.provider, self.hosts, self.policy, self.db_path = provider, frozenset(hosts), policy, db_path
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self.clock, self.sleep = clock, sleep

    def _validate_url(self, url):
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or parsed.hostname not in self.hosts or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            raise ValueError('Evidence URL is outside configured HTTPS sources')

    def get(self, url, *, ttl_seconds=0, compact=None):
        """`compact(body)` may return a smaller body to store; the response identity stays the original's."""
        self._validate_url(url)
        cached = cached_response(self.provider, url, db_path=self.db_path)
        scope = 'http:' + url
        state = collection_state(self.provider, scope, db_path=self.db_path)
        if cached and state.get('checked_at'):
            age = (self.clock() - datetime.fromisoformat(state['checked_at'])).total_seconds()
            if age < ttl_seconds:
                # The last response can be older than another version if a URL reverts.
                return {**self._cached_version(state.get('response_id'), cached),
                        'observed_at': state.get('version_observed_at', cached['observed_at'])}
        failure = None
        cooldown = collection_state(self.provider, 'cooldown', db_path=self.db_path)
        if cooldown.get('until', 0) > self.clock().timestamp():
            raise ProviderUnavailable('Provider cooldown is still active')
        for attempt in range(self.policy.retries + 1):
            delay = reserve_provider_request(self.provider, self.clock(), daily_limit=self.policy.daily_requests,
                                            interval_seconds=self.policy.interval_seconds, db_path=self.db_path)
            self.sleep(delay)
            try:
                response = self.session.get(url, headers={'User-Agent': self.user_agent},
                                            timeout=self.policy.timeout_seconds, allow_redirects=False, stream=True)
                with response:
                    if response.status_code in (429, 500, 502, 503, 504):
                        failure = ProviderUnavailable(f'{self.provider} HTTP {response.status_code}')
                        if attempt < self.policy.retries:
                            self.sleep(self._retry_delay(response.headers.get('Retry-After'), attempt))
                        continue
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ProviderUnavailable(f'{self.provider} requires HTTP 200; redirects are not followed')
                    body = self._read_body(response)
                observed = self.clock().isoformat()
                saved = cache_response(self.provider, url, body, observed,
                                       stored_body=compact(body) if compact else None, db_path=self.db_path)
                version_observed = (state.get('version_observed_at', saved['observed_at'])
                                    if state.get('response_id') == saved['response_id'] else observed)
                save_collection_state(self.provider, scope,
                                      {'checked_at': observed, 'response_id': saved['response_id'],
                                       'version_observed_at': version_observed},
                                      observed, db_path=self.db_path)
                return {**saved, 'observed_at': version_observed}
            except (requests.Timeout, requests.ConnectionError) as exc:
                failure = ProviderUnavailable(f'{self.provider} transport failed: {type(exc).__name__}')
                if attempt < self.policy.retries:
                    self.sleep(2 ** attempt)
        raise failure or ProviderUnavailable(f'{self.provider} unavailable')

    def _cached_version(self, identity, fallback):
        if not identity or identity == fallback['response_id']:
            return fallback
        from contextlib import closing
        from db_common import get_connection
        with closing(get_connection(self.db_path)) as conn:
            row = conn.execute('SELECT * FROM company_provider_responses WHERE response_id=?', (identity,)).fetchone()
        return decode_response(row) if row else fallback

    def _read_body(self, response):
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > self.policy.max_response_bytes:
                raise ProviderUnavailable('Evidence response exceeds byte limit')
            chunks.append(chunk)
        return b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace')

    def _retry_delay(self, header, attempt):
        try:
            seconds = float(header)
        except (TypeError, ValueError):
            try:
                seconds = (parsedate_to_datetime(header) - self.clock()).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = 2 ** attempt
        if seconds > 60:
            save_collection_state(self.provider, 'cooldown', {'until': self.clock().timestamp() + seconds},
                                  self.clock().isoformat(), db_path=self.db_path)
            raise ProviderUnavailable('Provider requested a cooldown longer than this collection attempt')
        return max(0, seconds)
