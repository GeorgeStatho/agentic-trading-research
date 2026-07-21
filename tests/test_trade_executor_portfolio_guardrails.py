from __future__ import annotations

import logging
import sys
import unittest
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
            max_concurrent_option_positions=2,
            max_option_sector_exposure_pct=50.0,
            daily_option_loss_limit_dollars=2000.0,
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


if __name__ == "__main__":
    unittest.main()
