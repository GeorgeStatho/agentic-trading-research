"""News classification fallback: quota-exhausted calls move to a second provider and keep their real model."""
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_helpers.sector_opportunist as sector_helpers
from agent_helpers import llm_client
from agent_helpers.llm_client import Client, ModelAnswer, ask_classifier_model
from db_common import use_database
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from market_db_modules.entities import add_sector
from services.settings import (
    ClassifierFallbackSettings,
    FallbackModel,
    VertexSettings,
    use_settings,
)

DAILY_QUOTA_ERROR = "429 RESOURCE_EXHAUSTED. quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
MISTRAL = FallbackModel(provider="openai_compatible", model="mistral-small-latest")
SECOND_GEMINI = FallbackModel(provider="gemini", model="gemini-3.5-flash-lite")
FALLBACK = ClassifierFallbackSettings(chain=(MISTRAL,))
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


def classify(primary, *fallback_clients, fallback=FALLBACK):
    with use_settings(vertex=FAST_RETRIES, classifier_fallback=fallback), patch.object(llm_client.time, "sleep"), \
            patch.object(llm_client, "_get_classifier_fallback_client", side_effect=list(fallback_clients)):
        return ask_classifier_model(primary, "gemini-3.1-flash-lite", "system", "user")


class ClassifierAnswerTests(unittest.TestCase):
    def test_exhausted_quota_is_answered_by_the_fallback(self):
        answer = classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), chat_client(chat_completion('{"x": 1}')))
        self.assertEqual(answer, ModelAnswer(text='{"x": 1}', provider="openai_compatible",
                                             model="mistral-small-latest", model_version="mistral-small-2506",
                                             fallback=True))

    def test_other_errors_do_not_fall_back(self):
        fallback_client = chat_client(chat_completion())
        with self.assertRaises(RuntimeError):
            classify(gemini_client(HttpError(500), HttpError(500)), fallback_client)
        fallback_client.raw_client.chat.completions.create.assert_not_called()

    def test_fallbacks_are_tried_in_order(self):
        answer = classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), gemini_client(RuntimeError(DAILY_QUOTA_ERROR)),
                          chat_client(chat_completion()), fallback=ClassifierFallbackSettings(chain=(SECOND_GEMINI, MISTRAL)))
        self.assertEqual(answer.model, "mistral-small-latest")


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
        with use_database(self.db_path), \
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


class GoogleCodedError(Exception):
    """The google-genai SDK reports its HTTP status as `code`, not `status_code`."""

    def __init__(self, code):
        super().__init__(f"{code} INTERNAL")
        self.code = code


OTHER_HOSTED = FallbackModel(provider="openai_compatible", model="google/gemma-4-31b-it:free")


class UnavailableFallbackTests(unittest.TestCase):
    """A fallback that is retired or failing server-side is skipped; the chain moves on to the next one."""

    def test_a_retired_fallback_model_is_skipped(self):
        answer = classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), gemini_client(HttpError(404)),
                          chat_client(chat_completion()), fallback=ClassifierFallbackSettings(chain=(SECOND_GEMINI, MISTRAL)))
        self.assertEqual(answer.model, "mistral-small-latest")

    def test_a_fallback_server_error_is_skipped(self):
        answer = classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)),
                          gemini_client(GoogleCodedError(500), GoogleCodedError(500)),
                          chat_client(chat_completion()), fallback=ClassifierFallbackSettings(chain=(SECOND_GEMINI, MISTRAL)))
        self.assertEqual(answer.model, "mistral-small-latest")

    def test_a_hosted_fallback_server_error_is_skipped(self):
        answer = classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), chat_client(HttpError(502)),
                          chat_client(chat_completion()), fallback=ClassifierFallbackSettings(chain=(OTHER_HOSTED, MISTRAL)))
        self.assertEqual(answer.model, "mistral-small-latest")

    def test_a_bad_request_on_a_fallback_still_fails(self):
        # A malformed request would fail on every model; walking the chain would only hide it.
        last = chat_client(chat_completion())
        with self.assertRaises(RuntimeError):
            classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), chat_client(HttpError(400)), last,
                     fallback=ClassifierFallbackSettings(chain=(OTHER_HOSTED, MISTRAL)))
        last.raw_client.chat.completions.create.assert_not_called()

    def test_when_no_fallback_can_answer_the_call_fails(self):
        with self.assertRaises(RuntimeError):
            classify(gemini_client(RuntimeError(DAILY_QUOTA_ERROR)), gemini_client(HttpError(404)),
                     chat_client(HttpError(500)), fallback=ClassifierFallbackSettings(chain=(SECOND_GEMINI, MISTRAL)))

    def test_a_primary_server_error_still_does_not_fall_back(self):
        fallback_client = chat_client(chat_completion())
        with self.assertRaises(RuntimeError):
            classify(gemini_client(GoogleCodedError(500), GoogleCodedError(500)), fallback_client)
        fallback_client.raw_client.chat.completions.create.assert_not_called()


def gemini_response(text):
    return SimpleNamespace(text=text, candidates=None, model_version="gemini-3.1-flash-lite-001")


class ServerErrorRetryTests(unittest.TestCase):
    """Google treats 500 INTERNAL as transient, so it is retried like an overload before anything gives up."""

    def test_a_one_off_server_error_is_retried(self):
        primary = gemini_client(GoogleCodedError(500), gemini_response('{"x": 1}'))
        answer = classify(primary)
        self.assertEqual((answer.text, answer.fallback), ('{"x": 1}', False))
        self.assertEqual(primary.raw_client.models.generate_content.call_count, 2)

    def test_a_server_error_on_every_attempt_is_reported_as_unavailable(self):
        with self.assertRaises(llm_client.ModelUnavailable):
            classify(gemini_client(GoogleCodedError(500), GoogleCodedError(500)),
                     fallback=ClassifierFallbackSettings(chain=()))


if __name__ == "__main__":
    unittest.main()
