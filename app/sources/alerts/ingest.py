"""Turn alert emails into stored jobs: parse, dedup, match to the Company Radar.

Emails come from IMAP (app/sources/alerts/imap.py) or from .eml files dropped
into data/alert_dropbox/. A processed file moves to data/alert_dropbox/processed/
(never deleted). Jobs that match an indexed official job are linked to it, so
searches show the official URL and source-based status; the rest stay
"from an alert email", unverified. Counts are len() of parsed lists.
"""
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.services.job_identity import resolve_with_index
from app.sources.alerts.parser import ParsedAlert, parse_alert_email
from app.sources.registry import RadarConfigError, alias_map, read_config
from app.storage import alert_store, radar_store

logger = logging.getLogger(__name__)


@dataclass
class IngestOutcome:
    status: str = "ok"             # ok | skipped | error
    messages: int = 0              # emails read this run
    already_processed: int = 0     # skipped: seen in an earlier run
    parsed_jobs: int = 0           # len() of all jobs parsed from the emails
    new: int = 0
    duplicates: int = 0
    matched_official: int = 0      # linked to an official Company Radar job
    note: str = ""
    processed_keys: list[str] = field(default_factory=list)

    def merge(self, other: "IngestOutcome") -> "IngestOutcome":
        for name in ("messages", "already_processed", "parsed_jobs", "new", "duplicates", "matched_official"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.processed_keys += other.processed_keys
        self.note = "; ".join(note for note in (self.note, other.note) if note)
        return self


def message_key(raw: bytes, parsed: ParsedAlert) -> str:
    return parsed.message_id or "sha256:" + hashlib.sha256(raw).hexdigest()[:32]


def _match_official(parsed_jobs) -> int:
    if not parsed_jobs or not radar_store.has_jobs():
        return 0
    try:
        aliases = alias_map(read_config())
    except RadarConfigError:
        aliases = {}
    _resolved, matches = resolve_with_index(parsed_jobs, radar_store.index_entries(), aliases)
    radar_store.record_sources(matches)
    for key, job in matches:
        alert_store.set_radar_key(job, key)
    return len(matches)


def ingest_raw(messages: list[tuple[str, bytes]], *, dry_run: bool = False) -> IngestOutcome:
    """Parse and store (origin, raw email) pairs; returns counts and the keys processed."""
    outcome = IngestOutcome()
    for origin, raw in messages:
        parsed = parse_alert_email(raw)
        key = message_key(raw, parsed)
        if alert_store.message_processed(key):
            outcome.already_processed += 1
            outcome.processed_keys.append(key)
            continue
        outcome.messages += 1
        jobs = parsed.postings()
        outcome.parsed_jobs += len(jobs)
        if dry_run:
            for job in jobs:
                print(f"  [{parsed.platform}] {job.title} | {job.company} | {job.location} | {job.application_url}")
            continue
        stored = alert_store.upsert(jobs, kind="alert")
        outcome.new += stored.new
        outcome.duplicates += stored.duplicates
        outcome.matched_official += _match_official(jobs)
        alert_store.record_message(key, origin=origin, platform=parsed.platform, jobs_found=len(jobs))
        outcome.processed_keys.append(key)
    return outcome


def dropbox_dir() -> Path:
    return Path(settings.alerts_dropbox_dir) if settings.alerts_dropbox_dir else Path(settings.data_dir) / "alert_dropbox"


def ingest_dropbox(*, dry_run: bool = False) -> IngestOutcome:
    """Every .eml file in the dropbox; processed files move to processed/ (never deleted)."""
    folder = dropbox_dir()
    files = sorted(folder.glob("*.eml")) if folder.exists() else []
    outcome = IngestOutcome()
    for path in files:
        try:
            raw = path.read_bytes()
            result = ingest_raw([("dropbox", raw)], dry_run=dry_run)
        except Exception as exc:   # one unreadable file must not stop the others
            logger.warning("alert file %s could not be processed: %s", path.name, type(exc).__name__)
            outcome.note = "; ".join(filter(None, (outcome.note, f"{path.name}: {type(exc).__name__}")))
            continue
        outcome.merge(result)
        if not dry_run:
            processed = folder / "processed"
            processed.mkdir(parents=True, exist_ok=True)
            target = processed / path.name
            if target.exists():
                target = processed / f"{path.stem}-{hashlib.sha256(raw).hexdigest()[:8]}{path.suffix}"
            path.replace(target)
    return outcome
