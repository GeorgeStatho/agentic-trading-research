from __future__ import annotations

from foolAPI import get_stock_data


def get_demo_company_articles(symbol: str, limit: int = 10):
    return get_stock_data(symbol, limit=limit)
