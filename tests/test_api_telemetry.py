from __future__ import annotations

import os
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch

from market_db_modules.telemetry import TelemetryNetworkCapReached, record_telemetry_installation
from tests.db_fixtures import temporary_database


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



@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class TelemetryNetworkCapTests(unittest.TestCase):
    """A network can add only a few new install IDs a day; its address is kept only as a salted hash."""

    def setUp(self):
        self.db_path = temporary_database(self)
        self.enterContext(patch.dict(os.environ, {"TELEMETRY_RECEIVER_WRITE_AUTH_TOKEN": ""}))
        app = Flask(__name__)
        register_telemetry_routes(app)
        self.client = app.test_client()

    def ping(self, install_id: str, ip: str = "198.51.100.7") -> int:
        payload = {"event": "startup", "runtime": "worker", "install_id": install_id,
                   "started_at": "2026-11-18T12:00:00+00:00", "project": "Stock-trading-experiment"}
        return self.client.post("/api/telemetry/startup", json=payload, headers={"X-Real-IP": ip}).status_code

    def test_a_network_adds_at_most_three_new_installs_a_day(self):
        self.assertEqual([200, 200, 200, 429], [self.ping(f"install-{n}") for n in range(4)])

    def test_known_installs_are_never_capped(self):
        for n in range(3):
            self.ping(f"install-{n}")
        self.assertEqual(200, self.ping("install-0"))

    def test_other_networks_are_counted_separately(self):
        for n in range(3):
            self.ping(f"install-{n}")
        self.assertEqual(200, self.ping("install-9", ip="198.51.100.8"))

    def test_the_cap_can_be_set(self):
        with patch.dict(os.environ, {"TELEMETRY_RECEIVER_NEW_INSTALLS_PER_NETWORK_PER_DAY": "1"}):
            self.assertEqual([200, 429], [self.ping("install-a"), self.ping("install-b")])

    def test_the_address_is_never_stored(self):
        self.ping("install-0")
        with closing(sqlite3.connect(self.db_path)) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            dump = " ".join(str(row) for table in tables for row in conn.execute(f'SELECT * FROM "{table}"'))
        self.assertIn("install-0", dump)
        self.assertNotIn("198.51.100.7", dump)


class TelemetryNetworkDayTests(unittest.TestCase):
    def test_a_new_day_drops_yesterdays_salt_and_counts(self):
        db_path = temporary_database(self)
        day1, day2 = datetime(2026, 11, 18, 23, tzinfo=timezone.utc), datetime(2026, 11, 19, 1, tzinfo=timezone.utc)
        payload = {"event": "startup", "runtime": "worker"}
        for n in range(3):
            record_telemetry_installation({**payload, "install_id": f"a{n}"}, client_ip="198.51.100.7", now=day1)
        with self.assertRaises(TelemetryNetworkCapReached):
            record_telemetry_installation({**payload, "install_id": "a3"}, client_ip="198.51.100.7", now=day1)

        record_telemetry_installation({**payload, "install_id": "a3"}, client_ip="198.51.100.7", now=day2)
        with closing(sqlite3.connect(db_path)) as conn:
            self.assertEqual([("2026-11-19",)], conn.execute("SELECT day FROM telemetry_daily_salts").fetchall())
            self.assertEqual([("2026-11-19", 1)], conn.execute(
                "SELECT day, new_install_count FROM telemetry_network_days").fetchall())


if __name__ == "__main__":
    unittest.main()
