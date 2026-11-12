"""The one registry of deterministic entry gates.

Every consumer — order eligibility, selected-option summaries, decision history and
research inputs — iterates this registry, so adding a gate is a single entry here plus
the code that computes its verdict. Result keys match the persisted formats read by
stored decisions, snapshots, the API and the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent_contracts import GateVerdict


@dataclass(frozen=True)
class EntryGate:
    name: str
    result_key: str
    # Legacy gates always produce a verdict, so a missing one blocks. Optional gates exist
    # only while their feature is enabled; their absence leaves eligibility unchanged.
    verdict_required: bool
    # Its boolean verdict also gets a decision_gate_verdicts row, which outcome joins and the ledger read.
    records_verdict_row: bool = False

    def verdict(self, result: Mapping[str, Any]) -> GateVerdict | None:
        verdict = result.get(self.result_key)
        return verdict if isinstance(verdict, dict) else None

    def allows_entry(self, result: Mapping[str, Any]) -> bool:
        if self.result_key not in result or result.get(self.result_key) is None:
            return not self.verdict_required
        verdict = self.verdict(result)
        return verdict is not None and verdict.get("allowed") is True


ENTRY_GATES = (
    EntryGate("CONTINUATION", "entry_gate", verdict_required=True, records_verdict_row=True),
    EntryGate("REGIME", "market_regime_gate", verdict_required=True, records_verdict_row=True),
    EntryGate("EVIDENCE_THESIS", "evidence_thesis_gate", verdict_required=False),
    # Attached to every directional result while trade economics are on; see services/gates/trade_economics.py.
    EntryGate("TRADE_ECONOMICS", "trade_economics_gate", verdict_required=False),
    EntryGate("ENTRY_EXTENSION", "entry_extension_gate", verdict_required=False, records_verdict_row=True),
    # Judged before the strategist from its own chain fetch; see services/gates/tradability_gate.py.
    EntryGate("TRADABILITY", "tradability_gate", verdict_required=False, records_verdict_row=True),
    # Shadow-only alternative to REGIME; its verdict is always allowed. See services/gates/market_regime_variants.py.
    EntryGate("REGIME_SPY_TREND", "market_regime_spy_trend_gate", verdict_required=False,
              records_verdict_row=True),
)


def entry_allowed(result: Mapping[str, Any]) -> bool:
    return all(gate.allows_entry(result) for gate in ENTRY_GATES)


def carry_gate_verdicts(result: Mapping[str, Any]) -> dict[str, GateVerdict | None]:
    """Copy every gate verdict onto a derived record, keeping each gate's persisted default."""
    return {gate.result_key: result.get(gate.result_key, {} if gate.verdict_required else None)
            for gate in ENTRY_GATES}
