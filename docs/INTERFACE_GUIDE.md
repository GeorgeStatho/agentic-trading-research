# Interface Guide

This file defines the preferred import surface for the repository.

Use these modules for new code. The top-level compatibility wrappers that used to
re-export them were removed; import the canonical modules directly.

## General Rule

- Import the canonical package/module listed below.
- Do not add re-export wrappers; move callers to the canonical module instead.

## Bootstrap Policy

- Import roots are declared once, in `pyproject.toml`, and registered by
  `pip install -e .`. No module edits `sys.path`.
- Only entrypoints load `.env` and settings, by calling
  `services.core.settings.load_process_settings()` under `if __name__ == "__main__":`.
  Importing a module never loads `.env` or reads configuration; library code calls
  `services.core.settings.get_settings()` when it runs.
- Shared env parsing should live in reusable helpers or settings objects such as
  `services/core/env_parsing.py` and `services/core/trading_config.py`.

## Agent Research System

### Canonical modules

- pipeline orchestration:
  `from agent_pipeline.main import ...`
- stage implementations:
  `from agent_stages.<stage_name> import ...`
- helper and persistence logic:
  `from agent_helpers.<helper_name> import ...`
- payload builders:
  `from agent_builders.<builder_name> import ...`
- ranking helpers:
  `from agent_analysis.<analysis_name> import ...`
- runtime experiments:
  `from agent_runtime.<module_name> import ...`

## Webscraping System

### Canonical modules

- pipeline entrypoints:
  `from pipelines.<pipeline_module> import ...`
- crawl runtime:
  `from engine.<module_name> import ...`
- processing helpers:
  `from processing.<module_name> import ...`
- source-specific extractors:
  `from extractors.<source_name> import ...`
- source configuration:
  `from sources.<module_name> import ...`
- market data and source APIs:
  `from market_data.<module_name> import ...`, `from APIs.<module_name> import ...`

## Naming Policy For New Code

- Use correctly spelled names in all new files, imports, docs, and env vars.
- Do not introduce new module names using `Oppurt`, `Oppur`, or `Intrest`.
