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

Eligibility, the premium cap and portfolio limits use the settings production recorded in each
candidate's pipeline manifest, not this checkout's environment; a decision whose recorded selector
settings differ from the local ones is reported rather than re-selected under different rules.

Model answers are cached per frozen input, so re-running with new execution settings never asks
the model again. By default only the manager is replayed and the strategist's recorded output stays
in the payload. With `replay_strategist`, the model also answers the strategist's frozen input, and
that answer replaces production's in the manager input, projected exactly as production hands it
over. Trade economics is not replayed; it is shadow-only in production. No broker is called.
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
from typing import Any, Callable, Iterator

from market_db_modules.backtest_marketdata import fetch_marketdata_eod_quotes_for_contracts
from market_db_modules.llm_replay_store import (
    get_cached_llm_replay,
    get_candidate_manifest,
    get_frozen_strategist_input,
    get_saved_bucket_chain,
    list_frozen_manager_decisions,
    store_cached_llm_replay,
    symbols_without_daily_bars,
)
from market_db_modules.backtesting import run_decision_backtest
from market_db_modules.market_regime import store_regime_bars
from agent_helpers.deterministic_option_selector import SELECTOR_VERSION, apply_deterministic_option_selection
from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
from agent_helpers.shared import _normalize_provider
from agent_stages.manager_runner import MANAGER_PROMPT_VERSION, replay_manager_decision_from_payload
from agent_stages.manager_strategist_context import _build_manager_visible_strategist_context
from agent_stages.strategist_runner import (attach_thesis_check, replay_strategist_decision_from_payload,
                                            strategist_prompt_version)
from services.config import FrontMainSettings
from services.entry_gates import carry_gate_verdicts
from services.evaluation_clock import evaluated_at
from services.evidence_continuation_gate import evaluate_evidence_continuation_gate
from services.evidence_thesis import evaluate_evidence_thesis_gate
from services.evidence_thesis_policy import EvidenceThesisPolicy
from services.market_regime_gate import apply_market_regime_gate_to_manager_result
from services.option_dte_buckets import normalize_target_dte_bucket
from services.order_candidates import OrderCandidateBuilder
from services.pipeline_version import selector_constants
from services.settings import get_settings
from services.regime_history import fetch_daily_history
from services.trade_economics_policy import TradeEconomicsPolicy

LOGGER = logging.getLogger(__name__)

