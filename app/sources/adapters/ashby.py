"""Ashby public Job Postings API (https://developers.ashbyhq.com/docs/public-job-posting-api).

GET https://api.ashbyhq.com/posting-api/job-board/{board} needs no
authentication; only postings with isListed=true are public.
"""
from app.sources.adapters import FetchResult, SourceError, posting, require_ok
from app.sources.fetcher import PoliteFetcher
from app.sources.registry import CompanyEntry

API = "https://api.ashbyhq.com/posting-api/job-board/{board}"
_WORK_MODES = {"OnSite": "onsite", "Remote": "remote", "Hybrid": "hybrid"}


def _country(address) -> str:
    return str(((address or {}).get("postalAddress") or {}).get("addressCountry") or "")


def _locations(item: dict) -> list[str]:
    places = [str(item.get("location") or ""), _country(item.get("address"))]
    for secondary in item.get("secondaryLocations") or []:
        places += [str(secondary.get("location") or ""), _country(secondary.get("address"))]
    return [place for place in places if place]


async def fetch(company: CompanyEntry, fetcher: PoliteFetcher) -> FetchResult:
    url = API.format(board=company.source.board)
    response = await fetcher.get(url, check_robots=False, accept="application/json")
    payload = require_ok(response, f"Ashby board '{company.source.board}'")
    raw = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise SourceError("Ashby returned no job list.", response.status_code)
    listed = [item for item in raw if item.get("isListed", True)]
    india = company.india_pattern()
    jobs = []
    for item in listed:
        places = _locations(item)
        if not any(india.search(place) for place in places):
            continue
        work_mode = "remote" if item.get("isRemote") else _WORK_MODES.get(str(item.get("workplaceType")))
        jobs.append(posting(company, job_id=item.get("id"), title=item.get("title"), location=", ".join(dict.fromkeys(places)),
                            description=str(item.get("descriptionPlain") or ""), url=item.get("jobUrl") or item.get("applyUrl"),
                            posted=item.get("publishedAt"), employment_type=item.get("employmentType"), work_mode=work_mode))
    return FetchResult(jobs=jobs, fetched=len(listed), india=len(jobs), complete=True, http_status=response.status_code)
