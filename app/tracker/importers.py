"""Imports into the tracker: the M2 tracker in data/agent.sqlite3, and a CSV of applications logged by hand.

Both are idempotent and re-runnable: an application is recognised by its `identity` and an event by its
`request_id`, so a second run adds nothing, and a later run picks up what was recorded in between.

The old database is opened read-only and is never changed; a backup copy is made before a real import.
"""
import csv
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from app.services.job_identity import company_key, title_key
from app.tracker import store
from app.tracker.models import STATUSES, Application, ApplicationEvent, Company, CvVersion

CSV_COLUMNS = ["company", "role", "url", "source", "channel", "applied_on", "cv_version", "status", "status_date",
               "next_follow_up", "notes"]
# What a job post is, as kept in an application's snapshot. The stored M2 match is left out: it quotes the CV.
SNAPSHOT_FIELDS = ("title", "company", "location", "application_url", "description", "posted_date", "source",
                   "employment_type", "work_mode", "eligibility_status", "eligibility_summary")
STATUS_EVENT = {"SAVED": "saved", "APPLIED": "applied", "ONLINE_TEST": "online_test", "INTERVIEW": "interview", "OFFER": "offer",
                "REJECTED": "rejected", "WITHDRAWN": "withdrawn", "NO_RESPONSE": "no_response_confirmed", "SKIPPED": "skipped"}
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def open_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _company(session, owner: str, name: str, cache: dict[str, str]) -> str | None:
    key = company_key(name)
    if not key:
        return None
    if key not in cache:
        found = session.scalar(select(Company.id).where(Company.owner_id == owner, Company.company_key == key))
        if found is None:
            found = str(uuid4())
            session.add(Company(id=found, owner_id=owner, name=name.strip(), company_key=key, created_at=_now()))
            session.flush()         # written before the application that points at it
        cache[key] = found
    return cache[key]


def _snapshot(job: dict) -> tuple[dict, str]:
    kept = {name: job.get(name) for name in SNAPSHOT_FIELDS if job.get(name) not in (None, "")}
    return kept, _sha(json.dumps(kept, sort_keys=True, ensure_ascii=False))


