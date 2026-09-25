import re
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import settings
from app.models.schemas import JobPosting
from app.providers.base import JobProvider, ProviderError
from urllib.parse import urlsplit
from app.services.job_requirements import extract_requirements
from app.services.skills import extract_skills


FRESHER_TERMS = [
    "fresher", "fresh graduate", "fresh graduates", "recent graduate",
    "recent graduates", "entry level", "entry-level", "graduate trainee",
    "graduate engineer trainee", "intern", "internship", "no experience",
    "0-1 year", "0–1 year", "0 to 1 year"
]
# Whole phrases only: "intern" must not match "international" or "internal".
_FRESHER_PATTERN = re.compile(
    r"(?<![\w-])(?:" + "|".join(re.escape(term) for term in FRESHER_TERMS) + r")s?(?![\w-])",
    re.IGNORECASE,
)

def _infer_work_mode(job: dict) -> str:
    if job.get("job_is_remote") is True:
        return "remote"

    text = " ".join(
        str(job.get(k) or "")
        for k in ["job_title", "job_description", "job_employment_type"]
    ).lower()

    if re.search(r"\bhybrid\b", text):
        return "hybrid"
    if re.search(r"\b(?:no|not(?: a| fully)?|without)\s+(?:remote|work from home|wfh)\b|\bremote\s+(?:work\s+)?(?:is\s+)?not\s+(?:available|allowed|offered)\b", text):
        return "onsite"
    mode_text = re.sub(r"\bremote\s+(?:collaboration|team|communication|tools?)\b[^.;\n]*", "", text)
    if re.search(r"\bremote\b|\bwork from home\b|\bwfh\b", mode_text):
        return "remote"
    if re.search(r"\bon[- ]?site\b|\bin[- ]office\b|\bwork(?:ing)? from (?:the )?office\b", text):
        return "onsite"
    return "unknown"


def _extract_items(payload: dict) -> list[dict]:
    """
    JSearch /search-v2 may return:
      {"data": {"jobs": [...], "cursor": "..."}}
    while older shapes returned:
      {"data": [...]}

    Support both so future provider-side changes are less brittle.
    """
    data = payload.get("data") or []

    if isinstance(data, dict):
        jobs = data.get("jobs") or []
        return jobs if isinstance(jobs, list) else []

    return data if isinstance(data, list) else []


def normalize_item(item: dict) -> JobPosting:
    description = str(item.get("job_description") or "")[:60000]
    title = str(item.get("job_title") or "Unknown role")[:300]
    combined = title + "\n" + description
    exp_min, exp_max, years = extract_requirements(combined)
    apply_url = item.get("job_apply_link") or item.get("job_google_link")
    if not apply_url:
        options = item.get("apply_options")
        if isinstance(options, list):
            apply_url = next((o.get("apply_link") for o in options if isinstance(o, dict) and o.get("apply_link")), None)
    try:
        if not isinstance(apply_url, str):
            apply_url = None
        parsed = urlsplit(apply_url or "")
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            apply_url = None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            apply_url = None
    except ValueError:
        apply_url = None
    salary = None
    if item.get("job_min_salary") is not None or item.get("job_max_salary") is not None:
        salary = " ".join(str(item.get(k) or "") for k in ("job_min_salary", "job_max_salary", "job_salary_currency", "job_salary_period")).strip()
    return JobPosting(
        company=str(item.get("employer_name") or "Unknown company")[:300], title=title,
        location=", ".join(str(item[k]) for k in ("job_city", "job_state", "job_country") if item.get(k)) or "Not specified",
        work_mode=_infer_work_mode(item), description=description,
        experience_min=exp_min, experience_max=exp_max,
        fresher_allowed=bool(_FRESHER_PATTERN.search(combined)),
        recent_graduate_allowed="recent graduate" in combined.lower(), graduation_years=years,
        skills=extract_skills(combined), application_url=apply_url,
        source="JSearch/RapidAPI", source_job_id=str(item["job_id"]) if item.get("job_id") else None,
        employment_type=str(item["job_employment_type"]) if item.get("job_employment_type") else None,
        salary=salary, posted_date=str(item.get("job_posted_at_datetime_utc") or item.get("job_posted_at") or "") or None,
    )


# Reasons are chosen here from the status code; provider response bodies never reach users.
_STATUS_REASONS = {
    401: "invalid or missing RAPIDAPI_KEY",
    403: "not subscribed to JSearch, or access denied",
    429: "quota or rate limit exceeded",
}
_last_quota: dict | None = None


def _status_reason(status: int) -> str:
    return _STATUS_REASONS.get(status) or ("provider server error" if status >= 500 else "request rejected")


def _header_int(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name, "").strip()
    return int(value) if value.isdigit() else None


def _quota(response: httpx.Response) -> dict:
    """RapidAPI quota headers; the reset header counts seconds from now."""
    seconds = _header_int(response, "x-ratelimit-requests-reset")
    if seconds is None:
        seconds = _header_int(response, "retry-after")
    reset_at = None
    if seconds is not None:
        reset_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()
    return {"reset_at": reset_at, "remaining": _header_int(response, "x-ratelimit-requests-remaining"),
            "limit": _header_int(response, "x-ratelimit-requests-limit")}


def _remember_quota(quota: dict) -> None:
    global _last_quota
    if quota["remaining"] is not None or quota["reset_at"]:
        _last_quota = {**quota, "observed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}


def last_quota() -> dict | None:
    """Quota from the most recent JSearch response in this process, if any."""
    return dict(_last_quota) if _last_quota else None


def format_reset(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")


class JSearchProvider(JobProvider):
    name = "JSearch/RapidAPI"

    @classmethod
    def configured(cls) -> bool:
        return bool(settings.rapidapi_key)

    def quota(self) -> dict | None:
        """Quota reported by RapidAPI on the last response in this process, if any."""
        quota = last_quota()
        return {**quota, "window": None, "counted_locally": False} if quota else None

    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        if not settings.rapidapi_key:
            raise ProviderError("JSearch is not configured. Add credentials in the local .env.")
        # Fixed credential destination: never send an API key to a configurable arbitrary host.
        if settings.rapidapi_host != "jsearch.p.rapidapi.com":
            raise ProviderError("JSearch host must be jsearch.p.rapidapi.com.")
        headers = {"X-RapidAPI-Key": settings.rapidapi_key,
                   "X-RapidAPI-Host": "jsearch.p.rapidapi.com", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, verify=True,
                                         follow_redirects=False, trust_env=False) as client:
                response = await client.get("https://jsearch.p.rapidapi.com/search-v2", headers=headers,
                    params={"query": query[:180], "num_pages": 1, "country": settings.search_country,
                            "language": "en", "date_posted": "all"})
        except httpx.TimeoutException:
            raise ProviderError("JSearch request timed out.", reason="timed out") from None
        except httpx.HTTPError:
            raise ProviderError("JSearch could not be reached.", reason="could not be reached") from None
        quota = _quota(response)
        _remember_quota(quota)
        if response.status_code >= 300:
            reason = _status_reason(response.status_code)
            reset = f"; resets {format_reset(quota['reset_at'])}" if quota["reset_at"] else ""
            raise ProviderError(f"JSearch request failed (HTTP {response.status_code}: {reason}{reset}).",
                                status_code=response.status_code, reason=reason, **quota)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise ProviderError("JSearch returned an invalid response.", status_code=response.status_code,
                                reason="invalid response")
        results = []
        for item in _extract_items(payload):
            if not isinstance(item, dict):
                continue
            try:
                results.append(normalize_item(item))
            except (ValueError, TypeError):
                continue
        return results
