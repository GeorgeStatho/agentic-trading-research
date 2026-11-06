"""CI runs the same checks a developer runs, on the Python version the worker image uses."""
import re
import unittest

from tests import ROOT_DIR


WORKFLOW = ROOT_DIR / ".github" / "workflows" / "ci.yml"
DASHBOARD_DOCKERFILE = ROOT_DIR / "web_dashboard" / "Dockerfile"


class ContinuousIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKFLOW.exists(), "add .github/workflows/ci.yml")
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_on_every_push_and_pull_request(self):
        self.assertRegex(self.workflow, r"(?m)^on:\s*\n(?:\s+.*\n)*?\s+push:")
        self.assertRegex(self.workflow, r"(?m)^\s+pull_request:")

    def test_runs_the_python_checks_with_pinned_dependencies(self):
        for command in ('pip install -e ".[dev]" -c constraints.txt', "ruff check", "scripts/project.py test"):
            with self.subTest(command=command):
                self.assertIn(command, self.workflow)

    def test_runs_the_dashboard_checks(self):
        for command in ("npm ci", "npm run lint", "npm test", "npm run build"):
            with self.subTest(command=command):
                self.assertIn(command, self.workflow)

    def test_uses_the_worker_image_python_version(self):
        dockerfile = (ROOT_DIR / "Dockerfile.worker").read_text(encoding="utf-8")
        image_version = re.search(r"FROM python:(\d+\.\d+)", dockerfile).group(1)

        self.assertRegex(self.workflow, rf"python-version:\s*[\"']?{re.escape(image_version)}[\"']?")

    @unittest.skipUnless(DASHBOARD_DOCKERFILE.exists(), "the worker image leaves out web_dashboard/")
    def test_uses_the_dashboard_image_node_version(self):
        dockerfile = DASHBOARD_DOCKERFILE.read_text(encoding="utf-8")
        node_version = re.search(r"FROM node:(\d+)", dockerfile).group(1)

        self.assertRegex(self.workflow, rf"node-version:\s*[\"']?{node_version}[\"']?")


if __name__ == "__main__":
    unittest.main()
