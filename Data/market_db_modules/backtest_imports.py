from __future__ import annotations

from contextlib import closing
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from db_common import clean_text, get_connection, json_text

from .bootstrap import initialize_database
from services.core.numbers import safe_float


_HEADER_ALIASES = {
    "option_symbol": ("symbol", "option_symbol", "option_contract", "option_contract_symbol", "contract"),
    "quote_at": ("timestamp", "quote_at", "datetime", "time", "t"),
    "bid_price": ("bid_price", "bid", "bp"),
    "ask_price": ("ask_price", "ask", "ap"),
    "last_price": ("last_price", "last", "price", "lp"),
    "volume": ("volume", "v"),
    "open_interest": ("open_interest", "oi"),
    "implied_volatility": ("implied_volatility", "iv"),
    "delta": ("delta",),
    "gamma": ("gamma",),
    "theta": ("theta",),
    "vega": ("vega",),
}


def _normalized_header(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _normalized_option_symbol(value: Any) -> str | None:
    symbol = clean_text(value)
    if symbol is None:
        return None
    return symbol.upper().removeprefix("O:")


def _normalized_timestamp(value: Any) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    candidate = text.replace("Z", "+00:00")
    fraction_match = re.search(r"\.(\d{7,})(?=[+-]\d\d:\d\d$)", candidate)
    if fraction_match is not None:
        candidate = f"{candidate[:fraction_match.start() + 1]}{fraction_match.group(1)[:6]}{candidate[fraction_match.end():]}"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _field_map(fieldnames: Iterable[str] | None) -> dict[str, str]:
    fields = {_normalized_header(name): str(name) for name in fieldnames or [] if name}
    mapping: dict[str, str] = {}
    for target, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            if alias in fields:
                mapping[target] = fields[alias]
                break
    missing = [name for name in ("option_symbol", "quote_at", "bid_price", "ask_price") if name not in mapping]
    if missing:
        raise ValueError(
            "Alpaca quote CSV is missing required columns: "
            f"{', '.join(missing)}. Expected symbol/timestamp/bid_price/ask_price (or supported aliases)."
        )
    return mapping


def _quote_from_row(row: dict[str, str], field_map: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    option_symbol = _normalized_option_symbol(row.get(field_map["option_symbol"]))
    quote_at = _normalized_timestamp(row.get(field_map["quote_at"]))
    bid_price = safe_float(row.get(field_map["bid_price"]))
    ask_price = safe_float(row.get(field_map["ask_price"]))
    if option_symbol is None:
        return None, "missing_option_symbol"
    if quote_at is None:
        return None, "invalid_timestamp"
    if bid_price is None or ask_price is None or bid_price <= 0 or ask_price <= 0 or ask_price < bid_price:
        return None, "invalid_bid_ask"

    quote: dict[str, Any] = {
        "option_symbol": option_symbol,
        "quote_at": quote_at,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "raw_csv_row": row,
    }
    for field_name in ("last_price", "volume", "open_interest", "implied_volatility", "delta", "gamma", "theta", "vega"):
        source_name = field_map.get(field_name)
        if source_name is not None:
            quote[field_name] = safe_float(row.get(source_name))
    return quote, None


def _quote_params(quote: dict[str, Any], provider: str) -> tuple[Any, ...]:
    return (
        quote["option_symbol"], quote["quote_at"], quote.get("bid_price"), quote.get("ask_price"), quote.get("last_price"),
        quote.get("volume"), quote.get("open_interest"), quote.get("implied_volatility"), quote.get("delta"),
        quote.get("gamma"), quote.get("theta"), quote.get("vega"), provider, json_text(quote),
    )


def import_alpaca_option_quote_csv(
    csv_path: Path | str,
    *,
    provider: str = "alpaca_csv",
    db_path: Path | str | None = None,
    batch_size: int = 1_000,
    dry_run: bool = False,
    max_error_samples: int = 25,
) -> dict[str, Any]:
    """Import an Alpaca historical option-quote CSV without contacting any service.

    The importer accepts common Alpaca column names, including ``symbol``,
    ``timestamp``, ``bid_price`` and ``ask_price``. It streams the file, stores
    only valid two-sided quotes, and preserves every accepted source row in
    ``raw_json`` for auditability.
    """
    source_path = Path(csv_path).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"Historical quote CSV was not found: {source_path}")
    normalized_provider = clean_text(provider)
    if normalized_provider is None:
        raise ValueError("provider is required.")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1.")
    if max_error_samples < 0:
        raise ValueError("max_error_samples cannot be negative.")

    result: dict[str, Any] = {
        "source_path": str(source_path),
        "provider": normalized_provider,
        "dry_run": dry_run,
        "read_only": True,
        "processed_rows": 0,
        "valid_rows": 0,
        "inserted_rows": 0,
        "duplicate_rows": 0,
        "skipped_rows": 0,
        "error_samples": [],
        "first_quote_at": None,
        "last_quote_at": None,
    }

    initialize_database(db_path=db_path)
    insert_sql = """
        INSERT INTO historical_option_quotes (
            option_symbol, quote_at, bid_price, ask_price, last_price, volume,
            open_interest, implied_volatility, delta, gamma, theta, vega, provider, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(option_symbol, quote_at, provider) DO NOTHING
    """

    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        field_map = _field_map(reader.fieldnames)
        batch: list[tuple[Any, ...]] = []

        def flush(conn) -> None:
            if not batch or dry_run:
                batch.clear()
                return
            changes_before = conn.total_changes
            conn.executemany(insert_sql, batch)
            inserted = conn.total_changes - changes_before
            result["inserted_rows"] += inserted
            result["duplicate_rows"] += len(batch) - inserted
            batch.clear()

        with closing(get_connection(db_path)) as conn, conn:
            for line_number, row in enumerate(reader, start=2):
                if not any(clean_text(value) is not None for value in row.values()):
                    continue
                result["processed_rows"] += 1
                quote, error = _quote_from_row(row, field_map)
                if quote is None:
                    result["skipped_rows"] += 1
                    if len(result["error_samples"]) < max_error_samples:
                        result["error_samples"].append({"line": line_number, "reason": error})
                    continue
                result["valid_rows"] += 1
                quote_at = quote["quote_at"]
                if result["first_quote_at"] is None or quote_at < result["first_quote_at"]:
                    result["first_quote_at"] = quote_at
                if result["last_quote_at"] is None or quote_at > result["last_quote_at"]:
                    result["last_quote_at"] = quote_at
                batch.append(_quote_params(quote, normalized_provider))
                if len(batch) >= batch_size:
                    flush(conn)
            flush(conn)

    if dry_run:
        result["inserted_rows"] = 0
        result["duplicate_rows"] = 0
    return result
