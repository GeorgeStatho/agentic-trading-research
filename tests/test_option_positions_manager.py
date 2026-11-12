"""Exit submission paths of the option position manager loop."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from services.core.trading_config import MomentumHistoryConfig, OptionPositionSettings
from services.options.option_position_state import get_position_state, update_position_state
from trading_support import _option_positions_manager as manager


OPTION_SYMBOL = "AAPL260116C00200000"
OTHER_OPTION_SYMBOL = "MSFT260116P00300000"


class _Client:
    def __init__(self, positions):
        self.positions = positions
        self.closed: list[str] = []

    def get_all_positions(self):
        return self.positions

    def close_position(self, symbol):
        self.closed.append(symbol)
        return SimpleNamespace(id=f"close-{symbol}", status="accepted", side="sell", qty="2",
                               submitted_at="2026-11-06T15:00:00+00:00")


def _position(symbol, qty="2"):
    return SimpleNamespace(symbol=symbol, qty=qty)


def _snapshot(exit_action, **fields):
    def build(position, **kwargs):
        return {"symbol": position.symbol, "exit_action": exit_action, "exit_reason": "take_profit",
                "quantity": 2, **fields}
    return build


class OptionPositionsManagerTests(unittest.TestCase):
    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.state_path = Path(temp_dir.name) / "option_state.json"
        self.settings = replace(OptionPositionSettings.from_env(), trailing_profit_dry_run=False)

    def _manage(self, client, exit_action, *, settings=None, company=None, **callbacks):
        return manager.ManageCurrentOptionPositions(
            company,
            settings=settings or self.settings,
            state_path=self.state_path,
            execute_sales=True,
            trading_client_override=client,
            get_default_trading_client=lambda: client,
            build_option_position_snapshot=_snapshot(exit_action, qty_to_sell=1),
            **callbacks,
        )

    def test_close_option_positions_closes_only_the_company_s_option_positions(self):
        client = _Client([_position(OPTION_SYMBOL), _position(OTHER_OPTION_SYMBOL), _position("AAPL")])

        results = manager.CloseOptionPositions("AAPL", trading_client_override=client,
                                               get_default_trading_client=lambda: client)

        self.assertEqual([OPTION_SYMBOL], client.closed)
        self.assertEqual([{"symbol": OPTION_SYMBOL, "qty": "2", "close_order_id": f"close-{OPTION_SYMBOL}",
                           "close_order_status": "accepted"}], results)

    def test_manager_requires_the_client_and_snapshot_callbacks(self):
        with self.assertRaises(ValueError):
            manager.ManageCurrentOptionPositions(settings=self.settings, state_path=self.state_path)

    def test_full_exit_without_an_order_callback_closes_through_the_broker_and_records_the_pending_order(self):
        client = _Client([_position(OPTION_SYMBOL)])

        result = self._manage(client, "sell_full")

        summary = result["positions"][0]
        self.assertEqual([OPTION_SYMBOL], client.closed)
        self.assertTrue(summary["close_submitted"])
        self.assertEqual(f"close-{OPTION_SYMBOL}", summary["close_order_id"])
        self.assertEqual(2, summary["close_requested_qty"])
        self.assertEqual(1, result["close_submitted_count"])
        self.assertEqual(f"close-{OPTION_SYMBOL}", get_position_state(self.state_path, OPTION_SYMBOL)["pending_order_id"])

    def test_a_failed_full_exit_is_reported_on_the_position_not_raised(self):
        client = _Client([_position(OPTION_SYMBOL)])
        failing = mock.Mock(side_effect=RuntimeError("broker rejected"))

        result = self._manage(client, "sell_full", submit_full_exit_order=failing)

        self.assertEqual("broker rejected", result["positions"][0]["close_error"])
        self.assertEqual(0, result["close_submitted_count"])

    def test_dry_run_submits_no_exit_order(self):
        client = _Client([_position(OPTION_SYMBOL)])
        submit = mock.Mock()
        dry_run = replace(self.settings, trailing_profit_dry_run=True)

        full = self._manage(client, "sell_full", settings=dry_run, submit_full_exit_order=submit)
        partial = self._manage(client, "sell_partial", settings=dry_run, submit_partial_exit_order=submit)

        submit.assert_not_called()
        self.assertEqual([], client.closed)
        self.assertIn("Dry-run", full["positions"][0]["close_error"])
        self.assertIn("Dry-run", partial["positions"][0]["close_error"])

    def test_partial_exit_needs_a_partial_order_callback(self):
        result = self._manage(_Client([_position(OPTION_SYMBOL)]), "sell_partial")

        self.assertIn("no partial option sell callback", result["positions"][0]["close_error"])

    def test_partial_exit_submits_the_quantity_to_sell(self):
        submit = mock.Mock(return_value={"id": "partial-1", "status": "new", "side": "sell", "qty": "1"})

        result = self._manage(_Client([_position(OPTION_SYMBOL)]), "sell_partial", submit_partial_exit_order=submit)

        submit.assert_called_once_with(OPTION_SYMBOL, 1, limit_price=None)
        self.assertEqual(1, result["positions"][0]["close_requested_qty"])
        self.assertEqual(1, result["partial_sell_count"])

    def test_a_failed_partial_exit_is_reported_on_the_position_not_raised(self):
        failing = mock.Mock(side_effect=RuntimeError("no quote"))

        result = self._manage(_Client([_position(OPTION_SYMBOL)]), "sell_partial", submit_partial_exit_order=failing)

        self.assertEqual("no quote", result["positions"][0]["close_error"])

    def test_state_of_positions_no_longer_held_is_dropped_only_on_an_unfiltered_run(self):
        update_position_state(self.state_path, OTHER_OPTION_SYMBOL, {"last_action": "hold"})
        client = _Client([_position(OPTION_SYMBOL)])

        self._manage(client, "hold", company="AAPL")
        self.assertIsNotNone(get_position_state(self.state_path, OTHER_OPTION_SYMBOL))

        self._manage(client, "hold")
        self.assertIsNone(get_position_state(self.state_path, OTHER_OPTION_SYMBOL))

    def test_contracts_held_on_an_earlier_cycle_and_now_gone_are_reported_flattened(self):
        update_position_state(self.state_path, OTHER_OPTION_SYMBOL, {"last_action": "hold"})
        client = _Client([_position(OPTION_SYMBOL)])

        self.assertEqual([], self._manage(client, "hold", company="AAPL")["flattened_symbols"])
        self.assertEqual([OTHER_OPTION_SYMBOL], self._manage(client, "hold")["flattened_symbols"])

    def test_momentum_thresholds_larger_than_the_window_are_clamped_with_a_note(self):
        configured = MomentumHistoryConfig(enable_after_pnl_pct=5, window_size=3, min_samples=5,
                                           bad_count_exit_threshold=9, consecutive_bad_exit_threshold=4)

        config, notes = manager._build_momentum_history_config(configured)

        self.assertEqual((3, 3, 3), (config.min_samples, config.bad_count_exit_threshold,
                                     config.consecutive_bad_exit_threshold))
        self.assertEqual(3, len(notes))


if __name__ == "__main__":
    unittest.main()