STORED_MODEL = "stored"
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

    def recommendation(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        key = {"stage": STAGE, "provider": self.provider, "model": self.model,
               "prompt_version": MANAGER_PROMPT_VERSION, "input_hash": input_hash(payload)}
        return cached_answer(key, lambda: self.decide(payload)["recommendation"], db_path=self.db_path)


@dataclass
class StrategistReplay:
    """One strategist answer per frozen strategist input, handed to the manager as production would."""
    model: str
    provider: str
    thesis_policy: EvidenceThesisPolicy
    decide: Decider
    db_path: Any = None

    @property
    def prompt_version(self) -> str:
        return strategist_prompt_version(self.thesis_policy)

    def recommendation(self, strategist_input: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        key = {"stage": STRATEGIST_STAGE, "provider": self.provider, "model": self.model,
               "prompt_version": self.prompt_version, "input_hash": input_hash(strategist_input)}
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
    settings: FrontMainSettings
    selector_config: dict[str, Any] | None = None

    @property
    def recorded(self) -> bool:
        return self.selector_config is not None

    @classmethod
    def for_candidate(cls, candidate_id: str | None, *, local: FrontMainSettings, db_path: Any = None) -> "ProductionRules":
        manifest = get_candidate_manifest(candidate_id, db_path=db_path)
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
                             candidate_id: str | None, *, db_path: Any = None) -> dict[str, Any] | None:
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
    recorded_settings: int = 0
    selector_differences: set[str] = field(default_factory=set)

    def count(self, outcome: str) -> None:
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1


def evaluate_answer(decision: dict[str, Any], payload: dict[str, Any], recommendation: dict[str, Any],
                    rules: ProductionRules, tally: ReplayTally, *, thesis_policy: EvidenceThesisPolicy,
                    db_path: Any) -> tuple[str, dict[str, Any] | None]:
    """One answer through production's selection and eligibility; returns the outcome and any trade."""
    market = selection_market_context(payload, recommendation, decision.get("candidate_id"), db_path=db_path)
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
    """A decision, the input its manager answered and that answer; or the outcome explaining why there is none."""
    decision: dict[str, Any]
    payload: dict[str, Any] | None
    recommendation: dict[str, Any] | None = None
    unavailable: str | None = None


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

    @classmethod
    def build(cls, *, model: str, start_at: str | None = None, end_at: str | None = None, limit: int | None = None,
              min_hours_between: float = 0, replay_strategist: bool = False, decide: Decider | None = None,
              decide_strategist: Decider | None = None, db_path: Any = None) -> "ReplayRun":
        stored = model == STORED_MODEL
        if stored and replay_strategist:
            raise ValueError(f"replay_strategist needs a model to replay; '{STORED_MODEL}' reuses production's answers")
        models = get_settings().models
        thesis_policy = EvidenceThesisPolicy.from_env()
        replay = None if stored else CachedManagerReplay(
            model=model, provider=_normalize_provider(models.manager.provider),
            decide=decide or manager_decider(model), db_path=db_path)
        strategist = StrategistReplay(
            model=model, provider=_normalize_provider(models.strategist.provider), thesis_policy=thesis_policy,
            decide=decide_strategist or strategist_decider(model, thesis_policy), db_path=db_path) if replay_strategist else None
        window = list_frozen_manager_decisions(start_at=start_at, end_at=end_at, limit=limit, db_path=db_path)
        # Older decisions predate frozen inputs; dropped before spacing so they never take a replayable one's slot.
        replayable = [decision for decision in window if frozen_payload(decision) is not None]
        decisions = space_decisions_per_symbol(replayable, min_hours=min_hours_between) if min_hours_between > 0 else replayable
        return cls(model, window, replayable, decisions, min_hours_between, thesis_policy, replay, strategist, db_path)

    @property
    def stored(self) -> bool:
        return self.replay is None

    def answers(self, tally: "ReplayTally") -> Iterator[ReplayedAnswer]:
        """Each decision's answer, in order. One bad answer is recorded; a run of failures stops the run."""
        consecutive_failures = 0
        progress = ProgressEstimate(total=len(self.decisions))
        for index, decision in enumerate(self.decisions, start=1):
            payload = frozen_payload(decision)
            strategist_input = (get_frozen_strategist_input(decision.get("candidate_id"), db_path=self.db_path)
                                if self.strategist is not None else None)
            if self.strategist is not None and strategist_input is None:
                yield ReplayedAnswer(decision, payload, unavailable="missing_strategist_input")
            else:
                try:
                    asked_at = time.monotonic()
                    payload, recommendation, cached = self._answer(decision, payload, strategist_input, tally)
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
                    yield ReplayedAnswer(decision, payload, unavailable="model_error")
                else:
                    tally.changed_direction += recommendation.get("decision") != str(decision.get("manager_decision") or "").lower()
                    yield ReplayedAnswer(decision, payload, recommendation)
            if index % PROGRESS_EVERY == 0:
                LOGGER.info(progress.report(index, now=datetime.now().astimezone()))

    def _answer(self, decision: dict[str, Any], payload: dict[str, Any], strategist_input: dict[str, Any] | None,
                tally: "ReplayTally") -> tuple[dict[str, Any], dict[str, Any], bool]:
        """The manager input (with any replayed strategist), its answer, and whether no model was called."""
        strategist_cached = True
        if self.strategist is not None:
            strategist_answer, strategist_cached = self.strategist.recommendation(strategist_input)
            tally.strategist_cached_answers += strategist_cached
            production_strategist = (payload.get("strategist_recommendation") or {}).get("decision")
            tally.strategist_changed_decision += strategist_answer.get("decision") != production_strategist
            payload = self.strategist.manager_payload(payload, strategist_answer)
        if self.stored:
            tally.cached_answers += 1
            return payload, stored_recommendation(decision), True
        recommendation, cached = self.replay.recommendation(payload)
        tally.cached_answers += cached
        return payload, recommendation, cached and strategist_cached

    def summary(self, tally: "ReplayTally") -> dict[str, Any]:
        return {
            "source_decision_count": len(self.decisions),
            "missing_frozen_input_count": len(self.window) - len(self.replayable),
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
            } if self.strategist is not None else None,
            "provider": STORED_MODEL if self.stored else self.replay.provider,
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
    execution_config: dict[str, Any] | None = None,
    fetch_missing_bars: bool = False,
    fetch_option_quotes: bool = False,
    execute_medium_confidence: bool = False,
    min_hours_between: float = 0,
    replay_strategist: bool = False,
    decide: Decider | None = None,
    decide_strategist: Decider | None = None,
    db_path: Any = None,
) -> dict[str, Any]:
    """Replay `model` (or `stored`) over the window and record the trades as a backtest run.

    `execute_medium_confidence` overrides production's recorded rule, so the run is no longer a
    production-faithful replay; the summary records the override. `min_hours_between` replays only
    decisions spaced that far apart per symbol (see `space_decisions_per_symbol`); compare such a run
    with a `stored` run using the same spacing. `replay_strategist` has `model` answer the strategist
    too, so the manager answers that strategist instead of production's.
    """
    run = ReplayRun.build(model=model, start_at=start_at, end_at=end_at, limit=limit,
                          min_hours_between=min_hours_between, replay_strategist=replay_strategist,
                          decide=decide, decide_strategist=decide_strategist, db_path=db_path)
    local_settings = FrontMainSettings.from_env()
    latest_rules = ProductionRules(local_settings)
    tally = ReplayTally()
    traded: list[dict[str, Any]] = []
    for index, answer in enumerate(run.answers(tally), start=1):
        decision = answer.decision
        rules = ProductionRules.for_candidate(decision.get("candidate_id"), local=local_settings, db_path=db_path)
        if execute_medium_confidence:
            rules = rules.executing_medium_confidence()
        if rules.recorded:
            tally.recorded_settings += 1
            latest_rules = rules
        outcome = answer.unavailable
        if outcome is None:
            outcome, trade = evaluate_answer(decision, answer.payload, answer.recommendation, rules, tally,
                                             thesis_policy=run.thesis_policy, db_path=db_path)
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
                                            if (power := frozen_buying_power(frozen_payload(trade) or {})) > 0])
    config = {
        **limits,
        **(execution_config or {}),
        "strategy_version": STRATEGY_VERSION,
        "model": model,
        "prompt_version": "stored-output" if run.stored else MANAGER_PROMPT_VERSION,
        "risk_free_rate": TradeEconomicsPolicy.from_env().risk_free_rate,
        "data_source_versions": {"option_quotes": "historical_option_quotes",
                                 "fallback": "black_scholes_frozen_chain_daily_closes"},
    }
    replay_summary = {
        **run.summary(tally),
        "selector_version": SELECTOR_VERSION,
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
    return run_decision_backtest(traded, config=config, extra_metrics={"replay": replay_summary}, db_path=db_path)
