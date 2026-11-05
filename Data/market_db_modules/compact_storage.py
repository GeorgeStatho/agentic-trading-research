"""Content-addressed storage for large canonical JSON, so repeated subtrees are stored once.

A packed document is its canonical JSON with every subtree of at least MIN_BLOB_CHARS replaced by
{"$cas": sha256}; each subtree lives once in json_blobs as zlib-compressed canonical JSON (which may
hold further refs). Unpacking rebuilds the exact original text, so callers keep verifying the hash of
the text they wrote.
"""
from hashlib import sha256
import json
import zlib

from .foundation_store import canonical

PACKED_ENCODING = "cas1"
REF_KEY = "$cas"
# Below these sizes the bookkeeping costs more than it saves.
MIN_PACK_CHARS = 16 * 1024
MIN_BLOB_CHARS = 2 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS json_blobs (hash TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TRIGGER IF NOT EXISTS json_blobs_no_update BEFORE UPDATE ON json_blobs
BEGIN SELECT RAISE(ABORT, 'Immutable json blob'); END;
CREATE TRIGGER IF NOT EXISTS json_blobs_no_delete BEFORE DELETE ON json_blobs
BEGIN SELECT RAISE(ABORT, 'Immutable json blob'); END;
"""


_ADDED_COLUMNS = {
    "candidate_snapshots": {"snapshot_encoding": "TEXT"},
    "company_provider_responses": {"body_z": "BLOB", "stored_hash": "TEXT"},
}


def migrate_compact_storage(conn):
    conn.executescript(SCHEMA)
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, column_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def pack_json(conn, text):
    """The packed form of canonical JSON `text`, or None when it should be stored as is."""
    if len(text) < MIN_PACK_CHARS or f'"{REF_KEY}"' in text:
        return None
    value = json.loads(text)
    if canonical(value) != text:
        return None
    packed, _ = _pack(conn, value, root=True)
    skeleton = canonical(packed)
    # Never trade the original for a form that does not rebuild it exactly.
    return skeleton if unpack_json(conn, skeleton) == text else None


def unpack_json(conn, skeleton):
    return canonical(_unpack(conn, json.loads(skeleton), {}))


def encode_snapshot(conn, text):
    """(stored text, snapshot_encoding) for a candidate_snapshots row."""
    skeleton = pack_json(conn, text)
    return (text, None) if skeleton is None else (skeleton, PACKED_ENCODING)


def snapshot_text(row, conn):
    """The original snapshot_json of a candidate_snapshots row, packed or not."""
    encoding = row["snapshot_encoding"] if "snapshot_encoding" in row.keys() else None
    if encoding is None:
        return row["snapshot_json"]
    if encoding != PACKED_ENCODING:
        raise ValueError(f"Unknown snapshot encoding {encoding!r}")
    if conn is None:
        raise ValueError("A packed snapshot needs a connection to read its blobs")
    return unpack_json(conn, row["snapshot_json"])


def _pack(conn, value, *, root=False):
    """(packed value, length of the original canonical text)."""
    if isinstance(value, dict):
        children = {key: _pack(conn, item) for key, item in value.items()}
        packed = {key: child for key, (child, _) in children.items()}
        size = 2 + max(len(children) - 1, 0) + sum(len(json.dumps(key)) + 1 + child_size
                                                  for key, (_, child_size) in children.items())
    elif isinstance(value, list):
        children = [_pack(conn, item) for item in value]
        packed = [child for child, _ in children]
        size = 2 + max(len(children) - 1, 0) + sum(child_size for _, child_size in children)
    else:
        return value, len(canonical(value))
    if root or size < MIN_BLOB_CHARS:
        return packed, size
    text = canonical(packed)
    blob_hash = sha256(text.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO json_blobs (hash, data) VALUES (?, ?)",
                 (blob_hash, zlib.compress(text.encode(), 6)))
    return {REF_KEY: blob_hash}, size


def _unpack(conn, value, cache):
    if isinstance(value, dict):
        if len(value) == 1 and REF_KEY in value:
            blob_hash = value[REF_KEY]
            if blob_hash not in cache:
                row = conn.execute("SELECT data FROM json_blobs WHERE hash=?", (blob_hash,)).fetchone()
                if row is None:
                    raise ValueError(f"Missing json blob {blob_hash}")
                cache[blob_hash] = json.loads(zlib.decompress(row[0]))
            return _unpack(conn, cache[blob_hash], cache)
        return {key: _unpack(conn, item, cache) for key, item in value.items()}
    if isinstance(value, list):
        return [_unpack(conn, item, cache) for item in value]
    return value
