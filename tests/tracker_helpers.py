"""Point the tracker (data/tracker.sqlite3) at a temporary file for one test."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.tracker import store


def isolate_tracker(case: TestCase, directory: Path) -> Path:
    path = Path(directory) / "tracker.sqlite3"
    patcher = patch.object(store, "DB_PATH", path)
    patcher.start()
    case.addCleanup(patcher.stop)
    case.addCleanup(store.dispose_all)       # no engine keeps the file open (Windows would lock it)
    return path
