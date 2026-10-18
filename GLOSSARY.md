# Glossary

Domain terms used in code, docs and architecture reviews.

- **Frozen payload**: the exact input a stage decided on, saved at decision time (`manager_replay_payload`, the `pre_strategist` checkpoint) so the decision can be replayed later.
- **Stage decision**: frozen payload → recommendation. `decide_manager_recommendation` and `decide_strategist_recommendation` run it identically for live runs and for replays (LLM backtest, challenger and offline evaluation); live adds payload building, persistence and gates around it.
