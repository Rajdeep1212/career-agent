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

_FRESHER = re.compile(r"\bfreshers?\b|\brecent graduat\w*|\bgraduate trainee\b|\bentry[- ]level\b|\bno (?:prior )?experience\b"
                      r"|\bintern(?:ship)?s?\b", re.I)
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
    """Returns True when a batch mention includes the candidate's year.

    A year that excludes needs restrictive wording beside it ("pass-outs only",
    "eligible", "batch of"); a passing mention such as "founded in 2015 by IIT
    graduates" is ignored unless it names the candidate's year.
    """
    clauses = graduation_year_clauses(text)
    years = job.graduation_years or sorted({year for clause in clauses for year in clause.years})
    restrictive = [clause for clause in clauses if clause.restrictive]
    year = intent.graduation_year or preferences.required_graduation_year or profile.graduation_year
    matched = False
    if years and year and year in years:
        matched = True
        result.graduation_match = 'match'
        quote = next((clause.quote for clause in clauses if year in clause.years), None)
        decisions.add('eligible', f'Graduation year {year} is accepted.', quote)
    elif restrictive and year:
        result.graduation_match = 'mismatch'
        allowed = sorted({y for clause in restrictive for y in clause.years})
        decisions.add('excluded', f'Accepts graduation years {allowed}; your year is {year}.', restrictive[0].quote)
    elif restrictive:
        decisions.add('uncertain', 'The listing restricts graduation batches, but your graduation year is unknown.', restrictive[0].quote)
    if restrictive and preferences.exclude_batch_years and set(y for c in restrictive for y in c.years).issubset(preferences.exclude_batch_years):
        decisions.add('excluded', 'The listing is restricted to batches you excluded.', restrictive[0].quote)
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


# Common alternate names for Indian cities; either form matches the other.
_CITY_ALIASES = [
    {"bengaluru", "bangalore"}, {"gurugram", "gurgaon"}, {"mumbai", "bombay"}, {"kolkata", "calcutta"},
    {"chennai", "madras"}, {"kochi", "cochin"}, {"thiruvananthapuram", "trivandrum"}, {"mysuru", "mysore"},
    {"puducherry", "pondicherry"}, {"vadodara", "baroda"}, {"prayagraj", "allahabad"}, {"new delhi", "delhi"},
]
_COUNTRY_ONLY = re.compile(r"^(?:india|in|pan[- ]india|anywhere in india|multiple locations(?:,? india)?|various locations(?:,? india)?)$", re.I)


def _names(place: str) -> set[str]:
    place = place.casefold().strip()
    return next((group for group in _CITY_ALIASES if place in group), {place})


def _location_matches(job_location: str, requested: str) -> bool:
    location = job_location.casefold()
    return any(name in location for name in _names(requested))


def _location(job, intent, decisions, warnings):
    location = job.location.strip()
    if any(_location_matches(location, place) for place in intent.excluded_locations):
        decisions.add('excluded', 'The location is one you excluded.', location)
    if not intent.locations or job.work_mode == 'remote':
        return
    if any(_location_matches(location, place) for place in intent.locations):
        return
    if location.casefold() in ('unknown', 'not specified', ''):
        warnings.append('Location is not specified.')
    elif _COUNTRY_ONLY.match(location):
        decisions.add('uncertain', 'The listing names only the country; the city may still match.', location)
    elif intent.locations_from_preferences:
        decisions.add('uncertain', 'The location is outside your saved locations.', location)
    else:
        decisions.add('excluded', 'The location does not match the requested locations.', location)


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


# ---------------------------------------------------------------------------------------------------------------
# Fresher detectors (docs/SEMANTIC_PLAN.md section 4). Used by the SEM2 experiment only: evaluate_eligibility()
# does not call them, so the app's search behaves exactly as before. Each finding quotes one sentence of the listing.
# They were designed after reading the Q4 disagreements, so batch gold-20261001-r1 is an in-sample test for them.

