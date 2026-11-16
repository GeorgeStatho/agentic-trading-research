from __future__ import annotations

"""Read-side view of the V2 rollout: what is enforcing, what the shadow gates
would have done, and what evidence each stage still needs to be promoted.

Enforcement is not a setting. `services.research.research_promotion.guard_policy` rewrites
any `enforce` request that has no matching counterfactual proof back to `shadow`,
so an environment variable alone says nothing about live behaviour. Every feature
row therefore carries the requested mode, the effective mode, and the promotion
proof its stages are waiting on.

Nothing here initializes or migrates the database: a missing table is reported as
missing, because that is itself the answer to "why is there no V2 data yet".
"""

import json
import os
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from services.core.numbers import safe_float
from db_common import database_path, get_connection
from api_support.context import logger

DEFAULT_ACTIVITY_WINDOW_DAYS = 30
DEFAULT_RUN_LIMIT = 5
FULL_COHORT_MONTH = "all"
UNKNOWN_REGIME = "unknown"
REQUIRED_REGIME_COUNT = 2
VALID_MODES = ("shadow", "enforce", "disabled", "observe")
# Discovery intake and paired forward evaluations are research records, not the production stream.
PRODUCTION_CANDIDATE = "candidate_id NOT LIKE 'discovery:%' AND candidate_id NOT LIKE 'paired:%'"

# Each guarded feature maps an operator-facing control to the promotion stages
# that must carry proofs before that control can take effect. `block` names the
# policy object that owns the stage in research_promotion.GATE_BLOCKS.
GUARDED_FEATURES = (
    {
        "key": "provenance",
        "label": "Provenance roots (P1)",
        "description": "Admits a catalyst only on two independent roots, or a scored single-primary exception.",
        "env_var": "V2_PROVENANCE_MODE",
        "block": "catalyst",
        "field": "provenance_mode",
        "stages": ("PROVENANCE",),
    },
    {
        "key": "freshness",
        "label": "Catalyst freshness (P3)",
        "description": "Decays a catalyst's remaining value by calendar-time priors and refuses stale ones.",
        "env_var": "V2_FRESHNESS_MODE",
        "block": "catalyst",
        "field": "freshness_mode",
        "stages": ("FRESHNESS",),
    },
    {
        "key": "continuation",
        "label": "Evidence continuation",
        "description": "Requires independent sources, evidence score and relative strength before an entry.",
        "env_var": "EVIDENCE_CONTINUATION_MODE",
        "block": "continuation",
        "field": "enforcement_mode",
        "stages": ("CONTINUATION",),
    },
    {
        "key": "regime",
        "label": "Market regime scope (G3)",
        "description": "Weights regime opposition by scope; missing regime and macro blackouts veto under enforcement.",
        "env_var": "MARKET_REGIME_GATE_MODE",
        "block": "regime",
        "field": "mode",
        "stages": ("REGIME",),
    },
    {
        "key": "entry_extension",
        "label": "Entry extension",
        "description": "Refuses a pick whose stock already moved its way over five days unless the manager rated overextension low.",
        "env_var": "ENTRY_EXTENSION_MODE",
        "block": "entry_extension",
        "field": "mode",
        "stages": ("ENTRY_EXTENSION",),
    },
    {
        "key": "regime_spy_trend",
        "label": "Regime variant: SPY 5/20-day trend",
        "description": "Blocks calls only when SPY's 5- and 20-day returns are both down, puts only when both are up; "
                       "stays shadow while REGIME enforces.",
        "env_var": "REGIME_SPY_TREND_MODE",
        "block": "regime_spy_trend",
        "field": "mode",
        "stages": ("REGIME_SPY_TREND",),
    },
    {
        "key": "tradability",
        "label": "Tradability pre-check",
        "description": "Before the strategist, checks the selector's top pick in the default DTE bucket against the premium cap.",
        "env_var": "TRADABILITY_MODE",
        "block": "tradability",
        "field": "mode",
        "stages": ("TRADABILITY",),
    },
    {
        "key": "liquidity",
        "label": "Liquidity V2 (E2)",
        "description": "Depth, five-second quote freshness and 6% spread checks at selection and at execution.",
        "env_var": "V2_LIQUIDITY_MODE",
        "block": "contracts",
        "field": "liquidity_mode",
        "stages": ("LIQUIDITY", "EXECUTION_LIQUIDITY"),
    },
    {
        "key": "risk_clusters",
        "label": "Risk clusters (E4)",
        "description": "Caps positions and notional per correlated cluster, with same-session reservations.",
        "env_var": "V2_RISK_CLUSTER_MODE",
        "block": "contracts",
        "field": "cluster_mode",
        "stages": ("RISK_CLUSTER",),
    },
    {
        "key": "scenario_selection",
        "label": "Scenario contract selection (E3)",
        "description": "Ranks the strict hybrid pool on empirical catalyst scenarios instead of taking the first contract.",
        "env_var": "V2_SCENARIO_MODE",
        "block": "contracts",
        "field": "scenario_mode",
        "stages": ("SCENARIO_SELECTION",),
        "activity_basis": "selection",
    },
    {
        "key": "position_exit",
        "label": "Catalyst and volatility exits (E5)",
        "description": "Adds catalyst-expiry, time and IV-collapse rungs ahead of the legacy trailing ladder.",
        "env_var": "V2_POSITION_EXIT_MODE",
        "block": "contracts",
        "field": "exit_mode",
        "stages": ("POSITION_EXIT_V2",),
        "activity_basis": "positions",
    },
)

