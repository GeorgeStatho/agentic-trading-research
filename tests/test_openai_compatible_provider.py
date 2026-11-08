"""Hosted OpenAI-compatible provider: request settings, usage records and error handling, with no network."""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent_helpers import llm_usage
from agent_helpers.llm_client import Client, ask_llm_model
from db_common import get_connection
from market_db_modules.llm_usage_store import record_llm_call_usage
from services.core.settings import OpenAICompatibleSettings, use_settings
from tests import ROOT_DIR
from tests.db_fixtures import temporary_database

SCHEMA = {"type": "object", "properties": {"decision": {"type": "string"}}}
OPENROUTER = {"OPENAI_COMPATIBLE_BASE_URL": "https://openrouter.ai/api/v1"}


def completion(content='{"decision": "call"}', finish_reason="stop", **fields):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)], **fields)


def openrouter_completion():
    usage = SimpleNamespace(prompt_tokens=70000, completion_tokens=9000, total_tokens=79000, cost=0.0136,
                            completion_tokens_details=SimpleNamespace(reasoning_tokens=6000),
                            prompt_tokens_details=SimpleNamespace(cached_tokens=1200))
    return completion(usage=usage, provider="Wafer", model="deepseek/deepseek-v4.1-flash-1017", id="gen-1")


def client_for(provider, response):
    raw_client = Mock()
    raw_client.chat.completions.create.return_value = response
    return Client(provider=provider, raw_client=raw_client, label="https://fixture/v1")


def sent(provider="openai_compatible", environ=None):
    client = client_for(provider, completion())
    with use_settings(openai_compatible=OpenAICompatibleSettings.from_environ({**OPENROUTER, **(environ or {})})):
        ask_llm_model(client, "model", "system", "user", temperature=0, response_schema=SCHEMA)
    return client.raw_client.chat.completions.create.call_args.kwargs


class OpenAICompatibleRequestTests(unittest.TestCase):
    def test_defaults_replace_temperature_cap_tokens_and_set_reasoning(self):
        kwargs = sent()
        self.assertEqual(kwargs["temperature"], 1.0)
        self.assertEqual(kwargs["max_tokens"], 32000)
        self.assertEqual(kwargs["extra_body"], {"usage": {"include": True}, "reasoning": {"effort": "high"},
                                                "provider": {"require_parameters": True}})

    def test_blank_settings_keep_stage_temperature_and_send_no_reasoning(self):
        kwargs = sent(environ={"OPENAI_COMPATIBLE_TEMPERATURE": "", "OPENAI_COMPATIBLE_REASONING_EFFORT": ""})
        self.assertEqual(kwargs["temperature"], 0)
        self.assertNotIn("reasoning", kwargs["extra_body"])

    def test_provider_order_is_sent_with_fallbacks(self):
        kwargs = sent(environ={"OPENAI_COMPATIBLE_PROVIDER_ORDER": "Wafer, together"})
        self.assertEqual(kwargs["extra_body"]["provider"],
                         {"order": ["wafer", "together"], "allow_fallbacks": True, "require_parameters": True})

    def test_provider_list_can_be_closed_and_price_capped(self):
        kwargs = sent(environ={"OPENAI_COMPATIBLE_PROVIDER_ORDER": "wafer,relace",
                               "OPENAI_COMPATIBLE_PROVIDER_FALLBACKS": "false",
                               "OPENAI_COMPATIBLE_MAX_PRICE": "0.15, 0.60"})
        self.assertEqual(kwargs["extra_body"]["provider"],
                         {"order": ["wafer", "relace"], "allow_fallbacks": False,
                          "max_price": {"prompt": 0.15, "completion": 0.6}, "require_parameters": True})

    def test_unstructured_calls_do_not_require_parameters(self):
        client = client_for("openai_compatible", completion())
        with use_settings(openai_compatible=OpenAICompatibleSettings.from_environ(OPENROUTER)):
            ask_llm_model(client, "model", "system", "user", temperature=0)
        self.assertNotIn("provider", client.raw_client.chat.completions.create.call_args.kwargs["extra_body"])

    def test_malformed_max_price_is_rejected(self):
        for raw in ("0.15", "cheap,0.6", "-1,0.6"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "OPENAI_COMPATIBLE_MAX_PRICE"):
                OpenAICompatibleSettings.from_environ({"OPENAI_COMPATIBLE_MAX_PRICE": raw})

    def test_other_hosted_apis_get_no_openrouter_fields(self):
        kwargs = sent(environ={"OPENAI_COMPATIBLE_BASE_URL": "https://api.mistral.ai/v1",
                               "OPENAI_COMPATIBLE_PROVIDER_ORDER": "wafer"})
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


