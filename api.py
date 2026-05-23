from __future__ import annotations

import logging
import sys
from pathlib import Path

from flask import Flask

from portfolio_history_service import load_env


load_env()

ROOT_DIR = Path(__file__).resolve().parent
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

from api_routes.dashboard import register_dashboard_routes
from api_routes.health import register_health_routes
from api_routes.news import register_news_routes
from api_routes.trades import register_trade_routes


app = Flask(__name__)
logger = logging.getLogger(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


register_health_routes(app)
register_dashboard_routes(app)
register_trade_routes(app)
register_news_routes(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
