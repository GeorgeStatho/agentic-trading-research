from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = REPO_ROOT / "Python Scripts"
TRADING_SUPPORT_DIR = PYTHON_SCRIPTS_DIR / "trading_support"
OPTION_SYMBOL = "AAPL240621C00150000"

if str(PYTHON_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_SCRIPTS_DIR))

from services.option_momentum import MOMENTUM_GOOD, MOMENTUM_UNKNOWN, evaluate_option_momentum


def _install_alpaca_stubs() -> None:
    if "alpaca" in sys.modules:
        return

    alpaca_module = types.ModuleType("alpaca")
    data_module = types.ModuleType("alpaca.data")
    data_requests_module = types.ModuleType("alpaca.data.requests")
    trading_module = types.ModuleType("alpaca.trading")
    trading_client_module = types.ModuleType("alpaca.trading.client")

    class _Request:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class _TradingClient:
        pass

    data_requests_module.OptionLatestQuoteRequest = _Request
    data_requests_module.StockLatestQuoteRequest = _Request
    trading_client_module.TradingClient = _TradingClient

    sys.modules["alpaca"] = alpaca_module
    sys.modules["alpaca.data"] = data_module
    sys.modules["alpaca.data.requests"] = data_requests_module
    sys.modules["alpaca.trading"] = trading_module
    sys.modules["alpaca.trading.client"] = trading_client_module


