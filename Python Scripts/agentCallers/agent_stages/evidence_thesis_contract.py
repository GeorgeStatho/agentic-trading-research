"""Versioned structured thesis: model-facing schema, instructions and strict normalization.

The model writes the thesis. Python owns ``schema_version``, ``evidence_package_id`` and
``research_status``; they are added after normalization and never accepted from the model.
"""
from __future__ import annotations

from typing import Any


THESIS_SCHEMA_VERSION = "evidence-thesis-v1"
CLAIM_TYPES = ("observed_fact", "calculation", "inference")
DIRECTIONS = ("up", "down", "unclear")
UNCERTAINTY_LEVELS = ("low", "medium", "high")
BASELINE_TYPES = ("prior_guidance", "consensus", "prior_actual", "none")
ASSESSMENTS = ("supported_comparison", "unknown")
DATA_SOURCES = ("company_filing", "company_release", "price", "options_market", "news", "consensus", "other")
MAX_CLAIMS_PER_SECTION = 8
MAX_TEXT_CHARACTERS = 600

_TEXT = {"type": "string", "maxLength": MAX_TEXT_CHARACTERS}
_QUOTE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["source_id", "text"],
    "properties": {"source_id": {"type": "string"}, "text": _TEXT},
}
CLAIM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["claim_id", "statement", "claim_type", "central", "fiscal_period",
                 "fact_ids", "document_ids", "article_ids", "quotes"],
    "properties": {
        "claim_id": {"type": "string"},
        "statement": _TEXT,
        "claim_type": {"type": "string", "enum": list(CLAIM_TYPES)},
        "central": {"type": "boolean"},
        "fiscal_period": {"type": "string"},
        "fact_ids": {"type": "array", "items": {"type": "string"}},
        "document_ids": {"type": "array", "items": {"type": "string"}},
        "article_ids": {"type": "array", "items": {"type": "integer"}},
        "quotes": {"type": "array", "items": _QUOTE_SCHEMA},
    },
}
INVALIDATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["trigger", "data_source", "review_within_days"],
    "properties": {"trigger": _TEXT, "data_source": {"type": "string", "enum": list(DATA_SOURCES)},
                   "review_within_days": {"type": "integer"}},
}
EVIDENCE_THESIS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["causal_mechanism", "change_vs_prior_expectations", "direction", "horizon",
                 "forecast_uncertainty", "supporting_claims", "opposing_claims",
                 "invalidation_conditions", "unresolved_questions"],
    "properties": {
        "causal_mechanism": _TEXT,
        "change_vs_prior_expectations": {
            "type": "object", "additionalProperties": False,
            "required": ["assessment", "baseline_type", "statement", "claim_ids"],
            "properties": {"assessment": {"type": "string", "enum": list(ASSESSMENTS)},
                           "baseline_type": {"type": "string", "enum": list(BASELINE_TYPES)},
                           "statement": _TEXT,
                           "claim_ids": {"type": "array", "items": {"type": "string"}}},
        },
        "direction": {"type": "string", "enum": list(DIRECTIONS)},
        "horizon": {
            "type": "object", "additionalProperties": False, "required": ["minimum_days", "maximum_days"],
            "properties": {"minimum_days": {"type": "integer"}, "maximum_days": {"type": "integer"}},
        },
        "forecast_uncertainty": {"type": "string", "enum": list(UNCERTAINTY_LEVELS)},
        # No maxItems: Vertex response_schema rejects this schema with 400 INVALID_ARGUMENT when it is
        # present. The limit is stated in THESIS_INSTRUCTIONS and enforced by _list on normalization.
        "supporting_claims": {"type": "array", "items": CLAIM_SCHEMA},
        "opposing_claims": {"type": "array", "items": CLAIM_SCHEMA},
        "invalidation_conditions": {"type": "array", "items": INVALIDATION_SCHEMA},
        "unresolved_questions": {"type": "array", "items": _TEXT},
    },
}

