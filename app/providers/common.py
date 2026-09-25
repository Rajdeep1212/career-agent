"""Normalization shared by job-provider adapters."""
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.models.schemas import JobPosting
from app.services.job_requirements import extract_requirements
from app.services.skills import extract_skills


def safe_link(value) -> str | None:
    """An http(s) URL without credentials, or None."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
            return None
        if parts.port is not None and not 1 <= parts.port <= 65535:
            return None
    except ValueError:
        return None
    return value


def plain_text(value) -> str:
    """Provider snippets may contain HTML such as <b> highlights and entities."""
    text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ")
    return " ".join(text.split())


def split_location(planned_location: str, country_names: tuple[str, ...]) -> tuple[str, bool]:
    """(where, remote) for keyword APIs; a country-wide location becomes empty."""
    location = " ".join((planned_location or "").split())
    remote = location.casefold().startswith("remote")
    if remote:
        location = location[len("remote"):].strip()
    if location.casefold() in country_names:
        location = ""
    return location, remote


def build_posting(*, source: str, title, company, location, description, url, job_id=None,
                  posted=None, employment_type=None, salary=None) -> JobPosting:
    # Imported here: jsearch_provider imports this module for its own normalization.
    from app.providers.jsearch_provider import _FRESHER_PATTERN, _infer_work_mode

    title = plain_text(title)[:300] or "Unknown role"
    description = plain_text(description)[:60000]
    combined = title + "\n" + description
    exp_min, exp_max, years = extract_requirements(combined)
    return JobPosting(
        company=plain_text(company)[:300] or "Unknown company", title=title,
        location=plain_text(location)[:300] or "Not specified",
        work_mode=_infer_work_mode({"job_title": title, "job_description": description,
                                    "job_employment_type": employment_type or ""}),
        description=description, experience_min=exp_min, experience_max=exp_max,
        fresher_allowed=bool(_FRESHER_PATTERN.search(combined)),
        recent_graduate_allowed="recent graduate" in combined.lower(), graduation_years=years,
        skills=extract_skills(combined), application_url=safe_link(url), source=source,
        source_job_id=str(job_id) if job_id not in (None, "") else None,
        employment_type=str(employment_type) if employment_type else None,
        salary=plain_text(salary) or None, posted_date=str(posted) if posted else None,
    )
