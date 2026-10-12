"""Gemini 3+ request rules: default model, thinking levels and temperature, with no paid requests."""
from dataclasses import replace
import json
import os
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent_helpers.shared import Client, _resolve_model_name, ask_llm_model
from agent_stages.research_planner import ResearchPlanner
from services.gemini_models import (
    DEFAULT_VERTEX_MODEL,
    default_vertex_model,
    gemini_version,
    temperature_for,
    thinking_level_for,
)
from services.research_model_policy import ResearchModelPolicy
from services.settings import Settings, get_settings, use_settings


def settings_from(**environ):
    return Settings.from_environ(environ)


class ModelRuleTests(unittest.TestCase):
    def test_version_is_parsed_from_plain_and_qualified_ids(self):
        self.assertEqual([gemini_version(m) for m in ("gemini-3.8-flash", "gemini-3-pro",
                                                      "publishers/google/models/gemini-2.5-pro", "llama3.1")],
                         [(3, 8), (3, 0), (2, 5), None])

    def test_minimal_becomes_low_from_gemini_3_8(self):
        self.assertEqual([thinking_level_for(m, "minimal") for m in ("gemini-3.8-flash", "gemini-3.7-flash")],
                         ["low", "minimal"])

    def test_other_levels_pass_through(self):
        self.assertEqual(thinking_level_for("gemini-3.8-flash", " High "), "high")

    def test_gemini_3_runs_at_the_configured_temperature(self):
        self.assertEqual((temperature_for("gemini-3.8-flash", 0, 1.0), temperature_for("gemini-2.5-pro", 0, 1.0),
                          temperature_for("gemini-3.8-flash", 0.4, None)), (1.0, 0, 0.4))

    def test_blank_default_model_falls_back_to_the_constant(self):
        with patch.dict(os.environ, {"VERTEX_DEFAULT_MODEL": ""}):
            self.assertEqual(default_vertex_model(), DEFAULT_VERTEX_MODEL)

    def test_legacy_aliases_resolve_to_the_configured_default(self):
        with patch.dict(os.environ, {"VERTEX_DEFAULT_MODEL": "gemini-test"}):
            resolved = [_resolve_model_name("vertex", m) for m in ("world-news-sectors", "llama3.1", "", "gemini-x")]
        self.assertEqual(resolved, ["gemini-test", "gemini-test", "gemini-test", "gemini-x"])


class SettingsTests(unittest.TestCase):
    def test_stage_thinking_level_follows_the_stage_prefix(self):
        models = settings_from(MANAGER_THINKING_LEVEL="HIGH", WORLD_NEWS_THINKING_LEVEL="low").models
        self.assertEqual((models.manager.thinking_level, models.thesis_validator.thinking_level,
                          models.macro_news.thinking_level, models.strategist.thinking_level),
                         ("high", "high", "low", ""))

    def test_invalid_thinking_level_is_rejected_at_load(self):
        with self.assertRaises(ValueError):
            settings_from(STRATEGIST_THINKING_LEVEL="maximum")

    def test_gemini_3_temperature_defaults_to_google_recommendation(self):
        self.assertEqual(settings_from().vertex.gemini3_temperature, 1.0)

    def test_blank_gemini_3_temperature_keeps_caller_temperatures(self):
        self.assertIsNone(settings_from(VERTEX_GEMINI3_TEMPERATURE="").vertex.gemini3_temperature)

    def test_out_of_range_temperature_is_rejected(self):
        with self.assertRaises(ValueError):
            settings_from(VERTEX_GEMINI3_TEMPERATURE="3")


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

    def test_global_level_applies_when_the_stage_sets_none(self):
        config = self.config_for("gemini-3.8-flash", global_level="medium")
        self.assertEqual(config.thinking_config.thinking_level.value, "MEDIUM")

    def test_no_level_leaves_the_model_default(self):
        self.assertIsNone(self.config_for("gemini-3.8-flash").thinking_config)

    def test_blank_override_keeps_the_caller_temperature(self):
        self.assertEqual(self.config_for("gemini-3.8-flash", gemini3_temperature=None).temperature, 0)

    def test_gemini_2_5_request_is_unchanged(self):
        config = self.config_for("gemini-2.5-flash", stage_level="low", global_level="high")
        self.assertEqual((config.temperature, config.thinking_config), (0, None))


class ResearchPayloadTests(unittest.TestCase):
    def test_planner_sends_a_supported_level_and_gemini_3_temperature(self):
        reply = {"text": json.dumps({"outcome": "ready", "reason": "r", "requests": []}), "usage": {}, "error": False}
        planner = ResearchPlanner(provider="vertex", model="gemini-3.8-flash",
                                  policy=ResearchModelPolicy(thinking_level="minimal"))
        with patch("agent_stages.research_planner.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, json.dumps(reply))) as run:
            planner.generate({}, timeout=5)
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual((payload["thinking_level"], payload["temperature"]),
                         ("low", get_settings().vertex.gemini3_temperature))


if __name__ == "__main__":
    unittest.main()
