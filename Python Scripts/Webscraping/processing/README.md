# Processing Folder Guide

This folder contains the text-normalization and deterministic scoring logic used by the pipelines.

If `core/` provides pipeline-level shared helpers, `processing/` provides lower-level content processing utilities.

## What This Folder Does

The code in this folder is responsible for:

- URL normalization
- title normalization
- content hashing
- deterministic scoring functions used for saved articles

## Files

### [news_normalization.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/processing/news_normalization.py)

This file normalizes the pieces used for deduplication and storage consistency.

Important helpers:

- `normalize_title(title)`
  - lowercases and compresses whitespace

- `normalize_url(url)`
  - normalizes scheme and host
  - removes duplicate slashes
  - strips tracking parameters like `utm_*`

- `build_content_hash(text)`
  - normalizes whitespace
  - computes a SHA-256 hash

These helpers are used when checking whether an article is already known and when storing normalized metadata in `raw_json`.

### [scoring.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/processing/scoring.py)

This file contains the deterministic evidence-scoring formulas.

Important helpers:

- `recency_score(age_days)`
- `confirmation_score(independent_sources)`
- `compute_directness_score(...)`
- `compute_factuality_score(...)`
- `compute_evidence_score(...)`

How the scoring works:

- recency uses exponential decay
- confirmation increases as more independent sources confirm the story
- directness looks for signals like filings, press releases, and earnings calls
- factuality rewards report-like language and penalizes speculative language
- evidence score combines the other dimensions into one bounded value

This is rules-based scoring, not model-based scoring.

### [__init__.py](/mnt/e/ComputerScience/SmallProjects/StockExperiment-AgenticVersion/Stock-trading-experiment/Python%20Scripts/Webscraping/processing/__init__.py)

Package marker for the folder.

## How The Folder Fits Into The System

Typical use:

1. an article is extracted
2. its title and URL are normalized
3. a content hash is generated
4. the scoring helpers produce recency/directness/factuality/evidence values
5. the pipeline stores those values with the saved article

## Mental Model

`processing/` is where raw extracted content becomes more stable and comparable.

It is about normalization and scoring, not crawling.