# Features with no enforcement path in code at all. They are listed so the panel
# answers "is this on?" for every V2 component, not only the switchable ones.
SHADOW_ONLY_FEATURES = (
    {
        "key": "price_absorption",
        "label": "Price absorption (G1)",
        "stage": "PRICE_ABSORPTION",
        "description": "Measures how much of the catalyst the underlying already absorbed before entry.",
        "reason": "Hard-coded to shadow; needs live history and an R1 calibration before an enforce path is written.",
    },
    {
        "key": "option_hurdle",
        "label": "Option hurdle (G2)",
        "stage": "OPTION_HURDLE",
        "description": "Compares the option's breakeven against the catalyst family's historical move.",
        "reason": "Hard-coded to shadow; needs a populated catalyst-response sample before a threshold can be set.",
    },
    {
        "key": "limit_entry",
        "label": "Adaptive limit entry (E1)",
        "stage": "LIMIT_ENTRY_SHADOW",
        "description": "Shadows a repricing limit ladder against the market orders actually sent.",
        "reason": "Deferred: no measured broker-fill baseline exists, and an unsubmitted limit order proves nothing.",
    },
)

EMPTY_ACTIVITY = {"observed_candidates": 0, "shadow_blocks": 0, "enforced_blocks": 0, "no_verdict": 0}


@dataclass(frozen=True)
class _Reading:
    """One pass over one database: the proof lookup must not read another."""

    conn: Any
    db_path: Any
    code_hash: str | None
    source_hashes: dict | None
    activity: dict | None
    since: str


def build_v2_status_payload(
    *,
    window_days: int = DEFAULT_ACTIVITY_WINDOW_DAYS,
    run_limit: int = DEFAULT_RUN_LIMIT,
    db_path: Any = None,
) -> dict:
    db_path = database_path(db_path)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=window_days)).isoformat()
    policies = _load_policies()
    with closing(get_connection(db_path)) as conn:
        hashes = _source_hashes()
        code_hash = _fingerprint(hashes) if hashes else None
        reading = _Reading(conn, db_path, code_hash, hashes, _shadow_activity(conn, since), since)
        features = [_feature_status(reading, feature, policies) for feature in GUARDED_FEATURES]
        return {
            "as_of": now.isoformat(),
            "window_days": window_days,
            "source_hash": reading.code_hash,
            "mode_summary": _mode_summary(features),
            "activity_available": reading.activity is not None,
            "rejection_reasons": _rejection_reasons(conn, since) if reading.activity is not None else None,
            "features": features,
            "shadow_only_features": [_shadow_only_status(feature, reading.activity) for feature in SHADOW_ONLY_FEATURES],
            "research": _research_status(conn, reading.code_hash, run_limit),
            "prerequisites": _prerequisites(conn, since, window_days),
        }


