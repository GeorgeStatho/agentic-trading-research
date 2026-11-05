# Argon chat review: index

Source: `docs/archive/argonchat.md`, a Gemini chat proposing improvements to the options strategy.
Gemini's code is ignored here; only the ideas are rated. Gemini worked from a short description of
the system, and the code is well ahead of that description, so many of its "fixes" are already
present, or target bugs that do not exist.

Paths below are relative to `Python Scripts/` unless they start with `Data/` or `docs/`.
Line numbers were checked on 2026-10-29.

## Rating scale

- **Sig**: how much the idea would move consistency or PnL (1–5).
- **Qual**: how sound the idea is as stated (1–5).
- **Status**: ✅ done · 🟡 partial or shadow-only · ❌ not present.

## Ratings

| # | Idea | Sig | Qual | Status | Evidence | Problems with the idea | Plan |
|---|---|---|---|---|---|---|---|
| 1 | Earnings / binary-event gate | 3 | 4 | 🟡 shadow exit rung | `PRE_EARNINGS` rung (`services/options/position_exit_policy.py`) reads Yahoo dates via `next_earnings_date` (`Data/market_db_modules/company_evidence.py`) | Real holds are 1–6 days, so few trades reach a print. Built as cheap exit insurance; no entry gate | [01](01_earnings_holding_window.md) |
| 2 | Event clusters + price drift since first print | 5 | 4 | ✅ shadow | Clusters, roots, syndication (`Data/news_db_modules/provenance.py:170`). The `PRICE_ABSORPTION` gate measures the beta-adjusted residual move from the last close before `first_public_time`, normalized by the historical move or ATR (`services/research/price_response.py:86,157`). The Manager is told to use it (`agent_stages/manager_prompt.py:98`) | Already built, and better than Gemini's version: residual move rather than raw, with the overnight gap included. Gemini's TF-IDF single-linkage clustering would over-merge. Promotion is blocked by coverage, see below | — |
| 3 | Delta / ATR-scaled strikes | 4 | 4 | 🟡 | Hybrid mode has a delta floor of 0.25 and a target of 0.38 (`agentCallers/agent_helpers/deterministic_option_selector/config.py:25`), but ranks by fixed-% OTM distance first; delta is only a tiebreak (`.../filters.py:166`) | Gemini's KO/NVDA delta numbers are exaggerated. Greeks can be missing. Changing the strike changes premium, the hurdle and stop behaviour, so it needs an A/B | [03](03_delta_first_strike_ranking.md) |
| 4 | Bottom-up idiosyncratic bypass | 4 | 3 | 🟡 | `services/discovery/event_discovery.py` (primary-disclosure queue, Research Phase 2, shadow, off) | An RVOL>2 trigger enters after the move, which fights the overextension filters. It adds LLM calls against the requests/day limit | — |
| 5 | Portfolio net-direction cap | 3 | 4 | ❌ | Only cluster limits exist (`services/gates/risk_clusters.py`, `services/trading/trade_executor.py:47-57`) | Overlaps the regime gate. Blocks good trades in a strong trend | [04](04_net_direction_cap.md) |
| 6 | Calibrate in Python, not prompt text | 3 | 4 | 🟡 | The Manager receives up to 3 resolved autopsies plus recent decision history (`services/core/settings.py:244-249`, `agent_stages/manager_prompt.py:39`) | The concern is valid. Gemini's alternative ("Bayesian weights in Python") overfits just as badly on a few dozen trades. Measure first | [07](07_prompt_feedback_ablations.md) |
| 7 | Binary rubric instead of confidence | 3 | 3 | 🟡 | Confidence is already ordinal and split into thesis, timing and execution (`agent_stages/manager_prompt.py:94-96`). Deterministic claim checks exist (`services/thesis/evidence_thesis_checks.py`), and provenance computes primary source and root counts in Python | Asks the LLM things Python already knows. Hand-picked weights (0.20/0.15) are as uncalibrated as an LLM confidence. The only real gain is orthogonal booleans for autopsy group-bys | — |
| 8 | Promote the market-regime gate | 3 | 3 | 🟡 by design | `services/gates/market_regime_gate.py:22`, wrapped in `guard_policy` | Shadow mode is intended until a promotion proof exists. "60–75% of the move is market beta" is overstated for single-name catalyst stocks | — |
| 9 | Reasoning-first schema ordering | 2 | 4 | ❌ | Strategist schema lists `decision` and `confidence` first (`agent_stages/strategist_schema.py:101`) | Nearly free. Verify the provider honours key order | [05](05_reasoning_first_schema.md) |
| 10 | Blind the Manager to Strategist prose | 2 | 2 | ❌ by design | The Manager sees the structured thesis plus deterministic checks (`agent_stages/manager_strategist_context.py`) | The Manager's job is to judge whether the thesis is tradable now. Blinded, it is a checklist Python could run without a Pro model. Gemini's five ANDed booleans would force `neither` almost always. Worth one replay at most | [07](07_prompt_feedback_ablations.md) |
| 11 | Manager direction lock | — | 3 | ✅ not needed | Measured from stored payloads: 0 flips in 3,367 decisions (1,070 directional) | Nothing to lock | [06](06_manager_direction_disagreement.md) |
| 12 | Friday rule by DTE bucket | 2 | 3 | ❌ | Blanket block (`services/runtime/worker_application.py:113`) | The Thursday point is valid. The "weekend theta" argument is shaky, since the real Friday risk is a weekend gap. Low impact | — |
| 13 | Market-hours catalyst age | 2 | 4 | ❌ | Freshness decay uses `elapsed_calendar_minutes` (`Data/catalyst_models.py:27`, `services/gates/catalyst_gates.py:53-58`) | Small and correct: Friday-evening news looks about 65h stale on Monday | [02](02_market_hours_catalyst_age.md) |
| 14 | Fixed-dollar risk sizing | 2 | 2 | 🟡 | `MAX_PREMIUM_PER_TRADE_DOLLARS=800` caps the worst case (`services/core/trading_config.py:185`) | At qty 1 the real risk is the premium (gaps happen, and stops need consecutive breaches). Sizing up pushes cheap stocks into thin chains. "Skip if >1.5R" would reject most large caps | — |
| 15 | Escalating exit limit ladder | 1 | 3 | ✅ mostly | Exits start at mid and cross to the bid on a retry of the same reason. Stop-loss and near-expiration exits cross immediately (`trading_support/_option_positions_strategy.py:75`, `trading_support/_option_positions_snapshot.py:273`) | The "left unhedged after cancel" scenario doesn't happen, because the next cycle re-prices more aggressively | — |
| 16 | Slippage-adjusted PnL | — | 4 | ✅ | Half-spread charged on both legs (`services/gates/trade_economics.py:243`). Measured `slippage_vs_mid` and `spread_paid_pct` (`services/runtime/execution_telemetry.py:60`) | Its premise ("mid fills on entry") is wrong: live entries are market orders | — |
| 17 | Option hurdle vs historical move | — | 3 | ✅ better | Straddle-implied move vs the historical residual catalyst move (`services/gates/option_hurdle_gate.py:38`) | The existing version is more rigorous than Gemini's | — |
| 18 | Citation validation in Python | — | 4 | ✅ | `agentCallers/agent_stages/strategist_grounding.py` | — | — |
| 19 | qty=1 vs partial-sell "conflict" | — | — | ✅ no bug | `_compute_scaled_sell_qty` returns 0 for qty ≤ 1 and logs a note (`trading_support/_option_positions_strategy.py:66`) | Gemini guessed a bug that isn't there | — |
| 20 | Longer DTE when IV is high | 1 | 1 | ❌ | — | **Wrong.** A longer DTE carries more vega, so it increases IV-crush exposure. Do not adopt | — |

