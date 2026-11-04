"""DISCOVERY_MODE=enforce lets admitted discovery entries reach the production strategist and manager."""
from contextlib import closing
from dataclasses import replace
import json
import logging
import os
import unittest
from unittest.mock import patch

from db_common import get_connection
from services.discovery.discovery_policy import DiscoveryPolicy
from tests.research_foundation_fixtures import ENTRY
from tests.test_company_evidence import COMPANY
from tests.test_event_discovery import DiscoveryFixture


class StrategistReached(RuntimeError):
    pass


def reached(symbol, **kwargs):
    raise StrategistReached(symbol)


class PolicyTests(unittest.TestCase):
    def test_enforce_is_a_mode_and_unknown_modes_are_errors(self):
        self.assertEqual(DiscoveryPolicy(mode='enforce').mode, 'enforce')
        with self.assertRaises(ValueError):
            DiscoveryPolicy(mode='live')
        with self.assertRaises(ValueError):
            DiscoveryPolicy(mode='enforce', production_limit=-1)

    def test_an_invalid_mode_stops_startup_instead_of_disabling_discovery(self):
        from services.runtime.startup_readiness import StartupReadinessChecker
        with patch.dict(os.environ, {'DISCOVERY_MODE': 'enforced', 'STARTUP_READINESS_CHECK': 'false'}), \
                self.assertRaises(ValueError):
            StartupReadinessChecker(logging.getLogger(__name__)).run(runtime_name='worker', require_llm=False)


class EnforceFixture(DiscoveryFixture):
    def setUp(self):
        super().setUp()
        self.package(company={**COMPANY, 'id': 2, 'symbol': 'OTHER'})
        self.discover()
        self.process()
        self.policy = replace(self.policy, mode='enforce', production_limit=5)

    def entries(self, policy=None):
        from services.discovery.discovery_routing import admitted_entries
        return admitted_entries(policy=policy or self.policy, at=ENTRY, db_path=self.db)

    def run_stack(self, mode):
        from agentCallers import decision_pipeline as agent_main
        with patch.dict(os.environ, {'DISCOVERY_MODE': mode, 'DISCOVERY_PRODUCTION_LIMIT': '5'}), \
                patch.object(agent_main, 'run_agent_pipeline_from_existing_data', return_value={'sectors': []}), \
                patch.object(agent_main, 'build_market_regime_snapshot', return_value={}), \
                patch.object(agent_main, '_utc_now', return_value=ENTRY), \
                patch('services.discovery.discovery_queue.run_discovery_shadow_cycle', return_value={}), \
                patch.object(agent_main, 'decide_company_purchase', side_effect=reached):
            return agent_main.run_full_agent_stack_from_existing_data()


class AdmittedEntryTests(EnforceFixture):
    def test_a_ready_entry_is_offered_with_its_origin_and_interest_sources(self):
        entry, = self.entries()
        self.assertEqual((entry['symbol'], entry['company_id']), ('OTHER', 2))
        self.assertEqual(entry['discovery_origins'], ['direct_event'])
        self.assertIn('fixture', entry['interest_sources'])
        self.assertTrue(entry['admission']['allowed'])

    def test_a_routed_entry_is_not_offered_again(self):
        from services.discovery.discovery_routing import record_routes
        entry, = self.entries()
        record_routes([{**entry, 'candidate_id': 'candidate-1'}], at=ENTRY, db_path=self.db)
        self.assertEqual(self.entries(), [])

    def test_the_production_limit_caps_entries(self):
        self.assertEqual(self.entries(replace(self.policy, production_limit=0)), [])


class ProductionRoutingTests(EnforceFixture):
    def test_enforce_sends_a_discovery_only_company_to_the_strategist_as_a_production_candidate(self):
        result = self.run_stack('enforce')
        failure, = result['failed_companies']
        self.assertEqual((failure['symbol'], failure['stage'], failure['error_type']), ('OTHER', 'STRATEGIST', 'StrategistReached'))
        self.assertFalse(str(failure['candidate_id']).startswith('discovery:'))
        self.assertEqual(result['company_symbols'], ['OTHER'])
        with closing(get_connection(self.db)) as conn:
            route = conn.execute('SELECT symbol,candidate_id FROM discovery_production_routes').fetchone()
        self.assertEqual(tuple(route), ('OTHER', failure['candidate_id']))

    def test_the_routed_candidate_records_where_it_came_from(self):
        from market_db_modules.candidate_lifecycle import stage_payloads
        failure, = self.run_stack('enforce')['failed_companies']
        support = stage_payloads(failure['candidate_id'], ['SUPPORT'], db_path=self.db)['SUPPORT']
        self.assertEqual((support['route'], support['discovery_origins'], support['allowed']),
                         ('discovery', ['direct_event'], True))

    def test_the_routed_candidate_carries_the_discovery_event_catalyst_as_primary(self):
        failure, = self.run_stack('enforce')['failed_companies']
        with closing(get_connection(self.db)) as conn:
            event_catalyst, = conn.execute("SELECT catalyst_id FROM discovery_queue WHERE purpose='entry'").fetchone()
            primary, = conn.execute('SELECT catalyst_id FROM candidate_catalysts WHERE candidate_id=? AND is_primary=1',
                                    (failure['candidate_id'],)).fetchone()
            listed = json.loads(conn.execute('SELECT catalyst_ids_json FROM candidates WHERE candidate_id=?',
                                             (failure['candidate_id'],)).fetchone()[0])
        self.assertEqual(primary, event_catalyst)
        self.assertEqual(listed, [event_catalyst])

    def test_shadow_mode_never_reaches_the_strategist(self):
        result = self.run_stack('shadow')
        self.assertEqual((result['company_symbols'], result['failed_companies']), ([], []))


if __name__ == '__main__':
    unittest.main()
