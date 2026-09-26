"""SmartRecruiters adapter: pagination, details on demand; the network is never used."""
import unittest

from app.sources.adapters import smartrecruiters
from radar_helpers import FakeFetcher, company, fixture_text

LIST = "https://api.smartrecruiters.com/v1/companies/Example/postings?country=in&limit=2&offset={offset}"
DETAIL = "https://api.smartrecruiters.com/v1/companies/Example/postings/744000001"
SOURCE = {"type": "smartrecruiters", "company": "Example"}


class SmartRecruitersTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_pages_are_read_and_counted(self):
        fetcher = FakeFetcher({LIST.format(offset=0): fixture_text("smartrecruiters_page1.json"),
                               LIST.format(offset=2): fixture_text("smartrecruiters_page2.json")})
        result = await smartrecruiters.fetch(company(SOURCE), fetcher, page_size=2)
        self.assertEqual((result.fetched, result.india, result.complete), (3, 3, True))
        self.assertEqual(result.details_pending, ["744000001", "744000002", "744000003"])
        self.assertEqual(str(result.jobs[0].application_url), "https://jobs.smartrecruiters.com/Example/744000001")
        self.assertEqual(result.jobs[2].employment_type, "Internship")
        self.assertEqual([check for _, check in fetcher.calls], [False, False])

    async def test_detail_fills_the_description_and_posting_url(self):
        fetcher = FakeFetcher({LIST.format(offset=0): fixture_text("smartrecruiters_page1.json"),
                               LIST.format(offset=2): fixture_text("smartrecruiters_page2.json"),
                               DETAIL: fixture_text("smartrecruiters_detail.json")})
        listed = (await smartrecruiters.fetch(company(SOURCE), fetcher, page_size=2)).jobs[0]
        detailed = await smartrecruiters.fetch_detail(company(SOURCE), fetcher, listed)
        self.assertIn("2025 graduates eligible", detailed.description)
        self.assertIn("B.Tech in CS or ECE", detailed.description)
        self.assertNotIn("Example builds sensors", detailed.description)
        self.assertEqual(detailed.graduation_years, [2025])
        self.assertTrue(str(detailed.application_url).startswith("https://jobs.smartrecruiters.com/Example/744000001-"))
        self.assertEqual(detailed.source_job_id, "744000001")


if __name__ == "__main__":
    unittest.main()
