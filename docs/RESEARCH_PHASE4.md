# Phase 4: structured theses and claim checks

Phase 4 asks the strategist for a versioned, falsifiable `evidence_thesis` and checks every
claim deterministically against the frozen evidence the model saw. It runs on two routes:

- **Legacy strategist route**: the existing strategist → manager call. The thesis is added
  to the same model call, checked, persisted and handed to the manager unchanged. An entry
  gate after the manager records whether the thesis supports the manager's direction.
- **Research shadow route**: a separate model call over each finished Phase 3 research
  session's final package. It is shadow-only and has no path to the manager, selector or broker.

The legacy decision and confidence fields are unchanged. No thesis outcome grants trading
authority: in the default shadow mode, thesis checks never block an entry.

## Enable and operate

```dotenv
EVIDENCE_THESIS_MODE=shadow      # off | shadow | enforce
RESEARCH_DAILY_THESIS_CALLS=50   # research shadow route only
```

| Mode | Strategist prompt/schema | Checks and persistence | Entry gate |
| --- | --- | --- | --- |
| `off` | Legacy contract (`grounded-strategist-v2-company-evidence`) | None | Not attached; legacy eligibility |
| `shadow` (default) | Adds `evidence_thesis` (`grounded-strategist-v3-evidence-thesis`) | Every run | `allowed` stays true for directional decisions; `would_allow` records the verdict |
| `enforce` | As shadow | Every run | `allowed = would_allow`; the order builder rejects blocked entries |

`enforce` is guarded like the other V2 gates: `EvidenceThesisPolicy.from_env()` falls back to
shadow unless `research_gate_promotions` contains a matching `EVIDENCE_THESIS` proof for the
current policy and source hash. Setting the environment variable alone does not enforce.

The research shadow route runs inside `run_discovery_shadow_cycle` after research consumption,
and only when `RESEARCH_MODE=shadow` and the thesis mode is not `off`. To run it manually:

```bash
python3 scripts/research.py --db /path/to/market.db theses --limit 6
python3 scripts/research.py --db /path/to/market.db thesis 'thesis:research:HASH'
```

### Shadow-period report

```bash
python3 scripts/research.py --db /path/to/market.db thesis-report --days 7
python3 scripts/research.py --db /path/to/market.db thesis-report --days 0 --route legacy_strategist
```

The read-only report covers the last `--days` days, or all history when `--days` is `0`.
`--route` filters the check statistics only.

- **`checks`**: status counts and shares. Invalid-output and deferral rates are computed over
  checks where a thesis was requested. Also: why theses were not requested, thesis-level
  failure codes, claim failure codes grouped by prefix (`unsupported_number`,
  `quote_not_in_source`, ...), the most common shape errors, thesis directions, and semantic
  review status.
- **`entry_gate`**: legacy-route `EVIDENCE_THESIS` verdicts from `manager_decision_history`.
  Includes the would-block rate among directional decisions and its reason codes. Also counts
  would-block entries that passed every other gate and had a contract (what enforcement would
  actually remove), and executed trades the gate would have blocked.
- **`descriptive_outcomes`**: mean realized P&L of closed trades, split by gate verdict. This
  is monitoring only; promotion still requires the Part 6 counterfactual reports.
- **`research_route`**: finished research sessions still awaiting a thesis, and any unresolved
  reservations.

In the first shadow weeks, watch `invalid_output_rate` and the top claim failures. A high rate
means the model is not producing checkable theses, and the prompt needs tuning before the gate
data is meaningful.

## Cycle failure isolation

A larger strategist response makes an individual model failure more likely, so a failure in
one company's strategist, manager or contract-selection stage no longer ends the cycle. The
failure is:

- recorded as a `stage_error` candidate rejection (terminal for research enrollment)
- logged with its traceback
- emitted as a `company_failed` progress event
- listed under `failed_companies` in the agent result (`symbol`, `candidate_id`, `stage`, `error_type`, truncated `error`)

A failed company never reaches the execution callback, and the remaining companies continue.
Three consecutive company failures usually indicate a shared outage, such as the model
provider being down. After three in a row the cycle stops early, and the remaining companies
are recorded with stage `NOT_RUN`. Companies that completed earlier keep their results, so
the batch path still builds orders for them. Exceptions raised by the execution callback
itself are not isolated and still end the cycle.

