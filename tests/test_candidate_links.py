from __future__ import annotations

import unittest

from pipelines.companyNewsPipeline import _company_candidate_links, _company_reuse_filter
from pipelines.industryNewsPipeline import _filter_industry_candidate_links, _listing_topic_links

CNBC_SEARCH = "https://www.cnbc.com/search/?query=x"


def _link(text: str, href: str = "https://www.cnbc.com/2026/01/02/story.html") -> dict:
    return {"href": href, "text": text}


class CompanyCandidateLinkTests(unittest.TestCase):
    def test_search_page_links_pass_through_and_unsupported_page_is_empty(self):
        links = [_link("a"), _link("b", "https://www.cnbc.com/pro/news/x")]
        self.assertEqual(_company_candidate_links({"url": CNBC_SEARCH, "links": links}, {}), links)
        self.assertEqual(_company_candidate_links({"url": "https://example.com/search", "links": links}, {}), [])

    def test_reuse_filter_matches_name_trimmed_name_and_ticker_as_whole_words(self):
        may_reuse = _company_reuse_filter({"name": "Advanced Micro Devices, Inc.", "symbol": "AMD"})
        self.assertTrue(may_reuse(_link("Advanced Micro Devices, Inc. earnings")))
        self.assertTrue(may_reuse(_link("Why Advanced Micro Devices is rising")))
        self.assertTrue(may_reuse(_link("AMD stock jumps")))
        self.assertTrue(may_reuse(_link("chip news", "https://www.cnbc.com/2026/01/02/amd-beats-estimates.html")))

    def test_reuse_filter_rejects_substrings_and_unrelated_links(self):
        may_reuse = _company_reuse_filter({"name": "Advanced Micro Devices, Inc.", "symbol": "AMD"})
        self.assertFalse(may_reuse(_link("Amdocs wins contract", "https://www.cnbc.com/2026/01/02/amdocs-deal.html")))
        self.assertFalse(may_reuse(_link("Trending: Nvidia")))

    def test_reuse_filter_ignores_short_tickers_and_names(self):
        self.assertFalse(_company_reuse_filter({"name": "Inc", "symbol": "F"})(_link("F is for Ford")))
        self.assertFalse(_company_reuse_filter({"name": "X Corp", "symbol": "XC"})(_link("x marks the xc spot")))
        self.assertTrue(_company_reuse_filter({"name": "Ford Motor Company", "symbol": "F"})(_link("Ford Motor shares")))


class IndustryCandidateLinkTests(unittest.TestCase):
    def test_search_keeps_only_variant_matches(self):
        industry = {"name": "Semiconductors", "industry_key": "semiconductors"}
        match = _link("Semiconductors rally")
        other = _link("Oil prices fall", "https://www.cnbc.com/2026/01/02/oil.html")
        self.assertEqual(_filter_industry_candidate_links([match, other], industry), [match])

    def test_listing_keeps_only_topic_matches(self):
        by_text = _link("Semiconductors rally")
        by_href = _link("chips", "https://www.cnbc.com/2026/01/02/semiconductors-surge.html")
        other = _link("Oil prices fall", "https://www.cnbc.com/2026/01/02/oil.html")
        self.assertEqual(_listing_topic_links([by_text, other, by_href], "Semiconductors"), [by_text, by_href])


if __name__ == "__main__":
    unittest.main()
