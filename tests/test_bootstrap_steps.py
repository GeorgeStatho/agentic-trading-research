"""Schema setup runs as ordered lists of idempotent steps, and builds the schema it always has.

When a schema change is intended, regenerate the snapshot:
    UPDATE_SCHEMA_SNAPSHOT=1 python3 -m pytest tests/test_bootstrap_steps.py
and review its diff with the change.
"""
from contextlib import closing
import json
import os
import unittest

from db_common import get_connection
from tests import ROOT_DIR
from tests.db_fixtures import temporary_database


SNAPSHOT = ROOT_DIR / "tests" / "fixtures" / "fresh_schema.json"


def _schema(db_path) -> list[list[str]]:
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    return [[row["type"], row["name"], row["tbl_name"], row["sql"]] for row in rows]


class FreshSchemaTests(unittest.TestCase):
    def test_a_fresh_database_gets_the_snapshotted_schema(self):
        schema = _schema(temporary_database(self))

        if os.environ.get("UPDATE_SCHEMA_SNAPSHOT"):
            SNAPSHOT.write_text(json.dumps(schema, indent=1) + "\n", encoding="utf-8")
        self.assertEqual(json.loads(SNAPSHOT.read_text(encoding="utf-8")), schema)

    def test_setting_up_an_existing_database_again_changes_nothing(self):
        from db_common import forget_setup
        from market_db_modules.bootstrap import initialize_database as initialize_market
        from news_db_modules.bootstrap import initialize_database as initialize_news

        db_path = temporary_database(self)
        before = _schema(db_path)
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute("DELETE FROM schema_bootstrap")
        forget_setup(db_path)
        initialize_market(db_path)
        initialize_news(db_path)

        self.assertEqual(before, _schema(db_path))


class SetupStepTests(unittest.TestCase):
    def test_market_setup_steps_are_an_ordered_tuple_of_callables(self):
        from market_db_modules.bootstrap import _setup_steps

        steps = _setup_steps()

        self.assertIsInstance(steps, tuple)
        self.assertGreater(len(steps), 10)
        self.assertTrue(all(callable(step) for step in steps))

    def test_news_migration_steps_are_ordered_tuples_of_callables(self):
        from news_db_modules.bootstrap import _AFTER_SCHEMA_STEPS, _MIGRATION_STEPS

        for steps in (_MIGRATION_STEPS, _AFTER_SCHEMA_STEPS):
            self.assertIsInstance(steps, tuple)
            self.assertTrue(steps and all(callable(step) for step in steps))


if __name__ == "__main__":
    unittest.main()
