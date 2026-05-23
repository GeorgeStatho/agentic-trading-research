from __future__ import annotations

from flask import jsonify, request

from api_support.common import internal_error, read_json_file, safe_int
from api_support.context import TRADE_EXECUTION_OUTPUT_PATH
from api_support.trades import build_executed_trades_payload, build_trade_explanation_payload


def register_trade_routes(app) -> None:
    @app.get("/api/trade-execution-output")
    def trade_execution_output():
        return read_json_file(TRADE_EXECUTION_OUTPUT_PATH)

    @app.get("/api/executed-trades")
    def executed_trades():
        try:
            underlying_symbol = request.args.get("symbol")
            limit = safe_int(request.args.get("limit"), 100, maximum=500)
            return jsonify(
                build_executed_trades_payload(
                    underlying_symbol=underlying_symbol,
                    limit=limit,
                )
            ), 200
        except Exception as exc:
            return internal_error("Failed to load executed trades from SQLite.", exc)

    @app.get("/api/why-bot-traded")
    def why_bot_traded():
        try:
            return jsonify(build_trade_explanation_payload()), 200
        except Exception as exc:
            return internal_error("Failed to load trade explanations.", exc)
