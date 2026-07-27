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
    return module.OptionPriceMomentumConfig(
        bad_giveback_threshold=_env_float_value(env_values, "OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD", 0.35),
        mixed_giveback_threshold=_env_float_value(env_values, "OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD", 0.15),
        good_profit_threshold=_env_float_value(env_values, "OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD", 0.10),
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
    return module.TrailingProfitConfig(
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
    return module.MomentumHistoryConfig(
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
        cls.option_positions.DEFAULT_OPTION_PENDING_EXIT_STALE_MINUTES = 1.0
        cls.option_positions.DEFAULT_OPTION_PENDING_EXIT_CANCEL_ON_STALE = True

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
                pending_exit_config=self.option_positions.PendingExitConfig(
                    stale_minutes=10.0,
                    cancel_on_stale=True,
                ),
            )

            self.assertEqual(reconciliation["pending_order_status"], "filled")
            self.assertTrue(reconciliation["pending_order_terminal"])
            self.assertEqual(persisted_state["pending_order_id"], "")
            self.assertTrue(persisted_state["took_first_scale_out"])
            self.log_pass("filled Alpaca enum statuses clear pending exits instead of blocking trailing protection")

    def test_pending_order_status_policy_covers_every_supported_alpaca_status(self) -> None:
        expected_statuses = {
            "new",
            "partially_filled",
            "filled",
            "done_for_day",
            "canceled",
            "cancelled",
            "expired",
            "replaced",
            "pending_cancel",
            "pending_replace",
            "pending_review",
            "accepted",
            "pending_new",
            "accepted_for_bidding",
            "stopped",
            "rejected",
            "suspended",
            "calculated",
            "held",
        }
        covered_statuses = (
            self.option_positions.PENDING_ORDER_OPEN_STATUSES
            | self.option_positions.PENDING_ORDER_CLEAR_STATUSES
            | {"replaced"}
        )
        self.assertSetEqual(expected_statuses, covered_statuses)
        self.log_pass("pending-exit lifecycle has an explicit policy for every Alpaca order status")

    def test_reconcile_pending_review_and_suspended_orders_can_be_cancelled_when_stale(self) -> None:
        for status in ("OrderStatus.PENDING_REVIEW", "OrderStatus.SUSPENDED"):
            with self.subTest(status=status), TemporaryDirectory() as temp_dir:
                state_path = Path(temp_dir) / "option_state.json"
                submitted_at = (datetime.now() - timedelta(minutes=5)).isoformat()
                self.option_positions.update_position_state(
                    state_path,
                    OPTION_SYMBOL,
                    {
                        "pending_order_id": "order-stale",
                        "pending_qty": 1,
                        "pending_reason": "trailing_profit_stop",
                        "pending_action": "sell_full",
                        "pending_order_submitted_at": submitted_at,
                    },
                )
                cancelled_order_ids: list[str] = []
                persisted_state, reconciliation = self.option_positions._reconcile_pending_order_state(
                    state_path=state_path,
                    option_symbol=OPTION_SYMBOL,
                    position_state=self.option_positions.get_position_state(state_path, OPTION_SYMBOL),
                    resolve_order_status=lambda order_id, status=status: {
                        "id": order_id,
                        "status": status,
                        "submitted_at": submitted_at,
                        "qty": "1",
                        "requested_qty": 1,
                    },
                    cancel_order=lambda order_id: cancelled_order_ids.append(order_id)
                    or {"id": order_id, "status": "OrderStatus.CANCELED", "qty": "1", "requested_qty": 1},
                    broker_quantity=1,
                    pending_exit_config=self.option_positions.PendingExitConfig(
                        stale_minutes=1.0,
                        cancel_on_stale=True,
                    ),
                )
                self.assertEqual(cancelled_order_ids, ["order-stale"])
                self.assertTrue(reconciliation["pending_order_terminal"])
                self.assertEqual(persisted_state["pending_order_id"], "")

    def test_reconcile_done_for_day_clears_pending_exit_for_retry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            submitted_at = datetime.now().isoformat()
            self.option_positions.update_position_state(
                state_path,
                OPTION_SYMBOL,
                {
                    "pending_order_id": "order-done",
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
                    "status": "OrderStatus.DONE_FOR_DAY",
                    "submitted_at": submitted_at,
                    "qty": "1",
                    "requested_qty": 1,
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
            self.assertFalse(persisted_state["took_first_scale_out"])

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
                pending_exit_config=self.option_positions.PendingExitConfig(
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

    def test_option_price_momentum_thresholds_are_configurable(self) -> None:
        strict_config = _make_option_price_momentum_config(
            self.option_positions,
            bad_giveback_threshold=0.50,
            mixed_giveback_threshold=0.30,
            good_profit_threshold=0.25,
        )

        momentum = self.option_positions.evaluate_option_price_momentum(
            entry_option_price=1.0,
            current_option_mid_price=1.18,
            unrealized_pl_ratio=0.18,
            max_pnl_pct=0.50,
            config=strict_config,
        )

        self.assertEqual(momentum["status"], self.option_positions.MOMENTUM_MIXED)
        self.assertAlmostEqual(momentum["giveback_from_peak"], 0.32, places=6)
        self.log_pass("option-price momentum classification respected the configured giveback and good-profit thresholds")

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

    def test_structured_exit_action_does_not_append_momentum_history_while_pending_order_exists(self) -> None:
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
                "pending_order_id": "order-123",
                "max_pnl_pct": 2.20,
                "momentum_tracking_active": True,
                "momentum_history_sample_count": 4,
                "momentum_bad_count": 2,
                "momentum_mixed_count": 1,
                "momentum_negative_score": 2.5,
                "momentum_consecutive_bad_count": 1,
                "momentum_history": [
                    {"status": "bad", "checked_at": "2026-05-14T10:00:00", "pnl_pct": 2.10, "max_pnl_pct": 2.20, "negative_score": 1.0},
                    {"status": "mixed", "checked_at": "2026-05-14T10:05:00", "pnl_pct": 2.08, "max_pnl_pct": 2.20, "negative_score": 0.5},
                    {"status": "bad", "checked_at": "2026-05-14T10:10:00", "pnl_pct": 2.06, "max_pnl_pct": 2.20, "negative_score": 1.0},
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
            momentum_reasons=["Momentum deteriorated while an exit order was already working."],
            recently_filled_order=False,
        )

        self.assertEqual(exit_action["action"], "hold")
        self.assertEqual(exit_action["reason"], "pending_exit_order")
        self.assertEqual(updated_state["momentum_history_sample_count"], 4)
        self.assertEqual(updated_state["momentum_negative_score"], 2.5)
        self.assertEqual(len(updated_state["momentum_history"]), 3)
        self.log_pass("momentum history stayed frozen while a pending exit order was already blocking new actions")

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
            broker_unrealized_pl_pct_ratio=-0.35,
        )

        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "stop_loss")
        self.assertIn("Underlying stock quote was unavailable", exit_action["notes"][0])
        self.log_pass("stop-loss still fired when only a noncritical underlying-quote note was present")

    def test_structured_exit_action_ignores_midpoint_loss_for_stop_loss_when_broker_pl_is_above_floor(self) -> None:
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

    def test_stop_loss_fires_when_underlying_confirms_adverse_move(self) -> None:
        exit_action, updated_state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state={},
            stop_loss_confirm_checks=1,
            underlying_adverse_move_pct=0.025,
            stop_loss_underlying_move_pct=0.015,
        )
        self.assertEqual(exit_action["action"], "sell_full")
        self.assertEqual(exit_action["reason"], "stop_loss")
        self.log_pass("premium stop-loss fired once the underlying itself moved past the adverse-move threshold")

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

    def test_stop_loss_underlying_gate_composes_with_confirmation_checks(self) -> None:
        state: dict = {}

        pending_action, state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state=state,
            stop_loss_confirm_checks=2,
            underlying_adverse_move_pct=0.030,
            stop_loss_underlying_move_pct=0.015,
        )
        self.assertEqual(pending_action["action"], "hold")
        self.assertEqual(pending_action["reason"], "stop_loss_pending_confirmation")
        self.assertEqual(state["stop_loss_breach_count"], 1)

        confirmed_action, state = self._run_stop_loss_exit_action(
            broker_unrealized_pl_pct_ratio=-0.35,
            position_state=state,
            stop_loss_confirm_checks=2,
            underlying_adverse_move_pct=0.030,
            stop_loss_underlying_move_pct=0.015,
        )
        self.assertEqual(confirmed_action["action"], "sell_full")
        self.assertEqual(confirmed_action["reason"], "stop_loss")
        self.log_pass("an underlying-confirmed breach still had to clear the consecutive-check debounce before the full exit fired")

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

    def test_compute_atr_pct_averages_true_range_over_the_window(self) -> None:
        compute = self.option_positions._compute_atr_pct
        bars = [
            {"high": 10, "low": 9, "close": 10},
            {"high": 12, "low": 10, "close": 11},   # TR = 2
            {"high": 16, "low": 11, "close": 15},   # TR = 5
        ]
        # Full window averages both true ranges: (2 + 5) / 2 = 3.5, over close 15.
        self.assertAlmostEqual(compute(bars, period=14), 3.5 / 15.0, places=6)
        # A period of 1 keeps only the most recent true range: 5 / 15.
        self.assertAlmostEqual(compute(bars, period=1), 5.0 / 15.0, places=6)
        # Too few bars yields None.
        self.assertIsNone(compute([{"high": 10, "low": 9, "close": 10}], period=14))
        self.assertIsNone(compute([], period=14))
        self.log_pass("ATR% averaged the true range over the requested window and needed at least two bars")

    def test_resolve_volatility_scale_clamps_ratio_and_falls_back_to_one(self) -> None:
        resolve = self.option_positions._resolve_volatility_scale
        # ATR twice the baseline doubles the thresholds.
        self.assertEqual(resolve(current_atr_pct=0.04, baseline_atr_pct=0.02, min_scale=0.5, max_scale=2.0), 2.0)
        # Very high vol is clamped to the max.
        self.assertEqual(resolve(current_atr_pct=0.10, baseline_atr_pct=0.02, min_scale=0.5, max_scale=2.0), 2.0)
        # Very low vol is clamped to the min.
        self.assertEqual(resolve(current_atr_pct=0.005, baseline_atr_pct=0.02, min_scale=0.5, max_scale=2.0), 0.5)
        # Missing or invalid inputs fall back to no scaling.
        self.assertEqual(resolve(current_atr_pct=None, baseline_atr_pct=0.02, min_scale=0.5, max_scale=2.0), 1.0)
        self.assertEqual(resolve(current_atr_pct=0.04, baseline_atr_pct=0.0, min_scale=0.5, max_scale=2.0), 1.0)
        self.log_pass("volatility scale clamped the ATR ratio and fell back to 1.0 when the inputs were unusable")

    def test_structured_exit_action_ignores_midpoint_profit_for_scale_out_when_broker_pl_is_below_trigger(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            protection_trigger_pct=0.25,
            first_scale_out_trigger_pct=0.55,
            first_scale_out_fraction=0.50,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=4,
            unrealized_pl_pct_ratio=0.80,
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
            broker_unrealized_pl_pct_ratio=0.30,
        )

        self.assertEqual(exit_action["action"], "hold")
        self.assertEqual(exit_action["reason"], "hold")
        self.assertFalse(updated_state["took_first_scale_out"])
        self.assertEqual(updated_state["max_pnl_pct"], 0.30)
        self.log_pass("first scale-out ignored a midpoint-only profit spike when broker P/L stayed below the trigger")

    def test_structured_exit_action_holds_when_profit_protection_starts_at_take_profit_level(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            protection_trigger_pct=0.40,
            initial_floor_pct=0.05,
            dynamic_giveback_start_pct=0.75,
            first_scale_out_trigger_pct=0.55,
            first_scale_out_fraction=0.50,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=4,
            unrealized_pl_pct_ratio=0.80,
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
            broker_unrealized_pl_pct_ratio=0.40,
        )

        self.assertEqual(exit_action["action"], "hold")
        self.assertEqual(exit_action["reason"], "hold")
        self.assertTrue(updated_state["profit_protection_active"])
        self.assertEqual(updated_state["protected_profit_floor_pct"], 0.05)
        self.assertFalse(updated_state["took_first_scale_out"])
        self.assertEqual(updated_state["max_pnl_pct"], 0.40)
        self.log_pass("profit protection activated at the take-profit threshold without forcing a midpoint-only full exit")

    def test_env_style_trailing_profit_configs_do_not_force_full_exit_above_protected_floor(self) -> None:
        scenarios = (
            {
                "label": "protection arms before first scale-out",
                "env_values": {
                    "OPTION_POSITION_ENABLE_MOMENTUM_EXIT": "true",
                    "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT": "2.00",
                    "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE": "10",
                    "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES": "5",
                    "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD": "6",
                    "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD": "3",
                    "OPTION_TRAIL_PROTECTION_TRIGGER_PCT": "0.54",
                    "OPTION_TRAIL_INITIAL_FLOOR_PCT": "0.40",
                    "OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT": "0.70",
                    "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT": "0.70",
                    "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION": "0.50",
                    "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT": "2.00",
                    "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION": "0.25",
                    "OPTION_TRAIL_12_20_GIVEBACK_PCT": "0.20",
                    "OPTION_TRAIL_20_30_GIVEBACK_PCT": "0.30",
                    "OPTION_TRAIL_30_45_GIVEBACK_PCT": "0.35",
                    "OPTION_TRAIL_45_60_GIVEBACK_PCT": "0.35",
                    "OPTION_TRAIL_100_FLOOR_PCT": "0.75",
                    "OPTION_TRAIL_150_FLOOR_PCT": "1.30",
                    "OPTION_TRAIL_200_FLOOR_PCT": "1.70",
                    "OPTION_POSITION_12_20_DTE_TAKE_PROFIT_PCT": "35",
                    "OPTION_POSITION_12_20_DTE_STOP_LOSS_PCT": "-28",
                },
                "quantity": 4,
                "trigger_pl": 0.54,
                "expected_trigger_action": "hold",
                "trigger_state_updates": {},
                "follow_up_quantity": 4,
                "follow_up_state_updates": {},
            },
            {
                "label": "first scale-out shares the protection trigger",
                "env_values": {
                    "OPTION_POSITION_ENABLE_MOMENTUM_EXIT": "true",
                    "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT": "2.00",
                    "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE": "10",
                    "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES": "5",
                    "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD": "6",
                    "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD": "3",
                    "OPTION_TRAIL_PROTECTION_TRIGGER_PCT": "0.55",
                    "OPTION_TRAIL_INITIAL_FLOOR_PCT": "0.40",
                    "OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT": "0.70",
                    "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT": "0.55",
                    "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION": "0.50",
                    "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT": "2.00",
                    "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION": "0.25",
                    "OPTION_TRAIL_12_20_GIVEBACK_PCT": "0.20",
                    "OPTION_TRAIL_20_30_GIVEBACK_PCT": "0.30",
                    "OPTION_TRAIL_30_45_GIVEBACK_PCT": "0.35",
                    "OPTION_TRAIL_45_60_GIVEBACK_PCT": "0.35",
                    "OPTION_TRAIL_100_FLOOR_PCT": "0.75",
                    "OPTION_TRAIL_150_FLOOR_PCT": "1.30",
                    "OPTION_TRAIL_200_FLOOR_PCT": "1.70",
                    "OPTION_POSITION_12_20_DTE_TAKE_PROFIT_PCT": "35",
                    "OPTION_POSITION_12_20_DTE_STOP_LOSS_PCT": "-28",
                },
                "quantity": 4,
                "trigger_pl": 0.55,
                "expected_trigger_action": "sell_partial",
                "trigger_state_updates": {"took_first_scale_out": True},
                "follow_up_quantity": 2,
                "follow_up_state_updates": {"took_first_scale_out": True},
            },
        )

        for scenario in scenarios:
            with self.subTest(scenario=scenario["label"]):
                env_values = scenario["env_values"]
                trailing_profit_config = _make_trailing_profit_config_from_env_values(self.option_positions, env_values)
                momentum_history_config = _make_momentum_history_config_from_env_values(self.option_positions, env_values)
                exit_thresholds = self._build_env_exit_thresholds(env_values, bucket_key="12_20", label="12-20 DTE")
                protected_floor = trailing_profit_config.initial_floor_pct

                trigger_action, trigger_state = self.option_positions._structured_exit_action(
                    option_symbol=OPTION_SYMBOL,
                    quantity=scenario["quantity"],
                    unrealized_pl_pct_ratio=0.80,
                    days_to_expiration=15,
                    hours_to_expiration=300.0,
                    exit_thresholds=exit_thresholds,
                    position_state={},
                    critical_errors=[],
                    context_notes=[],
                    enable_trailing_profit=True,
                    trailing_profit_config=trailing_profit_config,
                    momentum_history_config=momentum_history_config,
                    momentum_status=MOMENTUM_UNKNOWN,
                    momentum_reasons=[],
                    recently_filled_order=False,
                    broker_unrealized_pl_pct_ratio=scenario["trigger_pl"],
                )
                self.assertEqual(trigger_action["action"], scenario["expected_trigger_action"])
                self.assertNotEqual(trigger_action["action"], "sell_full")
                self.assertTrue(trigger_state["profit_protection_active"])
                self.assertEqual(trigger_action["protected_profit_floor_pct"], protected_floor)
                self.assertEqual(trigger_state["protected_profit_floor_pct"], protected_floor)

                hold_action, hold_state = self.option_positions._structured_exit_action(
                    option_symbol=OPTION_SYMBOL,
                    quantity=scenario["follow_up_quantity"],
                    unrealized_pl_pct_ratio=0.60,
                    days_to_expiration=15,
                    hours_to_expiration=300.0,
                    exit_thresholds=exit_thresholds,
                    position_state={
                        "profit_protection_active": True,
                        "protected_profit_floor_pct": protected_floor,
                        "max_pnl_pct": scenario["trigger_pl"],
                        **scenario["follow_up_state_updates"],
                    },
                    critical_errors=[],
                    context_notes=[],
                    enable_trailing_profit=True,
                    trailing_profit_config=trailing_profit_config,
                    momentum_history_config=momentum_history_config,
                    momentum_status=MOMENTUM_UNKNOWN,
                    momentum_reasons=[],
                    recently_filled_order=False,
                    broker_unrealized_pl_pct_ratio=0.45,
                )
                self.assertEqual(hold_action["action"], "hold")
                self.assertEqual(hold_state["protected_profit_floor_pct"], protected_floor)

                stop_action, _ = self.option_positions._structured_exit_action(
                    option_symbol=OPTION_SYMBOL,
                    quantity=scenario["follow_up_quantity"],
                    unrealized_pl_pct_ratio=0.39,
                    days_to_expiration=15,
                    hours_to_expiration=300.0,
                    exit_thresholds=exit_thresholds,
                    position_state={
                        "profit_protection_active": True,
                        "protected_profit_floor_pct": protected_floor,
                        "max_pnl_pct": scenario["trigger_pl"],
                        **scenario["follow_up_state_updates"],
                    },
                    critical_errors=[],
                    context_notes=[],
                    enable_trailing_profit=True,
                    trailing_profit_config=trailing_profit_config,
                    momentum_history_config=momentum_history_config,
                    momentum_status=MOMENTUM_UNKNOWN,
                    momentum_reasons=[],
                    recently_filled_order=False,
                    broker_unrealized_pl_pct_ratio=0.39,
                )
                self.assertEqual(stop_action["action"], "sell_full")
                self.assertEqual(stop_action["reason"], "trailing_profit_stop")

        self.log_pass("env-style trailing-profit configurations can arm protection and scale out, but they never force a full exit above the protected floor")

    def test_env_style_momentum_exit_requires_minimum_samples_before_full_exit(self) -> None:
        env_values = {
            "OPTION_POSITION_ENABLE_MOMENTUM_EXIT": "true",
            "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT": "2.00",
            "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE": "10",
            "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES": "5",
            "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD": "3",
            "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD": "2",
            "OPTION_TRAIL_PROTECTION_TRIGGER_PCT": "0.55",
            "OPTION_TRAIL_INITIAL_FLOOR_PCT": "0.40",
            "OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT": "0.70",
            "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT": "0.80",
            "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION": "0.50",
            "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT": "2.00",
            "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION": "0.25",
            "OPTION_TRAIL_12_20_GIVEBACK_PCT": "0.20",
            "OPTION_TRAIL_20_30_GIVEBACK_PCT": "0.30",
            "OPTION_TRAIL_30_45_GIVEBACK_PCT": "0.35",
            "OPTION_TRAIL_45_60_GIVEBACK_PCT": "0.35",
            "OPTION_TRAIL_100_FLOOR_PCT": "0.75",
            "OPTION_TRAIL_150_FLOOR_PCT": "1.30",
            "OPTION_TRAIL_200_FLOOR_PCT": "1.70",
            "OPTION_POSITION_12_20_DTE_TAKE_PROFIT_PCT": "35",
            "OPTION_POSITION_12_20_DTE_STOP_LOSS_PCT": "-28",
        }
        trailing_profit_config = _make_trailing_profit_config_from_env_values(self.option_positions, env_values)
        momentum_history_config = _make_momentum_history_config_from_env_values(self.option_positions, env_values)
        exit_thresholds = self._build_env_exit_thresholds(env_values, bucket_key="12_20", label="12-20 DTE")

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=0.62,
            days_to_expiration=15,
            hours_to_expiration=300.0,
            exit_thresholds=exit_thresholds,
            position_state={
                "profit_protection_active": True,
                "protected_profit_floor_pct": 0.40,
                "max_pnl_pct": 0.62,
                "momentum_history": [
                    {"status": "bad", "max_pnl_pct": 0.62},
                    {"status": "bad", "max_pnl_pct": 0.62},
                ],
            },
            critical_errors=[],
            context_notes=[],
            enable_trailing_profit=True,
            trailing_profit_config=trailing_profit_config,
            momentum_history_config=momentum_history_config,
            momentum_status="bad",
            momentum_reasons=["Momentum slipped, but the configured minimum sample count has not been met yet."],
            recently_filled_order=False,
            broker_unrealized_pl_pct_ratio=0.45,
        )
        self.assertEqual(exit_action["action"], "hold")
        self.assertEqual(updated_state["last_decision_reason"], "hold")
        self.assertEqual(updated_state["protected_profit_floor_pct"], 0.40)
        self.log_pass("env-style momentum settings cannot force a full exit before the configured minimum sample count is reached")

    def test_structured_exit_action_uses_broker_pl_for_trailing_profit_stop(self) -> None:
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
            broker_unrealized_pl_pct_ratio=0.70,
        )

        self.assertEqual(exit_action["action"], "hold")
        self.assertEqual(exit_action["reason"], "hold")
        self.assertEqual(updated_state["last_decision_reason"], "hold")
        self.log_pass("trailing profit stop ignored a midpoint-only giveback while broker P/L stayed above the protected floor")

    def test_structured_exit_action_uses_broker_pl_for_static_take_profit(self) -> None:
        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=2,
            unrealized_pl_pct_ratio=0.80,
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
        bucket_values = {
            bucket.key: round(0.11 * (index + 1), 2)
            for index, bucket in enumerate(self.option_positions.OPTION_DTE_BUCKETS)
        }
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            giveback_pct_by_bucket_key=bucket_values,
        )

        buckets = self.option_positions.OPTION_DTE_BUCKETS
        for index, bucket in enumerate(buckets):
            next_bucket = buckets[index + 1] if index + 1 < len(buckets) else None
            unique_upper_day = bucket.max_days
            if next_bucket is not None and next_bucket.min_days <= bucket.max_days:
                unique_upper_day = next_bucket.min_days - 1

            self.assertEqual(
                self.option_positions._resolve_trailing_giveback_pct(bucket.min_days, trailing_profit_config),
                bucket_values[bucket.key],
            )
            if unique_upper_day >= bucket.min_days:
                self.assertEqual(
                    self.option_positions._resolve_trailing_giveback_pct(unique_upper_day, trailing_profit_config),
                    bucket_values[bucket.key],
                )

        for index, bucket in enumerate(buckets[1:], start=1):
            previous_bucket = buckets[index - 1]
            if bucket.min_days <= previous_bucket.max_days:
                self.assertEqual(
                    self.option_positions._resolve_trailing_giveback_pct(bucket.min_days, trailing_profit_config),
                    bucket_values[bucket.key],
                )
        self.log_pass("trailing giveback buckets matched the registry for unique ranges and favored the higher bucket on shared boundaries")

    def test_exit_thresholds_use_registry_defined_15_to_19_dte_range(self) -> None:
        thresholds = self.option_positions._resolve_option_exit_thresholds(
            days_to_expiration=19,
            default_take_profit_pct=25.0,
            default_stop_loss_pct=-20.0,
            default_exit_hours_to_expiration=24.0,
        )

        resolved_rule = next(
            rule
            for rule in self.option_positions.OPTION_EXIT_DTE_RULES
            if rule.min_days_to_expiration <= 19 <= rule.max_days_to_expiration
        )
        expected_exit_hours = (
            float(resolved_rule.force_exit_days_to_expiration * 24)
            if resolved_rule.force_exit_days_to_expiration is not None
            else 24.0
        )

        self.assertEqual(thresholds.dte_rule_label, resolved_rule.label)
        self.assertEqual(thresholds.take_profit_pct, resolved_rule.take_profit_pct)
        self.assertEqual(thresholds.stop_loss_pct, resolved_rule.stop_loss_pct)
        self.assertEqual(thresholds.exit_hours_to_expiration, expected_exit_hours)
        self.assertFalse(thresholds.is_default_rule)
        self.log_pass("19 DTE positions resolved through the current bucket registry without any special-case bridge logic")

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
        self.assertEqual(updated_state["protected_profit_floor_pct"], 0.05)
        expected_bucket_key = self._trailing_giveback_bucket_key_for_days(10)
        self.assertEqual(
            updated_state["trailing_giveback_pct"],
            trailing_profit_config.giveback_pct_by_bucket_key[expected_bucket_key],
        )
        self.assertIn("Profit protection activated.", exit_action["notes"])
        self.log_pass("profit protection activated once gains crossed the configured trigger")

    def test_structured_exit_action_scale_out_does_not_start_dynamic_giveback_before_threshold(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            protection_trigger_pct=0.40,
            initial_floor_pct=0.05,
            dynamic_giveback_start_pct=0.75,
            first_scale_out_trigger_pct=0.55,
            first_scale_out_fraction=0.50,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=4,
            unrealized_pl_pct_ratio=0.60,
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

        self.assertEqual(exit_action["action"], "sell_partial")
        self.assertEqual(exit_action["reason"], "first_scale_out_trigger")
        self.assertEqual(exit_action["qty_to_sell"], 2)
        self.assertEqual(exit_action["protected_profit_floor_pct"], 0.05)
        self.assertEqual(updated_state["protected_profit_floor_pct"], 0.05)
        self.log_pass("first scale-out fired at the split trigger without prematurely tightening the protected floor")

    def test_structured_exit_action_starts_dynamic_giveback_only_after_threshold(self) -> None:
        trailing_profit_config = _make_trailing_profit_config(
            self.option_positions,
            protection_trigger_pct=0.40,
            initial_floor_pct=0.05,
            dynamic_giveback_start_pct=0.75,
            first_scale_out_trigger_pct=0.55,
        )

        exit_action, updated_state = self.option_positions._structured_exit_action(
            option_symbol=OPTION_SYMBOL,
            quantity=1,
            unrealized_pl_pct_ratio=0.80,
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

        expected_floor = max(
            0.25,
            0.80
            - trailing_profit_config.giveback_pct_by_bucket_key[
                self._trailing_giveback_bucket_key_for_days(10)
            ],
        )
        self.assertEqual(exit_action["action"], "hold")
        self.assertAlmostEqual(exit_action["protected_profit_floor_pct"], expected_floor, places=6)
        self.assertAlmostEqual(updated_state["protected_profit_floor_pct"], expected_floor, places=6)
        self.log_pass("dynamic giveback trailing stayed dormant before the split threshold and tightened the floor only after 75 percent profit")

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

    def test_build_option_position_snapshot_persists_momentum_history_after_threshold(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            option_symbol = _make_future_option_symbol("AAPL", days_until_expiration=35)

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
                        "entry_underlying_price_source": "manual",
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

    def test_build_option_position_snapshot_treats_untrusted_underlying_baseline_as_informational_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            option_symbol = _make_future_option_symbol("AAPL", days_until_expiration=35)

            position = SimpleNamespace(
                symbol=option_symbol,
                avg_entry_price="1.0",
                qty="2",
                unrealized_plpc=0.60,
            )

            original_get_latest_option_quote = self.option_positions._get_latest_option_quote
            original_get_latest_stock_price = self.option_positions._get_latest_stock_price
            try:
                self.option_positions._get_latest_option_quote = lambda _symbol: {
                    "bid_price": 1.58,
                    "ask_price": 1.62,
                    "mid_price": 1.60,
                    "price": 1.60,
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
                    momentum_history_config=_make_momentum_history_config(self.option_positions),
                    state_path=state_path,
                    position_state_override={},
                    pending_reconciliation=None,
                )
            finally:
                self.option_positions._get_latest_option_quote = original_get_latest_option_quote
                self.option_positions._get_latest_stock_price = original_get_latest_stock_price

            persisted_state = self.option_positions.get_position_state(state_path, option_symbol)
            assert persisted_state is not None

            self.assertEqual(summary["underlying_momentum_status"], MOMENTUM_UNKNOWN)
            self.assertTrue(summary["underlying_momentum_informational_only"])
            self.assertEqual(summary["option_price_momentum_status"], MOMENTUM_GOOD)
            self.assertEqual(summary["momentum_status"], MOMENTUM_GOOD)
            self.assertNotIn("entry_underlying_price", persisted_state)
            self.assertEqual(persisted_state["first_observed_underlying_price"], 120.0)
            self.log_pass("missing or untrusted underlying baselines stayed informational-only while option-price momentum continued to drive the combined signal")

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
                    take_profit_pct=25.0,
                    stop_loss_pct=-20.0,
                    exit_hours_to_expiration=24.0,
                    enable_trailing_profit=True,
                    trailing_profit_dry_run=False,
                    trailing_profit_config=_make_trailing_profit_config(self.option_positions),
                    momentum_history_config=_make_momentum_history_config(self.option_positions),
                    option_price_momentum_config=_make_option_price_momentum_config(self.option_positions),
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

    def test_manage_current_option_positions_submits_partial_scale_out_for_trailing_profit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "option_state.json"
            partial_orders: list[tuple[str, int]] = []
            future_option_symbol = _make_future_option_symbol("AAPL", days_until_expiration=35)

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
                    trail_first_scale_out_trigger_pct_override=0.55,
                    trail_first_scale_out_fraction_override=0.50,
                    exit_use_limit_orders_override=False,
                    trading_client_override=_FakeTradingClient(),
                    submit_partial_exit_order=lambda symbol, qty, limit_price=None: partial_orders.append((symbol, qty, limit_price))
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
            self.assertEqual(partial_orders, [(future_option_symbol, 2, None)])

            summary = result["positions"][0]
            self.assertEqual(summary["exit_action"], "sell_partial")
            self.assertEqual(summary["exit_reason"], "first_scale_out_trigger")
            self.assertEqual(summary["qty_to_sell"], 2)
            self.assertTrue(summary["close_submitted"])
            self.assertTrue(summary["took_first_scale_out"])
            self.log_pass("position manager submitted the first partial scale-out order for a trailing-profit trigger")

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
        **manage_kwargs,
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
            self.option_positions._get_underlying_atr_pct = lambda _symbol: underlying_atr_pct
        # Pin every env-driven exit knob to a neutral baseline so these tests are
        # deterministic regardless of the ambient .env (which may enable limit
        # exits, stop-loss confirmation, the liquidity guard, etc.). Individual
        # tests re-enable exactly the feature they exercise via manage_kwargs.
        hermetic_overrides = dict(
            exit_use_limit_orders_override=False,
            exit_max_spread_pct_override=0.0,
            stop_loss_confirm_checks_override=1,
            stop_loss_underlying_move_pct_override=0.0,
            volatility_scaled_thresholds_override=False,
        )
        try:
            return self.option_positions.ManageCurrentOptionPositions(
                execute_sales=True,
                state_path_override=state_path,
                trading_client_override=_FakeTradingClient(),
                submit_full_exit_order=_fake_full_exit,
                submit_partial_exit_order=_fake_partial_exit,
                **{**hermetic_overrides, **manage_kwargs},
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
                enable_trailing_profit_override=True,
                trail_first_scale_out_trigger_pct_override=0.55,
                trail_first_scale_out_fraction_override=0.50,
                exit_use_limit_orders_override=True,
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
                enable_trailing_profit_override=True,
                trail_first_scale_out_trigger_pct_override=0.55,
                trail_first_scale_out_fraction_override=0.50,
                exit_use_limit_orders_override=True,
                exit_max_spread_pct_override=0.10,
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
                exit_use_limit_orders_override=True,
                exit_max_spread_pct_override=0.10,
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
                enable_trailing_profit_override=True,
                trail_first_scale_out_trigger_pct_override=0.55,
                trail_first_scale_out_fraction_override=0.50,
                exit_use_limit_orders_override=True,
            )

            summary = result["positions"][0]
            self.assertEqual(summary["exit_action"], "sell_partial")
            # Second attempt at the same exit crosses to the bid instead of
            # re-posting passively at the 1.60 midpoint.
            self.assertEqual(summary["exit_limit_price"], 1.55)
            self.assertEqual(partial_orders, [(symbol, 2, 1.55)])
            self.log_pass("a scale-out that had already been cancelled once repriced from the midpoint down to the bid")

    def test_manager_widens_stop_loss_for_high_volatility_underlying(self) -> None:
        with TemporaryDirectory() as temp_dir:
            symbol = _make_future_option_symbol("AAPL", days_until_expiration=35)
            # A small loss that fires no exit, so both runs just report the resolved
            # thresholds; the test asserts the scaling math rather than a decision
            # that would depend on the ambient per-bucket stop value.
            position = SimpleNamespace(symbol=symbol, avg_entry_price="1.0", qty="4", unrealized_plpc="-0.05")

            base = self._run_manager_exit(
                position=position,
                option_bid=0.90,
                option_ask=0.95,
                state_path=Path(temp_dir) / "base_state.json",
                full_orders=[],
                partial_orders=[],
            )["positions"][0]

            # ATR 3x the baseline -> raw scale 3.0, clamped to the 2.0 maximum.
            scaled = self._run_manager_exit(
                position=position,
                option_bid=0.90,
                option_ask=0.95,
                state_path=Path(temp_dir) / "scaled_state.json",
                full_orders=[],
                partial_orders=[],
                underlying_atr_pct=0.06,
                volatility_scaled_thresholds_override=True,
                volatility_baseline_atr_pct_override=0.02,
                volatility_min_scale_override=0.5,
                volatility_max_scale_override=2.0,
            )["positions"][0]

            self.assertEqual(base["volatility_scale"], 1.0)
            self.assertEqual(scaled["volatility_scale"], 2.0)
            self.assertEqual(scaled["underlying_atr_pct"], 0.06)
            # Both the stop-loss and take-profit thresholds scaled 2x off the base.
            self.assertEqual(scaled["stop_loss_pct"], round(base["stop_loss_pct"] * 2.0, 4))
            self.assertEqual(scaled["take_profit_pct"], round(base["take_profit_pct"] * 2.0, 4))
            self.log_pass("a high-ATR underlying doubled (clamped) the stop-loss and take-profit thresholds through the manager")

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
