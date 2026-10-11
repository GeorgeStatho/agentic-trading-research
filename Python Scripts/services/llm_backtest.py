"""Replay a model on frozen manager inputs, finalize each answer like production, and backtest it.

For every stored manager decision: the configured model answers the frozen manager prompt (or
the stored answer is reused, for a like-for-like baseline); the live deterministic steps run as
of the decision's own timestamp (continuation gate, contract selection, regime and
evidence-thesis gates); and the live order-eligibility rule decides whether it trades.

Production selects a directional contract from a chain it re-fetches for the manager's DTE
bucket, never from the pre-decision chain in the frozen input. That bucket chain was saved
(candidate snapshots) only for the bucket production chose, and only since snapshots began, so
a replayed answer naming another bucket, or an older decision, is `bucket_chain_unavailable`.
Trades go through the backtest engine: stored option quotes when they exist, otherwise
Black-Scholes marks from the frozen chain and daily underlying closes.

Model answers are cached per frozen input, so re-running with new execution settings never asks
the model again. Only the manager is replayed: the strategist's recorded output stays in the
payload. Trade economics is not replayed; it is shadow-only in production. No broker is called.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
from typing import Any, Callable

from db_common import DB_PATH
from db_helpers.market import (
    get_cached_llm_replay,
    get_saved_bucket_chain,
    list_frozen_manager_decisions,
    run_decision_backtest,
    store_cached_llm_replay,
)
from agent_helpers.deterministic_option_selector import SELECTOR_VERSION, apply_deterministic_option_selection
from agent_helpers.deterministic_option_selector import config as selector_config
from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
from agent_helpers.shared import _normalize_provider
from agent_stages.manager_runner import MANAGER_PROMPT_VERSION, replay_manager_decision_from_payload
from services.config import FrontMainSettings
from services.entry_gates import carry_gate_verdicts
from services.evaluation_clock import evaluated_at
from services.evidence_continuation_gate import evaluate_evidence_continuation_gate
from services.evidence_thesis import evaluate_evidence_thesis_gate
from services.evidence_thesis_policy import EvidenceThesisPolicy
from services.market_regime_gate import apply_market_regime_gate_to_manager_result
from services.option_dte_buckets import normalize_target_dte_bucket
from services.order_candidates import OrderCandidateBuilder
from services.trade_economics_policy import TradeEconomicsPolicy

LOGGER = logging.getLogger(__name__)

STORED_MODEL = "stored"
STAGE = "manager"
DIRECTIONAL = frozenset({"call", "put"})
STRATEGY_VERSION = "llm-replay-manager-v1"
# A run of failures this long means the model server is down, not that inputs are bad.
MAX_CONSECUTIVE_FAILURES = 5
# Fields the selector adds; a stored answer is re-finalized from the manager's own fields.
SELECTION_FIELDS = frozenset({
    "confidence_after_volatility", "selected_expiration_date", "selected_option_id",
    "selected_option_source", "selected_strike_price", "selection_debug",
})

Decider = Callable[[dict[str, Any]], dict[str, Any]]


class ModelUnavailable(RuntimeError):
    pass


def frozen_payload(decision: dict[str, Any]) -> dict[str, Any] | None:
    manager_input = json.loads(decision.get("manager_input_json") or "{}")
    payload = manager_input.get("replay_input")
    return payload if isinstance(payload, dict) and isinstance(payload.get("company"), dict) else None


def stored_recommendation(decision: dict[str, Any]) -> dict[str, Any]:
    output = json.loads(decision.get("manager_output_json") or "{}")
    recommendation = output.get("recommendation") or {}
    return {key: value for key, value in recommendation.items() if key not in SELECTION_FIELDS}


def input_hash(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass
class CachedManagerReplay:
    """One model answer per frozen input, asked once and then read back."""
    model: str
    provider: str
    decide: Decider
    db_path: Any = DB_PATH

    def recommendation(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        key = {"stage": STAGE, "provider": self.provider, "model": self.model,
               "prompt_version": MANAGER_PROMPT_VERSION, "input_hash": input_hash(payload)}
        cached = get_cached_llm_replay(**key, db_path=self.db_path)
        if cached is not None:
            return cached, True
        recommendation = self.decide(payload)["recommendation"]
        store_cached_llm_replay(**key, output=recommendation, db_path=self.db_path)
        return recommendation, False


def selection_market_context(payload: dict[str, Any], recommendation: dict[str, Any],
                             candidate_id: str | None, *, db_path: Any = DB_PATH) -> dict[str, Any] | None:
    """The market context production would have selected from; None when that chain was never saved.

    Like production, a non-directional answer or a bucket of `none` keeps the pre-decision chain.
    """
    market = copy.deepcopy(payload.get("market_context") or {})
    bucket = normalize_target_dte_bucket(recommendation.get("target_dte_bucket")) or "none"
    if recommendation.get("decision") not in DIRECTIONAL or bucket == "none":
        return market
    saved = get_saved_bucket_chain(candidate_id, db_path=db_path)
    if saved is None or normalize_target_dte_bucket(saved["target_dte_bucket"]) != bucket:
        return None
    market["option_market"] = copy.deepcopy(saved["option_market"])
    return market


def finalize(payload: dict[str, Any], recommendation: dict[str, Any], market: dict[str, Any], *, as_of: str,
             thesis_policy: EvidenceThesisPolicy) -> dict[str, Any]:
    """The live post-manager steps, read against the decision's own timestamp."""
    result = {"company": payload.get("company"), "candidate_context": None, "market_context": market,
              "strategist_recommendation": payload.get("strategist_recommendation") or {},
              "recommendation": dict(recommendation), "selected_option": None}
    with evaluated_at(as_of):
        result["entry_gate"] = evaluate_evidence_continuation_gate(
            recommendation=recommendation, payload=payload, market_context=market)
        result = apply_deterministic_option_selection(result)
        result = apply_market_regime_gate_to_manager_result(result)
        thesis_gate = evaluate_evidence_thesis_gate(result["strategist_recommendation"], recommendation.get("decision"),
                                                    policy=thesis_policy)
        if thesis_gate is not None:
            result["evidence_thesis_gate"] = thesis_gate
    return result


