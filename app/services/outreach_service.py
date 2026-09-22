"""Outreach assistance uses stored contacts and supplied facts; it never sends."""
from app.models.schemas import CandidateProfile
from app.services.email_composer import compose_job_application_email, relevant_skills
from app.storage import career_store


def find_outreach_options(job: dict) -> dict:
    company = str(job.get('company', '')).strip()
    contacts = career_store.list_contacts(company) if company else []
    if contacts:
        message = 'Stored contacts are available. Review their source and recipient before preparing a draft.'
    else:
        message = 'No contact is stored for this company. Add a contact you know or a contact published by the company.'
    return {'contacts': contacts, 'message': message}


def draft_outreach(profile: CandidateProfile, job: dict, recipient: str, custom_note: str | None = None, has_attachment: bool = False) -> dict:
    subject, body = compose_job_application_email(
        recipient, str(job.get('company', '')), str(job.get('title', '')),
        application_url=job.get('application_url'), custom_note=custom_note,
        profile=profile, job=job, has_attachment=has_attachment,
    )
    overlap = relevant_skills(profile, job)
    short = f"Hello, I am interested in the {job['title']} opportunity at {job['company']}."
    if overlap:
        short += ' My relevant skills include ' + ', '.join(overlap[:3]) + '.'
    short += ' May I share my profile for your consideration?'
    return {'subject': subject, 'body': body, 'short_message': short}
