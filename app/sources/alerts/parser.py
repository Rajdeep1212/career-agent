"""Parse job-alert emails from LinkedIn, Naukri and Indeed into jobs.

Layouts change often, so nothing depends on CSS classes. A job is found by the
shape of its link (LinkedIn /jobs/view/<id>, Naukri /job-listings-...-<id>,
Indeed ?jk=<id>); links to the same job (logo, title, "View job") are grouped.
The title is the link's text, and company and location are read from the lines
that follow it in the same block. Links are cleaned of tracking parameters and
stored; the pages behind them are never fetched.
"""
import email
import email.policy
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, Tag

from app.models.schemas import JobPosting
from app.providers.common import build_posting

SOURCES = {"linkedin": "LinkedIn alert", "naukri": "Naukri alert", "indeed": "Indeed alert"}
PLATFORM_NAMES = {"linkedin": "LinkedIn", "naukri": "Naukri", "indeed": "Indeed"}
_GENERIC = re.compile(r"^(?:view(?: job| details)?|apply(?: now)?|see (?:all|more)(?: jobs)?|easy apply|save|"
                      r"actively recruiting|promoted|new|be an early applicant|more jobs)$", re.I)
_EXPERIENCE = re.compile(r"^\d+\s*(?:-|to)\s*\d+\s*(?:yrs?|years?)$|^\d+\+?\s*(?:yrs?|years?)$", re.I)
_SALARY = re.compile(r"(?:₹|rs\.?|inr|lpa|\blakh|\bper (?:year|month|annum)\b|\ba (?:year|month)\b)", re.I)
_BLOCK_TAGS = ("td", "div", "li", "tr", "table", "section", "article")


@dataclass
class AlertJob:
    platform: str
    job_id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    extra: list[str] = field(default_factory=list)   # experience, skills or snippet lines from the email


@dataclass
class ParsedAlert:
    platform: str | None
    message_id: str
    subject: str
    received_at: str | None
    jobs: list[AlertJob]

    def postings(self) -> list[JobPosting]:
        received = ""
        if self.received_at:
            day = datetime.fromisoformat(self.received_at)
            received = f" received {day.day} {day:%b %Y}"
        result = []
        for job in self.jobs:
            note = f"From a {PLATFORM_NAMES[job.platform]} job alert{received}."
            description = " ".join([note, *(_describe(line) for line in job.extra)])
            posting = build_posting(source=SOURCES[job.platform], title=job.title, company=job.company or "Unknown company",
                                    location=job.location or "Not specified", description=description, url=job.url,
                                    job_id=job.job_id)
            mode = "remote" if re.search(r"\bremote\b", job.location, re.I) else "hybrid" if re.search(r"\bhybrid\b", job.location, re.I) else None
            result.append(posting.model_copy(update={"work_mode": mode}) if mode else posting)
        return result


def _describe(line: str) -> str:
    """'0-2 Yrs' -> 'Experience: 0-2 years.' so eligibility can read it; other lines kept as sentences."""
    if _EXPERIENCE.match(line):
        return "Experience: " + re.sub(r"\s*yrs?\b", " years", line, flags=re.I).strip() + "."
    return line if line.endswith((".", "!", "?")) else line + "."


