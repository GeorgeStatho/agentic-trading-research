from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify

from portfolio_history_service import fetch_portfolio_history, load_env


load_env()
app = Flask(__name__)
ROOT_DIR = Path(__file__).resolve().parent
SCRIPT_STATUS_PATH = Path(
    os.getenv("SCRIPT_STATUS_PATH", str(ROOT_DIR / "web_dashboard" / "public" / "script_status.json"))
)
TRADE_EXECUTION_OUTPUT_PATH = Path(
    os.getenv(
        "TRADE_EXECUTION_OUTPUT_PATH",
        str(ROOT_DIR / "Data" / "trade_execution_output.json"),
    )
)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


@app.get("/api/health")
def health() -> tuple[dict, int]:
    return {"status": "ok"}, 200


@app.get("/api/portfolio-history")
def portfolio_history():
    try:
        payload = fetch_portfolio_history()
        return jsonify(payload), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def read_json_file(path: Path) -> tuple[dict, int]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return jsonify(__import__("json").load(handle)), 200
    except FileNotFoundError:
        return jsonify({"error": f"File not found: {path.name}"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/script-status")
def script_status():
    return read_json_file(SCRIPT_STATUS_PATH)


@app.get("/api/trade-execution-output")
def trade_execution_output():
    return read_json_file(TRADE_EXECUTION_OUTPUT_PATH)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
