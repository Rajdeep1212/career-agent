"""Persistent job/application/contact/draft linkage without HTTP concerns."""
from app.models.schemas import PrepareJobEmailRequest
from app.services.outreach_service import draft_outreach
from app.storage import career_store
from app.storage.email_store import create_draft
from app.storage.profile_store import load_profile


class OutreachNotFoundError(ValueError):
    pass


class OutreachConflictError(ValueError):
    pass


class OutreachInputError(ValueError):
    pass


def draft_with_outreach_linkage(draft: dict) -> dict:
    linkage = career_store.get_outreach_for_draft(draft["id"])
    if linkage is None:
        return draft
    return {
        **draft,
        "job_id": linkage["job_id"],
        "application_id": linkage["application_id"],
        "contact_id": linkage["contact_id"],
        "short_message": linkage["short_message"],
    }


def prepare_linked_outreach(request: PrepareJobEmailRequest) -> dict:
    application = None
    job = None
    job_id = request.job_id
    if request.application_id:
        application = career_store.get_application(request.application_id)
        if application is None:
            raise OutreachNotFoundError("Application not found.")
        if job_id and application["job_id"] != job_id:
            raise OutreachConflictError("Job and application do not match.")
        job_id = application["job_id"]
        job = application["job"]
    elif job_id:
        job = career_store.get_job(job_id)
        if job is None:
            raise OutreachNotFoundError("Job not found.")
    if request.contact_id and job is None:
        raise OutreachInputError("A contact requires a stored job or application.")

    recipient = request.recipient
    if request.contact_id:
        contact = career_store.get_contact(request.contact_id)
        if contact is None:
            raise OutreachNotFoundError("Contact not found.")
        if job is None:
            raise OutreachInputError("A contact requires a stored job or application.")
        if contact["company"].strip().casefold() != str(job["company"]).strip().casefold():
            raise OutreachConflictError("Contact and job company do not match.")
        recipient = contact["contact_method"]

    if application is None and job_id:
        application = career_store.save_application(job_id, status="SAVED")
    grounded_job = job or {
        "company": request.company,
        "title": request.title,
        "application_url": request.application_url,
        "skills": [],
        "description": "",
    }
    try:
        composed = draft_outreach(
            load_profile(), grounded_job, recipient,
            custom_note=request.custom_note,
            has_attachment=bool(request.attachment_id),
        )
        draft = create_draft(
            recipient=recipient,
            subject=composed["subject"],
            body=composed["body"],
            attachment_id=request.attachment_id,
        )
    except ValueError as exc:
        raise OutreachInputError("Outreach draft fields are invalid.") from exc
    if application is None:
        return {**draft, "short_message": composed["short_message"]}
    career_store.link_outreach(
        application["id"], draft["id"], composed["short_message"], contact_id=request.contact_id,
    )
    return draft_with_outreach_linkage(draft)

