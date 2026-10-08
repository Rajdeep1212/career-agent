"""The three read-only MCP tools: search_jobs, explain_fit, list_applications.

Hard rule: no tool result contains CV text or text extracted from the CV. Tools return job data,
scores, tracker facts, and facts from data/eval/target_profile.md only.

- Scoring uses the target profile (app/mcp/target_profile.py), never the CV-derived profile. Without
  that file there is no fit score.
- Tracker rows are reduced to an explicit list of fields; notes, outreach text and the stored match
  (which quote the CV) are never returned.
- `call()` checks every result against the strings of the CV-derived profile before returning it and
  blocks the whole result if one appears. The check reads the profile; it never returns it.

Every database is read inside db.read_only(); nothing is written. Scores are claim level L0.
"""
import json
import re
from collections.abc import Callable
from datetime import date
from typing import Any
from pathlib import Path

from app.core.config import settings
from app.mcp import target_profile
from app.models.career import SearchQuery
from app.models.schemas import CandidateProfile, JobPosting, JobSearchPreferences
from app.providers.radar_provider import matches_any, plans_for
from app.services.eligibility import evaluate_eligibility
from app.services.freshness import freshness, today_ist
from app.services.matching import match_job
from app.services.search_intent import interpret_search_request
from app.services.skills import contains_phrase, extract_skills
from app.storage import db, profile_store, radar_store
from app.tracker import importers as tracker_importers
from app.tracker import store as tracker_store
from app.tracker.models import STATUSES as TRACKER_STATUSES

TARGET_PROFILE = Path(settings.data_dir) / "eval" / "target_profile.md"
TARGET_PROFILE_NAME = "data/eval/target_profile.md"
MAX_LIMIT = 50
_NO_PROFILE = (f"No fit score: {TARGET_PROFILE_NAME} does not exist. Write it yourself (roles, locations, graduation year, "
               "skills); the CV is never used here.")
_SCOPE = "Jobs come from the local Company Radar index only; nothing is fetched."

TOOLS: list[dict[str, Any]] = [
    {"name": "search_jobs",
     "description": "Fresh, eligible jobs from the local Company Radar index for a role query, with a heuristic fit score "
                    "(claim level L0, scored against the user's target profile, never the CV), freshness status and link. Read-only.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {
         "query": {"type": "string", "description": "Role to search for, e.g. 'AI Engineer jobs for freshers in India'."},
         "city": {"type": "string", "description": "Only jobs in this city."},
         "max_age_days": {"type": "integer", "minimum": 1, "description": "Hide jobs posted more than this many days ago."},
         "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "description": "Jobs to return (default 20)."}}}},
    {"name": "explain_fit",
     "description": "Why one job does or does not fit: eligibility evidence and skills, quoting the job description only. Read-only.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["job_id"], "properties": {
         "job_id": {"type": "string", "description": "A job_id returned by search_jobs."}}}},
    {"name": "list_applications",
     "description": "Rows of the application tracker: status, dates and the job's title, company and link. Read-only.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
         "status": {"type": "string", "enum": list(TRACKER_STATUSES), "description": "Only applications with this status."}}}},
]
_TYPES = {"string": str, "integer": int}


def _target() -> CandidateProfile | None:
    return target_profile.load(TARGET_PROFILE)


def _index() -> list[tuple[str, JobPosting]]:
    if not radar_store.DB_PATH.exists():
        return []
    with db.read_only(radar_store.DB_PATH):
        return radar_store.index_entries()


def _scored(score: int) -> dict:
    return {"score": score, "claim_level": "L0", "label": f"Heuristic fit {score}/100"}


def _fit(score: int | None) -> dict | None:
    return None if score is None else _scored(score)


def _summary(key: str, job: JobPosting) -> dict:
    return {"job_id": key, "title": job.title, "company": job.company, "location": job.location, "work_mode": job.work_mode,
            "employment_type": job.employment_type, "posted_date": job.posted_date,
            "link": str(job.application_url) if job.application_url else None, "source": job.source}


def search_jobs(query: str, city: str | None = None, max_age_days: int | None = None, limit: int = 20, *,
                today: date | None = None) -> dict:
    today = today or today_ist()
    target = _target()
    scoring = target or CandidateProfile()
    intent = interpret_search_request(query)
    if city:
        intent.locations, intent.locations_from_preferences = [city], False
    place = city or (intent.locations[0] if intent.locations else "")
    plans = plans_for([SearchQuery(query=role, reason="", role=role, location=place) for role in intent.roles_requested or [query]])
    preferences = JobSearchPreferences()
    found = []
    for key, job in _index():
        if not matches_any(job, plans):
            continue
        fresh = freshness(job, today, max_age_days=max_age_days, check_age_days=max_age_days)
        if fresh.decision == "hide":
            continue
        eligibility = evaluate_eligibility(scoring, job, preferences, intent)
        if eligibility.status == "excluded":
            continue
        score = match_job(scoring, job, intent, eligibility).overall_score if target else None
        found.append({**_summary(key, job), "fit": _fit(score), "freshness": fresh.as_dict(),
                      "eligibility": {"status": eligibility.status, "summary": eligibility.summary, "claim_level": "L0"}})
    found.sort(key=lambda item: (item["eligibility"]["status"] != "eligible", -(item["fit"]["score"] if item["fit"] else 0),
                                 -(date.fromisoformat(item["posted_date"][:10]).toordinal() if item["posted_date"] else 0)))
    limit = max(1, min(int(limit), MAX_LIMIT))
    return {"query": query, "city": city, "today": today.isoformat(), "matched": len(found), "jobs": found[:limit],
            "profile_source": TARGET_PROFILE_NAME if target else None,
            "notes": [_SCOPE] + ([] if target else [_NO_PROFILE])}


