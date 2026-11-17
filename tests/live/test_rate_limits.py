"""One production-sized trading cycle against the real Alpaca and Yahoo APIs, to see whether it hits a rate limit.

The models stay scripted (no paid calls), and the database is a throwaway one seeded with production's
ranking shape (3 sectors x 3 industries x 6 companies). Everything that talks to Alpaca or Yahoo runs for real:
option contracts and chains, quotes, bars, account reads and yfinance history. Every request is timed and
counted at the HTTP layer, beneath alpaca-py's own 429 retries, so no throttling hides.

Guards: only Alpaca and Yahoo hosts are reachable. Any request to Alpaca's trading API other than a GET
(placing, replacing or cancelling an order, closing a position) is refused before it leaves the machine.

Configuration comes from the operator's .env, so the request pattern matches production. The database
path, output paths and telemetry stay test-owned. Run with:

    python3 scripts/project.py test --live-market

Set LIVE_MARKET_REPORT=/path/report.json to keep the measurements. Set ALPACA_RATE_LIMIT_PER_MINUTE to your
plan's limit (default 200, the basic plan). Run it while the market is open: on a closed market the quotes
are stale, so later stages may stop early and make fewer requests than a real cycle.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import tempfile
import time
import unittest
from collections import Counter, defaultdict
from contextlib import ExitStack, closing
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from db_common import get_connection
from tests import ROOT_DIR
from tests.db_fixtures import temporary_database
from tests.e2e.test_trading_cycle import OUTPUT_ENV, TradingCycleHarness
from tests.integration.fakes import ScriptedModel, add_company, grounded_strategist_reply, manager_reply, scripted_models

WINDOW_SECONDS = 60
# Production ranks 3 sectors, 3 industries in each, and 6 companies in each industry.
UNIVERSE = {
    ("technology", "Technology"): {
        ("semiconductors", "Semiconductors"): ("NVDA", "AMD", "AVGO", "QCOM", "MU", "INTC"),
        ("software-infrastructure", "Software - Infrastructure"): ("MSFT", "ORCL", "PLTR", "ADBE", "CRWD", "PANW"),
        ("consumer-electronics", "Consumer Electronics"): ("AAPL", "DELL", "HPQ", "ANET", "CSCO", "IBM"),
    },
    ("financial-services", "Financial Services"): {
        ("banks-diversified", "Banks - Diversified"): ("JPM", "BAC", "WFC", "C", "GS", "MS"),
        ("credit-services", "Credit Services"): ("V", "MA", "AXP", "PYPL", "COF", "SCHW"),
        ("insurance", "Insurance"): ("PGR", "AIG", "MET", "ALL", "TRV", "CB"),
    },
    ("consumer-cyclical", "Consumer Cyclical"): {
        ("internet-retail", "Internet Retail"): ("AMZN", "EBAY", "ETSY", "W", "CHWY", "BABA"),
        ("auto-manufacturers", "Auto Manufacturers"): ("TSLA", "F", "GM", "RIVN", "LCID", "NIO"),
        ("home-improvement", "Home Improvement Retail"): ("HD", "LOW", "TGT", "WMT", "COST", "BBY"),
    },
}
ALLOWED_HOST_SUFFIXES = ("alpaca.markets", "yahoo.com")
# Paths and environment variables the test owns, whatever .env says.
TEST_OWNED = {"DB_PATH", "TELEMETRY_ENABLED", *OUTPUT_ENV}


def _group(host):
    if host.endswith("alpaca.markets"):
        return "alpaca_data" if host.startswith("data.") else "alpaca_trading"
    return "yahoo" if host.endswith("yahoo.com") else "other"


# A path segment that names an API version or a resource; anything else (a symbol, an order id) is collapsed.
RESOURCE_SEGMENT = re.compile(r"v\d+(beta\d+)?|[a-z_-]+")


def endpoint(path):
    """The first three path segments with ids collapsed, e.g. /v2/stocks/NVDA/bars -> /v2/stocks/{id}."""
    return "/".join(part if not part or RESOURCE_SEGMENT.fullmatch(part) else "{id}" for part in path.split("/")[:4])


def peak_in_window(times, window=WINDOW_SECONDS):
    """The most requests that fell inside any `window`-second span."""
    peak, start = 0, 0
    for end, at in enumerate(times):
        while at - times[start] > window:
            start += 1
        peak = max(peak, end - start + 1)
    return peak


class RequestMeter:
    """Times every outgoing HTTP request, refuses hosts outside Alpaca and Yahoo, and refuses broker writes."""

    def __init__(self):
        self.requests = []   # (seconds since start, group, method, path, status)
        self.refused = Counter()
        self.started = time.monotonic()

    def guard(self, original):
        meter = self

        def request(session, method, url, *args, **kwargs):
            host = (urlsplit(str(url)).hostname or "").lower()
            group, path = _group(host), urlsplit(str(url)).path
            if not host.endswith(ALLOWED_HOST_SUFFIXES):
                meter.refused[f"host {host}"] += 1
                raise RuntimeError(f"live rate test only reaches Alpaca and Yahoo, not {host}")
            if group == "alpaca_trading" and str(method).upper() != "GET":
                meter.refused[f"{method} {path}"] += 1
                raise RuntimeError(f"live rate test refuses broker writes: {method} {path}")
            at = time.monotonic() - meter.started
            try:
                response = original(session, method, url, *args, **kwargs)
            except Exception:
                meter.requests.append((at, group, str(method).upper(), path, "error"))
                raise
            meter.requests.append((at, group, str(method).upper(), path, response.status_code))
            return response
        return request

    def report(self, *, limit_per_minute):
        groups = defaultdict(list)
        for at, group, method, path, status in self.requests:
            groups[group].append((at, path, status))
        summary = {}
        for group, rows in sorted(groups.items()):
            statuses = Counter(str(status) for _, _, status in rows)
            endpoints = Counter(endpoint(path) for _, path, _ in rows)
            summary[group] = {"requests": len(rows), "peak_per_minute": peak_in_window([at for at, _, _ in rows]),
                              "limit_per_minute": limit_per_minute if group.startswith("alpaca") else None,
                              "throttled_429": statuses.get("429", 0), "statuses": dict(statuses),
                              "top_endpoints": dict(endpoints.most_common(8))}
        return summary


def _operator_configuration():
    """The operator's .env, except what the test owns. Read here only; values are never printed."""
    from dotenv import dotenv_values
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return {}
    return {key: value for key, value in dotenv_values(env_path).items()
            if value is not None and key not in TEST_OWNED}


