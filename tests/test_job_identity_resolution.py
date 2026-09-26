"""Cross-source identity: aggregator copies fold into the official Company Radar record."""
import gc
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import CandidateProfile, JobPosting
from app.providers.base import JobProvider
from app.providers.radar_provider import RadarProvider
from app.services.career_agent import CareerAgent
from app.services.job_identity import city_key, company_key, resolve_with_index, same_job, title_key
from app.sources.adapters import RADAR_SOURCE, posting
from app.sources.registry import alias_map, load_seed
from app.storage import career_store, db, history, preference_store, profile_store, radar_store
from radar_helpers import company

D1 = date(2026, 9, 26)
ENTRY = company({"type": "greenhouse", "board": "databricks"}, id="databricks", name="Databricks", aliases=["Databricks Inc"])
OFFICIAL = posting(ENTRY, job_id="7001", title="Software Engineer - GenAI (New Grad)", location="Bengaluru, Karnataka, India",
                   description="2025 graduates. Python, RAG.", url="https://job-boards.greenhouse.io/databricks/jobs/7001")
ALIASES = {"databricks": "databricks"}


def aggregator(title="Software Engineer, GenAI (New Grad)", company_name="Databricks India Pvt. Ltd.", location="Bangalore, IN",
               source="JSearch/RapidAPI", url="https://www.linkedin.com/jobs/view/123", job_id="js-1"):
    return JobPosting(company=company_name, title=title, location=location, source=source, application_url=url,
                      source_job_id=job_id)


def resolve(jobs, index=None):
    return resolve_with_index(jobs, index if index is not None else [("databricks:7001", OFFICIAL)], ALIASES)


class NormalizationTests(unittest.TestCase):
    def test_company_title_and_city_keys(self):
        self.assertEqual(company_key("Databricks India Pvt. Ltd."), "databricks")
        self.assertEqual(company_key("Procter & Gamble"), "procter and gamble")
        self.assertEqual(company_key("Group"), "group")   # never reduced to nothing
        self.assertEqual(title_key("Software Engineer - GenAI (New Grad)"), "software engineer genai new grad")
        self.assertEqual(city_key("Bangalore, IN"), "bengaluru")
        self.assertEqual(city_key("Gurgaon / Noida, India"), "gurugram")
        self.assertIsNone(city_key("India"))

    def test_same_job_needs_matching_city_and_similar_title(self):
        self.assertTrue(same_job(aggregator(), OFFICIAL))
        self.assertFalse(same_job(aggregator(location="Pune, India"), OFFICIAL))
        self.assertFalse(same_job(aggregator(title="Senior Staff Engineer"), OFFICIAL))
        # With no city, only an exact (normalized) title matches.
        self.assertFalse(same_job(aggregator(title="Software Engineer GenAI New Graduate", location="India"), OFFICIAL))
        self.assertTrue(same_job(aggregator(title="Software Engineer GenAI New Graduate"), OFFICIAL))
        self.assertTrue(same_job(aggregator(title="Software Engineer - GenAI (New Grad)", location="India"), OFFICIAL))

    def test_seed_aliases_map_to_the_canonical_company(self):
        aliases = alias_map(load_seed())
        self.assertEqual(aliases[company_key("Procter and Gamble")], company_key("P&G"))


class ResolutionTests(unittest.TestCase):
    def test_aggregator_copy_becomes_the_official_job_with_sources(self):
        jobs, matches = resolve([aggregator()])
        self.assertEqual(len(jobs), 1)
        merged = jobs[0]
        self.assertEqual((merged.source, str(merged.application_url)), (RADAR_SOURCE, "https://job-boards.greenhouse.io/databricks/jobs/7001"))
        self.assertEqual([(ref.source, ref.url) for ref in merged.sources], [("JSearch/RapidAPI", "https://www.linkedin.com/jobs/view/123")])
        self.assertEqual([key for key, _ in matches], ["databricks:7001"])
        self.assertEqual(OFFICIAL.sources, [])   # the index record itself is not modified

    def test_radar_result_and_two_aggregator_copies_become_one_card(self):
        copies = [OFFICIAL.model_copy(deep=True), aggregator(), aggregator(source="Adzuna", url="https://www.adzuna.in/land/ad/9", job_id="9")]
        jobs, matches = resolve(copies)
        self.assertEqual(len(jobs), 1)
        self.assertEqual([ref.source for ref in jobs[0].sources], ["JSearch/RapidAPI", "Adzuna"])
        self.assertEqual(len(matches), 2)

    def test_same_url_matches_even_with_a_different_title(self):
        jobs, _ = resolve([aggregator(title="SWE", url="https://job-boards.greenhouse.io/databricks/jobs/7001/")])
        self.assertEqual(jobs[0].source, RADAR_SOURCE)

    def test_closed_official_status_is_inherited(self):
        closed = OFFICIAL.model_copy(update={"verification_state": "CLOSED", "application_status": "closed",
                                             "verification_reason": "No longer listed on the company's official board."})
        jobs, _ = resolve([aggregator()], [("databricks:7001", closed)])
        self.assertEqual((jobs[0].verification_state, jobs[0].verification_reason[:14]), ("CLOSED", "No longer list"))

    def test_ambiguous_or_unknown_listings_are_kept(self):
        twin = OFFICIAL.model_copy(update={"source_job_id": "7002", "application_url": "https://job-boards.greenhouse.io/databricks/jobs/7002"})
        jobs, matches = resolve([aggregator()], [("databricks:7001", OFFICIAL), ("databricks:7002", twin)])
        self.assertEqual((jobs[0].source, matches), ("JSearch/RapidAPI", []))
        other = aggregator(company_name="Another Co")
        self.assertEqual(resolve([other])[0], [other])


class FixtureProvider(JobProvider):
    name = "JSearch/RapidAPI"

    async def search(self, query, page=1):
        return [aggregator(), aggregator(title="Data Analyst", company_name="Unrelated Ltd", url="https://example.com/j/2", job_id="js-2")]


async def _unchanged(job):
    return job


class SearchIntegrationTests(unittest.IsolatedAsyncioTestCase):
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
        profile_store.save_profile(CandidateProfile(name="Test Student", degree="B.Tech", graduation_year=2025,
                                                    skills=["Python", "RAG"], preferred_locations=["Bengaluru"],
                                                    preferred_roles=["Software Engineer"]))
        radar_store.record_listing("databricks", [OFFICIAL], complete=True, today=D1)

    async def test_search_merges_aggregator_copies_and_records_presence(self):
        with patch("app.services.career_agent.verify_application", side_effect=_unchanged), \
                patch("app.services.career_agent.read_config", lambda: load_seed()):
            # Aggregators run next to a filled index only on a manual refresh.
            response = await CareerAgent([RadarProvider(), FixtureProvider()]).search("software engineer fresher", include_seen=True,
                                                                                     refresh=True)
        self.assertEqual(response["diagnostics"]["matched_official"], 1)
        databricks = [result for result in response["results"] if result["company"] == "Databricks"]
        self.assertEqual(len(databricks), 1)
        self.assertEqual(databricks[0]["sources"][0]["source"], "JSearch/RapidAPI")
        self.assertEqual(databricks[0]["verification_state"], "ACTIVE_VERIFIED")
        self.assertEqual([row["source"] for row in radar_store.job_sources("databricks:7001")], ["JSearch/RapidAPI"])


if __name__ == "__main__":
    unittest.main()
