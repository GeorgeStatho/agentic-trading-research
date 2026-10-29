from __future__ import annotations

import os

from services.env_parsing import env_flag
from services.option_dte_buckets import DTE_BUCKET_TO_TARGET_OTM_PCT


def _env_str(name: str, default: str) -> str:
    value = str(os.getenv(name, default)).strip().lower()
    return value or default.lower()


OPTION_SELECTOR_MODE = _env_str("OPTION_SELECTOR_MODE", "hybrid")
ALLOW_RISKY_SIMPLE_FALLBACK = env_flag("ALLOW_RISKY_SIMPLE_FALLBACK", True)

SIMPLE_REQUIRE_ONE_DOLLAR_OTM = True
SIMPLE_PREFERRED_OTM_DISTANCE = 0.3
SIMPLE_TARGET_ABS_DELTA = 0.28

HYBRID_MIN_DTE = 3
HYBRID_MAX_DTE = 10  # HYBRID_MIN_DTE/MAX_DTE apply only when the target bucket is none
HYBRID_MIN_OPEN_INTEREST = 100.0
HYBRID_MAX_SPREAD_PCT = 0.12
HYBRID_MIN_ABS_DELTA = 0.25
HYBRID_TARGET_ABS_DELTA = 0.38
HYBRID_MAX_THETA_TO_PRICE = 0.25
HYBRID_MIN_GAMMA = 0.005  # raw gamma; used only when the stock price is unknown
# Delta change for a 1% stock move (gamma * spot / 100). Raw gamma shrinks with share price.
HYBRID_MIN_GAMMA_PER_PCT_MOVE = 0.01
HYBRID_PREFERRED_OTM_DISTANCE = 0.3
# Off: the OTM target ranks candidates in _hybrid_fast_score; a hard floor dropped the most liquid strikes.
HYBRID_REQUIRE_MIN_OTM_DISTANCE = False

MIN_DTE = 5
MAX_DTE = 14
MIN_OPEN_INTEREST = 200.0
MAX_SPREAD_PCT = 0.10
MIN_ABS_DELTA = 0.30
MAX_ABS_DELTA = 0.55
TARGET_ABS_DELTA = 0.42
MIN_GAMMA = 0.005
MAX_THETA_TO_PRICE = 0.20
REQUIRE_ONE_DOLLAR_OTM = True
PREFERRED_OTM_DISTANCE = 0.3

MIN_TARGET_OTM_DOLLARS = 0.50
MAX_TARGET_OTM_DOLLARS = 10.00
SHORT_TERM_EVENT_PRICING_DTE = 7
NEAR_TERM_EVENT_PRICING_DTE = 14
VOLATILITY_CONFIDENCE_LOWEST_ALLOWED = "medium"
