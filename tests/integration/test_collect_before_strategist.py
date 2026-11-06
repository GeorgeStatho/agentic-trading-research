"""Company evidence is collected synchronously before the strategist runs on a candidate.

The request queue is worked only by the news collector (every few hours, a handful of companies
per cycle), so a candidate's evidence package often did not exist yet when the strategist read it.
`collect_companies_now` collects the candidates that lack a fresh package right before the
strategist, under the collector lock, and never stops the trading cycle.
"""
from contextlib import closing
from datetime import datetime
import unittest
from unittest.mock import Mock, patch

from tests.research_foundation_fixtures import ENTRY
from tests.test_company_evidence import COMPANY, guidance
from tests.test_event_discovery import DiscoveryFixture, later
from db_common import get_connection
from market_db_modules.company_evidence import latest_evidence_package
from services.discovery.company_evidence_demand import request_pipeline_evidence
from services.discovery.company_evidence_policy import CompanyEvidencePolicy
from services.discovery.company_evidence_service import collect_companies_now
from services.research.research_lock import research_session_lock
from Webscraping.company_evidence.models import CollectionResult
from agentCallers import decision_pipeline

ENV = {'SEC_USER_AGENT': 'Fixture contact@example.test', 'COMPANY_EVIDENCE_SYMBOLS': '',
       'COMPANY_EVIDENCE_ISSUERS_FILE': '', 'DISCOVERY_MODE': 'disabled', 'DISCOVERY_SYMBOLS': ''}


class CollectNowTests(DiscoveryFixture):
    def collect_now(self, symbols=('NVDA',), *, at=ENTRY, policy=None, mapping=None, env=None):
        self.adapter = Mock(name='sec-fixture')
        self.adapter.name = 'sec'
        self.adapter.issuer_map.return_value = mapping if mapping is not None else {'NVDA': COMPANY['cik']}
        self.adapter.collect.return_value = CollectionResult((guidance(),), {})
        with patch('services.discovery.company_evidence_service.SECAdapter', return_value=self.adapter), \
             patch.dict('os.environ', env or ENV):
            return collect_companies_now(list(symbols), db_path=self.db,
                                         policy=policy or CompanyEvidencePolicy(enabled=True),
                                         clock=lambda: datetime.fromisoformat(at))

    def requests(self):
        with closing(get_connection(self.db)) as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM company_evidence_requests')]

    def test_collects_a_company_that_has_no_package(self):
        self.assertIsNone(latest_evidence_package(1, as_of=ENTRY, db_path=self.db))
        result = self.collect_now()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.adapter.collect.call_count, 1)
        self.assertIsNotNone(latest_evidence_package(1, as_of=ENTRY, db_path=self.db))

    def test_completes_the_companys_pending_requests(self):
        request_pipeline_evidence(['NVDA'], scope='manager', at=ENTRY, db_path=self.db)
        self.collect_now()
        self.assertEqual({row['status'] for row in self.requests()}, {'completed'})

    def test_company_with_a_fresh_package_is_not_collected_again(self):
        self.collect_now()
        self.collect_now(at=later(60))
        self.assertEqual(self.adapter.collect.call_count, 0, 'second call must not reach the provider')

    def test_company_with_a_stale_package_is_collected_again(self):
        policy = CompanyEvidencePolicy(enabled=True)
        self.collect_now(policy=policy)
        self.collect_now(at=later(policy.refresh_seconds + 1), policy=policy)
        self.assertEqual(self.adapter.collect.call_count, 1)

    def test_disabled_collection_never_calls_a_provider(self):
        result = self.collect_now(policy=CompanyEvidencePolicy(enabled=False))
        self.assertEqual(result['status'], 'disabled')
        self.adapter.collect.assert_not_called()
        self.adapter.issuer_map.assert_not_called()

    def test_before_strategist_collection_can_be_switched_off(self):
        result = self.collect_now(policy=CompanyEvidencePolicy(enabled=True, before_strategist=False))
        self.assertEqual(result['status'], 'disabled')
        self.adapter.collect.assert_not_called()

    def test_before_strategist_settings_come_from_the_environment(self):
        with patch.dict('os.environ', {'COMPANY_EVIDENCE_BEFORE_STRATEGIST': 'false',
                                       'COMPANY_EVIDENCE_BEFORE_STRATEGIST_MAX_COMPANIES': '3'}):
            policy = CompanyEvidencePolicy.from_env()
        self.assertEqual((policy.before_strategist, policy.before_strategist_max_companies), (False, 3))
        defaults = CompanyEvidencePolicy()
        self.assertEqual((defaults.before_strategist, defaults.before_strategist_max_companies), (True, 8))

    def test_busy_collector_returns_immediately(self):
        with research_session_lock(self.db, 'company-evidence-collector'):
            result = self.collect_now()
        self.assertEqual(result['status'], 'busy')
        self.adapter.collect.assert_not_called()

    def test_collects_at_most_the_configured_number_of_companies(self):
        policy = CompanyEvidencePolicy(enabled=True, before_strategist_max_companies=1)
        self.collect_now(['NVDA', 'OTHER'], policy=policy,
                         mapping={'NVDA': COMPANY['cik'], 'OTHER': '0000000002'})
        self.assertEqual(self.adapter.collect.call_count, 1)

    def test_unknown_symbol_is_ignored(self):
        result = self.collect_now(['NOPE'])
        self.assertNotEqual(result['status'], 'failed')
        self.adapter.collect.assert_not_called()

    def test_missing_sec_contact_needs_configuration(self):
        result = self.collect_now(env={**ENV, 'SEC_USER_AGENT': ''})
        self.assertEqual(result['status'], 'configuration_required')
        self.adapter.collect.assert_not_called()