The writer is configured like the strategist and manager stages:

| Setting | Resolution |
| --- | --- |
| Provider | `RESEARCH_THESIS_PROVIDER` → `STRATEGIST_PROVIDER` → `LLM_PROVIDER` (any shared provider) |
| Model | `RESEARCH_THESIS_MODEL` → `STRATEGIST_MODEL` → `MACRO_NEWS_MODEL` → `WORLD_NEWS_MODEL`; Vertex falls back to `VERTEX_DEFAULT_MODEL` |
| Ollama host | `RESEARCH_THESIS_OLLAMA_HOST` → `STRATEGIST_OLLAMA_HOST` → `MACRO_NEWS_OLLAMA_HOST` → `WORLD_NEWS_OLLAMA_HOST` → `http://127.0.0.1:11434` |
| Token limits and rates | `RESEARCH_THESIS_<NAME>` for each `RESEARCH_PLANNER_<NAME>` limit/rate, falling back to the planner value |

A thesis model on a billed provider (Vertex, `openai`, or any provider with rates) that differs from the planner model must set
`RESEARCH_THESIS_INPUT_USD_PER_MILLION` and `RESEARCH_THESIS_OUTPUT_USD_PER_MILLION`. Otherwise
construction fails rather than reserving spend at another model's prices. The writer still
uses the planner's single-attempt, deadline-bounded model call and reservation contract. Its calls count
against `RESEARCH_DAILY_THESIS_CALLS`, and its reserved cost shares `RESEARCH_DAILY_COST_BUDGET`
with planner and retrieval calls. The resolved writer configuration, without credentials, is
part of the pipeline foundation manifest.

## Thesis contract (`evidence-thesis-v1`)

The model writes these fields (schema: `agent_stages/evidence_thesis_contract.py`):

| Field | Meaning |
| --- | --- |
| `causal_mechanism` | How the observed change affects the business |
| `change_vs_prior_expectations` | `assessment` (`supported_comparison`/`unknown`), `baseline_type` (`prior_guidance`/`consensus`/`prior_actual`/`none`), `statement`, and the `claim_ids` that establish it |
| `direction`, `horizon` | `up`/`down`/`unclear`; `minimum_days`..`maximum_days` calendar days for the thesis to play out |
| `forecast_uncertainty` | Ordinal `low`/`medium`/`high`; never converted to a probability |
| `supporting_claims`, `opposing_claims` | Claims with `claim_id`, `statement`, `claim_type` (`observed_fact`/`calculation`/`inference`), `central`, `fiscal_period`, `fact_ids`, `document_ids`, `article_ids`, and verbatim `quotes` |
| `invalidation_conditions` | Observable `trigger`, `data_source` and `review_within_days` |
| `unresolved_questions` | What remains unknown |

Python adds `schema_version`, `evidence_package_id` and `research_status` after
normalization. The model is never asked for them, so it cannot misstate them. The legacy
route's `research_status` is `not_investigated`, because that route has no research session.

Shape normalization is strict. A missing or mistyped field makes the thesis
`invalid_output`; no defaults are invented.

## Deterministic checks

Checks read the frozen package (facts, full documents, package claims) from immutable
storage, plus the article text the model saw. Per claim:

- **References**: every fact/document ID must belong to the package, and every article ID
  must be in `grounding.article_ids`. Uncited claims fail.
- **Spans**: each quote must be a whitespace-tolerant verbatim match inside its source. For a
  fact that means inside the fact's own source span, not merely somewhere in the document.
  The quote's source must also be one of the claim's citations. Resolved character offsets
  are stored. An `observed_fact` needs a cited fact or a verified quote.
- **Numbers** (`observed_fact` and `calculation`): each stated quantity must be a rounding of
  a cited fact (value, range bound, midpoint, with scale and billion/million/thousand
  conversion), a difference or percent change between cited compatible facts, a package
  comparison over cited facts, or appear verbatim in cited source text. Fiscal labels
  (`Q3`, `FY2026`) and form names (`10-Q`) are not treated as quantities. Inference numbers
  are listed as `unverified_numbers`, not checked.
- **Compatibility**: calculation inputs must share metric, period type, basis, unit, currency
  and scale. Guidance and actuals must also share the fiscal period.
