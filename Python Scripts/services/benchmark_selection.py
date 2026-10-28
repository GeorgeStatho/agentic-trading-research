"""Choose a balanced, repeatable set of frozen manager decisions to benchmark models on.

The pool is every replayable decision in a window, thinned so one symbol's neighbouring re-decisions
do not repeat each other. Items are drawn stratum by stratum (sector, direction, regime, month and,
once labelled, catalyst family) with a floor per stratum, a ceiling on abstentions and on any one
symbol, and busier, more diverse days sampled more often. Whole symbols are then assigned to
holdout, so a symbol never appears in both splits. The seed defaults to a hash of the set id, so the
same id always selects the same items.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from math import ceil, log
from random import Random
from typing import Any, Sequence

from market_db_modules.benchmarks import benchmark_set_summary, insert_benchmark_set, primary_catalysts
from market_db_modules.foundation_store import SnapshotHashMismatch
from market_db_modules.llm_replay_store import FrozenDecision, frozen_manager_input, list_frozen_manager_decisions
from services.llm_backtest import space_decisions_per_symbol

SELECTION_VERSION = "benchmark-selection-v1"
MIN_HOURS_BETWEEN_SAME_SYMBOL = 24
STRATUM_FLOOR = 2
MAX_NEITHER_SHARE = 0.4
# Items allowed per symbol, as a multiple of an even share of the set.
SYMBOL_BUDGET_FACTOR = 2


def default_seed(set_id: str) -> int:
    return int(sha256(set_id.encode()).hexdigest(), 16)


def production_direction(decision: dict[str, Any]) -> str:
    called = str(decision.get("manager_decision") or "").lower()
    return called if called in ("call", "put") else "neither"


def stratum_key(decision: dict[str, Any]) -> str:
    market = (frozen_manager_input(decision) or {}).get("market_context") or {}
    regime = (market.get("market_regime") or {}).get("state") or "unknown"
    key = "|".join((decision.get("sector_key") or "unknown", production_direction(decision), str(regime),
                    decision["decision_run_at"][:7]))
    family = decision.get("catalyst_family")
    return f"{key}|{family}" if family and family != "other" else key


def day_weights(pool: Sequence[dict[str, Any]]) -> dict[int, float]:
    """Each decision's sampling weight: its day's volume relative to the average day, times one plus the day's sector entropy.

    A relative volume rather than a z-score, which can be negative and so cannot weight a draw.
    """
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in pool:
        days[decision["decision_run_at"][:10]].append(decision)
    mean_volume = len(pool) / len(days)
    weights = {}
    for day in days.values():
        sectors = Counter(decision.get("sector_key") for decision in day)
        entropy = -sum(count / len(day) * log(count / len(day)) for count in sectors.values())
        weights.update({decision["id"]: len(day) / mean_volume * (1 + entropy) for decision in day})
    return weights


def select_items(decisions: Sequence[dict[str, Any]], *, set_id: str, size: int, holdout_share: float,
                 seed: int | None = None) -> list[dict[str, Any]]:
    """Up to `size` `{manager_decision_history_id, split, stratum}` items from `decisions`, ordered by id.

    `decisions` are `list_frozen_manager_decisions` rows, each optionally with its primary `catalyst_family`.
    """
    random = Random(default_seed(set_id) if seed is None else seed)
    ordered = sorted(decisions, key=lambda decision: (decision["decision_run_at"], decision["id"]))
    symbol_cap = ceil(size / len({decision["symbol"] for decision in ordered}) * SYMBOL_BUDGET_FACTOR) if ordered else 0
    pool = space_decisions_per_symbol(ordered, min_hours=MIN_HOURS_BETWEEN_SAME_SYMBOL)
    weights = day_weights(pool) if pool else {}
    remaining: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in pool:
        remaining[stratum_key(decision)].append(decision)
    available = {stratum: len(group) for stratum, group in remaining.items()}
    abstaining = {stratum for stratum, group in remaining.items() if production_direction(group[0]) == "neither"}
    target = min(size, len(pool))
    neither_cap = int(MAX_NEITHER_SHARE * size)

    chosen: list[dict[str, Any]] = []
    per_stratum: Counter[str] = Counter()
    per_symbol: Counter[str] = Counter()

    def abstentions() -> int:
        return sum(per_stratum[stratum] for stratum in abstaining)

    def candidates(stratum: str) -> list[dict[str, Any]]:
        if stratum in abstaining and abstentions() >= neither_cap:
            return []
        return [decision for decision in remaining[stratum] if per_symbol[decision["symbol"]] < symbol_cap]

    def priority(stratum: str) -> tuple[bool, float, str]:
        """Strata below their floor go first, then the one furthest below its proportional share."""
        below_floor = per_stratum[stratum] < min(STRATUM_FLOOR, available[stratum])
        return below_floor, target * available[stratum] / len(pool) - per_stratum[stratum], stratum

    while len(chosen) < target:
        open_strata = [stratum for stratum in remaining if candidates(stratum)]
        if not open_strata:
            break
        stratum = max(open_strata, key=priority)
        group = candidates(stratum)
        pick = random.choices(group, weights=[weights[decision["id"]] for decision in group])[0]
        remaining[stratum].remove(pick)
        chosen.append(pick)
        per_stratum[stratum] += 1
        per_symbol[pick["symbol"]] += 1

    chosen = _trim_abstentions(chosen, random)
    holdout = _holdout_symbols(chosen, holdout_share, random)
    return [{"manager_decision_history_id": decision["id"], "split": "holdout" if decision["symbol"] in holdout else "dev",
             "stratum": stratum_key(decision)} for decision in sorted(chosen, key=lambda decision: decision["id"])]


def _trim_abstentions(chosen: list[dict[str, Any]], random: Random) -> list[dict[str, Any]]:
    """Drop seeded-random abstentions until they are at most `MAX_NEITHER_SHARE` of the items actually chosen.

    The draw caps abstentions against the requested size, which a set that falls short of it can exceed.
    """
    abstentions = sorted((decision for decision in chosen if production_direction(decision) == "neither"),
                         key=lambda decision: decision["id"])
    allowed = int((len(chosen) - len(abstentions)) * MAX_NEITHER_SHARE / (1 - MAX_NEITHER_SHARE) + 1e-9)
    dropped = {decision["id"] for decision in random.sample(abstentions, max(0, len(abstentions) - allowed))}
    return [decision for decision in chosen if decision["id"] not in dropped]


def _holdout_symbols(chosen: Sequence[dict[str, Any]], share: float, random: Random) -> set[str]:
    """Whole symbols, in seeded order, added while that brings the holdout's item count closer to its share."""
    counts = Counter(decision["symbol"] for decision in chosen)
    goal = share * len(chosen)
    symbols = sorted(counts)
    random.shuffle(symbols)
    holdout: set[str] = set()
    items = 0
    for symbol in symbols:
        if abs(items + counts[symbol] - goal) >= abs(items - goal):
            continue   # too big here; a smaller symbol later may still fit
        holdout.add(symbol)
        items += counts[symbol]
    return holdout