def trade_outcome(result: dict[str, Any], builder: OrderCandidateBuilder) -> str:
    recommendation = result.get("recommendation") or {}
    if recommendation.get("decision") not in DIRECTIONAL:
        return "non_directional"
    view = {"decision": recommendation.get("decision"), "confidence": recommendation.get("confidence"),
            "strategist_recommendation": result.get("strategist_recommendation"), **carry_gate_verdicts(result)}
    if not builder.recommendation_allows_entry(view):
        return "not_eligible"
    if not (result.get("selected_option") or {}).get("symbol"):
        return "no_contract"
    return "traded"


def backtest_decision(decision: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """The engine's decision shape, carrying the frozen contract for model pricing."""
    recommendation = result["recommendation"]
    contract = result["selected_option"]
    return {**decision, "manager_decision": recommendation.get("decision"),
            "manager_reason": recommendation.get("reason"), "selected_option_symbol": contract.get("symbol"),
            "selected_contract": contract, "reference_spot": _get_reference_stock_price(result["market_context"])}


def manager_decider(model: str) -> Decider:
    return lambda payload: replay_manager_decision_from_payload(payload, model=model)


def run_llm_backtest(
    *,
    model: str,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int | None = None,
    execution_config: dict[str, Any] | None = None,
    decide: Decider | None = None,
    db_path: Any = DB_PATH,
) -> dict[str, Any]:
    """Replay `model` (or `stored`) over the window and record the trades as a backtest run."""
    stored = model == STORED_MODEL
    replay = None if stored else CachedManagerReplay(model=model, provider=_normalize_provider(),
                                                     decide=decide or manager_decider(model), db_path=db_path)
    thesis_policy = EvidenceThesisPolicy.from_env()
    builder = OrderCandidateBuilder(
        execute_medium_confidence_trades=FrontMainSettings.from_env().execute_medium_confidence_trades)
    decisions = list_frozen_manager_decisions(start_at=start_at, end_at=end_at, limit=limit, db_path=db_path)
    outcomes: dict[str, int] = {}
    traded: list[dict[str, Any]] = []
    changed_direction = cached_answers = consecutive_failures = 0
    for index, decision in enumerate(decisions, start=1):
        payload = frozen_payload(decision)
        if payload is None:
            outcome = "missing_frozen_input"
        else:
            try:
                recommendation, cached = (stored_recommendation(decision), True) if stored else replay.recommendation(payload)
                consecutive_failures = 0
            except Exception as exc:  # One bad answer is recorded; a dead server stops the run.
                consecutive_failures += 1
                LOGGER.warning("Replay failed for decision %s: %s", decision["id"], exc)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise ModelUnavailable(f"{consecutive_failures} replays failed in a row; last error: {exc}") from exc
                recommendation, cached = None, False
            if recommendation is None:
                outcome = "model_error"
            else:
                cached_answers += cached
                changed_direction += recommendation.get("decision") != str(decision.get("manager_decision") or "").lower()
                market = selection_market_context(payload, recommendation, decision.get("candidate_id"), db_path=db_path)
                if market is None:
                    outcome = "bucket_chain_unavailable"
                else:
                    result = finalize(payload, recommendation, market, as_of=decision["decision_run_at"],
                                      thesis_policy=thesis_policy)
                    outcome = trade_outcome(result, builder)
                    if outcome == "traded":
                        traded.append(backtest_decision(decision, result))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        LOGGER.info("[%d/%d] %s %s -> %s", index, len(decisions), decision["symbol"], decision["decision_run_at"], outcome)
    config = {
        **(execution_config or {}),
        "strategy_version": STRATEGY_VERSION,
        "model": model,
        "prompt_version": "stored-output" if stored else MANAGER_PROMPT_VERSION,
        "risk_free_rate": TradeEconomicsPolicy.from_env().risk_free_rate,
        "data_source_versions": {"option_quotes": "historical_option_quotes",
                                 "fallback": "black_scholes_frozen_chain_daily_closes"},
    }
    replay_summary = {"source_decision_count": len(decisions), "outcomes": outcomes,
                      "changed_direction_count": changed_direction, "cached_answer_count": cached_answers,
                      "provider": STORED_MODEL if stored else replay.provider,
                      # Selection only matches production when these match the server's settings.
                      "selector": {"version": SELECTOR_VERSION, "mode": selector_config.OPTION_SELECTOR_MODE,
                                   "allow_risky_simple_fallback": selector_config.ALLOW_RISKY_SIMPLE_FALLBACK}}
    return run_decision_backtest(traded, config=config, extra_metrics={"replay": replay_summary}, db_path=db_path)
