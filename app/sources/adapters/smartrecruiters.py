"""SmartRecruiters Posting API (https://developers.smartrecruiters.com/docs/posting-api).

Public postings need no key. The list endpoint is paginated and has no
descriptions, so each listed job is returned with details pending; the sync
calls fetch_detail() for new jobs only, capped per run.
"""
from urllib.parse import quote

from app.models.schemas import JobPosting
from app.sources.adapters import FetchResult, SourceError, html_text, posting, require_ok
from app.sources.fetcher import PoliteFetcher
from app.sources.registry import CompanyEntry

LIST = "https://api.smartrecruiters.com/v1/companies/{company}/postings?country=in&limit={limit}&offset={offset}"
DETAIL = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{job_id}"
PAGE_SIZE = 100
MAX_PAGES = 20


def _location(item: dict) -> str:
    location = item.get("location") or {}
    return ", ".join(str(value) for value in (location.get("city"), location.get("region"), "India") if value)


def _public_url(company: CompanyEntry, job_id: object) -> str:
    return f"https://jobs.smartrecruiters.com/{quote(company.source.company or '')}/{quote(str(job_id))}"


async def fetch(company: CompanyEntry, fetcher: PoliteFetcher, *, page_size: int = PAGE_SIZE) -> FetchResult:
    raw: list[dict] = []
    status = 200
    for page in range(MAX_PAGES):
        url = LIST.format(company=quote(company.source.company or ""), limit=page_size, offset=page * page_size)
        response = await fetcher.get(url, check_robots=False, accept="application/json")
        status = response.status_code
        payload = require_ok(response, f"SmartRecruiters company '{company.source.company}'")
        content = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(content, list):
            raise SourceError("SmartRecruiters returned no posting list.", status)
        raw.extend(content)
        if not content or len(raw) >= int(payload.get("totalFound") or 0):
            break
    jobs = [posting(company, job_id=item.get("id"), title=item.get("name"), location=_location(item), description="",
                    url=_public_url(company, item.get("id")), posted=item.get("releasedDate"),
                    employment_type=(item.get("typeOfEmployment") or {}).get("label"),
                    work_mode="remote" if (item.get("location") or {}).get("remote") else None)
            for item in raw if str((item.get("location") or {}).get("country") or "in").lower() == "in"]
    return FetchResult(jobs=jobs, fetched=len(raw), india=len(jobs), complete=True, http_status=status,
                       details_pending=[str(job.source_job_id) for job in jobs])


async def fetch_detail(company: CompanyEntry, fetcher: PoliteFetcher, job: JobPosting) -> JobPosting:
    """The same job with its description and canonical posting URL filled in."""
    url = DETAIL.format(company=quote(company.source.company or ""), job_id=quote(str(job.source_job_id)))
    response = await fetcher.get(url, check_robots=False, accept="application/json")
    payload = require_ok(response, f"SmartRecruiters posting {job.source_job_id}")
    sections = ((payload.get("jobAd") or {}).get("sections") or {})
    description = "\n".join(html_text(section.get("text")) for key, section in sections.items()
                            if key != "companyDescription" and isinstance(section, dict) and section.get("text"))
    detailed = posting(company, job_id=job.source_job_id, title=payload.get("name") or job.title, location=job.location,
                       description=description, url=payload.get("postingUrl") or str(job.application_url or ""),
                       posted=payload.get("releasedDate") or job.posted_date, employment_type=job.employment_type,
                       work_mode=job.work_mode)
    return detailed
