from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = REPO_ROOT / "Python Scripts"
TRADING_SUPPORT_DIR = PYTHON_SCRIPTS_DIR / "trading_support"
OPTION_SYMBOL = "AAPL240621C00150000"


from services.trading_config import (
    MomentumHistoryConfig,
    OptionPositionSettings,
    OptionPriceMomentumConfig,
    PendingExitConfig,
    TrailingProfitConfig,
)
from services.option_momentum import MOMENTUM_GOOD, MOMENTUM_UNKNOWN


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


def _make_future_option_symbol(
    underlying: str,
    *,
    days_until_expiration: int,
    contract_type: str = "C",
    strike_price: float = 150.0,
) -> str:
    expiration = datetime.now() + timedelta(days=days_until_expiration)
    strike_component = f"{int(round(strike_price * 1000)):08d}"
    return f"{underlying.upper()}{expiration:%y%m%d}{contract_type.upper()}{strike_component}"


def _default_option_env_values(module) -> dict[str, str]:
    values = {
        "OPTION_POSITION_ENABLE_MOMENTUM_EXIT": "false",
        "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT": "2.00",
        "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE": "10",
        "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES": "5",
        "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD": "6",
        "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD": "3",
        "OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD": "0.35",
        "OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD": "0.15",
        "OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD": "0.10",
        "OPTION_TRAIL_PROTECTION_TRIGGER_PCT": "0.40",
        "OPTION_TRAIL_INITIAL_FLOOR_PCT": "0.05",
        "OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT": "0.75",
        "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT": "0.55",
        "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION": "0.50",
        "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT": "2.00",
        "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION": "0.25",
        "OPTION_TRAIL_100_FLOOR_PCT": "0.60",
        "OPTION_TRAIL_150_FLOOR_PCT": "1.00",
        "OPTION_TRAIL_200_FLOOR_PCT": "1.40",
    }
    for bucket in module.OPTION_DTE_BUCKETS:
        giveback_value = float(bucket.default_trailing_giveback_pct if bucket.default_trailing_giveback_pct is not None else 0.25)
        values[f"OPTION_TRAIL_{bucket.key.upper()}_GIVEBACK_PCT"] = f"{giveback_value:.2f}"
    return values


def _make_trailing_profit_config(module, **overrides):
    env_values = _default_option_env_values(module)
    for key, value in overrides.items():
        if key == "giveback_pct_by_bucket_key":
            for bucket_key, bucket_value in dict(value).items():
                env_values[f"OPTION_TRAIL_{str(bucket_key).upper()}_GIVEBACK_PCT"] = str(bucket_value)
            continue
        env_name = {
            "protection_trigger_pct": "OPTION_TRAIL_PROTECTION_TRIGGER_PCT",
            "initial_floor_pct": "OPTION_TRAIL_INITIAL_FLOOR_PCT",
            "dynamic_giveback_start_pct": "OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT",
            "first_scale_out_trigger_pct": "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT",
            "first_scale_out_fraction": "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION",
            "second_scale_out_trigger_pct": "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT",
            "second_scale_out_fraction": "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION",
            "floor_100_pct": "OPTION_TRAIL_100_FLOOR_PCT",
            "floor_150_pct": "OPTION_TRAIL_150_FLOOR_PCT",
            "floor_200_pct": "OPTION_TRAIL_200_FLOOR_PCT",
            "enable_momentum_exit": "OPTION_POSITION_ENABLE_MOMENTUM_EXIT",
        }.get(key)
        if env_name is not None:
            env_values[env_name] = str(value).lower() if isinstance(value, bool) else str(value)
    return _make_trailing_profit_config_from_env_values(module, env_values)


def _make_momentum_history_config(module, **overrides):
    env_values = _default_option_env_values(module)
    for key, value in overrides.items():
        env_name = {
            "enable_after_pnl_pct": "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT",
            "window_size": "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE",
            "min_samples": "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES",
            "bad_count_exit_threshold": "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD",
            "consecutive_bad_exit_threshold": "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD",
        }.get(key)
        if env_name is not None:
            env_values[env_name] = str(value)
    return _make_momentum_history_config_from_env_values(module, env_values)


def _make_option_price_momentum_config(module, **overrides):
    env_values = _default_option_env_values(module)
    for key, value in overrides.items():
        env_name = {
            "bad_giveback_threshold": "OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD",
            "mixed_giveback_threshold": "OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD",
            "good_profit_threshold": "OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD",
        }.get(key)
        if env_name is not None:
            env_values[env_name] = str(value)
    return OptionPriceMomentumConfig(
        bad_giveback_threshold=_env_float_value(env_values, "OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD", 0.35),
        mixed_giveback_threshold=_env_float_value(env_values, "OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD", 0.15),
        good_profit_threshold=_env_float_value(env_values, "OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD", 0.10),
    )


