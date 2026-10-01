# Phase 2: event discovery and shadow research intake

Phase 2 checks for meaningful changes among companies currently of interest to the
pipeline. It consumes durable notifications, tracks event revisions, and prepares
frozen research candidates for Phase 3. Discovery and research remain shadow-only.

## Enable the shadow route

Configure the collector and worker with:

```dotenv
DISCOVERY_MODE=shadow
DISCOVERY_SCAN_LIMIT=20
DISCOVERY_RESCAN_SECONDS=300
DISCOVERY_BATCH_SIZE=6
DISCOVERY_DIRECT_SLOTS=2
DISCOVERY_LEASE_SECONDS=120
DISCOVERY_MAX_ATTEMPTS=3
DISCOVERY_RETRY_SECONDS=60
DISCOVERY_STALE_SECONDS=604800
```

`DISCOVERY_SYMBOLS` is obsolete and ignored, including `*`. No manual universe is
required. Company selection comes from the same `company_interest` records used by
Phase 1: actual pipeline selections, opportunist findings, active investigations,
open positions, and watchlists. A news item about an unrelated database company
does not enroll that company. Collection completion does not renew interest.

Selections have a source, scope, selection time, and expiry. Updating a selection
replaces membership within that scope, including an empty selection. Independent
manager/industry selections and managed positions remain independent reasons for
interest. Recent opportunist/research interest expires after
`COMPANY_EVIDENCE_STALE_SECONDS` without renewal; active managed interest is
reconciled each cycle. Pre-upgrade explicit collection requests are adopted once
with their original age. Collection-generated requests never bootstrap interest.

Enable Phase 1 and provide its SEC contact to fetch missing primary evidence.
Phase 2 itself reads the archive; it never performs provider or model calls.
Discovery admission still does not certify broker tradability or option availability.

## Notifications and missing evidence

`discovery_checks` holds one coalesced check per company. New interest, changed
company-linked news, changed primary evidence, and watchlist changes wake it.
Repeated notifications for the same source version do not create more work.
`DISCOVERY_SCAN_LIMIT` bounds each batch. Periodic checks of active companies run
no more frequently than `DISCOVERY_RESCAN_SECONDS` when there is no new signal,
recovering missed notifications and checking scheduled review context. Waiting
checks are processed oldest first; normal research admission retains its urgency
priorities and reserved slots.

A missing or stale primary package creates or reuses a Phase 1 refresh request.
The check records `waiting_for_evidence` and the request ID. A refresh request is
not a fabricated market event. Existing news-backed events can still enter research
with explicit evidence gaps. Collection completion or failure wakes the check;
unchanged evidence does not create a new event revision or model run.

A process lock excludes concurrent check workers on one host/database. Generation
checks prevent an acknowledgement from erasing a newer notification. A crash leaves
work recoverable, and individual check failures retry after `DISCOVERY_RETRY_SECONDS`.
All provider TTLs, quotas, and collection retry limits remain owned by Phase 1.

Discovery candidates and collection-driven research continuations atomically claim
a shared research owner for the event transition, purpose, and immutable source
snapshot. Whichever route arrives second records the existing owner. This prevents
one collection update from launching two investigations, in either ordering.
A genuinely new revision, including a reversion, can start new research. Original
observations, candidate snapshots, and parent journals remain unchanged.

The collector persists events after its evidence refresh. Both full agent-stack
entrypoints scan and consume the queue alongside the existing sector route.
Results appear under `pipeline.discovery_shadow`; they never enter the live
`manager` or `selected_options` arrays. Target previews expose separate
`discovery_targets` / `discovery_companies` entries marked `discovery_shadow`.

## Discovery, provenance and revisions

Checks operate only on active interest. A bad company record is reported without
blocking the rest of the batch. Local event comparison retains normalized primary
text, financial facts, news roots, and schedules as revision inputs.

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

Claims reserve direct-event slots, then fill the remaining slots by aged priority.
Unused reservations flow to the general pool. Configuration must leave at least one
general slot so older sector/watchlist work can compete. The discovery worker never
claims `position_review` items: since Phase 6 the option manager's event thesis
review owns them (see [Phase 6](RESEARCH_PHASE6.md)), so they no longer need reserved
discovery slots.

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

Open-position items feed Phase 6 event-triggered thesis reviews, in shadow, rather
than research intake. The same event is still researched through its entry item.
Phase 3 supplies
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

Inspect shared interest and pending/deferred checks without invoking providers:

```bash
python3 scripts/discovery.py --db /path/to/database.db interest
python3 scripts/discovery.py --db /path/to/database.db checks
```

`tests/test_discovery_interest.py` covers selection replacement and expiry,
notifications, periodic recovery, collection dependencies, worker exclusion,
immutable replay, and discovery/continuation ownership in both arrival orders.
