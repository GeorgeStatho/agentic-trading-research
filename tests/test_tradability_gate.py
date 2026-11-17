"""TRADABILITY: before the strategist, does the selector's top pick in the default bucket fit the premium cap?

In shadow the verdict is only recorded; an enforced rejection skips the strategist and manager. The broker is mocked.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_helpers.deterministic_option_selector as selector
from agent_helpers.deterministic_option_selector.market import selection_date
from agentCallers import decision_pipeline
from services.gates import tradability_gate as gate
from services.gates.tradability_gate import TradabilityPolicy, check_tradability, cycle_premium_cap, evaluate_tradability
from tests.test_contract_selector_thin_chains import DECISION_TIME, contract

SPOT = 120.27
NEAR = contract(1, "BX261204C00121000", "2026-12-04", 121.0, 500.0, 4.20, 4.40, 0.47, 0.033, -0.070, 0.150, 0.31)
AT_TARGET = contract(2, "BX261204C00124000", "2026-12-04", 124.0, 500.0, 2.90, 3.04, 0.40, 0.033, -0.068, 0.148, 0.31)
POLICY = TradabilityPolicy()
MARKET = {"current_stock_price": {"price": SPOT}}


def evaluate(contracts, cap, policy=POLICY):
    with selection_date(DECISION_TIME):
        return evaluate_tradability(contracts, market_context=MARKET, premium_cap=cap, policy=policy)


@patch.object(selector, "OPTION_SELECTOR_MODE", "hybrid")
@patch.object(selector, "ALLOW_RISKY_SIMPLE_FALLBACK", False)
@patch.dict(os.environ, {"V2_LIQUIDITY_MODE": "shadow", "V2_SCENARIO_MODE": "shadow"})
class EvaluateTests(unittest.TestCase):
    def test_the_selectors_top_pick_under_the_cap_is_tradable(self):
        verdict = evaluate([NEAR, AT_TARGET], 350.0)

        self.assertIs(verdict["would_allow"], True)
        self.assertEqual(verdict["reason_codes"], ["tradable"])
        self.assertEqual(verdict["metrics"]["call"], {"outcome": "tradable", "option_symbol": "BX261204C00124000",
                                                      "expiration_date": "2026-12-04", "strike_price": 124.0,
                                                      "premium": 304.0})
        self.assertEqual(verdict["metrics"]["put"]["outcome"], "no_eligible_contract")
        self.assertEqual((verdict["metrics"]["premium_cap"], verdict["metrics"]["dte_bucket"]), (350.0, "30_45"))
        self.assertEqual((verdict["enforcement_mode"], verdict["allowed"]), ("shadow", True))

    def test_a_top_pick_over_the_cap_rejects_even_when_a_cheaper_contract_is_eligible(self):
        with patch.object(gate, "_pick_matching_contract", return_value=(NEAR, {})):
            verdict = evaluate([NEAR, AT_TARGET], 350.0)

        self.assertIs(verdict["would_allow"], False)
        self.assertEqual(verdict["reason_codes"], ["over_premium_cap"])
        self.assertEqual(verdict["metrics"]["call"]["premium"], 440.0)
        self.assertIs(verdict["allowed"], True)

    def test_no_contract_on_either_side_rejects(self):
        verdict = evaluate([], 350.0)

        self.assertIs(verdict["would_allow"], False)
        self.assertEqual(verdict["reason_codes"], ["no_eligible_contract"])

    def test_a_top_pick_without_a_quote_is_unknown(self):
        unquoted = {**AT_TARGET, "latest_quote": {}}
        with patch.object(gate, "_pick_matching_contract", return_value=(unquoted, {})):
            verdict = evaluate([unquoted], 350.0)

        self.assertIsNone(verdict["would_allow"])
        self.assertEqual(verdict["reason_codes"], ["missing_quote"])

    def test_an_unknown_cap_is_unknown(self):
        verdict = evaluate([NEAR, AT_TARGET], None)

        self.assertIsNone(verdict["would_allow"])
        self.assertEqual(verdict["reason_codes"], ["premium_cap_unknown"])

    def test_a_zero_cap_means_uncapped(self):
        self.assertIs(evaluate([NEAR, AT_TARGET], 0.0)["would_allow"], True)

    def test_enforce_refuses_a_rejected_pick(self):
        verdict = evaluate([], 350.0, policy=TradabilityPolicy(mode="enforce"))

        self.assertIs(verdict["allowed"], False)


class CheckTests(unittest.TestCase):
    def check(self, chains, **kwargs):
        with patch.object(gate, "_build_current_stock_price_snapshot", return_value={"price": SPOT}) as quote, \
                patch.object(gate, "build_bucket_chains", **chains) as fetch:
            return check_tradability("BX", premium_cap=350.0, policy=POLICY), quote, fetch

    def test_a_request_failure_is_unknown_and_never_raises(self):
        verdict, _, _ = self.check({"side_effect": RuntimeError("429 Too Many Requests")})

        self.assertIsNone(verdict["would_allow"])
        self.assertEqual(verdict["reason_codes"], ["chain_request_failed"])
        self.assertIn("429", verdict["metrics"]["failure"])

    def test_an_unavailable_chain_is_unknown(self):
        verdict, _, _ = self.check({"return_value": {"available": False, "error": "no credentials", "buckets": {}}})

        self.assertIsNone(verdict["would_allow"])
        self.assertEqual(verdict["reason_codes"], ["chain_unavailable"])

    def test_one_chain_fetch_reads_the_default_bucket_at_the_quoted_price(self):
        chains = {"available": True, "raw_contract_count": 2, "buckets": {"30_45": [AT_TARGET], "20_30": [NEAR]}}
        with patch.object(gate, "evaluate_tradability", return_value={"would_allow": True}) as evaluate_mock:
            _, quote, fetch = self.check({"return_value": chains})

        quote.assert_called_once_with({"symbol": "BX"})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.kwargs["reference_stock_price"], SPOT)
        self.assertEqual(evaluate_mock.call_args.args[0], [AT_TARGET])


class CyclePremiumCapTests(unittest.TestCase):
    def settings(self, pct, dollars):
        from services.core.trading_config import WorkerSettings
        return SimpleNamespace(max_premium_per_trade_pct=pct, max_premium_per_trade_dollars=dollars,
                               resolve_max_premium_per_trade=lambda bp: WorkerSettings.resolve_max_premium_per_trade(
                                   SimpleNamespace(max_premium_per_trade_pct=pct, max_premium_per_trade_dollars=dollars), bp))

    def test_buying_power_is_read_once_for_the_percent_cap(self):
        trading = Mock()
        trading.get_account.return_value = SimpleNamespace(buying_power="1000")
        with patch.object(gate, "_get_alpaca_clients", return_value={"trading": trading}):
            cap = cycle_premium_cap(self.settings(55.0, 800.0))

        self.assertEqual(cap, 550.0)
        trading.get_account.assert_called_once()

    def test_an_account_failure_leaves_the_cap_unknown(self):
        trading = Mock()
        trading.get_account.side_effect = RuntimeError("timeout")
        with patch.object(gate, "_get_alpaca_clients", return_value={"trading": trading}):
            self.assertIsNone(cycle_premium_cap(self.settings(55.0, 800.0)))

    def test_a_dollar_only_cap_needs_no_account_call(self):
        with patch.object(gate, "_get_alpaca_clients") as clients:
            self.assertEqual(cycle_premium_cap(self.settings(0.0, 800.0)), 800.0)
        clients.assert_not_called()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patches = {
            "build_market_regime_snapshot": {"return_value": {"state": "trend_up", "as_of": "2026-07-21T15:00:00+00:00"}},
            "record_manager_decision_history": {"return_value": 1},
            "decide_company_purchase": {"side_effect": lambda symbol, **kw: self.calls.append("strategist") or {
                "company": {"symbol": symbol}, "recommendation": {"decision": "trade_candidate"}}},
            "decide_company_option_position": {"side_effect": lambda symbol, **kw: self.calls.append("manager") or {
                "company": {"symbol": symbol}, "recommendation": {"decision": "call", "confidence": "high"}}},
            "apply_deterministic_option_selection": {"side_effect": lambda result: result},
            "cycle_premium_cap": {"side_effect": lambda settings: self.calls.append("cap") or 500.0},
        }
        for name, options in patches.items():
            patcher = patch.object(decision_pipeline, name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cycle(self, check):
        results = []
        with patch.object(decision_pipeline, "check_tradability", side_effect=check):
            decision_pipeline._run_strategist_and_manager(["AAA", "BBB"], on_manager_result=results.append)
        return results

    def test_a_rejection_is_recorded_before_the_strategist_and_skips_nothing(self):
        verdict = {"would_allow": False, "allowed": True, "reason_codes": ["over_premium_cap"]}

        def check(symbol, **kwargs):
            self.calls.append(("tradability", symbol, kwargs["premium_cap"]))
            return verdict

        results = self.run_cycle(check)

        self.assertEqual(self.calls, ["cap", ("tradability", "AAA", 500.0), "strategist", "manager",
                                      ("tradability", "BBB", 500.0), "strategist", "manager"])
        self.assertEqual([result["tradability_gate"] for result in results], [verdict, verdict])

    def test_an_enforced_rejection_skips_the_strategist_and_manager(self):
        blocked = {"would_allow": False, "allowed": False, "reason_codes": ["over_premium_cap"]}

        results = self.run_cycle(lambda symbol, **kwargs: blocked if symbol == "AAA" else {"would_allow": True,
                                                                                          "allowed": True})

        self.assertEqual(self.calls, ["cap", "strategist", "manager"])
        self.assertEqual([result["company"]["symbol"] for result in results], ["BBB"])

    def test_a_check_that_raises_never_stops_the_cycle(self):
        def check(symbol, **kwargs):
            raise RuntimeError("broker down")

        results = self.run_cycle(check)

        self.assertEqual(len(results), 2)
        self.assertIsNone(results[0]["tradability_gate"])

    def test_disabled_mode_makes_no_request(self):
        with patch.dict(os.environ, {"TRADABILITY_MODE": "disabled"}):
            results = self.run_cycle(lambda symbol, **kwargs: self.fail("checked while disabled"))

        self.assertNotIn("cap", self.calls)
        self.assertEqual(len(results), 2)


class LedgerTests(unittest.TestCase):
    def test_a_recorded_verdict_appears_in_the_gate_ledger(self):
        from market_db_modules.gate_ledger import gate_ledger
        from services.runtime.candidate_lifecycle import record_gate
        from tests.db_fixtures import temporary_database
        from tests.test_gate_ledger import GateLedgerTests

        self.db, self.count = temporary_database(self), 0
        GateLedgerTests.decision(self, "call", .03)
        verdict = evaluate([], 350.0)
        context = {"candidate_id": "candidate-1", "symbol": "MSFT", "created_at": DECISION_TIME.isoformat(),
                   "pipeline_version": {"pipeline_version": "fixture", "gate_config": {"tradability": {}}}}
        record_gate(context, "TRADABILITY", verdict)

        cell = gate_ledger(days=60, db_path=self.db)["stages"]["TRADABILITY[tradability-v1]"]
        self.assertEqual((cell["passed"], cell["rejected"]), (0, 1))


class RegistryTests(unittest.TestCase):
    def test_tradability_is_an_optional_entry_gate_with_its_own_proof_files(self):
        from services.core.pipeline_version import gate_config
        from services.gates.entry_gates import ENTRY_GATES
        from services.research.research_promotion import GATE_BLOCKS, GATE_FILES

        entry = next(entry for entry in ENTRY_GATES if entry.name == "TRADABILITY")
        self.assertEqual((entry.result_key, entry.verdict_required), ("tradability_gate", False))
        self.assertEqual(GATE_BLOCKS["TRADABILITY"], "tradability")
        self.assertIn("Python Scripts/services/gates/tradability_gate.py", GATE_FILES["TRADABILITY"])
        self.assertEqual(gate_config()["tradability"]["mode"], "shadow")

    def test_its_proof_files_cover_its_direct_imports_and_the_whole_selector(self):
        from services.core import trading_path
        from services.research.research_promotion import GATE_FILES

        roots = tuple(trading_path.ROOT / name for name in trading_path.IMPORT_ROOTS)
        resolve = trading_path._Resolver(roots)
        rule = trading_path.ROOT / "Python Scripts/services/gates/tradability_gate.py"
        imported = {resolve(name) for name in trading_path._imported_names(rule, roots)} - {None}
        selector_dir = trading_path.ROOT / "Python Scripts/agentCallers/agent_helpers/deterministic_option_selector"
        expected = {str(path.relative_to(trading_path.ROOT)) for path in imported | set(selector_dir.glob("*.py"))
                    if path.name != "__main__.py"}
        # Shared infrastructure, not inputs to the verdict: the proof machinery, settings and number helpers.
        infrastructure = {"Python Scripts/services/research/research_promotion.py",
                          "Python Scripts/services/core/settings.py", "Python Scripts/services/core/numbers.py",
                          "Python Scripts/services/__init__.py", "Python Scripts/services/core/__init__.py",
                          "Python Scripts/services/gates/__init__.py", "Python Scripts/services/options/__init__.py",
                          "Python Scripts/services/research/__init__.py",
                          "Python Scripts/agentCallers/agent_helpers/__init__.py",
                          "Python Scripts/agentCallers/agent_helpers/market_context/__init__.py"}
        self.assertEqual(expected - infrastructure - set(GATE_FILES["TRADABILITY"]), set())

    def test_enforce_stays_shadow_without_a_proof(self):
        with patch.dict(os.environ, {"TRADABILITY_MODE": "enforce"}), \
                patch("services.research.research_promotion.approved", return_value=False):
            self.assertEqual(TradabilityPolicy.from_env().mode, "shadow")


if __name__ == "__main__":
    unittest.main()
