from pathlib import Path
from app.core.config import settings
from app.models.schemas import CandidateProfile, JobSearchPreferences

PREFERENCES_PATH = Path(settings.data_dir) / "preferences.json"


def preferences_for(profile: CandidateProfile) -> JobSearchPreferences:
    if PREFERENCES_PATH.exists():
        return JobSearchPreferences.model_validate_json(PREFERENCES_PATH.read_text(encoding="utf-8"))
    return JobSearchPreferences(
        required_graduation_year=profile.graduation_year,
        preferred_locations=profile.preferred_locations,
        allowed_work_modes=profile.work_mode_preferences,
        max_required_experience_years=max(1, profile.experience_years or 0),
        require_fresher_or_recent_grad_language=(profile.experience_years or 0) < 1,
    )


def save_preferences(preferences: JobSearchPreferences):
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PREFERENCES_PATH.with_suffix(".tmp")
    temporary.write_text(preferences.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(PREFERENCES_PATH)
    return preferences
