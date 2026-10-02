"""One rule for "is this board pollable?", used by the slug probe and by ATS detection (Q2c4).

A board is `pollable` when its public API answers, the company name is verified, at least one job
is in India, and the newest posting is at most 180 days old. A board whose name is verified but
that fails the India or the freshness check is `stale_no_india`: it stays in the seed list and is
left out of the pollable count. A board whose name is not verified is `name_mismatch`. Claim level L0.

Who verifies the name differs by caller and is passed in as `name_ok`: the probe guessed the slug,
so it needs the API's organisation name or the job descriptions to match; detection reads a board
address the seed list or the company's own careers page gave it.

The readers return what a public job API said about one board: counts are len() of the lists it
returned. SmartRecruiters counts are of one page of at most 100 postings.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from urllib.parse import quote

from app.sources.adapters import ashby, lever
from app.sources.fetcher import PoliteFetcher
from app.sources.registry import DEFAULT_INDIA_FILTER

STALE_DAYS = 180
STATUSES = ["pollable", "stale_no_india", "name_mismatch"]
_INDIA = re.compile(DEFAULT_INDIA_FILTER, re.IGNORECASE)


class BoardError(RuntimeError):
    """The board's API did not answer 200; 404 means no board has that name."""

    def __init__(self, what: str, http_status: int):
        super().__init__(f"{what} returned HTTP {http_status}.")
        self.http_status = http_status


@dataclass
class Board:
    total: int              # len() of the job list
    india: int              # len() of the jobs located in India
    name: str | None        # the organisation name the API returned; None when it returns none
    texts: list[str]        # job descriptions, for the APIs that return no name
    key: str                # the board id for the public URL
    posted: list = field(default_factory=list)      # each job's posting date as the API gave it


def newest(values: list) -> date | None:
    """The latest posting date among ISO strings and epoch milliseconds; None when no value can be read."""
    days = []
    for value in values:
        try:
            if isinstance(value, (int, float)):
                days.append(datetime.fromtimestamp(value / 1000, tz=timezone.utc).date())
            elif value:
                days.append(date.fromisoformat(str(value)[:10]))
        except (ValueError, OverflowError, OSError):
            continue
    return max(days) if days else None


def judge(board: Board, *, name_ok: bool, today: date) -> tuple[str, str]:
    """(status, evidence): the status is one of STATUSES; the evidence states the India count and the newest posting."""
    if not board.total:
        return ("stale_no_india" if name_ok else "name_mismatch"), "the board lists no job"
    india = f"{board.india} of {board.total} jobs in India" if board.india else f"no India job among {board.total}"
    latest = newest(board.posted)
    if latest is None:
        fresh, age = False, "no posting date given"
    else:
        fresh = (today - latest).days <= STALE_DAYS
        age = f"newest posting {latest}" + ("" if fresh else f" is more than {STALE_DAYS} days old")
    evidence = f"{india}; {age}"
    if not name_ok:
        return "name_mismatch", evidence
    return ("pollable" if board.india and fresh else "stale_no_india"), evidence


async def _json(fetcher: PoliteFetcher, url: str, what: str):
    response = await fetcher.get(url, check_robots=False, accept="application/json")
    if response.status_code != 200:
        raise BoardError(what, response.status_code)
    return response.json()


async def _greenhouse(key: str, fetcher: PoliteFetcher, region: str, need_name: bool = True) -> Board | None:
    what = f"Greenhouse board '{key}'"
    payload = await _json(fetcher, f"https://boards-api.greenhouse.io/v1/boards/{quote(key)}/jobs?content=true", what)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return None
    name = next((str(job["company_name"]) for job in jobs if job.get("company_name")), None)
    if name is None and jobs and need_name:
        try:
            details = await _json(fetcher, f"https://boards-api.greenhouse.io/v1/boards/{quote(key)}", what)
        except BoardError:
            details = None
        name = str(details.get("name") or "") if isinstance(details, dict) else ""
    india = [job for job in jobs if _INDIA.search(str((job.get("location") or {}).get("name") or ""))]
    return Board(len(jobs), len(india), name or "", [], key, [job.get("first_published") or job.get("updated_at") for job in jobs])


async def _lever(key: str, fetcher: PoliteFetcher, region: str) -> Board | None:
    jobs = await _json(fetcher, lever.API["eu" if region == "eu" else "global"].format(site=quote(key)), f"Lever site '{key}'")
    if not isinstance(jobs, list):
        return None
    india = [job for job in jobs if _INDIA.search(lever._locations(job)) or str(job.get("country") or "").upper() == "IN"]
    return Board(len(jobs), len(india), None, [str(job.get("descriptionPlain") or "") for job in jobs], key,
                 [job.get("createdAt") for job in jobs])


async def _ashby(key: str, fetcher: PoliteFetcher, region: str) -> Board | None:
    payload = await _json(fetcher, f"https://api.ashbyhq.com/posting-api/job-board/{quote(key)}", f"Ashby board '{key}'")
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return None
    listed = [job for job in jobs if job.get("isListed", True)]
    india = [job for job in listed if any(_INDIA.search(place) for place in ashby._locations(job))]
    return Board(len(listed), len(india), None, [str(job.get("descriptionPlain") or "") for job in listed], key,
                 [job.get("publishedAt") for job in listed])


async def _smartrecruiters(key: str, fetcher: PoliteFetcher, region: str) -> Board | None:
    what = f"SmartRecruiters company '{key}'"
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(key)}/postings"
    payload = await _json(fetcher, f"{base}?limit=100", what)
    jobs = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return None
    if not jobs:
        return Board(0, 0, "", [], key)
    company = jobs[0].get("company") or {}
    india_page = await _json(fetcher, f"{base}?country=in&limit=100&offset=0", what)
    india = india_page.get("content") if isinstance(india_page, dict) else None
    return Board(len(jobs), len(india) if isinstance(india, list) else 0, str(company.get("name") or ""), [],
                 str(company.get("identifier") or key), [job.get("releasedDate") for job in jobs])


async def _workable(key: str, fetcher: PoliteFetcher, region: str) -> Board | None:
    payload = await _json(fetcher, f"https://apply.workable.com/api/v1/widget/accounts/{quote(key)}", f"Workable account '{key}'")
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return None
    india = [job for job in jobs if _INDIA.search(" ".join(str(job.get(field) or "") for field in ("country", "city", "state")))]
    return Board(len(jobs), len(india), str(payload.get("name") or ""), [], key,
                 [job.get("published_on") or job.get("created_at") for job in jobs])


READERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "smartrecruiters": _smartrecruiters, "workable": _workable}


async def read_board(ats: str, key: str, fetcher: PoliteFetcher, region: str = "", *, need_name: bool = True) -> Board | None:
    """What the ATS's public job API says about this board; None when the answer holds no job list.

    Raises BoardError when the API does not answer 200 (404: no such board), and whatever the fetcher
    raises (HostBlocked after a 429)."""
    if ats == "greenhouse":
        return await _greenhouse(key, fetcher, region, need_name)     # may ask the board for its name
    return await READERS[ats](key, fetcher, region)
