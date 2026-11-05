# Plan 06: Manager vs Strategist direction disagreement

## Context

The Strategist states an expected stock direction and a preferred option direction. The Manager
then picks call, put or neither. Only the prompt stops the Manager from flipping a bullish thesis
into a put. A flip can sometimes be a legitimate read of the same evidence, but nobody measures how
often it happens or how flips perform. Gemini proposed a hard direction lock. The cheaper, safer
first step is to record the disagreement and look at the data.

## Current state

- Strategist fields seen by the Manager:
  `agentCallers/agent_stages/manager_strategist_context.py` (`expected_stock_direction`,
  `preferred_option_direction`).
- Post-Manager gate chain: `agentCallers/decision_pipeline.py:448` (`_apply_post_manager_gates`),
  which calls `apply_market_regime_gate_to_manager_result` at `:473`.
- Decision history recording: `agentCallers/decision_pipeline.py:379`
  (`_record_manager_decision_history_for_result`).
- No code compares the two directions.

## Proposed change

1. Add a pure function `direction_agreement(strategist, manager_decision)`. It returns:
   - `agree`: call with `up` or `call`, or put with `down` or `put`;
   - `flip`: the opposite;
   - `strategist_neutral`: the Strategist gave no direction;
   - `not_directional`: the Manager chose neither.
2. Attach the result to the Manager result in `_apply_post_manager_gates` and persist it with the
   decision history. It is metadata only and never blocks.
3. After about 4 weeks of data, run a read-only query on the backup DB covering flip count, flip
   rate and the outcomes of flipped trades vs agreeing ones.
4. **Only if flips under-perform:** add a shadow gate `DIRECTION_AGREEMENT` following the playbook,
   with reason codes `pass_direction_agreement` and `manager_flipped_strategist_direction`.

## Files to touch

- `agentCallers/decision_pipeline.py`
- A small helper module, e.g. `services/evaluation/direction_agreement.py`
- The decision-history store, only if it needs a new column; prefer the existing JSON payload
- `tests/test_direction_agreement.py` (new)

## Risks / open questions

- Field aliases: the normalization accepts `bullish`/`bearish`/`up`/`down`. Reuse
  `_normalize_strategist_recommendation` rather than parsing again.
- Expect few flips. If there are fewer than about 10 in 4 weeks, the gate isn't worth building.

## Tests

- Covers agree, flip, strategist_neutral and not_directional.
- Accepts both alias spellings.
- The field is persisted without changing `allowed`.

## Verification

Unit tests, then one dry-run pipeline pass on stored inputs (no LLM call needed if existing
recorded results are replayed through `_apply_post_manager_gates`).

## Rollout

Logging only. Build a gate only if the data asks for it.
