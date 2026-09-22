"""Shared one-attempt Gmail boundary used by HTTP routes and the graph."""
from app.services.gmail_service import send_approved_email
from app.storage import career_store
from app.storage.email_store import (
    approve_draft,
    claim_draft_for_send,
    get_draft,
    mark_send_failed,
    mark_sent,
)


class DraftNotFoundError(ValueError):
    pass


class DraftNotClaimableError(ValueError):
    pass


class SendResultUncertainError(RuntimeError):
    pass


def send_draft_once(draft_id: int, *, sender=send_approved_email) -> dict:
    if not get_draft(draft_id):
        raise DraftNotFoundError("Draft not found.")
    draft = claim_draft_for_send(draft_id)
    if not draft:
        raise DraftNotClaimableError("Draft is not approved or has already been processed.")
    try:
        result = sender(
            recipient=draft["recipient"],
            subject=draft["subject"],
            body=draft["body"],
            attachment_id=draft["attachment_id"],
        )
        career_store.mark_outreach_sent(draft_id)
        sent = mark_sent(draft_id, gmail_message_id=result.get("id"))
        if not sent:
            raise RuntimeError("Local send state could not be finalized.")
        return sent
    except Exception as exc:
        mark_send_failed(draft_id)
        raise SendResultUncertainError(
            "The Gmail send result is uncertain. This draft is locked and will not be retried automatically."
        ) from exc


def approve_and_send_draft(draft_id: int, *, sender=send_approved_email) -> dict:
    if not get_draft(draft_id):
        raise DraftNotFoundError("Draft not found.")
    if not approve_draft(draft_id):
        raise DraftNotClaimableError("Draft could not be approved for this send action.")
    return send_draft_once(draft_id, sender=sender)

