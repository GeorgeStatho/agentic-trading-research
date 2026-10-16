"""Vertex research contracts and accounting, with no credentials or paid requests."""
from dataclasses import replace
from contextlib import closing
import json
import os
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_research_orchestrator import ResearchFixture
from tests.research_foundation_fixtures import ENTRY
from db_common import get_connection
from agent_stages.research_planner import PLAN_SCHEMA, ResearchPlanner, ResearchProviderError
from agent_stages.research_planner_transport import dispatch, vertex_response
from services.research_model_policy import ResearchModelPolicy, planner_manifest
from services.research_orchestrator import ResearchOrchestrator
from market_db_modules.research_runtime import ResearchBudgetExceeded


RATES = ResearchModelPolicy(input_usd_per_million=1, output_usd_per_million=2)
READY = {'outcome': 'ready', 'reason': 'Evidence available', 'requests': []}
USAGE = {'prompt_token_count': 120, 'candidates_token_count': 30, 'thoughts_token_count': 10,
         'cached_content_token_count': 0, 'total_token_count': 160}


def envelope(*, error=False, usage=None, text=None, code=429, retryable=True):
    return {'text': json.dumps(READY) if text is None else text, 'usage': USAGE if usage is None else usage,
            'error': error, 'diagnostics': {'provider': 'vertex', 'status_code': code if error else 200,
                                           'retryable': error and retryable, 'model_version': 'fixture-version'}}


def completed(response):
    return subprocess.CompletedProcess([], 0, json.dumps(response))


class VertexConfigurationTests(unittest.TestCase):
    def test_identity_and_manifest_do_not_contain_credential_path(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'vertex', 'GOOGLE_APPLICATION_CREDENTIALS': '/secret/key.json'}, clear=True):
            values = json.dumps([ResearchPlanner().identity, planner_manifest()])
        self.assertNotIn('/secret/key.json', values)

    def test_missing_rates_reject_paid_reservation(self):
        with self.assertRaisesRegex(ValueError, 'token rates'):
            ResearchPlanner(provider='vertex', policy=ResearchModelPolicy()).reservation({})

    def test_input_limit_is_checked_before_provider_access(self):
        planner = ResearchPlanner(provider='vertex', policy=replace(RATES, max_input_tokens=10))
        with self.assertRaisesRegex(ResearchBudgetExceeded, 'input_token_budget'):
            planner.reservation({'evidence': 'x' * 100})

    def test_reported_cost_includes_thinking_tokens(self):
        self.assertAlmostEqual(RATES.usage_cost(USAGE), .0002)


class VertexTransportTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'provider': 'vertex', 'model': 'gemini-2.5-flash', 'timeout': 7,
                        'project': None, 'location': 'global', 'format': PLAN_SCHEMA,
                        'max_output_tokens': 2048, 'thinking_budget': 1024,
                        'messages': [{'content': 'system'}, {'content': 'untrusted evidence'}]}
        self.response = SimpleNamespace(text=json.dumps(READY), usage_metadata=SimpleNamespace(**USAGE),
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value='STOP'))], model_version='fixture-version')
        self.client = Mock()
        self.client.models.generate_content.return_value = self.response
        self.sdk = Mock(Client=Mock(return_value=self.client))
        self.auth = Mock(return_value=(object(), 'adc-project'))

    def call(self):
        return vertex_response(self.payload, sdk=self.sdk, auth_default=self.auth)

    def test_sdk_retries_are_disabled(self):
        self.call()
        self.assertEqual(self.sdk.Client.call_args.kwargs['http_options']['retry_options'], {'attempts': 1})

    def test_error_details_do_not_leak_provider_exception_message(self):
        error = RuntimeError('secret token https://private.example')
        error.code = 429
        with patch('agent_stages.research_planner_transport.vertex_response', side_effect=error):
            result = dispatch(self.payload)
        self.assertTrue(result['diagnostics']['retryable'] and 'secret' not in json.dumps(result))


