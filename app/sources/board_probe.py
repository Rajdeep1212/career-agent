"""Careers boards for seed companies that list no careers_url (docs/ROADMAP_QUEUE.md Q2c2).

A few slugs are made from the company name and tried against public job APIs only: Greenhouse,
Lever, Ashby, SmartRecruiters and the Workable careers widget. No web page is read. Claim level L0.

A slug that answers with jobs is a hit, and a hit is only as good as its evidence:

- `confirmed`: the API returned jobs, the name matches, and at least one job is in India.
- `probable`: the API returned jobs, but the name does not fully match or no job is in India.

Name evidence: Greenhouse, SmartRecruiters and Workable return the organisation's name. Lever and
Ashby return none, so there the company's name must appear in the job descriptions; a slug that
merely equals the name proves nothing, because the slug was made from the name.

Requests go through DetectFetcher: one request per host every `delay` seconds, a 429 stops that
API for the rest of the run, and every answer (404s too) is cached on disk.
"""
import asyncio
import csv
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from app.sources.adapters import ashby, lever
from app.sources.ats_detect import DetectFetcher
from app.sources.company_seed import SeedRow
from app.sources.fetcher import HostBlocked
from app.sources.registry import DEFAULT_INDIA_FILTER

ATS = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable"]
MAX_SLUGS = 4
REVIEW_COLUMNS = ["company", "city", "ats", "slug", "status", "jobs_total", "jobs_india", "evidence"]
BOARD_URL = {"greenhouse": "https://boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
             "ashby": "https://jobs.ashbyhq.com/{}", "smartrecruiters": "https://jobs.smartrecruiters.com/{}",
             "workable": "https://apply.workable.com/{}"}
_LEGAL = {"pvt", "ltd", "limited", "private", "inc", "llp"}
_SOFT = {"technologies", "technology", "tech", "labs", "lab", "software", "systems", "solutions", "india", "global", "group",
         "services", "company", "co", "corp", "corporation", "ai", "io", "app", "hq"}
_INDIA = re.compile(DEFAULT_INDIA_FILTER, re.IGNORECASE)


def _words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.casefold()) if word not in _LEGAL]


def _core(words: list[str]) -> list[str]:
    """The name without a leading 'the' and trailing words such as 'labs' or 'technologies'."""
    core = words[1:] if len(words) > 1 and words[0] == "the" else list(words)
    while len(core) > 1 and core[-1] in _SOFT:
        core.pop()
    return core


def _parts(company: str) -> tuple[list[str], list[str]]:
    """(words of the main name, words of the bracketed short name): 'Tata Consultancy Services (TCS)'."""
    return _words(re.sub(r"\(.*?\)", " ", company)), _words(" ".join(re.findall(r"\((.*?)\)", company)))


def slug_candidates(company: str) -> list[str]:
    """Up to four distinct slugs: the full name, the name without common suffixes, hyphenated, the short name."""
    main, alias = _parts(company)
    slugs = ["".join(main), "".join(_core(main)), "-".join(main) if len(main) > 1 else "", "".join(alias)]
    return [slug for slug in dict.fromkeys(slugs) if len(slug) >= 3][:MAX_SLUGS]


def name_match(company: str, found: str) -> str:
    """'exact', 'partial' or 'none': how the name an API returned compares with the seed company's name."""
    main, alias = _parts(company)
    theirs = _words(re.sub(r"\(.*?\)", " ", found or ""))
    if not main or not theirs:
        return "none"
    ours = {"".join(main), "".join(_core(main)), "".join(alias)} - {""}
    if {"".join(theirs), "".join(_core(theirs))} & ours:
        return "exact"
    first, other = _core(main)[0], _core(theirs)[0]
    longest = max(ours, key=len)
    if (first == other and len(first) >= 4) or (len("".join(theirs)) >= 4 and "".join(theirs) in longest) or \
            (len(longest) >= 4 and longest in "".join(theirs)):
        return "partial"
    return "none"


@dataclass(frozen=True)
class ProbeHit:
    line: int
    company: str
    list_type: str
    city: str
    ats: str
    slug: str
    status: str             # confirmed | probable
    jobs_total: int         # len() of the job list the API returned
    jobs_india: int         # len() of the jobs whose location is in India
    evidence: str
    board_url: str


@dataclass
class ProbeRun:
    hits: list[ProbeHit] = field(default_factory=list)
    companies: int = 0
    with_hit: int = 0
    slugs_tried: int = 0
    stopped: dict[str, str] = field(default_factory=dict)       # ats -> why its API was stopped (HTTP 429)
    not_sent: dict[str, int] = field(default_factory=dict)      # ats -> slugs not tried because the API was stopped
    errors: dict[str, int] = field(default_factory=dict)        # ats -> requests that failed (timeout, bad answer)


@dataclass
class _Board:
    total: int
    india: int
    name: str | None        # the organisation name the API returned; None when it returns none
    texts: list[str]        # job descriptions, for the APIs that return no name
    key: str                # the board id for the public URL


async def _json(fetcher: DetectFetcher, url: str):
    response = await fetcher.get(url, check_robots=False, accept="application/json")
    return response.json() if response.status_code == 200 else None


