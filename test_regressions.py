"""Exercise existing search and CV flows with external HTTP mocked only."""
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

import fitz
import httpx
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage import attachment_store, career_store, history, preference_store, profile_store


class RegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(gc.collect)
        directory = Path(temporary.name)
        for module, name, value in (
            (history, "DB_PATH", directory / "history.sqlite3"),
            (attachment_store, "DB_PATH", directory / "agent.sqlite3"),
            (career_store, "DB_PATH", directory / "career.sqlite3"),
            (attachment_store, "UPLOAD_DIR", directory / "uploads"),
            (profile_store, "PROFILE_PATH", directory / "profile.json"),
            (preference_store, "PREFERENCES_PATH", directory / "preferences.json"),
            (settings, "rapidapi_key", "test-rapidapi-key"),
            (settings, "rapidapi_host", "jsearch.p.rapidapi.com"),
        ):
            p = patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app, base_url="http://localhost:8010")
        self.addCleanup(self.client.close)

    def test_search_pipeline_ranks_and_deduplicates(self):
        def handler(request):
            if request.url.host == "jsearch.p.rapidapi.com":
                self.assertEqual(request.url.path, "/search-v2")
                return httpx.Response(200, json={"data": {"jobs": [{
                    "job_title": "Junior Python Backend Developer", "employer_name": "Example",
                    "job_description": "Fresher Python FastAPI SQL role for recent graduates.",
                    "job_is_remote": True, "job_country": "India",
                    "job_apply_link": "https://example.com/roles/12345",
                }]}})
            return httpx.Response(200, text="<html><title>Junior Python Backend Developer</title><body>Example Job description Python backend. Apply now</body></html>")
        original = httpx.AsyncClient
        def factory(**kwargs):
            return original(transport=httpx.MockTransport(handler), **kwargs)
        page = httpx.Response(200, text="<html><title>Junior Python Backend Developer</title><body>Example Job description Python backend. <a href='/roles/12345/apply'>Apply now</a></body></html>", request=httpx.Request('GET', 'https://example.com/roles/12345'))
        with patch("httpx.AsyncClient", side_effect=factory), patch('app.services.application_verifier.safe_get', new=AsyncMock(return_value=page)):
            first = self.client.post("/agent/search", json={"query": "Python fresher"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["agent_action"], "search_verify_rank")
            self.assertEqual(first.json()["result_count"], 1)
            self.assertGreater(first.json()["results"][0]["total_score"], 0)
            second = self.client.post("/agent/search", json={"query": "Python fresher"})
            self.assertEqual(second.json()["result_count"], 0)
            repeated = self.client.post("/agent/search", json={"query": "Python fresher", "include_seen": True})
            self.assertEqual(repeated.json()["result_count"], 1)

    def test_cv_upload_persists_profile_and_attachment(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "TEST MEMBER\nB.Tech 2025\nPython FastAPI SQL\nInventory Dashboard")
            content = pdf.tobytes()
        response = self.client.post(
            "/cv/upload",
            files={"file": ("resume.pdf", content, "application/pdf")},
            headers={"Origin": "http://localhost:8010"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Python", response.json()["profile"]["skills"])
        self.assertEqual(self.client.get("/profile/current").json()["name"], "Test Member")
        attachment = attachment_store.get_attachment(response.json()["attachment"]["id"])
        self.assertEqual(Path(attachment["stored_path"]).read_bytes(), content)

    def test_cv_parse_does_not_replace_profile(self):
        baseline = self.client.get("/profile/current").json()
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "OTHER MEMBER\nPython 2025")
            content = pdf.tobytes()
        self.assertEqual(self.client.post("/cv/parse", files={"file": ("cv.pdf", content, "application/pdf")}).status_code, 200)
        self.assertEqual(self.client.get("/profile/current").json(), baseline)

    def test_cv_rejects_wrong_file_type(self):
        self.assertEqual(self.client.post(
            "/cv/upload",
            files={"file": ("cv.txt", b"text")},
            headers={"Origin": "http://localhost:8010"},
        ).status_code, 400)

    def test_search_connection_reports_configuration_without_secrets(self):
        response = self.client.get("/connections/search/status")
        self.assertEqual(response.json(), {"configured": True})
        self.assertNotIn("test-rapidapi-key", response.text)
        settings.rapidapi_key = ""
        self.assertEqual(self.client.get("/connections/search/status").json(), {"configured": False})
