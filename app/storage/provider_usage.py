"""Local request counts for providers that do not report their own quota.

Counts are keyed by a short hash of the API key, never the key itself, and
only cover requests made from this machine.
"""
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

USAGE_PATH = Path(settings.data_dir) / "provider_usage.json"
_LOCK = threading.Lock()


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _load() -> dict:
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # Missing or unreadable counts start fresh; they are advisory only.
        return {}


def record(provider: str, key: str, count: int = 1) -> None:
    """Count requests about to be sent with this key."""
    now = datetime.now(timezone.utc)
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    with _LOCK:
        data = _load()
        entry = data.setdefault(f"{provider}:{_fingerprint(key)}", {"total": 0, "days": {}, "months": {}})
        entry["total"] += count
        entry["days"] = {day: entry["days"].get(day, 0) + count}  # only today's count is needed
        entry["months"][month] = entry["months"].get(month, 0) + count
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = USAGE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(USAGE_PATH)


def usage(provider: str, key: str) -> dict:
    now = datetime.now(timezone.utc)
    entry = _load().get(f"{provider}:{_fingerprint(key)}", {})
    return {"total": entry.get("total", 0), "today": entry.get("days", {}).get(now.strftime("%Y-%m-%d"), 0),
            "month": entry.get("months", {}).get(now.strftime("%Y-%m"), 0)}
