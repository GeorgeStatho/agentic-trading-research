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

### Research and decision pipeline

| Document | Status |
| --- | --- |
| [RESEARCH_GUIDE.md](RESEARCH_GUIDE.md) | Operator guide: what runs where, turning phases on, watching them, known issues |
| [RESEARCH_LIVE_SETUP.md](RESEARCH_LIVE_SETUP.md) | Checklist for enabling the research features on the live server |
| [COMPANY_EVIDENCE_PROVIDERS.md](COMPANY_EVIDENCE_PROVIDERS.md) | Provider capability matrix |

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
| [archive/DECISION_EXPECTANCY_IMPLEMENTATION_PLAN.md](archive/DECISION_EXPECTANCY_IMPLEMENTATION_PLAN.md) | Completed implementation plan |
| [archive/MANAGER_DECISION_HISTORY_IMPLEMENTATION_CHECKLIST.md](archive/MANAGER_DECISION_HISTORY_IMPLEMENTATION_CHECKLIST.md) | Completed implementation checklist |
| [archive/LLMThesisValidator.md](archive/LLMThesisValidator.md) | Design notes for the position thesis validator, now implemented |
| [archive/ORDER_LIFECYCLE_AUDIT.md](archive/ORDER_LIFECYCLE_AUDIT.md) | Dated audit (2026-07-27) |
| `archive/scratch/` | Pasted logs and UI/design dumps (`Error.md`, `backtest.md`, `design.md`) |