THESIS_INSTRUCTIONS = (
    "Also write evidence_thesis, a falsifiable structured thesis. "
    "causal_mechanism explains how the observed change affects this business. "
    "Every supporting or opposing claim cites only IDs present in the input: company_evidence fact_id values in fact_ids, "
    "company_evidence document_id values in document_ids, and grounding article_ids in article_ids. "
    "claim_type is observed_fact for something a source states, calculation for arithmetic over cited facts, "
    "and inference for your reasoning. A figure one fact reports directly (a margin, revenue, EPS) is an observed_fact, "
    "not a calculation. A calculation claim states one computed figure and cites only the facts it is computed from, "
    "all with the same unit, currency, scale, period type and fiscal period, except a change or growth figure for one "
    "metric across periods (such as year-on-year), which is what a prior_actual baseline cites; put every further "
    "figure in its own claim. "
    "Mark central=true only for claims the thesis cannot survive without. "
    "quotes copy exact text, at least a full phrase, from a cited article or document; source_id is the document_id "
    "or article:<article_id> of the article the text appears in. A company_evidence document's release_excerpt is the "
    "opening of its earnings release: quote it with that document's document_id as source_id and in document_ids.Copy quotes character for character: escape inner "
    "double quotes as \\\" and never swap them for single quotes or drop them. A fact is verified through fact_ids alone, so a claim citing only facts has quotes []. "
    "Never quote JSON, field names or bare values. "
    "An observed_fact citing an article quotes that article's title, summary or body text that states it; when no such "
    "text states it, make it an inference. agent_conclusions reason and mechanism are another model's reading, not "
    "source text: use them only in inference claims, never as an observed_fact or a quote. "
    "Every number in an observed_fact or calculation statement must match a cited fact, a calculation over cited compatible facts, "
    "or text in a cited quote or article. Set fiscal_period to the cited fact's exact fiscal_period; "
    "a claim with no fact_ids leaves fiscal_period empty, even when its article or document names the quarter, "
    "because a period is verified only against a cited fact. "
    "Never combine different accounting bases, fiscal period types, currencies, units or scales. "
    "The one exception: a consensus fact's matches_actual_fact_ids are the actuals of the quarter it estimated; "
    "cite the consensus fact with one of them to compare result against consensus (baseline_type consensus). "
    "change_vs_prior_expectations compares against a cited baseline: prior_guidance, consensus or prior_actual. "
    "Its claim_ids reference only observed_fact or calculation claims, never inference claims. "
    "The baseline must be a cited company_evidence fact (consensus, guidance or prior actual); analyst expectations "
    "quoted only in articles belong in supporting claims, with assessment unknown and baseline_type none. "
    "When no cited baseline exists use assessment unknown and baseline_type none. A guidance change is not a market surprise; "
    "never claim a beat, miss or surprise without a cited consensus fact. "
    "Put contradicting and package opposing evidence in opposing_claims instead of omitting it. "
    "horizon is the calendar-day window for the thesis to play out. invalidation_conditions name an observable trigger, "
    "its data_source and review_within_days. forecast_uncertainty is ordinal, never a probability. "
    "List what is still unknown in unresolved_questions. "
    f"supporting_claims, opposing_claims, invalidation_conditions and unresolved_questions hold at most "
    f"{MAX_CLAIMS_PER_SECTION} items each; when more apply, keep the {MAX_CLAIMS_PER_SECTION} most material and "
    f"merge or drop the rest."
)

REQUIRED_OUTPUT_EXAMPLE = {
    "causal_mechanism": "how the observed change affects the business",
    "change_vs_prior_expectations": {"assessment": "|".join(ASSESSMENTS), "baseline_type": "|".join(BASELINE_TYPES),
                                     "statement": "supported comparison or explicit unknown", "claim_ids": ["S1"]},
    "direction": "|".join(DIRECTIONS),
    "horizon": {"minimum_days": 1, "maximum_days": 10},
    "forecast_uncertainty": "|".join(UNCERTAINTY_LEVELS),
    "supporting_claims": [{"claim_id": "S1", "statement": "short claim", "claim_type": "|".join(CLAIM_TYPES),
                           "central": True, "fiscal_period": "", "fact_ids": [], "document_ids": [],
                           "article_ids": [123],
                           "quotes": [{"source_id": "article:123 | document id", "text": "exact source text"}]},
                          {"claim_id": "S2", "statement": "one reported or computed figure", "claim_type": "observed_fact",
                           "central": True, "fiscal_period": "exact cited fiscal_period", "fact_ids": ["fact:..."],
                           "document_ids": [], "article_ids": [], "quotes": []}],
    "opposing_claims": [],
    "invalidation_conditions": [{"trigger": "observable event", "data_source": "|".join(DATA_SOURCES),
                                 "review_within_days": 5}],
    "unresolved_questions": ["what is still unknown"],
}


class ThesisShapeError(ValueError):
    pass