def _make_exit_settings(module, **fields) -> OptionPositionSettings:
    return replace(
        OptionPositionSettings.from_env(),
        take_profit_pct=25.0,
        stop_loss_pct=-20.0,
        exit_hours_to_expiration=24.0,
        trailing=_make_trailing_profit_config(module),
        price_momentum=_make_option_price_momentum_config(module),
        **fields,
    )


def _env_float_value(env_values: dict[str, str], name: str, default: float) -> float:
    raw_value = str(env_values.get(name, default)).strip()
    try:
        return float(raw_value)
    except ValueError:
        return float(default)


def _env_flag_value(env_values: dict[str, str], name: str, default: bool) -> bool:
    raw_value = str(env_values.get(name, str(default))).strip().lower()
    return raw_value not in {"0", "false", "no", "off", ""}


def _make_trailing_profit_config_from_env_values(module, env_values: dict[str, str]):
    giveback_pct_by_bucket_key = {}
    for bucket in module.OPTION_DTE_BUCKETS:
        fallback = float(bucket.default_trailing_giveback_pct if bucket.default_trailing_giveback_pct is not None else 0.25)
        giveback_pct_by_bucket_key[bucket.key] = _env_float_value(
            env_values,
            f"OPTION_TRAIL_{bucket.key.upper()}_GIVEBACK_PCT",
            fallback,
        )
    return TrailingProfitConfig(
        protection_trigger_pct=_env_float_value(env_values, "OPTION_TRAIL_PROTECTION_TRIGGER_PCT", 0.40),
        initial_floor_pct=_env_float_value(env_values, "OPTION_TRAIL_INITIAL_FLOOR_PCT", 0.05),
        dynamic_giveback_start_pct=_env_float_value(env_values, "OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT", 0.75),
        first_scale_out_trigger_pct=_env_float_value(env_values, "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT", 0.55),
        first_scale_out_fraction=_env_float_value(env_values, "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION", 0.50),
        second_scale_out_trigger_pct=_env_float_value(env_values, "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT", 2.00),
        second_scale_out_fraction=_env_float_value(env_values, "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION", 0.25),
        giveback_pct_by_bucket_key=giveback_pct_by_bucket_key,
        floor_100_pct=_env_float_value(env_values, "OPTION_TRAIL_100_FLOOR_PCT", 0.60),
        floor_150_pct=_env_float_value(env_values, "OPTION_TRAIL_150_FLOOR_PCT", 1.00),
        floor_200_pct=_env_float_value(env_values, "OPTION_TRAIL_200_FLOOR_PCT", 1.40),
        enable_momentum_exit=_env_flag_value(env_values, "OPTION_POSITION_ENABLE_MOMENTUM_EXIT", False),
    )


def _make_momentum_history_config_from_env_values(module, env_values: dict[str, str]):
    return MomentumHistoryConfig(
        enable_after_pnl_pct=_env_float_value(env_values, "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT", 2.00),
        window_size=int(_env_float_value(env_values, "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE", 10)),
        min_samples=int(_env_float_value(env_values, "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES", 5)),
        bad_count_exit_threshold=int(_env_float_value(env_values, "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD", 6)),
        consecutive_bad_exit_threshold=int(
            _env_float_value(env_values, "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD", 3)
        ),
    )


class VerboseTestCase(unittest.TestCase):
    def log_pass(self, message: str) -> None:
        print(f"[PASS] {self.__class__.__name__}.{self._testMethodName}: {message}")


