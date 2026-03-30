from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import NotRequired, TypedDict

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from pipelines._constants import FOOL_EXCHANGE_SLUGS
from pipelines._shared import build_source_url, supports_source_type
from urlFactories import COMPANY_NEWS_SOURCES, INDUSTRY_NEWS_SOURCES


class CompanySourceJob(TypedDict):
    url: str
    company: dict
    source_name: str
    source_type: str
    search_term: str


class IndustrySourceJob(TypedDict):
    url: str
    industry: dict
    source_name: str
    source_type: str
    search_term: NotRequired[str]


PipelineJob = CompanySourceJob | IndustrySourceJob


def _build_company_search_terms(company: dict) -> list[str]:
    company_name = " ".join((company.get("name") or "").split()).strip()
    return [company_name] if company_name else []


def build_company_source_job(
    url: str,
    company: dict,
    source_name: str,
    source_type: str,
    search_term: str,
) -> CompanySourceJob | None:
    normalized_url = str(url).strip()
    normalized_search_term = " ".join(str(search_term or "").split()).strip()

    if not normalized_url or not normalized_search_term:
        return None
    if not supports_source_type(normalized_url, source_type):
        return None

    return {
        "url": normalized_url,
        "company": company,
        "source_name": source_name,
        "source_type": source_type,
        "search_term": normalized_search_term,
    }


def build_yahoo_news_jobs(
    company: dict,
    news_pairs: list[tuple[str, str]],
    *,
    source_name: str = "yahoo_finance",
    source_type: str = "article",
) -> list[CompanySourceJob]:
    jobs: list[CompanySourceJob] = []
    seen_urls: set[str] = set()

    search_term = " ".join((company.get("name") or "").split()).strip()
    if not search_term:
        return jobs

    for title, url in news_pairs:
        normalized_url = str(url).strip()
        normalized_title = " ".join(str(title or "").split()).strip()

        if not normalized_url or normalized_url in seen_urls:
            continue

        job = build_company_source_job(
            url=normalized_url,
            company=company,
            source_name=source_name,
            source_type=source_type,
            search_term=normalized_title or search_term,
        )
        if job is None:
            continue

        jobs.append(job)
        seen_urls.add(normalized_url)

    return jobs


def _get_company_exchange_slug(company: dict) -> str | None:
    raw_json = company.get("raw_json")
    if not raw_json:
        return None

    payload: dict | None = None
    if isinstance(raw_json, str):
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            payload = None
    elif isinstance(raw_json, dict):
        payload = raw_json

    if not payload:
        return None

    exchange = str(payload.get("exchange") or "").strip().upper()
    if not exchange:
        return None
    return FOOL_EXCHANGE_SLUGS.get(exchange, exchange.lower())


def _build_company_source_url(company: dict, search_term: str, source_config: dict) -> str | None:
    company_specific_type = source_config.get("company_specific")
    if company_specific_type == "fool_quote":
        symbol = str(company.get("symbol") or "").strip().lower()
        exchange_slug = _get_company_exchange_slug(company)
        if not symbol or not exchange_slug:
            return None
        return source_config["url"].format(exchange=exchange_slug, symbol=symbol)

    return build_source_url(search_term, source_config)


def build_company_source_jobs(companies: list[dict]) -> list[CompanySourceJob]:
    jobs: list[CompanySourceJob] = []

    for company in companies:
        for search_term in _build_company_search_terms(company):
            for source_name, source_config in COMPANY_NEWS_SOURCES.items():
                url = _build_company_source_url(company, search_term, source_config)
                if not url:
                    continue
                job = build_company_source_job(
                    url=url,
                    company=company,
                    source_name=source_name,
                    source_type=source_config["type"],
                    search_term=search_term,
                )
                if job is not None:
                    jobs.append(job)

    return jobs


def build_industry_source_jobs(industries: list[dict]) -> list[IndustrySourceJob]:
    jobs: list[IndustrySourceJob] = []

    for industry in industries:
        for source_name, source_config in INDUSTRY_NEWS_SOURCES.items():
            url = build_source_url(industry["name"], source_config)
            if not supports_source_type(url, source_config["type"]):
                continue
            job: IndustrySourceJob = {
                "url": url,
                "industry": industry,
                "source_name": source_name,
                "source_type": source_config["type"],
            }
            if source_config["type"] == "search":
                job["search_term"] = industry["name"]
            jobs.append(job)

    return jobs


def group_jobs_by_url(jobs: list[PipelineJob]) -> dict[str, list[PipelineJob]]:
    jobs_by_url: dict[str, list[PipelineJob]] = {}
    for job in jobs:
        jobs_by_url.setdefault(job["url"], []).append(job)
    return jobs_by_url


def unique_job_urls(jobs: list[PipelineJob]) -> list[str]:
    return list(group_jobs_by_url(jobs).keys())
