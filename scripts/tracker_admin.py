"""Tracker database tasks (docs/ROADMAP_QUEUE.md TRK1).

Usage (from the repository root, in the app's Python environment):
    python scripts/tracker_admin.py migrate
    python scripts/tracker_admin.py import-m2 --dry-run
    python scripts/tracker_admin.py import-m2
    python scripts/tracker_admin.py template
    python scripts/tracker_admin.py import-csv --file data/tracker/applications.csv --dry-run
    python scripts/tracker_admin.py import-csv --file data/tracker/applications.csv

`migrate` creates or upgrades data/tracker.sqlite3. `import-m2` copies the M2 tracker out of data/agent.sqlite3, which
is opened read-only and backed up first under data/backups/. `import-csv` imports applications logged by hand;
`template` writes the empty CSV to fill in. Both imports can be run again safely. See app/tracker/importers.py.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.storage import career_store  # noqa: E402
from app.tracker import importers, store  # noqa: E402

TEMPLATE = Path(settings.data_dir) / "tracker" / "applications_template.csv"


def _print(report: dict) -> None:
    for line, problem in report.get("problems", []):
        print(f"  line {line}: {problem}")
    print(", ".join(f"{key} {value}" for key, value in report.items() if key not in ("problems", "message", "backup")))
    if report.get("backup"):
        print(f"backup: {report['backup']}")
    print(report["message"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tracker database tasks.")
    parser.add_argument("command", choices=("migrate", "import-m2", "import-csv", "template"))
    parser.add_argument("--file", type=Path, default=Path(settings.data_dir) / "tracker" / "applications.csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "template":
        importers.write_template(TEMPLATE)
        print(f"template: {TEMPLATE}")
        return 0
    store.upgrade()
    if args.command == "migrate":
        print(f"tracker database is up to date: {store.DB_PATH}")
        return 0
    if args.command == "import-m2":
        report = importers.import_m2(career_store.DB_PATH, backup_root=Path(settings.data_dir) / "backups", dry_run=args.dry_run)
    else:
        report = importers.import_csv(args.file, dry_run=args.dry_run)
    _print(report)
    return 1 if report.get("problems") else 0


if __name__ == "__main__":
    raise SystemExit(main())
