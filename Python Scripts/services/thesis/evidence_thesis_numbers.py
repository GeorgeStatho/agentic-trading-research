"""Deterministic support checks for numbers written in thesis claims.

A stated number is supported when it is a rounding of a cited fact, of a calculation over
cited compatible facts, or appears verbatim in cited source text. Nothing else counts:
plausible-looking numbers without a source fail.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
import math
import re

from services.discovery.company_evidence_extraction import COMPARISON_FIELDS, consensus_matches_actual, guidance_midpoint


_NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-−])?\$?(?P<digits>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<decimals>\d+))?"
    r"(?:\s?(?P<suffix>%|percent\b|bps\b|basis points\b|billion\b|bn\b|million\b|mn\b|mm\b|thousand\b|[kmb]\b))?"
    r"(?![\w-])",
    re.IGNORECASE,
)
_MULTIPLIERS = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6,
                "thousand": 1e3, "k": 1e3}
_PERCENT_SUFFIXES = {"%", "percent"}
_BASIS_POINT_SUFFIXES = {"bps", "basis points"}
# Calculations may compare different fiscal periods of otherwise identical measurements.
_CALCULATION_FIELDS = tuple(field for field in COMPARISON_FIELDS if field != "fiscal_period")


@dataclass(frozen=True)
class StatedNumber:
    text: str
    core: str
    value: float
    decimals: int
    kind: str

    def matches(self, candidate: float) -> bool:
        tolerance = 0.5 * 10 ** -(self.decimals + 6)
        return abs(round(abs(candidate), self.decimals) - self.value) <= tolerance


def stated_numbers(statement: str) -> list[StatedNumber]:
    numbers = []
    for match in _NUMBER.finditer(statement):
        suffix = (match["suffix"] or "").lower()
        core = match["digits"].replace(",", "") + ("." + match["decimals"] if match["decimals"] else "")
        kind = ("percent" if suffix in _PERCENT_SUFFIXES else "basis_points" if suffix in _BASIS_POINT_SUFFIXES
                else "amount")
        multiplier = _MULTIPLIERS.get(suffix, 1.0)
        numbers.append(StatedNumber(text=match[0].strip(), core=core, value=float(core),
                                    decimals=len(match["decimals"] or ""), kind=kind + ":" + str(multiplier)))
    return numbers


def _fact_values(fact):
    values = [fact["value"], guidance_midpoint(fact)]
    if fact.get("range_high") is not None:
        values.append(fact["range_high"])
    return values


def _compatible_for_calculation(first, second):
    # An estimate and the actual of the quarter it estimated: "revenue beat consensus by X%".
    if consensus_matches_actual(first, second) or consensus_matches_actual(second, first):
        return True
    if any(first.get(field) != second.get(field) for field in _CALCULATION_FIELDS):
        return False
    # Guidance and actuals are only comparable for the identical fiscal period.
    families = {fact["kind"] in {"guidance", "prior_guidance"} for fact in (first, second)}
    return len(families) == 1 or first["fiscal_period"] == second["fiscal_period"]


def incompatible_calculation_facts(facts):
    usable = [fact for fact in facts if fact.get("value") is not None and fact["kind"] != "derived"]
    return [(a["fact_id"], b["fact_id"]) for a, b in permutations(usable, 2)
            if a["fact_id"] < b["fact_id"] and not _compatible_for_calculation(a, b)]


class NumberSupport:
    """Candidate values derived from one claim's citations."""

    def __init__(self, facts, package_claims, source_texts):
        known = [fact for fact in facts if fact.get("value") is not None]
        self.amounts, self.percents, self.percent_changes = [], [], []
        for fact in known:
            bucket = self.percents if fact.get("unit") == "percent" else self.amounts
            for value in _fact_values(fact):
                bucket.append((value, fact.get("scale") or 1))
        for first, second in permutations([f for f in known if f["kind"] != "derived"], 2):
            if not _compatible_for_calculation(first, second):
                continue
            change = guidance_midpoint(first) - guidance_midpoint(second)
            bucket = self.percents if first.get("unit") == "percent" else self.amounts
            bucket.append((change, first.get("scale") or 1))
            if guidance_midpoint(second):
                self.percent_changes.append(change / abs(guidance_midpoint(second)) * 100)
        cited = {fact["fact_id"] for fact in facts}
        for claim in package_claims:
            if isinstance(claim.get("value"), (int, float)) and set(claim.get("fact_ids", [])) <= cited:
                self.amounts.append((claim["value"], claim.get("scale") or 1))
        self.text = " ".join(re.sub(r"(?<=\d),(?=\d{3})", "", text) for text in source_texts)

    def _verbatim(self, number):
        return re.search(r"(?<![\d.])" + re.escape(number.core) + r"(?![\d])", self.text) is not None

    def supports(self, number: StatedNumber) -> bool:
        if self._verbatim(number):
            return True
        kind, multiplier = number.kind.split(":")
        multiplier = float(multiplier)
        if kind == "percent":
            return any(number.matches(value) for value, _ in self.percents) or any(
                number.matches(value) for value in self.percent_changes)
        if kind == "basis_points":
            return any(number.matches(value * 100) for value, _ in self.percents)
        for value, scale in self.amounts:
            candidates = (value * scale / multiplier,) if multiplier != 1 else (value, value * scale)
            if any(math.isfinite(candidate) and number.matches(candidate) for candidate in candidates):
                return True
        return False
