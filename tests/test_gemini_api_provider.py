"""Gemini API (AI Studio key) provider and per-stage provider overrides, with no network."""
from dataclasses import replace
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import agent_stages.sector_opportunist as sector_opportunist
from agent_helpers import shared
from agent_helpers.shared import Client, ask_llm_model, get_model_client
from services.settings import Settings, StageModel, VertexSettings, get_settings, use_settings

DAILY_QUOTA_ERROR = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric generate_content_free_tier_requests, "
                     "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
MINUTE_QUOTA_ERROR = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric generate_content_free_tier_requests, "
                      "quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier")
STRICT_SCHEMA = {"type": "object", "properties": {"pairs": {"type": "array", "items": {
    "type": "object", "properties": {"sector_id": {"type": "integer"}}, "additionalProperties": False}}},
    "required": ["pairs"], "additionalProperties": False}
FAST_RETRIES = VertexSettings(max_attempts=3, retry_initial_delay_seconds=0.5, retry_max_delay_seconds=0.5,
                              explicit_cache=False, cache_ttl_seconds=900)


def models_from(**environ):
    return Settings.from_environ(environ).models


def gemini_client(*responses):
    raw_client = Mock()
    raw_client.models.generate_content.side_effect = list(responses)
    return Client(provider="gemini", raw_client=raw_client)


def ask(client, model="gemini-3.1-flash-lite"):
    with use_settings(vertex=FAST_RETRIES), patch.object(shared.time, "sleep"):
        return ask_llm_model(client, model, "system", "user")


class StageProviderSettingsTests(unittest.TestCase):
    def test_unset_provider_is_blank_so_llm_provider_applies(self):
        self.assertEqual(models_from().company_opportunist.provider, "")

    def test_stage_provider_is_normalized(self):
        self.assertEqual(models_from(COMPANY_OPPURUNTIST_PROVIDER=" Gemini ").company_opportunist.provider,
                         "gemini")

    def test_macro_news_falls_back_to_world_news_provider(self):
        self.assertEqual(models_from(WORLD_NEWS_PROVIDER="gemini").macro_news.provider, "gemini")

    def test_strategist_does_not_inherit_the_news_provider(self):
        self.assertEqual(models_from(WORLD_NEWS_PROVIDER="gemini").strategist.provider, "")

    def test_thesis_validator_falls_back_to_the_manager_provider(self):
        self.assertEqual(models_from(MANAGER_PROVIDER="vertex").thesis_validator.provider, "vertex")


class GeminiClientTests(unittest.TestCase):
    def test_builds_an_api_key_client(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "key"}), patch.object(shared, "genai") as genai:
            get_model_client(provider="gemini")
        genai.Client.assert_called_once_with(vertexai=False, api_key="key")

    def test_vertex_environment_flag_does_not_redirect_the_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "key", "GOOGLE_GENAI_USE_VERTEXAI": "true"}):
            client = get_model_client(provider="gemini")
        self.assertFalse(client.raw_client.vertexai)

    def test_missing_key_raises(self):
        environ = {k: v for k, v in os.environ.items() if k not in {"GEMINI_API_KEY", "GOOGLE_API_KEY"}}
        with patch.dict(os.environ, environ, clear=True), self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
            get_model_client(provider="gemini")

    def test_blank_provider_uses_llm_provider(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "key"}), \
                patch.object(shared, "genai"):
            self.assertEqual(get_model_client(provider="").provider, "gemini")

    def test_stage_builds_its_client_with_its_own_provider(self):
        models = replace(get_settings().models, sector_opportunist=StageModel("m", "host", provider="gemini"))
        with use_settings(models=models), patch.object(sector_opportunist, "_sector_opportunist_client", None), \
                patch.object(sector_opportunist, "get_model_client") as factory:
            sector_opportunist._get_default_client()
        factory.assert_called_once_with("host", "gemini")


class GeminiRequestTests(unittest.TestCase):
    def test_returns_response_text(self):
        self.assertEqual(ask(gemini_client(SimpleNamespace(text='{"ok": true}'))), '{"ok": true}')

    def test_ollama_era_alias_resolves_to_the_default_gemini_model(self):
        client = gemini_client(SimpleNamespace(text="{}"))
        with patch.object(shared, "default_vertex_model", return_value="gemini-default"):
            ask(client, model="llama3.1")
        self.assertEqual(client.raw_client.models.generate_content.call_args.kwargs["model"], "gemini-default")

    def test_schema_goes_in_the_json_schema_field(self):
        client = gemini_client(SimpleNamespace(text="{}"))
        with use_settings(vertex=FAST_RETRIES):
            ask_llm_model(client, "gemini-3.1-flash-lite", "system", "user", response_schema=STRICT_SCHEMA)
        config = client.raw_client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual((config.response_json_schema, config.response_schema), (STRICT_SCHEMA, None))

    def test_vertex_keeps_the_response_schema_field(self):
        client = gemini_client(SimpleNamespace(text="{}"))
        client.provider = "vertex"
        with use_settings(vertex=FAST_RETRIES):
            ask_llm_model(client, "gemini-3.1-flash-lite", "system", "user", response_schema=STRICT_SCHEMA)
        config = client.raw_client.models.generate_content.call_args.kwargs["config"]
        self.assertIsNone(config.response_json_schema)

    def test_per_minute_quota_is_retried(self):
        client = gemini_client(RuntimeError(MINUTE_QUOTA_ERROR), SimpleNamespace(text="{}"))
        self.assertEqual(ask(client), "{}")

    def test_daily_quota_fails_without_retrying(self):
        client = gemini_client(RuntimeError(DAILY_QUOTA_ERROR), SimpleNamespace(text="{}"))
        with self.assertRaises(RuntimeError):
            ask(client)
        self.assertEqual(client.raw_client.models.generate_content.call_count, 1)

    def test_daily_quota_error_names_the_gemini_api_and_reset(self):
        with self.assertRaisesRegex(RuntimeError, "Gemini API daily quota exhausted.*midnight Pacific"):
            ask(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)))


if __name__ == "__main__":
    unittest.main()
