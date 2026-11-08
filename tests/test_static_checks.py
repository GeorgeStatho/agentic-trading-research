"""The Python code passes the project's ruff rules (configured in pyproject.toml)."""
import shutil
import subprocess
import unittest

from tests import ROOT_DIR


@unittest.skipIf(shutil.which("ruff") is None, "ruff is not installed (dev extras)")
class StaticCheckTests(unittest.TestCase):
    def test_ruff_finds_nothing(self):
        result = subprocess.run(["ruff", "check", "--output-format", "concise", "."], cwd=ROOT_DIR,
                                capture_output=True, text=True, check=False)

        self.assertEqual(0, result.returncode, result.stdout[-4000:] + result.stderr[-1000:])


if __name__ == "__main__":
    unittest.main()
