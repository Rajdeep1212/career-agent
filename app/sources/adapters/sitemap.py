"""Sitemaps plus schema.org JobPosting JSON-LD: Workday, Phenom and custom career sites.

The listing is the career site's own sitemap (named in its robots.txt); each
job page carries a JobPosting JSON-LD block, the same data the site publishes
for search engines. robots.txt is honoured for every request.

- Workday: https://{host}/{site}/siteMap.xml. Job URLs end in the requisition
  id and start with a location slug, so non-India jobs are skipped without
  opening their pages. Many Workday sitemaps are capped at 100 recent URLs; for
  those the result is incomplete and absence never closes a job (the sync
  checks the job page instead, see check_page()).
- sitemap_jsonld: sitemaps (or sitemap indexes) whose job URLs carry no
  location, so India is decided from the JSON-LD address.

Only new job pages are opened, at most `detail_cap` per run; jobs already in
the index are reused as stored, and pages already found to be outside India
are passed in as `skip` so they are not re-read every day.
"""
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from app.models.schemas import JobPosting
from app.sources.adapters import FetchResult, SourceError, html_text, posting
from app.sources.fetcher import PoliteFetcher
from app.sources.registry import CompanyEntry

DETAIL_CAP = 40            # new job pages opened per company per run
MAX_CHILD_SITEMAPS = 10    # children read from one sitemap index
_NS = re.compile(r"^\{[^}]*\}")
_MULTI_LOCATION = re.compile(r"^\d+-Locations$", re.IGNORECASE)
_INDIA_COUNTRY = {"in", "ind", "india"}
_REMOTE = "TELECOMMUTE"


def _local(tag: str) -> str:
    return _NS.sub("", tag)


def _parse_sitemap(text: str, url: str) -> tuple[list[str], list[str]]:
    """(page URLs, child sitemap URLs) of one sitemap document."""
    if re.search(r"<!(DOCTYPE|ENTITY)", text[:4096], re.IGNORECASE):
        raise SourceError(f"{url} declares a DTD; refusing to parse it.")
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise SourceError(f"{url} is not a valid sitemap.") from exc
    locations = [(element.text or "").strip() for element in root.iter() if _local(element.tag) == "loc"]
    if _local(root.tag) == "sitemapindex":
        return [], [loc for loc in locations if loc]
    return [loc for loc in locations if loc], []


async def _read_sitemap(fetcher: PoliteFetcher, url: str) -> tuple[list[str], list[str]]:
    response = await fetcher.get(url, conditional=True, accept="application/xml, text/xml")
    if response.status_code != 200:
        raise SourceError(f"Sitemap {url} returned HTTP {response.status_code}.", response.status_code)
    return _parse_sitemap(response.text, url)


async def _job_urls(company: CompanyEntry, fetcher: PoliteFetcher) -> tuple[list[str], bool]:
    """Every job URL the company's sitemaps list, in order, and whether all of them were read."""
    source = company.source
    complete = not source.sitemap_capped
    if source.type == "workday":
        sitemaps, pattern = [f"https://{source.host}/{site}/siteMap.xml" for site in source.sites], "/job/"
    else:
        sitemaps, pattern = list(source.sitemaps), source.job_url_pattern or "/job"
    urls: list[str] = []
    for sitemap in sitemaps:
        pages, children = await _read_sitemap(fetcher, sitemap)
        if len(children) > MAX_CHILD_SITEMAPS:
            complete = False
        for child in children[:MAX_CHILD_SITEMAPS]:
            child_pages, _ = await _read_sitemap(fetcher, child)
            pages += child_pages
        urls += [url for url in pages if pattern in urlsplit(url).path]
    return list(dict.fromkeys(urls)), complete


def job_id(company: CompanyEntry, url: str) -> str:
    """A stable id from the job URL: Workday's requisition id, else the first id-like path segment."""
    path = unquote(urlsplit(url).path).rstrip("/")
    if company.source.type == "workday":
        last = path.rsplit("/", 1)[-1]
        return last.rsplit("_", 1)[-1] if "_" in last else last
    pattern = company.source.job_url_pattern or "/job"
    rest = path.split(pattern, 1)[-1].strip("/")
    for segment in rest.split("/"):
        if re.search(r"\d", segment):
            return segment
    return rest or path


def _workday_slug(url: str) -> str | None:
    """The location slug of a Workday job URL (/<site>/job/<location>/<title>_<id>)."""
    parts = unquote(urlsplit(url).path).strip("/").split("/")
    if "job" in parts and len(parts) > parts.index("job") + 2:
        return parts[parts.index("job") + 1]
    return None


