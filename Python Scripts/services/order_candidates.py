from __future__ import annotations

from typing import Any


class OrderCandidateBuilder:
    """Turn agent output into executable option-order candidates."""

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

    @classmethod
    def _is_trade_eligible(cls, company_result: dict[str, Any]) -> bool:
        decision = str(company_result.get("decision") or "").strip().lower()
        confidence = str(company_result.get("confidence") or "").strip().lower()
        strategist_recommendation = company_result.get("strategist_recommendation") or {}
        strategist_decision = cls._normalize_strategist_decision(
            strategist_recommendation.get("decision")
        )

        if decision not in {"call", "put"}:
            return False
        if confidence not in {"high", "medium"}:
            return False
        if strategist_decision == "do_not_trade":
            return False
        return True

    @staticmethod
    def _build_candidate(company_result: dict[str, Any]) -> dict[str, Any] | None:
        decision = str(company_result.get("decision") or "").strip().lower()
        confidence = str(company_result.get("confidence") or "").strip().lower()
        selected_option = company_result.get("selected_option") or {}
        option_symbol = str(selected_option.get("symbol") or "").strip().upper()

        if not OrderCandidateBuilder._is_trade_eligible(company_result):
            return None
        if not option_symbol:
            return None

        return {
            "company_id": company_result.get("company_id"),
            "symbol": company_result.get("symbol"),
            "name": company_result.get("name"),
            "decision": decision,
            "confidence": confidence,
            "selected_option_id": company_result.get("selected_option_id"),
            "selected_expiration_date": company_result.get("selected_expiration_date"),
            "selected_strike_price": company_result.get("selected_strike_price"),
            "selected_option_source": company_result.get("selected_option_source"),
            "selected_option_symbol": option_symbol,
            "selected_option": selected_option,
            "strategist_recommendation": company_result.get("strategist_recommendation", {}),
            "reason": company_result.get("reason"),
        }

    def build(self, agent_result: dict[str, Any]) -> list[dict[str, Any]]:
        return self.build_from_company_results(self._extract_selected_option_candidates(agent_result))

    def build_from_company_results(self, company_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build unique order candidates from selected-option company summaries."""
        candidates: list[dict[str, Any]] = []
        seen_option_symbols: set[str] = set()

        for company_result in company_results:
            candidate = self._build_candidate(company_result)
            if candidate is None:
                continue

            option_symbol = str(candidate.get("selected_option_symbol") or "").strip().upper()
            if option_symbol in seen_option_symbols:
                continue

            seen_option_symbols.add(option_symbol)
            candidates.append(candidate)

        return candidates

    def build_from_manager_result(self, manager_result: dict[str, Any]) -> dict[str, Any] | None:
        """Build a single executable candidate from one manager result."""
        company = dict(manager_result.get("company") or {})
        recommendation = dict(manager_result.get("recommendation") or {})
        company_result = {
            "company_id": company.get("company_id"),
            "symbol": company.get("symbol"),
            "name": company.get("name"),
            "decision": recommendation.get("decision"),
            "confidence": recommendation.get("confidence"),
            "selected_option_id": recommendation.get("selected_option_id"),
            "selected_expiration_date": recommendation.get("selected_expiration_date"),
            "selected_strike_price": recommendation.get("selected_strike_price"),
            "selected_option_source": recommendation.get("selected_option_source"),
            "strategist_recommendation": manager_result.get("strategist_recommendation", {}),
            "selected_option": manager_result.get("selected_option"),
            "reason": recommendation.get("reason"),
        }
        return self._build_candidate(company_result)
