"""What the tracker API does to data/tracker.sqlite3 (docs/TRACKER_PLAN.md sections 3 and 4, item TRK2).

Every function takes the owner and filters on it: another user's record is "not found", never "forbidden". A status
changes only by appending an event; going back is an `undone` event for the latest one. A mutation carries the
caller's Idempotency-Key, stored as the event's `request_id`, so a retry records nothing twice.
"""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.schemas import JobPosting
from app.services.job_identity import company_key
from app.tracker import matching, store
from app.tracker.importers import STATUS_EVENT, company_id_for, snapshot_of
from app.tracker.models import STATUSES, Application, ApplicationEvent, Company, Contact, CvVersion

# docs/TRACKER_PLAN.md section 3.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "SAVED": ("APPLIED", "SKIPPED"),
    "APPLIED": ("ONLINE_TEST", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"),
    "ONLINE_TEST": ("INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"),
    "INTERVIEW": ("INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"),     # another round is an event, not a change
    "OFFER": ("WITHDRAWN", "REJECTED"),
    "NO_RESPONSE": ("ONLINE_TEST", "INTERVIEW", "OFFER", "REJECTED"),
    "REJECTED": (), "WITHDRAWN": (), "SKIPPED": (),
}
EVENT_STATUS = {event: status for status, event in STATUS_EVENT.items()}
NEUTRAL_EVENTS = ("note", "recruiter_reply")            # recorded in any status; the status stays
_AFTER_APPLYING = ("online_test", "interview", "offer", "rejected", "withdrawn", "no_response_confirmed", "recruiter_reply")
CREATE_STATUSES = ("SAVED", "APPLIED")
_PASTED = ("title", "company", "location", "application_url", "description", "source")


class TrackerError(Exception):
    status_code = 400

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.extra = extra


class NotFound(TrackerError):
    status_code = 404


class Conflict(TrackerError):
    status_code = 409


class Invalid(TrackerError):
    status_code = 422


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _day(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise Invalid(f"{field} must be a date like 2026-10-03.") from exc


def _when(value: str | None) -> tuple[str, bool]:
    """(occurred_at, exact). A bare date is kept as given and marked as not exact; the future is refused."""
    if not value:
        return _now(), True
    try:
        day = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise Invalid("occurred_at must be a date (2026-10-03) or an ISO 8601 date and time.") from exc
    if day > datetime.now(timezone.utc).date() + timedelta(days=1):
        raise Invalid("occurred_at is in the future.")
    return value, len(value) != 10


# ---------- reading ----------

def _application(session: Session, owner: str, application_id: str) -> Application:
    found = session.scalar(select(Application).where(Application.id == application_id, Application.owner_id == owner))
    if found is None:
        raise NotFound("No application with that id.")
    return found


def _events(session: Session, application: Application) -> list[ApplicationEvent]:
    return list(session.scalars(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id, ApplicationEvent.owner_id == application.owner_id).order_by(ApplicationEvent.id)))


def _live(events: list[ApplicationEvent]) -> list[ApplicationEvent]:
    undone = {event.undoes_event_id for event in events if event.event_type == "undone"}
    return [event for event in events if event.event_type != "undone" and event.id not in undone]


def _event(event: ApplicationEvent, undone: bool = False) -> dict:
    return {"id": event.id, "event_type": event.event_type, "occurred_at": event.occurred_at, "occurred_at_exact": bool(event.occurred_at_exact),
            "recorded_at": event.recorded_at, "source": event.source, "request_id": event.request_id,
            "undoes_event_id": event.undoes_event_id, "note": event.note, "undone": undone}


def _summary(application: Application) -> dict:
    return {name: getattr(application, name) for name in (
        "id", "title", "company_name", "company_id", "job_id", "url", "source", "channel", "status", "applied_at", "cv_version_id",
        "next_follow_up_at", "notes", "created_at", "updated_at")}


