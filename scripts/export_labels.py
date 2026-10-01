"""Export the live thumbs labels for the evaluation harness (docs/M2_PLAN.md §4).

Usage (from the repository root, in the app's Python environment):
    python scripts/export_labels.py

Writes data/eval/labels/inapp_<UTC time>.jsonl: one JSON line per job that has a current label, with
the job as it was when labelled. The CV appears only as its version hash. These labels are biased
toward what the ranker showed, so the harness reports them apart from the graded batch labels.
Read-only for the app databases.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.storage import career_store  # noqa: E402


def export(directory: Path | None = None) -> Path:
    """Write the export and return its path."""
    directory = Path(directory) if directory else Path(settings.data_dir) / "eval" / "labels"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"inapp_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    rows = career_store.thumb_export_rows()
    target.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                      encoding="utf-8")
    return target


if __name__ == "__main__":
    path = export()
    count = len(path.read_text(encoding="utf-8").splitlines())
    print(f"Exported {count} live thumbs labels to {path}")
