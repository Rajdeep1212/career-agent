from typing import TypedDict


class CareerGraphState(TypedDict, total=False):
    latest_user_message: str
    conversation_summary: str
    thread_id: str
    turn_id: str
    action: str
    stage: str
    response_text: str
    career_session_id: str | None
    recommendation_ids: list[str]
    selected_job_id: str | None
    application_id: str | None
    contact_id: str | None
    draft_id: int | None
    include_seen: bool
    strict_mode: bool | None
    refresh: bool
    advisory_roles: list[str]
    model_explanation: str | None
    confirmed: bool
