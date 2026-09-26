"""Three-way eligibility (eligible / uncertain / excluded) with quoted evidence.

This is a deterministic heuristic (claim level L0 in CLAUDE.md), not a
prediction. A listing is excluded only for an explicit disqualifier in its own
text or an explicit user constraint; ambiguous or missing evidence is
"uncertain" and still shown to the user, ranked below "eligible".
"""
import re
from datetime import datetime, timezone

from app.models.career import EligibilityEvidence, EligibilityResult, EligibilityStatus, SearchIntent
from app.services.job_requirements import experience_clauses, graduation_year_clauses, quote_around

_FRESHER = re.compile(r"\bfreshers?\b|\brecent graduat\w*|\bgraduate trainee\b|\bentry[- ]level\b|\bno (?:prior )?experience\b", re.I)
_SENIOR_TITLE = re.compile(r"\b(?:senior|sr\.?|principal|staff|director|head of|vp|vice president|architect)\b", re.I)
_LEAD_TITLE = re.compile(r"\blead\b", re.I)
# A minimum this far above the candidate's limit is a clear disqualifier; closer is uncertain.
_CLEAR_EXPERIENCE_GAP = 2


class _Decisions:
    def __init__(self):
        self.items: list[EligibilityEvidence] = []

    def add(self, outcome: EligibilityStatus, reason: str, quote: str | None = None):
        self.items.append(EligibilityEvidence(outcome=outcome, reason=reason, quote=quote))

    def of(self, outcome: EligibilityStatus) -> list[EligibilityEvidence]:
        return [item for item in self.items if item.outcome == outcome]


def _quote(text: str, pattern: re.Pattern) -> str | None:
    match = pattern.search(text)
    return quote_around(text, match.start(), match.end()) if match else None


def _experience_limit(profile, preferences, intent) -> float:
    candidate = profile.experience_years
    permitted = intent.experience_max if intent.experience_max is not None else preferences.max_required_experience_years
    if intent.experience_max is None and 'max_required_experience_years' not in preferences.model_fields_set and candidate is not None:
        permitted = max(1, candidate)
    if candidate is not None and candidate > 0:
        permitted = min(permitted, candidate + 1)
    return permitted


def _application_status(job, preferences, intent, decisions, warnings):
    state = 'CLOSED' if job.application_status == 'closed' else job.verification_state
    if state == 'CLOSED':
        decisions.add('excluded', 'The application is closed.', job.verification_reason or None)
    elif state == 'UNVERIFIED' and job.application_status != 'active':
        if intent.strict_mode or preferences.require_active_application:
            decisions.add('excluded', 'Strict mode requires verified or likely-active evidence.', job.verification_reason or None)
        else:
            warnings.append('Application status is unverified; check the listing before applying.')
    elif state == 'LIKELY_ACTIVE':
        warnings.append('Likely active; direct application status is not confirmed.')


def _graduation(profile, job, preferences, intent, text, decisions, result) -> bool:
    """Returns True when the listing's batch list includes the candidate's year."""
    clauses = graduation_year_clauses(text)
    years = job.graduation_years or sorted({year for clause_years, _ in clauses for year in clause_years})
    quote = next((sentence for _, sentence in clauses), None)
    year = intent.graduation_year or preferences.required_graduation_year or profile.graduation_year
    matched = False
    if years and year:
        matched = year in years
        result.graduation_match = 'match' if matched else 'mismatch'
        if matched:
            decisions.add('eligible', f'Graduation year {year} is accepted.', quote)
        else:
            decisions.add('excluded', f'Accepts graduation years {years}; your year is {year}.', quote)
    elif years:
        decisions.add('uncertain', 'The listing names graduation batches, but your graduation year is unknown.', quote)
    if years and preferences.exclude_batch_years and set(years).issubset(preferences.exclude_batch_years):
        decisions.add('excluded', 'The listing is restricted to batches you excluded.', quote)
    return matched


def _experience(profile, job, preferences, intent, text, decisions, result, batch_match: bool):
    clauses = experience_clauses(text)
    firm = [clause for clause in clauses if not clause.soft]
    soft = [clause for clause in clauses if clause.soft]
    strongest = max(firm, key=lambda clause: clause.minimum, default=None)
    minimum = max(strongest.minimum if strongest else 0, job.experience_min)
    quote = strongest.quote if strongest and strongest.minimum >= minimum else None
    limit = _experience_limit(profile, preferences, intent)
    fresher_quote = _quote(text, _FRESHER)
    if minimum > 0:
        within = minimum <= limit or (preferences.allow_zero_to_two_when_fresher_friendly and fresher_quote and minimum <= 2)
        if within:
            result.experience_match = 'possible'
            decisions.add('eligible', f'Asks for {minimum:g} years; within your limit of {limit:g}.', quote or fresher_quote)
        elif minimum < limit + _CLEAR_EXPERIENCE_GAP:
            result.experience_match = 'possible'
            decisions.add('uncertain', f'Asks for {minimum:g} years, slightly above your limit of {limit:g}.', quote)
        else:
            result.experience_match = 'mismatch'
            decisions.add('excluded', f'Requires at least {minimum:g} years; your limit is {limit:g}.', quote)
    elif fresher_quote:
        result.experience_match = 'match'
        decisions.add('eligible', 'Explicit entry-level or graduate language.', fresher_quote)
    elif firm:
        result.experience_match = 'match'
        decisions.add('eligible', 'The stated experience range starts at 0 years.', firm[0].quote)
    elif batch_match:
        result.experience_match = 'match'
    elif soft:
        result.experience_match = 'possible'
        decisions.add('uncertain', 'Experience is preferred, not required.', soft[0].quote)
    else:
        decisions.add('uncertain', 'The listing states no experience requirement.')