def _detail(session: Session, application: Application) -> dict:
    events = _events(session, application)
    undone = {event.undoes_event_id for event in events if event.event_type == "undone"}
    return {**_summary(application), "allowed_next": list(TRANSITIONS[application.status]),
            "snapshot": application.snapshot_json, "snapshot_sha256": application.snapshot_sha256,
            "snapshot_captured_at": application.snapshot_captured_at,
            "timeline": [_event(event, event.id in undone) for event in events]}


def list_applications(owner: str, *, status: str | None = None, company: str | None = None, q: str | None = None) -> list[dict]:
    query = select(Application).where(Application.owner_id == owner)
    if status:
        if status not in STATUSES:
            raise Invalid(f"status must be one of {', '.join(STATUSES)}.")
        query = query.where(Application.status == status)
    if q and q.strip():
        text = q.strip().lower()
        query = query.where(or_(*(func.lower(column).contains(text, autoescape=True)
                                  for column in (Application.title, Application.company_name, Application.notes))))
    with store.session() as session:
        rows = list(session.scalars(query.order_by(Application.updated_at.desc(), Application.id)))
        if company and company.strip():
            wanted = company_key(company)
            rows = [row for row in rows if company_key(row.company_name) == wanted or company.strip().casefold() in row.company_name.casefold()]
        latest: dict[str, dict | None] = {}
        events: dict[str, list[ApplicationEvent]] = {}
        for event in session.scalars(select(ApplicationEvent).where(ApplicationEvent.owner_id == owner).order_by(ApplicationEvent.id)):
            events.setdefault(event.application_id, []).append(event)
        for application_id, log in events.items():
            live = _live(log)
            undoable = live[-1] if live and live[-1].id != log[0].id else None
            latest[application_id] = {"id": undoable.id, "event_type": undoable.event_type} if undoable else None
        # latest_event: the one event Undo would take back, or None when only the first event is left.
        return [{**_summary(row), "latest_event": latest.get(row.id)} for row in rows]


def get_application(owner: str, application_id: str) -> dict:
    with store.session() as session:
        return _detail(session, _application(session, owner, application_id))


# ---------- applications ----------

def _by_request(session: Session, owner: str, request_id: str) -> ApplicationEvent | None:
    return session.scalar(select(ApplicationEvent).where(ApplicationEvent.owner_id == owner, ApplicationEvent.request_id == request_id))


def _cv_version(session: Session, owner: str, version_id: str | None) -> str | None:
    if version_id is not None and session.scalar(select(CvVersion.id).where(CvVersion.id == version_id, CvVersion.owner_id == owner)) is None:
        raise Invalid("No CV version with that id.")
    return version_id


def _same_application(session: Session, owner: str, identity: str, url: str, job_id: str | None) -> Application | None:
    """An application for this job that the owner already has: same identity, same index job or same normalised URL."""
    for row in session.scalars(select(Application).where(Application.owner_id == owner)):
        if row.identity == identity or (job_id and row.job_id == job_id) or (url and matching.normalise_url(row.url) == url):
            return row
    return None


def index_job(job_id: str) -> JobPosting:
    for key, job in matching.index_jobs():
        if key == job_id:
            return job
    raise NotFound("No index job with that id.")


