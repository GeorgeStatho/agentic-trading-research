"""llama.cpp provider: client wiring and Chat Completions requests, with no running server."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent_helpers.shared import Client, ask_llm_model, get_model_client
from services.settings import DEFAULT_LLAMACPP_BASE_URL, LlamaCppSettings, use_settings

SCHEMA = {"type": "object", "properties": {"decision": {"type": "string"}}}


def completion(content='{"decision": "call"}', finish_reason="stop"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def llamacpp_client(response):
    raw_client = Mock()
    raw_client.chat.completions.create.return_value = response
    return Client(provider="llamacpp", raw_client=raw_client, label="http://fixture:8080/v1")


def ask(client):
    return ask_llm_model(client, "local", "system", "user", response_schema=SCHEMA)


class LlamaCppSettingsTests(unittest.TestCase):
    def test_blank_base_url_uses_default(self):
        self.assertEqual(LlamaCppSettings.from_environ({"LLAMACPP_BASE_URL": " "}).base_url,
                         DEFAULT_LLAMACPP_BASE_URL)

    def test_client_points_at_configured_server(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "llamacpp"}), \
                use_settings(llamacpp=LlamaCppSettings(base_url="http://gpu-box:9000/v1")):
            client = get_model_client()
        self.assertEqual(str(client.raw_client.base_url), "http://gpu-box:9000/v1/")


class LlamaCppRequestTests(unittest.TestCase):
    def test_returns_message_content(self):
        self.assertEqual(ask(llamacpp_client(completion())), '{"decision": "call"}')

    def test_sends_schema_as_response_format(self):
        client = llamacpp_client(completion())
        ask(client)
        sent = client.raw_client.chat.completions.create.call_args.kwargs["response_format"]
        self.assertEqual(sent["json_schema"]["schema"], SCHEMA)

    def test_truncated_response_raises(self):
        with self.assertRaisesRegex(RuntimeError, "context size"):
            ask(llamacpp_client(completion(finish_reason="length")))


if __name__ == "__main__":
    unittest.main()
