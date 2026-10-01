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
from agent_stages.research_planner_transport import dispatch, thinking_config, vertex_response
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
    def test_inherits_existing_vertex_provider_and_strategist_model(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'vertex', 'STRATEGIST_MODEL': 'gemini-2.5-pro'}, clear=True):
            planner = ResearchPlanner()
        self.assertEqual((planner.provider, planner.model), ('vertex', 'gemini-2.5-pro'))

    def test_research_override_takes_precedence(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'ollama', 'RESEARCH_PLANNER_PROVIDER': 'vertex',
                                    'RESEARCH_PLANNER_MODEL': 'gemini-2.5-flash'}, clear=True):
            planner = ResearchPlanner()
        self.assertEqual((planner.provider, planner.model), ('vertex', 'gemini-2.5-flash'))

    def test_legacy_model_alias_uses_vertex_default(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'vertex', 'STRATEGIST_MODEL': 'world-news-sectors',
                                    'VERTEX_DEFAULT_MODEL': 'gemini-2.5-flash'}, clear=True):
            self.assertEqual(ResearchPlanner().model, 'gemini-2.5-flash')

    def test_google_project_and_location_aliases_are_preserved(self):
        with patch.dict(os.environ, {'VERTEX_PROJECT': 'fixture-project', 'VERTEX_LOCATION': 'us-central1'}, clear=True):
            planner = ResearchPlanner(provider='vertex')
        self.assertEqual((planner.project, planner.location), ('fixture-project', 'us-central1'))

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

    def test_thinking_is_included_in_reservation(self):
        reservation = ResearchPlanner(provider='vertex', policy=RATES).reservation({})
        self.assertEqual(reservation['output_tokens_reserved'], RATES.max_output_tokens + RATES.thinking_token_reservation)

    def test_reported_cost_includes_thinking_tokens(self):
        self.assertAlmostEqual(RATES.usage_cost(USAGE), .0002)

    def test_missing_usage_is_unknown_instead_of_zero(self):
        self.assertIsNone(RATES.usage_cost({'prompt_token_count': 100}))

    def test_gemini_three_uses_thinking_level(self):
        self.assertEqual(thinking_config({'model': 'gemini-3-pro', 'thinking_budget': 1024}),
                         {'thinking_level': 'LOW', 'include_thoughts': False})

    def test_invalid_negative_rate_is_rejected(self):
        with self.assertRaises(ValueError):
            replace(RATES, output_usd_per_million=-1)

    def test_project_change_changes_session_identity(self):
        with patch.dict(os.environ, {'GOOGLE_CLOUD_PROJECT': 'project-a'}):
            original = ResearchPlanner(provider='vertex').identity
        with patch.dict(os.environ, {'GOOGLE_CLOUD_PROJECT': 'project-b'}):
            self.assertNotEqual(original, ResearchPlanner(provider='vertex').identity)


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

    def test_provider_timeout_is_in_milliseconds(self):
        self.call()
        self.assertEqual(self.sdk.Client.call_args.kwargs['http_options']['timeout'], 7000)

    def test_uses_adc_project_when_no_explicit_project(self):
        self.call()
        self.assertEqual(self.sdk.Client.call_args.kwargs['project'], 'adc-project')

    def test_generation_is_bounded_and_disables_automatic_tools(self):
        self.call()
        config = self.client.models.generate_content.call_args.kwargs['config']
        self.assertEqual((config['candidate_count'], config['max_output_tokens'], config['thinking_config'],
                          config['automatic_function_calling']),
                         (1, 2048, {'thinking_budget': 1024, 'include_thoughts': False}, {'disable': True}))

    def test_usage_is_returned_without_thought_content(self):
        self.assertEqual(self.call()['usage'], USAGE)

    def test_no_cache_or_tool_requests_are_created(self):
        self.call()
        self.assertEqual(self.client.method_calls, [
            unittest.mock.call.models.generate_content(**self.client.models.generate_content.call_args.kwargs),
            unittest.mock.call.close()])

    def test_truncated_response_keeps_usage_but_cannot_become_ready(self):
        self.response.candidates[0].finish_reason = 'MAX_TOKENS'
        result = self.call()
        self.assertTrue(result['error'] and result['usage'] == USAGE)

    def test_close_is_called_on_provider_failure(self):
        self.client.models.generate_content.side_effect = RuntimeError()
        with self.assertRaises(RuntimeError):
            self.call()
        self.client.close.assert_called_once()

    def test_error_details_do_not_leak_provider_exception_message(self):
        error = RuntimeError('secret token https://private.example')
        error.code = 429
        with patch('agent_stages.research_planner_transport.vertex_response', side_effect=error):
            result = dispatch(self.payload)
        self.assertTrue(result['diagnostics']['retryable'] and 'secret' not in json.dumps(result))

    def test_authentication_error_is_not_retryable(self):
        error = RuntimeError('unauthorized')
        error.code = 401
        with patch('agent_stages.research_planner_transport.vertex_response', side_effect=error):
            self.assertFalse(dispatch(self.payload)['diagnostics']['retryable'])

    def test_subprocess_retains_remaining_wall_timeout(self):
        planner = ResearchPlanner(provider='vertex', policy=RATES)
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope())) as run:
            result = planner.plan({}, timeout=4)
        self.assertTrue(result == READY and run.call_args.kwargs['timeout'] == 4)