_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
_EXPERIENCE_WORD = re.compile(r"\b(?:experience|exp)\b", re.I)
_YEARS = r"(?:years?|yrs?)\b"
_BACHELOR_PLUS = re.compile(r"bachelor'?s?\s*\+\s*(\d+(?:\.\d+)?)\s*" + _YEARS, re.I)
_YEAR_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*\+?\s*" + _YEARS, re.I)
_YEAR_PLUS = re.compile(r"(\d+(?:\.\d+)?)\s*\+\s*" + _YEARS, re.I)
_YEAR_MINIMUM = re.compile(r"(?:minimum|at least|min\.?)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*" + _YEARS, re.I)
_YEAR_PLAIN = re.compile(r"(\d+(?:\.\d+)?)\s*" + _YEARS, re.I)
_SENIOR_EXCLUDED = re.compile(r"\b(?:staff|principal|lead|manager|mgr|head of|director|vice president|avp|vp)\b", re.I)
_SENIOR_TEXT = re.compile(r"\b(?:(?i:assistant vice president|vice president)|AVP)\b")     # "AVP" only in capitals
_SENIOR_ONLY = re.compile(r"\b(?:senior|sr\.?)\b", re.I)
_GPA = re.compile(r"\bc?gpa\b\D{0,25}?(\d{1,2}(?:\.\d+)?)|(\d{1,2}(?:\.\d+)?)\s*(?:or above|or more|and above|\+)?\s*c?gpa\b", re.I)
_BATCH_WORD = re.compile(r"\b(?:batch|graduat\w*|pass(?:ing|ed)?[- ]?out)\b", re.I)
_YEAR_SPAN = re.compile(r"(20\d\d)\s*(?:-|–|to)\s*(20\d\d)")
_CALENDAR_YEAR = re.compile(r"\b20\d\d\b")
_STUDENTS_ONLY = re.compile(r"final[- ]year|currently (?:enrolled|pursuing)|career break|returnship|restart your career", re.I)
_INTERN_TITLE = re.compile(r"\bintern(?:ship)?\b", re.I)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text or "") if part.strip()]


def _clip(sentence: str) -> str:
    return sentence if len(sentence) <= 200 else sentence[:197] + "..."


def _years_needed(sentence: str) -> float | None:
    bachelor = _BACHELOR_PLUS.search(sentence)
    if bachelor:
        return float(bachelor.group(1))
    found = [float(match.group(1)) for pattern in (_YEAR_RANGE, _YEAR_PLUS, _YEAR_MINIMUM, _YEAR_PLAIN) for match in pattern.finditer(sentence)]
    return min(found) if found else None


def fresher_detectors(title: str, text: str, facts: dict) -> list[EligibilityEvidence]:
    """Findings about whether a fresher (facts: graduation_year, cgpa) can get this job, each quoting the listing."""
    found: list[EligibilityEvidence] = []
    sentences = _sentences(text)
    years_excluded = False
    for sentence in sentences:                                   # years required
        if not _EXPERIENCE_WORD.search(sentence):
            continue
        needed = _years_needed(sentence)
        if needed is None or needed < 1:
            continue
        if needed >= 2 and not _FRESHER.search(sentence):
            years_excluded = True
            found.append(EligibilityEvidence(outcome="excluded", reason=f"asks for at least {needed:g} years of experience",
                                             quote=_clip(sentence)))
        else:
            found.append(EligibilityEvidence(outcome="uncertain", reason=f"asks for {needed:g} or more years of experience",
                                             quote=_clip(sentence)))
    if _SENIOR_EXCLUDED.search(title or ""):                     # seniority
        found.append(EligibilityEvidence(outcome="excluded", reason="a senior, lead or management title", quote=_clip(title)))
    else:
        senior_text = next((sentence for sentence in sentences if _SENIOR_TEXT.search(sentence)), None)
        if senior_text:
            found.append(EligibilityEvidence(outcome="excluded", reason="a vice-president level role", quote=_clip(senior_text)))
        elif _SENIOR_ONLY.search(title or "") and not years_excluded:
            found.append(EligibilityEvidence(outcome="uncertain", reason="'Senior' in the title with no years stated",
                                             quote=_clip(title)))
    gate = False
    for sentence in sentences:                                   # batch or GPA cutoffs
        for match in _GPA.finditer(sentence):
            value = float(match.group(1) or match.group(2))
            if value <= 10 and facts.get("cgpa") is not None and value > facts["cgpa"]:
                gate = True
                found.append(EligibilityEvidence(outcome="excluded", reason=f"a GPA floor of {value:g}, above {facts['cgpa']:g}",
                                                 quote=_clip(sentence)))
                break
        if _BATCH_WORD.search(sentence) and facts.get("graduation_year"):
            years = {int(year) for year in _CALENDAR_YEAR.findall(sentence)}
            for start, end in _YEAR_SPAN.findall(sentence):
                years |= set(range(int(start), int(end) + 1))
            if years:
                gate = True
                if facts["graduation_year"] not in years:
                    found.append(EligibilityEvidence(outcome="excluded", reason=f"a batch window without {facts['graduation_year']}",
                                                     quote=_clip(sentence)))
    students = next((sentence for sentence in sentences if _STUDENTS_ONLY.search(sentence)), None)   # students only
    if students:
        found.append(EligibilityEvidence(outcome="excluded", reason="open to current students or people returning from a break only",
                                         quote=_clip(students)))
    elif _INTERN_TITLE.search(title or "") and not gate:
        found.append(EligibilityEvidence(outcome="uncertain", reason="an internship with no eligibility sentence", quote=_clip(title)))
    return found
