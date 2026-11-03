"""Pack candidate snapshots and compress cached provider responses in place, then VACUUM.

Stop the Python services first: the rewrite holds the write lock batch by batch and VACUUM needs
the database to itself. Without --apply this is a dry run that packs a sample and rolls it back.
Every rewritten row is checked to rebuild its original hash before it replaces the stored text.
"""
import argparse
from contextlib import closing
from hashlib import sha256
import os
import sqlite3
import time
import zlib

from db_common import database_path, get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_collection import decode_response
from market_db_modules.compact_storage import encode_snapshot, unpack_json
from market_db_modules.foundation_store import verified_envelope
from Webscraping.company_evidence.sec import SECAdapter

SAMPLE_ROWS = 2000
# Stored provider responses are immutable; the rewrite lifts these within each batch's transaction.
RESPONSE_TRIGGERS = ("company_responses_no_update",)


def pack_snapshots(conn, *, batch, limit=None):
    stats = {"packed": 0, "kept_plain": 0, "bytes_before": 0, "bytes_after": 0}
    last_id = 0
    while limit is None or stats["packed"] + stats["kept_plain"] < limit:
        rows = conn.execute('''SELECT id, snapshot_json, snapshot_hash FROM candidate_snapshots
            WHERE snapshot_encoding IS NULL AND id > ? ORDER BY id LIMIT ?''', (last_id, batch)).fetchall()
        if not rows:
            break
        for row in rows:
            last_id = row["id"]
            text = row["snapshot_json"]
            if sha256(text.encode()).hexdigest() != row["snapshot_hash"]:
                raise ValueError(f"candidate_snapshots {row['id']} already fails its hash; not rewriting it")
            stored, encoding = encode_snapshot(conn, text)
            if encoding is None:
                stats["kept_plain"] += 1
                continue
            if sha256(unpack_json(conn, stored).encode()).hexdigest() != row["snapshot_hash"]:
                raise ValueError(f"candidate_snapshots {row['id']} did not rebuild its hash")
            conn.execute("UPDATE candidate_snapshots SET snapshot_json=?, snapshot_encoding=? WHERE id=?",
                         (stored, encoding, row["id"]))
            stats["packed"] += 1
            stats["bytes_before"] += len(text)
            stats["bytes_after"] += len(stored)
        if limit is None:
            conn.commit()
            print(f"  snapshots: {stats['packed']} packed, {stats['kept_plain']} kept plain (through id {last_id})")
    return stats


def compress_responses(conn, *, batch, limit=None):
    sec = SECAdapter(http=None, policy=None)
    triggers = [row["sql"] for row in conn.execute(
        f"SELECT sql FROM sqlite_master WHERE type='trigger' AND name IN ({','.join('?' for _ in RESPONSE_TRIGGERS)})",
        RESPONSE_TRIGGERS)]
    stats = {"compressed": 0, "stripped": 0, "bytes_before": 0, "bytes_after": 0}
    while limit is None or stats["compressed"] < limit:
        rows = conn.execute('''SELECT response_id, provider, body, content_hash FROM company_provider_responses
            WHERE body_z IS NULL LIMIT ?''', (batch,)).fetchall()
        if not rows:
            break
        # sqlite3 opens transactions only before DML; without this the DROP below would autocommit.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        for name in RESPONSE_TRIGGERS:
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        for row in rows:
            body = row["body"]
            if sha256(body.encode()).hexdigest() != row["content_hash"]:
                raise ValueError(f"company_provider_responses {row['response_id']} already fails its hash")
            # compact_filing keeps the body whole unless everything the SEC adapter reads is unchanged.
            stored = sec.compact_filing(body) if row["provider"] == SECAdapter.name else body
            compressed = zlib.compress(stored.encode(), 6)
            conn.execute('''UPDATE company_provider_responses SET body='', body_z=?, stored_hash=?
                WHERE response_id=?''', (compressed, sha256(stored.encode()).hexdigest(), row["response_id"]))
            stats["compressed"] += 1
            stats["stripped"] += stored != body
            stats["bytes_before"] += len(body)
            stats["bytes_after"] += len(compressed)
        for sql in triggers:
            conn.execute(sql)
        if limit is None:
            conn.commit()
            print(f"  responses: {stats['compressed']} compressed, {stats['stripped']} had attachments removed")
    return stats


def verify(conn, *, batch):
    snapshots = responses = 0
    last_id = 0
    while rows := conn.execute("SELECT * FROM candidate_snapshots WHERE id > ? ORDER BY id LIMIT ?",
                               (last_id, batch)).fetchall():
        for row in rows:
            verified_envelope(row, conn)
            last_id = row["id"]
        snapshots += len(rows)
    for row in conn.execute("SELECT * FROM company_provider_responses"):
        decode_response(row)
        responses += 1
    return snapshots, responses


def _ratio(stats):
    return f"{stats['bytes_before'] / 1e6:.0f} MB -> {stats['bytes_after'] / 1e6:.1f} MB"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", help="Write a consistent copy here before changing anything.")
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--no-vacuum", action="store_true")
    args = parser.parse_args(argv)
    db_path = database_path(args.db or None)
    started, size_before = time.time(), os.path.getsize(db_path)

    if args.backup:
        print(f"backing up to {args.backup}")
        with closing(get_connection(db_path)) as source, closing(sqlite3.connect(args.backup)) as target:
            source.backup(target)
    initialize_database(db_path=db_path)

    with closing(get_connection(db_path)) as conn:
        if not args.apply:
            snapshots = pack_snapshots(conn, batch=args.batch, limit=SAMPLE_ROWS)
            responses = compress_responses(conn, batch=50, limit=50)
            conn.rollback()
            print(f"dry run, rolled back. sample snapshots: {snapshots['packed']} packed, "
                  f"{snapshots['kept_plain']} kept plain, {_ratio(snapshots)} before json_blobs")
            print(f"sample responses: {responses['compressed']}, {responses['stripped']} stripped, {_ratio(responses)}")
            print("pass --apply to rewrite")
            return
        print("packing candidate_snapshots")
        snapshots = pack_snapshots(conn, batch=args.batch)
        print("compressing company_provider_responses")
        responses = compress_responses(conn, batch=50)
        print("verifying every row")
        checked = verify(conn, batch=args.batch)
        print(f"  verified {checked[0]} snapshots and {checked[1]} responses")
        if not args.no_vacuum:
            print("vacuuming")
            conn.isolation_level = None
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            conn.execute("VACUUM")
    print(f"snapshots {_ratio(snapshots)} (plus json_blobs); responses {_ratio(responses)}")
    print(f"file {size_before / 1e9:.2f} GB -> {os.path.getsize(db_path) / 1e9:.2f} GB "
          f"in {(time.time() - started) / 60:.1f} min")


if __name__ == "__main__":
    main()
