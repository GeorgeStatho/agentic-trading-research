from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_pipeline.main import get_current_pipeline_targets

from api_support.common import parse_datetime, trim_text
from api_support.context import (
    ANALYZED_COMPANY_NEWS_DEFAULT_PAGE_SIZE,
    DB_PATH,
    get_connection,
)

VALID_ANALYZED_COMPANY_NEWS_VIEWS = {"company", "sector", "industry", "macro"}


def _empty_confidence_counts() -> dict[str, int]:
    return {"high": 0, "medium": 0, "low": 0}


def _build_news_section_payload() -> dict:
    return {
        "article_count": 0,
        "latest_published_at": "",
        "confidence_counts": _empty_confidence_counts(),
        "articles": [],
        "_articles_by_id": {},
    }


def _build_analyzed_company_news_pagination_payload(
    *,
    page: int,
    page_size: int,
    total_company_count: int,
    page_company_count: int,
) -> dict:
    if total_company_count <= 0:
        return {
            "page": 1,
            "page_size": page_size,
            "total_pages": 0,
            "has_previous_page": False,
            "has_next_page": False,
            "page_start": 0,
            "page_end": 0,
            "page_company_count": 0,
            "company_count": 0,
        }

    total_pages = (total_company_count + page_size - 1) // page_size
    normalized_page = max(1, min(page, total_pages))
    page_start = ((normalized_page - 1) * page_size) + 1 if page_company_count else 0
    page_end = page_start + page_company_count - 1 if page_company_count else 0
    return {
        "page": normalized_page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_previous_page": normalized_page > 1,
        "has_next_page": normalized_page < total_pages,
        "page_start": page_start,
        "page_end": page_end,
        "page_company_count": page_company_count,
        "company_count": total_company_count,
    }


def _empty_analyzed_company_news_payload(*, page: int, page_size: int, total_company_count: int = 0) -> dict:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "lookup_query": "",
        "lookup_active": False,
        "lookup_match_count": total_company_count,
        **_build_analyzed_company_news_pagination_payload(
            page=page,
            page_size=page_size,
            total_company_count=total_company_count,
            page_company_count=0,
        ),
        "article_count": 0,
        "section_article_counts": {
            "company": 0,
            "sector": 0,
            "industry": 0,
            "macro": 0,
        },
        "companies": [],
    }


