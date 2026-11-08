"""Where the dashboard and LLM client pieces live, and that every patch target in the tests exists.

A test that patches a name its module no longer has fails loudly; one that patches a stale name the
code no longer looks up silently lets the real call through (to Alpaca, or the usage table).
"""
import ast
import importlib
import unittest

from tests import ROOT_DIR


MODULE_NAMES = {
    "api_support.company_decisions": ("build_company_decisions_payload", "build_market_regime_payload"),
    "api_support.decision_expectancy": ("build_decision_expectancy_payload",),
    "api_support.bot_status": ("read_bot_components", "summarize_bot_status", "status_snapshot",
                               "summarize_market_status"),
    "api_support.open_positions": ("build_open_positions_payload",),
    "api_support.dashboard": ("build_dashboard_kpis", "build_risk_controls_payload"),
    "agent_helpers.llm_errors": ("ModelQuotaExhausted", "ModelUnavailable", "provider_status_code"),
    "agent_helpers.llm_vertex": ("ask_vertex_model",),
    "agent_helpers.llm_openai": ("ask_openai_model", "ask_chat_completions_model"),
    "agent_helpers.llm_usage": ("build_model_response_diagnostic", "store_call_usage"),
    "agent_helpers.llm_client": ("Client", "ModelAnswer", "ask_llm_model", "get_model_client"),
}


def _string_patch_targets():
    for path in sorted((ROOT_DIR / "tests").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
                    and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr) == "patch"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                yield f"{path.name}:{node.lineno}", node.args[0].value


def _resolves(target: str) -> bool:
    parts = target.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            owner = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for attribute in parts[split:]:
            if not hasattr(owner, attribute):
                return False
            owner = getattr(owner, attribute)
        return True
    return False


class ModuleLayoutTests(unittest.TestCase):
    def test_each_piece_lives_in_its_own_module(self):
        for module_name, names in MODULE_NAMES.items():
            module = importlib.import_module(module_name)
            for name in names:
                with self.subTest(name=f"{module_name}.{name}"):
                    self.assertTrue(hasattr(module, name))

    def test_every_string_patch_target_in_the_tests_exists(self):
        missing = [f"{where} {target}" for where, target in _string_patch_targets() if not _resolves(target)]

        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
