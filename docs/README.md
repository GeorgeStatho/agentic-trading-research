# Documentation index

Start here. **Current** documents describe the system as it is and are kept up to date.
**Archive** documents are dated snapshots, finished plans and scratch notes. They record
history and are not updated when the code changes.

## Current

### Orientation and conventions

| Document | Use it for |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Where new code belongs; runtime and safety boundaries |
| [FEATURE_PLAYBOOK.md](FEATURE_PLAYBOOK.md) | Checklists for persisted, LLM-assisted, dashboard and demo features |
| [CODE_MAP.md](CODE_MAP.md) | Map of the repository and how the subsystems connect |
| [INTERFACE_GUIDE.md](INTERFACE_GUIDE.md) | Preferred import surface versus compatibility wrappers |
| [CleanCode.md](CleanCode.md) | Code style rules used in reviews |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Installing the project, running tests and entrypoints |

### Research and decision pipeline (in progress)

| Document | Status |
| --- | --- |
| [RESEARCH_AND_DECISION_IMPLEMENTATION_PLAN.md](RESEARCH_AND_DECISION_IMPLEMENTATION_PLAN.md) | The plan: Phases 0–6 implemented, 7 proposed |
| [RESEARCH_PHASE0.md](RESEARCH_PHASE0.md) | Evidence/observation contracts and comparison identity |
| [RESEARCH_PHASE1.md](RESEARCH_PHASE1.md) | Earnings and guidance evidence ingestion |
| [COMPANY_EVIDENCE_PROVIDERS.md](COMPANY_EVIDENCE_PROVIDERS.md) | Provider capability matrix |
| [RESEARCH_PHASE2.md](RESEARCH_PHASE2.md) | Event discovery and the durable review queue |
| [RESEARCH_PHASE3.md](RESEARCH_PHASE3.md) | Bounded investigative research |
| [RESEARCH_PHASE4.md](RESEARCH_PHASE4.md) | Structured theses, claim checks and the thesis gate |
| [RESEARCH_PHASE5.md](RESEARCH_PHASE5.md) | Trade-economics reports, finalization and submission rechecks |
| [RESEARCH_PHASE6.md](RESEARCH_PHASE6.md) | Event-triggered thesis reviews of open positions (shadow) |
| [RESEARCH_PHASE7.md](RESEARCH_PHASE7.md) | Forward cohorts, challenger comparisons and pre-registered promotion |

### V2 trading architecture (implemented)

| Document | Contents |
| --- | --- |
| [v2/TRADINGBOT_V2.md](v2/TRADINGBOT_V2.md) | Target architecture and design rationale |
| [v2/TRADINGBOT_V2_IMPLEMENTATION.md](v2/TRADINGBOT_V2_IMPLEMENTATION.md) | Implementation plan and status board |
| `v2/TRADINGBOT_V2_PART1_CHANGES.md` … [`PART6`](v2/TRADINGBOT_V2_PART6_CHANGES.md) | What each part implemented, configuration and validation |
| [v2/TRADINGBOT_V2_DEFECT_FIXES.md](v2/TRADINGBOT_V2_DEFECT_FIXES.md) | Post-audit correctness fixes |

### Operating guides

| Document | Contents |
| --- | --- |
| [guides/OPTION_TRAILING_PROFIT_AND_MOMENTUM_GUIDE.md](guides/OPTION_TRAILING_PROFIT_AND_MOMENTUM_GUIDE.md) | How the option exit engine manages open positions |
| [guides/deterministic_option_selector_report.md](guides/deterministic_option_selector_report.md) | How the deterministic contract selector works |

The repository root keeps [README.md](../README.md) (overview and setup),
[SECURITY.md](../SECURITY.md), [PRIVACY.md](../PRIVACY.md) and the licence.

## Archive

Kept for history. Where one conflicts with a current document, the current document wins.

| Document | Why archived |
| --- | --- |
| [archive/AGENT_HANDOFF.md](archive/AGENT_HANDOFF.md) | Snapshot of 2026-09-20 (after V2 Part 6), before research Phases 0–4 |
| [archive/AgenticStockExperiemnt.md](archive/AgenticStockExperiemnt.md) | April architecture description; superseded by ARCHITECTURE.md and CODE_MAP.md |
| [archive/REFACTOR_ROADMAP.md](archive/REFACTOR_ROADMAP.md) | May refactor plan; superseded by the current package layout and conventions |
| [archive/OPTION_TRAILING_PROFIT_PROGRESS.md](archive/OPTION_TRAILING_PROFIT_PROGRESS.md) | Progress tracker for work now described in the exit guide |
| [archive/DECISION_EXPECTANCY_IMPLEMENTATION_PLAN.md](archive/DECISION_EXPECTANCY_IMPLEMENTATION_PLAN.md) | Completed implementation plan |
| [archive/MANAGER_DECISION_HISTORY_IMPLEMENTATION_CHECKLIST.md](archive/MANAGER_DECISION_HISTORY_IMPLEMENTATION_CHECKLIST.md) | Completed implementation checklist |
| [archive/LLMThesisValidator.md](archive/LLMThesisValidator.md) | Design notes for the position thesis validator, now implemented |
| [archive/ORDER_LIFECYCLE_AUDIT.md](archive/ORDER_LIFECYCLE_AUDIT.md) | Dated audit (2026-07-27) |
| [archive/HACKATHON_SUBMISSION.md](archive/HACKATHON_SUBMISSION.md) | Hackathon write-up |
| `archive/scratch/` | Pasted logs and UI/design dumps (`Error.md`, `backtest.md`, `design.md`) |
