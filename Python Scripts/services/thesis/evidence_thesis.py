"""Evaluate, persist and gate structured strategist theses.

Deterministic checks decide whether a thesis is deferred. The optional semantic reviewer
is recorded as a review signal only and never changes that decision.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from agent_stages.evidence_thesis_contract import THESIS_SCHEMA_VERSION, ThesisShapeError, normalize_evidence_thesis
from services.thesis.evidence_thesis_checks import build_thesis_sources, check_thesis
from services.thesis.evidence_thesis_policy import EvidenceThesisPolicy
from market_db_modules.evidence_thesis import record_evidence_thesis


LOGGER = logging.getLogger(__name__)
LEGACY_RESEARCH_STATUS = "not_investigated"
ACCEPTED_RESEARCH_STATUSES = {"ready", LEGACY_RESEARCH_STATUS}
SEMANTIC_VERDICTS = {"consistent", "inconsistent", "unclear"}
DIRECTION_BY_DECISION = {"call": "up", "put": "down"}


def _semantic_review(reviewer, thesis, claims):
    if reviewer is None:
        return {"status": "not_configured", "authority": "review_signal_only"}
    try:
        verdicts = reviewer.review(thesis, claims)
        known = {claim["claim_id"] for claim in claims}
        if not isinstance(verdicts, list) or any(
                not isinstance(item, dict) or item.get("claim_id") not in known
                or item.get("verdict") not in SEMANTIC_VERDICTS for item in verdicts):
            raise ValueError("Semantic reviewer returned an invalid verdict list")
    except Exception as exc:
        return {"status": "failed", "authority": "review_signal_only", "error_type": type(exc).__name__}
    return {"status": "completed", "authority": "review_signal_only",
            "reviewer": getattr(reviewer, "identity", type(reviewer).__name__),
            "verdicts": [{"claim_id": item["claim_id"], "verdict": item["verdict"],
                          "reason": str(item.get("reason") or "")[:600]} for item in verdicts],
            "requires_human_review": any(item["verdict"] == "inconsistent" for item in verdicts)}


def _status(failures, claims):
    if failures:
        return "deferred"
    return "validated_with_warnings" if any(claim["status"] == "failed" for claim in claims) else "validated"


def check_evidence_thesis(raw_thesis, payload, *, route, research_status=LEGACY_RESEARCH_STATUS,
                          policy=None, reviewer=None, db_path=None):
    """Return the complete, persistable check record for one model-authored thesis."""
    policy = policy or EvidenceThesisPolicy.from_env()
    package_id = (payload.get("company_evidence") or {}).get("package_id")
    record = {"schema_version": THESIS_SCHEMA_VERSION, "policy_version": policy.policy_version,
              "route": route, "package_id": package_id, "research_status": research_status,
              "raw_thesis": raw_thesis, "thesis": None, "claims": [], "failures": [],
              "unaddressed_package_opposing_claims": [],
              "semantic_review": {"status": "not_run", "authority": "review_signal_only"}}
    try:
        thesis = normalize_evidence_thesis(raw_thesis)
    except ThesisShapeError as exc:
        return {**record, "status": "invalid_output", "failures": ["invalid_thesis_shape"], "shape_error": str(exc)}
    thesis = {**thesis, "schema_version": THESIS_SCHEMA_VERSION, "evidence_package_id": package_id,
              "research_status": research_status}
    try:
        sources = build_thesis_sources(payload, db_path=db_path)
        checked = check_thesis(thesis, sources, max_horizon_days=policy.max_horizon_days)
    except Exception as exc:
        LOGGER.exception("Evidence thesis checks failed")
        return {**record, "thesis": thesis, "status": "check_error", "failures": ["thesis_check_error"],
                "error_type": type(exc).__name__}
    claims = checked["claims"]
    return {**record, **checked, "thesis": thesis, "status": _status(checked["failures"], claims),
            "evidence_support": {
                "claims": len(claims), "verified_claims": sum(c["status"] == "verified" for c in claims),
                "central_claims": sum(c["central"] for c in claims),
                "failed_central_claims": sum(c["central"] and c["status"] == "failed" for c in claims),
                "reference_only_claims": sum(c["content_check"] == "references_only" for c in claims)},
            "semantic_review": _semantic_review(reviewer, thesis, claims)}


def not_requested_check(payload, reason, *, route, research_status=LEGACY_RESEARCH_STATUS, policy=None):
    policy = policy or EvidenceThesisPolicy.from_env()
    return {"schema_version": THESIS_SCHEMA_VERSION, "policy_version": policy.policy_version, "route": route,
            "package_id": (payload.get("company_evidence") or {}).get("package_id"),
            "research_status": research_status, "raw_thesis": None, "thesis": None, "claims": [],
            "status": "not_requested", "failures": [reason],
            "semantic_review": {"status": "not_run", "authority": "review_signal_only"}}


def compact_thesis_check(check, evaluation_id):
    """The summary carried with the recommendation; the full record stays in storage."""
    return {"evaluation_id": evaluation_id, "schema_version": check["schema_version"],
            "policy_version": check["policy_version"], "status": check["status"],
            "package_id": check["package_id"], "research_status": check["research_status"],
            "failures": check["failures"],
            "failed_claims": [{"claim_id": claim["claim_id"], "central": claim["central"], "failures": claim["failures"]}
                              for claim in check["claims"] if claim["status"] == "failed"],
            "semantic_review_status": check["semantic_review"]["status"]}


def persist_thesis_check(check, *, company_id, candidate_id=None, research_session_id=None,
                         context=None, db_path=None):
    """Persist the check; return its ID or None. A missing ID later blocks an enforced gate."""
    try:
        return record_evidence_thesis(
            {**check, "company_id": company_id, "candidate_id": candidate_id,
             "research_session_id": research_session_id,
             "context": context or {}, "created_at": datetime.now(timezone.utc).isoformat()},
            db_path=db_path)
    except Exception:
        LOGGER.exception("Failed to persist evidence thesis check")
        return None


def evaluate_evidence_thesis_gate(strategist_recommendation, decision, *, policy=None):
    """Auditable entry eligibility from the thesis the manager actually received.

    Returns None when theses are off, so legacy results keep their existing eligibility.
    """
    policy = policy or EvidenceThesisPolicy.from_env()
    if policy.mode == "off":
        return None
    recommendation = strategist_recommendation if isinstance(strategist_recommendation, dict) else {}
    check = recommendation.get("evidence_thesis_check") if isinstance(recommendation.get("evidence_thesis_check"), dict) else {}
    thesis = recommendation.get("evidence_thesis") if isinstance(recommendation.get("evidence_thesis"), dict) else {}
    decision = str(decision or "").strip().lower()
    reasons = []
    if decision not in DIRECTION_BY_DECISION:
        reasons.append("manager_not_directional")
    if recommendation.get("evidence_thesis_handoff") == "package_mismatch":
        reasons.append("thesis_package_mismatch")
    if not check:
        reasons.append("thesis_missing")
    else:
        if check.get("status") not in {"validated", "validated_with_warnings"}:
            reasons.append("thesis_" + str(check.get("status") or "unknown"))
        if not check.get("evaluation_id"):
            reasons.append("thesis_check_not_persisted")
        if check.get("research_status") not in ACCEPTED_RESEARCH_STATUSES:
            reasons.append("research_not_ready")
    if thesis and decision in DIRECTION_BY_DECISION and thesis.get("direction") != DIRECTION_BY_DECISION[decision]:
        reasons.append("thesis_direction_conflicts_with_decision")
    would_allow = not reasons
    allowed = would_allow or (policy.mode == "shadow" and decision in DIRECTION_BY_DECISION)
    return {"allowed": allowed, "would_allow": would_allow, "proposed_decision": decision or "neither",
            "eligible_decision": decision if allowed else "neither", "reason_codes": reasons,
            "evaluation_id": check.get("evaluation_id"), "thesis_status": check.get("status"),
            "policy_version": policy.policy_version, "enforcement_mode": policy.mode}
