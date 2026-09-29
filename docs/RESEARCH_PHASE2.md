# Phase 2: event discovery and shadow research intake

Phase 2 adds a rotating company scan, event revision tracking, a durable queue,
and frozen research candidates. It prepares inputs for the investigative loop in
Phase 3. It does not run a new model loop, submit orders, or trigger position exits.

## Enable the shadow route

Configure the collector and worker with:

```dotenv
DISCOVERY_MODE=shadow
DISCOVERY_SYMBOLS=AAPL,MSFT,NVDA
DISCOVERY_SCAN_LIMIT=20
DISCOVERY_BATCH_SIZE=6
DISCOVERY_DIRECT_SLOTS=2
DISCOVERY_REVIEW_SLOTS=2
DISCOVERY_LEASE_SECONDS=120
DISCOVERY_MAX_ATTEMPTS=3
DISCOVERY_RETRY_SECONDS=60
DISCOVERY_STALE_SECONDS=604800
```

Default mode is `disabled`; the only enabled mode is `shadow`. There is no live
discovery switch. Current pipeline interest, open positions and watchlists are
included automatically. `DISCOVERY_SYMBOLS` adds optional targets for the local
archive/news scan; `*` includes all existing company records in that scan. Neither
setting directly triggers provider collection. Discovery does not independently
certify broker tradability or option availability.

For fresh primary-source documents, enable the Phase 1 collector and supply its
SEC contact as described in [Phase 1](RESEARCH_PHASE1.md). Collection follows actual
pipeline selections and explicit investigation requests, with existing provider
quotas, cache policy, retry limits and the company-per-cycle cap. A company selected
by the shadow discovery consumer requests collection even when it lacks primary
evidence; later collection can resolve that gap.

The collector persists events after its evidence refresh. Both full agent-stack
entrypoints scan and consume the queue alongside the existing sector route.
Results appear under `pipeline.discovery_shadow`; they never enter the live
`manager` or `selected_options` arrays. Target previews expose separate
`discovery_targets` / `discovery_companies` entries marked `discovery_shadow`.

## Discovery, provenance and revisions

At least half of each bounded scan visits the least recently scanned companies.
The remaining capacity favors incoming company-linked news, confirmed earnings
events in the next seven days, open positions and watchlisted names. Event scanning
retains its rotation state; provider collection services the evidence request queue. A bad company
record is reported and does not prevent the rest of the scan from progressing.

News uses existing company links, catalyst IDs and syndication roots. Copies of
the same root use its canonical article rather than increasing independent support
or refreshing the queue's age. Primary packages reuse a linked news catalyst when
their URL or SEC accession matches; otherwise their existing primary-disclosure
catalyst is retained. Primary disclosures without a news root receive a persisted
source root. Links are conservative: there is no fuzzy cross-company event matching.

Revisions use normalized source content, active financial facts and scheduled
events. Download timestamps and regenerated document IDs alone do not create new
work. A changed value or qualitative disclosure creates a new revision; a later
reversion to an older value is another revision. Explicit corrected/restated news
and amended filings are labeled as corrections. New revisions expire unfinished
work for the superseded event, including running leases.

The idempotency key remains company + catalyst + evidence revision + purpose.
Sector and direct-event routes merge into the same entry item and retain both
origins. Watchlist and position-review purposes remain distinct. Watchlist rationale
updates can create deferred research work even when primary evidence is missing.
Sector-only items retain the configured opportunist support threshold and require
at least one actual supporting article; they do not gain invented primary evidence.

Each event freezes its evidence and cutoff. Discovery-specific package aliases
link the original immutable facts/documents to the chosen catalyst and material
revision without rewriting Phase 1 packages.

## Queue behavior

Priority records explain materiality, novelty, source reliability, freshness and
review urgency. Waiting time adds one point per hour without resetting on a repeated
delivery. Direction is not a priority multiplier: bullish and bearish events follow
the same rules.

Materiality detection is conservative: changed normalized primary text is treated
as potentially material. It is not a semantic determination that the economics
changed; later research must establish that distinction.

Claims reserve review slots and direct-event slots, then fill the remaining slots
by aged priority. Unused reservations flow to the general pool. Configuration must
leave at least one general slot so older sector/watchlist work can compete.

