"""Replay a model on frozen manager inputs, finalize each answer like production, and backtest it.

For every stored manager decision: the configured model answers the frozen manager prompt (or
the stored answer is reused, for a like-for-like baseline); the live deterministic steps run as
of the decision's own timestamp (continuation gate, contract selection, regime and
evidence-thesis gates); and the live order-eligibility rule decides whether it trades.

Production selects a directional contract from a chain it re-fetches for the manager's DTE
bucket, never from the pre-decision chain in the frozen input. That bucket chain was saved
(candidate snapshots) only for the bucket production chose, and only since snapshots began, so
a replayed answer naming another bucket, or an older decision, selects from the pre-decision chain
instead: an approximation of production's contract, counted as `pre_decision_chain_trade_count`.
Without contracts in that chain either, the answer is `bucket_chain_unavailable`.
Trades go through the backtest engine: stored option quotes when they exist, otherwise
Black-Scholes marks from the frozen chain and daily underlying closes.

Eligibility, the premium cap and portfolio limits use the settings production recorded in each
candidate's pipeline manifest, not this checkout's environment; a decision whose recorded selector
settings differ from the local ones is reported rather than re-selected under different rules.

Model answers are cached per frozen input, so re-running with new execution settings never asks
the model again. By default only the manager is replayed and the strategist's recorded output stays
in the payload. With `replay_strategist`, the model also answers the strategist's frozen input, and
that answer replaces production's in the manager input, projected exactly as production hands it
over. With `strategist_reuse_max_age_minutes`, a symbol's replayed strategist answer is reused for its
later decisions under production's reuse rule (unchanged evidence and regime, a small price move, younger
than the limit), measured against this replay's own earlier answers in time order. Trade economics is not
replayed; it is shadow-only in production. No broker is called.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields, replace
from hashlib import sha256
import json
import logging
import time
from statistics import median
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator, Sequence

from market_db_modules.backtest_marketdata import fetch_marketdata_eod_quotes_for_contracts
from market_db_modules.foundation_store import SnapshotHashMismatch
from market_db_modules.llm_replay_store import (
    FrozenDecision,
    frozen_manager_input,
    get_cached_llm_replay,
    list_frozen_manager_decisions,
    store_cached_llm_replay,
    symbols_without_daily_bars,
)
from market_db_modules.backtesting import run_decision_backtest
from market_db_modules.market_regime import store_regime_bars
from agent_helpers.deterministic_option_selector import SELECTOR_VERSION, apply_deterministic_option_selection
from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
from agent_helpers.llm_client import _normalize_provider
from agent_stages.manager_runner import MANAGER_PROMPT_VERSION, replay_manager_decision_from_payload
from agent_stages.manager_strategist_context import _build_manager_visible_strategist_context
from agent_stages.strategist_runner import (THESIS_ROUTE, attach_thesis_check, replay_strategist_decision_from_payload,
                                            strategist_answer_reusable, strategist_prompt_version)
from services.core.trading_config import AgentPipelineSettings, WorkerSettings
from services.gates.entry_gates import carry_gate_verdicts
from services.evaluation.evaluation_clock import evaluated_at
from services.gates.evidence_continuation_gate import evaluate_evidence_continuation_gate
from services.thesis.evidence_thesis import (LEGACY_RESEARCH_STATUS, check_evidence_thesis, compact_thesis_check,
                                             evaluate_evidence_thesis_gate)
from services.thesis.evidence_thesis_policy import EvidenceThesisPolicy
from services.evaluation.model_cutoffs import CONTAMINATION_MARGIN_DAYS, cutoff_record, eligibility
from services.gates.market_regime_gate import apply_market_regime_gate_to_manager_result
from services.options.option_dte_buckets import normalize_target_dte_bucket
from services.trading.order_candidates import OrderCandidateBuilder
from services.core.pipeline_version import selector_constants
from services.core.settings import get_settings
from services.gates.regime_history import fetch_daily_history
from services.gates.trade_economics_policy import TradeEconomicsPolicy

LOGGER = logging.getLogger(__name__)

STORED_MODEL = "stored"
STORED_PROMPT_VERSION = "stored-output"
STAGE = "manager"
STRATEGIST_STAGE = "strategist"
DIRECTIONAL = frozenset({"call", "put"})
STRATEGY_VERSION = "llm-replay-manager-v1"
# Quote windows match the engine's defaults: next-day entry at the latest, then the longest hold.
QUOTE_ENTRY_DELAY = timedelta(days=1)
DEFAULT_HOLD_HOURS = 168
PROGRESS_EVERY = 5
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


def stored_recommendation(decision: dict[str, Any]) -> dict[str, Any]:
    output = json.loads(decision.get("manager_output_json") or "{}")
    recommendation = output.get("recommendation") or {}
    return {key: value for key, value in recommendation.items() if key not in SELECTION_FIELDS}


def input_hash(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def cached_answer(key: dict[str, str], ask: Callable[[], dict[str, Any]], *, db_path: Any) -> tuple[dict[str, Any], bool]:
    """One model answer per cache key, asked once and then read back."""
    cached = get_cached_llm_replay(**key, db_path=db_path)
    if cached is not None:
        return cached, True
    answer = ask()
    store_cached_llm_replay(**key, output=answer, db_path=db_path)
    return answer, False


@dataclass
class CachedManagerReplay:
    """One manager answer per frozen input."""
    model: str
    provider: str
    decide: Decider
    db_path: Any = None
    sample: int = 0

    def recommendation(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        # Sample 0 keeps the original key, so earlier runs count as the first sample.
        suffix = f":sample{self.sample}" if self.sample else ""
        key = {"stage": STAGE, "provider": self.provider, "model": self.model,
               "prompt_version": MANAGER_PROMPT_VERSION, "input_hash": input_hash(payload) + suffix}
        return cached_answer(key, lambda: self.decide(payload)["recommendation"], db_path=self.db_path)


@dataclass
class StrategistReplay:
    """One strategist answer per frozen strategist input, handed to the manager as production would."""
    model: str
    provider: str
    thesis_policy: EvidenceThesisPolicy
    decide: Decider
    db_path: Any = None
    reuse_max_age_minutes: int = 0
    reuse_max_price_move_pct: float = 2.0
    sample: int = 0
    # Each symbol's last answer that came from the model (or its cache): (decided_at, input, answer).
    answered: dict[str, tuple[datetime, dict[str, Any], dict[str, Any]]] = field(default_factory=dict, repr=False)

    @property
    def prompt_version(self) -> str:
        return strategist_prompt_version(self.thesis_policy)

    def answer(self, strategist_input: dict[str, Any], *, symbol: str,
               decided_at: datetime) -> tuple[dict[str, Any], bool, bool]:
        """(answer, cached, reused). Production's reuse rule over this replay's earlier answers, then the model."""
        previous = self.answered.get(symbol)
        if previous is not None and strategist_answer_reusable(
                previous[1], strategist_input, age_minutes=(decided_at - previous[0]).total_seconds() / 60,
                max_age_minutes=self.reuse_max_age_minutes, max_price_move_pct=self.reuse_max_price_move_pct):
            return copy.deepcopy(previous[2]), False, True
        answer, cached = self.recommendation(strategist_input)
        if self.reuse_max_age_minutes > 0:
            self.answered[symbol] = (decided_at, strategist_input, answer)
        return answer, cached, False

    def recommendation(self, strategist_input: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        # Same suffix as the manager's: sample 0 is the existing entry, sample N a fresh answer to measure noise.
        suffix = f":sample{self.sample}" if self.sample else ""
        key = {"stage": STRATEGIST_STAGE, "provider": self.provider, "model": self.model,
               "prompt_version": self.prompt_version, "input_hash": input_hash(strategist_input) + suffix}
        return cached_answer(key, lambda: self.checked_recommendation(strategist_input), db_path=self.db_path)

    def checked_recommendation(self, strategist_input: dict[str, Any]) -> dict[str, Any]:
        # The thesis check is attached as production attaches it, but never persisted.
        replay = self.decide(strategist_input)
        check = replay.get("thesis_check")
        return attach_thesis_check(replay["recommendation"], check, None) if check else replay["recommendation"]

    def manager_payload(self, payload: dict[str, Any], recommendation: dict[str, Any]) -> dict[str, Any]:
        """The frozen manager input with this answer in place of production's strategist's.

        Production's run reference is kept with this replay's model and prompt version, so the
        manager prompt differs only where the strategist's answer does.
        """
        replayed = copy.deepcopy(payload)
        answer = copy.deepcopy(recommendation)
        reference = (payload.get("strategist_recommendation") or {}).get("strategist_run_reference")
        if isinstance(reference, dict):
            answer["strategist_run_reference"] = {**reference, "model": self.model, "prompt_version": self.prompt_version}
        replayed["strategist_recommendation"] = answer
        replayed["strategist_recommendation"] = _build_manager_visible_strategist_context(replayed)
        return replayed


@dataclass(frozen=True)
class ProductionRules:
    """The settings a decision ran under: its recorded manifest's, or local ones when none exists."""
    settings: WorkerSettings
    selector_config: dict[str, Any] | None = None

    @property
    def recorded(self) -> bool:
        return self.selector_config is not None

    @classmethod
    def for_manifest(cls, manifest: dict[str, Any] | None, *, local: WorkerSettings) -> "ProductionRules":
        if manifest is None:
            return cls(local)
        runtime = manifest.get("runtime_settings") or {}
        recorded = {setting.name: runtime[setting.name] for setting in fields(local)
                    if setting.name in runtime and isinstance(getattr(local, setting.name), (bool, int, float, str))}
        return cls(replace(local, **recorded), manifest.get("selector_config") or {})

    def executing_medium_confidence(self) -> "ProductionRules":
        """The same rules with medium-confidence entries allowed: scores a model's direction apart from its confidence."""
        return replace(self, settings=replace(self.settings, execute_medium_confidence_trades=True))

    def selector_differences(self, current: dict[str, Any]) -> list[str]:
        if not self.recorded:
            return []
        return sorted(key for key in set(current) | set(self.selector_config)
                      if current.get(key) != self.selector_config.get(key))

    def portfolio_limits(self, buying_powers: list[float]) -> dict[str, Any]:
        limits = {"max_concurrent_positions": self.settings.max_concurrent_option_positions,
                  "max_sector_notional_pct": self.settings.max_option_sector_exposure_pct,
                  "daily_loss_limit_dollars": self.settings.daily_option_loss_limit_dollars}
        if buying_powers:
            limits["max_total_entry_notional"] = median(buying_powers) * self.settings.max_deployable_buying_power_ratio
        return limits


