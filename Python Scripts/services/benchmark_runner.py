"""Run a model over one split of a benchmark set, score each item in three layers, and compare runs item by item.

A run replays the split's frozen decisions through `run_llm_backtest` (so the contamination guard applies),
then grades every answer with Plan C's layers: evidence (the replayed strategist's own thesis check, or
production's, reported as fixed), direction (the stock's move after the decision) and expression (the
option trade's P&L). It is a screen for choosing challengers: it never writes a promotion.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean
from typing import Any, Sequence

from agent_stages.strategist_runner import strategist_prompt_version
from catalyst_models import timestamp
from market_db_modules.backtesting import daily_closes_after
from market_db_modules.benchmarks import (get_benchmark_run, list_benchmark_items, list_benchmark_runs, primary_catalysts,
                                          record_benchmark_run)
from market_db_modules.llm_replay_store import frozen_manager_input, list_frozen_manager_decisions
from services.decision_attribution import attribute, bootstrap_se
from services.evidence_thesis_policy import EvidenceThesisPolicy
from services.llm_backtest import (MANAGER_PROMPT_VERSION, STORED_MODEL, STORED_PROMPT_VERSION, ReplayedAnswer,
                                   run_llm_backtest)
from services.llm_direction_score import CLOSE_LOOKUP_DAYS, DIRECTION_SIGN, HORIZONS, horizon_stats

GRADED = ("correct", "wrong", "flat")
EVIDENCE_SCORE = {"strong": 1.0, "adequate": 0.5, "weak": 0.0}


def prompt_versions(model: str, replay_strategist: bool) -> dict[str, str | None]:
    return {"manager": STORED_PROMPT_VERSION if model == STORED_MODEL else MANAGER_PROMPT_VERSION,
            "strategist": strategist_prompt_version(EvidenceThesisPolicy.from_env()) if replay_strategist else None}


def run_benchmark(set_id: str, *, split: str, model: str, replay_strategist: bool = False, allow_unknown_cutoff: bool = False,
                  decide=None, decide_strategist=None, db_path: Any = None) -> dict[str, Any]:
    """Replay and score `split` of the set with `model`, store the run and return it as stored.

    Raises ValueError for an unknown or empty split, for a holdout this model already scored (with any prompts or
    strategist flag; before any model call), and, from the replay, for a model with no known cutoff unless
    `allow_unknown_cutoff`.
    """
    strata = {item["manager_decision_history_id"]: item["stratum"]
              for item in list_benchmark_items(set_id, split=split, db_path=db_path)}
    ids = list(strata)
    if not ids:
        raise ValueError(f"Benchmark set '{set_id}' has no {split} items")
    versions = prompt_versions(model, replay_strategist)
    if split == "holdout" and list_benchmark_runs(set_id, split="holdout", model=model, db_path=db_path):
        raise ValueError(f"The holdout of '{set_id}' was already scored for {model}; it is single-use per model")
    backtest = run_llm_backtest(model=model, decision_ids=ids, replay_strategist=replay_strategist,
                                allow_unknown_cutoff=allow_unknown_cutoff, decide=decide, decide_strategist=decide_strategist,
                                db_path=db_path)
    item_scores = _item_scores(ids, strata, backtest, replay_strategist, db_path)
    scores = _scores(item_scores, backtest["run"]["metrics"]["replay"], replay_strategist)
    run_id = record_benchmark_run(set_id=set_id, split=split, model=model, prompt_versions=versions,
                                  replay_strategist=replay_strategist, scores=scores, item_scores=item_scores,
                                  backtest_run_id=backtest["run"]["id"], db_path=db_path)
    return get_benchmark_run(run_id, db_path=db_path)


# --- Scoring one item -----------------------------------------------------------------------------------------------

def _item_scores(ids: Sequence[int], strata: dict[int, str], backtest: dict[str, Any], replay_strategist: bool,
                 db_path: Any) -> dict[str, dict[str, Any]]:
    answers = {answer.decision["id"]: answer for answer in backtest["answers"]}
    pnl = {event["manager_decision_history_id"]: event["realized_pnl_pct"] for event in backtest["events"]
           if event["realized_pnl_pct"] is not None}
    catalysts = primary_catalysts(db_path=db_path)
    unanswered = _unanswered_status(sorted(set(ids) - set(answers)), db_path)
    scores = {}
    for decision_id in sorted(ids):
        answer = answers.get(decision_id)
        if answer is None or answer.unavailable is not None:
            layers = {"decision": None, "evidence": None, "direction": None, "expression": None}
            status = unanswered[decision_id] if answer is None else answer.unavailable
        else:
            layers, status = _layers(answer, decision_id in pnl, pnl.get(decision_id), catalysts, replay_strategist, db_path), "scored"
        scores[str(decision_id)] = {"status": status, "stratum": strata[decision_id], **layers}
    return scores


def _unanswered_status(ids: Sequence[int], db_path: Any) -> dict[int, str]:
    """Why items never reached the model: the contamination guard dropped them, or their frozen input is missing."""
    listed = {decision["id"]: decision for decision in list_frozen_manager_decisions(ids=ids, db_path=db_path)} if ids else {}
    statuses = {}
    for decision_id in ids:
        decision = listed.get(decision_id)
        if decision is None:
            statuses[decision_id] = "missing_decision"
        elif frozen_manager_input(decision) is not None:
            statuses[decision_id] = "excluded_pre_knowledge_cutoff"
        else:
            statuses[decision_id] = "missing_frozen_input"
    return statuses


def _layers(answer: ReplayedAnswer, traded: bool, pnl_pct: float | None, catalysts: dict[str, Any], replay_strategist: bool,
            db_path: Any) -> dict[str, Any]:
    decision = answer.decision
    decided_at = timestamp(decision["decision_run_at"])
    closes = daily_closes_after(decision["symbol"], decided_at, until=decided_at + timedelta(days=CLOSE_LOOKUP_DAYS),
                                db_path=db_path)
    replayed = {**decision, "manager_decision": answer.recommendation.get("decision"),
                "trade_executed": traded, "realized_pnl_pct": pnl_pct}
    strategist = answer.strategist_answer or {}
    payload = {**answer.payload, "strategist_recommendation": strategist} if replay_strategist else answer.payload
    catalyst = catalysts.get(decision.get("candidate_id"))
    attribution = attribute(replayed, payload, strategist.get("evidence_thesis_check"),
                            [catalyst[1]] if catalyst else [], closes)
    return {"decision": str(replayed["manager_decision"]).lower(),
            "evidence": attribution["evidence"] if replay_strategist else None,
            "direction": attribution["direction"], "expression": attribution["expression"]}


# --- Aggregates -----------------------------------------------------------------------------------------------------

def _scores(item_scores: dict[str, dict[str, Any]], replay: dict[str, Any], replay_strategist: bool) -> dict[str, Any]:
    scored = [item for item in item_scores.values() if item["status"] == "scored"]
    by_stratum = defaultdict(list)
    for item in scored:
        by_stratum[item["stratum"]].append(item)
    return {
        "item_count": len(item_scores), "scored_count": len(scored), "replay": replay,
        "evidence": ({"mode": "replayed", "quality_mix": dict(Counter(item["evidence"]["quality"] for item in scored))}
                     if replay_strategist else {"mode": "fixed"}),
        "direction": {**_direction_stats(scored),
                      "abstention_rate": (sum(item["decision"] == "neither" for item in scored) / len(scored)) if scored else None},
        "expression": _expression_stats(scored),
        "baseline": {"unconditional_stock_moves": horizon_stats(_stock_moves(scored), "stock_return_pct")},
        "by_stratum": {stratum: {"scored": len(by_stratum[stratum]), "direction": _direction_stats(by_stratum[stratum])}
                       for stratum in sorted({item["stratum"] for item in item_scores.values()})},
    }


def _direction_stats(items: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats = {}
    for horizon in map(str, HORIZONS):
        graded = [item for item in items if item["direction"]["horizon_outcomes"][horizon] in GRADED]
        # Sorted so the seeded bootstrap does not depend on item order.
        hits = sorted((int(item["direction"]["horizon_outcomes"][horizon] == "correct") for item in graded), reverse=True)
        returns = sorted(value for item in items if (value := direction_value(item, horizon)) is not None
                         and item["decision"] != "neither")
        # `hit_rate` follows Plan C (a move inside its flat band is a miss); the baseline counts any positive signed return.
        sign_hits = [int(direction_value(item, horizon) > 0) for item in graded]
        stats[horizon] = {"graded": len(hits), "hit_rate": mean(hits) if hits else None,
                          "hit_rate_se": bootstrap_se(hits),
                          "sign_hit_rate": mean(sign_hits) if sign_hits else None,
                          "mean_signed_return": mean(returns) if returns else None, "mean_signed_return_se": bootstrap_se(returns)}
    return stats


def _expression_stats(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    traded = [item["expression"] for item in items if item["expression"]["outcome"] != "not_traded"]
    pnl = sorted(expression["pnl_pct"] for expression in traded if expression["pnl_pct"] is not None)
    captured = sum(expression["outcome"] == "captured" for expression in traded)
    return {"traded": len(traded), "captured": captured, "capture_rate": captured / len(traded) if traded else None,
            "mean_pnl_pct": mean(pnl) if pnl else None, "mean_pnl_pct_se": bootstrap_se(pnl)}


def _stock_moves(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows of the stock's own move after each decision that has every horizon's return, in `llm_direction_score`'s shape."""
    returns = [item["direction"]["horizon_returns"] for item in items]
    return [{"stock_return_pct": {horizon: round(move[str(horizon)] * 100, 3) for horizon in HORIZONS}}
            for move in returns if all(str(horizon) in move for horizon in HORIZONS)]


