# Interface Guide

This file defines the preferred import surface for the repository.

Use these modules for new code. Older top-level wrapper files remain in place for
backward compatibility, but they should be treated as compatibility-only entrypoints.

## General Rule

- Prefer the canonical package/module listed below.
- Avoid importing from legacy wrapper modules in new code.
- Compatibility wrappers may stay in place for old scripts, local tooling, or
  older branches, but should not accumulate new business logic.

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

### Legacy compatibility wrappers

These still work, but new code should not import from them:

- `CompanyOppurtunist.py` -> `agent_stages.company_opportunist`
- `CompanyOppurtunityBuilder.py` -> `agent_builders.company_opportunity`
- `IndustryOppuruntinst.py` -> `agent_stages.industry_opportunist`
- `MacroNewsToSectors.py` -> `agent_stages.macro_news_to_sectors`
- `OppurtunistPayloadBuilder.py` -> `agent_builders.opportunist_payload`
- `SectorOppurtunist.py` -> `agent_stages.sector_opportunist`
- `Strategist.py` -> `agent_stages.strategist`
- `StrategistPayloadBuilder.py` -> `agent_builders.strategist_payload`
- `WorldNewsToSectors.py` -> `agent_stages.world_news_to_sectors`
- `agentMain.py` -> `agent_pipeline.main`
- `industryIntrest.py` -> `agent_analysis.industry_interest`
- `sectorIntrest.py` -> `agent_analysis.sector_interest`
- `initagents.py` -> `agent_runtime.init_agents`
- `marketHealth.py` -> `agent_runtime.market_health`
- `researcher.py` -> `agent_runtime.researcher`
- `_shared.py` -> `agent_helpers.shared`
- `_macro_news_helpers.py` -> `agent_helpers.macro_news`
- `_opportunist_payload_helpers.py` -> `agent_helpers.opportunist_payload`
- `_sector_opportunist_helpers.py` -> `agent_helpers.sector_opportunist`
- `_industry_opportunist_helpers.py` -> `agent_helpers.industry_opportunist`
- `_company_opportunist_helpers.py` -> `agent_helpers.company_opportunist`
- `_strategist_helpers.py` -> `agent_helpers.strategist`

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

### Legacy compatibility wrappers

These remain for older imports, but new code should target `pipelines/`,
`engine/`, `processing/`, `extractors/`, or `sources/` directly.

- `companyNewsPipeline.py` -> `pipelines.companyNewsPipeline`
- `industryNewsPipeline.py` -> `pipelines.industryNewsPipeline`
- `sectorNewsPipeline.py` -> `pipelines.sectorNewsPipeline`
- `macroNewsPipeline.py` -> `pipelines.macroNewsPipeline`
- `USNewsPipeline.py` -> `pipelines.USNewsPipeline`
- `worldNewsPipeline.py` -> `pipelines.worldNewsPipeline`
- `article_scraper.py` -> `engine.article_scraper`
- `article_extraction.py` -> `engine.article_extraction`
- `Normalization.py` -> `engine.Normalization`
- `news_normalization.py` -> `processing.news_normalization`
- `scoring.py` -> `processing.scoring`

## Naming Policy For New Code

- Use correctly spelled names in all new files, imports, docs, and env vars.
- Do not introduce new module names using `Oppurt`, `Oppur`, or `Intrest`.
- Keep existing misspelled wrappers only as compatibility shims unless and until
  their callers are migrated.
