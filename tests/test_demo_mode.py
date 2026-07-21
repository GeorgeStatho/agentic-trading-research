from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from api_support.demo_mode import DEMO_ARTICLE_ID, DEMO_OPTION_SYMBOL, build_demo_mode_payload  # noqa: E402


class DemoModeTests(unittest.TestCase):
    def test_demo_payload_contains_a_complete_read_only_exposure_story(self) -> None:
        payload = build_demo_mode_payload()

        self.assertTrue(payload["demo_mode"])
        self.assertIn("No live news", payload["disclaimer"])
        exposure = payload["exposure"]["payload"]
        self.assertEqual(exposure["article_id"], DEMO_ARTICLE_ID)
        self.assertTrue(exposure["has_run"])
        self.assertTrue(exposure["no_trade_executed"])
        self.assertTrue(any(edge["direction"] == "negative" for edge in exposure["edges"]))
        self.assertEqual(exposure["position_impacts"][0]["option_symbol"], DEMO_OPTION_SYMBOL)
        self.assertEqual(exposure["position_impacts"][0]["risk_level"], "high")

        autopsy = payload["autopsy"]
        self.assertTrue(autopsy["read_only"])
        self.assertFalse(autopsy["records"][0]["outcome"]["trade_executed"])
        self.assertEqual(autopsy["records"][0]["evidence"][0]["article_id"], DEMO_ARTICLE_ID)
