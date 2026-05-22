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


class TradeExecutorHistoryLinkTests(unittest.TestCase):
    def test_execute_candidate_links_successful_execution_to_manager_decision_history(self) -> None:
        settings = SimpleNamespace(
            option_contract_multiplier=100,
            per_order_sizing_buying_power_ratio=0.10,
            max_option_order_qty_multiplier=2,
            alpaca_paper=True,
            max_deployable_buying_power_pct=50.0,
        )
        trading_gateway = MagicMock()
        trading_gateway.submit_option_market_order.return_value = {
            "id": "order-123",
            "symbol": "AAPL260619C00190000",
            "qty": "1",
            "status": "new",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "submitted_at": "2026-05-22T14:00:00+00:00",
        }
        trade_journal = MagicMock()
        trade_journal.record_execution.return_value = 88
        executor = OptionTradeExecutor(
            settings=settings,
            trading_gateway=trading_gateway,
            trade_journal=trade_journal,
            logger=logging.getLogger("test_trade_executor_history_link"),
        )
        session = TradeExecutionSession(
            available_buying_power=10_000.0,
            max_deployable_buying_power=5_000.0,
            remaining_deployable_buying_power=5_000.0,
            base_order_qty=1,
        )
        candidate = {
            "company_id": 1,
            "symbol": "AAPL",
            "name": "Apple",
            "decision": "call",
            "confidence": "high",
            "selected_option_symbol": "AAPL260619C00190000",
            "selected_option": {
                "latest_quote": {
                    "ask_price": 2.0,
                    "midpoint_price": 1.95,
                    "bid_price": 1.9,
                }
            },
        }

        with patch("services.trade_executor.link_manager_decision_to_trade_execution") as mock_link:
            mock_link.return_value = 55
            execution = executor.execute_candidate(
                trading_client=object(),
                candidate=candidate,
                session=session,
            )

        trade_journal.record_execution.assert_called_once()
        mock_link.assert_called_once_with(
            trade_execution_order_id="order-123",
            trade_execution_record_id=88,
            company_id=1,
            symbol="AAPL",
            selected_option_symbol="AAPL260619C00190000",
            submitted_at="2026-05-22T14:00:00+00:00",
        )
        self.assertTrue(execution["submitted"])
        self.assertTrue(execution["manager_decision_history_linked"])
        self.assertEqual(execution["manager_decision_history_id"], 55)


if __name__ == "__main__":
    unittest.main()
