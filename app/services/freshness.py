"""Freshness: is a job plausibly open today? A pure rule over dates the job already carries.

    open_verified  its source feed listed it, or a link check found it open, within 48 hours -> show
    closed         its source or its page says it is closed                                  -> hide
    unknown        posted within max_age_days                                                -> show
                   posted within check_age_days                                              -> check before applying
                   older, or no posted date                                                  -> hide
                   (the user's own saved pages stay shown for 7 days)

A job with no posted date is aged from the day its alert email arrived ("seen on"). Relative text
("3 days ago") is counted from the day it was read, when that day is known.

Dates are compared as calendar days in India time. Nothing here sends a request.
"""
import re
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
    dated_by: Literal["posted", "seen_on"] | None = None   # which date the age is counted from

    def as_dict(self) -> dict:
        return asdict(self)


def today_ist() -> date:
    return datetime.now(IST).date()


def day_of(value: str | None) -> date | None:
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


_RELATIVE = re.compile(r"(?:posted\s+)?(?:(just posted|just now|today)|(yesterday)|(\d{1,3})(\+)?\s*"
                       r"(minute|min|hour|hr|day|week|month)s?\s+ago)\.?", re.IGNORECASE)
_UNIT_DAYS = {"minute": 0, "min": 0, "hour": 0, "hr": 0, "day": 1, "week": 7, "month": 30}


def resolve_relative(text: str | None, reference: date) -> date | None:
    """The day a posting age such as "3 days ago" or "30+ days ago" refers to, counted from `reference`
    (the day the text was read). None unless the whole text is a posting age: "Active 3 days ago" is not."""
    match = _RELATIVE.fullmatch(str(text or "").strip())
    if match is None:
        return None
    if match[1]:
        return reference
    if match[2]:
        return reference - timedelta(days=1)
    # "30+ days ago" means more than 30.
    return reference - timedelta(days=int(match[3]) * _UNIT_DAYS[match[5].lower()] + (1 if match[4] else 0))


def _dated(job: JobPosting) -> tuple[date | None, Literal["posted", "seen_on"] | None]:
    """The day the job's age is counted from, and which date that is."""
    posted = day_of(job.posted_date)
    if posted:
        return posted, "posted"
    seen = day_of(job.seen_on)
    read_on = seen or day_of(job.captured_at)
    relative = resolve_relative(job.posted_date, read_on) if read_on else None
    if relative:
        return relative, "posted"
    return (seen, "seen_on") if seen else (None, None)


def freshness(job: JobPosting, today: date, *, max_age_days: int | None = None, check_age_days: int | None = None) -> Freshness:
    max_age = settings.max_age_days if max_age_days is None else max_age_days
    check_age = settings.check_age_days if check_age_days is None else check_age_days
    posted, basis = _dated(job)
    age = max(0, (today - posted).days) if posted else None
    what = (f"Seen on {posted.isoformat()} in an alert email ({age} days ago)" if basis == "seen_on" and posted
            else f"Posted {age} days ago")
    if job.verification_state == "CLOSED" or job.application_status == "closed":
        return Freshness("closed", "hide", age, "Closed: its source or its page says applications are closed.", basis)
    seen = day_of(job.last_listed_on) or (day_of(job.verification_checked_at) if job.verification_state == "ACTIVE_VERIFIED" else None)
    if seen and (today - seen).days <= VERIFIED_WITHIN_DAYS:
        return Freshness("open_verified", "show", age, f"Seen open on {seen.isoformat()}.", basis)
    captured = day_of(job.captured_at)
    if captured and (today - captured).days <= CAPTURE_GRACE_DAYS:
        return Freshness("unknown", "show", age, f"Saved by you on {captured.isoformat()}; not confirmed open.", basis)
    if age is None:
        return Freshness("unknown", "hide", None, "No posted date, and not confirmed open in the last 48 hours.")
    if age <= max_age:
        return Freshness("unknown", "show", age, f"{what}; not confirmed open in the last 48 hours.", basis)
    if age <= check_age:
        return Freshness("unknown", "check", age, f"Check before applying: {what[0].lower()}{what[1:]} and not confirmed open.", basis)
    return Freshness("unknown", "hide", age, f"{what} and not confirmed open in the last 48 hours.", basis)
