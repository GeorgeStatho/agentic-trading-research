"""Offline fixtures shared by foundation storage and comparison tests."""
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
import tempfile
from unittest.mock import patch

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.candidate_lifecycle import record_candidate, record_candidate_snapshot
from market_db_modules.research_observations import record_research_observation, get_research_observation
from news_db_modules.catalysts import build_catalyst_state, store_catalyst_state
from research_foundation_contracts import ResearchObservation
from services.contract_policy import ContractPolicy
from services.research_contract import canonical


START = "2026-09-01T20:00:00+00:00"
ENTRY = "2026-09-02T20:00:00+00:00"
END = "2026-09-10T20:00:00+00:00"
CATALYST = "catalyst:earnings-fixture"


def database(test):
    temp = tempfile.TemporaryDirectory()
    test.addCleanup(temp.cleanup)
    db = Path(temp.name) / "foundation.db"
    initialize_database(db_path=db)
    with closing(get_connection(db)) as conn, conn:
        conn.execute("INSERT INTO sectors(id,sector_key,name) VALUES (1,'technology','Technology')")
        conn.execute("INSERT INTO industries(id,sector_id,industry_key,name) VALUES (1,1,'chips','Chips')")
        conn.execute("INSERT INTO companies(id,industry_id,symbol,name) VALUES (1,1,'NVDA','Fixture')")
        conn.execute("INSERT INTO companies(id,industry_id,symbol,name) VALUES (2,1,'OTHER','Other')")
        state = build_catalyst_state(CATALYST, [], ticker="NVDA")
        state.update(catalyst_family="earnings_positive", direction="positive")
        store_catalyst_state(state, conn=conn)
    return db


def manifest(name="incumbent"):
    return {"pipeline_version": name, "model": name,
            "gate_config": {"contracts": asdict(ContractPolicy())}}


def contract(symbol, delta):
    return {"symbol": symbol, "contract_type": "call", "expiration_date": "2026-10-16",
            "greeks": {"delta": delta},
            "latest_quote": {"bid_price": 1.9, "ask_price": 2, "timestamp": ENTRY}}


def observation(db, index=0, **changes):
    contracts = [contract(f"inc-{index}", .7), contract(f"new-{index}", .8), contract(f"base-{index}", .5)]
    market = {"market_regime": {"state": "trend_up" if index % 2 else "range"},
              "option_market": {"contracts": contracts},
              "underlying_price_history": {"technical_summary": {"trend_state": "bullish"},
                  "technical_indicators": {"relative_volume": {"relative_volume_20d": 2}}},
              "_v2_scenario_distribution": {"returns": [.02] * 30, "minimum_samples": 30, "as_of": ENTRY}}
    record = ResearchObservation(**{
        "company_id": 1, "symbol": "NVDA", "catalyst_id": CATALYST, "event_revision": f"event-{index}",
        "information_cutoff_time": ENTRY, "source_snapshot": {"evidence": [], "market_context": market},
        **changes,
    })
    return record_research_observation(record, db_path=db)


def candidate(db, version, observation_id=None, *, selection=0, completed=True, created_at=ENTRY):
    with patch("market_db_modules.candidate_lifecycle.now_iso", return_value=created_at):
        context = record_candidate("NVDA", version, evidence=[], observation_id=observation_id, db_path=db)
        if observation_id:
            source = get_research_observation(observation_id, db_path=db)["source_snapshot"]
        else:
            source = get_research_observation(observation(db), db_path=db)["source_snapshot"]
        market = source["market_context"]
        selected = market["option_market"]["contracts"][selection]
        recommendation = {"decision": "call", "confidence": "high", "thesis_confidence": "high"}
        payload = {"recommendation": recommendation, "manager_model": version["model"],
                   "strategist_recommendation": {"decision": "call"},
                   "candidate_context": {"catalysts": source["catalysts"]},
                   "selected_option": selected, "market_context": market}
        stages = [("pre_gates", {"recommendation": recommendation,
                    "payload": {"opportunist_rollup": {"company": {"dominant_direction": "positive"}}}}),
                  ("post_contract_selection", payload)]
        if completed:
            stages.append(("candidate_complete", {}))
        for stage, value in stages:
            record_candidate_snapshot(context, stage, value, db_path=db)
    return context


def prices(db, count):
    with closing(get_connection(db)) as conn, conn:
        for n in range(count):
            for prefix, exit_bid in (("inc", 2.1), ("new", 2.3), ("base", 1.2)):
                for day in (2, 3, 4, 7):
                    at = f"2026-09-{day:02d}T20:00:00+00:00"
                    bid = 1.9 if day == 2 else exit_bid
                    conn.execute("""INSERT INTO historical_option_quotes
                        (option_symbol,quote_at,bid_price,ask_price,provider,created_at)
                        VALUES (?,?,?,?,?,?)""", (f"{prefix}-{n}", at, bid, max(bid+.1, 2), "fixture", at))
        for symbol in ("SMH", "QQQ", "SPY"):
            for day in (2, 3, 4, 7):
                at = f"2026-09-{day:02d}T20:00:00+00:00"
                conn.execute("""INSERT INTO historical_underlying_bars
                    (symbol,bar_at,timeframe,close_price,provider,raw_json,created_at)
                    VALUES (?,?,'1d',100,'fixture',?,?)""", (symbol, at, canonical({"available_at": at}), at))
