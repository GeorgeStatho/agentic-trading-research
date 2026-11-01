# Phase 6: event-triggered thesis reviews

Phase 6 reviews an open position's entry thesis as soon as a material event arrives for its
company, instead of waiting for the daily 15:30 ET validator run. Reviews reuse the daily
validator's evidence pack, adjudicator and arbiter. They record what changed against the
previous verdict and why.

Everything is **shadow only**. The arbiter's action (close, close half, flag, tighten) is
stored as `would_be_action` and never executed, even when `THESIS_VALIDATOR_MODE=live`. Exit
authority for event reviews is a separate rollout decision.

## Enable and operate

```dotenv
DISCOVERY_MODE=shadow                     # required: discovery creates the position_review items
EVENT_THESIS_REVIEW_MODE=shadow           # off | shadow (default shadow)
EVENT_THESIS_REVIEW_DEBOUNCE_SECONDS=900  # wait after an event's latest revision
EVENT_THESIS_REVIEW_DAILY_LIMIT=3         # model-backed reviews per position per New York day
```

Discovery itself makes no model calls (see [Phase 2](RESEARCH_PHASE2.md)), but with
`DISCOVERY_MODE=disabled` no review items exist and Phase 6 stays idle. Each review costs the
daily validator's sample count of adjudicator calls (`THESIS_VALIDATOR_SAMPLES`, default 3) and
uses its model (`THESIS_VALIDATOR_MODEL`), temperature and news limit.

Inspect reviews without network or model access:

```bash
python3 scripts/research.py --db /path/to/market.db event-reviews --days 7
python3 scripts/research.py --db /path/to/market.db event-review 'event-review:HASH'
```

`event-reviews` counts statuses, thesis states, would-be actions, how many reviews changed the
verdict, and how many cited the new evidence.

## How it works

```text
news / primary filings -> discovery event (revision, provenance, syndication merged)
  -> position_review queue item for a company with an open thesis
  -> settles for the debounce window (a newer revision expires the older item)
  -> option manager, after mechanical exits and the daily validator, claims 1 item per cycle
  -> every open thesis on that company: skip checks -> evidence pack -> adjudicator -> arbiter
  -> immutable review record (would-be action only) -> queue item completed
```

The discovery worker no longer claims `position_review` items, and `DISCOVERY_REVIEW_SLOTS` is
gone. Reviews have their own consumer, so they never compete with discretionary research for
discovery slots. The same event is still researched through its `entry` item.

### Review identity, debounce and budgets

- **Identity.** One review per open thesis per queue item, and a queue item is one event
  revision. The daily validator's one-run-per-date check is untouched and does not suppress
  event reviews, nor the other way round.
- **Debounce.** An item is claimed only once it is 15 minutes old. A newer revision of the same
  event expires the older item, so a burst of updates is reviewed once, at its latest revision.
- **Coalescing.** Reviews are keyed on a hash of the event's evidence content: news root text and
  primary-evidence signature. Evidence the thesis was already reviewed on, for example a reverted
  revision, is `coalesced`, with no model call.
- **Syndicated copies.** These never create a new revision; discovery groups them under one
  provenance root.
- **Daily limit.** At most three model-backed reviews per position per New York day. Beyond that,
  `daily_limit_reached` is recorded, and the scheduled daily review still sees the evidence.
- **Latency.** Reviews run inside the position-management loop, so their model calls delay the
  next mechanical check. One item per cycle bounds that delay to one review.

### Statuses

| Status | Model call | Meaning |
| --- | --- | --- |
| `evaluated` | yes | Adjudicated; verdict, would-be action and change recorded |
| `position_not_open` | no | The thesis is open but no broker position matches it |
| `rules_exit_precedence` | no | The rules layer already decided to sell |
| `active_exit_order` | no | An exit order is working; the review stays out of its way |
| `no_new_evidence_since_entry` | no | Every event article was first seen before entry, with no new primary evidence |
| `coalesced` | no | Same evidence as an earlier review of this thesis |
| `daily_limit_reached` | no | Daily budget used |

A model outage (every sample failed) or an unexpected error records nothing. The queue item is
deferred and retried with backoff, up to `DISCOVERY_MAX_ATTEMPTS`, so an outage cannot mark an
event as reviewed.

### The evidence pack

The daily validator's pack (`build_thesis_evidence_pack`) gains these additions:

| Addition | Contents |
| --- | --- |
| `review_trigger` | Catalyst family, type and direction, change kind, publication time, `trigger_article_ids` and any primary-evidence package. IDs and metadata only, no source text. |
| New articles | Event articles first seen after entry are placed first in `news_since_entry`, inside the adjudicator's untrusted article blocks, and added to `allowed_evidence_article_ids` so they can be cited. |
| `previous_verdict` | State and would-open of the latest daily or event verdict. The previous rationale is left out to avoid anchoring. |
| `structured_entry_thesis` | When the entry decision has a Phase 4 check: direction, horizon, causal mechanism and observable invalidation conditions. Otherwise the templated conditions from `position_theses` are all the pack has. |
| `review_reason` | A fixed sentence telling the adjudicator which articles are new. |

