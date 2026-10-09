"""Keka careers feed.

GET https://{tenant}.keka.com/careers/api/jobs/default/active needs no authentication and returns every
active job as one JSON list. It is the feed the tenant's own careers page reads, not a documented API, so
robots.txt is checked like for any crawled page and entries are marked unofficial. Checked live on disprz
and gokwik on 9 Oct 2026; a job's public page is https://{tenant}.keka.com/careers/jobdetails/{id}.

`publishedOn` is the posted date. `publishedSinceDays` is only a cross-check: a job whose two values
disagree by more than a day is counted in `date_mismatches`, and no date is ever made from the day count.
"""
from datetime import date, datetime, timezone

from app.models.schemas import JobSourceRef
from app.sources.adapters import FetchResult, SourceError, html_text, posting, require_ok
from app.sources.fetcher import PoliteFetcher
from app.sources.registry import CompanyEntry

SOURCE = "Keka"
API = "https://{tenant}.keka.com/careers/api/jobs/default/active"
JOB_PAGE = "https://{tenant}.keka.com/careers/jobdetails/{job_id}"


def location_of(item: dict) -> str:
    """"Chennai, TN, India" for each place the job lists, joined with "; "."""
    places = []
    for place in item.get("jobLocations") or []:
        if isinstance(place, dict):
            parts = [str(place.get(key) or "").strip() for key in ("city", "state", "countryName")]
            places.append(", ".join(part for part in parts if part) or str(place.get("name") or "").strip())
    return "; ".join(place for place in places if place)


def in_india(item: dict, pattern) -> bool:
    places = [place for place in item.get("jobLocations") or [] if isinstance(place, dict)]
    return any(str(place.get("countryCode") or "").upper() == "IN" for place in places) or bool(pattern.search(location_of(item)))


def posted_day(item: dict) -> date | None:
    try:
        return datetime.fromisoformat(str(item.get("publishedOn")).replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except (TypeError, ValueError):
        return None


def well_formed(item) -> bool:
    return isinstance(item, dict) and item.get("id") not in (None, "") and bool(str(item.get("title") or "").strip())


async def fetch(company: CompanyEntry, fetcher: PoliteFetcher, *, today: date | None = None) -> FetchResult:
    tenant = company.source.tenant
    response = await fetcher.get(API.format(tenant=tenant), check_robots=True, accept="application/json")
    raw = require_ok(response, f"Keka tenant '{tenant}'")
    if not isinstance(raw, list):
        raise SourceError("Keka returned no job list.", response.status_code)
    today = today or datetime.now(timezone.utc).date()
    india = company.india_pattern()
    jobs, malformed, mismatches = [], 0, 0
    for item in raw:
        if not well_formed(item):
            malformed += 1
            continue
        if not in_india(item, india):
            continue
        day = posted_day(item)
        since = item.get("publishedSinceDays")
        if day is not None and isinstance(since, int) and abs((today - day).days - since) > 1:
            mismatches += 1
        experience = str(item.get("experience") or "").strip()
        description = html_text(item.get("description")) + (f"\nExperience: {experience}" if experience else "")
        url = JOB_PAGE.format(tenant=tenant, job_id=item["id"])
        job = posting(company, job_id=item["id"], title=item.get("title"), location=location_of(item) or "India",
                      description=description, url=url, posted=item.get("publishedOn") if day is not None else None)
        salary = str(item.get("salaryRangeFormat") or "").strip()
        jobs.append(job.model_copy(update={"salary": salary or None,
                                           "sources": [JobSourceRef(source=SOURCE, url=url, source_job_id=str(item["id"]))]}))
    return FetchResult(jobs=jobs, fetched=len(raw), india=len(jobs), complete=True, http_status=response.status_code,
                       malformed=malformed, date_mismatches=mismatches)
