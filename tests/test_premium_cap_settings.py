from __future__ import annotations

import unittest
from unittest.mock import patch

from services.trading_config import WorkerSettings  # noqa: E402


class PremiumCapSettingsTests(unittest.TestCase):
    """Cover how the per-trade premium ceiling is resolved from configuration."""

    @staticmethod
    def _settings(**env_overrides) -> WorkerSettings:
        with patch.dict("os.environ", env_overrides, clear=False):
            return WorkerSettings.from_env()

    def test_tighter_limit_wins_when_both_are_configured(self) -> None:
        settings = self._settings(
            MAX_PREMIUM_PER_TRADE_PCT="50",
            MAX_PREMIUM_PER_TRADE_DOLLARS="1000",
        )
        # Percentage is tighter on a small account.
        self.assertEqual(settings.resolve_max_premium_per_trade(1_400.0), 700.0)
        # The absolute ceiling takes over once the account is large enough.
        self.assertEqual(settings.resolve_max_premium_per_trade(10_000.0), 1_000.0)

    def test_cap_is_disabled_when_neither_limit_is_set(self) -> None:
        settings = self._settings(
            MAX_PREMIUM_PER_TRADE_PCT="0",
            MAX_PREMIUM_PER_TRADE_DOLLARS="0",
        )
        self.assertEqual(settings.resolve_max_premium_per_trade(2_000.0), 0.0)


if __name__ == "__main__":
    unittest.main()