class OptionPositionTests(VerboseTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.option_positions = _load_option_positions_module()

    def _registry_bucket_for_days(self, days_to_expiration: int) -> object | None:
        for bucket in reversed(self.option_positions.OPTION_DTE_BUCKETS):
            if bucket.min_days <= days_to_expiration <= bucket.max_days:
                return bucket
        return None

    def _trailing_giveback_bucket_key_for_days(self, days_to_expiration: int) -> str:
        resolved_bucket = self._registry_bucket_for_days(days_to_expiration)
        if resolved_bucket is not None:
            return resolved_bucket.key
        return self.option_positions.OPTION_DTE_BUCKETS[0].key

    def _build_env_exit_thresholds(self, env_values: dict[str, str], *, bucket_key: str, label: str) -> object:
        return self.option_positions.ExitThresholds(
            dte_rule_label=label,
            take_profit_pct=_env_float_value(
                env_values,
                f"OPTION_POSITION_{bucket_key.upper()}_DTE_TAKE_PROFIT_PCT",
                35.0,
            ),
            stop_loss_pct=_env_float_value(
                env_values,
                f"OPTION_POSITION_{bucket_key.upper()}_DTE_STOP_LOSS_PCT",
                -25.0,
            ),
            force_exit_days_to_expiration=8,
            exit_hours_to_expiration=192.0,
            is_default_rule=False,
        )

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
                pending_exit_config=PendingExitConfig(
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
                pending_exit_config=PendingExitConfig(
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
                pending_exit_config=PendingExitConfig(
                    stale_minutes=10.0,
                    cancel_on_stale=True,
                ),
            )

            self.assertTrue(reconciliation["pending_order_terminal"])
            self.assertEqual(persisted_state["pending_order_id"], "")
            self.assertFalse(persisted_state["took_first_scale_out"])
            self.assertEqual(persisted_state["last_retryable_exit_status"], "rejected")
            self.log_pass("retryable zero-fill rejection reset the first scale-out flag for a clean retry")

    def test_reconcile_filled_alpaca_enum_status_clears_pending_order(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            submitted_at = datetime.now().isoformat()
            self.option_positions.update_position_state(
                state_path,
                OPTION_SYMBOL,
                {
                    "pending_order_id": "order-filled",
                    "pending_qty": 1,
                    "pending_reason": "first_scale_out_trigger",
                    "pending_action": "sell_partial",
                    "pending_order_submitted_at": submitted_at,
                    "took_first_scale_out": True,
                },
            )

            persisted_state, reconciliation = self.option_positions._reconcile_pending_order_state(
                state_path=state_path,
                option_symbol=OPTION_SYMBOL,
                position_state=self.option_positions.get_position_state(state_path, OPTION_SYMBOL),
                resolve_order_status=lambda order_id: {
                    "id": order_id,
                    "status": "OrderStatus.FILLED",
                    "submitted_at": submitted_at,
                    "updated_at": submitted_at,
                    "qty": "1",
                    "requested_qty": 1,
                    "filled_qty": "1",
                },
                cancel_order=None,
                broker_quantity=1,
                pending_exit_config=PendingExitConfig(
                    stale_minutes=10.0,
                    cancel_on_stale=True,
                ),
            )

            self.assertEqual(reconciliation["pending_order_status"], "filled")
            self.assertTrue(reconciliation["pending_order_terminal"])
            self.assertEqual(persisted_state["pending_order_id"], "")
            self.assertTrue(persisted_state["took_first_scale_out"])
            self.log_pass("filled Alpaca enum statuses clear pending exits instead of blocking trailing protection")

    def test_reconcile_replaced_order_follows_the_broker_replacement(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            submitted_at = datetime.now().isoformat()
            self.option_positions.update_position_state(
                state_path,
                OPTION_SYMBOL,
                {
                    "pending_order_id": "old-order",
                    "pending_qty": 1,
                    "pending_reason": "trailing_profit_stop",
                    "pending_action": "sell_full",
                    "pending_order_submitted_at": submitted_at,
                },
            )
            persisted_state, reconciliation = self.option_positions._reconcile_pending_order_state(
                state_path=state_path,
                option_symbol=OPTION_SYMBOL,
                position_state=self.option_positions.get_position_state(state_path, OPTION_SYMBOL),
                resolve_order_status=lambda order_id: {
                    "id": order_id,
                    "status": "OrderStatus.REPLACED",
                    "replaced_by": "replacement-order",
                    "submitted_at": submitted_at,
                    "updated_at": submitted_at,
                    "qty": "1",
                    "requested_qty": 1,
                },
                cancel_order=None,
                broker_quantity=1,
                pending_exit_config=PendingExitConfig(
                    stale_minutes=10.0,
                    cancel_on_stale=True,
                ),
            )
            self.assertEqual(reconciliation["pending_order_status"], "replaced")
            self.assertFalse(reconciliation["pending_order_terminal"])
            self.assertEqual(persisted_state["pending_order_id"], "replacement-order")
            self.assertEqual(persisted_state["last_replaced_order_id"], "old-order")

    def test_structured_exit_action_triggers_momentum_history_failed(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=3,
            unrealized_pl_pct_ratio=2.10,
            exit_rule_days_to_expiration=10,
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

    def test_structured_exit_action_ignores_midpoint_loss_for_stop_loss_when_broker_pl_is_above_floor(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=-0.35,
            exit_rule_days_to_expiration=10,
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
            trailing_profit_config=_make_trailing_profit_config(self.option_positions),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=MOMENTUM_UNKNOWN,
            momentum_reasons=[],
            recently_filled_order=False,
            broker_unrealized_pl_pct_ratio=-0.10,
        )

        self.assertEqual(exit_action["action"], "hold")
        self.assertEqual(exit_action["reason"], "hold")
        self.log_pass("stop-loss ignored a worse midpoint drawdown when broker P/L stayed above the stop floor")

    def _run_stop_loss_exit_action(
        self,
        *,
        broker_unrealized_pl_pct_ratio: float,
        position_state: dict,
        stop_loss_confirm_checks: int | None = None,
        underlying_adverse_move_pct: float | None = None,
        stop_loss_underlying_move_pct: float | None = None,
    ) -> tuple[dict, dict]:
        kwargs = dict(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=broker_unrealized_pl_pct_ratio,
            exit_rule_days_to_expiration=10,
            hours_to_expiration=120.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="7-14 DTE",
                take_profit_pct=40.0,
                stop_loss_pct=-28.0,
                force_exit_days_to_expiration=3,
                exit_hours_to_expiration=72.0,
                is_default_rule=False,
            ),
            position_state=position_state,
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(self.option_positions),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=MOMENTUM_UNKNOWN,
            momentum_reasons=[],
            recently_filled_order=False,
            broker_unrealized_pl_pct_ratio=broker_unrealized_pl_pct_ratio,
        )
        if stop_loss_confirm_checks is not None:
            kwargs["stop_loss_confirm_checks"] = stop_loss_confirm_checks
        if underlying_adverse_move_pct is not None:
            kwargs["underlying_adverse_move_pct"] = underlying_adverse_move_pct
        if stop_loss_underlying_move_pct is not None:
            kwargs["stop_loss_underlying_move_pct"] = stop_loss_underlying_move_pct
        return self.option_positions._structured_exit_action(**kwargs)

    def test_stop_loss_sells_on_first_breach_by_default(self) -> None:
        exit_action, updated_state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state={},
        )
        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "stop_loss")
        self.assertEqual(updated_state["stop_loss_breach_count"], 1)
        self.log_pass("stop-loss defaults to selling on the first breach when no confirmation window is configured")

    def test_stop_loss_confirmation_gate_holds_until_required_checks_then_fires(self) -> None:
        state: dict = {}

        first_action, state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state=state,
            stop_loss_confirm_checks=3,
        )
        self.assertEqual(first_action["action"], "hold")
        self.assertEqual(first_action["reason"], "stop_loss_pending_confirmation")
        self.assertEqual(state["stop_loss_breach_count"], 1)

        second_action, state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state=state,
            stop_loss_confirm_checks=3,
        )
        self.assertEqual(second_action["action"], "hold")
        self.assertEqual(state["stop_loss_breach_count"], 2)

        third_action, state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state=state,
            stop_loss_confirm_checks=3,
        )
        self.assertEqual(third_action["action"], "sell_full")
        self.assertEqual(third_action["reason"], "stop_loss")
        self.assertEqual(state["stop_loss_breach_count"], 3)
        self.log_pass("stop-loss held for two cycles and only forced a full exit once the third consecutive breach confirmed it")

    def test_stop_loss_confirmation_counter_resets_when_broker_pl_recovers(self) -> None:
        state: dict = {}

        breach_action, state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state=state,
            stop_loss_confirm_checks=3,
        )
        self.assertEqual(breach_action["action"], "hold")
        self.assertEqual(state["stop_loss_breach_count"], 1)

        recovered_action, state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.10,
            position_state=state,
            stop_loss_confirm_checks=3,
        )
        self.assertEqual(recovered_action["action"], "hold")
        self.assertEqual(state["stop_loss_breach_count"], 0)

        re_breach_action, state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state=state,
            stop_loss_confirm_checks=3,
        )
        self.assertEqual(re_breach_action["action"], "hold")
        self.assertEqual(re_breach_action["reason"], "stop_loss_pending_confirmation")
        self.assertEqual(state["stop_loss_breach_count"], 1)
        self.log_pass("a dip that bounced back above the stop floor reset the confirmation streak so the next dip re-confirms from scratch")

    def test_stop_loss_holds_when_underlying_has_not_moved_against_position(self) -> None:
        exit_action, updated_state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state={"stop_loss_breach_count": 2},
            stop_loss_confirm_checks=1,
            underlying_adverse_move_pct=0.004,
            stop_loss_underlying_move_pct=0.015,
        )
        self.assertEqual(exit_action["action"], "hold")
        self.assertEqual(exit_action["reason"], "stop_loss_underlying_hold")
        # The gate outranks any premium-breach streak: the stock is still fine, so
        # the streak is cleared rather than advanced toward a forced exit.
        self.assertEqual(updated_state["stop_loss_breach_count"], 0)
        self.log_pass("premium stop-loss held because the underlying had barely moved against the position")

    def test_stop_loss_falls_back_to_premium_stop_when_underlying_move_unknown(self) -> None:
        exit_action, _ = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state={},
            stop_loss_confirm_checks=1,
            underlying_adverse_move_pct=None,
            stop_loss_underlying_move_pct=0.015,
        )
        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "stop_loss")
        self.log_pass("premium stop-loss still protected the position when the underlying baseline was unavailable")

    def test_resolve_option_exit_limit_price_prices_passive_and_marketable_exits(self) -> None:
        resolve = self.option_positions._resolve_option_exit_limit_price
        # Passive exit posts at the midpoint to try to capture the spread.
        self.assertEqual(resolve(bid=1.58, ask=1.62, mid=1.60, cross_spread=False), 1.60)
        # Missing mid falls back to the bid/ask average.
        self.assertEqual(resolve(bid=1.58, ask=1.62, mid=None, cross_spread=False), 1.60)
        # A passive price is never posted below the bid.
        self.assertEqual(resolve(bid=1.58, ask=1.62, mid=1.40, cross_spread=False), 1.58)
        # Urgent / already-retried exits cross to the bid for a near-certain fill.
        self.assertEqual(resolve(bid=1.58, ask=1.62, mid=1.60, cross_spread=True), 1.58)
        # An unusable quote yields None so the caller falls back to a market order.
        self.assertIsNone(resolve(bid=None, ask=1.62, mid=1.60, cross_spread=False))
        self.assertIsNone(resolve(bid=0.0, ask=1.62, mid=1.60, cross_spread=True))
        self.log_pass("exit limit pricing posted passively at mid, clamped to the bid, and crossed to the bid when urgent")

    def test_exit_should_hold_for_wide_spread_guards_only_non_urgent_exits(self) -> None:
        should_hold = self.option_positions._exit_should_hold_for_wide_spread
        # Disabled guard never holds.
        self.assertFalse(should_hold(exit_reason="first_scale_out_trigger", option_spread_pct=0.50, max_spread_pct=0.0))
        # Non-urgent exit into a spread wider than the ceiling is held.
        self.assertTrue(should_hold(exit_reason="first_scale_out_trigger", option_spread_pct=0.20, max_spread_pct=0.10))
        # A tight spread is fine.
        self.assertFalse(should_hold(exit_reason="first_scale_out_trigger", option_spread_pct=0.05, max_spread_pct=0.10))
        # Hard risk exits bypass the guard even on a blown-out spread.
        self.assertFalse(should_hold(exit_reason="stop_loss", option_spread_pct=0.50, max_spread_pct=0.10))
        self.assertFalse(should_hold(exit_reason="near_expiration", option_spread_pct=0.50, max_spread_pct=0.10))
        # An unmeasurable spread does not block.
        self.assertFalse(should_hold(exit_reason="first_scale_out_trigger", option_spread_pct=None, max_spread_pct=0.10))
        self.log_pass("liquidity guard held only non-urgent exits on wide spreads and let hard risk exits through")

    def test_round_option_sell_price_to_tick_rounds_down_to_valid_increments(self) -> None:
        round_tick = self.option_positions._round_option_sell_price_to_tick
        # Below the threshold uses the small (nickel) tick, rounding down.
        self.assertEqual(round_tick(1.62), 1.60)
        self.assertEqual(round_tick(1.58), 1.55)
        self.assertEqual(round_tick(1.60), 1.60)
        # At/above the threshold uses the large (dime) tick.
        self.assertEqual(round_tick(3.05), 3.00)
        self.assertEqual(round_tick(3.00), 3.00)
        self.assertEqual(round_tick(2.99), 2.95)
        # A price below one tick falls back to a penny instead of rounding to 0.
        self.assertEqual(round_tick(0.03), 0.03)
        # Penny-name override keeps full precision.
        self.assertEqual(round_tick(1.58, small_tick=0.01, large_tick=0.01), 1.58)
        # Invalid/empty prices pass through untouched.
        self.assertIsNone(round_tick(None))
        self.log_pass("sell-limit tick rounding floored to valid increments and fell back to pennies for sub-tick prices")

    def test_structured_exit_action_uses_broker_pl_for_static_take_profit(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=0.80,
            exit_rule_days_to_expiration=10,
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
            enable_trailing_profit=False,
            trailing_profit_config=_make_trailing_profit_config(self.option_positions),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=MOMENTUM_UNKNOWN,
            momentum_reasons=[],
            recently_filled_order=False,
            broker_unrealized_pl_pct_ratio=0.30,
        )

        self.assertEqual(exit_action["action"], "hold")
        self.assertEqual(exit_action["reason"], "hold")
        self.assertEqual(updated_state["last_decision_reason"], "hold")
        self.log_pass("static take-profit ignored a midpoint-only gain when broker P/L stayed below the target")

    def test_structured_exit_action_allows_near_expiration_exit_without_option_pricing(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=None,
            exit_rule_days_to_expiration=1,
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

    def test_structured_exit_action_activates_profit_protection_after_trigger(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            protection_trigger_pct=0.40,
            initial_floor_pct=0.05,
            dynamic_giveback_start_pct=0.75,
            first_scale_out_trigger_pct=0.80,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=0.45,
            exit_rule_days_to_expiration=10,
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
        self.assertEqual(updated_state["protected_profit_floor_pct"], 0.05)
        expected_bucket_key = self._trailing_giveback_bucket_key_for_days(10)
        self.assertEqual(
            updated_state["trailing_giveback_pct"],
            trailing_profit_config.giveback_pct_by_bucket_key[expected_bucket_key],
        )
        self.assertIn("Profit protection activated.", exit_action["notes"])
        self.log_pass("profit protection activated once gains crossed the configured trigger")

    def test_structured_exit_action_does_not_scale_out_single_contract_position(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            protection_trigger_pct=0.40,
            initial_floor_pct=0.10,
            first_scale_out_trigger_pct=0.55,
            first_scale_out_fraction=0.50,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=1,
            unrealized_pl_pct_ratio=0.80,
            exit_rule_days_to_expiration=10,
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
        self.assertEqual(exit_action["reason"], "hold")
        self.assertEqual(exit_action["qty_to_sell"], 0)
        self.assertFalse(updated_state["took_first_scale_out"])
        self.assertIn("too small to sell a partial lot", " ".join(exit_action["notes"]))
        self.log_pass("single-contract winners stayed in profit-protection mode instead of converting a scale-out trigger into a full exit")

    def test_structured_exit_action_triggers_trailing_profit_stop_after_giveback(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            dynamic_giveback_start_pct=0.75,
            floor_100_pct=0.60,
            first_scale_out_trigger_pct=0.80,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=3,
            unrealized_pl_pct_ratio=0.58,
            exit_rule_days_to_expiration=20,
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

        expected_floor = max(
            trailing_profit_config.floor_100_pct,
            max(
                0.25,
                1.10 - self.option_positions._resolve_trailing_giveback_pct(20, trailing_profit_config),
            ),
        )
        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "trailing_profit_stop")
        self.assertAlmostEqual(exit_action["protected_profit_floor_pct"], expected_floor, places=6)
        self.assertEqual(updated_state["last_decision_reason"], "trailing_profit_stop")
        self.log_pass("trailing profit stop sold after gains gave back through the protected floor")

    def _early_floor_exit_action(self, *, protection_trigger_pct: float, initial_floor_pct: float):
        # A position that peaked at +25% and has retraced to +4%: the common live
        # path where a good trade decayed back into a stop-loss before protection
        # ever armed.
        return self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=1,
            unrealized_pl_pct_ratio=0.04,
            exit_rule_days_to_expiration=30,
            hours_to_expiration=720.0,
            exit_thresholds=self.option_positions.ExitThresholds(
                dte_rule_label="30-45 DTE",
                take_profit_pct=70.0,
                stop_loss_pct=-35.0,
                force_exit_days_to_expiration=18,
                exit_hours_to_expiration=432.0,
                is_default_rule=False,
            ),
            position_state={"max_pnl_pct": 0.25},
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=_make_trailing_profit_config(
                self.option_positions,
                protection_trigger_pct=protection_trigger_pct,
                initial_floor_pct=initial_floor_pct,
            ),
            momentum_history_config=_make_momentum_history_config(self.option_positions),
            momentum_status=MOMENTUM_GOOD,
            momentum_reasons=[],
            recently_filled_order=False,
        )

    def test_build_option_position_snapshot_prefers_quote_midpoint_over_broker_pl_for_options(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            option_symbol = _make_future_option_symbol(
                "XOM",
                days_until_expiration=42,
                strike_price=155.0,
            )

            position = SimpleNamespace(
                symbol=option_symbol,
                avg_entry_price="3.25",
                qty="1",
                unrealized_plpc=0.7846,
            )

            original_get_latest_option_quote = self.option_positions._get_latest_option_quote
            original_get_latest_stock_price = self.option_positions._get_latest_stock_price
            try:
                self.option_positions._get_latest_option_quote = lambda _symbol: {
                    "bid_price": 3.67,
                    "ask_price": 3.86,
                    "mid_price": 3.765,
                    "price": 3.765,
                    "timestamp": "2026-05-15T15:30:00Z",
                    "error": "",
                }
                self.option_positions._get_latest_stock_price = lambda _symbol: {
                    "price": 156.215,
                    "timestamp": "2026-05-15T15:30:00Z",
                    "error": "",
                }

                summary = self.option_positions._build_option_position_snapshot(
                    position,
                    settings=_make_exit_settings(self.option_positions, enable_trailing_profit=True),
                    momentum_history_config=_make_momentum_history_config(self.option_positions),
                    state_path=state_path,
                    position_state_override={},
                    pending_reconciliation=None,
                )
            finally:
                self.option_positions._get_latest_option_quote = original_get_latest_option_quote
                self.option_positions._get_latest_stock_price = original_get_latest_stock_price

            expected_unrealized_pl_pct = round(((3.765 - 3.25) / 3.25) * 100.0, 4)
            self.assertEqual(summary["mid_price"], 3.765)
            self.assertEqual(summary["unrealized_pl_pct"], expected_unrealized_pl_pct)
            self.assertNotEqual(summary["unrealized_pl_pct"], round(0.7846 * 100.0, 4))
            self.log_pass("snapshot pricing preferred the live option midpoint over broker-reported option P/L when they disagreed")

    def _flat_position_snapshot(self, *, days_until_expiration: int, position_state: dict) -> dict:
        with TemporaryDirectory() as temp_dir:
            position = SimpleNamespace(
                symbol=_make_future_option_symbol("XOM", days_until_expiration=days_until_expiration, strike_price=155.0),
                avg_entry_price="2.00",
                qty="1",
                unrealized_plpc=0.0,
            )
            original_get_latest_option_quote = self.option_positions._get_latest_option_quote
            original_get_latest_stock_price = self.option_positions._get_latest_stock_price
            try:
                self.option_positions._get_latest_option_quote = lambda _symbol: {
                    "bid_price": 1.98, "ask_price": 2.02, "mid_price": 2.00, "price": 2.00,
                    "timestamp": "2026-05-15T15:30:00Z", "error": "",
                }
                self.option_positions._get_latest_stock_price = lambda _symbol: {
                    "price": 150.0, "timestamp": "2026-05-15T15:30:00Z", "error": "",
                }
                return self.option_positions._build_option_position_snapshot(
                    position,
                    settings=_make_exit_settings(self.option_positions, enable_trailing_profit=False),
                    momentum_history_config=_make_momentum_history_config(self.option_positions),
                    state_path=Path(temp_dir) / "option_state.json",
                    position_state_override=position_state,
                    pending_reconciliation=None,
                )
            finally:
                self.option_positions._get_latest_option_quote = original_get_latest_option_quote
                self.option_positions._get_latest_stock_price = original_get_latest_stock_price

    def test_entry_bucket_forced_exit_fires_after_the_position_ages_out_of_its_bucket(self) -> None:
        # Entered at 25 DTE (20-30 bucket). At 10 DTE no bucket matches the current DTE, which
        # used to fall back to the 1-day default and hold past the bucket's forced exit.
        exit_rules = self.option_positions.build_option_exit_rules(OptionPositionSettings.from_env().dte_rules)
        entry_rule = next(rule for rule in exit_rules
                          if rule.min_days_to_expiration <= 25 <= rule.max_days_to_expiration)
        summary = self._flat_position_snapshot(days_until_expiration=10, position_state={"entry_dte": 25})
        self.assertEqual((summary["dte_exit_rule"], summary["exit_rule_days_to_expiration"], summary["exit_reason"]),
                         (entry_rule.label, 25, "near_expiration"))
        self.log_pass("a position kept its entry bucket's rule and was force-exited at that bucket's DTE")

    def _run_manager_exit(
        self,
        *,
        position: object,
        option_bid: float,
        option_ask: float,
        state_path: Path,
        full_orders: list,
        partial_orders: list,
        underlying_atr_pct: float | None = None,
        trailing_overrides: dict | None = None,
        **settings_overrides,
    ) -> dict:
        class _FakeTradingClient:
            def get_all_positions(self):
                return [position]

        option_history_client = SimpleNamespace(
            get_option_latest_quote=lambda _request: {
                position.symbol: SimpleNamespace(
                    bid_price=option_bid,
                    ask_price=option_ask,
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

        def _fake_full_exit(symbol, limit_price=None):
            full_orders.append((symbol, limit_price))
            return {
                "id": "full-order-1",
                "status": "new",
                "side": "sell",
                "qty": str(getattr(position, "qty", "")),
                "requested_qty": _safe_int(getattr(position, "qty", 0)),
                "submitted_at": datetime.now().isoformat(),
            }

        def _fake_partial_exit(symbol, qty, limit_price=None):
            partial_orders.append((symbol, qty, limit_price))
            return {
                "id": "partial-order-1",
                "status": "new",
                "side": "sell",
                "qty": str(qty),
                "requested_qty": qty,
                "submitted_at": datetime.now().isoformat(),
            }

        original_option_client = self.option_positions.get_option_history_client
        original_stock_client = self.option_positions.get_stock_history_client
        original_get_atr = self.option_positions._get_underlying_atr_pct
        self.option_positions.get_option_history_client = lambda: option_history_client
        self.option_positions.get_stock_history_client = lambda: stock_history_client
        if underlying_atr_pct is not None:
            self.option_positions._get_underlying_atr_pct = lambda _symbol, _settings: underlying_atr_pct
        # Pin every env-driven exit knob to a neutral baseline so these tests are
        # deterministic regardless of the ambient .env (which may enable limit
        # exits, stop-loss confirmation, the liquidity guard, etc.). Individual
        # tests re-enable exactly the feature they exercise via settings_overrides.
        hermetic = replace(
            OptionPositionSettings.from_env(),
            exit_use_limit_orders=False,
            exit_max_spread_pct=0.0,
            stop_loss_confirm_checks=1,
            stop_loss_underlying_move_pct=0.0,
            volatility_scaled_thresholds=False,
        )
        settings = replace(
            hermetic,
            trailing=replace(hermetic.trailing, **(trailing_overrides or {})),
            **settings_overrides,
        )
        try:
            return self.option_positions.ManageCurrentOptionPositions(
                settings=settings,
                state_path=state_path,
                execute_sales=True,
                trading_client_override=_FakeTradingClient(),
                submit_full_exit_order=_fake_full_exit,
                submit_partial_exit_order=_fake_partial_exit,
            )
        finally:
            self.option_positions.get_option_history_client = original_option_client
            self.option_positions.get_stock_history_client = original_stock_client
            self.option_positions._get_underlying_atr_pct = original_get_atr

    def test_manager_prices_passive_partial_scale_out_as_limit_at_mid(self) -> None:
        with TemporaryDirectory() as temp_dir:
            full_orders: list = []
            partial_orders: list = []
            symbol = _make_future_option_symbol("AAPL", days_until_expiration=35)
            position = SimpleNamespace(symbol=symbol, avg_entry_price="1.0", qty="4", unrealized_plpc=None)

            result = self._run_manager_exit(
                position=position,
                option_bid=1.58,
                option_ask=1.62,
                state_path=Path(temp_dir) / "option_state.json",
                full_orders=full_orders,
                partial_orders=partial_orders,
                enable_trailing_profit=True,
                trailing_overrides={"first_scale_out_trigger_pct": 0.55, "first_scale_out_fraction": 0.50},
                exit_use_limit_orders=True,
            )

            summary = result["positions"][0]
            self.assertEqual(summary["exit_action"], "sell_partial")
            self.assertEqual(summary["exit_reason"], "first_scale_out_trigger")
            self.assertEqual(summary["exit_limit_price"], 1.60)
            self.assertEqual(partial_orders, [(symbol, 2, 1.60)])
            self.log_pass("a non-urgent scale-out was submitted as a passive limit order posted at the option midpoint")

    def test_manager_holds_non_urgent_exit_when_spread_is_too_wide(self) -> None:
        with TemporaryDirectory() as temp_dir:
            full_orders: list = []
            partial_orders: list = []
            symbol = _make_future_option_symbol("AAPL", days_until_expiration=35)
            position = SimpleNamespace(symbol=symbol, avg_entry_price="1.0", qty="4", unrealized_plpc=None)

            result = self._run_manager_exit(
                position=position,
                option_bid=1.20,
                option_ask=2.00,
                state_path=Path(temp_dir) / "option_state.json",
                full_orders=full_orders,
                partial_orders=partial_orders,
                enable_trailing_profit=True,
                trailing_overrides={"first_scale_out_trigger_pct": 0.55, "first_scale_out_fraction": 0.50},
                exit_use_limit_orders=True,
                exit_max_spread_pct=0.10,
            )

            summary = result["positions"][0]
            self.assertEqual(summary["exit_action"], "hold")
            self.assertEqual(summary["exit_reason"], "liquidity_guard_wide_spread")
            self.assertEqual(result["partial_sell_count"], 0)
            self.assertEqual(result["close_submitted_count"], 0)
            self.assertEqual(partial_orders, [])
            self.log_pass("a scale-out into a 50%-wide spread was held for the next cycle instead of paying up")

    def test_manager_crosses_to_bid_for_urgent_stop_loss_despite_wide_spread(self) -> None:
        with TemporaryDirectory() as temp_dir:
            full_orders: list = []
            partial_orders: list = []
            symbol = _make_future_option_symbol("AAPL", days_until_expiration=35)
            # A deep loss (entry 3.0, mid 1.60) breaches any per-bucket stop, so the
            # test does not depend on the ambient stop-loss threshold.
            position = SimpleNamespace(symbol=symbol, avg_entry_price="3.0", qty="4", unrealized_plpc="-0.60")

            result = self._run_manager_exit(
                position=position,
                option_bid=1.20,
                option_ask=2.00,
                state_path=Path(temp_dir) / "option_state.json",
                full_orders=full_orders,
                partial_orders=partial_orders,
                exit_use_limit_orders=True,
                exit_max_spread_pct=0.10,
            )

            summary = result["positions"][0]
            self.assertEqual(summary["exit_action"], "sell_full")
            self.assertEqual(summary["exit_reason"], "stop_loss")
            self.assertEqual(summary["exit_limit_price"], 1.20)
            self.assertEqual(full_orders, [(symbol, 1.20)])
            self.log_pass("an urgent stop-loss bypassed the liquidity guard and crossed to the bid as a marketable limit")

    def test_manager_reprices_previously_cancelled_exit_to_the_bid(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            full_orders: list = []
            partial_orders: list = []
            symbol = _make_future_option_symbol("AAPL", days_until_expiration=35)
            position = SimpleNamespace(symbol=symbol, avg_entry_price="1.0", qty="4", unrealized_plpc=None)
            # Simulate a prior passive scale-out limit that was cancelled after
            # going stale; the reconciliation records it as a retryable exit.
            self.option_positions.update_position_state(
                state_path,
                symbol,
                {"last_retryable_exit_reason": "first_scale_out_trigger"},
            )

            result = self._run_manager_exit(
                position=position,
                option_bid=1.55,
                option_ask=1.65,
                state_path=state_path,
                full_orders=full_orders,
                partial_orders=partial_orders,
                enable_trailing_profit=True,
                trailing_overrides={"first_scale_out_trigger_pct": 0.55, "first_scale_out_fraction": 0.50},
                exit_use_limit_orders=True,
            )

            summary = result["positions"][0]
            self.assertEqual(summary["exit_action"], "sell_partial")
            # Second attempt at the same exit crosses to the bid instead of
            # re-posting passively at the 1.60 midpoint.
            self.assertEqual(summary["exit_limit_price"], 1.55)
            self.assertEqual(partial_orders, [(symbol, 2, 1.55)])
            self.log_pass("a scale-out that had already been cancelled once repriced from the midpoint down to the bid")


if __name__ == "__main__":
    unittest.main()
