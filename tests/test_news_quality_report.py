import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from scripts import news_quality_report as report

SCHEMA = """
CREATE TABLE news_articles (id INTEGER PRIMARY KEY, source TEXT, source_type TEXT,
    evidence_score REAL, created_at TEXT);
CREATE TABLE company_news_articles (id INTEGER PRIMARY KEY, company_id INTEGER, created_at TEXT);
CREATE TABLE strategist_runs (id INTEGER PRIMARY KEY, created_at TEXT, input_payload_json TEXT);
CREATE TABLE candidate_rejections (id INTEGER PRIMARY KEY, reason_code TEXT, occurred_at TEXT);
"""

DAY = "2026-10-27"
IN_RANGE = "2026-10-27T09:30:00+00:00"
OUT_OF_RANGE = "2026-10-26T23:59:59+00:00"


def summary(primary_ticker=None, layers=None, root=None):
    return {"primary_ticker": primary_ticker, "evidence_layers": layers or [], "root_source_id": root}


def payload(symbol="AAPL", summaries=(), evidence="default"):
    data = {"company": {"symbol": symbol}, "supporting_articles": {"article_summaries": list(summaries)}}
    if evidence == "default":
        evidence = {"freshness_status": "fresh", "coverage_status": "partial", "gaps": []}
    if evidence is not None:
        data["company_evidence"] = evidence
    return data


class NewsQualityReportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "news.db")
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(SCHEMA)
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)

    def insert(self, sql, *rows):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executemany(sql, rows)

    def add_payloads(self, *payloads, created_at=IN_RANGE):
        self.insert(
            "INSERT INTO strategist_runs (created_at, input_payload_json) VALUES (?, ?)",
            *[(created_at, None if p is None else json.dumps(p)) for p in payloads],
        )

    def rows(self):
        return report.payload_rows(self.conn, DAY, DAY)

    def test_articles_grouped_by_source_and_type_excluding_out_of_range(self):
        self.insert(
            "INSERT INTO news_articles (source, source_type, evidence_score, created_at) VALUES (?, ?, ?, ?)",
            ("reuters", "wire", 0.5, IN_RANGE),
            ("reuters", "wire", 1.0, "2026-10-27T23:59:59+00:00"),
            ("reuters", "blog", 0.2, IN_RANGE),
            ("reuters", "wire", 0.9, OUT_OF_RANGE),
            ("sec", "filing", 0.8, "2026-10-28T00:00:00+00:00"),
        )
        result = {(r["source"], r["source_type"]): r for r in report.articles_by_source(self.conn, DAY, DAY)}
        self.assertEqual(set(result), {("reuters", "wire"), ("reuters", "blog")})
        self.assertEqual(result[("reuters", "wire")]["count"], 2)
        self.assertAlmostEqual(result[("reuters", "wire")]["avg_evidence_score"], 0.75)
        self.assertEqual(result[("reuters", "blog")]["count"], 1)

    def test_articles_end_date_makes_range_inclusive(self):
        self.insert(
            "INSERT INTO news_articles (source, source_type, evidence_score, created_at) VALUES (?, ?, ?, ?)",
            ("a", "wire", 1.0, OUT_OF_RANGE),
            ("a", "wire", 1.0, IN_RANGE),
        )
        (row,) = report.articles_by_source(self.conn, "2026-10-26", DAY)
        self.assertEqual(row["count"], 2)

    def test_companies_with_company_news_counts_distinct_in_range(self):
        self.insert(
            "INSERT INTO company_news_articles (company_id, created_at) VALUES (?, ?)",
            (1, IN_RANGE),
            (1, IN_RANGE),
            (2, IN_RANGE),
            (3, OUT_OF_RANGE),
        )
        self.assertEqual(report.companies_with_company_news(self.conn, DAY, DAY), 2)

    def test_company_direct_counts_each_summary_once(self):
        self.add_payloads(payload("AAPL", [
            summary(primary_ticker="aapl"),                      # ticker match, case-insensitive
            summary(primary_ticker="MSFT", layers=["company_view"]),  # layer match
            summary(primary_ticker="AAPL", layers=["company_view"]),  # both: counted once
            summary(primary_ticker="MSFT", layers=["sector_view"]),   # neither
            summary(),                                                # neither
        ]))
        (row,) = self.rows()
        self.assertEqual(row["symbol"], "AAPL")
        self.assertEqual(row["summary_count"], 5)
        self.assertEqual(row["company_direct"], 3)

    def test_symbol_is_uppercased(self):
        self.add_payloads(payload("aapl", [summary(primary_ticker="AAPL")]))
        (row,) = self.rows()
        self.assertEqual(row["symbol"], "AAPL")
        self.assertEqual(row["company_direct"], 1)

    def test_distinct_root_sources_ignores_empty_and_none(self):
        self.add_payloads(payload(summaries=[
            summary(root="r1"), summary(root="r1"), summary(root="r2"), summary(root=""), summary(root=None),
        ]))
        (row,) = self.rows()
        self.assertEqual(row["distinct_root_sources"], 2)

    def test_missing_company_evidence_gives_none_and_empty_list(self):
        self.add_payloads(
            payload("AAPL", evidence=None),
            payload("MSFT", evidence={"freshness_status": "stale"}),
        )
        rows = {r["symbol"]: r for r in self.rows()}
        self.assertIsNone(rows["AAPL"]["evidence_freshness"])
        self.assertIsNone(rows["AAPL"]["evidence_coverage"])
        self.assertEqual(rows["AAPL"]["evidence_gaps"], [])
        self.assertEqual(rows["MSFT"]["evidence_freshness"], "stale")
        self.assertIsNone(rows["MSFT"]["evidence_coverage"])
        self.assertEqual(rows["MSFT"]["evidence_gaps"], [])

    def test_payload_rows_skip_null_and_out_of_range_runs(self):
        self.add_payloads(None)
        self.add_payloads(payload("TSLA"), created_at=OUT_OF_RANGE)
        self.add_payloads(payload("AAPL"))
        self.assertEqual([r["symbol"] for r in self.rows()], ["AAPL"])
        self.assertIn("run_id", self.rows()[0])

    def test_payload_summary_aggregates(self):
        self.add_payloads(
            payload("AAPL", [summary("AAPL"), summary("AAPL"), summary("MSFT")], evidence={
                "freshness_status": "fresh", "coverage_status": "partial",
                "gaps": ["consensus_unavailable:no_provider", "other"]}),
            payload("MSFT", [summary("MSFT")], evidence={
                "gaps": ["company_evidence_not_collected"]}),
            payload("NVDA", [], evidence={"gaps": ["consensus_unavailable", "company_evidence_not_collected"]}),
            payload("AMD", [summary("AMD"), summary("X"), summary("Y"), summary("Z")], evidence=None),
        )
        result = report.payload_summary(self.rows())
        self.assertEqual(result["payloads"], 4)
        self.assertAlmostEqual(result["mean_company_direct"], (2 + 1 + 0 + 1) / 4)
        self.assertAlmostEqual(result["mean_summary_count"], (3 + 1 + 0 + 4) / 4)
        self.assertEqual(result["consensus_unavailable"], 2)
        self.assertEqual(result["company_evidence_not_collected"], 2)

    def test_payload_summary_with_no_payloads_does_not_crash(self):
        result = report.payload_summary([])
        self.assertEqual(result["payloads"], 0)
        self.assertEqual(result["consensus_unavailable"], 0)

    def test_rejection_counts(self):
        self.insert(
            "INSERT INTO candidate_rejections (reason_code, occurred_at) VALUES (?, ?)",
            ("insufficient_independent_roots", IN_RANGE),
            ("insufficient_independent_roots", IN_RANGE),
            ("insufficient_independent_roots", OUT_OF_RANGE),
            ("insufficient_high_confidence_articles", IN_RANGE),
            ("some_other_reason", IN_RANGE),
        )
        self.assertEqual(report.rejections(self.conn, DAY, DAY), {
            "insufficient_independent_roots": 2,
            "insufficient_high_confidence_articles": 1,
        })

    def test_rejections_zero_when_none(self):
        self.assertEqual(report.rejections(self.conn, DAY, DAY), {
            "insufficient_independent_roots": 0,
            "insufficient_high_confidence_articles": 0,
        })

    def run_main(self, *args):
        out = mock.MagicMock()
        with mock.patch("sys.stdout", out):
            code = report.main(["--db", self.path, *args])
        return code, "".join(c.args[0] for c in out.write.call_args_list)

    def test_main_json_has_expected_top_level_keys(self):
        self.add_payloads(payload("AAPL", [summary("AAPL", root="r1")]))
        _, text = self.run_main("--start", DAY, "--json")
        data = json.loads(text)
        for key in ("articles_by_source", "companies_with_company_news", "payloads", "payload_summary", "rejections"):
            self.assertIn(key, data)
        self.assertEqual(data["payloads"][0]["symbol"], "AAPL")
        self.assertEqual(data["payload_summary"]["payloads"], 1)

    def test_main_text_output_is_not_json_and_mentions_sections(self):
        self.add_payloads(payload("AAPL", [summary("AAPL")]), payload("AAPL", [summary("AAPL")]))
        _, text = self.run_main("--start", DAY)
        self.assertTrue(text.strip())
        with self.assertRaises(ValueError):
            json.loads(text)
        self.assertIn("AAPL", text)

    def test_main_opens_db_with_immutable_uri(self):
        with mock.patch.object(report.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            self.run_main("--start", DAY, "--end", DAY, "--json")
        connect.assert_called_once_with(f"file:{self.path}?immutable=1", uri=True)

    def test_script_does_not_use_get_connection(self):
        self.assertFalse(hasattr(report, "get_connection"))
        self.assertNotIn("get_connection", Path(report.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