def frozen_buying_power(payload: dict[str, Any]) -> float:
    account = (payload.get("market_context") or {}).get("account_state") or {}
    try:
        return float(account.get("buying_power") or 0)
    except (TypeError, ValueError):
        return 0.0


def selection_market_context(payload: dict[str, Any], recommendation: dict[str, Any],
                             frozen: FrozenDecision) -> tuple[dict[str, Any] | None, bool]:
    """The market context to select from, and whether the pre-decision chain stands in for an unsaved bucket chain.

    Like production, a non-directional answer or a bucket of `none` keeps the pre-decision chain. A bucket
    production never fetched falls back to that chain too (the selector still enforces the bucket's DTE range);
    None when it has no contracts either.
    """
    market = copy.deepcopy(payload.get("market_context") or {})
    bucket = normalize_target_dte_bucket(recommendation.get("target_dte_bucket")) or "none"
    if recommendation.get("decision") not in DIRECTIONAL or bucket == "none":
        return market, False
    option_market = frozen.bucket_chain(bucket)
    if option_market is None:
        return (market, True) if (market.get("option_market") or {}).get("contracts") else (None, False)
    market["option_market"] = copy.deepcopy(option_market)
    return market, False


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


def backtest_decision(decision: dict[str, Any], result: dict[str, Any], *, max_premium_dollars: float) -> dict[str, Any]:
    """The engine's decision shape, carrying the frozen contract for model pricing."""
    recommendation = result["recommendation"]
    contract = result["selected_option"]
    return {**decision, "manager_decision": recommendation.get("decision"),
            "manager_reason": recommendation.get("reason"), "selected_option_symbol": contract.get("symbol"),
            "selected_contract": contract, "reference_spot": _get_reference_stock_price(result["market_context"]),
            "max_premium_dollars": max_premium_dollars}