def create_application(owner: str, request_id: str, *, job_id: str | None = None, job: JobPosting | None = None,
                       title: str = "", company: str = "", url: str | None = None, location: str | None = None,
                       description: str | None = None, source: str | None = None, channel: str | None = None,
                       notes: str = "", cv_version_id: str | None = None, status: str = "SAVED",
                       occurred_at: str | None = None, stored_job_id: str | None = None) -> tuple[dict, bool]:
    """(application, created). From an index job (its post is the snapshot) or from the fields the user gave.

    `stored_job_id` is the dashboard's id for a job card (a stored search result, not an index job): it is kept in
    `job_id` so the card and the application find each other, and nothing is looked up with it.
    """
    if status not in CREATE_STATUSES:
        raise Invalid(f"status must be one of {', '.join(CREATE_STATUSES)}.")
    reference = job_id or stored_job_id
    if reference is not None and job is None:
        with store.session() as session:         # already tracked: answered even after the job has left the index
            tracked = session.scalar(select(Application).where(Application.owner_id == owner, Application.job_id == reference))
            if tracked is not None:
                return _detail(session, tracked), False
    if job_id is not None and job is None:
        job = index_job(job_id)
    if job is not None:
        post = job.model_dump(mode="json")
        title, company, url, source = job.title, job.company, post.get("application_url"), source or job.source
    else:
        title, company = title.strip(), company.strip()
        if not title or not company_key(company):
            raise Invalid("Give an index job_id, or a title and a company.")
        if url is not None and not matching.normalise_url(url):
            raise Invalid("url must be an http or https address.")
        post = dict(zip(_PASTED, (title, company, location, url, description, source)))
    normalised = matching.normalise_url(url)
    identity = f"url:{normalised}" if normalised else f"fields:{company_key(company)}|{title.casefold()}"
    when, exact = _when(occurred_at)
    with store.session() as session:
        prior = _by_request(session, owner, request_id)
        if prior is not None:
            if prior.event_type not in (STATUS_EVENT[name] for name in CREATE_STATUSES) or prior.id != _events(
                    session, _application(session, owner, prior.application_id))[0].id:
                raise Conflict("This Idempotency-Key was already used for another request.")
            return _detail(session, _application(session, owner, prior.application_id)), False
        existing = _same_application(session, owner, identity, normalised, reference)
        if existing is not None:
            return _detail(session, existing), False
        _cv_version(session, owner, cv_version_id)
        kept, digest = snapshot_of(post)
        stamp = _now()
        application = Application(
            id=str(uuid4()), owner_id=owner, company_id=company_id_for(session, owner, company, {}), job_id=reference, identity=identity,
            title=title, company_name=company, url=url, source=source, channel=channel, status=status,
            applied_at=when if status == "APPLIED" else None, cv_version_id=cv_version_id, notes=notes, created_at=stamp,
            updated_at=stamp, snapshot_json=kept, snapshot_sha256=digest, snapshot_captured_at=stamp)
        session.add(application)
        session.flush()
        session.add(ApplicationEvent(application_id=application.id, owner_id=owner, event_type=STATUS_EVENT[status], occurred_at=when,
                                     occurred_at_exact=exact, recorded_at=stamp, source="user", request_id=request_id))
        session.commit()
        return _detail(session, application), True


def quick_add(owner: str, request_id: str, *, url: str, title: str = "", company: str = "", location: str | None = None,
              confirm_job_id: str | None = None, preview: bool = False, **fields) -> tuple[dict | None, dict, bool]:
    """(application, match, created). A matched URL becomes an application for the index job; a probable match is
    reported and linked only when `confirm_job_id` names one of its candidates; otherwise what was given is stored."""
    if not matching.normalise_url(url):
        raise Invalid("url must be an http or https address.")
    index = matching.index_jobs()
    found = matching.match(url, title=title, company=company, location=location or "", index=index)
    if preview:                          # the page shows the match first; nothing is stored and the key is not used
        return None, found, False
    job_id = found["job_id"]
    if confirm_job_id is not None:
        if confirm_job_id not in [candidate["job_id"] for candidate in found["candidates"]]:
            raise Invalid("confirm_job_id is not one of the probable matches for this job.", match=found)
        job_id = confirm_job_id
    if job_id is not None:
        application, created = create_application(owner, request_id, job_id=job_id, job=dict(index)[job_id], **fields)
    elif not title.strip() or not company_key(company):
        raise Invalid("This URL is not in the index. Give the title and the company to save it.", match=found)
    else:
        application, created = create_application(owner, request_id, title=title, company=company, url=url, location=location, **fields)
    return application, found, created


def update_application(owner: str, application_id: str, changes: dict) -> dict:
    with store.session() as session:
        application = _application(session, owner, application_id)
        if "notes" in changes:
            application.notes = changes["notes"] or ""
        if "next_follow_up_at" in changes:
            application.next_follow_up_at = _day(changes["next_follow_up_at"], "next_follow_up_at")
        if "cv_version_id" in changes:
            application.cv_version_id = _cv_version(session, owner, changes["cv_version_id"])
        application.updated_at = _now()
        session.commit()
        return _detail(session, application)