def _load_policies() -> dict:
    """Effective policies, after guard_policy has applied any downgrade.

    A malformed mode variable raises here exactly as it raises in the trading
    pipeline, so the exception is kept and reported against its own feature.
    """
    from services.gates.catalyst_gates import CatalystGatePolicy
    from services.gates.contract_policy import ContractPolicy
    from services.gates.evidence_continuation_policy import EvidenceContinuationPolicy
    from services.gates.market_regime_gate import MarketRegimePolicy
    from services.gates.entry_extension_gate import EntryExtensionPolicy
    from services.gates.market_regime_variants import RegimeSpyTrendPolicy
    from services.gates.tradability_gate import TradabilityPolicy

    loaders = {
        "catalyst": CatalystGatePolicy.from_env,
        "contracts": ContractPolicy.from_env,
        "continuation": EvidenceContinuationPolicy.from_env,
        "regime": MarketRegimePolicy.from_env,
        "entry_extension": EntryExtensionPolicy.from_env,
        "regime_spy_trend": RegimeSpyTrendPolicy.from_env,
        "tradability": TradabilityPolicy.from_env,
    }
    policies = {}
    for block, loader in loaders.items():
        try:
            policies[block] = loader()
        except Exception as exc:
            logger.warning("V2 policy %s could not be read: %s", block, exc)
            policies[block] = exc
    return policies


@lru_cache(maxsize=1)
def _trading_path_hashes() -> dict:
    """Once per process: a running process's code cannot change. A raised error is not cached."""
    from services.core import trading_path

    return trading_path.trading_path_hashes()


def _source_hashes() -> dict | None:
    try:
        return dict(_trading_path_hashes())
    except OSError as exc:
        logger.warning("V2 source hash could not be computed: %s", exc)
        return None


def _fingerprint(hashes: dict) -> str:
    from services.research.research_contract import fingerprint

    return fingerprint(hashes)


def _requested_mode(env_var: str) -> str:
    """The raw operator request, before promotion evidence is consulted."""
    value = str(os.getenv(env_var, "shadow")).strip().lower()
    return value if value in VALID_MODES else "invalid: " + value


def _feature_status(reading: _Reading, feature: dict, policies: dict) -> dict:
    policy = policies.get(feature["block"])
    requested = _requested_mode(feature["env_var"])
    status = {
        "key": feature["key"],
        "label": feature["label"],
        "description": feature["description"],
        "env_var": feature["env_var"],
        "requested_mode": requested,
        "activity_basis": feature.get("activity_basis", "candidates"),
    }
    if isinstance(policy, Exception):
        return {**status, "effective_mode": None, "downgraded": False, "stages": [],
                "configuration_error": str(policy)}

    effective = str(getattr(policy, feature["field"]))
    return {
        **status,
        "effective_mode": effective,
        "downgraded": requested == "enforce" and effective != "enforce",
        "stages": [_stage_status(reading, stage, policy, feature) for stage in feature["stages"]],
        "detail": _scenario_divergence(reading) if feature["key"] == "scenario_selection" else None,
    }


def _stage_status(reading: _Reading, stage: str, policy, feature: dict) -> dict:
    """One promotion stage: its proof, and what the shadow stream has seen."""
    from services.research.research_promotion import gate_code_hash, policy_hash

    proofs = _promotion_rows(reading.conn, stage)
    current_hash = (policy_hash(stage, policy, gate_code_hash(stage, reading.source_hashes))
                    if reading.source_hashes else None)
    matching = next((row for row in proofs if row["policy_hash"] == current_hash), None)
    measured_on_candidates = feature.get("activity_basis", "candidates") == "candidates"
    return {
        "stage": stage,
        "enforcement_allowed": _enforcement_allowed(reading, stage, policy),
        "proof": matching,
        "superseded_proofs": len(proofs) - (1 if matching else 0),
        "activity": _activity_for(reading.activity, stage) if measured_on_candidates else None,
    }


