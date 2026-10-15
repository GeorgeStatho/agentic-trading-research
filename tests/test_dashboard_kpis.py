from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.db_fixtures import temporary_database


try:
    from api_support.dashboard import build_dashboard_kpis

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_dashboard_kpis = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class DashboardKpiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)

    def test_dashboard_kpis_include_unique_install_count_from_telemetry(self):
        account_payload = {
            "equity": "10000",
            "buying_power": "5000",
            "last_equity": "9800",
        }
        positions_payload: list[dict] = []
        clock_payload = {
            "is_open": True,
            "next_open": "2026-05-25T13:30:00+00:00",
            "next_close": "2026-05-24T20:00:00+00:00",
            "timestamp": "2026-05-24T15:00:00+00:00",
        }
        fills_payload: list[dict] = []
        portfolio_history_payload = {
            "equity": [9800, 10000],
            "timestamp": ["2026-05-23T20:00:00+00:00", "2026-05-24T20:00:00+00:00"],
        }

        def _fake_alpaca_get_json(path, params=None):
            if path == "/v2/account":
                return account_payload
            if path == "/v2/positions":
                return positions_payload
            if path == "/v2/clock":
                return clock_payload
            if path == "/v2/account/activities/FILL":
                return fills_payload
            raise AssertionError(f"Unexpected path: {path} params={params}")

        with (
            patch("api_support.dashboard.alpaca_get_json", side_effect=_fake_alpaca_get_json),
            patch("api_support.dashboard.fetch_portfolio_history", return_value=portfolio_history_payload),
            patch(
                "api_support.dashboard.get_telemetry_summary",
                return_value={
                    "unique_install_count": 7,
                    "startup_ping_count": 19,
                    "last_seen_at": "2026-05-24T15:30:00+00:00",
                },
            ),
            patch(
                "api_support.dashboard.read_json_payload",
                return_value={"state": "running", "updated_at": "2026-05-24T15:30:00+00:00"},
            ),
            patch("api_support.dashboard._build_top_rankings_payload", return_value={"top_sectors": []}),
        ):
            payload = build_dashboard_kpis()

        self.assertEqual(payload["usage_summary"]["unique_install_count"], 7)
        self.assertEqual(payload["usage_summary"]["startup_ping_count"], 19)
        self.assertEqual(payload["usage_summary"]["last_seen_at"], "2026-05-24T15:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
