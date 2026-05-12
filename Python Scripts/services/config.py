from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from services.common import env_flag, env_percentage, env_positive_int, load_exit_hours_to_expiration
from services.runtime_paths import DATA_DIR, LOGS_DIR, ROOT_DIR


def _env_float(name: str, default: float) -> float:
    raw_value = str(os.getenv(name, str(default))).strip()
    try:
        return float(raw_value)
    except ValueError:
        return float(default)


@dataclass(frozen=True)
class FrontMainPaths:
    """Filesystem paths used by the front-facing trading workflow."""

    root_dir: Path
    data_dir: Path
    logs_dir: Path
    status_path: Path
    trade_output_path: Path
    option_position_management_output_path: Path
    option_position_state_path: Path
    agent_output_path: Path
    selected_options_output_path: Path
    combined_output_path: Path

    @classmethod
    def from_env(cls) -> FrontMainPaths:
        return cls(
            root_dir=ROOT_DIR,
            data_dir=DATA_DIR,
            logs_dir=LOGS_DIR,
            status_path=Path(
                os.getenv("SCRIPT_STATUS_PATH", str(ROOT_DIR / "web_dashboard" / "public" / "script_status.json"))
            ),
            trade_output_path=Path(
                os.getenv(
                    "TRADE_EXECUTION_OUTPUT_PATH",
                    str(DATA_DIR / "trade_execution_output.json"),
                )
            ),
            option_position_management_output_path=Path(
                os.getenv(
                    "OPTION_POSITION_MANAGEMENT_OUTPUT_PATH",
                    str(DATA_DIR / "option_position_management_output.json"),
                )
            ),
            option_position_state_path=Path(
                os.getenv(
                    "OPTION_POSITION_STATE_PATH",
                    str(ROOT_DIR / "shared" / "option_position_state.json"),
                )
            ),
            agent_output_path=DATA_DIR / "agent_runner_output.json",
            selected_options_output_path=DATA_DIR / "selected_options_output.json",
            combined_output_path=DATA_DIR / "front_main_output.json",
        )