def _may_be_india(company: CompanyEntry, url: str) -> bool:
    if company.source.type != "workday":
        return True
    slug = _workday_slug(url)
    if slug is None or _MULTI_LOCATION.match(slug):
        return True   # decided from the page
    return bool(company.india_pattern().search(slug.replace("-", " ")))


def job_posting_ld(html: str) -> dict | None:
    """The first schema.org JobPosting object on a page."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            kind = item.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return item
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack += graph
    return None


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    return str(value or "")


def _places(ld: dict) -> tuple[list[str], bool]:
    """Human-readable places and whether any is in India by country code."""
    locations = ld.get("jobLocation") or []
    places, india_country = [], False
    for location in locations if isinstance(locations, list) else [locations]:
        address = (location or {}).get("address") or {} if isinstance(location, dict) else {}
        if isinstance(address, str):
            places.append(address)
            continue
        country = _text(address.get("addressCountry"))
        india_country = india_country or country.strip().lower() in _INDIA_COUNTRY
        parts = [_text(address.get(key)) for key in ("addressLocality", "addressRegion")]
        parts.append("India" if country.strip().lower() in _INDIA_COUNTRY else country)
        places.append(", ".join(dict.fromkeys(part for part in parts if part)))
    return [place for place in places if place], india_country


def expired(ld: dict, today: date) -> bool:
    try:
        return datetime.fromisoformat(str(ld.get("validThrough") or "").replace("Z", "+00:00")).date() < today
    except ValueError:
        return False


def is_india(company: CompanyEntry, ld: dict) -> bool:
    places, india_country = _places(ld)
    return india_country or any(company.india_pattern().search(place) for place in places)


def to_posting(company: CompanyEntry, url: str, ld: dict) -> JobPosting:
    places, _ = _places(ld)
    employment = ld.get("employmentType")
    employment = ", ".join(map(str, employment)) if isinstance(employment, list) else employment
    return posting(company, job_id=job_id(company, url), title=html_text(ld.get("title")), location="; ".join(places),
                   description=html_text(ld.get("description")), url=url, posted=ld.get("datePosted"),
                   employment_type=employment, work_mode="remote" if ld.get("jobLocationType") == _REMOTE else None)


async def fetch(company: CompanyEntry, fetcher: PoliteFetcher, *, known: dict[str, JobPosting] | None = None,
                skip: set[str] | None = None, detail_cap: int = DETAIL_CAP, today: date | None = None) -> FetchResult:
    """Listed India jobs: known ones as stored, new ones read from their pages (up to detail_cap)."""
    known, skip, today = known or {}, skip or set(), today or date.today()
    urls, complete = await _job_urls(company, fetcher)
    jobs: list[JobPosting] = []
    rejected: list[str] = []
    deferred = opened = 0
    status = 200
    for url in urls:
        identifier = job_id(company, url)
        if identifier in known:
            jobs.append(known[identifier])
            continue
        if identifier in skip or not _may_be_india(company, url):
            continue
        if opened >= detail_cap:
            deferred += 1
            continue
        opened += 1
        response = await fetcher.get(url, accept="text/html")
        status = response.status_code
        ld = job_posting_ld(response.text) if response.status_code == 200 else None
        if ld is None:
            continue   # not readable today; tried again tomorrow
        if not is_india(company, ld) or expired(ld, today):
            rejected.append(identifier)
            continue
        jobs.append(to_posting(company, url, ld))
    return FetchResult(jobs=jobs, fetched=len(urls), india=len(jobs), complete=complete, http_status=status,
                       rejected=rejected, deferred=deferred)


async def check_page(company: CompanyEntry, fetcher: PoliteFetcher, job: JobPosting, *, today: date | None = None) -> str | None:
    """Why a job missing from a capped sitemap is closed, or None when its page still shows it."""
    today = today or date.today()
    url = str(job.application_url or "")
    response = await fetcher.get(url, accept="text/html")
    if response.status_code in (404, 410):
        return f"The job page returned HTTP {response.status_code} (checked {today.isoformat()})."
    if response.status_code != 200:
        raise SourceError(f"Job page returned HTTP {response.status_code}.", response.status_code)
    ld = job_posting_ld(response.text)
    if ld is None:
        return f"The job page no longer describes an open posting (checked {today.isoformat()})."
    if expired(ld, today):
        return f"The job page says applications closed on {str(ld.get('validThrough'))[:10]}."
    return None
