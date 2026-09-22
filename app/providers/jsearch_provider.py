import re
import httpx

from app.core.config import settings
from app.models.schemas import JobPosting
from app.providers.base import JobProvider, ProviderError
from urllib.parse import urlsplit
from app.services.job_requirements import extract_requirements


FRESHER_TERMS = [
    "fresher", "fresh graduate", "fresh graduates", "recent graduate",
    "recent graduates", "entry level", "entry-level", "graduate trainee",
    "graduate engineer trainee", "intern", "internship", "no experience",
    "0-1 year", "0–1 year", "0 to 1 year"
]

SKILL_TERMS = [
    "Python", "SQL", "FastAPI", "Flask", "Django", "Docker", "REST API",
    "PyTorch", "TensorFlow", "scikit-learn", "Pandas", "NumPy",
    "NLP", "LLM", "RAG", "Hugging Face", "AWS", "GCP", "PostgreSQL",
    "MySQL", "MongoDB", "Power BI", "Tableau", "LangChain", "LangGraph",
]


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


def _infer_experience(text: str) -> tuple[float, float | None]:
    t = text.lower()

    if any(x in t for x in [
        "no experience", "fresher", "fresh graduate", "recent graduate"
    ]):
        return 0.0, 1.0

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)",
        t,
    )
    if range_match:
        return float(range_match.group(1)), float(range_match.group(2))

    min_match = re.search(r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)", t)
    if min_match:
        return float(min_match.group(1)), None

    return 0.0, None


def _extract_skills(text: str) -> list[str]:
    low = text.lower()
    return sorted({s for s in SKILL_TERMS if s.lower() in low})


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
    from app.services.cv_parser import extract_skills
    salary = None
    if item.get("job_min_salary") is not None or item.get("job_max_salary") is not None:
        salary = " ".join(str(item.get(k) or "") for k in ("job_min_salary", "job_max_salary", "job_salary_currency", "job_salary_period")).strip()
    return JobPosting(
        company=str(item.get("employer_name") or "Unknown company")[:300], title=title,
        location=", ".join(str(item[k]) for k in ("job_city", "job_state", "job_country") if item.get(k)) or "Not specified",
        work_mode=_infer_work_mode(item), description=description,
        experience_min=exp_min, experience_max=exp_max,
        fresher_allowed=any(x in combined.lower() for x in FRESHER_TERMS),
        recent_graduate_allowed="recent graduate" in combined.lower(), graduation_years=years,
        skills=extract_skills(combined), application_url=apply_url,
        source="JSearch/RapidAPI", source_job_id=str(item["job_id"]) if item.get("job_id") else None,
        employment_type=str(item["job_employment_type"]) if item.get("job_employment_type") else None,
        salary=salary, posted_date=str(item.get("job_posted_at_datetime_utc") or item.get("job_posted_at") or "") or None,
    )


class JSearchProvider(JobProvider):
    name = "JSearch/RapidAPI"

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
            if response.status_code >= 300:
                raise ProviderError(f"JSearch request failed (HTTP {response.status_code}). Check provider access or quota.")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ProviderError("JSearch returned an invalid response.")
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("JSearch could not be reached or returned an invalid response.") from None
        results = []
        for item in _extract_items(payload):
            if not isinstance(item, dict):
                continue
            try:
                results.append(normalize_item(item))
            except (ValueError, TypeError):
                continue
        return results
