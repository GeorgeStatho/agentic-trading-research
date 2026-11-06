"""Gemini API (AI Studio key) provider and per-stage provider overrides, with no network."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import agent_stages.sector_opportunist as sector_opportunist
from agent_helpers import llm_vertex
from agent_helpers.llm_client import Client, ask_llm_model
from services.core.settings import Settings, StageModel, VertexSettings, get_settings, use_settings

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
    with use_settings(vertex=FAST_RETRIES), patch.object(llm_vertex.time, "sleep"):
        return ask_llm_model(client, model, "system", "user")


class GeminiClientTests(unittest.TestCase):
    def test_stage_builds_its_client_with_its_own_provider(self):
        models = replace(get_settings().models, sector_opportunist=StageModel("m", "host", provider="gemini"))
        with use_settings(models=models), patch.object(sector_opportunist, "_sector_opportunist_client", None), \
                patch.object(sector_opportunist, "get_model_client") as factory:
            sector_opportunist._get_default_client()
        factory.assert_called_once_with("host", "gemini")


class GeminiRequestTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
