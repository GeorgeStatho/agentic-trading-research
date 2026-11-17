from __future__ import annotations

from flask import jsonify, request

from api_support.backtest import (
    build_backtest_run_payload,
    build_backtest_runs_payload,
    create_backtest_run,
    fetch_backtest_eod_quotes,
    fetch_backtest_minute_bars,
)
from api_support.request_utils import internal_error, safe_int


def register_backtest_routes(app) -> None:
    @app.get("/api/backtests")
    def backtests():
        try:
            return jsonify(build_backtest_runs_payload(limit=safe_int(request.args.get("limit"), 25, maximum=100))), 200
        except Exception as exc:
            return internal_error("Failed to load backtest runs.", exc)

    @app.get("/api/backtests/<int:run_id>")
    def backtest_run(run_id: int):
        try:
            return jsonify(build_backtest_run_payload(run_id)), 200
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:
            return internal_error("Failed to load backtest run.", exc)

    @app.post("/api/backtests")
    def create_backtest():
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        try:
            return jsonify(create_backtest_run(payload)), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return internal_error("Failed to run backtest.", exc)

    @app.post("/api/backtests/marketdata-eod")
    def fetch_marketdata_eod_quotes():
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        try:
            return jsonify(fetch_backtest_eod_quotes(payload)), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return internal_error("Failed to fetch MarketData.app EOD quotes.", exc)

    @app.post("/api/backtests/alpaca-minute-bars")
    def fetch_alpaca_minute_bars():
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        try:
            return jsonify(fetch_backtest_minute_bars(payload)), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return internal_error("Failed to fetch Alpaca minute bars.", exc)