def _load_option_positions_module():
    _install_alpaca_stubs()

    package_name = "_test_trading_support"
    if package_name not in sys.modules:
        package_module = types.ModuleType(package_name)
        package_module.__path__ = [str(TRADING_SUPPORT_DIR)]
        sys.modules[package_name] = package_module

    utils_module_name = f"{package_name}.utils"
    if utils_module_name not in sys.modules:
        utils_spec = importlib.util.spec_from_file_location(
            utils_module_name,
            TRADING_SUPPORT_DIR / "utils.py",
        )
        assert utils_spec is not None and utils_spec.loader is not None
        utils_module = importlib.util.module_from_spec(utils_spec)
        sys.modules[utils_module_name] = utils_module
        utils_spec.loader.exec_module(utils_module)

    clients_module_name = f"{package_name}.clients"
    clients_module = types.ModuleType(clients_module_name)
    clients_module.get_default_trading_client = lambda: None
    clients_module.get_option_history_client = lambda: types.SimpleNamespace(
        get_option_latest_quote=lambda request: {}
    )
    clients_module.get_stock_history_client = lambda: types.SimpleNamespace(
        get_stock_latest_quote=lambda request: {}
    )
    sys.modules[clients_module_name] = clients_module

    module_name = f"{package_name}.option_positions"
    module_spec = importlib.util.spec_from_file_location(
        module_name,
        TRADING_SUPPORT_DIR / "option_positions.py",
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    return module


def _make_trailing_profit_config(module, **overrides):
    values = {
        "protection_trigger_pct": module.DEFAULT_OPTION_TRAIL_PROTECTION_TRIGGER_PCT,
        "initial_floor_pct": module.DEFAULT_OPTION_TRAIL_INITIAL_FLOOR_PCT,
        "first_scale_out_trigger_pct": module.DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT,
        "first_scale_out_fraction": module.DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION,
        "second_scale_out_trigger_pct": module.DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT,
        "second_scale_out_fraction": module.DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION,
        "giveback_pct_by_bucket_key": dict(module.DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY),
        "floor_100_pct": module.DEFAULT_OPTION_TRAIL_100_FLOOR_PCT,
        "floor_150_pct": module.DEFAULT_OPTION_TRAIL_150_FLOOR_PCT,
        "floor_200_pct": module.DEFAULT_OPTION_TRAIL_200_FLOOR_PCT,
        "enable_momentum_exit": module.DEFAULT_OPTION_POSITION_ENABLE_MOMENTUM_EXIT,
    }
    values.update(overrides)
    return module.TrailingProfitConfig(**values)


def _make_momentum_history_config(module, **overrides):
    values = {
        "enable_after_pnl_pct": module.DEFAULT_OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT,
        "window_size": module.DEFAULT_OPTION_MOMENTUM_HISTORY_WINDOW_SIZE,
        "min_samples": module.DEFAULT_OPTION_MOMENTUM_HISTORY_MIN_SAMPLES,
        "bad_count_exit_threshold": module.DEFAULT_OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD,
        "consecutive_bad_exit_threshold": module.DEFAULT_OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD,
    }
    values.update(overrides)
    return module.MomentumHistoryConfig(**values)


class VerboseTestCase(unittest.TestCase):
    def log_pass(self, message: str) -> None:
        print(f"[PASS] {self.__class__.__name__}.{self._testMethodName}: {message}")


class OptionPositionTests(VerboseTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.option_positions = _load_option_positions_module()
        cls.option_positions.DEFAULT_OPTION_PENDING_EXIT_STALE_MINUTES = 1.0
        cls.option_positions.DEFAULT_OPTION_PENDING_EXIT_CANCEL_ON_STALE = True

    def test_reconcile_stale_pending_order_submits_cancel(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            submitted_at = (datetime.now() - timedelta(minutes=5)).isoformat()
            self.option_positions.update_position_state(
                state_path,
                OPTION_SYMBOL,
                {
                    "pending_order_id": "order-123",
                    "pending_qty": 2,
                    "pending_reason": "trailing_profit_stop",
                    "pending_action": "sell_full",
                    "pending_order_submitted_at": submitted_at,
                    "last_order_submitted_at": submitted_at,
                },
            )

            cancelled_order_ids: list[str] = []

            persisted_state, reconciliation = self.option_positions._reconcile_pending_order_state(
                state_path=state_path,
                option_symbol=OPTION_SYMBOL,
                position_state=self.option_positions.get_position_state(state_path, OPTION_SYMBOL),
                resolve_order_status=lambda order_id: {
                    "id": order_id,
                    "status": "new",
                    "submitted_at": submitted_at,
                    "qty": "2",
                    "requested_qty": 2,
                    "filled_qty": "0",
                },
                cancel_order=lambda order_id: cancelled_order_ids.append(order_id)
                or {
                    "id": order_id,
                    "status": "pending_cancel",
                    "submitted_at": submitted_at,
                    "qty": "2",
                    "requested_qty": 2,
                    "filled_qty": "0",
                },
                broker_quantity=2,
                pending_exit_config=self.option_positions.PendingExitConfig(
                    stale_minutes=1.0,
                    cancel_on_stale=True,
                ),
            )

            self.assertEqual(cancelled_order_ids, ["order-123"])
            self.assertTrue(reconciliation["pending_order_stale"])
            self.assertTrue(reconciliation["pending_order_cancel_submitted"])
            self.assertEqual(reconciliation["pending_order_status"], "pending_cancel")
            self.assertEqual(persisted_state["pending_order_id"], "order-123")
            self.assertIn("last_cancel_attempt_at", persisted_state)
            self.log_pass("stale pending exit order was canceled and the state kept the cancel attempt details")

    def test_reconcile_retryable_partial_fill_preserves_scale_out_flag(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            submitted_at = datetime.now().isoformat()
            self.option_positions.update_position_state(
                state_path,
                OPTION_SYMBOL,
                {
                    "pending_order_id": "order-234",
                    "pending_qty": 2,
                    "pending_reason": "first_scale_out_trigger",
                    "pending_action": "sell_partial",
                    "pending_order_submitted_at": submitted_at,
                    "last_order_submitted_at": submitted_at,
                    "took_first_scale_out": True,
                },
            )

            persisted_state, reconciliation = self.option_positions._reconcile_pending_order_state(
                state_path=state_path,
                option_symbol=OPTION_SYMBOL,
                position_state=self.option_positions.get_position_state(state_path, OPTION_SYMBOL),
                resolve_order_status=lambda order_id: {
                    "id": order_id,
                    "status": "canceled",
                    "submitted_at": submitted_at,
                    "updated_at": submitted_at,
                    "qty": "2",
                    "requested_qty": 2,
                    "filled_qty": "1",
                },
                cancel_order=None,
                broker_quantity=1,
                pending_exit_config=self.option_positions.PendingExitConfig(
                    stale_minutes=10.0,
                    cancel_on_stale=True,
                ),
            )

            self.assertTrue(reconciliation["pending_order_terminal"])
            self.assertEqual(persisted_state["pending_order_id"], "")
            self.assertTrue(persisted_state["took_first_scale_out"])
            self.assertEqual(persisted_state["last_filled_qty"], 1)
            self.assertEqual(persisted_state["last_retryable_exit_status"], "canceled")
            self.log_pass("retryable partial fill kept the first scale-out flag while clearing the pending order")

    def test_reconcile_retryable_zero_fill_resets_scale_out_flag(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            submitted_at = datetime.now().isoformat()
            self.option_positions.update_position_state(
                state_path,
                OPTION_SYMBOL,
                {
                    "pending_order_id": "order-345",
                    "pending_qty": 2,
                    "pending_reason": "first_scale_out_trigger",
                    "pending_action": "sell_partial",
                    "pending_order_submitted_at": submitted_at,
                    "last_order_submitted_at": submitted_at,
                    "took_first_scale_out": True,
                },
            )

            persisted_state, reconciliation = self.option_positions._reconcile_pending_order_state(
                state_path=state_path,
                option_symbol=OPTION_SYMBOL,
                position_state=self.option_positions.get_position_state(state_path, OPTION_SYMBOL),
                resolve_order_status=lambda order_id: {
                    "id": order_id,
                    "status": "rejected",
                    "submitted_at": submitted_at,
                    "updated_at": submitted_at,
                    "qty": "2",
                    "requested_qty": 2,
                    "filled_qty": "0",
                },
                cancel_order=None,
                broker_quantity=2,
                pending_exit_config=self.option_positions.PendingExitConfig(
                    stale_minutes=10.0,
                    cancel_on_stale=True,
                ),
            )

            self.assertTrue(reconciliation["pending_order_terminal"])
            self.assertEqual(persisted_state["pending_order_id"], "")
            self.assertFalse(persisted_state["took_first_scale_out"])
            self.assertEqual(persisted_state["last_retryable_exit_status"], "rejected")
            self.log_pass("retryable zero-fill rejection reset the first scale-out flag for a clean retry")

    def test_structured_exit_action_triggers_momentum_history_failed(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=3,
            unrealized_pl_pct_ratio=2.10,
            days_to_expiration=10,
            hours_to_expiration=120.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="7-14 DTE",
                take_profit_pct=40.0,
                stop_loss_pct=-28.0,
                force_exit_days_to_expiration=3,
                exit_hours_to_expiration=72.0,
                is_default_rule=False,
            ),
            position_state={
                "max_pnl_pct": 2.20,
                "profit_protection_active": True,
                "protected_profit_floor_pct": 1.40,
                "momentum_tracking_active": True,
                "momentum_history_sample_count": 10,
                "momentum_bad_count": 6,
                "momentum_consecutive_bad_count": 2,
                "momentum_history": [
                    {"status": "good", "checked_at": "2026-05-14T10:00:00", "pnl_pct": 2.1, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:05:00", "pnl_pct": 2.08, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:10:00", "pnl_pct": 2.06, "max_pnl_pct": 2.2},
                    {"status": "good", "checked_at": "2026-05-14T10:15:00", "pnl_pct": 2.05, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:20:00", "pnl_pct": 2.04, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:25:00", "pnl_pct": 2.03, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:30:00", "pnl_pct": 2.02, "max_pnl_pct": 2.2},
                    {"status": "good", "checked_at": "2026-05-14T10:35:00", "pnl_pct": 2.01, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:40:00", "pnl_pct": 2.00, "max_pnl_pct": 2.2},
                ],
            },
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(
                self.option_positions,
                enable_momentum_exit=True,
            ),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=self.option_positions.MOMENTUM_BAD,
            momentum_reasons=["Momentum deteriorated after the large winner started rolling over."],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "momentum_history_failed")
        self.assertEqual(updated_state["last_decision_reason"], "momentum_history_failed")
        self.log_pass("momentum history full-exit fired once the tracked bad-count threshold was reached")

    def test_structured_exit_action_triggers_momentum_history_failed_from_weighted_mixed_score(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=3,
            unrealized_pl_pct_ratio=2.08,
            days_to_expiration=10,
            hours_to_expiration=120.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="7-14 DTE",
                take_profit_pct=40.0,
                stop_loss_pct=-28.0,
                force_exit_days_to_expiration=3,
                exit_hours_to_expiration=72.0,
                is_default_rule=False,
            ),
            position_state={
                "max_pnl_pct": 2.20,
                "profit_protection_active": True,
                "protected_profit_floor_pct": 1.40,
                "momentum_tracking_active": True,
                "momentum_history_sample_count": 8,
                "momentum_bad_count": 4,
                "momentum_mixed_count": 4,
                "momentum_negative_score": 6.0,
                "momentum_consecutive_bad_count": 1,
                "momentum_history": [
                    {"status": "bad", "checked_at": "2026-05-14T10:00:00", "pnl_pct": 2.18, "max_pnl_pct": 2.2, "negative_score": 1.0},
                    {"status": "mixed", "checked_at": "2026-05-14T10:05:00", "pnl_pct": 2.17, "max_pnl_pct": 2.2, "negative_score": 0.5},
                    {"status": "bad", "checked_at": "2026-05-14T10:10:00", "pnl_pct": 2.16, "max_pnl_pct": 2.2, "negative_score": 1.0},
                    {"status": "mixed", "checked_at": "2026-05-14T10:15:00", "pnl_pct": 2.15, "max_pnl_pct": 2.2, "negative_score": 0.5},
                    {"status": "bad", "checked_at": "2026-05-14T10:20:00", "pnl_pct": 2.14, "max_pnl_pct": 2.2, "negative_score": 1.0},
                    {"status": "mixed", "checked_at": "2026-05-14T10:25:00", "pnl_pct": 2.13, "max_pnl_pct": 2.2, "negative_score": 0.5},
                    {"status": "bad", "checked_at": "2026-05-14T10:30:00", "pnl_pct": 2.12, "max_pnl_pct": 2.2, "negative_score": 1.0},
                ],
            },
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(
                self.option_positions,
                enable_momentum_exit=True,
            ),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=self.option_positions.MOMENTUM_MIXED,
            momentum_reasons=["Option premium started to roll over while the underlying still looked partially constructive."],
            option_momentum_status=self.option_positions.MOMENTUM_BAD,
            underlying_momentum_status=self.option_positions.MOMENTUM_GOOD,
            momentum_negative_score=0.5,
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "momentum_history_failed")
        self.assertEqual(updated_state["last_decision_reason"], "momentum_history_failed")
        self.assertAlmostEqual(updated_state["momentum_negative_score"], 6.0, places=6)
        self.log_pass("weighted momentum scoring treated mixed samples as half-bad and still triggered the full exit once the threshold was reached")

    def test_structured_exit_action_triggers_momentum_history_failed_on_consecutive_bad_streak(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=3,
            unrealized_pl_pct_ratio=2.05,
            days_to_expiration=10,
            hours_to_expiration=120.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="7-14 DTE",
                take_profit_pct=40.0,
                stop_loss_pct=-28.0,
                force_exit_days_to_expiration=3,
                exit_hours_to_expiration=72.0,
                is_default_rule=False,
            ),
            position_state={
                "max_pnl_pct": 2.20,
                "profit_protection_active": True,
                "protected_profit_floor_pct": 1.40,
                "momentum_tracking_active": True,
                "momentum_history_sample_count": 5,
                "momentum_bad_count": 3,
                "momentum_consecutive_bad_count": 3,
                "momentum_history": [
                    {"status": "good", "checked_at": "2026-05-14T10:00:00", "pnl_pct": 2.15, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:05:00", "pnl_pct": 2.10, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:10:00", "pnl_pct": 2.08, "max_pnl_pct": 2.2},
                    {"status": "bad", "checked_at": "2026-05-14T10:15:00", "pnl_pct": 2.06, "max_pnl_pct": 2.2},
                ],
            },
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(
                self.option_positions,
                enable_momentum_exit=True,
            ),
            momentum_history_config=_make_momentum_history_config(
                self.option_positions,
                bad_count_exit_threshold=9,
                consecutive_bad_exit_threshold=3,
            ),
            momentum_status=self.option_positions.MOMENTUM_BAD,
            momentum_reasons=["Momentum deteriorated for three checks in a row after the large winner rolled over."],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "momentum_history_failed")
        self.assertEqual(updated_state["last_decision_reason"], "momentum_history_failed")
        self.log_pass("momentum history full-exit fired once the consecutive bad-sample threshold was reached")

    def test_structured_exit_action_does_not_force_momentum_history_exit_before_min_samples(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=3,
            unrealized_pl_pct_ratio=2.10,
            days_to_expiration=10,
            hours_to_expiration=120.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="7-14 DTE",
                take_profit_pct=40.0,
                stop_loss_pct=-28.0,
                force_exit_days_to_expiration=3,
                exit_hours_to_expiration=72.0,
                is_default_rule=False,
            ),
            position_state={
                "max_pnl_pct": 2.20,
                "profit_protection_active": True,
                "protected_profit_floor_pct": 1.40,
                "momentum_tracking_active": True,
                "momentum_history_sample_count": 2,
                "momentum_bad_count": 2,
                "momentum_consecutive_bad_count": 2,
                "momentum_history": [
                    {"status": "bad", "checked_at": "2026-05-14T10:00:00", "pnl_pct": 2.1, "max_pnl_pct": 2.2}
                ],
            },
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(
                self.option_positions,
                enable_momentum_exit=True,
            ),
            momentum_history_config=_make_momentum_history_config(
                self.option_positions,
                min_samples=5,
            ),
            momentum_status=self.option_positions.MOMENTUM_BAD,
            momentum_reasons=["Momentum deteriorated after the large winner started rolling over."],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "sell_partial")
        self.assertEqual(exit_action["reason"], "first_scale_out_trigger")
        self.assertEqual(updated_state["last_decision_reason"], "first_scale_out_trigger")
        self.log_pass("momentum history waited for the minimum sample count before allowing a full exit")

    def test_structured_exit_action_does_not_force_momentum_history_exit_when_disabled(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=3,
            unrealized_pl_pct_ratio=2.10,
            days_to_expiration=10,
            hours_to_expiration=120.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="7-14 DTE",
                take_profit_pct=40.0,
                stop_loss_pct=-28.0,
                force_exit_days_to_expiration=3,
                exit_hours_to_expiration=72.0,
                is_default_rule=False,
            ),
            position_state={
                "max_pnl_pct": 2.20,
                "profit_protection_active": True,
                "protected_profit_floor_pct": 1.40,
                "momentum_tracking_active": True,
                "momentum_history_sample_count": 10,
                "momentum_bad_count": 7,
                "momentum_consecutive_bad_count": 4,
                "momentum_history": [
                    {"status": "bad", "checked_at": "2026-05-14T10:00:00", "pnl_pct": 2.1, "max_pnl_pct": 2.2}
                ],
            },
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(
                self.option_positions,
                enable_momentum_exit=False,
            ),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=self.option_positions.MOMENTUM_BAD,
            momentum_reasons=["Momentum deteriorated after the large winner started rolling over."],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "sell_partial")
        self.assertEqual(exit_action["reason"], "first_scale_out_trigger")
        self.assertEqual(updated_state["last_decision_reason"], "first_scale_out_trigger")
        self.log_pass("disabled momentum history exit preserved the trailing scale-out path instead of forcing a full exit")

    def test_structured_exit_action_allows_stop_loss_with_noncritical_quote_note(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=-0.35,
            days_to_expiration=10,
            hours_to_expiration=120.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="7-14 DTE",
                take_profit_pct=40.0,
                stop_loss_pct=-28.0,
                force_exit_days_to_expiration=3,
                exit_hours_to_expiration=72.0,
                is_default_rule=False,
            ),
            position_state={},
            critical_errors=[],
            context_notes=["Underlying stock quote was unavailable; momentum was left informational only."],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(self.option_positions),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=MOMENTUM_UNKNOWN,
            momentum_reasons=["Current underlying price was missing or invalid."],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "stop_loss")
        self.assertIn("Underlying stock quote was unavailable", exit_action["notes"][0])
        self.log_pass("stop-loss still fired when only a noncritical underlying-quote note was present")

    def test_structured_exit_action_allows_near_expiration_exit_without_option_pricing(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=None,
            days_to_expiration=1,
            hours_to_expiration=8.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="3-7 DTE",
                take_profit_pct=30.0,
                stop_loss_pct=-22.0,
                force_exit_days_to_expiration=1,
                exit_hours_to_expiration=24.0,
                is_default_rule=False,
            ),
            position_state={},
            critical_errors=[],
            context_notes=[
                "Option quote was unavailable; the manager used whatever broker P/L and expiration context was still available."
            ],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(self.option_positions),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=MOMENTUM_UNKNOWN,
            momentum_reasons=["Current underlying price was missing or invalid."],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "near_expiration")
        self.assertIn("Option quote was unavailable", exit_action["notes"][0])
        self.log_pass("near-expiration rule still protected the position even without option pricing")

    def test_trailing_giveback_uses_same_bucket_boundaries_as_exit_thresholds(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            giveback_pct_by_bucket_key={
                "7_14": 0.22,
                "14_30": 0.33,
                "30_45": 0.44,
                "45_60": 0.55,
            },
        )

        self.assertEqual(
            self.option_positions._resolve_trailing_giveback_pct(7, trailing_profit_config),
            0.22,
        )
        self.assertEqual(
            self.option_positions._resolve_trailing_giveback_pct(14, trailing_profit_config),
            0.33,
        )
        self.assertEqual(
            self.option_positions._resolve_trailing_giveback_pct(30, trailing_profit_config),
            0.44,
        )
        self.assertEqual(
            self.option_positions._resolve_trailing_giveback_pct(45, trailing_profit_config),
            0.55,
        )
        self.log_pass("trailing giveback buckets matched the shared DTE boundaries including the newer 30-45 and 45-60 ranges")

    def test_structured_exit_action_activates_profit_protection_after_trigger(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            protection_trigger_pct=0.40,
            initial_floor_pct=0.10,
            first_scale_out_trigger_pct=0.80,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=0.45,
            days_to_expiration=10,
            hours_to_expiration=120.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="7-14 DTE",
                take_profit_pct=40.0,
                stop_loss_pct=-28.0,
                force_exit_days_to_expiration=3,
                exit_hours_to_expiration=72.0,
                is_default_rule=False,
            ),
            position_state={},
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=trailing_profit_config,
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=MOMENTUM_UNKNOWN,
            momentum_reasons=[],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "hold")
        self.assertTrue(updated_state["profit_protection_active"])
        self.assertEqual(updated_state["protected_profit_floor_pct"], 0.10)
        self.assertEqual(updated_state["trailing_giveback_pct"], trailing_profit_config.giveback_pct_by_bucket_key["7_14"])
        self.assertIn("Profit protection activated.", exit_action["notes"])
        self.log_pass("profit protection activated once gains crossed the configured trigger")

    def test_structured_exit_action_triggers_trailing_profit_stop_after_giveback(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            floor_100_pct=0.60,
            first_scale_out_trigger_pct=0.80,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=3,
            unrealized_pl_pct_ratio=0.58,
            days_to_expiration=20,
            hours_to_expiration=200.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="14-30 DTE",
                take_profit_pct=60.0,
                stop_loss_pct=-35.0,
                force_exit_days_to_expiration=7,
                exit_hours_to_expiration=168.0,
                is_default_rule=False,
            ),
            position_state={
                "max_pnl_pct": 1.10,
                "profit_protection_active": True,
                "protected_profit_floor_pct": 0.40,
            },
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=trailing_profit_config,
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=MOMENTUM_GOOD,
            momentum_reasons=[],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "trailing_profit_stop")
        self.assertAlmostEqual(exit_action["protected_profit_floor_pct"], 0.65, places=6)
        self.assertEqual(updated_state["last_decision_reason"], "trailing_profit_stop")
        self.log_pass("trailing profit stop sold after gains gave back through the protected floor")

    def test_build_option_position_snapshot_persists_momentum_history_after_threshold(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            option_symbol = "AAPL260523C00150000"

            position = SimpleNamespace(
                symbol=option_symbol,
                avg_entry_price="1.0",
                qty="2",
                unrealized_plpc=2.10,
            )

            original_get_latest_option_quote = self.option_positions._get_latest_option_quote
            original_get_latest_stock_price = self.option_positions._get_latest_stock_price
            try:
                self.option_positions._get_latest_option_quote = lambda _symbol: {
                    "bid_price": 3.05,
                    "ask_price": 3.15,
                    "mid_price": 3.10,
                    "price": 3.10,
                    "timestamp": "2026-05-14T15:30:00Z",
                    "error": "",
                }
                self.option_positions._get_latest_stock_price = lambda _symbol: {
                    "price": 120.0,
                    "timestamp": "2026-05-14T15:30:00Z",
                    "error": "",
                }

                summary = self.option_positions._build_option_position_snapshot(
                    position,
                    take_profit_pct=25.0,
                    stop_loss_pct=-20.0,
                    exit_hours_to_expiration=24.0,
                    enable_trailing_profit=True,
                    trailing_profit_dry_run=False,
                    trailing_profit_config=_make_trailing_profit_config(self.option_positions),
                    momentum_history_config=_make_momentum_history_config(
                        self.option_positions,
                        enable_after_pnl_pct=2.00,
                        window_size=10,
                    ),
                    state_path=state_path,
                    position_state_override={
                        "entry_underlying_price": 100.0,
                    },
                    pending_reconciliation=None,
                )
            finally:
                self.option_positions._get_latest_option_quote = original_get_latest_option_quote
                self.option_positions._get_latest_stock_price = original_get_latest_stock_price

            persisted_state = self.option_positions.get_position_state(state_path, option_symbol)
            assert persisted_state is not None

            self.assertTrue(persisted_state["momentum_tracking_active"])
            self.assertEqual(persisted_state["momentum_history_sample_count"], 1)
            self.assertEqual(persisted_state["momentum_bad_count"], 0)
            self.assertEqual(persisted_state["momentum_consecutive_bad_count"], 0)
            self.assertEqual(persisted_state["last_momentum_status"], "good")
            self.assertEqual(len(persisted_state["momentum_history"]), 1)
            self.assertEqual(persisted_state["momentum_history"][0]["status"], "good")
            self.assertEqual(summary["momentum_history_sample_count"], 1)
            self.assertTrue(summary["momentum_tracking_active"])
            self.log_pass("momentum history sample was persisted once the position exceeded the configured activation threshold")

    def test_manage_current_option_positions_submits_partial_scale_out_for_trailing_profit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            partial_orders: list[tuple[str, int]] = []
            future_option_symbol = "AAPL260523C00150000"

            position = SimpleNamespace(
                symbol=future_option_symbol,
                avg_entry_price="1.0",
                qty="4",
                unrealized_plpc=None,
            )

            class _FakeTradingClient:
                def get_all_positions(self):
                    return [position]

            option_history_client = SimpleNamespace(
                get_option_latest_quote=lambda _request: {
                    future_option_symbol: SimpleNamespace(
                        bid_price=1.58,
                        ask_price=1.62,
                        timestamp="2026-05-13T15:30:00Z",
                    )
                }
            )
            stock_history_client = SimpleNamespace(
                get_stock_latest_quote=lambda _request: {
                    "AAPL": SimpleNamespace(
                        bid_price=199.5,
                        ask_price=200.5,
                        timestamp="2026-05-13T15:30:00Z",
                    )
                }
            )

            original_option_client = self.option_positions.get_option_history_client
            original_stock_client = self.option_positions.get_stock_history_client
            self.option_positions.get_option_history_client = lambda: option_history_client
            self.option_positions.get_stock_history_client = lambda: stock_history_client
            try:
                result = self.option_positions.ManageCurrentOptionPositions(
                    execute_sales=True,
                    state_path_override=state_path,
                    enable_trailing_profit_override=True,
                    trading_client_override=_FakeTradingClient(),
                    submit_partial_exit_order=lambda symbol, qty: partial_orders.append((symbol, qty))
                    or {
                        "id": "partial-order-1",
                        "status": "new",
                        "side": "sell",
                        "qty": str(qty),
                        "requested_qty": qty,
                        "submitted_at": datetime.now().isoformat(),
                    },
                )
            finally:
                self.option_positions.get_option_history_client = original_option_client
                self.option_positions.get_stock_history_client = original_stock_client

            self.assertEqual(result["position_count"], 1)
            self.assertEqual(result["partial_sell_count"], 1)
            self.assertEqual(result["close_submitted_count"], 1)
            self.assertEqual(partial_orders, [(future_option_symbol, 2)])

            summary = result["positions"][0]
            self.assertEqual(summary["exit_action"], "sell_partial")
            self.assertEqual(summary["exit_reason"], "first_scale_out_trigger")
            self.assertEqual(summary["qty_to_sell"], 2)
            self.assertTrue(summary["close_submitted"])
            self.assertTrue(summary["took_first_scale_out"])
            self.log_pass("position manager submitted the first partial scale-out order for a trailing-profit trigger")

    def test_momentum_marks_itm_call_getting_deeper_itm_as_good(self) -> None:
        assessment = evaluate_option_momentum(
            contract_type="call",
            entry_underlying_price=105.0,
            current_underlying_price=112.0,
            strike_price=100.0,
        )

        self.assertEqual(assessment.status, MOMENTUM_GOOD)
        self.assertTrue(assessment.moved_in_profitable_direction)
        self.assertTrue(assessment.moved_toward_strike)
        self.assertLess(assessment.distance_to_strike_now, assessment.distance_to_strike_at_entry)
        self.log_pass("ITM call momentum was marked good when the underlying moved deeper in the profitable direction")

    def test_momentum_marks_itm_put_getting_deeper_itm_as_good(self) -> None:
        assessment = evaluate_option_momentum(
            contract_type="put",
            entry_underlying_price=95.0,
            current_underlying_price=88.0,
            strike_price=100.0,
        )

        self.assertEqual(assessment.status, MOMENTUM_GOOD)
        self.assertTrue(assessment.moved_in_profitable_direction)
        self.assertTrue(assessment.moved_toward_strike)
        self.assertLess(assessment.distance_to_strike_now, assessment.distance_to_strike_at_entry)
        self.log_pass("ITM put momentum was marked good when the underlying moved deeper in the profitable direction")


if __name__ == "__main__":
    unittest.main()
