"""Gemini 3+ request rules: default model, thinking levels and temperature, with no paid requests."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from agent_helpers.shared import Client, ask_llm_model
from services.gemini_models import thinking_level_for
from services.settings import Settings, get_settings, use_settings


def settings_from(**environ):
    return Settings.from_environ(environ)


class ModelRuleTests(unittest.TestCase):
    def test_minimal_becomes_low_from_gemini_3_8(self):
        self.assertEqual([thinking_level_for(m, "minimal") for m in ("gemini-3.8-flash", "gemini-3.7-flash")],
                         ["low", "minimal"])


class VertexRequestTests(unittest.TestCase):
    def config_for(self, model, *, stage_level=None, global_level="", gemini3_temperature=1.0):
        raw = Mock()
        raw.models.generate_content.return_value = SimpleNamespace(text="{}")
        settings = replace(get_settings().vertex, explicit_cache=False, thinking_level=global_level,
                           gemini3_temperature=gemini3_temperature)
        with use_settings(vertex=settings):
            ask_llm_model(Client(provider="vertex", raw_client=raw), model, "system", "user",
                          temperature=0, thinking_level=stage_level)
        return raw.models.generate_content.call_args.kwargs["config"]

    def test_gemini_3_gets_thinking_level_and_recommended_temperature(self):
        config = self.config_for("gemini-3.8-flash", stage_level="minimal")
        self.assertEqual((config.temperature, config.thinking_config.thinking_level.value), (1.0, "LOW"))

    def test_stage_level_overrides_the_global_level(self):
        config = self.config_for("gemini-3.8-flash", stage_level="high", global_level="low")
        self.assertEqual(config.thinking_config.thinking_level.value, "HIGH")

    def test_gemini_2_5_request_is_unchanged(self):
        config = self.config_for("gemini-2.5-flash", stage_level="low", global_level="high")
        self.assertEqual((config.temperature, config.thinking_config), (0, None))


if __name__ == "__main__":
    unittest.main()
