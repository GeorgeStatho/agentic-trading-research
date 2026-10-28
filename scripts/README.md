# Developer Commands

Use `project.py` as the canonical local entrypoint dispatcher. It delegates to the existing scripts, so Docker Compose and compatibility imports continue to work.

```bash
# Start individual services
python3 scripts/project.py run api
python3 scripts/project.py run worker
python3 scripts/project.py run news
python3 scripts/project.py run option-manager

# Run the offline evaluator with its normal arguments
python3 scripts/project.py run offline-evaluation --historical-outcome-audit --limit 100

# Grid-search backtest exit settings; tune before --split-at, validate after it
python3 scripts/project.py run backtest-sweep --db path/to.db --split-at 2026-09-01 \
    --setting take_profit_pct=25,50,100 --setting stop_loss_pct=-25,-50 --csv sweep.csv

# Validate Python or the dashboard
python3 scripts/project.py test
python3 scripts/project.py test tests.test_exposure_map tests.test_settings
python3 scripts/project.py dashboard build

# Share the deterministic demo walkthrough
python3 scripts/project.py demo-url
```

Use `docker compose up --build` for the integrated containerized stack.

## News quality report

`scripts/news_quality_report.py` measures how much news, and how relevant, the strategist received over a date range. It opens the DB read-only (`?immutable=1`) and never writes.

```bash
python scripts/news_quality_report.py --db ~/stock-restore/db-dev/stock_experiment.db --start 2026-10-27
python scripts/news_quality_report.py --db PATH --start 2026-10-28 --end 2026-10-30 --json > report.json
```

It reports:
- `articles_by_source`: saved articles by `source` / `source_type`, with average evidence score;
- `companies_with_company_news`: companies with at least one company-linked article;
- `payloads`: one row per strategist run. `company_direct` counts the summaries about the company itself (matching `primary_ticker` or a `company_view` layer); the row also has `distinct_root_sources` and the company-evidence freshness and gaps;
- `payload_summary`: the means, plus counts of payloads with `consensus_unavailable` or `company_evidence_not_collected`;
- `rejections`: `insufficient_independent_roots` and `insufficient_high_confidence_articles` counts.

The 2026-10-27 baseline (before the news-quality changes) was:
- 2 sources;
- 32 companies with company news;
- mean `company_direct` 1.59 of 20;
- `consensus_unavailable` in 49 of 59 payloads.

Compare new runs against it.

## Extractor check

`scripts/check_extractors.py` is a read-only live check of the article extractors; it saves nothing to any database.

```bash
WEBSCRAPING_PLAYWRIGHT_CONCURRENCY=1 python scripts/check_extractors.py finance.yahoo.com --limit 10
python scripts/check_extractors.py morningstar.com --limit 10 --html-dir /tmp/pages   # also keep the fetched pages
```

A domain PASSES when 70% of its URLs return success, a title, a body of 500+ characters and a parseable `published_at`. Page loads are counted per domain in a JSON ledger (`--max-loads`, default 15, over all runs). Run one non-Yahoo domain per invocation, because Scrapy's reactor cannot restart.