@dataclass
class ReplayTally:
    outcomes: dict[str, int] = field(default_factory=dict)
    changed_direction: int = 0
    cached_answers: int = 0
    strategist_changed_decision: int = 0
    strategist_cached_answers: int = 0
    strategist_reused_answers: int = 0
    thesis_rechecked: int = 0
    thesis_recheck_changed: int = 0
    recorded_settings: int = 0
    pre_decision_chain_trades: int = 0
    selector_differences: set[str] = field(default_factory=set)
    # side ("production" or "replay") -> strategist decision -> thesis check status -> count
    strategist_thesis_statuses: dict[str, dict[str, dict[str, int]]] = field(
        default_factory=lambda: {"production": {}, "replay": {}})

    def count(self, outcome: str) -> None:
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1

    def count_thesis(self, side: str, recommendation: dict[str, Any]) -> None:
        status = (recommendation.get("evidence_thesis_check") or {}).get("status") or "missing"
        by_status = self.strategist_thesis_statuses[side].setdefault(str(recommendation.get("decision")), {})
        by_status[status] = by_status.get(status, 0) + 1


def evaluate_answer(frozen: FrozenDecision, payload: dict[str, Any], recommendation: dict[str, Any],
                    rules: ProductionRules, tally: ReplayTally, *,
                    thesis_policy: EvidenceThesisPolicy) -> tuple[str, dict[str, Any] | None]:
    """One answer through production's selection and eligibility; returns the outcome and any trade."""
    decision = frozen.decision
    market, pre_decision_chain = selection_market_context(payload, recommendation, frozen)
    if market is None:
        return "bucket_chain_unavailable", None
    differences = rules.selector_differences(selector_constants())
    if recommendation.get("decision") in DIRECTIONAL and differences:
        tally.selector_differences.update(differences)
        return "selector_config_mismatch", None
    result = finalize(payload, recommendation, market, as_of=decision["decision_run_at"], thesis_policy=thesis_policy)
    builder = OrderCandidateBuilder(execute_medium_confidence_trades=rules.settings.execute_medium_confidence_trades)
    outcome = trade_outcome(result, builder)
    if outcome != "traded":
        return outcome, None
    tally.pre_decision_chain_trades += pre_decision_chain
    cap = rules.settings.resolve_max_premium_per_trade(frozen_buying_power(payload))
    return outcome, backtest_decision(decision, result, max_premium_dollars=cap)