def normalize_analyzed_company_news_view(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_ANALYZED_COMPANY_NEWS_VIEWS else "company"


def normalize_company_lookup_query(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _score_company_lookup_match(company: dict[str, Any], needle: str) -> tuple[int, str, str]:
    symbol = str(company.get("symbol") or "").strip()
    name = str(company.get("name") or "").strip()
    symbol_lower = symbol.lower()
    name_lower = name.lower()

    if symbol_lower == needle:
        return (0, symbol_lower, name_lower)
    if name_lower == needle:
        return (1, symbol_lower, name_lower)
    if symbol_lower.startswith(needle):
        return (2, symbol_lower, name_lower)
    if name_lower.startswith(needle):
        return (3, symbol_lower, name_lower)
    if needle in symbol_lower:
        return (4, symbol_lower, name_lower)
    return (5, symbol_lower, name_lower)


def _lookup_companies_for_news(conn, company_lookup_query: str) -> list[dict[str, Any]]:
    normalized_query = normalize_company_lookup_query(company_lookup_query).lower()
    if not normalized_query:
        return []

    rows = conn.execute(
        """
        SELECT
            c.id AS company_id,
            c.symbol,
            c.name,
            i.id AS industry_id,
            i.industry_key,
            i.name AS industry_name,
            s.id AS sector_id,
            s.sector_key,
            s.name AS sector_name
        FROM companies AS c
        JOIN industries AS i ON i.id = c.industry_id
        JOIN sectors AS s ON s.id = i.sector_id
        WHERE EXISTS (SELECT 1 FROM company_news_articles AS cna WHERE cna.company_id = c.id)
           OR EXISTS (SELECT 1 FROM company_opportunist_impacts AS coi WHERE coi.company_id = c.id)
        ORDER BY c.symbol ASC, c.name ASC
        """
    ).fetchall()

    exact_matches: list[dict[str, Any]] = []
    partial_matches: list[dict[str, Any]] = []
    for row in rows:
        company = dict(row)
        symbol_lower = str(company.get("symbol") or "").strip().lower()
        name_lower = str(company.get("name") or "").strip().lower()
        if not symbol_lower and not name_lower:
            continue
        if symbol_lower == normalized_query or name_lower == normalized_query:
            exact_matches.append(company)
            continue
        if (
            symbol_lower.startswith(normalized_query)
            or name_lower.startswith(normalized_query)
            or normalized_query in symbol_lower
            or normalized_query in name_lower
        ):
            partial_matches.append(company)

    matches = exact_matches if exact_matches else partial_matches
    matches.sort(key=lambda company: _score_company_lookup_match(company, normalized_query))
    return matches


def _load_default_companies_for_news(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            c.id AS company_id,
            c.symbol,
            c.name,
            i.id AS industry_id,
            i.industry_key,
            i.name AS industry_name,
            s.id AS sector_id,
            s.sector_key,
            s.name AS sector_name,
            MAX(na.published_at) AS latest_company_news_published_at
        FROM companies AS c
        JOIN industries AS i ON i.id = c.industry_id
        JOIN sectors AS s ON s.id = i.sector_id
        JOIN company_news_articles AS cna ON cna.company_id = c.id
        JOIN news_articles AS na ON na.id = cna.article_id
        GROUP BY
            c.id,
            c.symbol,
            c.name,
            i.id,
            i.industry_key,
            i.name,
            s.id,
            s.sector_key,
            s.name
        ORDER BY MAX(na.published_at) DESC, c.symbol ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _build_company_news_entry(base_company: dict) -> dict:
    return {
        "company_id": int(base_company.get("company_id") or 0),
        "symbol": str(base_company.get("symbol") or "").strip().upper(),
        "name": str(base_company.get("name") or "").strip(),
        "industry_id": int(base_company["industry_id"]) if base_company.get("industry_id") else None,
        "industry_key": str(base_company.get("industry_key") or "").strip(),
        "industry_name": str(base_company.get("industry_name") or "").strip(),
        "sector_id": int(base_company["sector_id"]) if base_company.get("sector_id") else None,
        "sector_key": str(base_company.get("sector_key") or "").strip(),
        "sector_name": str(base_company.get("sector_name") or "").strip(),
        "analyzed_article_count": 0,
        "latest_published_at": "",
        "confidence_counts": _empty_confidence_counts(),
        "company_news": _build_news_section_payload(),
        "sector_news": _build_news_section_payload(),
        "industry_news": _build_news_section_payload(),
        "macro_news": _build_news_section_payload(),
    }


def _append_scoped_news_row(section: dict, row_data: dict) -> None:
    try:
        article_id = int(row_data.get("article_id"))
    except (TypeError, ValueError):
        return

    articles_by_id = section["_articles_by_id"]
    article_entry = articles_by_id.get(article_id)
    if article_entry is None:
        article_entry = {
            "article_id": article_id,
            "title": str(row_data.get("title") or "").strip(),
            "summary": str(row_data.get("summary") or "").strip(),
            "body_preview": trim_text(str(row_data.get("body_preview") or row_data.get("summary") or "").strip(), 320),
            "source": str(row_data.get("source") or "").strip(),
            "source_url": str(row_data.get("source_url") or "").strip(),
            "published_at": str(row_data.get("published_at") or "").strip(),
            "assessments": [],
            "_assessment_keys": set(),
        }
        articles_by_id[article_id] = article_entry
        section["articles"].append(article_entry)

    confidence = str(row_data.get("confidence") or "").strip().lower()
    impact_direction = str(row_data.get("impact_direction") or "").strip().lower()
    impact_magnitude = str(row_data.get("impact_magnitude") or "").strip().lower()
    reason = str(row_data.get("reason") or "").strip()
    impact_created_at = str(row_data.get("impact_created_at") or "").strip()
    news_scope = str(row_data.get("news_scope") or "").strip().lower()
    if not any((confidence, impact_direction, impact_magnitude, reason, impact_created_at, news_scope)):
        return
    assessment_key = "|".join(
        [
            confidence,
            impact_direction,
            impact_magnitude,
            reason,
            impact_created_at,
            news_scope,
        ]
    )
    if assessment_key in article_entry["_assessment_keys"]:
        return

    article_entry["_assessment_keys"].add(assessment_key)
    article_entry["assessments"].append(
        {
            "confidence": confidence,
            "impact_direction": impact_direction,
            "impact_magnitude": impact_magnitude,
            "reason": reason,
            "created_at": impact_created_at,
            "news_scope": news_scope,
        }
    )
    if confidence in section["confidence_counts"]:
        section["confidence_counts"][confidence] += 1


def _finalize_news_section_payload(section: dict) -> dict:
    articles_by_id = section.pop("_articles_by_id", None)
    del articles_by_id

    for article in section["articles"]:
        assessment_keys = article.pop("_assessment_keys", None)
        del assessment_keys

    section["article_count"] = len(section["articles"])
    section["latest_published_at"] = (
        str(section["articles"][0].get("published_at") or "").strip() if section["articles"] else ""
    )
    return section


def _combine_confidence_counts(*counts: dict[str, int]) -> dict[str, int]:
    combined = _empty_confidence_counts()
    for count_set in counts:
        if not isinstance(count_set, dict):
            continue
        for key in combined:
            combined[key] += int(count_set.get(key) or 0)
    return combined


def _max_timestamp_value(*values: str) -> str:
    latest_value = ""
    latest_dt = None
    for value in values:
        parsed = parse_datetime(value)
        if parsed is None:
            continue
        normalized = parsed.astimezone(timezone.utc)
        if latest_dt is None or normalized > latest_dt:
            latest_dt = normalized
            latest_value = str(value or "").strip()
    return latest_value


def build_analyzed_company_news_payload(
    *,
    page: int = 1,
    page_size: int = ANALYZED_COMPANY_NEWS_DEFAULT_PAGE_SIZE,
    view: str = "company",
    company_lookup: str = "",
) -> dict:
    active_view = normalize_analyzed_company_news_view(view)
    with get_connection(DB_PATH) as conn:
        normalized_company_lookup = normalize_company_lookup_query(company_lookup)
        company_lookup_by_id: dict[int, dict] = {}
        all_company_ids: list[int] = []

        if normalized_company_lookup:
            matched_companies = _lookup_companies_for_news(conn, normalized_company_lookup)
            for company in matched_companies:
                company_id = int(company["company_id"])
                all_company_ids.append(company_id)
                company_lookup_by_id[company_id] = company
        else:
            default_companies = _load_default_companies_for_news(conn)
            if not default_companies:
                payload = _empty_analyzed_company_news_payload(page=page, page_size=page_size)
                payload["lookup_query"] = normalized_company_lookup
                payload["lookup_active"] = False
                payload["lookup_match_count"] = 0
                return payload

            for company in default_companies:
                company_id = int(company["company_id"])
                all_company_ids.append(company_id)
                company_lookup_by_id[company_id] = {
                    "company_id": company_id,
                    "symbol": str(company.get("symbol") or "").strip().upper(),
                    "name": str(company.get("name") or "").strip(),
                    "industry_id": company.get("industry_id"),
                    "industry_key": str(company.get("industry_key") or "").strip(),
                    "industry_name": str(company.get("industry_name") or "").strip(),
                    "sector_id": company.get("sector_id"),
                    "sector_key": str(company.get("sector_key") or "").strip(),
                    "sector_name": str(company.get("sector_name") or "").strip(),
                }

        total_company_count = len(all_company_ids)
        if not all_company_ids:
            payload = _empty_analyzed_company_news_payload(
                page=page,
                page_size=page_size,
                total_company_count=0,
            )
            payload["lookup_query"] = normalized_company_lookup
            payload["lookup_active"] = bool(normalized_company_lookup)
            payload["lookup_match_count"] = 0
            return payload

        total_pages = (total_company_count + page_size - 1) // page_size
        normalized_page = max(1, min(page, total_pages))
        page_start_index = (normalized_page - 1) * page_size
        company_ids = all_company_ids[page_start_index : page_start_index + page_size]

        if not company_ids:
            payload = _empty_analyzed_company_news_payload(
                page=normalized_page,
                page_size=page_size,
                total_company_count=total_company_count,
            )
            payload["lookup_query"] = normalized_company_lookup
            payload["lookup_active"] = bool(normalized_company_lookup)
            payload["lookup_match_count"] = total_company_count
            return payload

        placeholders = ",".join("?" for _ in company_ids)
        metadata_rows = conn.execute(
            f"""
            SELECT
                c.id AS company_id,
                c.symbol,
                c.name,
                i.id AS industry_id,
                i.industry_key,
                i.name AS industry_name,
                s.id AS sector_id,
                s.sector_key,
                s.name AS sector_name
            FROM companies AS c
            JOIN industries AS i ON i.id = c.industry_id
            JOIN sectors AS s ON s.id = i.sector_id
            WHERE c.id IN ({placeholders})
            """,
            tuple(company_ids),
        ).fetchall()

        company_rows = []
        if active_view == "company":
            company_rows = conn.execute(
                f"""
                SELECT
                    cna.article_id,
                    cna.company_id,
                    c.symbol,
                    c.name AS company_name,
                    i.industry_key,
                    s.sector_key,
                    coi.confidence,
                    coi.impact_direction,
                    coi.impact_magnitude,
                    coi.reason,
                    coi.created_at AS impact_created_at,
                    na.title,
                    na.summary,
                    substr(COALESCE(na.body, na.summary, ''), 1, 320) AS body_preview,
                    na.source,
                    na.source_url,
                    na.published_at
                FROM company_news_articles AS cna
                JOIN companies AS c ON c.id = cna.company_id
                JOIN industries AS i ON i.id = c.industry_id
                JOIN sectors AS s ON s.id = i.sector_id
                JOIN news_articles AS na ON na.id = cna.article_id
                LEFT JOIN company_opportunist_impacts AS coi
                    ON coi.article_id = cna.article_id
                   AND coi.company_id = cna.company_id
                WHERE cna.company_id IN ({placeholders})
                ORDER BY c.symbol ASC, na.published_at DESC, cna.article_id DESC, coi.created_at DESC
                """,
                tuple(company_ids),
            ).fetchall()

        metadata_by_company_id: dict[int, dict] = {}
        industry_ids: list[int] = []
        sector_ids: list[int] = []
        seen_industry_ids: set[int] = set()
        seen_sector_ids: set[int] = set()
        for row in metadata_rows:
            row_data = dict(row)
            company_id = int(row_data["company_id"])
            metadata_by_company_id[company_id] = row_data

            try:
                industry_id = int(row_data["industry_id"])
            except (TypeError, ValueError):
                industry_id = 0
            if industry_id and industry_id not in seen_industry_ids:
                seen_industry_ids.add(industry_id)
                industry_ids.append(industry_id)

            try:
                sector_id = int(row_data["sector_id"])
            except (TypeError, ValueError):
                sector_id = 0
            if sector_id and sector_id not in seen_sector_ids:
                seen_sector_ids.add(sector_id)
                sector_ids.append(sector_id)

        industry_rows = []
        if industry_ids and active_view == "industry":
            industry_placeholders = ",".join("?" for _ in industry_ids)
            industry_rows = conn.execute(
                f"""
                SELECT
                    ioi.article_id,
                    ioi.industry_id,
                    i.industry_key,
                    i.name AS industry_name,
                    ioi.confidence,
                    ioi.impact_direction,
                    ioi.impact_magnitude,
                    ioi.reason,
                    ioi.created_at AS impact_created_at,
                    na.title,
                    na.summary,
                    substr(COALESCE(na.body, na.summary, ''), 1, 320) AS body_preview,
                    na.source,
                    na.source_url,
                    na.published_at
                FROM industry_opportunist_impacts AS ioi
                JOIN industries AS i ON i.id = ioi.industry_id
                JOIN news_articles AS na ON na.id = ioi.article_id
                LEFT JOIN industry_opportunist_article_processing AS iop
                    ON iop.article_id = ioi.article_id
                WHERE ioi.industry_id IN ({industry_placeholders})
                ORDER BY i.industry_key ASC, na.published_at DESC, ioi.article_id DESC, ioi.created_at DESC
                """,
                tuple(industry_ids),
            ).fetchall()

        sector_rows = []
        macro_rows = []
        if sector_ids and active_view == "sector":
            sector_placeholders = ",".join("?" for _ in sector_ids)
            sector_rows = conn.execute(
                f"""
                SELECT
                    soi.article_id,
                    soi.sector_id,
                    s.sector_key,
                    s.name AS sector_name,
                    soi.confidence,
                    soi.impact_direction,
                    soi.impact_magnitude,
                    soi.reason,
                    soi.created_at AS impact_created_at,
                    na.title,
                    na.summary,
                    substr(COALESCE(na.body, na.summary, ''), 1, 320) AS body_preview,
                    na.source,
                    na.source_url,
                    na.published_at
                FROM sector_opportunist_impacts AS soi
                JOIN sectors AS s ON s.id = soi.sector_id
                JOIN news_articles AS na ON na.id = soi.article_id
                LEFT JOIN sector_opportunist_article_processing AS sop
                    ON sop.article_id = soi.article_id
                WHERE soi.sector_id IN ({sector_placeholders})
                ORDER BY s.sector_key ASC, na.published_at DESC, soi.article_id DESC, soi.created_at DESC
                """,
                tuple(sector_ids),
            ).fetchall()

        if sector_ids and active_view == "macro":
            sector_placeholders = ",".join("?" for _ in sector_ids)
            macro_rows = conn.execute(
                f"""
                SELECT
                    combined.news_scope,
                    combined.article_id,
                    combined.sector_id,
                    s.sector_key,
                    s.name AS sector_name,
                    combined.confidence,
                    '' AS impact_direction,
                    '' AS impact_magnitude,
                    combined.reason,
                    combined.impact_created_at,
                    combined.title,
                    combined.summary,
                    combined.body_preview,
                    combined.source,
                    combined.source_url,
                    combined.published_at
                FROM (
                    SELECT
                        'world' AS news_scope,
                        wsi.article_id,
                        wsi.sector_id,
                        wsi.confidence,
                        wsi.reason,
                        wsi.created_at AS impact_created_at,
                        na.title,
                        na.summary,
                        substr(COALESCE(na.body, na.summary, ''), 1, 320) AS body_preview,
                        na.source,
                        na.source_url,
                        na.published_at
                    FROM world_news_sector_impacts AS wsi
                    JOIN news_articles AS na ON na.id = wsi.article_id
                    LEFT JOIN world_news_article_processing AS wnap
                        ON wnap.article_id = wsi.article_id

                    UNION ALL

                    SELECT
                        'us' AS news_scope,
                        usi.article_id,
                        usi.sector_id,
                        usi.confidence,
                        usi.reason,
                        usi.created_at AS impact_created_at,
                        na.title,
                        na.summary,
                        substr(COALESCE(na.body, na.summary, ''), 1, 320) AS body_preview,
                        na.source,
                        na.source_url,
                        na.published_at
                    FROM us_news_sector_impacts AS usi
                    JOIN news_articles AS na ON na.id = usi.article_id
                    LEFT JOIN us_news_article_processing AS unap
                        ON unap.article_id = usi.article_id
                ) AS combined
                JOIN sectors AS s ON s.id = combined.sector_id
                WHERE combined.sector_id IN ({sector_placeholders})
                ORDER BY s.sector_key ASC, combined.published_at DESC, combined.article_id DESC, combined.impact_created_at DESC
                """,
                tuple(sector_ids),
            ).fetchall()

    companies_by_id: dict[int, dict] = {}
    for company_id in company_ids:
        company_info = dict(company_lookup_by_id.get(company_id, {}))
        metadata = metadata_by_company_id.get(company_id) or {}
        company_info.update(
            {
                "symbol": str(metadata.get("symbol") or company_info.get("symbol") or "").strip().upper(),
                "name": str(metadata.get("name") or company_info.get("name") or "").strip(),
                "industry_id": metadata.get("industry_id") or company_info.get("industry_id"),
                "industry_key": str(metadata.get("industry_key") or company_info.get("industry_key") or "").strip(),
                "industry_name": str(metadata.get("industry_name") or company_info.get("industry_name") or "").strip(),
                "sector_id": metadata.get("sector_id") or company_info.get("sector_id"),
                "sector_key": str(metadata.get("sector_key") or company_info.get("sector_key") or "").strip(),
                "sector_name": str(metadata.get("sector_name") or company_info.get("sector_name") or "").strip(),
            }
        )
        companies_by_id[company_id] = _build_company_news_entry(company_info)

    company_ids_by_industry_id: dict[int, list[int]] = {}
    company_ids_by_sector_id: dict[int, list[int]] = {}
    for company_id, company_entry in companies_by_id.items():
        industry_id = company_entry.get("industry_id")
        if isinstance(industry_id, int):
            company_ids_by_industry_id.setdefault(industry_id, []).append(company_id)
        sector_id = company_entry.get("sector_id")
        if isinstance(sector_id, int):
            company_ids_by_sector_id.setdefault(sector_id, []).append(company_id)

    for row in company_rows:
        row_data = dict(row)
        company_id = int(row_data["company_id"])
        company_entry = companies_by_id.get(company_id)
        if company_entry is not None:
            _append_scoped_news_row(company_entry["company_news"], row_data)

    for row in industry_rows:
        row_data = dict(row)
        try:
            industry_id = int(row_data["industry_id"])
        except (TypeError, ValueError):
            continue
        for company_id in company_ids_by_industry_id.get(industry_id, []):
            company_entry = companies_by_id.get(company_id)
            if company_entry is not None:
                _append_scoped_news_row(company_entry["industry_news"], row_data)

    for row in sector_rows:
        row_data = dict(row)
        try:
            sector_id = int(row_data["sector_id"])
        except (TypeError, ValueError):
            continue
        for company_id in company_ids_by_sector_id.get(sector_id, []):
            company_entry = companies_by_id.get(company_id)
            if company_entry is not None:
                _append_scoped_news_row(company_entry["sector_news"], row_data)

    for row in macro_rows:
        row_data = dict(row)
        try:
            sector_id = int(row_data["sector_id"])
        except (TypeError, ValueError):
            continue
        for company_id in company_ids_by_sector_id.get(sector_id, []):
            company_entry = companies_by_id.get(company_id)
            if company_entry is not None:
                _append_scoped_news_row(company_entry["macro_news"], row_data)

    normalized_companies: list[dict] = []
    section_article_counts = {"company": 0, "sector": 0, "industry": 0, "macro": 0}
    total_article_count = 0
    for company_id in company_ids:
        company_entry = companies_by_id.get(company_id)
        if company_entry is None:
            continue

        for section_key, count_key in (
            ("company_news", "company"),
            ("sector_news", "sector"),
            ("industry_news", "industry"),
            ("macro_news", "macro"),
        ):
            finalized_section = _finalize_news_section_payload(company_entry[section_key])
            company_entry[section_key] = finalized_section
            section_article_counts[count_key] += finalized_section["article_count"]

        company_entry["analyzed_article_count"] = (
            company_entry["company_news"]["article_count"]
            + company_entry["sector_news"]["article_count"]
            + company_entry["industry_news"]["article_count"]
            + company_entry["macro_news"]["article_count"]
        )
        company_entry["confidence_counts"] = _combine_confidence_counts(
            company_entry["company_news"]["confidence_counts"],
            company_entry["sector_news"]["confidence_counts"],
            company_entry["industry_news"]["confidence_counts"],
            company_entry["macro_news"]["confidence_counts"],
        )
        company_entry["latest_published_at"] = _max_timestamp_value(
            company_entry["company_news"]["latest_published_at"],
            company_entry["sector_news"]["latest_published_at"],
            company_entry["industry_news"]["latest_published_at"],
            company_entry["macro_news"]["latest_published_at"],
        )
        total_article_count += company_entry["analyzed_article_count"]
        normalized_companies.append(company_entry)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "lookup_query": normalized_company_lookup,
        "lookup_active": bool(normalized_company_lookup),
        "lookup_match_count": total_company_count,
        **_build_analyzed_company_news_pagination_payload(
            page=normalized_page,
            page_size=page_size,
            total_company_count=total_company_count,
            page_company_count=len(normalized_companies),
        ),
        "article_count": total_article_count,
        "section_article_counts": section_article_counts,
        "active_view": active_view,
        "companies": normalized_companies,
    }
