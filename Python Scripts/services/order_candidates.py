from __future__ import annotations

from typing import Any

from agent_contracts import OrderCandidate
from services.entry_gates import carry_gate_verdicts, entry_allowed
from services.catalyst_gates import catalyst_admission_allowed
from services.research_promotion import research_only_candidate


class OrderCandidateBuilder:
    """Turn agent output into executable option-order candidates."""

    def __init__(self, *, execute_medium_confidence_trades: bool = True) -> None:
        self._execute_medium_confidence_trades = execute_medium_confidence_trades

    @staticmethod
    def _extract_selected_option_candidates(agent_result: dict[str, Any]) -> list[dict[str, Any]]:
        selected_options = agent_result.get("selected_options", {})
        companies = selected_options.get("companies", [])
        return companies if isinstance(companies, list) else []

    @staticmethod
    def _normalize_strategist_decision(value: Any) -> str:
        decision = str(value or "").strip().lower()
        replacements = {
            "trade candidate": "trade_candidate",
            "watch list": "watchlist",
            "do not trade": "do_not_trade",
        }
        decision = replacements.get(decision, decision)
        return decision if decision in {"trade_candidate", "watchlist", "do_not_trade"} else ""

    def _is_trade_eligible(self, company_result: dict[str, Any]) -> bool:
        context = company_result.get('candidate_context') or {}
        identity = str(company_result.get('candidate_id') or context.get('candidate_id') or '')
        if company_result.get('discovery_shadow') or context.get('discovery_shadow') or research_only_candidate(identity):
            return False
        if not catalyst_admission_allowed(company_result.get('candidate_context')):
            return False
        return self.recommendation_allows_entry(company_result)

    def recommendation_allows_entry(self, company_result: dict[str, Any]) -> bool:
        """The manager decision, confidence, strategist veto and entry gates, without admission.

        Offline replays use this alone: their decisions were already admitted when recorded.
        """
        decision = str(company_result.get("decision") or "").strip().lower()
        confidence = str(company_result.get("confidence") or "").strip().lower()
        strategist_recommendation = company_result.get("strategist_recommendation") or {}
        strategist_decision = self._normalize_strategist_decision(
            strategist_recommendation.get("decision")
        )

        if decision not in {"call", "put"}:
            return False
        if confidence not in {"high", "medium"}:
            return False
        if confidence == "medium" and not self._execute_medium_confidence_trades:
            return False
        if strategist_decision == "do_not_trade":
            return False
        return entry_allowed(company_result)

    def _record_ineligible(self, company_result, reason):
        from services.candidate_lifecycle import reject
        reject(company_result.get("candidate_context"), "ORDER_ELIGIBILITY", reason,
               metrics=company_result, thresholds={"execute_medium_confidence_trades": self._execute_medium_confidence_trades})

    def _build_candidate(self, company_result: dict[str, Any]) -> OrderCandidate | None:
        # Assemble the candidate so callers can work from one normalized shape.
        decision = str(company_result.get("decision") or "").strip().lower()
        confidence = str(company_result.get("confidence") or "").strip().lower()
        selected_option = company_result.get("selected_option") or {}
        option_symbol = str(selected_option.get("symbol") or "").strip().upper()

        if not self._is_trade_eligible(company_result):
            self._record_ineligible(company_result, "ineligible_recommendation")
            return None
        if not option_symbol:
            self._record_ineligible(company_result, "missing_contract")
            return None

        return {
            "candidate_id": company_result.get("candidate_id"),
            "candidate_context": company_result.get("candidate_context"),
            "market_context": company_result.get("market_context", {}),
            "manager_decision_history_id": company_result.get("manager_decision_history_id"),
            "company_id": company_result.get("company_id"),
            "symbol": company_result.get("symbol"),
            "name": company_result.get("name"),
            "sector_key": company_result.get("sector_key") or company_result.get("sector"),
            "decision": decision,
            "confidence": confidence,
            "selected_option_id": company_result.get("selected_option_id"),
            "selected_expiration_date": company_result.get("selected_expiration_date"),
            "selected_strike_price": company_result.get("selected_strike_price"),
            "selected_option_source": company_result.get("selected_option_source"),
            "selected_option_symbol": option_symbol,
            "selected_option": selected_option,
            "strategist_recommendation": company_result.get("strategist_recommendation", {}),
            **carry_gate_verdicts(company_result),
            "reason": company_result.get("reason"),
            # The executor's champion guard reads these top-level fields, so a research
            # identity must survive every conversion rather than being dropped here.
            "research_run_id": company_result.get("research_run_id"),
            "research_strategy": company_result.get("research_strategy"),
        }

    def build(self, agent_result: dict[str, Any]) -> list[OrderCandidate]:
        return self.build_from_company_results(self._extract_selected_option_candidates(agent_result))

    def build_from_company_results(self, company_results: list[dict[str, Any]]) -> list[OrderCandidate]:
        """Build unique order candidates from selected-option company summaries."""
        candidates: list[OrderCandidate] = []
        seen_option_symbols: set[str] = set()

        for company_result in company_results:
            candidate = self._build_candidate(company_result)
            if candidate is None:
                continue

            option_symbol = str(candidate.get("selected_option_symbol") or "").strip().upper()
            if option_symbol in seen_option_symbols:
                self._record_ineligible(company_result, "duplicate_contract_in_batch")
                continue

            seen_option_symbols.add(option_symbol)
            candidates.append(candidate)

        return candidates

    def build_from_manager_result(self, manager_result: dict[str, Any]) -> OrderCandidate | None:
        """Build a single executable candidate from one manager result."""
        company = dict(manager_result.get("company") or {})
        recommendation = dict(manager_result.get("recommendation") or {})
        company_result = {
            "candidate_id": manager_result.get("candidate_id"),
            "candidate_context": manager_result.get("candidate_context"),
            "market_context": manager_result.get("market_context", {}),
            "manager_decision_history_id": manager_result.get("manager_decision_history_id"),
            "company_id": company.get("company_id"),
            "symbol": company.get("symbol"),
            "name": company.get("name"),
            "sector_key": company.get("sector_key") or company.get("sector"),
            "decision": recommendation.get("decision"),
            "confidence": recommendation.get("confidence"),
            "selected_option_id": recommendation.get("selected_option_id"),
            "selected_expiration_date": recommendation.get("selected_expiration_date"),
            "selected_strike_price": recommendation.get("selected_strike_price"),
            "selected_option_source": recommendation.get("selected_option_source"),
            "strategist_recommendation": manager_result.get("strategist_recommendation", {}),
            **carry_gate_verdicts(manager_result),
            "selected_option": manager_result.get("selected_option"),
            "reason": recommendation.get("reason"),
            "research_run_id": manager_result.get("research_run_id"),
            "research_strategy": manager_result.get("research_strategy"),
        }
        return self._build_candidate(company_result)
