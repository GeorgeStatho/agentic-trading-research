# FinanceHarness follow-ups

Status: proposed (2026-10-27). None of these plans is implemented yet.

These four plans adapt ideas from *FinanceHarness: Autonomous Financial Deep Research
Framework* (Xiao et al., Google, [arXiv 2607.27853](https://arxiv.org/abs/2607.27853)). Each
plan ships as its own change, in shadow mode or behind a flag, under the existing guard_policy
and promotion rules in [RESEARCH_GUIDE.md](../RESEARCH_GUIDE.md).

| Plan | What it adds | Depends on |
| --- | --- | --- |
| [A. Catalyst classifier](PLAN_A_CATALYST_CLASSIFIER.md) | A cheap model labels catalysts the headline rules leave in `needs_review` (Known issue #5) | — |
| [B. PIT replay benchmark](PLAN_B_PIT_REPLAY_BENCHMARK.md) | Fixed, balanced replay sets with a dev/holdout split, a model knowledge-cutoff guard and paired comparisons | C; A for catalyst-family strata |
| [C. Decision attribution](PLAN_C_DECISION_ATTRIBUTION.md) | Splits each decision into evidence, direction and expression outcomes in a report and the Decision Autopsy | — |
| [D. Archive search](PLAN_D_ARCHIVE_SEARCH.md) | A PIT-filtered full-text research operation (`search_archive`, SQLite FTS5) for Phase 3 | — |

Recommended order: A → C → B. D is independent and can land at any point.

## The paper in brief

**FinanceGym** is a benchmark of 400 analyst-style questions. Each question has a cutoff date,
and expert-validated rubrics total 2,464 items. Agents search a 100M-article archive through a
point-in-time sandbox (dense embeddings + FAISS) that hides anything published after the cutoff.
Each rubric is split in two:
- **Pre-cutoff items** ask whether the agent found and used the evidence.
- **Post-cutoff items** ask whether its forecast matched what happened.

An LLM judge scores each item from 0 to 4.

**FinanceHarness** is the agent runtime evaluated on the benchmark. It has:
- a bounded agent loop with run budgets;
- tiered tools (core, a deferred catalog, MCP extensions);
- research and analytical modes, and reusable workflow "skills";
- numbered citations and a grounding-review pass;
- recovery policies;
- a cheap reader model for long documents.

Results:
- Every system scores about 46–50% on pre-cutoff items but only 10–13% on post-cutoff items.
  Finding evidence works; forecasting is the bottleneck.
- On a fixed backbone, the harness adds 7.1 points (25.3 → 32.4), while GRPO fine-tuning adds
  0.4.

## How this project compares

| FinanceHarness | This project |
| --- | --- |
| PIT cutoff on every search | Every research operation carries `as_of`. Evidence records publication, first-observed and ingestion times. Price responses must precede the cutoff. |
| Bounded loop and budgets | Phase 3 caps rounds, tool calls and seconds, and reserves daily calls and cost |
| Validated tool calls | Unknown arguments, URLs, company/event changes and cutoff changes are rejected |
| LLM grounding review | Phase 4 checks claims deterministically: quote spans, number reconciliation and reference ownership. This is stricter. |
| Scored by an LLM judge against rubrics | Scored by realized outcomes, forward cohorts and pre-registered promotion. More honest, but slower. |
| Dense semantic search over 100M documents | Six typed operations over the project's own archive and no free text (Plan D) |
| Separate retrieval and forecast scores | Only option P&L sign (Plan C) |
| A fixed, balanced benchmark set | Ad hoc replay windows, and promotion only through 60-day cohorts (Plan B) |
| Neither guards against model memory | Frozen inputs are PIT-correct, but a model trained past a decision's date can know the outcome (Plan B) |
| A reader model for extraction | Headline regex leaves most catalysts unclassified (Plan A) |

The paper also supports the project's choice to invest in the harness rather than
fine-tuning.
