# agent_helpers

This folder contains the lower-level helpers that the agent stages depend on. These modules handle shared model utilities, time-window filtering, database reads/writes, saved-processing checks, normalization, and evidence assembly.

## Files

### `shared.py`

Purpose: common Ollama, JSON, token-estimation, and time-window utilities used across many stages.

Functions:
- `get_ollama_client(host)`: builds an Ollama client for the requested host.
- `ask_ollama_model(...)`: performs a chat call against Ollama with optional structured-output schema support.
- `parse_published_at(value)`: parses a published timestamp and normalizes it to UTC.
- `normalize_time_window(...)`: turns `start_time` / `end_time` / `max_age_days` into a concrete UTC window.
- `published_at_in_window(...)`: checks whether one article timestamp falls inside the active window.
- `estimate_tokens(text)`: rough token estimate used for prompt sizing.
- `estimate_article_tokens(article)`: rough token estimate for one article record.
- `build_token_limited_batches(...)`: splits articles into batches that fit within a context budget.
- `extract_json_value(text)`: tries to parse JSON from raw model text, fenced JSON, or embedded JSON.
- `extract_json_object(text)`: same as `extract_json_value`, but only returns dictionaries.

### `macro_news.py`

Purpose: helper logic for the world/us macro-news-to-sector stages.

Functions:
- `get_scope_config(news_scope)`: returns the database/table configuration for `world` or `us`.
- `_load_unprocessed_macro_news_rows(news_scope)`: loads unprocessed macro-news articles for the chosen scope.
- `_row_to_macro_article(row)`: normalizes one database row into the macro article payload shape.
- `_is_recent_enough(row, ...)`: checks whether a macro article is within the age cutoff.
- `get_recent_macro_news_articles(news_scope, ...)`: returns recent unprocessed macro articles for one scope.
- `get_sector_reference()`: returns the sector reference list used during macro-to-sector classification.
- `save_batch_results(...)`: persists macro-to-sector impacts and processing metadata for one batch.

### `opportunist_payload.py`

Purpose: helper logic for finding a sector and loading the raw evidence that feeds sector and industry opportunist stages.

Functions:
- `find_sector(sector_identifier)`: resolves a sector by key or display name.
- `_load_sector_rss_rows(sector_id)`: loads saved CNBC-linked sector RSS article rows.
- `_row_to_sector_rss_article(row)`: normalizes a sector RSS row into the prompt payload shape.
- `get_sector_rss_news(sector_identifier, ...)`: returns recent sector RSS articles for one sector.
- `get_high_confidence_macro_news_for_sector(sector_identifier, ...)`: returns recent high-confidence macro-to-sector impacts relevant to one sector.

### `sector_opportunist.py`

Purpose: helper logic specific to the sector opportunist stage.

Functions:
- `get_sector_reference(sector_identifier)`: loads the sector identity block used in prompts and results.
- `_load_opportunist_payload(...)`: loads the combined RSS + macro evidence payload for one sector.
- `_make_base_article_record(...)`: normalizes one source article into the shared sector-stage article shape.
- `_seed_articles_from_sector_rss(payload)`: seeds the article set from sector RSS evidence.
- `_merge_macro_articles(...)`: merges related macro evidence into the article set.
- `_sort_articles(deduped_articles)`: orders candidate articles newest first.
- `_load_processed_article_ids(article_ids)`: loads already-processed article IDs for the sector opportunist stage.
- `_filter_unprocessed_articles(articles)`: removes articles already marked as processed.
- `build_sector_opportunist_articles(sector_identifier, ...)`: builds the final candidate article list for the stage.
- `extract_sector_impacts(payload)`: extracts the `impacts` array from a parsed model payload.
- `build_empty_sector_result(sector)`: returns the no-article/no-impact result shape.
- `build_sector_valid_reference_sets(sector, articles)`: builds trusted sector/article identity sets for normalization.
- `normalize_sector_impact(...)`: validates and injects trusted identifiers into a model-produced sector impact.
- `save_sector_opportunist_batch_results(...)`: saves sector opportunist impacts and processed-article markers.

### `industry_opportunist.py`

Purpose: helper logic specific to the industry opportunist stage.

