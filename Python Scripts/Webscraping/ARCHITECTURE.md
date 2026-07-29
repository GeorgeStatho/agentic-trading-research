# Webscraping Architecture

The scraper is organized as a staged pipeline:

```text
discovery -> source policy -> fetching -> extraction -> validation -> storage
```

## Boundaries

- `domain/` contains dependency-free data contracts shared across stages.
- `sources/registry.py` owns hostname policy resolution. The most-specific
  configured hostname wins; `source_config.py` remains the compatibility API.
- `sources/company_official.py` adapts first-party company disclosures. SEC
  EDGAR daily indexes are filtered to tracked-company CIKs, while configured
  investor-relations RSS feeds are normalized directly into company evidence.
- `fetching/` owns retry, timeout, and normalized HTTP outcomes for requests
  that do not require a browser.
- `discovery/` turns feeds and provider responses into candidate URLs. A bad
  feed is contained to that feed instead of aborting a collection run.
- `engine/` and `extractors/` retain the Scrapy/Playwright extraction adapters.
- `processing/validation.py` rejects blocked, empty, and implausibly short
  article payloads before persistence.
- `pipelines/` are workflow adapters for company, industry, sector, and
  regional entry points. Existing public functions are intentionally retained.

## Operational conventions

- Source-specific behavior belongs in source policy or an extractor, not a
  workflow.
- Network failures return or raise bounded, contextual failures; batch flows
  log and continue wherever one source can safely be skipped.
- Validate article content before scoring or saving it.
- Persist normalized URLs and link an existing article to each relevant entity.

## Official company-source configuration

- Set `SEC_USER_AGENT` to a descriptive contact value before enabling EDGAR.
  The adapter reads the recent daily indexes, so it covers all tracked symbols
  that have an SEC ticker/CIK mapping without one request per company.
- Add only verified HTTPS investor-relations RSS URLs to
  `config/company_ir_feeds.json`. Feed URLs are publisher-specific and cannot
  be safely inferred from a ticker. The format is:

```json
{
  "companies": {
    "EXAMPLE": {
      "feeds": ["https://investors.example.com/rss.xml"]
    }
  }
}
```
