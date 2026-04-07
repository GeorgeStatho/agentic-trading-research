# agent_runtime

This folder holds smaller runtime scripts and experiments. These files are less central than the staged pipeline modules and are mostly useful for local testing or manual experimentation with agents/models.

## Files

### `init_agents.py`

Purpose: defines a set of named Ollama clients representing older conceptual agent roles.

Objects:
- `researcher`: intended to choose stocks that deserve deeper analysis.
- `questioner`: intended to ask deeper follow-up questions about a company.
- `riskManager`: intended to block overly risky decisions.
- `marketScanner`: intended to scan market/news context for promising opportunities.
- `newsAnalayst`: intended to summarize company or stock news.
- `executor`: intended to decide buy/sell/hold actions.
- `stratgist`: intended to decide higher-level strategy.
- `manager`: intended to review logs and adjust weak agents.
- `urlBuilder`: intended to decide what URLs the researcher should visit.
- `worldNews`: intended to watch for breaking macro/world news.

Notes:
- This file does not define functions; it just instantiates named clients.

### `market_health.py`

Purpose: very small local Ollama smoke test / experiment script.

Functions:
- `ask_model(client, model, system_prompt, user_prompt)`: sends a simple chat request and returns the raw text response.

Notes:
- The file runs an example prompt immediately when executed.

### `researcher.py`

Purpose: simple local script that asks a “stock researcher” prompt through the shared Ollama helper.

Notes:
- This file currently does not define helper functions; it creates a client, sends one example prompt, and prints the answer.

### `__init__.py`

Purpose: package marker for the `agent_runtime` folder.
