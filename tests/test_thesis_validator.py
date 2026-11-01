from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"

from market_db_modules.thesis_validator import (  # noqa: E402
    close_position_thesis,
    count_thesis_validator_exits_for_date,
    get_open_position_thesis_for_option_symbol,
    get_thesis_validation_run_for_date,
    record_position_thesis,
    record_thesis_validation_run,
    summarize_thesis_validator_health,
)
from market_db_modules.bootstrap import initialize_database  # noqa: E402
from services.thesis.thesis_validator_adjudicator import (  # noqa: E402
    adjudicate_thesis,
    apply_consistency_gate,
    ground_sample,
)
from services.thesis.thesis_validator_arbiter import decide_thesis_arbiter_action  # noqa: E402
from services.thesis.thesis_capture import build_position_thesis_record  # noqa: E402
from services.thesis.thesis_evidence_pack import build_thesis_evidence_pack  # noqa: E402
from services.thesis.thesis_validator_service import (  # noqa: E402
    ThesisValidatorService,
    ThesisValidatorSettings,
)


def _sample_execution(*, expiration_days_out: int = 35) -> dict:
    expiration_date = (date.today() + timedelta(days=expiration_days_out)).isoformat()
    return {
        "company_id": 7,
        "symbol": "NVDA",
        "name": "NVIDIA",
        "decision": "call",
        "confidence": "high",
        "reason": "Strong momentum after the earnings beat.",
        "selected_option_symbol": "NVDA260619C00190000",
        "selected_expiration_date": expiration_date,
        "manager_decision_history_id": 42,
        "order": {"id": "order-9", "submitted_at": "2026-08-20T14:00:00+00:00"},
        "strategist_recommendation": {
            "catalyst": ["Q2 earnings beat with raised full-year guidance"],
            "thesis": ["Data center demand is accelerating."],
            "risks": ["Export restrictions could cap China revenue."],
            "why_now": "Earnings surprise not yet fully priced in.",
            "summary": "Beat and raise quarter.",
            "time_horizon": "medium_term",
            "expected_stock_direction": "up",
            "evidence_claims": [
                {"claim_type": "catalyst", "claim": "x", "evidence_article_ids": [11, 12]},
                {"claim_type": "thesis", "claim": "y", "evidence_article_ids": [12, 13]},
            ],
        },
    }


class ThesisCaptureTests(unittest.TestCase):
    def test_builds_structured_record_from_execution(self) -> None:
        record = build_position_thesis_record(_sample_execution())
        self.assertIsNotNone(record)
        self.assertEqual(record["symbol"], "NVDA")
        self.assertEqual(record["option_symbol"], "NVDA260619C00190000")
        self.assertEqual(record["structure"], "long_call")
        self.assertEqual(record["catalyst_type"], "earnings_surprise")
        self.assertEqual(record["source_article_ids"], [11, 12, 13])
        self.assertEqual(record["opened_at"], "2026-08-20T14:00:00+00:00")
        self.assertEqual(record["manager_decision_history_id"], 42)
        self.assertEqual(record["trade_execution_order_id"], "order-9")
        # 35 DTE resolves to the 30-45 bucket, whose live exit is forced at 18 DTE: 35 - 18.
        self.assertEqual(record["expected_horizon_days"], 17)
        conditions = record["invalidation_conditions"]
        self.assertTrue(1 <= len(conditions) <= 3)
        self.assertTrue(all(condition.startswith("This thesis is wrong if") for condition in conditions))


class EvidencePackTests(unittest.TestCase):
    def test_pack_shape_and_anchor_exclusions(self) -> None:
        thesis = {
            "symbol": "NVDA",
            "company_id": 7,
            "structure": "long_call",
            "opened_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
            "catalyst_type": "earnings_surprise",
            "catalyst_summary": "Beat and raise.",
            "expected_horizon_days": 27,
            "invalidation_conditions": ["This thesis is wrong if the beat is reversed."],
        }
        position_summary = {
            "symbol": "NVDA260619C00190000",
            "underlying_stock_price": 200.0,
            "strike": 190.0,
            "contract_type": "call",
            "days_to_expiration": 30,
            "option_spread_pct": 0.05,
            "entry_price": 2.5,
            "unrealized_pl_pct": 40.0,
            "quantity": 2,
        }
        with (
            patch(
                "services.thesis.thesis_evidence_pack._symbol_move_since",
                side_effect=lambda symbol, *, since: {
                    "symbol": symbol,
                    "move_since_entry_pct": {"NVDA": 6.0, "SPY": 1.0, "XLK": 2.0}.get(symbol),
                    "available": True,
                    "error": "",
                },
            ),
            patch("services.thesis.thesis_evidence_pack._resolve_sector_etf_symbol", return_value="XLK"),
            patch(
                "services.thesis.thesis_evidence_pack._news_since_entry",
                return_value=[
                    {"article_id": 21, "source": "cnbc", "published_at": "", "title": "t", "summary": "s"}
                ],
            ),
        ):
            pack = build_thesis_evidence_pack(thesis, position_summary)

        self.assertEqual(pack["price_path"]["idiosyncratic_move_pct"], 4.0)
        self.assertEqual(pack["allowed_evidence_article_ids"], [21])
        self.assertEqual(pack["position_context"]["moneyness"]["description"], "in_the_money")
        serialized = str(pack)
        for excluded in ("entry_price", "unrealized", "quantity", "buying_power"):
            self.assertNotIn(excluded, serialized)


