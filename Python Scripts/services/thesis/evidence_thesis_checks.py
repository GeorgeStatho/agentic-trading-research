"""Deterministic claim-support and coverage checks for a normalized evidence thesis.

Checks read the frozen evidence package from immutable storage and the article text that
the model actually saw. They verify references, quotes, numbers, fiscal periods and
required coverage. They cannot judge whether an inference is sound; that is the optional
semantic review's job, and its output is only a review signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from market_db_modules.company_evidence import get_company_fact, get_evidence_document, get_evidence_package
from services.discovery.analyst_consensus import consensus_matches_actual
from services.discovery.company_evidence_extraction import comparison_key
from services.thesis.evidence_thesis_numbers import NumberSupport, incompatible_calculation_facts, stated_numbers


ARTICLE_PREFIX = "article:"
MIN_QUOTE_CHARACTERS = 8
# Assertions of a result against market expectations, not mere mentions: "surprise is unknown"
# and "no consensus is available" are correct statements and must not be flagged.
_MARKET_SURPRISE = re.compile(
    r"\b(?:beat|beats|missed|misses|topped|tops|exceeded|exceeds|surpassed|fell short of)\b[^.;]{0,40}?"
    r"\b(?:estimates?|consensus|expectations|forecasts?)\b"
    r"|\b(?:positive|negative|upside|downside|earnings|revenue) surprises?\b", re.IGNORECASE)
_CONTENT_CHECKED_TYPES = {"observed_fact", "calculation"}
# Filings and issuers' own releases. Yahoo consensus and the companyfacts JSON are package documents too, but not primary.
PRIMARY_DOCUMENT_TYPES = frozenset({"regulatory_filing", "issuer_release"})


@dataclass
class ThesisSources:
    """Everything a claim may cite, resolved from the frozen payload and immutable records."""
    company_id: int | None
    package_id: str | None = None
    facts: dict = field(default_factory=dict)
    documents: dict = field(default_factory=dict)
    articles: dict = field(default_factory=dict)
    package_claims: list = field(default_factory=list)
    visible_opposing_claims: list = field(default_factory=list)

    def article_text(self, article_id):
        return " ".join(self.articles.get(article_id, {}).values())


def _visible_articles(payload):
    supporting = payload.get("supporting_articles") if isinstance(payload.get("supporting_articles"), dict) else {}
    allowed = {value for value in (payload.get("grounding") or {}).get("article_ids", [])
               if type(value) is int and value > 0}
    articles = {}
    for section in ("article_summaries", "full_articles"):
        for article in supporting.get(section) or []:
            identity = article.get("article_id") if isinstance(article, dict) else None
            if identity not in allowed:
                continue
            fields = articles.setdefault(identity, {})
            for name in ("title", "summary", "body"):
                if isinstance(article.get(name), str) and article[name].strip():
                    fields[name] = article[name]
    # Views show the model articles it may cite but that are not in supporting_articles. Their title and
    # summary are the scraped article's own text; the reason is classifier-written, so it is never a source.
    views = payload.get("views") if isinstance(payload.get("views"), dict) else {}
    for view in views.values():
        for signal in (view.get("agent_conclusions") or []) if isinstance(view, dict) else []:
            try:
                identity = int(signal.get("article_id")) if isinstance(signal, dict) else None
            except (TypeError, ValueError):
                continue
            if identity not in allowed:
                continue
            fields = articles.setdefault(identity, {})
            for name in ("title", "summary"):
                if name not in fields and isinstance(signal.get(name), str) and signal[name].strip():
                    fields[name] = signal[name]
    # A grounded ID without visible text can be cited, but it can never support a quote or number.
    return {identity: articles.get(identity, {}) for identity in allowed}


def build_thesis_sources(payload, *, db_path):
    company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
    evidence = payload.get("company_evidence") if isinstance(payload.get("company_evidence"), dict) else {}
    sources = ThesisSources(company_id=company.get("company_id"), articles=_visible_articles(payload))
    package_id = evidence.get("package_id")
    if not package_id:
        return sources
    package = get_evidence_package(package_id, db_path=db_path)
    if package["company_id"] != sources.company_id:
        raise ValueError("Evidence package belongs to another company")
    sources.package_id = package_id
    sources.facts = {identity: get_company_fact(identity, db_path=db_path) for identity in package["fact_ids"]}
    sources.documents = {identity: get_evidence_document(identity, db_path=db_path) for identity in package["document_ids"]}
    sources.package_claims = list(package["supporting_claims"]) + list(package["opposing_claims"])
    sources.visible_opposing_claims = [claim for claim in evidence.get("opposing_claims") or [] if claim.get("fact_ids")]
    return sources


# Quote marks plus the invisible zero-width and soft-hyphen characters some wire articles carry.
_QUOTE_MARKS = "\"'“”‘’​‌‍⁠﻿­"


def _quote_pattern(text):
    # Models re-quote (" to ') and re-space text when copying it into JSON, so match on every other character.
    characters = [ch for ch in text.rstrip(".,;:" + _QUOTE_MARKS) if ch not in _QUOTE_MARKS and not ch.isspace()]
    if not characters:
        return re.compile(r"(?!)")
    return re.compile(f"[\\s{_QUOTE_MARKS}]*".join(map(re.escape, characters)))


def _locate_quote(quote, claim, sources):
    """Return (span, failure). Spans use the stored document's own character offsets."""
    source_id, text = quote["source_id"], quote["text"]
    if len(text) < MIN_QUOTE_CHARACTERS:
        return None, "quote_too_short"
    pattern = _quote_pattern(text)
    if source_id.startswith(ARTICLE_PREFIX):
        suffix = source_id[len(ARTICLE_PREFIX):]
        article_id = int(suffix) if suffix.isdigit() else None
        if article_id not in claim["article_ids"]:
            return None, "quote_source_not_cited"
        for name, value in sources.articles.get(article_id, {}).items():
            match = pattern.search(value)
            if match:
                return {"source_id": source_id, "field": name, "start": match.start(), "end": match.end()}, None
        return None, "quote_not_in_source"
    if source_id in claim["fact_ids"] and source_id in sources.facts:
        fact = sources.facts[source_id]
        document = sources.documents.get(fact["document_id"])
        if document is None:
            return None, "quote_not_in_source"
        match = pattern.search(document["text"], fact["source_start"], fact["source_end"])
    elif source_id in claim["document_ids"] and source_id in sources.documents:
        document = sources.documents[source_id]
        match = pattern.search(document["text"])
    else:
        return None, "quote_source_not_cited"
    if match is None:
        return None, "quote_not_in_source"
    return {"source_id": source_id, "document_id": document["document_id"],
            "start": match.start(), "end": match.end()}, None


