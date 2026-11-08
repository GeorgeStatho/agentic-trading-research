"""The dashboard's nginx compresses text responses and lets browsers keep hashed assets."""
import re
import unittest

from tests import ROOT_DIR


NGINX_CONF = ROOT_DIR / "web_dashboard" / "nginx.conf"
CONFIG = NGINX_CONF.read_text(encoding="utf-8") if NGINX_CONF.exists() else ""


def _location(pattern: str) -> str:
    match = re.search(r"location " + pattern + r" \{(.*?)\n  \}", CONFIG, re.S)
    return match.group(1) if match else ""


@unittest.skipUnless(NGINX_CONF.exists(), "the worker image leaves out web_dashboard/")
class NginxConfigTests(unittest.TestCase):
    def test_text_responses_are_gzipped(self):
        self.assertIn("gzip on;", CONFIG)
        gzip_types = re.search(r"gzip_types ([^;]+);", CONFIG)
        self.assertIsNotNone(gzip_types)
        for media_type in ("application/javascript", "text/css", "application/json", "image/svg+xml"):
            self.assertIn(media_type, gzip_types.group(1))
        # API responses arrive through proxy_pass; without this nginx leaves them uncompressed.
        self.assertIn("gzip_proxied any;", CONFIG)

    def test_hashed_assets_are_cached_for_good(self):
        assets = _location("/assets/")
        self.assertIn('Cache-Control "public, max-age=31536000, immutable"', assets)
        # The block replaces "location /", so it must keep that block's limits.
        self.assertIn("limit_req zone=page_limit", assets)
        self.assertIn("limit_except GET HEAD { deny all; }", assets)

    def test_index_html_is_revalidated(self):
        self.assertIn('Cache-Control "no-cache"', _location("= /index.html"))


if __name__ == "__main__":
    unittest.main()
