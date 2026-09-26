"""Adzuna job search API (https://developer.adzuna.com/).

Terms that shape this adapter (https://developer.adzuna.com/docs/terms_of_service):
- Every displayed Adzuna job must be labeled "Jobs by Adzuna" with a link to
  Adzuna; the dashboard renders that label for jobs whose source is Adzuna.
- Default limits are 25 calls a minute and 250 a day (1,000 a week, 2,500 a month).
- Predicted ("Jobsworth") salaries need their own attribution, so they are dropped.
- Job links are Adzuna redirect URLs; they are opened only when the user clicks them.
"""
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import settings
from app.models.schemas import JobPosting
from app.providers.base import JobProvider, ProviderError
from app.providers.common import build_posting, split_location
from app.storage import provider_usage

_BASE_URL = "https://api.adzuna.com/v1/api/jobs"
_COUNTRY_NAMES = ("india", "in")
_STATUS_REASONS = {
    400: "request rejected",
    401: "invalid ADZUNA_APP_ID or ADZUNA_APP_KEY",
    403: "access denied; check ADZUNA_APP_ID and ADZUNA_APP_KEY",
    429: "rate limit exceeded (default 25 a minute, 250 a day)",
}


def _next_utc(period: str) -> str:
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return (now + timedelta(days=1)).isoformat()
    return (now.replace(day=1) + timedelta(days=32)).replace(day=1).isoformat()


def normalize_item(item: dict) -> JobPosting:
    company: dict = item["company"] if isinstance(item.get("company"), dict) else {}
    location: dict = item["location"] if isinstance(item.get("location"), dict) else {}
    salary = None
    # Predicted salaries require separate "Adzuna Jobsworth" attribution; keep advertised ones only.
    if str(item.get("salary_is_predicted", "0")) != "1" and (item.get("salary_min") or item.get("salary_max")):
        salary = " ".join(str(item.get(key) or "") for key in ("salary_min", "salary_max")).strip()
    employment = " ".join(str(item.get(key) or "") for key in ("contract_time", "contract_type")).strip()
    return build_posting(
        source="Adzuna", title=item.get("title"), company=company.get("display_name"),
        location=location.get("display_name"), description=item.get("description"),
        url=item.get("redirect_url"), job_id=item.get("id"), posted=item.get("created"),
        employment_type=employment.replace("_", " ") or None, salary=salary,
    )


class AdzunaProvider(JobProvider):
    name = "Adzuna"

    def __init__(self):
        # Identical (what, where) pairs in one search are requested once.
        self._seen: dict[tuple[str, str], list[JobPosting]] = {}

    @classmethod
    def configured(cls) -> bool:
        return bool(settings.adzuna_app_id and settings.adzuna_app_key)

    def quota(self) -> dict:
        """Remaining requests by local count: the tighter of the daily and monthly limits."""
        used = provider_usage.usage("adzuna", settings.adzuna_app_key or "")
        daily = settings.adzuna_daily_limit - used["today"]
        monthly = settings.adzuna_monthly_limit - used["month"]
        if daily <= monthly:
            return {"remaining": max(0, daily), "limit": settings.adzuna_daily_limit, "window": "today (UTC)",
                    "reset_at": _next_utc("day"), "counted_locally": True}
        return {"remaining": max(0, monthly), "limit": settings.adzuna_monthly_limit, "window": "this month (UTC)",
                "reset_at": _next_utc("month"), "counted_locally": True}

    def usage_text(self) -> str:
        used = provider_usage.usage("adzuna", settings.adzuna_app_key or "")
        return (f"{used['today']}/{settings.adzuna_daily_limit} today, "
                f"{used['month']}/{settings.adzuna_monthly_limit} this month (counted on this machine)")

    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        return await self._search(query, "", page)

    async def search_planned(self, planned) -> list[JobPosting]:
        where, remote = split_location(planned.location, _COUNTRY_NAMES)
        what = f"{planned.role} remote" if remote else planned.role
        return await self._search(what or planned.query, where)

    async def _search(self, what: str, where: str, page: int = 1) -> list[JobPosting]:
        if not self.configured():
            raise ProviderError("Adzuna is not configured. Add ADZUNA_APP_ID and ADZUNA_APP_KEY in the local .env.")
        country = settings.search_country.strip().lower()
        key = (what.casefold(), where.casefold())
        if key in self._seen:
            return [job.model_copy(deep=True) for job in self._seen[key]]
        params = {"app_id": settings.adzuna_app_id, "app_key": settings.adzuna_app_key,
                  "what": what[:180], "results_per_page": 20, "content-type": "application/json"}
        if where:
            params["where"] = where[:100]
        provider_usage.record("adzuna", settings.adzuna_app_key or "")
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, verify=True,
                                         follow_redirects=False, trust_env=False) as client:
                response = await client.get(f"{_BASE_URL}/{country}/search/{int(page)}", params=params,
                                            headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise ProviderError("Adzuna request timed out.", reason="timed out") from None
        except httpx.HTTPError:
            raise ProviderError("Adzuna could not be reached.", reason="could not be reached") from None
        if response.status_code >= 300:
            status = response.status_code
            reason = _STATUS_REASONS.get(status) or ("provider server error" if status >= 500 else "request rejected")
            raise ProviderError(f"Adzuna request failed (HTTP {status}: {reason}).", status_code=status, reason=reason)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        items = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ProviderError("Adzuna returned an invalid response.", status_code=response.status_code,
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
