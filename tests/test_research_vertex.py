"""Vertex research contracts and accounting, with no credentials or paid requests."""
from dataclasses import replace
from contextlib import closing, contextmanager
import json
import os
import unittest
from unittest.mock import Mock, patch

from tests.test_research_orchestrator import ResearchFixture
from tests.research_foundation_fixtures import ENTRY
from db_common import get_connection
from agent_helpers.llm_client import Client, ask_llm_model
from agent_stages.research_planner import PLAN_SCHEMA, ResearchPlanner, ResearchProviderError
from services.research.research_model_policy import ResearchModelPolicy, planner_manifest
from services.research.research_orchestrator import ResearchOrchestrator
from market_db_modules.research_runtime import ResearchBudgetExceeded


RATES = ResearchModelPolicy(input_usd_per_million=1, output_usd_per_million=2)
READY = {'outcome': 'ready', 'reason': 'Evidence available', 'requests': []}
USAGE = {'prompt_token_count': 120, 'candidates_token_count': 30, 'thoughts_token_count': 10,
         'cached_content_token_count': 0, 'total_token_count': 160}


class ProviderFailure(Exception):
    def __init__(self, status_code):
        super().__init__('secret token https://private.example')
        self.status_code = status_code


def answer(*, usage=None, text=None, finish='STOP'):
    """An ask_llm_model stand-in that reports usage the way the shared Vertex path does."""
    def ask(*args, response_diagnostic, **kwargs):
        response_diagnostic.update({'provider': 'vertex', 'model_version': 'fixture-version', 'finish_reason': finish,
                                    **(USAGE if usage is None else usage)})
        return json.dumps(READY) if text is None else text
    return ask


def failure(code=429):
    def ask(*args, **kwargs):
        # ask_llm_model wraps the provider's exception, whose message may carry URLs or credentials.
        raise RuntimeError('Could not complete the request. secret token') from ProviderFailure(code)
    return ask


@contextmanager
def provider(*effects):
    """Patch the shared model call; each effect answers one request."""
    calls = Mock(side_effect=[effect if isinstance(effect, BaseException) else None for effect in effects])

    def ask(*args, **kwargs):
        index = calls.call_count
        calls(*args, **kwargs)
        return effects[index](*args, **kwargs)

    with patch('agent_stages.research_planner.get_model_client', return_value=Client(provider='vertex', raw_client=None)), \
            patch('agent_stages.research_planner.ask_llm_model', side_effect=ask):
        yield calls


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


