"""Small opt-in collection policy; never include contacts or credentials in manifests."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

from market_db_modules.foundation_store import digest


@dataclass(frozen=True)
class CompanyEvidencePolicy:
    enabled: bool = False
    companies_per_cycle: int = 5
    filings_per_company: int = 3
    refresh_seconds: int = 10800
    stale_seconds: int = 604800
    timeout_seconds: int = 20
    retries: int = 2
    daily_requests: int = 500
    interval_seconds: float = 1.0
    max_response_bytes: int = 20000000

    def __post_init__(self):
        if min(self.companies_per_cycle, self.filings_per_company, self.refresh_seconds,
               self.stale_seconds, self.timeout_seconds, self.daily_requests, self.max_response_bytes) < 1:
            raise ValueError('Company evidence limits must be positive')
        if not 0 <= self.retries <= 5 or not 0.1 <= self.interval_seconds <= 60:
            raise ValueError('Invalid company evidence retry/rate limit')

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv('COMPANY_EVIDENCE_ENABLED', 'false').lower() == 'true',
                   companies_per_cycle=int(os.getenv('COMPANY_EVIDENCE_COMPANIES_PER_CYCLE', '5')),
                   filings_per_company=int(os.getenv('COMPANY_EVIDENCE_FILINGS_PER_COMPANY', '3')),
                   refresh_seconds=int(os.getenv('COMPANY_EVIDENCE_REFRESH_SECONDS', '10800')),
                   stale_seconds=int(os.getenv('COMPANY_EVIDENCE_STALE_SECONDS', '604800')),
                   timeout_seconds=int(os.getenv('COMPANY_EVIDENCE_TIMEOUT_SECONDS', '20')),
                   retries=int(os.getenv('COMPANY_EVIDENCE_RETRIES', '2')),
                   daily_requests=int(os.getenv('COMPANY_EVIDENCE_DAILY_REQUESTS', '500')))


def issuer_configuration():
    path = os.getenv('COMPANY_EVIDENCE_ISSUERS_FILE', '').strip()
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Issuer configuration must map symbols to source settings')
    return {symbol.upper(): settings for symbol, settings in data.items()}


def provider_manifest():
    policy = CompanyEvidencePolicy.from_env()
    return {'policy': asdict(policy), 'issuer_configuration_hash': digest(issuer_configuration()),
            'enabled_providers': ['sec', 'configured_issuer'] if policy.enabled else [],
            'consensus': 'unconfigured', 'sec_contact_configured': bool(os.getenv('SEC_USER_AGENT', '').strip())}
