"""Read-only report on how much news, and how relevant, the strategist receives.

Usage: python scripts/news_quality_report.py --db PATH --start YYYY-MM-DD [--end YYYY-MM-DD] [--json]
"""

import argparse
import json
import sqlite3
from collections import Counter
from urllib.parse import urlsplit

REJECTION_REASONS = ("insufficient_independent_roots", "insufficient_high_confidence_articles")


def articles_by_source(conn, start, end):
    cursor = conn.execute(
        "SELECT source, source_type, COUNT(*), AVG(evidence_score) FROM news_articles "
        "WHERE substr(created_at, 1, 10) BETWEEN ? AND ? GROUP BY source, source_type "
        "ORDER BY COUNT(*) DESC, source, source_type",
        (start, end),
    )
    return [
        {"source": s, "source_type": t, "count": n, "avg_evidence_score": avg}
        for s, t, n, avg in cursor
    ]


def _source_for_host(host, sources):
    # Match the scraper's own naming: the longest saved source the host equals or is a subdomain of.
    matches = [s for s in sources if host == s or host.endswith(f".{s}")]
    return max(matches, key=len) if matches else host


def extraction_success_by_source(conn, start, end):
    # A saved article is a successful extraction; a failed_urls row is a url whose latest
    # extraction failed (a later success deletes the row). Each url counts once.
    saved = dict(conn.execute(
        "SELECT source, COUNT(*) FROM news_articles WHERE substr(created_at, 1, 10) BETWEEN ? AND ? GROUP BY source",
        (start, end),
    ).fetchall())
    known = [s.lower() for (s,) in conn.execute("SELECT DISTINCT source FROM news_articles") if s]
    failed, permanent, errors = Counter(), Counter(), {}
    cursor = conn.execute(
        "SELECT url, is_permanent, last_error FROM failed_urls WHERE substr(updated_at, 1, 10) BETWEEN ? AND ?",
        (start, end),
    )
    for url, is_permanent, error in cursor:
        source = _source_for_host((urlsplit(url).hostname or "").lower(), known)
        failed[source] += 1
        permanent[source] += bool(is_permanent)
        errors.setdefault(source, Counter())[(error or "").strip()[:120] or "unknown"] += 1
    rows = []
    for source in set(saved) | set(failed):
        succeeded = saved.get(source, 0)
        attempted = succeeded + failed[source]
        rows.append({
            "source": source,
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed[source],
            "permanent_failures": permanent[source],
            "success_rate": succeeded / attempted,
            "top_error": errors[source].most_common(1)[0][0] if source in errors else None,
        })
    return sorted(rows, key=lambda r: (-r["attempted"], r["source"]))


def companies_with_company_news(conn, start, end):
    return conn.execute(
        "SELECT COUNT(DISTINCT company_id) FROM company_news_articles "
        "WHERE substr(created_at, 1, 10) BETWEEN ? AND ?",
        (start, end),
    ).fetchone()[0]


def payload_rows(conn, start, end):
    cursor = conn.execute(
        "SELECT id, input_payload_json FROM strategist_runs "
        "WHERE input_payload_json IS NOT NULL AND substr(created_at, 1, 10) BETWEEN ? AND ? ORDER BY id",
        (start, end),
    )
    rows = []
    for run_id, raw in cursor:
        payload = json.loads(raw)
        symbol = ((payload.get("company") or {}).get("symbol") or "").upper()
        summaries = (payload.get("supporting_articles") or {}).get("article_summaries") or []
        evidence = payload.get("company_evidence") or {}
        rows.append({
            "run_id": run_id,
            "symbol": symbol,
            "summary_count": len(summaries),
            # A summary matching by ticker and by layer is still one summary.
            "company_direct": sum(
                (s.get("primary_ticker") or "").upper() == symbol
                or "company_view" in (s.get("evidence_layers") or [])
                for s in summaries
            ),
            "distinct_root_sources": len({s["root_source_id"] for s in summaries if s.get("root_source_id")}),
            "evidence_freshness": evidence.get("freshness_status"),
            "evidence_coverage": evidence.get("coverage_status"),
            "evidence_gaps": evidence.get("gaps") or [],
        })
    return rows


def payload_summary(rows):
    n = len(rows)
    has_gap = lambda row, prefix: any(g.startswith(prefix) for g in row["evidence_gaps"])
    return {
        "payloads": n,
        "mean_company_direct": sum(r["company_direct"] for r in rows) / n if n else 0.0,
        "mean_summary_count": sum(r["summary_count"] for r in rows) / n if n else 0.0,
        "consensus_unavailable": sum(has_gap(r, "consensus_unavailable") for r in rows),
        "company_evidence_not_collected": sum(has_gap(r, "company_evidence_not_collected") for r in rows),
    }


def rejections(conn, start, end):
    counts = dict.fromkeys(REJECTION_REASONS, 0)
    cursor = conn.execute(
        "SELECT reason_code, COUNT(*) FROM candidate_rejections "
        f"WHERE reason_code IN ({','.join('?' * len(REJECTION_REASONS))}) "
        "AND substr(occurred_at, 1, 10) BETWEEN ? AND ? GROUP BY reason_code",
        (*REJECTION_REASONS, start, end),
    )
    counts.update(dict(cursor))
    return counts


def build_report(conn, start, end):
    rows = payload_rows(conn, start, end)
    return {
        "start": start,
        "end": end,
        "articles_by_source": articles_by_source(conn, start, end),
        "extraction_success_by_source": extraction_success_by_source(conn, start, end),
        "companies_with_company_news": companies_with_company_news(conn, start, end),
        "payloads": rows,
        "payload_summary": payload_summary(rows),
        "rejections": rejections(conn, start, end),
    }


def format_text(report):
    lines = [f"News quality report {report['start']} .. {report['end']}", "", "Articles by source"]
    lines += [
        f"  {r['source']} / {r['source_type']}: {r['count']} (avg evidence {r['avg_evidence_score']})"
        for r in report["articles_by_source"]
    ]
    lines += ["", "Extraction success rate by source"]
    lines += [
        f"  {r['source']}: {r['success_rate']:.0%} ({r['succeeded']}/{r['attempted']}, "
        f"{r['permanent_failures']} permanent)" + (f", top error: {r['top_error']}" if r["top_error"] else "")
        for r in report["extraction_success_by_source"]
    ]
    lines += ["", f"Companies with company news: {report['companies_with_company_news']}", "", "Payload summary"]
    lines += [f"  {k}: {v}" for k, v in report["payload_summary"].items()]
    lines += ["", "Payloads (first per symbol)"]
    seen = set()
    for r in report["payloads"]:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            lines.append("  " + ", ".join(f"{k}={v}" for k, v in r.items()))
    lines += ["", "Rejections"] + [f"  {k}: {v}" for k, v in report["rejections"].items()]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    # immutable=1: never write to (or lock) the database being measured.
    conn = sqlite3.connect(f"file:{args.db}?immutable=1", uri=True)
    try:
        report = build_report(conn, args.start, args.end or args.start)
    finally:
        conn.close()
    print(json.dumps(report, indent=2) if args.json else format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