### What changed and why

Each evaluated review stores:
- `change.thesis_state` and `change.would_open_today` (before and after) against the latest
  daily or event verdict, and `change.changed`
- `change.cited_trigger_article_ids`: which new articles the verdict cited
- the adjudicator's `rationale`, `evidence_ids`, samples and consistency
- the full evidence pack
- the option mid price at review time, as the counterfactual exit mark

## Safety boundaries

- **No execution path.** The service has no broker callbacks; `executed` is always false and
  `authority` is `shadow_only`.
- **Hard-risk priority.** A rules-layer sell or a working exit order is checked before any model
  call. The would-be action still passes through the arbiter's kill switch and daily exit cap, so
  the shadow record shows what the live rules would have allowed.
- **Failure isolation.** Reviews run after mechanical position management and the daily
  validator. Any exception is logged and returned as `{"error": ...}` in the management output.
  Position management has already finished for that cycle.

## Persistence

`thesis_event_reviews` (`Data/market_db_modules/thesis_event_reviews*.{py,sql}`) is additive,
immutable and hash-checked. It is unique on (thesis, queue item) and indexed by thesis day and
evidence hash. If a crash happens after a review is recorded, the retry reuses that review
instead of calling the model again.

## Design decisions

- **Discovery queue as the event source.** It already provides revisions, syndication merging,
  primary-filing evidence and fenced leases. The cost is that Phase 6 needs `DISCOVERY_MODE=shadow`.
- **Reviews own `position_review` items.** Previously the discovery worker turned them into
  research candidates, duplicating the entry route's research and never reaching a thesis review.
- **Shadow only, no live mode.** The daily validator's live authority does not extend to event
  reviews. A future live mode needs its own decision.
- **A new table, not the daily one.** `thesis_validation_runs` is unique per thesis and date by
  table constraint.
- **Outages retry instead of recording.** Recording a failed adjudication would coalesce the
  evidence and count against the daily limit.

## Limitations

- **New articles only.** "Material" means a new discovery revision with articles first seen after
  entry. Discovery treats any changed primary text as potentially material, so some reviews will
  find nothing relevant.
- **Live inputs.** The evidence pack fetches live daily bars at review time, so a review is not
  exactly reproducible from stored data.
- **Market-hours only.** Reviews run while the option manager manages positions, during market
  hours. A pre-market event is reviewed in the first cycle after the open.

## Existing behaviour found during implementation

1. **`.env.example` turns the daily validator live.** It sets `THESIS_VALIDATOR_MODE=live`, while
   its own comment and the code default say shadow. A copied template lets the daily validator
   close positions.
2. **A model outage consumes the day's daily review.** If every sample fails, the verdict is
   recorded as that day's run, so the run-once-per-date check skips the rest of the day.
   `record_thesis_validation_run` also silently drops a second run for the same date.
3. **Position theses use templated invalidation conditions.** `thesis_capture` writes generic
   text, and Phase 4's observable conditions are never linked to the position journal. Event
   reviews look them up by candidate; the daily validator still uses the templates.
4. **Discovery's position-review items never reached a review.** They became shadow research
   candidates like entry items.
5. **The daily pack windows news by publication time**, not when this system first saw it.
   It deduplicates by article key and title rather than syndication root.
6. **`normalize_timestamp` replaces an unparseable timestamp with the current time** instead of
   rejecting it.

## Code and validation

| Concern | Location |
| --- | --- |
| Policy | `Python Scripts/services/thesis/thesis_event_review_policy.py` |
| Review service | `Python Scripts/services/thesis/thesis_event_review.py` |
| Queue split and debounced claim | `Data/market_db_modules/discovery_queue.py` (`claim_position_reviews`) |
| Storage and summary | `Data/market_db_modules/thesis_event_reviews.py`, `thesis_event_reviews_schema.sql` |
| Lookups | `list_open_position_theses_for_symbol` (`thesis_validator.py`), `latest_strategist_thesis_check` (`evidence_thesis.py`) |
| Integration | `services/options/position_manager.py`, `Python Scripts/main.py`, `services/thesis/thesis_validator_service.py` (`rules_exit_active`) |
| Tests | `tests/test_event_thesis_review.py`, `tests/test_event_discovery.py` |

```bash
DB_PATH=/tmp/phase6.db TELEMETRY_ENABLED=false PYTHONPATH=tests:. \
  python3 -m unittest tests.test_event_thesis_review
```

The 20 Phase 6 tests use isolated databases and a fixed clock, with no model, provider or broker
calls. They cover the plan's acceptance cases:

- A morning contradiction is reviewed while the daily validator is not yet due.
- Duplicate evidence is not reviewed twice.
- A review failure stays out of mechanical position management.

They also cover live-mode independence, rules-exit and working-order precedence, debounce and
burst collapse, the daily limit, pre-entry evidence, outage retry, the discovery worker's
exclusion, trigger-article citation, change against the previous daily verdict, the structured
entry thesis, multiple positions per company, crash reuse and the summary.
