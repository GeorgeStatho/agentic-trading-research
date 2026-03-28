from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, cast

import yfinance as yf


DATA_DIR = Path(__file__).resolve().parents[3] / "Data"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("yfinance_data")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"yfinance_requests_{timestamp}.log"
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.info("Logging yfinance requests to %s", log_path)
    return logger


LOGGER = _build_logger()


class YFinanceRequestHandler:
    """Small throttled wrapper for yfinance calls."""

    def __init__(self, min_interval_seconds: float = 0.35, max_retries: int = 3, base_backoff_seconds: float = 1.0):
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self._last_request_time = 0.0

    def _sleep_if_needed(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def run(self, func, *args, **kwargs):
        last_exc: Exception | None = None
        context = kwargs.pop("_context", getattr(func, "__name__", "yfinance_call"))
        for attempt in range(self.max_retries + 1):
            self._sleep_if_needed()
            try:
                LOGGER.info("yfinance request start: %s (attempt %s)", context, attempt + 1)
                result = func(*args, **kwargs)
                self._last_request_time = time.monotonic()
                LOGGER.info("yfinance request success: %s (attempt %s)", context, attempt + 1)
                return result
            except Exception as exc:
                last_exc = exc
                self._last_request_time = time.monotonic()
                LOGGER.warning(
                    "yfinance request failed: %s (attempt %s/%s): %s",
                    context,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
                if attempt >= self.max_retries:
                    raise

                normalized_error = str(exc).lower()
                backoff = self.base_backoff_seconds * (attempt + 1)
                if "too many requests" in normalized_error or "rate limit" in normalized_error:
                    backoff *= 2
                LOGGER.info("Sleeping %.2fs before retrying %s", backoff, context)
                time.sleep(backoff)

        if last_exc is not None:
            raise last_exc


REQUEST_HANDLER = YFinanceRequestHandler()


def _require_value(value: Any | None, context: str) -> Any:
    if value is None:
        raise ValueError(f"Missing yfinance value for {context}")
    return value


async def stream_stock_prices(stocks: list[str], on_message: Callable[[dict], None]) -> None:
    async with yf.AsyncWebSocket() as ws:
        await ws.subscribe(stocks)
        await ws.listen(on_message)


def fetch_company_fast_info(company: str) -> Any:
    company_info = cast(
        Any,
        _require_value(
            REQUEST_HANDLER.run(yf.Ticker, company, _context=f"Ticker({company})"),
            f"ticker:{company}",
        ),
    )
    return cast(
        Any,
        _require_value(
            REQUEST_HANDLER.run(lambda: company_info.fast_info, _context=f"fast_info({company})"),
            f"fast_info:{company}",
        ),
    )


def fetch_industry_snapshot(
    industry: str,
    *,
    include_top_growth: bool = True,
    include_top_performing: bool = True,
    include_research_reports: bool = True,
) -> dict[str, Any]:
    LOGGER.info("Loading industry payload for %s", industry)
    industry_info = cast(
        Any,
        _require_value(
            REQUEST_HANDLER.run(yf.Industry, industry, _context=f"Industry({industry})"),
            f"industry:{industry}",
        ),
    )
    return {
        "industry_info": industry_info,
        "top_companies": REQUEST_HANDLER.run(
            lambda: industry_info.top_companies,
            _context=f"industry.top_companies({industry})",
        ),
        "top_growth_companies": (
            REQUEST_HANDLER.run(
                lambda: industry_info.top_growth_companies,
                _context=f"industry.top_growth({industry})",
            )
            if include_top_growth
            else None
        ),
        "top_performing_companies": (
            REQUEST_HANDLER.run(
                lambda: industry_info.top_performing_companies,
                _context=f"industry.top_performing({industry})",
            )
            if include_top_performing
            else None
        ),
        "research_reports": (
            REQUEST_HANDLER.run(
                lambda: industry_info.research_reports,
                _context=f"industry.research_reports({industry})",
            )
            if include_research_reports
            else []
        ),
    }


def fetch_sector_snapshot(
    sector: str,
    *,
    include_research_reports: bool = True,
    include_sector_top_companies: bool = True,
) -> dict[str, Any]:
    LOGGER.info("Loading sector payload for %s", sector)
    sector_info = cast(
        Any,
        _require_value(
            REQUEST_HANDLER.run(yf.Sector, sector, _context=f"Sector({sector})"),
            f"sector:{sector}",
        ),
    )
    return {
        "sector_info": sector_info,
        "industries": cast(
            Any,
            _require_value(
                REQUEST_HANDLER.run(
                    lambda: sector_info.industries,
                    _context=f"sector.industries({sector})",
                ),
                f"sector_industries:{sector}",
            ),
        ),
        "top_companies": (
            REQUEST_HANDLER.run(
                lambda: sector_info.top_companies,
                _context=f"sector.top_companies({sector})",
            )
            if include_sector_top_companies
            else None
        ),
        "research_reports": (
            REQUEST_HANDLER.run(
                lambda: sector_info.research_reports,
                _context=f"sector.research_reports({sector})",
            )
            if include_research_reports
            else []
        ),
    }
