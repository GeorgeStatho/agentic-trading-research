# Webscraping TODO

This checklist tracks the current state of the webscraping and data-gathering system, including what is already implemented and what still needs work.

## Status Summary

The repo already has a strong scraping foundation in place:
- deterministic source URL generation
- source allowlist and source metadata
- source-page crawl followed by filtered article follow
- DB reuse for already-saved article URLs
- failed URL tracking
- deterministic article scoring
- company and industry article linking
- macro event scraping
- Yahoo market-data ingestion

The highest-value next steps are:
- add a Yahoo Finance news discovery adapter
- build Stage 5 follow-up scraping
- enforce research-loop limits at runtime
- improve confirmation scoring across independent sources
- strengthen article/body relevance checks
- verify and stabilize source configuration and weaker extractors

---

## Already Finished

### Core scraping pipeline
- Deterministic source URL generation is in place.
- Source allowlist and metadata loading are in place.
- Source-page crawling is implemented.
- Filtered one-level article follow is implemented.
- Existing article reuse from the DB is implemented.
- Failed URL tracking is implemented.
- Deterministic article scoring is implemented.
- Company and industry article linking is implemented.

### Extraction and normalization
- Generic article extraction exists.
- Several source-specific extractors already exist.
- URL normalization and title normalization are already part of the pipeline.
- Content hash generation is already part of article handling.

### Storage and scoring
- Articles are being saved with scoring metadata.
- Evidence-oriented scoring fields already exist, including:
  - recency
  - source reputation
  - directness
  - factuality
  - evidence score
- Raw extraction payloads are already being preserved in saved article records.

### Market and structured data
- Yahoo market-data ingestion is in place.
- Sector and industry snapshots are in place.
- Company fast-info fetching is in place.
- Macro event scraping is already present.

---

## Highest Priority

### 1. Add Yahoo Finance news discovery
- Add a Yahoo Finance / `yfinance` news adapter for article discovery.
- Use Yahoo Finance as a discovery layer, not a full replacement for scraping.
- Normalize Yahoo-discovered news items into the same article pipeline.
- Scrape full article text only for URLs that survive filtering.
- Deduplicate Yahoo-discovered URLs against already saved articles.

### 2. Build Stage 5 follow-up scraping
- Accept follow-up search queries from the questioning stage.
- Execute those queries deterministically.
- Reuse the same source allowlist, recency rules, and URL filtering rules.
- Store `query_used` on each follow-up article/result.
- Feed follow-up results back as a separate research-round dataset.

### 3. Add runtime research-loop limits
- Enforce `max 2 follow-up rounds`.
- Enforce `max 2-3 follow-up questions per company per round`.
- Stop early if evidence quality is already sufficient.

### 4. Improve confirmation scoring
- Stop assuming `independent_source_count = 1`.
- Group or cluster similar articles across sources.
- Count distinct confirming domains for the same claim/event.
- Use true independent-source counts in evidence scoring.

### 5. Add stronger post-fetch relevance checks
- After full article extraction, verify the article is actually about the intended company or industry.
- Check company/ticker mention density in title/body.
- Detect weak passing-mention articles.
- Prevent noisy generic market articles from entering later research stages.

---

## High Priority

### 6. Verify and stabilize source configuration
- Confirm the expected `config/source.json` file exists and is correctly loaded at runtime.
- Make sure source allowlist, article patterns, and reputation scores are active.
- Add validation or startup checks so missing config fails loudly.

### 7. Improve source-specific extraction coverage
- Review sources that still behave mostly like noisy search pages.
- Decide which sources should be:
  - upgraded with dedicated extractors
  - downgraded
  - or removed
- Candidate sources still needing review:
  - Morningstar
  - Business Insider
  - any other source mostly relying on generic fallback extraction

### 8. Store research-round artifacts more explicitly
- Save first-pass company article bundles.
- Save follow-up question bundles.
- Save follow-up result bundles.
- Preserve linkage between:
  - company
  - round
  - question
  - query
  - result articles

### 9. Add non-news industry signals for Stage 1
- Pull sector ETF movement into the industry discovery flow.
- Add optional commodity/rate inputs for industries that depend on them.
- Store these structured signals in a form the industry-analysis stage can consume.

### 10. Expand company search-term coverage
- Move beyond only the formal company name for company discovery.
- Add a small, deterministic set of variants such as:
  - formal company name
  - ticker
  - trimmed company name without suffixes
  - `Name (TICKER)`-style queries where useful
- Keep query count controlled to avoid explosion.

---

## Medium Priority

### 11. Add claim-level evidence structures
- Move beyond article-level storage only.
- Create a lightweight way to store evidence items such as:
  - claim
  - support excerpt
  - article URL
  - source type
  - confidence/evidence score

### 12. Improve excerpt generation
- Store a short deterministic excerpt from the article body or lead section.
- Prefer early-paragraph extraction so later agent stages can use compact evidence items.
- Make it easy to tie important claims back to:
  - source URL
  - source type
  - timestamp
  - evidence/confidence score
  - supporting excerpt or summary

### 13. Integrate structured company-selection inputs more tightly
- Make company candidate selection use more of:
  - price movement
  - volume
  - earnings timing
  - liquidity metrics
- Keep the pre-score deterministic before any agent ranking.

### 14. Add score-based save or keep thresholds
- Optionally skip saving clearly weak article results.
- Or keep all saved articles but add downstream thresholds for:
  - low evidence score
  - low directness
  - low factuality

### 15. Add stronger generic extraction fallback
- Add a more robust fallback for sites without dedicated extractors.
- Consider JSON-LD article extraction where available.
- Consider a readability-style fallback for article body extraction.

---

## Lower Priority

### 16. Expand per-site extractor coverage
- Add dedicated extractors only where the generic fallback is still too noisy.
- Keep the extractor registry deterministic and per-site.

### 17. Add better bundle/debug inspection tools
- Add DB test queries for:
  - scored articles
  - failed URLs by stage
  - follow-up result bundles
  - ranking/source quality summaries

### 18. Consolidate legacy ingestion paths
- Decide whether older one-off news collection scripts should be retired or converted.
- Keep one normalized ingestion path as the source of truth.
- Avoid maintaining parallel flat-file and DB-backed article flows long term.

---

## Suggested Build Order

1. Verify source configuration is present and working
2. Add Yahoo Finance news discovery adapter
3. Build Stage 5 follow-up scraping
4. Enforce runtime research-loop limits
5. Improve independent-source confirmation scoring
6. Add body-level relevance validation
7. Improve excerpt/support storage
8. Add sector ETF and commodity/rate inputs
9. Improve weak source-specific extraction coverage
10. Add claim-level evidence items
11. Strengthen company selection inputs

---

## Notes

### Finished but worth revisiting later
These pieces exist and are useful, but may need refinement:
- source-specific extractor coverage
- generic article extraction quality
- evidence scoring calibration
- Yahoo market-data freshness and caching policy

### Guiding principle
Keep the system:
- deterministic in data gathering
- structured in storage
- evidence-oriented in scoring
- and limited in recursive follow-up behavior
