# Plan 07: Prompt-feedback ablations

## Context

Gemini made two claims about the Manager prompt, both plausible and both untested:

- **A, feedback drift.** Feeding resolved autopsies and recent decision history into the prompt can
  cause recency bias: the model over-learns last month's losers.
- **B, narrative anchoring.** A persuasive Strategist thesis can lead the Manager to forgive weak
  technicals.

Gemini's fixes (move calibration into Python weights, or blind the Manager to all prose) are bigger
changes than the evidence supports. Python weights fitted on a few dozen trades overfit just as
badly, and a fully blinded Manager is a checklist that Python could run. The right first step is a
paired replay on frozen inputs, measuring whether each input changes decisions and outcomes.

## Current state

- Autopsy and history settings: `services/core/settings.py:244-249`, with `MANAGER_RESOLVED_AUTOPSY_LIMIT`
  defaulting to 3 and `MANAGER_DECISION_HISTORY_LIMIT` defaulting to 3. The **minimum is 1**
  (`_integer(..., minimum=1)` at `:36-37`), so a true zero isn't possible today.
- The prompt's usage rules are at `agentCallers/agent_stages/manager_prompt.py:39`.
- Strategist context given to the Manager: `agentCallers/agent_stages/manager_strategist_context.py`
  (`_build_manager_visible_strategist_context`).
- Replay tooling: `agentCallers/offline_manager_evaluation.py` and `services/evaluation/llm_backtest.py`
  (paired replays, frozen inputs, which refuse to run if `gate_config` has changed).

## Proposed change

### A: Autopsy and history ablation

1. Allow 0 for the two limits: change their `minimum` to 0, and make sure an empty list produces no
   prompt section rather than an empty one.
2. Replay variants on the same frozen Manager inputs:
   - `baseline`: production settings;
   - `no_autopsy`: autopsy limit 0;
   - `no_history`: history limit 0;
   - `neither`: both 0.

### B: Prose blinding

1. Add a replay-only variant flag (never read by production) to
   `_build_manager_visible_strategist_context`. It drops `thesis`, `why_now` and the free-text
   evidence-thesis fields.
2. It keeps the structured fields: decision, directions, horizon, quality ordinals,
   `contradictions_present`, claim IDs, and the deterministic `evidence_thesis_check` result.
3. Replay it as variant `blinded` against the baseline.

### Metrics for each variant vs baseline

- Decision flip rate (call/put/neither) on identical inputs.
- Approval rate.
- For resolved decisions: outcome of the approvals that differ (price-response or position outcome).
- Variant drift: the same variant re-run once, to estimate LLM noise. A difference smaller than the
  noise means no effect.

## Files to touch

- `services/core/settings.py` (minimums)
- `agentCallers/agent_stages/manager_strategist_context.py` (variant flag)
- `agentCallers/offline_manager_evaluation.py` (variant wiring)
- `tests/test_manager_context*.py` and `tests/test_offline_manager_evaluation*.py` (extend)

## Cost and approval

**These are paid LLM replays (Gemini Pro).** Before running, the plan owner states:

- N frozen decisions (suggest 40–60 resolved ones);
- 5 variants plus 1 noise re-run, so about 6×N Manager calls;
- the estimated cost.

**Ask for explicit approval before the run.** Code changes and unit tests make no paid calls.

## Risks / open questions

- A small N makes outcome differences noisy. Treat the decision flip rate as the main result and
  outcomes as a supporting signal.
- Frozen inputs must include the autopsies as they existed at decision time, or variant A compares
  against something production never saw.

## Tests

- A zero limit produces no autopsy or history section.
- The blinded context keeps the structured fields and removes the prose fields.
- Production code paths never set the variant flag.

## Verification

Unit tests, then the approved replay. Write the results into this file.

## Rollout

- If `no_autopsy` or `no_history` matches or beats the baseline on outcomes, lower the production
  default through a normal config change.
- If `blinded` beats the baseline, try a middle ground before full blinding: keep `why_now` and drop
  `thesis`.
