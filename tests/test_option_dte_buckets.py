from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from services.option_dte_buckets import (
    get_dte_bucket_range,
    get_dte_bucket_selection_range,
    target_dte_bucket_to_expiration_filters,
)
from services.trading_config import WorkerSettings, OptionPositionSettings


class OptionDteBucketRegressionTests(unittest.TestCase):
    def test_current_bucket_env_names_load_into_settings_and_giveback_map(self) -> None:
        env_overrides = {
            "OPTION_POSITION_12_20_DTE_TAKE_PROFIT_PCT": "41",
            "OPTION_POSITION_12_20_DTE_STOP_LOSS_PCT": "-27",
            "OPTION_POSITION_20_30_DTE_TAKE_PROFIT_PCT": "58",
            "OPTION_POSITION_20_30_DTE_STOP_LOSS_PCT": "-32",
            "OPTION_POSITION_30_45_DTE_TAKE_PROFIT_PCT": "73",
            "OPTION_POSITION_30_45_DTE_STOP_LOSS_PCT": "-36",
            "OPTION_POSITION_45_60_DTE_TAKE_PROFIT_PCT": "88",
            "OPTION_POSITION_45_60_DTE_STOP_LOSS_PCT": "-42",
            "OPTION_TRAIL_12_20_GIVEBACK_PCT": "0.21",
            "OPTION_TRAIL_20_30_GIVEBACK_PCT": "0.31",
            "OPTION_TRAIL_30_45_GIVEBACK_PCT": "0.41",
            "OPTION_TRAIL_45_60_GIVEBACK_PCT": "0.51",
        }

        with patch.dict(os.environ, env_overrides, clear=True):
            option_position_settings = OptionPositionSettings.from_env()
            front_main_settings = WorkerSettings.from_env()

        rules_by_key = {rule.key: rule for rule in option_position_settings.dte_rules}
        self.assertEqual(rules_by_key["12_20"].take_profit_pct, 41.0)
        self.assertEqual(rules_by_key["12_20"].stop_loss_pct, -27.0)
        self.assertEqual(rules_by_key["20_30"].take_profit_pct, 58.0)
        self.assertEqual(rules_by_key["20_30"].stop_loss_pct, -32.0)
        self.assertEqual(rules_by_key["30_45"].take_profit_pct, 73.0)
        self.assertEqual(rules_by_key["30_45"].stop_loss_pct, -36.0)
        self.assertEqual(rules_by_key["45_60"].take_profit_pct, 88.0)
        self.assertEqual(rules_by_key["45_60"].stop_loss_pct, -42.0)
        self.assertEqual(front_main_settings.option_positions.trailing.giveback_pct_by_bucket_key["12_20"], 0.21)
        self.assertEqual(front_main_settings.option_positions.trailing.giveback_pct_by_bucket_key["20_30"], 0.31)
        self.assertEqual(front_main_settings.option_positions.trailing.giveback_pct_by_bucket_key["30_45"], 0.41)
        self.assertEqual(front_main_settings.option_positions.trailing.giveback_pct_by_bucket_key["45_60"], 0.51)

class BucketSelectionRangeTests(unittest.TestCase):
    """A weeklies-only bucket window reaches forward to the next monthly expiration when it is close."""

    # Calendar facts: third Fridays are 2026-10-16, 2026-11-20 and 2026-12-18.
    TODAY = date(2026, 10, 29)

    def test_weeklies_only_window_extends_to_the_next_monthly(self) -> None:
        # 30-45 DTE from Oct 29 is Nov 28 - Dec 13: only the Dec 4 and Dec 11 weeklies. Dec 18 is 5 days past it.
        self.assertEqual(get_dte_bucket_selection_range("30_45", today=self.TODAY), (30, 50))
        self.assertEqual(
            target_dte_bucket_to_expiration_filters("30_45", today=self.TODAY),
            (None, "2026-11-28", "2026-12-18"),
        )

    def test_window_holding_a_monthly_is_unchanged(self) -> None:
        # 20-30 DTE from Oct 29 is Nov 18 - Nov 28, which holds the Nov 20 monthly.
        self.assertEqual(get_dte_bucket_selection_range("20_30", today=self.TODAY), (20, 30))
        self.assertEqual(
            target_dte_bucket_to_expiration_filters("20_30", today=self.TODAY),
            (None, "2026-11-18", "2026-11-28"),
        )

    def test_monthly_too_far_past_the_window_is_not_reached(self) -> None:
        # 12-20 DTE from Oct 20 is Nov 1 - Nov 9; the Nov 20 monthly is 11 days past it.
        self.assertEqual(get_dte_bucket_selection_range("12_20", today=date(2026, 10, 20)), (12, 20))

    def test_range_never_extends_to_an_earlier_expiration(self) -> None:
        for bucket in ("12_20", "20_30", "30_45", "45_60"):
            for offset in range(0, 35):
                today = self.TODAY + timedelta(days=offset)
                min_days, max_days = get_dte_bucket_selection_range(bucket, today=today)
                self.assertEqual(min_days, get_dte_bucket_range(bucket)[0], (bucket, today))
                self.assertGreaterEqual(max_days, get_dte_bucket_range(bucket)[1], (bucket, today))
                self.assertLessEqual(max_days, get_dte_bucket_range(bucket)[1] + 7, (bucket, today))

    def test_no_bucket_has_no_range(self) -> None:
        self.assertIsNone(get_dte_bucket_selection_range("none", today=self.TODAY))


if __name__ == "__main__":
    unittest.main()