def _sentence(listing: str, skill: str) -> str:
    """The sentence of the listing that names the skill, verbatim; '' when no single sentence does."""
    return next((part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", listing) if contains_phrase(part, skill)), "")


def explain_fit(job_id: str, *, today: date | None = None) -> dict:
    today = today or today_ist()
    job = dict(_index()).get(job_id)
    if job is None:
        return {"error": f"No job with job_id '{job_id}' in the local index. Use a job_id from search_jobs."}
    target = _target()
    scoring = target or CandidateProfile()
    intent = interpret_search_request(" ".join(scoring.preferred_roles[:1]))
    eligibility = evaluate_eligibility(scoring, job, JobSearchPreferences(), intent)
    match = match_job(scoring, job, intent, eligibility)
    listing = f"{job.title}. {job.description}"
    wanted = {skill.casefold() for skill in scoring.skills}
    skills = list(dict.fromkeys([*job.skills, *extract_skills(listing)]))
    return {**_summary(job_id, job),
            "fit": {**_scored(match.overall_score), "components": match.components} if target else None,
            "eligibility": {"status": eligibility.status, "summary": eligibility.summary, "claim_level": "L0",
                            "evidence": [{"outcome": item.outcome, "reason": item.reason, "quote": item.quote}
                                         for item in eligibility.evidence]},
            "skills_in_listing": [{"skill": skill, "in_target_profile": (skill.casefold() in wanted) if target else None,
                                   "quote": _sentence(listing, skill)} for skill in skills],
            "freshness": freshness(job, today).as_dict(),
            "profile_source": TARGET_PROFILE_NAME if target else None,
            "notes": ["Every quote is from the job description."] + ([] if target else [_NO_PROFILE])}


def list_applications(status: str | None = None) -> dict:
    """The local user's applications in data/tracker.sqlite3, opened read-only (a missing file is an empty list)."""
    wanted = status.upper() if status else None
    if wanted and wanted not in TRACKER_STATUSES:
        return {"error": f"Unknown status '{status}'. Use one of: {', '.join(TRACKER_STATUSES)}."}
    rows: list = []
    if tracker_store.DB_PATH.exists():
        conn = tracker_importers.open_read_only(tracker_store.DB_PATH)
        try:
            # An explicit list of columns: notes are never selected, and of the saved job post only its location is read.
            rows = conn.execute(
                """SELECT id, status, created_at, updated_at, applied_at, next_follow_up_at, job_id, title, company_name, url,
                          json_extract(snapshot_json, '$.location') AS location
                   FROM applications WHERE owner_id = ? ORDER BY updated_at DESC, id""", (tracker_store.LOCAL_USER_ID,)).fetchall()
        finally:
            conn.close()
    applications = [{"application_id": row["id"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                     "applied_at": row["applied_at"], "next_follow_up_at": row["next_follow_up_at"],
                     "job": {"job_id": row["job_id"], "title": row["title"], "company": row["company_name"], "location": row["location"],
                             "link": row["url"]}}
                    for row in rows if not wanted or row["status"] == wanted]
    return {"status": wanted, "count": len(applications), "applications": applications}


HANDLERS: dict[str, Callable[..., dict]] = {"search_jobs": search_jobs, "explain_fit": explain_fit, "list_applications": list_applications}


_CV_PROSE_FIELDS = ("projects", "research", "experience", "internships", "evidence")


def _cv_strings() -> list[str]:
    """Strings of the CV-derived profile that must never appear in a result: the name and the CV's own prose.

    Role titles, places, skills, degrees and certificate names are left out: they are ordinary job vocabulary
    that listings contain anyway, and no tool reads them from the profile."""
    try:
        data = json.loads(profile_store.PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    found: list[str] = []

    def walk(value) -> None:
        if isinstance(value, str) and len(value.strip()) >= 20:
            found.append(value.strip().casefold())
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for field in _CV_PROSE_FIELDS:
        walk(data.get(field))
    name = str(data.get("name") or "").strip()
    return found + ([name.casefold()] if len(name) >= 4 else [])


def call(name: str, arguments: dict | None) -> dict:
    """Run one tool the way the server does: checked arguments, then the CV check on the result."""
    arguments = arguments or {}
    spec = next((tool for tool in TOOLS if tool["name"] == name), None)
    if spec is None:
        return {"error": f"Unknown tool '{name}'."}
    properties = spec["inputSchema"]["properties"]
    for key, value in arguments.items():
        if key not in properties:
            return {"error": f"Unknown argument '{key}' for {name}."}
        expected = _TYPES[properties[key]["type"]]
        if not isinstance(value, expected) or isinstance(value, bool):
            return {"error": f"Argument '{key}' must be {properties[key]['type']}."}
    try:
        result = HANDLERS[name](**arguments)
    except TypeError as exc:
        return {"error": f"Bad arguments for {name}: {exc}"}
    text = json.dumps(result, ensure_ascii=False).casefold()
    if any(secret in text for secret in _cv_strings()):
        return {"error": "blocked: the result contained text from the CV profile, so nothing was returned."}
    return result