@dataclass(frozen=True)
class FrontMainSettings:
    """Runtime configuration for trading cycles and loop scheduling."""

    default_option_order_qty: int
    option_contract_multiplier: int
    run_interval_seconds: int
    market_recheck_seconds: int
    auto_manage_option_positions: bool
    auto_close_option_positions: bool
    immediate_option_execution: bool
    execute_medium_confidence_trades: bool
    option_position_management_interval_seconds: int
    option_position_take_profit_pct: float
    option_position_stop_loss_pct: float
    option_position_exit_hours_to_expiration: float
    option_position_enable_trailing_profit: bool
    option_position_trailing_profit_dry_run: bool
    option_position_enable_momentum_exit: bool
    option_trail_protection_trigger_pct: float
    option_trail_initial_floor_pct: float
    option_trail_first_scale_out_trigger_pct: float
    option_trail_first_scale_out_fraction: float
    option_trail_second_scale_out_trigger_pct: float
    option_trail_second_scale_out_fraction: float
    option_trail_3_7_giveback_pct: float
    option_trail_7_14_giveback_pct: float
    option_trail_14_30_giveback_pct: float
    option_trail_100_floor_pct: float
    option_trail_150_floor_pct: float
    option_trail_200_floor_pct: float
    option_pending_exit_stale_minutes: float
    option_pending_exit_cancel_on_stale: bool
    max_deployable_buying_power_pct: float
    per_order_sizing_buying_power_pct: float
    max_option_order_qty_multiplier: int
    alpaca_paper: bool
    cold_start_sanity_check_enabled: bool

    @property
    def max_deployable_buying_power_ratio(self) -> float:
        return self.max_deployable_buying_power_pct / 100.0

    @property
    def per_order_sizing_buying_power_ratio(self) -> float:
        return self.per_order_sizing_buying_power_pct / 100.0

    @classmethod
    def from_env(cls) -> FrontMainSettings:
        market_recheck_seconds = 5 * 60
        return cls(
            default_option_order_qty=max(1, int(os.getenv("AGENT_OPTION_ORDER_QTY", "1"))),
            option_contract_multiplier=100,
            run_interval_seconds=max(60, int(os.getenv("RUN_INTERVAL_SECONDS", str(3 * 60 * 60)))),
            market_recheck_seconds=market_recheck_seconds,
            auto_manage_option_positions=env_flag("AUTO_MANAGE_OPTION_POSITIONS", True),
            auto_close_option_positions=env_flag("AUTO_CLOSE_OPTION_POSITIONS", True),
            immediate_option_execution=env_flag("IMMEDIATE_OPTION_EXECUTION", False),
            execute_medium_confidence_trades=env_flag("EXECUTE_MEDIUM_CONFIDENCE_TRADES", True),
            option_position_management_interval_seconds=max(
                60,
                int(os.getenv("OPTION_POSITION_MANAGEMENT_INTERVAL_SECONDS", str(market_recheck_seconds))),
            ),
            option_position_take_profit_pct=float(os.getenv("OPTION_POSITION_TAKE_PROFIT_PCT", "25")),
            option_position_stop_loss_pct=float(os.getenv("OPTION_POSITION_STOP_LOSS_PCT", "-20")),
            option_position_exit_hours_to_expiration=load_exit_hours_to_expiration(),
            option_position_enable_trailing_profit=env_flag("OPTION_POSITION_ENABLE_TRAILING_PROFIT", False),
            option_position_trailing_profit_dry_run=env_flag("OPTION_POSITION_TRAILING_PROFIT_DRY_RUN", False),
            option_position_enable_momentum_exit=env_flag("OPTION_POSITION_ENABLE_MOMENTUM_EXIT", False),
            option_trail_protection_trigger_pct=_env_float("OPTION_TRAIL_PROTECTION_TRIGGER_PCT", 0.40),
            option_trail_initial_floor_pct=_env_float("OPTION_TRAIL_INITIAL_FLOOR_PCT", 0.10),
            option_trail_first_scale_out_trigger_pct=_env_float(
                "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT",
                0.55,
            ),
            option_trail_first_scale_out_fraction=_env_float(
                "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION",
                0.50,
            ),
            option_trail_second_scale_out_trigger_pct=_env_float(
                "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT",
                2.00,
            ),
            option_trail_second_scale_out_fraction=_env_float(
                "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION",
                0.25,
            ),
            option_trail_3_7_giveback_pct=_env_float("OPTION_TRAIL_3_7_GIVEBACK_PCT", 0.25),
            option_trail_7_14_giveback_pct=_env_float("OPTION_TRAIL_7_14_GIVEBACK_PCT", 0.35),
            option_trail_14_30_giveback_pct=_env_float("OPTION_TRAIL_14_30_GIVEBACK_PCT", 0.45),
            option_trail_100_floor_pct=_env_float("OPTION_TRAIL_100_FLOOR_PCT", 0.60),
            option_trail_150_floor_pct=_env_float("OPTION_TRAIL_150_FLOOR_PCT", 1.00),
            option_trail_200_floor_pct=_env_float("OPTION_TRAIL_200_FLOOR_PCT", 1.40),
            option_pending_exit_stale_minutes=_env_float("OPTION_PENDING_EXIT_STALE_MINUTES", 10.0),
            option_pending_exit_cancel_on_stale=env_flag("OPTION_PENDING_EXIT_CANCEL_ON_STALE", True),
            max_deployable_buying_power_pct=env_percentage("MAX_DEPLOYABLE_BUYING_POWER_PCT", 30.0),
            per_order_sizing_buying_power_pct=env_percentage("PER_ORDER_SIZING_BUYING_POWER_PCT", 30.0),
            max_option_order_qty_multiplier=env_positive_int("MAX_OPTION_ORDER_QTY_MULTIPLIER", 50),
            alpaca_paper=env_flag("ALPACA_PAPER", True),
            cold_start_sanity_check_enabled=env_flag("COLD_START_SANITY_CHECK", True),
        )
