"""'Save to Career Agent': a bookmarklet opens a local form with the page's URL and title.

The bookmarklet reads only location.href and document.title of the page you
are viewing; nothing is crawled or scraped, and LinkedIn/Naukri/Indeed pages
are never fetched. You confirm the details and save (exact local origin). The
job is stored as 'Saved by you', linked to an official Company Radar job when
one matches, and checked with the same eligibility and matching as searches.
"""
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.career import _public_url
from app.core.config import settings
from app.core.origin_security import has_exact_local_origin
from app.core.static_assets import versioned_html
from app.providers.common import build_posting
from app.services.career_agent import evaluate_job
from app.services.job_identity import canonical_url, resolve_with_index
from app.sources.alerts.parser import job_link
from app.sources.digest import saved_role_intent
from app.sources.registry import RadarConfigError, alias_map, read_config
from app.storage import alert_store, radar_store

router = APIRouter(tags=["Save a job"])
SAVED_SOURCE = "Saved by you"


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=20000)

    @field_validator("url")
    @classmethod
    def public_http_url(cls, value: str) -> str:
        value = value.strip()
        if not _public_url(value):
            raise ValueError("Use an http(s) link without credentials")
        return value

    @field_validator("title", "company", "location")
    @classmethod
    def plain_text(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("Use text without control characters")
        return " ".join(value.split())


def bookmarklet() -> str:
    """Opens the local form with this page's URL and title; reads nothing else from the page."""
    target = json.dumps(settings.app_origin + "/capture?url=")
    return ("javascript:(()=>{window.open(" + target + "+encodeURIComponent(location.href)"
            "+'&title='+encodeURIComponent(document.title),'_blank');})()")


@router.get("/capture")
def capture_page():
    return HTMLResponse(versioned_html("capture.html", ("capture.js", "styles.css")), headers={"Cache-Control": "no-cache"})


@router.get("/capture/bookmarklet")
def capture_bookmarklet():
    return {"bookmarklet": bookmarklet()}


@router.post("/capture")
def save_captured_job(payload: CaptureRequest, request: Request):
    if not has_exact_local_origin(request):
        raise HTTPException(status_code=403, detail="Open the localhost form to save a job.")
    link = job_link(payload.url)
    job_id, url = (link[1], link[2]) if link else (None, canonical_url(payload.url) or payload.url)
    job = build_posting(source=SAVED_SOURCE, title=payload.title, company=payload.company, location=payload.location or "Not specified",
                        description=payload.description or "Saved by you with the bookmarklet.", url=url, job_id=job_id)
    stored = alert_store.upsert([job], kind="capture")
    target, matched = job, False
    if radar_store.has_jobs():
        try:
            aliases = alias_map(read_config())
        except RadarConfigError:
            aliases = {}
        resolved, matches = resolve_with_index([job], radar_store.index_entries(), aliases)
        if matches:
            radar_store.record_sources(matches)
            alert_store.set_radar_key(job, matches[0][0])
            target, matched = resolved[0], True
    profile, preferences, intent = saved_role_intent()
    result, _eligibility, _match = evaluate_job(profile, target, preferences, intent)
    return {**result, "matched_official": matched, "already_saved": stored.duplicates > 0}
