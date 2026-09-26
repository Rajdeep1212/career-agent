"""Lever adapter against a recorded-shape fixture; the network is never used."""
import unittest

from app.sources.adapters import lever
from radar_helpers import FakeFetcher, company, fixture_text

URL = "https://api.lever.co/v0/postings/example?mode=json"


class LeverTests(unittest.IsolatedAsyncioTestCase):
    async def test_india_postings_by_location_or_country_code(self):
        fetcher = FakeFetcher({URL: fixture_text("lever_postings.json")})
        result = await lever.fetch(company({"type": "lever", "site": "example"}), fetcher)
        self.assertEqual((result.fetched, result.india, result.complete), (3, 2, True))
        first = result.jobs[0]
        self.assertEqual((first.title, first.work_mode, first.employment_type), ("Software Engineer I (Backend)", "hybrid", "Full-time"))
        self.assertEqual(str(first.application_url), "https://jobs.lever.co/example/a1b2c3d4-0001")
        self.assertIn("0-1 years of experience", first.description)
        self.assertNotIn("<li>", first.description)
        self.assertTrue(first.posted_date.startswith("2025-09-25"))
        self.assertEqual(fetcher.calls, [(URL, False)])

    async def test_eu_region_uses_the_eu_api(self):
        eu = "https://api.eu.lever.co/v0/postings/example?mode=json"
        fetcher = FakeFetcher({eu: "[]"})
        result = await lever.fetch(company({"type": "lever", "site": "example", "region": "eu"}), fetcher)
        self.assertEqual((result.fetched, result.india), (0, 0))
        self.assertEqual(fetcher.calls[0][0], eu)


if __name__ == "__main__":
    unittest.main()
