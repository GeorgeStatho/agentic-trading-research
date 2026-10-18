from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from services.config import FrontMainSettings, OptionPositionSettings


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
            front_main_settings = FrontMainSettings.from_env()

        rules_by_key = {rule.key: rule for rule in option_position_settings.dte_rules}
        self.assertEqual(rules_by_key["12_20"].take_profit_pct, 41.0)
        self.assertEqual(rules_by_key["12_20"].stop_loss_pct, -27.0)
        self.assertEqual(rules_by_key["20_30"].take_profit_pct, 58.0)
        self.assertEqual(rules_by_key["20_30"].stop_loss_pct, -32.0)
        self.assertEqual(rules_by_key["30_45"].take_profit_pct, 73.0)
        self.assertEqual(rules_by_key["30_45"].stop_loss_pct, -36.0)
        self.assertEqual(rules_by_key["45_60"].take_profit_pct, 88.0)
        self.assertEqual(rules_by_key["45_60"].stop_loss_pct, -42.0)
        self.assertEqual(front_main_settings.option_trail_giveback_pct_by_bucket_key["12_20"], 0.21)
        self.assertEqual(front_main_settings.option_trail_giveback_pct_by_bucket_key["20_30"], 0.31)
        self.assertEqual(front_main_settings.option_trail_giveback_pct_by_bucket_key["30_45"], 0.41)
        self.assertEqual(front_main_settings.option_trail_giveback_pct_by_bucket_key["45_60"], 0.51)

if __name__ == "__main__":
    unittest.main()
