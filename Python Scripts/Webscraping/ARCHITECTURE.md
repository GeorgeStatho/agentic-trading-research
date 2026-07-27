# Webscraping Architecture

The scraper is organized as a staged pipeline:

```text
discovery -> source policy -> fetching -> extraction -> validation -> storage
```

## Boundaries

- `domain/` contains dependency-free data contracts shared across stages.
- `sources/registry.py` owns hostname policy resolution. The most-specific
  configured hostname wins; `source_config.py` remains the compatibility API.
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
