from __future__ import annotations

import importlib
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


class ApiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            api_module = importlib.import_module("api")
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                raise unittest.SkipTest("Flask is not installed in this environment.") from exc
            raise

        cls.api_module = api_module
        cls.app = api_module.app
        cls.client = cls.app.test_client()

    def test_api_module_exposes_flask_app(self):
        self.assertIsNotNone(self.app)
        self.assertEqual(self.app.import_name, "api")

    def test_health_route_returns_ok_json(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")


if __name__ == "__main__":
    unittest.main()
