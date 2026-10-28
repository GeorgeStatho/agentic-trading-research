# Plan D: point-in-time free-text search for Phase 3 research (`search_archive`)

Status: proposed (2026-10-27). See [README.md](README.md) for the index.

## Context
FinanceHarness gives its agent dense search over a point-in-time (PIT) archive. Phase 3 has
six typed operations ([`Python Scripts/services/research_tools.py:17-24`](../../Python%20Scripts/services/research_tools.py#L17-L24)). Each one looks up
facts and documents by metric, period or event, and nothing accepts free text. The planner
cannot ask "what has been reported about NVDA's export licences?"; it can only fetch what an
evidence package already holds.

This plan adds one bounded, PIT-filtered full-text operation over two sources:
- ingested news articles, about 11.5k on the server backup;
- company evidence documents (SEC filings and issuer releases).

It uses SQLite FTS5. Search results become citable through the existing document path.

**Honest scope.** `coverage_outcome` ([`services/research_evidence.py:65-75`](../../Python%20Scripts/services/research_evidence.py#L65-L75)) needs an `actual` or
`guidance` company fact before a session can be `ready`. Search returns documents, not facts,
so it enriches packages and the contradiction evidence, but does not by itself turn
`verified_company_facts_unavailable` sessions into `ready`. A "news-only readiness" route would
be a separate policy decision and is out of scope.

## Findings that shape the design
- **Raw news articles cannot enter a package.** `record_evidence_package` reads every
  `document_ids` entry from `evidence_documents`. `ResearchTools.validate_result` (:182) needs a
  stored `EvidenceDocument` with `metadata.company_id` and `ingested_at <= cutoff`. Search hits
  therefore have to be snapshotted as immutable `EvidenceDocument`s.
- **Article text is not PIT.** On re-scrape, `add_news_article`
  ([`Data/news_db_modules/article_store.py:137-162`](../../Data/news_db_modules/article_store.py#L137-L162)`, 204-214`) overwrites the title, summary,
  `published_at` and body. Only `first_seen_time` is stable, and there is no record of when the
  content last changed.
- **Search must not emit a truncation gap.** Any gap containing `truncated` blocks readiness
  (`research_evidence.py:73`), and search always has more results than its top-k.
- **No FTS5 yet.** It is not used anywhere in the repo. It works locally (SQLite 3.53.2).
  `Dockerfile.worker` uses `python:3.11-slim` (Debian SQLite, normally built with FTS5), which
  must be verified.
- **Design constraints from `research_planner.py:28-38`.** `REQUEST_SCHEMA.arguments` has
  `additionalProperties: False`, so `query` must be added there. The planner prompt has no
  per-operation descriptions.

## Changes

### 1. Content-change timestamp ([`Data/news_schema.sql`](../../Data/news_schema.sql) and the news bootstrap)
- Add a column `news_articles.content_updated_at TEXT`. Add it in
  [`Data/news_db_modules/bootstrap.py`](../../Data/news_db_modules/bootstrap.py), following the existing `ALTER TABLE ADD COLUMN`
  migrations (:18-30).
- Add triggers in [`news_schema.sql`](../../Data/news_schema.sql):
  - `AFTER UPDATE OF title, summary ON news_articles` sets `content_updated_at` to the current
    UTC ISO time, but only when the title or summary actually changed (`IS NOT`).
  - `AFTER UPDATE OF body ON news_article_content` does the same on the parent article, but
    only when the body changed.
- Legacy rows keep NULL. Searches treat NULL as unchanged since first seen, which is the
  documented residual risk.

### 2. FTS indexes
**News articles** ([`news_schema.sql`](../../Data/news_schema.sql)):
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS news_article_fts USING fts5(title, summary, body, tokenize='porter unicode61');
```
- `rowid` is `news_articles.id`.
- Triggers on insert and update of `news_articles` and `news_article_content` delete and
  reinsert the row. The row's `body` comes from a subselect on `news_article_content`. The update
  triggers carry `WHEN ... IS NOT OLD...` guards, so an unchanged re-scrape does not reindex. A delete
  trigger removes the row with its article.
- Backfill once in the news bootstrap, under `db_common.lock_for_migration`:
  `INSERT INTO news_article_fts(rowid,...) SELECT ... WHERE id NOT IN (SELECT rowid FROM news_article_fts)`.

**Evidence documents** ([`Data/market_db_modules/research_foundation_schema.sql`](../../Data/market_db_modules/research_foundation_schema.sql)):
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_document_fts USING fts5(document_id UNINDEXED, company_id UNINDEXED, text, tokenize='porter unicode61');
```
- An `AFTER INSERT ON evidence_documents` trigger fills it from
  `json_extract(record_json,'$.text')` and `json_extract(record_json,'$.metadata.company_id')`.
- The table is insert-only, so no update triggers are needed.
- Backfill existing rows once in the market bootstrap.
- Both schema files are in the bootstrap fingerprints, so the migrations rerun automatically.

### 3. Operation contract ([`services/research_tools.py`](../../Python%20Scripts/services/research_tools.py))
- Add `'search_archive': ('query',)` to `OPERATIONS`.
- In `ResearchRequest.parse`, add a new `query` branch:
  - The value must be a string of 3–200 characters.
  - Tokenise with `re.findall(r'[a-z0-9]+', query.lower())`.
  - Drop a small fixed stop-word set.
  - Require 1–8 unique terms, each 2–40 characters.
  - Canonicalise `args['query']` to the space-joined sorted unique terms. `key` dedup then works,
    and reordered queries count as repeats.
- **No model-controlled FTS syntax.** The FTS expression is built only from those terms:
  `' AND '.join('"%s"' % t for t in terms)`.
  - If the AND form returns zero hits, fall back to the OR form. Record which mode was used in
    the result.

### 4. Execution (`ResearchTools._search`)
Constants:
- `MAX_SEARCH_RESULTS=8`
- `SEARCH_SNAPSHOT_PROVIDER='news_archive'`

**Article hits.**
```sql
SELECT a.id, bm25(news_article_fts) AS rank FROM news_article_fts f
JOIN news_articles a ON a.id=f.rowid
JOIN company_news_articles l ON l.article_id=a.id AND l.company_id=?
WHERE news_article_fts MATCH ?
  AND julianday(COALESCE(a.first_seen_time,a.created_at))<=julianday(?)
  AND (a.content_updated_at IS NULL OR julianday(a.content_updated_at)<=julianday(?))
ORDER BY rank LIMIT ?
```

**Document hits.**
- The same pattern over `evidence_document_fts`, filtered on `company_id`.
- The time filter is the document's `ingested_at <= cutoff`. Check it via
  `get_evidence_document` in `validate_result`.

**Merging.**
- Interleave both hit lists by rank and keep the top 8.
- If more hits exist, set `more_results_available=True` in the result. This is not a gap.

**Snapshotting article hits** (new `services/research_search.py: snapshot_article(article_id,
company_id, *, db_path)`):
- Build an `EvidenceDocument` with these fields:

  | Field | Value |
  |---|---|
  | `document_id` | `'news:' + article_id + ':' + sha256(title+summary+body)[:16]` |
  | `provider` | `'news_archive'` |
  | `document_type` | `'news_article'` |
  | `source_url` | `source_url` or `normalized_url` |
  | `publisher` | `source` |
  | `text` | `title\n\nsummary\n\nbody` |
  | `first_observed_at`, `ingested_at` | `first_seen_time` (or `created_at`) |
  | `published_at` | `min(published_at, first_seen)`; record `published_at_clamped` in metadata if clamped |
  | `metadata` | `{company_id, article_id, text_snapshot_at: now, content_updated_at}` |

- As built: when a document with that id already exists, the stored one is reused rather than
  re-recorded, because `text_snapshot_at` would otherwise change the immutable record's hash. `ingested_at`
  is the later of first sight and `content_updated_at`, so a snapshot never claims its text was known earlier.
- Record it with `record_evidence_document`. `insert_record` is idempotent on identical
  content, and a changed text yields a new `document_id`, so the call is safe to repeat.
- The new document's FTS row is then added by the trigger. Exclude `provider='news_archive'`
  from document hits so an article is not counted twice.

**Facts.** Return the active `company_facts` whose `document_id` is among the document hits,
using the same pattern as `get_event_documents`.

**Validation.**
- Pass everything through the existing `validate_result`. Document equality, company, cutoff
  and fact checks are unchanged.
- `available = bool(documents)`.
- Zero hits gives the gap `search_no_results`. There is never a truncation gap.

**Dispatch.** Add a `search_archive` branch at the top of `execute`, next to
`get_event_price_response`.

### 5. Planner ([`agentCallers/agent_stages/research_planner.py`](../../Python%20Scripts/agentCallers/agent_stages/research_planner.py))
- Add `'query': {'type': 'string'}` to the `REQUEST_SCHEMA` argument properties.
- Add one sentence to `SYSTEM_PROMPT`: `search_archive` takes a short keyword query (no
  operators) and returns dated articles and filings for this company published before the
  cutoff; use it to find contradicting or missing context.
- Bump `PROMPT_VERSION` to `'bounded-research-planner-v3-search'`. In-flight sessions end with
  `planner_version_changed_during_session`, which is the existing, intended behaviour.

### 6. Flag and budget ([`services/evidence_research_policy.py`](../../Python%20Scripts/services/evidence_research_policy.py))
- Add `search_enabled: bool = False` to `EvidenceResearchPolicy`, read from
  `RESEARCH_SEARCH_ENABLED` in `from_env`. It is snapshotted into `progress['policy']`.
- When the flag is off:
  - the orchestrator passes `OPERATIONS` without `search_archive` in the planner context
    (`research_orchestrator.py:185`);
  - `ResearchTools.parse` rejects the operation. Pass `search_enabled` into `ResearchTools`.
- Search calls count against the existing `max_tool_calls` and `daily_retrieval_calls`. No new
  budget is added.

### 7. Phase 4 compatibility
Snapshotted documents go into the final package's `document_ids` through
`merge_evidence`/`final_package`, which are unchanged. Phase 4 claims cite them through
`document_ids`, and quotes are verbatim-checked against `text`
([`services/evidence_thesis_checks.py`](../../Python%20Scripts/services/evidence_thesis_checks.py)). No Phase 4 change is needed. Verify this in a test.

### 8. Docs
- [`docs/RESEARCH_PHASE3.md`](../../docs/RESEARCH_PHASE3.md):
  - add a row for the operation;
  - document the PIT semantics (`first_seen_time`, `content_updated_at`, NULL residual risk);
  - document the snapshot behaviour and the flag.
- `.env.example`: add `RESEARCH_SEARCH_ENABLED=false`.
- Dockerfile: no change, beyond the build-time test below.

## Tests
Extend [`tests/test_research_orchestrator.py`](../../tests/test_research_orchestrator.py), using the `ResearchFixture` and
`DiscoveryFixture.article()` helpers. Add `tests/test_research_search.py`.

**Parsing.** Length bounds, the term limits and canonicalisation:
`"Export  licence NVDA"` and `"nvda export licence"` give the same key.

**Injection.** Queries with `"`, `*`, `NEAR(`, `OR`, `-` and `:` never reach MATCH unescaped,
and no error is raised.

**PIT filtering.**
- An article first seen after the cutoff is excluded.
- An article whose content changed after the cutoff is excluded.
- An article with NULL `content_updated_at` is included.

**Scope and results.**
- An article linked only to another company is excluded.
- Overflow sets `more_results_available` and creates no gap, so the session can still reach
  `ready` when facts exist.
- Zero hits gives `search_no_results`.

**Snapshots.**
- The snapshot is idempotent.
- Changed text gives a new `document_id`.
- The snapshot is accepted by `record_evidence_package`.
- A Phase 4 claim quoting the snapshot text verifies.

**Flag.** When off, the operation is absent from the planner context and `parse` raises.

**Triggers.** The FTS row follows a title or body update. `content_updated_at` is set only on a
real change.

**Environment.** `test_fts5_available` asserts that `CREATE VIRTUAL TABLE ... USING fts5`
works. Dockerfile.worker runs the suite at build, so this guards the image.

## Verification
1. Run `python3 scripts/project.py test`, and run `docker compose build worker`; the build runs
   the tests, including the FTS5 check.
2. Copy the live database backup to a throwaway location and run the bootstraps on the copy. Confirm the
   `news_article_fts` row count equals the `news_articles` count.
3. With `DISCOVERY_MODE=shadow RESEARCH_MODE=shadow RESEARCH_SEARCH_ENABLED=true` and a local or
   free-tier planner, run [`scripts/research.py`](../../scripts/research.py)` --db <copy> consume --limit 2`. Then run
   [`scripts/research.py`](../../scripts/research.py)` --db <copy> replay 'research:…'`.
4. Check for a `search_archive` retrieval step whose `news:` documents appear in the final
   package.
