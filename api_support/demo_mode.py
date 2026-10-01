from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEMO_ARTICLE_ID = 990001
DEMO_OPTION_SYMBOL = "UBER260724C00090000"


def build_demo_mode_payload() -> dict[str, Any]:
    """Return a deterministic, clearly fictional walkthrough for product demos."""
    now = datetime.now(timezone.utc).isoformat()
    article = {
        "id": DEMO_ARTICLE_ID,
        "title": "Demo scenario: Uber lowers near-term bookings outlook",
        "summary": "Seeded demo evidence only. This is not a live news article or market signal.",
        "source": "Seeded demo scenario",
        "source_url": "",
        "published_at": "2026-07-20T14:00:00+00:00",
        "evidence_score": 0.94,
    }
    score_inputs = {
        "position_exposure": 4.0,
        "impact_magnitude_weight": 1.0,
        "confidence_weight": 1.0,
        "dte": 3,
        "dte_multiplier": 1.5,
        "implied_volatility": 0.62,
        "volatility_multiplier": 1.31,
        "is_adverse": True,
        "alignment_multiplier": 1.0,
        "formula": "position_exposure × impact_magnitude × confidence_weight × DTE/volatility_multiplier",
    }
    exposure = {
        "article_id": DEMO_ARTICLE_ID,
        "has_run": True,
        "demo_data": True,
        "article": article,
        "run": {
            "id": 990001,
            "model": "gpt-5.6-terra demo fixture",
            "prompt_version": "exposure-map-demo-v1",
            "created_at": now,
            "article_summary": "Demo-only scenario: weaker bookings guidance creates adverse short-dated long-call exposure for Uber.",
        },
        "nodes": [
            {"id": "article:990001", "type": "article", "label": article["title"], "subtitle": article["source"], "evidence_score": 0.94},
            {"id": "sector:consumer-discretionary", "type": "sector", "label": "Consumer Discretionary", "subtitle": "consumer-discretionary", "direction": "negative"},
            {"id": "industry:ground-transportation", "type": "industry", "label": "Ground Transportation", "subtitle": "ground-transportation", "direction": "negative"},
            {"id": "company:UBER", "type": "company", "label": "Uber Technologies", "subtitle": "UBER", "direction": "negative"},
            {
                "id": f"position:{DEMO_OPTION_SYMBOL}",
                "type": "position",
                "label": DEMO_OPTION_SYMBOL,
                "subtitle": "UBER long call",
                "direction": "negative",
                "dte": 3,
                "quantity": 4,
                "paper": True,
                "risk_score": 100.0,
                "risk_level": "high",
                "reason": "The fictional guidance cut conflicts with a short-dated long-call position.",
                "score_inputs": score_inputs,
            },
        ],
        "edges": [
            {"id": "evidence:demo-sector", "source": "article:990001", "target": "sector:consumer-discretionary", "kind": "evidence", "label": "Lower bookings guidance can weaken discretionary mobility demand.", "score": 100.0, "direction": "negative", "confidence": "high", "magnitude": "high"},
            {"id": "hierarchy:demo-industry", "source": "sector:consumer-discretionary", "target": "industry:ground-transportation", "kind": "hierarchy", "label": "Industry membership", "score": 0.0, "direction": "negative"},
            {"id": "hierarchy:demo-company", "source": "industry:ground-transportation", "target": "company:UBER", "kind": "hierarchy", "label": "Industry membership", "score": 0.0, "direction": "negative"},
            {"id": "position:demo-uber-call", "source": "company:UBER", "target": f"position:{DEMO_OPTION_SYMBOL}", "kind": "position", "label": "Short DTE and long-call direction amplify adverse exposure.", "score": 100.0, "direction": "negative", "score_inputs": score_inputs},
        ],
        "position_impacts": [{"option_symbol": DEMO_OPTION_SYMBOL, "risk_level": "high", "reason": "Long-call exposure conflicts with the demo article's negative demand scenario.", "risk_score": 100.0, "score_inputs": score_inputs}],
        "uncertainties": [
            "This is a seeded, fictional walkthrough rather than a live market assertion.",
            "Actual policy, guidance details, and company-specific offsets would require source review.",
        ],
        "no_trade_executed": True,
    }
    autopsy = {
        "as_of": now,
        "count": 1,
        "demo_data": True,
        "records": [{
            "id": 990001,
            "manager_decision_history_id": 990001,
            "symbol": "UBER",
            "company_name": "Uber Technologies",
            "decision_run_at": "2026-07-20T14:10:00+00:00",
            "created_at": "2026-07-20T14:10:00+00:00",
            "strategist": {
                "decision": "trade_candidate",
                "confidence": "high",
                "model": "gpt-5.6-terra demo fixture",
                "summary": "The pre-guidance demand narrative appeared constructive, but short-dated option timing was fragile.",
                "thesis": ["Bookings momentum was the core pre-trade assumption.", "A call required guidance to remain intact."],
                "risks": ["Guidance could reverse the demand narrative.", "Three DTE leaves little time to absorb a surprise."],
                "evidence_claims": [{"claim_type": "demand", "claim": "Bookings momentum supported the original thesis.", "evidence_article_ids": [DEMO_ARTICLE_ID]}],
            },
            "manager": {"decision": "call", "confidence": "high", "reason": "Demo proposal: evidence and timing initially aligned, subject to guidance risk."},
            "evidence": [{"article_id": DEMO_ARTICLE_ID, "title": article["title"], "source": article["source"], "published_at": article["published_at"], "article_scope": "company"}],
            "constraints": {"target_dte_bucket": "very_short", "selected_option_id": DEMO_OPTION_SYMBOL, "selected_option_symbol": DEMO_OPTION_SYMBOL, "selected_expiration_date": "2026-07-24", "selected_strike_price": 90.0, "selected_option_source": "demo_fixture"},
            "outcome": {"trade_executed": False, "order_id": "", "pnl_pct": None, "pnl_updated_at": "", "resolved_outcome_label": "demo_review_only"},
            "review": {"status": "adverse_outcome", "headline": "Demo review: weaker guidance invalidated the long-call premise.", "what_proved_us_wrong": "The demand-and-guidance assumption failed; short DTE magnified the timing risk."},
        }],
        "read_only": True,
        "no_trade_executed_by_feature": True,
    }
    return {"demo_mode": True, "disclaimer": "Seeded fictional walkthrough. No live news, position, order, or trade is created.", "exposure": {"articles": [article], "payload": exposure}, "autopsy": autopsy,
            "forward_evaluation": build_demo_forward_evaluation(now)}


