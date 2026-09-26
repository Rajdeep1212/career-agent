"""The daily alert-email step: the IMAP inbox (if configured), then the .eml dropbox.

Run by the Company Radar sync after the companies, or on its own:
    python -m app.sources.alerts            ingest and store
    python -m app.sources.alerts --dry-run  print what would be stored; change nothing
Re-running is safe: IMAP reads only unread mail and dropped files are moved out.
"""
import argparse
import sys

from app.sources.alerts.imap import configured as imap_configured
from app.sources.alerts.imap import ingest_imap
from app.sources.alerts.ingest import IngestOutcome, dropbox_dir, ingest_dropbox


def run_alerts(*, dry_run: bool = False) -> IngestOutcome:
    from_imap = ingest_imap(dry_run=dry_run)
    has_files = dropbox_dir().exists() and any(dropbox_dir().glob("*.eml"))
    from_files = ingest_dropbox(dry_run=dry_run)
    if from_imap.status == "skipped" and not has_files:
        return IngestOutcome(status="skipped", note="no alerts inbox configured and no .eml files in the dropbox")
    outcome = IngestOutcome(status="error" if from_imap.status == "error" else "ok")
    if from_imap.status != "skipped":
        outcome.merge(from_imap)
    return outcome.merge(from_files)


def summary(outcome: IngestOutcome) -> str:
    if outcome.status == "skipped":
        return f"Alert emails: skipped ({outcome.note})"
    text = (f"Alert emails: {outcome.status}, {outcome.messages} emails, {outcome.parsed_jobs} jobs "
            f"({outcome.new} new, {outcome.matched_official} matched an official job)")
    return text + (f"; {outcome.note}" if outcome.note else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.sources.alerts", description="Read job-alert emails into Career Agent.")
    parser.add_argument("--dry-run", action="store_true", help="print the parsed jobs; store nothing, mark nothing read")
    args = parser.parse_args(argv)
    print("Alerts inbox: " + ("configured" if imap_configured() else "not configured (set ALERTS_IMAP_* in .env)")
          + f"; dropbox: {dropbox_dir()}")
    outcome = run_alerts(dry_run=args.dry_run)
    print(summary(outcome) + (" (dry run: nothing saved)" if args.dry_run else ""))
    return 1 if outcome.status == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
