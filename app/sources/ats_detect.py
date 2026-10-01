"""Which applicant-tracking system (ATS) a seed company's careers page uses. Detection only.

Claim level L0: a deterministic pattern match, each with the text that matched.

1. The careers URL itself is a board address (boards.greenhouse.io/<board>, ...).
2. Otherwise the careers page is read once (robots.txt honoured, one request per
   host every `delay` seconds, a 429 stops that host) and matched by the address
   it redirected to, or by a board address it embeds or links.
3. A Greenhouse, Lever, Ashby or SmartRecruiters board is then confirmed through
   its documented public API with the Company Radar adapters; job counts are
   len() of the list that API returned.

Companies without a careers URL are not checked: no board name is guessed from
a company name. Sites on the never-fetched list are not requested, directly or
through a redirect. Responses are cached on disk so a re-run sends nothing new.
"""
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.core.config import settings
from app.services.safe_http import safe_get
from app.sources.adapters import SourceError, ashby, greenhouse, lever, smartrecruiters
from app.sources.company_seed import SeedFile, SeedRow, fetch_allowed
from app.sources.fetcher import Fetched, HostBlocked, PoliteFetcher, RobotsDisallowed
from app.sources.registry import DEFAULT_INDIA_FILTER, CompanyEntry, Evidence, SourceSpec

CACHE_DIR = Path(settings.data_dir) / "ats_detect_cache"
STATUSES = ["confirmed", "detected", "board_missing", "not_detected", "not_read", "no_careers_url"]
_ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters}
_START = r"(?<![A-Za-z0-9.-])"
_HOST = r"(?P<key>[a-z0-9-]+\.{domain})"
# Order matters only for ties: the most specific board addresses come first.
_PATTERNS = [(name, re.compile(_START + pattern, re.IGNORECASE)) for name, pattern in [
    ("greenhouse", r"(?:boards|job-boards)(?:\.(?P<region>eu))?\.greenhouse\.io/embed/job_board(?:/js)?\?for=(?P<key>[A-Za-z0-9_-]+)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.(?P<region>eu))?\.greenhouse\.io/(?!embed\b)(?P<key>[A-Za-z0-9_-]+)"),
    ("lever", r"jobs\.(?:(?P<region>eu)\.)?lever\.co/(?P<key>[A-Za-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/(?P<key>[A-Za-z0-9_.%-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/(?P<key>[A-Za-z0-9_-]+)"),
    ("workday", r"(?P<key>[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)"),
    ("workable", r"apply\.workable\.com/(?P<key>[A-Za-z0-9_-]+)"),
    ("zohorecruit", _HOST.format(domain=r"zohorecruit\.(?:com|in|eu)")),
    ("darwinbox", _HOST.format(domain=r"darwinbox\.(?:in|com)")),
    ("keka", _HOST.format(domain=r"keka\.com")),
    ("eightfold", _HOST.format(domain=r"eightfold\.ai")),
    ("freshteam", _HOST.format(domain=r"freshteam\.com")),
    ("recruitee", _HOST.format(domain=r"recruitee\.com")),
    ("teamtailor", _HOST.format(domain=r"teamtailor\.com")),
    ("bamboohr", _HOST.format(domain=r"bamboohr\.com")),
    ("icims", _HOST.format(domain=r"icims\.com")),
    ("taleo", _HOST.format(domain=r"taleo\.net")),
    ("successfactors", _HOST.format(domain=r"successfactors\.(?:com|eu)")),
]]
_VENDOR_HOSTS = {"www", "app", "api", "help", "support", "docs"}     # the vendor's own site: not evidence
_SHARED_HOSTS = {"rmkcdn", "files", "static", "cdn", "assets"}       # shared asset hosts: the ATS, but no tenant


class NeverFetched(RuntimeError):
    """The address is on a site the app never fetches."""


@dataclass(frozen=True)
class Hit:
    ats: str
    key: str            # board, site or tenant host; "" when only a shared vendor host was seen
    region: str         # "eu" for an EU board, else ""
    quote: str          # the text that matched


