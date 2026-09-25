from app.models.schemas import JobPosting
from app.providers.base import JobProvider


class MockJobProvider(JobProvider):
    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        return [
            JobPosting(
                company="Example AI Labs",
                title="Junior AI Engineer",
                location="India",
                work_mode="remote",
                experience_min=0,
                experience_max=1,
                fresher_allowed=True,
                graduation_years=[2025, 2026],
                skills=["Python", "FastAPI", "Docker", "SQL", "RAG", "LLM"],
                description="Build and evaluate production GenAI applications.",
                application_status="active",
            ),
            JobPosting(
                company="Example Software",
                title="Python Backend Developer",
                location="Noida, India",
                work_mode="hybrid",
                experience_min=0,
                experience_max=2,
                fresher_allowed=True,
                skills=["Python", "FastAPI", "SQL", "Docker", "REST API"],
                description="Backend services, APIs, databases and Docker.",
                application_status="active",
            ),
        ]