def _fact_excerpt(fact, sources):
    document = sources.documents.get(fact["document_id"])
    return document["text"][fact["source_start"]:fact["source_end"]] if document else ""


def _surprise_claimed_without_consensus(statement, cited_facts, supporting_texts):
    """An asserted surprise needs a cited consensus fact paired with a cited actual of the same metric
    and fiscal period (or the quarter a next_report estimate was for), or a cited source asserting it too."""
    if not _MARKET_SURPRISE.search(statement):
        return False
    actuals = {(fact["metric"], fact["fiscal_period"]) for fact in cited_facts if fact["kind"] == "actual"}
    if any(fact["kind"] == "consensus" and (fact["metric"], fact["fiscal_period"]) in actuals for fact in cited_facts):
        return False
    if any(consensus_matches_actual(first, second) for first in cited_facts for second in cited_facts):
        return False
    return not any(_MARKET_SURPRISE.search(text) for text in supporting_texts)


def check_claim(claim, section, sources):
    failures, spans, quote_texts = [], [], []
    unknown =([identity for identity in claim["fact_ids"] if identity not in sources.facts]
               + [identity for identity in claim["document_ids"] if identity not in sources.documents]
               + [ARTICLE_PREFIX + str(identity) for identity in claim["article_ids"] if identity not in sources.articles])
    if unknown:
        failures.append("unknown_source_reference")
    if not (claim["fact_ids"] or claim["document_ids"] or claim["article_ids"]):
        failures.append("no_citation")
    for quote in claim["quotes"]:
        span, failure = _locate_quote(quote, claim, sources)
        if failure:
            failures.append(failure)
        else:
            spans.append(span)
            # Only verified quotes may support numbers or assertions.
            quote_texts.append(quote["text"])
    facts = [sources.facts[identity] for identity in claim["fact_ids"] if identity in sources.facts]
    cited_texts = ([_fact_excerpt(fact, sources) for fact in facts] + quote_texts
                   + [sources.article_text(identity) for identity in claim["article_ids"]])
    if claim["claim_type"] == "observed_fact" and not (facts or spans):
        failures.append("observed_fact_without_source_span")
    if claim["claim_type"] == "calculation":
        if not facts:
            failures.append("calculation_without_cited_facts")
        if incompatible_calculation_facts(facts):
            failures.append("incompatible_calculation_inputs")
    if claim["fiscal_period"]:
        if not facts:
            failures.append("fiscal_period_unverifiable")
        elif claim["fiscal_period"] not in {fact["fiscal_period"] for fact in facts}:
            failures.append("fiscal_period_mismatch")
    numbers = stated_numbers(claim["statement"])
    unverified_numbers = []
    if claim["claim_type"] in _CONTENT_CHECKED_TYPES:
        support = NumberSupport(facts, sources.package_claims,
                                cited_texts + [fact["fiscal_period"] for fact in facts])
        failures.extend("unsupported_number:" + number.text for number in numbers if not support.supports(number))
    else:
        unverified_numbers = [number.text for number in numbers]
    if _surprise_claimed_without_consensus(claim["statement"], facts, quote_texts + [
            sources.article_text(identity) for identity in claim["article_ids"]]):
        failures.append("market_surprise_without_consensus")
    return {"claim_id": claim["claim_id"], "section": section, "claim_type": claim["claim_type"],
            "central": claim["central"], "status": "failed" if failures else "verified",
            "content_check": "numbers_and_spans" if claim["claim_type"] in _CONTENT_CHECKED_TYPES else "references_only",
            "failures": sorted(set(failures)), "unknown_references": unknown, "spans": spans,
            "unverified_numbers": unverified_numbers}


