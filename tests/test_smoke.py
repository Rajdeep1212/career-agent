"""End-to-end smoke checks for ranking and the draft approval store."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.preferences import DEFAULT_JOB_PREFERENCES
from app.models.schemas import CandidateProfile, JobPosting
from app.services.ranker import rank_jobs
from app.storage import email_store


class SmokeTests(unittest.TestCase):
    def test_eligible_job_is_ranked(self):
        profile = CandidateProfile(
            graduation_year=2025, degree="B.Tech",
            skills=["Python", "FastAPI", "Docker", "SQL", "RAG", "LLM", "PyTorch"],
            projects=["Inventory assistant"], research=["Example University research project"],
            preferred_roles=["AI Engineer", "Python Backend"],
        )
        job = JobPosting(
            company="Demo", title="Junior AI Engineer", location="India", work_mode="remote",
            experience_min=0, fresher_allowed=True, recent_graduate_allowed=True,
            graduation_years=[2025, 2026], skills=["Python", "FastAPI", "Docker", "SQL", "RAG", "LLM"],
            description="AI Engineer working with RAG, LLM, Python and FastAPI",
            application_status="active", official_application=True,
        )
        result = rank_jobs(profile, [job], DEFAULT_JOB_PREFERENCES)
        self.assertTrue(result)
        self.assertTrue(result[0].eligible)
        self.assertGreater(result[0].total_score, 0)

    def test_draft_needs_explicit_approval(self):
        with tempfile.TemporaryDirectory(prefix="job-agent-smoke-") as directory, \
             patch.object(email_store, "DB_PATH", Path(directory) / "agent.sqlite3"):
            draft = email_store.create_draft(recipient="recruiter@example.com", subject="Application",
                                             body="Hello", attachment_id=None)
            self.assertEqual(draft["status"], "draft")
            self.assertEqual(email_store.approve_draft(draft["id"])["status"], "approved")
            self.assertEqual(email_store.get_draft(draft["id"])["status"], "approved")


if __name__ == "__main__":
    unittest.main()
