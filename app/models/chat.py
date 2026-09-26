from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.career import ApplicationStatus


ChatAction = Literal[
    "search_jobs",
    "career_advice",
    "select_job",
    "save_application",
    "update_application",
    "prepare_outreach",
    "send_draft",
    "none",
]


class ModelCapabilities(BaseModel):
    structured_output: bool = False
    tool_calling: bool = False


class ModelDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ChatAction = "none"
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)
    explanation: str | None = Field(default=None, max_length=2000)
    advisory_roles: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("advisory_roles")
    @classmethod
    def clean_advisory_roles(cls, values):
        result = []
        seen = set()
        for value in values:
            value = value.strip()[:100]
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result


class _RequestIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    turn_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ChatRunRequest(_RequestIdentity):
    message: str = Field(min_length=1, max_length=4000)
    career_session_id: str | None = Field(default=None, max_length=128)
    selected_job_id: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    application_id: str | None = Field(default=None, max_length=64)
    contact_id: str | None = Field(default=None, max_length=64)
    draft_id: int | None = Field(default=None, ge=1)
    recipient: str | None = Field(default=None, max_length=320)
    application_status: ApplicationStatus | None = None
    notes: str | None = Field(default=None, max_length=20000)
    include_seen: bool = False
    strict_mode: bool | None = None
    # Query job-site aggregators (uses API quota); otherwise the local index and cache only.
    refresh: bool = False


class ChatResumeRequest(_RequestIdentity):
    draft_id: int = Field(ge=1)
    confirmed: bool
    edited_subject: str | None = Field(default=None, min_length=1, max_length=998)
    edited_body: str | None = Field(default=None, min_length=1, max_length=100000)


class ChatResponse(BaseModel):
    thread_id: str
    turn_id: str
    stage: Literal["completed", "needs_clarification", "awaiting_send_confirmation", "cancelled"]
    message: str = Field(max_length=4000)
    career_session_id: str | None = None
    recommendation_ids: list[str] = Field(default_factory=list)
    advisory_roles: list[str] = Field(default_factory=list)
    draft_id: int | None = None
    application_id: str | None = None
    replayed: bool = False