@dataclass(frozen=True)
class Detection:
    line: int
    company: str
    list_type: str
    careers_url: str
    declared_ats: str
    status: str                 # one of STATUSES
    ats: str = ""
    key: str = ""
    method: str = ""            # url_pattern | redirect | page_link
    evidence: str = ""
    fetched: int | None = None  # len() of the job list the board's API returned
    india: int | None = None    # len() after the India filter
    note: str = ""


def _hits(text: str) -> Iterable[Hit]:
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            key = match.group("key").rstrip(".")
            label = key.split(".")[0].lower() if "." in key else ""
            if label in _VENDOR_HOSTS:
                continue
            yield Hit(name, "" if label in _SHARED_HOSTS else key, (match.groupdict().get("region") or "").lower(),
                      match.group(0)[:160])


def match_url(url: str) -> Hit | None:
    return next(iter(_hits(url)), None)


def match_page(html: str) -> Hit | None:
    """The board the page embeds or links most often; a shared vendor host counts only when no board is named."""
    hits = list(_hits(html.replace("\\/", "/")))
    hits = [hit for hit in hits if hit.key] or hits
    if not hits:
        return None
    (ats, key), _ = Counter((hit.ats, hit.key) for hit in hits).most_common(1)[0]
    return next(hit for hit in hits if (hit.ats, hit.key) == (ats, key))


async def _guarded_get(url: str, **options) -> httpx.Response:
    return await safe_get(url, redirect_allowed=fetch_allowed, **options)


class DetectFetcher(PoliteFetcher):
    """PoliteFetcher with an on-disk response cache, the redirect guard and the final address of each page."""

    def __init__(self, *, max_age_hours: float = 168.0, now: Callable[[], datetime] | None = None,
                 cache_dir: Path | None = None, get=None, **options):
        super().__init__(get=get or _guarded_get, cache_dir=cache_dir or CACHE_DIR, **options)
        self._max_age = timedelta(hours=max_age_hours)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._final: dict[str, str] = {}
        self.cache_hits = 0

    def final_url(self, url: str) -> str:
        return self._final.get(url, url)

    async def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        response = await super()._request(url, headers)
        self._final[url] = str(response.request.url)
        return response

    def _cached(self, url: str) -> dict | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            fresh = self._now() - datetime.fromisoformat(record["fetched_at"]) <= self._max_age
            return record if fresh and record["url"] == url else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def get(self, url: str, *, check_robots: bool = True, conditional: bool = False, accept: str | None = None) -> Fetched:
        if not fetch_allowed(url):
            raise NeverFetched(f"{url} is on a site that is never fetched")
        record = self._cached(url)
        if record:
            self.cache_hits += 1
            self._final[url] = record["final_url"]
            return Fetched(url, record["status"], record["body"], {}, from_cache=True)
        result = await super().get(url, check_robots=check_robots, accept=accept)
        if not fetch_allowed(self.final_url(url)):
            raise NeverFetched(f"{url} redirects to a site that is never fetched")
        if result.status_code in (200, 404, 410):
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(url).write_text(json.dumps({
                "url": url, "final_url": self.final_url(url), "status": result.status_code, "body": result.text,
                "fetched_at": self._now().isoformat()}), encoding="utf-8")
        return result


def _probe_entry(row: SeedRow, hit: Hit, checked: datetime) -> CompanyEntry:
    fields = {"greenhouse": {"board": hit.key}, "ashby": {"board": hit.key}, "smartrecruiters": {"company": hit.key},
              "lever": {"site": hit.key, "region": hit.region or "global"}}[hit.ats]
    return CompanyEntry(id="probe", name=row.company, tags=["india_product"], enabled=False,
                        source=SourceSpec.model_validate({"type": hit.ats, **fields}),
                        evidence=Evidence(method="api_probe", checked_at=checked.date()))


