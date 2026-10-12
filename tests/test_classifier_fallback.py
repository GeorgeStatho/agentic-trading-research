"""News classification fallback: quota-exhausted calls move to a second provider and keep their real model."""
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_helpers.sector_opportunist as sector_helpers
from agent_helpers import shared
from agent_helpers.shared import Client, ModelAnswer, ModelQuotaExhausted, ask_classifier_model, get_model_client
from db_helpers import initialize_market_database, initialize_news_database
from market_db import add_sector
from services.settings import (ClassifierFallbackSettings, OpenAICompatibleSettings, Settings, VertexSettings,
                               use_settings)

DAILY_QUOTA_ERROR = "429 RESOURCE_EXHAUSTED. quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
FALLBACK = ClassifierFallbackSettings(provider="openai_compatible", model="mistral-small-latest")
FAST_RETRIES = VertexSettings(max_attempts=2, retry_initial_delay_seconds=0.5, retry_max_delay_seconds=0.5,
                              explicit_cache=False, cache_ttl_seconds=900)


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def gemini_client(*responses):
    raw_client = Mock()
    raw_client.models.generate_content.side_effect = list(responses)
    return Client(provider="gemini", raw_client=raw_client)


def chat_client(*responses):
    raw_client = Mock()
    raw_client.chat.completions.create.side_effect = list(responses)
    return Client(provider="openai_compatible", raw_client=raw_client, label="https://api.mistral.ai/v1")


def chat_completion(content="{}", model="mistral-small-2506"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(model=model, choices=[SimpleNamespace(message=message, finish_reason="stop")])


def classify(primary, fallback_client=None, fallback=FALLBACK):
    with use_settings(vertex=FAST_RETRIES, classifier_fallback=fallback), patch.object(shared.time, "sleep"), \
            patch.object(shared, "_get_classifier_fallback_client", return_value=fallback_client):
        return ask_classifier_model(primary, "gemini-3.1-flash-lite", "system", "user")


class FallbackSettingsTests(unittest.TestCase):
    def test_blank_provider_disables_the_fallback(self):
        self.assertEqual(Settings.from_environ({}).classifier_fallback.provider, "")

    def test_provider_without_a_model_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "CLASSIFIER_FALLBACK_MODEL"):
            Settings.from_environ({"CLASSIFIER_FALLBACK_PROVIDER": "openai_compatible"})


class ClassifierAnswerTests(unittest.TestCase):
    def test_primary_answer_names_the_primary_model(self):
        answer = classify(gemini_client(SimpleNamespace(text="{}", model_version="gemini-3.1-flash-lite-001")))
        self.assertEqual(answer, ModelAnswer(text="{}", provider="gemini", model="gemini-3.1-flash-lite",
                                             model_version="gemini-3.1-flash-lite-001"))

    def test_exhausted_quota_is_answered_by_the_fallback(self):
        answer = classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), chat_client(chat_completion('{"x": 1}')))
        self.assertEqual(answer, ModelAnswer(text='{"x": 1}', provider="openai_compatible",
                                             model="mistral-small-latest", model_version="mistral-small-2506",
                                             fallback=True))

    def test_exhausted_quota_without_a_fallback_raises(self):
        with self.assertRaises(ModelQuotaExhausted):
            classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), fallback=ClassifierFallbackSettings())

    def test_other_errors_do_not_fall_back(self):
        fallback_client = chat_client(chat_completion())
        with self.assertRaises(RuntimeError):
            classify(gemini_client(HttpError(500), HttpError(500)), fallback_client)
        fallback_client.raw_client.chat.completions.create.assert_not_called()

    def test_fallback_quota_error_is_raised(self):
        with self.assertRaises(ModelQuotaExhausted):
            classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), chat_client(HttpError(429)))


class OpenAICompatibleClientTests(unittest.TestCase):
    def test_client_points_at_the_configured_endpoint(self):
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "key"}), \
                use_settings(openai_compatible=OpenAICompatibleSettings(base_url="https://api.mistral.ai/v1")):
            client = get_model_client(provider="openai_compatible")
        self.assertEqual(str(client.raw_client.base_url), "https://api.mistral.ai/v1/")

    def test_missing_key_raises(self):
        environ = {k: v for k, v in os.environ.items() if k != "OPENAI_COMPATIBLE_API_KEY"}
        with patch.dict(os.environ, environ, clear=True), \
                use_settings(openai_compatible=OpenAICompatibleSettings(base_url="https://api.mistral.ai/v1")), \
                self.assertRaisesRegex(RuntimeError, "OPENAI_COMPATIBLE_API_KEY"):
            get_model_client(provider="openai_compatible")


class StoredModelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = str(Path(directory.name) / "news.db")
        initialize_market_database(db_path=self.db_path)
        initialize_news_database(db_path=self.db_path)
        self.sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("INSERT INTO news_articles (id, source, article_key, title, source_url) "
                         "VALUES (1, 'Wire', 'a1', 'Title', 'https://example.com/1')")

    def stored_processing_row(self, answer):
        with patch.object(sector_helpers, "DB_PATH", self.db_path), \
                patch.object(sector_helpers, "initialize_news_database"):
            sector_helpers.save_sector_opportunist_batch_results([{"article_id": 1}], [],
                                                                 sector_id=self.sector_id, answer=answer)
        with closing(sqlite3.connect(self.db_path)) as conn:
            model, raw_json = conn.execute(
                "SELECT model, raw_json FROM sector_opportunist_article_processing WHERE article_id = 1").fetchone()
        return model, json.loads(raw_json)

    def test_fallback_classification_stores_the_fallback_model(self):
        answer = ModelAnswer(text="{}", provider="openai_compatible", model="mistral-small-latest",
                             model_version="mistral-small-2506", fallback=True)
        model, raw_json = self.stored_processing_row(answer)
        self.assertEqual((model, raw_json["answered_by"]), ("mistral-small-latest", answer.record()))


if __name__ == "__main__":
    unittest.main()
