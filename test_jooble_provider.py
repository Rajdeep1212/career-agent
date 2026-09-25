"""Jooble adapter: country endpoint, request body, normalization and safe errors."""
import json
import unittest
from unittest.mock import patch

import httpx

from app.core.config import Settings, settings
from app.models.career import SearchQuery
from app.providers.base import ProviderError
from app.providers.jooble_provider import JoobleProvider, normalize_item

ITEM = {
    'id': -812345, 'title': 'Python Developer (Fresher)', 'company': 'Example Soft',
    'location': 'Bengaluru, Karnataka', 'snippet': '&nbsp;...<b>Python</b> developer, FastAPI and SQL. Freshers welcome...',
    'salary': '₹4,00,000 - ₹6,00,000 a year', 'source': 'examplejobs.in', 'type': 'Full-time',
    'link': 'https://in.jooble.org/desc/-812345?ckey=python', 'updated': '2026-09-23T10:15:00.0000000',
}


class JoobleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.multiple(settings, jooble_api_key='JOOBLE-SECRET', jooble_host='in.jooble.org')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.requests = []

    def _serve(self, response):
        original = httpx.AsyncClient

        def handler(request):
            self.requests.append(request)
            return response
        return patch('httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw))

    async def test_planned_query_posts_keywords_and_location_to_the_country_site(self):
        planned = SearchQuery(query='Python Developer fresher Bengaluru', reason='', role='Python Developer', location='Bengaluru')
        with self._serve(httpx.Response(200, json={'totalCount': 1, 'jobs': [ITEM]})):
            jobs = await JoobleProvider().search_planned(planned)
        request = self.requests[0]
        self.assertEqual((request.method, request.url.host, request.url.path), ('POST', 'in.jooble.org', '/api/JOOBLE-SECRET'))
        self.assertEqual(json.loads(request.content), {'keywords': 'Python Developer', 'location': 'Bengaluru', 'page': '1'})
        self.assertEqual(len(jobs), 1)

    async def test_remote_and_country_wide_locations(self):
        with self._serve(httpx.Response(200, json={'jobs': []})):
            await JoobleProvider().search_planned(SearchQuery(query='q', reason='', role='Data Analyst', location='remote India'))
        self.assertEqual(json.loads(self.requests[0].content), {'keywords': 'Data Analyst remote', 'location': '', 'page': '1'})

    async def test_errors_name_status_without_body_or_key(self):
        for status, reason in ((403, 'invalid JOOBLE_API_KEY, or the key is for another country site'),
                               (429, 'rate limit exceeded'), (500, 'provider server error')):
            with self._serve(httpx.Response(status, text='PRIVATE-BODY')):
                with self.assertRaises(ProviderError) as caught:
                    await JoobleProvider().search('python')
            self.assertEqual((caught.exception.status_code, caught.exception.reason), (status, reason))
            for private in ('PRIVATE-BODY', 'JOOBLE-SECRET'):
                self.assertNotIn(private, str(caught.exception))

    def test_normalization(self):
        job = normalize_item(ITEM)
        self.assertEqual((job.title, job.company, job.location), ('Python Developer (Fresher)', 'Example Soft', 'Bengaluru, Karnataka'))
        self.assertEqual(job.source, 'Jooble')
        self.assertEqual(job.source_job_id, '-812345')
        self.assertEqual(str(job.application_url), ITEM['link'])
        self.assertEqual(job.employment_type, 'Full-time')
        self.assertEqual(job.salary, ITEM['salary'])
        self.assertNotIn('<b>', job.description)
        self.assertTrue(job.fresher_allowed)
        self.assertIn('FastAPI', job.skills)

    def test_host_must_be_a_jooble_country_site(self):
        for host in ('in.jooble.org', 'jooble.org', 'uk.jooble.org'):
            self.assertEqual(Settings(_env_file=None, jooble_host=host).jooble_host, host)
        for host in ('evil.example', 'in.jooble.org.evil.example', 'https://in.jooble.org', 'a.b.jooble.org'):
            with self.assertRaises(ValueError, msg=host):
                Settings(_env_file=None, jooble_host=host)


if __name__ == '__main__':
    unittest.main()
