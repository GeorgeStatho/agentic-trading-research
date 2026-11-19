"""CI runs the same checks a developer runs, on the Python version the worker image uses."""
import json
import re
import unittest

from tests import ROOT_DIR


WORKFLOW = ROOT_DIR / ".github" / "workflows" / "ci.yml"
DASHBOARD_DOCKERFILE = ROOT_DIR / "web_dashboard" / "Dockerfile"
PACKAGE_LOCK = ROOT_DIR / "web_dashboard" / "package-lock.json"


def _major_allowed(node_range: str, major: int) -> bool | None:
    """Whether some `major`.x version can satisfy an npm engines range; None when the range is not understood.

    shortcut: compares majors only (a `^22.22.2` floor passes on any 22.x image), enough to catch a stale pin.
    """
    for clause in node_range.split("||"):
        ok = True
        for token in clause.split():
            match = re.fullmatch(r"(\^|~|>=|>|<=|<|=)?v?(\d+)(?:\.(\d+|x))?(?:\.(\d+|x))?", token)
            if token in ("*", "x"):
                continue
            if match is None:
                return None
            op, target = match.group(1) or "=", int(match.group(2))
            exact_floor = all(part in (None, "0", "x") for part in match.group(3, 4))
            ok &= {"^": major == target, "~": major == target, "=": major == target, ">=": major >= target,
                   ">": major >= target, "<=": major <= target,
                   "<": major < target if exact_floor else major <= target}[op]
        if ok:
            return True
    return False


class ContinuousIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKFLOW.exists(), "add .github/workflows/ci.yml")
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_on_every_push_and_pull_request(self):
        self.assertRegex(self.workflow, r"(?m)^on:\s*\n(?:\s+.*\n)*?\s+push:")
        self.assertRegex(self.workflow, r"(?m)^\s+pull_request:")

    def test_runs_the_python_checks_with_pinned_dependencies(self):
        for command in ('pip install -e ".[dev]" -c constraints.txt', "ruff check", "scripts/project.py test",
                        "scripts/project.py test --e2e"):
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


    def test_the_dashboard_node_version_runs_every_locked_package(self):
        # Node 20 lacked zlib's zstd support that undici 8 (via jsdom) needs: every test file failed to start.
        node_major = int(re.search(r"FROM node:(\d+)", DASHBOARD_DOCKERFILE.read_text(encoding="utf-8")).group(1))
        packages = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))["packages"]
        refused = sorted(
            f"{name.removeprefix('node_modules/')} needs node {info['engines']['node']}"
            for name, info in packages.items()
            if isinstance(info.get("engines"), dict) and isinstance(info["engines"].get("node"), str)
            and _major_allowed(info["engines"]["node"], node_major) is False
        )
        self.assertEqual([], refused, f"node {node_major}")


if __name__ == "__main__":
    unittest.main()
