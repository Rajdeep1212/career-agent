"""LinkedIn, Naukri and Indeed pages are never fetched automatically (CLAUDE.md hard constraint)."""
import unittest
from unittest.mock import AsyncMock, patch

from app.models.schemas import JobPosting
from app.services import application_verifier as verifier


class NeverFetchedSiteTests(unittest.IsolatedAsyncioTestCase):
    async def _verify(self, url, source=None):
        job = JobPosting(company="Example", title="Data Analyst", location="Pune", application_url=url, source=source)
        with patch.object(verifier, "safe_get", AsyncMock(side_effect=AssertionError("page was fetched"))):
            return await verifier.verify_application(job)

    async def test_linkedin_naukri_and_indeed_links_are_never_fetched(self):
        for url in ("https://www.linkedin.com/jobs/view/4012345678/", "https://in.linkedin.com/jobs/view/1",
                    "https://lnkd.in/abcd", "https://www.naukri.com/job-listings-data-analyst-example-pune-0-to-2-years-250926000123",
                    "https://in.indeed.com/viewjob?jk=0123456789abcdef", "https://www.indeed.com/rc/clk?jk=1"):
            job = await self._verify(url)
            self.assertEqual(job.verification_state, "UNVERIFIED", url)
            self.assertIn("never opened automatically", job.verification_reason)

    async def test_lookalike_hosts_are_checked_normally(self):
        job = JobPosting(company="Example", title="Data Analyst", location="Pune",
                         application_url="https://notlinkedin.example/jobs/1")
        with patch.object(verifier, "safe_get", AsyncMock(side_effect=OSError("offline"))) as fetch:
            await verifier.verify_application(job)
        fetch.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
