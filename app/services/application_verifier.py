"""Conservative, evidence-based status checks for job application pages."""
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.core.config import settings
from app.models.schemas import JobPosting, VerificationState
from app.services.safe_http import UnsafeURLError, safe_get


CLOSED_PATTERNS = (
    "no longer accepting applications", "position has been filled",
    "job is no longer available", "requisition is closed", "applications are closed",
    "job expired", "position closed", "job has expired", "job not found",
)
APPLY_PATTERN = re.compile(r"^(apply(?: now| for (?:this|the) (?:job|position|role))?|submit (?:your )?application|start (?:your )?application)$", re.I)
ATS_DOMAINS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com", "apply.workable.com", "jobs.smartrecruiters.com", "myworkdayjobs.com")


def _status(job: JobPosting, state: VerificationState, reason: str) -> JobPosting:
    job.verification_state = state
    job.verification_reason = reason
    job.verification_checked_at = datetime.now(timezone.utc).isoformat()
    job.application_status = "active" if state == "ACTIVE_VERIFIED" else "closed" if state == "CLOSED" else "unverified"
    return job


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _is_generic_careers_page(url: str, title: str, text: str) -> bool:
    path = urlparse(url).path.lower().strip("/")
    if path in {"", "careers", "jobs", "career", "job-search", "search"}:
        return True
    return "search jobs" in title.lower() and "job description" not in text.lower()


def _recent_posted_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        posted = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - posted).total_seconds()
        return 0 <= age <= 30 * 86400
    except (ValueError, TypeError):
        return False


def _has_apply_control(soup: BeautifulSoup, role: str) -> bool:
    for control in soup.select("a[href], button, input[type=submit], [role=button]"):
        if control.has_attr("disabled") or control.get("aria-disabled") == "true" or control.find_parent(["nav", "footer", "header"]) or control.find_parent("fieldset", disabled=True):
            continue
        label = str(control.get("value") or "") if control.name == "input" else control.get_text(" ", strip=True)
        label = label or str(control.get("aria-label") or "")
        if not APPLY_PATTERN.fullmatch(label.strip()):
            continue
        if control.name == "a" and str(control.get("href") or "").strip().lower().startswith(("javascript:", "mailto:")):
            continue
        # An apply control inside a separate card must identify this role in
        # that card. Controls elsewhere on a single job page use its body.
        scope = control.find_parent(["article", "section", "main", "form"]) or soup
        if role and role in _normalized(scope.get_text(" ", strip=True)):
            return True
    return False


def _has_explicit_closure(soup: BeautifulSoup, role: str) -> bool:
    """Ignore help text and closure messages attached to a different job card."""
    inspected = set()
    for node in soup.find_all(string=True):
        block = node.find_parent(["p", "li", "h1", "h2", "h3", "div", "section", "article", "main", "body"]) or soup
        if id(block) in inspected or node.find_parent(["footer", "nav", "header"]):
            continue
        inspected.add(id(block))
        text = block.get_text(" ", strip=True).lower()
        statements = re.split(r"(?<=[.!?])\s+", text)
        statements = [statement for statement in statements if any(pattern in statement for pattern in CLOSED_PATTERNS)]
        if not any(not re.search(r"\b(?:if|when|whether|may|might|could|once|until)\b|\?", statement) for statement in statements):
            continue
        scope = node.find_parent(["article", "section", "main"])
        if scope is not None and role not in _normalized(scope.get_text(" ", strip=True)):
            # A differently headed card describes another vacancy. An
            # unheaded status region can refer to the enclosing job page.
            if scope.find(["h1", "h2", "h3"]):
                continue
        return True
    return False


AGGREGATOR_SOURCES = ("Adzuna", "Jooble")
_AGGREGATOR_HOST = re.compile(r"(?:^|\.)(?:adzuna\.[a-z.]+|jooble\.org)$")


def _is_aggregator_link(job: JobPosting) -> bool:
    host = (urlparse(str(job.application_url)).hostname or "").lower().rstrip(".")
    return job.source in AGGREGATOR_SOURCES or bool(_AGGREGATOR_HOST.search(host))


# Hard constraint (CLAUDE.md): these sites are never fetched by the app, only opened by the user.
_NEVER_FETCHED_HOST = re.compile(r"(?:^|\.)(?:linkedin\.com|lnkd\.in|naukri\.com|indeed\.[a-z.]+)$")


def _is_never_fetched(job: JobPosting) -> bool:
    host = (urlparse(str(job.application_url)).hostname or "").lower().rstrip(".")
    return bool(_NEVER_FETCHED_HOST.search(host))


async def verify_application(job: JobPosting) -> JobPosting:
    if not job.application_url:
        return _status(job, "UNVERIFIED", "No application URL was provided.")
    if settings.demo_mode:
        return _status(job, "UNVERIFIED", "Demo data: synthetic listing, never fetched.")
    if _is_never_fetched(job):
        return _status(job, "UNVERIFIED", "LinkedIn, Naukri and Indeed pages are never opened automatically. "
                                          "Open the link to check the listing.")
    if _is_aggregator_link(job):
        # Adzuna and Jooble links are tracked redirects; an automated visit would count as a click.
        return _status(job, "UNVERIFIED", "Aggregator link (Adzuna/Jooble): not checked automatically, because "
                                          "automated visits would count as clicks. Open it to check the listing.")
    try:
        response = await safe_get(str(job.application_url), timeout=settings.request_timeout_seconds)
    except UnsafeURLError:
        return _status(job, "UNVERIFIED", "Destination or response was blocked by public-page safety checks.")
    except Exception:
        return _status(job, "UNVERIFIED", "Application page could not be reached or inspected.")
    if response.status_code in {404, 410}:
        return _status(job, "CLOSED", f"Application page returned HTTP {response.status_code}.")
    if response.status_code != 200:
        return _status(job, "UNVERIFIED", f"Application page returned HTTP {response.status_code}; availability is uncertain.")
    soup = BeautifulSoup(response.text or "", "html.parser")
    for hidden in soup.select('script, style, template, noscript, [hidden], [aria-hidden="true"]'):
        hidden.decompose()
    for hidden in soup.select("[style]"):
        if hidden.attrs and re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", str(hidden.get("style") or ""), re.I):
            hidden.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    visible = soup.get_text(" ", strip=True)
    role = _normalized(job.title)
    if _has_explicit_closure(soup, role):
        return _status(job, "CLOSED", "Application page explicitly says the job is closed or unavailable.")
    final_url = str(response.url)
    if _is_generic_careers_page(final_url, title, visible):
        return _status(job, "UNVERIFIED", "The link leads to a general careers or job-search page.")
    role_identified = bool(role and role in _normalized(visible))
    if role_identified and _has_apply_control(soup, role):
        return _status(job, "ACTIVE_VERIFIED", "The identified role has a visible, enabled application control on its page.")
    host = (urlparse(final_url).hostname or "").lower().rstrip(".")
    ats = any(host == domain or host.endswith("." + domain) for domain in ATS_DOMAINS)
    if role_identified and ats and _recent_posted_date(job.posted_date):
        return _status(job, "LIKELY_ACTIVE", "A recent listing links to an identified role on a known ATS; an application control was not verified.")
    return _status(job, "UNVERIFIED", "No enabled application control for this role was found; the page may require JavaScript.")
