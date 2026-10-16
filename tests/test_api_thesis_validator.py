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
    def test_latest_verdicts_fail_soft_on_db_errors(self) -> None:
        with patch(
            "api_support.thesis_validator.get_latest_thesis_validation_runs_by_option_symbol",
            side_effect=RuntimeError("db locked"),
        ):
            self.assertEqual(load_latest_thesis_verdicts(), {})


if __name__ == "__main__":
    unittest.main()