def job_link(href: str) -> tuple[str, str, str] | None:
    """(platform, stable job id, canonical URL) for a job link, else None."""
    try:
        parts = urlsplit(href.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        match = re.search(r"/jobs/view/(\d+)", parts.path)
        if match:
            return "linkedin", f"linkedin:{match[1]}", f"https://www.linkedin.com/jobs/view/{match[1]}/"
    if host == "naukri.com" or host.endswith(".naukri.com"):
        match = re.match(r"^/job-listings-[a-z0-9-]*?-(\d{6,})$", parts.path.rstrip("/"))
        if match:
            return "naukri", f"naukri:{match[1]}", f"https://www.naukri.com{parts.path.rstrip('/')}"
    if re.search(r"(?:^|\.)indeed\.[a-z.]+$", host):
        jk = parse_qs(parts.query).get("jk", [""])[0]
        if re.fullmatch(r"[0-9a-f]{16}", jk):
            return "indeed", f"indeed:{jk}", f"https://{host}/viewjob?jk={jk}"
    return None


def _lines(node: Tag) -> list[str]:
    return [" ".join(text.split()) for text in node.get_text("\n").split("\n") if text.strip()]


def _block(anchor: Tag, title: str) -> list[str]:
    """Lines after the title in the smallest enclosing block that has any."""
    for parent in anchor.parents:
        if parent.name not in _BLOCK_TAGS:
            continue
        lines = _lines(parent)
        if title in lines and len(lines) > lines.index(title) + 1:
            following = lines[lines.index(title) + 1:]
            return [line for line in following if not _GENERIC.match(line)][:5]
    return []


def _split_company_location(platform: str, lines: list[str]) -> tuple[str, str, list[str]]:
    if not lines:
        return "", "", []
    first, rest = lines[0], lines[1:]
    for separator in (" · ", " • ", " | "):
        if separator in first:
            company, location = first.split(separator, 1)
            return company.strip(), location.strip(), rest
    if platform == "indeed" and " - " in first:
        company, location = first.rsplit(" - ", 1)
        return company.strip(), location.strip(), rest
    extra = [line for line in rest if _EXPERIENCE.match(line)]
    others = [line for line in rest if not _EXPERIENCE.match(line) and not _SALARY.search(line)]
    location = others[0] if others else ""
    extra += [line for line in others[1:]]
    return first, location, extra


def _from_html(html: str) -> list[AlertJob]:
    soup = BeautifulSoup(html, "html.parser")
    order: list[str] = []
    anchors: dict[str, list[Tag]] = {}
    links: dict[str, tuple[str, str]] = {}
    for anchor in soup.find_all("a", href=True):
        found = job_link(str(anchor["href"]))
        if found is None:
            continue
        platform, job_id, url = found
        if job_id not in anchors:
            order.append(job_id)
            anchors[job_id] = []
            links[job_id] = (platform, url)
        anchors[job_id].append(anchor)
    jobs = []
    for job_id in order:
        titled = [(" ".join(a.get_text(" ").split()), a) for a in anchors[job_id]]
        titled = [(text, a) for text, a in titled if text and re.search(r"[A-Za-z]", text) and not _GENERIC.match(text)]
        if not titled:
            continue
        title, anchor = max(titled, key=lambda pair: len(pair[0]))
        platform, url = links[job_id]
        company, location, extra = _split_company_location(platform, _block(anchor, title))
        jobs.append(AlertJob(platform, job_id, url, title[:200], company[:200], location[:200], extra))
    return jobs


def _from_text(text: str) -> list[AlertJob]:
    jobs, seen = [], set()
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        for href in re.findall(r"https?://\S+", line):
            found = job_link(href)
            if found is None or found[1] in seen:
                continue
            platform, job_id, url = found
            # Plain-text alerts list "Title / Company / Location" on the lines before the link.
            before = [value for value in lines[max(0, index - 4):index] if value][-3:]
            if not before:
                continue
            title, company, location = (before + ["", ""])[:3]
            seen.add(job_id)
            jobs.append(AlertJob(platform, job_id, url, title, company, location))
    return jobs


def parse_alert_email(raw: bytes) -> ParsedAlert:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    html = message.get_body(preferencelist=("html",))
    plain = message.get_body(preferencelist=("plain",))
    jobs = _from_html(html.get_content()) if html is not None else []
    if not jobs and plain is not None:
        jobs = _from_text(plain.get_content())
    received = None
    if message["Date"]:
        try:
            received = parsedate_to_datetime(str(message["Date"])).isoformat()
        except (TypeError, ValueError):
            received = None
    platforms = [job.platform for job in jobs]
    platform = max(set(platforms), key=platforms.count) if platforms else None
    return ParsedAlert(platform=platform, message_id=str(message["Message-ID"] or "").strip(),
                       subject=str(message["Subject"] or ""), received_at=received, jobs=jobs)