Claims run inside a short SQLite `BEGIN IMMEDIATE` transaction. Every attempt gets
a new fencing token. Completion, renewal and candidate persistence require the
current token and an unexpired lease. No provider or model call runs inside a queue
transaction. Lease expiry allows another worker to claim the item; an old worker
cannot complete or write a candidate afterward.

- `pending`: eligible when due.
- `running`: held by the current owner/token until lease expiry.
- `deferred`: retryable, with exponential backoff.
- `completed`: a supported shadow research candidate was saved.
- `expired`: source age exceeded the limit, or a newer event superseded the item.
- `failed`: retries were exhausted or an explicit permanent failure was recorded.

Crashes count toward the attempt limit. A crash after candidate persistence but
before queue acknowledgement reuses the same candidate on recovery. Immutable
events, observations and intake snapshots preserve the original decision inputs.
Phase 0 queue records that have no frozen event remain intact and are not claimed;
they need an explicit event attachment through `enqueue_discovery_event`.

## Admission and execution boundary

Fresh primary-company facts or a sufficiently supported primary news root can
satisfy research admission without opportunist coverage. The package must carry
usable source excerpts, dated documents and known actual/guidance values. Weak or
stale evidence becomes a deferred candidate with a recorded reason. This is
permission to investigate, not a finding that the company is an attractive trade.

Accepted and deferred candidates are saved in `candidates`, associated with a shared
`research_observation`, and linked through `discovery_shadow_candidates`. Their IDs
start with `discovery:`. The manager runner excludes these identities and marked
shadow contexts before model/manager processing, and the order-candidate builder
rejects them even if they are accidentally supplied with an otherwise eligible
recommendation. The streaming broker callback receives no discovery output.

Open-position items are research intake only. Phase 6 will connect material-event
reviews to the thesis validator under the existing exit controls. Phase 3 supplies
the bounded investigative/model loop. These intake snapshots are not completed
strategy evaluations or promotion evidence.

## Inspect and operate locally

The CLI uses exported environment variables, matching the Phase 1 CLI convention:

```bash
python3 scripts/discovery.py --db /path/to/database.db scan
python3 scripts/discovery.py --db /path/to/database.db consume
python3 scripts/discovery.py --db /path/to/database.db status
```

`scan` reads saved news and company evidence; it does not fetch providers. `consume`
creates shadow research inputs. `status` shows attempts, leases, completion reasons
and linked candidate IDs. These commands contain no broker integration.

## Implementation map

| Concern | Location |
| --- | --- |
| Policy, bounds and shadow mode | `Python Scripts/services/discovery_policy.py` |
| Event normalization and route merging | `Python Scripts/services/event_discovery.py` |
| Universe, schedules, watchlists and position inputs | `Data/market_db_modules/discovery_sources.py` |
| Atomic claims, retries and expiry | `Data/market_db_modules/discovery_queue.py` |
| Immutable shadow candidate persistence | `Data/market_db_modules/discovery_candidates.py` |
| Worker and research input projection | `Python Scripts/services/discovery_queue.py` |
| Research admission | `Python Scripts/services/discovery_admission.py` |
| Additive schema | `Data/market_db_modules/discovery_schema.sql` |
| Offline acceptance tests | `tests/test_event_discovery.py` |

Queue APIs are exported through the existing market database facades. New source,
schema and discovery policy settings enter the pipeline manifest.

Tests use isolated SQLite databases, synthetic documents/news and mocked providers.
They cover out-of-sector research intake, copy deduplication, changed/reverted
guidance, corrections, watchlist/position routing, priority reservations, rotation,
two concurrent claimants, crash recovery, fencing, retries, expiry, frozen inputs,
and suppression of model/order callbacks. No live provider or broker run is part
of implementation validation.

Validation: 131 discovery/evidence/pipeline tests passed, plus 25 execution and
admission checks with an initialized isolated database. The broader research
regression run also exercised the Phase 0 observation/comparison contracts. When
running execution tests separately, initialize their `DB_PATH` first; the existing
champion guard deliberately rejects an unreadable/missing database. Include `tests`
on `PYTHONPATH` for the existing V2 tests that import sibling test modules directly.