class GroundingGuardTests(unittest.TestCase):
    def test_out_of_pack_citation_forces_unclear(self) -> None:
        sample = {
            "thesis_state": "invalidated",
            "would_open_today": "no",
            "evidence_ids": [99],
            "rationale": "hallucinated",
            "confidence": 0.9,
        }
        grounded = ground_sample(sample, allowed_article_ids={1, 2})
        self.assertEqual(grounded["thesis_state"], "unclear")
        self.assertFalse(grounded["grounded"])
        self.assertEqual(grounded["grounding_failure"], "cited_article_outside_pack")


class ConsistencyGateTests(unittest.TestCase):
    @staticmethod
    def _sample(state: str, *, would_open: str = "no", evidence_ids: list[int] | None = None) -> dict:
        return {
            "thesis_state": state,
            "would_open_today": would_open,
            "evidence_ids": evidence_ids or [1],
            "rationale": "r",
            "confidence": 0.8,
            "grounded": True,
        }

    def test_majority_downgrades_one_severity_level(self) -> None:
        verdict = apply_consistency_gate(
            [self._sample("invalidated"), self._sample("invalidated"), self._sample("intact")]
        )
        self.assertEqual(verdict["thesis_state"], "degraded")
        self.assertEqual(verdict["consistency"], "majority")
        self.assertTrue(verdict["flip_detected"])


class AdjudicatorTests(unittest.TestCase):
    def test_adjudicate_thesis_grounds_and_gates_samples(self) -> None:
        pack = {
            "allowed_evidence_article_ids": [5],
            "news_since_entry": [
                {"article_id": 5, "source": "cnbc", "published_at": "", "title": "t", "summary": "s"}
            ],
            "thesis": {},
        }
        responses = iter(
            [
                '{"thesis_state": "invalidated", "would_open_today": "no", "evidence_ids": [5], "rationale": "a", "confidence": 0.9}',
                '{"thesis_state": "invalidated", "would_open_today": "no", "evidence_ids": [5], "rationale": "b", "confidence": 0.8}',
                '{"thesis_state": "invalidated", "would_open_today": "no", "evidence_ids": [99], "rationale": "c", "confidence": 0.9}',
            ]
        )
        with patch("agent_helpers.llm_client.ask_llm_model", side_effect=lambda *args, **kwargs: next(responses)):
            verdict = adjudicate_thesis(pack, client=object(), samples=3)

        # Third sample cited outside the pack, so it was forced to unclear and
        # the 2/3 invalidated majority downgrades to degraded.
        self.assertEqual(verdict["thesis_state"], "degraded")
        self.assertEqual(verdict["consistency"], "majority")
        self.assertFalse(verdict["grounded"])
        self.assertEqual(verdict["sample_count"], 3)

    def test_all_call_failures_produce_error_verdict(self) -> None:
        with patch("agent_helpers.llm_client.ask_llm_model", side_effect=RuntimeError("model down")):
            verdict = adjudicate_thesis({"allowed_evidence_article_ids": []}, client=object(), samples=2)
        self.assertEqual(verdict["consistency"], "error")
        self.assertEqual(verdict["thesis_state"], "unclear")


