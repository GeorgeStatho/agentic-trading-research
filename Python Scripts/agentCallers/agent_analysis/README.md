# agent_analysis

This folder contains read-oriented analysis utilities that score saved agent results. These modules do not call Ollama directly. They read processed database rows and turn them into simple counts and rankings that the pipeline can use to choose sectors and industries.

## Files

### `sector_interest.py`

Purpose: summarize saved world/us macro-to-sector impacts and rank sectors by how many high-confidence signals they currently have.

Functions:
- `get_processed_sector_interest(...)`: loads saved sector-impact rows from the world and US macro stages, applies the requested time window, and returns normalized records.
- `getSectorScores()`: counts high-confidence rows per `sector_key`.
- `getTopThreeSectors(sectorScores)`: sorts the sector score mapping descending and returns the top three entries.

### `industry_interest.py`

Purpose: summarize saved industry opportunist impacts for one sector and rank industries by high-confidence signal count.

Functions:
- `get_processed_industry_interest(sector_identifier, ...)`: loads saved industry opportunist rows for one sector, applies the requested time window, and returns normalized records.
- `getIndustryScores(sector_identifier, ...)`: counts high-confidence rows per `industry_key` for the requested sector.
- `getTopThreeIndustries(industry_scores)`: sorts the industry score mapping descending and returns the top three entries.

### `__init__.py`

Purpose: package marker for the `agent_analysis` folder.
