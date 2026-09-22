import tempfile
from pathlib import Path
from unittest.mock import patch

from app.core.preferences import DEFAULT_JOB_PREFERENCES
from app.models.schemas import CandidateProfile, JobPosting
from app.services.ranker import rank_jobs
from app.storage import email_store


profile = CandidateProfile(
    graduation_year=2025,
    degree="B.Tech",
    skills=["Python", "FastAPI", "Docker", "SQL", "RAG", "LLM", "PyTorch"],
    projects=["Inventory assistant"],
    research=["Example University research project"],
    preferred_roles=["AI Engineer", "Python Backend"],
)

jobs = [
    JobPosting(
        company="Demo",
        title="Junior AI Engineer",
        location="India",
        work_mode="remote",
        experience_min=0,
        fresher_allowed=True,
        recent_graduate_allowed=True,
        graduation_years=[2025, 2026],
        skills=["Python", "FastAPI", "Docker", "SQL", "RAG", "LLM"],
        description="AI Engineer working with RAG, LLM, Python and FastAPI",
        application_status="active",
        official_application=True,
    )
]

result = rank_jobs(profile, jobs, DEFAULT_JOB_PREFERENCES)
assert result
assert result[0].eligible
assert result[0].total_score > 0

with tempfile.TemporaryDirectory(prefix='job-agent-smoke-') as directory:
    with patch.object(email_store, 'DB_PATH', Path(directory) / 'agent.sqlite3'):
        draft = email_store.create_draft(
            recipient="recruiter@example.com",
            subject="Application",
            body="Hello",
            attachment_id=None,
        )
        assert draft["status"] == "draft"

        approved = email_store.approve_draft(draft["id"])
        assert approved["status"] == "approved"
        assert email_store.get_draft(draft["id"])["status"] == "approved"

print("Smoke tests passed.")
