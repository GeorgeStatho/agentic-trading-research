from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from db_common import get_connection
from market_db import (
    initialize_database, record_manager_decision_history, list_decision_autopsy_ledger,
    backfill_gate_verdicts, list_shadow_gate_outcomes,
)
from news_db import initialize_database as initialize_news, add_company_news_article, add_news_article
from news_db_modules.provenance import backfill_news_provenance
from services.outcome_reconciliation import closed_round_trip, reconcile_closed_outcomes
from services.regime_history import refresh_regime_history
from services.evidence_continuation_gate import EvidenceContinuationPolicy, _evidence_snapshot
from services.market_regime_gate import MarketRegimePolicy


NOW = datetime(2026, 9, 17, 15, tzinfo=timezone.utc)


def order(order_id, side, quantity, price, day, **extra):
    return {"id": order_id, "side": side, "status": "filled", "filled_qty": str(quantity),
            "filled_avg_price": str(price), "filled_at": f"2026-09-{day:02d}T15:00:00+00:00", **extra}


class ClosedRoundTripTests(unittest.TestCase):
    def test_partial_exits_are_weighted_by_quantity(self):
        result = closed_round_trip([order("buy", "buy", 3, 2, 1), order("s1", "sell", 1, 4, 2),
                                    order("s2", "sell", 2, 1, 3)], "buy")
        self.assertEqual(result["realized_pnl_pct"], 0)
        self.assertEqual(result["closed_at"], "2026-09-03T15:00:00+00:00")

    def test_unfilled_or_partial_exit_does_not_close(self):
        self.assertIsNone(closed_round_trip([order("b", "buy", 3, 2, 1), order("s", "sell", 2, 4, 2)], "b"))

    def test_canceled_partial_fills_contribute_to_closed_outcome(self):
        result = closed_round_trip([order("b", "buy", 2, 2, 1),
                                    order("s1", "sell", 1, 3, 2, status="canceled"),
                                    order("s2", "sell", 1, 1, 3)], "b")
        self.assertEqual(result["realized_pnl_pct"], 0)

    def test_reentries_match_by_order_id_not_latest_symbol(self):
        fills = [order("b", "buy", 1, 2, 1), order("s", "sell", 1, 3, 2),
                 order("b2", "buy", 1, 4, 3), order("s2", "sell", 1, 2, 4)]
        self.assertEqual(closed_round_trip(fills, "b2")["realized_pnl_pct"], -50)

    def test_overlapping_entries_are_not_arbitrarily_attributed(self):
        self.assertIsNone(closed_round_trip([order("b", "buy", 1, 2, 1), order("b2", "buy", 1, 4, 2),
                                             order("s", "sell", 2, 3, 3)], "b"))

    def test_missing_timestamp_and_nonfinite_prices_are_not_guessed(self):
        for extra in ({"filled_at": None}, {"filled_avg_price": "NaN"}, {"status": "partially_filled"}):
            with self.subTest(extra=extra):
                self.assertIsNone(closed_round_trip([order("b", "buy", 1, 2, 1),
                                                     order("s", "sell", 1, 3, 2, **extra)], "b"))


