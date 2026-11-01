# Plan 05: Reasoning-first Strategist schema

## Context

The model writes JSON in order. When `decision` and `confidence` come first, the model picks a side
and then writes the evidence to justify it. Putting evidence claims and contradictions first, and
the decision last, makes the model work through the evidence before it commits. The change is
almost free, so it's worth one measured attempt.

## Current state

- Schema: `agentCallers/agent_stages/strategist_schema.py:101-110`. The `required` list (and the
  property order) starts with `decision`, `confidence`, `evidence_quality` and `setup_quality`. The
  evidence-thesis variant is built at `:138-140`.
- Transport: `agentCallers/agent_helpers/llm_client.py:607-612` sends the schema as
  `response_json_schema` for Gemini.
- Prompt version: `agentCallers/agent_stages/strategist_runner.py:55`
  (`STRATEGIST_PROMPT_VERSION`). It is persisted with each run.

## Proposed change

1. Reorder `properties` (and `required`, for readability) in both schema variants:
   1. `evidence_claims`, the grounded entity IDs, and `risks` / `contradictions_present`
   2. `why_now`, `thesis`
   3. `evidence_quality`, `setup_quality`, `timing_clarity`
   4. `expected_stock_direction`, `preferred_option_direction`, `time_horizon`
   5. `confidence`
   6. `decision`
2. Check that the Gemini output actually follows schema key order with `response_json_schema`. If
   it doesn't, set `propertyOrdering` on the provider path in `llm_client.py`.
3. Bump `STRATEGIST_PROMPT_VERSION` (e.g. `grounded-strategist-v6-reasoning-first`), so the research
   layer splits runs before and after the change.
4. Make no change to the prompt text, normalization or the Manager.

## Files to touch

- `agentCallers/agent_stages/strategist_schema.py`
- `agentCallers/agent_stages/strategist_runner.py` (version string)
- Possibly `agentCallers/agent_helpers/llm_client.py` (`propertyOrdering`)
- `tests/test_strategist_schema*.py` (extend)

## Risks / open questions

- Any code that reads the raw JSON text by position. Unlikely, because output is parsed into a dict.
  Check with a grep in `strategist_normalization.py` and the stored-output readers.
- Longer reasoning before the decision may add a few output tokens per call. That's negligible, but
  note it against the daily request budget.
- The effect is probably small and hard to measure. Treat it as hygiene, not a strategy change.

## Tests

- The schema's property order matches the list above.
- `required` keeps the same set of keys, so the order changes but the contract doesn't.
- An existing normalization fixture still parses.

## Verification

Unit tests first. Then **one** dry-run strategist call to confirm the output key order. **This is a
paid LLM call: ask before running it.**

## Rollout

Ship with the version bump. After about 2 weeks, compare the decision mix (trade candidate rate,
contradiction rate) and outcomes by prompt version in the research layer.