def _text(value, name, *, allow_empty=False):
    if not isinstance(value, str):
        raise ThesisShapeError(f"{name} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise ThesisShapeError(f"{name} is empty")
    return text[:MAX_TEXT_CHARACTERS]


def _choice(value, name, allowed):
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise ThesisShapeError(f"{name} is not one of {', '.join(allowed)}")
    return text


def _integer(value, name):
    if type(value) is not int:
        raise ThesisShapeError(f"{name} must be an integer")
    return value


def _list(value, name, *, limit=MAX_CLAIMS_PER_SECTION):
    if not isinstance(value, list) or len(value) > limit:
        raise ThesisShapeError(f"{name} must be a list of at most {limit} items")
    return value


def _object(value, name, fields):
    if not isinstance(value, dict) or not set(fields) <= set(value):
        raise ThesisShapeError(f"{name} is missing required fields")
    return value


def _identifiers(value, name):
    return sorted({_text(item, name) for item in _list(value, name, limit=24)})


def _article_ids(value):
    items = _list(value, "article_ids", limit=24)
    if any(type(item) is not int or item <= 0 for item in items):
        raise ThesisShapeError("article_ids must be positive integers")
    return sorted(set(items))


def _claim(value):
    claim = _object(value, "claim", CLAIM_SCHEMA["required"])
    if not isinstance(claim["central"], bool):
        raise ThesisShapeError("claim central must be a boolean")
    quotes = [_object(quote, "quote", ("source_id", "text")) for quote in _list(claim["quotes"], "quotes")]
    return {"claim_id": _text(claim["claim_id"], "claim_id"), "statement": _text(claim["statement"], "statement"),
            "claim_type": _choice(claim["claim_type"], "claim_type", CLAIM_TYPES), "central": claim["central"],
            "fiscal_period": _text(claim["fiscal_period"], "fiscal_period", allow_empty=True),
            "fact_ids": _identifiers(claim["fact_ids"], "fact_ids"),
            "document_ids": _identifiers(claim["document_ids"], "document_ids"),
            "article_ids": _article_ids(claim["article_ids"]),
            "quotes": [{"source_id": _text(q["source_id"], "quote source_id"), "text": _text(q["text"], "quote text")}
                       for q in quotes]}


def _invalidation(value):
    condition = _object(value, "invalidation condition", INVALIDATION_SCHEMA["required"])
    return {"trigger": _text(condition["trigger"], "trigger"),
            "data_source": _choice(condition["data_source"], "data_source", DATA_SOURCES),
            "review_within_days": _integer(condition["review_within_days"], "review_within_days")}


def normalize_evidence_thesis(value: Any) -> dict[str, Any]:
    """Return the model-authored thesis in canonical form or raise ThesisShapeError.

    Shape normalization never repairs content: a missing or mistyped field is an invalid
    thesis, not a default value.
    """
    thesis = _object(value, "evidence_thesis", EVIDENCE_THESIS_SCHEMA["required"])
    change = _object(thesis["change_vs_prior_expectations"], "change_vs_prior_expectations",
                     ("assessment", "baseline_type", "statement", "claim_ids"))
    horizon = _object(thesis["horizon"], "horizon", ("minimum_days", "maximum_days"))
    normalized = {
        "causal_mechanism": _text(thesis["causal_mechanism"], "causal_mechanism"),
        "change_vs_prior_expectations": {
            "assessment": _choice(change["assessment"], "assessment", ASSESSMENTS),
            "baseline_type": _choice(change["baseline_type"], "baseline_type", BASELINE_TYPES),
            "statement": _text(change["statement"], "change statement"),
            "claim_ids": _identifiers(change["claim_ids"], "change claim_ids"),
        },
        "direction": _choice(thesis["direction"], "direction", DIRECTIONS),
        "horizon": {"minimum_days": _integer(horizon["minimum_days"], "minimum_days"),
                    "maximum_days": _integer(horizon["maximum_days"], "maximum_days")},
        "forecast_uncertainty": _choice(thesis["forecast_uncertainty"], "forecast_uncertainty", UNCERTAINTY_LEVELS),
        "supporting_claims": [_claim(item) for item in _list(thesis["supporting_claims"], "supporting_claims")],
        "opposing_claims": [_claim(item) for item in _list(thesis["opposing_claims"], "opposing_claims")],
        "invalidation_conditions": [_invalidation(item) for item in
                                    _list(thesis["invalidation_conditions"], "invalidation_conditions")],
        "unresolved_questions": [_text(item, "unresolved question") for item in
                                 _list(thesis["unresolved_questions"], "unresolved_questions")],
    }
    claim_ids = [claim["claim_id"] for section in ("supporting_claims", "opposing_claims") for claim in normalized[section]]
    if len(claim_ids) != len(set(claim_ids)):
        raise ThesisShapeError("claim_id values must be unique")
    return normalized