class UsageRecordTests(unittest.TestCase):
    def test_records_the_upstream_provider_tokens_and_cost(self):
        client = client_for("openai_compatible", openrouter_completion())
        with patch.object(llm_usage, "record_llm_call_usage") as record:
            ask_llm_model(client, "deepseek/deepseek-v4.1-flash-1017", "system", "user", response_schema=SCHEMA)
        self.assertEqual(record.call_args.args[0], {
            "model": "deepseek/deepseek-v4.1-flash-1017", "served_model": "deepseek/deepseek-v4.1-flash-1017",
            "upstream_provider": "Wafer", "generation_id": "gen-1", "prompt_tokens": 70000,
            "completion_tokens": 9000, "reasoning_tokens": 6000, "cached_tokens": 1200, "cost_usd": 0.0136,
            "stage": None})

    def test_records_the_stage_that_asked(self):
        client = replace(client_for("openai_compatible", openrouter_completion()), stage="manager")
        with patch.object(llm_usage, "record_llm_call_usage") as record:
            ask_llm_model(client, "model", "system", "user", response_schema=SCHEMA)
        self.assertEqual(record.call_args.args[0]["stage"], "manager")

    def test_a_failed_record_does_not_fail_the_call(self):
        client = client_for("openai_compatible", openrouter_completion())
        with patch.object(llm_usage, "record_llm_call_usage", side_effect=OSError("disk full")):
            answer = ask_llm_model(client, "model", "system", "user", response_schema=SCHEMA)
        self.assertEqual(answer, '{"decision": "call"}')

    def test_usage_is_stored(self):
        db = temporary_database(self)
        record_llm_call_usage({"model": "m", "upstream_provider": "Wafer", "prompt_tokens": 10, "cost_usd": 0.5,
                               "stage": "strategist"}, db_path=db)
        with closing(get_connection(db)) as conn:
            row = conn.execute("SELECT model, upstream_provider, prompt_tokens, cost_usd, stage FROM llm_call_usage").fetchone()
        self.assertEqual(tuple(row), ("m", "Wafer", 10, 0.5, "strategist"))

    def test_every_model_client_is_built_with_its_stage(self):
        # A client without a stage records its spend as "unknown" on System › Health.
        import ast

        missing = []
        for path in (ROOT_DIR / "Python Scripts").rglob("*.py"):
            if path.name == "llm_client.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "get_model_client"
                        and not any(keyword.arg == "stage" for keyword in node.keywords)):
                    missing.append(f"{path.relative_to(ROOT_DIR)}:{node.lineno}")
        self.assertEqual(missing, [])

    def test_an_older_usage_table_gains_the_stage_column_and_keeps_its_rows(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from market_db_modules.bootstrap import initialize_database

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "old.db"
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("""CREATE TABLE llm_call_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, called_at TEXT NOT NULL,
                    model TEXT NOT NULL, served_model TEXT, upstream_provider TEXT, generation_id TEXT,
                    prompt_tokens INTEGER, completion_tokens INTEGER, reasoning_tokens INTEGER,
                    cached_tokens INTEGER, cost_usd REAL)""")
                conn.execute("INSERT INTO llm_call_usage (called_at, model, cost_usd) VALUES ('2026-11-01', 'm', 0.25)")
            initialize_database(db_path=db)
            with closing(get_connection(db)) as conn:
                row = conn.execute("SELECT model, cost_usd, stage FROM llm_call_usage").fetchone()
        self.assertEqual(tuple(row), ("m", 0.25, None))


if __name__ == "__main__":
    unittest.main()