# --- Comparison -----------------------------------------------------------------------------------------------------

def direction_value(item: dict[str, Any], horizon: str) -> float | None:
    """The stock's return at `horizon` in the direction the item called; an abstention scores 0."""
    move = ((item.get("direction") or {}).get("horizon_returns") or {}).get(horizon)
    return None if move is None else DIRECTION_SIGN.get(item["decision"], 0) * move


def evidence_value(item: dict[str, Any]) -> float | None:
    return EVIDENCE_SCORE.get(item["evidence"]["quality"]) if item.get("evidence") else None


def sign_hit_value(item: dict[str, Any], horizon: str) -> int | None:
    """1 if a call or put moved the stock its way at `horizon`, else 0; None for an abstention or a missing return."""
    value = direction_value(item, horizon)
    return None if value is None or item["decision"] not in DIRECTION_SIGN else int(value > 0)


def expression_value(item: dict[str, Any]) -> float | None:
    return (item.get("expression") or {}).get("pnl_pct")


def compare_runs(run_a: dict[str, Any], run_b: dict[str, Any]) -> dict[str, Any]:
    """Paired comparison of two runs' `item_scores`: per layer, the mean of (b minus a) over the items both scored.

    Items are paired by decision id and only items scored in both runs count; the others are listed in `excluded`
    with each run's status. A layer pairs an item only where both runs have a value for it. Direction compares the
    signed return per horizon and counts the items where b beat, lost to, or matched a; its `sign_hit_rate` pairs the
    items both runs called in a direction.
    """
    items_a, items_b = run_a["item_scores"], run_b["item_scores"]

    def status(items, decision_id) -> str:
        return items[decision_id]["status"] if decision_id in items else "absent"

    ids = sorted(set(items_a) | set(items_b), key=int)
    paired = [decision_id for decision_id in ids if status(items_a, decision_id) == status(items_b, decision_id) == "scored"]

    def layer(value, *args) -> list[float]:
        pairs = [(value(items_a[i], *args), value(items_b[i], *args)) for i in paired]
        return [b - a for a, b in pairs if a is not None and b is not None]

    def summary(diffs: list[float]) -> dict[str, Any]:
        return {"n": len(diffs), "delta": mean(diffs) if diffs else None, "se": bootstrap_se(diffs)}

    direction = {}
    for horizon in map(str, HORIZONS):
        diffs = layer(direction_value, horizon)
        direction[horizon] = {**summary(diffs), "wins": sum(diff > 0 for diff in diffs),
                              "losses": sum(diff < 0 for diff in diffs), "ties": sum(diff == 0 for diff in diffs),
                              "sign_hit_rate": summary(layer(sign_hit_value, horizon))}
    return {"paired_items": len(paired),
            "excluded": {i: {"a": status(items_a, i), "b": status(items_b, i)} for i in ids if i not in paired},
            "layers": {"evidence": summary(layer(evidence_value)), "direction": direction,
                       "expression": summary(layer(expression_value))}}


def latest_run(set_id: str, split: str, spec: str, *, db_path: Any = None) -> dict[str, Any]:
    """The newest run of `MODEL` or `MODEL:PROMPTS` (the manager prompt version) on the split. Model ids may contain ':'."""
    candidates = [(spec, None)] + ([tuple(spec.rsplit(":", 1))] if ":" in spec else [])
    for model, prompts in candidates:
        runs = [run for run in list_benchmark_runs(set_id, split=split, model=model, db_path=db_path)
                if prompts is None or run["prompt_versions"]["manager"] == prompts]
        if runs:
            return runs[-1]
    raise ValueError(f"No {split} run of '{spec}' on benchmark set '{set_id}'")


def compare_benchmark(set_id: str, *, a: str, b: str, split: str = "dev", db_path: Any = None) -> dict[str, Any]:
    runs = [latest_run(set_id, split, spec, db_path=db_path) for spec in (a, b)]
    return {"set_id": set_id, "split": split,
            "a": {"model": runs[0]["model"], "run_id": runs[0]["run_id"]},
            "b": {"model": runs[1]["model"], "run_id": runs[1]["run_id"]}, **compare_runs(*runs)}
