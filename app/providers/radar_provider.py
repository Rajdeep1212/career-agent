"""Company Radar as a job provider: searches the local index, sends no requests.

The index (data/radar.sqlite3) is filled by the daily sync from companies'
official boards, so every job it returns already carries a source-based status
(ACTIVE_VERIFIED while listed). A search reads the index once for all planned
queries; a job is returned when its title matches a planned role (the phrase
itself or the same role family) and, for a city query, its location.
"""
import re
from functools import lru_cache

from app.models.career import SearchQuery
from app.models.schemas import JobPosting
from app.providers.base import JobProvider
from app.services.role_discovery import contains_phrase, family_for_title
from app.sources.adapters import RADAR_SOURCE
from app.storage import radar_store

MAX_RESULTS = 300
_CITY_ALIASES = {"bengaluru": "bengaluru|bangalore", "bangalore": "bengaluru|bangalore", "gurugram": "gurugram|gurgaon",
                 "gurgaon": "gurugram|gurgaon", "mumbai": "mumbai|bombay", "chennai": "chennai|madras",
                 "delhi": "delhi|new delhi|ncr", "kolkata": "kolkata|calcutta"}
_NO_CITY = re.compile(r"^\s*(?:india|anywhere|any|remote.*|)\s*$", re.IGNORECASE)


@lru_cache(maxsize=4096)
def _family(title: str) -> str | None:
    family = family_for_title(title)
    return family["family"] if family else None


def _location_pattern(location: str) -> re.Pattern | None:
    if _NO_CITY.match(location or ""):
        return None
    city = re.split(r"[,/]", location)[0].strip().lower()
    return re.compile(r"\b(?:" + _CITY_ALIASES.get(city, re.escape(city)) + r")\b", re.IGNORECASE)


def _matches(job: JobPosting, role: str, role_family: str | None, place: re.Pattern | None) -> bool:
    if place is not None and not place.search(job.location) and job.work_mode != "remote":
        return False
    return bool(role and contains_phrase(job.title, role)) or bool(role_family and _family(job.title) == role_family)


class RadarProvider(JobProvider):
    name = RADAR_SOURCE
    local = True   # reads the local index: no requests, no quota, not part of the request round-robin

    @classmethod
    def configured(cls) -> bool:
        return radar_store.has_jobs()

    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        return self.search_local([SearchQuery(query=query, reason="", role=query, location="")])

    def search_local(self, queries: list[SearchQuery]) -> list[JobPosting]:
        """Active index jobs matching any planned (role, location), newest first."""
        plans = [(query.role or query.query, _family(query.role or query.query), _location_pattern(query.location))
                 for query in queries]
        found = [job for job in radar_store.list_jobs()
                 if any(_matches(job, role, family, place) for role, family, place in plans)]
        found.sort(key=lambda job: job.posted_date or "", reverse=True)
        return found[:MAX_RESULTS]
