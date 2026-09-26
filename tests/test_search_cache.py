"""Aggregators run only on a manual refresh once the Radar index has jobs; results are cached 12 h."""
import gc
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.models.career import SearchQuery
from app.models.schemas import CandidateProfile, JobPosting
from app.services.career_agent import CareerAgent
from app.storage import career_store, db, history, preference_store, profile_store, radar_store, search_cache
from test_radar_provider import _fill_index

from app.providers.radar_provider import RadarProvider


class CountingProvider:
    """A remote aggregator that counts requests."""
    name = "Aggregator"

    def __init__(self):
        self.calls = 0

    async def search(self, query, page=1):
        return await self.search_planned(SearchQuery(query=query, reason="", role=query, location=""))

    async def search_planned(self, planned):
        self.calls += 1
        return [JobPosting(company="Example Aggregated", title=f"{planned.role} (aggregator {self.calls})",
                           location="Bengaluru, India", description="Freshers welcome. Python.",
                           application_url=f"https://aggregator.example/jobs/{self.calls}")]


class _Isolated(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(directory.name)
        for module, name, value in ((history, "DB_PATH", root / "history.sqlite3"), (career_store, "DB_PATH", root / "agent.sqlite3"),
                                    (profile_store, "PROFILE_PATH", root / "profile.json"),
                                    (preference_store, "PREFERENCES_PATH", root / "preferences.json"),
                                    (radar_store, "DB_PATH", root / "radar.sqlite3"),
                                    (search_cache, "DB_PATH", root / "search_cache.sqlite3"),
                                    (settings, "search_cache_hours", 12.0)):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CandidateProfile(
            name="Test Student", degree="B.Tech", graduation_year=2025, skills=["Python", "PyTorch"],
            preferred_locations=["Bengaluru"], preferred_roles=["Machine Learning Engineer"]))


class CacheStoreTests(_Isolated):
    def test_entries_expire_after_the_ttl(self):
        planned = SearchQuery(query="q", reason="", role="  Machine  Learning Engineer ", location="Bengaluru")
        key = search_cache.cache_key("Aggregator", planned)
        self.assertEqual(key, search_cache.cache_key("aggregator", SearchQuery(query="x", reason="", role="machine learning engineer",
                                                                               location="bengaluru")))
        now = datetime(2026, 9, 26, 7, tzinfo=timezone.utc)
        search_cache.put(key, "Aggregator", "q", [JobPosting(company="A", title="B", location="C")], now=now)
        self.assertEqual(len(search_cache.get(key, now=now + timedelta(hours=11))), 1)
        self.assertIsNone(search_cache.get(key, now=now + timedelta(hours=13)))

    def test_latest_daily_returns_the_newest_day(self):
        for day in ("2026-09-24", "2026-09-26", "2026-09-25"):
            search_cache.put(search_cache.daily_key("JSearch/RapidAPI", day), "JSearch/RapidAPI", day,
                             [JobPosting(company="A", title=day, location="C")])
        day, jobs = search_cache.latest_daily("JSearch/RapidAPI")
        self.assertEqual((day, jobs[0].title), ("2026-09-26", "2026-09-26"))
        self.assertIsNone(search_cache.latest_daily("Adzuna"))


class RefreshGatingTests(_Isolated):
    async def _search(self, remote, **kwargs):
        with patch("app.services.career_agent.verify_application", side_effect=_unchanged):
            return await CareerAgent([RadarProvider(), remote]).search("Machine learning engineer fresher", include_seen=True, **kwargs)

    async def test_index_search_sends_no_aggregator_requests_without_refresh(self):
        _fill_index(12)
        remote = CountingProvider()
        response = await self._search(remote)
        diagnostics = response["diagnostics"]
        self.assertEqual(remote.calls, 0)
        self.assertEqual(diagnostics["provider_requests"], 0)
        self.assertEqual(diagnostics["aggregator_skipped"], diagnostics["generated_queries"])
        self.assertGreater(response["result_count"], 0)
        self.assertIn("Refresh", response["summary"])

    async def test_refresh_queries_aggregators_and_fills_the_cache(self):
        _fill_index(12)
        remote = CountingProvider()
        refreshed = await self._search(remote, refresh=True)
        requests = refreshed["diagnostics"]["provider_requests"]
        self.assertGreater(requests, 0)
        self.assertEqual(remote.calls, requests)
        cached = await self._search(remote)
        self.assertEqual(remote.calls, requests)              # no new requests
        self.assertEqual(cached["diagnostics"]["cache_hits"], requests)
        self.assertEqual(cached["diagnostics"]["provider_requests"], 0)
        self.assertIn("Aggregator", cached["diagnostics"]["provider_counts"])

    async def test_empty_index_falls_back_to_aggregators_with_the_cache(self):
        remote = CountingProvider()
        first = await self._search(remote)
        self.assertGreater(remote.calls, 0)
        calls = remote.calls
        second = await self._search(remote)
        self.assertEqual(remote.calls, calls)
        # Planned queries that share a role and city share one request, even within a search.
        self.assertEqual(second["diagnostics"]["cache_hits"],
                         first["diagnostics"]["provider_requests"] + first["diagnostics"]["cache_hits"])

    async def test_the_daily_jsearch_batch_is_read_at_no_cost(self):
        _fill_index(12)
        remote = CountingProvider()
        remote.name = "JSearch/RapidAPI"
        today = datetime.now(timezone.utc).date().isoformat()
        search_cache.put(search_cache.daily_key("JSearch/RapidAPI", today), "JSearch/RapidAPI", "ML Engineer",
                         [JobPosting(company="Daily Co", title="Machine Learning Engineer", location="Bengaluru, India",
                                     description="Freshers welcome.", application_url="https://daily.example/1")])
        response = await self._search(remote)
        self.assertEqual(remote.calls, 0)
        self.assertEqual(response["diagnostics"]["daily_results"], 1)
        self.assertIn("Daily Co", [result["company"] for result in response["results"]])

    async def test_preview_counts_only_requests_that_would_be_sent(self):
        _fill_index(12)
        agent = CareerAgent([RadarProvider(), CountingProvider()])
        self.assertEqual(agent.plan("Machine learning engineer fresher")["provider_requests"], 0)
        self.assertGreater(agent.plan("Machine learning engineer fresher", refresh=True)["provider_requests"], 0)

    async def test_cache_can_be_disabled(self):
        remote = CountingProvider()
        with patch.object(settings, "search_cache_hours", 0.0):
            await self._search(remote)
            calls = remote.calls
            await self._search(remote)
        self.assertEqual(remote.calls, 2 * calls)



class RefreshPlumbingTests(unittest.TestCase):
    def test_api_and_chat_forward_refresh(self):
        from unittest.mock import AsyncMock
        from fastapi.testclient import TestClient
        from app.main import app
        search = AsyncMock(return_value={"results": [], "result_count": 0, "diagnostics": {}})
        with patch("app.services.career_agent.CareerAgent.search", search),              TestClient(app, base_url="http://localhost:8010") as client:
            client.post("/agent/search", json={"query": "ml engineer", "refresh": True})
        self.assertTrue(search.await_args.kwargs["refresh"])
        from app.models.chat import ChatRunRequest
        self.assertFalse(ChatRunRequest(thread_id="t", turn_id="u", message="m").refresh)

async def _unchanged(job):
    return job


if __name__ == "__main__":
    unittest.main()