# ---------- events ----------

def append_event(owner: str, application_id: str, request_id: str, event_type: str, *, occurred_at: str | None = None,
                 note: str = "") -> tuple[dict, dict, bool]:
    """(event, application, replayed)."""
    if event_type not in EVENT_STATUS and event_type not in NEUTRAL_EVENTS:
        raise Invalid(f"event_type must be one of {', '.join([*EVENT_STATUS, *NEUTRAL_EVENTS])}.")
    when, exact = _when(occurred_at)
    with store.session() as session:
        application = _application(session, owner, application_id)
        prior = _by_request(session, owner, request_id)
        if prior is not None:
            if prior.application_id != application.id or prior.event_type != event_type:
                raise Conflict("This Idempotency-Key was already used for another request.")
            return _event(prior), _detail(session, application), True
        target = EVENT_STATUS.get(event_type)
        if target is not None and target not in TRANSITIONS[application.status]:
            allowed = list(TRANSITIONS[application.status])
            raise Conflict(f"{application.status} cannot become {target}." + (f" Allowed next: {', '.join(allowed)}." if allowed else
                                                                              " Nothing follows this status; undo the latest event to go back."),
                           status=application.status, allowed=allowed, allowed_events=[STATUS_EVENT[name] for name in allowed])
        if event_type in _AFTER_APPLYING and application.applied_at and when[:10] < application.applied_at[:10]:
            raise Invalid(f"occurred_at is before the application date ({application.applied_at[:10]}).")
        stamp = _now()
        event = ApplicationEvent(application_id=application.id, owner_id=owner, event_type=event_type, occurred_at=when,
                                 occurred_at_exact=exact, recorded_at=stamp, source="user", request_id=request_id, note=note)
        session.add(event)
        if target is not None:
            application.status = target
            if target == "APPLIED":
                application.applied_at = when
        application.updated_at = stamp
        session.commit()
        return _event(event), _detail(session, application), False


def undo_event(owner: str, application_id: str, event_id: int, request_id: str) -> tuple[dict, dict, bool]:
    """(the `undone` event, application, replayed). Only the latest event that still counts can be undone."""
    with store.session() as session:
        application = _application(session, owner, application_id)
        prior = _by_request(session, owner, request_id)
        if prior is not None:
            if prior.application_id != application.id or prior.event_type != "undone" or prior.undoes_event_id != event_id:
                raise Conflict("This Idempotency-Key was already used for another request.")
            return _event(prior), _detail(session, application), True
        events = _events(session, application)
        if event_id not in [event.id for event in events]:
            raise NotFound("No such event on this application.")
        live = _live(events)
        latest = live[-1]
        if latest.id != event_id:
            raise Conflict("Only the latest event can be undone.", latest_event_id=latest.id)
        if latest.id == events[0].id:
            raise Conflict("The first event of an application cannot be undone.", latest_event_id=latest.id)
        stamp = _now()
        event = ApplicationEvent(application_id=application.id, owner_id=owner, event_type="undone", occurred_at=stamp, occurred_at_exact=True,
                                 recorded_at=stamp, source="user", request_id=request_id, undoes_event_id=event_id)
        session.add(event)
        remaining = [item for item in live[:-1] if item.event_type in EVENT_STATUS]
        if remaining:                    # an imported log may start with a non-status event; the status then stays
            application.status = EVENT_STATUS[remaining[-1].event_type]
        if latest.event_type == "applied":
            application.applied_at = None
        application.updated_at = stamp
        session.commit()
        return _event(event), _detail(session, application), False


# ---------- CV versions, companies, contacts ----------

def _cv(row: CvVersion) -> dict:
    return {"id": row.id, "label": row.label, "created_at": row.created_at, "file_sha256": row.file_sha256, "notes": row.notes}


def list_cv_versions(owner: str) -> list[dict]:
    with store.session() as session:
        return [_cv(row) for row in session.scalars(select(CvVersion).where(CvVersion.owner_id == owner).order_by(CvVersion.created_at, CvVersion.label))]


