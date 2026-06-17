from __future__ import annotations

import json
import sys
import unittest
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

from services.telemetry import TelemetrySettings, load_or_create_installation_metadata, send_startup_ping


class TelemetryTests(unittest.TestCase):
    def test_installation_metadata_is_created_once_and_reused(self):
        with TemporaryDirectory() as temp_dir:
            metadata_path = Path(temp_dir) / "telemetry_installation.json"

            first = load_or_create_installation_metadata(metadata_path)
            second = load_or_create_installation_metadata(metadata_path)

            self.assertEqual(first["install_id"], second["install_id"])
            self.assertEqual(first["created_at"], second["created_at"])
            self.assertTrue(metadata_path.exists())

    def test_send_startup_ping_posts_minimal_payload(self):
        with TemporaryDirectory() as temp_dir:
            metadata_path = Path(temp_dir) / "telemetry_installation.json"
            settings = TelemetrySettings(
                enabled=True,
                endpoint="https://example.com/startup",
                installation_id_path=metadata_path,
                timeout_seconds=1.5,
                project_name="Stock-trading-experiment",
                release="test-release",
                auth_token="secret-token",
            )
            captured: dict[str, object] = {}

            class _FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self, _: int = -1) -> bytes:
                    return b""

            def _fake_urlopen(request, timeout):
                captured["url"] = request.full_url
                captured["method"] = request.get_method()
                captured["timeout"] = timeout
                captured["headers"] = dict(request.header_items())
                captured["payload"] = json.loads(request.data.decode("utf-8"))
                return _FakeResponse()

            with patch("services.telemetry.urlopen", side_effect=_fake_urlopen):
                sent = send_startup_ping("worker", settings=settings)

            self.assertTrue(sent)
            self.assertEqual(captured["url"], "https://example.com/startup")
            self.assertEqual(captured["method"], "POST")
            self.assertEqual(captured["timeout"], 1.5)

            headers = captured["headers"]
            self.assertIsInstance(headers, dict)
            self.assertEqual(headers["Authorization"], "Bearer secret-token")

            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            self.assertEqual(payload["event"], "startup")
            self.assertEqual(payload["runtime"], "worker")
            self.assertEqual(payload["project"], "Stock-trading-experiment")
            self.assertEqual(payload["release"], "test-release")
            self.assertTrue(payload["install_id"])
            self.assertTrue(payload["install_created_at"])
            self.assertTrue(payload["started_at"])

    def test_send_startup_ping_returns_false_when_disabled(self):
        with TemporaryDirectory() as temp_dir:
            settings = TelemetrySettings(
                enabled=False,
                endpoint="https://example.com/startup",
                installation_id_path=Path(temp_dir) / "telemetry_installation.json",
                timeout_seconds=1.0,
                project_name="Stock-trading-experiment",
                release="",
                auth_token="",
            )

            with patch("services.telemetry.urlopen") as mocked_urlopen:
                sent = send_startup_ping("worker", settings=settings)

            self.assertFalse(sent)
            mocked_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
