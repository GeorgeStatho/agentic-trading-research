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


@unittest.skipUnless(NGINX_CONF.exists(), "the worker image leaves out web_dashboard/")
class DashboardDomainTests(unittest.TestCase):
    """The domain comes from DASHBOARD_DOMAIN, which nginx's image substitutes into its templates at start."""

    def test_configs_take_the_domain_from_the_environment(self):
        for name in ("nginx.conf", "nginx.bootstrap.conf"):
            config = (ROOT_DIR / "web_dashboard" / name).read_text(encoding="utf-8")
            names = re.findall(r"server_name ([^;]+);", config)
            self.assertTrue(names, name)
            self.assertEqual({"${DASHBOARD_DOMAIN}"}, set(names), name)
        self.assertIn("/etc/letsencrypt/live/${DASHBOARD_DOMAIN}/fullchain.pem", CONFIG)

    def test_every_config_is_mounted_as_a_template(self):
        template = "/etc/nginx/templates/default.conf.template"
        dockerfile = (ROOT_DIR / "web_dashboard" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(f"COPY nginx.conf {template}", dockerfile)
        for overlay in ("docker-compose.bootstrap.yml", "docker-compose.tunnel.yml", "docker-compose.local.yml"):
            path = ROOT_DIR / overlay
            if not path.exists():  # docker-compose.local.yml is gitignored, so a fresh clone has none.
                continue
            text = path.read_text(encoding="utf-8")
            # A read-only mount over conf.d/default.conf would make the template step fail at start.
            self.assertIn(f"nginx.bootstrap.conf:{template}:ro", text, overlay)
            self.assertNotIn("/etc/nginx/conf.d/default.conf", text, overlay)

    def test_compose_passes_the_domain_to_the_web_container(self):
        compose = (ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8")
        web = compose.split("\n  web:\n", 1)[1].split("\n  certbot:", 1)[0]
        self.assertIn("DASHBOARD_DOMAIN: ${DASHBOARD_DOMAIN:-localhost}", web)


if __name__ == "__main__":
    unittest.main()
