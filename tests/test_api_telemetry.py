from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

try:
    from flask import Flask
    from api_routes.telemetry import register_telemetry_routes

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    Flask = None
    register_telemetry_routes = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class ApiTelemetryTests(unittest.TestCase):
    def test_startup_receiver_records_unique_install_and_summary(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "telemetry.sqlite3"
            app = Flask(__name__)
            register_telemetry_routes(app)
            client = app.test_client()
            now = datetime.now(timezone.utc)
            install_created_at = (now - timedelta(days=1)).isoformat()
            first_started_at = now.isoformat()
            second_started_at = (now + timedelta(minutes=5)).isoformat()

            first_payload = {
                "event": "startup",
                "telemetry_version": 1,
                "project": "Stock-trading-experiment",
                "runtime": "worker",
                "install_id": "install-123",
                "install_created_at": install_created_at,
                "started_at": first_started_at,
                "release": "2026.05.24",
            }
            second_payload = {
                **first_payload,
                "runtime": "api",
                "started_at": second_started_at,
            }

            with patch.dict(
                os.environ,
                {
                    "TELEMETRY_RECEIVER_READ_AUTH_TOKEN": "",
                    "TELEMETRY_RECEIVER_WRITE_AUTH_TOKEN": "",
                    "TELEMETRY_RECEIVER_AUTH_TOKEN": "",
                },
                clear=False,
            ), patch("api_support.telemetry.DB_PATH", db_path):
                first_response = client.post("/api/telemetry/startup", json=first_payload)
                second_response = client.post("/api/telemetry/startup", json=second_payload)
                summary_response = client.get("/api/telemetry/summary")

            self.assertEqual(first_response.status_code, 200)
            self.assertEqual(second_response.status_code, 200)
            self.assertEqual(summary_response.status_code, 200)

            first_json = first_response.get_json()
            second_json = second_response.get_json()
            summary_json = summary_response.get_json()

            self.assertEqual(first_json["startup_ping_count"], 1)
            self.assertEqual(second_json["startup_ping_count"], 2)
            self.assertEqual(second_json["last_runtime"], "api")
            self.assertEqual(summary_json["unique_install_count"], 1)
            self.assertEqual(summary_json["startup_ping_count"], 2)
            self.assertEqual(summary_json["unique_installs_last_7d"], 1)
            self.assertEqual(summary_json["project"], "")

    def test_summary_requires_bearer_token_when_read_token_is_configured(self):
        app = Flask(__name__)
        register_telemetry_routes(app)
        client = app.test_client()

        with patch.dict(os.environ, {"TELEMETRY_RECEIVER_READ_AUTH_TOKEN": "secret-token"}, clear=False):
            response = client.get("/api/telemetry/summary")

        self.assertEqual(response.status_code, 401)

    def test_startup_receiver_stays_public_when_only_read_token_is_configured(self):
        app = Flask(__name__)
        register_telemetry_routes(app)
        client = app.test_client()

        payload = {
            "event": "startup",
            "telemetry_version": 1,
            "project": "Stock-trading-experiment",
            "runtime": "worker",
            "install_id": "public-install-123",
            "install_created_at": "2026-05-24T00:00:00+00:00",
            "started_at": "2026-05-24T12:00:00+00:00",
        }

        with TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"TELEMETRY_RECEIVER_READ_AUTH_TOKEN": "secret-token"},
            clear=False,
        ):
            db_path = Path(temp_dir) / "telemetry.sqlite3"
            with patch("api_support.telemetry.DB_PATH", db_path):
                response = client.post("/api/telemetry/startup", json=payload)

        self.assertEqual(response.status_code, 200)

    def test_startup_receiver_requires_bearer_token_when_write_token_is_configured(self):
        app = Flask(__name__)
        register_telemetry_routes(app)
        client = app.test_client()

        payload = {
            "event": "startup",
            "telemetry_version": 1,
            "project": "Stock-trading-experiment",
            "runtime": "worker",
            "install_id": "protected-install-123",
            "install_created_at": "2026-05-24T00:00:00+00:00",
            "started_at": "2026-05-24T12:00:00+00:00",
        }

        with patch.dict(os.environ, {"TELEMETRY_RECEIVER_WRITE_AUTH_TOKEN": "write-secret"}, clear=False):
            response = client.post("/api/telemetry/startup", json=payload)

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
