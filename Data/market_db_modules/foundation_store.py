"""Small shared primitives for immutable research records."""
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import json

from db_common import get_connection
from research_foundation_contracts import SCHEMA_VERSION, timestamp


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def record_data(record):
    data = asdict(record)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported research record schema")
    for name, value in data.items():
        if value is not None and (name.endswith("_at") or name == "information_cutoff_time"):
            data[name] = timestamp(value)
    return json.loads(canonical(data))


def insert_record(conn, table, key, data, columns):
    """Table and column names are internal constants, never model/provider input."""
    values = {key: data[key], **columns, "record_hash": digest(data), "record_json": canonical(data)}
    names = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders}) ON CONFLICT({key}) DO NOTHING",
                 tuple(values.values()))
    existing = conn.execute(f"SELECT record_hash FROM {table} WHERE {key}=?", (data[key],)).fetchone()
    if existing["record_hash"] != values["record_hash"]:
        raise ValueError(f"Immutable {table} record already has different content")
    return data[key]


def read_record(conn, table, key, identity):
    row = conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (identity,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown {table} record")
    data = json.loads(row["record_json"])
    if digest(data) != row["record_hash"]:
        raise ValueError(f"Corrupt {table} record hash")
    return data


class SnapshotHashMismatch(ValueError):
    pass


def verified_envelope(row):
    """A candidate_snapshots row's envelope, checked against the hash of its stored text."""
    if sha256(row["snapshot_json"].encode()).hexdigest() != row["snapshot_hash"]:
        raise SnapshotHashMismatch("Candidate snapshot hash mismatch")
    return json.loads(row["snapshot_json"])


def load_record(db_path, table, key, identity):
    with closing(get_connection(db_path)) as conn:
        return read_record(conn, table, key, identity)
