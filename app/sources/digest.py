"""The daily self-digest: jobs first seen in the Company Radar index today.

Jobs are kept when their title matches a saved role (the phrase or the same
role family), then checked with the same three-way eligibility as searches:
eligible first, then uncertain (each with its quoted evidence); excluded jobs
are only counted. Scores are heuristic fit (claim level L0), not predictions.
The digest is a self-contained HTML file in data/digests/ and feeds the
dashboard's "New today" panel.
"""
import html
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path

from app.core.config import settings
from app.models.career import SearchIntent
from app.providers.radar_provider import _family, _matches
from app.services.candidate_intelligence import analyze_candidate
from app.services.eligibility import evaluate_eligibility
from app.services.matching import match_job
from app.storage import alert_store, radar_store
from app.storage.preference_store import preferences_for
from app.storage.profile_store import load_profile

MAX_PER_TIER = 50


@dataclass
class DigestItem:
    title: str
    company: str
    location: str
    url: str | None
    posted: str | None
    status: str
    summary: str
    score: int
    source: str = "Company Radar"


@dataclass
class Digest:
    day: str
    new_jobs: int = 0
    off_role: int = 0
    excluded: int = 0
    eligible: list[DigestItem] = field(default_factory=list)
    uncertain: list[DigestItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def digest_dir() -> Path:
    return Path(settings.data_dir) / "digests"


def _start_of_local_day(day: date) -> str:
    """Midnight of `day` in local time, as the UTC ISO text used by first_seen_at."""
    return datetime.combine(day, time.min).astimezone().astimezone(timezone.utc).isoformat()


def build_digest(*, today: date | None = None) -> Digest:
    today = today or date.today()
    digest = Digest(day=today.isoformat())
    since = _start_of_local_day(today)
    # Official Radar jobs, plus new alert-email and saved jobs that matched no official job.
    jobs = (radar_store.new_since(since) if radar_store.DB_PATH.exists() else []) + alert_store.new_since(since)
    if not jobs:
        return digest
    digest.new_jobs = len(jobs)
    profile = analyze_candidate(load_profile())
    preferences = preferences_for(profile)
    roles = [role for role in profile.preferred_roles if role.strip()]
    intent = SearchIntent(roles_requested=roles, role_families=[f for f in (_family(role) for role in roles) if f],
                          locations=preferences.preferred_locations[:], locations_from_preferences=True)
    items = []
    for job in jobs:
        if roles and not any(_matches(job, role, _family(role), None) for role in roles):
            digest.off_role += 1
            continue
        eligibility = evaluate_eligibility(profile, job, preferences, intent)
        if eligibility.status == "excluded":
            digest.excluded += 1
            continue
        match = match_job(profile, job, intent, eligibility)
        items.append(DigestItem(title=job.title, company=job.company, location=job.location,
                                url=str(job.application_url) if job.application_url else None, posted=job.posted_date,
                                status=eligibility.status, summary=eligibility.summary, score=match.overall_score,
                                source=job.source or "Unknown"))
    items.sort(key=lambda item: -item.score)
    digest.eligible = [item for item in items if item.status == "eligible"][:MAX_PER_TIER]
    digest.uncertain = [item for item in items if item.status == "uncertain"][:MAX_PER_TIER]
    return digest


def _rows(items: list[DigestItem]) -> str:
    if not items:
        return '<p class="muted">None today.</p>'
    rows = []
    for item in items:
        title = html.escape(item.title)
        link = f'<a href="{html.escape(item.url)}" rel="noopener noreferrer">{title}</a>' if item.url else title
        rows.append(f"<li><div class=\"head\">{link} <span class=\"score\">heuristic fit {item.score}/100</span></div>"
                    f"<div>{html.escape(item.company)} · {html.escape(item.location)}"
                    + (f" · from your {html.escape(item.source)} email (not verified)" if item.source.endswith(" alert") else "")
                    + (" · saved by you" if item.source == "Saved by you" else "") + "</div>"
                    f"<div class=\"why\">{html.escape(item.summary)}</div></li>")
    return "<ul>" + "".join(rows) + "</ul>"


def render_html(digest: Digest) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Career Agent digest {html.escape(digest.day)}</title>
<style>
:root {{ --bg:#fff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --accent:#0969da; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#3d444d; --accent:#4493f8; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif; max-width:760px; margin:0 auto; padding:16px; }}
a {{ color:var(--accent); }} ul {{ list-style:none; padding:0; }} li {{ border-top:1px solid var(--line); padding:10px 0; }}
.head {{ font-weight:600; }} .score, .muted, .why {{ color:var(--muted); font-size:13px; font-weight:400; }}
</style></head><body>
<h1>New today · {html.escape(digest.day)}</h1>
<p class="muted">{digest.new_jobs} new jobs from your Company Radar index and alert emails; {digest.off_role} outside your saved roles and
{digest.excluded} excluded by an explicit disqualifier are not listed. Scores are heuristic fit (L0), not predictions.</p>
<h2>Eligible ({len(digest.eligible)})</h2>{_rows(digest.eligible)}
<h2>Uncertain ({len(digest.uncertain)}): check before applying</h2>{_rows(digest.uncertain)}
</body></html>
"""


def write_digest(digest: Digest) -> Path:
    directory = digest_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest.day}.html"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(render_html(digest), encoding="utf-8")
    temporary.replace(path)
    return path
