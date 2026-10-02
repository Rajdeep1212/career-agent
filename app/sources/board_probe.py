"""Careers boards for seed companies that list no careers_url (docs/ROADMAP_QUEUE.md Q2c2).

A few slugs are made from the company name and tried against public job APIs only: Greenhouse,
Lever, Ashby, SmartRecruiters and the Workable careers widget. No web page is read. Claim level L0.

A slug that answers with jobs is a hit, and every hit is judged by the one pollable rule in
app/sources/board_rule.py: `pollable`, `stale_no_india` or `name_mismatch`.

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
from datetime import date
from pathlib import Path

from app.sources.ats_detect import DetectFetcher
from app.sources.board_rule import Board, BoardError, judge, read_board
from app.sources.company_seed import SeedRow
from app.sources.fetcher import HostBlocked

ATS = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable"]
MAX_SLUGS = 4
REVIEW_COLUMNS = ["company", "city", "ats", "slug", "status", "jobs_total", "jobs_india", "evidence"]
BOARD_URL = {"greenhouse": "https://boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
             "ashby": "https://jobs.ashbyhq.com/{}", "smartrecruiters": "https://jobs.smartrecruiters.com/{}",
             "workable": "https://apply.workable.com/{}"}
_LEGAL = {"pvt", "ltd", "limited", "private", "inc", "llp"}
_SOFT = {"technologies", "technology", "tech", "labs", "lab", "software", "systems", "solutions", "india", "global", "group",
         "services", "company", "co", "corp", "corporation", "ai", "io", "app", "hq"}


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
    status: str             # pollable | stale_no_india | name_mismatch (app/sources/board_rule.py)
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


def _judge(row: SeedRow, board: Board, today: date) -> tuple[str, str]:
    """(status, evidence): the name evidence for a guessed slug, then the one pollable rule."""
    if board.name is not None:
        match = name_match(row.company, board.name)
        named = f"board name '{board.name}' " + {"exact": "matches", "partial": "partly matches", "none": "does not match"}[match]
    else:
        main, _ = _parts(row.company)
        pattern = re.compile(r"(?<![a-z0-9])" + r"[\W_]*".join(re.escape(word) for word in main) + r"(?![a-z0-9])", re.IGNORECASE)
        mentions = len([text for text in board.texts if pattern.search(text)]) if len("".join(main)) >= 4 else 0
        match = "exact" if mentions else "none"
        named = f"the API returns no organisation name; the company name appears in {mentions} of {len(board.texts)} job descriptions"
    status, facts = judge(board, name_ok=match == "exact", today=today)
    return status, f"{named}; {facts}"


def _hit(row: SeedRow, ats: str, slug: str, board: Board, today: date) -> ProbeHit:
    status, evidence = _judge(row, board, today)
    return ProbeHit(row.line, row.company, row.list_type, row.city, ats, slug, status, board.total, board.india, evidence,
                    BOARD_URL[ats].format(board.key))


async def probe_company(row: SeedRow, fetcher: DetectFetcher, run: ProbeRun | None = None) -> list[ProbeHit]:
    run = run if run is not None else ProbeRun()
    hits: dict[str, ProbeHit] = {}

    async def read(ats: str, slug: str) -> None:
        try:
            board = await read_board(ats, slug, fetcher)
        except HostBlocked as exc:
            run.stopped.setdefault(ats, str(exc))
            return
        except BoardError as exc:
            if exc.http_status != 404:      # 404: no board has this name
                run.errors[ats] = run.errors.get(ats, 0) + 1
            return
        except Exception:
            run.errors[ats] = run.errors.get(ats, 0) + 1
            return
        if board and board.total:
            hits[ats] = _hit(row, ats, slug, board, fetcher._now().date())

    for slug in slug_candidates(row.company):
        if any(hit.status == "pollable" for hit in hits.values()):
            break
        run.slugs_tried += 1
        wanted = [ats for ats in ATS if ats not in hits]
        for ats in wanted:
            if ats in run.stopped:
                run.not_sent[ats] = run.not_sent.get(ats, 0) + 1
        await asyncio.gather(*(read(ats, slug) for ats in wanted if ats not in run.stopped))
    return [hits[ats] for ats in ATS if ats in hits]


async def probe_all(rows: list[SeedRow], fetcher: DetectFetcher,
                    progress: Callable[[SeedRow, list[ProbeHit]], None] | None = None,
                    skip: dict[str, str] | None = None) -> ProbeRun:
    """`skip` maps an API to the reason it is not queried in this run (for one that answered 429 earlier)."""
    run = ProbeRun(stopped=dict(skip or {}))
    for row in rows:
        hits = await probe_company(row, fetcher, run)
        run.companies += 1
        run.with_hit += bool(hits)
        run.hits += hits
        if progress:
            progress(row, hits)
    return run


async def rejudge(review: list[dict[str, str]], rows: list[SeedRow], fetcher: DetectFetcher) -> list[ProbeHit]:
    """Judge every row of a review file again by the current rule, from the cache only; nothing is requested.

    A row whose board is no longer in the cache keeps its counts and is marked as not re-read."""
    by_company = {row.company: row for row in rows}
    fetcher.offline = True
    hits = []
    try:
        for item in review:
            row = by_company[item["company"]]
            try:
                board = await read_board(item["ats"], item["slug"], fetcher)
            except Exception:
                board = None
            if board and board.total:
                hits.append(_hit(row, item["ats"], item["slug"], board, fetcher._now().date()))
            else:
                hits.append(ProbeHit(row.line, row.company, row.list_type, row.city, item["ats"], item["slug"], item["status"],
                                     int(item["jobs_total"]), int(item["jobs_india"]),
                                     f"{item['evidence']} (not re-read: the board is not in the cache)",
                                     BOARD_URL[item["ats"]].format(item["slug"])))
    finally:
        fetcher.offline = False
    return hits


def confirmed_updates(hits: list[ProbeHit]) -> dict[str, dict[str, str]]:
    """Seed changes for companies with exactly one pollable board; two pollable boards wait for review."""
    confirmed: dict[str, list[ProbeHit]] = {}
    for hit in hits:
        if hit.status == "pollable":
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
