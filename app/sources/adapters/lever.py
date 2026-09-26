"""Lever Postings API (https://github.com/lever/postings-api).

GET https://api.lever.co/v0/postings/{site}?mode=json (EU: api.eu.lever.co)
needs no authentication and returns every published posting.
"""
from app.sources.adapters import FetchResult, SourceError, html_text, iso_from_millis, posting, require_ok
from app.sources.fetcher import PoliteFetcher
from app.sources.registry import CompanyEntry

API = {"global": "https://api.lever.co/v0/postings/{site}?mode=json", "eu": "https://api.eu.lever.co/v0/postings/{site}?mode=json"}


def _locations(item: dict) -> str:
    categories = item.get("categories") or {}
    return ", ".join(str(value) for value in [categories.get("location"), *(categories.get("allLocations") or [])] if value)


def _description(item: dict) -> str:
    sections = [str(item.get("descriptionPlain") or "")]
    for block in item.get("lists") or []:
        sections.append(f"{block.get('text') or ''}: {html_text(block.get('content'))}")
    sections.append(str(item.get("additionalPlain") or ""))
    return "\n".join(section for section in sections if section.strip())


async def fetch(company: CompanyEntry, fetcher: PoliteFetcher) -> FetchResult:
    url = API[company.source.region].format(site=company.source.site)
    response = await fetcher.get(url, check_robots=False, accept="application/json")
    raw = require_ok(response, f"Lever site '{company.source.site}'")
    if not isinstance(raw, list):
        raise SourceError("Lever returned no posting list.", response.status_code)
    india = company.india_pattern()
    jobs = []
    for item in raw:
        location = _locations(item)
        if not (india.search(location) or str(item.get("country") or "").upper() == "IN"):
            continue
        categories = item.get("categories") or {}
        jobs.append(posting(company, job_id=item.get("id"), title=item.get("text"), location=location or "India",
                            description=_description(item), url=item.get("hostedUrl") or item.get("applyUrl"),
                            posted=iso_from_millis(item.get("createdAt")), employment_type=categories.get("commitment"),
                            work_mode=item.get("workplaceType")))
    return FetchResult(jobs=jobs, fetched=len(raw), india=len(jobs), complete=True, http_status=response.status_code)
