from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from db_common import use_database
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database


def temporary_database(test: unittest.TestCase, name: str = "test.db") -> Path:
    """Point the current database at a fresh, initialised file for the rest of `test`.

    For a database shared by a whole class, enter `use_database(path)` with
    `cls.enterClassContext(...)` in `setUpClass` instead.
    """
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / name
    test.enterContext(use_database(path))
    initialize_market_database()
    initialize_news_database()
    return path
