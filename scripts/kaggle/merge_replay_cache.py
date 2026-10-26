"""Move cached LLM replay answers between databases, so a backtest answered on Kaggle replays at home.

    export --db DB --out FILE [--manifest manifest.json]   every llm_replay_cache row, as JSON
    import FILE --db DB                                     INSERT OR IGNORE; never overwrites an answer

A cached answer is found again only under the same (stage, provider, model, prompt_version,
input_hash), so `import` warns when the export came from another commit or prompt version.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

COLUMNS = ("stage", "provider", "model", "prompt_version", "input_hash", "output_json", "created_at")
ROOT_DIR = Path(__file__).resolve().parents[2]


def connect(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).is_file():
        sys.exit(f"No database at {db_path}")
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def export_rows(db_path: str, out: str, manifest_path: str | None) -> None:
    with closing(connect(db_path)) as conn:
        rows = [dict(row) for row in conn.execute(f"SELECT {', '.join(COLUMNS)} FROM llm_replay_cache ORDER BY id")]
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8")) if manifest_path else {}
    export = {"exported_at": datetime.now(timezone.utc).isoformat(), "manifest": manifest, "rows": rows}
    Path(out).write_text(json.dumps(export), encoding="utf-8")
    print(f"Exported {len(rows)} cached answers to {out}")
    print_counts(rows)


def import_rows(path: str, db_path: str) -> None:
    export = json.loads(Path(path).read_text(encoding="utf-8"))
    warn_on_version_drift(export.get("manifest") or {})
    rows = export["rows"]
    with closing(connect(db_path)) as conn, conn:
        before = conn.execute("SELECT COUNT(*) FROM llm_replay_cache").fetchone()[0]
        conn.executemany(
            f"INSERT OR IGNORE INTO llm_replay_cache ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
            [tuple(row[column] for column in COLUMNS) for row in rows])
        after = conn.execute("SELECT COUNT(*) FROM llm_replay_cache").fetchone()[0]
    print(f"Imported {after - before} new cached answers ({len(rows) - (after - before)} already present) into {db_path}")
    print_counts(rows)


def print_counts(rows: list[dict]) -> None:
    for (stage, provider, model, version), count in sorted(Counter(
            (row["stage"], row["provider"], row["model"], row["prompt_version"]) for row in rows).items()):
        print(f"  {count:>6}  {stage:<10} {provider:<10} {model:<28} {version}")


def warn_on_version_drift(manifest: dict) -> None:
    """The cache only hits under the prompt versions it was written with; say so before importing."""
    if not manifest:
        print("Note: the export carries no manifest, so its commit and prompt versions cannot be checked.")
        return
    local = local_versions()
    for key, theirs in (("git_commit", manifest.get("git_commit")),
                        ("manager_prompt_version", manifest.get("manager_prompt_version")),
                        ("strategist_prompt_version", manifest.get("strategist_prompt_version"))):
        ours = local.get(key)
        if theirs and ours and theirs != ours:
            consequence = ("its prompts may differ from this checkout's" if key == "git_commit"
                           else "these answers will not be cache hits here")
            print(f"WARNING: export {key} is {theirs}, this checkout has {ours}; {consequence}.")


def local_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, capture_output=True, text=True, check=False)
    if commit.returncode == 0:
        versions["git_commit"] = commit.stdout.strip()
    try:  # Needs the project installed (pip install -e .); without it only the commit is compared.
        from services.settings import load_process_settings
        load_process_settings()
        from agent_stages.manager_runner import MANAGER_PROMPT_VERSION
        from agent_stages.strategist_runner import strategist_prompt_version
        from services.evidence_thesis_policy import EvidenceThesisPolicy
        versions["manager_prompt_version"] = MANAGER_PROMPT_VERSION
        versions["strategist_prompt_version"] = strategist_prompt_version(EvidenceThesisPolicy.from_env())
    except ImportError:
        pass
    return versions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Write every llm_replay_cache row to a JSON file.")
    export.add_argument("--db", required=True)
    export.add_argument("--out", required=True)
    export.add_argument("--manifest", help="The bundle's manifest.json, recorded so `import` can check versions.")
    imported = commands.add_parser("import", help="Add an export's rows to a database, keeping existing answers.")
    imported.add_argument("file")
    imported.add_argument("--db", required=True)
    args = parser.parse_args()
    if args.command == "export":
        export_rows(args.db, args.out, args.manifest)
    else:
        import_rows(args.file, args.db)


if __name__ == "__main__":
    main()
