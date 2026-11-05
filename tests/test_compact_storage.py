from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import sqlite3
import unittest
import zlib

from db_common import get_connection
from market_db_modules.company_collection import cache_response, cached_response, decode_response
from market_db_modules.compact_storage import PACKED_ENCODING, encode_snapshot, pack_json, unpack_json
from market_db_modules.foundation_store import SnapshotHashMismatch, canonical, verified_envelope
from scripts.compact_db import compress_responses, pack_snapshots
from tests.db_fixtures import temporary_database
from Webscraping.company_evidence.sec import SECAdapter, strip_binary_attachments


def _envelope(stage, *, evidence_seed=0):
    evidence = [{"article_id": index, "body": f"evidence {evidence_seed} {index} " * 20} for index in range(60)]
    return {"candidate_id": "c-1", "stage": stage, "information_cutoff_time": "2026-11-03T14:00:00+00:00",
            "pipeline_version": {"pipeline_version": "v1", "source_hashes": {f"f{i}.py": "x" * 64 for i in range(60)}},
            "payload": {"candidate_context": {"evidence": evidence, "price": 101.5, "ok": True, "none": None},
                        "stage_note": stage}}


SUBMISSION = """<SEC-DOCUMENT>0000000000-26-000001.txt
<SEC-HEADER>CENTRAL INDEX KEY:\t\t\t0000000001
</SEC-HEADER>
<DOCUMENT>
<TYPE>8-K
<TEXT>
Company reported revenue of $10 million and raised guidance.
</TEXT>
</DOCUMENT>
<DOCUMENT>
<TYPE>GRAPHIC
<FILENAME>logo.jpg
<TEXT>
begin 644 logo.jpg
M_]C_X``02D9)1@`!`0$`8`!@``#_VP!#``@&!@<&!0@'!P<)
end
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""


class PackJsonTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        from market_db_modules.compact_storage import SCHEMA
        self.conn.executescript(SCHEMA)

    def test_round_trip_is_byte_exact(self):
        text = canonical(_envelope("pre_gates"))
        skeleton = pack_json(self.conn, text)
        self.assertIsNotNone(skeleton)
        self.assertLess(len(skeleton), len(text) // 10)
        self.assertEqual(unpack_json(self.conn, skeleton), text)

    def test_shared_subtrees_are_stored_once(self):
        pack_json(self.conn, canonical(_envelope("pre_gates")))
        blobs = self.conn.execute("SELECT COUNT(*) FROM json_blobs").fetchone()[0]
        pack_json(self.conn, canonical(_envelope("post_manager")))
        # Only the subtrees containing the differing stage note are new.
        self.assertLessEqual(self.conn.execute("SELECT COUNT(*) FROM json_blobs").fetchone()[0], blobs + 1)

    def test_small_or_non_canonical_text_stays_plain(self):
        self.assertIsNone(pack_json(self.conn, canonical({"a": 1})))
        text = canonical(_envelope("pre_gates"))
        self.assertIsNone(pack_json(self.conn, text.replace(",", ", ", 1)))
        self.assertIsNone(pack_json(self.conn, text[:-1] + ',"x":{"$cas":"abc"}}'))


class CandidateSnapshotStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = temporary_database(self)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json,
                article_ids_json, pipeline_version_json) VALUES ('c-1', 'AAA', 'now', '[]', '[]', '{}')''')

    def _insert(self, conn, stage, text, stored, encoding):
        conn.execute('''INSERT INTO candidate_snapshots (candidate_id, stage, captured_at, information_cutoff_time,
            pipeline_version, snapshot_version, snapshot_hash, snapshot_json, snapshot_encoding)
            VALUES ('c-1', ?, 'now', 'now', 'v1', 'candidate-snapshot-v1', ?, ?, ?)''',
                     (stage, sha256(text.encode()).hexdigest(), stored, encoding))
        return conn.execute("SELECT * FROM candidate_snapshots WHERE stage=?", (stage,)).fetchone()

    def test_packed_and_plain_rows_verify(self):
        envelope = _envelope("pre_gates")
        text = canonical(envelope)
        with closing(get_connection(self.db)) as conn, conn:
            stored, encoding = encode_snapshot(conn, text)
            self.assertEqual(encoding, PACKED_ENCODING)
            packed = self._insert(conn, "pre_gates", text, stored, encoding)
            plain = self._insert(conn, "plain", text, text, None)
            self.assertEqual(verified_envelope(packed, conn), envelope)
            self.assertEqual(verified_envelope(plain), envelope)
            with self.assertRaises(ValueError):
                verified_envelope(packed)

    def test_tampered_blob_fails_the_hash(self):
        text = canonical(_envelope("pre_gates"))
        with closing(get_connection(self.db)) as conn, conn:
            row = self._insert(conn, "pre_gates", text, *encode_snapshot(conn, text))
            conn.execute("DROP TRIGGER json_blobs_no_update")
            blob_hash, data = conn.execute("SELECT hash, data FROM json_blobs LIMIT 1").fetchone()
            tampered = zlib.decompress(data).decode().replace("evidence", "EVIDENCE", 1)
            conn.execute("UPDATE json_blobs SET data=? WHERE hash=?", (zlib.compress(tampered.encode()), blob_hash))
            with self.assertRaises(SnapshotHashMismatch):
                verified_envelope(row, conn)

    def test_backfill_packs_legacy_rows(self):
        text = canonical(_envelope("pre_gates"))
        with closing(get_connection(self.db)) as conn:
            with conn:
                self._insert(conn, "pre_gates", text, text, None)
            stats = pack_snapshots(conn, batch=10)
            row = conn.execute("SELECT * FROM candidate_snapshots").fetchone()
            self.assertEqual((stats["packed"], row["snapshot_encoding"]), (1, PACKED_ENCODING))
            self.assertEqual(canonical(verified_envelope(row, conn)), text)


class ProviderResponseStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = temporary_database(self)

    def test_stripping_keeps_what_the_sec_adapter_reads(self):
        adapter = SECAdapter(http=None, policy=None)
        stripped = adapter.compact_filing(SUBMISSION)
        self.assertNotIn("begin 644", stripped)
        self.assertIn("[binary attachment removed:", stripped)
        self.assertIn("raised guidance", stripped)
        self.assertEqual(adapter._read_filing(stripped), adapter._read_filing(SUBMISSION))
        self.assertEqual(strip_binary_attachments('{"cik": "1"}'), '{"cik": "1"}')

    def test_identity_stays_with_the_body_as_received(self):
        stripped = SECAdapter(http=None, policy=None).compact_filing(SUBMISSION)
        url = "https://www.sec.gov/Archives/x.txt"
        saved = cache_response("sec", url, SUBMISSION, "2026-11-03T14:00:00+00:00", stored_body=stripped,
                               db_path=self.db)
        self.assertEqual(saved["content_hash"], sha256(SUBMISSION.encode()).hexdigest())
        self.assertEqual(saved["body"], stripped)
        again = cache_response("sec", url, SUBMISSION, "2026-11-04T14:00:00+00:00", db_path=self.db)
        self.assertEqual(again["response_id"], saved["response_id"])
        self.assertEqual(cached_response("sec", url, db_path=self.db)["body"], stripped)

    def test_legacy_plain_rows_decode_and_backfill(self):
        content_hash = sha256(SUBMISSION.encode()).hexdigest()
        with closing(get_connection(self.db)) as conn:
            with conn:
                conn.execute('''INSERT INTO company_provider_responses
                    (response_id, provider, url, observed_at, body, content_hash) VALUES ('r-1','sec','u','now',?,?)''',
                             (SUBMISSION, content_hash))
            self.assertEqual(decode_response(conn.execute("SELECT * FROM company_provider_responses").fetchone())["body"],
                             SUBMISSION)
            stats = compress_responses(conn, batch=10)
            row = decode_response(conn.execute("SELECT * FROM company_provider_responses").fetchone())
            self.assertEqual((stats["compressed"], stats["stripped"]), (1, 1))
            self.assertEqual(row["content_hash"], content_hash)
            self.assertNotIn("begin 644", row["body"])
            # The immutability trigger is back after the rewrite.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE company_provider_responses SET url='x'")


if __name__ == "__main__":
    unittest.main()
