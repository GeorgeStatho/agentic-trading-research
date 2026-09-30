from __future__ import annotations

"""Phase 5 of the LLM thesis validator: daily orchestration and shadow mode.

Runs once per position per trading day, after the configured time (default
15:30 ET), piggybacking on the option-position management cycle so it reuses
the freshly computed rules-layer verdicts. In shadow mode (the default) the
full pipeline runs and persists verdicts but executes nothing; the recorded
option mid price at verdict time is the counterfactual exit mark.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from services.common import env_flag, env_positive_int, safe_float
from services.thesis_arbiter import ACTION_CLOSE, ACTION_CLOSE_HALF, decide_thesis_arbiter_action


LOGGER = logging.getLogger(__name__)

MARKET_TIMEZONE = ZoneInfo("America/New_York")
VALID_MODES = {"off", "shadow", "live"}


@dataclass(frozen=True)
class ThesisValidatorSettings:
    """Runtime configuration for the daily thesis validation pipeline."""

    mode: str = "shadow"
    kill_switch: bool = False
    run_after_hour: int = 15
    run_after_minute: int = 30
    samples: int = 3
    temperature: float = 0.4
    news_limit: int = 10
    max_daily_llm_exits: int = 2
    model: str = ""

    @classmethod
    def from_env(cls) -> "ThesisValidatorSettings":
        mode = str(os.getenv("THESIS_VALIDATOR_MODE", "shadow")).strip().lower()
        if mode not in VALID_MODES:
            mode = "shadow"
        try:
            temperature = float(os.getenv("THESIS_VALIDATOR_TEMPERATURE", "0.4"))
        except ValueError:
            temperature = 0.4
        run_after = str(os.getenv("THESIS_VALIDATOR_RUN_AFTER_ET", "15:30")).strip()
        try:
            hour_text, minute_text = run_after.split(":", 1)
            run_after_hour = min(23, max(0, int(hour_text)))
            run_after_minute = min(59, max(0, int(minute_text)))
        except ValueError:
            run_after_hour, run_after_minute = 15, 30
        return cls(
            mode=mode,
            kill_switch=env_flag("THESIS_VALIDATOR_KILL_SWITCH", False),
            run_after_hour=run_after_hour,
            run_after_minute=run_after_minute,
            samples=env_positive_int("THESIS_VALIDATOR_SAMPLES", 3),
            temperature=min(1.0, max(0.0, temperature)),
            news_limit=env_positive_int("THESIS_VALIDATOR_NEWS_LIMIT", 10),
            max_daily_llm_exits=env_positive_int("THESIS_VALIDATOR_MAX_DAILY_EXITS", 2),
            model=str(os.getenv("THESIS_VALIDATOR_MODEL", "")).strip(),
        )


class ThesisValidatorService:
    """Adjudicate every open position's entry thesis once per trading day."""

    def __init__(
        self,
        *,
        settings: ThesisValidatorSettings | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings or ThesisValidatorSettings.from_env()
        self._logger = logger or LOGGER

    @property
    def settings(self) -> ThesisValidatorSettings:
        return self._settings

    def _is_due(self, market_now: datetime) -> bool:
        run_after = market_now.replace(
            hour=self._settings.run_after_hour,
            minute=self._settings.run_after_minute,
            second=0,
            microsecond=0,
        )
        return market_now >= run_after

    @staticmethod
    def _rules_exit_active(position_summary: dict[str, Any]) -> bool:
        if position_summary.get("close_submitted"):
            return True
        return str(position_summary.get("decision") or "").strip().lower() == "sell"

    def _execute_action(
        self,
        *,
        action: str,
        option_symbol: str,
        quantity: int,
        submit_full_exit: Callable[..., Any] | None,
        submit_partial_exit: Callable[..., Any] | None,
    ) -> tuple[bool, str]:
        try:
            if action == ACTION_CLOSE and submit_full_exit is not None:
                submit_full_exit(option_symbol)
                return True, ""
            if action == ACTION_CLOSE_HALF and submit_partial_exit is not None:
                submit_partial_exit(option_symbol, max(1, quantity // 2))
                return True, ""
        except Exception as exc:
            self._logger.exception("Thesis validator exit submission failed for %s: %s", option_symbol, exc)
            return False, str(exc)
        return False, "no_exit_callback_available"

    def _validate_position(
        self,
        *,
        position_summary: dict[str, Any],
        run_date: str,
        run_at: str,
        submit_full_exit: Callable[..., Any] | None,
        submit_partial_exit: Callable[..., Any] | None,
    ) -> dict[str, Any] | None:
        from db_helpers.market import (
            get_open_position_thesis_for_option_symbol,
            get_thesis_validation_run_for_date,
            record_thesis_validation_run,
        )

        option_symbol = str(position_summary.get("symbol") or "").strip().upper()
        if not option_symbol:
            return None

        thesis = get_open_position_thesis_for_option_symbol(option_symbol)
        if thesis is None:
            self._logger.info(
                "Thesis validator skipped %s: no captured entry thesis (position pre-dates Phase 0).",
                option_symbol,
            )
            return {"option_symbol": option_symbol, "status": "missing_thesis"}

        if get_thesis_validation_run_for_date(thesis_id=int(thesis["id"]), run_date=run_date) is not None:
            return {"option_symbol": option_symbol, "status": "already_ran"}

        run_record: dict[str, Any] = {
            "thesis_id": int(thesis["id"]),
            "option_symbol": option_symbol,
            "symbol": thesis.get("symbol"),
            "run_at": run_at,
            "run_date": run_date,
            "mode": self._settings.mode,
            "mark_mid_price": safe_float(position_summary.get("mid_price")),
        }

        if self._rules_exit_active(position_summary):
            # The deterministic layer already decided to exit; the LLM is not consulted.
            run_record.update(
                {
                    "thesis_state": "not_evaluated",
                    "consistency": "not_evaluated",
                    "rules_verdict": "exit",
                    "arbiter_action": "none",
                    "arbiter_reason": "rules_exit_precedence",
                }
            )
            record_thesis_validation_run(run_record)
            return {"option_symbol": option_symbol, "status": "rules_exit_precedence"}

        run_record["rules_verdict"] = "hold"
        verdict = self._adjudicate_into_record(
            run_record,
            thesis=thesis,
            position_summary=position_summary,
        )
        arbiter = self._arbitrate_into_record(
            run_record,
            verdict=verdict,
            position_summary=position_summary,
        )
        executed = self._maybe_execute_arbiter_action(
            run_record,
            arbiter=arbiter,
            option_symbol=option_symbol,
            position_summary=position_summary,
            submit_full_exit=submit_full_exit,
            submit_partial_exit=submit_partial_exit,
        )

        record_thesis_validation_run(run_record)
        return {
            "option_symbol": option_symbol,
            "status": "evaluated",
            "thesis_state": run_record.get("thesis_state"),
            "would_open_today": run_record.get("would_open_today"),
            "consistency": run_record.get("consistency"),
            "arbiter_action": arbiter["action"],
            "arbiter_reason": arbiter["reason"],
            "executed": executed,
        }

    def _adjudicate_into_record(
        self,
        run_record: dict[str, Any],
        *,
        thesis: dict[str, Any],
        position_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run Phases 1-3 and fold the gated verdict into the run record."""
        try:
            from services.thesis_adjudicator import adjudicate_thesis
            from services.thesis_evidence_pack import build_thesis_evidence_pack

            evidence_pack = build_thesis_evidence_pack(
                thesis,
                position_summary,
                news_limit=self._settings.news_limit,
            )
            run_record["evidence_pack"] = evidence_pack
            verdict = adjudicate_thesis(
                evidence_pack,
                model=self._settings.model or None,
                samples=self._settings.samples,
                temperature=self._settings.temperature,
            )
        except Exception as exc:
            self._logger.exception(
                "Thesis validation failed for %s: %s", run_record.get("option_symbol"), exc
            )
            run_record.update(
                {
                    "thesis_state": "unclear",
                    "consistency": "error",
                    "error_text": str(exc),
                }
            )
            return None

        run_record.update(
            {
                "raw_samples": verdict.get("raw_samples", []),
                "sample_count": verdict.get("sample_count", 0),
                "consistency": verdict.get("consistency"),
                "flip_detected": verdict.get("flip_detected"),
                "grounded": verdict.get("grounded"),
                "thesis_state": verdict.get("thesis_state"),
                "would_open_today": verdict.get("would_open_today"),
                "evidence_ids": verdict.get("evidence_ids", []),
                "rationale": verdict.get("rationale"),
                "confidence": verdict.get("confidence"),
            }
        )
        return verdict

    def _arbitrate_into_record(
        self,
        run_record: dict[str, Any],
        *,
        verdict: dict[str, Any] | None,
        position_summary: dict[str, Any],
    ) -> dict[str, Any]:
        from db_helpers.market import count_thesis_validator_exits_for_date

        arbiter = decide_thesis_arbiter_action(
            rules_exit_active=False,
            verdict=verdict,
            quantity=self._contract_quantity(position_summary),
            llm_exits_today=count_thesis_validator_exits_for_date(run_record["run_date"]),
            max_daily_llm_exits=self._settings.max_daily_llm_exits,
            kill_switch=self._settings.kill_switch,
        )
        run_record["arbiter_action"] = arbiter["action"]
        run_record["arbiter_reason"] = arbiter["reason"]
        return arbiter

    def _maybe_execute_arbiter_action(
        self,
        run_record: dict[str, Any],
        *,
        arbiter: dict[str, Any],
        option_symbol: str,
        position_summary: dict[str, Any],
        submit_full_exit: Callable[..., Any] | None,
        submit_partial_exit: Callable[..., Any] | None,
    ) -> bool:
        executed = False
        live_execution_allowed = self._settings.mode == "live" and not self._settings.kill_switch
        if live_execution_allowed and arbiter["action"] in {ACTION_CLOSE, ACTION_CLOSE_HALF}:
            executed, execution_error = self._execute_action(
                action=arbiter["action"],
                option_symbol=option_symbol,
                quantity=self._contract_quantity(position_summary),
                submit_full_exit=submit_full_exit,
                submit_partial_exit=submit_partial_exit,
            )
            if execution_error:
                run_record["error_text"] = execution_error
        run_record["executed"] = executed
        return executed

    @staticmethod
    def _contract_quantity(position_summary: dict[str, Any]) -> int:
        return int(safe_float(position_summary.get("quantity")) or 0)

    def run_due_validation(
        self,
        *,
        positions: list[dict[str, Any]],
        submit_full_exit: Callable[..., Any] | None = None,
        submit_partial_exit: Callable[..., Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Run the daily pipeline if due; returns None when nothing should run."""
        if self._settings.mode == "off":
            return None

        market_now = (now or datetime.now(MARKET_TIMEZONE)).astimezone(MARKET_TIMEZONE)
        if not self._is_due(market_now):
            return None

        run_date = market_now.date().isoformat()
        run_at = market_now.isoformat()
        results: list[dict[str, Any]] = []
        for position_summary in positions:
            if not isinstance(position_summary, dict):
                continue
            try:
                result = self._validate_position(
                    position_summary=position_summary,
                    run_date=run_date,
                    run_at=run_at,
                    submit_full_exit=submit_full_exit,
                    submit_partial_exit=submit_partial_exit,
                )
            except Exception as exc:
                self._logger.exception(
                    "Thesis validator crashed for %s: %s",
                    position_summary.get("symbol"),
                    exc,
                )
                result = {
                    "option_symbol": str(position_summary.get("symbol") or "").strip().upper(),
                    "status": "error",
                    "error": str(exc),
                }
            if result is not None:
                results.append(result)

        evaluated_count = sum(1 for result in results if result.get("status") == "evaluated")
        if results:
            self._logger.info(
                "Thesis validator ran for %s positions (%s evaluated) in %s mode on %s.",
                len(results),
                evaluated_count,
                self._settings.mode,
                run_date,
            )
        return {
            "ran_at": run_at,
            "run_date": run_date,
            "mode": self._settings.mode,
            "kill_switch": self._settings.kill_switch,
            "position_count": len(results),
            "evaluated_count": evaluated_count,
            "results": results,
        }
