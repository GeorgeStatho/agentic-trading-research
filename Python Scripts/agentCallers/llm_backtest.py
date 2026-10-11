from __future__ import annotations

import argparse
import json
import logging

from services.settings import get_settings, load_process_settings

if __name__ == "__main__":
    # Before importing modules that read configuration at import time.
    load_process_settings()

from services.llm_backtest import STORED_MODEL, run_llm_backtest  # noqa: E402


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
    parser.add_argument("--take-profit-pct", type=float)
    parser.add_argument("--stop-loss-pct", type=float)
    parser.add_argument("--max-hold-hours", type=int)
    parser.add_argument("--entry-slippage-bps", type=float)
    parser.add_argument("--exit-slippage-bps", type=float)
    return parser.parse_args()


def _summary(result: dict) -> dict:
    run = result["run"]
    metrics = {key: value for key, value in run["metrics"].items() if key != "equity_curve"}
    return {"backtest_run_id": run["id"], "model": run["model"], "prompt_version": run["prompt_version"],
            "metrics": metrics}


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    execution_config = {key: value for key, value in {
        "take_profit_pct": args.take_profit_pct,
        "stop_loss_pct": args.stop_loss_pct,
        "max_hold_hours": args.max_hold_hours,
        "entry_slippage_bps": args.entry_slippage_bps,
        "exit_slippage_bps": args.exit_slippage_bps,
    }.items() if value is not None}
    result = run_llm_backtest(model=args.model, start_at=args.start_at or None, end_at=args.end_at or None,
                              limit=args.limit, execution_config=execution_config)
    print(json.dumps(_summary(result), indent=2, default=str))


if __name__ == "__main__":
    main()
