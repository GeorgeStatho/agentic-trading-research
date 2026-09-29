# Phase 3: bounded investigative research

Phase 3 adds a separate, opt-in research worker for Phase 2 shadow candidates. It produces a dated evidence package and a recorded research outcome. The existing discovery observation remains immutable. No research outcome grants strategist, manager, broker or position-exit authority.
Research package IDs use the reserved `package:research*` namespace, excluded from default evidence/discovery reads so shadow enrichment cannot replace the legacy strategist's latest package.

## Enable and run

Keep Phase 1 evidence collection and Phase 2 discovery configured as described in their operating notes. Export configuration before starting the worker; the standalone CLI does not load `.env`.

```bash
export DISCOVERY_MODE=shadow
export RESEARCH_MODE=shadow
export RESEARCH_PLANNER_MODEL=your-installed-local-model
export RESEARCH_PLANNER_HOST=http://localhost:11434
python3 scripts/research.py --db /path/to/market.db consume --limit 6
```

The regular `run_discovery_shadow_cycle` also consumes research candidates after discovery intake. Both admitted and deferred shadow candidates can be investigated. A bounded batch can include interrupted sessions left by an earlier process. `RESEARCH_MODE=disabled` is the default; live mode is rejected.

The planner inherits `LLM_PROVIDER` (`vertex` or `ollama`) and `STRATEGIST_MODEL`, with the existing macro/world model fallbacks and Vertex aliases. Optional `RESEARCH_PLANNER_PROVIDER` and `RESEARCH_PLANNER_MODEL` override them. Unsupported providers fail explicitly; there is no silent fallback to another provider.

### Gemini through Vertex

Use the repo's existing Google Application Default Credentials. Project resolution follows `GOOGLE_CLOUD_PROJECT`, `VERTEX_PROJECT`, `GCP_PROJECT`, then the ADC-detected project. Location follows `GOOGLE_CLOUD_LOCATION`, `VERTEX_LOCATION`, then `global`. Credential files and tokens are never included in research payloads or manifests. The existing `google-genai` dependency supplies the client.

```dotenv
LLM_PROVIDER=vertex
STRATEGIST_MODEL=gemini-2.5-flash
GOOGLE_CLOUD_PROJECT=your-project
GOOGLE_CLOUD_LOCATION=global
RESEARCH_MODE=shadow
# Choose your own positive daily allowance and model-specific token rates:
RESEARCH_DAILY_COST_BUDGET=<daily USD allowance>
RESEARCH_PLANNER_INPUT_USD_PER_MILLION=<input USD per million tokens>
RESEARCH_PLANNER_OUTPUT_USD_PER_MILLION=<output USD per million tokens>
```

