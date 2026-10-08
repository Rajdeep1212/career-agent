"""The tracker API, /api/v1 (docs/TRACKER_PLAN.md section 4, the routes marked TRK2).

Reads and writes data/tracker.sqlite3 through app/tracker/store.py; data/agent.sqlite3 and the M2 routes in
app/api/career.py are not touched. Every query is scoped to the owner, who is the one local user until TRK4 adds
sign-in. Mutations need the local origin and an `Idempotency-Key` header. No page is ever fetched.
"""
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.origin_security import has_tracker_origin
from app.tracker import service, store


def current_owner() -> str:
    return store.LOCAL_USER_ID


def _database() -> None:
    store.ensure()


def local_origin(request: Request) -> None:
    if not has_tracker_origin(request):
        raise HTTPException(status_code=403, detail="Open the localhost dashboard or the tracker web app to make this change.")


router = APIRouter(prefix="/api/v1", tags=["tracker"], dependencies=[Depends(_database)])
_MUTATION = [Depends(local_origin)]
Owner = Depends(current_owner)
Key = Header(..., alias="Idempotency-Key", min_length=1, max_length=200)
_SHA256 = r"^[0-9a-f]{64}$"


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except service.TrackerError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"message": str(exc), **exc.extra}) from exc


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApplicationCreate(_Body):
    job_id: str | None = None
    title: str = ""
    company: str = ""
    url: str | None = None
    location: str | None = None
    description: str | None = None
    source: str | None = None
    channel: str | None = None
    notes: str = ""
    cv_version_id: str | None = None
    status: str = "SAVED"
    occurred_at: str | None = None


class QuickAdd(_Body):
    url: str = Field(min_length=1)
    title: str = ""
    company: str = ""
    location: str | None = None
    description: str | None = None
    source: str | None = None
    channel: str | None = None
    notes: str = ""
    cv_version_id: str | None = None
    status: str = "SAVED"
    occurred_at: str | None = None
    confirm_job_id: str | None = None
    preview: bool = False


class ApplicationPatch(_Body):
    notes: str | None = None
    next_follow_up_at: str | None = None
    cv_version_id: str | None = None


class EventCreate(_Body):
    event_type: str
    occurred_at: str | None = None
    note: str = ""


class CvVersionCreate(_Body):
    label: str
    file_sha256: str | None = Field(default=None, pattern=_SHA256)
    notes: str = ""


class CompanyCreate(_Body):
    name: str
    ats: str | None = None
    careers_url: str | None = None
    reapply_after: str | None = None
    notes: str = ""


class CompanyPatch(_Body):
    ats: str | None = None
    careers_url: str | None = None
    reapply_after: str | None = None
    notes: str | None = None


class ContactCreate(_Body):
    name: str
    company_id: str | None = None
    role: str | None = None
    channel: str | None = None
    referral_status: str | None = None
    notes: str = ""


class ContactPatch(_Body):
    name: str | None = None
    company_id: str | None = None
    role: str | None = None
    channel: str | None = None
    referral_status: str | None = None
    notes: str | None = None


def _status(response: Response, new: bool) -> None:
    response.status_code = 201 if new else 200


# ---------- applications ----------

@router.get("/applications")
def list_applications(status: str | None = None, company: str | None = None, q: str | None = None, owner: str = Owner):
    with _errors():
        rows = service.list_applications(owner, status=status, company=company, q=q)
    return {"count": len(rows), "applications": rows}


@router.post("/applications", dependencies=_MUTATION)
def create_application(body: ApplicationCreate, response: Response, key: str = Key, owner: str = Owner):
    with _errors():
        application, created = service.create_application(owner, key, **body.model_dump())
    _status(response, created)
    return {"created": created, "application": application}


@router.post("/applications/quick-add", dependencies=_MUTATION)
def quick_add(body: QuickAdd, response: Response, key: str = Key, owner: str = Owner):
    with _errors():
        application, match, created = service.quick_add(owner, key, **body.model_dump())
    _status(response, created)                # a preview stores nothing and answers 200
    return {"created": created, "match": match, "application": application}


@router.get("/applications/{application_id}")
def get_application(application_id: str, owner: str = Owner):
    with _errors():
        return service.get_application(owner, application_id)


@router.patch("/applications/{application_id}", dependencies=_MUTATION)
def update_application(application_id: str, body: ApplicationPatch, owner: str = Owner):
    with _errors():
        return {"application": service.update_application(owner, application_id, body.model_dump(exclude_unset=True))}


@router.post("/applications/{application_id}/events", dependencies=_MUTATION)
def append_event(application_id: str, body: EventCreate, response: Response, key: str = Key, owner: str = Owner):
    with _errors():
        event, application, replayed = service.append_event(owner, application_id, key, body.event_type,
                                                            occurred_at=body.occurred_at, note=body.note)
    _status(response, not replayed)
    return {"replayed": replayed, "event": event, "application": application}


@router.post("/applications/{application_id}/events/{event_id}/undo", dependencies=_MUTATION)
def undo_event(application_id: str, event_id: int, response: Response, key: str = Key, owner: str = Owner):
    with _errors():
        event, application, replayed = service.undo_event(owner, application_id, event_id, key)
    _status(response, not replayed)
    return {"replayed": replayed, "event": event, "application": application}


# ---------- CV versions, companies, contacts ----------

@router.get("/cv-versions")
def list_cv_versions(owner: str = Owner):
    return {"cv_versions": service.list_cv_versions(owner)}


@router.post("/cv-versions", dependencies=_MUTATION)
def add_cv_version(body: CvVersionCreate, response: Response, owner: str = Owner):
    with _errors():
        version, created = service.add_cv_version(owner, **body.model_dump())
    _status(response, created)
    return {"created": created, "cv_version": version}


@router.get("/companies")
def list_companies(owner: str = Owner):
    return {"companies": service.list_companies(owner)}


@router.post("/companies", dependencies=_MUTATION)
def add_company(body: CompanyCreate, response: Response, owner: str = Owner):
    with _errors():
        company, created = service.add_company(owner, **body.model_dump())
    _status(response, created)
    return {"created": created, "company": company}


@router.patch("/companies/{company_id}", dependencies=_MUTATION)
def update_company(company_id: str, body: CompanyPatch, owner: str = Owner):
    with _errors():
        return {"company": service.update_company(owner, company_id, body.model_dump(exclude_unset=True))}


@router.get("/contacts")
def list_contacts(company_id: str | None = None, owner: str = Owner):
    return {"contacts": service.list_contacts(owner, company_id)}


@router.post("/contacts", dependencies=_MUTATION)
def add_contact(body: ContactCreate, response: Response, owner: str = Owner):
    with _errors():
        contact, created = service.add_contact(owner, **body.model_dump())
    _status(response, created)
    return {"created": created, "contact": contact}


@router.patch("/contacts/{contact_id}", dependencies=_MUTATION)
def update_contact(contact_id: str, body: ContactPatch, owner: str = Owner):
    with _errors():
        return {"contact": service.update_contact(owner, contact_id, body.model_dump(exclude_unset=True))}
