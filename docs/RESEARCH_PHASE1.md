# Phase 1: earnings and guidance evidence

Phase 1 adds collector-owned primary evidence ingestion and database-only agent
payloads. Provider capabilities and primary documentation are recorded in
[the provider matrix](COMPANY_EVIDENCE_PROVIDERS.md).

## Enable collection

Set these environment variables for the news collector:

```dotenv
COMPANY_EVIDENCE_ENABLED=true
SEC_USER_AGENT=YourApplication operator@your-real-domain.com
```

No symbol list is required. The pipeline queues its actual selected companies before
company opportunist processing and before the strategist/manager stages. Saved
company opportunist findings and selected shadow research candidates also queue
refreshes. The collector services those requests, plus current open positions and
watchlist companies; it does not scan every company in the database.

`COMPANY_EVIDENCE_SYMBOLS` is obsolete and ignored. `DISCOVERY_SYMBOLS` is also
obsolete. Both phases use the shared `company_interest` registry. Issuer configuration
supplies source settings and identity overrides only; its keys do not select targets.
Company identities must already exist in `companies`. SEC ticker-to-CIK mappings
refresh daily and persist locally. The default collection cap is five companies
per cycle, shared across request sources.

The existing `news_collector_main.py` cycle records `company_evidence_refresh` in
its output. Collection is disabled by default. A missing SEC contact produces a
configuration diagnostic. With no due requests it returns `idle` without a provider
call. No paid API or model is needed for collection.

### Demand and investigation handoff

Requests persist in `company_evidence_requests`; interest is stored independently
in `company_interest`, so completing collection does not remove an active company. Repeated selections within a
refresh window deduplicate, and requests for the same company share a fetch.
Missing/stale-evidence investigations take priority; remaining requests run oldest
first. A process lock prevents overlapping collectors on the same host/database.
Demand older than `COMPANY_EVIDENCE_STALE_SECONDS` expires, and superseded investigations are removed before collection; current
positions and watchlists receive new periodic requests.

