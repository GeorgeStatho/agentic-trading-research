from __future__ import annotations

import os
from pathlib import Path
import sys


if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers
from services.option_dte_buckets import DTE_BUCKET_TO_TARGET_OTM_PCT


bootstrap_agent_callers()


def _env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _env_str(name: str, default: str) -> str:
    value = str(os.getenv(name, default)).strip().lower()
    return value or default.lower()


OPTION_SELECTOR_MODE = _env_str("OPTION_SELECTOR_MODE", "hybrid")
ALLOW_RISKY_SIMPLE_FALLBACK = _env_flag("ALLOW_RISKY_SIMPLE_FALLBACK", True)

SIMPLE_REQUIRE_ONE_DOLLAR_OTM = True
SIMPLE_PREFERRED_OTM_DISTANCE = 0.3
SIMPLE_TARGET_ABS_DELTA = 0.28

HYBRID_MIN_DTE = 3
HYBRID_MAX_DTE = 10
HYBRID_MIN_OPEN_INTEREST = 100.0
HYBRID_MAX_SPREAD_PCT = 0.12
HYBRID_MIN_ABS_DELTA = 0.25
HYBRID_TARGET_ABS_DELTA = 0.38
HYBRID_MAX_THETA_TO_PRICE = 0.25
HYBRID_MIN_GAMMA = 0.005
HYBRID_PREFERRED_OTM_DISTANCE = 0.3
HYBRID_REQUIRE_MIN_OTM_DISTANCE = True

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
MAX_TARGET_OTM_DOLLARS = 5.00
SHORT_TERM_EVENT_PRICING_DTE = 7
NEAR_TERM_EVENT_PRICING_DTE = 14
VOLATILITY_CONFIDENCE_LOWEST_ALLOWED = "medium"
