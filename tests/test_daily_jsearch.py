"""The sync's single daily JSearch request, its use in searches, and the one-off probe."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx

from app.core.config import settings
from app.models.schemas import CandidateProfile
from app.providers import jsearch_provider
from app.sources import daily_jsearch, jsearch_probe
from app.storage import db, profile_store, provider_usage, search_cache

TODAY = date(2026, 9, 26)


def _item(title, job_id):
    return {"job_id": job_id, "job_title": title, "employer_name": "Example", "job_description": "Freshers welcome.",
            "job_city": "Bengaluru", "job_country": "IN", "job_apply_link": f"https://example.com/jobs/{job_id}"}


class _JSearchServer:
    """Records requests and replies with a quota header that counts down."""

    def __init__(self, remaining=200, cost_per_page=1):
        self.remaining, self.cost_per_page, self.requests = remaining, cost_per_page, []

    def __call__(self, request):
        self.requests.append(request)
        params = request.url.params
        self.remaining -= self.cost_per_page * int(params.get("num_pages", "1"))
        if params.get("job_requirements") == "under_3_years_experience":
            return httpx.Response(400, text="bad value", headers={"x-ratelimit-requests-remaining": str(self.remaining)})
        titles = ["Data Analyst", "Python Developer"] if " OR " in params["query"] else ["Python Developer"]
        items = [_item(title, f"{len(self.requests)}-{i}") for i, title in enumerate(titles)]
        return httpx.Response(200, json={"data": {"jobs": items}},
                              headers={"x-ratelimit-requests-remaining": str(self.remaining), "x-ratelimit-requests-limit": "200"})


class DailyJSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for target, values in ((search_cache, {"DB_PATH": root / "cache.sqlite3"}),
                               (profile_store, {"PROFILE_PATH": root / "profile.json"}),
                               (provider_usage, {"USAGE_PATH": root / "usage.json"}),
                               (jsearch_provider, {"_last_quota": None}),
                               (settings, {"rapidapi_key": "RAPID-SECRET", "jsearch_daily_query": "",
                                           "jsearch_daily_date_posted": "3days"})):
            patcher = patch.multiple(target, **values)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CandidateProfile(preferred_roles=["Machine Learning Engineer", "GenAI Engineer",
                                                                     "Data Scientist", "Analyst"]))
        self.server = _JSearchServer()
        original = httpx.AsyncClient
        client = patch("httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(self.server), **kw))
        client.start()
        self.addCleanup(client.stop)

    async def test_one_recent_request_per_day_stored_as_the_daily_batch(self):
        first = await daily_jsearch.run_daily_jsearch(today=TODAY)
        second = await daily_jsearch.run_daily_jsearch(today=TODAY)
        self.assertEqual((first.status, first.jobs), ("ok", 2))   # the mock answers OR queries with two roles
        self.assertEqual(first.query, "Machine Learning Engineer OR GenAI Engineer OR Data Scientist")
        self.assertEqual((second.status, second.note), ("skipped", "already fetched today"))
        self.assertEqual(len(self.server.requests), 1)
        params = self.server.requests[0].url.params
        self.assertEqual((params["date_posted"], params["num_pages"], params["country"]), ("3days", "1", "in"))
        day, jobs = search_cache.latest_daily("JSearch/RapidAPI")
        self.assertEqual((day, len(jobs)), ("2026-09-26", 2))

    async def test_skipped_without_a_key_or_in_demo(self):
        with patch.object(settings, "rapidapi_key", ""):
            self.assertEqual((await daily_jsearch.run_daily_jsearch(today=TODAY)).status, "skipped")
        with patch.object(settings, "demo_mode", True):
            self.assertEqual((await daily_jsearch.run_daily_jsearch(today=TODAY)).status, "skipped")
        self.assertEqual(self.server.requests, [])

    async def test_configured_query_overrides_saved_roles(self):
        with patch.object(settings, "jsearch_daily_query", "genai engineer fresher"):
            outcome = await daily_jsearch.run_daily_jsearch(today=TODAY)
        self.assertEqual(outcome.query, "genai engineer fresher")

    async def test_probe_measures_cost_or_syntax_and_parameters_without_printing_the_key(self):
        self.server.cost_per_page = 1
        lines = await jsearch_probe.probe()
        self.assertEqual(len(self.server.requests), jsearch_probe.REQUESTS)
        text = "\n".join(lines)
        self.assertIn("num_pages=2: 1 jobs; cost 2 request(s)", text)
        self.assertIn("OR looks supported", text)
        self.assertIn("job_requirements=no_experience: accepted", text)
        self.assertIn("job_requirements=under_3_years_experience: rejected (400", text)
        self.assertNotIn("RAPID-SECRET", text)

    def test_probe_asks_before_spending_quota(self):
        with patch("builtins.input", return_value="n"), patch("builtins.print") as printed:
            self.assertEqual(jsearch_probe.main([]), 0)
        self.assertEqual(self.server.requests, [])
        self.assertIn("Nothing sent.", json.dumps([call.args for call in printed.call_args_list]))


if __name__ == "__main__":
    unittest.main()
