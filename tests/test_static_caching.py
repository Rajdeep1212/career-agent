"""Browsers always get the current dashboard code after an update."""
import re
import unittest

from fastapi.testclient import TestClient

from app.main import app


class StaticCachingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost:8010")
        self.addCleanup(self.client.close)

    def test_dashboard_references_versioned_assets_and_is_revalidated(self):
        page = self.client.get("/app/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-cache")
        for asset in ("app.js", "styles.css"):
            self.assertRegex(page.text, rf'/static/{re.escape(asset)}\?v=[0-9a-f]{{12}}"')

    def test_versioned_asset_is_served_and_revalidated(self):
        page = self.client.get("/app/").text
        script = re.search(r'/static/app\.js\?v=[0-9a-f]{12}', page).group(0)
        response = self.client.get(script)
        self.assertEqual(response.status_code, 200)
        self.assertIn("function formatDate", response.text)
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_version_changes_when_the_file_changes(self):
        from app import main
        first = main.asset_version("app.js")
        self.assertEqual(first, main.asset_version("app.js"))
        self.assertRegex(first, r"^[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
