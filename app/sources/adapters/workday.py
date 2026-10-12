"""Workday careers feed ("cxs"), opt-in per company with `source.cxs`; the sitemap reader stays the default.

POST https://{host}/wday/cxs/{tenant}/{site}/jobs is the search the tenant's own careers page runs, not a
documented API, so robots.txt is checked for every request and entries are marked unofficial. Recorded on
six tenants on 9 Oct 2026.

- A page holds 20 jobs; a larger `limit` returns no jobs and no error. `total` stops at 2,000, so a board
  that reports 2,000 is not complete and a job's absence never closes it.
- When the first answer offers a country filter with India in it, the listing is asked again for India only.
  Without one, only jobs whose location reads as India are opened. Either way a job is kept only when its
  own detail places it in India, so a tenant that ignores the filter adds no foreign jobs.
- `postedOn` is display text ("Posted 14 Days Ago") and never becomes a date: the posted date is `startDate`
  from the detail endpoint, GET https://{host}/wday/cxs/{tenant}/{site}{externalPath}, read for new jobs only.

Job ids follow the sitemap reader's rule, so a company moved from the sitemap to this feed keeps its jobs.
"""
from datetime import date

from app.models.schemas import JobPosting, JobSourceRef
from app.sources.adapters import FetchResult, SourceError, html_text, posting, require_ok, sitemap
from app.sources.fetcher import PoliteFetcher
from app.sources.registry import CompanyEntry

SOURCE = "Workday"
API = "https://{host}/wday/cxs/{tenant}/{site}"
PAGE = 20           # a larger limit returns no jobs and no error
MAX_PAGES = 10      # listing pages per site per run
CEILING = 2000      # the largest total Workday reports
DETAIL_CAP = 40     # new job details opened per company per run
_WORK_MODES = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}


def india_filter(facets) -> tuple[dict, object] | None:
    """({facet name: [India's id]}, the board's count of India jobs) from the filters a listing offers; tenants name and nest the country facet differently."""
    for facet in facets if isinstance(facets, list) else []:
        if not isinstance(facet, dict):
            continue
        values = facet.get("values") or []
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict) and value.get("descriptor") == "India" and value.get("id") and facet.get("facetParameter"):
                return {str(facet["facetParameter"]): [str(value["id"])]}, value.get("count")
        nested = india_filter(values)
        if nested:
            return nested
    return None


async def _listing(fetcher: PoliteFetcher, url: str, what: str) -> tuple[list, bool, bool, int]:
    """(listed records, whether that is the whole board, whether Workday filtered it to India, HTTP status)."""
    applied: dict = {}
    expected = None
    items: list = []
    total = None
    page = 0
    while page < MAX_PAGES:
        response = await fetcher.post(url, {"appliedFacets": applied, "limit": PAGE, "offset": page * PAGE, "searchText": ""},
                                      check_robots=True, accept="application/json")
        payload = require_ok(response, what)
        postings = payload.get("jobPostings") if isinstance(payload, dict) else None
        if not isinstance(postings, list):
            raise SourceError(f"{what} returned no job list.", response.status_code)
        if page == 0:
            if not applied:
                applied, expected = india_filter(payload.get("facets")) or ({}, None)
                if applied:
                    continue        # the same first page again, India only
            elif not postings and type(expected) is int and expected > 0:
                # Read as a whole, empty board this answer would close every stored job.
                raise SourceError(f"{what} listed no India job though its own filter counts {expected}.", response.status_code)
            total = payload.get("total")        # the first page's count is the one used
        items += postings
        page += 1
        if len(postings) < PAGE or (isinstance(total, int) and page * PAGE >= total):
            # Whole only when as many jobs were read as the board says it has: a short or empty answer is not a closed board.
            return items, type(total) is int and len(items) >= total < CEILING, bool(applied), response.status_code
    return items, False, bool(applied), response.status_code