def replayable_pool(*, start_at: str | None, end_at: str | None, db_path: Any = None) -> list[dict[str, Any]]:
    """Decisions in the window with a frozen input and valid snapshot hashes, each with its primary catalyst family."""
    catalysts = primary_catalysts(db_path=db_path)
    pool = []
    for decision in list_frozen_manager_decisions(start_at=start_at, end_at=end_at, db_path=db_path):
        if frozen_manager_input(decision) is None:
            continue
        try:
            FrozenDecision.load(decision, db_path=db_path)
        except SnapshotHashMismatch:
            continue
        pool.append({**decision, "catalyst_family": catalysts.get(decision["candidate_id"], (None,))[0]})
    return pool


def create_benchmark_set(set_id: str, *, size: int, holdout_share: float, start_at: str | None = None,
                         end_at: str | None = None, seed: int | None = None, db_path: Any = None) -> dict[str, Any]:
    """Select and store a benchmark set, returning its summary. A `set_id` already used raises `sqlite3.IntegrityError`."""
    seed = default_seed(set_id) if seed is None else seed
    pool = replayable_pool(start_at=start_at, end_at=end_at, db_path=db_path)
    items = select_items(pool, set_id=set_id, size=size, holdout_share=holdout_share, seed=seed)
    selection = {"size": size, "holdout_share": holdout_share, "seed": seed, "start_at": start_at, "end_at": end_at,
                 "pool_size": len(pool), "min_hours_between_same_symbol": MIN_HOURS_BETWEEN_SAME_SYMBOL,
                 "stratum_floor": STRATUM_FLOOR, "max_neither_share": MAX_NEITHER_SHARE,
                 "symbol_budget_factor": SYMBOL_BUDGET_FACTOR}
    insert_benchmark_set(set_id, selection_version=SELECTION_VERSION, selection=selection, items=items, db_path=db_path)
    summary = benchmark_set_summary(set_id, db_path=db_path)
    if summary["item_count"] < size:
        summary["warning"] = (f"Selected {summary['item_count']} of the {size} requested items: the pool, the per-symbol "
                              f"cap, 24 h spacing or the abstention ceiling left no more")
    return summary
