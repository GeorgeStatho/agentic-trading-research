from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services.runtime.telemetry import TelemetrySettings, send_startup_ping


class TelemetryTests(unittest.TestCase):
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

            with patch("services.runtime.telemetry.urlopen") as mocked_urlopen:
                sent = send_startup_ping("worker", settings=settings)

            self.assertFalse(sent)
            mocked_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