class ArbiterTests(unittest.TestCase):
    @staticmethod
    def _decide(**overrides) -> dict:
        defaults = dict(
            rules_exit_active=False,
            verdict={"thesis_state": "invalidated", "would_open_today": "no", "consistency": "unanimous"},
            quantity=2,
            llm_exits_today=0,
            max_daily_llm_exits=2,
            kill_switch=False,
        )
        defaults.update(overrides)
        return decide_thesis_arbiter_action(**defaults)

    def test_rules_exit_takes_precedence(self) -> None:
        result = self._decide(rules_exit_active=True)
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["reason"], "rules_exit_precedence")

    def test_invalidated_would_not_open_closes(self) -> None:
        self.assertEqual(self._decide()["action"], "close")

    def test_invalidated_would_still_open_closes_half_with_two_contracts(self) -> None:
        result = self._decide(
            verdict={"thesis_state": "invalidated", "would_open_today": "marginal", "consistency": "unanimous"}
        )
        self.assertEqual(result["action"], "close_half")

    def test_invalidated_single_contract_flags_only(self) -> None:
        result = self._decide(
            verdict={"thesis_state": "invalidated", "would_open_today": "yes", "consistency": "unanimous"},
            quantity=1,
        )
        self.assertEqual(result["action"], "flag")

    def test_intact_unclear_and_errors_do_nothing(self) -> None:
        for verdict in (
            {"thesis_state": "intact", "would_open_today": "yes"},
            {"thesis_state": "unclear", "would_open_today": "no"},
            {"consistency": "error"},
            None,
        ):
            self.assertEqual(self._decide(verdict=verdict)["action"], "none")

    def test_daily_exit_cap_downgrades_to_flag(self) -> None:
        result = self._decide(llm_exits_today=2)
        self.assertEqual(result["action"], "flag")
        self.assertEqual(result["reason"], "daily_exit_cap_reached")

    def test_kill_switch_downgrades_to_flag(self) -> None:
        result = self._decide(kill_switch=True)
        self.assertEqual(result["action"], "flag")
        self.assertEqual(result["reason"], "kill_switch_active")


class ThesisValidatorDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "thesis.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")

    def tearDown(self) -> None:
        # The shared get_connection() pattern commits but keeps the sqlite
        # handle alive until GC, which locks the file on Windows.
        import gc

        gc.collect()
        try:
            self.temp_dir.cleanup()
        except OSError:
            pass

    def test_thesis_lifecycle_round_trip(self) -> None:
        thesis = {
            "symbol": "NVDA",
            "option_symbol": "NVDA260619C00190000",
            "structure": "long_call",
            "opened_at": "2026-08-20T14:00:00+00:00",
            "catalyst_type": "earnings_surprise",
            "catalyst_summary": "Beat and raise.",
            "source_article_ids": [11, 12],
            "expected_horizon_days": 27,
            "invalidation_conditions": ["This thesis is wrong if the beat is reversed."],
        }
        thesis_id = record_position_thesis(thesis, db_path=self.db_path)
        self.assertIsNotNone(thesis_id)
        # Idempotent: a second capture for the same open option returns the same row.
        self.assertEqual(record_position_thesis(thesis, db_path=self.db_path), thesis_id)

        stored = get_open_position_thesis_for_option_symbol("NVDA260619C00190000", db_path=self.db_path)
        self.assertEqual(stored["source_article_ids"], [11, 12])
        self.assertEqual(stored["invalidation_conditions"], thesis["invalidation_conditions"])

        run_id = record_thesis_validation_run(
            {
                "thesis_id": thesis_id,
                "option_symbol": "NVDA260619C00190000",
                "symbol": "NVDA",
                "run_at": "2026-08-25T19:30:00+00:00",
                "run_date": "2026-08-25",
                "mode": "shadow",
                "thesis_state": "invalidated",
                "would_open_today": "no",
                "consistency": "unanimous",
                "grounded": True,
                "evidence_ids": [11],
                "arbiter_action": "close",
                "executed": True,
                "mark_mid_price": 3.1,
            },
            db_path=self.db_path,
        )
        self.assertIsNotNone(run_id)
        # One run per thesis per day.
        self.assertIsNone(
            record_thesis_validation_run(
                {
                    "thesis_id": thesis_id,
                    "option_symbol": "NVDA260619C00190000",
                    "run_at": "2026-08-25T19:45:00+00:00",
                    "run_date": "2026-08-25",
                    "thesis_state": "intact",
                },
                db_path=self.db_path,
            )
        )

        stored_run = get_thesis_validation_run_for_date(
            thesis_id=thesis_id, run_date="2026-08-25", db_path=self.db_path
        )
        self.assertEqual(stored_run["thesis_state"], "invalidated")
        self.assertEqual(stored_run["evidence_ids"], [11])
        self.assertEqual(
            count_thesis_validator_exits_for_date("2026-08-25", db_path=self.db_path), 1
        )

        self.assertTrue(
            close_position_thesis(
                "NVDA260619C00190000", closed_at="2026-08-26T15:00:00+00:00", db_path=self.db_path
            )
        )
        self.assertIsNone(
            get_open_position_thesis_for_option_symbol("NVDA260619C00190000", db_path=self.db_path)
        )

        # The health window counts back from today; widen it so the fixed 2026-08-25 run never ages out.
        health = summarize_thesis_validator_health(max_age_days=36500, db_path=self.db_path)
        self.assertEqual(health["evaluated_count"], 1)
        self.assertEqual(health["grounding_rate"], 1.0)
        self.assertEqual(health["executed_exit_count"], 1)