def _activity_for(activity: dict | None, stage: str) -> dict | None:
    """A null reading means the stream is absent, which a measured zero does not."""
    return None if activity is None else activity.get(stage, EMPTY_ACTIVITY)


def _enforcement_allowed(reading: _Reading, stage: str, policy) -> bool:
    """research_promotion.approved is the authority; never re-derive its rule.

    The reading's source hashes are passed through so one payload hashes the sources
    once instead of once per stage.
    """
    from services.research.research_promotion import approved

    try:
        return bool(approved(stage, policy, db_path=reading.db_path, source_hashes=reading.source_hashes))
    except Exception as exc:
        logger.warning("Promotion proof lookup failed for %s: %s", stage, exc)
        return False


def _promotion_rows(conn, stage: str) -> list[dict]:
    if not _table_exists(conn, "research_gate_promotions"):
        return []
    rows = conn.execute(
        """SELECT policy_hash, report_id, information_cutoff_time, sample_size, gate_value
           FROM research_gate_promotions WHERE stage=? ORDER BY information_cutoff_time DESC""",
        (stage,),
    ).fetchall()
    return [dict(row) for row in rows]


def _shadow_activity(conn, since: str) -> dict | None:
    """Per stage: candidates that reached it, and what it would have blocked.

    A `shadow=1` rejection is a verdict the gate recorded without acting on it,
    which is the whole point of the observation period. Returns None where the
    lifecycle tables are absent, so a panel never reads that as a quiet gate.
    """
    if not (_table_exists(conn, "candidate_rejections") and _table_exists(conn, "candidate_snapshots")):
        return None
    activity: dict[str, dict] = {}
    for row in conn.execute(
        f"""SELECT stage, COUNT(DISTINCT candidate_id) AS observed FROM candidate_snapshots
           WHERE julianday(captured_at) >= julianday(?) AND {PRODUCTION_CANDIDATE} GROUP BY stage""",
        (since,),
    ):
        activity.setdefault(row["stage"], dict(EMPTY_ACTIVITY))["observed_candidates"] = row["observed"]
    for row in conn.execute(
        f"""SELECT stage, shadow, COUNT(DISTINCT candidate_id) AS blocked FROM candidate_rejections
           WHERE julianday(occurred_at) >= julianday(?) AND {PRODUCTION_CANDIDATE} GROUP BY stage, shadow""",
        (since,),
    ):
        counter = "shadow_blocks" if row["shadow"] else "enforced_blocks"
        activity.setdefault(row["stage"], dict(EMPTY_ACTIVITY))[counter] = row["blocked"]
    if _table_exists(conn, "catalyst_gate_observations"):
        # No verdict is not a rejection (promotion research reads rejections), yet enforcement would still refuse entry.
        row = conn.execute(
            f"""SELECT COUNT(DISTINCT candidate_id) AS missing FROM catalyst_gate_observations
               WHERE stage='FRESHNESS' AND remaining_value IS NULL AND julianday(observed_at) >= julianday(?)
               AND {PRODUCTION_CANDIDATE}""",
            (since,),
        ).fetchone()
        activity.setdefault("FRESHNESS", dict(EMPTY_ACTIVITY))["no_verdict"] = row["missing"]
    return activity


def _rejection_reasons(conn, since: str) -> list[dict]:
    """Candidates stopped per stage and reason code in the window, enforced and shadow apart."""
    rows = conn.execute(
        f"""SELECT stage, reason_code, shadow, COUNT(DISTINCT candidate_id) AS candidates FROM candidate_rejections
           WHERE julianday(occurred_at) >= julianday(?) AND {PRODUCTION_CANDIDATE}
           GROUP BY stage, reason_code, shadow ORDER BY candidates DESC, stage, reason_code""",
        (since,),
    )
    return [{"stage": row["stage"], "reason_code": row["reason_code"], "shadow": bool(row["shadow"]),
             "candidates": row["candidates"]} for row in rows]


