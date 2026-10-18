"""llama.cpp provider: client wiring and Chat Completions requests, with no running server."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from agent_helpers.shared import Client, ask_llm_model

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


class LlamaCppRequestTests(unittest.TestCase):
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
