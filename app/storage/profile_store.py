import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.preferences import DEFAULT_PROFILE
from app.models.schemas import CandidateProfile


PROFILE_PATH = Path(settings.data_dir) / "current_profile.json"


class ProfileUnreadableError(Exception):
    """The saved profile exists but cannot be read; never replace it with demo data."""


def _read() -> CandidateProfile:
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        return CandidateProfile.model_validate(data)
    except (OSError, ValueError) as exc:
        raise ProfileUnreadableError(
            "Your saved profile could not be read. Upload your CV again to recreate it; "
            "the unreadable file is kept as a backup."
        ) from exc


def save_profile(profile: CandidateProfile) -> CandidateProfile:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PROFILE_PATH.exists():
        try:
            _read()
        except ProfileUnreadableError:
            # Keep the unreadable file instead of silently overwriting it.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            shutil.copy2(PROFILE_PATH, PROFILE_PATH.with_name(f"{PROFILE_PATH.stem}.corrupt-{stamp}.json"))
    temporary = PROFILE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(profile.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(PROFILE_PATH)
    return profile


def load_profile() -> CandidateProfile:
    """The saved profile, or the labeled fictional demo profile before any upload."""
    if not PROFILE_PATH.exists():
        return DEFAULT_PROFILE.model_copy(deep=True)
    return _read()
