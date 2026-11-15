import unittest
from unittest.mock import MagicMock, patch

import requests
from fastapi import HTTPException

from APIs import foolAPI


def _response(status, url="https://www.fool.com/quote/nasdaq/nvda/"):
    return MagicMock(status_code=status, url=url)


class FoolQuoteLookupTests(unittest.TestCase):
    def resolve(self, side_effect):
        with patch.object(foolAPI.session, "get", side_effect=side_effect) as get:
            with self.assertRaises(HTTPException) as raised:
                foolAPI._resolve_quote_url("NVDA", {})
        return raised.exception, get

    def test_rate_limit_stops_at_the_first_exchange_and_says_so(self):
        error, get = self.resolve([_response(429)] * len(foolAPI.COMMON_EXCHANGES))
        self.assertEqual(error.status_code, 429)
        self.assertIn("HTTP 429", error.detail)
        get.assert_called_once()

    def test_not_found_everywhere_is_a_404_naming_each_exchange(self):
        error, _ = self.resolve([_response(404)] * len(foolAPI.COMMON_EXCHANGES))
        self.assertEqual(error.status_code, 404)
        self.assertIn("nasdaq: HTTP 404", error.detail)

    def test_a_transport_error_is_reported_instead_of_a_404(self):
        outcomes = [requests.ConnectionError("dns down")] + [_response(404)] * (len(foolAPI.COMMON_EXCHANGES) - 1)
        error, _ = self.resolve(outcomes)
        self.assertEqual(error.status_code, 502)
        self.assertIn("ConnectionError: dns down", error.detail)

    def test_found_quote_page_is_returned(self):
        with patch.object(foolAPI.session, "get", return_value=_response(200)):
            self.assertEqual(foolAPI._resolve_quote_url("NVDA", {}), "https://www.fool.com/quote/nasdaq/nvda/")


if __name__ == "__main__":
    unittest.main()
