"""Fictional demo data; real preferences are derived from the saved profile."""
from app.models.schemas import CandidateProfile, JobSearchPreferences

DEFAULT_PROFILE = CandidateProfile(
    name="Alex Morgan (demo)", graduation_year=2025, degree="B.Sc. Information Systems",
    skills=["Python", "SQL", "Excel", "Postman"],
    projects=["Inventory reporting dashboard using Python and SQL"],
    parsing_warnings=["Fictional demo profile. Upload your resume before using personal recommendations."],
)
DEFAULT_JOB_PREFERENCES = JobSearchPreferences()