## Plans

| Plan | Priority | Cost to build | Paid runs? |
|---|---|---|---|
| [01 Earnings in the holding window](01_earnings_holding_window.md) | Built: shadow `PRE_EARNINGS` exit rung | Small | No |
| [02 Market-hours catalyst age](02_market_hours_catalyst_age.md) | 4 | Small | No |
| [03 Delta-first strike ranking](03_delta_first_strike_ranking.md) | Parked: deltas already even; delta-first only lowers Δ | — | No |
| [04 Net-direction cap](04_net_direction_cap.md) | 3 | Small | No |
| [05 Reasoning-first strategist schema](05_reasoning_first_schema.md) | 5 | Small | One dry run, needs approval |
| [06 Manager direction disagreement](06_manager_direction_disagreement.md) | Closed: 0 flips measured | — | No |
| [07 Prompt-feedback ablations](07_prompt_feedback_ablations.md) | 7 | Medium | **Yes**, LLM replays need approval |

## Not planned, and why

- **#2 drift since first print**: already built as `PRICE_ABSORPTION`, but it can't be promoted yet (checked 2026-11-04):
  - **Coverage.** Of 594 Manager-reaching candidates since 2026-10-02, only 25% had a usable verdict. 34% were measured
    but the catalyst had no direction. 36% were `missing_aligned_prices`, which is news under about 24h old with no
    stored price after the event yet (hourly bars are only stored in a daily batch). The `missing_regression_history`
    cases before 2026-10-02 were a one-time gap: daily stock and sector history was first backfilled on 2026-10-01.
  - **Fixed:** a shadow `PRICE_ABSORPTION_DIRECTED` verdict signs undirected catalysts with the Manager's call/put
    (`directed_absorption`, `record_directed_absorption`, called in `_apply_post_manager_gates`). The forward collector now
    stores the raw 5-day residual for undirected candidates too.
  - **Still blocked:** `PricingPolicy` refuses any mode but shadow, and there's no `GATE_BLOCKS` entry. No
    `frozen_forward` run has sealed yet (60-day window). A proof also goes stale whenever trading-path code changes.
  - **Fixed:** fresh news (event within 4 days, verdict `missing_aligned_prices`) now fetches hourly bars for the stock,
    SPY and the sector when the gate runs (`refresh_intraday_history` in `services/gates/pricing_gates.py`), then
    re-evaluates. Only bars completed by the candidate's creation are cached, and each symbol is fetched at most once
    per half hour per process. News that broke less than an hour before the candidate is still unmeasurable, because the
    first hourly bar after it hasn't finished.
- **#4 bottom-up bypass**: Research Phase 2 discovery already covers it; revisit once that phase is on.
- **#7 binary rubric**: ordinal confidence plus deterministic checks already capture most of the value.
- **#8 regime promotion**: already on the promotion path. Shadow mode is intentional.
- **#12 Friday rule**: low impact.
- **#14 dollar-risk sizing**: defer until multi-contract trading is wanted.
- **#15–#19**: already done.
- **#20**: wrong.

## Conventions every plan follows

Each new gate follows the "Entry Gate" section of `docs/FEATURE_PLAYBOOK.md`:

- It returns a verdict dict with `allowed`, `would_allow`, `reason_codes`, `policy_version` and `enforcement_mode`.
- It is one `EntryGate` row in `services/gates/entry_gates.py`.
- It is recorded with `record_gate` (`services/runtime/candidate_lifecycle.py:63`).
- Its policy block goes in `gate_config` (`services/core/pipeline_version.py:43`) and `GATE_BLOCKS` (`services/research/research_promotion.py:16`).
- It defaults to shadow mode, with enforcement behind `guard_policy` and a promotion proof.
