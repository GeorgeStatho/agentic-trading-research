"""Plan B: the point-in-time replay benchmark. Slice B1 (model knowledge cutoffs and the contamination guard) is here.

Tests read `tests/fixtures/model_cutoffs.csv` through the module's `CUTOFFS_CSV` path, never the real table.
Modules under test are imported on use so each test fails on its own while they do not exist yet.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from contextlib import closing, redirect_stdout
import csv
from datetime import date, datetime, timedelta, timezone
import importlib
import importlib.util
import io
import json
from math import ceil
from pathlib import Path
from statistics import mean
import sqlite3
import sys
import unittest
from unittest.mock import Mock, patch

from db_common import get_connection
from market_db_modules.entities import add_company, add_industry, add_sector
from services import llm_backtest
from services.decision_attribution import bootstrap_se
from services.llm_direction_score import run_direction_score
from tests.test_llm_backtest import DatabaseFixture, TRADEABLE

FIXTURE_CSV = Path(__file__).resolve().parent / "fixtures" / "model_cutoffs.csv"
REAL_CSV = Path(__file__).resolve().parents[1] / "config" / "model_knowledge_cutoffs.csv"
NO_SUCH_CSV = Path(__file__).resolve().parent / "fixtures" / "does_not_exist.csv"


def cutoffs():
    return importlib.import_module("services.model_cutoffs")


class PatchedCutoffTable(unittest.TestCase):
    csv_path = FIXTURE_CSV

    def setUp(self) -> None:
        super().setUp()
        patcher = patch("services.model_cutoffs.CUTOFFS_CSV", self.csv_path)
        patcher.start()
        self.addCleanup(patcher.stop)


class KnowledgeCutoffTests(PatchedCutoffTable):
    def test_the_exact_model_id_matches(self) -> None:
        self.assertEqual(cutoffs().knowledge_cutoff("test-model"), date(2026, 1, 1))

    def test_a_dated_variant_matches_its_longest_prefix(self) -> None:
        # test-model-pro is also prefixed by test-model, whose cutoff is earlier.
        self.assertEqual(cutoffs().knowledge_cutoff("test-model-pro-0301"), date(2026, 3, 1))
        self.assertEqual(cutoffs().knowledge_cutoff("test-model-flash-001"), date(2026, 1, 1))

    def test_an_exact_id_beats_a_shorter_prefix(self) -> None:
        self.assertEqual(cutoffs().knowledge_cutoff("test-model-pro"), date(2026, 3, 1))

    def test_an_unlisted_model_has_no_cutoff(self) -> None:
        # "other-test-model" contains a listed id but does not start with it.
        for model in ("mystery-model", "other-test-model", "", "test"):
            with self.subTest(model=model):
                self.assertIsNone(cutoffs().knowledge_cutoff(model))

    def test_the_margin_is_thirty_days(self) -> None:
        self.assertEqual(cutoffs().CONTAMINATION_MARGIN_DAYS, 30)


class EligibilityTests(PatchedCutoffTable):
    # test-model's cutoff is 2026-01-01, so its 30-day margin ends on 2026-01-31.
    def test_the_margin_boundary_is_whole_days(self) -> None:
        cases = (
            ("2026-01-15T12:00:00+00:00", "pre_knowledge_cutoff"),
            ("2026-01-01T00:00:00+00:00", "pre_knowledge_cutoff"),
            ("2025-06-01T00:00:00+00:00", "pre_knowledge_cutoff"),
            ("2026-01-31T00:00:00+00:00", "pre_knowledge_cutoff"),
            ("2026-01-31T23:59:59+00:00", "pre_knowledge_cutoff"),
            ("2026-02-01T00:00:00+00:00", "eligible"),
            ("2026-03-02T15:00:00+00:00", "eligible"),
        )
        for decided_at, expected in cases:
            with self.subTest(decided_at=decided_at):
                self.assertEqual(cutoffs().eligible(decided_at, "test-model"), expected)

    def test_a_longer_prefix_moves_the_boundary(self) -> None:
        # test-model-pro-0301: cutoff 2026-03-01, margin ends 2026-03-31.
        self.assertEqual(cutoffs().eligible("2026-03-31T23:00:00+00:00", "test-model-pro-0301"), "pre_knowledge_cutoff")
        self.assertEqual(cutoffs().eligible("2026-04-01T00:00:00+00:00", "test-model-pro-0301"), "eligible")

    def test_an_unlisted_model_is_unknown(self) -> None:
        self.assertEqual(cutoffs().eligible("2026-09-01T00:00:00+00:00", "mystery-model"), "cutoff_unknown")


class RealCutoffTableTests(unittest.TestCase):
    """Structure only: which models are listed is a data decision this test does not pin."""

    def test_the_shipped_table_is_well_formed(self) -> None:
        with REAL_CSV.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ["model", "knowledge_cutoff", "source"])
            rows = list(reader)
        for row in rows:
            with self.subTest(model=row["model"]):
                self.assertTrue(row["model"].strip())
                date.fromisoformat(row["knowledge_cutoff"])
                self.assertTrue(row["source"].startswith("http") or row["source"] == "release-date-proxy")
        self.assertEqual(len({row["model"] for row in rows}), len(rows), "duplicate model rows")


class ContaminationGuardTests(PatchedCutoffTable, DatabaseFixture):
    """`ReplayRun.build` (shared by the option backtest and the direction scorer) refuses decisions a model may remember."""

    def setUp(self) -> None:
        super().setUp()
        self.pre_id = self.add_decision("2026-01-15T15:00:00+00:00")
        self.last_excluded_id = self.add_decision("2026-01-31T23:59:00+00:00")
        self.first_eligible_id = self.add_decision("2026-02-01T00:00:00+00:00")

    def add_decision(self, at: str) -> int:
        with closing(get_connection(self.db_path)) as conn, conn:
            history_id = conn.execute("INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) "
                                      "VALUES ('MSFT', ?, 'neither')", (at,)).lastrowid
            conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, "
                         "manager_output_json) VALUES (?, ?, ?)",
                         (history_id, json.dumps({"replay_input": {"company": {"symbol": "MSFT", "at": at}}}),  # distinct: answers are cached by input
                          json.dumps({"recommendation": {"decision": "neither"}})))
        return history_id

    def build(self, model: str, **options) -> llm_backtest.ReplayRun:
        return llm_backtest.ReplayRun.build(model=model, decide=Mock(), db_path=self.db_path, **options)

    def summary(self, run: llm_backtest.ReplayRun) -> dict:
        return run.summary(llm_backtest.ReplayTally())

    def test_decisions_inside_the_margin_are_dropped_and_counted(self) -> None:
        run = self.build("test-model")
        self.assertEqual([d["id"] for d in run.decisions], [self.first_eligible_id, self.decision_id])
        self.assertEqual(self.summary(run)["excluded_pre_knowledge_cutoff"], 2)

    def test_the_contamination_record_names_the_cutoff_margin_and_source(self) -> None:
        self.assertEqual(self.summary(self.build("test-model"))["contamination"],
                         {"knowledge_cutoff": "2026-01-01", "margin_days": 30,
                          "source": "https://example.test/models/test-model"})

    def test_a_model_known_past_every_decision_replays_nothing(self) -> None:
        run = self.build("test-model-pro-0301")
        self.assertEqual(run.decisions, [])
        self.assertEqual(self.summary(run)["excluded_pre_knowledge_cutoff"], 4)

    def test_an_unknown_cutoff_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build("mystery-model")

    def test_an_unknown_cutoff_can_be_allowed_and_then_nothing_is_excluded(self) -> None:
        run = self.build("mystery-model", allow_unknown_cutoff=True)
        self.assertEqual(len(run.decisions), 4)
        self.assertEqual(self.summary(run).get("excluded_pre_knowledge_cutoff", 0), 0)

    def test_the_override_does_not_disable_the_guard_for_a_known_model(self) -> None:
        run = self.build("test-model", allow_unknown_cutoff=True)
        self.assertEqual([d["id"] for d in run.decisions], [self.first_eligible_id, self.decision_id])

    def test_stored_bypasses_the_guard_without_reading_the_table(self) -> None:
        with patch("services.model_cutoffs.CUTOFFS_CSV", NO_SUCH_CSV):
            run = self.build(llm_backtest.STORED_MODEL)
        self.assertEqual(len(run.decisions), 4)
        summary = self.summary(run)
        self.assertEqual(summary.get("excluded_pre_knowledge_cutoff", 0), 0)
        self.assertIsNone(summary.get("contamination"))

    def test_the_direction_scorer_shares_the_guard(self) -> None:
        decide = Mock(return_value={"recommendation": {"decision": "neither", "confidence": "high"}})
        with self.assertRaises(ValueError):
            run_direction_score(model="mystery-model", decide=decide, db_path=self.db_path)
        result = run_direction_score(model="test-model", decide=decide, db_path=self.db_path)
        self.assertEqual(result["replay"]["excluded_pre_knowledge_cutoff"], 2)
        self.assertEqual(decide.call_count, 2)
        run_direction_score(model="mystery-model", allow_unknown_cutoff=True, decide=decide, db_path=self.db_path)
        self.assertEqual(decide.call_count, 6)

    def test_the_direction_scorer_takes_decision_ids(self) -> None:
        result = run_direction_score(model=llm_backtest.STORED_MODEL, decision_ids=[self.pre_id, self.decision_id],
                                     db_path=self.db_path)
        self.assertEqual(result["replay"]["source_decision_count"], 2)


# --- Slice B2: benchmark sets, selection, runner, comparison and CLI ------------------------------------------------

RESEARCH_CLI = Path(__file__).resolve().parents[1] / "scripts" / "research.py"
WINDOW_START = "2026-07-01T00:00:00+00:00"
SECTORS = ("technology", "energy", "healthcare")
NOT_ELIGIBLE = {**TRADEABLE, "market_regime_gate": {"allowed": False}}  # finalize result that never trades
ITEM_KEYS = {"manager_decision_history_id", "split", "stratum"}


def selection():
    return importlib.import_module("services.benchmark_selection")


def benchmarks():
    return importlib.import_module("market_db_modules.benchmarks")


def runner():
    return importlib.import_module("services.benchmark_runner")


def day(offset: int, hour: int = 15) -> str:
    return (datetime(2026, 7, 1, hour, tzinfo=timezone.utc) + timedelta(days=offset)).isoformat()


def regime_input(symbol: str, at: str, regime: str | None = "range") -> dict:
    market = {"current_stock_price": {"price": 400.0}}
    if regime is not None:
        market["market_regime"] = {"state": regime}
    return {"replay_input": {"company": {"symbol": symbol, "at": at}, "market_context": market}}


def synthetic(decision_id: int, symbol: str, at: str, direction: str = "call", sector: str = "technology",
              regime: str | None = "range", family: str | None = None) -> dict:
    """A decision as `list_frozen_manager_decisions` returns it; `catalyst_family` is the optional primary family."""
    row = {"id": decision_id, "symbol": symbol, "decision_run_at": at, "manager_decision": direction,
           "sector_key": sector, "manager_input_json": json.dumps(regime_input(symbol, at, regime)),
           "manager_output_json": json.dumps({"recommendation": {"decision": direction}})}
    return {**row, "catalyst_family": family} if family else row


def expected_stratum(decision: dict, regime: str = "range") -> str:
    return f"{decision['sector_key']}|{decision['manager_decision']}|{regime}|{decision['decision_run_at'][:7]}"


def ample_pool() -> list[dict]:
    """40 symbols x 4 decisions over 18 common strata (60% abstentions), three rare-stratum decisions and one singleton."""
    ids = iter(range(1, 10_000))
    pool = [synthetic(next(ids), f"S{symbol:02d}", day(offset), ("neither", "neither", "call", "put", "neither")[(symbol + k) % 5],
                      SECTORS[symbol % 3])
            for symbol in range(40) for k, offset in enumerate((0, 3, 40, 43))]
    pool += [synthetic(next(ids), f"U{symbol}", day(5), sector="utilities") for symbol in range(3)]
    pool.append(synthetic(next(ids), "M0", day(6), sector="materials"))
    return pool


def pick(items: list[dict], pool: list[dict]) -> list[dict]:
    chosen = {item["manager_decision_history_id"] for item in items}
    return [decision for decision in pool if decision["id"] in chosen]


class SelectionTests(unittest.TestCase):
    SIZE = 60

    def select(self, pool: list[dict] | None = None, **options) -> list[dict]:
        options = {"set_id": "bench-a", "size": self.SIZE, "holdout_share": 0.3, **options}
        return selection().select_items(ample_pool() if pool is None else pool, **options)

    def test_items_are_pool_decisions_with_a_split_and_the_stratum_key(self) -> None:
        pool = ample_pool()
        items = self.select(pool)
        self.assertEqual(len(items), self.SIZE)
        self.assertEqual({frozenset(item) for item in items}, {frozenset(ITEM_KEYS)})
        ids = [item["manager_decision_history_id"] for item in items]
        self.assertEqual(len(set(ids)), len(ids))
        by_id = {decision["id"]: decision for decision in pool}
        for item in items:
            with self.subTest(item=item):
                self.assertIn(item["split"], ("dev", "holdout"))
                regime = "range"
                self.assertEqual(item["stratum"], expected_stratum(by_id[item["manager_decision_history_id"]], regime))

    def test_the_stratum_key_is_sector_direction_regime_month_and_a_labelled_family(self) -> None:
        pool = [synthetic(1, "AAA", day(0), "call", "technology", "range"),
                synthetic(2, "BBB", day(40), "put", "energy", None),
                synthetic(3, "CCC", day(0), "call", "technology", "range", family="earnings"),
                synthetic(4, "DDD", day(0), "call", "technology", "range", family="other")]
        strata = {item["manager_decision_history_id"]: item["stratum"]
                  for item in self.select(pool, size=4, holdout_share=0.5)}
        self.assertEqual(strata, {1: "technology|call|range|2026-07", 2: "energy|put|unknown|2026-08",
                                  3: "technology|call|range|2026-07|earnings", 4: "technology|call|range|2026-07"})

    def test_the_same_set_id_gives_identical_items(self) -> None:
        self.assertEqual(self.select(), self.select())

    def test_the_default_seed_comes_from_the_set_id(self) -> None:
        self.assertNotEqual(self.select(set_id="bench-a"), self.select(set_id="bench-b"))

    def test_a_different_seed_gives_different_items(self) -> None:
        self.assertNotEqual(self.select(seed=1), self.select(seed=2))

    def test_an_explicit_seed_overrides_the_set_id(self) -> None:
        self.assertEqual(self.select(set_id="bench-a", seed=7), self.select(set_id="bench-b", seed=7))

    def test_every_stratum_keeps_its_floor_of_two(self) -> None:
        pool = ample_pool()
        selected = Counter(item["stratum"] for item in self.select(pool))
        available = Counter(expected_stratum(decision) for decision in pool)
        self.assertEqual(len(available), 20)
        for stratum, count in available.items():
            with self.subTest(stratum=stratum):
                self.assertGreaterEqual(selected[stratum], min(2, count))
        self.assertEqual(selected["materials|call|range|2026-07"], 1)

    def test_abstentions_are_capped_at_forty_percent(self) -> None:
        pool = ample_pool()
        neither = sum(item["stratum"].split("|")[1] == "neither" for item in self.select(pool))
        self.assertLessEqual(neither, int(0.4 * self.SIZE))
        self.assertGreater(sum(d["manager_decision"] == "neither" for d in pool) / len(pool), 0.5)  # the pool would exceed it

    def test_no_symbol_exceeds_the_entity_budget(self) -> None:
        pool = [synthetic(index, "AAA", day(index)) for index in range(1, 26)]
        pool += [synthetic(100 + 10 * n + index, f"B{n}", day(index)) for n in range(9) for index in range(5)]
        items = self.select(pool, size=20)
        cap = ceil(20 / 10 * 2)
        counts = Counter(decision["symbol"] for decision in pick(items, pool))
        self.assertEqual(len(items), 20)
        self.assertLessEqual(max(counts.values()), cap)

    def test_neighbouring_redecisions_of_a_symbol_are_spaced_a_day_apart(self) -> None:
        pool = ample_pool() + [synthetic(5000 + hour, "HRLY", day(0) if hour == 0 else
                                         (datetime.fromisoformat(day(0)) + timedelta(hours=hour)).isoformat())
                               for hour in range(72)]
        chosen = sorted(datetime.fromisoformat(d["decision_run_at"]) for d in pick(self.select(pool), pool)
                        if d["symbol"] == "HRLY")
        self.assertGreater(len(chosen), 0)
        for earlier, later in zip(chosen, chosen[1:]):
            self.assertGreaterEqual(later - earlier, timedelta(hours=24))

    def test_a_symbol_is_never_in_both_dev_and_holdout(self) -> None:
        pool = ample_pool()
        items = self.select(pool)
        symbols = {split: {d["symbol"] for d in pick([i for i in items if i["split"] == split], pool)}
                   for split in ("dev", "holdout")}
        self.assertTrue(symbols["dev"] and symbols["holdout"])
        self.assertEqual(symbols["dev"] & symbols["holdout"], set())
        share = sum(item["split"] == "holdout" for item in items) / len(items)
        self.assertAlmostEqual(share, 0.3, delta=0.1)

    def test_busy_days_are_sampled_more_often_but_quiet_days_stay_eligible(self) -> None:
        busy = [synthetic(index, f"H{index}", day(10)) for index in range(10)]
        quiet = [synthetic(100 + index, f"L{index}", day(11 + index)) for index in range(10)]
        busy_picks = quiet_picks = 0
        for seed in range(20):
            ids = {item["manager_decision_history_id"] for item in self.select(busy + quiet, size=5, seed=seed)}
            busy_picks += sum(item["id"] in ids for item in busy)
            quiet_picks += sum(item["id"] in ids for item in quiet)
        self.assertGreater(busy_picks / 20, 3.5)  # uniform sampling would give 2.5
        self.assertGreater(quiet_picks, 0)


class BenchmarkPool(DatabaseFixture):
    """Decisions in a real temporary database, with sectors and frozen manager inputs."""

    def setUp(self) -> None:
        super().setUp()
        self.industries: dict[str, int] = {}
        self.companies: dict[str, int] = {}

    def company_id(self, symbol: str, sector: str) -> int:
        if sector not in self.industries:
            sector_id = add_sector(sector, sector.title(), db_path=self.db_path)
            self.industries[sector] = add_industry(sector_id, f"{sector}-industry", sector.title(), db_path=self.db_path)
        if symbol not in self.companies:
            self.companies[symbol] = add_company(self.industries[sector], symbol, symbol, db_path=self.db_path)
        return self.companies[symbol]

    def add_pool_decision(self, symbol: str, at: str, direction: str = "call", sector: str = "technology",
                          regime: str | None = "range", replayable: bool = True) -> int:
        company_id = self.company_id(symbol, sector)
        manager_input = regime_input(symbol, at, regime) if replayable else {}
        with closing(get_connection(self.db_path)) as conn, conn:
            history_id = conn.execute("INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision, "
                                      "company_id) VALUES (?, ?, ?, ?)", (symbol, at, direction, company_id)).lastrowid
            conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, "
                         "manager_output_json) VALUES (?, ?, ?)",
                         (history_id, json.dumps(manager_input), json.dumps({"recommendation": {"decision": direction}})))
        return history_id

    def seed_pool(self, symbols: int = 24) -> list[int]:
        return [self.add_pool_decision(f"S{symbol:02d}", day(offset), ("call", "put", "neither", "call")[(symbol + k) % 4],
                                       SECTORS[symbol % 3])
                for symbol in range(symbols) for k, offset in enumerate((0, 19))]

    def create_set(self, set_id: str = "bench", **options) -> dict:
        options = {"size": 20, "holdout_share": 0.3, "start_at": WINDOW_START, **options}
        return selection().create_benchmark_set(set_id, db_path=self.db_path, **options)

    def rows(self, query: str, *parameters) -> list[sqlite3.Row]:
        with closing(get_connection(self.db_path)) as conn:
            return conn.execute(query, parameters).fetchall()

    def set_items(self, set_id: str, split: str | None = None) -> list[dict]:
        return benchmarks().list_benchmark_items(set_id, split=split, db_path=self.db_path)


class CreateBenchmarkSetTests(BenchmarkPool):
    def test_a_set_holds_only_replayable_decisions_in_the_window(self) -> None:
        valid = set(self.seed_pool())
        unreplayable = self.add_pool_decision("NOPE", day(1), replayable=False)
        too_early = self.add_pool_decision("EARLY", "2026-06-15T15:00:00+00:00")
        tampered = self.add_pool_decision("TAMP", day(2))
        self.add_candidate("candidate-tampered", tampered, pre_contract_selection={"recommendation": {}})
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE candidate_snapshots SET snapshot_hash = 'tampered' WHERE candidate_id = 'candidate-tampered'")
        summary = self.create_set(size=len(valid))
        ids = {item["manager_decision_history_id"] for item in self.set_items("bench")}
        self.assertEqual(ids, valid)
        self.assertFalse(ids & {unreplayable, too_early, tampered, self.decision_id})
        self.assertEqual(summary["item_count"], len(valid))

    def test_the_end_of_the_window_is_honoured(self) -> None:
        self.seed_pool()
        self.create_set(end_at=day(5))
        self.assertTrue(all(row["decision_run_at"] <= day(5)
                            for row in self.rows("SELECT history.decision_run_at FROM benchmark_items AS item JOIN "
                                                 "manager_decision_history AS history ON history.id = "
                                                 "item.manager_decision_history_id")))

    def test_the_set_records_its_items_and_every_selection_parameter(self) -> None:
        self.seed_pool()
        summary = self.create_set(end_at=day(30))
        stored = benchmarks().benchmark_set_summary("bench", db_path=self.db_path)
        self.assertEqual(stored, summary)
        parameters = summary["selection"]
        self.assertEqual({key: parameters[key] for key in ("size", "holdout_share", "start_at", "end_at")},
                         {"size": 20, "holdout_share": 0.3, "start_at": WINDOW_START, "end_at": day(30)})
        self.assertIsInstance(parameters["seed"], int)
        self.assertEqual(summary["item_count"], len(self.set_items("bench")))
        self.assertEqual(self.rows("SELECT item_count, selection_version FROM benchmark_sets")[0]["item_count"], 20)

    def test_the_summary_counts_splits_and_strata(self) -> None:
        self.seed_pool()
        summary = self.create_set()
        splits = summary["splits"]
        self.assertEqual(splits["dev"]["items"], len(self.set_items("bench", "dev")))
        self.assertEqual(splits["holdout"]["items"], len(self.set_items("bench", "holdout")))
        self.assertEqual(splits["dev"]["items"] + splits["holdout"]["items"], 20)
        self.assertGreater(splits["holdout"]["items"], 0)
        self.assertEqual(sum(row["dev"] + row["holdout"] for row in summary["strata"].values()), 20)
        self.assertEqual({item["stratum"] for item in self.set_items("bench")}, set(summary["strata"]))

    def test_a_primary_catalyst_family_other_than_other_extends_the_stratum(self) -> None:
        labelled = self.add_pool_decision("AAA", day(0), sector="technology")
        plain = self.add_pool_decision("BBB", day(0), sector="technology")
        secondary_only = self.add_pool_decision("CCC", day(0), sector="technology")
        for candidate, decision_id, catalysts in (("c-lab", labelled, (("earnings", 1),)), ("c-plain", plain, (("other", 1),)),
                                                  ("c-sec", secondary_only, (("guidance", 0),))):
            self.add_candidate(candidate, decision_id)
            with closing(get_connection(self.db_path)) as conn, conn:
                for family, primary in catalysts:
                    catalyst_id = f"catalyst:{candidate}"
                    conn.execute("INSERT INTO catalysts (catalyst_id, ticker, catalyst_type, catalyst_subtype, catalyst_family, "
                                 "catalyst_scope, direction, mechanism, expected_horizon, article_count, source_count, "
                                 "independent_root_count, primary_source_present, classification_version, "
                                 "classification_status, state_json, updated_at) VALUES (?, 'MSFT', 't', 's', ?, 'company', "
                                 "'positive', 'm', '1d', 1, 1, 1, 0, 'v', 'ok', '{}', '2026-07-01')", (catalyst_id, family))
                    conn.execute("INSERT INTO candidate_catalysts VALUES (?, ?, ?, ?, '{}')",
                                 (candidate, catalyst_id, family, primary))
        self.create_set(size=3, holdout_share=0.5)
        strata = {item["manager_decision_history_id"]: item["stratum"] for item in self.set_items("bench")}
        self.assertEqual(strata, {labelled: "technology|call|range|2026-07|earnings",
                                  plain: "technology|call|range|2026-07", secondary_only: "technology|call|range|2026-07"})

    def test_a_set_id_cannot_be_created_twice(self) -> None:
        self.seed_pool()
        self.create_set()
        before = self.set_items("bench")
        with self.assertRaises(sqlite3.IntegrityError):
            self.create_set(size=10)
        self.assertEqual(self.set_items("bench"), before)


class ImmutabilityTests(BenchmarkPool):
    def setUp(self) -> None:
        super().setUp()
        self.seed_pool()
        self.create_set()

    def refuse(self, statement: str, *parameters) -> None:
        with closing(get_connection(self.db_path)) as conn, conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute(statement, parameters)

    def record(self, split: str = "dev", model: str = "m", prompts: dict | None = None, replay_strategist: bool = False) -> int:
        return benchmarks().record_benchmark_run(
            set_id="bench", split=split, model=model, prompt_versions=prompts or {"manager": "p1", "strategist": None},
            replay_strategist=replay_strategist, scores={"scored_count": 1}, item_scores={"1": {"status": "scored"}},
            backtest_run_id=None, db_path=self.db_path)

    def test_sets_reject_update_and_delete(self) -> None:
        self.refuse("UPDATE benchmark_sets SET item_count = 1 WHERE set_id = 'bench'")
        self.refuse("DELETE FROM benchmark_sets WHERE set_id = 'bench'")
        self.assertEqual(self.rows("SELECT item_count FROM benchmark_sets")[0]["item_count"], 20)

    def test_items_reject_update_and_delete(self) -> None:
        self.refuse("UPDATE benchmark_items SET split = 'dev' WHERE set_id = 'bench'")
        self.refuse("UPDATE benchmark_items SET stratum = 'x' WHERE set_id = 'bench'")
        self.refuse("DELETE FROM benchmark_items WHERE set_id = 'bench'")
        self.assertEqual(len(self.set_items("bench")), 20)

    def test_runs_reject_update_and_delete(self) -> None:
        run_id = self.record()
        self.refuse("UPDATE benchmark_runs SET scores_json = '{}' WHERE run_id = ?", run_id)
        self.refuse("DELETE FROM benchmark_runs WHERE run_id = ?", run_id)
        self.assertEqual(benchmarks().get_benchmark_run(run_id, db_path=self.db_path)["scores"], {"scored_count": 1})

    def test_a_second_holdout_run_for_the_same_model_prompts_and_flag_is_refused(self) -> None:
        self.record("holdout")
        with self.assertRaises(sqlite3.IntegrityError):
            self.record("holdout")
        self.assertEqual(len(self.rows("SELECT run_id FROM benchmark_runs WHERE split = 'holdout'")), 1)

    def test_holdout_is_once_per_model_prompts_and_strategist_flag_only(self) -> None:
        self.record("holdout")
        self.record("holdout", model="other-model")
        self.record("holdout", prompts={"manager": "p2", "strategist": None})
        self.record("holdout", replay_strategist=True)
        self.assertEqual(len(self.rows("SELECT run_id FROM benchmark_runs WHERE split = 'holdout'")), 4)

    def test_dev_runs_may_repeat(self) -> None:
        first, second = self.record("dev"), self.record("dev")
        self.assertNotEqual(first, second)
        runs = benchmarks().list_benchmark_runs("bench", split="dev", db_path=self.db_path)
        self.assertEqual([run["run_id"] for run in runs], [first, second])

    def test_a_stored_run_reads_back_as_recorded(self) -> None:
        run_id = self.record("dev", model="m", replay_strategist=True)
        self.assertEqual(benchmarks().get_benchmark_run(run_id, db_path=self.db_path),
                         {"run_id": run_id, "set_id": "bench", "split": "dev", "model": "m",
                          "prompt_versions": {"manager": "p1", "strategist": None}, "replay_strategist": True,
                          "backtest_run_id": None, "scores": {"scored_count": 1},
                          "item_scores": {"1": {"status": "scored"}},
                          "created_at": self.rows("SELECT created_at FROM benchmark_runs")[0]["created_at"]})


# --- Runner ---------------------------------------------------------------------------------------------------------

TRADING_DAYS = [date(2026, 7, 21) + timedelta(days=n) for n in range(30) if (date(2026, 7, 21) + timedelta(days=n)).weekday() < 5]
RISING_CLOSES = [(trading_day, 410.0 + 10 * index) for index, trading_day in enumerate(TRADING_DAYS)]  # from a 400.0 reference
AT = {"a": "2026-07-21T15:00:00+00:00", "b": "2026-07-22T15:00:00+00:00", "c": "2026-07-23T15:00:00+00:00",
      "x": "2026-07-24T15:00:00+00:00", "pre": "2026-01-15T15:00:00+00:00"}
STRATUM = {"a": "technology|call|range|2026-07", "b": "technology|put|range|2026-07",
           "c": "technology|neither|range|2026-07", "x": "technology|call|range|2026-07",
           "pre": "technology|call|range|2026-01"}
MODEL_SAYS = {AT["a"]: "call", AT["b"]: "call", AT["c"]: "neither", AT["x"]: "call", AT["pre"]: "call"}


def thesis_check(status: str) -> dict:
    return {"thesis": {"claims": []}, "schema_version": "v1", "policy_version": "p1", "status": status,
            "package_id": "pkg-1", "research_status": "complete", "failures": [], "claims": [],
            "semantic_review": {"status": "skipped"}}


class ReplayDecisions(PatchedCutoffTable, BenchmarkPool):
    """Five MSFT decisions: a, b, c and a pre-cutoff one (pre) are for dev, x for holdout. Prices rise after every decision."""

    def setUp(self) -> None:
        super().setUp()
        directions = {"a": "call", "b": "put", "c": "neither", "x": "call", "pre": "call"}
        self.ids = {name: self.add_pool_decision("MSFT", AT[name], direction) for name, direction in directions.items()}
        self.add_closes(*[(trading_day.isoformat(), price) for trading_day, price in RISING_CLOSES])

    def forward_return(self, name: str, horizon: int) -> float:
        closes = [price for trading_day, price in RISING_CLOSES if trading_day >= date.fromisoformat(AT[name][:10])]
        return closes[horizon - 1] / 400.0 - 1

    def decider(self) -> Mock:
        return Mock(side_effect=lambda payload: {"recommendation": {"decision": MODEL_SAYS[payload["company"]["at"]],
                                                                    "confidence": "high"}})


class AnswersTests(ReplayDecisions):
    def test_run_llm_backtest_returns_its_answers_in_replay_order(self) -> None:
        ids = [self.ids[name] for name in ("c", "a", "pre", "b")]
        with patch.object(llm_backtest, "finalize", return_value=NOT_ELIGIBLE):
            result = llm_backtest.run_llm_backtest(model="test-model", decide=self.decider(), decision_ids=ids,
                                                   db_path=self.db_path)
        self.assertEqual([answer.decision["id"] for answer in result["answers"]],
                         [self.ids["a"], self.ids["b"], self.ids["c"]])
        self.assertEqual([answer.recommendation["decision"] for answer in result["answers"]], ["call", "call", "neither"])


class RunnerFixture(ReplayDecisions):
    """The decisions above as a benchmark set named `bench`."""

    def setUp(self) -> None:
        super().setUp()
        benchmarks().insert_benchmark_set(
            "bench", selection_version="test", selection={"size": 5},
            items=[{"manager_decision_history_id": decision_id, "split": "holdout" if name == "x" else "dev",
                    "stratum": STRATUM[name]} for name, decision_id in self.ids.items()], db_path=self.db_path)

    def run_set(self, model: str = "test-model", split: str = "dev", set_id: str = "bench", decide: Mock | None = None,
                finalized: dict | Callable = NOT_ELIGIBLE, **options) -> dict:
        outcome = {"side_effect": finalized} if callable(finalized) else {"return_value": finalized}
        with patch.object(llm_backtest, "finalize", **outcome):
            return runner().run_benchmark(set_id, split=split, model=model, decide=decide or self.decider(),
                                          db_path=self.db_path, **options)

    def count(self, table: str) -> int:
        return self.rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


class RunBenchmarkTests(RunnerFixture):
    def test_only_the_splits_own_items_are_replayed_and_the_cutoff_exclusion_is_counted(self) -> None:
        decide = self.decider()
        result = self.run_set(decide=decide)
        asked = sorted(call.args[0]["company"]["at"] for call in decide.call_args_list)
        self.assertEqual(asked, [AT["a"], AT["b"], AT["c"]])  # not x (holdout), not the decision outside the set, not pre
        scores = result["scores"]
        self.assertEqual((scores["item_count"], scores["scored_count"]), (4, 3))
        self.assertEqual(scores["replay"]["excluded_pre_knowledge_cutoff"], 1)
        self.assertEqual(scores["replay"]["contamination"]["knowledge_cutoff"], "2026-01-01")
        items = result["item_scores"]
        self.assertEqual(set(items), {str(self.ids[name]) for name in ("a", "b", "c", "pre")})
        self.assertEqual(items[str(self.ids["pre"])]["status"], "excluded_pre_knowledge_cutoff")
        self.assertEqual({items[str(self.ids[name])]["status"] for name in ("a", "b", "c")}, {"scored"})

    def test_the_replayed_backtest_run_is_linked(self) -> None:
        result = self.run_set()
        latest = self.rows("SELECT id, model FROM backtest_runs ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual((result["backtest_run_id"], latest["model"]), (latest["id"], "test-model"))

    def test_item_scores_carry_plan_cs_direction_expression_and_a_fixed_evidence_layer(self) -> None:
        items = self.run_set()["item_scores"]
        for name, answer in (("a", "call"), ("b", "call"), ("c", "neither")):
            with self.subTest(item=name):
                item = items[str(self.ids[name])]
                self.assertEqual((item["decision"], item["stratum"]), (answer, STRATUM[name]))
                direction = item["direction"]
                for horizon in (5, 10):
                    self.assertAlmostEqual(direction["horizon_returns"][str(horizon)], self.forward_return(name, horizon))
                    self.assertEqual(direction["horizon_outcomes"][str(horizon)],
                                     "abstained" if answer == "neither" else "correct")
                self.assertEqual(item["expression"], {"outcome": "not_traded", "pnl_pct": None})
                self.assertIsNone(item["evidence"])

    def test_scores_aggregate_direction_against_the_unconditional_baseline(self) -> None:
        scores = self.run_set()["scores"]
        self.assertEqual(scores["evidence"]["mode"], "fixed")
        five = scores["direction"]["5"]
        self.assertEqual((five["graded"], five["hit_rate"]), (2, 1.0))
        self.assertEqual(five["hit_rate_se"], bootstrap_se([1, 1]))
        self.assertAlmostEqual(five["mean_signed_return"], mean([self.forward_return("a", 5), self.forward_return("b", 5)]))
        self.assertIsNotNone(five["mean_signed_return_se"])
        self.assertEqual(scores["direction"]["10"]["graded"], 2)
        self.assertAlmostEqual(scores["direction"]["abstention_rate"], 1 / 3)
        moves = scores["baseline"]["unconditional_stock_moves"]
        self.assertEqual(moves["count"], 3)
        self.assertAlmostEqual(moves["5d"]["mean_pct"], 100 * mean(self.forward_return(name, 5) for name in "abc"), places=2)
        self.assertEqual(scores["expression"]["traded"], 0)

    def test_scores_are_also_broken_down_by_stratum(self) -> None:
        by_stratum = self.run_set()["scores"]["by_stratum"]
        self.assertEqual(set(by_stratum), {STRATUM[name] for name in ("a", "b", "c", "pre")})
        self.assertEqual(by_stratum[STRATUM["a"]]["scored"], 1)
        self.assertEqual(by_stratum[STRATUM["a"]]["direction"]["5"]["hit_rate"], 1.0)
        self.assertEqual(by_stratum[STRATUM["pre"]]["scored"], 0)

    def test_the_run_is_persisted_and_returned_as_stored(self) -> None:
        result = self.run_set()
        stored = benchmarks().get_benchmark_run(result["run_id"], db_path=self.db_path)
        self.assertEqual(result, stored)
        self.assertEqual((stored["set_id"], stored["split"], stored["model"], stored["replay_strategist"]),
                         ("bench", "dev", "test-model", False))
        self.assertEqual(stored["prompt_versions"], {"manager": llm_backtest.MANAGER_PROMPT_VERSION, "strategist": None})

    def test_stored_answers_reproduce_a_direct_direction_score(self) -> None:
        ids = [self.ids[name] for name in ("a", "b", "c", "pre")]
        direct = run_direction_score(model=llm_backtest.STORED_MODEL, decision_ids=ids, db_path=self.db_path)
        scores = self.run_set(model=llm_backtest.STORED_MODEL)["scores"]
        self.assertEqual(direct["score"]["directional"]["5d"]["hit_rate_pct"], 50.0)
        self.assertAlmostEqual(100 * scores["direction"]["5"]["hit_rate"], direct["score"]["directional"]["5d"]["hit_rate_pct"])
        self.assertEqual(scores["replay"]["excluded_pre_knowledge_cutoff"], 0)
        self.assertIsNone(scores["replay"]["contamination"])
        self.assertEqual(scores["scored_count"], 4)

    def test_a_failed_answer_is_recorded_with_its_reason_and_not_scored(self) -> None:
        def decide(payload):
            if payload["company"]["at"] == AT["c"]:
                raise RuntimeError("model hiccup")
            return {"recommendation": {"decision": "call", "confidence": "high"}}

        result = self.run_set(decide=Mock(side_effect=decide))
        self.assertEqual(result["item_scores"][str(self.ids["c"])]["status"], "model_error")
        self.assertEqual(result["scores"]["scored_count"], 2)

    def test_a_model_with_no_known_cutoff_is_refused_unless_allowed(self) -> None:
        with self.assertRaises(ValueError):
            self.run_set(model="mystery-model")
        self.assertEqual(self.count("benchmark_runs"), 0)
        result = self.run_set(model="mystery-model", allow_unknown_cutoff=True)
        self.assertEqual((result["scores"]["scored_count"], result["scores"]["replay"]["excluded_pre_knowledge_cutoff"]), (4, 0))

    def test_holdout_scores_once_and_a_refusal_comes_before_any_model_call(self) -> None:
        first = self.run_set(split="holdout")
        self.assertEqual(set(first["item_scores"]), {str(self.ids["x"])})
        runs, backtests = self.count("benchmark_runs"), self.count("backtest_runs")
        decide = self.decider()
        with self.assertRaises(ValueError):
            self.run_set(split="holdout", decide=decide)
        decide.assert_not_called()
        self.assertEqual((self.count("benchmark_runs"), self.count("backtest_runs")), (runs, backtests))
        self.assertIsNotNone(self.run_set(split="holdout", model=llm_backtest.STORED_MODEL))  # another model may still score it

    def test_dev_may_be_run_again(self) -> None:
        first, second = self.run_set(), self.run_set()
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["scores"]["direction"], second["scores"]["direction"])

    def test_the_benchmark_never_writes_a_promotion(self) -> None:
        self.run_set()
        self.run_set(split="holdout")
        self.assertEqual(self.count("research_gate_promotions"), 0)


class ExpressionAndEvidenceTests(RunnerFixture):
    def test_a_traded_item_is_scored_from_its_backtest_event(self) -> None:
        contract = {**TRADEABLE["selected_option"], "symbol": "MSFT261016C00400000", "expiration_date": "2026-10-16"}

        def finalize(payload, recommendation, market, **_):  # trades whatever directional answer it is given
            return {**TRADEABLE, "selected_option": contract, "recommendation": recommendation}

        result = self.run_set(finalized=finalize)
        events = self.rows("SELECT manager_decision_history_id, realized_pnl_pct FROM backtest_events WHERE run_id = ?",
                           result["backtest_run_id"])
        self.assertEqual(len(events), 2)  # a and b were called by the model; c abstained
        for event in events:
            with self.subTest(decision=event["manager_decision_history_id"]):
                self.assertIsNotNone(event["realized_pnl_pct"])
                expression = result["item_scores"][str(event["manager_decision_history_id"])]["expression"]
                self.assertEqual(expression["pnl_pct"], event["realized_pnl_pct"])
                self.assertEqual(expression["outcome"], "captured" if event["realized_pnl_pct"] > 0 else "lost_despite_direction")
        self.assertEqual(result["scores"]["expression"]["traded"], 2)

    def test_a_replayed_strategist_gives_evidence_from_its_own_thesis_check(self) -> None:
        # The frozen payloads carry no company_evidence package, so the manager never sees these checks: the
        # evidence layer must read them from the strategist's answer itself.
        first = self.add_pool_decision("MSFT", "2026-07-27T15:00:00+00:00")
        second = self.add_pool_decision("MSFT", "2026-07-28T15:00:00+00:00")
        for number, decision_id in enumerate((first, second), start=1):
            self.add_candidate(f"cand-{number}", decision_id, pre_strategist={"company": {"symbol": "MSFT", "n": number}})
        benchmarks().insert_benchmark_set(
            "strat", selection_version="test", selection={"size": 2}, db_path=self.db_path,
            items=[{"manager_decision_history_id": decision_id, "split": "dev", "stratum": "technology|call|range|2026-07"}
                   for decision_id in (first, second)])
        statuses = {1: "validated", 2: "deferred"}
        strategist = Mock(side_effect=lambda payload: {"recommendation": {"decision": "trade_candidate", "confidence": "high"},
                                                       "thesis_check": thesis_check(statuses[payload["company"]["n"]])})
        manager = Mock(return_value={"recommendation": {"decision": "call", "confidence": "high"}})
        result = self.run_set(set_id="strat", decide=manager, decide_strategist=strategist, replay_strategist=True)
        self.assertEqual(result["scores"]["evidence"]["mode"], "replayed")
        self.assertEqual(result["scores"]["evidence"]["quality_mix"], {"strong": 1, "weak": 1})
        items = result["item_scores"]
        self.assertEqual(items[str(first)]["evidence"]["quality"], "strong")
        self.assertEqual(items[str(second)]["evidence"]["quality"], "weak")
        self.assertEqual(items[str(first)]["evidence"]["source"], "thesis_check")
        self.assertTrue(result["replay_strategist"])
        self.assertIsNotNone(result["prompt_versions"]["strategist"])


# --- Comparison -----------------------------------------------------------------------------------------------------

def scored(decision: str, return_5: float, return_10: float, *, evidence: str | None = None, pnl: float | None = None) -> dict:
    return {"status": "scored", "decision": decision, "stratum": "s",
            "evidence": None if evidence is None else {"quality": evidence, "source": "thesis_check", "signals": {}},
            "direction": {"horizon_returns": {"5": return_5, "10": return_10}},
            "expression": {"outcome": "not_traded" if pnl is None else "captured", "pnl_pct": pnl}}


def unscored(status: str) -> dict:
    return {"status": status, "decision": None, "stratum": "s", "evidence": None, "direction": None, "expression": None}


RUN_A = {"model": "model-a", "item_scores": {
    "1": scored("call", 0.04, 0.06, evidence="strong", pnl=10.0),
    "2": scored("neither", 0.02, -0.01, evidence="adequate", pnl=-5.0),
    "3": scored("call", 0.03, 0.03, evidence="strong", pnl=20.0),
    "4": scored("call", 0.01, 0.01),
    "5": scored("call", 0.01, 0.01),
    "6": scored("call", 0.01, 0.01)}}
RUN_B = {"model": "model-b", "item_scores": {
    "1": scored("put", 0.04, 0.06, evidence="weak"),
    "2": scored("call", 0.02, -0.01, evidence="strong", pnl=5.0),
    "3": scored("call", 0.03, 0.03, evidence="strong", pnl=10.0),
    "4": unscored("excluded_pre_knowledge_cutoff"),
    "5": unscored("model_error"),
    "7": scored("call", 0.01, 0.01)}}


class CompareRunsTests(unittest.TestCase):
    def compare(self, run_a: dict = RUN_A, run_b: dict = RUN_B) -> dict:
        return runner().compare_runs(run_a, run_b)

    def test_only_items_scored_in_both_runs_are_paired(self) -> None:
        self.assertEqual(self.compare()["paired_items"], 3)

    def test_excluded_items_are_listed_with_each_runs_status(self) -> None:
        self.assertEqual(self.compare()["excluded"], {
            "4": {"a": "scored", "b": "excluded_pre_knowledge_cutoff"}, "5": {"a": "scored", "b": "model_error"},
            "6": {"a": "scored", "b": "absent"}, "7": {"a": "absent", "b": "scored"}})

    def test_direction_deltas_are_b_minus_a_in_signed_return_with_win_loss_tie_counts(self) -> None:
        direction = self.compare()["layers"]["direction"]
        # Item order is ascending id; abstaining scores 0 and a put scores minus the stock's return.
        diffs = {"5": [-0.04 - 0.04, 0.02 - 0.0, 0.03 - 0.03], "10": [-0.06 - 0.06, -0.01 - 0.0, 0.03 - 0.03]}
        counts = {"5": (1, 1, 1), "10": (0, 2, 1)}
        for horizon in ("5", "10"):
            with self.subTest(horizon=horizon):
                self.assertEqual(direction[horizon]["n"], 3)
                self.assertAlmostEqual(direction[horizon]["delta"], mean(diffs[horizon]))
                self.assertAlmostEqual(direction[horizon]["se"], bootstrap_se(diffs[horizon]))
                self.assertEqual((direction[horizon]["wins"], direction[horizon]["losses"], direction[horizon]["ties"]),
                                 counts[horizon])

    def test_evidence_delta_scores_strong_adequate_weak_as_one_half_zero(self) -> None:
        evidence = self.compare()["layers"]["evidence"]
        diffs = [0.0 - 1.0, 1.0 - 0.5, 1.0 - 1.0]
        self.assertEqual(evidence["n"], 3)
        self.assertAlmostEqual(evidence["delta"], mean(diffs))
        self.assertAlmostEqual(evidence["se"], bootstrap_se(diffs))

    def test_expression_pairs_only_items_both_runs_traded(self) -> None:
        expression = self.compare()["layers"]["expression"]
        diffs = [5.0 - -5.0, 10.0 - 20.0]
        self.assertEqual(expression["n"], 2)
        self.assertAlmostEqual(expression["delta"], 0.0)
        self.assertAlmostEqual(expression["se"], bootstrap_se(diffs))

    def test_swapping_the_runs_flips_deltas_and_swaps_wins_and_losses(self) -> None:
        forward, backward = self.compare(), self.compare(RUN_B, RUN_A)
        self.assertEqual(backward["paired_items"], forward["paired_items"])
        self.assertAlmostEqual(backward["layers"]["direction"]["5"]["delta"], -forward["layers"]["direction"]["5"]["delta"])
        self.assertEqual((backward["layers"]["direction"]["10"]["wins"], backward["layers"]["direction"]["10"]["losses"]),
                         (forward["layers"]["direction"]["10"]["losses"], forward["layers"]["direction"]["10"]["wins"]))

    def test_runs_that_share_no_scored_item_pair_nothing(self) -> None:
        only_a = {"model": "a", "item_scores": {"1": scored("call", 0.01, 0.01)}}
        only_b = {"model": "b", "item_scores": {"2": scored("call", 0.01, 0.01)}}
        result = self.compare(only_a, only_b)
        self.assertEqual(result["paired_items"], 0)
        self.assertIsNone(result["layers"]["direction"]["5"]["delta"])


# --- CLI ------------------------------------------------------------------------------------------------------------

class BenchmarkCliTests(BenchmarkPool):
    def setUp(self) -> None:
        super().setUp()
        self.seed_pool()
        self.manager = Mock(return_value={"recommendation": {"decision": "neither", "confidence": "high"}})
        for patcher in (patch.object(llm_backtest, "finalize", return_value=NOT_ELIGIBLE),
                        patch.object(llm_backtest, "manager_decider", return_value=self.manager)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *args) -> dict:
        spec = importlib.util.spec_from_file_location("research_cli", RESEARCH_CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = io.StringIO()
        with patch.object(sys, "argv", ["research.py", "--db", str(self.db_path), *args]), redirect_stdout(out):
            module.main()
        return json.loads(out.getvalue())

    def benchmark(self, *args) -> dict:
        return self.run_cli("benchmark", *args)

    def create(self) -> dict:
        return self.benchmark("create", "--set-id", "cli", "--size", "20", "--holdout", "0.3", "--start-at", WINDOW_START)

    def test_create_and_show_print_the_same_summary_as_json(self) -> None:
        created = self.create()
        self.assertEqual(created["item_count"], 20)
        self.assertEqual(created["splits"]["dev"]["items"] + created["splits"]["holdout"]["items"], 20)
        self.assertEqual(self.benchmark("show", "cli"), created)

    def test_holdout_needs_confirmation_and_is_refused_before_any_model_call(self) -> None:
        self.create()
        for args in (("run", "cli", "--model", "local", "--split", "holdout"),
                     ("run", "cli", "--model", "stored", "--split", "holdout")):
            with self.subTest(args=args), redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    self.benchmark(*args)
                self.assertNotIn(raised.exception.code, (0, None))
        self.manager.assert_not_called()
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM benchmark_runs")[0]["n"], 0)
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM backtest_runs")[0]["n"], 0)

    def test_run_and_compare_two_models_on_dev_then_score_holdout_once(self) -> None:
        created = self.create()
        dev_items = created["splits"]["dev"]["items"]
        stored = self.benchmark("run", "cli", "--model", "stored", "--split", "dev")
        local = self.benchmark("run", "cli", "--model", "local", "--split", "dev")
        self.assertEqual((stored["scores"]["scored_count"], local["scores"]["scored_count"]), (dev_items, dev_items))
        self.assertEqual(self.manager.call_count, dev_items)
        compared = self.benchmark("compare", "cli", "--a", "stored", "--b", "local", "--split", "dev")
        self.assertEqual((compared["paired_items"], compared["excluded"]), (dev_items, {}))
        self.assertEqual(set(compared["layers"]), {"evidence", "direction", "expression"})

        holdout = self.benchmark("run", "cli", "--model", "stored", "--split", "holdout", "--confirm-holdout")
        self.assertEqual(holdout["scores"]["item_count"], created["splits"]["holdout"]["items"])
        with self.assertRaises((SystemExit, ValueError)):
            with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
                self.benchmark("run", "cli", "--model", "stored", "--split", "holdout", "--confirm-holdout")
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM research_gate_promotions")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