async def _confirm(row: SeedRow, hit: Hit, fetcher: DetectFetcher) -> tuple[str, int | None, int | None, str]:
    """(status, fetched, india, note) from the board's public API; counts are None when it gave no job list."""
    adapter = _ADAPTERS.get(hit.ats)
    if adapter is None:
        return "detected", None, None, "no public API is used for this ATS"
    if hit.ats == "greenhouse" and hit.region:
        return "detected", None, None, "EU board; its API is not probed"
    try:
        result = await adapter.fetch(_probe_entry(row, hit, fetcher._now()), fetcher)
    except SourceError as exc:
        return ("board_missing" if exc.http_status == 404 else "detected"), None, None, str(exc)
    except HostBlocked as exc:
        return "detected", None, None, str(exc)
    except Exception as exc:
        return "detected", None, None, f"board API could not be read ({type(exc).__name__})"
    return "confirmed", result.fetched, result.india, ""


_BOARD_PAGE = "https://job-boards{region}.greenhouse.io/{board}"
_BOARD_JOB = re.compile(r'<a href="https://job-boards(?:\.eu)?\.greenhouse\.io/[^"/]+/jobs/(\d+)"[^>]*>\s*<p[^>]*>.*?</p>\s*<p[^>]*>(.*?)</p>',
                        re.DOTALL)


async def _html_board(hit: Hit, fetcher: DetectFetcher) -> tuple[str, int, int] | None:
    """html_board mode: (page, job links, of which in India) from the public Greenhouse board page.

    Used only when the board's API answered 404. The page is read like any crawled page (robots.txt,
    per-host delay, cache); None when it cannot be read or lists no job."""
    url = _BOARD_PAGE.format(region=".eu" if hit.region else "", board=hit.key)
    try:
        page = await fetcher.get(url)
    except Exception:
        return None
    if page.status_code != 200:
        return None
    locations = {job_id: location for job_id, location in _BOARD_JOB.findall(page.text)}
    if not locations:
        return None
    india = re.compile(DEFAULT_INDIA_FILTER, re.IGNORECASE)
    return url, len(locations), len([place for place in locations.values() if india.search(place)])


async def detect_company(row: SeedRow, fetcher: DetectFetcher) -> Detection:
    def result(status: str, **fields) -> Detection:
        return Detection(line=row.line, company=row.company, list_type=row.list_type, careers_url=row.careers_url,
                         declared_ats=row.ats, status=status, **fields)

    if not row.careers_url:
        return result("no_careers_url")
    hit, method = match_url(row.careers_url), "url_pattern"
    evidence = f"careers_url matches '{hit.quote}'" if hit else ""
    if not hit:
        try:
            page = await fetcher.get(row.careers_url)
        except (NeverFetched, RobotsDisallowed, HostBlocked) as exc:
            return result("not_read", note=str(exc))
        except Exception as exc:
            return result("not_read", note=f"careers page could not be read ({type(exc).__name__})")
        if page.status_code != 200:
            return result("not_read", note=f"careers page returned HTTP {page.status_code}")
        final = fetcher.final_url(row.careers_url)
        hit, method = (match_url(final) if final != row.careers_url else None), "redirect"
        evidence = f"careers page redirected to {final}" if hit else ""
        if not hit:
            hit, method = match_page(page.text), "page_link"
            evidence = f"careers page contains '{hit.quote}'" if hit else ""
        if not hit:
            return result("not_detected")
    status, fetched, india, note = await _confirm(row, hit, fetcher)
    if status == "board_missing" and hit.ats == "greenhouse":
        listed = await _html_board(hit, fetcher)
        if listed:
            url, fetched, india = listed
            status, method = "confirmed", "html_board"
            evidence, note = f"board page {url} lists {fetched} job links", f"{note} The count is of job links on the board page."
    return result(status, ats=hit.ats, key=hit.key, method=method, evidence=evidence, fetched=fetched, india=india, note=note)


async def detect_all(rows: list[SeedRow], fetcher: DetectFetcher,
                     progress: Callable[[Detection], None] | None = None) -> list[Detection]:
    results = []
    for row in rows:
        found = await detect_company(row, fetcher)
        results.append(found)
        if progress:
            progress(found)
    return results


