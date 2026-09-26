"""The 'Save to Career Agent' bookmarklet: URL and title only, a local form, then the usual pipeline."""
import gc
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.schemas import CandidateProfile
from app.providers.alerts_provider import AlertsProvider
from app.models.career import SearchQuery
from app.sources.adapters import posting
from app.storage import alert_store, career_store, db, preference_store, profile_store, radar_store
from radar_helpers import company

LOCAL = {"Origin": "http://localhost:8010"}
JOB = {"url": "https://careers.example.com/jobs/ml-engineer-123?utm_source=x", "title": "Machine Learning Engineer",
       "company": "Example Analytics", "location": "Bengaluru, India",
       "description": "Freshers welcome. Python and PyTorch. 0-1 years of experience."}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(directory.name)
        for module, name, value in ((alert_store, "DB_PATH", root / "alerts.sqlite3"), (radar_store, "DB_PATH", root / "radar.sqlite3"),
                                    (career_store, "DB_PATH", root / "agent.sqlite3"),
                                    (profile_store, "PROFILE_PATH", root / "profile.json"),
                                    (preference_store, "PREFERENCES_PATH", root / "preferences.json")):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CandidateProfile(graduation_year=2025, experience_years=0, skills=["Python", "PyTorch"],
                                                    preferred_roles=["Machine Learning Engineer"]))
        self.client = TestClient(app, base_url="http://localhost:8010")
        self.addCleanup(self.client.close)

    def test_bookmarklet_sends_only_the_url_and_title(self):
        code = self.client.get("/capture/bookmarklet").json()["bookmarklet"]
        self.assertTrue(code.startswith("javascript:"))
        self.assertIn(settings.app_origin + "/capture?url=", code)
        self.assertIn("location.href", code)
        self.assertIn("document.title", code)
        for scraping in ("querySelector", "innerText", "innerHTML", "document.body", "getElementsBy", "fetch("):
            self.assertNotIn(scraping, code)

    def test_capture_page_is_served_with_its_script(self):
        page = self.client.get("/capture?url=https%3A%2F%2Fexample.com&title=X")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-cache")
        self.assertRegex(page.text, r'/static/capture\.js\?v=[0-9a-f]{12}"')

    def test_saving_needs_the_local_origin(self):
        self.assertEqual(self.client.post("/capture", json=JOB).status_code, 403)
        self.assertEqual(self.client.post("/capture", json=JOB, headers={"Origin": "https://attacker.example"}).status_code, 403)
        self.assertFalse(alert_store.DB_PATH.exists())

    def test_saved_job_goes_through_eligibility_and_matching(self):
        response = self.client.post("/capture", json=JOB, headers=LOCAL)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "Saved by you")
        self.assertEqual(body["eligibility_status"], "eligible")
        self.assertTrue(body["eligibility_summary"].startswith("eligible: quoted"))
        self.assertGreater(body["total_score"], 0)
        self.assertFalse(body["matched_official"])
        self.assertEqual(body["application_url"], "https://careers.example.com/jobs/ml-engineer-123")   # tracking removed
        self.assertIsNotNone(career_store.get_job(body["id"]))   # can be saved to the tracker
        found = AlertsProvider().search_local([SearchQuery(query="q", reason="", role="Machine Learning Engineer", location="")])
        self.assertEqual([job.source for job in found], ["Saved by you"])

    def test_saving_again_updates_instead_of_duplicating(self):
        self.client.post("/capture", json=JOB, headers=LOCAL)
        again = self.client.post("/capture", json={**JOB, "url": "https://careers.example.com/jobs/ml-engineer-123"}, headers=LOCAL)
        self.assertTrue(again.json()["already_saved"])
        self.assertEqual(len(alert_store.list_jobs()), 1)

    def test_linkedin_link_is_canonical_and_never_fetched(self):
        with patch("app.services.application_verifier.safe_get", side_effect=AssertionError("fetched")):
            body = self.client.post("/capture", headers=LOCAL, json={
                **JOB, "url": "https://www.linkedin.com/jobs/view/4012345678/?refId=abc&trackingId=x"}).json()
        self.assertEqual(body["application_url"], "https://www.linkedin.com/jobs/view/4012345678/")
        self.assertEqual(alert_store.rows()[0]["key"], "linkedin:4012345678")

    def test_saved_job_matching_an_official_radar_job_is_linked(self):
        entry = company({"type": "greenhouse", "board": "example"}, id="example-analytics", name="Example Analytics")
        radar_store.record_listing(entry.id, [posting(entry, job_id="7001", title="Machine Learning Engineer", location="Bengaluru, India",
                                                      description="Freshers welcome.", url="https://job-boards.greenhouse.io/example/jobs/7001")],
                                   complete=True, today=date(2026, 9, 25))
        body = self.client.post("/capture", json=JOB, headers=LOCAL).json()
        self.assertTrue(body["matched_official"])
        self.assertEqual(body["source"], "Company Radar")
        self.assertEqual(body["application_url"], "https://job-boards.greenhouse.io/example/jobs/7001")

    def test_invalid_input_is_rejected(self):
        for change in ({"url": "javascript:alert(1)"}, {"url": "https://user:pw@example.com/job"}, {"url": "ftp://example.com/x"},
                       {"title": ""}, {"company": ""}, {"description": "x" * 20001}, {"unexpected": 1}):
            with self.subTest(change=change):
                self.assertEqual(self.client.post("/capture", json={**JOB, **change}, headers=LOCAL).status_code, 422)


if __name__ == "__main__":
    unittest.main()
