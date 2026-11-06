"""The shared clock, timestamp and number helpers, and the copies they replaced staying gone."""
import ast
from datetime import datetime, timedelta, timezone
import math
import unittest

from tests import ROOT_DIR


# (module file, function) pairs that duplicated a helper in services.core.time_utils or numbers.
REPLACED_COPIES = (
    ("Data/market_db_modules/candidate_lifecycle.py", "now_iso"),
    ("Data/market_db_modules/marketdata_budget.py", "_now"),
    ("Python Scripts/agentCallers/decision_pipeline.py", "_utc_now"),
    ("Python Scripts/agentCallers/agent_pipeline/news_refresh.py", "_utc_now"),
    ("Python Scripts/services/discovery/discovery_queue.py", "utc_now"),
    ("Python Scripts/services/discovery/event_discovery.py", "utc_now"),
    ("Python Scripts/services/evaluation/challenger_evaluation.py", "utc_now"),
    ("Python Scripts/services/evaluation/decision_observations.py", "utc_now"),
    ("Python Scripts/services/evaluation/forward_evaluation.py", "utc_now"),
    ("Python Scripts/services/evaluation/forward_cohorts.py", "utc_now"),
    ("Python Scripts/services/research/research_thesis.py", "utc_now"),
    ("Python Scripts/services/research/research_orchestrator.py", "utc_now"),
    ("Python Scripts/services/thesis/thesis_event_review.py", "utc_now"),
    ("Python Scripts/Webscraping/company_evidence/transport.py", "utc_now"),
    ("api_support/exposure_map.py", "_parse_timestamp"),
    ("Data/market_db_modules/backtesting.py", "_parse_timestamp"),
    ("api_support/request_utils.py", "parse_datetime"),
    ("Python Scripts/performance_metrics.py", "_parse_datetime"),
    ("Python Scripts/services/gates/market_regime.py", "_parse_datetime"),
    ("Python Scripts/Webscraping/market_data/hydration.py", "_parse_refresh_timestamp"),
    ("Data/news_db_modules/provenance.py", "_timestamp"),
    ("Python Scripts/services/trading/outcome_reconciliation.py", "parse_timestamp"),
    ("Data/db_common.py", "coerce_float"),
    ("Data/news_db_modules/shared.py", "coerce_float"),
    ("Data/news_db_modules/shared.py", "coerce_int"),
    ("Data/market_db_modules/position_outcomes.py", "_number"),
    ("Python Scripts/services/research/research_contract.py", "number"),
    ("Python Scripts/services/runtime/execution_telemetry.py", "number"),
    ("Python Scripts/agentCallers/agent_builders/strategist_payload_price.py", "_safe_int"),
    ("Python Scripts/agentCallers/agent_builders/strategist_payload_rollup_common.py", "_safe_int"),
    ("Python Scripts/Webscraping/company_evidence/sec.py", "_int"),
)


class ClockTests(unittest.TestCase):
    def test_utc_now_is_an_aware_utc_datetime(self):
        from services.core.time_utils import utc_now

        now = utc_now()

        self.assertEqual(timedelta(0), now.utcoffset())
        self.assertLess(abs((datetime.now(timezone.utc) - now).total_seconds()), 5)

    def test_utc_now_iso_is_an_iso_string_with_an_explicit_offset(self):
        from services.core.time_utils import utc_now_iso

        text = utc_now_iso()

        self.assertTrue(text.endswith("+00:00"))
        self.assertEqual(timedelta(0), datetime.fromisoformat(text).utcoffset())


class TimestampParsingTests(unittest.TestCase):
    def test_parse_timestamp_keeps_the_offset_and_treats_naive_text_as_utc(self):
        from services.core.time_utils import parse_timestamp

        cases = {
            "2026-01-02T03:04:05Z": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            "  2026-01-02T03:04:05  ": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            "2026-01-02T03:04:05": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, parse_timestamp(text))
        eastern = parse_timestamp("2026-01-02T03:04:05-05:00")
        self.assertEqual(timedelta(hours=-5), eastern.utcoffset())

    def test_parse_utc_timestamp_converts_every_offset_to_utc(self):
        from services.core.time_utils import parse_utc_timestamp

        parsed = parse_utc_timestamp(" 2026-01-02T03:04:05-05:00 ")

        self.assertEqual(datetime(2026, 1, 2, 8, 4, 5, tzinfo=timezone.utc), parsed)
        self.assertEqual(timedelta(0), parsed.utcoffset())

    def test_unparseable_or_empty_input_is_none(self):
        from services.core.time_utils import parse_timestamp, parse_utc_timestamp

        for parse in (parse_timestamp, parse_utc_timestamp):
            for value in (None, "", "   ", "not a time", 0):
                with self.subTest(parse=parse.__name__, value=value):
                    self.assertIsNone(parse(value))


class NumberTests(unittest.TestCase):
    def test_safe_float(self):
        from services.core.numbers import safe_float

        self.assertIsNone(safe_float(None))
        self.assertIsNone(safe_float(""))
        self.assertIsNone(safe_float("abc"))
        self.assertEqual(1.5, safe_float("1.5"))
        self.assertEqual(1.0, safe_float(True))
        self.assertTrue(math.isnan(safe_float("nan")))

    def test_finite_float_drops_nan_and_infinity(self):
        from services.core.numbers import finite_float

        for value in ("nan", float("inf"), "-inf", None, "", "abc"):
            with self.subTest(value=value):
                self.assertIsNone(finite_float(value))
        self.assertEqual(2.0, finite_float("2"))

    def test_safe_int(self):
        from services.core.numbers import safe_int

        self.assertEqual(3, safe_int("3"))
        self.assertEqual(1, safe_int(1.9))
        self.assertEqual(1, safe_int(True))
        for value in (None, "", "1.5", "abc"):
            with self.subTest(value=value):
                self.assertIsNone(safe_int(value))


class ReplacedCopiesTests(unittest.TestCase):
    def test_the_replaced_copies_are_not_defined_again(self):
        redefined = []
        for path, name in REPLACED_COPIES:
            tree = ast.parse((ROOT_DIR / path).read_text(encoding="utf-8"))
            if any(isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body):
                redefined.append(f"{path}:{name}")
        self.assertEqual([], redefined)


if __name__ == "__main__":
    unittest.main()