class VertexAccountingTests(ResearchFixture):
    def setUp(self):
        super().setUp()
        self.prior()
        self.planner = ResearchPlanner(provider='vertex', model='gemini-2.5-flash', policy=RATES)
        self.research_policy = replace(self.research_policy, daily_cost_budget=2)

    def worker(self, **kwargs):
        return ResearchOrchestrator(planner=self.planner, policy=self.research_policy,
                                    clock=lambda: ENTRY, db_path=self.db, sleep=lambda seconds: None, **kwargs)

    def test_vertex_result_completes_shadow_research(self):
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope())):
            self.assertEqual(self.worker().run(self.candidate)['status'], 'ready')

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

    def test_retry_counts_toward_daily_model_allowance(self):
        self.research_policy = replace(self.research_policy, daily_planner_calls=1)
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(error=True, usage={}))):
            result = self.worker().run(self.candidate)
        self.assertEqual(result['stop_reason'], 'daily_planner_allowance')

    def test_retry_is_denied_when_its_second_cost_reservation_does_not_fit(self):
        self.research_policy = replace(self.research_policy, daily_cost_budget=1)
        self.planner.reservation = Mock(return_value={'provider': 'vertex', 'cost_reserved_usd': .75})
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(error=True, usage={}))) as run:
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['cost_consumed'], run.call_count), ('budget_exhausted', .75, 1))

    def test_later_day_overage_is_charged_to_original_reservation_day(self):
        from tests.test_event_discovery import later
        now = [ENTRY]
        usage = {**USAGE, 'thoughts_token_count': 1_000_000}
        def crosses_midnight(*args, **kwargs):
            now[0] = later(86400)
            return completed(envelope(usage=usage))
        worker = ResearchOrchestrator(planner=self.planner, policy=self.research_policy, clock=lambda: now[0], db_path=self.db)
        with patch('agent_stages.research_planner.subprocess.run', side_effect=crosses_midnight):
            worker.run(self.candidate)
        with closing(get_connection(self.db)) as conn:
            rows = conn.execute("SELECT day,reserved_cost FROM research_daily_allowances WHERE kind='planner'").fetchall()
        self.assertTrue(len(rows) == 1 and rows[0]['day'] == ENTRY[:10] and rows[0]['reserved_cost'] >= RATES.usage_cost(usage))

    def test_retry_cost_reservation_is_not_refunded(self):
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(error=True, usage={}))):
            result = self.worker().run(self.candidate)
        trace = self.trace(result)
        costs = [s['reservation']['cost_reserved_usd'] for s in trace['steps'] if s['kind'] == 'call_reserved' and s['call_type'] == 'planner']
        self.assertAlmostEqual(result['cost_consumed'], sum(costs))

    def test_usage_and_version_survive_offline_replay(self):
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope())):
            result = self.worker().run(self.candidate)
        records = [s for s in self.trace(result)['steps'] if s['kind'] == 'planner_usage']
        self.assertEqual((records[0]['usage'], records[0]['diagnostics']['model_version']), (USAGE, 'fixture-version'))

    def test_invalid_json_keeps_usage_and_does_not_retry(self):
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(text='invalid JSON'))):
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['planner_calls'], result['reported_cost_estimate_usd']), ('failed', 1, .0002))

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

    def test_deadline_timeout_is_not_automatically_retried(self):
        with patch('agent_stages.research_planner.subprocess.run', side_effect=subprocess.TimeoutExpired('planner', 1)):
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['planner_calls']), ('budget_exhausted', 1))

    def test_crashed_paid_call_keeps_reservation_and_is_not_repeated(self):
        with patch('agent_stages.research_planner.subprocess.run', side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.worker().run(self.candidate)
        with patch('agent_stages.research_planner.subprocess.run') as run:
            result = self.worker().run(self.candidate)
        self.assertTrue(result['status'] == 'failed' and result['cost_consumed'] > 0 and run.call_count == 0)

    def test_resume_after_recorded_retryable_failure_uses_same_round(self):
        original = ResearchOrchestrator.checkpoint
        def crash(worker, event, allowance=None, cost_adjustment=None):
            original(worker, event, allowance, cost_adjustment)
            if event['kind'] == 'planner_attempt_failed':
                raise KeyboardInterrupt()
        with patch.object(ResearchOrchestrator, 'checkpoint', crash), \
             patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope(error=True, usage={}))), \
             self.assertRaises(KeyboardInterrupt):
            self.worker().run(self.candidate)
        with patch('agent_stages.research_planner.subprocess.run', return_value=completed(envelope())):
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['planner_calls']), ('ready', 2))


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

    def test_real_sdk_accepts_vertex_schema_and_token_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            result = vertex_response(self.payload, sdk=SimpleNamespace(Client=self.client), auth_default=self.auth)
        request = json.loads(self.requests[0].content)
        self.assertTrue(not result['error'] and request['generationConfig']['responseJsonSchema'] == PLAN_SCHEMA
                        and result['usage']['thoughts_token_count'] == 10)

    def test_real_sdk_sends_only_one_generation_request_on_429(self):
        self.status = 429
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(Exception):
            vertex_response(self.payload, sdk=SimpleNamespace(Client=self.client), auth_default=self.auth)
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
