"""The Kaggle bundle's table-read trace replays a fake model, which no cutoff table can list."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kaggle" / "prepare_kaggle_bundle.py"


class TraceChildTests(unittest.TestCase):
    def trace_calls(self) -> dict:
        spec = importlib.util.spec_from_file_location("prepare_kaggle_bundle", SCRIPT)
        bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bundle)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sqlite3, "connect", sqlite3.connect), \
                patch("services.settings.load_process_settings"), \
                patch("services.llm_backtest.run_llm_backtest", return_value={}) as backtest, \
                patch("services.llm_direction_score.run_direction_score", return_value={}):
            bundle.trace_child(str(Path(directory) / "trace.db"), str(Path(directory) / "result.json"), None, None)
        return {call.kwargs["model"]: call.kwargs for call in backtest.call_args_list}

    def test_the_fake_model_trace_overrides_the_unknown_cutoff_guard(self) -> None:
        calls = self.trace_calls()
        self.assertTrue(calls["kaggle-trace-fake"].get("allow_unknown_cutoff"))

    def test_the_stored_trace_needs_no_override(self) -> None:
        self.assertFalse(self.trace_calls()["stored"].get("allow_unknown_cutoff", False))


if __name__ == "__main__":
    unittest.main()
