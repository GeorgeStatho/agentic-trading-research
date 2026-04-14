# agentCallers

This folder contains the current agent-driven research and decision pipeline.

Grouped layout:

- `agent_stages/`: model-facing stage implementations
- `agent_helpers/`: helper and persistence logic
- `agent_builders/`: payload and selection builders
- `agent_analysis/`: score/ranking utilities
- `agent_pipeline/`: orchestration entry points
- `agent_runtime/`: local experiments and runtime scripts

Main entrypoint:

- [main.py](main.py): runs the pipeline, strategist stage, manager stage, and deterministic option selection

Notes:

- the project is in the middle of an Ollama -> Vertex migration
- the shared provider abstraction already lives in `agent_helpers/shared.py`
- some older wrappers and filenames still exist for compatibility
