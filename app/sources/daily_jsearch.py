"""The Radar sync's one daily JSearch request.

One page of recent postings (date_posted=3days by default) for the saved
roles, stored as the day's batch in the search cache. Searches read the
newest batch at no cost, and identity resolution folds copies of indexed
jobs into their official Radar records. At most one request per day.
"""
from dataclasses import dataclass
from datetime import date

from app.core.config import settings
from app.providers.base import ProviderError
from app.providers.jsearch_provider import JSearchProvider
from app.storage import search_cache
from app.storage.profile_store import ProfileUnreadableError, load_profile

MAX_ROLES = 3


@dataclass
class DailyOutcome:
    status: str          # ok | skipped | error
    query: str = ""
    jobs: int | None = None
    note: str = ""


def daily_query() -> str:
    """JSEARCH_DAILY_QUERY, else the saved roles joined with OR (confirm OR support with jsearch_probe)."""
    if settings.jsearch_daily_query.strip():
        return settings.jsearch_daily_query.strip()
    try:
        roles = load_profile().preferred_roles
    except ProfileUnreadableError:
        roles = []
    roles = [role.strip() for role in roles if role.strip()][:MAX_ROLES] or ["software engineer"]
    return " OR ".join(roles)


async def run_daily_jsearch(*, today: date | None = None, force: bool = False) -> DailyOutcome:
    today = today or date.today()
    if settings.demo_mode:
        return DailyOutcome("skipped", note="demo mode")
    if not JSearchProvider.configured():
        return DailyOutcome("skipped", note="RAPIDAPI_KEY is not set")
    key = search_cache.daily_key(JSearchProvider.name, today.isoformat())
    if not force and search_cache.get(key, max_age_hours=36) is not None:
        return DailyOutcome("skipped", note="already fetched today")
    query = daily_query()
    try:
        jobs = await JSearchProvider().search_recent(query, date_posted=settings.jsearch_daily_date_posted)
    except ProviderError as exc:
        return DailyOutcome("error", query=query, note=str(exc))
    search_cache.put(key, JSearchProvider.name, query, jobs)
    return DailyOutcome("ok", query=query, jobs=len(jobs))