def import_m2(agent_db: Path, url: str | None = None, *, backup_root: Path, dry_run: bool = False,
              owner: str = store.LOCAL_USER_ID) -> dict:
    """Copy the M2 tracker's applications, their events and the job post they refer to into the tracker."""
    report: dict = {"m2_applications": 0, "m2_events": 0, "applications_added": 0, "events_added": 0, "applications_in_tracker": 0,
                    "events_in_tracker": 0, "backup": None, "message": ""}
    agent_db = Path(agent_db)
    if not agent_db.exists():
        report["message"] = f"Nothing to import: {agent_db.name} does not exist."
        return report
    with closing(open_read_only(agent_db)) as old:
        tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "career_applications" not in tables:
            report["message"] = f"Nothing to import: {agent_db.name} has no M2 tracker tables."
            return report
        applications = [dict(row) for row in old.execute(
            "SELECT a.*, j.job_json FROM career_applications a JOIN career_jobs j ON j.id = a.job_id ORDER BY a.created_at, a.id")]
        events = ([dict(row) for row in old.execute("SELECT * FROM job_events WHERE application_id IS NOT NULL ORDER BY id")]
                  if "job_events" in tables else [])
        snapshots = ({row["id"]: dict(row) for row in old.execute("SELECT id, job_json, captured_at FROM job_snapshots")}
                     if "job_snapshots" in tables else {})
        if not dry_run and applications:
            target = Path(backup_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") / agent_db.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(target)) as copy:
                old.backup(copy)                    # a consistent copy, read through the read-only connection
            report["backup"] = str(target)
    known = {row["id"] for row in applications}
    events = [event for event in events if event["application_id"] in known]
    report.update(m2_applications=len(applications), m2_events=len(events))
    if not applications:
        report["message"] = f"Nothing to import: {agent_db.name} holds no applications."
        return report

    with store.session(url) as session:
        present = {identity: key for identity, key in session.execute(
            select(Application.identity, Application.id).where(Application.owner_id == owner, Application.identity.like("m2:%")))}
        event_ids = {request: key for request, key in session.execute(
            select(ApplicationEvent.request_id, ApplicationEvent.id).where(ApplicationEvent.owner_id == owner,
                                                                           ApplicationEvent.request_id.like("m2:event:%")))}
        report["applications_added"] = len([row for row in applications if f"m2:{row['job_id']}" not in present])
        report["events_added"] = len([event for event in events if f"m2:event:{event['id']}" not in event_ids])
        if dry_run:
            report.update(applications_in_tracker=len(present), events_in_tracker=len(event_ids),
                          message="Dry run: nothing was written and no backup was made.")
            return report
        companies: dict[str, str] = {}
        for row in applications:
            job = json.loads(row["job_json"])
            referenced = [event["snapshot_id"] for event in events if event["application_id"] == row["id"] and event.get("snapshot_id")]
            source = snapshots.get(referenced[-1]) if referenced else None
            kept, digest = _snapshot(json.loads(source["job_json"]) if source else job)
            fields = dict(status=row["status"], applied_at=row.get("applied_at"), channel=row.get("applied_via"),
                          notes=row.get("notes") or "", next_follow_up_at=row.get("follow_up_at"), updated_at=row["updated_at"])
            identity = f"m2:{row['job_id']}"
            if identity in present:
                existing = session.get(Application, present[identity])
                for name, value in fields.items():      # the old tables are still the live ones until TRK2: follow them
                    setattr(existing, name, value)
                continue
            session.add(Application(
                id=row["id"], owner_id=owner, company_id=_company(session, owner, str(job.get("company") or ""), companies),
                job_id=row["job_id"], identity=identity, title=str(job.get("title") or ""), company_name=str(job.get("company") or ""),
                url=job.get("application_url"), source=job.get("source"), created_at=row["created_at"], snapshot_json=kept,
                snapshot_sha256=digest, snapshot_captured_at=(source or {}).get("captured_at") or row["created_at"], **fields))
        session.flush()
        for event in events:
            request = f"m2:event:{event['id']}"
            if request in event_ids:
                continue
            undone = event.get("undoes_event_id")
            created = ApplicationEvent(
                application_id=event["application_id"], owner_id=owner, event_type=event["event_type"], occurred_at=event["occurred_at"],
                occurred_at_exact=bool(event.get("occurred_at_exact", 1)), recorded_at=event["recorded_at"], source="import",
                request_id=request, undoes_event_id=event_ids.get(f"m2:event:{undone}") if undone else None, note=event.get("note") or "",
                context_json={"m2_source": event.get("source"), "m2_request_id": event.get("request_id"),
                              "m2_snapshot_id": event.get("snapshot_id")})
            session.add(created)
            session.flush()
            event_ids[request] = created.id
        session.commit()
        report["applications_in_tracker"] = session.scalar(select(func.count()).select_from(Application).where(
            Application.owner_id == owner, Application.identity.like("m2:%")))
        report["events_in_tracker"] = session.scalar(select(func.count()).select_from(ApplicationEvent).where(
            ApplicationEvent.owner_id == owner, ApplicationEvent.request_id.like("m2:event:%")))
    if (report["applications_in_tracker"], report["events_in_tracker"]) != (report["m2_applications"], report["m2_events"]):
        raise RuntimeError(f"The import does not add up: {report}")
    report["message"] = "Imported; the counts match and the old tables were not changed."
    return report


def write_template(path: Path) -> None:
    """The CSV to fill in by hand. A file that is already there is never overwritten."""
    path = Path(path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(CSV_COLUMNS) + "\n", encoding="utf-8")


def _row_problems(row: dict[str, str]) -> list[str]:
    problems = []
    if not row["company"]:
        problems.append("company is empty")
    if not row["role"]:
        problems.append("role is empty")
    for name in ("applied_on", "status_date", "next_follow_up"):
        value = row[name]
        if value:
            try:
                if not _DAY.match(value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                problems.append(f"{name} '{value}' is not a date written as YYYY-MM-DD")
    if row["status"] and row["status"].upper() not in STATUSES:
        problems.append(f"status '{row['status']}' is not one of {', '.join(STATUSES)}")
    return problems


def import_csv(path: Path, url: str | None = None, *, dry_run: bool = False, owner: str = store.LOCAL_USER_ID) -> dict:
    """Import applications logged by hand. Any problem row stops the whole import; fix the file and run it again."""
    report: dict = {"rows": 0, "valid": 0, "applications_added": 0, "events_added": 0, "already_present": 0, "problems": [], "message": ""}
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) <= 1 and (not lines or lines[0].replace(" ", "").split(",") == CSV_COLUMNS):
        report["message"] = f"Nothing to import: {path.name} is absent or has no rows."
        return report
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames != CSV_COLUMNS:
        report["problems"].append((1, f"the header must be exactly: {','.join(CSV_COLUMNS)}"))
        report["message"] = "Not imported: the header is wrong."
        return report
    rows = []
    for record in reader:
        row = {name: (record.get(name) or "").strip() for name in CSV_COLUMNS}
        if not any(row.values()):
            continue
        report["rows"] += 1
        problems = _row_problems(row)
        report["problems"] += [(reader.line_num, problem) for problem in problems]
        if not problems:
            report["valid"] += 1
            rows.append(row)
    if report["problems"]:
        report["message"] = "Not imported: fix the rows listed and run it again. Nothing was written."
        return report

    with store.session(url) as session:
        present = set(session.scalars(select(Application.identity).where(Application.owner_id == owner, Application.identity.like("csv:%"))))
        companies: dict[str, str] = {}
        versions: dict[str, str] = {}
        stamp = _now()
        for row in rows:
            identity = "csv:" + _sha(f"{company_key(row['company'])}|{title_key(row['role'])}|{row['url']}")[:32]
            if identity in present:
                report["already_present"] += 1
                continue
            present.add(identity)
            status = (row["status"] or ("APPLIED" if row["applied_on"] else "SAVED")).upper()
            events = []
            if row["applied_on"] or status == "APPLIED":
                events.append(("applied", row["applied_on"] or row["status_date"] or stamp, bool(row["applied_on"] or row["status_date"])))
            if status != "APPLIED":
                when = row["status_date"] or row["applied_on"] or stamp
                events.append((STATUS_EVENT[status], when, bool(row["status_date"])))
            report["applications_added"] += 1
            report["events_added"] += len(events)
            if dry_run:
                continue
            version = None
            if row["cv_version"]:
                label = row["cv_version"]
                if label not in versions:
                    found = session.scalar(select(CvVersion.id).where(CvVersion.owner_id == owner, CvVersion.label == label))
                    if found is None:
                        found = str(uuid4())
                        session.add(CvVersion(id=found, owner_id=owner, label=label, created_at=stamp))
                        session.flush()
                    versions[label] = found
                version = versions[label]
            application = Application(
                id=str(uuid4()), owner_id=owner, company_id=_company(session, owner, row["company"], companies), identity=identity,
                title=row["role"], company_name=row["company"], url=row["url"] or None, source=row["source"] or None,
                channel=row["channel"] or None, status=status, applied_at=row["applied_on"] or None, cv_version_id=version,
                next_follow_up_at=row["next_follow_up"] or None, notes=row["notes"], created_at=stamp, updated_at=stamp)
            session.add(application)
            session.flush()
            for event_type, when, exact in events:
                session.add(ApplicationEvent(application_id=application.id, owner_id=owner, event_type=event_type, occurred_at=when,
                                             occurred_at_exact=exact, recorded_at=stamp, source="import",
                                             request_id=f"{identity}:{event_type}", context_json={"csv": path.name}))
        if dry_run:
            report["message"] = "Dry run: nothing was written."
            return report
        session.commit()
    report["message"] = f"Imported {report['applications_added']} applications; {report['already_present']} were already there."
    return report
