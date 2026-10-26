"""Research on every shared provider: selection, endpoints, billing and the shared call, with no network."""
from dataclasses import replace
from hashlib import sha256
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_research_orchestrator import ResearchFixture
from tests.research_foundation_fixtures import ENTRY
from agent_helpers.shared import Client
from agent_stages.research_planner import ResearchPlanner, ResearchProviderError
from agent_stages.research_thesis_writer import ResearchThesisWriter
from services.research_model_policy import (
    SUPPORTED_PROVIDERS, ResearchModelPolicy, billed, configured_provider, planner_manifest, thesis_provider,
)
from services.research_orchestrator import ResearchOrchestrator
from services.settings import OpenAICompatibleSettings, use_settings

MISTRAL = OpenAICompatibleSettings(base_url='https://api.mistral.ai/v1')
RATES = ResearchModelPolicy(input_usd_per_million=1, output_usd_per_million=2)
READY = {'outcome': 'ready', 'reason': 'Evidence available', 'requests': []}


def completion(content=json.dumps(READY), finish_reason='stop'):
    return SimpleNamespace(model='mistral-small-2509', choices=[
        SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=300, completion_tokens=40, total_tokens=340,
                              completion_tokens_details=None))


def hosted_client(response):
    raw = Mock()
    raw.with_options.return_value.chat.completions.create.return_value = response
    return Client(provider='openai_compatible', raw_client=raw, host=MISTRAL.base_url, label=MISTRAL.base_url)


class ProviderSelectionTests(unittest.TestCase):
    def test_every_shared_provider_builds_a_planner(self):
        with use_settings(openai_compatible=MISTRAL):
            for provider in SUPPORTED_PROVIDERS:
                with self.subTest(provider=provider):
                    self.assertEqual(ResearchPlanner(provider=provider, model='fixture', policy=RATES).provider, provider)

    def test_blank_hosted_base_url_fails_before_any_reservation(self):
        with use_settings(openai_compatible=OpenAICompatibleSettings()), self.assertRaisesRegex(ValueError, 'host'):
            ResearchPlanner(provider='openai_compatible', model='fixture')

    def test_research_inherits_the_strategist_provider_with_its_model(self):
        environ = {'LLM_PROVIDER': 'vertex', 'STRATEGIST_PROVIDER': 'openai_compatible'}
        with patch.dict(os.environ, environ, clear=True):
            self.assertEqual((configured_provider(), thesis_provider()), ('openai_compatible', 'openai_compatible'))
        with patch.dict(os.environ, {**environ, 'RESEARCH_PLANNER_PROVIDER': 'ollama',
                                     'RESEARCH_THESIS_PROVIDER': 'gemini'}, clear=True):
            self.assertEqual((configured_provider(), thesis_provider()), ('ollama', 'gemini'))

    def test_identity_hashes_the_real_endpoint(self):
        with use_settings(openai_compatible=MISTRAL):
            hosted = ResearchPlanner(provider='openai_compatible', model='fixture').identity
        self.assertEqual(hosted['endpoint_hash'], sha256(MISTRAL.base_url.encode()).hexdigest())
        self.assertIsNone(ResearchPlanner(provider='gemini', model='fixture', policy=RATES).identity['endpoint_hash'])

    def test_existing_vertex_manifest_is_unchanged(self):
        # The manifest is part of a champion's identity; an unchanged configuration keeps its value.
        with patch.dict(os.environ, {'LLM_PROVIDER': 'vertex'}, clear=True):
            manifest = planner_manifest()
        self.assertEqual(manifest['endpoint_hash'], sha256(b'http://localhost:11434').hexdigest())


