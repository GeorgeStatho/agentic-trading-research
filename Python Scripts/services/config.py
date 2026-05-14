from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from services.common import (
    env_flag,
    env_float,
    env_optional_positive_int,
    env_percentage,
    env_positive_int,
    load_exit_hours_to_expiration,
)
from services.option_dte_buckets import OPTION_DTE_BUCKETS
from services.runtime_paths import DATA_DIR, LOGS_DIR, ROOT_DIR


def _env_float(name: str, default: float) -> float:
    raw_value = str(os.getenv(name, str(default))).strip()
    try:
        return float(raw_value)
    except ValueError:
        return float(default)


def _load_option_trail_giveback_pct_by_bucket_key() -> dict[str, float]:
    # Build per-bucket trailing giveback settings from the shared bucket list so
    # newly added DTE buckets inherit config support automatically.
    values: dict[str, float] = {}
    for bucket in OPTION_DTE_BUCKETS:
        env_name = f"OPTION_TRAIL_{bucket.key.upper()}_GIVEBACK_PCT"
        fallback = (
            float(bucket.default_trailing_giveback_pct)
            if bucket.default_trailing_giveback_pct is not None
            else 0.45
        )
        values[bucket.key] = _env_float(env_name, fallback)
    return values


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
        # Read the relevant environment variables in one place and normalize them into this settings object.
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
    option_momentum_history_enable_after_pnl_pct: float
    option_momentum_history_window_size: int
    option_momentum_history_min_samples: int
    option_momentum_history_bad_count_exit_threshold: int
    option_momentum_history_consecutive_bad_exit_threshold: int
    option_trail_protection_trigger_pct: float
    option_trail_initial_floor_pct: float
    option_trail_first_scale_out_trigger_pct: float
    option_trail_first_scale_out_fraction: float
    option_trail_second_scale_out_trigger_pct: float
    option_trail_second_scale_out_fraction: float
    option_trail_giveback_pct_by_bucket_key: dict[str, float]
    option_trail_3_7_giveback_pct: float
    option_trail_7_14_giveback_pct: float
    option_trail_14_30_giveback_pct: float
    option_trail_30_45_giveback_pct: float
    option_trail_45_60_giveback_pct: float
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
        # Read the relevant environment variables in one place and normalize them into this settings object.
        market_recheck_seconds = 5 * 60
        giveback_pct_by_bucket_key = _load_option_trail_giveback_pct_by_bucket_key()
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
            option_momentum_history_enable_after_pnl_pct=_env_float(
                "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT",
                2.00,
            ),
            option_momentum_history_window_size=env_positive_int(
                "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE",
                10,
            ),
            option_momentum_history_min_samples=env_positive_int(
                "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES",
                5,
            ),
            option_momentum_history_bad_count_exit_threshold=env_positive_int(
                "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD",
                6,
            ),
            option_momentum_history_consecutive_bad_exit_threshold=env_positive_int(
                "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD",
                3,
            ),
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
            option_trail_giveback_pct_by_bucket_key=giveback_pct_by_bucket_key,
            option_trail_3_7_giveback_pct=giveback_pct_by_bucket_key.get("3_7", 0.25),
            option_trail_7_14_giveback_pct=giveback_pct_by_bucket_key.get("7_14", 0.35),
            option_trail_14_30_giveback_pct=giveback_pct_by_bucket_key.get("14_30", 0.45),
            option_trail_30_45_giveback_pct=giveback_pct_by_bucket_key.get("30_45", 0.45),
            option_trail_45_60_giveback_pct=giveback_pct_by_bucket_key.get("45_60", 0.45),
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


@dataclass(frozen=True)
class OptionExitRuleConfig:
    """Threshold configuration for a DTE bucket."""

    key: str
    label: str
    take_profit_pct: float
    stop_loss_pct: float
    force_exit_days_to_expiration: int | None


@dataclass(frozen=True)
class OptionPositionSettings:
    """Shared option position management settings used by API and trading helpers."""

    take_profit_pct: float
    stop_loss_pct: float
    exit_hours_to_expiration: float
    dte_rules: tuple[OptionExitRuleConfig, ...]

    @classmethod
    def from_env(cls) -> OptionPositionSettings:
        # Read the relevant environment variables in one place and normalize them into this settings object.
        return cls(
            take_profit_pct=env_float("OPTION_POSITION_TAKE_PROFIT_PCT", 25.0),
            stop_loss_pct=env_float("OPTION_POSITION_STOP_LOSS_PCT", -20.0),
            exit_hours_to_expiration=load_exit_hours_to_expiration(),
            dte_rules=tuple(
                OptionExitRuleConfig(
                    key=bucket.key,
                    label=bucket.label,
                    take_profit_pct=env_float(
                        f"OPTION_POSITION_{bucket.key.upper()}_DTE_TAKE_PROFIT_PCT",
                        float(bucket.default_take_profit_pct or 25.0),
                    ),
                    stop_loss_pct=env_float(
                        f"OPTION_POSITION_{bucket.key.upper()}_DTE_STOP_LOSS_PCT",
                        float(bucket.default_stop_loss_pct or -20.0),
                    ),
                    force_exit_days_to_expiration=(
                        int(bucket.force_exit_days_to_expiration)
                        if bucket.force_exit_days_to_expiration is not None
                        else None
                    ),
                )
                for bucket in OPTION_DTE_BUCKETS
            ),
        )