class VertexAccountingTests(ResearchFixture):
    def setUp(self):
        super().setUp()
        self.prior()
        self.planner = ResearchPlanner(provider='vertex', model='gemini-2.5-flash', policy=RATES)
        self.research_policy = replace(self.research_policy, daily_cost_budget=2)

    def worker(self, **kwargs):
        return ResearchOrchestrator(planner=self.planner, policy=self.research_policy,
                                    clock=lambda: ENTRY, db_path=self.db, sleep=lambda seconds: None, **kwargs)

    def test_zero_daily_allowance_prevents_paid_dispatch(self):
        self.research_policy = replace(self.research_policy, daily_cost_budget=0)
        with patch('agent_stages.research_planner.subprocess.run') as run:
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], run.call_count, result['cost_consumed']), ('budget_exhausted', 0, 0))

    def test_known_transient_failure_is_retried_and_counted(self):
        responses = [completed(envelope(error=True, usage={})), completed(envelope())]
        with patch('agent_stages.research_planner.subprocess.run', side_effect=responses):
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['planner_calls']), ('ready', 2))

    def test_retry_limit_stops_before_third_attempt(self):
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(error=True, usage={}))):
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['planner_calls']), ('failed', 2))

    def test_retry_is_denied_when_its_second_cost_reservation_does_not_fit(self):
        self.research_policy = replace(self.research_policy, daily_cost_budget=1)
        self.planner.reservation = Mock(return_value={'provider': 'vertex', 'cost_reserved_usd': .75})
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(error=True, usage={}))) as run:
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['cost_consumed'], run.call_count), ('budget_exhausted', .75, 1))

    def test_missing_usage_retains_the_full_reservation(self):
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(usage={}))):
            result = self.worker().run(self.candidate)
        records = [s for s in self.trace(result)['steps'] if s['kind'] == 'planner_usage']
        self.assertTrue(records[0]['cost_status'] == 'unknown' and result['cost_consumed'] > 0)

    def test_reported_overage_is_charged_and_stops_research(self):
        usage = {**USAGE, 'thoughts_token_count': 1_000_000, 'total_token_count': 1_000_150}
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(usage=usage))):
            result = self.worker().run(self.candidate)
        with closing(get_connection(self.db)) as conn:
            reserved = conn.execute("SELECT reserved_cost FROM research_daily_allowances WHERE kind='planner'").fetchone()[0]
        self.assertTrue(result['stop_reason'] == 'provider_usage_exceeded_reservation' and reserved >= RATES.usage_cost(usage))

    def test_crashed_paid_call_keeps_reservation_and_is_not_repeated(self):
        with patch('agent_stages.research_planner.subprocess.run', side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.worker().run(self.candidate)
        with patch('agent_stages.research_planner.subprocess.run') as run:
            result = self.worker().run(self.candidate)
        self.assertTrue(result['status'] == 'failed' and result['cost_consumed'] > 0 and run.call_count == 0)


class GoogleSDKContractTests(unittest.TestCase):
    """Use the real SDK with an in-memory HTTP transport; never contact Google."""
    def setUp(self):
        try:
            from google import genai
            from google.auth.credentials import AnonymousCredentials
            import httpx
        except ImportError:
            self.skipTest('Optional google-genai dependency is not installed')
        self.genai, self.httpx = genai, httpx
        credentials = AnonymousCredentials()
        credentials.token = 'fixture-token'
        self.auth = lambda **kwargs: (credentials, 'fixture-project')
        self.requests = []
        self.status = 200
        self.payload = {'provider': 'vertex', 'model': 'gemini-2.5-flash', 'timeout': 4,
                        'project': 'fixture-project', 'location': 'global', 'format': PLAN_SCHEMA,
                        'max_output_tokens': 2048, 'thinking_budget': 1024,
                        'messages': [{'content': 'system'}, {'content': 'fixture evidence'}]}

    def handler(self, request):
        self.requests.append(request)
        if self.status != 200:
            return self.httpx.Response(self.status, json={'error': {'code': self.status, 'message': 'fixture error',
                                                                  'status': 'RESOURCE_EXHAUSTED'}})
        return self.httpx.Response(200, json={
            'candidates': [{'content': {'role': 'model', 'parts': [{'text': json.dumps(READY)}]}, 'finishReason': 'STOP'}],
            'usageMetadata': {'promptTokenCount': 120, 'candidatesTokenCount': 30, 'thoughtsTokenCount': 10,
                              'totalTokenCount': 160}, 'modelVersion': 'fixture-version'})

    def client(self, **kwargs):
        kwargs['http_options']['client_args']['transport'] = self.httpx.MockTransport(self.handler)
        kwargs['http_options']['client_args']['trust_env'] = False
        return self.genai.Client(**kwargs)

    def test_real_sdk_sends_only_one_generation_request_on_429(self):
        self.status = 429
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(Exception):
            vertex_response(self.payload, sdk=SimpleNamespace(Client=self.client), auth_default=self.auth)
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
