"""RadarProvider: a fresher search served from the local index with zero provider requests."""
import gc
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.models.career import SearchQuery
from app.models.schemas import CandidateProfile
from app.providers import registry
from app.providers.radar_provider import RadarProvider
from app.services.career_agent import CareerAgent
from app.sources.adapters import RADAR_SOURCE, posting
from app.storage import career_store, db, history, preference_store, profile_store, radar_store
from radar_helpers import company

TODAY = date(2026, 9, 26)
TITLES = ["Machine Learning Engineer", "GenAI Engineer", "AI Engineer - LLM Applications", "Associate Machine Learning Engineer",
          "Graduate GenAI Engineer", "Machine Learning Engineer I"]
CITIES = ["Bengaluru, Karnataka, India", "Pune, India", "Bangalore, India"]
FRESHER = ("Freshers welcome: 0-1 years of experience, 2025 graduates eligible. B.Tech in CS. "
           "You will build RAG pipelines with Python, PyTorch, LangChain and FastAPI, and write SQL.")


class FailingProvider:
    """A remote provider that must never be called by these tests."""
    name = "Remote"
    search = AsyncMock(side_effect=AssertionError("remote provider was called"))
    search_planned = AsyncMock(side_effect=AssertionError("remote provider was called"))


def _fill_index(count=60):
    for index in range(count):
        entry = company({"type": "greenhouse", "board": f"board{index % 6}"}, id=f"co{index % 6}", name=f"Company {index % 6}")
        job = posting(entry, job_id=f"j{index}", title=TITLES[index % len(TITLES)], location=CITIES[index % len(CITIES)],
                      description=FRESHER, url=f"https://job-boards.greenhouse.io/board{index % 6}/jobs/{index}",
                      posted=f"2026-09-{10 + index % 15:02d}")
        radar_store.record_listing(entry.id, [job], complete=False, today=TODAY)
    noise = company({"type": "greenhouse", "board": "noise"}, id="noise")
    radar_store.record_listing("noise", [
        posting(noise, job_id="n1", title="Accountant", location="Bengaluru, India", description=FRESHER,
                url="https://job-boards.greenhouse.io/noise/jobs/1"),
        posting(noise, job_id="n2", title="Machine Learning Engineer", location="Chennai, India", description=FRESHER,
                url="https://job-boards.greenhouse.io/noise/jobs/2")], complete=True, today=TODAY)


class RadarProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(directory.name)
        for module, name, value in ((history, "DB_PATH", root / "history.sqlite3"), (career_store, "DB_PATH", root / "agent.sqlite3"),
                                    (profile_store, "PROFILE_PATH", root / "profile.json"),
                                    (preference_store, "PREFERENCES_PATH", root / "preferences.json"),
                                    (radar_store, "DB_PATH", root / "radar.sqlite3")):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CandidateProfile(
            name="Test Student", degree="B.Tech", graduation_year=2025,
            skills=["Python", "SQL", "PyTorch", "LangChain", "RAG", "FastAPI"],
            preferred_locations=["Bengaluru", "Pune"], preferred_roles=["Machine Learning Engineer", "GenAI Engineer"]))

    def test_configured_only_once_the_index_has_jobs(self):
        self.assertFalse(RadarProvider.configured())
        self.assertFalse(radar_store.DB_PATH.exists())   # checking never creates the database
        _fill_index(1)
        self.assertTrue(RadarProvider.configured())

    def test_registry_lists_radar_first_when_configured(self):
        _fill_index(1)
        with patch.object(registry.settings, "job_providers", "radar,jsearch"), patch.object(registry.settings, "rapidapi_key", "k"):
            self.assertEqual([provider.name for provider in registry.get_providers()], [RADAR_SOURCE, "JSearch/RapidAPI"])

    def test_role_family_and_city_matching(self):
        _fill_index(6)
        found = RadarProvider().search_local([SearchQuery(query="q", reason="", role="Machine Learning Engineer", location="Bengaluru")])
        self.assertTrue(found)
        self.assertTrue(all("Bengaluru" in job.location or "Bangalore" in job.location for job in found))
        self.assertNotIn("Accountant", [job.title for job in found])
        anywhere = RadarProvider().search_local([SearchQuery(query="q", reason="", role="Machine Learning Engineer", location="India")])
        self.assertIn("Chennai, India", [job.location for job in anywhere])

    async def test_fresher_search_is_served_from_the_index_without_requests(self):
        _fill_index(60)
        verifier = AsyncMock(side_effect=AssertionError("radar jobs must not be re-verified"))
        remote = FailingProvider()
        agent = CareerAgent([RadarProvider()])
        plan = agent.plan("GenAI engineer fresher")
        self.assertEqual(plan["provider_requests"], 0)
        with patch("app.services.career_agent.verify_application", verifier):
            response = await agent.search("GenAI engineer fresher", include_seen=True)
        diagnostics = response["diagnostics"]
        self.assertEqual(diagnostics["provider_requests"], 0)
        self.assertGreaterEqual(diagnostics["provider_counts"][RADAR_SOURCE], 50)
        self.assertGreaterEqual(response["result_count"], 50)
        self.assertTrue(all(result["source"] == RADAR_SOURCE for result in response["results"]))
        self.assertTrue(all(result["verification_state"] == "ACTIVE_VERIFIED" for result in response["results"]))
        self.assertTrue(all(result["official_application"] for result in response["results"]))
        self.assertEqual(diagnostics["verification_attempted"], 0)
        self.assertEqual(diagnostics["source_verified"], diagnostics["unique_jobs"])
        self.assertNotIn("Accountant", [result["title"] for result in response["results"]])
        remote.search_planned.assert_not_called()


if __name__ == "__main__":
    unittest.main()
