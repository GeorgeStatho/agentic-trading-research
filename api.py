from __future__ import annotations

import logging

from flask import Flask

if __name__ == "__main__":
    # Before route modules import services that still read configuration at import time.
    from services.settings import load_process_settings
    load_process_settings()

from api_routes.dashboard import register_dashboard_routes
from api_routes.backtest import register_backtest_routes
from api_routes.demo_mode import register_demo_mode_routes
from api_routes.decision_autopsy import register_decision_autopsy_routes
from api_routes.exposure_map import register_exposure_map_routes
from api_routes.health import register_health_routes
from api_routes.news import register_news_routes
from api_routes.telemetry import register_telemetry_routes
from api_routes.thesis_validator import register_thesis_validator_routes
from api_routes.trades import register_trade_routes
from api_routes.v2_status import register_v2_status_routes
from api_routes.forward_evaluation import register_forward_evaluation_routes
from db_common import DB_PATH
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from services.telemetry import send_startup_ping_async


app = Flask(__name__)
logger = logging.getLogger(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


register_health_routes(app)
register_dashboard_routes(app)
register_backtest_routes(app)
register_demo_mode_routes(app)
register_decision_autopsy_routes(app)
register_trade_routes(app)
register_news_routes(app)
register_exposure_map_routes(app)
register_telemetry_routes(app)
register_thesis_validator_routes(app)
register_v2_status_routes(app)
register_forward_evaluation_routes(app)


if __name__ == "__main__":
    # Read routes rely on this one setup instead of re-checking the schema on every request.
    initialize_market_database(db_path=DB_PATH)
    initialize_news_database(db_path=DB_PATH)
    send_startup_ping_async("api", logger=logger)
    app.run(host="0.0.0.0", port=5000, debug=False)
