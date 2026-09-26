"""Shared helpers for Company Radar tests: fixture loading and a fake fetcher."""
import json
from pathlib import Path

from app.sources.fetcher import Fetched, HostBlocked
from app.sources.registry import CompanyEntry

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sources"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def company(source: dict, **overrides) -> CompanyEntry:
    entry = {"id": "example", "name": "Example Corp", "tags": ["big_tech"], "source": source,
             "evidence": {"method": "api_probe", "checked_at": "2026-09-26"}, "enabled": True, "reviewed": True}
    entry.update(overrides)
    return CompanyEntry.model_validate(entry)


class FakeFetcher:
    """Serves canned bodies by URL and records every call (url, check_robots)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, bool]] = []

    async def get(self, url, *, check_robots=True, conditional=False, accept=None):
        self.calls.append((url, check_robots))
        route = self.routes.get(url)
        if route is None:
            return Fetched(url, 404, "")
        if route == 429:
            raise HostBlocked("429")
        if isinstance(route, BaseException):
            raise route
        status, body = route if isinstance(route, tuple) else (200, route)
        return Fetched(url, status, body if isinstance(body, str) else json.dumps(body))
