from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api_support.dashboard import status_snapshot, summarize_bot_status
from services.core.status_files import StatusReporter


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class StatusSnapshotTests(unittest.TestCase):
    def test_fresh_heartbeat_keeps_reported_state(self):
        snapshot = status_snapshot("Worker", {"state": "paused", "updated_at": _iso(10), "state_since": _iso(900)})
        self.assertEqual(snapshot["state"], "paused")

    def test_stale_heartbeat_is_down(self):
        snapshot = status_snapshot("Worker", {"state": "paused", "updated_at": _iso(600)})
        self.assertEqual(snapshot["state"], "down")

    def test_missing_payload_is_down(self):
        self.assertEqual(status_snapshot("Worker", None)["state"], "down")

    def test_long_running_state_is_stalled(self):
        snapshot = status_snapshot("News Collector", {
            "state": "running", "updated_at": _iso(5), "state_since": _iso(7 * 60 * 60),
        })
        self.assertEqual(snapshot["state"], "stalled")

    def test_recent_error_is_flagged_after_state_moves_on(self):
        snapshot = status_snapshot("Worker", {
            "state": "paused", "updated_at": _iso(5), "last_error": "boom", "last_error_at": _iso(120),
        })
        self.assertEqual(snapshot["state"], "paused")
        self.assertTrue(snapshot["recent_error"])

    def test_old_error_is_not_flagged(self):
        snapshot = status_snapshot("Worker", {
            "state": "paused", "updated_at": _iso(5), "last_error_at": _iso(2 * 60 * 60),
        })
        self.assertFalse(snapshot["recent_error"])


class SummarizeBotStatusTests(unittest.TestCase):
    def test_sleeping_components_are_healthy(self):
        summary = summarize_bot_status([
            ("Worker", {"state": "paused", "updated_at": _iso(5)}),
            ("News Collector", {"state": "running", "updated_at": _iso(5), "state_since": _iso(60)}),
        ])
        self.assertEqual(summary["state"], "healthy")

    def test_undeployed_components_are_left_out(self):
        summary = summarize_bot_status([
            ("Worker", {"state": "paused", "updated_at": _iso(5)}),
            ("EOD Sync", None),
        ])
        self.assertEqual([component["name"] for component in summary["components"]], ["Worker"])
        self.assertEqual(summary["state"], "healthy")

    def test_one_dead_component_degrades(self):
        summary = summarize_bot_status([
            ("Worker", {"state": "paused", "updated_at": _iso(5)}),
            ("Option Manager", {"state": "paused", "updated_at": _iso(600)}),
        ])
        self.assertEqual(summary["state"], "degraded")

    def test_all_dead_is_down(self):
        summary = summarize_bot_status([("Worker", None), ("Option Manager", None)])
        self.assertEqual(summary["state"], "down")


class StatusReporterTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "status.json"
        self.reporter = StatusReporter(self.path, heartbeat_seconds=0)

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_state_since_holds_while_state_repeats(self):
        self.reporter.write("paused", "Market is closed")
        first = self._read()["state_since"]
        self.reporter.write("paused", "Market is closed")
        self.assertEqual(self._read()["state_since"], first)
        self.reporter.write("running", "Executing trading cycle")
        self.assertNotEqual(self._read()["state_since"], first)

    def test_last_error_survives_later_writes(self):
        self.reporter.write("error", "Cycle failed: boom")
        self.reporter.write("paused", "Sleeping until next task", sleep_seconds=300)
        payload = self._read()
        self.assertEqual(payload["state"], "paused")
        self.assertEqual(payload["last_error"], "Cycle failed: boom")
        self.assertEqual(payload["sleep_seconds"], 300)

    def test_heartbeat_refreshes_updated_at(self):
        reporter = StatusReporter(self.path, heartbeat_seconds=0.05)
        reporter.write("paused", "Sleeping")
        first = self._read()["updated_at"]
        deadline = datetime.now() + timedelta(seconds=2)
        while self._read()["updated_at"] == first and datetime.now() < deadline:
            time.sleep(0.02)
        self.assertNotEqual(self._read()["updated_at"], first)


if __name__ == "__main__":
    unittest.main()
