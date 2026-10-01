# Research foundation: Phase 0

Phase 0 adds persistence and comparison contracts. It does not enable new data providers, investigative model calls, event workers, economics enforcement, or broker submissions.

## Implemented boundaries

- `Data/research_foundation_contracts.py` defines versioned dataclasses for source documents, company facts, evidence packages, discovery items, research sessions, and shared observations. (An economics-report contract was removed in Phase 5, which stores reports in `candidate_economics_reports`.)
- `Data/market_db_modules/research_foundation_schema.sql` adds tables, indexes, and foreign keys without modifying historical candidate IDs, snapshot JSON, report JSON, or run configuration hashes.
- `company_evidence.py` stores immutable documents/facts/packages and supports dated fact lookup. Revisions get new identities. Facts retain units, accounting basis, fiscal period, source span, and availability time. Unknown numeric values require a reason.
- `research_foundation.py` supplies queue insertion/deduplication and session creation. Leasing, retries, session progress, retrieval steps, and report generation are reserved for later phases; their schema fields are available now.
- `research_observations.py` records frozen source observations and their links to independently produced strategy candidates. A candidate must be linked at creation, before decision snapshots exist.
- `services/research_versions.py` hashes evaluator implementation separately from a strategy's manifest. New runs carry an immutable evaluator identity; legacy runs retain their existing broad source checks.
- `services/evidence_research_policy.py` records non-secret research budgets, provider/adapter inventory, foundation source hashes, and economics assumptions in pipeline manifests. No provider adapters are enabled in Phase 0.

Public storage APIs are exported through `market_db`, `db_helpers.market`, and `db_helpers`. Schema initialization remains the existing `initialize_database` operation; it adds the foundation tables on the next normal initialization. The new foundation tests use isolated databases. For broad existing-suite runs, set `DB_PATH` to a disposable database as some older tests exercise default storage paths.

## Creating shared observations

The company and catalyst must already exist. A future discovery/research producer freezes an input snapshot once, then supplies its observation ID to every strategy evaluation. Observation identity includes the company, event revision, cutoff, source contents, and protocol; it excludes the strategy manifest and candidate ID.

```python
from research_foundation_contracts import ResearchObservation
from db_helpers.market import record_research_observation, record_candidate

observation_id = record_research_observation(
    ResearchObservation(
        company_id=company_id,
        symbol=symbol,
        catalyst_id=catalyst_id,
        event_revision=event_revision,
        information_cutoff_time=cutoff,
        source_snapshot={
            "evidence": dated_articles,
            "market_context": frozen_market_context,
        },
    ),
    db_path=database_path,
)

candidate = record_candidate(
    symbol,
    frozen_strategy_manifest,
    observation_id=observation_id,
    db_path=database_path,
)
```

If the source snapshot omits catalyst states, observation creation freezes the existing catalyst state into it. Subsequent candidate creation uses that frozen state, not refreshed news or catalyst data. Returned candidate context includes `source_snapshot`, `observation_id`, and `observation_cutoff` for the evaluator to consume.

Use `record_candidate_snapshot` with that returned context. Snapshots retain the observation ID and its cutoff. Enrollment validates the original source witness and rejects inconsistent market/evidence inputs or cutoffs. Research evaluation continues to validate feature timestamps and snapshot hashes.

The producer must actually evaluate each strategy on the frozen inputs. Phase 0 does not generate alternate model outputs. The existing live worker does not automatically create shared observations, and existing unlinked candidates are not retroactively paired.

## Research enrollment and promotion

1. Start each strategy's frozen-forward run **before** the shared observation cutoff. Each run still freezes its own exact strategy manifest.
2. Produce separate candidates for each strategy using the same observation ID. Store their independent outputs.
3. Collect through the existing `scripts/v2_part6.py collect` command. A run enrolls at most one candidate per observation. Duplicate evaluations, protocol mismatches, and pre-run observations do not increase cohort size.
4. Generate full-cohort reports. New reports contain `report_version`, `evaluation_version`, `observation_results`, `n_shared_observations`, and `n_legacy_unpaired`.
5. Replacement promotion compares frozen report outcomes using shared observation IDs, the same evaluator/protocol and evaluation policy, complete cohorts, and the existing sample/coverage/outperformance requirements.

When the incumbent and challenger were evaluated on the same cohort:

```bash
python3 scripts/v2_part6.py --db /path/to/research.db promote-champion CHALLENGER_REPORT_ID
```

For a later challenger on a new forward cohort, start a separate run of the incumbent manifest alongside it. Supply that new incumbent report explicitly:

```bash
python3 scripts/v2_part6.py --db /path/to/research.db promote-champion CHALLENGER_REPORT_ID \
  --incumbent-report INCUMBENT_COMPARISON_REPORT_ID
```

The comparison report must evaluate the exact currently promoted incumbent manifest and cover its entire sealed forward cohort. An older champion's historical cohort is never silently matched to a new cohort by ticker/date.

Changed strategy source does not stop new-version runs from evaluating already recorded selections when the evaluator is unchanged. Missing selections are not replayed through changed strategy code. Evaluator changes stop collection. Promotion proofs still require the current broad source identity; this separation does not weaken execution policy or activate stale gate proofs.

Legacy reports remain unchanged and readable. Legacy candidates without explicit observations remain unpaired. Re-evaluate an incumbent prospectively on shared observations when its original report cannot support replacement comparison.

## Configuration

The manifest records these initial budget settings:

| Setting | Default |
| --- | --- |
| `RESEARCH_MAX_ROUNDS` | `2` |
| `RESEARCH_MAX_TOOL_CALLS` | `6` |
| `RESEARCH_MAX_SECONDS` | `90` |
| `RESEARCH_DAILY_COST_BUDGET` | `0` |

They define future research limits and configuration identity; they do not start research work. The zero cost allowance does not authorize paid retrieval. Credential values are never included in the manifest. Provider configuration is currently an explicit empty inventory until adapters are implemented.

The V2 status API retains `code_hash_matches` for broad source identity and adds `collection_version_matches` for evaluator compatibility. The dashboard uses the latter where available.

## Validation

```bash
python3 -m unittest tests.test_research_foundation tests.test_research_observation_identity
python3 -m unittest tests.test_v2_part6 tests.test_api_v2_status
python3 -m unittest discover -s tests -p 'test_*.py'
```

Coverage includes empty/repeated migrations, preserved historical rows and hashes, evidence revisions, temporal and company mismatches, immutable records, queue deduplication, source/protocol mismatches, duplicate enrollment, evaluator drift, and a synthetic 100-observation replacement promotion using genuinely different candidate IDs and strategy manifests. Synthetic outcome fixtures validate mechanics, not investment performance.

Implementation verification:

- The focused foundation, observation, and V2 Part 6 suite passed all 114 tests.
- A subsequent unfinished-candidate cutoff regression was reproduced, fixed, and verified with 28 passing targeted checks, including the new regression.
- Repeated migration of a temporary copy of the local database preserved all inspected existing rows and passed SQLite integrity and foreign-key checks. The migration check opened its source read-only.
- The broad suite ran 617 tests with two errors and 28 skips in this environment. Flask is not installed, and an existing verdict test injects a minimal Flask stub that breaks two other API tests during combined discovery. The affected API modules skip appropriately in isolation. These errors do not represent a passing API integration run.
- Dashboard build verification was blocked: the available `npm` resolves to a Windows installation that cannot run under the current WSL environment; no Linux Node executable was available.
