"""Read-only live check of the article extractors; it saves nothing to any database.

Usage: python scripts/check_extractors.py DOMAIN [DOMAIN ...] [--limit 10]
       [--ledger PATH] [--max-loads 15] [--html-dir DIR] [--reuse-urls] [--listing-url URL]

Gathers recent article URLs for each domain, runs them through the project's
fetch_articles, and prints title, published_at, body length and success per URL.
A domain PASSES when at least 70% of its URLs have success, a title, a body of
500+ characters and a parseable published_at.

Every page load is counted per domain in a JSON ledger that survives between
runs, and a domain is never fetched past --max-loads. With --html-dir, every page
the extractors see is also written there, so selectors can be fixed offline.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO_ROOT / ".superpowers/sdd/plan-the-implementation-of-scalable-raven/extractor-loads.json"
HTML_DIR_ENV = "CHECK_EXTRACTORS_HTML_DIR"
MIN_BODY_CHARS = 500
PASS_RATE = 0.7
YAHOO_DOMAIN = "finance.yahoo.com"
YAHOO_TICKERS = ("NVDA", "AAPL", "MSFT")
SEARCH_QUERY = "NVDA"
# Hosts the check may touch, by the domain name used in config/source.json.
HOSTS = {
    "finance.yahoo.com": "finance.yahoo.com",
    "marketwatch.com": "www.marketwatch.com",
    "morningstar.com": "www.morningstar.com",
    "investing.com": "www.investing.com",
}


def install_html_dump(directory):
    """Write every page the extractors see to directory; also runs in the spawned Scrapy child."""
    from engine import article_extraction, article_scraper

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    original = article_extraction.extract_from_response

    def dumping(response):
        parts = urlsplit(response.url)
        slug = re.sub(r"[^a-z0-9]+", "-", (parts.hostname or "") + parts.path.lower()).strip("-")[-60:]
        digest = hashlib.sha1(response.url.encode()).hexdigest()[:8]
        (directory / f"{slug}-{digest}.html").write_text(response.text, encoding="utf-8")
        return original(response)

    article_extraction.extract_from_response = dumping
    article_scraper.extract_from_response = dumping


# Scrapy articles run in a spawned child that re-imports this file, so install at import.
if os.environ.get(HTML_DIR_ENV):
    install_html_dump(os.environ[HTML_DIR_ENV])


def load_ledger(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_ledger(path, ledger):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")


API_REQUESTS = Counter()  # outgoing `requests` calls per host, redirects included
SCRAPY_LOG_GLOB = "Data/logs/scraper_*.log"


def count_api_requests():
    import requests

    original = requests.Session.send

    def send(self, request, **kwargs):
        API_REQUESTS[urlsplit(request.url).hostname] += 1
        return original(self, request, **kwargs)

    requests.Session.send = send


def scrapy_requests_since(marks):
    # Scrapy runs in this process or a spawned child; both log their downloader stats.
    total = 0
    for log in REPO_ROOT.glob(SCRAPY_LOG_GLOB):
        with log.open(encoding="utf-8", errors="replace") as handle:
            handle.seek(marks.get(log, 0))
            total += sum(int(n) for n in re.findall(r"'downloader/request_count': (\d+)", handle.read()))
    return total


@contextmanager
def charged(ledger, path, domain, estimate):
    """Charge `estimate` loads before the fetch (a crash can only over-count), then true up to the measured count."""
    entry = ledger.setdefault(domain, {"loads": 0, "urls": []})
    base = entry["loads"]
    entry["loads"] = base + estimate
    save_ledger(path, ledger)
    marks = {log: log.stat().st_size for log in REPO_ROOT.glob(SCRAPY_LOG_GLOB)}
    api_before = API_REQUESTS[HOSTS[domain]]
    try:
        yield
    finally:
        # Playwright pages are not measured, so never go below the estimate.
        measured = API_REQUESTS[HOSTS[domain]] - api_before + scrapy_requests_since(marks)
        entry["loads"] = base + max(estimate, measured)
        save_ledger(path, ledger)


def passes_article_patterns(url, domain):
    from sources.source_config import get_article_patterns

    parts = urlsplit(url)
    return parts.hostname == HOSTS[domain] and any(p in parts.path for p in get_article_patterns(url))


def yahoo_urls():
    import yfinance as yf

    urls = []
    for ticker in YAHOO_TICKERS:
        for item in yf.Search(ticker, news_count=10).news:
            link = str(item.get("link") or (item.get("content") or {}).get("canonicalUrl", {}).get("url") or "")
            link = link.split("?")[0]
            if urlsplit(link).hostname == HOSTS[YAHOO_DOMAIN] and link not in urls:
                urls.append(link)
    unmatched = [u for u in urls if not passes_article_patterns(u, YAHOO_DOMAIN)]
    if unmatched:
        print(f"  NOTE {len(unmatched)}/{len(urls)} Yahoo links do not match article_patterns, e.g. {unmatched[0]}")
    return urls


def listing_urls(domain):
    from sources.urlFactories import INDUSTRY_LISTING_SOURCES, SEARCH_SOURCES

    candidates = []
    for source in (*SEARCH_SOURCES.values(), *INDUSTRY_LISTING_SOURCES.values()):
        if urlsplit(source["url"]).hostname == HOSTS[domain]:
            candidates.append(source["url"].replace("{query}", quote(SEARCH_QUERY)))
    return candidates


def discover(domain, ledger, ledger_path, max_loads, reuse, listing_url):
    """Return (urls, error). Listing pages count as page loads; yf.Search is not a page load."""
    entry = ledger.setdefault(domain, {"loads": 0, "urls": []})
    if reuse and entry["urls"]:
        return list(entry["urls"]), ""
    if domain == YAHOO_DOMAIN:
        urls = yahoo_urls()
        return urls, "" if urls else "NO_ARTICLE_LINKS: yf.Search returned no finance.yahoo.com links"

    from engine.page_fetch import fetch_source_pages

    # One listing load per domain: Scrapy's reactor cannot restart, so a second crawl in this process would fail.
    page_url = listing_url or listing_urls(domain)[0]
    if entry["loads"] >= max_loads:
        return [], f"BUDGET: {max_loads} loads already used"
    with charged(ledger, ledger_path, domain, 1):
        pages = fetch_source_pages([page_url])
    for page in pages:
        urls = []
        for link in page.get("links") or []:
            href = str(link.get("href") or "").split("#")[0]
            if passes_article_patterns(href, domain) and href not in urls:
                urls.append(href)
        if urls:
            return urls, ""
        print(f"  listing {page_url}: status={page.get('status')} error={page.get('error')!r} links={len(page.get('links') or [])}")
    return [], "NO_ARTICLE_LINKS: listing pages yielded no links matching article_patterns"


def check_row(result):
    from engine.article_extraction import _normalize_datetime

    published = _normalize_datetime(result.published_at)
    body = len(result.text or "")
    ok = bool(result.success and result.title and body >= MIN_BODY_CHARS and published)
    return ok, published, body


def check_domain(domain, args, ledger):
    from engine.page_fetch import fetch_articles

    print(f"\n== {domain}")
    urls, error = discover(domain, ledger, args.ledger, args.max_loads, args.reuse_urls, args.listing_url)
    ledger[domain]["urls"] = urls
    save_ledger(args.ledger, ledger)
    if error:
        print(f"  FAIL {error}")
        return {"domain": domain, "ok": 0, "total": 0, "passed": False, "error": error}

    budget = args.max_loads - ledger[domain]["loads"]
    chosen = urls[: min(args.limit, max(budget, 0))]
    if len(chosen) < args.limit:
        print(f"  only {len(chosen)} of {args.limit} URLs checked (found {len(urls)}, load budget left {budget})")
    with charged(ledger, args.ledger, domain, len(chosen)):
        results = fetch_articles(chosen) if chosen else {}

    ok_count = 0
    for url in chosen:
        result = results.get(url)
        if result is None:
            print(f"  MISSING {url}")
            continue
        ok, published, body = check_row(result)
        ok_count += ok
        print(
            f"  {'OK  ' if ok else 'FAIL'} success={result.success} body={body} "
            f"published={published or repr(result.published_at)} title={result.title[:70]!r} {url}"
            + ("" if result.success else f" error={result.error!r}")
        )
    total = len(chosen)
    passed = total > 0 and ok_count / total >= PASS_RATE
    print(f"  {'PASS' if passed else 'FAIL'} {domain}: {ok_count}/{total} ({ok_count / max(total, 1):.0%})")
    return {"domain": domain, "ok": ok_count, "total": total, "passed": passed, "error": ""}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("domains", nargs="+", choices=sorted(HOSTS))
    parser.add_argument("--limit", type=int, default=10, help="article URLs per domain")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help="JSON file counting page loads per domain")
    parser.add_argument("--max-loads", type=int, default=15, help="page loads allowed per domain, over all runs")
    parser.add_argument("--html-dir", help="also write every fetched page's HTML here")
    parser.add_argument("--listing-url", help="listing page to load instead of the domain's search URL")
    parser.add_argument("--reuse-urls", action="store_true", help="reuse the URLs found by the previous run")
    args = parser.parse_args(argv)
    if len([d for d in args.domains if d != YAHOO_DOMAIN]) > 1 and not args.reuse_urls:
        parser.error("run one non-Yahoo domain per invocation (Scrapy's reactor cannot restart for a second listing crawl)")

    if args.html_dir:
        os.environ[HTML_DIR_ENV] = args.html_dir
        install_html_dump(args.html_dir)
    count_api_requests()
    ledger = load_ledger(args.ledger)
    summaries = [check_domain(domain, args, ledger) for domain in args.domains]

    print("\n== SUMMARY")
    for s in summaries:
        print(f"  {'PASS' if s['passed'] else 'FAIL'} {s['domain']}: {s['ok']}/{s['total']} "
              f"page loads used so far: {ledger[s['domain']]['loads']}/{args.max_loads}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
