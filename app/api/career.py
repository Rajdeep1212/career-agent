"""Local career tracking endpoints. These routes never send messages or apply."""
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.origin_security import has_exact_local_origin
from app.models.career import ApplicationStatus, ContactCandidate
from app.services.job_snapshot import snapshot_inputs
from app.storage import career_events, career_store


router = APIRouter(tags=['Career'])

_PRIVATE_FIELDS = {
    'access_token', 'authorization_code', 'client_secret', 'credentials',
    'oauth_tokens', 'provider_payload', 'raw_payload', 'raw_provider_payload',
    'raw_response', 'refresh_token', 'token', 'tokens',
}


def _is_private_field(key: object) -> bool:
    name = str(key).casefold()
    return (
        name.startswith('_')
        or name in _PRIVATE_FIELDS
        or name.endswith('_cache_key')
        or name.endswith('_secret')
        or name.endswith('_token')
    )


def public_career_data(value):
    if isinstance(value, dict):
        return {
            key: public_career_data(item)
            for key, item in value.items()
            if not _is_private_field(key)
        }
    if isinstance(value, list):
        return [public_career_data(item) for item in value]
    return value


def require_tracker_origin(request: Request) -> None:
    if not has_exact_local_origin(request):
        raise HTTPException(
            status_code=403,
            detail='Open the localhost dashboard to modify tracker data.',
        )


class SaveApplicationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(min_length=1, max_length=128)
    status: ApplicationStatus = 'SAVED'
    notes: str = Field(default='', max_length=20000)


class UpdateApplicationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: ApplicationStatus | None = None
    notes: str | None = Field(default=None, max_length=20000)


BACKDATE_LIMIT_DAYS = 365
RequestId = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')]


