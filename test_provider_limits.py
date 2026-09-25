"""Local request counts keep searches within Adzuna's and Jooble's published limits."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.core.config import settings
from app.models.career import SearchQuery
from app.providers.adzuna_provider import AdzunaProvider
from app.providers.jooble_provider import JoobleProvider
from app.storage import provider_usage


class _TempUsage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'provider_usage.json'
        for target, values in ((provider_usage, {'USAGE_PATH': self.path}),
                               (settings, {'adzuna_app_id': 'id', 'adzuna_app_key': 'ADZUNA-SECRET',
                                           'jooble_api_key': 'JOOBLE-SECRET', 'jooble_host': 'in.jooble.org',
                                           'rapidapi_key': 'rapid', 'adzuna_daily_limit': 250,
                                           'adzuna_monthly_limit': 2500, 'jooble_key_limit': 500})):
            patcher = patch.multiple(target, **values)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _serve(self, response):
        original = httpx.AsyncClient
        return patch('httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda _r: response), **kw))


class UsageStoreTests(_TempUsage):
    async def test_counts_are_keyed_by_a_key_fingerprint_not_the_key(self):
        provider_usage.record('jooble', 'JOOBLE-SECRET', count=3)
        provider_usage.record('jooble', 'OTHER-KEY')
        self.assertEqual(provider_usage.usage('jooble', 'JOOBLE-SECRET')['total'], 3)
        self.assertEqual(provider_usage.usage('jooble', 'OTHER-KEY')['total'], 1)
        self.assertNotIn('SECRET', self.path.read_text(encoding='utf-8'))

    async def test_unreadable_usage_file_starts_fresh_without_crashing(self):
        self.path.write_text('{broken', encoding='utf-8')
        self.assertEqual(provider_usage.usage('adzuna', 'k'), {'total': 0, 'today': 0, 'month': 0})
        provider_usage.record('adzuna', 'k')
        self.assertEqual(provider_usage.usage('adzuna', 'k')['today'], 1)


class ProviderQuotaTests(_TempUsage):
    async def test_requests_are_counted_and_duplicates_are_not(self):
        provider = AdzunaProvider()
        planned = SearchQuery(query='q', reason='', role='Data Analyst', location='Pune')
        with self._serve(httpx.Response(200, json={'results': []})):
            await provider.search_planned(planned)
            await provider.search_planned(planned)
        self.assertEqual(provider_usage.usage('adzuna', 'ADZUNA-SECRET')['today'], 1)

    async def test_adzuna_quota_is_the_tighter_of_daily_and_monthly(self):
        provider_usage.record('adzuna', 'ADZUNA-SECRET', count=248)
        quota = AdzunaProvider().quota()
        self.assertEqual((quota['remaining'], quota['limit'], quota['window']), (2, 250, 'today (UTC)'))
        self.assertIsNotNone(quota['reset_at'])

    async def test_jooble_quota_is_per_key_and_does_not_reset(self):
        provider_usage.record('jooble', 'JOOBLE-SECRET', count=495)
        quota = JoobleProvider().quota()
        self.assertEqual((quota['remaining'], quota['limit'], quota['reset_at']), (5, 500, None))


class PreviewWarningTests(_TempUsage):
    def _plan(self, **counts):
        from app.services.career_agent import CareerAgent
        from app.providers.jsearch_provider import JSearchProvider
        agent = CareerAgent([JSearchProvider(), AdzunaProvider(), JoobleProvider()])
        with patch('app.services.career_agent.plan_search_queries', return_value=[
                SearchQuery(query=f'q{i}', reason='', role='Data Analyst', location='Pune') for i in range(counts.get('n', 4))]):
            return agent.plan('Find data analyst jobs')

    async def test_requests_are_split_across_providers(self):
        plan = self._plan()
        self.assertEqual(plan['requests_by_provider'], {'JSearch/RapidAPI': 2, 'Adzuna': 1, 'Jooble': 1})

    async def test_low_jooble_lifetime_quota_is_warned_about(self):
        from app.services.career_agent import search_cost_warning
        provider_usage.record('jooble', 'JOOBLE-SECRET', count=460)
        warning = search_cost_warning(self._plan())
        self.assertIn('Only 40 of 500 Jooble requests remain for this key', warning)
        self.assertIn('counted on this machine', warning)

    async def test_exhausted_adzuna_day_is_warned_about(self):
        from app.services.career_agent import search_cost_warning
        provider_usage.record('adzuna', 'ADZUNA-SECRET', count=250)
        self.assertIn('Only 0 of 250 Adzuna requests remain today (UTC)', search_cost_warning(self._plan()))

    async def test_no_warning_with_plenty_of_quota(self):
        from app.services.career_agent import search_cost_warning
        self.assertIsNone(search_cost_warning(self._plan()))


if __name__ == '__main__':
    unittest.main()
