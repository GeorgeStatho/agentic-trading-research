"""Hermetic test environment, applied before any test module imports project code.

Tests exercise code defaults against a throwaway database. They never read the operator's
.env, exported project configuration or real database, so results do not depend on the
machine or on test order.
"""
import atexit
import os
from pathlib import Path
import re
import shutil
import tempfile


ROOT_DIR = Path(__file__).resolve().parents[1]
_CONFIGURATION_KEY = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def _project_configuration_keys():
    return set(_CONFIGURATION_KEY.findall((ROOT_DIR / ".env.example").read_text(encoding="utf-8")))


def _isolate_environment():
    for key in _project_configuration_keys():
        os.environ.pop(key, None)
    workspace = Path(tempfile.mkdtemp(prefix="stock-experiment-tests-"))
    atexit.register(shutil.rmtree, workspace, ignore_errors=True)
    os.environ.update(DB_PATH=str(workspace / "stock_experiment.db"), TELEMETRY_ENABLED="false")


def _initialize_default_database():
    # Execution guards deliberately reject a missing database, so the default must exist.
    from market_db_modules.bootstrap import initialize_database
    from news_db_modules.bootstrap import initialize_database as initialize_news_database
    initialize_database()
    initialize_news_database()


def _isolate_model_cutoffs():
    # Replays of the generic "local" model must not depend on which models the shipped table lists.
    from services import model_cutoffs
    model_cutoffs.CUTOFFS_CSV = ROOT_DIR / "tests" / "fixtures" / "default_model_cutoffs.csv"


_isolate_environment()
_isolate_model_cutoffs()
_initialize_default_database()