Replace the angle-bracket placeholders before running. Use conservative rates covering your selected model, region and applicable context-length tier; rates are operator configuration rather than a hard-coded pricing table. Consult [Google's pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing). A zero daily allowance prevents paid calls; absent/zero Vertex rates fail configuration validation. Local Ollama remains usable with zero provider cost.

Each attempt runs in a subprocess terminated at the remaining session deadline. Vertex also receives an HTTP timeout and `retry_options.attempts=1`; SDK retries, automatic function calling, explicit cache creation, grounding and provider tools are disabled. The research orchestrator owns retries. Only explicit HTTP 429/500/502/503/504 responses are retryable. Authentication failures, invalid/truncated output, network failures with unknown outcomes, and subprocess timeouts are not automatically retried.

`RESEARCH_PLANNER_MAX_OUTPUT_TOKENS` defaults to 4096. Gemini 2.5 uses `RESEARCH_PLANNER_THINKING_BUDGET=1024`; Gemini 3+ uses thinking level `LOW` unless `RESEARCH_PLANNER_THINKING_LEVEL` overrides it. Choose a level supported by the selected model. These are distinct API controls, and thinking budgets are soft limits; see [Google's thinking configuration](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking). `RESEARCH_PLANNER_THINKING_TOKEN_RESERVATION=32768` provides a separate conservative accounting allowance, never a claim that the provider enforces an exact thinking-token ceiling.

`RESEARCH_PLANNER_MAX_INPUT_TOKENS=131072` bounds a conservative text-only estimate: UTF-8 bytes for serialized messages/schema plus 1024 overhead units. It is intentionally stricter than ordinary tokenization and avoids an extra token-count API request. Provider-reported prompt, candidate, thinking, cached and total token counts are journaled after the request. Reported usage exceeding its reservation stops further research and any additional estimated cost is charged to the original reservation's UTC day.

Local Ollama uses the same deadline-controlled subprocess, configured output limit and journal. Its host remains `RESEARCH_PLANNER_HOST`; the [Ollama chat API](https://docs.ollama.com/api/chat) runs without automatic retries or redirects. Ollama cloud models remain unsupported; use Vertex for paid Gemini research.

## Approved retrieval and evidence boundaries

The six operations in `services/research_tools.py` search **already ingested evidence**, using the frozen discovery observation cutoff. If the collection package is older, an immutable research copy uses the observation cutoff while retaining its original evidence IDs:

| Operation | Retrieval |
| --- | --- |
| `get_event_documents` | Documents in archived packages for the same company and catalyst |
| `get_company_facts` | Requested metrics and fiscal periods |
| `compare_guidance` | Compatible current/prior guidance and a documented midpoint calculation |
| `get_available_consensus` | Recorded consensus facts, with explicit unavailability when absent |
| `find_contradictory_evidence` | Company facts, retained disagreements, and source references |
| `get_event_price_response` | Price responses whose recording and outcome times both precede the cutoff |

Every request carries the exact `company_id` and `as_of`; event operations also require `event_id`. Unknown arguments, arbitrary URLs, code, execution operations, a different company/event, and a changed cutoff are rejected. Filters are bounded and canonicalized so reordering them cannot evade duplicate detection.

The archive is the approved retrieval adapter in this release. SEC, issuer and optional consensus ingestion remain in Phase 1, with their existing provider quotas. The research worker does **not** perform fresh internet searches or extend its information cutoff. Evidence first collected after a decision cannot justify that earlier decision; a new observation/session must carry it. A configured consensus collector is still needed to populate consensus facts. Missing prior guidance or consensus is never fabricated.

Missing or stale evidence now creates a durable request for the Phase 1 collector.
The collector follows the actual research/manager company selection and explicit
research requests, without requiring a symbol universe. After collection, the next
research cycle prepares an immutable successor candidate, observation, and session
when source evidence changed. Its `continuation` field identifies the parent session,
refresh request, and prior observation. Replay of the parent keeps its original
cutoff and trace. Old articles are retained with their original dates.

The successor gets its own bounded session deadline and call limits while sharing
the existing daily model/retrieval/cost allowances. Only one automatic successor
is allowed per investigation, and unchanged evidence creates none. Superseded
investigations are handled by the normal new-event route. Disabled collection leaves
requests pending; disabled research never dispatches a successor model call.


Returned facts are checked against immutable records, company identity, timestamp, requested metric/period, numeric values and original source spans before joining the research package. Calculations require matching metric, fiscal period, period type, accounting basis, unit, currency and scale. Prior guidance can be explicitly labeled or come from the latest earlier dated forecast for the same period and basis. Simultaneous disagreements remain visible; ambiguous baselines cannot support a comparison. Historical forecasts remain recorded as baselines rather than current competing forecasts.

The planner sees dated excerpts and approved operation descriptions. Source text is untrusted evidence. Python adds mandatory requests for missing same-period prior guidance and an archive contradiction search even when the planner initially declares readiness. Critical unavailable results, missing verified company facts, ambiguous guidance baselines and truncated archive coverage prevent readiness. Readiness means sufficient evidence for subsequent thesis analysis; Phase 4 claim-level thesis validation is separate.

## Budgets and outcomes

Defaults are two planner rounds, six retrieval calls and 90 seconds per session, using `RESEARCH_MAX_ROUNDS`, `RESEARCH_MAX_TOOL_CALLS`, and `RESEARCH_MAX_SECONDS`. These are engineering limits, not calibrated trading assumptions.

`RESEARCH_DAILY_PLANNER_CALLS=100` and `RESEARCH_DAILY_RETRIEVAL_CALLS=600` are atomically reserved per UTC day across all sessions before dispatch. `RESEARCH_PLANNER_MAX_ATTEMPTS=2` includes the first attempt in each planner round: two rounds therefore allow at most four generation attempts. Every retry consumes another call and cost reservation. Exponential backoff starts at `RESEARCH_PLANNER_RETRY_SECONDS=1`, is capped at 30 seconds, and must fit the original deadline. Known retryable failures and their next-attempt times survive restarts.

The paid reservation uses the configured input rate and conservative input-token estimate, plus the output rate applied to the output-token limit and separate thinking-token reservation. Reservations are retained even when reported usage is smaller, missing, or the call fails. `cost_consumed` and daily `reserved_cost` are conservative budget consumption, not Google billing totals. `reported_cost_estimate_usd` sums estimates from known token usage, including thinking tokens; missing usage is explicitly unknown in the journal. Cached-token discounts are not assumed. These estimates depend on correctly configured prices and provider behavior; use Google Cloud billing controls for an independent spending boundary. Phase 1 collection retries retain their own provider accounting.

Stored terminal statuses are `ready`, `deferred_missing_evidence`, `conflicting_evidence`, `budget_exhausted`, `superseded`, and `failed`. Each records a reason. Repeated operations and batches that add no evidence stop the loop. Superseding event revisions are checked between calls and before completion. Provider/validation failures are recorded without authorizing downstream trading.

## Persistence, recovery and replay

`research_sessions` stores initial/final package IDs, model/prompt version, deadline, total attempted calls, cost and outcome. `research_steps` is an immutable, hashed journal of planner inputs/responses, validated requests, reservations, retrieved evidence, validation failures and terminal reasons. `research_session_progress` stores a hash-checked recovery checkpoint; `research_daily_allowances` stores shared reservations. These are additive migrations.

A session has a deterministic identity per discovery queue item. A single-host OS file lock prevents overlapping workers and is released when a process exits, including a crash. Lock files are retained to avoid inode races, and ignored by Git. Deploy workers for a SQLite database on one host; distributed locking is outside this implementation.

Completed model responses and retrieval results are reused after interruption, including a response saved immediately before request validation. If a process dies after reserving a call but before recording its result, resume records `failed` with `interrupted_call_outcome_unknown_no_automatic_retry`. It does not blindly repeat a possibly completed external call. The original deadline and budgets persist across restarts. Terminal sessions are idempotent; a new evidence revision gets a new session.

```bash
python3 scripts/research.py --db /path/to/market.db replay 'research:SESSION_HASH'
```

Replay reads only saved input, journal and evidence snapshots. It does not call models, providers, current-evidence lookups or brokers. Source files/configuration are included in the pipeline foundation manifest. No live model, provider or broker calls were used during acceptance testing.

## Validation

`tests/test_research_orchestrator.py` covers retrieval for missing guidance, company/period/source validation, unavailable consensus, contradictory evidence, call/deadline limits, daily reservations, repeated/no-progress requests, supersession, interrupted planner/results recovery, uncertain in-flight work, worker exclusion, journal immutability, offline replay and shadow integration. Phase 1 and Phase 2 tests provide ingestion, provenance and execution-boundary regression coverage.

`tests/test_research_vertex.py` covers provider inheritance, ADC/project resolution, SDK request controls, retry/deadline limits, paid reservations, thinking-token usage, unknown costs, overage handling and interrupted retries. Its optional real-SDK tests use an in-memory HTTP transport and fixture credentials; they make no network or paid model calls.
