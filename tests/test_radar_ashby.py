"""Ashby adapter against a recorded-shape fixture; the network is never used."""
import unittest

from app.sources.adapters import ashby
from radar_helpers import FakeFetcher, company, fixture_text

URL = "https://api.ashbyhq.com/posting-api/job-board/example"


class AshbyTests(unittest.IsolatedAsyncioTestCase):
    async def test_listed_india_jobs_including_secondary_locations(self):
        fetcher = FakeFetcher({URL: fixture_text("ashby_board.json")})
        result = await ashby.fetch(company({"type": "ashby", "board": "example"}), fetcher)
        # 3 listed postings (one unlisted is not public); 2 have an India location.
        self.assertEqual((result.fetched, result.india, result.complete), (3, 2, True))
        titles = [job.title for job in result.jobs]
        self.assertEqual(titles, ["Applied AI Engineer", "Research Scientist"])
        first = result.jobs[0]
        self.assertEqual((first.work_mode, str(first.application_url)), ("onsite", "https://jobs.ashbyhq.com/example/11111111-aaaa"))
        self.assertIn("Fine-tuning", first.skills)
        self.assertIn("Hyderabad", result.jobs[1].location)
        self.assertEqual(fetcher.calls, [(URL, False)])


if __name__ == "__main__":
    unittest.main()