class PipelineHookTests(unittest.TestCase):
    """The trading cycle collects evidence before its strategist calls and survives a failure."""

    def setUp(self):
        self.events = []
        patches = {
            'build_market_regime_snapshot': {'return_value': {'state': 'trend_up', 'as_of': '2026-07-21T15:00:00+00:00'}},
            'record_manager_decision_history': {'return_value': 1},
            'decide_company_purchase': {'side_effect': self.strategist},
            'decide_company_option_position': {'side_effect': self.manager},
            'apply_deterministic_option_selection': {'side_effect': lambda result: result},
            'collect_companies_now': {'side_effect': self.collect},
            'LOGGER': {},
        }
        for name, options in patches.items():
            patcher = patch.object(decision_pipeline, name, **options)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.collect_error = None

    def collect(self, symbols, *args, **kwargs):
        self.events.append(('collect', tuple(symbols)))
        if self.collect_error:
            raise self.collect_error
        return {'status': 'completed', 'companies': []}

    def strategist(self, symbol, **kwargs):
        self.events.append(('strategist', symbol))
        return {'company': {'symbol': symbol}, 'recommendation': {'decision': 'trade_candidate', 'confidence': 'high'}}

    def manager(self, symbol, **kwargs):
        return {'company': {'symbol': symbol}, 'recommendation': {'decision': 'call', 'confidence': 'high'}}

    def run_cycle(self, symbols):
        return decision_pipeline._run_strategist_and_manager(symbols, on_manager_result=lambda result: None)

    def test_evidence_is_collected_before_any_strategist_call(self):
        self.run_cycle(['AAPL', 'MSFT'])
        self.assertEqual(self.events[0], ('collect', ('AAPL', 'MSFT')))
        self.assertEqual([event for event in self.events if event[0] == 'strategist'],
                         [('strategist', 'AAPL'), ('strategist', 'MSFT')])

    def test_collection_failure_does_not_stop_the_cycle(self):
        self.collect_error = RuntimeError('SEC unreachable')
        _, manager_results, failures = self.run_cycle(['AAPL', 'MSFT'])
        self.assertEqual(([r['company']['symbol'] for r in manager_results], failures), (['AAPL', 'MSFT'], []))


if __name__ == '__main__':
    unittest.main()
