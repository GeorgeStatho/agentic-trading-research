"""Evaluator identity is independent of the strategy that produced a decision."""
from hashlib import sha256
from pathlib import Path

from research_foundation_contracts import OBSERVATION_PROTOCOL
from services.research_contract import fingerprint


ROOT = Path(__file__).resolve().parents[2]
VERSION = "research-evaluator-v2"
EVALUATOR_FILES = (
    "Data/db_common.py",
    "Data/research_foundation_contracts.py",
    "Data/market_db_modules/foundation_store.py",
    "Data/market_db_modules/research_foundation.py",
    "Data/market_db_modules/research_foundation_schema.sql",
    "Data/market_db_modules/research_observations.py",
    "Data/market_db_modules/research_runs.py",
    "Data/market_db_modules/research_reports.py",
    "Data/market_db_modules/forward_comparison.py",
    "Data/market_db_modules/research_schema.sql",
    "Data/market_db_modules/pricing_research.py",
    "Data/market_db_modules/pricing_schema.sql",
    "Data/catalyst_models.py",
    "Python Scripts/services/research_contract.py",
    "Python Scripts/services/research_inputs.py",
    "Python Scripts/services/research_versions.py",
    "Python Scripts/services/shadow_portfolio.py",
    "Python Scripts/services/price_response.py",
)


def evaluator_source_hash():
    return fingerprint({name: sha256((ROOT / name).read_bytes()).hexdigest() for name in EVALUATOR_FILES})


def evaluation_version(protocol_version=OBSERVATION_PROTOCOL):
    return {"version": VERSION, "evaluator_hash": evaluator_source_hash(),
            "protocol_version": protocol_version}


def require_current_evaluator(run, legacy_source_hash):
    version = run.get("evaluation_version")
    if version:
        if version["version"] != VERSION or version["evaluator_hash"] != evaluator_source_hash():
            raise ValueError("Research evaluator changed; open a new comparable experiment")
    elif run["code_hash"] != legacy_source_hash:
        raise ValueError("Research implementation changed; open a new challenger")
