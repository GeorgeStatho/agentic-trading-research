from __future__ import annotations

from flask import jsonify, request

from api_support.common import internal_error, safe_int
from api_support.context import ANALYZED_COMPANY_NEWS_DEFAULT_PAGE_SIZE, ANALYZED_COMPANY_NEWS_MAX_PAGE_SIZE
from api_support.news import build_analyzed_company_news_payload


def register_news_routes(app) -> None:
    @app.get("/api/opportunist-company-news")
    def opportunist_company_news():
        try:
            page = safe_int(request.args.get("page"), 1)
            page_size = safe_int(
                request.args.get("page_size"),
                ANALYZED_COMPANY_NEWS_DEFAULT_PAGE_SIZE,
                maximum=ANALYZED_COMPANY_NEWS_MAX_PAGE_SIZE,
            )
            return jsonify(build_analyzed_company_news_payload(page=page, page_size=page_size)), 200
        except Exception as exc:
            return internal_error("Failed to load analyzed company news.", exc)
