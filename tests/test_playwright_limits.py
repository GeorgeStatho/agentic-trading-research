from __future__ import annotations

import asyncio
import inspect
import os
import unittest
from unittest.mock import MagicMock, patch

from engine import page_fetch, playwright_runner
from engine.article_extraction import ArticleExtractionResult
from pipelines import _article_follow
from pipelines._article_follow import NewsEntity, save_link_verdicts
from pipelines._link_policy import LinkVerdict

CONCURRENCY_ENV = "WEBSCRAPING_PLAYWRIGHT_CONCURRENCY"
MAX_PAGES_ENV = "WEBSCRAPING_PLAYWRIGHT_MAX_PAGES"
HTML = "<html><body><p>hello</p></body></html>"


class _FakeResponse:
    status = 200


class _FakeBrowserState:
    """Records what the runner did to the fake browser, with no real Chromium involved."""

    def __init__(self) -> None:
        self.routes: list[tuple[object, object]] = []
        self.goto_urls: list[str] = []
        self.open_pages = 0
        self.peak_pages = 0
        self.closed = False


class _FakePage:
    def __init__(self, state: _FakeBrowserState) -> None:
        self._state = state
        self.url = ""
        state.open_pages += 1
        state.peak_pages = max(state.peak_pages, state.open_pages)

    async def goto(self, url, **kwargs):
        self._state.goto_urls.append(url)
        self.url = url
        await asyncio.sleep(0.01)  # let other tabs start so overlap is visible
        return _FakeResponse()

    async def wait_for_load_state(self, *args, **kwargs):
        return None

    async def wait_for_selector(self, *args, **kwargs):
        return None

    async def evaluate(self, *args, **kwargs):
        return None

    async def content(self):
        return HTML

    async def close(self):
        self._state.open_pages -= 1


class _FakeContext:
    def __init__(self, state: _FakeBrowserState) -> None:
        self._state = state

    async def route(self, pattern, handler):
        self._state.routes.append((pattern, handler))

    async def new_page(self):
        return _FakePage(self._state)

    async def close(self):
        return None


class _FakeBrowser:
    def __init__(self, state: _FakeBrowserState) -> None:
        self._state = state

    async def new_context(self, **kwargs):
        return _FakeContext(self._state)

    async def close(self):
        self._state.closed = True


class _FakePlaywright:
    def __init__(self, state: _FakeBrowserState) -> None:
        self.chromium = self
        self._state = state

    async def launch(self, **kwargs):
        return _FakeBrowser(self._state)


class _FakeAsyncPlaywright:
    def __init__(self, state: _FakeBrowserState) -> None:
        self._state = state

    def __call__(self):
        return self

    async def __aenter__(self):
        return _FakePlaywright(self._state)

    async def __aexit__(self, *exc):
        return False


class _FakeRoute:
    def __init__(self, resource_type: str) -> None:
        self.request = type("Request", (), {"resource_type": resource_type})()
        self.aborted = False
        self.continued = False

    async def abort(self, *args, **kwargs):
        self.aborted = True

    async def continue_(self, *args, **kwargs):
        self.continued = True


def _urls(count: int) -> list[str]:
    return [f"https://www.barrons.com/articles/story-{i}" for i in range(count)]


class _PlaywrightFakeCase(unittest.TestCase):
    def setUp(self):
        self.state = _FakeBrowserState()
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(CONCURRENCY_ENV, None)
        os.environ.pop(MAX_PAGES_ENV, None)
        fake = patch.object(playwright_runner, "_get_async_playwright", return_value=_FakeAsyncPlaywright(self.state))
        fake.start()
        self.addCleanup(fake.stop)

    def render(self, urls: list[str]) -> list[dict]:
        return playwright_runner.fetch_rendered_pages(urls)


class ResourceBlockingTests(_PlaywrightFakeCase):
    def _handler(self):
        self.state.routes.clear()  # subtests call this repeatedly on one fake browser
        self.render(_urls(1))
        self.assertEqual(len(self.state.routes), 1, "expected one context-level route to be registered")
        return self.state.routes[0][1]

    def _run(self, resource_type: str) -> _FakeRoute:
        handler = self._handler()
        route = _FakeRoute(resource_type)
        result = handler(route)
        if inspect.isawaitable(result):
            asyncio.run(result)
        return route

    def test_blocked_resource_types_constant(self):
        self.assertEqual(getattr(playwright_runner, "BLOCKED_RESOURCE_TYPES", None), ("image", "media", "font"))

    def test_heavy_resources_are_aborted(self):
        for resource_type in ("image", "media", "font"):
            with self.subTest(resource_type=resource_type):
                route = self._run(resource_type)
                self.assertTrue(route.aborted)
                self.assertFalse(route.continued)

    def test_page_critical_resources_continue(self):
        for resource_type in ("document", "script", "stylesheet", "xhr"):
            with self.subTest(resource_type=resource_type):
                route = self._run(resource_type)
                self.assertTrue(route.continued)
                self.assertFalse(route.aborted)


