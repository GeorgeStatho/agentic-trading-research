"""The process settings object: environment resolution, injection and the manifest record."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from services.settings import Settings, get_settings, load_settings, use_settings


def settings_from(**environ):
    return Settings.from_environ(environ)


class ModelResolutionTests(unittest.TestCase):
    def test_defaults_match_the_historical_constants(self):
        models = settings_from().models
        self.assertEqual((models.strategist.model, models.strategist.ollama_host),
                         ("world-news-sectors", "http://127.0.0.1:11434"))

    def test_stage_setting_wins_over_shared_fallbacks(self):
        models = settings_from(STRATEGIST_MODEL="strategist", MACRO_NEWS_MODEL="macro").models
        self.assertEqual((models.strategist.model, models.manager.model), ("strategist", "macro"))

    def test_macro_news_falls_back_to_world_news(self):
        self.assertEqual(settings_from(WORLD_NEWS_OLLAMA_HOST="http://world:11434").models.macro_news.ollama_host,
                         "http://world:11434")

    def test_thesis_validator_falls_back_to_the_manager(self):
        self.assertEqual(settings_from(MANAGER_MODEL="manager").models.thesis_validator.model, "manager")

    def test_set_but_empty_variable_still_wins_like_nested_getenv(self):
        self.assertEqual(settings_from(STRATEGIST_MODEL="", MACRO_NEWS_MODEL="macro").models.strategist.model, "")

    def test_misspelled_legacy_names_are_kept(self):
        self.assertEqual(settings_from(SECTOR_OPPURUNTIST_MODEL="sector").models.sector_opportunist.model, "sector")


class LimitTests(unittest.TestCase):
    def test_lower_bounds_are_enforced(self):
        self.assertEqual(settings_from(MANAGER_OPTION_FETCH_MIN="10").manager_context.option_fetch_min, 100)

    def test_long_volatility_window_is_never_shorter_than_short(self):
        context = settings_from(MANAGER_UNDERLYING_HV_SHORT_WINDOW_DAYS="40",
                                MANAGER_UNDERLYING_HV_LONG_WINDOW_DAYS="30").manager_context
        self.assertEqual(context.hv_long_window_days, 40)

    def test_vertex_max_delay_is_never_below_initial_delay(self):
        vertex = settings_from(VERTEX_RETRY_INITIAL_DELAY_SECONDS="10", VERTEX_RETRY_MAX_DELAY_SECONDS="1").vertex
        self.assertEqual(vertex.retry_max_delay_seconds, 10.0)


class LifecycleTests(unittest.TestCase):
    def test_settings_are_read_once_per_process(self):
        self.assertIs(get_settings(), get_settings())

    def test_environment_changes_apply_only_when_settings_are_reloaded(self):
        original = get_settings()
        # use_settings restores the process settings when the block exits.
        with use_settings(original), patch.dict("os.environ", {"STRATEGIST_MODEL": "reloaded"}):
            unchanged = get_settings().models.strategist.model
            reloaded = load_settings().models.strategist.model
        self.assertEqual((unchanged, reloaded), (original.models.strategist.model, "reloaded"))

    def test_use_settings_replaces_a_section_temporarily(self):
        original = get_settings().opportunists
        with use_settings(opportunists=replace(original, company_top_companies_limit=1)):
            inside = get_settings().opportunists.company_top_companies_limit
        self.assertEqual((inside, get_settings().opportunists), (1, original))

    def test_manifest_records_the_loaded_settings(self):
        from services.pipeline_version import build_pipeline_version
        with use_settings(opportunists=replace(get_settings().opportunists, company_top_companies_limit=7)):
            manifest = build_pipeline_version()
        self.assertEqual(manifest["process_settings"]["opportunists"]["company_top_companies_limit"], 7)


if __name__ == "__main__":
    unittest.main()
