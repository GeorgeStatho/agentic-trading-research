from __future__ import annotations

import argparse
import json
import logging

from services.settings import get_settings, load_process_settings

if __name__ == "__main__":
    # Before importing modules that read configuration at import time.
    load_process_settings()

from services.llm_backtest import STORED_MODEL, run_llm_backtest  # noqa: E402
from services.llm_direction_score import HORIZONS, run_direction_score  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a manager model on frozen decisions, then backtest the trades it would have made. "
                    "Reads DB_PATH; answers are cached per model, so reruns only re-simulate.")
    parser.add_argument("--model", default=get_settings().models.manager.model,
                        help=f"Model label to replay (default MANAGER_MODEL), or '{STORED_MODEL}' to "
                             "re-finalize production's own answers as a like-for-like baseline.")
    parser.add_argument("--start-at", default="", help="Inclusive ISO-8601 decision timestamp.")
    parser.add_argument("--end-at", default="", help="Inclusive ISO-8601 decision timestamp.")
    parser.add_argument("--limit", type=int, default=None, help="Most recent decisions in the window (default all).")
    parser.add_argument("--fetch-missing-bars", action="store_true",
                        help="Download daily history (yfinance) for traded symbols with none, so their exits can be priced.")
    parser.add_argument("--fetch-option-quotes", action="store_true",
                        help="Download MarketData.app end-of-day quotes for the replay's contracts (MARKETDATA_TOKEN, "
                             "daily credit budget); contracts without them are Black-Scholes priced.")
    parser.add_argument("--execute-medium-confidence", action="store_true",
                        help="Trade medium-confidence answers too, overriding production's recorded rule, to score a "
                             "model's direction apart from its confidence. The run summary records the override.")
    parser.add_argument("--min-hours-between", type=float, default=0,
                        help="Replay a company's decision only if it is at least this many hours after the last one "
                             "replayed for that company (default 0: every decision). Fewer, less repetitive samples; "
                             "compare with a stored run using the same value.")
    parser.add_argument("--replay-strategist", action="store_true",
                        help="Have --model answer the strategist too, on each decision's frozen strategist input; the "
                             "manager then answers that strategist instead of production's. Not valid with 'stored'.")
    parser.add_argument("--strategist-reuse-minutes", type=int, default=0,
                        help="With --replay-strategist, reuse a symbol's replayed strategist answer for later decisions "
                             "under production's reuse rule (unchanged evidence and regime, price move under "
                             "STRATEGIST_REUSE_MAX_PRICE_MOVE_PCT) while it is younger than this many minutes. "
                             "Match production's STRATEGIST_REUSE_MAX_AGE_MINUTES (default 0: always ask).")
    parser.add_argument("--score-direction", action="store_true",
                        help="Instead of trading options, score each answer's direction by the stock's return over the "
                             f"next {' and '.join(map(str, HORIZONS))} trading-day closes. Works on decisions before "
                             "option-chain snapshots began; nothing but cached answers is stored.")
    parser.add_argument("--output", default="", help="With --score-direction, also write every scored decision to this JSON file.")
    parser.add_argument("--take-profit-pct", type=float)
    parser.add_argument("--stop-loss-pct", type=float)
    parser.add_argument("--max-hold-hours", type=int)
    parser.add_argument("--entry-slippage-bps", type=float)
    parser.add_argument("--exit-slippage-bps", type=float)
    args = parser.parse_args()
    trading_only = [name for name in ("execute_medium_confidence", "fetch_option_quotes", "take_profit_pct", "stop_loss_pct",
                                      "max_hold_hours", "entry_slippage_bps", "exit_slippage_bps")
                    if getattr(args, name) not in (None, False)]
    if args.strategist_reuse_minutes and not args.replay_strategist:
        parser.error("--strategist-reuse-minutes needs --replay-strategist")
    if args.score_direction and trading_only:
        parser.error(f"--score-direction does not trade options; remove {', '.join('--' + n.replace('_', '-') for n in trading_only)}")
    return args


def _summary(result: dict) -> dict:
    run = result["run"]
    metrics = {key: value for key, value in run["metrics"].items() if key != "equity_curve"}
    return {"backtest_run_id": run["id"], "model": run["model"], "prompt_version": run["prompt_version"],
            "metrics": metrics}


def _score_direction(args: argparse.Namespace) -> None:
    result = run_direction_score(model=args.model, start_at=args.start_at or None, end_at=args.end_at or None,
                                 limit=args.limit, min_hours_between=args.min_hours_between,
                                 replay_strategist=args.replay_strategist, fetch_missing_bars=args.fetch_missing_bars,
                                 strategist_reuse_max_age_minutes=args.strategist_reuse_minutes)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, default=str)
    print(json.dumps({key: value for key, value in result.items() if key != "decisions"}, indent=2, default=str))


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.score_direction:
        _score_direction(args)
        return
    execution_config = {key: value for key, value in {
        "take_profit_pct": args.take_profit_pct,
        "stop_loss_pct": args.stop_loss_pct,
        "max_hold_hours": args.max_hold_hours,
        "entry_slippage_bps": args.entry_slippage_bps,
        "exit_slippage_bps": args.exit_slippage_bps,
    }.items() if value is not None}
    result = run_llm_backtest(model=args.model, start_at=args.start_at or None, end_at=args.end_at or None,
                              limit=args.limit, execution_config=execution_config,
                              fetch_missing_bars=args.fetch_missing_bars, fetch_option_quotes=args.fetch_option_quotes,
                              execute_medium_confidence=args.execute_medium_confidence,
                              min_hours_between=args.min_hours_between, replay_strategist=args.replay_strategist,
                              strategist_reuse_max_age_minutes=args.strategist_reuse_minutes)
    print(json.dumps(_summary(result), indent=2, default=str))


if __name__ == "__main__":
    main()