class OrderHistoryTests(unittest.TestCase):
    def test_pagination_overlaps_and_deduplicates_boundary(self):
        from services.trading_gateway import AlpacaTradingGateway
        batch = [SimpleNamespace(id=str(i), symbol="NVDA261016C00150000", qty=1,
                                 submitted_at=NOW + timedelta(seconds=i)) for i in range(501)]
        client = Mock()
        client.get_orders.side_effect = [batch[:500], batch[499:]]
        requests = SimpleNamespace(GetOrdersRequest=lambda **kwargs: SimpleNamespace(**kwargs))
        with patch.dict(sys.modules, {"alpaca.trading.requests": requests}):
            result = AlpacaTradingGateway(paper=True, logger=Mock()).get_option_order_history(
                client, option_symbol="NVDA261016C00150000",
            )
        self.assertEqual(len(result), 501)
        self.assertEqual(client.get_orders.call_args.kwargs["filter"].after,
                         batch[499].submitted_at - timedelta(microseconds=1))

    def test_saturated_page_raises_instead_of_silently_truncating(self):
        from services.trading_gateway import AlpacaTradingGateway
        batch = [SimpleNamespace(id=str(i), submitted_at=NOW) for i in range(500)]
        client = Mock()
        client.get_orders.return_value = batch
        requests = SimpleNamespace(GetOrdersRequest=lambda **kwargs: SimpleNamespace(**kwargs))
        with patch.dict(sys.modules, {"alpaca.trading.requests": requests}):
            with self.assertRaisesRegex(RuntimeError, "did not advance"):
                AlpacaTradingGateway(paper=True, logger=Mock()).get_option_order_history(client, option_symbol="NVDA")


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.db"
        initialize_database(db_path=self.db)
        initialize_news(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO sectors(id, sector_key, name) VALUES (1, 'tech', 'Technology')")
            conn.execute("INSERT INTO industries(id, sector_id, industry_key, name) VALUES (1, 1, 'chips', 'Chips')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (1, 1, 'NVDA', 'Nvidia')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (2, 1, 'AMD', 'AMD')")

    def tearDown(self):
        self.temp.cleanup()

    def decision(self, **extra):
        return record_manager_decision_history({
            "symbol": "NVDA", "decision_run_at": "2026-09-01T14:00:00+00:00",
            "selected_option_symbol": "NVDA261016C00150000", "trade_executed": True,
            "trade_execution_order_id": "b", **extra,
        }, db_path=self.db)

    def test_reconciliation_resolves_autopsy_and_is_idempotent(self):
        self.decision()
        gateway = Mock()
        gateway.get_open_option_positions.return_value = []
        gateway.get_option_order_history.return_value = [order("b", "buy", 2, 2, 1), order("s", "sell", 2, 3, 2)]
        report = reconcile_closed_outcomes(gateway=gateway, trading_client=object(), db_path=self.db)
        self.assertEqual(report["resolved"], 1)
        resolved = list_decision_autopsy_ledger(db_path=self.db, resolved_only=True)
        self.assertEqual((resolved[0]["realized_pnl_pct"], resolved[0]["outcome_status"]), (50, "closed"))
        self.assertEqual(reconcile_closed_outcomes(gateway=gateway, trading_client=object(), db_path=self.db)["examined"], 0)

    def test_broker_failure_never_means_an_empty_book(self):
        self.decision()
        gateway = Mock()
        gateway.get_open_option_positions.side_effect = RuntimeError("offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            reconcile_closed_outcomes(gateway=gateway, trading_client=object(), db_path=self.db)
        self.assertEqual(list_decision_autopsy_ledger(db_path=self.db, resolved_only=True), [])

    def test_open_position_and_preview_do_not_resolve(self):
        self.decision()
        gateway = Mock()
        gateway.get_open_option_positions.return_value = [{"option_symbol": "NVDA261016C00150000"}]
        reconcile_closed_outcomes(gateway=gateway, trading_client=object(), db_path=self.db)
        gateway.get_option_order_history.assert_not_called()
        gateway.get_open_option_positions.return_value = []
        gateway.get_option_order_history.return_value = [order("b", "buy", 1, 2, 1), order("s", "sell", 1, 3, 2)]
        report = reconcile_closed_outcomes(gateway=gateway, trading_client=object(), db_path=self.db, apply=False)
        self.assertEqual((len(report["ready"]), report["resolved"]), (1, 0))
        self.assertEqual(list_decision_autopsy_ledger(db_path=self.db, resolved_only=True), [])

    def article(self, number, domain="reuters.com", body="Reuters reported Nvidia results.", **extra):
        return add_company_news_article(
            company_id=extra.pop("company_id", 1), source=domain, article_key=f"article-{number}",
            title=extra.pop("title", "Nvidia raises full year revenue guidance after record results"),
            source_url=f"https://{domain}/story/{number}", body=body,
            published_at=extra.pop("published_at", "2026-09-17T12:00:00+00:00"),
            recency_score=1, source_reputation_score=.9, directness_score=.9, factuality_score=.9,
            db_path=self.db, **extra,
        )

    def rows(self):
        with closing(get_connection(self.db)) as conn, conn:
            return [dict(row) for row in conn.execute("SELECT * FROM news_articles ORDER BY id")]

    def test_wire_and_three_reposts_count_as_one_root(self):
        for number, domain in enumerate(("reuters.com", "yahoo.com", "calendar.example.com", "newswire.example.com")):
            self.article(number, domain)
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["independent_source_count"] for row in rows}, {1})
        self.assertEqual(len({row["root_event_id"] for row in rows}), 1)
        self.assertEqual(rows[-1]["syndication_parent_id"], rows[0]["id"])

    def test_independent_reporting_updates_all_cluster_members(self):
        self.article(1)
        self.article(2, "newswire.example.com", body="Our reporter interviewed management.")
        self.assertEqual({row["independent_source_count"] for row in self.rows()}, {2})

    def test_primary_source_url_collapses_a_republication(self):
        self.article(1, "sec.gov", body="Filed results")
        self.article(2, "newswire.example.com", raw_json={"provenance": {"primary_source_url": "https://sec.gov/story/1"}})
        self.assertEqual({row["independent_source_count"] for row in self.rows()}, {1})

    def test_different_tickers_dates_and_topics_do_not_merge(self):
        self.article(1)
        self.article(2, company_id=2)
        self.article(3, published_at="2026-09-19T12:00:00+00:00")
        self.article(4, title="Nvidia announces new chief financial officer")
        self.assertEqual(len({row["catalyst_id"] for row in self.rows()}), 4)

    def test_unknown_aggregator_does_not_add_independence(self):
        self.article(1)
        self.article(2, "example.com", body="A report about results")
        self.assertEqual({row["independent_source_count"] for row in self.rows()}, {1})

    def test_repeat_ingestion_and_backfill_preserve_ids_and_first_seen(self):
        self.article(1)
        self.article(2, "newswire.example.com", body="Original reporting")
        before = [(row["id"], row["root_event_id"], row["first_seen_time"]) for row in self.rows()]
        self.article(1)
        backfill_news_provenance(db_path=self.db)
        self.assertEqual(before, [(row["id"], row["root_event_id"], row["first_seen_time"]) for row in self.rows()])

    def test_migrations_preserve_legacy_rows_and_are_repeatable(self):
        article_id = self.article(1)
        decision_id = self.decision()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("DROP INDEX idx_news_provenance_ticker_time")
            conn.execute("DROP INDEX idx_news_provenance_catalyst")
            for column in ("source_type", "primary_ticker", "catalyst_id", "root_event_id", "root_source_id",
                           "syndication_parent_id", "primary_source_url", "event_time", "first_seen_time"):
                conn.execute(f"ALTER TABLE news_articles DROP COLUMN {column}")
            for column in ("exit_reason", "closing_order_ids_json"):
                conn.execute(f"ALTER TABLE manager_decision_history DROP COLUMN {column}")
            conn.execute("DELETE FROM schema_bootstrap")  # older code set this database up
        for _ in range(2):
            initialize_database(db_path=self.db)
            initialize_news(db_path=self.db)
        self.assertEqual(self.rows()[0]["id"], article_id)
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT id, exit_reason FROM manager_decision_history").fetchone()["id"], decision_id)

    def test_provenance_survives_supporting_article_payload(self):
        from agent_builders.strategist_payload_articles import _build_supporting_articles
        self.article(1)
        self.article(2, "newswire.example.com", body="Original reporting")
        evidence = {key: [] for key in ("macro_impacts", "sector_impacts", "industry_impacts", "company_impacts", "sector_rss_articles")}
        evidence["company_impacts"] = [{**row, "article_id": row["id"]} for row in self.rows()]
        supporting = _build_supporting_articles(evidence, summary_article_limit=10, full_article_limit=10)
        result = _evidence_snapshot({"grounding": {"article_ids": [1, 2]}, "supporting_articles": supporting}, EvidenceContinuationPolicy())
        self.assertEqual(result["independent_source_count"], 2)

    def test_autopsy_serialization_returns_fill_outcome_without_a_mark(self):
        try:
            from api_support.decision_autopsy import _serialize_ledger_row
        except ImportError:
            self.skipTest("API dependencies unavailable")
        self.decision(outcome_status="closed", realized_pnl_pct=12.5, closed_at=NOW.isoformat())
        serialized = _serialize_ledger_row(list_decision_autopsy_ledger(db_path=self.db)[0])
        self.assertEqual(serialized["outcome"]["realized_pnl_pct"], 12.5)
        self.assertEqual(serialized["outcome"]["outcome_status"], "closed")

    def test_shadow_report_uses_realized_outcomes_and_keeps_unresolved(self):
        output = {"entry_gate": {"would_allow": False, "allowed": True, "enforcement_mode": "shadow", "policy_version": "v1"}}
        self.decision(manager_output_json=output, latest_trade_pnl_pct=99)
        self.decision(manager_output_json=output, outcome_status="closed", realized_pnl_pct=-10,
                      closed_at="2026-09-02T12:00:00+00:00")
        self.assertEqual(backfill_gate_verdicts(db_path=self.db), 0)
        row = list_shadow_gate_outcomes(db_path=self.db)[0]
        self.assertEqual((row["trade_count"], row["resolved_count"], row["losers"], row["sum_realized_pnl_pct"]), (2, 1, 1, -10))

    def test_regime_history_persists_and_falls_back_without_redating(self):
        bars = [{"bar_at": (NOW - timedelta(days=day)).isoformat(), "close": 100 + day}
                for day in range(1, 270)]
        fetcher = Mock(return_value=("fixture", bars))
        closes, timestamp = refresh_regime_history("SPY", as_of=NOW, db_path=self.db, fetcher=fetcher)
        self.assertEqual(len(closes), 252)
        fetcher.side_effect = RuntimeError("provider down")
        fallback = refresh_regime_history("SPY", as_of=NOW + timedelta(days=4), db_path=self.db, fetcher=fetcher)
        self.assertEqual(fallback, (closes, timestamp))


