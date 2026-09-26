"""Greenhouse Job Board API (https://docs.greenhouse.io/job-board.html).

GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
needs no authentication and returns every open job with its description.
"""
from app.sources.adapters import FetchResult, SourceError, html_text, posting, require_ok
from app.sources.fetcher import PoliteFetcher
from app.sources.registry import CompanyEntry

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


async def fetch(company: CompanyEntry, fetcher: PoliteFetcher) -> FetchResult:
    url = API.format(board=company.source.board)
    response = await fetcher.get(url, check_robots=False, accept="application/json")
    payload = require_ok(response, f"Greenhouse board '{company.source.board}'")
    raw = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise SourceError("Greenhouse returned no job list.", response.status_code)
    india = company.india_pattern()
    jobs = []
    for item in raw:
        location = str((item.get("location") or {}).get("name") or "")
        if not india.search(location):
            continue
        jobs.append(posting(company, job_id=item.get("id"), title=item.get("title"), location=location,
                            description=html_text(item.get("content")), url=item.get("absolute_url"),
                            posted=item.get("first_published") or item.get("updated_at")))
    return FetchResult(jobs=jobs, fetched=len(raw), india=len(jobs), complete=True, http_status=response.status_code)
