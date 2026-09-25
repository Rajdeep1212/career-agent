"""Provider failures name the HTTP status, a safe reason and the quota reset time."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx

from app.core.config import settings
from app.providers import jsearch_provider
from app.providers.base import ProviderError
from app.providers.jsearch_provider import JSearchProvider


def _client_returning(response):
    original = httpx.AsyncClient

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(lambda _request: response), **kwargs)
    return factory


class JSearchErrorTests(unittest.IsolatedAsyncioTestCase):
    async def _error(self, response):
        with patch.object(settings, 'rapidapi_key', 'test-key'), \
             patch('httpx.AsyncClient', side_effect=_client_returning(response)):
            with self.assertRaises(ProviderError) as caught:
                await JSearchProvider().search('python fresher')
        return caught.exception

    async def test_quota_exceeded_reports_status_reason_and_reset(self):
        error = await self._error(httpx.Response(429, text='PRIVATE-PROVIDER-BODY', headers={
            'x-ratelimit-requests-limit': '200', 'x-ratelimit-requests-remaining': '0',
            'x-ratelimit-requests-reset': '3600'}))
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.reason, 'quota or rate limit exceeded')
        self.assertEqual(error.remaining, 0)
        self.assertEqual(error.limit, 200)
        reset = datetime.fromisoformat(error.reset_at)
        self.assertAlmostEqual((reset - datetime.now(timezone.utc)).total_seconds(), 3600, delta=60)
        self.assertIn('HTTP 429', str(error))
        self.assertIn('resets', str(error))
        self.assertNotIn('PRIVATE-PROVIDER-BODY', str(error))

    async def test_retry_after_is_used_when_no_quota_reset_header(self):
        error = await self._error(httpx.Response(429, headers={'retry-after': '120'}))
        reset = datetime.fromisoformat(error.reset_at)
        self.assertAlmostEqual((reset - datetime.now(timezone.utc)).total_seconds(), 120, delta=60)

    async def test_access_and_server_errors_have_specific_reasons(self):
        for status, reason in ((401, 'invalid or missing RAPIDAPI_KEY'),
                               (403, 'not subscribed to JSearch, or access denied'),
                               (500, 'provider server error'), (503, 'provider server error'),
                               (418, 'request rejected')):
            error = await self._error(httpx.Response(status))
            self.assertEqual((error.status_code, error.reason), (status, reason))
            self.assertIn(f'HTTP {status}', str(error))
            self.assertIsNone(error.reset_at)

    async def test_timeout_and_network_errors_have_reasons_without_status(self):
        for exc, reason in ((httpx.ConnectTimeout('slow'), 'timed out'), (httpx.ConnectError('down'), 'could not be reached')):
            original = httpx.AsyncClient

            def factory(**kwargs):
                def raise_error(_request):
                    raise exc
                return original(transport=httpx.MockTransport(raise_error), **kwargs)
            with patch.object(settings, 'rapidapi_key', 'test-key'), patch('httpx.AsyncClient', side_effect=factory):
                with self.assertRaises(ProviderError) as caught:
                    await JSearchProvider().search('python')
            self.assertIsNone(caught.exception.status_code)
            self.assertEqual(caught.exception.reason, reason)

    async def test_successful_response_records_remaining_quota(self):
        response = httpx.Response(200, json={'data': []}, headers={
            'x-ratelimit-requests-limit': '200', 'x-ratelimit-requests-remaining': '3',
            'x-ratelimit-requests-reset': '86400'})
        with patch.object(settings, 'rapidapi_key', 'test-key'), \
             patch('httpx.AsyncClient', side_effect=_client_returning(response)):
            await JSearchProvider().search('python')
        quota = jsearch_provider.last_quota()
        self.assertEqual((quota['remaining'], quota['limit']), (3, 200))
        self.assertIsNotNone(quota['reset_at'])


class CareerAgentErrorSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_names_the_provider_status_and_reset(self):
        import tempfile
        from pathlib import Path
        from app.services.career_agent import CareerAgent
        from app.storage import career_store, history, preference_store, profile_store
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for module, name, value in ((history, 'DB_PATH', root / 'h.sqlite3'), (career_store, 'DB_PATH', root / 'c.sqlite3'),
                                    (profile_store, 'PROFILE_PATH', root / 'p.json'),
                                    (preference_store, 'PREFERENCES_PATH', root / 'prefs.json')):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        reset = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
        provider = JSearchProvider()
        provider.search = AsyncMock(side_effect=ProviderError(
            'JSearch request failed (HTTP 429: quota or rate limit exceeded).', status_code=429,
            reason='quota or rate limit exceeded', reset_at=reset.isoformat(), remaining=0, limit=200))
        response = await CareerAgent([provider]).search('Find python jobs')
        expected = f'JSearch/RapidAPI: HTTP 429 quota or rate limit exceeded, resets {reset:%d %b %Y %H:%M} UTC'
        self.assertIn(expected, response['diagnostics']['errors'])
        self.assertIn(expected, response['summary'])
        self.assertNotIn('Provider errors occurred', response['summary'])
        detail = response['diagnostics']['provider_errors'][0]
        self.assertEqual((detail['provider'], detail['status_code'], detail['remaining']), ('JSearch/RapidAPI', 429, 0))
        self.assertEqual(response['diagnostics']['errors'].count(expected), 1)
        # A quota error would repeat for every planned query, so the rest are skipped.
        self.assertEqual(provider.search.await_count, 1)
        self.assertGreater(response['diagnostics']['skipped_requests'], 0)


if __name__ == '__main__':
    unittest.main()
