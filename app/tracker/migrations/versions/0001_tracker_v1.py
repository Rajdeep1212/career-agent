"""Tracker v1: users, companies, contacts, cv_versions, applications, application_events, reminders (TRK1).

`application_events` is append-only: UPDATE and DELETE are refused by triggers, written per dialect. One fixed
local user is created for single-user mode.

Rollback: `store.downgrade()` drops these tables. Local data lives only in data/tracker.sqlite3; copy that file first.
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0001_tracker_v1"
down_revision = None
branch_labels = None
depends_on = None

ROLES = ("student", "mentor", "admin")
STATUSES = ("SAVED", "APPLIED", "ONLINE_TEST", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE", "SKIPPED")
EVENT_TYPES = ("saved", "applied", "online_test", "interview", "offer", "rejected", "withdrawn", "skipped",
               "recruiter_reply", "no_response_confirmed", "outreach_prepared", "outreach_sent",
               "thumbs_up", "thumbs_down", "thumbs_cleared", "removed_from_results", "undone", "note", "follow_up_sent")
EVENT_SOURCES = ("user", "derived", "import", "email_suggestion")
REMINDER_KINDS = ("nudge_7", "nudge_14", "no_response_30", "custom")
REMINDER_STATES = ("pending", "sent", "dismissed", "done")
LOCAL_USER_ID, LOCAL_USER_EMAIL = "local", "local@localhost"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _owner() -> sa.Column:
    return sa.Column("owner_id", sa.Text, sa.ForeignKey("users.id"), nullable=False)


def upgrade() -> None:
    users = op.create_table(
        "users",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("display_name", sa.Text),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("google_sub", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("disabled_at", sa.Text),
        sa.CheckConstraint(_one_of("role", ROLES), name="users_role"))
    op.create_table(
        "companies",
        sa.Column("id", sa.Text, primary_key=True), _owner(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("company_key", sa.Text, nullable=False),
        sa.Column("seed_company", sa.Text),
        sa.Column("ats", sa.Text),
        sa.Column("careers_url", sa.Text),
        sa.Column("reapply_after", sa.Text),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.UniqueConstraint("owner_id", "company_key", name="companies_owner_key"))
    op.create_table(
        "contacts",
        sa.Column("id", sa.Text, primary_key=True), _owner(),
        sa.Column("company_id", sa.Text, sa.ForeignKey("companies.id")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("role", sa.Text),
        sa.Column("channel", sa.Text),
        sa.Column("referral_status", sa.Text),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.Text, nullable=False))
    op.create_table(
        "cv_versions",
        sa.Column("id", sa.Text, primary_key=True), _owner(),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("file_sha256", sa.Text),
        sa.Column("skills_json", sa.JSON),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.UniqueConstraint("owner_id", "label", name="cv_versions_owner_label"))
    op.create_table(
        "applications",
        sa.Column("id", sa.Text, primary_key=True), _owner(),
        sa.Column("company_id", sa.Text, sa.ForeignKey("companies.id")),
        sa.Column("job_id", sa.Text),
        sa.Column("identity", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("company_name", sa.Text, nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("source", sa.Text),
        sa.Column("channel", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("applied_at", sa.Text),
        sa.Column("cv_version_id", sa.Text, sa.ForeignKey("cv_versions.id")),
        sa.Column("next_follow_up_at", sa.Text),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.Column("snapshot_json", sa.JSON),
        sa.Column("snapshot_sha256", sa.Text),
        sa.Column("snapshot_captured_at", sa.Text),
        sa.UniqueConstraint("owner_id", "identity", name="applications_owner_identity"),
        sa.CheckConstraint(_one_of("status", STATUSES), name="applications_status"))
    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("application_id", sa.Text, sa.ForeignKey("applications.id"), nullable=False), _owner(),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.Text, nullable=False),
        sa.Column("occurred_at_exact", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("recorded_at", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("request_id", sa.Text),
        sa.Column("undoes_event_id", sa.Integer, sa.ForeignKey("application_events.id")),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("context_json", sa.JSON),
        sa.UniqueConstraint("owner_id", "request_id", name="application_events_owner_request"),
        sa.CheckConstraint(_one_of("event_type", EVENT_TYPES), name="application_events_type"),
        sa.CheckConstraint(_one_of("source", EVENT_SOURCES), name="application_events_source"))
    op.create_index("application_events_application", "application_events", ["application_id", "id"])
    op.create_table(
        "reminders",
        sa.Column("id", sa.Text, primary_key=True), _owner(),
        sa.Column("application_id", sa.Text, sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("due_at", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.Text),
        sa.Column("dedupe_key", sa.Text, nullable=False, unique=True),
        sa.CheckConstraint(_one_of("kind", REMINDER_KINDS), name="reminders_kind"),
        sa.CheckConstraint(_one_of("state", REMINDER_STATES), name="reminders_state"))

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f"""CREATE TRIGGER application_events_no_{action.lower()} BEFORE {action} ON application_events
                           BEGIN SELECT RAISE(ABORT, 'application_events is append-only'); END""")
    elif dialect == "postgresql":
        op.execute("""CREATE FUNCTION application_events_append_only() RETURNS trigger AS $$
                      BEGIN RAISE EXCEPTION 'application_events is append-only'; END; $$ LANGUAGE plpgsql""")
        op.execute("""CREATE TRIGGER application_events_no_change BEFORE UPDATE OR DELETE ON application_events
                      FOR EACH ROW EXECUTE FUNCTION application_events_append_only()""")

    op.bulk_insert(users, [{"id": LOCAL_USER_ID, "email": LOCAL_USER_EMAIL, "display_name": "Local user", "role": "student",
                            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])


def downgrade() -> None:
    for table in ("reminders", "application_events", "applications", "cv_versions", "contacts", "companies", "users"):
        op.drop_table(table)            # dropping application_events drops its SQLite triggers with it
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS application_events_append_only()")