@dataclass(frozen=True)
class ApiPaths:
    """Filesystem paths used by the Flask API and dashboard readers."""

    script_status_path: Path
    trade_execution_output_path: Path
    selected_options_output_path: Path
    option_position_management_output_path: Path
    option_manager_status_path: Path

    @classmethod
    def from_env(cls) -> ApiPaths:
        # Read the relevant environment variables in one place and normalize them into this settings object.
        return cls(
            script_status_path=Path(
                os.getenv("SCRIPT_STATUS_PATH", str(ROOT_DIR / "web_dashboard" / "public" / "script_status.json"))
            ),
            trade_execution_output_path=Path(
                os.getenv(
                    "TRADE_EXECUTION_OUTPUT_PATH",
                    str(DATA_DIR / "trade_execution_output.json"),
                )
            ),
            selected_options_output_path=DATA_DIR / "selected_options_output.json",
            option_position_management_output_path=Path(
                os.getenv(
                    "OPTION_POSITION_MANAGEMENT_OUTPUT_PATH",
                    str(DATA_DIR / "option_position_management_output.json"),
                )
            ),
            option_manager_status_path=Path(
                os.getenv(
                    "OPTION_MANAGER_STATUS_PATH",
                    str(DATA_DIR / "option_manager_status.json"),
                )
            ),
        )


@dataclass(frozen=True)
class ApiSettings:
    """Shared API/runtime display settings."""

    bot_status_down_threshold_seconds: int
    default_option_order_qty: int
    analyzed_company_news_default_page_size: int
    analyzed_company_news_max_page_size: int
    max_deployable_buying_power_pct: float
    per_order_sizing_buying_power_pct: float
    max_option_order_qty_multiplier: int

    @classmethod
    def from_env(cls) -> ApiSettings:
        default_page_size = env_positive_int("ANALYZED_COMPANY_NEWS_DEFAULT_PAGE_SIZE", 5)
        return cls(
            bot_status_down_threshold_seconds=max(30, env_positive_int("BOT_STATUS_DOWN_THRESHOLD_SECONDS", 90)),
            default_option_order_qty=env_positive_int("AGENT_OPTION_ORDER_QTY", 1),
            analyzed_company_news_default_page_size=default_page_size,
            analyzed_company_news_max_page_size=max(
                default_page_size,
                env_positive_int("ANALYZED_COMPANY_NEWS_MAX_PAGE_SIZE", 20),
            ),
            max_deployable_buying_power_pct=env_percentage("MAX_DEPLOYABLE_BUYING_POWER_PCT", 30.0),
            per_order_sizing_buying_power_pct=env_percentage("PER_ORDER_SIZING_BUYING_POWER_PCT", 30.0),
            max_option_order_qty_multiplier=env_positive_int("MAX_OPTION_ORDER_QTY_MULTIPLIER", 50),
        )


@dataclass(frozen=True)
class AlpacaSettings:
    """Credentials and base URLs for Alpaca-backed services."""

    api_key: str
    api_secret: str
    paper: bool

    @property
    def trading_base_url(self) -> str:
        return "https://paper-api.alpaca.markets" if self.paper else "https://api.alpaca.markets"

    @property
    def data_base_url(self) -> str:
        return "https://data.alpaca.markets"

    @classmethod
    def from_env(cls) -> AlpacaSettings:
        return cls(
            api_key=str(os.getenv("PUBLIC_KEY") or "").strip(),
            api_secret=str(os.getenv("PRIVATE_KEY") or "").strip(),
            paper=env_flag("ALPACA_PAPER", True),
        )


@dataclass(frozen=True)
class AgentPipelineSettings:
    """Shared pipeline ranking settings for agent callers."""

    ranking_max_age_days: int | None

    @classmethod
    def from_env(cls) -> AgentPipelineSettings:
        return cls(
            ranking_max_age_days=env_optional_positive_int("PIPELINE_RANKING_MAX_AGE_DAYS"),
        )
