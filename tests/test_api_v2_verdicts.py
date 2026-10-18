"""Per-decision V2 shadow verdicts: what a decision record may claim about gates."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database

try:
    from api_support.v2_verdicts import shadow_verdicts_for_decisions
except ModuleNotFoundError as exc:  # the API package imports Flask at module scope
    if exc.name != "flask":
        raise
    # A stub would replace the real module for every later API test in the same process.
    raise unittest.SkipTest("flask is not installed in this environment") from exc


def decision(conn, decision_id, candidate_id):
    conn.execute(
        "INSERT INTO manager_decision_history (id, candidate_id, symbol, decision_run_at) VALUES (?,?,?,?)",
        (decision_id, candidate_id, "NVDA", "2026-09-20T14:00:00+00:00"),
    )
    if candidate_id:
        conn.execute(
            "INSERT OR IGNORE INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, pipeline_version_json)"
            " VALUES (?,?,?,'[]','[]','{}')",
            (candidate_id, "NVDA", "2026-09-20T13:00:00+00:00"),
        )


def rejection(conn, candidate_id, stage, reason_code, shadow):
    conn.execute(
        "INSERT INTO candidate_rejections (candidate_id, symbol, stage, reason_code, occurred_at, shadow)"
        " VALUES (?,?,?,?,?,?)",
        (candidate_id, "NVDA", stage, reason_code, "2026-09-20T14:05:00+00:00", shadow),
    )


class ShadowVerdictTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Path(self.directory.name) / "market.db"
        initialize_database(db_path=self.db)
        self.addCleanup(self.directory.cleanup)

    def test_a_verdict_is_attributed_to_the_decision_sharing_its_candidate(self):
        with closing(get_connection(self.db)) as conn:
            decision(conn, 1, "cand-a")
            decision(conn, 2, "cand-b")
            rejection(conn, "cand-a", "LIQUIDITY", "spread_too_wide", 1)
            rejection(conn, "cand-b", "REGIME", "regime_opposed", 1)
            conn.commit()

        verdicts = shadow_verdicts_for_decisions([1, 2], db_path=self.db)
        self.assertEqual([v["stage"] for v in verdicts[1]], ["LIQUIDITY"])
        self.assertEqual([v["stage"] for v in verdicts[2]], ["REGIME"])
        self.assertEqual(verdicts[1][0]["label"], "Liquidity V2 (E2)")
        self.assertEqual(verdicts[1][0]["reason_code"], "spread_too_wide")

    def test_a_decision_with_no_candidate_link_collects_nothing(self):
        """A null candidate_id must not join to every other null one."""
        with closing(get_connection(self.db)) as conn:
            decision(conn, 1, None)
            decision(conn, 2, None)
            conn.execute("INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, pipeline_version_json)"
                         " VALUES ('cand-a','NVDA','2026-09-20T13:00:00+00:00','[]','[]','{}')")
            rejection(conn, "cand-a", "LIQUIDITY", "spread_too_wide", 1)
            conn.commit()
        self.assertEqual(shadow_verdicts_for_decisions([1, 2], db_path=self.db), {})

    def test_unreadable_lifecycle_tables_are_reported_as_unreadable(self):
        """None and {} mean opposite things to someone reading a decision."""
        with closing(get_connection(self.db)) as conn:
            conn.execute("DROP TABLE candidate_rejections")
            conn.commit()
        self.assertIsNone(shadow_verdicts_for_decisions([1], db_path=self.db))


if __name__ == "__main__":
    unittest.main()