async def _greenhouse(slug: str, fetcher: DetectFetcher) -> _Board | None:
    payload = await _json(fetcher, f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs?content=true")
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        return None
    name = next((str(job["company_name"]) for job in jobs if job.get("company_name")), None)
    if name is None:
        board = await _json(fetcher, f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}")
        name = str(board.get("name") or "") if isinstance(board, dict) else ""
    india = [job for job in jobs if _INDIA.search(str((job.get("location") or {}).get("name") or ""))]
    return _Board(len(jobs), len(india), name, [], slug)


async def _lever(slug: str, fetcher: DetectFetcher) -> _Board | None:
    jobs = await _json(fetcher, f"https://api.lever.co/v0/postings/{quote(slug)}?mode=json")
    if not isinstance(jobs, list) or not jobs:
        return None
    india = [job for job in jobs if _INDIA.search(lever._locations(job)) or str(job.get("country") or "").upper() == "IN"]
    return _Board(len(jobs), len(india), None, [str(job.get("descriptionPlain") or "") for job in jobs], slug)


async def _ashby(slug: str, fetcher: DetectFetcher) -> _Board | None:
    payload = await _json(fetcher, f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}")
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    listed = [job for job in jobs if job.get("isListed", True)] if isinstance(jobs, list) else []
    if not listed:
        return None
    india = [job for job in listed if any(_INDIA.search(place) for place in ashby._locations(job))]
    return _Board(len(listed), len(india), None, [str(job.get("descriptionPlain") or "") for job in listed], slug)


async def _smartrecruiters(slug: str, fetcher: DetectFetcher) -> _Board | None:
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings"
    payload = await _json(fetcher, f"{base}?limit=100")
    jobs = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        return None
    company = jobs[0].get("company") or {}
    india_page = await _json(fetcher, f"{base}?country=in&limit=100&offset=0")
    india = india_page.get("content") if isinstance(india_page, dict) else None
    # Both counts are of one page of at most 100 postings.
    return _Board(len(jobs), len(india) if isinstance(india, list) else 0, str(company.get("name") or ""), [],
                  str(company.get("identifier") or slug))


async def _workable(slug: str, fetcher: DetectFetcher) -> _Board | None:
    payload = await _json(fetcher, f"https://apply.workable.com/api/v1/widget/accounts/{quote(slug)}")
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        return None
    india = [job for job in jobs if _INDIA.search(" ".join(str(job.get(key) or "") for key in ("country", "city", "state")))]
    return _Board(len(jobs), len(india), str(payload.get("name") or ""), [], slug)


_READERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "smartrecruiters": _smartrecruiters, "workable": _workable}


def _judge(row: SeedRow, board: _Board) -> tuple[str, str]:
    """(status, evidence) for a board that returned jobs."""
    if board.name is not None:
        match = name_match(row.company, board.name)
        named = f"board name '{board.name}' " + {"exact": "matches", "partial": "partly matches", "none": "does not match"}[match]
    else:
        main, _ = _parts(row.company)
        pattern = re.compile(r"(?<![a-z0-9])" + r"[\W_]*".join(re.escape(word) for word in main) + r"(?![a-z0-9])", re.IGNORECASE)
        mentions = len([text for text in board.texts if pattern.search(text)]) if len("".join(main)) >= 4 else 0
        match = "exact" if mentions else "none"
        named = f"the API returns no organisation name; the company name appears in {mentions} of {len(board.texts)} job descriptions"
    india = f"{board.india} of {board.total} jobs in India" if board.india else f"no India job among {board.total}"
    return ("confirmed" if match == "exact" and board.india else "probable"), f"{named}; {india}"


async def probe_company(row: SeedRow, fetcher: DetectFetcher, run: ProbeRun | None = None) -> list[ProbeHit]:
    run = run if run is not None else ProbeRun()
    hits: dict[str, ProbeHit] = {}

    async def read(ats: str, slug: str) -> None:
        try:
            board = await _READERS[ats](slug, fetcher)
        except HostBlocked as exc:
            run.stopped.setdefault(ats, str(exc))
            return
        except Exception:
            run.errors[ats] = run.errors.get(ats, 0) + 1
            return
        if board:
            status, evidence = _judge(row, board)
            hits[ats] = ProbeHit(row.line, row.company, row.list_type, row.city, ats, slug, status, board.total, board.india,
                                 evidence, BOARD_URL[ats].format(board.key))

    for slug in slug_candidates(row.company):
        if any(hit.status == "confirmed" for hit in hits.values()):
            break
        run.slugs_tried += 1
        wanted = [ats for ats in ATS if ats not in hits]
        for ats in wanted:
            if ats in run.stopped:
                run.not_sent[ats] = run.not_sent.get(ats, 0) + 1
        await asyncio.gather(*(read(ats, slug) for ats in wanted if ats not in run.stopped))
    return [hits[ats] for ats in ATS if ats in hits]


async def probe_all(rows: list[SeedRow], fetcher: DetectFetcher,
                    progress: Callable[[SeedRow, list[ProbeHit]], None] | None = None) -> ProbeRun:
    run = ProbeRun()
    for row in rows:
        hits = await probe_company(row, fetcher, run)
        run.companies += 1
        run.with_hit += bool(hits)
        run.hits += hits
        if progress:
            progress(row, hits)
    return run


def confirmed_updates(hits: list[ProbeHit]) -> dict[str, dict[str, str]]:
    """Seed changes for companies with exactly one confirmed board; two confirmed boards wait for review."""
    confirmed: dict[str, list[ProbeHit]] = {}
    for hit in hits:
        if hit.status == "confirmed":
            confirmed.setdefault(hit.company, []).append(hit)
    return {company: {"careers_url": found[0].board_url, "ats": found[0].ats} for company, found in confirmed.items() if len(found) == 1}


def write_review(path: Path, hits: list[ProbeHit]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(REVIEW_COLUMNS)
        for hit in hits:
            writer.writerow([hit.company, hit.city, hit.ats, hit.slug, hit.status, hit.jobs_total, hit.jobs_india, hit.evidence])


def read_review(path: Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