def add_cv_version(owner: str, label: str, file_sha256: str | None = None, notes: str = "") -> tuple[dict, bool]:
    """A label and the file's hash only: the CV file and its text never come here."""
    label = label.strip()
    if not label:
        raise Invalid("label is empty.")
    with store.session() as session:
        found = session.scalar(select(CvVersion).where(CvVersion.owner_id == owner, CvVersion.label == label))
        if found is not None:
            if file_sha256 and found.file_sha256 and file_sha256 != found.file_sha256:
                raise Conflict("A CV version with this label has a different file hash. Use a new label.")
            return _cv(found), False
        row = CvVersion(id=str(uuid4()), owner_id=owner, label=label, created_at=_now(), file_sha256=file_sha256, notes=notes)
        session.add(row)
        session.commit()
        return _cv(row), True


def _company(row: Company) -> dict:
    return {name: getattr(row, name) for name in ("id", "name", "seed_company", "ats", "careers_url", "reapply_after", "notes", "created_at")}


def list_companies(owner: str) -> list[dict]:
    with store.session() as session:
        return [_company(row) for row in session.scalars(select(Company).where(Company.owner_id == owner).order_by(Company.name))]


def _owned_company(session: Session, owner: str, company_id: str, missing: type[TrackerError]) -> Company:
    found = session.scalar(select(Company).where(Company.id == company_id, Company.owner_id == owner))
    if found is None:
        raise missing("No company with that id.")
    return found


def _apply(row, changes: dict) -> None:
    for name, value in changes.items():
        if name == "reapply_after":
            value = _day(value, name)
        elif name in ("notes", "name"):
            value = (value or "").strip() if name == "name" else value or ""
            if name == "name" and not value:
                raise Invalid("name is empty.")
        setattr(row, name, value)


def add_company(owner: str, name: str, **fields) -> tuple[dict, bool]:
    key = company_key(name)
    if not key:
        raise Invalid("name is empty.")
    with store.session() as session:
        found = session.scalar(select(Company).where(Company.owner_id == owner, Company.company_key == key))
        if found is not None:
            return _company(found), False
        row = Company(id=str(uuid4()), owner_id=owner, name=name.strip(), company_key=key, created_at=_now())
        _apply(row, fields)
        session.add(row)
        session.commit()
        return _company(row), True


def update_company(owner: str, company_id: str, changes: dict) -> dict:
    with store.session() as session:
        row = _owned_company(session, owner, company_id, NotFound)
        _apply(row, changes)
        session.commit()
        return _company(row)


def _contact(row: Contact) -> dict:
    return {name: getattr(row, name) for name in ("id", "company_id", "name", "role", "channel", "referral_status", "notes", "created_at")}


def list_contacts(owner: str, company_id: str | None = None) -> list[dict]:
    query = select(Contact).where(Contact.owner_id == owner)
    if company_id:
        query = query.where(Contact.company_id == company_id)
    with store.session() as session:
        return [_contact(row) for row in session.scalars(query.order_by(Contact.name))]


def add_contact(owner: str, name: str, company_id: str | None = None, **fields) -> tuple[dict, bool]:
    name = name.strip()
    if not name:
        raise Invalid("name is empty.")
    with store.session() as session:
        if company_id is not None:
            _owned_company(session, owner, company_id, Invalid)
        for found in session.scalars(select(Contact).where(Contact.owner_id == owner, Contact.company_id == company_id)):
            if found.name.casefold() == name.casefold():
                return _contact(found), False
        row = Contact(id=str(uuid4()), owner_id=owner, company_id=company_id, name=name, created_at=_now())
        _apply(row, fields)
        session.add(row)
        session.commit()
        return _contact(row), True


def update_contact(owner: str, contact_id: str, changes: dict) -> dict:
    with store.session() as session:
        row = session.scalar(select(Contact).where(Contact.id == contact_id, Contact.owner_id == owner))
        if row is None:
            raise NotFound("No contact with that id.")
        if changes.get("company_id") is not None:
            _owned_company(session, owner, changes["company_id"], Invalid)
        _apply(row, changes)
        session.commit()
        return _contact(row)
