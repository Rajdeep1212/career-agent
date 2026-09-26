"""Connections shows each provider's quota use, e.g. "JSearch 37/200 this month"."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app.core.config import settings
from app.providers import jsearch_provider, registry
from app.providers.adzuna_provider import AdzunaProvider
from app.providers.jooble_provider import JoobleProvider
from app.providers.jsearch_provider import JSearchProvider
from app.storage import provider_usage


class UsageDisplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for target, values in ((provider_usage, {"USAGE_PATH": Path(directory.name) / "usage.json"}),
                               (jsearch_provider, {"_last_quota": None}),
                               (settings, {"rapidapi_key": "RAPID-SECRET", "adzuna_app_id": "id", "adzuna_app_key": "ADZ-SECRET",
                                           "jooble_api_key": "JOO-SECRET", "job_providers": "jsearch,adzuna,jooble",
                                           "jsearch_monthly_limit": 200})):
            patcher = patch.multiple(target, **values)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_jsearch_requests_are_counted_and_shown(self):
        original = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": {"jobs": []}}))
        with patch("httpx.AsyncClient", side_effect=lambda **kw: original(transport=transport, **kw)):
            for _ in range(3):
                await JSearchProvider().search("python developer")
        self.assertEqual(JSearchProvider().usage_text(), "3/200 this month (counted on this machine)")
        quota = JSearchProvider().quota()
        self.assertEqual((quota["remaining"], quota["limit"], quota["counted_locally"]), (197, 200, True))

    def test_rapidapi_headers_take_precedence(self):
        jsearch_provider._last_quota = {"remaining": 163, "limit": 200, "reset_at": None,
                                        "observed_at": datetime.now(timezone.utc).isoformat()}
        self.assertEqual(JSearchProvider().usage_text(), "37/200 this month (reported by RapidAPI)")

    def test_adzuna_and_jooble_usage(self):
        provider_usage.record("adzuna", "ADZ-SECRET", count=12)
        provider_usage.record("jooble", "JOO-SECRET", count=40)
        self.assertEqual(AdzunaProvider().usage_text(), "12/250 today, 12/2500 this month (counted on this machine)")
        self.assertEqual(JoobleProvider().usage_text(), "40/500 for this key (counted on this machine)")

    def test_connections_status_includes_usage_without_secrets(self):
        provider_usage.record("jooble", "JOO-SECRET", count=2)
        status = {item["id"]: item for item in registry.provider_status() if item["installed"]}
        self.assertEqual(status["jooble"]["usage"], "2/500 for this key (counted on this machine)")
        self.assertIn("usage", status["jsearch"])
        self.assertNotIn("SECRET", str(status))


if __name__ == "__main__":
    unittest.main()