def _baseline_cited(baseline_type, facts):
    """A baseline only counts against an exactly comparable observation.

    Guidance and consensus baselines need the identical metric, fiscal period, period type,
    basis, unit, currency and scale; a prior actual needs all of those except the period.
    """
    def pairs(baselines, observations, same_period):
        for baseline in baselines:
            for observation in observations:
                if baseline["fact_id"] == observation["fact_id"]:
                    continue
                if same_period and comparison_key(baseline) == comparison_key(observation):
                    return True
                if (not same_period and baseline["fiscal_period"] != observation["fiscal_period"]
                        and comparison_key(baseline)[2:] == comparison_key(observation)[2:]
                        and baseline["metric"] == observation["metric"]):
                    return True
        return False

    by_kind = lambda *kinds: [fact for fact in facts if fact["kind"] in kinds and fact.get("value") is not None]
    if baseline_type == "prior_guidance":
        return pairs(by_kind("prior_guidance", "guidance"), by_kind("guidance"), True)
    if baseline_type == "consensus":
        return (pairs(by_kind("consensus"), by_kind("actual", "guidance"), True)
                or any(consensus_matches_actual(estimate, reported)
                       for estimate in by_kind("consensus") for reported in by_kind("actual")))
    if baseline_type == "prior_actual":
        return pairs(by_kind("actual"), by_kind("actual"), False)
    return False


