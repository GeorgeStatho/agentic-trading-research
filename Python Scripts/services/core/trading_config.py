from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from services.core.env_parsing import (
    env_flag,
    env_float,
    env_non_negative_int,
    env_optional_positive_int,
    env_percentage,
    env_positive_int,
)
from services.options.option_dte_buckets import OPTION_DTE_BUCKETS
from services.core.runtime_paths import DATA_DIR, LOGS_DIR, ROOT_DIR

# The premium-per-trade cap's defaults; TRADABILITY's policy falls back to the same values.
DEFAULT_MAX_PREMIUM_PER_TRADE_PCT = 55.0
DEFAULT_MAX_PREMIUM_PER_TRADE_DOLLARS = 800.0


def _load_exit_hours_to_expiration() -> float:
    """Hours before expiry to force an exit; the older *_DAYS_* variable is converted to hours."""
    if os.getenv("OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION") is not None:
        return max(0.0, env_float("OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION", 24.0))
    return max(0.0, env_float("OPTION_POSITION_EXIT_DAYS_TO_EXPIRATION", 1.0) * 24.0)


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
        values[bucket.key] = env_float(env_name, fallback)
    return values


@dataclass(frozen=True)
class WorkerPaths:
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
    def from_env(cls) -> WorkerPaths:
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
            agent_output_path=Path(
                os.getenv(
                    "AGENT_OUTPUT_PATH",
                    str(ROOT_DIR / "shared" / "agent_runner_output.json"),
                )
            ),
            selected_options_output_path=Path(
                os.getenv("SELECTED_OPTIONS_OUTPUT_PATH", str(DATA_DIR / "selected_options_output.json"))
            ),
            combined_output_path=DATA_DIR / "front_main_output.json",
        )


