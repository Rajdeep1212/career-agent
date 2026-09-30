"""Inputs for a job snapshot: the current CV and the L0 features of one stored job (docs/M2_PLAN.md §1.3).

Unlike career_agent.evaluate_job, this never writes the job back, so snapshotting cannot change the
stored job it describes.
"""
from app.models.schemas import JobPosting
from app.services.eligibility import evaluate_eligibility
from app.services.matching import match_job
from app.sources.digest import saved_role_intent
from app.storage.profile_store import load_profile


def snapshot_inputs(job: dict) -> tuple[dict, dict]:
    """(profile, features): the CV as stored now, and the heuristic fit (claim level L0) against the
    saved roles and locations."""
    profile, preferences, intent = saved_role_intent()
    posting = JobPosting.model_validate(job)
    eligibility = evaluate_eligibility(profile, posting, preferences, intent)
    match = match_job(profile, posting, intent, eligibility)
    features = {
        "claim_level": "L0",
        "overall_score": match.overall_score,
        "components": match.components,
        "skill_score": match.skill_score,
        "role_score": match.role_score,
        "experience_score": match.experience_score,
        "location_score": match.location_score,
        "matched_skills": match.matched_skills,
        "missing_skills": match.missing_skills,
        "eligibility_status": eligibility.status,
        "eligibility_summary": eligibility.summary,
        "intent": "saved roles and locations",
    }
    return load_profile().model_dump(mode="json"), features
