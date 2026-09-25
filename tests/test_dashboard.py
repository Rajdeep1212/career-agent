"""The dashboard and basic read endpoints load with the labeled demo profile."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.storage import profile_store


class DashboardSmokeTests(unittest.TestCase):
    def test_dashboard_and_read_endpoints(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(profile_store, "PROFILE_PATH", Path(directory) / "missing.json"), \
             TestClient(app) as client:
            self.assertIn(client.get("/", follow_redirects=False).status_code, (200, 307))
            page = client.get("/app/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Job Agent", page.text)
            profile = client.get("/profile/current").json()
            self.assertIn("demo", profile["name"].lower())
            self.assertEqual(profile["graduation_year"], 2025)
            self.assertIn("Python", profile["skills"])
            self.assertEqual(client.get("/auth/google/status").status_code, 200)


if __name__ == "__main__":
    unittest.main()
