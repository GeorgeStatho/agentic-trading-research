"""ENTRY_EXTENSION: a shadow check against chasing a move the stock already made."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from services.gates.entry_extension_gate import EntryExtensionPolicy, evaluate_entry_extension_gate
from services.gates.entry_gates import ENTRY_GATES, entry_allowed


def _result(decision='call', move=3.0, overextension='high'):
    recommendation = {'decision': decision}
    if overextension is not None:
        recommendation['overextension_risk'] = overextension
    return {'recommendation': recommendation, 'company': {'trend_quality': {'5d_return_pct': move}}}


def _verdict(**kwargs):
    return evaluate_entry_extension_gate(_result(**kwargs), policy=EntryExtensionPolicy())


class EntryExtensionVerdictTests(unittest.TestCase):
    def test_call_after_a_fall_is_allowed_whatever_the_overextension(self):
        self.assertIs(_verdict(move=-2.0, overextension='high')['would_allow'], True)

    def test_put_after_a_rise_is_allowed_because_the_move_is_signed_by_direction(self):
        self.assertIs(_verdict(decision='put', move=2.0, overextension='high')['would_allow'], True)

    def test_put_after_a_fall_with_high_overextension_is_rejected(self):
        verdict = _verdict(decision='put', move=-2.0, overextension='high')
        self.assertIs(verdict['would_allow'], False)
        self.assertEqual(verdict['signed_prior_move_pct'], 2.0)

    def test_extended_move_with_low_overextension_is_allowed(self):
        self.assertIs(_verdict(move=4.0, overextension='low')['would_allow'], True)

    def test_extended_move_with_medium_or_high_overextension_is_rejected(self):
        for overextension in ('medium', 'high'):
            verdict = _verdict(move=4.0, overextension=overextension)
            self.assertIs(verdict['would_allow'], False, overextension)
            self.assertEqual(verdict['reason_codes'], ['extended_prior_move_without_low_overextension'])

    def test_missing_move_is_unknown_unless_overextension_is_low(self):
        self.assertIsNone(_verdict(move=None, overextension='high')['would_allow'])
        self.assertIs(_verdict(move=None, overextension='low')['would_allow'], True)

    def test_missing_overextension_is_unknown_only_when_the_move_is_extended(self):
        self.assertIsNone(_verdict(move=4.0, overextension=None)['would_allow'])
        self.assertIs(_verdict(move=-1.0, overextension=None)['would_allow'], True)

    def test_non_directional_decision_has_no_verdict(self):
        self.assertIsNone(_verdict(decision='neither')['would_allow'])

    def test_shadow_never_blocks_and_enforce_blocks_only_a_rejection(self):
        self.assertTrue(_verdict(move=4.0)['allowed'])
        enforce = EntryExtensionPolicy(mode='enforce')
        self.assertFalse(evaluate_entry_extension_gate(_result(move=4.0), policy=enforce)['allowed'])
        self.assertTrue(evaluate_entry_extension_gate(_result(move=None), policy=enforce)['allowed'])

    def test_verdict_carries_the_entry_gate_contract(self):
        verdict = _verdict()
        for key in ('allowed', 'would_allow', 'reason_codes', 'policy_version', 'enforcement_mode'):
            self.assertIn(key, verdict)
        self.assertEqual(verdict['enforcement_mode'], 'shadow')


class EntryExtensionRegistrationTests(unittest.TestCase):
    def test_enforce_request_without_a_proof_stays_shadow(self):
        with patch.dict(os.environ, {'ENTRY_EXTENSION_MODE': 'enforce'}):
            self.assertEqual(EntryExtensionPolicy.from_env().mode, 'shadow')

    def test_registered_as_an_optional_entry_gate(self):
        gate = next(gate for gate in ENTRY_GATES if gate.name == 'ENTRY_EXTENSION')
        self.assertEqual(gate.result_key, 'entry_extension_gate')
        self.assertFalse(gate.verdict_required)
        self.assertTrue(entry_allowed({**{g.result_key: {'allowed': True} for g in ENTRY_GATES},
                                       'entry_extension_gate': _verdict(move=4.0)}))

    def test_promotion_proofs_bind_to_its_own_files_and_policy_block(self):
        from services.core.pipeline_version import gate_config
        from services.research.research_promotion import GATE_BLOCKS, GATE_FILES
        self.assertEqual(GATE_BLOCKS['ENTRY_EXTENSION'], 'entry_extension')
        self.assertIn('Python Scripts/services/gates/entry_extension_gate.py', GATE_FILES['ENTRY_EXTENSION'])
        self.assertEqual(gate_config()['entry_extension']['policy_version'], 'entry-extension-v1')

    def test_decision_pipeline_records_extension_and_regime_variant_verdicts(self):
        import decision_pipeline
        recorded = []
        with patch('services.runtime.candidate_lifecycle.record_gate',
                   side_effect=lambda _context, name, gate: recorded.append((name, gate))), \
                patch('services.runtime.candidate_lifecycle.checkpoint'), \
                patch('services.runtime.candidate_lifecycle.reject'), \
                patch.object(decision_pipeline, 'apply_option_hurdle_to_manager_result', side_effect=lambda r: r), \
                patch.object(decision_pipeline, 'prepare_scenario_inputs', side_effect=lambda r: r), \
                patch.object(decision_pipeline, 'apply_deterministic_option_selection', side_effect=lambda r: r), \
                patch.object(decision_pipeline, 'record_directed_absorption'), \
                patch.object(decision_pipeline, 'record_scenario_selection'), \
                patch.object(decision_pipeline, 'evaluate_evidence_thesis_gate', return_value=None), \
                patch.object(decision_pipeline, 'apply_trade_economics', side_effect=lambda r, policy: r):
            result = decision_pipeline._apply_post_manager_gates(
                _result(move=4.0), candidate_context={'candidate_id': 'c1'}, cycle_version={},
                thesis_policy=None, economics_policy=None)
        self.assertIs(result['entry_extension_gate']['would_allow'], False)
        self.assertIn(('ENTRY_EXTENSION', result['entry_extension_gate']), recorded)
        self.assertIn(('REGIME_SPY_TREND', result['market_regime_spy_trend_gate']), recorded)
        # The production verdict is recorded once, under its own stage.
        self.assertEqual([gate for name, gate in recorded if name == 'REGIME'], [result['market_regime_gate']])


if __name__ == '__main__':
    unittest.main()
