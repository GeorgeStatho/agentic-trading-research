import re
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from fastapi import HTTPException
from pydantic import BaseModel


class ArticleContent(BaseModel):
    url: str
    title: str
    published_at: Optional[str] = None
    body: str


class ArticleResponse(BaseModel):
    data: ArticleContent
    status: str = "success"


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.replace("\xa0", " ").replace("’", "'").replace("“", '"').replace("”", '"')


def _normalize_article_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text_blocks(container: Optional[Tag]) -> list[str]:
    blocks: list[str] = []
    if container is None:
        return blocks

    for node in container.find_all(["p", "li"]):
        text = _clean_text(node.get_text(" ", strip=True))
        if text:
            blocks.append(text)

    return blocks


def _validate_url_host(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()

    if not host:
        raise HTTPException(status_code=400, detail="URL must include a valid host.")

    normalized_allowed = tuple(h.lower() for h in allowed_hosts)

    if not any(host == allowed or host.endswith("." + allowed) for allowed in normalized_allowed):
        raise HTTPException(
            status_code=400,
            detail=f"URL must belong to one of: {', '.join(normalized_allowed)}",
        )


def _extract_article_date(soup: BeautifulSoup) -> Optional[str]:
    candidates: list[str] = []

    meta_selectors = [
        ("property", "article:published_time"),
        ("property", "og:published_time"),
        ("name", "pubdate"),
        ("name", "publish-date"),
        ("name", "published"),
        ("name", "date"),
        ("itemprop", "datePublished"),
    ]

    for attr_name, attr_value in meta_selectors:
        tag = soup.find("meta", attrs={attr_name: attr_value})
        if tag and tag.get("content"):
            candidates.append(_clean_text(tag["content"]))

    for time_tag in soup.find_all("time"):
        dt = time_tag.get("datetime")
        if dt:
            candidates.append(_clean_text(dt))

        text = _clean_text(time_tag.get_text(" ", strip=True))
        if text:
            candidates.append(text)

    page_text = soup.get_text(" ", strip=True)

    patterns = [
        r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}(?:,\s+\d{1,2}:\d{2}(?::\d{2})?\s+[AP]M)?)",
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)",
        r"(\d{4}-\d{2}-\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, page_text)
        if match:
            candidates.append(_clean_text(match.group(1)))

    return candidates[0] if candidates else None