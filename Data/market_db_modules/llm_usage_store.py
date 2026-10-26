"""Per-call usage of hosted models: tokens, the upstream provider that answered, and its reported cost."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_common import get_connection
from .bootstrap import initialize_database

USAGE_COLUMNS = ("model", "served_model", "upstream_provider", "generation_id", "prompt_tokens",
                 "completion_tokens", "reasoning_tokens", "cached_tokens", "cost_usd")


def record_llm_call_usage(usage: dict[str, Any], *, db_path: Path | str | None = None) -> None:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute(
            f"INSERT INTO llm_call_usage (called_at, {', '.join(USAGE_COLUMNS)}) "
            f"VALUES (?, {', '.join('?' for _ in USAGE_COLUMNS)})",
            (datetime.now(timezone.utc).isoformat(), *(usage.get(column) for column in USAGE_COLUMNS)),
        )
