"""Which applicant-tracking system (ATS) a seed company's careers page uses. Detection only.

Claim level L0: a deterministic pattern match, each with the text that matched.

1. The careers URL itself is a board address (boards.greenhouse.io/<board>, ...).
2. Otherwise the careers page is read once (robots.txt honoured, one request per
   host every `delay` seconds, a 429 stops that host) and matched by the address
   it redirected to, or by a board address it embeds or links.
3. A Greenhouse, Lever, Ashby or SmartRecruiters board is then read through its
   documented public API and judged by the one pollable rule
   (app/sources/board_rule.py): an India job and a posting at most 180 days old.
   Job counts are len() of the list that API returned.

Companies without a careers URL are not checked: no board name is guessed from
a company name. Sites on the never-fetched list are not requested, directly or
through a redirect. Responses are cached on disk so a re-run sends nothing new.
"""
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.core.config import settings
from app.services.safe_http import safe_get
from app.sources.board_rule import STATUSES as RULE_STATUSES
from app.sources.board_rule import BoardError, judge, read_board
from app.sources.company_seed import SeedFile, SeedRow, fetch_allowed
from app.sources.fetcher import Fetched, HostBlocked, PoliteFetcher, RobotsDisallowed
from app.sources.registry import DEFAULT_INDIA_FILTER

CACHE_DIR = Path(settings.data_dir) / "ats_detect_cache"
STATUSES = ["pollable", "stale_no_india", "detected", "board_missing", "not_detected", "not_read", "no_careers_url"]
_WITH_ADAPTER = {"greenhouse", "lever", "ashby", "smartrecruiters"}     # the Company Radar can poll these
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


class NotRequested(RuntimeError):
    """The fetcher is answering from its cache only and has no answer for this address."""


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
        self.offline = False        # True: answer from the cache only, send nothing

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
        if self.offline:
            raise NotRequested("not requested in this run and not in the cache")
        result = await super().get(url, check_robots=check_robots, accept=accept)
        if not fetch_allowed(self.final_url(url)):
            raise NeverFetched(f"{url} redirects to a site that is never fetched")
        if result.status_code in (200, 404, 410):
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(url).write_text(json.dumps({
                "url": url, "final_url": self.final_url(url), "status": result.status_code, "body": result.text,
                "fetched_at": self._now().isoformat()}), encoding="utf-8")
        return result


async def _confirm(hit: Hit, fetcher: DetectFetcher) -> tuple[str, int | None, int | None, str]:
    """(status, fetched, india, note) from the board's public API, judged by the one pollable rule.

    The name counts as verified here: the board address came from the seed list or from the company's
    own careers page, not from a guessed slug."""
    if hit.ats not in _WITH_ADAPTER:
        return "detected", None, None, "no public API is used for this ATS"
    if hit.ats == "greenhouse" and hit.region:
        return "detected", None, None, "EU board; its API is not probed"
    if not hit.key:
        return "detected", None, None, "the board is not identified"
    try:
        board = await read_board(hit.ats, hit.key, fetcher, hit.region, need_name=False)
    except BoardError as exc:
        return ("board_missing" if exc.http_status == 404 else "detected"), None, None, str(exc)
    except (HostBlocked, NotRequested) as exc:
        return "detected", None, None, str(exc)
    except Exception as exc:
        return "detected", None, None, f"board API could not be read ({type(exc).__name__})"
    if board is None:
        return "detected", None, None, "the board API returned no job list"
    status, evidence = judge(board, name_ok=True, today=fetcher._now().date())
    return status, board.total, board.india, evidence


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
        except (NeverFetched, NotRequested, RobotsDisallowed, HostBlocked) as exc:
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
    status, fetched, india, note = await _confirm(hit, fetcher)
    if status == "board_missing" and hit.ats == "greenhouse":
        listed = await _html_board(hit, fetcher)
        if listed:
            url, fetched, india = listed
            # A board page gives no posting dates and no API to poll, so it is never pollable.
            status, method = "detected", "html_board"
            evidence, note = f"board page {url} lists {fetched} job links", f"{note} The count is of job links on the board page."
    return result(status, ats=hit.ats, key=hit.key, method=method, evidence=evidence, fetched=fetched, india=india, note=note)


