# Glossary

Domain terms used in code, docs and architecture reviews.

- **Frozen payload**: the exact input a stage decided on, saved at decision time so the decision can be replayed later. The manager's is `manager_decision_payloads.replay_input` (`manager_replay_payload`; equal to the `pre_manager` checkpoint when the decision has a candidate). The strategist's is the `pre_strategist` checkpoint.
- **Frozen decision**: one manager decision's Frozen payloads, plus the bucket chain production selected from (the `pre_contract_selection` checkpoint) and the candidate's pipeline manifest. Replays read them only through `FrozenDecision` (`llm_replay_store.py`), which hash-checks every checkpoint; they never query those stores directly.
- **Stage decision**: frozen payload → recommendation. `decide_manager_recommendation` and `decide_strategist_recommendation` run it identically for live runs and for replays (LLM backtest, challenger and offline evaluation); live adds payload building, persistence and gates around it.