def _scenario_divergence(reading: _Reading) -> dict | None:
    """Scenario selection never rejects a candidate; it picks a different contract.

    Counting rejections would report a permanent zero, so the shadow signal is
    how often the scenario ranking disagreed with the contract actually used.
    """
    if not _table_exists(reading.conn, "contract_research_observations"):
        return None
    comparisons = [
        _loads(row["payload_json"]) for row in reading.conn.execute(
            """SELECT payload_json FROM contract_research_observations
               WHERE stage='SCENARIO_SELECTION' AND julianday(observed_at) >= julianday(?)""",
            (reading.since,),
        )
    ]
    scored = [item for item in comparisons if item.get("status") == "complete"]
    differing = [
        item for item in scored
        if item.get("scenario_option_symbol") and item["scenario_option_symbol"] != item.get("baseline_option_symbol")
    ]
    return {
        "observations": len(comparisons),
        "scored": len(scored),
        "different_contract": len(differing),
        "basis": "scenario ranking compared against the contract the pipeline used",
    }


def _shadow_only_status(feature: dict, activity: dict | None) -> dict:
    return {**feature, "activity": _activity_for(activity, feature["stage"])}


def _mode_summary(features: list[dict]) -> dict:
    modes = [feature.get("effective_mode") for feature in features]
    return {
        "enforcing": sum(mode == "enforce" for mode in modes),
        "shadow": sum(mode in ("shadow", "observe") for mode in modes),
        "disabled": sum(mode == "disabled" for mode in modes),
        "downgraded": sum(bool(feature.get("downgraded")) for feature in features),
        "misconfigured": sum("configuration_error" in feature for feature in features),
    }


def _research_status(conn, code_hash: str | None, run_limit: int) -> dict:
    if not _table_exists(conn, "research_runs"):
        return {
            "available": False,
            "reason": "Research tables do not exist in this database; the Part 6 migration has not run here.",
            "runs": [],
            "champion": None,
        }
    runs = [
        _run_status(conn, dict(row), code_hash)
        for row in conn.execute(
            "SELECT * FROM research_runs ORDER BY julianday(started_at) DESC LIMIT ?", (run_limit,)
        )
    ]
    champion = conn.execute("SELECT * FROM research_champion WHERE singleton=1").fetchone()
    return {"available": True, "runs": runs, "champion": dict(champion) if champion else None}


def _run_status(conn, run: dict, code_hash: str | None) -> dict:
    """A run's enrollment progress and its latest full-cohort report.

    Broad source identity still governs promotion proofs. New runs separately
    freeze evaluator identity so a strategy change need not stop collection.
    """
    enrolled = conn.execute(
        "SELECT COUNT(*) FROM research_candidates WHERE run_id=?", (run["run_id"],)
    ).fetchone()[0]
    policy = _loads(run.get("policy_json"))
    return {
        "run_id": run["run_id"],
        "kind": run["kind"],
        "status": run["status"],
        "started_at": run["started_at"],
        "information_cutoff_time": run["information_cutoff_time"],
        "target_candidates": run["target_candidates"],
        "enrolled_candidates": enrolled,
        "promotion_sample_floor": policy.get("minimum_promotion_samples"),
        "code_hash_matches": bool(code_hash) and run["code_hash"] == code_hash,
        "collection_version_matches": _collection_version_matches(conn, run, code_hash),
        "latest_full_cohort_report": _latest_report(conn, run["run_id"], enrolled, policy),
    }


def _collection_version_matches(conn, run: dict, code_hash: str | None) -> bool:
    if not str(run.get("config_hash", "")).startswith("research-v2:"):
        return bool(code_hash) and run["code_hash"] == code_hash
    if not _table_exists(conn, "research_run_versions"):
        return False
    from market_db_modules.research_runs import read_run
    from services.research.research_versions import require_current_evaluator
    try:
        require_current_evaluator(read_run(conn, run["run_id"]), code_hash)
    except (ValueError, TypeError, KeyError):
        return False
    return True


