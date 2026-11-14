#!/usr/bin/env python3
"""Canonical local-development commands for the project.

This deliberately dispatches to existing entrypoints rather than replacing
them, so legacy imports and Docker commands remain compatible.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

RUNTIME_ENTRYPOINTS = {
    "api": ROOT_DIR / "api.py",
    "worker": ROOT_DIR / "Python Scripts" / "main.py",
    "news": ROOT_DIR / "Python Scripts" / "news_collector_main.py",
    "option-manager": ROOT_DIR / "Python Scripts" / "option_manager_main.py",
    "eod-sync": ROOT_DIR / "Python Scripts" / "marketdata_eod_sync_main.py",
    "offline-evaluation": ROOT_DIR / "Python Scripts" / "agentCallers" / "offline_manager_evaluation.py",
    "backtest-sweep": ROOT_DIR / "Python Scripts" / "agentCallers" / "backtest_sweep.py",
    "llm-backtest": ROOT_DIR / "Python Scripts" / "agentCallers" / "llm_backtest.py",
}


def _run(command: list[str], *, cwd: Path = ROOT_DIR) -> int:
    return subprocess.run(command, cwd=cwd, check=False).returncode


# Where testmon records which code each test executed; written by `test --changed`.
TESTMON_DATA = ROOT_DIR / ".testmondata"
TEST_INPUTS_FINGERPRINT = ROOT_DIR / ".testmon-inputs.json"
# Files the code reads at run time. testmon only sees executed Python, so a change to any of
# these cannot be traced to the tests it affects; it re-runs every test once and re-records.
NON_PYTHON_TEST_INPUTS = ("*.sql", "config/*", "Data/*.json", ".env.example", "pyproject.toml", "requirements.txt",
                          "constraints.txt")


def _test_inputs_fingerprint() -> str | None:
    """Content hash of the non-Python test inputs, or None when git cannot list them."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *NON_PYTHON_TEST_INPUTS],
        cwd=ROOT_DIR, capture_output=True, check=False,
    )
    if listed.returncode != 0:
        return None
    digest = hashlib.sha256()
    for name in sorted({path for path in listed.stdout.decode().split("\0") if path}):
        path = ROOT_DIR / name
        if path.is_file():
            digest.update(name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _run_changed_tests() -> int:
    """Run only the tests whose executed code changed since the last recorded run."""
    if importlib.util.find_spec("testmon") is None:
        print('Selecting changed tests needs the dev extras: pip install -e ".[dev]"', file=sys.stderr)
        return 2
    fingerprint = _test_inputs_fingerprint()
    try:
        recorded = json.loads(TEST_INPUTS_FINGERPRINT.read_text(encoding="utf-8"))["fingerprint"]
    except (OSError, ValueError, KeyError):
        recorded = None
    if not TESTMON_DATA.exists():
        reason = "no recorded test map yet"
    elif fingerprint is None:
        reason = "git could not list the schema and configuration files"
    elif fingerprint != recorded:
        reason = "schema, configuration or dependency files changed"
    else:
        reason = ""
    if reason:
        print(f"Running every test and re-recording: {reason}.", file=sys.stderr)
    command = [PYTHON, "-m", "pytest", "--testmon-noselect" if reason else "--testmon", "-q"]
    if importlib.util.find_spec("xdist") is not None:
        command += ["-n", "auto"]
    code = _run(command)
    # 0 and 1 are completed runs; testmon itself keeps selecting tests that failed until they pass.
    if fingerprint is not None and code in (0, 1):
        TEST_INPUTS_FINGERPRINT.write_text(json.dumps({"fingerprint": fingerprint}) + "\n", encoding="utf-8")
    return code


def _test_command(modules: list[str]) -> list[str]:
    # Tests import as the `tests` package, whose __init__ isolates the environment first;
    # each parallel worker process gets its own throwaway database that way.
    if modules:
        return [PYTHON, "-m", "unittest", *modules]
    if importlib.util.find_spec("xdist") is not None:
        return [PYTHON, "-m", "pytest", "-n", "auto", "-q"]
    print('Running tests one at a time. For parallel runs: pip install -e ".[dev]"', file=sys.stderr)
    return [PYTHON, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_*.py"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project entrypoint dispatcher.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a supported Python entrypoint.")
    run_parser.add_argument("target", choices=sorted(RUNTIME_ENTRYPOINTS))
    run_parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to the target entrypoint.")

    test_parser = subparsers.add_parser("test", help="Run the Python test suite or selected test modules.")
    test_parser.add_argument("modules", nargs="*", help="Optional unittest modules, for example tests.test_exposure_map.")
    test_parser.add_argument("--changed", action="store_true",
                             help="Run only tests affected by changes since the last --changed run (needs the dev extras).")
    test_parser.add_argument("--e2e", action="store_true",
                             help="Run the end-to-end trading-cycle tests in tests/e2e (skipped by a plain run).")
    test_parser.add_argument("--live-market", action="store_true",
                             help="Run one cycle against real Alpaca and yfinance and report rate limits (tests/live; "
                                  "reads the Alpaca keys from .env, places no orders, makes no model calls).")

    dashboard_parser = subparsers.add_parser("dashboard", help="Run a dashboard development command.")
    dashboard_parser.add_argument("target", choices=("dev", "build"))

    subparsers.add_parser("demo-url", help="Print the shareable seeded-demo URL fragment.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "run":
        return _run([PYTHON, str(RUNTIME_ENTRYPOINTS[args.target]), *args.args])
    if args.command == "test":
        if importlib.util.find_spec("db_common") is None:
            print("The project is not installed. Run: pip install -e .", file=sys.stderr)
            return 2
        if args.changed:
            if args.modules:
                print("--changed selects tests itself; do not also name modules.", file=sys.stderr)
                return 2
            return _run_changed_tests()
        if args.live_market:
            os.environ["RUN_LIVE_MARKET"] = "1"
            return _run([PYTHON, "-m", "unittest", "discover", "-s", "tests/live", "-t", ".", "-p", "test_*.py"])
        if args.e2e:
            os.environ["RUN_E2E"] = "1"
            return _run([PYTHON, "-m", "unittest", "discover", "-s", "tests/e2e", "-t", ".", "-p", "test_*.py"])
        return _run(_test_command(args.modules))
    if args.command == "dashboard":
        return _run(["npm", "run", args.target], cwd=ROOT_DIR / "web_dashboard")
    if args.command == "demo-url":
        print("#/exposure-map?demo=1")
        return 0
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