def fill_missing_daily_closes(symbols: set[str], *, since: str, db_path: Any = None,
                              fetch: Callable[[str], tuple[str, list[dict[str, Any]]]] = fetch_daily_history) -> dict[str, Any]:
    """Store daily history for traded symbols that have none, so model-priced exits can be marked."""
    return store_daily_history(symbols_without_daily_bars(symbols, since=since, db_path=db_path), db_path=db_path,
                               fetch=fetch)


def store_daily_history(symbols: set[str], *, db_path: Any = None,
                        fetch: Callable[[str], tuple[str, list[dict[str, Any]]]] = fetch_daily_history) -> dict[str, Any]:
    """Download and store each symbol's daily history; a provider failure is reported, not raised."""
    fetched, failed = [], {}
    for symbol in sorted(symbols):
        try:
            provider, bars = fetch(symbol)
            store_regime_bars(symbol, bars, provider=provider, db_path=db_path)
            fetched.append(symbol)
        except Exception as exc:  # A provider gap leaves that symbol's exits unpriced, as before.
            failed[symbol] = str(exc)
    return {"fetched": fetched, "failed": failed}


def rechecked_thesis_payload(payload: dict[str, Any], strategist_input: dict[str, Any] | None, *,
                             policy: EvidenceThesisPolicy, db_path: Any = None) -> tuple[dict[str, Any], bool | None]:
    """The frozen manager input with its stored thesis re-checked by this checkout's checker.

    Returns the payload and whether the check's verdict changed (None when there was no thesis to check).
    The check reads the frozen evidence package by id, so it sees only what production saw.
    """
    strategist = payload.get("strategist_recommendation") or {}
    old = strategist.get("evidence_thesis_check")
    if not isinstance(old, dict) or not isinstance(strategist.get("evidence_thesis"), dict):
        return payload, None
    check = check_evidence_thesis(strategist["evidence_thesis"], strategist_input or payload, route=THESIS_ROUTE,
                                  research_status=old.get("research_status") or LEGACY_RESEARCH_STATUS,
                                  policy=policy, db_path=db_path)
    new = compact_thesis_check(check, old.get("evaluation_id"))
    verdict = ("status", "failures", "failed_claims")
    changed = any(old.get(key) != new[key] for key in verdict)
    return {**payload, "strategist_recommendation": {**strategist, "evidence_thesis_check": new}}, changed


def manager_decider(model: str) -> Decider:
    return lambda payload: replay_manager_decision_from_payload(payload, model=model)


def strategist_decider(model: str, thesis_policy: EvidenceThesisPolicy) -> Decider:
    return lambda payload: replay_strategist_decision_from_payload(payload, model=model, thesis_policy=thesis_policy)


@dataclass
class ProgressEstimate:
    """Done and remaining decisions, with a finish time paced by the model calls made so far.

    Cached answers return instantly, so only real model calls set the pace; the estimate assumes
    every remaining decision needs one, which holds for a run resumed in chronological order.
    """
    total: int
    model_seconds: float = 0.0
    model_calls: int = 0

    def record_model_call(self, seconds: float) -> None:
        self.model_seconds += seconds
        self.model_calls += 1

    def report(self, done: int, *, now: datetime) -> str:
        remaining = self.total - done
        if not self.model_calls:
            return f"Progress: {done}/{self.total} done, {remaining} remaining; no model calls yet to estimate from"
        per_call = self.model_seconds / self.model_calls
        finish = now + timedelta(seconds=per_call * remaining)
        return (f"Progress: {done}/{self.total} done, {remaining} remaining, {per_call / 60:.1f} min per model answer, "
                f"estimated finish {finish:%Y-%m-%d %H:%M %Z}")