def _latest_report(conn, run_id: str, cohort_size: int, policy: dict) -> dict | None:
    row = conn.execute(
        """SELECT report_id, information_cutoff_time, report_json FROM research_reports
           WHERE run_id=? AND month=? ORDER BY julianday(information_cutoff_time) DESC LIMIT 1""",
        (run_id, FULL_COHORT_MONTH),
    ).fetchone()
    if row is None:
        return None
    report = _loads(row["report_json"])
    sample_floor = policy.get("minimum_promotion_samples") or 0
    return {
        "report_id": row["report_id"],
        "information_cutoff_time": row["information_cutoff_time"],
        "n_observed": report.get("n_observed"),
        "covers_whole_cohort": report.get("n_observed") == cohort_size,
        "paired_regimes": report.get("paired_regimes") or [],
        "gates": [_gate_readiness(gate, sample_floor) for gate in report.get("gates") or []],
    }


def _gate_readiness(gate: dict, sample_floor: int) -> dict:
    return {
        "stage": gate.get("stage"),
        "n_seen": gate.get("n_seen"),
        "n_evaluable": gate.get("n_evaluable"),
        "n_passed": gate.get("n_passed"),
        "n_rejected": gate.get("n_rejected"),
        "n_missing_outcomes": gate.get("n_missing_outcomes"),
        "regimes": gate.get("regimes") or [],
        "basis": gate.get("basis") or "option_pnl",
        "gate_value": safe_float(gate.get("gate_value")),
        "losses_avoided": safe_float(gate.get("losses_avoided")),
        "profits_sacrificed": safe_float(gate.get("profits_sacrificed")),
        "blocking_reasons": _promotion_blockers(gate, sample_floor),
    }


def _promotion_blockers(gate: dict, sample_floor: int) -> list[str]:
    """Which promote_gate conditions this gate currently fails.

    market_db_modules.research_reports.promote_gate is the authority and raises a
    single lumped error; this names the individual conditions so the panel can
    say what is still missing rather than only that something is.
    """
    # Move-scored gates need forward stock returns, not priced option trades.
    option_basis = gate.get("basis") != "underlying_residual_move"
    checks = (
        (not gate.get("policy_hash"), "no policy hash recorded for this stage"),
        (not gate.get("coverage_complete"), "counterfactual coverage is incomplete"),
        (option_basis and not gate.get("production_selection_complete"), "some production contract selections are unknown"),
        (option_basis and not gate.get("daily_coverage_complete"), "daily option marks are missing"),
        ((gate.get("n_evaluable") or 0) < sample_floor, "fewer than " + str(sample_floor) + " evaluable observations"),
        (not gate.get("n_passed"), "no passed observations"),
        (not gate.get("n_rejected"), "no rejected observations"),
        (len(gate.get("regimes") or []) < REQUIRED_REGIME_COUNT, "fewer than two known regimes"),
        ((gate.get("gate_value") or 0) <= 0, "gate value is not positive"),
    )
    return [reason for failed, reason in checks if failed]


def _prerequisites(conn, since: str, window_days: int) -> list[dict]:
    """The inputs the whole rollout waits on, each read from live data."""
    return [
        _candidate_stream(conn, since, window_days),
        _regime_diversity(conn, since, window_days),
        _option_mark_coverage(conn),
        _outcome_resolution(conn),
    ]


def _candidate_stream(conn, since: str, window_days: int) -> dict:
    if not _table_exists(conn, "candidates"):
        return _prerequisite("candidate_stream", "Candidate stream", "missing", "No table",
                             "The candidate lifecycle tables do not exist in this database yet.")
    row = conn.execute(
        f"""SELECT COUNT(*) AS recent, MAX(created_at) AS latest,
           (SELECT COUNT(*) FROM candidates WHERE {PRODUCTION_CANDIDATE}) AS total FROM candidates
           WHERE julianday(created_at) >= julianday(?) AND {PRODUCTION_CANDIDATE}""",
        (since,),
    ).fetchone()
    if not row["total"]:
        return _prerequisite("candidate_stream", "Candidate stream", "missing", "0 candidates",
                             "No candidate has been recorded, so no gate has anything to observe.")
    per_week = (row["recent"] / window_days) * 7 if window_days else 0
    return _prerequisite(
        "candidate_stream",
        "Candidate stream",
        "ok" if row["recent"] else "attention",
        str(row["recent"]) + " in " + str(window_days) + "d",
        "Roughly {:.1f} candidates a week; {} recorded in total. Last seen {}.".format(
            per_week, row["total"], row["latest"]),
    )


