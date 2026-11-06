"""Builds install the dependency versions that were tested, not whatever was released last."""
import re
import unittest

from tests import ROOT_DIR


def _names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-"))
    return names


def _pins(text: str) -> dict[str, str]:
    pins = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)$", line.strip())
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


class DependencyPinTests(unittest.TestCase):
    def test_every_direct_dependency_has_an_exact_pin(self):
        requirements = _names((ROOT_DIR / "requirements.txt").read_text(encoding="utf-8"))
        pins = _pins((ROOT_DIR / "constraints.txt").read_text(encoding="utf-8"))

        self.assertEqual(set(), requirements - set(pins))

    def test_the_test_runners_are_pinned_too(self):
        pins = _pins((ROOT_DIR / "constraints.txt").read_text(encoding="utf-8"))

        self.assertTrue({"pytest", "pytest-xdist"} <= set(pins))

    def test_the_worker_image_installs_through_the_constraints(self):
        dockerfile = (ROOT_DIR / "Dockerfile.worker").read_text(encoding="utf-8")

        self.assertIn("COPY requirements.txt constraints.txt", dockerfile)
        self.assertRegex(dockerfile, r"pip wheel [^\n]*-r requirements\.txt -c constraints\.txt")
        self.assertRegex(dockerfile, r"pip install [^\n]*pytest[^\n]*-c constraints\.txt")

    def test_the_dashboard_image_installs_the_lockfile(self):
        dockerfile = (ROOT_DIR / "web_dashboard" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("npm ci", dockerfile)
        self.assertNotIn("npm install", dockerfile)


if __name__ == "__main__":
    unittest.main()