@dataclass(frozen=True)
class WorkerSettings:
    """Runtime configuration for trading cycles and loop scheduling."""

    default_option_order_qty: int
    option_contract_multiplier: int
    run_interval_seconds: int
    market_recheck_seconds: int
    main_loop_entry_cooldown_seconds_after_market_open: int | None
    auto_manage_option_positions: bool
    auto_close_option_positions: bool
    immediate_option_execution: bool
    execute_medium_confidence_trades: bool
    option_position_management_interval_seconds: int
    option_positions: OptionPositionSettings
    manager_decision_pnl_retention_days: int
    max_deployable_buying_power_pct: float
    per_order_sizing_buying_power_pct: float
    max_premium_per_trade_pct: float
    max_premium_per_trade_dollars: float
    max_option_order_qty_multiplier: int
    max_concurrent_option_positions: int
    max_option_sector_exposure_pct: float
    daily_option_loss_limit_dollars: float
    max_symbol_entries_per_window: int
    symbol_entry_window_days: int
    symbol_stop_loss_cooldown_days: int
    alpaca_paper: bool
    cold_start_sanity_check_enabled: bool

    def resolve_max_premium_per_trade(self, available_buying_power: float) -> float:
        """Return the hard premium ceiling for one entry, or 0.0 when uncapped.

        Both limits are optional; when both are set the tighter one wins. The
        absolute limit keeps the ceiling stable on a small account whose buying
        power swings as positions open and close.
        """
        limits = []
        if self.max_premium_per_trade_pct > 0:
            limits.append(available_buying_power * self.max_premium_per_trade_pct / 100.0)
        if self.max_premium_per_trade_dollars > 0:
            limits.append(self.max_premium_per_trade_dollars)
        return min(limits) if limits else 0.0

    @property
    def max_deployable_buying_power_ratio(self) -> float:
        return self.max_deployable_buying_power_pct / 100.0

    @property
    def per_order_sizing_buying_power_ratio(self) -> float:
        return self.per_order_sizing_buying_power_pct / 100.0

    @property
    def max_premium_per_trade_ratio(self) -> float:
        return self.max_premium_per_trade_pct / 100.0

    @classmethod
    def from_env(cls) -> WorkerSettings:
        # Read the relevant environment variables in one place and normalize them into this settings object.
        market_recheck_seconds = 5 * 60
        return cls(
            default_option_order_qty=env_positive_int("AGENT_OPTION_ORDER_QTY", 1),
            option_contract_multiplier=100,
            run_interval_seconds=max(60, env_positive_int("RUN_INTERVAL_SECONDS", 3 * 60 * 60)),
            market_recheck_seconds=market_recheck_seconds,
            main_loop_entry_cooldown_seconds_after_market_open=env_optional_positive_int(
                "MAIN_LOOP_ENTRY_COOLDOWN_SECONDS_AFTER_MARKET_OPEN"
            ),
            auto_manage_option_positions=env_flag("AUTO_MANAGE_OPTION_POSITIONS", True),
            auto_close_option_positions=env_flag("AUTO_CLOSE_OPTION_POSITIONS", True),
            immediate_option_execution=env_flag("IMMEDIATE_OPTION_EXECUTION", False),
            execute_medium_confidence_trades=env_flag("EXECUTE_MEDIUM_CONFIDENCE_TRADES", True),
            option_position_management_interval_seconds=max(
                5,
                env_positive_int("OPTION_POSITION_MANAGEMENT_INTERVAL_SECONDS", market_recheck_seconds),
            ),
            option_positions=OptionPositionSettings.from_env(),
            manager_decision_pnl_retention_days=env_positive_int(
                "MANAGER_DECISION_PNL_RETENTION_DAYS",
                14,
            ),
            max_deployable_buying_power_pct=env_percentage("MAX_DEPLOYABLE_BUYING_POWER_PCT", 30.0),
            per_order_sizing_buying_power_pct=env_percentage("PER_ORDER_SIZING_BUYING_POWER_PCT", 30.0),
            # Hard ceiling on total premium (qty x contract cost) for one new entry.
            # Both limits are optional and the tighter one wins; 0 disables that
            # limit. Unlike the per-order sizing budget, this cap also refuses the
            # trade outright when even a single contract costs more than it allows.
            max_premium_per_trade_pct=env_percentage("MAX_PREMIUM_PER_TRADE_PCT", DEFAULT_MAX_PREMIUM_PER_TRADE_PCT),
            max_premium_per_trade_dollars=max(
                0.0, env_float("MAX_PREMIUM_PER_TRADE_DOLLARS", DEFAULT_MAX_PREMIUM_PER_TRADE_DOLLARS)),
            max_option_order_qty_multiplier=env_positive_int("MAX_OPTION_ORDER_QTY_MULTIPLIER", 50),
            max_concurrent_option_positions=env_positive_int("MAX_CONCURRENT_OPTION_POSITIONS", 5),
            max_option_sector_exposure_pct=max(0.01, env_percentage("MAX_OPTION_SECTOR_EXPOSURE_PCT", 50.0)),
            daily_option_loss_limit_dollars=max(1.0, env_float("DAILY_OPTION_LOSS_LIMIT_DOLLARS", 2000.0)),
            # Per-underlying churn brakes: at most N submitted buys per rolling
            # window, plus a re-entry cooldown after a stop-loss exit in the same
            # underlying. Both windows are calendar days and 0 disables that brake.
            # Default off: replayed against the live May-Sep 2026 record these lose
            # money. Re-entries within 7 days of a stop averaged -$8 (40% win rate)
            # against -$24 (35%) for fresh entries, so the rules mostly block the
            # same-name follow-ups that produced the two largest winners.
            max_symbol_entries_per_window=env_non_negative_int("MAX_OPTION_ENTRIES_PER_SYMBOL_PER_WINDOW", 0),
            symbol_entry_window_days=env_positive_int("OPTION_SYMBOL_ENTRY_WINDOW_DAYS", 7),
            symbol_stop_loss_cooldown_days=env_non_negative_int("OPTION_SYMBOL_STOP_LOSS_COOLDOWN_DAYS", 0),
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
class TrailingProfitConfig:
    protection_trigger_pct: float
    initial_floor_pct: float
    dynamic_giveback_start_pct: float
    first_scale_out_trigger_pct: float
    first_scale_out_fraction: float
    second_scale_out_trigger_pct: float
    second_scale_out_fraction: float
    giveback_pct_by_bucket_key: dict[str, float]
    floor_100_pct: float
    floor_150_pct: float
    floor_200_pct: float
    enable_momentum_exit: bool


@dataclass(frozen=True)
class MomentumHistoryConfig:
    enable_after_pnl_pct: float
    window_size: int
    min_samples: int
    bad_count_exit_threshold: int
    consecutive_bad_exit_threshold: int


@dataclass(frozen=True)
class OptionPriceMomentumConfig:
    bad_giveback_threshold: float
    mixed_giveback_threshold: float
    good_profit_threshold: float


@dataclass(frozen=True)
class PendingExitConfig:
    stale_minutes: float
    cancel_on_stale: bool


@dataclass(frozen=True)
class OptionPositionSettings:
    """Every option exit setting: shared by the option manager, the worker manifest and the API."""

    take_profit_pct: float
    stop_loss_pct: float
    exit_hours_to_expiration: float
    dte_rules: tuple[OptionExitRuleConfig, ...]
    enable_trailing_profit: bool
    trailing_profit_dry_run: bool
    stop_loss_confirm_checks: int
    stop_loss_underlying_move_pct: float
    exit_use_limit_orders: bool
    exit_max_spread_pct: float
    stop_loss_max_spread_wait_minutes: float
    exit_tick_size_small: float
    exit_tick_size_large: float
    exit_tick_price_threshold: float
    volatility_scaled_thresholds: bool
    volatility_baseline_atr_pct: float
    volatility_min_scale: float
    volatility_max_scale: float
    volatility_atr_period: int
    volatility_atr_lookback_days: int
    trailing: TrailingProfitConfig
    momentum_history: MomentumHistoryConfig
    price_momentum: OptionPriceMomentumConfig
    pending_exit: PendingExitConfig

    @classmethod
    def from_env(cls) -> OptionPositionSettings:
        # Read the relevant environment variables in one place and normalize them into this settings object.
        return cls(
            take_profit_pct=env_float("OPTION_POSITION_TAKE_PROFIT_PCT", 25.0),
            stop_loss_pct=env_float("OPTION_POSITION_STOP_LOSS_PCT", -20.0),
            exit_hours_to_expiration=_load_exit_hours_to_expiration(),
            enable_trailing_profit=env_flag("OPTION_POSITION_ENABLE_TRAILING_PROFIT", False),
            trailing_profit_dry_run=env_flag("OPTION_POSITION_TRAILING_PROFIT_DRY_RUN", False),
            # Number of consecutive manager cycles the broker P/L must stay at or below the
            # stop-loss floor before a full exit fires. 1 preserves the legacy behavior of
            # selling on the first breach; higher values ride out momentary dips that bounce
            # back (option premium is leveraged and spread-noisy, so a small underlying wobble
            # can briefly cross the floor and recover).
            stop_loss_confirm_checks=env_positive_int("OPTION_POSITION_STOP_LOSS_CONFIRM_CHECKS", 1),
            # Minimum adverse move in the UNDERLYING (as a fraction of the entry underlying
            # price) required before a broker-P/L stop-loss breach is treated as real. 0.0
            # disables the check and keeps the legacy premium-only stop. A value like 0.015
            # means "only stop a call once the stock is down >=1.5% from entry (or a put's
            # underlying is up >=1.5%)", so IV crush / spread noise on the option premium can
            # no longer force an exit while the underlying itself is still fine. When the
            # underlying baseline is unavailable or untrusted the premium stop still applies.
            stop_loss_underlying_move_pct=max(0.0, env_float("OPTION_POSITION_STOP_LOSS_UNDERLYING_MOVE_PCT", 0.0)),
            # When enabled, exits are submitted as limit orders priced off the live option
            # quote (passive at the midpoint to capture spread; marketable at the bid for
            # urgent or already-retried exits) instead of crossing the whole spread with a
            # market order. Disabled by default so behavior is unchanged until opted in.
            exit_use_limit_orders=env_flag("OPTION_POSITION_EXIT_USE_LIMIT_ORDERS", False),
            # Liquidity guard: when > 0, an exit is held for another cycle if the option's
            # bid/ask spread exceeds this fraction of the midpoint, rather than paying up into
            # a blown-out book. Only the near-expiration exit bypasses it. 0 disables it.
            exit_max_spread_pct=max(0.0, env_float("OPTION_POSITION_EXIT_MAX_SPREAD_PCT", 0.0)),
            # Longest a stop-loss waits on a wide spread before it sells at the bid anyway.
            stop_loss_max_spread_wait_minutes=max(0.0, env_float("OPTION_POSITION_STOP_LOSS_MAX_SPREAD_WAIT_MINUTES", 30.0)),
            # Tick sizes for rounding limit-exit prices to a broker-valid increment. Defaults
            # to the OCC standard (a nickel below $3, a dime at/above), which is valid for any
            # option class; set both to 0.01 for penny-quoted names to keep full precision.
            # Sell limits round DOWN to the tick, so they stay valid and marketable.
            exit_tick_size_small=env_float("OPTION_POSITION_EXIT_TICK_SIZE_SMALL", 0.05),
            exit_tick_size_large=env_float("OPTION_POSITION_EXIT_TICK_SIZE_LARGE", 0.10),
            exit_tick_price_threshold=env_float("OPTION_POSITION_EXIT_TICK_PRICE_THRESHOLD", 3.00),
            # Volatility-scaled exit thresholds: when enabled, the stop-loss, take-profit, and
            # underlying-move gate are multiplied by the underlying's ATR relative to a
            # baseline, so a fixed -25% stop widens on a high-vol name and tightens on a calm
            # one. Disabled by default; falls back to fixed thresholds (scale 1.0) whenever the
            # ATR cannot be measured.
            volatility_scaled_thresholds=env_flag("OPTION_POSITION_VOLATILITY_SCALED_THRESHOLDS", False),
            # The underlying daily-ATR-as-fraction-of-price that maps to a 1.0x scale. A name
            # whose ATR is this fraction of its price gets its thresholds unchanged; twice as
            # volatile -> ~2x (subject to the min/max clamp).
            volatility_baseline_atr_pct=env_float("OPTION_POSITION_VOLATILITY_BASELINE_ATR_PCT", 0.02),
            volatility_min_scale=env_float("OPTION_POSITION_VOLATILITY_MIN_SCALE", 0.5),
            volatility_max_scale=env_float("OPTION_POSITION_VOLATILITY_MAX_SCALE", 2.0),
            volatility_atr_period=env_positive_int("OPTION_POSITION_VOLATILITY_ATR_PERIOD", 14),
            volatility_atr_lookback_days=env_positive_int("OPTION_POSITION_VOLATILITY_ATR_LOOKBACK_DAYS", 40),
            trailing=TrailingProfitConfig(
                # Arm profit protection once the position's tracked peak reaches +20%,
                # with the floor just above breakeven. Live May-Sep 2026 exits showed
                # good outcomes peaking in the +20..+50% band and retracing to stops;
                # floors ratchet up and never cap runners, so this harvests the band
                # without giving up the occasional +100%+ winner.
                protection_trigger_pct=env_float("OPTION_TRAIL_PROTECTION_TRIGGER_PCT", 0.20),
                initial_floor_pct=env_float("OPTION_TRAIL_INITIAL_FLOOR_PCT", 0.05),
                dynamic_giveback_start_pct=env_float("OPTION_TRAIL_DYNAMIC_GIVEBACK_START_PCT", 0.75),
                first_scale_out_trigger_pct=env_float("OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT", 0.55),
                first_scale_out_fraction=env_float("OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION", 0.50),
                second_scale_out_trigger_pct=env_float("OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT", 2.00),
                second_scale_out_fraction=env_float("OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION", 0.25),
                giveback_pct_by_bucket_key=_load_option_trail_giveback_pct_by_bucket_key(),
                floor_100_pct=env_float("OPTION_TRAIL_100_FLOOR_PCT", 0.60),
                floor_150_pct=env_float("OPTION_TRAIL_150_FLOOR_PCT", 1.00),
                floor_200_pct=env_float("OPTION_TRAIL_200_FLOOR_PCT", 1.40),
                enable_momentum_exit=env_flag("OPTION_POSITION_ENABLE_MOMENTUM_EXIT", False),
            ),
            momentum_history=MomentumHistoryConfig(
                enable_after_pnl_pct=env_float("OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT", 2.00),
                window_size=env_positive_int("OPTION_MOMENTUM_HISTORY_WINDOW_SIZE", 10),
                min_samples=env_positive_int("OPTION_MOMENTUM_HISTORY_MIN_SAMPLES", 5),
                bad_count_exit_threshold=env_positive_int("OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD", 6),
                consecutive_bad_exit_threshold=env_positive_int(
                    "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD",
                    3,
                ),
            ),
            price_momentum=OptionPriceMomentumConfig(
                bad_giveback_threshold=env_float("OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD", 0.35),
                mixed_giveback_threshold=env_float("OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD", 0.15),
                good_profit_threshold=env_float("OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD", 0.10),
            ),
            pending_exit=PendingExitConfig(
                stale_minutes=env_float("OPTION_PENDING_EXIT_STALE_MINUTES", 10.0),
                cancel_on_stale=env_flag("OPTION_PENDING_EXIT_CANCEL_ON_STALE", True),
            ),
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
    agent_output_path: Path
    selected_options_output_path: Path
    option_position_management_output_path: Path
    option_manager_status_path: Path
    news_collector_status_path: Path
    marketdata_eod_sync_status_path: Path
    db_backup_status_path: Path

    @classmethod
    def from_env(cls) -> ApiPaths:
        # The API reads the files the worker writes, so it shares the worker's paths.
        worker = WorkerPaths.from_env()
        return cls(
            script_status_path=worker.status_path,
            trade_execution_output_path=worker.trade_output_path,
            agent_output_path=worker.agent_output_path,
            selected_options_output_path=worker.selected_options_output_path,
            option_position_management_output_path=worker.option_position_management_output_path,
            option_manager_status_path=Path(
                os.getenv(
                    "OPTION_MANAGER_STATUS_PATH",
                    str(DATA_DIR / "option_manager_status.json"),
                )
            ),
            news_collector_status_path=Path(
                os.getenv("NEWS_COLLECTOR_STATUS_PATH", str(DATA_DIR / "news_collector_status.json"))
            ),
            marketdata_eod_sync_status_path=Path(
                os.getenv("MARKETDATA_EOD_SYNC_STATUS_PATH", str(DATA_DIR / "marketdata_eod_sync_status.json"))
            ),
            db_backup_status_path=Path(os.getenv("DB_BACKUP_STATUS_PATH", str(DATA_DIR / "db_backup_status.json"))),
        )


@dataclass(frozen=True)
class ApiSettings:
    """Shared API/runtime display settings."""

    bot_status_down_threshold_seconds: int
    bot_status_stall_seconds: int
    bot_status_error_window_seconds: int
    default_option_order_qty: int
    analyzed_company_news_default_page_size: int
    analyzed_company_news_max_page_size: int
    max_deployable_buying_power_pct: float
    per_order_sizing_buying_power_pct: float
    max_premium_per_trade_pct: float
    max_premium_per_trade_dollars: float
    max_option_order_qty_multiplier: int
    max_concurrent_option_positions: int
    max_option_sector_exposure_pct: float
    daily_option_loss_limit_dollars: float
    max_symbol_entries_per_window: int
    symbol_entry_window_days: int
    symbol_stop_loss_cooldown_days: int

    @classmethod
    def from_env(cls) -> ApiSettings:
        # Sizing and risk limits are displayed exactly as the worker enforces them.
        worker = WorkerSettings.from_env()
        default_page_size = env_positive_int("ANALYZED_COMPANY_NEWS_DEFAULT_PAGE_SIZE", 5)
        return cls(
            bot_status_down_threshold_seconds=max(30, env_positive_int("BOT_STATUS_DOWN_THRESHOLD_SECONDS", 90)),
            # News cycles take up to ~2h, so only a far longer run counts as hung.
            bot_status_stall_seconds=env_positive_int("BOT_STATUS_STALL_SECONDS", 6 * 60 * 60),
            bot_status_error_window_seconds=env_positive_int("BOT_STATUS_ERROR_WINDOW_SECONDS", 60 * 60),
            default_option_order_qty=worker.default_option_order_qty,
            analyzed_company_news_default_page_size=default_page_size,
            analyzed_company_news_max_page_size=max(
                default_page_size,
                env_positive_int("ANALYZED_COMPANY_NEWS_MAX_PAGE_SIZE", 20),
            ),
            max_deployable_buying_power_pct=worker.max_deployable_buying_power_pct,
            per_order_sizing_buying_power_pct=worker.per_order_sizing_buying_power_pct,
            max_premium_per_trade_pct=worker.max_premium_per_trade_pct,
            max_premium_per_trade_dollars=worker.max_premium_per_trade_dollars,
            max_option_order_qty_multiplier=worker.max_option_order_qty_multiplier,
            max_concurrent_option_positions=worker.max_concurrent_option_positions,
            max_option_sector_exposure_pct=worker.max_option_sector_exposure_pct,
            daily_option_loss_limit_dollars=worker.daily_option_loss_limit_dollars,
            max_symbol_entries_per_window=worker.max_symbol_entries_per_window,
            symbol_entry_window_days=worker.symbol_entry_window_days,
            symbol_stop_loss_cooldown_days=worker.symbol_stop_loss_cooldown_days,
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
    minimum_high_confidence_company_articles: int
    # A strategist answer is reused for unchanged evidence while younger than this; 0 disables reuse.
    strategist_reuse_max_age_minutes: int = 0
    strategist_reuse_max_price_move_pct: float = 2.0

    @classmethod
    def from_env(cls) -> AgentPipelineSettings:
        return cls(
            ranking_max_age_days=env_optional_positive_int("PIPELINE_RANKING_MAX_AGE_DAYS"),
            minimum_high_confidence_company_articles=env_non_negative_int(
                "PIPELINE_MIN_HIGH_CONFIDENCE_COMPANY_ARTICLES",
                1,
            ),
            strategist_reuse_max_age_minutes=env_non_negative_int("STRATEGIST_REUSE_MAX_AGE_MINUTES", 0),
            strategist_reuse_max_price_move_pct=env_float("STRATEGIST_REUSE_MAX_PRICE_MOVE_PCT", 2.0),
        )
