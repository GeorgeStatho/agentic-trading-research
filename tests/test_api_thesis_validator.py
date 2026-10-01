from __future__ import annotations

import unittest
from unittest.mock import patch


try:
    from api_support.thesis_validator import (
        build_thesis_validator_payload,
        load_latest_thesis_verdicts,
    )

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_thesis_validator_payload = None
    load_latest_thesis_verdicts = None
    FLASK_AVAILABLE = False


def _stored_run(**overrides) -> dict:
    run = {
        "thesis_id": 1,
        "option_symbol": "NVDA260619C00190000",
        "symbol": "NVDA",
        "run_at": "2026-08-25T19:30:00+00:00",
        "run_date": "2026-08-25",
        "mode": "shadow",
        "sample_count": 3,
        "consistency": "unanimous",
        "flip_detected": 0,
        "grounded": 1,
        "thesis_state": "invalidated",
        "would_open_today": "no",
        "rationale": "Catalyst reversed.",
        "confidence": 0.9,
        "rules_verdict": "hold",
        "arbiter_action": "close",
        "arbiter_reason": "invalidated_would_not_open",
        "executed": 0,
        "mark_mid_price": 3.1,
        "error_text": None,
        "evidence_ids": [11],
        "evidence_pack": {"huge": "blob"},
        "raw_samples": [{"huge": "blob"}],
    }
    run.update(overrides)
    return run


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class ThesisValidatorPayloadTests(unittest.TestCase):
    def test_payload_summarizes_runs_without_heavy_blobs(self) -> None:
        health = {"position_day_count": 4, "flip_rate": 0.0, "action_rate": 0.25, "grounding_rate": 1.0}
        with (
            patch("api_support.thesis_validator.list_thesis_validation_runs", return_value=[_stored_run()]),
            patch("api_support.thesis_validator.summarize_thesis_validator_health", return_value=health),
        ):
            payload = build_thesis_validator_payload()

        self.assertEqual(payload["run_count"], 1)
        self.assertEqual(payload["health"], health)
        run = payload["runs"][0]
        self.assertEqual(run["thesis_state"], "invalidated")
        self.assertEqual(run["evidence_ids"], [11])
        self.assertTrue(run["grounded"])
        self.assertFalse(run["executed"])
        self.assertNotIn("evidence_pack", run)
        self.assertNotIn("raw_samples", run)

    def test_latest_verdicts_are_keyed_by_option_symbol(self) -> None:
        with patch(
            "api_support.thesis_validator.get_latest_thesis_validation_runs_by_option_symbol",
            return_value={"NVDA260619C00190000": _stored_run()},
        ):
            verdicts = load_latest_thesis_verdicts()

        self.assertEqual(list(verdicts.keys()), ["NVDA260619C00190000"])
        self.assertEqual(verdicts["NVDA260619C00190000"]["arbiter_action"], "close")

    def test_latest_verdicts_fail_soft_on_db_errors(self) -> None:
        with patch(
            "api_support.thesis_validator.get_latest_thesis_validation_runs_by_option_symbol",
            side_effect=RuntimeError("db locked"),
        ):
            self.assertEqual(load_latest_thesis_verdicts(), {})


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class OpenPositionsThesisVerdictTests(unittest.TestCase):
    def test_option_rows_carry_latest_thesis_verdict(self) -> None:
        from api_support.dashboard import build_open_positions_payload

        raw_positions = [
            {
                "symbol": "NVDA260619C00190000",
                "asset_class": "option",
                "qty": "1",
                "avg_entry_price": "3.25",
                "current_price": "3.67",
                "unrealized_plpc": "0.1292",
            }
        ]
        verdict = {
            "thesis_state": "degraded",
            "arbiter_action": "tighten",
            "rationale": "Momentum fading.",
        }
        with (
            patch("api_support.dashboard.alpaca_get_json", return_value=raw_positions),
            patch("api_support.dashboard.load_option_management_snapshot", return_value=({}, {})),
            patch(
                "api_support.dashboard.get_latest_option_quote",
                return_value={"bid_price": 3.6, "ask_price": 3.8, "timestamp": ""},
            ),
            patch(
                "api_support.dashboard.load_latest_thesis_verdicts",
                return_value={"NVDA260619C00190000": verdict},
            ),
        ):
            payload = build_open_positions_payload()

        self.assertEqual(payload["positions"][0]["thesis_verdict"], verdict)


if __name__ == "__main__":
    unittest.main()
