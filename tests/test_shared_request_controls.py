"""ask_llm_model's opt-in request controls and usage reporting, with no network."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent_helpers.llm_client import Client, _strict_compatible, ask_llm_model, provider_status_code
from services.core.settings import VertexSettings, use_settings

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["decision"],
          "properties": {"decision": {"type": "string"}}}
ANSWER = '{"decision": "call"}'


def chat_client(provider="openai_compatible", *, content=ANSWER, finish_reason="stop", usage=None):
    raw = Mock()
    response = SimpleNamespace(model="fixture-model", usage=usage,
                               choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                        finish_reason=finish_reason)])
    raw.chat.completions.create.return_value = response
    raw.with_options.return_value.chat.completions.create.return_value = response
    return Client(provider=provider, raw_client=raw, label="https://fixture/v1")


def chat_usage(prompt=100, completion=50, reasoning=20):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion,
                           completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning))


class ChatCompletionsControlTests(unittest.TestCase):
    def test_defaults_use_the_shared_client_unchanged(self):
        client = chat_client()
        ask_llm_model(client, "model", "system", "user", response_schema=SCHEMA)
        self.assertFalse(client.raw_client.with_options.called)
        self.assertEqual(client.raw_client.chat.completions.create.call_args.kwargs["max_tokens"], 32000)

    def test_single_timed_request_with_its_own_token_cap(self):
        client = chat_client()
        ask_llm_model(client, "model", "system", "user", max_output_tokens=4096, timeout=30, retry=False)
        client.raw_client.with_options.assert_called_once_with(max_retries=0, timeout=30)
        kwargs = client.raw_client.with_options.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 4096)

    def test_usage_is_recorded_even_when_the_answer_is_truncated(self):
        client = chat_client(content=None, finish_reason="length", usage=chat_usage())
        diagnostic = {}
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            ask_llm_model(client, "model", "system", "user", response_diagnostic=diagnostic)
        self.assertEqual({key: diagnostic[key] for key in ("prompt_token_count", "candidates_token_count",
                                                           "thoughts_token_count", "total_token_count", "finish_reason")},
                         {"prompt_token_count": 100, "candidates_token_count": 30, "thoughts_token_count": 20,
                          "total_token_count": 150, "finish_reason": "length"})


class OpenAIResponsesControlTests(unittest.TestCase):
    def client(self):
        raw = Mock()
        raw.with_options.return_value.responses.create.return_value = SimpleNamespace(
            output_text=ANSWER, model="gpt-fixture", status="completed", incomplete_details=None,
            usage=SimpleNamespace(input_tokens=10, output_tokens=7, total_tokens=17,
                                  output_tokens_details=SimpleNamespace(reasoning_tokens=3)))
        return Client(provider="openai", raw_client=raw)

    def test_request_controls_and_usage(self):
        client, diagnostic = self.client(), {}
        ask_llm_model(client, "gpt-fixture", "system", "user", response_schema=SCHEMA, response_diagnostic=diagnostic,
                      max_output_tokens=900, timeout=5, retry=False)
        kwargs = client.raw_client.with_options.return_value.responses.create.call_args.kwargs
        self.assertEqual((kwargs["max_output_tokens"], kwargs["text"]["format"]["strict"]), (900, True))
        self.assertEqual((diagnostic["candidates_token_count"], diagnostic["thoughts_token_count"],
                          diagnostic["finish_reason"]), (4, 3, "completed"))

    def test_optional_properties_are_sent_without_strict_mode(self):
        self.assertFalse(_strict_compatible({**SCHEMA, "properties": {**SCHEMA["properties"], "note": {"type": "string"}}}))
        self.assertFalse(_strict_compatible({"type": "array", "items": {"type": "object", "properties": {"a": {}}}}))
        self.assertTrue(_strict_compatible({"type": "array", "items": SCHEMA}))


class OllamaControlTests(unittest.TestCase):
    def test_timed_call_uses_its_own_client_and_reports_usage(self):
        response = {"model": "llama3.1", "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 5,
                    "message": {"content": ANSWER}}
        shared_client = Client(provider="ollama", raw_client=Mock(), host="http://ollama:11434")
        diagnostic = {}
        with patch("agent_helpers.llm_client.OllamaClient") as built:
            built.return_value.chat.return_value = response
            ask_llm_model(shared_client, "llama3.1", "system", "user", response_diagnostic=diagnostic,
                          max_output_tokens=256, timeout=9)
        built.assert_called_once_with(host="http://ollama:11434", timeout=9)
        self.assertEqual(built.return_value.chat.call_args.kwargs["options"]["num_predict"], 256)
        self.assertFalse(shared_client.raw_client.chat.called)
        self.assertEqual((diagnostic["prompt_token_count"], diagnostic["candidates_token_count"],
                          diagnostic["thoughts_token_count"]), (12, 5, 0))


class VertexControlTests(unittest.TestCase):
    def setUp(self):
        try:
            from google.genai import types  # noqa: F401
        except ImportError:
            self.skipTest("Optional google-genai dependency is not installed")
        self.raw = Mock()
        self.raw.models.generate_content.return_value = SimpleNamespace(
            text=ANSWER, candidates=[SimpleNamespace(finish_reason="STOP", finish_message=None)],
            usage_metadata=None, model_version="fixture")
        self.raw.caches.create.return_value = SimpleNamespace(name="cachedContents/fixture")
        self.client = Client(provider="vertex", raw_client=self.raw)
        self.settings = VertexSettings.from_environ({"VERTEX_EXPLICIT_CACHE": "true"})

    def config(self, **controls):
        with use_settings(vertex=self.settings):
            ask_llm_model(self.client, "gemini-2.5-flash", "system", "user", **controls)
        return self.raw.models.generate_content.call_args.kwargs["config"]

    def test_defaults_keep_cache_and_client_retries(self):
        config = self.config()
        self.assertIsNone(config.http_options)
        self.assertEqual(config.cached_content, "cachedContents/fixture")

    def test_single_attempt_disables_sdk_retries_and_cache_creation(self):
        config = self.config(timeout=2.5, retry=False, max_output_tokens=512, thinking_budget=64)
        self.assertEqual((config.http_options.timeout, config.http_options.retry_options.attempts), (2500, 1))
        self.assertEqual((config.max_output_tokens, config.thinking_config.thinking_budget), (512, 64))
        self.assertFalse(self.raw.caches.create.called)
        self.assertEqual(config.system_instruction, "system")


class ProviderStatusCodeTests(unittest.TestCase):
    def test_status_comes_from_the_wrapped_provider_error(self):
        cause = Exception("fixture")
        cause.code = 503
        try:
            raise RuntimeError("wrapped") from cause
        except RuntimeError as exc:
            self.assertEqual(provider_status_code(exc), 503)
        self.assertIsNone(provider_status_code(RuntimeError("no status")))


if __name__ == "__main__":
    unittest.main()
