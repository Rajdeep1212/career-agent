"""Regression: a 30-job fresher search returned 0 recommendations (23 rejected).

The fixture (tests/fixtures/jobs/fresher_search_30.json) is a synthetic
equivalent of the live search: ambiguous experience wording, 'Bangalore' vs a
saved 'Bengaluru', country-only locations, and a few explicit disqualifiers.
"""
import gc
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import CandidateProfile, JobPosting
from app.providers.base import JobProvider
from app.services.career_agent import CareerAgent
from app.storage import career_store, db, history, preference_store, profile_store

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "jobs" / "fresher_search_30.json"

# Only explicit disqualifiers may exclude: firm minimums well above a fresher's
# range, an explicitly senior title, or a batch list without 2025.
EXPLICITLY_DISQUALIFIED = {
    "Senior Machine Learning Engineer",  # '5+ years experience required'
    "Software Engineer - Backend",       # 'Minimum 3 years of experience'
    "Graduate Engineer Trainee",         # '2023 and 2024 pass-outs only'
    "MLOps Engineer",                    # '3-5 years experience'
    "Lead Data Scientist",               # '8+ years'
    "Data Engineer",                     # '4+ years ... required'
    "Principal Engineer",                # '12+ years'
    "Backend Engineer",                  # 'At least 6 years'
}


class FixtureProvider(JobProvider):
    name = "Fixture"

    def __init__(self):
        self.jobs = [JobPosting.model_validate(item) for item in json.loads(FIXTURE.read_text(encoding="utf-8"))]

    async def search(self, query, page=1):
        return [job.model_copy(deep=True) for job in self.jobs]


async def _unchanged(job):
    return job


class FresherSearchRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(directory.name)
        for module, name, value in ((history, "DB_PATH", root / "history.sqlite3"), (career_store, "DB_PATH", root / "agent.sqlite3"),
                                    (profile_store, "PROFILE_PATH", root / "profile.json"),
                                    (preference_store, "PREFERENCES_PATH", root / "preferences.json")):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CandidateProfile(
            name="Test Student", degree="B.Tech", graduation_year=2025,
            skills=["Python", "SQL", "PyTorch", "LangChain", "RAG", "FastAPI"],
            preferred_locations=["Bengaluru", "Pune"], preferred_roles=["Machine Learning Engineer", "GenAI Engineer"]))

    async def _search(self, query="GenAI engineer fresher"):
        with patch("app.services.career_agent.verify_application", side_effect=_unchanged):
            return await CareerAgent([FixtureProvider()]).search(query, include_seen=True)

    async def test_fresher_search_returns_recommendations(self):
        response = await self._search()
        diagnostics = response["diagnostics"]
        self.assertGreater(response["result_count"], 0)
        self.assertGreaterEqual(response["result_count"], 5)
        self.assertTrue(response["intent"]["locations_from_preferences"])
        self.assertEqual(diagnostics["eligibility_rejected"], len(EXPLICITLY_DISQUALIFIED))

    async def test_only_explicit_disqualifiers_exclude_and_each_quotes_the_listing(self):
        response = await self._search()
        examples = response["diagnostics"]["filtered_examples"]
        self.assertEqual({example["title"] for example in examples}, EXPLICITLY_DISQUALIFIED)
        for example in examples:
            self.assertTrue(example["summary"].startswith("excluded: quoted '"), example)

    async def test_uncertain_results_are_shown_below_eligible_ones(self):
        response = await self._search()
        statuses = [result["eligibility_status"] for result in response["results"]]
        self.assertIn("eligible", statuses)
        self.assertIn("uncertain", statuses)
        self.assertEqual(statuses, sorted(statuses, key=["eligible", "uncertain"].index))
        diagnostics = response["diagnostics"]
        self.assertEqual(diagnostics["eligible_results"] + diagnostics["uncertain_results"], response["result_count"])
        for result in response["results"]:
            self.assertTrue(result["eligibility_summary"].startswith(result["eligibility_status"] + ":"))
        uncertain = next(result for result in response["results"] if result["eligibility_status"] == "uncertain")
        self.assertIn(uncertain["eligibility_summary"], uncertain["next_action"])


if __name__ == "__main__":
    unittest.main()