def _places(info: dict) -> list[str]:
    more = info.get("additionalLocations")
    return [str(place).strip() for place in [info.get("location"), *(more if isinstance(more, list) else [])] if str(place or "").strip()]


def _country(info: dict) -> str:
    country = info.get("country")
    return str(country.get("descriptor") or "").strip() if isinstance(country, dict) else ""


def _in_india(company: CompanyEntry, info: dict) -> bool:
    country, places = _country(info).lower(), _places(info)
    if country == "india":
        return True
    # The country is that of the first location: when it names another country, only the further locations can be in India.
    return any(company.india_pattern().search(place) for place in (places[1:] if country and info.get("location") else places))


def _posted(info: dict) -> str | None:
    try:
        return date.fromisoformat(str(info.get("startDate"))).isoformat()
    except ValueError:
        return None


def _posting(company: CompanyEntry, identifier: str, url: str, item: dict, info: dict) -> JobPosting:
    places, country = _places(info), _country(info)
    if places and country and country.lower() not in places[0].lower():
        places[0] = f"{places[0]}, {country}"       # the country is Workday's own field for the first location
    job = posting(company, job_id=identifier, title=info.get("title") or item.get("title"), location="; ".join(places) or country,
                  description=html_text(info.get("jobDescription")), url=url, posted=_posted(info),
                  employment_type=info.get("timeType") or item.get("timeType") or None,
                  work_mode=_WORK_MODES.get(str(info.get("remoteType") or item.get("remoteType") or "").lower()))
    return job.model_copy(update={"sources": [JobSourceRef(source=SOURCE, url=url, source_job_id=identifier)]})


def well_formed(item) -> bool:
    """A record with a title and a plain /job/ path; the path becomes part of two URLs, so nothing else is accepted."""
    path = item.get("externalPath") if isinstance(item, dict) else None
    return (isinstance(path, str) and path.startswith("/job/") and ".." not in path
            and not any(char in "\\?#" or ord(char) <= 32 for char in path) and bool(str(item.get("title") or "").strip()))


async def fetch(company: CompanyEntry, fetcher: PoliteFetcher, *, known: dict[str, JobPosting] | None = None,
                skip: set[str] | None = None, detail_cap: int = DETAIL_CAP) -> FetchResult:
    """Listed India jobs: known ones as stored, new ones read from the detail endpoint (up to detail_cap)."""
    known, skip = known or {}, skip or set()
    host = company.source.host or ""
    jobs: list[JobPosting] = []
    rejected: list[str] = []
    seen: set[str] = set()
    fetched = malformed = deferred = opened = 0
    complete, status = True, 200
    for site in company.source.sites:
        api = API.format(host=host, tenant=host.split(".")[0], site=site)
        items, whole, filtered, status = await _listing(fetcher, f"{api}/jobs", f"Workday site '{site}' of {host}")
        fetched += len(items)
        complete = complete and whole
        for item in items:
            if not well_formed(item):
                malformed += 1
                continue
            path = item["externalPath"]
            identifier = sitemap.job_id(company, path)
            if identifier in seen:
                continue
            seen.add(identifier)
            if identifier in known:
                jobs.append(known[identifier])
                continue
            if identifier in skip or not (filtered or sitemap._may_be_india(company, path)):
                continue
            if opened >= detail_cap:
                deferred += 1
                continue
            opened += 1
            response = await fetcher.get(api + path, check_robots=True, accept="application/json")
            try:
                info = response.json().get("jobPostingInfo") if response.status_code == 200 else None
            except (ValueError, AttributeError):
                info = None
            if not isinstance(info, dict):
                continue        # not readable today; tried again tomorrow
            if not _in_india(company, info):
                rejected.append(identifier)
                continue
            jobs.append(_posting(company, identifier, f"https://{host}/{site}{path}", item, info))
    return FetchResult(jobs=jobs, fetched=fetched, india=len(jobs), complete=complete, http_status=status,
                       rejected=rejected, deferred=deferred, malformed=malformed)