async def detect_all(rows: list[SeedRow], fetcher: DetectFetcher,
                     progress: Callable[[Detection], None] | None = None, only: set[str] | None = None) -> list[Detection]:
    """With `only`, the named companies are read live; every other row is answered from the cache and never requested."""
    results = []
    for row in rows:
        fetcher.offline = only is not None and row.company not in only
        found = await detect_company(row, fetcher)
        results.append(found)
        if progress:
            progress(found)
    return results


def carry_notes(results: list[Detection], notes: dict[str, dict[str, str]]) -> tuple[list[Detection], dict[str, dict[str, str]]]:
    """Keep the reason a page could not be read across runs that do not request it again.

    `notes` maps a company to {"careers_url", "note"} from earlier runs. A row this run did not request
    gets its earlier reason back when the careers_url is unchanged; the returned notes hold every
    current reason."""
    carried, current = [], {}
    for found in results:
        earlier = notes.get(found.company)
        if found.status == "not_read" and found.note.startswith("not requested in this run"):
            if earlier and earlier.get("careers_url") == found.careers_url:
                found = replace(found, note=f"{earlier['note']} (earlier run; not requested again)")
                current[found.company] = dict(earlier)
        elif found.status == "not_read":
            current[found.company] = {"careers_url": found.careers_url, "note": found.note}
        carried.append(found)
    return carried, current


def _cell(value) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: Iterable[Iterable]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows] + [""]


