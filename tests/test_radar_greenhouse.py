"""Greenhouse adapter against a recorded-shape fixture; the network is never used."""
import unittest

from app.sources.adapters import SourceError, greenhouse
from radar_helpers import FakeFetcher, company, fixture_text

URL = "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"


class GreenhouseTests(unittest.IsolatedAsyncioTestCase):
    async def test_india_jobs_are_normalized_and_counts_come_from_the_parsed_list(self):
        fetcher = FakeFetcher({URL: fixture_text("greenhouse_jobs.json")})
        result = await greenhouse.fetch(company({"type": "greenhouse", "board": "example"}), fetcher)
        self.assertEqual((result.fetched, result.india, result.complete), (4, 2, True))
        self.assertEqual(len(result.jobs), result.india)
        job = result.jobs[0]
        self.assertEqual((job.company, job.title, job.source_job_id), ("Example Corp", "Machine Learning Engineer, New Grad", "7001"))
        self.assertEqual(str(job.application_url), "https://job-boards.greenhouse.io/example/jobs/7001")
        self.assertIn("Freshers welcome", job.description)
        self.assertNotIn("<", job.description)
        self.assertTrue(job.official_application)
        self.assertIn("LangChain", job.skills)
        self.assertEqual(fetcher.calls, [(URL, False)])  # documented API: no robots check

    async def test_indiana_is_not_india(self):
        fetcher = FakeFetcher({URL: fixture_text("greenhouse_jobs.json")})
        result = await greenhouse.fetch(company({"type": "greenhouse", "board": "example"}), fetcher)
        self.assertNotIn("7004", [job.source_job_id for job in result.jobs])

    async def test_missing_board_is_a_source_error_with_status(self):
        with self.assertRaises(SourceError) as caught:
            await greenhouse.fetch(company({"type": "greenhouse", "board": "example"}), FakeFetcher({}))
        self.assertEqual(caught.exception.http_status, 404)


if __name__ == "__main__":
    unittest.main()
