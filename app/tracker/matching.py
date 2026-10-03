"""Quick-add matching: is the job the user pasted already in the Company Radar index? (docs/ROADMAP_QUEUE.md TRK2)

Nothing is fetched. The URL is normalised and compared with the index; when that fails, the title and company the
user gave are compared with `app/services/job_identity.py`. The answer is `matched` (same URL), `probable` (same
company and a matching title: offered to the user, never linked without their confirmation) or `none`, always with
the reason.
"""
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.schemas import JobPosting
from app.services.job_identity import TITLE_SIMILARITY, city_key, company_key, title_key
from app.storage import db, radar_store

# Query parameters that say where a visitor came from, not which job the page shows.
_TRACKING = frozenset({"ref", "refid", "source", "src", "trk", "trackingid", "gh_src", "lever-source", "lever-origin",
                       "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "igshid"})
_APPLY_TAIL = re.compile(r"/(?:apply|application)$")    # the form page of a posting is the same posting


def normalise_url(value) -> str:
    """Lower-case scheme and host, no fragment, no tracking parameters, sorted query; '' when it is not a web URL."""
    try:
        parts = urlsplit(str(value or "").strip())
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return ""
    host = parts.hostname.lower() + (f":{port}" if port and port not in (80, 443) else "")
    path = _APPLY_TAIL.sub("", parts.path.rstrip("/"))
    query = sorted((key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
                   if key.lower() not in _TRACKING and not key.lower().startswith("utm_"))
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def index_jobs() -> list[tuple[str, JobPosting]]:
    """Every indexed job with its index key. Read-only; a missing index is an empty one and is not created."""
    if not radar_store.DB_PATH.exists():
        return []
    with db.read_only(radar_store.DB_PATH):
        return radar_store.index_entries()


def _candidate(key: str, job: JobPosting, reason: str) -> dict:
    return {"job_id": key, "title": job.title, "company": job.company, "location": job.location,
            "url": str(job.application_url) if job.application_url else None, "reason": reason}


def match(url, *, title: str = "", company: str = "", location: str = "", index: list[tuple[str, JobPosting]]) -> dict:
    """{'status': matched | probable | none, 'reason', 'job_id' (matched only), 'candidates' (probable only)}."""
    wanted = normalise_url(url)
    if wanted:
        for key, job in index:
            if normalise_url(job.application_url) == wanted:
                return {"status": "matched", "job_id": key, "candidates": [],
                        "reason": f"The URL is the one of index job {key} ({job.title}, {job.company}) after normalising: {wanted}"}
    name, wanted_title = company_key(company), title_key(title)
    if not name or not wanted_title:
        return {"status": "none", "job_id": None, "candidates": [],
                "reason": "No index job has this URL, and no title and company were given to compare."}
    city = city_key(location)
    candidates = []
    for key, job in index:
        if company_key(job.company) != name:
            continue
        theirs = city_key(job.location)
        if city and theirs and city != theirs:
            continue
        indexed_title = title_key(job.title)
        if indexed_title == wanted_title:
            candidates.append(_candidate(key, job, "Same company and the same title; the URL is different."))
            continue
        similarity = SequenceMatcher(None, wanted_title, indexed_title).ratio()
        if similarity >= TITLE_SIMILARITY:
            candidates.append(_candidate(key, job, f"Same company and a similar title (similarity {similarity:.2f}); the URL is different."))
    if candidates:
        return {"status": "probable", "job_id": None, "candidates": candidates,
                "reason": f"No index job has this URL. {len(candidates)} index job(s) at the same company have a matching title; "
                          "none is linked until you confirm one."}
    return {"status": "none", "job_id": None, "candidates": [],
            "reason": "No index job has this URL, and none at this company has a matching title."}
