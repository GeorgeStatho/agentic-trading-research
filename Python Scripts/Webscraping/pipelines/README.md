# Pipelines Folder Guide

This folder contains the orchestration layer for the public demo scraper.

## Responsibilities

- build entity-specific crawl jobs
- route source pages and article pages through the shared engine
- persist normalized articles for companies, industries, sectors, and macro feeds
- keep the planning layer separate from the crawl runtime

## Public Modules

- `companyNewsPipeline.py`: company-oriented orchestration
- `industryNewsPipeline.py`: industry-oriented orchestration
- `sectorNewsPipeline.py`: RSS-first sector demo flow
- `USNewsPipeline.py` and `worldNewsPipeline.py`: fixed demo feed flows
- `macroNewsPipeline.py`: generic macro-calendar ingestion
- `job_builder.py`: shared job construction helpers
- `_shared.py`, `_orchestration.py`, `_entity_adapters.py`: shared orchestration helpers

## Public Design

The public repo keeps the orchestration model visible but routes it through demo-safe sources and generic extraction. Real production feed mapping, source prioritization, and filtering logic live only in the private repo.
