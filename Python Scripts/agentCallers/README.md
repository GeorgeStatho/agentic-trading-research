# agentCallers

Grouped layout:

- `agent_stages/`: model-facing stage implementations
- `agent_helpers/`: helper and persistence logic
- `agent_builders/`: payload and selection builders
- `agent_analysis/`: score/ranking utilities
- `agent_pipeline/`: orchestration entry points
- `agent_runtime/`: local experiments and runtime scripts

The legacy top-level filenames are kept as compatibility wrappers so older imports and direct script execution still work.
