"""The process settings object: environment resolution, injection and the manifest record."""
from dataclasses import replace
import unittest

from services.settings import Settings, get_settings, use_settings


def settings_from(**environ):
    return Settings.from_environ(environ)


class ModelResolutionTests(unittest.TestCase):
    def test_stage_setting_wins_over_shared_fallbacks(self):
        models = settings_from(STRATEGIST_MODEL="strategist", MACRO_NEWS_MODEL="macro").models
        self.assertEqual((models.strategist.model, models.manager.model), ("strategist", "macro"))


class LimitTests(unittest.TestCase):
    def test_lower_bounds_are_enforced(self):
        self.assertEqual(settings_from(MANAGER_OPTION_FETCH_MIN="10").manager_context.option_fetch_min, 100)


class LifecycleTests(unittest.TestCase):
    def test_use_settings_replaces_a_section_temporarily(self):
        original = get_settings().opportunists
        with use_settings(opportunists=replace(original, company_top_companies_limit=1)):
            inside = get_settings().opportunists.company_top_companies_limit
        self.assertEqual((inside, get_settings().opportunists), (1, original))


if __name__ == "__main__":
    unittest.main()
