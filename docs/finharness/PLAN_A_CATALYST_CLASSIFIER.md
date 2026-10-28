# Plan A: LLM-assisted catalyst classification

Status: proposed (2026-10-27). See [README.md](README.md) for the index.

## Context
Known issue #5 in [`docs/RESEARCH_GUIDE.md`](../../docs/RESEARCH_GUIDE.md) is that most catalysts are never classified. The
server backup (a local copy of the live database, opened read-only with
`immutable=1`) shows:

| Measure | Value |
|---|---|
| Catalysts in `needs_review` | 8,080 of 8,474 |
| Of those, with a ticker | 7,257 |
| `catalyst_type='other'` | 7,303 |
| `direction='unknown'` | 7,051 |
| Registered response events | 305 |

**Why it happens.** `classify_catalyst` ([`Data/catalyst_models.py:75-144`](../../Data/catalyst_models.py#L75-L144)) applies regex to the
headline only, and only for the earliest article. Status is `needs_review` exactly when family is
`other`.

**Who it hurts.** Unclassified events are never registered for price responses
(`pricing_research.register_response_events:39-51` filters `classification_status='classified'
AND ticker IS NOT NULL`). That starves four consumers:
- trade-economics scenarios
- Part 4 hurdles (`catalyst_stats`, minimum 30 samples)
- calibration labels
- the discovery ledger

**Inspiration.** The FinanceHarness paper uses a cheap "reader" model for long-document
extraction. [`docs/v2/TRADINGBOT_V2_IMPLEMENTATION.md:552`](../../docs/v2/TRADINGBOT_V2_IMPLEMENTATION.md#L552) already planned "deterministic rules
first, LLM assist only for ambiguous cases", but it was never built.

**Outcome.** Rules stay authoritative where they match. A cheap classifier model labels only
rule failures, and the labels survive catalyst refreshes and are PIT-correct for candidates.
More events reach price-response registration.

## Key constraint
`refresh_catalyst` ([`Data/news_db_modules/catalysts.py:93`](../../Data/news_db_modules/catalysts.py#L93)) and `attach_candidate_catalysts`
(:118) rebuild catalyst state from articles, then upsert every `catalysts` column
(`store_catalyst_state`). A label written directly into `catalysts` is lost on the next
article. Labels therefore live in their own table, which the state builder reads.

## Changes

### 1. Schema ([`Data/catalyst_schema.sql`](../../Data/catalyst_schema.sql), additive)
```sql
CREATE TABLE IF NOT EXISTS catalyst_model_labels (
  catalyst_id TEXT NOT NULL, label_version TEXT NOT NULL,
  catalyst_type TEXT NOT NULL, direction TEXT NOT NULL, confidence TEXT NOT NULL,
  anchor_article_id INTEGER, model TEXT NOT NULL, answered_by_json TEXT NOT NULL,
  labeled_at TEXT NOT NULL, PRIMARY KEY (catalyst_id, label_version, labeled_at));
CREATE INDEX IF NOT EXISTS idx_catalyst_model_labels_time ON catalyst_model_labels(catalyst_id, labeled_at);
```
- Add immutability triggers (`BEFORE UPDATE/DELETE ... RAISE(ABORT)`), following
  `discovery_schema.sql:34-40`.
- [`catalyst_schema.sql`](../../Data/catalyst_schema.sql) is in both bootstrap fingerprints (`Data/*.sql`), so the migration runs
  automatically.
- Readers and writers go in `Data/news_db_modules/catalyst_labels.py`:
  - `record_catalyst_label(...)`
  - `latest_catalyst_label(catalyst_id, *, as_of=None, conn)`
  - `pending_catalysts_for_labeling(limit, label_version, *, conn)`
- Labels are insert-only. A new `label_version` re-labels without overwriting, and an unresolved catalyst can
  gain further labels at the same version (see §5).
- `latest_catalyst_label` returns the newest *usable* label (not `low`, not type `other`), falling back to the
  newest label of any kind, so a later uninformative answer never hides an earlier usable one.

### 2. Taxonomy refactor ([`Data/catalyst_models.py`](../../Data/catalyst_models.py))
- Split `classify_catalyst` into:
  - `detect_catalyst(title, summary) -> (kind, direction, text)`, the current rule block;
  - `derive_classification(kind, direction, text) -> dict`, the current family, scope,
    subtype, horizon and mechanism logic, unchanged.
- `classify_catalyst` becomes `derive_classification(*detect_catalyst(...))`. Its output must be
  byte-identical to today's.
- Add `LABEL_VERSION = 'catalyst-llm-v1'` and `LABELED_CLASSIFICATION_VERSION =
  CLASSIFICATION_VERSION + '+' + LABEL_VERSION`.

### 3. Merge rule (`build_catalyst_state`, [`Data/news_db_modules/catalysts.py:20`](../../Data/news_db_modules/catalysts.py#L20))
- Add a keyword `label=None`.
- **When the label is used.** Apply it only if the rule result has family `other`, or direction
  `unknown`/`mixed`.
  When the rules found a kind, the label must also describe the same event: the same kind, or a kind in the
  same family (`product`/`customer_contract`). A rule `ma` with an `earnings, positive` label stays rules-only.
- **Kind.** Rules win on kind when the rule kind is not `other`.
  - Example: rule `earnings` with `unknown` direction plus label direction `negative` gives
    `earnings_negative`.
- **Building the state.** Rebuild the classification through
  `derive_classification(kind, label_direction, text)`.
- **New state fields.**
  - `classification_source ∈ {rules, rules+model}`. This also means a `CatalystState` field.
    `store_catalyst_state` only writes existing columns, so it lives in `state_json`, with no
    column change.
  - `classification_version = LABELED_CLASSIFICATION_VERSION`.
- **Confidence.** Low-confidence labels are recorded but not applied. Low confidence keeps
  `needs_review`.
- **Where labels are read.**
  - `refresh_catalyst` reads `latest_catalyst_label(catalyst_id, conn=conn)` with no cutoff, to
    show the current state.
  - `attach_candidate_catalysts` reads `latest_catalyst_label(catalyst, as_of=cutoff, conn=conn)`.
    A candidate frozen at time T never sees a label made after T.
- **Price-response events.** `register_response_events` freezes `state_json` at registration
  (`ON CONFLICT DO NOTHING`). Labelled historical events register with their labelled state.
  This is fine for response statistics: event time and prices are historical facts. The
  `classification_version` lets reports separate rule-only events from model-assisted ones.

### 4. Classifier stage
New `Python Scripts/agentCallers/agent_stages/catalyst_classifier.py`, with a helper in
`agent_helpers/catalyst_classifier.py`. Mirror [`agent_stages/company_opportunist.py`](../../Python%20Scripts/agentCallers/agent_stages/company_opportunist.py):

- **Model.**
  - Add `catalyst_classifier: StageModel` to `ModelSettings`
    ([`services/settings.py:82-100`](../../Python%20Scripts/services/settings.py#L82-L100)) as `StageModel.from_environ(environ, "CATALYST_CLASSIFIER",
    "COMPANY_OPPURUNTIST")`, so it inherits the company opportunist's model.
  - Keep a cached client, like `_get_default_client`.
  - Call `ask_classifier_model(client, model, system, user, temperature=0,
    thinking_level=..., response_schema=CATALYST_LABELS_SCHEMA)`. The existing
    `CLASSIFIER_FALLBACKS` chain (`llm_client.py:998`) applies automatically.
- **Input per catalyst.**
  - `catalyst_id`, `ticker`, plus the anchor article's `title`, `summary` and the first 1,500
    chars of `news_article_content.body`.
  - `later_headlines`: titles of up to 5 other articles in the catalyst, newest first, so a re-ask sees the
    coverage that arrived since the last answer.
  - The anchor is the earliest article by `event_time`, the same anchor the rules use.
  - The system prompt lists `CATALYST_TYPES` with one-line definitions, plus the direction enum.
  - Article text is untrusted data.
- **Batching.** 20 catalysts per call, using `build_token_limited_batches`
  ([`agent_helpers/text_utils.py:88`](../../Python%20Scripts/agentCallers/agent_helpers/text_utils.py#L88)).
- **Schema.** `{"labels":[{"catalyst_id","catalyst_type","direction","confidence"}]}`, with
  enums from `CATALYST_TYPES`, `{positive,negative,mixed,unknown}` and `{high,medium,low}`.
- **Validation.**
  - Drop items whose `catalyst_id` is not in the batch, whose enum is invalid, or that are
    duplicates.
  - Store `answered_by_json = json.dumps(answer.record())`.
- **Empty answers.** Handle the MAX_TOKENS empty-answer case as the company stage does (:237-247).

### 5. Runner and settings
`services/catalyst_classification.py: classify_pending_catalysts(*, limit=None, db_path=None) -> dict`:

1. Select pending catalysts: `classification_status='needs_review' AND ticker IS NOT NULL`,
   at least one linked article, and not the placeholders `catalyst:unknown:%` or `catalyst:sector-discovery:%`;
   and either no label at the current `LABEL_VERSION`, or fewer than `MAX_LABEL_ATTEMPTS` (3) labels there and an
   article first seen after the latest of them (re-ask on new evidence). Newest `first_seen_time` comes first.
2. Classify them in batches.
3. Record the labels.
4. Call `refresh_catalyst` on each labelled catalyst inside a transaction.
5. Return a summary: `{selected, labeled, applied, low_confidence, invalid, quota_stopped}`.
   - On `ModelQuotaExhausted`, stop the loop and return `quota_stopped=True`.

Settings:
- `CatalystClassifierSettings` (enabled, max_per_cycle) is added to `Settings`.
- `CATALYST_CLASSIFIER_ENABLED` defaults to `false`. Turn it on after the backfill is reviewed.
- `CATALYST_CLASSIFIER_MAX_PER_CYCLE` defaults to `200`.

### 6. Collector hook
In [`Python Scripts/news_collector_main.py`](../../Python%20Scripts/news_collector_main.py)`:run_news_collection_cycle`, before
`collect_catalyst_responses`:
```python
from services.catalyst_classification import classify_pending_catalysts
result['catalyst_classification'] = observe(classify_pending_catalysts)
```
- It runs before registration, so new labels are priced in the same cycle.
- `observe` keeps the rest of the cycle alive on failure.

### 7. Backfill CLI
New `scripts/catalysts.py` (argparse, `--db`, JSON output, like [`scripts/research.py`](../../scripts/research.py)):
- `classify --limit N`
- `status`: counts by status, `classification_source`, type and confidence.
- `review --sample 20`: prints labelled catalysts with their headlines for a manual spot-check.

The backfill of about 7,257 catalysts takes about 360 calls.

### 8. Reporting
`pricing_report` ([`services/catalyst_response_collector.py:180`](../../Python%20Scripts/services/catalyst_response_collector.py#L180)) adds counts by
`classification_source`. It also lists catalysts still `needs_review` because their type has no
family: `supply_chain`, `management`, `capital_return`, `financing`.

**Out of scope:** adding families for those types is a separate taxonomy decision.

### 9. Docs
- [`docs/RESEARCH_GUIDE.md`](../../docs/RESEARCH_GUIDE.md) Known issue #5: describe the mitigation and the setting.
- `.env.example`: add `CATALYST_CLASSIFIER_*`.
- [`docs/CODE_MAP.md`](../../docs/CODE_MAP.md): add the new modules.

## Tests
`tests/test_catalyst_classifier.py`. Build the temp DB with `tests/db_fixtures.temporary_database`
and add articles the way [`tests/test_v2_part3.py:26-54`](../../tests/test_v2_part3.py#L26-L54) does. Mock the model with
`patch.object(stage, "ask_model")`, as in [`tests/test_stage_decision_parity.py:52`](../../tests/test_stage_decision_parity.py#L52).

- `classify_catalyst` output is unchanged across a corpus of headlines.
  [`tests/test_v2_part4.py:113`](../../tests/test_v2_part4.py#L113) must still pass.
- A label applies only to rule failures. A rule-classified catalyst ignores labels.
- A rule kind plus a label direction gives the combined family.
- A low-confidence label is stored but not applied.
- The label survives `refresh_catalyst` after a new article joins the catalyst.
- `attach_candidate_catalysts` with a cutoff before `labeled_at` ignores the label.
- Invalid or foreign `catalyst_id` items are dropped.
- Quota exhaustion returns `quota_stopped` without raising.
- Placeholder catalysts are never selected.
- A labelled catalyst appears in `catalyst_response_events` after `register_response_events`.
- Immutability triggers reject UPDATE and DELETE on labels.

## Verification
1. Run `python3 scripts/project.py test`.
2. Copy the live database backup to a throwaway location and set `DB_PATH` to the copy.
3. Run `scripts/catalysts.py classify --limit 40` against a free-tier model, then
   `scripts/catalysts.py review --sample 20`.
4. Spot-check the labels against the headlines.
5. Run `register_response_events` on the copy and confirm that new events register.
6. Check that `pricing_report` coverage rises.
