"""Jooble REST API (https://help.jooble.org/en/support/solutions/articles/60001448238).

- Each country site issues its own key; India keys work on in.jooble.org (JOOBLE_HOST).
- The free plan allows 500 requests per key in total, not per month.
- Results are snippets, and each job link goes through Jooble. Links are opened
  only when the user clicks them, and the dashboard credits Jooble on each job.
"""
import httpx

from app.core.config import settings
from app.models.schemas import JobPosting
from app.providers.base import JobProvider, ProviderError
from app.providers.common import build_posting, split_location
from app.storage import provider_usage

_COUNTRY_NAMES = ("india", "in")
_STATUS_REASONS = {
    400: "request rejected",
    401: "invalid JOOBLE_API_KEY, or the key is for another country site",
    403: "invalid JOOBLE_API_KEY, or the key is for another country site",
    404: "invalid JOOBLE_API_KEY, or the key is for another country site",
    429: "rate limit exceeded",
}


def normalize_item(item: dict) -> JobPosting:
    return build_posting(
        source="Jooble", title=item.get("title"), company=item.get("company"),
        location=item.get("location"), description=item.get("snippet"), url=item.get("link"),
        job_id=item.get("id"), posted=item.get("updated"), employment_type=item.get("type"),
        salary=item.get("salary"),
    )


class JoobleProvider(JobProvider):
    name = "Jooble"

    def __init__(self):
        # Identical (keywords, location) pairs in one search are requested once.
        self._seen: dict[tuple[str, str], list[JobPosting]] = {}

    @classmethod
    def configured(cls) -> bool:
        return bool(settings.jooble_api_key)

    def quota(self) -> dict:
        """Remaining requests for this key by local count; the free plan never resets."""
        used = provider_usage.usage("jooble", settings.jooble_api_key or "")
        return {"remaining": max(0, settings.jooble_key_limit - used["total"]), "limit": settings.jooble_key_limit,
                "window": "for this key", "reset_at": None, "counted_locally": True}

    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        return await self._search(query, "", page)

    async def search_planned(self, planned) -> list[JobPosting]:
        location, remote = split_location(planned.location, _COUNTRY_NAMES)
        keywords = f"{planned.role} remote" if remote else planned.role
        return await self._search(keywords or planned.query, location)

    async def _search(self, keywords: str, location: str, page: int = 1) -> list[JobPosting]:
        if not self.configured():
            raise ProviderError("Jooble is not configured. Add JOOBLE_API_KEY in the local .env.")
        key = (keywords.casefold(), location.casefold())
        if key in self._seen:
            return [job.model_copy(deep=True) for job in self._seen[key]]
        body = {"keywords": keywords[:180], "location": location[:100], "page": str(int(page))}
        provider_usage.record("jooble", settings.jooble_api_key)
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, verify=True,
                                         follow_redirects=False, trust_env=False) as client:
                response = await client.post(f"https://{settings.jooble_host}/api/{settings.jooble_api_key}",
                                             json=body, headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise ProviderError("Jooble request timed out.", reason="timed out") from None
        except httpx.HTTPError:
            raise ProviderError("Jooble could not be reached.", reason="could not be reached") from None
        if response.status_code >= 300:
            status = response.status_code
            reason = _STATUS_REASONS.get(status) or ("provider server error" if status >= 500 else "request rejected")
            raise ProviderError(f"Jooble request failed (HTTP {status}: {reason}).", status_code=status, reason=reason)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        items = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ProviderError("Jooble returned an invalid response.", status_code=response.status_code,
                                reason="invalid response")
        results = []
        for item in items:
            if isinstance(item, dict):
                try:
                    results.append(normalize_item(item))
                except (ValueError, TypeError):
                    continue
        self._seen[key] = results
        return [job.model_copy(deep=True) for job in results]
