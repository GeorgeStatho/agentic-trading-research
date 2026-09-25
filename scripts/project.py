#!/usr/bin/env python3
"""Canonical local-development commands for the project.

This deliberately dispatches to existing entrypoints rather than replacing
them, so legacy imports and Docker commands remain compatible.
"""

from __future__ import annotations

import argparse
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
}


def _run(command: list[str], *, cwd: Path = ROOT_DIR) -> int:
    return subprocess.run(command, cwd=cwd, check=False).returncode


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project entrypoint dispatcher.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a supported Python entrypoint.")
    run_parser.add_argument("target", choices=sorted(RUNTIME_ENTRYPOINTS))
    run_parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to the target entrypoint.")

    test_parser = subparsers.add_parser("test", help="Run the Python test suite or selected test modules.")
    test_parser.add_argument("modules", nargs="*", help="Optional unittest modules, for example tests.test_exposure_map.")

    dashboard_parser = subparsers.add_parser("dashboard", help="Run a dashboard development command.")
    dashboard_parser.add_argument("target", choices=("dev", "build"))

    subparsers.add_parser("demo-url", help="Print the shareable seeded-demo URL fragment.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "run":
        return _run([PYTHON, str(RUNTIME_ENTRYPOINTS[args.target]), *args.args])
    if args.command == "test":
        command = [PYTHON, "-m", "unittest"]
        if args.modules:
            command.extend(args.modules)
        else:
            command.extend(["discover", "-s", "tests", "-p", "test_*.py", "-v"])
        return _run(command)
    if args.command == "dashboard":
        return _run(["npm", "run", args.target], cwd=ROOT_DIR / "web_dashboard")
    if args.command == "demo-url":
        print("#/exposure-map?demo=1")
        return 0
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