- **Fiscal period**: a non-empty `fiscal_period` must equal a cited fact's period.
- **Market surprise**: asserting a beat, miss or surprise against estimates needs a cited
  consensus fact, or a cited source making the same assertion.

Thesis-level coverage (any failure defers):

- Horizon and review timing are within 1..`max_horizon_days` (90).
- A directional thesis needs a central supporting claim, at least one opposing claim and an
  invalidation condition.
- No central claim failed.
- `unknown` pairs with `none`. A `supported_comparison` must reference verified
  fact/calculation claims whose facts contain an exactly comparable baseline. Prior guidance
  or consensus needs the identical comparison key. A prior actual needs the same key for a
  different period.
- Every package opposing claim visible to the model is addressed by citing one of its facts.

Status is `validated`, `validated_with_warnings` (only non-central claims failed),
`deferred`, `invalid_output`, `check_error`, `not_requested` or, on the research route,
`failed`.

### Semantic review hook

`check_evidence_thesis(..., reviewer=...)` accepts an optional reviewer with
`review(thesis, claim_results)`. It returns per-claim `consistent`/`inconsistent`/`unclear`
verdicts. Verdicts are stored with `authority: review_signal_only`, and disagreement sets
`requires_human_review`. They never change the deterministic status or the gate. No reviewer
is configured or called by default, so the hook adds no cost.

## Handoff and gating

The strategist recommendation carries `evidence_thesis` plus a compact `evidence_thesis_check`
(status, evaluation ID, package ID, failures, failed claims). The manager's visible strategist
context passes both through unchanged only when the check's package ID equals the manager
payload's frozen `company_evidence` package. Otherwise it withholds the thesis and sets
`evidence_thesis_handoff=package_mismatch`. The manager prompt treats failed or missing checks
as unverified support.

After the regime gate, `evaluate_evidence_thesis_gate` reads the thesis the manager actually
received. It blocks (`would_allow=false`) for any of:

- a non-directional decision
- a missing, deferred or invalid check
- an unpersisted check
- a thesis direction that disagrees with call/put
- a research status other than `ready`/`not_investigated`
- a package mismatch

The verdict is recorded as the `EVIDENCE_THESIS` gate and carried to the selected-options
output, decision history and order candidates. That lets Part 6 research reports measure it
like the continuation and regime gates.

## Persistence and replay

| Record | Contents |
| --- | --- |
| `evidence_thesis_checks` | Immutable, hash-checked: raw model thesis, normalized thesis, per-claim results and spans, failures, semantic review, policy/schema versions, package, candidate/session links. The research route also stores model input, raw response, reservation, usage and diagnostics. |
| `research_thesis_progress` | A reservation made before a research-route call. A leftover row means the outcome is unknown; the session is recorded `failed` and never retried automatically. |
| `research_thesis_allowances` | Daily thesis calls and reserved cost. |

The legacy check is persisted before the strategist run row, and the run's stored
recommendation carries its `evaluation_id`. The strategist run already stores the full input
payload and raw response. A research session gets at most one thesis check
(`thesis:research:<hash of session>`). Rechecking a stored thesis needs only the database and
no model: pass `record["raw_thesis"]` to `check_evidence_thesis` with the recorded input. For
the research route that is `record["context"]["model_input"]`; for the legacy route it is the
linked `strategist_runs.input_payload`.

## Design decisions

- **`evidence_thesis`, not `thesis`**: the legacy recommendation already has `thesis` as a
  list of strings, consumed by summaries, the manager and history. Reusing the name would
  break those consumers. The object is additive under a new key, with `evidence_*` names
  throughout, to avoid confusion with the existing position `thesis_validator`.
- **Same model call on the legacy route**: a second strategist call would double cost and
  could see different evidence. The thesis is part of the same structured response.
- **The gate carries the deferral**: `order_candidates` only blocks strategist
  `do_not_trade`, so downgrading to `watchlist` would not stop an order. The gate follows the
  regime-gate pattern: evaluated after the manager, recorded, and checked at order
  eligibility. The manager still runs in every mode, so shadow comparisons stay complete.
- **Promotion-guarded enforcement**: enforcement only removes entries, but the continuation
  gate, also restrict-only, is guarded too. Consistency and measurable counterfactuals won.
- **Thesis failures never fail the strategist**: shape or check errors become statuses, not
  exceptions. A strategist that fails for other reasons, such as unparseable legacy fields,
  is isolated to its company (see "Cycle failure isolation").
