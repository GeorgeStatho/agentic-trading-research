"""Shadow evidence theses for finished research sessions. This module has no execution dependency.

One thesis per research session, over the session's final package and frozen articles. A
reserved call without a recorded check is never retried: its outcome is unknown.
"""
from datetime import datetime, timezone

from market_db_modules.company_evidence import get_evidence_package
from market_db_modules.evidence_thesis import (
    charge_research_thesis_usage, pending_research_theses, research_thesis_reservation, reserve_research_thesis,
)
from market_db_modules.research_foundation import get_research_session
from market_db_modules.research_runtime import ResearchBudgetExceeded, load_research_progress, research_event_superseded
from services.evidence_research_policy import EvidenceResearchPolicy
from services.evidence_thesis import check_evidence_thesis, not_requested_check, persist_thesis_check
from services.evidence_thesis_policy import EvidenceThesisPolicy
from services.research_lock import ResearchSessionBusy, research_session_lock
from services.research_model_policy import billed
from agent_stages.research_thesis_writer import ResearchThesisWriter


ROUTE = "research_shadow"
THESIS_RESEARCH_STATUSES = {"ready", "conflicting_evidence"}
MAX_ARTICLES = 10
MAX_ARTICLE_CHARACTERS = 3000


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def thesis_payload(session, progress, *, db_path):
    """The exact, bounded context the writer sees and the checks validate against."""
    from agent_builders.company_evidence_payload import build_company_evidence_payload
    research_input = progress["initial_input"]["research_input"]
    company = research_input["company"]
    package = get_evidence_package(session["final_package_id"], db_path=db_path)
    articles = [{"article_id": article["article_id"], "title": article.get("title") or "",
                 "body": (article.get("body") or "")[:MAX_ARTICLE_CHARACTERS],
                 "published_at": article.get("published_at")}
                for article in research_input.get("evidence", [])[:MAX_ARTICLES] if type(article.get("article_id")) is int]
    return {
        "company": company,
        "company_evidence": build_company_evidence_payload(
            company["company_id"], package_id=package["package_id"], as_of=package["information_cutoff_time"],
            db_path=db_path),
        "supporting_articles": {"article_summaries": [], "full_articles": articles},
        "grounding": {"article_ids": sorted({article["article_id"] for article in articles})},
        "research": {"session_id": session["session_id"], "status": session["status"],
                     "stop_reason": session["stop_reason"], "unresolved_questions": progress["unresolved"],
                     "gaps": package["gaps"]},
    }


class ResearchThesisRun:
    def __init__(self, session_id, *, policy, research_policy, writer_factory, clock, db_path):
        self.session_id, self.policy, self.research_policy = session_id, policy, research_policy
        self.writer_factory, self.clock, self.db_path = writer_factory, clock, db_path
        self.session = get_research_session(session_id, db_path=db_path)
        self.progress = load_research_progress(session_id, db_path=db_path)
        self.payload = thesis_payload(self.session, self.progress, db_path=db_path)

    def record(self, check, **context):
        candidate = self.progress["initial_input"]
        evaluation_id = persist_thesis_check(
            check, company_id=self.payload["company"]["company_id"], candidate_id=candidate.get("candidate_id"),
            research_session_id=self.session_id, context={"model_input": self.payload, **context},
            db_path=self.db_path)
        return {"session_id": self.session_id, "evaluation_id": evaluation_id, "status": check["status"],
                "failures": check["failures"], "shadow": True}

    def skipped(self, status, reason):
        check = not_requested_check(self.payload, reason, route=ROUTE, research_status=self.session["status"],
                                    policy=self.policy)
        return self.record({**check, "status": status})

    def run(self):
        if research_thesis_reservation(self.session_id, db_path=self.db_path):
            return self.skipped("failed", "interrupted_call_outcome_unknown_no_automatic_retry")
        if self.session["status"] not in THESIS_RESEARCH_STATUSES:
            return self.skipped("not_requested", "research_status:" + self.session["status"])
        if research_event_superseded(self.session["queue_id"], session_id=self.session_id, db_path=self.db_path):
            return self.skipped("not_requested", "superseded_before_thesis")
        writer = self.writer_factory()
        reservation = writer.reservation(self.payload)
        day = reserve_research_thesis(self.session_id, reservation, at=self.clock(),
                                      daily_calls=self.policy.daily_research_thesis_calls,
                                      daily_cost_budget=self.research_policy.daily_cost_budget, db_path=self.db_path)
        call = {"model": writer.model, "prompt_version": writer.prompt_version,
                "writer_identity": getattr(writer, "identity", None), "reservation": reservation}
        try:
            raw = writer.write(self.payload, timeout=self.research_policy.max_seconds)
        except TimeoutError:
            return self.skipped_after_call("deadline_exhausted", writer, reservation, day, call)
        except Exception as exc:
            reason = "provider_failure" if type(exc).__name__ == "ResearchProviderError" else "invalid_model_response"
            return self.skipped_after_call(reason, writer, reservation, day,
                                           {**call, "diagnostics": getattr(exc, "diagnostics", {"error_type": type(exc).__name__})})
        usage = self.charge_usage(writer, reservation, day)
        raw_thesis = raw.get("evidence_thesis") if isinstance(raw, dict) else None
        check = check_evidence_thesis(raw_thesis, self.payload, route=ROUTE, research_status=self.session["status"],
                                      policy=self.policy, db_path=self.db_path)
        return self.record(check, **call, **usage, raw_response=raw)

    def skipped_after_call(self, reason, writer, reservation, day, call):
        usage = self.charge_usage(writer, reservation, day)
        check = not_requested_check(self.payload, reason, route=ROUTE, research_status=self.session["status"],
                                    policy=self.policy)
        # Call diagnostics describe the failure; they take precedence over transport metadata.
        return self.record({**check, "status": "failed"}, **{**usage, **call})

    def charge_usage(self, writer, reservation, day):
        response = getattr(writer, "last_response", {}) or {}
        usage = response.get("usage", {})
        estimated = writer.policy.usage_cost(usage) if billed(reservation) else 0
        extra = max(0, (estimated or 0) - reservation["cost_reserved_usd"])
        if extra:
            charge_research_thesis_usage(day, extra, db_path=self.db_path)
        return {"usage": usage, "diagnostics": response.get("diagnostics", {}),
                "reported_cost_estimate_usd": estimated,
                "cost_status": "estimated_from_usage" if estimated is not None else "unknown"}


def process_research_theses(*, policy=None, research_policy=None, writer=None, clock=utc_now, limit=6, db_path=None):
    policy = policy or EvidenceThesisPolicy.from_env()
    research_policy = research_policy or EvidenceResearchPolicy.from_env()
    if policy.mode == "off" or research_policy.mode == "disabled":
        return {"status": "disabled", "theses": []}
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Research thesis batch limit must be between 1 and 100")

    def writer_factory():
        if writer is not None:
            return writer
        return ResearchThesisWriter()

    results, failures, status = [], [], "shadow"
    for session_id in pending_research_theses(limit=limit, db_path=db_path):
        try:
            with research_session_lock(db_path, "thesis:" + session_id):
                results.append(ResearchThesisRun(session_id, policy=policy, research_policy=research_policy,
                                                 writer_factory=writer_factory, clock=clock, db_path=db_path).run())
        except ResearchSessionBusy:
            continue
        except ResearchBudgetExceeded as exc:
            status = "budget_exhausted:" + str(exc)
            break
        except Exception as exc:
            failures.append({"session_id": session_id, "reason": type(exc).__name__})
    return {"status": status, "theses": results, "failures": failures}
