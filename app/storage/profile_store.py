import json
from pathlib import Path

from app.core.config import settings
from app.core.preferences import DEFAULT_PROFILE
from app.models.schemas import CandidateProfile


PROFILE_PATH = Path(settings.data_dir) / "current_profile.json"


def save_profile(profile: CandidateProfile) -> CandidateProfile:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps(profile.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return profile


def load_profile() -> CandidateProfile:
    if not PROFILE_PATH.exists():
        return DEFAULT_PROFILE.model_copy(deep=True)

    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        return CandidateProfile.model_validate(data)
    except Exception:
        return DEFAULT_PROFILE.model_copy(deep=True)
