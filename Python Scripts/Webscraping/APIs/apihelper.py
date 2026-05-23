from __future__ import annotations

from bs4 import BeautifulSoup


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("h1") or soup.find("title")
    return title.get_text(" ", strip=True) if title else ""


def extract_paragraph_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    paragraphs = [node.get_text(" ", strip=True) for node in soup.select("article p, main p, p")]
    return "\n".join(text for text in paragraphs if text)