class ConcurrencyTests(_PlaywrightFakeCase):
    def test_default_concurrency_is_two(self):
        self.assertEqual(playwright_runner.DEFAULT_PLAYWRIGHT_CONCURRENCY, 2)
        self.render(_urls(6))
        self.assertEqual(len(self.state.goto_urls), 6)
        self.assertEqual(self.state.peak_pages, 2)

    def test_env_override_is_honoured(self):
        os.environ[CONCURRENCY_ENV] = "3"
        self.render(_urls(6))
        self.assertEqual(self.state.peak_pages, 3)


class PageCapTests(_PlaywrightFakeCase):
    def test_default_cap_renders_first_thirty_in_input_order(self):
        urls = _urls(35)
        result = self.render(urls)
        self.assertEqual(sorted(self.state.goto_urls), sorted(urls[:30]))
        self.assertEqual([page["request_url"] for page in result], urls[:30])

    def test_deferred_urls_are_absent_from_result(self):
        urls = _urls(35)
        result = self.render(urls)
        self.assertEqual(len(result), 30)
        self.assertFalse({page["request_url"] for page in result} & set(urls[30:]))
        self.assertFalse(set(self.state.goto_urls) & set(urls[30:]))

    def test_env_override(self):
        os.environ[MAX_PAGES_ENV] = "2"
        urls = _urls(5)
        result = self.render(urls)
        self.assertEqual(len(self.state.goto_urls), 2)
        self.assertEqual([page["request_url"] for page in result], urls[:2])

    def test_invalid_env_falls_back_to_default(self):
        for raw in ("abc", ""):
            with self.subTest(raw=raw):
                os.environ[MAX_PAGES_ENV] = raw
                self.state.goto_urls.clear()
                self.render(_urls(35))
                self.assertEqual(len(self.state.goto_urls), 30)

    def test_zero_or_negative_env_means_one_page(self):
        for raw in ("0", "-4"):
            with self.subTest(raw=raw):
                os.environ[MAX_PAGES_ENV] = raw
                self.state.goto_urls.clear()
                self.render(_urls(5))
                self.assertEqual(len(self.state.goto_urls), 1)

    def test_duplicates_count_once_toward_the_cap(self):
        os.environ[MAX_PAGES_ENV] = "2"
        a, b, c = _urls(3)
        result = self.render([a, a, b, c])
        self.assertEqual(sorted(self.state.goto_urls), sorted([a, b]))
        self.assertEqual([page["request_url"] for page in result], [a, b])

    def test_deferred_count_is_logged_once(self):
        with patch.object(playwright_runner.LOGGER, "info") as info:
            self.render(_urls(35))
        lines = [str(call.args[0]) % call.args[1:] for call in info.call_args_list if call.args]
        deferred = [line for line in lines if "defer" in line.lower()]
        self.assertEqual(len(deferred), 1)
        self.assertIn("5", deferred[0])


class DeferredUrlIsMissingNotFailedTests(_PlaywrightFakeCase):
    def test_deferred_article_is_not_recorded_as_failed(self):
        os.environ["WEBSCRAPING_ARTICLE_BACKEND"] = "playwright"
        os.environ[MAX_PAGES_ENV] = "1"
        rendered_url, deferred_url = _urls(2)

        fetched = page_fetch.LiveFetcher().articles([rendered_url, deferred_url])

        # Only the first URL was rendered; the second is simply absent from the result.
        self.assertEqual(self.state.goto_urls, [rendered_url])
        self.assertIn(rendered_url, fetched)
        self.assertNotIn(deferred_url, fetched)

        entity = NewsEntity(kind="us", id=1, label="us", identity={}, link=lambda **kw: 0)
        verdict = LinkVerdict(link={}, href=deferred_url, normalized_url=deferred_url, action="fetch")
        with patch.object(_article_follow, "record_failed_url") as record_failed:
            saved = save_link_verdicts(
                source_page_url="https://www.barrons.com/",
                verdicts=[verdict],
                entity=entity,
                fetched_articles=fetched,
                logger=MagicMock(),
                outcomes={},
            )
        record_failed.assert_not_called()
        self.assertEqual(saved, 0)
        self.assertIsInstance(fetched[rendered_url], ArticleExtractionResult)


if __name__ == "__main__":
    unittest.main()