def _expectation_failures(thesis, claims_by_id, results_by_id, sources):
    change = thesis["change_vs_prior_expectations"]
    unknown = change["assessment"] == "unknown"
    if unknown != (change["baseline_type"] == "none"):
        return ["expectation_baseline_inconsistent"]
    failures = []
    if _MARKET_SURPRISE.search(change["statement"]) and change["baseline_type"] != "consensus":
        failures.append("market_surprise_without_consensus")
    if unknown:
        return failures
    referenced = [identity for identity in change["claim_ids"] if identity in claims_by_id]
    if not change["claim_ids"] or len(referenced) != len(change["claim_ids"]):
        return failures + ["expectation_claim_unknown"]
    if any(results_by_id[identity]["status"] != "verified"
           or claims_by_id[identity]["claim_type"] not in _CONTENT_CHECKED_TYPES for identity in referenced):
        failures.append("expectation_claim_unverified")
    cited = [sources.facts[fact_id] for identity in referenced for fact_id in claims_by_id[identity]["fact_ids"]
             if fact_id in sources.facts]
    if not _baseline_cited(change["baseline_type"], cited):
        failures.append("expectation_baseline_not_cited")
    return failures


def evidence_basis(thesis, results_by_id, sources):
    """Recorded with the check, never shown to the manager or the gate: does a verified central claim rest on a
    package fact, or on a quote from a primary document such as an earnings release, and were filing facts
    (anything beyond Yahoo consensus) there to cite."""
    central = [claim for claim in thesis["supporting_claims"] if claim["central"]
               and claim["claim_type"] in _CONTENT_CHECKED_TYPES and results_by_id[claim["claim_id"]]["status"] == "verified"]
    fact_backed = any(claim["fact_ids"] for claim in central)
    document_backed = any(span["source_id"] in claim["document_ids"]
                          and (sources.documents.get(span["source_id"]) or {}).get("document_type") in PRIMARY_DOCUMENT_TYPES
                          for claim in central for span in results_by_id[claim["claim_id"]]["spans"])
    if fact_backed:
        basis = "facts"
    elif document_backed:
        basis = "primary_document"
    else:
        basis = "articles_only"
    available = any(fact["kind"] != "consensus" for fact in sources.facts.values())
    if basis != "articles_only":
        requirement = "met"
    elif available:
        requirement = "not_met"
    else:
        requirement = "not_applicable"
    return {"basis": basis, "filing_facts_available": available, "fact_requirement": requirement}


def check_thesis(thesis, sources, *, max_horizon_days):
    """Return claim results and thesis-level failures. Any thesis-level failure defers."""
    sections = ("supporting_claims", "opposing_claims")
    claims_by_id = {claim["claim_id"]: claim for section in sections for claim in thesis[section]}
    results = [check_claim(claim, section, sources) for section in sections for claim in thesis[section]]
    results_by_id = {result["claim_id"]: result for result in results}
    failures = []
    horizon = thesis["horizon"]
    if not 1 <= horizon["minimum_days"] <= horizon["maximum_days"] <= max_horizon_days:
        failures.append("invalid_horizon")
    if any(not 1 <= condition["review_within_days"] <= max_horizon_days
           for condition in thesis["invalidation_conditions"]):
        failures.append("invalid_invalidation_review_timing")
    if thesis["direction"] != "unclear":
        if not any(claim["central"] for claim in thesis["supporting_claims"]):
            failures.append("no_central_supporting_claim")
        if not thesis["opposing_claims"]:
            failures.append("no_opposing_claims")
        if not thesis["invalidation_conditions"]:
            failures.append("missing_invalidation_condition")
    if any(result["central"] and result["status"] == "failed" for result in results):
        failures.append("central_claim_failed")
    failures.extend(_expectation_failures(thesis, claims_by_id, results_by_id, sources))
    cited_facts = {fact_id for claim in claims_by_id.values() for fact_id in claim["fact_ids"]}
    unaddressed = [claim for claim in sources.visible_opposing_claims if not cited_facts & set(claim["fact_ids"])]
    if unaddressed:
        failures.append("package_opposing_evidence_unaddressed")
    return {"claims": results, "failures": sorted(set(failures)),
            "unaddressed_package_opposing_claims": unaddressed,
            "evidence_basis": evidence_basis(thesis, results_by_id, sources)}