def seed_universe():
    """Production's ranking shape, every company with fresh high-confidence news, as the collectors leave it."""
    from market_data.hydration import _record_industry_top_companies_refresh
    company_id, article_id = 0, 0
    for sector_id, ((sector_key, sector_name), industries) in enumerate(UNIVERSE.items(), start=1):
        with closing(get_connection()) as conn, conn:
            conn.execute("INSERT INTO sectors(id,sector_key,name) VALUES (?,?,?)", (sector_id, sector_key, sector_name))
        for (industry_key, industry_name), symbols in industries.items():
            industry_id = company_id // 6 + 1
            with closing(get_connection()) as conn, conn:
                conn.execute("INSERT INTO industries(id,sector_id,industry_key,name) VALUES (?,?,?,?)",
                             (industry_id, sector_id, industry_key, industry_name))
            for symbol in symbols:
                company_id += 1
                add_company(company_id, symbol, (article_id + 1, article_id + 2),
                            industry_id=industry_id, sector_id=sector_id)
                article_id += 2
            # Keeps the seeded companies as the industry's list, so ranking does not replace them from Yahoo.
            _record_industry_top_companies_refresh(industry_key)
    return company_id


class RequestMeterGuardTests(unittest.TestCase):
    """Offline, in every run: the guards must refuse before the real request is ever sent."""

    def setUp(self):
        self.sent = []
        self.meter = RequestMeter()
        self.request = self.meter.guard(lambda session, method, url, *a, **k: self.sent.append((method, url)) or
                                        mock.Mock(status_code=200))

    def test_broker_writes_are_refused_and_never_sent(self):
        for method, url in (("POST", "https://paper-api.alpaca.markets/v2/orders"),
                            ("DELETE", "https://api.alpaca.markets/v2/positions/NVDA"),
                            ("PATCH", "https://api.alpaca.markets/v2/orders/abc")):
            with self.subTest(method=method, url=url), self.assertRaisesRegex(RuntimeError, "refuses broker writes"):
                self.request(None, method, url)
        self.assertEqual(self.sent, [])

    def test_only_alpaca_and_yahoo_hosts_are_reachable(self):
        with self.assertRaisesRegex(RuntimeError, "only reaches Alpaca and Yahoo"):
            self.request(None, "GET", "https://api.openai.com/v1/chat/completions")
        self.request(None, "GET", "https://data.alpaca.markets/v1beta1/options/snapshots/NVDA")
        self.request(None, "GET", "https://query2.finance.yahoo.com/v8/finance/chart/NVDA")
        self.request(None, "GET", "https://paper-api.alpaca.markets/v2/account")
        self.assertEqual(len(self.sent), 3)
        self.assertEqual({group for _, group, *_ in self.meter.requests}, {"alpaca_data", "alpaca_trading", "yahoo"})

    def test_endpoints_keep_the_api_version_and_collapse_ids(self):
        self.assertEqual(endpoint("/v2/stocks/bars"), "/v2/stocks/bars")
        self.assertEqual(endpoint("/v1beta1/options/snapshots/NVDA"), "/v1beta1/options/snapshots")
        self.assertEqual(endpoint("/v2/orders/61e69015-8549"), "/v2/orders/{id}")
        self.assertEqual(endpoint("/v8/finance/chart/NVDA"), "/v8/finance/chart")

    def test_peak_counts_the_busiest_minute(self):
        self.assertEqual(peak_in_window([0, 10, 59, 61, 62, 200]), 4)
        self.assertEqual(peak_in_window([]), 0)


