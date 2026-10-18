"""Strategist answer reuse: unchanged evidence and timing context reuse the last model answer."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from unittest.mock import Mock, patch

from agent_stages import strategist_runner
from market_db_modules.bootstrap import initialize_database
from market_db_modules.entities import add_company, add_industry, add_sector
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from news_db_modules.strategist_runs import latest_answered_strategist_run, record_strategist_run
from services.config import AgentPipelineSettings
from services.evidence_thesis_policy import EvidenceThesisPolicy

DATA_DIR = Path(__file__).resolve().parents[1] / "Data"
MODEL = "test-model"
RECOMMENDATION = {"decision": "watchlist", "confidence": "medium", "summary": "Earlier answer."}


def strategist_payload(company_id):
    return {
        "company": {"company_id": company_id, "symbol": "MSFT", "market_data": {"last_price": 400.0},
                    "historical_price_data": {}},
        "views": {"company_view": {"count": 1, "agent_conclusions": [{"article_id": 7, "reason": "Guidance raised"}]}},
        "supporting_articles": {"article_summaries": [{"article_id": 7, "title": "Guidance raised"}], "full_articles": []},
        "opportunist_rollup": {"company": {"direction": "up"}},
        "company_evidence": {"facts": []},
        "grounding": {"article_ids": [7]},
        "market_context": {"market_regime": {"state": "range"}},
    }


class StrategistReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "reuse.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        initialize_news_database(db_path=self.db_path, schema_path=DATA_DIR / "news_schema.sql")
        sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        self.company_id = add_company(add_industry(sector_id, "software", "Software", db_path=self.db_path),
                                      "MSFT", "Microsoft", db_path=self.db_path)
        self.prompt_version = strategist_runner.strategist_prompt_version(EvidenceThesisPolicy.from_env())
        self.settings = replace(AgentPipelineSettings.from_env(), strategist_reuse_max_age_minutes=120,
                                strategist_reuse_max_price_move_pct=2.0)
        patcher = patch.object(strategist_runner, "latest_answered_strategist_run",
                               partial(latest_answered_strategist_run, db_path=self.db_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, payload, *, minutes_ago=30, model=MODEL, prompt_version=None, reused_from=None):
        output = {"raw_response": "{}", "recommendation": RECOMMENDATION}
        if reused_from is not None:
            output["reused_from_run_id"] = reused_from
        return record_strategist_run(
            company_id=self.company_id, model=model, prompt_version=prompt_version or self.prompt_version,
            input_article_ids=[7], input_payload=payload, output_payload=output, status="completed",
            created_at=(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(),
            db_path=self.db_path)

    def reusable(self, payload, settings=None):
        return strategist_runner._reusable_strategist_answer(
            payload, model=MODEL, prompt_version=self.prompt_version, settings=settings or self.settings)

    def test_lookup_skips_runs_that_reused_an_answer(self):
        answered = self.record(strategist_payload(self.company_id), minutes_ago=60)
        self.record(strategist_payload(self.company_id), minutes_ago=5, reused_from=answered)
        self.assertEqual(latest_answered_strategist_run(self.company_id, db_path=self.db_path)["id"], answered)

    def test_unchanged_evidence_reuses_and_any_change_asks_again(self):
        payload = strategist_payload(self.company_id)
        answered = self.record(payload)
        anchor, age = self.reusable(copy.deepcopy(payload))
        self.assertEqual(anchor["id"], answered)
        self.assertAlmostEqual(age, 30, delta=1)

        def changed(edit):
            current = copy.deepcopy(payload)
            edit(current)
            return current

        misses = {
            "evidence": changed(lambda p: p["views"]["company_view"]["agent_conclusions"][0].update(reason="Cut")),
            "article set": changed(lambda p: p["grounding"].update(article_ids=[7, 8])),
            "regime": changed(lambda p: p["market_context"]["market_regime"].update(state="trend_down")),
            "price move": changed(lambda p: p["company"]["market_data"].update(last_price=408.0)),
            "no price": changed(lambda p: p["company"].pop("market_data")),
        }
        for name, current in misses.items():
            with self.subTest(name):
                self.assertIsNone(self.reusable(current))
        with self.subTest("disabled"):
            self.assertIsNone(self.reusable(payload, replace(self.settings, strategist_reuse_max_age_minutes=0)))

    def test_old_or_differently_produced_answers_are_not_reused(self):
        payload = strategist_payload(self.company_id)
        for name, kwargs in {"too old": {"minutes_ago": 121}, "other model": {"model": "other-model"},
                             "other prompt": {"prompt_version": "grounded-strategist-v0"}}.items():
            with self.subTest(name):
                self.record(payload, **kwargs)
                self.assertIsNone(self.reusable(payload))

    def test_live_hit_skips_the_model_and_records_the_reuse(self):
        payload = strategist_payload(self.company_id)
        answered = self.record(payload)
        saved = {}
        ask = Mock(side_effect=AssertionError("model asked"))
        with patch.dict(os.environ, {"STRATEGIST_REUSE_MAX_AGE_MINUTES": "120"}), \
                patch.object(strategist_runner, "build_strategist_input", return_value=copy.deepcopy(payload)), \
                patch.object(strategist_runner, "ask_model", ask), \
                patch.object(strategist_runner, "_save_strategist_summary",
                             side_effect=lambda **kwargs: saved.update(kwargs) or {"id": 2}):
            result = strategist_runner.decide_company_purchase("MSFT", client=object(), model=MODEL)
        ask.assert_not_called()
        self.assertEqual(result["recommendation"], RECOMMENDATION)
        self.assertEqual(saved["reused_from_run_id"], answered)
        self.assertIsNone(saved["raw_response"])
        self.assertEqual(result["response_diagnostic"]["reused_from_run_id"], answered)

    def test_replay_always_asks_the_model(self):
        payload = strategist_payload(self.company_id)
        self.record(payload)
        with patch.dict(os.environ, {"STRATEGIST_REUSE_MAX_AGE_MINUTES": "120"}), \
                patch.object(strategist_runner, "ask_model", side_effect=RuntimeError("model asked")), \
                self.assertRaisesRegex(RuntimeError, "model asked"):
            strategist_runner.replay_strategist_decision_from_payload(payload, client=object(), model=MODEL)


if __name__ == "__main__":
    unittest.main()
