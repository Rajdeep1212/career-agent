"""Plain-text, candidate-grounded application drafts with safe mail headers."""
import re
from urllib.parse import urlsplit

from app.models.schemas import CandidateProfile


def validate_header(value: str, label: str = 'Header') -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f'{label} must be nonempty text without control characters')
    return value.strip()


def validate_recipient(recipient: str) -> str:
    recipient = validate_header(recipient, 'Recipient')
    if len(recipient) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", recipient):
        raise ValueError('Provide one valid recipient email address')
    local = recipient.split('@')[0]
    if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
        raise ValueError('Invalid recipient email address')
    return recipient


def _job_text(job: dict) -> str:
    return ' '.join([str(job.get('title', '')), str(job.get('description', '')), *job.get('skills', [])]).casefold()


def relevant_skills(profile: CandidateProfile, job: dict) -> list[str]:
    text = _job_text(job)
    return [skill for skill in profile.skills if re.search(r'(?<!\w)' + re.escape(skill.casefold()) + r'(?!\w)', text)]


def _best_evidence(items: list[str], job: dict) -> str | None:
    tokens = set(re.findall(r'\w{3,}', _job_text(job)))
    return max(items, key=lambda item: len(tokens.intersection(re.findall(r'\w{3,}', item.casefold())))) if items else None


def compose_job_application_email(
    recipient: str,
    company: str,
    title: str,
    application_url: str | None = None,
    custom_note: str | None = None,
    *,
    profile: CandidateProfile | None = None,
    job: dict | None = None,
    attachment_id: str | None = None,
    has_attachment: bool = False,
) -> tuple[str, str]:
    validate_recipient(recipient)
    company = validate_header(company, 'Company')
    title = validate_header(title, 'Role title')
    if profile is None:
        from app.storage.profile_store import load_profile
        profile = load_profile()
    name = validate_header(profile.name, 'Candidate name') if profile.name else 'Candidate'
    job = {**(job or {}), 'company': company, 'title': title}
    subject = f'Application for {title} | {name}'
    paragraphs = ['Dear Hiring Team,', f'I am interested in the {title} opportunity at {company}.']
    education = []
    if profile.degree:
        education.append(f'My education includes {profile.degree}.')
    if profile.graduation_year:
        education.append(f'My listed graduation year is {profile.graduation_year}.')
    if education:
        paragraphs.append(' '.join(education))
    overlap = relevant_skills(profile, job)
    if overlap:
        paragraphs.append('My skills relevant to the role include ' + ', '.join(overlap[:6]) + '.')
    elif profile.skills:
        paragraphs.append('My profile includes skills in ' + ', '.join(profile.skills[:6]) + '.')
    project = _best_evidence(profile.projects, job)
    if project:
        paragraphs.append('Project evidence from my profile: ' + project)
    research = _best_evidence(profile.research, job)
    if research:
        paragraphs.append('Research listed in my profile: ' + research)
    if custom_note and custom_note.strip():
        paragraphs.append(custom_note.strip())
    if application_url:
        link = str(application_url)
        parts = urlsplit(link)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or any(char.isspace() for char in link):
            raise ValueError('Role link must be an HTTP(S) URL without credentials')
        paragraphs.append('Role link: ' + link)
    if has_attachment or attachment_id:
        paragraphs.append('I have attached my resume for your consideration.')
    paragraphs.append('Thank you for your time. I would welcome the opportunity to discuss how my background relates to the role.')
    paragraphs.append('Kind regards,\n' + name)
    return subject, '\n\n'.join(paragraphs) + '\n'