class ThesisValidatorServiceTests(unittest.TestCase):
    def _service(self, **setting_overrides) -> ThesisValidatorService:
        settings = ThesisValidatorSettings(**setting_overrides)
        return ThesisValidatorService(settings=settings)

    def test_shadow_mode_records_but_never_executes(self) -> None:
        service = self._service(mode="shadow")
        late = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)  # 16:00 ET
        position = {"symbol": "NVDA260619C00190000", "decision": "hold", "quantity": 2, "mid_price": 3.0}
        recorded_runs: list[dict] = []
        with (
            patch(
                "market_db_modules.thesis_validator.get_open_position_thesis_for_option_symbol",
                return_value={"id": 1, "symbol": "NVDA", "company_id": 7, "opened_at": "2026-08-20T14:00:00+00:00"},
            ),
            patch("market_db_modules.thesis_validator.get_thesis_validation_run_for_date", return_value=None),
            patch("market_db_modules.thesis_validator.count_thesis_validator_exits_for_date", return_value=0),
            patch("market_db_modules.thesis_validator.record_thesis_validation_run", side_effect=recorded_runs.append),
            patch("services.thesis.thesis_evidence_pack.build_thesis_evidence_pack", return_value={"allowed_evidence_article_ids": []}),
            patch(
                "services.thesis.thesis_validator_adjudicator.adjudicate_thesis",
                return_value={
                    "thesis_state": "invalidated",
                    "would_open_today": "no",
                    "evidence_ids": [5],
                    "rationale": "r",
                    "confidence": 0.9,
                    "consistency": "unanimous",
                    "flip_detected": False,
                    "grounded": True,
                    "sample_count": 3,
                    "raw_samples": [],
                },
            ),
        ):
            full_exit_calls: list = []
            summary = service.run_due_validation(
                positions=[position],
                submit_full_exit=full_exit_calls.append,
                now=late,
            )

        self.assertEqual(summary["evaluated_count"], 1)
        self.assertEqual(summary["results"][0]["arbiter_action"], "close")
        self.assertFalse(summary["results"][0]["executed"])
        self.assertEqual(full_exit_calls, [])
        self.assertEqual(len(recorded_runs), 1)
        self.assertEqual(recorded_runs[0]["thesis_state"], "invalidated")
        self.assertEqual(recorded_runs[0]["mode"], "shadow")
        self.assertEqual(recorded_runs[0]["mark_mid_price"], 3.0)

    def test_rules_exit_skips_llm_entirely(self) -> None:
        service = self._service(mode="shadow")
        late = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)
        position = {"symbol": "NVDA260619C00190000", "close_submitted": True, "decision": "sell"}
        recorded_runs: list[dict] = []
        with (
            patch(
                "market_db_modules.thesis_validator.get_open_position_thesis_for_option_symbol",
                return_value={"id": 1, "symbol": "NVDA", "opened_at": "2026-08-20T14:00:00+00:00"},
            ),
            patch("market_db_modules.thesis_validator.get_thesis_validation_run_for_date", return_value=None),
            patch("market_db_modules.thesis_validator.record_thesis_validation_run", side_effect=recorded_runs.append),
            patch("services.thesis.thesis_validator_adjudicator.adjudicate_thesis") as mock_adjudicate,
        ):
            summary = service.run_due_validation(positions=[position], now=late)

        mock_adjudicate.assert_not_called()
        self.assertEqual(summary["results"][0]["status"], "rules_exit_precedence")
        self.assertEqual(recorded_runs[0]["rules_verdict"], "exit")
        self.assertEqual(recorded_runs[0]["arbiter_action"], "none")


if __name__ == "__main__":
    unittest.main()
