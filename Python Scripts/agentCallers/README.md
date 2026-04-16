# agentCallers

This folder contains the current agent-driven research and decision pipeline.

Grouped layout:

- `agent_stages/`: model-facing stage implementations
- `agent_helpers/`: helper and persistence logic
- `agent_builders/`: payload and selection builders
- `agent_analysis/`: score/ranking utilities
- `agent_pipeline/`: orchestration entry points
- `agent_runtime/`: local experiments and runtime scripts

Recent refactor direction:

- shared runtime bootstrapping now lives in `_paths.py`
- manager payload orchestration stays in `agent_helpers/manager.py`
- Alpaca market/account collection is isolated in `agent_helpers/market_context.py`
- shared opportunist article handling is isolated in `agent_helpers/opportunist_support.py`

Main entrypoint:

- [main.py](main.py): runs the pipeline, strategist stage, manager stage, and deterministic option selection

Common usage:

- build a manager payload: `from agent_helpers.manager import build_manager_input`
- inspect live market/account data only: `from agent_helpers.manager import test_market_context`
- deterministically pick an option after manager output: `from agent_helpers.deterministic_option_selector import apply_deterministic_option_selection`

Notes:

- the project is in the middle of an Ollama -> Vertex migration
- the shared provider abstraction already lives in `agent_helpers/shared.py`
- some older wrappers and filenames still exist for compatibility
