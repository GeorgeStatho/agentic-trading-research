from __future__ import annotations

from typing import Any


def get_stock_data(symbol: str, since: str | None = None, limit: int = 10, **_: Any) -> list[dict[str, Any]]:
    """Public demo helper.

    The real source-specific implementation is private. This public version
    returns harmless sample records so the call shape stays documented.
    """

    del since
    limit = max(1, int(limit))
    return [
        {
            "symbol": symbol.upper(),
            "title": f"Demo article {index} for {symbol.upper()}",
            "url": f"https://example.com/demo/articles/{symbol.lower()}-{index}",
            "published_at": f"2026-05-{index:02d}T12:00:00+00:00",
            "summary": "Sample article metadata used in the public repository.",
            "source": "demo",
        }
        for index in range(1, limit + 1)
    ]


def get_article_content(url: str) -> dict[str, Any]:
    """Public demo helper: the real article API client is private, so no body is returned."""

    return {"data": {"url": url, "title": "", "body": "", "published_at": ""}}
