"""Hosted-demo support: synthetic data only, no real credentials or outside calls.

DEMO_MODE is enforced in app/core/config.py (separate data directory, blank
credentials) and by the checks here and in main.py, gmail_service,
email_send_boundary and application_verifier. tests/test_demo_mode.py proves it.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.models.schemas import CandidateProfile, JobPosting
from app.providers.base import JobProvider
from app.providers.common import build_posting

logger = logging.getLogger(__name__)
_SEED = Path(__file__).resolve().parent


def demo_profile() -> CandidateProfile:
    return CandidateProfile.model_validate_json((_SEED / "profile.json").read_text(encoding="utf-8"))


def demo_jobs() -> list[JobPosting]:
    now = datetime.now(timezone.utc)
    jobs = []
    for item in json.loads((_SEED / "jobs.json").read_text(encoding="utf-8")):
        item = dict(item)
        posted = now - timedelta(days=item.pop("posted_offset_days"))
        job = build_posting(source="Demo (synthetic)", title=item["title"], company=item["company"],
                            location=item["location"], description=item["description"], url=item["application_url"],
                            job_id=item["application_url"].rsplit("/", 1)[-1], posted=posted.isoformat(),
                            employment_type=item["employment_type"])
        jobs.append(job.model_copy(update={"work_mode": item["work_mode"]}))
    return jobs


class DemoJobProvider(JobProvider):
    """Synthetic listings; never contacts a job site."""
    name = "Demo (synthetic)"

    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        words = {word for word in query.casefold().split() if len(word) > 3}
        jobs = demo_jobs()
        matching = [job for job in jobs if words & set((job.title + " " + job.description).casefold().split())]
        return matching or jobs


def _blocked(*_args, **_kwargs):
    raise RuntimeError("Outbound HTTP is disabled in demo mode.")


def install_network_guard() -> None:
    """No outbound HTTP from the app in demo mode (the in-process TestClient is unaffected)."""
    httpx.HTTPTransport.handle_request = _blocked  # type: ignore[method-assign]
    httpx.AsyncHTTPTransport.handle_async_request = _blocked  # type: ignore[method-assign]


def prepare(settings) -> None:
    """Seed the demo data directory and install the guard; called once at startup."""
    from app.storage import profile_store
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    if not profile_store.PROFILE_PATH.exists():
        profile_store.save_profile(demo_profile())
    if settings.demo_ignored:
        logger.warning("Demo mode ignores these settings: %s", ", ".join(settings.demo_ignored))
    install_network_guard()
