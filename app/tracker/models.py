"""SQLAlchemy models for the tracker (docs/TRACKER_PLAN.md section 2, the tables TRK1 needs).

One schema for SQLite (local, tests) and Postgres (deployment). Types are kept portable: ids and timestamps are
text (ISO 8601, UTC), as in the M2 tracker. Every table except `users` carries a required `owner_id`.
`application_events` is append-only; the triggers that enforce it are in the migration, per dialect.
"""
from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("student", "mentor", "admin")
STATUSES = ("SAVED", "APPLIED", "ONLINE_TEST", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE", "SKIPPED")
EVENT_TYPES = ("saved", "applied", "online_test", "interview", "offer", "rejected", "withdrawn", "skipped",
               "recruiter_reply", "no_response_confirmed", "outreach_prepared", "outreach_sent",
               "thumbs_up", "thumbs_down", "thumbs_cleared", "removed_from_results", "undone", "note", "follow_up_sent")
EVENT_SOURCES = ("user", "derived", "import", "email_suggestion")
REMINDER_KINDS = ("nudge_7", "nudge_14", "no_response_30", "custom")
REMINDER_STATES = ("pending", "sent", "dismissed", "done")


def one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(one_of("role", ROLES), name="users_role"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    google_sub: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    disabled_at: Mapped[str | None] = mapped_column(Text)


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("owner_id", "company_key", name="companies_owner_key"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)
    company_key: Mapped[str] = mapped_column(Text)
    seed_company: Mapped[str | None] = mapped_column(Text)
    ats: Mapped[str | None] = mapped_column(Text)
    careers_url: Mapped[str | None] = mapped_column(Text)
    reapply_after: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(Text)


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    referral_status: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(Text)


class CvVersion(Base):
    """Label, date, file hash and the local parser's skill list only; never the CV file or its text."""
    __tablename__ = "cv_versions"
    __table_args__ = (UniqueConstraint("owner_id", "label", name="cv_versions_owner_label"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str | None] = mapped_column(Text)
    skills_json: Mapped[list | None] = mapped_column(JSON)
    notes: Mapped[str] = mapped_column(Text, default="")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("owner_id", "identity", name="applications_owner_identity"),
                      CheckConstraint(one_of("status", STATUSES), name="applications_status"))
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    job_id: Mapped[str | None] = mapped_column(Text)              # the index job, when the application came from one
    identity: Mapped[str] = mapped_column(Text)                   # what makes it the same application on a re-import
    title: Mapped[str] = mapped_column(Text)
    company_name: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)                     # a cache of the event log
    applied_at: Mapped[str | None] = mapped_column(Text)
    cv_version_id: Mapped[str | None] = mapped_column(ForeignKey("cv_versions.id"))
    next_follow_up_at: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
    snapshot_json: Mapped[dict | None] = mapped_column(JSON)      # the job post as it was: survives the posting being removed
    snapshot_sha256: Mapped[str | None] = mapped_column(Text)
    snapshot_captured_at: Mapped[str | None] = mapped_column(Text)


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (UniqueConstraint("owner_id", "request_id", name="application_events_owner_request"),
                      CheckConstraint(one_of("event_type", EVENT_TYPES), name="application_events_type"),
                      CheckConstraint(one_of("source", EVENT_SOURCES), name="application_events_source"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[str] = mapped_column(Text)
    occurred_at_exact: Mapped[bool] = mapped_column(Boolean, default=True)
    recorded_at: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    undoes_event_id: Mapped[int | None] = mapped_column(ForeignKey("application_events.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    context_json: Mapped[dict | None] = mapped_column(JSON)


class Reminder(Base):
    """Table only in TRK1; the jobs that fill it come with TRK5."""
    __tablename__ = "reminders"
    __table_args__ = (CheckConstraint(one_of("kind", REMINDER_KINDS), name="reminders_kind"),
                      CheckConstraint(one_of("state", REMINDER_STATES), name="reminders_state"))
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    kind: Mapped[str] = mapped_column(Text)
    due_at: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, default="pending")
    sent_at: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