def build_demo_forward_evaluation(now: str) -> dict[str, Any]:
    """A fictional interim incumbent/challenger comparison; no cohort or report exists behind it."""
    def strategy(net_pnl: float, trades: int, drawdown: float) -> dict[str, Any]:
        return {"observations": 48, "trades": trades, "cash": 48 - trades, "net_pnl": net_pnl, "capital_at_risk": trades * 210.0,
                "net_return": net_pnl / (trades * 210.0), "win_rate": 0.52, "worst_trade": -151.3, "max_drawdown": drawdown,
                "verified_daily_marks": True}

    criteria = [("paired_observations", False), ("independent_clusters", False), ("regimes", True), ("coverage", True),
                ("positive_paired_net_pnl", True), ("bootstrap_lower_bound", False), ("beats_every_baseline", False),
                ("drawdown", True), ("verified_daily_marks", True)]
    return {
        "as_of": now, "available": True, "demo_data": True, "window_days": 30,
        "settings": {"forward_evaluation": {"mode": "shadow"},
                     "challenger": {"mode": "shadow", "strategist_model": "", "manager_model": "demo-challenger-model",
                                    "daily_model_calls": 120}},
        "cohorts": [{"cohort_id": "cohort:demo", "base_pipeline_version": "v2:demo-fixture-manifest", "challenger_id": "models:demo",
                     "window_start": "2026-07-01T14:00:00+00:00", "window_end": "2026-08-30T14:00:00+00:00",
                     "acceptance_hash": "demo",
                     "runs": {"incumbent": {"run_id": "demo-incumbent", "status": "open", "enrolled": 51, "resolved": 48, "target_candidates": 10000},
                              "challenger": {"run_id": "demo-challenger", "status": "open", "enrolled": 50, "resolved": 48, "target_candidates": 10000}}}],
        "decision_observations": {"recorded": 53, "unavailable": 2},
        "paired_evaluations": [{"role": "challenger", "status": "abstained", "n": 19, "model_calls": 19},
                               {"role": "challenger", "status": "evaluated", "n": 31, "model_calls": 31},
                               {"role": "incumbent", "status": "evaluated", "n": 51, "model_calls": 0}],
        "mark_sessions": [{"session_date": "2026-07-24", "status": "complete", "summary": {"symbols": 22, "stored": 21, "missing": ["DEMO260821C00050000"]}}],
        "comparisons": [{
            "report_id": "comparison:demo-fixture-report", "cohort_id": "cohort:demo", "information_cutoff_time": "2026-07-24T22:00:00+00:00",
            "window": {"start": "2026-07-01T14:00:00+00:00", "end": "2026-08-30T14:00:00+00:00"},
            "challenger_definition": {"strategist_model": "", "manager_model": "demo-challenger-model", "challenger_id": "models:demo"},
            "verdict": "interim", "final": False,
            "observations": {"recorded": 53, "unavailable": 2,
                             "pair_statuses": {"resolved": 48, "challenger_outcome_pending": 3, "incumbent_outcome_missing_daily_marks": 2}},
            "n_paired": 48, "n_clusters": 17, "regimes": ["range", "trend_up"], "coverage": 0.87, "paired_difference": 412.6,
            "mean_paired_difference": 8.6, "bootstrap": {"lower_bound": -3.1, "upper_bound": 19.8, "confidence": 0.9},
            "incumbent": strategy(-96.4, 29, 388.0), "challenger": strategy(316.2, 21, 251.0),
            "criteria": [{"criterion": name, "passed": passed, "value": None, "threshold": None} for name, passed in criteria],
            "research_cost": {"challenger_model_calls": 50, "challenger_latency_seconds_median": 11.2}}],
        "discovery": {"events_seen": 64, "admitted_union": 27,
                      "route_status_counts": {"production": {"admitted": 19, "not_admitted": 41, "not_evaluated": 4},
                                              "discovery": {"admitted": 12, "not_admitted": 9, "not_evaluated": 43}},
                      "admission_latency_seconds_median": {"production": 30600.0, "discovery": 7200.0},
                      "outcomes_by_admission": {"both": {"5d": {"events": 4, "resolved": 3, "mean_signed_residual_return": 0.011, "hit_rate": 0.67, "mean_absolute_residual_return": 0.024}},
                                                "discovery_only": {"5d": {"events": 8, "resolved": 5, "mean_signed_residual_return": 0.006, "hit_rate": 0.6, "mean_absolute_residual_return": 0.019}},
                                                "production_only": {"5d": {"events": 15, "resolved": 9, "mean_signed_residual_return": -0.002, "hit_rate": 0.44, "mean_absolute_residual_return": 0.015}}}},
        "research_route": {"theses": 9, "outcomes_by_check_status": {"validated": {"theses": 5, "resolved": 3, "mean_signed_residual_return": 0.009, "hit_rate": 0.67},
                                                                     "deferred": {"theses": 4, "resolved": 2, "mean_signed_residual_return": -0.004, "hit_rate": 0.5}},
                           "research_cost": {"reserved_cost_usd": 1.84, "model_and_retrieval_calls": 61},
                           "latency_seconds": {"session_to_thesis_median": 5400.0, "event_to_thesis_median": 19800.0}},
    }