def _regime_diversity(conn, since: str, window_days: int) -> dict:
    """Promotion needs two known regimes, and reports discard `unknown` entirely."""
    if not _table_exists(conn, "market_regime_runs"):
        return _prerequisite("regime_diversity", "Regime diversity", "missing", "No table",
                             "Market regime runs have never been recorded in this database.")
    states = [
        row["state"] for row in conn.execute(
            "SELECT DISTINCT state FROM market_regime_runs WHERE julianday(as_of) >= julianday(?)", (since,)
        )
    ]
    known = sorted(state for state in states if state and state != UNKNOWN_REGIME)
    latest = conn.execute(
        "SELECT state FROM market_regime_runs ORDER BY julianday(as_of) DESC LIMIT 1"
    ).fetchone()
    return _prerequisite(
        "regime_diversity",
        "Regime diversity",
        "ok" if len(known) >= REQUIRED_REGIME_COUNT else "attention",
        str(len(known)) + " known in " + str(window_days) + "d",
        "Latest classification is '{}'. Promotion needs {} known regimes in the cohort, and '{}' is discarded, "
        "so a degraded snapshot can never satisfy it.".format(
            (latest["state"] if latest else None) or "none", REQUIRED_REGIME_COUNT, UNKNOWN_REGIME),
    )


def _option_mark_coverage(conn) -> dict:
    """R1 prices rejected candidates from cached daily option quotes."""
    if not _table_exists(conn, "historical_option_quotes"):
        return _prerequisite("option_marks", "Daily option marks", "missing", "No table",
                             "Historical option quote storage does not exist in this database.")
    row = conn.execute(
        """SELECT COUNT(*) AS quotes, COUNT(DISTINCT option_symbol) AS symbols, MAX(quote_at) AS latest
           FROM historical_option_quotes"""
    ).fetchone()
    if not row["quotes"]:
        return _prerequisite(
            "option_marks", "Daily option marks", "missing", "0 quotes",
            "Without cached daily marks every counterfactual outcome is unevaluable, so no gate value can be computed.",
        )
    return _prerequisite(
        "option_marks", "Daily option marks", "ok", str(row["quotes"]) + " quotes",
        "{} option symbols cached; latest mark {}.".format(row["symbols"], row["latest"]),
    )


def _outcome_resolution(conn) -> dict:
    """Unresolved decisions leave expectancy and calibration reading nothing."""
    if not _table_exists(conn, "manager_decision_history"):
        return _prerequisite("outcome_resolution", "Outcome resolution", "missing", "No table",
                             "Manager decision history does not exist in this database.")
    row = conn.execute(
        """SELECT COUNT(*) AS executed,
           SUM(CASE WHEN outcome_status='closed' THEN 1 ELSE 0 END) AS closed,
           SUM(CASE WHEN COALESCE(outcome_status,'') != 'closed' THEN 1 ELSE 0 END) AS unresolved
           FROM manager_decision_history WHERE trade_executed=1"""
    ).fetchone()
    executed, closed, unresolved = row["executed"] or 0, row["closed"] or 0, row["unresolved"] or 0
    if not executed:
        return _prerequisite("outcome_resolution", "Outcome resolution", "attention", "No executed trades",
                             "No executed decision exists to resolve yet.")
    return _prerequisite(
        "outcome_resolution",
        "Outcome resolution",
        "ok" if closed and unresolved <= closed else "attention",
        "{}/{} closed".format(closed, executed),
        "{} executed decisions have no closed outcome; calibration and expectancy read only resolved rows.".format(
            unresolved),
    )


def _prerequisite(key: str, label: str, status: str, value: str, detail: str) -> dict:
    return {"key": key, "label": label, "status": status, "value": value, "detail": detail}


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _loads(value) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
