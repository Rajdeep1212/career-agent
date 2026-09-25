"""Aggregator job links are opened only by the user: automated visits would count as clicks."""
import unittest
from unittest.mock import AsyncMock, patch

from app.models.schemas import JobPosting
from app.services import application_verifier as verifier


class AggregatorLinkTests(unittest.IsolatedAsyncioTestCase):
    async def _verify(self, url, source=None):
        job = JobPosting(company='Example', title='Data Analyst', location='Pune', application_url=url, source=source)
        with patch.object(verifier, 'safe_get', AsyncMock(side_effect=AssertionError('aggregator link was fetched'))):
            return await verifier.verify_application(job)

    async def test_adzuna_and_jooble_links_are_not_fetched(self):
        for url, source in (('https://www.adzuna.in/land/ad/4711?se=abc', 'Adzuna'),
                            ('https://www.adzuna.co.uk/details/1', None),
                            ('https://in.jooble.org/desc/-812345', 'Jooble'),
                            ('https://jooble.org/desc/1', None),
                            ('https://employer.example/jobs/1', 'Jooble')):
            job = await self._verify(url, source)
            self.assertEqual(job.verification_state, 'UNVERIFIED', url)
            self.assertIn('count as clicks', job.verification_reason)

    async def test_lookalike_hosts_are_still_checked_normally(self):
        job = JobPosting(company='Example', title='Data Analyst', location='Pune',
                         application_url='https://notadzuna.example/jobs/1')
        with patch.object(verifier, 'safe_get', AsyncMock(side_effect=OSError('offline'))) as fetch:
            await verifier.verify_application(job)
        fetch.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
