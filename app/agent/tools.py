from app.models.schemas import PrepareJobEmailRequest
from app.services.email_send_boundary import approve_and_send_draft
from app.services.outreach_workflow import prepare_linked_outreach
from app.storage import career_store
from app.storage.email_store import update_draft_content


class CareerGraphTools:
    def save_application(self, state: dict) -> dict:
        job_id = state.get("selected_job_id")
        if not job_id:
            raise ValueError("A stored job is required.")
        return career_store.save_application(job_id, status="SAVED", notes=state.get("notes") or "")

    def update_application(self, state: dict) -> dict:
        application_id = state.get("application_id")
        if not application_id:
            raise ValueError("A stored application is required.")
        result = career_store.update_application(
            application_id,
            status=state.get("application_status"),
            notes=state.get("notes"),
        )
        if result is None:
            raise ValueError("Application not found.")
        return result

    def prepare_outreach(self, state: dict) -> dict:
        job = career_store.get_job(state.get("selected_job_id")) if state.get("selected_job_id") else None
        application = career_store.get_application(state.get("application_id")) if state.get("application_id") else None
        grounded = (application or {}).get("job") or job or {}
        request = PrepareJobEmailRequest(
            recipient=state.get("recipient") or "",
            company=grounded.get("company") or "Stored company",
            title=grounded.get("title") or "Stored role",
            application_url=grounded.get("application_url"),
            job_id=state.get("selected_job_id"),
            application_id=state.get("application_id"),
            contact_id=state.get("contact_id"),
        )
        return prepare_linked_outreach(request)

    def update_draft(self, draft_id: int, subject=None, body=None) -> None:
        if subject is None and body is None:
            return
        if not update_draft_content(draft_id, subject=subject, body=body):
            raise ValueError("Draft can no longer be edited.")

    def approve_and_send(self, draft_id: int) -> dict:
        return approve_and_send_draft(draft_id)
