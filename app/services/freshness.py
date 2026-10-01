"""Freshness: is a job plausibly open today? A pure rule over dates the job already carries.

    open_verified  its source feed listed it, or a link check found it open, within 48 hours -> show
    closed         its source or its page says it is closed                                  -> hide
    unknown        posted within max_age_days                                                -> show
                   posted within check_age_days                                              -> check before applying
                   older, or no posted date                                                  -> hide
                   (the user's own saved pages stay shown for 7 days)

Dates are compared as calendar days in India time. Nothing here sends a request.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from app.core.config import settings
from app.models.schemas import JobPosting

IST = timezone(timedelta(hours=5, minutes=30), "IST")   # India has no daylight saving time
VERIFIED_WITHIN_DAYS = 2
CAPTURE_GRACE_DAYS = 7


@dataclass(frozen=True)
class Freshness:
    state: Literal["open_verified", "closed", "unknown"]
    decision: Literal["show", "check", "hide"]
    age_days: int | None
    summary: str

    def as_dict(self) -> dict:
        return asdict(self)


def today_ist() -> date:
    return datetime.now(IST).date()


def _day(value: str | None) -> date | None:
    """The India calendar day of a stored date or timestamp; None when absent or unreadable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) <= 10:
            return date.fromisoformat(text)
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(IST).date() if moment.tzinfo else moment.date()


def freshness(job: JobPosting, today: date, *, max_age_days: int | None = None, check_age_days: int | None = None) -> Freshness:
    max_age = settings.max_age_days if max_age_days is None else max_age_days
    check_age = settings.check_age_days if check_age_days is None else check_age_days
    posted = _day(job.posted_date)
    age = max(0, (today - posted).days) if posted else None
    if job.verification_state == "CLOSED" or job.application_status == "closed":
        return Freshness("closed", "hide", age, "Closed: its source or its page says applications are closed.")
    seen = _day(job.last_listed_on) or (_day(job.verification_checked_at) if job.verification_state == "ACTIVE_VERIFIED" else None)
    if seen and (today - seen).days <= VERIFIED_WITHIN_DAYS:
        return Freshness("open_verified", "show", age, f"Seen open on {seen.isoformat()}.")
    captured = _day(job.captured_at)
    if captured and (today - captured).days <= CAPTURE_GRACE_DAYS:
        return Freshness("unknown", "show", age, f"Saved by you on {captured.isoformat()}; not confirmed open.")
    if age is None:
        return Freshness("unknown", "hide", None, "No posted date, and not confirmed open in the last 48 hours.")
    if age <= max_age:
        return Freshness("unknown", "show", age, f"Posted {age} days ago; not confirmed open in the last 48 hours.")
    if age <= check_age:
        return Freshness("unknown", "check", age, f"Check before applying: posted {age} days ago and not confirmed open.")
    return Freshness("unknown", "hide", age, f"Posted {age} days ago and not confirmed open in the last 48 hours.")