class BillingTests(unittest.TestCase):
    def test_hosted_providers_without_rates_are_free(self):
        reservation = ResearchModelPolicy().reservation('openai_compatible', [], {})
        self.assertEqual((reservation['billed'], reservation['cost_reserved_usd']), (False, 0))

    def test_hosted_providers_with_rates_reserve_thinking_too(self):
        reservation = RATES.reservation('openai_compatible', [], {})
        self.assertTrue(reservation['billed'] and reservation['cost_reserved_usd'] > 0)
        self.assertEqual(reservation['output_tokens_reserved'],
                         RATES.max_output_tokens + RATES.thinking_token_reservation)
        self.assertEqual(RATES.request_output_tokens('openai_compatible'), reservation['output_tokens_reserved'])

    def test_openai_always_needs_rates(self):
        with self.assertRaisesRegex(ValueError, 'token rates'):
            ResearchModelPolicy().reservation('openai', [], {})

    def test_local_models_never_charge(self):
        self.assertEqual(RATES.reservation('llamacpp', [], {})['cost_reserved_usd'], 0)

    def test_stored_reservations_without_the_flag_keep_their_billing(self):
        self.assertEqual((billed({'provider': 'vertex'}), billed({'provider': 'ollama'})), (True, False))

    def test_thesis_model_differing_from_a_billed_planner_needs_its_own_rates(self):
        environ = {'LLM_PROVIDER': 'openai_compatible', 'RESEARCH_PLANNER_MODEL': 'mistral-small-latest',
                   'RESEARCH_THESIS_MODEL': 'mistral-medium-latest', 'RESEARCH_PLANNER_INPUT_USD_PER_MILLION': '1',
                   'RESEARCH_PLANNER_OUTPUT_USD_PER_MILLION': '2'}
        with patch.dict(os.environ, environ, clear=True), use_settings(openai_compatible=MISTRAL), \
                self.assertRaisesRegex(ValueError, 'RESEARCH_THESIS_INPUT'):
            ResearchThesisWriter()


class HostedPlannerTests(unittest.TestCase):
    def plan(self, response, policy=RATES):
        with use_settings(openai_compatible=MISTRAL):
            planner = ResearchPlanner(provider='openai_compatible', model='mistral-small-latest', policy=policy)
            client = hosted_client(response)
            with patch('agent_stages.research_planner.get_model_client', return_value=client):
                return planner, client, planner.plan({}, timeout=20)

    def test_one_capped_request_through_the_shared_client(self):
        planner, client, plan = self.plan(completion())
        client.raw_client.with_options.assert_called_once_with(max_retries=0, timeout=20)
        kwargs = client.raw_client.with_options.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual((plan, kwargs['max_tokens']), (READY, RATES.request_output_tokens('openai_compatible')))
        self.assertEqual(planner.last_response['usage'], {'prompt_token_count': 300, 'candidates_token_count': 40,
                                                          'thoughts_token_count': 0, 'total_token_count': 340})

    def test_rate_limit_is_retryable_and_never_records_the_message(self):
        error = Exception('https://api.mistral.ai/v1 key=secret')
        error.status_code = 429
        with use_settings(openai_compatible=MISTRAL):
            planner = ResearchPlanner(provider='openai_compatible', model='mistral-small-latest')
            client = hosted_client(completion())
            client.raw_client.with_options.return_value.chat.completions.create.side_effect = error
            with patch('agent_stages.research_planner.get_model_client', return_value=client), \
                    self.assertRaises(ResearchProviderError) as raised:
                planner.plan({}, timeout=20)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn('secret', json.dumps(planner.last_response))


class HostedAccountingTests(ResearchFixture):
    def test_rated_hosted_planner_charges_the_daily_budget(self):
        self.prior()
        with use_settings(openai_compatible=MISTRAL):
            planner = ResearchPlanner(provider='openai_compatible', model='mistral-small-latest', policy=RATES)
        policy = replace(self.research_policy, daily_cost_budget=2)
        with patch('agent_stages.research_planner.get_model_client', return_value=hosted_client(completion())):
            result = ResearchOrchestrator(planner=planner, policy=policy, clock=lambda: ENTRY, db_path=self.db,
                                          sleep=lambda seconds: None).run(self.candidate)
        self.assertEqual(result['status'], 'ready')
        self.assertGreater(result['cost_consumed'], 0)


if __name__ == '__main__':
    unittest.main()
