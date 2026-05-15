from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

try:
    from api_support.dashboard import build_open_positions_payload

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_open_positions_payload = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class DashboardOpenPositionsTests(unittest.TestCase):
    def test_option_rows_use_broker_mark_price_for_display_and_pl_when_available(self):
        raw_positions = [
            {
                "symbol": "XOM260612C00155000",
                "asset_class": "option",
                "qty": "1",
                "avg_entry_price": "3.25",
                "current_price": "3.67",
                "unrealized_plpc": "0.1292",
            }
        ]

        with patch("api_support.dashboard.alpaca_get_json", return_value=raw_positions), patch(
            "api_support.dashboard.load_option_management_snapshot",
            return_value=({}, {}),
        ), patch(
            "api_support.dashboard.get_latest_option_quote",
            return_value={
                "bid_price": 3.67,
                "ask_price": 3.86,
                "timestamp": "2026-05-15T15:30:00Z",
            },
        ):
            payload = build_open_positions_payload()

        self.assertEqual(payload["position_count"], 1)
        self.assertEqual(payload["option_count"], 1)
        position = payload["positions"][0]
        expected_mid_price = 3.765
        expected_mark_price = 3.67
        expected_unrealized_pl_pct = round(((expected_mark_price - 3.25) / 3.25) * 100.0, 4)
        self.assertEqual(position["mark_price"], expected_mark_price)
        self.assertEqual(position["mid_price"], expected_mid_price)
        self.assertEqual(position["unrealized_pl_pct"], expected_unrealized_pl_pct)
        self.assertNotEqual(position["unrealized_pl_pct"], round(((expected_mid_price - 3.25) / 3.25) * 100.0, 4))


if __name__ == "__main__":
    unittest.main()