def _cell(value) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: Iterable[Iterable]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows] + [""]


def render_report(seed: SeedFile, results: list[Detection], *, run_at: datetime, requests: int, cache_hits: int,
                  seed_name: str) -> str:
    by_status = {status: [found for found in results if found.status == status] for status in STATUSES}
    found_ats = by_status["confirmed"] + by_status["detected"]
    list_types = sorted({found.list_type for found in results})
    lines = ["# ATS detection for the company seed list (Q2c)", "",
             f"Generated by `python scripts/detect_ats.py` on {run_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC from "
             f"`{seed_name}` (sha256 `{seed.sha256[:16]}`). Do not edit by hand; re-run the script.", "",
             "Claim level L0: each row is a deterministic pattern match with the text that matched. `confirmed` means the "
             "board's public API returned a job list during this run; `detected` means an ATS address was found and no API "
             "was available to confirm it; `board_missing` means the address names a board that its API answered 404 for, so "
             "the address is probably out of date. None of these says the company is hiring freshers. Job counts are `len()` of the list the API returned "
             "(for SmartRecruiters, of its India postings).", "",
             "## Seed list", "",
             f"- Rows: {len(seed.rows)}. Validation problems: {len(seed.problems)}.",
             *[f"  - {problem}" for problem in seed.problems],
             f"- Rows with a `careers_url`: {len([row for row in seed.rows if row.careers_url])}. "
             f"Rows where the list already names an ATS: {len([row for row in seed.rows if row.ats != 'unknown'])}.", "",
             "## Result", ""]
    lines += _table(["Status", "Companies", *list_types],
                    [[status, len(by_status[status]), *[len([f for f in by_status[status] if f.list_type == kind]) for kind in list_types]]
                     for status in STATUSES]
                    + [["Total", len(results), *[len([f for f in results if f.list_type == kind]) for kind in list_types]]])
    lines += [f"Requests sent: {requests}. Answers taken from the on-disk cache: {cache_hits}.", "",
              "`no_careers_url` rows were not checked: the list gives no careers address and no board name is guessed from a "
              "company name.", "", "## ATS found", ""]
    ats_counts = Counter(found.ats for found in found_ats)
    lines += _table(["ATS", "Companies", "Confirmed by API"],
                    [[name, count, len([f for f in by_status["confirmed"] if f.ats == name])] for name, count in ats_counts.most_common()])
    lines += ["## Confirmed boards", ""]
    lines += _table(["Company", "ATS", "Board", "Method", "Jobs fetched", "India"],
                    [[f.company, f.ats, f.key, f.method, f.fetched, f.india] for f in by_status["confirmed"]])
    lines += ["## Detected, not confirmed", ""]
    lines += _table(["Company", "ATS", "Board or host", "Method", "Evidence", "Note"],
                    [[f.company, f.ats, f.key or "(not identified)", f.method, f.evidence, f.note] for f in by_status["detected"]])
    lines += ["## Board address not found", ""]
    lines += _table(["Company", "ATS", "Board", "Evidence", "Note"],
                    [[f.company, f.ats, f.key, f.evidence, f.note] for f in by_status["board_missing"]])
    declared = [found for found in results if found.declared_ats != "unknown"]
    lines += ["## What the list says against what was found", ""]
    lines += _table(["Company", "List says", "Found", "Same ATS"],
                    [[f.company, f.declared_ats, f"{f.ats or 'none'} ({f.status})", "yes" if f.ats == f.declared_ats else "no"]
                     for f in declared])
    lines += ["## Page read, no ATS found", "",
              "The page was fetched and held no known board address. Pages that build their job list in the browser look "
              "like this too.", ""]
    lines += _table(["Company", "Careers URL"], [[f.company, f.careers_url] for f in by_status["not_detected"]])
    lines += ["## Not read", ""]
    lines += _table(["Company", "Careers URL", "Why"], [[f.company, f.careers_url, f.note] for f in by_status["not_read"]])
    return "\n".join(lines)