@unittest.skipUnless(os.environ.get("RUN_LIVE_MARKET"), "live market tests run with: scripts/project.py test --live-market")
class LiveMarketRateLimitTests(TradingCycleHarness):
    def setUp(self):
        configuration = _operator_configuration()
        if not (configuration.get("PUBLIC_KEY") and configuration.get("PRIVATE_KEY")):
            self.skipTest("PUBLIC_KEY and PRIVATE_KEY must be set in .env for a live market run")
        temporary_database(self)
        outputs = tempfile.TemporaryDirectory()
        self.addCleanup(outputs.cleanup)
        self.outputs = Path(outputs.name)
        self.enterContext(mock.patch.dict(os.environ, {
            **configuration, "TELEMETRY_ENABLED": "false",
            **{name: str(self.outputs / f"{name.lower()}.json") for name in OUTPUT_ENV}}))
        self.companies = seed_universe()
        self.limit = int(os.environ.get("ALPACA_RATE_LIMIT_PER_MINUTE", "200"))

    def test_one_production_sized_cycle_stays_inside_the_rate_limits(self):
        import curl_cffi.requests
        import requests
        from agent_helpers.market_context import alpaca_base
        from trading_support.clients import get_default_trading_client

        meter = RequestMeter()
        model = ScriptedModel(strategist=grounded_strategist_reply, manager=manager_reply("call"))
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(mock.patch.object(requests.Session, "request", meter.guard(requests.Session.request)))
            enter(mock.patch.object(curl_cffi.requests.Session, "request", meter.guard(curl_cffi.requests.Session.request)))
            # Built from the keys loaded above, not from anything an earlier import cached.
            enter(mock.patch.object(alpaca_base, "_ALPACA_CLIENTS", None))
            enter(scripted_models(model))
            trading_client = get_default_trading_client()
            market_open = bool(getattr(trading_client.get_clock(), "is_open", False))
            worker = self.worker()
            # The harness pins the worker clock to a Wednesday; keep today's real date for a live run.
            stack.enter_context(mock.patch.object(type(worker), "_market_now",
                                                  staticmethod(lambda: datetime.now(ZoneInfo("America/New_York")))))
            # A full account makes production skip the whole pipeline; the worst case for rate limits is a
            # cycle that runs, so the cycle sees no open exposure. The executor still reads the real positions.
            exposure = worker._get_option_exposure_snapshot(trading_client)
            started = time.monotonic()
            result = worker.run_trading_cycle(
                trading_client=trading_client,
                exposure_snapshot=dataclasses.replace(exposure, current_option_exposure=0.0, option_position_count=0))
            duration = time.monotonic() - started

        summary = meter.report(limit_per_minute=self.limit)
        report = {"market_open": market_open, "companies_seeded": self.companies,
                  "account_exposure_full": exposure.exceeds_max_exposure, "exposure_bypassed": True,
                  "strategist_calls": sum(stage == "strategist" for stage, _ in model.prompts),
                  "manager_calls": sum(stage == "manager" for stage, _ in model.prompts),
                  "cycle_seconds": round(duration, 1), "refused": dict(meter.refused), "by_service": summary,
                  "orders_attempted": (result.get("trade_result") or {}).get("submitted_count")}
        print("\n" + json.dumps(report, indent=2, default=str))
        if os.environ.get("LIVE_MARKET_REPORT"):
            Path(os.environ["LIVE_MARKET_REPORT"]).write_text(json.dumps(report, indent=2, default=str))

        self.assertGreater(report["strategist_calls"], 0, "no candidate reached the strategist, so nothing was measured")
        for service, numbers in summary.items():
            with self.subTest(service=service):
                self.assertEqual(numbers["throttled_429"], 0, f"{service} returned 429s: {numbers}")
                if numbers["limit_per_minute"]:
                    self.assertLessEqual(numbers["peak_per_minute"], numbers["limit_per_minute"], numbers)


if __name__ == "__main__":
    unittest.main()
