# agent_stages

This folder contains the model-facing stages in the pipeline. These modules build prompts, call Ollama, normalize model output, and return stage results in the shapes the rest of the project expects.

## Files

### `macro_news_to_sectors.py`

Purpose: map unprocessed world or US macro news articles to affected sectors.

Functions:
- `_configure_console_logging()`: configures logging for direct script runs.
- `ask_model(client, model, system_prompt, user_prompt)`: sends the macro-to-sector prompt to Ollama.
- `build_macro_news_to_sectors_prompt(articles, sectors, news_scope)`: builds the system/user prompt pair for macro-to-sector classification.
- `_extract_pairs(payload)`: extracts the model’s sector-impact pair list from a parsed JSON response.
- `_build_valid_reference_sets(sectors, articles)`: builds trusted article and sector reference sets for normalization.
- `_classify_article_batch(...)`: runs one macro-news batch through the model and extracts raw pairs.
- `_normalize_pair(...)`: validates and normalizes one model-produced sector impact pair.
- `_collect_cleaned_pairs(...)`: processes batches, normalizes valid pairs, and persists batch results.
- `classify_macro_news_to_sectors(...)`: main entry point for classifying macro articles for a chosen scope.
- `classify_world_news_to_sectors(**kwargs)`: convenience wrapper for `news_scope="world"`.
- `classify_us_news_to_sectors(**kwargs)`: convenience wrapper for `news_scope="us"`.

### `world_news_to_sectors.py`

Purpose: thin world-news-specific wrapper around the generic macro-news-to-sector stage.

Functions:
- `ask_model(client, model, system_prompt, user_prompt)`: forwards the prompt call to the shared macro stage implementation.
- `get_recent_world_news_articles(max_age_days=...)`: loads recent unprocessed world-news articles.
- `build_world_news_to_sectors_prompt(articles, sectors)`: builds a world-news-specific macro prompt.
- `classify_world_news_to_sectors(...)`: runs the world-news-to-sector classification flow.

### `sector_opportunist.py`

Purpose: classify sector-linked articles one at a time into saved sector opportunist impacts.

Functions:
- `ask_model(client, model, system_prompt, user_prompt)`: sends the sector opportunist prompt to Ollama.
- `build_sector_opportunist_prompt(sector, articles, ...)`: builds the system/user prompt pair for sector opportunist classification.
- `_classify_article_batch(...)`: runs one article batch and extracts raw impacts.
- `_collect_cleaned_impacts(...)`: loops article-by-article, normalizes valid impacts, and persists results.
- `classify_sector_articles(sector_identifier, ...)`: main entry point for sector opportunist processing.
- `classify_sector_articles_to_companies(sector_identifier, **kwargs)`: compatibility alias that currently forwards to `classify_sector_articles`.

### `industry_opportunist.py`

Purpose: classify sector-linked articles one at a time into affected industries inside one sector.

Functions:
- `ask_model(client, model, system_prompt, user_prompt)`: sends the industry opportunist prompt to Ollama.
- `build_industry_opportunist_prompt(sector, industries, articles, ...)`: builds the system/user prompt pair for industry opportunist classification.
- `_classify_article_batch(...)`: runs one article batch and extracts raw impacts.
- `_collect_cleaned_impacts(...)`: loops article-by-article, normalizes valid impacts, and persists results.
- `classify_sector_articles_to_industries(sector_identifier, ...)`: main entry point for industry opportunist processing.

### `company_opportunist.py`

Purpose: classify company-linked articles one at a time into company-specific impacts for one target company.

Functions:
- `ask_model(client, model, system_prompt, user_prompt)`: sends the company opportunist prompt to Ollama.
- `build_company_opportunist_prompt(company, peer_groups, articles, ...)`: builds the system/user prompt pair for company opportunist classification.
- `_classify_article_batch(...)`: runs one article batch and extracts raw impacts.
- `_collect_cleaned_impacts(...)`: loops article-by-article, normalizes valid impacts, and persists results.
- `classify_company_articles(company_identifier, ...)`: main entry point for company opportunist processing.

### `strategist.py`

Purpose: use the structured strategist payload to decide whether one company is currently a buy or not a buy.

Functions:
- `ask_model(client, model, system_prompt, user_prompt)`: sends the strategist prompt to Ollama.
- `_payload_has_evidence(payload)`: checks whether the strategist has enough saved evidence to attempt a recommendation.
- `_build_context_snapshot(payload)`: builds a compact count-only snapshot of the strategist context.
- `build_strategist_prompt(payload, ...)`: builds the system/user prompt pair for the strategist model.
- `_extract_recommendation(payload)`: extracts a recommendation object from parsed JSON output.
- `_normalize_string_list(value)`: normalizes model-produced thesis/risk list values into string lists.
- `_normalize_decision(value)`: normalizes decision variants like `hold` or `do not buy`.
- `_normalize_confidence(value)`: normalizes confidence variants into `high`, `medium`, or `low`.
- `_normalize_recommendation(recommendation)`: validates and normalizes the final recommendation object.
- `_extract_labeled_section(text, label, next_labels)`: extracts a labeled free-text section from a fallback text response.
- `_clean_bullet_lines(text)`: cleans a text block into bullet-like list items.
- `_extract_recommendation_from_text(raw_response)`: fallback parser for non-JSON strategist responses.
- `_build_no_evidence_result(company, ...)`: returns the default low-confidence `do_not_buy` result when evidence is missing.
- `decide_company_purchase(company_identifier, ...)`: main entry point for strategist recommendations.

### `__init__.py`

Purpose: package marker for the `agent_stages` folder.