def _seniority(profile, job, decisions):
    if (profile.experience_years or 0) >= 3:
        return
    senior = _SENIOR_TITLE.search(job.title)
    if senior:
        decisions.add('excluded', 'The title is explicitly senior for your experience.', job.title)
    elif _LEAD_TITLE.search(job.title):
        decisions.add('uncertain', '"Lead" in the title may mean a senior role.', job.title)


def _work_mode(job, preferences, intent, decisions):
    if job.work_mode == 'unknown':
        return
    allowed_by_intent = {'remote': intent.remote_allowed, 'hybrid': intent.hybrid_allowed, 'onsite': intent.onsite_allowed}
    if not allowed_by_intent.get(job.work_mode, True) or job.work_mode not in preferences.allowed_work_modes:
        mode_word = {'onsite': r'on[- ]?site|in[- ]office', 'remote': r'remote|work from home', 'hybrid': r'hybrid'}[job.work_mode]
        quote = _quote(f"{job.title}. {job.description}", re.compile(rf"\b(?:{mode_word})\b", re.I))
        decisions.add('excluded', f'Work mode "{job.work_mode}" is excluded by your settings.', quote or f'work mode: {job.work_mode}')


def _location(job, intent, decisions, warnings):
    location = job.location.casefold()
    if any(place.casefold() in location for place in intent.excluded_locations):
        decisions.add('excluded', 'The location is one you excluded.', job.location)
    if intent.locations and job.work_mode != 'remote' and not any(place.casefold() in location for place in intent.locations):
        if location in ('unknown', 'not specified'):
            warnings.append('Location is not specified.')
        else:
            decisions.add('excluded', 'The location does not match the requested locations.', job.location)


def _employment(job, intent, decisions):
    employment_type = (job.employment_type or '').lower()
    if not intent.internship_allowed:
        if re.search(r'\bintern(?:ship)?s?\b', employment_type + ' ' + job.title.lower()):
            decisions.add('excluded', 'Internships were excluded.', job.employment_type or job.title)
    if not intent.contract_allowed:
        if 'contract' in employment_type:
            decisions.add('excluded', 'Contract work was excluded.', job.employment_type)
        elif re.search(r'\bcontract(?:ual|or)?\b', job.title, re.I):
            decisions.add('uncertain', 'The title mentions contract work; you excluded contracts.', job.title)


def _company_and_industry(job, intent, text, decisions):
    if intent.company_preferences and not any(c.casefold() in job.company.casefold() for c in intent.company_preferences):
        decisions.add('excluded', 'The company is not one you requested.', job.company)
    if intent.industry_preferences:
        if job.industry and not any(x.casefold() in job.industry.casefold() for x in intent.industry_preferences):
            decisions.add('excluded', 'The industry is not one you requested.', job.industry)
        elif not job.industry and not any(x.casefold() in text.casefold() for x in intent.industry_preferences):
            decisions.add('uncertain', 'The listing does not confirm the requested industry.')


def _freshness(job, intent, decisions):
    if not intent.freshness_preference:
        return
    value = re.search(r'(\d+)\s*(day|week|month|hour)', intent.freshness_preference)
    limit = float(value[1]) * {'day': 1, 'week': 7, 'month': 30, 'hour': 1 / 24}[value[2]] if value else 30
    try:
        posted = datetime.fromisoformat((job.posted_date or '').replace('Z', '+00:00'))
    except ValueError:
        decisions.add('uncertain', 'The posted date is missing or unclear, so freshness cannot be confirmed.')
        return
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - posted).total_seconds() / 86400 > limit:
        decisions.add('excluded', f'Posted outside the requested {intent.freshness_preference} window.', f'posted {job.posted_date}')


def evaluate_eligibility(profile, job, preferences, intent: SearchIntent) -> EligibilityResult:
    result = EligibilityResult()
    decisions = _Decisions()
    warnings: list[str] = []
    text = f"{job.title}. {job.description}"
    _application_status(job, preferences, intent, decisions, warnings)
    batch_match = _graduation(profile, job, preferences, intent, text, decisions, result)
    _experience(profile, job, preferences, intent, text, decisions, result, batch_match)
    _seniority(profile, job, decisions)
    _work_mode(job, preferences, intent, decisions)
    _location(job, intent, decisions, warnings)
    _employment(job, intent, decisions)
    _company_and_industry(job, intent, text, decisions)
    _freshness(job, intent, decisions)

    excluded, uncertain, supporting = decisions.of('excluded'), decisions.of('uncertain'), decisions.of('eligible')
    result.status = 'excluded' if excluded else 'uncertain' if uncertain or not supporting else 'eligible'
    result.eligible = result.status != 'excluded'
    result.evidence = decisions.items
    decisive = (excluded or uncertain or supporting)
    result.summary = decisive[0].describe() if decisive else 'uncertain: no eligibility evidence in the listing.'
    result.hard_rejections = [item.reason for item in excluded]
    result.warnings = [item.reason for item in uncertain] + warnings
    result.positive_signals = [item.reason for item in supporting]
    fresher = result.experience_match == 'match'
    result.confidence = 'high' if excluded or (result.graduation_match == 'match' and fresher) else 'medium' if supporting else 'low'
    return result