- **Articles-only baselines are unknown**: an article reporting "raised guidance from X"
  is not a verified baseline. `supported_comparison` requires comparable package facts.
- **Research theses reuse planner infrastructure, not planner configuration**: the writer
  subclasses the planner's transport, deadline and reservation code, and allowances share
  the daily cost budget. Its model and host follow the strategist/manager
  `<STAGE>_MODEL`/`<STAGE>_OLLAMA_HOST` convention, because it writes the strategist's thesis
  rather than planning retrieval. One thesis
  per session. Only `ready` and `conflicting_evidence` sessions spend a model call; other
  terminal sessions get a `not_requested` record, so Phase 7 can tell deliberate
  non-evaluation from missing data.
- **No invented quality score**: `evidence_support` reports claim counts. Evidence quality
  (checks), forecast uncertainty (model, ordinal) and execution suitability (the manager's
  `execution_confidence`) stay separate.

## Limitations

- Deterministic checks verify references, quotes, numbers and comparability, not whether an
  inference follows. Only the optional reviewer looks at that, and only as a signal.
- Number matching accepts roundings and verbatim occurrences. A claim can cite the right
  number with the wrong meaning; spans and fact kinds narrow that but do not eliminate it.
- The market-surprise check is lexical and conservative. It catches assertions like "beat
  estimates", not every paraphrase.
- Research-route theses do not retry provider failures. A failed session is recorded; a new
  evidence revision gets a new session and thesis.
- Legacy packages frame `supporting`/`opposing` as bullish/bearish, so a bearish thesis must
  still address "opposing" guidance decreases by citing them.

## Code and validation

| Concern | Location |
| --- | --- |
| Contract, schema, normalization | `Python Scripts/agentCallers/agent_stages/evidence_thesis_contract.py` |
| Claim and coverage checks | `Python Scripts/services/thesis/evidence_thesis_checks.py`, `evidence_thesis_numbers.py` |
| Check orchestration, gate, compact view | `Python Scripts/services/thesis/evidence_thesis.py` |
| Policy | `Python Scripts/services/thesis/evidence_thesis_policy.py` |
| Strategist/manager integration | `strategist_runner.py`, `strategist_prompt.py`, `strategist_schema.py`, `manager_strategist_context.py`, `manager_prompt.py`, `agentCallers/decision_pipeline.py`, `services/trading/order_candidates.py` |
| Research shadow route | `agent_stages/research_thesis_writer.py`, `services/research/research_thesis.py` |
| Storage and report | `Data/market_db_modules/evidence_thesis.py`, `evidence_thesis_schema.sql`, `evidence_thesis_report.py` |
| Tests | `tests/test_thesis_evidence_contract.py`, `tests/test_research_thesis.py`, `tests/test_agent_runner_main.py` (failure isolation) |

```bash
DB_PATH=/tmp/phase4.db TELEMETRY_ENABLED=false PYTHONPATH=tests \
  python3 -m unittest tests.test_thesis_evidence_contract tests.test_research_thesis
```

Implementation verification:

- The 67 Phase 4 tests and 7 cycle failure-isolation tests pass. They cover the plan's
  acceptance cases: a valid article ID with an unsupported number fails, opposing evidence
  survives the manager handoff, a missing expectation baseline must be explicitly unknown,
  and legacy payload/prompt tests pass. They also cover spans, scaling and rounding, incompatible periods and bases, surprise assertions,
  package opposing evidence, the semantic hook, immutability, the gate in all modes, order
  eligibility, `off`-mode legacy behaviour, one-thesis-per-session, unknown-outcome recovery,
  the shared daily budgets, the writer's strategist-style model/host/rate resolution, and the
  shadow-period report.
- The strategist/manager, research, discovery, evidence, execution and V2 Parts 1–6 suites pass.
- The full discovery run: 947 tests, 2 failures, 2 errors, 30 skips. The errors are the
  pre-existing Flask-stub interaction noted in Phase 0; Flask is not installed here. The
  failures are two `PlannerContractTests` that pass in isolation.
  `tests/test_market_context_volatility.py` calls `load_project_env()` at import, which loads
  the local `.env` (`LLM_PROVIDER=vertex`) into later tests.
- No live model, provider or broker call was made.
