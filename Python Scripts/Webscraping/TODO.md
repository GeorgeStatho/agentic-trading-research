# Webscraping TODO

This checklist turns the remaining webscraping work from the design doc into a practical implementation plan.

## Highest Priority

- Build Stage 5 follow-up scraping
  - Accept follow-up search queries from the questioning stage.
  - Execute those queries deterministically.
  - Reuse the same source allowlist, recency rules, and URL filtering rules.
  - Store `query_used` on each follow-up article/result.
  - Feed follow-up results back as a separate research-round dataset.

- Add runtime research-loop limits
  - Enforce `max 2 follow-up rounds`.
  - Enforce `max 2-3 follow-up questions per company per round`.
  - Stop early if evidence quality is already sufficient.

- Improve evidence traceability
  - Store supporting excerpts or short article evidence snippets.
  - Make it easy to tie important claims back to:
    - source URL
    - source type
    - timestamp
    - evidence/confidence score
    - supporting excerpt or summary

## High Priority

- Add non-news industry signals for Stage 1
  - Pull sector ETF movement into the industry discovery flow.
  - Add optional commodity/rate inputs for industries that depend on them.
  - Store these structured signals in a form the industry-analysis stage can consume.

- Improve source-specific extraction coverage
  - Decide which weak/noisy sources should be:
    - upgraded with dedicated extractors
    - downgraded
    - or removed
  - Candidate sources still needing review:
    - Morningstar
    - Barron's
    - any source that still behaves mostly like a noisy search page

- Store research-round artifacts more explicitly
  - Save first-pass company article bundles.
  - Save follow-up question bundles.
  - Save follow-up result bundles.
  - Preserve the linkage between:
    - company
    - round
    - question
    - query
    - result articles

## Medium Priority

- Add claim-level evidence structures
  - Move beyond article-level storage only.
  - Create a lightweight way to store evidence items such as:
    - claim
    - support excerpt
    - article URL
    - source type
    - confidence/evidence score

- Integrate structured company-selection inputs more tightly
  - Make company candidate selection use more of:
    - price movement
    - volume
    - earnings timing
    - liquidity metrics
  - Keep the pre-score deterministic before any agent ranking.

- Add score-based save or keep thresholds
  - Optionally skip saving clearly weak article results.
  - Or keep all saved articles but add downstream thresholds for:
    - low evidence score
    - low directness
    - low factuality

## Lower Priority

- Expand per-site extractor coverage
  - Add dedicated extractors only where the generic fallback is still too noisy.
  - Keep the extractor registry deterministic and per-site.

- Improve excerpt generation
  - Store a short deterministic excerpt from the article body or lead section.
  - Prefer early-paragraph extraction so later agent stages can use compact evidence items.

- Add better bundle/debug inspection tools
  - Add DB test queries for:
    - scored articles
    - failed URLs by stage
    - follow-up result bundles
    - ranking/source quality summaries

## Suggested Build Order

1. Stage 5 follow-up scraping
2. Research-loop enforcement
3. Evidence excerpt/support storage
4. Sector ETF and commodity/rate inputs
5. Better source-specific coverage for weak sources
6. Claim-level evidence items
7. Stronger company selection inputs

## Already In Place

- deterministic source URL generation
- source allowlist and source metadata from `source.json`
- source-page crawl followed by filtered article follow
- DB reuse for already-saved article URLs
- failed URL tracking
- deterministic article scoring
- company and industry article linking
- macro event scraping
- Yahoo market-data ingestion