def quote_windows(traded: list[dict[str, Any]], *, max_hold_hours: int) -> list[dict[str, Any]]:
    windows = []
    for trade in traded:
        start = datetime.fromisoformat(trade["decision_run_at"])
        windows.append({"decision": {"id": trade["id"], "decision_run_at": trade["decision_run_at"]},
                        "option_symbol": trade["selected_option_symbol"], "start_at": start.isoformat(),
                        "end_at": (start + QUOTE_ENTRY_DELAY + timedelta(hours=max_hold_hours)).isoformat()})
    return windows


def fetch_quotes_for_trades(traded: list[dict[str, Any]], *, max_hold_hours: int, db_path: Any = None) -> dict[str, Any]:
    """Real end-of-day quotes for the replay's own contracts, within the MarketData credit budget."""
    result = fetch_marketdata_eod_quotes_for_contracts(quote_windows(traded, max_hold_hours=max_hold_hours), db_path=db_path)
    gap_reasons: dict[str, int] = {}
    for gap in result["coverage_gaps"]:
        reason = str(gap["reason"]).split(":", 1)[0]
        gap_reasons[reason] = gap_reasons.get(reason, 0) + 1
    return {key: result[key] for key in ("fetched_count", "already_cached_count", "no_data_count", "unavailable_count",
                                         "error_count", "rate_limited_count", "stop_reason")} | {"gap_reasons": gap_reasons}


def space_decisions_per_symbol(decisions: list[dict[str, Any]], *, min_hours: float) -> list[dict[str, Any]]:
    """Each symbol's first decision, then its next one at least `min_hours` after the last one kept.

    Production re-decides a company every cycle, so neighbouring decisions largely repeat each other.
    Selection is by time alone, never by what production decided, so the subset is not tilted toward
    the decisions that traded. `decisions` must be in chronological order.
    """
    gap = timedelta(hours=min_hours)
    last_kept: dict[str, datetime] = {}
    kept = []
    for decision in decisions:
        decided_at = datetime.fromisoformat(decision["decision_run_at"])
        previous = last_kept.get(decision["symbol"])
        if previous is None or decided_at - previous >= gap:
            last_kept[decision["symbol"]] = decided_at
            kept.append(decision)
    return kept


@dataclass(frozen=True)
class ReplayedAnswer:
    """A decision, the input its manager answered and that answer; or the outcome explaining why there is none.

    `frozen` is None only when the decision's frozen snapshots failed their hash check. `strategist_answer`
    is the replayed strategist's own answer (thesis check attached), which the manager payload may not carry.
    """
    decision: dict[str, Any]
    payload: dict[str, Any] | None
    recommendation: dict[str, Any] | None = None
    unavailable: str | None = None
    frozen: FrozenDecision | None = None
    strategist_answer: dict[str, Any] | None = None


