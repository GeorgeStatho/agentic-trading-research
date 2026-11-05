from __future__ import annotations

"""Leakage-safe, no-execution A/B replay for manager autopsy feedback."""

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from services.core.settings import get_settings
from services.core.settings import load_process_settings

from agent_helpers.manager_inputs import (
    build_resolved_autopsy_insights,
)
from agent_helpers.llm_client import Client  # noqa: E402
from agent_stages.manager_runner import replay_manager_decision_from_payload  # noqa: E402
from agent_stages.manager_model import MANAGER_PROMPT_VERSION  # noqa: E402
from market_db_modules.llm_replay_store import frozen_manager_input  # noqa: E402
from market_db_modules.manager_replay import (  # noqa: E402
    complete_manager_replay_evaluation,
    create_manager_replay_evaluation,
    list_replayable_manager_decisions,
    record_manager_replay_result,
)


ReplayDecider = Callable[[dict[str, Any]], dict[str, Any]]
_DIRECTIONAL_DECISIONS = {"call", "put"}
_CONFIDENCE_BUCKETS = ("high", "medium", "low")
HISTORICAL_OUTCOME_AUDIT_VERSION = "historical-outcome-audit-v1"
LEGACY_PARTIAL_REPLAY_VERSION = "legacy-partial-replay-v1"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_pnl(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _execution_mode(decision: dict[str, Any]) -> str:
    paper = decision.get("execution_paper")
    if paper is None:
        return "unlinked"
    return "paper" if bool(paper) else "live"


def _execution_details(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": _execution_mode(decision),
        "record_id": int(decision.get("trade_execution_record_id") or 0) or None,
        "order_id": str(decision.get("trade_execution_order_id") or ""),
        "status": str(decision.get("execution_order_status") or ""),
        "option_symbol": str(decision.get("execution_option_symbol") or ""),
        "underlying_symbol": str(decision.get("execution_underlying_symbol") or ""),
        "quantity": decision.get("execution_order_qty"),
        "side": str(decision.get("execution_order_side") or ""),
        "submitted_at": str(decision.get("execution_submitted_at") or ""),
    }


def _outcome_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_values = [float(event["pnl_pct"]) for event in events if event.get("pnl_pct") is not None]
    wins = [value for value in pnl_values if value > 0]
    false_positives = [value for value in pnl_values if value <= 0]
    return {
        "count": len(events),
        "win_rate": (len(wins) / len(pnl_values)) if pnl_values else None,
        "average_pnl_pct": (sum(pnl_values) / len(pnl_values)) if pnl_values else None,
        "false_positive_rate": (len(false_positives) / len(pnl_values)) if pnl_values else None,
    }


def build_replay_variants(
    decision: dict[str, Any],
    *,
    autopsy_limit: int,
    autopsy_max_age_days: int,
    db_path: Path | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[int]] | None:
    """Build baseline and feedback inputs from the same frozen decision snapshot.

    Feedback is strictly limited to resolved same-company autopsies whose P/L
    update had already occurred before this decision's timestamp.
    """
    replay_input = frozen_manager_input(decision)
    if replay_input is None:
        return None

    baseline_payload = copy.deepcopy(replay_input)
    feedback_payload = copy.deepcopy(replay_input)
    baseline_payload["resolved_autopsy_insights"] = []
    feedback = build_resolved_autopsy_insights(
        feedback_payload["company"],
        limit=autopsy_limit,
        max_age_days=autopsy_max_age_days,
        as_of=decision.get("decision_run_at"),
        db_path=db_path,
        exclude_decision_history_id=int(decision.get("id") or 0),
    )
    feedback_payload["resolved_autopsy_insights"] = feedback
    autopsy_ids = [int(item.get("autopsy_id") or 0) for item in feedback if int(item.get("autopsy_id") or 0) > 0]
    return baseline_payload, feedback_payload, autopsy_ids


def _legacy_article_summaries(manager_input: dict[str, Any]) -> list[dict[str, Any]]:
    article_references = manager_input.get("article_references")
    if not isinstance(article_references, list):
        article_references = (
            manager_input.get("supporting_articles", {}).get("article_summaries", [])
            if isinstance(manager_input.get("supporting_articles"), dict)
            else []
        )
    summaries: list[dict[str, Any]] = []
    for article in article_references[:5] if isinstance(article_references, list) else []:
        if not isinstance(article, dict):
            continue
        summaries.append(
            {
                "article_id": int(article.get("article_id") or 0),
                "title": str(article.get("title") or ""),
                "summary": str(article.get("summary") or ""),
                "source": str(article.get("source") or ""),
                "published_at": str(article.get("published_at") or ""),
                "article_scope": str(article.get("article_scope") or ""),
                "evidence_layers": [
                    str(layer).strip()
                    for layer in article.get("evidence_layers", [])
                    if str(layer).strip()
                ]
                if isinstance(article.get("evidence_layers"), list)
                else [],
            }
        )
    return summaries


def build_legacy_partial_replay_payload(
    decision: dict[str, Any],
    *,
    autopsy_limit: int,
    autopsy_max_age_days: int,
    db_path: Path | str | None = None,
) -> tuple[dict[str, Any], list[int]] | None:
    """Reconstruct a constrained prompt from legacy decision-time fields only."""
    manager_input = _json_object(decision.get("manager_input_json"))
    if isinstance(manager_input.get("replay_input"), dict):
        return None
    company = manager_input.get("company") if isinstance(manager_input.get("company"), dict) else {}
    if not company:
        company = {
            "company_id": decision.get("company_id"),
            "symbol": decision.get("symbol"),
            "name": decision.get("company_name"),
        }
    symbol = str(company.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    company = {**company, "symbol": symbol}
    market_context = manager_input.get("market_context") if isinstance(manager_input.get("market_context"), dict) else {}
    strategist = (
        manager_input.get("strategist_recommendation")
        if isinstance(manager_input.get("strategist_recommendation"), dict)
        else {}
    )
    article_summaries = _legacy_article_summaries(manager_input)
    if not market_context and not strategist and not article_summaries:
        return None
    feedback = build_resolved_autopsy_insights(
        company,
        limit=autopsy_limit,
        max_age_days=autopsy_max_age_days,
        as_of=decision.get("decision_run_at"),
        db_path=db_path,
        exclude_decision_history_id=int(decision.get("id") or 0),
    )
    payload = {
        "company": company,
        "filters": {
            "replay_mode": "legacy_partial",
            "source": "decision_time_snapshot_only",
            "limitations": [
                "No full opportunist rollup was retained for this decision.",
                "Article summaries are limited to saved references and fields.",
                "No live data, contract selection, or execution is available in this replay.",
            ],
        },
        "opportunist_rollup": {
            "source": "saved_article_references",
            "article_reference_count": len(article_summaries),
            "limitations": "Legacy partial reconstruction; do not infer missing research evidence.",
        },
        "views": {
            "legacy_saved_references": {
                "label": "Saved article references",
                "count": len(article_summaries),
                "agent_conclusions": [],
            }
        },
        "supporting_articles": {"article_summaries": article_summaries, "full_articles": []},
        "market_context": copy.deepcopy(market_context),
        "strategist_recommendation": copy.deepcopy(strategist),
        "resolved_autopsy_insights": feedback,
    }
    autopsy_ids = [
        int(item.get("autopsy_id") or 0)
        for item in feedback
        if int(item.get("autopsy_id") or 0) > 0
    ]
    return payload, autopsy_ids


def calculate_replay_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Score only replays matching the historically executed direction.

    Alternate directions and no-trade decisions are retained as decision-change
    evidence but not assigned fictional P/L without historical option pricing.
    """
    metrics: dict[str, Any] = {}
    for mode in ("baseline", "resolved_autopsy_feedback"):
        mode_events = [event for event in events if event.get("replay_mode") == mode]
        completed = [event for event in mode_events if not event.get("error")]
        directional = [event for event in completed if event.get("replay_decision") in _DIRECTIONAL_DECISIONS]
        scored = [
            event
            for event in directional
            if event.get("replay_decision") == event.get("historical_decision") and event.get("pnl_pct") is not None
        ]
        pnl_values = [float(event["pnl_pct"]) for event in scored]
        wins = [value for value in pnl_values if value > 0]
        false_positives = [value for value in pnl_values if value <= 0]
        buckets: dict[str, dict[str, Any]] = {}
        for confidence in _CONFIDENCE_BUCKETS:
            bucket_values = [
                float(event["pnl_pct"])
                for event in scored
                if event.get("replay_confidence") == confidence
            ]
            bucket_wins = [value for value in bucket_values if value > 0]
            bucket_losses = [value for value in bucket_values if value <= 0]
            buckets[confidence] = {
                "count": len(bucket_values),
                "win_rate": (len(bucket_wins) / len(bucket_values)) if bucket_values else None,
                "average_pnl_pct": (sum(bucket_values) / len(bucket_values)) if bucket_values else None,
                "false_positive_rate": (len(bucket_losses) / len(bucket_values)) if bucket_values else None,
            }
        metrics[mode] = {
            "replayed_count": len(completed),
            "error_count": len(mode_events) - len(completed),
            "approved_count": len(directional),
            "scored_historical_trade_count": len(scored),
            "counterfactual_unscored_count": len(directional) - len(scored),
            "win_rate": (len(wins) / len(scored)) if scored else None,
            "average_pnl_pct": (sum(pnl_values) / len(pnl_values)) if pnl_values else None,
            "false_positive_rate": (len(false_positives) / len(scored)) if scored else None,
            "confidence_buckets": buckets,
            "execution_modes": {
                execution_mode: _outcome_summary(
                    [event for event in scored if event.get("execution_mode") == execution_mode]
                )
                for execution_mode in ("paper", "live", "unlinked")
            },
        }
    return metrics


def calculate_historical_outcome_audit_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate observed calibration from stored manager decisions and outcomes."""
    audited = [event for event in events if event.get("manager_decision") in _DIRECTIONAL_DECISIONS]
    pnl_values = [float(event["pnl_pct"]) for event in audited if event.get("pnl_pct") is not None]
    wins = [value for value in pnl_values if value > 0]
    false_positives = [value for value in pnl_values if value <= 0]
    confidence_buckets: dict[str, dict[str, Any]] = {}
    for confidence in _CONFIDENCE_BUCKETS:
        bucket_values = [
            float(event["pnl_pct"])
            for event in audited
            if event.get("manager_confidence") == confidence and event.get("pnl_pct") is not None
        ]
        bucket_wins = [value for value in bucket_values if value > 0]
        bucket_losses = [value for value in bucket_values if value <= 0]
        confidence_buckets[confidence] = {
            "count": len(bucket_values),
            "win_rate": (len(bucket_wins) / len(bucket_values)) if bucket_values else None,
            "average_pnl_pct": (sum(bucket_values) / len(bucket_values)) if bucket_values else None,
            "false_positive_rate": (len(bucket_losses) / len(bucket_values)) if bucket_values else None,
        }
    return {
        "audited_decision_count": len(audited),
        "non_directional_excluded_count": len(events) - len(audited),
        "win_rate": (len(wins) / len(pnl_values)) if pnl_values else None,
        "average_pnl_pct": (sum(pnl_values) / len(pnl_values)) if pnl_values else None,
        "false_positive_rate": (len(false_positives) / len(pnl_values)) if pnl_values else None,
        "confidence_buckets": confidence_buckets,
        "execution_modes": {
            execution_mode: _outcome_summary(
                [event for event in audited if event.get("execution_mode") == execution_mode]
            )
            for execution_mode in ("paper", "live", "unlinked")
        },
    }


def run_historical_outcome_audit(
    *,
    limit: int = 100,
    db_path: Path | str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Audit resolved historical manager outcomes without invoking any model."""
    source_rows = [dict(row) for row in list_replayable_manager_decisions(limit=limit, db_path=db_path)]
    evaluation_id = None
    if persist:
        evaluation_id = create_manager_replay_evaluation(
            model="stored-manager-decision-history",
            prompt_version=HISTORICAL_OUTCOME_AUDIT_VERSION,
            autopsy_limit=1,
            autopsy_max_age_days=1,
            db_path=db_path,
        )

    events: list[dict[str, Any]] = []
    for decision in source_rows:
        manager_decision = str(decision.get("manager_decision") or "").lower()
        manager_confidence = str(decision.get("manager_confidence") or "").lower()
        event = {
            "manager_decision_history_id": int(decision.get("id") or 0),
            "manager_decision": manager_decision,
            "manager_confidence": manager_confidence,
            "pnl_pct": _coerce_pnl(decision.get("latest_trade_pnl_pct")),
            "outcome_label": str(decision.get("resolved_outcome_label") or ""),
            "execution_mode": _execution_mode(decision),
            "execution": _execution_details(decision),
        }
        events.append(event)
        if evaluation_id is not None:
            record_manager_replay_result(
                evaluation_id=evaluation_id,
                manager_decision_history_id=event["manager_decision_history_id"],
                replay_mode="historical_outcome_audit",
                decision_run_at=decision.get("decision_run_at"),
                autopsy_ids=[],
                recommendation={
                    "decision": manager_decision,
                    "confidence": manager_confidence,
                    "source": "stored_manager_decision_history",
                },
                db_path=db_path,
            )

    metrics = calculate_historical_outcome_audit_metrics(events)
    report = {
        "evaluation_id": evaluation_id,
        "mode": "historical_outcome_audit",
        "offline_only": True,
        "model_invoked": False,
        "source_decision_count": len(source_rows),
        "metrics": metrics,
        "events": events,
    }
    if evaluation_id is not None:
        complete_manager_replay_evaluation(
            evaluation_id=evaluation_id,
            source_decision_count=len(source_rows),
            replayed_decision_count=len(events),
            skipped_decision_count=0,
            metrics=metrics,
            completed_at=datetime.now(timezone.utc),
            db_path=db_path,
        )
    return report


def run_legacy_partial_replay(
    *,
    model: str | None = None,
    limit: int = 100,
    autopsy_limit: int | None = None,
    autopsy_max_age_days: int | None = None,
    client: Client | None = None,
    db_path: Path | str | None = None,
    persist: bool = True,
    replay_decider: ReplayDecider | None = None,
) -> dict[str, Any]:
    """Run a separately reported approximation for legacy snapshot records."""
    model = model or get_settings().models.manager.model
    if autopsy_limit is None:
        autopsy_limit = get_settings().manager_context.resolved_autopsy_limit
    if autopsy_max_age_days is None:
        autopsy_max_age_days = get_settings().manager_context.resolved_autopsy_max_age_days
    source_rows = [dict(row) for row in list_replayable_manager_decisions(limit=limit, db_path=db_path)]
    evaluation_id = None
    if persist:
        evaluation_id = create_manager_replay_evaluation(
            model=model,
            prompt_version=LEGACY_PARTIAL_REPLAY_VERSION,
            autopsy_limit=autopsy_limit,
            autopsy_max_age_days=autopsy_max_age_days,
            db_path=db_path,
        )

    def decide(payload: dict[str, Any]) -> dict[str, Any]:
        if replay_decider is not None:
            return replay_decider(payload)
        return replay_manager_decision_from_payload(payload, client=client, model=model)

    events: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for decision in source_rows:
        rebuilt = build_legacy_partial_replay_payload(
            decision,
            autopsy_limit=autopsy_limit,
            autopsy_max_age_days=autopsy_max_age_days,
            db_path=db_path,
        )
        if rebuilt is None:
            skipped.append(
                {
                    "manager_decision_history_id": decision.get("id"),
                    "reason": "exact_replay_available_or_missing_legacy_snapshot_context",
                }
            )
            continue
        payload, autopsy_ids = rebuilt
        event = {
            "manager_decision_history_id": int(decision.get("id") or 0),
            "replay_mode": "legacy_partial",
            "historical_decision": str(decision.get("manager_decision") or "").lower(),
            "historical_confidence": str(decision.get("manager_confidence") or "").lower(),
            "pnl_pct": _coerce_pnl(decision.get("latest_trade_pnl_pct")),
            "outcome_label": str(decision.get("resolved_outcome_label") or ""),
            "autopsy_ids": autopsy_ids,
        }
        recommendation: dict[str, Any] | None = None
        try:
            result = decide(payload)
            recommendation = result.get("recommendation") if isinstance(result, dict) else None
            if not isinstance(recommendation, dict):
                raise RuntimeError("Legacy partial replay did not return a recommendation object.")
            event["replay_decision"] = str(recommendation.get("decision") or "").lower()
            event["replay_confidence"] = str(recommendation.get("confidence") or "").lower()
            event["recommendation"] = recommendation
        except Exception as exc:
            event["error"] = str(exc)
        events.append(event)
        if evaluation_id is not None:
            record_manager_replay_result(
                evaluation_id=evaluation_id,
                manager_decision_history_id=event["manager_decision_history_id"],
                replay_mode="legacy_partial",
                decision_run_at=decision.get("decision_run_at"),
                autopsy_ids=autopsy_ids,
                recommendation=recommendation,
                error_text=event.get("error"),
                db_path=db_path,
            )

    completed = [event for event in events if not event.get("error")]
    directional = [event for event in completed if event.get("replay_decision") in _DIRECTIONAL_DECISIONS]
    scored = [
        event
        for event in directional
        if event.get("replay_decision") == event.get("historical_decision") and event.get("pnl_pct") is not None
    ]
    pnl_values = [float(event["pnl_pct"]) for event in scored]
    metrics = {
        "legacy_partial_replayed_count": len(completed),
        "error_count": len(events) - len(completed),
        "scored_historical_trade_count": len(scored),
        "counterfactual_unscored_count": len(directional) - len(scored),
        "win_rate": (sum(value > 0 for value in pnl_values) / len(pnl_values)) if pnl_values else None,
        "average_pnl_pct": (sum(pnl_values) / len(pnl_values)) if pnl_values else None,
        "false_positive_rate": (sum(value <= 0 for value in pnl_values) / len(pnl_values)) if pnl_values else None,
    }
    report = {
        "evaluation_id": evaluation_id,
        "mode": "legacy_partial",
        "offline_only": True,
        "comparability": "Do not combine these approximate metrics with exact frozen-input replay metrics.",
        "model": model,
        "prompt_version": LEGACY_PARTIAL_REPLAY_VERSION,
        "source_decision_count": len(source_rows),
        "legacy_partial_replayed_count": len(events),
        "skipped": skipped,
        "metrics": metrics,
        "events": events,
    }
    if evaluation_id is not None:
        complete_manager_replay_evaluation(
            evaluation_id=evaluation_id,
            source_decision_count=len(source_rows),
            replayed_decision_count=len(events),
            skipped_decision_count=len(skipped),
            metrics=metrics,
            completed_at=datetime.now(timezone.utc),
            db_path=db_path,
        )
    return report


def run_offline_manager_evaluation(
    *,
    model: str | None = None,
    limit: int = 100,
    autopsy_limit: int | None = None,
    autopsy_max_age_days: int | None = None,
    client: Client | None = None,
    db_path: Path | str | None = None,
    persist: bool = True,
    replay_decider: ReplayDecider | None = None,
) -> dict[str, Any]:
    """Compare frozen manager decisions with and without resolved feedback.

    The function calls only the manager's LLM prompt path. It never refreshes
    market data, invokes deterministic option selection, or submits an order.
    """
    model = model or get_settings().models.manager.model
    if autopsy_limit is None:
        autopsy_limit = get_settings().manager_context.resolved_autopsy_limit
    if autopsy_max_age_days is None:
        autopsy_max_age_days = get_settings().manager_context.resolved_autopsy_max_age_days
    source_rows = [dict(row) for row in list_replayable_manager_decisions(limit=limit, db_path=db_path)]
    evaluation_id = None
    if persist:
        evaluation_id = create_manager_replay_evaluation(
            model=model,
            prompt_version=MANAGER_PROMPT_VERSION,
            autopsy_limit=autopsy_limit,
            autopsy_max_age_days=autopsy_max_age_days,
            db_path=db_path,
        )

    def decide(payload: dict[str, Any]) -> dict[str, Any]:
        if replay_decider is not None:
            return replay_decider(payload)
        return replay_manager_decision_from_payload(payload, client=client, model=model)

    events: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for decision in source_rows:
        variants = build_replay_variants(
            decision,
            autopsy_limit=autopsy_limit,
            autopsy_max_age_days=autopsy_max_age_days,
            db_path=db_path,
        )
        if variants is None:
            skipped.append({"manager_decision_history_id": decision.get("id"), "reason": "missing_frozen_replay_input"})
            continue
        baseline_payload, feedback_payload, autopsy_ids = variants
        for replay_mode, payload, current_autopsy_ids in (
            ("baseline", baseline_payload, []),
            ("resolved_autopsy_feedback", feedback_payload, autopsy_ids),
        ):
            event = {
                "manager_decision_history_id": int(decision.get("id") or 0),
                "replay_mode": replay_mode,
                "historical_decision": str(decision.get("manager_decision") or "").lower(),
                "historical_confidence": str(decision.get("manager_confidence") or "").lower(),
                "pnl_pct": _coerce_pnl(decision.get("latest_trade_pnl_pct")),
                "outcome_label": str(decision.get("resolved_outcome_label") or ""),
                "execution_mode": _execution_mode(decision),
                "execution": _execution_details(decision),
                "autopsy_ids": current_autopsy_ids,
            }
            recommendation: dict[str, Any] | None = None
            try:
                result = decide(payload)
                recommendation = result.get("recommendation") if isinstance(result, dict) else None
                if not isinstance(recommendation, dict):
                    raise RuntimeError("Replay did not return a recommendation object.")
                event["replay_decision"] = str(recommendation.get("decision") or "").lower()
                event["replay_confidence"] = str(recommendation.get("confidence") or "").lower()
                event["recommendation"] = recommendation
            except Exception as exc:  # Preserve individual replay failures for review.
                event["error"] = str(exc)
            events.append(event)
            if evaluation_id is not None:
                record_manager_replay_result(
                    evaluation_id=evaluation_id,
                    manager_decision_history_id=event["manager_decision_history_id"],
                    replay_mode=replay_mode,
                    decision_run_at=decision.get("decision_run_at"),
                    autopsy_ids=current_autopsy_ids,
                    recommendation=recommendation,
                    error_text=event.get("error"),
                    db_path=db_path,
                )

    metrics = calculate_replay_metrics(events)
    report = {
        "evaluation_id": evaluation_id,
        "offline_only": True,
        "model": model,
        "prompt_version": MANAGER_PROMPT_VERSION,
        "source_decision_count": len(source_rows),
        "replayed_decision_count": len(source_rows) - len(skipped),
        "skipped": skipped,
        "metrics": metrics,
        "events": events,
    }
    if evaluation_id is not None:
        complete_manager_replay_evaluation(
            evaluation_id=evaluation_id,
            source_decision_count=len(source_rows),
            replayed_decision_count=len(source_rows) - len(skipped),
            skipped_decision_count=len(skipped),
            metrics=metrics,
            completed_at=datetime.now(timezone.utc),
            db_path=db_path,
        )
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline A/B manager replay with resolved autopsy feedback.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum resolved historical decisions to replay.")
    parser.add_argument("--model", default=get_settings().models.manager.model, help="Configured provider model to replay.")
    parser.add_argument("--autopsy-limit", type=int, default=get_settings().manager_context.resolved_autopsy_limit)
    parser.add_argument("--autopsy-max-age-days", type=int, default=get_settings().manager_context.resolved_autopsy_max_age_days)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--no-persist", action="store_true", help="Print results without writing evaluation records.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--historical-outcome-audit",
        action="store_true",
        help="Report stored decision/confidence/P&L calibration without invoking an LLM.",
    )
    mode_group.add_argument(
        "--legacy-partial-replay",
        action="store_true",
        help="Approximate legacy decisions from saved decision-time snapshots only.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    load_process_settings()
    args = _parse_args()
    if args.historical_outcome_audit:
        result = run_historical_outcome_audit(
            limit=max(1, args.limit),
            db_path=args.db_path,
            persist=not args.no_persist,
        )
    elif args.legacy_partial_replay:
        result = run_legacy_partial_replay(
            model=args.model,
            limit=max(1, args.limit),
            autopsy_limit=max(1, args.autopsy_limit),
            autopsy_max_age_days=max(1, args.autopsy_max_age_days),
            db_path=args.db_path,
            persist=not args.no_persist,
        )
    else:
        result = run_offline_manager_evaluation(
            model=args.model,
            limit=max(1, args.limit),
            autopsy_limit=max(1, args.autopsy_limit),
            autopsy_max_age_days=max(1, args.autopsy_max_age_days),
            db_path=args.db_path,
            persist=not args.no_persist,
        )
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
