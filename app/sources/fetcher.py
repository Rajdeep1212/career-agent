"""Polite HTTP for the Company Radar sync.

- Crawled pages (sitemaps, job pages) honour robots.txt; documented public
  APIs (Greenhouse, Lever, Ashby, SmartRecruiters) skip that check.
- Requests to one host are spaced by at least `delay` seconds.
- A 429 stops every further request to that host for the rest of the run.
- Conditional requests reuse a cached body on 304 Not Modified.
- Every request goes through safe_http.safe_get (SSRF-safe, bounded) with a
  User-Agent that names the app.
"""
import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import settings
from app.services.safe_http import safe_get

USER_AGENT = "CareerAgent/0.4 (local personal job search; reads public job boards once a day)"
CACHE_DIR = Path(settings.data_dir) / "radar_http_cache"


class HostBlocked(RuntimeError):
    """The host answered 429 earlier in this run; no more requests go to it."""


class RobotsDisallowed(RuntimeError):
    """robots.txt disallows this path for our User-Agent."""


class RobotsUnavailable(RobotsDisallowed):
    """robots.txt could not be read (server error or no response), so nothing on that host is fetched this run."""


@dataclass
class Fetched:
    url: str
    status_code: int
    text: str
    headers: dict = field(default_factory=dict)
    from_cache: bool = False

    def json(self):
        return json.loads(self.text)


Getter = Callable[..., Awaitable[httpx.Response]]


class PoliteFetcher:
    def __init__(self, *, delay: float = 1.5, get: Getter | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, cache_dir: Path | None = None,
                 timeout: float | None = None, max_bytes: int = 5_000_000):
        self.delay = delay
        self._get = get or safe_get
        self._clock = clock
        self._sleep = sleep
        self._cache_dir = cache_dir or CACHE_DIR
        self._timeout = timeout or min(settings.request_timeout_seconds, 30.0)
        self._max_bytes = max_bytes
        self._last_request: dict[str, float] = {}
        self._blocked: set[str] = set()
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_unavailable: dict[str, str] = {}   # origin -> why robots.txt could not be read
        self.requests = 0  # requests actually sent this run

    async def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        host = urlsplit(url).netloc.lower()
        if host in self._blocked:
            raise HostBlocked(f"{host} asked us to slow down (HTTP 429) earlier in this run.")
        last = self._last_request.get(host)
        if last is not None:
            wait = self.delay - (self._clock() - last)
            if wait > 0:
                await self._sleep(wait)
        self._last_request[host] = self._clock()
        self.requests += 1
        response = await self._get(url, timeout=self._timeout, headers={"User-Agent": USER_AGENT, **headers},
                                   max_bytes=self._max_bytes)
        if response.status_code == 429:
            self._blocked.add(host)
            raise HostBlocked(f"{host} answered HTTP 429; skipping it for the rest of this run.")
        return response

    async def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = None   # None: disallow everything this run
            try:
                response = await self._request(origin + "/robots.txt", {"Accept": "text/plain"})
                if response.status_code == 200 or 400 <= response.status_code < 500:
                    parser = RobotFileParser()
                    # A missing robots.txt (4xx) allows everything; a server error stays conservative.
                    parser.parse(response.text.splitlines() if response.status_code == 200 else [])
                else:
                    self._robots_unavailable[origin] = (f"robots.txt returned HTTP {response.status_code}; "
                                                        "the site may be down or under maintenance")
            except HostBlocked:
                raise
            except Exception as exc:
                parser = None
                self._robots_unavailable[origin] = f"robots.txt could not be read ({type(exc).__name__})"
            self._robots[origin] = parser
        parser = self._robots[origin]
        return bool(parser and parser.can_fetch(USER_AGENT, url))

    def _cache_path(self, url: str) -> Path:
        return self._cache_dir / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + ".json")

    async def get(self, url: str, *, check_robots: bool = True, conditional: bool = False, accept: str | None = None) -> Fetched:
        if check_robots and not await self._allowed(url):
            origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
            if origin in self._robots_unavailable:
                raise RobotsUnavailable(f"{self._robots_unavailable[origin]}; not fetching {url}")
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        headers = {"Accept": accept} if accept else {}
        cached = None
        if conditional:
            path = self._cache_path(url)
            if path.exists():
                try:
                    cached = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    cached = None
            if cached and cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached and cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        response = await self._request(url, headers)
        if response.status_code == 304 and cached:
            return Fetched(url, 200, cached["body"], {}, from_cache=True)
        result = Fetched(url, response.status_code, response.text, dict(response.headers))
        if conditional and response.status_code == 200 and (response.headers.get("etag") or response.headers.get("last-modified")):
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(url).write_text(json.dumps({"etag": response.headers.get("etag"),
                                                         "last_modified": response.headers.get("last-modified"),
                                                         "body": response.text}), encoding="utf-8")
        return result
