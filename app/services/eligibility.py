"""Eligibility is independent of the fit score; unknown evidence is a warning."""
import re
from datetime import datetime, timezone
from app.models.career import EligibilityResult, SearchIntent
from app.services.job_requirements import extract_requirements


def evaluate_eligibility(profile, job, preferences, intent: SearchIntent) -> EligibilityResult:
    result=EligibilityResult()
    text=job.title+' '+job.description
    minimum,maximum,years=extract_requirements(text)
    minimum=max(minimum,job.experience_min)
    years=job.graduation_years or years
    year=intent.graduation_year or preferences.required_graduation_year or profile.graduation_year
    state='CLOSED' if job.application_status=='closed' else job.verification_state
    if state=='CLOSED':result.hard_rejections.append('Application is closed.')
    elif state=='UNVERIFIED' and job.application_status!='active':
        if intent.strict_mode or preferences.require_active_application:result.hard_rejections.append('Strict Mode requires verified or likely-active evidence.')
        else:result.warnings.append('Application status is unverified; check the listing before applying.')
    elif state=='LIKELY_ACTIVE':result.warnings.append('Likely active; direct application status is not confirmed.')
    if years and year:
        result.graduation_match='match' if year in years else 'mismatch'
        if year not in years:result.hard_rejections.append(f'Accepts graduation years {years}; candidate year is {year}.')
        else:result.positive_signals.append(f'Graduation year {year} is accepted.')
    elif years:result.warnings.append('Candidate graduation year is unknown; confirm batch eligibility.')
    if years and preferences.exclude_batch_years and set(years).issubset(preferences.exclude_batch_years):
        result.hard_rejections.append('Listing is restricted to excluded graduation batches.')
    fresher=job.fresher_allowed or job.recent_graduate_allowed or bool(re.search(r'freshers?|recent graduat|graduate trainee|entry.level|no experience',text,re.I))
    candidate_exp=profile.experience_years
    permitted=intent.experience_max if intent.experience_max is not None else preferences.max_required_experience_years
    if intent.experience_max is None and 'max_required_experience_years' not in preferences.model_fields_set and candidate_exp is not None:
        permitted=max(1,candidate_exp)
    if candidate_exp is not None:permitted=min(permitted,candidate_exp+1) if candidate_exp>0 else permitted
    exception=preferences.allow_zero_to_two_when_fresher_friendly and fresher and minimum<=2
    if minimum>permitted and not exception:
        result.hard_rejections.append(f'Requires at least {minimum:g} years; current experience limit is {permitted:g}.')
        result.experience_match='mismatch'
    elif minimum>0:
        result.experience_match='possible'
        if candidate_exp is None or minimum>candidate_exp:result.warnings.append(f'Requests {minimum:g} years; confirm your relevant experience.')
    elif fresher:
        result.experience_match='match';result.positive_signals.append('Explicit entry-level or graduate language.')
    else:result.warnings.append('Experience requirement is not explicit.')
    if re.search(r'\b(senior|sr\.?|lead|principal|director|head of|vp)\b',job.title,re.I) and (candidate_exp or 0)<3:
        result.hard_rejections.append('Clearly senior title does not match current candidate experience.')
    modes={'remote':intent.remote_allowed,'hybrid':intent.hybrid_allowed,'onsite':intent.onsite_allowed}
    if job.work_mode!='unknown' and (not modes.get(job.work_mode,True) or job.work_mode not in preferences.allowed_work_modes):
        result.hard_rejections.append('Work mode is excluded by your preferences.')
    if any(x.casefold() in job.location.casefold() for x in intent.excluded_locations):
        result.hard_rejections.append('Location is explicitly excluded.')
    if intent.locations and job.work_mode!='remote' and not any(x.casefold() in job.location.casefold() for x in intent.locations):
        if job.location.lower() in ('unknown','not specified'):result.warnings.append('Location is not specified.')
        else:result.hard_rejections.append('Location does not match the requested locations.')
    employment=(job.employment_type or '').lower()+' '+job.title.lower()
    if not intent.internship_allowed and 'intern' in employment:result.hard_rejections.append('Internships were excluded.')
    if not intent.contract_allowed and 'contract' in employment:result.hard_rejections.append('Contract work was excluded.')
    if intent.company_preferences and not any(c.casefold() in job.company.casefold() for c in intent.company_preferences):
        result.hard_rejections.append('Company does not match the requested companies.')
    if intent.industry_preferences:
        if job.industry and not any(x.casefold() in job.industry.casefold() for x in intent.industry_preferences):
            result.hard_rejections.append('Industry does not match the requested industries.')
        elif not job.industry and not any(x.casefold() in text.casefold() for x in intent.industry_preferences):
            result.warnings.append('Requested industry is not confirmed by the listing.')
    if intent.freshness_preference:
        value=re.search(r'(\d+)\s*(day|week|month|hour)',intent.freshness_preference)
        limit=float(value[1])*{'day':1,'week':7,'month':30,'hour':1/24}[value[2]] if value else 30
        try:
            posted=datetime.fromisoformat((job.posted_date or '').replace('Z','+00:00'))
            if posted.tzinfo is None:posted=posted.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc)-posted).total_seconds()/86400>limit:
                result.hard_rejections.append(f'Job was posted outside the requested {intent.freshness_preference} window.')
        except ValueError:result.warnings.append('Posted date is missing or unclear; freshness cannot be confirmed.')
    result.eligible=not result.hard_rejections
    result.confidence='high' if result.hard_rejections or (years and fresher) else 'medium' if fresher else 'low'
    return result