def drop_remembered(window: list[dict[str, Any]], model: str, allow_unknown_cutoff: bool,
                    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """The decisions the model cannot remember (see `services.evaluation.model_cutoffs`), how many were dropped, and the cutoff used.

    Raises ValueError for a model with no known cutoff unless `allow_unknown_cutoff`, which then keeps every decision.
    """
    cutoff, source = cutoff_record(model) or (None, None)
    if cutoff is None and window and not allow_unknown_cutoff:
        raise ValueError(f"No knowledge cutoff for model '{model}' (config/model_knowledge_cutoffs.csv); a replay could be "
                         "scored on decisions it remembers. Add it to the table or pass allow_unknown_cutoff.")
    contamination = {"knowledge_cutoff": cutoff.isoformat() if cutoff else None,
                     "margin_days": CONTAMINATION_MARGIN_DAYS, "source": source}
    if cutoff is None:
        return window, 0, contamination
    kept = [decision for decision in window if eligibility(decision["decision_run_at"], cutoff) == "eligible"]
    return kept, len(window) - len(kept), contamination


@dataclass
class ReplayRun:
    """The decisions in a window and the cached model, or production's stored answers, that answer them."""
    model: str
    window: list[dict[str, Any]]
    replayable: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    min_hours_between: float
    thesis_policy: EvidenceThesisPolicy
    replay: CachedManagerReplay | None
    strategist: StrategistReplay | None
    db_path: Any = None
    excluded_pre_knowledge_cutoff: int = 0
    contamination: dict[str, Any] | None = None
    recheck_thesis: bool = False

    @classmethod
    def build(cls, *, model: str, start_at: str | None = None, end_at: str | None = None, limit: int | None = None,
              decision_ids: Sequence[int] | None = None, allow_unknown_cutoff: bool = False,
              min_hours_between: float = 0, replay_strategist: bool = False, decide: Decider | None = None,
              decide_strategist: Decider | None = None, strategist_reuse_max_age_minutes: int = 0,
              recheck_thesis: bool = False, sample: int = 0, db_path: Any = None) -> "ReplayRun":
        stored = model == STORED_MODEL
        if sample < 0 or (sample and stored):
            raise ValueError(f"sample must be 0 or more and needs a model to ask; '{STORED_MODEL}' has one answer")
        if recheck_thesis and replay_strategist:
            raise ValueError("recheck_thesis re-checks production's strategist thesis; a replayed strategist is checked already")
        if stored and replay_strategist:
            raise ValueError(f"replay_strategist needs a model to replay; '{STORED_MODEL}' reuses production's answers")
        if strategist_reuse_max_age_minutes > 0 and not replay_strategist:
            raise ValueError("strategist_reuse_max_age_minutes applies only with replay_strategist")
        models = get_settings().models
        thesis_policy = EvidenceThesisPolicy.from_env()
        replay = None if stored else CachedManagerReplay(
            model=model, provider=_normalize_provider(models.manager.provider),
            decide=decide or manager_decider(model), db_path=db_path, sample=sample)
        strategist = StrategistReplay(
            model=model, provider=_normalize_provider(models.strategist.provider), thesis_policy=thesis_policy,
            decide=decide_strategist or strategist_decider(model, thesis_policy), db_path=db_path,
            reuse_max_age_minutes=strategist_reuse_max_age_minutes,
            reuse_max_price_move_pct=AgentPipelineSettings.from_env().strategist_reuse_max_price_move_pct,
            sample=sample,
        ) if replay_strategist else None
        window = list_frozen_manager_decisions(start_at=start_at, end_at=end_at, limit=limit, ids=decision_ids,
                                               db_path=db_path)
        excluded, contamination = 0, None
        if not stored:
            window, excluded, contamination = drop_remembered(window, model, allow_unknown_cutoff)
        # Older decisions predate frozen inputs; dropped before spacing so they never take a replayable one's slot.
        replayable = [decision for decision in window if frozen_manager_input(decision) is not None]
        decisions = space_decisions_per_symbol(replayable, min_hours=min_hours_between) if min_hours_between > 0 else replayable
        return cls(model, window, replayable, decisions, min_hours_between, thesis_policy, replay, strategist, db_path,
                   excluded, contamination, recheck_thesis)

    @property
    def stored(self) -> bool:
        return self.replay is None

    def answers(self, tally: "ReplayTally") -> Iterator[ReplayedAnswer]:
        """Each decision's answer, in order. One bad answer is recorded; a run of failures stops the run."""
        consecutive_failures = 0
        progress = ProgressEstimate(total=len(self.decisions))
        for index, decision in enumerate(self.decisions, start=1):
            try:
                frozen = FrozenDecision.load(decision, db_path=self.db_path)
            except SnapshotHashMismatch:
                LOGGER.warning("Frozen snapshot hash mismatch for decision %s", decision["id"])
                frozen = None
            payload = frozen_manager_input(decision)
            if frozen is None:
                yield ReplayedAnswer(decision, payload, unavailable="snapshot_hash_mismatch")
            elif self.strategist is not None and frozen.strategist_input is None:
                yield ReplayedAnswer(decision, payload, unavailable="missing_strategist_input", frozen=frozen)
            else:
                try:
                    asked_at = time.monotonic()
                    payload, recommendation, cached, strategist_answer = self._answer(
                        decision, payload, frozen.strategist_input, tally)
                    if not cached:
                        progress.record_model_call(time.monotonic() - asked_at)
                    consecutive_failures = 0
                except Exception as exc:
                    consecutive_failures += 1
                    LOGGER.warning("Replay failed for decision %s: %s", decision["id"], exc)
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        raise ModelUnavailable(f"{consecutive_failures} replays failed in a row; last error: {exc}") from exc
                    recommendation = None
                if recommendation is None:
                    yield ReplayedAnswer(decision, payload, unavailable="model_error", frozen=frozen)
                else:
                    tally.changed_direction += recommendation.get("decision") != str(decision.get("manager_decision") or "").lower()
                    yield ReplayedAnswer(decision, payload, recommendation, frozen=frozen, strategist_answer=strategist_answer)
            if index % PROGRESS_EVERY == 0:
                LOGGER.info(progress.report(index, now=datetime.now().astimezone()))

    def _answer(self, decision: dict[str, Any], payload: dict[str, Any], strategist_input: dict[str, Any] | None,
                tally: "ReplayTally") -> tuple[dict[str, Any], dict[str, Any], bool, dict[str, Any] | None]:
        """The manager input (with any replayed strategist), its answer, whether no model was called, and the strategist's answer."""
        strategist_cached, strategist_answer = True, None
        if self.strategist is not None:
            strategist_answer, strategist_cached, reused = self.strategist.answer(
                strategist_input, symbol=decision["symbol"],
                decided_at=datetime.fromisoformat(decision["decision_run_at"]))
            tally.strategist_cached_answers += strategist_cached
            tally.strategist_reused_answers += reused
            strategist_cached = strategist_cached or reused
            production_strategist = payload.get("strategist_recommendation") or {}
            tally.strategist_changed_decision += strategist_answer.get("decision") != production_strategist.get("decision")
            tally.count_thesis("production", production_strategist)
            tally.count_thesis("replay", strategist_answer)
            payload = self.strategist.manager_payload(payload, strategist_answer)
        if self.recheck_thesis:
            payload, changed = rechecked_thesis_payload(payload, strategist_input, policy=self.thesis_policy,
                                                        db_path=self.db_path)
            tally.thesis_rechecked += changed is not None
            tally.thesis_recheck_changed += bool(changed)
        if self.stored:
            tally.cached_answers += 1
            return payload, stored_recommendation(decision), True, strategist_answer
        recommendation, cached = self.replay.recommendation(payload)
        tally.cached_answers += cached
        return payload, recommendation, cached and strategist_cached, strategist_answer

    def summary(self, tally: "ReplayTally") -> dict[str, Any]:
        return {
            "source_decision_count": len(self.decisions),
            "missing_frozen_input_count": len(self.window) - len(self.replayable),
            "excluded_pre_knowledge_cutoff": self.excluded_pre_knowledge_cutoff,
            "contamination": self.contamination,
            "min_hours_between_same_symbol": self.min_hours_between,
            "spaced_out_decision_count": len(self.replayable) - len(self.decisions),
            "outcomes": tally.outcomes,
            "changed_direction_count": tally.changed_direction,
            "cached_answer_count": tally.cached_answers,
            "strategist_replay": {
                "model": self.model,
                "prompt_version": self.strategist.prompt_version,
                "provider": self.strategist.provider,
                "changed_decision_count": tally.strategist_changed_decision,
                "cached_answer_count": tally.strategist_cached_answers,
                "reuse_max_age_minutes": self.strategist.reuse_max_age_minutes,
                "reused_answer_count": tally.strategist_reused_answers,
                "thesis_status_by_decision": tally.strategist_thesis_statuses,
            } if self.strategist is not None else None,
            "provider": STORED_MODEL if self.stored else self.replay.provider,
            "sample": 0 if self.stored else self.replay.sample,
            "thesis_recheck": {"rechecked": tally.thesis_rechecked, "changed": tally.thesis_recheck_changed}
            if self.recheck_thesis else None,
        }


def log_outcome(index: int, run: ReplayRun, answer: ReplayedAnswer, outcome: str) -> None:
    decision = answer.decision
    LOGGER.info("[%d/%d] %s %s -> %s", index, len(run.decisions), decision["symbol"], decision["decision_run_at"], outcome)


def run_llm_backtest(
    *,
    model: str,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int | None = None,
    decision_ids: Sequence[int] | None = None,
    allow_unknown_cutoff: bool = False,
    execution_config: dict[str, Any] | None = None,
    fetch_missing_bars: bool = False,
    fetch_option_quotes: bool = False,
    execute_medium_confidence: bool = False,
    min_hours_between: float = 0,
    replay_strategist: bool = False,
    decide: Decider | None = None,
    decide_strategist: Decider | None = None,
    strategist_reuse_max_age_minutes: int = 0,
    keep_answers: bool = True,
    recheck_thesis: bool = False,
    sample: int = 0,
    db_path: Any = None,
) -> dict[str, Any]:
    """Replay `model` (or `stored`) over the window and record the trades as a backtest run.

    `execute_medium_confidence` overrides production's recorded rule, so the run is no longer a
    production-faithful replay; the summary records the override. `min_hours_between` replays only
    decisions spaced that far apart per symbol (see `space_decisions_per_symbol`); compare such a run
    with a `stored` run using the same spacing. `replay_strategist` has `model` answer the strategist
    too, so the manager answers that strategist instead of production's. `strategist_reuse_max_age_minutes`
    reuses those answers under production's reuse rule (see the module docstring). `decision_ids` replays only
    those decisions; unless `model` is `stored`, decisions within a model's knowledge cutoff plus margin are
    dropped, and a model with no known cutoff is refused unless `allow_unknown_cutoff`. The result also
    carries every replayed `answers` entry, in replay order, for callers that score them. Answers pin their frozen
    inputs (about 250 KB each), so long ad hoc runs should pass `keep_answers=False`, which leaves the key out.
    `recheck_thesis` re-runs this checkout's thesis checker on production's stored strategist thesis, so the
    manager answers the corrected check (see `rechecked_thesis_payload`); not valid with `replay_strategist`.
    `sample` N > 0 asks the manager again for inputs it has answered and caches that answer separately, to
    measure how often the same input gets a different answer; sample 0 is the usual cache entry.
    """
    run = ReplayRun.build(model=model, start_at=start_at, end_at=end_at, limit=limit, decision_ids=decision_ids,
                          allow_unknown_cutoff=allow_unknown_cutoff, min_hours_between=min_hours_between, replay_strategist=replay_strategist,
                          decide=decide, decide_strategist=decide_strategist,
                          strategist_reuse_max_age_minutes=strategist_reuse_max_age_minutes,
                          recheck_thesis=recheck_thesis, sample=sample, db_path=db_path)
    local_settings = WorkerSettings.from_env()
    latest_rules = ProductionRules(local_settings)
    tally = ReplayTally()
    traded: list[dict[str, Any]] = []
    answers: list[ReplayedAnswer] = []
    for index, answer in enumerate(run.answers(tally), start=1):
        if keep_answers:
            answers.append(answer)
        rules = ProductionRules.for_manifest(answer.frozen.manifest if answer.frozen else None, local=local_settings)
        if execute_medium_confidence:
            rules = rules.executing_medium_confidence()
        if rules.recorded:
            tally.recorded_settings += 1
            latest_rules = rules
        outcome = answer.unavailable
        if outcome is None:
            outcome, trade = evaluate_answer(answer.frozen, answer.payload, answer.recommendation, rules, tally,
                                             thesis_policy=run.thesis_policy)
            if trade is not None:
                traded.append(trade)
        tally.count(outcome)
        log_outcome(index, run, answer, outcome)
    traded_symbols = {trade["symbol"] for trade in traded}
    since = traded[0]["decision_run_at"] if traded else ""
    bars = (fill_missing_daily_closes(traded_symbols, since=since, db_path=db_path) if fetch_missing_bars and traded
            else {"fetched": [], "failed": {}})
    max_hold_hours = int((execution_config or {}).get("max_hold_hours") or DEFAULT_HOLD_HOURS)
    quotes = (fetch_quotes_for_trades(traded, max_hold_hours=max_hold_hours, db_path=db_path)
              if fetch_option_quotes and traded else None)
    limits = latest_rules.portfolio_limits([power for trade in traded
                                            if (power := frozen_buying_power(frozen_manager_input(trade) or {})) > 0])
    config = {
        **limits,
        **(execution_config or {}),
        "strategy_version": STRATEGY_VERSION,
        "model": model,
        "prompt_version": STORED_PROMPT_VERSION if run.stored else MANAGER_PROMPT_VERSION,
        "risk_free_rate": TradeEconomicsPolicy.from_env().risk_free_rate,
        "data_source_versions": {"option_quotes": "historical_option_quotes",
                                 "fallback": "black_scholes_frozen_chain_daily_closes"},
    }
    replay_summary = {
        **run.summary(tally),
        "selector_version": SELECTOR_VERSION,
        "pre_decision_chain_trade_count": tally.pre_decision_chain_trades,
        "production_settings": {
            "recorded_decision_count": tally.recorded_settings,
            "local_fallback_decision_count": len(run.decisions) - tally.recorded_settings,
            "execute_medium_confidence_trades": latest_rules.settings.execute_medium_confidence_trades,
            "overrides": ["execute_medium_confidence_trades"] if execute_medium_confidence else [],
            "portfolio_limits": limits,
            "selector_settings_differing_from_local": sorted(tally.selector_differences),
        },
        "option_quotes": quotes,
        "daily_bars": {**bars, "still_missing": sorted(symbols_without_daily_bars(traded_symbols, since=since, db_path=db_path))
                       if traded else []},
    }
    result = run_decision_backtest(traded, config=config, extra_metrics={"replay": replay_summary}, db_path=db_path)
    return {**result, "answers": answers} if keep_answers else result
