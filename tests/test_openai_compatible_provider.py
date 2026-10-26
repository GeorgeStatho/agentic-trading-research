"""Hosted OpenAI-compatible provider: request settings and error handling, with no network."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from agent_helpers.shared import Client, ask_llm_model
from services.settings import OpenAICompatibleSettings, use_settings

SCHEMA = {"type": "object", "properties": {"decision": {"type": "string"}}}


def completion(content='{"decision": "call"}', finish_reason="stop"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def client_for(provider, response):
    raw_client = Mock()
    raw_client.chat.completions.create.return_value = response
    return Client(provider=provider, raw_client=raw_client, label="https://fixture/v1")


def sent(provider="openai_compatible", environ=None):
    client = client_for(provider, completion())
    with use_settings(openai_compatible=OpenAICompatibleSettings.from_environ(environ or {})):
        ask_llm_model(client, "model", "system", "user", temperature=0, response_schema=SCHEMA)
    return client.raw_client.chat.completions.create.call_args.kwargs


class OpenAICompatibleRequestTests(unittest.TestCase):
    def test_defaults_replace_temperature_cap_tokens_and_set_reasoning(self):
        kwargs = sent()
        self.assertEqual(kwargs["temperature"], 1.0)
        self.assertEqual(kwargs["max_tokens"], 32000)
        self.assertEqual(kwargs["extra_body"], {"reasoning": {"effort": "high"}})

    def test_blank_settings_keep_stage_temperature_and_send_no_reasoning(self):
        kwargs = sent(environ={"OPENAI_COMPATIBLE_TEMPERATURE": "", "OPENAI_COMPATIBLE_REASONING_EFFORT": ""})
        self.assertEqual(kwargs["temperature"], 0)
        self.assertNotIn("extra_body", kwargs)

    def test_unknown_reasoning_effort_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "OPENAI_COMPATIBLE_REASONING_EFFORT"):
            OpenAICompatibleSettings.from_environ({"OPENAI_COMPATIBLE_REASONING_EFFORT": "extreme"})

    def test_llamacpp_requests_are_unchanged(self):
        kwargs = sent(provider="llamacpp")
        self.assertEqual(kwargs["temperature"], 0)
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("extra_body", kwargs)

    def test_reasoning_that_uses_the_whole_budget_reports_truncation(self):
        client = client_for("openai_compatible", completion(content=None, finish_reason="length"))
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            ask_llm_model(client, "model", "system", "user", response_schema=SCHEMA)


if __name__ == "__main__":
    unittest.main()
