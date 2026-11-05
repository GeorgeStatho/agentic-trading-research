"""Score a model's direction calls against what the underlying stock did next, without option pricing.

The option backtest needs the chain production fetched for the manager's DTE bucket, which was only
saved from late September 2026. Frozen manager inputs go back further, so this replays the same
decisions (see `ReplayRun`) and grades each answer by the stock's return from the price the model
saw to the close a fixed number of trading days later. A call scores the return, a put its negative;
`neither` answers are counted but not scored. Premium caps, contract choice and portfolio limits are
ignored: this measures direction, not P&L. Nothing is written except cached model answers.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any, Sequence

from market_db_modules.backtesting import daily_closes_after
from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
from services.evaluation.llm_backtest import Decider, ReplayRun, ReplayTally, ReplayedAnswer, log_outcome, store_daily_history

# Trading-day closes after the decision; when the decision precedes the close, its own day counts as the first.
HORIZONS = (5, 10)
DIRECTION_SIGN = {"call": 1, "put": -1}
# Calendar days that always contain the longest horizon's trading days, holidays included.
CLOSE_LOOKUP_DAYS = 21


def forward_returns(decision: dict[str, Any], payload: dict[str, Any], *, db_path: Any = None) -> dict[int, float] | None:
    """The stock's return from the price the model saw to each horizon's close; None without that data."""
    reference = _get_reference_stock_price(payload.get("market_context") or {})
    decided_at = datetime.fromisoformat(decision["decision_run_at"])
    closes = daily_closes_after(decision["symbol"], decided_at, until=decided_at + timedelta(days=CLOSE_LOOKUP_DAYS),
                                db_path=db_path)
    if not reference or len(closes) < max(HORIZONS):
        return None
    return returns_from_closes(reference, closes)


def returns_from_closes(reference: float, closes: list[tuple[datetime, float]]) -> dict[int, float]:
    """The return from `reference` to each horizon's close, for the horizons the closes reach."""
    return {horizon: closes[horizon - 1][1] / reference - 1 for horizon in HORIZONS if len(closes) >= horizon}


def score_answer(answer: ReplayedAnswer, *, db_path: Any = None) -> tuple[str, dict[str, Any] | None]:
    if answer.unavailable is not None:
        return answer.unavailable, None
    returns = forward_returns(answer.decision, answer.payload, db_path=db_path)
    if returns is None:
        return "no_forward_prices", None
    decision = str(answer.recommendation.get("decision") or "").lower()
    sign = DIRECTION_SIGN.get(decision)
    row = {
        "decision_id": answer.decision["id"],
        "symbol": answer.decision["symbol"],
        "decision_run_at": answer.decision["decision_run_at"],
        "decision": decision,
        "confidence": answer.recommendation.get("confidence"),
        "production_decision": str(answer.decision.get("manager_decision") or "").lower(),
        "stock_return_pct": {horizon: round(value * 100, 3) for horizon, value in returns.items()},
        "signed_return_pct": ({horizon: round(sign * value * 100, 3) for horizon, value in returns.items()}
                              if sign is not None else None),
    }
    return ("scored_directional" if sign is not None else "scored_neither"), row


def horizon_stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    stats: dict[str, Any] = {"count": len(rows)}
    for horizon in HORIZONS:
        values = [row[key][horizon] for row in rows]
        stats[f"{horizon}d"] = {
            "hit_rate_pct": round(100 * sum(value > 0 for value in values) / len(values), 2),
            "mean_pct": round(mean(values), 3),
            "median_pct": round(median(values), 3),
        } if values else None
    return stats


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Hit rate and returns in the called direction, overall and by confidence; plus how every scored stock moved.

    The unconditional block is the bar to clear: a model that only says `call` in a rising market
    scores well on hit rate without any skill.
    """
    directional = [row for row in rows if row["signed_return_pct"] is not None]
    confidences = sorted({str(row["confidence"]) for row in directional})
    return {
        "horizons_trading_days": list(HORIZONS),
        "directional": {
            **horizon_stats(directional, "signed_return_pct"),
            "calls": sum(row["decision"] == "call" for row in directional),
            "puts": sum(row["decision"] == "put" for row in directional),
            "by_confidence": {confidence: horizon_stats([row for row in directional if str(row["confidence"]) == confidence],
                                                        "signed_return_pct") for confidence in confidences},
        },
        "neither_count": len(rows) - len(directional),
        "unconditional_stock_moves": horizon_stats(rows, "stock_return_pct"),
        "agreement_with_production_pct": (round(100 * sum(row["decision"] == row["production_decision"] for row in rows)
                                                / len(rows), 2) if rows else None),
    }


def fetch_missing_history(run: ReplayRun, *, db_path: Any = None) -> dict[str, Any]:
    """Daily history for symbols with no close shortly after their first decision in the run."""
    first_decision: dict[str, str] = {}
    for decision in run.decisions:
        first_decision.setdefault(decision["symbol"], decision["decision_run_at"])
    missing = {symbol for symbol, decided in first_decision.items()
               if not daily_closes_after(symbol, datetime.fromisoformat(decided),
                                         until=datetime.fromisoformat(decided) + timedelta(days=CLOSE_LOOKUP_DAYS),
                                         db_path=db_path)}
    return store_daily_history(missing, db_path=db_path)


def run_direction_score(
    *,
    model: str,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int | None = None,
    decision_ids: Sequence[int] | None = None,
    allow_unknown_cutoff: bool = False,
    min_hours_between: float = 0,
    replay_strategist: bool = False,
    fetch_missing_bars: bool = False,
    decide: Decider | None = None,
    decide_strategist: Decider | None = None,
    strategist_reuse_max_age_minutes: int = 0,
    db_path: Any = None,
) -> dict[str, Any]:
    """Replay `model` (or `stored`) over the window and score each answer's direction by the stock's next moves."""
    run = ReplayRun.build(model=model, start_at=start_at, end_at=end_at, limit=limit, decision_ids=decision_ids,
                          allow_unknown_cutoff=allow_unknown_cutoff, min_hours_between=min_hours_between, replay_strategist=replay_strategist,
                          decide=decide, decide_strategist=decide_strategist,
                          strategist_reuse_max_age_minutes=strategist_reuse_max_age_minutes, db_path=db_path)
    bars = fetch_missing_history(run, db_path=db_path) if fetch_missing_bars else None
    tally = ReplayTally()
    rows: list[dict[str, Any]] = []
    for index, answer in enumerate(run.answers(tally), start=1):
        outcome, row = score_answer(answer, db_path=db_path)
        if row is not None:
            rows.append(row)
        tally.count(outcome)
        log_outcome(index, run, answer, outcome)
    return {"model": model, "replay": {**run.summary(tally), "daily_bars": bars},
            "score": summarize(rows), "decisions": rows}