class _TrackerEventRequest(BaseModel):
    """A client-generated request_id makes retries and double clicks safe (docs/M2_PLAN.md §3)."""
    model_config = ConfigDict(extra='forbid')
    request_id: RequestId
    occurred_at: datetime | None = None
    note: str = Field(default='', max_length=2000)

    @field_validator('occurred_at')
    @classmethod
    def _within_limits(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if value > now:
            raise ValueError('The date cannot be in the future.')
        if value < now - timedelta(days=BACKDATE_LIMIT_DAYS):
            raise ValueError(f'The date can be at most {BACKDATE_LIMIT_DAYS} days ago.')
        return value.astimezone(timezone.utc)


class MarkAppliedRequest(_TrackerEventRequest):
    job_id: str = Field(min_length=1, max_length=128)
    applied_via: Literal['company_site', 'ats', 'job_board', 'email', 'referral', 'other'] | None = None
    effort_minutes: int | None = Field(default=None, ge=0, le=1440)


class OutcomeRequest(_TrackerEventRequest):
    event_type: Literal['recruiter_reply', 'online_test', 'interview', 'offer', 'rejected', 'withdrawn',
                        'no_response_confirmed']


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: RequestId


class RelevanceContext(BaseModel):
    """Where the labelled job was shown; kept with the label for the evaluation harness."""
    model_config = ConfigDict(extra='forbid')
    search_session_id: str | None = Field(default=None, max_length=128)
    position: int | None = Field(default=None, ge=1, le=1000)
    query: str | None = Field(default=None, max_length=500)


class RelevanceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: Literal['up', 'down', 'clear']
    request_id: RequestId
    context: RelevanceContext = Field(default_factory=RelevanceContext)


def _public_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
        host = parts.hostname
        return bool(parts.scheme in ('http', 'https') and host and '.' in host
                    and not parts.username and not parts.password and not any(char.isspace() for char in value))
    except ValueError:
        return False


class SaveContactRequest(ContactCandidate):
    model_config = ConfigDict(extra='forbid')
    company: str = Field(min_length=1, max_length=200)
    contact_method: str = Field(min_length=1, max_length=2000)
    public_source: str = Field(default='Provided by user', min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)

    @field_validator('company', 'public_source', 'name', 'role')
    @classmethod
    def plain_text(cls, value):
        if value is None:
            return value
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError('Use nonempty text without control characters')
        if value.lower().startswith(('javascript:', 'data:', 'file:')):
            raise ValueError('Unsafe source scheme')
        return value.strip()

    @field_validator('contact_method')
    @classmethod
    def valid_contact(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('Contact cannot contain control characters')
        value = value.strip()
        email = re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", value)
        if not email and not _public_url(value):
            raise ValueError('Provide one email address or an HTTP(S) profile URL without credentials')
        if email and (len(value) > 254 or value.startswith('.') or '..' in value.split('@')[0] or value.split('@')[0].endswith('.')):
            raise ValueError('Invalid email address')
        return value


@router.get('/applications')
def applications():
    return public_career_data(career_store.list_applications())


@router.get('/tracker/funnel')
def tracker_funnel():
    """Descriptive counts from the event log (claim level L0), never estimates."""
    return public_career_data(career_store.funnel())


@router.get('/applications/{application_id}')
def get_application(application_id: str):
    result = career_store.get_application(application_id)
    if result is None:
        raise HTTPException(status_code=404, detail='Application not found')
    return public_career_data(result)


def _snapshot(job_id: str):
    """The CV and L0 features to freeze with a status event (docs/M2_PLAN.md §1.3)."""
    job = career_store.get_job(job_id)
    return snapshot_inputs(job) if job else None


@router.post('/applications')
def save_application(request: SaveApplicationRequest, http_request: Request):
    require_tracker_origin(http_request)
    try:
        result = career_store.save_application(request.job_id, request.status, request.notes,
                                               snapshot=_snapshot(request.job_id))
        return public_career_data(result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail='Job not found') from exc


def _event_response(call):
    """Run one tracker-event store call and map its errors to HTTP statuses."""
    try:
        return public_career_data(call())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except career_store.RequestConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@router.post('/applications/applied')
def mark_applied(request: MarkAppliedRequest, http_request: Request):
    """One click from a job card or the tracker (docs/M2_PLAN.md §3)."""
    require_tracker_origin(http_request)
    if career_store.get_job(request.job_id) is None:
        raise HTTPException(status_code=404, detail='Job not found')
    return _event_response(lambda: career_store.record_applied(
        request.job_id, request_id=request.request_id, occurred_at=_iso(request.occurred_at),
        applied_via=request.applied_via, effort_minutes=request.effort_minutes, note=request.note,
        snapshot=_snapshot(request.job_id)))


@router.post('/applications/{application_id}/events')
def record_outcome(application_id: str, request: OutcomeRequest, http_request: Request):
    require_tracker_origin(http_request)
    current = career_store.get_application(application_id)
    if current is None:
        raise HTTPException(status_code=404, detail='Application not found')
    return _event_response(lambda: career_store.record_outcome(
        application_id, request.event_type, request_id=request.request_id, occurred_at=_iso(request.occurred_at),
        note=request.note, snapshot=_snapshot(current['job_id'])))


@router.post('/applications/{application_id}/events/{event_id}/undo')
def undo_event(application_id: str, event_id: int, request: UndoRequest, http_request: Request):
    require_tracker_origin(http_request)
    return _event_response(lambda: career_store.undo_event(application_id, event_id, request_id=request.request_id))


@router.get('/jobs/relevance')
def job_relevance_labels():
    """Current live thumbs labels. A separate scale from graded evaluation labels (docs/M2_PLAN.md §4)."""
    return {'scale': career_events.THUMBS_SCALE, 'rubric_version': career_events.THUMBS_RUBRIC_VERSION,
            'labels': career_store.thumbs()}


@router.post('/jobs/{job_id}/relevance')
def rate_job(job_id: str, request: RelevanceRequest, http_request: Request):
    """Thumbs up, down or clear on a stored job. Feedback only: it never changes ranking or the tracker."""
    require_tracker_origin(http_request)
    if career_store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail='Job not found')
    snapshot = None if request.label == 'clear' else _snapshot(job_id)
    return _event_response(lambda: career_store.record_thumb(
        job_id, request.label, request_id=request.request_id, context=request.context.model_dump(), snapshot=snapshot))


@router.patch('/applications/{application_id}')
def update_application(application_id: str, request: UpdateApplicationRequest, http_request: Request):
    require_tracker_origin(http_request)
    current = career_store.get_application(application_id)
    if current is None:
        raise HTTPException(status_code=404, detail='Application not found')
    snapshot = _snapshot(current['job_id']) if request.status not in (None, current['status']) else None
    result = career_store.update_application(application_id, request.status, request.notes, snapshot=snapshot)
    if result is None:
        raise HTTPException(status_code=404, detail='Application not found')
    return public_career_data(result)


@router.get('/career/jobs/{job_id}')
def get_job(job_id: str):
    result = career_store.get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail='Job not found')
    return public_career_data(result)


@router.get('/agent/sessions/{session_id}')
def get_session(session_id: str):
    result = career_store.get_session(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail='Session not found')
    return public_career_data(result)


@router.get('/contacts')
def contacts(company: str = Query(min_length=1, max_length=200)):
    return public_career_data(career_store.list_contacts(company))


@router.post('/contacts')
def save_contact(request: SaveContactRequest, http_request: Request):
    require_tracker_origin(http_request)
    # A user-entered source URL is provenance, not proof of independent verification.
    contact = ContactCandidate(**{**request.model_dump(), 'confidence': 'user_provided'})
    return public_career_data(career_store.save_contact(contact))
