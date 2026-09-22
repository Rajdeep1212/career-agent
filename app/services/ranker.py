"""Legacy ranking API backed by generic eligibility and evidence-based matching."""
from app.models.schemas import JobScore
from app.models.career import SearchIntent
from app.services.eligibility import evaluate_eligibility
from app.services.matching import match_job


def score_job(profile, job, prefs):
    intent = SearchIntent(roles_requested=profile.preferred_roles, locations=[], strict_mode=prefs.require_active_application)
    eligibility = evaluate_eligibility(profile, job, prefs, intent)
    match = match_job(profile, job, intent, eligibility)
    return JobScore(company=job.company,title=job.title,location=job.location,work_mode=job.work_mode,
        application_url=str(job.application_url) if job.application_url else None,source=job.source,
        posted_date=job.posted_date,official_application=job.official_application,total_score=match.overall_score,
        skill_score=match.skill_score,eligibility_score=match.experience_score,
        role_relevance_score=match.role_score,location_score=match.location_score,
        project_relevance_score=match.components.get("projects_research",0),
        application_quality_score=match.components.get("verification",0),
        matched_skills=match.matched_skills,missing_skills=match.missing_skills,
        eligible=eligibility.eligible,reasons=match.strengths+match.gaps+eligibility.hard_rejections)


def rank_jobs(profile, jobs, prefs):
    scores=[score_job(profile,job,prefs) for job in jobs]
    return sorted((score for score in scores if score.eligible),key=lambda score:score.total_score,reverse=True)
