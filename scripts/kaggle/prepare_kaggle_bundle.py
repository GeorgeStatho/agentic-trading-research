"""Build the private Kaggle dataset that runs `llm-backtest` on Kaggle's GPUs.

Writes to --out (default ~/stock-restore/kaggle-bundle):
    code.zip                      `git archive HEAD`: tracked files only, so no .env or keys
    kaggle.env                    .env without credentials, hosts, providers, models or DB_PATH (you review it)
    stock_experiment.slim.db.zst  only the tables the backtest reads
    manifest.json                 commit, prompt versions, kept tables, GGUF sha256, llama-server flags
    merge_replay_cache.py         the notebook's answer export/import tool

The kept tables are found, not guessed: a working copy of the source DB is traced (every table any
connection reads) through a stored replay, a fake-model replay with the strategist replayed and
reused, and a stored direction score. The slim DB copies those tables from the untouched source,
then the same runs are repeated on a copy of it and must give identical results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any
import zipfile

ROOT_DIR = Path(__file__).resolve().parents[2]
HOME = Path.home()
DEFAULT_SOURCE = HOME / "stock-restore" / "db" / "stock_experiment.db"
DEFAULT_GGUF = Path("/mnt/d/Ollama/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf")
DEFAULT_LAUNCH = Path("/mnt/d/llama-prism/last-launch.bat")
FAKE_MODEL = "kaggle-trace-fake"
# Always kept: the answers to resume from, and the setup record that stops a migration re-running.
ALWAYS_KEPT = {"llm_replay_cache", "schema_bootstrap"}
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|CREDENTIAL|PRIVATE|AUTH|WEBHOOK|ALPACA|GOOGLE_|EMAIL|DSN|"
                         r"ENDPOINT|URL|HOST|TELEMETRY|USER_AGENT|_PROVIDER$|^LLM_PROVIDER$|_MODEL$|^DB_PATH$")
# Long opaque strings look like credentials; an address is personal data.
SECRET_VALUE = re.compile(r"[A-Za-z0-9_\-]{32,}|[^@\s]+@[^@\s]+\.\w+")
# Not request settings, or set by the notebook for Kaggle's server.
SERVER_ONLY_FLAGS = {"-m", "--host", "--port", "-np", "--parallel", "-c", "--ctx-size"}
VOLATILE_KEYS = {"id", "run_id", "backtest_run_id", "started_at", "completed_at", "created_at"}


def sanitized_env(env_path: Path) -> tuple[str, list[str]]:
    kept, dropped = [], []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.removeprefix("export ").split("=", 1)
        name = name.strip()
        if SECRET_NAME.search(name) or SECRET_VALUE.search(value):
            dropped.append(name)
        else:
            kept.append(f"{name}={value.strip()}")
    return "\n".join(kept) + "\n", dropped


def build_code_zip(out: Path) -> str:
    commit = git("rev-parse", "HEAD")
    if git("status", "--porcelain", "--untracked-files=no"):
        print("WARNING: uncommitted changes are NOT in code.zip (it is built from HEAD). Run the backtest at home "
              "from this same commit, or the prompt versions may differ and the answers will not be cache hits.")
    target = out / "code.zip"
    subprocess.run(["git", "archive", "--format=zip", "-o", str(target), "HEAD"], cwd=ROOT_DIR, check=True)
    with zipfile.ZipFile(target) as archive:
        for name in archive.namelist():
            leaked = Path(name).name == ".env" or (
                name.endswith(".json") and b'"private_key"' in archive.read(name))
            if leaked:
                target.unlink()
                sys.exit(f"Refusing to continue: {name} in code.zip looks like a secret. Untrack it first.")
    return commit


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT_DIR, capture_output=True, text=True, check=True).stdout.strip()


def launch_flags(launch: Path) -> list[str]:
    """The request-shaping flags of the local llama-server launch, without paths, ports or slot counts."""
    if not launch.is_file():
        return []
    command = next((line for line in launch.read_text(encoding="utf-8", errors="replace").splitlines()
                    if "llama-server" in line), "")
    tokens = re.findall(r'"[^"]*"|\S+', command)
    tokens = tokens[next((i for i, token in enumerate(tokens) if "llama-server" in token), -1) + 1:]
    flags, skip = [], False
    for token in tokens:
        if skip:
            skip = False
        elif token in SERVER_ONLY_FLAGS:
            skip = True
        elif token != "--kv-unified":
            flags.append(token.strip('"'))
    return flags


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 24):
            digest.update(chunk)
    return digest.hexdigest()


def trace(db_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Run the three traced replays against `db_path` in a fresh process, so DB_PATH is set before import."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        result_path = Path(handle.name)
    command = [sys.executable, __file__, "--trace-child", str(db_path), str(result_path)]
    if args.start_at:
        command += ["--start-at", args.start_at]
    if args.limit:
        command += ["--limit", str(args.limit)]
    subprocess.run(command, cwd=ROOT_DIR, check=True, env={**os.environ, "DB_PATH": str(db_path)})
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result_path.unlink()
    return result


def trace_child(db_path: str, result_path: str, start_at: str | None, limit: int | None) -> None:
    from services.settings import load_process_settings
    load_process_settings()

    read, written, databases = set(), set(), set()

    def authorizer(action: int, first: str | None, _second, _database, _source) -> int:
        if action == sqlite3.SQLITE_READ and first:
            read.add(first)
        elif action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and first:
            written.add(first)
        return sqlite3.SQLITE_OK

    original_connect = sqlite3.connect

    def traced_connect(database, *connect_args, **connect_kwargs):
        conn = original_connect(database, *connect_args, **connect_kwargs)
        name = str(database)
        if name not in ("", ":memory:"):  # read-only callers open the same file as a file: URI
            databases.add(str(Path(name.removeprefix("file:").split("?", 1)[0]).resolve()))
        conn.set_authorizer(authorizer)
        return conn

    sqlite3.connect = traced_connect
    from services.llm_backtest import input_hash, run_llm_backtest
    from services.llm_direction_score import run_direction_score
    from services.option_dte_buckets import OPTION_DTE_BUCKETS

    buckets = [bucket.key for bucket in OPTION_DTE_BUCKETS]

    def pick(payload: dict[str, Any], options: list[str]) -> str:
        return options[int(input_hash(payload)[:8], 16) % len(options)]

    def fake_manager(payload: dict[str, Any]) -> dict[str, Any]:
        # Directional and high confidence, so selection, gates and the trade engine all run.
        return {"recommendation": {"decision": pick(payload, ["call", "put", "neither"]), "confidence": "high",
                                   "target_dte_bucket": pick(payload, buckets), "reason": "trace"}}

    def fake_strategist(payload: dict[str, Any]) -> dict[str, Any]:
        direction = pick(payload, ["call", "put"])
        return {"recommendation": {"decision": "trade_candidate", "confidence": "high",
                                   "preferred_option_direction": direction,
                                   "expected_stock_direction": "up" if direction == "call" else "down",
                                   "summary": "trace"}}

    window = {"start_at": start_at, "limit": limit, "db_path": db_path}
    results = {
        "stored": run_llm_backtest(model="stored", **window),
        "fake_model": run_llm_backtest(model=FAKE_MODEL, replay_strategist=True, strategist_reuse_max_age_minutes=120,
                                       decide=fake_manager, decide_strategist=fake_strategist, **window),
        "direction_score": run_direction_score(model="stored", **window),
    }
    Path(result_path).write_text(json.dumps({
        "read": sorted(read), "written": sorted(written), "databases": sorted(databases),
        "results": json.loads(json.dumps(results, default=str)),
    }), encoding="utf-8")


def without_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_volatile(item) for key, item in value.items() if key not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [without_volatile(item) for item in value]
    return value


def build_slim(source: Path, target: Path, tables: set[str]) -> None:
    """Same schema as `source`; rows only for `tables`. Indexes and triggers come after the rows."""
    target.unlink(missing_ok=True)
    conn = sqlite3.connect(target, uri=True, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("ATTACH DATABASE ? AS src", (f"file:{source}?mode=ro&immutable=1",))
        schema = conn.execute("SELECT type, name, sql FROM src.sqlite_master "
                              "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
        order = {"table": 0, "index": 1, "trigger": 2, "view": 3}
        conn.execute("BEGIN")
        for kind, _name, sql in sorted(schema, key=lambda row: order[row[0]]):
            if kind == "table":
                conn.execute(sql)
        for kind, name, _sql in schema:
            if kind == "table" and name in tables:
                conn.execute(f'INSERT INTO main."{name}" SELECT * FROM src."{name}"')
        for kind, _name, sql in sorted(schema, key=lambda row: order[row[0]]):
            if kind != "table":
                conn.execute(sql)
        conn.execute("COMMIT")
        conn.execute("DETACH DATABASE src")
        conn.execute("PRAGMA journal_mode = DELETE")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="DB to slim; opened read-only and immutable.")
    parser.add_argument("--out", type=Path, default=HOME / "stock-restore" / "kaggle-bundle")
    parser.add_argument("--work", type=Path, default=HOME / "stock-restore" / "kaggle-work",
                        help="Scratch for the full working copy (needs the source's size free; not a tmpfs).")
    parser.add_argument("--gguf", type=Path, default=DEFAULT_GGUF, help="Local model file, hashed for the manifest.")
    parser.add_argument("--launch", type=Path, default=DEFAULT_LAUNCH, help="Local llama-server launch script.")
    parser.add_argument("--start-at", default="", help="Trace only decisions from here (default: all).")
    parser.add_argument("--limit", type=int, help="Trace only this many most recent decisions (default: all).")
    parser.add_argument("--yes", action="store_true", help="Do not stop to confirm kaggle.env.")
    parser.add_argument("--keep-work", action="store_true", help="Keep the working copies for inspection.")
    parser.add_argument("--trace-child", nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.trace_child:
        trace_child(*args.trace_child, args.start_at or None, args.limit)
        return

    args.out.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    env_text, dropped = sanitized_env(ROOT_DIR / ".env")
    print(f"kaggle.env keeps these settings (dropped {len(dropped)}: {', '.join(sorted(dropped))}):\n")
    print(env_text)
    if not args.yes and input("Upload these settings to Kaggle? [y/N] ").strip().lower() != "y":
        sys.exit("Stopped. Edit SECRET_NAME in this script, or your .env, and run again.")
    (args.out / "kaggle.env").write_text(env_text, encoding="utf-8")
    commit = build_code_zip(args.out)
    print(f"code.zip built from {commit}")
    # The notebook's export/import tool travels beside the code, so it is there even if not yet committed.
    shutil.copyfile(Path(__file__).with_name("merge_replay_cache.py"), args.out / "merge_replay_cache.py")

    full = args.work / "full.db"
    print(f"Copying {args.source} to {full} for tracing ...")
    shutil.copyfile(args.source, full)
    full_trace = trace(full, args)
    if full_trace["databases"] != [str(full.resolve())]:
        sys.exit(f"Trace touched other databases than the working copy: {full_trace['databases']}")
    tables = set(full_trace["read"]) - {"sqlite_master", "sqlite_sequence"} | ALWAYS_KEPT
    print(f"The backtest read {len(tables)} tables: {', '.join(sorted(tables))}")

    slim = args.out / "stock_experiment.slim.db"
    print(f"Building {slim} ...")
    build_slim(args.source, slim, tables)
    check = args.work / "slim-check.db"
    shutil.copyfile(slim, check)
    slim_trace = trace(check, args)
    unexpected = set(slim_trace["read"]) - tables - {"sqlite_master", "sqlite_sequence"}
    mismatched = [name for name in full_trace["results"]
                  if without_volatile(full_trace["results"][name]) != without_volatile(slim_trace["results"][name])]
    if unexpected or mismatched:
        sys.exit(f"Slim DB is not equivalent: read untraced tables {sorted(unexpected)}, differing runs {mismatched}. "
                 f"Kept {args.work} for inspection.")
    print("Slim DB gives identical stored, fake-model and direction-score results.")

    from agent_stages.manager_runner import MANAGER_PROMPT_VERSION
    from agent_stages.strategist_runner import strategist_prompt_version
    from services.evidence_thesis_policy import EvidenceThesisPolicy
    from services.settings import load_process_settings
    load_process_settings()
    print(f"Hashing {args.gguf} ...")
    manifest = {
        "git_commit": commit,
        "manager_prompt_version": MANAGER_PROMPT_VERSION,
        "strategist_prompt_version": strategist_prompt_version(EvidenceThesisPolicy.from_env()),
        "kept_tables": sorted(tables),
        "dropped_tables": sorted(row[0] for row in sqlite3.connect(f"file:{args.source}?mode=ro&immutable=1", uri=True)
                                 .execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                                 if row[0] not in tables and not row[0].startswith("sqlite_")),
        "gguf_file": args.gguf.name,
        "gguf_sha256": sha256_file(args.gguf),
        "llama_server_flags": launch_flags(args.launch),
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("Compressing ...")
    subprocess.run(["zstd", "-T0", "-12", "--long=27", "-f", "--rm", str(slim)], check=True)
    if not args.keep_work:
        shutil.rmtree(args.work)
    sizes = {path.name: f"{path.stat().st_size / 1e9:.2f} GB" for path in sorted(args.out.iterdir())}
    print(f"Bundle ready in {args.out}: {sizes}")


if __name__ == "__main__":
    main()
