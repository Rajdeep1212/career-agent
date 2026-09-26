"""Jobs from your alert emails and the bookmarklet, as a local provider (no requests).

Matching works like the Company Radar provider: a job is returned when its title
matches a planned role (the phrase or its role family) and, for a city query,
its location. Identity resolution in the search then folds jobs that match an
official Radar job into it; the rest keep their honest source ("LinkedIn alert",
"Saved by you") and stay unverified, because those pages are never fetched.
"""
from app.models.career import SearchQuery
from app.models.schemas import JobPosting
from app.providers.base import JobProvider
from app.providers.radar_provider import MAX_RESULTS, _family, _location_pattern, _matches
from app.storage import alert_store

ALERTS_NAME = "Alert emails and saved jobs"


class AlertsProvider(JobProvider):
    name = ALERTS_NAME
    local = True   # reads the local alert store: no requests, no quota

    @classmethod
    def configured(cls) -> bool:
        return alert_store.has_jobs()

    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        return self.search_local([SearchQuery(query=query, reason="", role=query, location="")])

    def search_local(self, queries: list[SearchQuery]) -> list[JobPosting]:
        plans = [(query.role or query.query, _family(query.role or query.query), _location_pattern(query.location))
                 for query in queries]
        found = [job for job in alert_store.list_jobs()
                 if any(_matches(job, role, family, place) for role, family, place in plans)]
        return found[:MAX_RESULTS]
