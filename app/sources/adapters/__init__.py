"""Source adapters for the Company Radar.

Each adapter turns one company's official board into normalized JobPostings.
Counts are always computed from the parsed data: `fetched` is len() of the
list the source returned and `india` is len() after the India filter.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.schemas import JobPosting
from app.providers.common import build_posting, plain_text
from app.sources.registry import CompanyEntry

RADAR_SOURCE = "Company Radar"


class SourceError(RuntimeError):
    """The source could not be read; carries the HTTP status when there was one."""

    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


@dataclass
class FetchResult:
    jobs: list[JobPosting]
    fetched: int                 # len() of the source's list
    india: int                   # len(jobs)
    complete: bool               # False for capped windows (absence does not mean closed)
    http_status: int | None = 200
    details_pending: list[str] = field(default_factory=list)  # source ids whose description still needs a detail fetch
    rejected: list[str] = field(default_factory=list)         # source ids read and found outside India or expired
    deferred: int = 0                                         # new jobs left for the next run by a per-run cap


def posting(company: CompanyEntry, *, job_id, title, location, description, url, posted=None,
            employment_type=None, work_mode: str | None = None) -> JobPosting:
    """A radar job: the company's canonical name, its official URL, and a stable source id."""
    job = build_posting(source=RADAR_SOURCE, title=title, company=company.name, location=location,
                        description=description, url=url, job_id=job_id, posted=posted, employment_type=employment_type)
    update: dict = {"official_application": True}
    if work_mode in ("remote", "hybrid", "onsite"):
        update["work_mode"] = work_mode
    return job.model_copy(update=update)


def html_text(value) -> str:
    """Board descriptions arrive as (sometimes entity-escaped) HTML."""
    import html
    return plain_text(html.unescape(str(value or "")))


def iso_from_millis(value) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def require_ok(response, what: str):
    if response.status_code != 200:
        raise SourceError(f"{what} returned HTTP {response.status_code}.", response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(f"{what} returned invalid JSON.", response.status_code) from exc