class ProvenanceGateTests(unittest.TestCase):
    def test_gate_counts_roots_not_publishers(self):
        payload = {"grounding": {"article_ids": [1, 2]}, "supporting_articles": {"article_summaries": [
            {"article_id": 1, "source": "Yahoo", "evidence_score": .9, "root_event_id": "r1", "root_source_id": "wire:reuters"},
            {"article_id": 2, "source": "CNBC", "evidence_score": .9, "root_event_id": "r1", "root_source_id": "wire:reuters"},
        ]}}
        self.assertEqual(_evidence_snapshot(payload, EvidenceContinuationPolicy())["independent_source_count"], 1)

    def test_unlinked_wire_articles_do_not_manufacture_independence(self):
        payload = {"grounding": {"article_ids": [1, 2]}, "supporting_articles": {"article_summaries": [
            {"article_id": 1, "evidence_score": .9, "root_event_id": "r1", "root_source_id": "wire:reuters"},
            {"article_id": 2, "evidence_score": .9, "root_event_id": "r2", "root_source_id": "wire:reuters"},
        ]}}
        self.assertEqual(_evidence_snapshot(payload, EvidenceContinuationPolicy())["independent_source_count"], 1)

    def test_runtime_defaults_remain_shadow(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(EvidenceContinuationPolicy.from_env().enforcement_mode, "shadow")
            self.assertEqual(MarketRegimePolicy.from_env().mode, "shadow")


if __name__ == "__main__":
    unittest.main()
