from __future__ import annotations

import re
import sqlite3

from flask import jsonify, request

from api_support.request_utils import internal_error, read_json_file
from api_support.context import SCRIPT_STATUS_PATH
from api_support.dashboard import (
    build_company_decisions_payload,
    build_decision_expectancy_payload,
    build_dashboard_kpis,
    build_open_positions_payload,
    build_risk_controls_payload,
    build_market_regime_payload,
)


def register_dashboard_routes(app) -> None:
    @app.get("/api/script-status")
    def script_status():
        return read_json_file(SCRIPT_STATUS_PATH)

    @app.get("/api/dashboard-kpis")
    def dashboard_kpis():
        try:
            return jsonify(build_dashboard_kpis()), 200
        except Exception as exc:
            return internal_error("Failed to load dashboard KPIs.", exc)

    @app.get("/api/open-positions")
    def open_positions():
        try:
            return jsonify(build_open_positions_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load open positions.", exc)

    @app.get("/api/risk-controls")
    def risk_controls():
        try:
            return jsonify(build_risk_controls_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load risk controls.", exc)

    @app.get("/api/market-regime")
    def market_regime():
        try:
            return jsonify(build_market_regime_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load market regime.", exc)

    @app.get("/api/company-decisions")
    def company_decisions():
        try:
            return jsonify(build_company_decisions_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load company decisions.", exc)

    @app.get("/api/decision-expectancy")
    def decision_expectancy():
        try:
            within_days = int(request.args.get("within_days", "90"))
            min_samples = int(request.args.get("min_samples", "5"))
            sector = str(request.args.get("sector", "")).strip().lower()
            if sector and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", sector):
                raise ValueError("Invalid sector value.")
            payload = build_decision_expectancy_payload(
                group_by=str(request.args.get("group_by", "confidence")).strip().lower(),
                basis=str(request.args.get("basis", "marked")).strip().lower(),
                within_days=within_days,
                min_samples=min_samples,
                decision=str(request.args.get("decision", "all")).strip().lower(),
                confidence=str(request.args.get("confidence", "all")).strip().lower(),
                sector=sector or None,
            )
            return jsonify(payload), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return internal_error("Failed to load decision expectancy.", exc)

    @app.get("/api/news-lag")
    def news_lag():
        # Read-only and offline: hourly bars are cached by the collector or `research.py news-lag --fetch-missing`.
        try:
            days = int(request.args.get("days", "0"))
            if days < 0:
                raise ValueError("days must be 0 or positive.")
            from market_db_modules.news_lag_report import news_lag_report

            return jsonify(news_lag_report(days=days)), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return internal_error("Failed to load the news-lag report.", exc)

    @app.get("/api/follower-accounts")
    def follower_accounts():
        # Read-only: follower positions and production's recorded outcomes, no broker calls.
        try:
            days = int(request.args.get("days", "0"))
            if not 0 <= days <= 36500:
                raise ValueError("days must be between 0 and 36500.")
            from market_db_modules.account_positions import follower_report

            try:
                return jsonify(follower_report(days=days)), 200
            except sqlite3.OperationalError as exc:
                # A database set up before follower accounts existed simply has none yet.
                if "no such table" not in str(exc):
                    raise
                return jsonify({"as_of": None, "days": days, "accounts": {}, "trades": []}), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return internal_error("Failed to load follower accounts.", exc)
