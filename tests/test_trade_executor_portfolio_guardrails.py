from __future__ import annotations

import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from services.trade_executor import OptionTradeExecutor, TradeExecutionSession  # noqa: E402


class OptionTradeExecutorPortfolioGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            default_option_order_qty=1,
            option_contract_multiplier=100,
            per_order_sizing_buying_power_ratio=0.10,
            max_option_order_qty_multiplier=2,
            alpaca_paper=True,
            max_deployable_buying_power_pct=50.0,
            max_deployable_buying_power_ratio=0.50,
            resolve_max_premium_per_trade=lambda _buying_power: 0.0,
            max_concurrent_option_positions=2,
            max_option_sector_exposure_pct=50.0,
            daily_option_loss_limit_dollars=2000.0,
            max_symbol_entries_per_window=0,
            symbol_entry_window_days=7,
            symbol_stop_loss_cooldown_days=0,
        )
        self.gateway = MagicMock()
        self.executor = OptionTradeExecutor(
            settings=self.settings,
            trading_gateway=self.gateway,
            logger=logging.getLogger("test_trade_executor_portfolio_guardrails"),
        )

    @staticmethod
    def _candidate() -> dict:
        return {
            "company_id": 1,
            "symbol": "MSFT",
            "sector_key": "technology",
            "decision": "call",
            "confidence": "high",
            "selected_option_symbol": "MSFT260619C00450000",
            "selected_option": {"latest_quote": {"ask_price": 10.0}},
        }

    @staticmethod
    def _session(**overrides) -> TradeExecutionSession:
        values = {
            "available_buying_power": 10_000.0,
            "max_deployable_buying_power": 5_000.0,
            "remaining_deployable_buying_power": 5_000.0,
            "base_order_qty": 1,
        }
        values.update(overrides)
        return TradeExecutionSession(**values)

    def _assert_rejected(self, session: TradeExecutionSession, reason: str) -> None:
        execution = self.executor.execute_candidate(
            trading_client=object(),
            candidate=self._candidate(),
            session=session,
        )
        self.assertFalse(execution["submitted"])
        self.assertEqual(execution["portfolio_guardrail"], reason)
        self.gateway.submit_option_market_order.assert_not_called()

    def test_create_session_reserves_existing_broker_exposure(self) -> None:
        self.gateway.get_available_buying_power.return_value = 10_000.0
        self.gateway.get_open_option_positions.return_value = [
            {"option_symbol": "MSFT260619C00450000", "underlying_symbol": "MSFT", "notional": 1_200.0},
        ]
        with patch("services.trade_executor.get_company_sector_key", return_value="technology"), patch(
            "services.trade_executor.get_realized_option_loss_since", return_value=300.0
        ):
            session = self.executor.create_session(trading_client=object())

        self.assertEqual(session.current_option_position_count, 1)
        self.assertEqual(session.current_option_exposure, 1_200.0)
        self.assertEqual(session.remaining_deployable_buying_power, 3_800.0)
        self.assertEqual(session.sector_exposure_by_key, {"technology": 1_200.0})
        self.assertEqual(session.daily_realized_loss, 300.0)

    def test_rejects_each_portfolio_limit_before_order_submission(self) -> None:
        self.settings.max_concurrent_option_positions = 1
        self._assert_rejected(
            self._session(current_option_position_count=1),
            "portfolio_max_concurrent_positions",
        )

        self.settings.max_concurrent_option_positions = 2
        self._assert_rejected(
            self._session(current_option_exposure=4_500.0, remaining_deployable_buying_power=500.0),
            "portfolio_total_entry_notional_limit",
        )

        self._assert_rejected(
            self._session(sector_exposure_by_key={"technology": 1_600.0}),
            "portfolio_sector_notional_limit",
        )

        self._assert_rejected(
            self._session(daily_realized_loss=2_000.0),
            "portfolio_daily_loss_limit",
        )

    def test_premium_cap_shrinks_order_qty_to_fit_the_allowance(self) -> None:
        # Sizing alone would buy 5 contracts ($5,000 of premium); the $2,500 cap
        # must cut it to 2 rather than rejecting an otherwise valid entry.
        self.settings.per_order_sizing_buying_power_ratio = 1.0
        self.settings.max_option_order_qty_multiplier = 50
        self.gateway.submit_option_market_order.return_value = {"id": "order-1"}

        execution = self.executor.execute_candidate(
            trading_client=object(),
            candidate=self._candidate(),
            session=self._session(max_premium_per_trade=2_500.0),
        )

        self.assertTrue(execution["submitted"])
        self.assertEqual(execution["order_qty"], 2)
        self.assertEqual(execution["estimated_order_cost"], 2_000.0)

    def test_premium_cap_rejects_when_one_contract_exceeds_the_allowance(self) -> None:
        # A $1,000 contract against a $600 cap has no permissible size, so the
        # entry is refused outright instead of falling back to the base qty.
        self._assert_rejected(
            self._session(max_premium_per_trade=600.0),
            "premium_per_trade_cap",
        )

    def test_premium_cap_is_disabled_when_unset(self) -> None:
        self.gateway.submit_option_market_order.return_value = {"id": "order-2"}

        execution = self.executor.execute_candidate(
            trading_client=object(),
            candidate=self._candidate(),
            session=self._session(max_premium_per_trade=0.0),
        )

        self.assertTrue(execution["submitted"])
        self.assertEqual(execution["order_qty"], 1)

    def test_rejects_entry_once_symbol_attempt_cap_is_reached(self) -> None:
        self.settings.max_symbol_entries_per_window = 2
        with patch("services.trade_executor.count_option_entry_executions_since", return_value=2) as counter:
            self._assert_rejected(self._session(), "symbol_entry_attempt_cap")
        self.assertEqual(counter.call_args.kwargs["underlying_symbol"], "MSFT")

    def test_counts_same_session_entries_against_the_symbol_attempt_cap(self) -> None:
        # The journal write can lag or fail, so submissions already made in this
        # session still count toward the cap.
        self.settings.max_symbol_entries_per_window = 1
        with patch("services.trade_executor.count_option_entry_executions_since", return_value=0):
            self._assert_rejected(
                self._session(entries_by_underlying={"MSFT": 1}),
                "symbol_entry_attempt_cap",
            )

    def test_rejects_entry_during_stop_loss_cooldown(self) -> None:
        self.settings.symbol_stop_loss_cooldown_days = 7
        recent_stop = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        with patch("services.trade_executor.get_latest_stop_loss_exit_at", return_value=recent_stop):
            self._assert_rejected(self._session(), "symbol_stop_loss_cooldown")

    def test_allows_entry_once_stop_loss_cooldown_has_elapsed(self) -> None:
        self.settings.symbol_stop_loss_cooldown_days = 7
        old_stop = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        self.gateway.submit_option_market_order.return_value = {"id": "order-3"}

        with patch("services.trade_executor.get_latest_stop_loss_exit_at", return_value=old_stop):
            execution = self.executor.execute_candidate(
                trading_client=object(),
                candidate=self._candidate(),
                session=self._session(),
            )

        self.assertTrue(execution["submitted"])
        self.assertEqual(execution["portfolio_guardrail"], "")

    def test_symbol_attempt_cap_falls_open_when_the_journal_read_fails(self) -> None:
        # A DB outage must not silently block every entry.
        self.settings.max_symbol_entries_per_window = 2
        self.gateway.submit_option_market_order.return_value = {"id": "order-4"}

        with patch(
            "services.trade_executor.count_option_entry_executions_since",
            side_effect=RuntimeError("db down"),
        ):
            execution = self.executor.execute_candidate(
                trading_client=object(),
                candidate=self._candidate(),
                session=self._session(),
            )

        self.assertTrue(execution["submitted"])

    def test_session_resolves_the_premium_cap_from_settings(self) -> None:
        self.gateway.get_available_buying_power.return_value = 2_000.0
        self.gateway.get_open_option_positions.return_value = []
        self.settings.resolve_max_premium_per_trade = lambda buying_power: buying_power * 0.25

        with patch("services.trade_executor.get_realized_option_loss_since", return_value=0.0):
            session = self.executor.create_session(trading_client=object())

        self.assertEqual(session.max_premium_per_trade, 500.0)

    def test_submitted_entry_increments_the_session_symbol_counter(self) -> None:
        self.gateway.submit_option_market_order.return_value = {"id": "order-5"}
        session = self._session()

        self.executor.execute_candidate(
            trading_client=object(),
            candidate=self._candidate(),
            session=session,
        )

        self.assertEqual(session.entries_by_underlying, {"MSFT": 1})


if __name__ == "__main__":
    unittest.main()