def render_report(seed: SeedFile, results: list[Detection], *, run_at: datetime, requests: int, cache_hits: int,
                  seed_name: str, review: list[dict[str, str]] | None = None, probe: dict | None = None,
                  baseline_pollable: int | None = None, live_rows: int | None = None) -> str:
    by_status = {status: [found for found in results if found.status == status] for status in STATUSES}
    answered = by_status["pollable"] + by_status["stale_no_india"]
    found_ats = answered + by_status["detected"]
    list_types = sorted({found.list_type for found in results})
    lines = ["# ATS detection for the company seed list (Q2c)", "",
             f"Generated by `python scripts/detect_ats.py` on {run_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC from "
             f"`{seed_name}` (sha256 `{seed.sha256[:16]}`). Do not edit by hand; re-run the script.", "",
             "Claim level L0: each row is a deterministic pattern match with the text that matched. For a board whose public "
             "API answered, one rule decides (`app/sources/board_rule.py`): `pollable` means at least one job is in India and "
             "the newest posting is at most 180 days old; `stale_no_india` means the API answered but one of those two fails, "
             "so the board stays in the seed and is left out of the count. `detected` means an ATS address was found and no API "
             "was available to confirm it; `board_missing` means the address names a board that its API answered 404 for, so "
             "the address is probably out of date. None of these says the company is hiring freshers. Job counts are `len()` "
             "of the list the API returned (for SmartRecruiters, of one page of at most 100 postings).", "",
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
    scope = "" if live_rows is None else (f" This run read {live_rows} newly filled rows live; every other row was answered "
                                          "from the cache and not requested again.")
    lines += [f"Requests sent: {requests}. Answers taken from the on-disk cache: {cache_hits}.{scope}", "",
              "`no_careers_url` rows have no page to read. " +
              ("They were probed by slug instead; see the slug probe section." if review is not None
               else "They were not checked: no board name is guessed from a company name."), "", "## ATS found", ""]
    ats_counts = Counter(found.ats for found in found_ats)
    lines += _table(["ATS", "Companies", "API answered", "Pollable"],
                    [[name, count, len([f for f in answered if f.ats == name]), len([f for f in by_status["pollable"] if f.ats == name])]
                     for name, count in ats_counts.most_common()])
    lines += ["## Pollable boards", ""]
    lines += _table(["Company", "ATS", "Board", "Method", "Jobs fetched", "India", "Evidence"],
                    [[f.company, f.ats, f.key, f.method, f.fetched, f.india, f.note] for f in by_status["pollable"]])
    lines += ["## API answered, not pollable (stale or no India job)", "",
              "These boards stay in the seed list and are left out of the pollable count.", ""]
    lines += _table(["Company", "ATS", "Board", "Jobs fetched", "India", "Why"],
                    [[f.company, f.ats, f.key, f.fetched, f.india, f.note] for f in by_status["stale_no_india"]])
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
    if review is not None:
        lines += _probe_section(seed, results, review, probe or {}, baseline_pollable)
    return "\n".join(lines)


def pollable(results: list[Detection]) -> list[Detection]:
    """Companies whose board passed the one pollable rule and that a Company Radar adapter can poll."""
    return [found for found in results if found.status == "pollable"]


def _probe_section(seed: SeedFile, results: list[Detection], review: list[dict[str, str]], probe: dict,
                   baseline_pollable: int | None) -> list[str]:
    by_rule = {status: [item for item in review if item["status"] == status] for status in RULE_STATUSES}
    confirmed = by_rule["pollable"]
    names = list(dict.fromkeys(item["ats"] for item in review))
    lines = ["## Slug probe for rows without a careers_url (Q2c2)", "",
             "`python scripts/probe_boards.py` tried up to four slugs made from each company name against the public job APIs "
             "of Greenhouse, Lever, Ashby, SmartRecruiters and Workable; no web page was read. Every hit is judged by the same "
             "rule as above. Because the slug was guessed, the name must be verified too: the API's organisation name must "
             "match, or, for Lever and Ashby, which return none, the company name must appear in the job descriptions. A hit "
             "whose name is not verified is `name_mismatch`. Every hit is in `seeds/companies_probe_review.csv`; only "
             "companies with exactly one pollable board were written into the seed list.", ""]
    lines += _table(["ATS", "Pollable", "Stale or no India", "Name mismatch"],
                    [[name, *[len([i for i in by_rule[status] if i["ats"] == name]) for status in RULE_STATUSES]] for name in names]
                    + [["Total", *[len(by_rule[status]) for status in RULE_STATUSES]]])
    stopped = "; ".join(f"{name}: {why} ({probe.get('not_sent', {}).get(name, 0)} slugs not tried)"
                        for name, why in probe.get("stopped", {}).items()) or "none"
    errors = ", ".join(f"{name} {count}" for name, count in probe.get("errors", {}).items()) or "none"
    lines += [f"Probe run {probe.get('run_at', 'unknown')}: {probe.get('companies', 0)} companies, {probe.get('with_hit', 0)} with at "
              f"least one board, {probe.get('slugs_tried', 0)} slugs tried, {probe.get('delay_seconds', '?')} s between requests to "
              f"one host. Requests sent: {probe.get('requests', 0)}; from the cache: {probe.get('cache_hits', 0)}.",
              f"APIs stopped on HTTP 429: {stopped}. Failed requests: {errors}.", ""]
    first = probe.get("first_run")
    if first:
        lines[-1:] = [f"The first run ({first.get('run_at')}) sent {first.get('requests')} requests; it stopped "
                      f"{', '.join(f'{name} ({count} slugs not tried)' for name, count in (first.get('not_sent') or {}).items()) or 'no API'}"
                      " on HTTP 429. Later runs answer from the cache and do not query a stopped API again.", ""]
    ready = pollable(results)
    before = "unknown" if baseline_pollable is None else str(baseline_pollable)
    lines += ["## Pollable companies", "",
              f"Pollable companies: before {before}, after {len(ready)}. Pollable means the board passed the rule above and "
              "the Company Radar has an adapter for it (Greenhouse, Lever, Ashby, SmartRecruiters).", ""]
    lines += _table(["List", "Pollable"], [[kind, count] for kind, count in Counter(f.list_type for f in ready).most_common()])
    city_of = {row.line: row.city_group for row in seed.rows}
    cities = Counter(city.strip() or "(not stated)" for f in ready for city in (city_of.get(f.line) or "").split(";"))
    lines += ["A company listed in two cities counts in both.", ""]
    lines += _table(["City", "Pollable"], [[city, count] for city, count in cities.most_common()])
    others = [f for f in results if f not in ready and (f.method == "html_board" or
                                                         (f.ats == "workable" and f.company in {i["company"] for i in confirmed}))]
    lines += ["Confirmed but not pollable yet (Workable has no adapter; a board read from its page has no API):", ""]
    lines += _table(["Company", "ATS", "Status", "Method"], [[f.company, f.ats, f.status, f.method] for f in others])
    return lines
