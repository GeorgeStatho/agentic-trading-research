from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services.market_regime import (  # noqa: E402
    MarketRegimeSnapshotPolicy,
    build_market_regime_snapshot,
    classify_market_regime,
)
from services.market_regime_gate import MarketRegimePolicy, evaluate_market_regime_gate  # noqa: E402
from services.order_candidates import OrderCandidateBuilder  # noqa: E402


NOW = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)


def _snapshot(*, state: str = "trend_up", macro_active: bool = False) -> dict:
    return {
        "as_of": NOW.isoformat(),
        "state": state,
        "macro_event_window": {"active": macro_active, "next_event": "US CPI" if macro_active else None},
        "policy_version": "market-regime-v1",
    }


def _classification(*, closes: list[float], macro_events: list[dict] | None = None, timestamp: datetime | None = None) -> dict:
    return classify_market_regime(
        spy_closes=closes,
        spy_last_timestamp=(timestamp or NOW).isoformat(),
        vix_closes=[],
        macro_events=macro_events or [],
        macro_refreshed_at=NOW.isoformat(),
        as_of=NOW,
        policy=MarketRegimeSnapshotPolicy(),
    )


class MarketRegimeClassificationTests(unittest.TestCase):
    def test_bullish_price_ma_alignment_classifies_trend_up(self) -> None:
        result = _classification(closes=[100 + index * 0.45 for index in range(70)])

        self.assertEqual(result["state"], "trend_up")
        self.assertEqual(result["signals"]["spy_trend"], "bullish")

    def test_stale_data_fails_closed_as_unknown(self) -> None:
        result = _classification(
            closes=[100 + index * 0.2 for index in range(70)],
            timestamp=NOW - timedelta(days=4),
        )

        self.assertEqual(result["state"], "unknown")
        self.assertIn("market_data_missing_or_stale", result["reason_codes"])

    def test_macro_blackout_overrides_bullish_market_alignment(self) -> None:
        result = _classification(
            closes=[100 + index * 0.45 for index in range(70)],
            macro_events=[
                {
                    "event_name": "US CPI",
                    "country": "United States",
                    "importance": "high",
                    "scheduled_at_utc": (NOW + timedelta(minutes=20)).isoformat(),
                }
            ],
        )

        self.assertEqual(result["state"], "stressed")
        self.assertTrue(result["macro_event_window"]["active"])

    def test_snapshot_records_a_failed_macro_refresh_without_disabling_fail_closed_behavior(self) -> None:
        with (
            patch(
                "services.market_regime.refresh_macro_calendar_for_market_regime",
                return_value={
                    "refreshed": False,
                    "macro_event_count": 0,
                    "reason_code": "macro_calendar_refresh_failed",
                },
            ),
            patch("services.market_regime._fetch_closes", side_effect=[([], None), ([], None)]),
            patch("services.market_regime._load_macro_calendar", return_value=([], None)),
        ):
            snapshot = build_market_regime_snapshot(as_of=NOW, persist=False)

        self.assertEqual(snapshot["state"], "unknown")
        self.assertIn("macro_calendar_refresh_failed", snapshot["reason_codes"])


class MarketRegimeGateTests(unittest.TestCase):
    def _gate(self, *, decision: str, confidence: str, state: str, macro_active: bool = False) -> dict:
        return evaluate_market_regime_gate(
            proposed_decision=decision,
            manager_confidence=confidence,
            regime=_snapshot(state=state, macro_active=macro_active),
            policy=MarketRegimePolicy(mode="enforce"),
        )

    def test_bullish_trend_permits_high_confidence_call(self) -> None:
        result = self._gate(decision="call", confidence="high", state="trend_up")

        self.assertTrue(result["allowed"])
        self.assertEqual(result["eligible_decision"], "call")

    def test_high_volatility_blocks_medium_confidence(self) -> None:
        result = self._gate(decision="call", confidence="medium", state="high_volatility")

        self.assertFalse(result["allowed"])
        self.assertEqual(result["sizing_multiplier"], 0.0)
        self.assertIn("regime_high_volatility_requires_high_confidence", result["reason_codes"])

    def test_stressed_blocks_all_directional_entries(self) -> None:
        for decision in ("call", "put"):
            result = self._gate(decision=decision, confidence="high", state="stressed")
            self.assertFalse(result["allowed"])
            self.assertEqual(result["eligible_decision"], "neither")

    def test_shadow_mode_records_block_without_changing_execution_permission(self) -> None:
        result = evaluate_market_regime_gate(
            proposed_decision="call",
            manager_confidence="medium",
            regime=_snapshot(state="high_volatility"),
            policy=MarketRegimePolicy(mode="shadow"),
        )

        self.assertTrue(result["allowed"])
        self.assertFalse(result["would_allow"])

    def test_batch_and_streaming_candidate_paths_require_regime_gate(self) -> None:
        builder = OrderCandidateBuilder()
        company_result = {
            "company_id": 1,
            "symbol": "MSFT",
            "decision": "call",
            "confidence": "high",
            "strategist_recommendation": {"decision": "trade_candidate"},
            "entry_gate": {"allowed": True},
            "market_regime_gate": {"allowed": False},
            "selected_option": {"symbol": "MSFT260619C00450000"},
        }
        self.assertEqual(builder.build_from_company_results([company_result]), [])
        self.assertIsNone(
            builder.build_from_manager_result(
                {
                    "company": {"company_id": 1, "symbol": "MSFT"},
                    "recommendation": {"decision": "call", "confidence": "high"},
                    "strategist_recommendation": {"decision": "trade_candidate"},
                    "entry_gate": {"allowed": True},
                    "market_regime_gate": {"allowed": False},
                    "selected_option": {"symbol": "MSFT260619C00450000"},
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