class VertexRequestTests(unittest.TestCase):
    def setUp(self):
        self.planner = ResearchPlanner(provider='vertex', model='gemini-2.5-flash', policy=RATES)

    def test_one_request_with_the_session_deadline(self):
        with provider(answer()) as ask:
            self.planner.plan({}, timeout=7)
        request = ask.call_args.kwargs
        self.assertEqual((request['retry'], request['timeout'], request['max_output_tokens'], request['thinking_budget']),
                         (False, 7, RATES.max_output_tokens, RATES.thinking_budget))

    def test_usage_and_diagnostics_are_recorded(self):
        with provider(answer()):
            self.planner.plan({}, timeout=7)
        self.assertEqual((self.planner.last_response['usage'], self.planner.last_response['diagnostics']['model_version']),
                         (USAGE, 'fixture-version'))

    def test_error_details_do_not_leak_provider_exception_message(self):
        with provider(failure(429)), self.assertRaises(ResearchProviderError) as raised:
            self.planner.plan({}, timeout=7)
        self.assertTrue(raised.exception.retryable and 'secret' not in json.dumps(self.planner.last_response))
        self.assertIsNone(raised.exception.__cause__)

    def test_truncated_answer_is_a_non_retryable_provider_failure(self):
        with provider(answer(finish='MAX_TOKENS')), self.assertRaises(ResearchProviderError) as raised:
            self.planner.plan({}, timeout=7)
        self.assertFalse(raised.exception.retryable)

    def test_request_timeout_exhausts_the_deadline(self):
        def timed_out(*args, **kwargs):
            raise RuntimeError('Could not complete the request.') from type('ReadTimeout', (Exception,), {})()
        with provider(timed_out), self.assertRaises(TimeoutError):
            self.planner.plan({}, timeout=7)


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
        with provider() as ask:
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], ask.call_count, result['cost_consumed']), ('budget_exhausted', 0, 0))

    def test_known_transient_failure_is_retried_and_counted(self):
        with provider(failure(), answer()):
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['planner_calls']), ('ready', 2))

    def test_retry_limit_stops_before_third_attempt(self):
        with provider(failure(), failure(), failure()):
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['planner_calls']), ('failed', 2))

    def test_retry_is_denied_when_its_second_cost_reservation_does_not_fit(self):
        self.research_policy = replace(self.research_policy, daily_cost_budget=1)
        self.planner.reservation = Mock(return_value={'provider': 'vertex', 'cost_reserved_usd': .75})
        with provider(failure(), failure()) as ask:
            result = self.worker().run(self.candidate)
        self.assertEqual((result['status'], result['cost_consumed'], ask.call_count), ('budget_exhausted', .75, 1))

    def test_missing_usage_retains_the_full_reservation(self):
        with provider(answer(usage={})):
            result = self.worker().run(self.candidate)
        records = [s for s in self.trace(result)['steps'] if s['kind'] == 'planner_usage']
        self.assertTrue(records[0]['cost_status'] == 'unknown' and result['cost_consumed'] > 0)

    def test_reported_overage_is_charged_and_stops_research(self):
        usage = {**USAGE, 'thoughts_token_count': 1_000_000, 'total_token_count': 1_000_150}
        with provider(answer(usage=usage)):
            result = self.worker().run(self.candidate)
        with closing(get_connection(self.db)) as conn:
            reserved = conn.execute("SELECT reserved_cost FROM research_daily_allowances WHERE kind='planner'").fetchone()[0]
        self.assertTrue(result['stop_reason'] == 'provider_usage_exceeded_reservation' and reserved >= RATES.usage_cost(usage))

    def test_crashed_paid_call_keeps_reservation_and_is_not_repeated(self):
        with provider(KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.worker().run(self.candidate)
        with provider() as ask:
            result = self.worker().run(self.candidate)
        self.assertTrue(result['status'] == 'failed' and result['cost_consumed'] > 0 and ask.call_count == 0)


class GoogleSDKContractTests(unittest.TestCase):
    """Use the real SDK through the shared client with an in-memory HTTP transport; never contact Google."""
    def setUp(self):
        try:
            from google import genai
            from google.auth.credentials import AnonymousCredentials
            import httpx
        except ImportError:
            self.skipTest('Optional google-genai dependency is not installed')
        credentials = AnonymousCredentials()
        credentials.token = 'fixture-token'
        self.requests = []
        self.httpx = httpx
        self.client = Client(provider='vertex', raw_client=genai.Client(
            vertexai=True, project='fixture-project', location='global', credentials=credentials,
            http_options={'client_args': {'transport': httpx.MockTransport(self.handler), 'trust_env': False}}))

    def handler(self, request):
        self.requests.append(request)
        return self.httpx.Response(429, json={'error': {'code': 429, 'message': 'fixture error',
                                                        'status': 'RESOURCE_EXHAUSTED'}})

    def test_real_sdk_sends_only_one_generation_request_on_429(self):
        with patch('agent_helpers.llm_client.time.sleep'), self.assertRaises(Exception):
            ask_llm_model(self.client, 'gemini-2.5-flash', 'system', 'fixture evidence', response_schema=PLAN_SCHEMA,
                          max_output_tokens=2048, thinking_budget=1024, timeout=4, retry=False)
        self.assertEqual(len(self.requests), 1)

    def test_the_shared_default_still_retries(self):
        # Other stages keep VERTEX_MAX_ATTEMPTS; only callers that opt out send a single request.
        with patch('agent_helpers.llm_client.time.sleep'), self.assertRaises(Exception):
            ask_llm_model(self.client, 'gemini-2.5-flash', 'system', 'fixture evidence')
        self.assertGreater(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
