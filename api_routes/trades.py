from __future__ import annotations

from flask import jsonify, request

from api_support.request_utils import csv_response, internal_error, read_json_file, safe_int
from api_support.context import TRADE_EXECUTION_OUTPUT_PATH
from api_support.trades import build_executed_trades_payload, build_trade_explanation_payload

EXECUTED_TRADE_CSV_COLUMNS = ("submitted_at", "underlying_symbol", "option_symbol", "order_side", "order_type",
                              "order_status", "order_qty", "filled_qty", "fill_price", "decision", "confidence",
                              "paper", "order_id")


def register_trade_routes(app) -> None:
    @app.get("/api/trade-execution-output")
    def trade_execution_output():
        return read_json_file(TRADE_EXECUTION_OUTPUT_PATH)

    @app.get("/api/executed-trades")
    def executed_trades():
        try:
            underlying_symbol = request.args.get("symbol")
            limit = safe_int(request.args.get("limit"), 100, maximum=500)
            payload = build_executed_trades_payload(underlying_symbol=underlying_symbol, limit=limit)
            if request.args.get("format") == "csv":
                return csv_response(payload["trades"], EXECUTED_TRADE_CSV_COLUMNS, "executed-trades.csv")
            return jsonify(payload), 200
        except Exception as exc:
            return internal_error("Failed to load executed trades from SQLite.", exc)

    @app.get("/api/why-bot-traded")
    def why_bot_traded():
        try:
            return jsonify(build_trade_explanation_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load trade explanations.", exc)
