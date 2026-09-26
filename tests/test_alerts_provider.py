"""Alert-email jobs in searches: matched ones become official Radar jobs; the rest are shown, honestly unverified."""
import gc
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.models.schemas import CandidateProfile
from app.providers.alerts_provider import AlertsProvider
from app.providers.radar_provider import RadarProvider
from app.services import application_verifier
from app.services.career_agent import CareerAgent
from app.sources.adapters import posting
from app.sources.alerts.ingest import ingest_raw
from app.storage import alert_store, career_store, db, history, preference_store, profile_store, radar_store
from radar_helpers import company
from test_search_cache import CountingProvider

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "alerts"


class AlertsProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(directory.name)
        for module, name, value in ((history, "DB_PATH", root / "history.sqlite3"), (career_store, "DB_PATH", root / "agent.sqlite3"),
                                    (profile_store, "PROFILE_PATH", root / "profile.json"),
                                    (preference_store, "PREFERENCES_PATH", root / "preferences.json"),
                                    (radar_store, "DB_PATH", root / "radar.sqlite3"),
                                    (alert_store, "DB_PATH", root / "alerts.sqlite3")):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        # No page may be fetched in these tests (LinkedIn links are never opened).
        patcher = patch.object(application_verifier, "safe_get", AsyncMock(side_effect=AssertionError("page fetched")))
        patcher.start()
        self.addCleanup(patcher.stop)
        profile_store.save_profile(CandidateProfile(name="Test Student", graduation_year=2025, experience_years=0,
                                                    skills=["Python", "PyTorch"], preferred_roles=["Machine Learning Engineer"]))

    def _index_official_ml_job(self):
        entry = company({"type": "greenhouse", "board": "example"}, id="example-analytics", name="Example Analytics")
        radar_store.record_listing(entry.id, [posting(entry, job_id="7001", title="Machine Learning Engineer",
                                                      location="Bengaluru, Karnataka, India", description="Freshers welcome.",
                                                      url="https://job-boards.greenhouse.io/example/jobs/7001")],
                                   complete=True, today=date(2026, 9, 25))

    def test_configured_only_with_stored_jobs(self):
        self.assertFalse(AlertsProvider.configured())
        self.assertFalse(alert_store.DB_PATH.exists())
        ingest_raw([("dropbox", (FIXTURES / "linkedin_alert.eml").read_bytes())])
        self.assertTrue(AlertsProvider.configured())

    async def test_matched_alert_jobs_become_official_and_the_rest_are_labeled_unverified(self):
        self._index_official_ml_job()
        ingest_raw([("dropbox", (FIXTURES / "linkedin_alert.eml").read_bytes())])
        response = await CareerAgent([RadarProvider(), AlertsProvider()]).search("machine learning engineer", include_seen=True)
        results = {result["title"]: result for result in response["results"]}
        official = results["Machine Learning Engineer"]
        self.assertEqual(official["source"], "Company Radar")
        self.assertEqual(official["verification_state"], "ACTIVE_VERIFIED")
        self.assertEqual([ref["source"] for ref in official["sources"]], ["LinkedIn alert"])
        self.assertEqual(response["diagnostics"]["provider_requests"], 0)
        alert_only = [result for result in response["results"] if result["source"] == "LinkedIn alert"]
        self.assertTrue(alert_only)
        for result in alert_only:
            self.assertEqual(result["verification_state"], "UNVERIFIED")
            self.assertIn("never opened automatically", result["verification_reason"])
            self.assertIn(result["eligibility_status"], ("eligible", "uncertain"))

    async def test_alert_jobs_are_not_limited_by_the_verification_budget(self):
        ingest_raw([("dropbox", (FIXTURES / "linkedin_alert.eml").read_bytes())])
        preference_store.save_preferences(preference_store.preferences_for(profile_store.load_profile()).model_copy(
            update={"verification_limit": 1}))
        response = await CareerAgent([AlertsProvider()]).search("find ai ml jobs", include_seen=True)
        reasons = {result["verification_reason"] for result in response["results"]}
        self.assertNotIn("Not checked: this search reached the configured verification budget.", reasons)

    async def test_alert_jobs_do_not_turn_off_the_empty_index_fallback(self):
        ingest_raw([("dropbox", (FIXTURES / "linkedin_alert.eml").read_bytes())])
        remote = CountingProvider()
        await CareerAgent([RadarProvider(), AlertsProvider(), remote]).search("machine learning engineer", include_seen=True)
        self.assertGreater(remote.calls, 0)


if __name__ == "__main__":
    unittest.main()
