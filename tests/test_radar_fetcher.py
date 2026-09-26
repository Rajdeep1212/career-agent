"""PoliteFetcher: robots.txt, per-host delay, 429 stop, conditional requests, clear UA."""
import tempfile
import unittest
from pathlib import Path

import httpx

from app.sources.fetcher import HostBlocked, PoliteFetcher, RobotsDisallowed, USER_AGENT


class FakeWeb:
    """Records requests and serves canned responses; never touches the network."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, *, timeout, headers, max_bytes):
        self.calls.append((url, dict(headers)))
        route = self.routes.get(url, (404, "", {}))
        status, body, response_headers = route(headers) if callable(route) else route
        return httpx.Response(status, text=body, headers=response_headers, request=httpx.Request("GET", url))


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


class PoliteFetcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        self.clock = FakeClock()

    def fetcher(self, web, delay=1.5):
        return PoliteFetcher(get=web.get, delay=delay, clock=self.clock.time, sleep=self.clock.sleep, cache_dir=self.cache)

    async def test_robots_disallow_blocks_the_page_without_requesting_it(self):
        web = FakeWeb({"https://site.example/robots.txt": (200, "User-agent: *\nDisallow: /private/\n", {}),
                       "https://site.example/private/job": (200, "secret", {})})
        with self.assertRaises(RobotsDisallowed):
            await self.fetcher(web).get("https://site.example/private/job")
        self.assertEqual([url for url, _ in web.calls], ["https://site.example/robots.txt"])

    async def test_missing_robots_allows_and_is_fetched_once_per_host(self):
        web = FakeWeb({"https://site.example/a": (200, "A", {}), "https://site.example/b": (200, "B", {})})
        fetcher = self.fetcher(web)
        self.assertEqual((await fetcher.get("https://site.example/a")).text, "A")
        self.assertEqual((await fetcher.get("https://site.example/b")).text, "B")
        self.assertEqual([url for url, _ in web.calls].count("https://site.example/robots.txt"), 1)

    async def test_documented_apis_skip_robots(self):
        web = FakeWeb({"https://api.example/v1/jobs": (200, "[]", {})})
        await self.fetcher(web).get("https://api.example/v1/jobs", check_robots=False)
        self.assertEqual([url for url, _ in web.calls], ["https://api.example/v1/jobs"])

    async def test_requests_to_one_host_are_spaced(self):
        web = FakeWeb({f"https://api.example/{n}": (200, "ok", {}) for n in range(3)})
        fetcher = self.fetcher(web, delay=1.5)
        for n in range(3):
            await fetcher.get(f"https://api.example/{n}", check_robots=False)
        self.assertEqual(self.clock.slept, [1.5, 1.5])

    async def test_different_hosts_are_not_delayed(self):
        web = FakeWeb({"https://a.example/x": (200, "ok", {}), "https://b.example/x": (200, "ok", {})})
        fetcher = self.fetcher(web)
        await fetcher.get("https://a.example/x", check_robots=False)
        await fetcher.get("https://b.example/x", check_robots=False)
        self.assertEqual(self.clock.slept, [])

    async def test_429_stops_that_host_for_the_rest_of_the_run(self):
        web = FakeWeb({"https://api.example/1": (429, "slow down", {}), "https://api.example/2": (200, "ok", {}),
                       "https://other.example/1": (200, "ok", {})})
        fetcher = self.fetcher(web)
        with self.assertRaises(HostBlocked):
            await fetcher.get("https://api.example/1", check_robots=False)
        with self.assertRaises(HostBlocked):
            await fetcher.get("https://api.example/2", check_robots=False)
        self.assertEqual((await fetcher.get("https://other.example/1", check_robots=False)).text, "ok")
        self.assertNotIn("https://api.example/2", [url for url, _ in web.calls])

    async def test_conditional_requests_reuse_the_cached_body(self):
        def sitemap(headers):
            if headers.get("If-None-Match") == '"v1"':
                return 304, "", {}
            return 200, "<urlset/>", {"ETag": '"v1"'}
        web = FakeWeb({"https://site.example/sitemap.xml": sitemap})
        first = await self.fetcher(web).get("https://site.example/sitemap.xml", check_robots=False, conditional=True)
        second = await self.fetcher(web).get("https://site.example/sitemap.xml", check_robots=False, conditional=True)
        self.assertEqual((first.text, first.from_cache), ("<urlset/>", False))
        self.assertEqual((second.text, second.from_cache, second.status_code), ("<urlset/>", True, 200))

    async def test_user_agent_identifies_the_app(self):
        web = FakeWeb({"https://api.example/x": (200, "ok", {})})
        await self.fetcher(web).get("https://api.example/x", check_robots=False)
        self.assertEqual(web.calls[0][1]["User-Agent"], USER_AGENT)
        self.assertIn("CareerAgent", USER_AGENT)


if __name__ == "__main__":
    unittest.main()