Functions:
- `get_sector_industry_reference(sector_identifier)`: returns the target sector plus all industries that belong to it.
- `_load_opportunist_payload(...)`: loads the combined RSS + macro evidence payload for one sector.
- `_make_base_article_record(...)`: normalizes one source article into the shared industry-stage article shape.
- `_seed_articles_from_sector_rss(payload)`: seeds the article set from sector RSS evidence.
- `_merge_macro_articles(...)`: merges related macro evidence into the article set.
- `_sort_articles(deduped_articles)`: orders candidate articles newest first.
- `_load_processed_article_ids(article_ids)`: loads already-processed article IDs for the industry opportunist stage.
- `_filter_unprocessed_articles(articles)`: removes articles already marked as processed.
- `build_industry_opportunist_articles(sector_identifier, ...)`: builds the sector, industry reference list, and final candidate articles.
- `extract_impacts(payload)`: extracts the `impacts` array from a parsed model payload.
- `build_empty_result(sector, industries)`: returns the no-article/no-impact result shape.
- `build_valid_reference_sets(industries, articles)`: builds trusted industry/article identity sets for normalization.
- `normalize_impact(...)`: validates and normalizes one model-produced industry impact.
- `save_industry_opportunist_batch_results(...)`: saves industry impacts and processed-article markers.

### `company_opportunist.py`

Purpose: helper logic specific to the company opportunist stage.

Functions:
- `get_company_reference(company_identifier)`: returns the target company, peer-group context, and linked company articles.
- `get_company_opportunist_summary(company_identifier, ...)`: summarizes saved company opportunist impacts already persisted in the database.
- `_make_company_article_record(article)`: normalizes one linked company article into the company-stage article shape.
- `_sort_articles(articles)`: orders candidate articles newest first.
- `_load_processed_article_ids(article_ids, company_id)`: loads already-processed `(article_id, company_id)` rows.
- `_filter_unprocessed_articles(articles, company_id)`: removes company/article pairs already marked as processed.
- `build_company_opportunist_articles(company_identifier, ...)`: builds the target company context and final candidate article list.
- `_parse_company_payload(text)`: tries to parse raw model text into JSON for company-stage output extraction.
- `extract_company_impacts(payload)`: extracts the `impacts` array from a parsed company-stage payload.
- `build_empty_company_result(company, peer_groups)`: returns the no-article/no-impact result shape.
- `build_company_valid_reference_sets(company, articles)`: builds trusted company/article identity values for normalization.
- `normalize_company_impact(...)`: validates and normalizes one model-produced company impact.
- `save_company_opportunist_batch_results(...)`: saves company impacts and processed-article markers.

### `strategist.py`

Purpose: helper logic that assembles saved evidence for the strategist stage.

Functions:
- `get_company_context(company_identifier)`: returns the strategist company block plus peer-group context.
- `_build_processed_article_record(...)`: normalizes one saved impact row into a strategist evidence article.
- `_filter_rows_to_window(...)`: filters saved evidence rows to the requested time window.
- `get_macro_news_for_company_sector(company, ...)`: loads high-confidence macro evidence for the company’s sector.
- `get_sector_news_for_company_sector(company, ...)`: loads raw sector RSS evidence for the company’s sector.
- `_load_high_confidence_sector_rows(sector_id)`: loads saved high-confidence sector opportunist rows.
- `get_high_confidence_sector_news(company, ...)`: returns normalized sector-level evidence rows for the company.
- `_load_high_confidence_industry_rows(industry_id)`: loads saved high-confidence industry opportunist rows.
- `get_high_confidence_industry_news(company, ...)`: returns normalized industry-level evidence rows for the company.
- `_load_high_confidence_company_rows(company_id)`: loads saved high-confidence company opportunist rows.
- `get_high_confidence_company_news(company, ...)`: returns normalized company-level evidence rows for the company.
- `build_strategist_evidence_sections(company_identifier, ...)`: builds the full set of strategist evidence sections.

### `manager.py`

Purpose: helper logic that gathers the live market and account context used by the manager stage.

Functions:
- `_get_alpaca_clients()`: lazily initializes Alpaca stock, option, and trading clients from `.env`.
- `_build_current_stock_price_snapshot(company)`: loads the current stock quote snapshot, with fallback to saved company market data when Alpaca is unavailable.
- `_fetch_option_contracts(...)`: loads filtered call or put contracts for the company from Alpaca trading endpoints.
- `_build_option_market_snapshot(...)`: builds the option-chain context, including stable `option_id` values plus expirations, strikes, implied volatility, greeks, and latest quote data for returned contracts.
- `_build_account_state(company_symbol)`: loads account buying power and the current stock or option positions related to the target company.
- `build_market_context(company, ...)`: combines stock price, option market, and account state into one manager-facing payload section.
- `build_manager_input(company_identifier, ...)`: starts from the strategist payload and injects the live market context before the manager stage builds its prompt.

### `__init__.py`

Purpose: package marker for the `agent_helpers` folder.