A deferred investigation records its refresh request atomically with its final
journal entry. The collector resolves the issuer, reuses provider caches, and
fetches when due under existing quotas. Research requests wait through provider
cache TTLs without consuming queue retry attempts. Provider failures preserve the
request with backoff, up to three collection attempts (each still subject to the
provider's own bounded HTTP retries). Failed identity/source configuration is
reported instead of substituting another issuer.

On the next research cycle, newly collected evidence creates a linked successor
session and observation with a later cutoff. Original inputs, evidence packages,
and journals remain unchanged. Identical evidence does not trigger another model
run. Each investigation gets at most one automatic collection-driven successor;
unresolved gaps remain explicit after that, and a newly discovered event can start
another investigation. Superseding events use the normal new-event research route.
See [Phase 3](RESEARCH_PHASE3.md) for replay and model budgets.

For a single collection cycle, export the variables above in your shell and run:

```bash
python3 scripts/company_evidence.py collect
```

Override the database with `--db /path/to/database.db` before the subcommand.
The CLI uses exported environment variables; the regular collector also supports
the project's existing dotenv loading.

## Optional issuer sources and identifiers

`COMPANY_EVIDENCE_ISSUERS_FILE` points to a JSON object keyed by company symbol.
An empty object for a symbol uses the maintained SEC mapping. A `cik` value
overrides it for a reviewed identifier association. To add an issuer RSS/Atom feed,
provide `feed_url`, `allowed_hosts`, and `allow_internal_cache: true` after checking
the publisher's terms. Example structure (replace the example host):

```json
{
  "NVDA": {},
  "YOUR_SYMBOL": {
    "cik": "0000000001",
    "feed_url": "https://investors.example.com/releases.xml",
    "allowed_hosts": ["investors.example.com"],
    "allow_internal_cache": true
  }
}
```

The feed association establishes issuer identity; fetched URLs must match its
HTTPS host allowlist. Redirects are rejected and must be resolved in configuration.
SEC releases furnished as EX-99 exhibits work without a separate issuer feed.

`ConsensusAdapter` is an optional injected integration for an independently chosen,
licensed provider. Return `CollectionResult`/`SourceDocument` records, the exact CIK
in metadata, and source-backed facts with `kind="consensus"`. Integrations must use
their own provider identity and quota policy. There is no bundled commercial
subscription, arbitrary endpoint, paid request, or synthetic consensus estimate.

## What is collected

- Recent 10-K/10-Q/8-K/20-F/6-K filings and amendments, including furnished earnings
  exhibits, through public SEC submissions and archive endpoints.
- Standard entity-wide US-GAAP companyfacts: revenue, net income, diluted EPS,
  gross/operating income, operating cash flow, cash, and identified debt components.
  Gross and operating margins are calculated only from compatible inputs in the
  same source snapshot. Debt components are not silently added into an assumed total.
- Explicit guidance ranges for revenue, diluted EPS and gross/operating margins.
  The initial deterministic prose parser requires a quarter/year or fiscal year,
  a range, explicit USD for monetary values, and an accounting basis for EPS/margins.
  It distinguishes prior guidance from current guidance. Revenue without a basis
  remains `unspecified`, and is not compared against a GAAP baseline.
- Publisher Event JSON-LD for scheduled earnings calls in configured releases.
  Date-only values stay date-only; cancelled/postponed dates are not confirmed.

This is not a universal filing parser. Custom taxonomies, segment XBRL, IFRS facts,
PDF/OCR, tables requiring semantic interpretation, inferred fiscal calendars,
single-value/plus-or-minus prose guidance, and implicit currencies remain coverage
gaps. Unsupported content stays available as a saved document. Adapters can supply
structured fact proposals through the same numeric, entity, period, accounting-basis
and source-span validation boundary. A model-driven extraction/research loop is
reserved for later phases.

## Time, revisions, and comparisons

Provider responses and normalized evidence documents are immutable. Response
versions, original URLs, timestamps, filing accessions and raw-response IDs remain
available for audit. Publication precision is retained; missing publication time
uses local observation time with `unknown` precision, never an invented midnight.
Fact availability is when extraction actually completes, not the filing's date.
Retrospective imports therefore cannot appear in an earlier live cutoff.

Every package freezes document/fact IDs, source-linked calculations, opposing
evidence, schedules, coverage gaps and freshness. A revision links to its predecessor;
the earlier package remains byte-for-byte reconstructable. Comparison keys include
metric, exact fiscal period, period type, basis, currency, unit and scale. Actuals
use reported start/end dates, not the filing's `fy`/`fp` labels, which may also wrap
comparative history. Guidance labels are normalized; converting them to exact
actual periods requires a verified fiscal calendar and is intentionally not inferred.

Conflicting current values or prior baselines are exposed and not averaged or
arbitrarily selected. Guidance midpoint changes store the formula and input fact
IDs. A reduction is opposing evidence. A guidance increase is not asserted to be a
market surprise; absent consensus remains an explicit gap.

Packages retain up to 24 documents and 240 source facts. Agent payloads show up to
48 facts and 600 characters per excerpt, prioritizing claim references and recent
periods, with truncation indicators. Full documents/facts remain in SQLite. Payloads
retain a saved package ID and recompute freshness at read time. The strategist's
evidence block is copied to the manager so a later collection cannot substitute a
different package during handoff. Existing article grounding and admission controls
remain in force; primary evidence alone does not yet create an entry candidate.

## Reliability and replay

The collector persists per-provider/company cursors, next-attempt times, failures,
and the persistent request queue. Each attempted HTTP call, including retries,
reserves daily quota before dispatch. SEC requests default to one per second,
500/day, a 20-second socket timeout, and two retries. SQLite serializes reservations
across collectors sharing that database; operators with separate databases must
coordinate the SEC's aggregate access limit themselves. Long Retry-After values
persist as provider cooldowns. Responses have a 20 MB decompressed limit.

Provider failures retain the last successful cursor and add a gap to the next
package. Caches avoid unnecessary downloads; changed and reverted content retain
their own observed versions. Repeated ingestion is idempotent. Freshness defaults
to seven days and refers to source age, not how recently a stale response was read.

Replay needs only the saved SQLite database, with no network, provider keys or model:

```bash
python3 scripts/company_evidence.py --db /path/to/database.db replay package:YOUR_ID
```

Replay defaults to the package's original cutoff. `--as-of` can request a later
freshness assessment; an earlier cutoff or different company is rejected.

## Code and verification

| Concern | Location |
| --- | --- |
| SEC, release adapters and normalized responses | `Python Scripts/Webscraping/company_evidence/` |
| Policy and collection | `Python Scripts/services/discovery/company_evidence_policy.py`, `company_evidence_service.py` |
| Validation and package calculations | `Python Scripts/services/discovery/company_evidence_extraction.py`, `company_evidence_packages.py` |
| Cache, quotas, identifiers, cursors | `Data/market_db_modules/company_collection.py`, `company_collection_schema.sql` |
| Historical evidence access | `Data/market_db_modules/company_evidence.py` |
| Bounded agent projection | `Python Scripts/agentCallers/agent_builders/company_evidence_payload.py` |
| Offline fixtures and acceptance cases | `tests/test_company_evidence.py` |

All new provider, schema, package, projection and configuration identities enter the
pipeline manifest. Contacts and credentials are not included. Additive migrations
preserve the existing Phase 0 records.

Run the focused tests with an isolated default database:

```bash
DB_PATH=/tmp/company-evidence-tests.db TELEMETRY_ENABLED=false python3 -m unittest tests.test_company_evidence
```

Tests use synthetic provider responses and temporary databases. They cover primary
filing and release collection, actuals/margins, missing and optional consensus,
restatements, revised/conflicting guidance, periods/basis, schedules, quotas/retries,
restart state, provider failures, and offline replay. No live SEC collection was
performed as part of implementation validation.
