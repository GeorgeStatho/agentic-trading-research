from __future__ import annotations

import os
import unittest
from unittest.mock import patch


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
    def test_summary_requires_bearer_token_when_read_token_is_configured(self):
        app = Flask(__name__)
        register_telemetry_routes(app)
        client = app.test_client()

        with patch.dict(os.environ, {"TELEMETRY_RECEIVER_READ_AUTH_TOKEN": "secret-token"}, clear=False):
            response = client.get("/api/telemetry/summary")

        self.assertEqual(response.status_code, 401)

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

    def test_summary_is_closed_when_no_read_token_is_configured(self):
        app = Flask(__name__)
        register_telemetry_routes(app)
        client = app.test_client()
        unset = {"TELEMETRY_RECEIVER_READ_AUTH_TOKEN": "", "TELEMETRY_RECEIVER_AUTH_TOKEN": ""}

        with patch.dict(os.environ, unset, clear=False):
            response = client.get("/api/telemetry/summary", headers={"Authorization": "Bearer "})

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
