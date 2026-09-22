"""Deterministic English intent parsing with structured, non-destructive refinements.

This deliberately has no model/API dependency. Ambiguous prose should be reviewed
through SearchIntent.warnings; role titles and locations need not be in a catalog.
"""
import re

from app.models.career import SearchIntent
from app.services.role_discovery import family_for_title, load_role_catalog


_LIMITATION = 'Rule-based English parser: review interpreted roles and filters; ambiguous or unsupported wording may be missed.'
_BOUNDARY = r'(?=\s*(?:[;.!]|$)|\s+(?:excluding|except|but not|not in|with|for|minimum|score|scores|posted|remote|hybrid|onsite|strict|no|only|at|from|in the|and remote)\b)'


def _list(text: str) -> list[str]:
    return list(dict.fromkeys(value.strip(' ,.:;\"\'') for value in re.split(r'\s*(?:,|\band\b|\bor\b|\+|/)\s*', text, flags=re.I) if value.strip(' ,.:;')))


def _places(text: str, excluded: bool = False) -> list[str]:
    prefix = r'\b(?:excluding|except|but not|not in|exclude|avoid)\s+(?:locations?\s+)?(?:in\s+)?' if excluded else r'\b(?:in|near|locations?\s*[:=])\s+'
    results = []
    for match in re.finditer(prefix + r'([A-Za-z][A-Za-z\s,\-+\'/]*?)' + _BOUNDARY, text, re.I):
        if not excluded and re.search(r'(?:not|except|excluding|posted)\s*$', text[:match.start()], re.I):
            continue
        values = _list(match.group(1))
        results.extend(value for value in values if not re.search(r'\b(?:last|past|days?|weeks?|months?|industry|sector|remote|hybrid|onsite)\b', value, re.I))
    return list(dict.fromkeys(results))


def _roles(text: str) -> list[str]:
    # Remove constraints before retaining free-form requested role text.
    head = re.split(r'\b(?:in|near|exclude|excluding|avoid|except|with|for|at|posted|remote|minimum|score|scores)\b', text, maxsplit=1, flags=re.I)[0]
    head = re.sub(r'^(?:(?:please|instead|also|can you|i want|i need|find|search|show|me|include|add|look for|looking for|suggest|only)\s+)+', '', head, flags=re.I)
    head = re.sub(r'\b(?:jobs?|roles?|positions?|opportunities)\b.*$', '', head, flags=re.I).strip(' ,.:')
    head = re.sub(r'\bfreshers?\b', '', head, flags=re.I).strip()
    generic = r'^(?:|great|suitable|relevant|any|all|more|non[- ]?coding|strong matches|best matches|freshers?|entry[- ]level)$'
    if head and not re.fullmatch(generic, head, re.I) and not re.search(r'\b(?:scores?|matches?|cv|resume|graduates?|years?|only|no|above|below|at least|strong|strict|relaxed)\b', head, re.I):
        results = []
        for title in _list(head):
            title = {'testing': 'QA Engineer', 'agentic ai': 'Agentic AI Engineer'}.get(title.lower(), title)
            catalog = family_for_title(title)
            exact = next((known for known in catalog['titles'] if known.lower() == title.lower()), None) if catalog else None
            results.append(exact or (catalog['titles'][0] if catalog and title.lower() in catalog['aliases'] else title))
        return list(dict.fromkeys(results))
    # Constraint-first prompts: longest catalog title/alias wins overlapping spans.
    hits = []
    for item in load_role_catalog():
        for title in item['titles'] + item['aliases']:
            for match in re.finditer(r'(?<!\w)' + re.escape(title) + r'(?!\w)', text, re.I):
                hits.append((match.start(), match.end(), title if title in item['titles'] else item['titles'][0]))
    picked = []
    for start, end, title in sorted(hits, key=lambda value: -(value[1] - value[0])):
        if not any(start < old_end and end > old_start for old_start, old_end, _ in picked):
            picked.append((start, end, title))
    return list(dict.fromkeys(title for _, _, title in sorted(picked)))


def interpret_search_request(text: str, previous: SearchIntent | None = None) -> SearchIntent:
    """Apply this turn to previous structured state; never concatenate old prompts."""
    intent = previous.model_copy(deep=True) if previous else SearchIntent()
    intent.filter_only = False
    intent.warnings = [_LIMITATION]
    text = ' '.join(text.split()).strip()
    low = text.lower()
    if not text:
        intent.warnings.append('Empty request: previous filters retained.')
        return intent
    changed = set()
    def assign(field, value):
        setattr(intent, field, value)
        changed.add(field)

    non_coding = bool(re.search(r'\bnon[- ]?coding\b|\bwithout (?:coding|programming)\b', low))
    if non_coding:
        assign('non_coding', True)
    elif re.search(r'\b(?:include|allow) coding\b', low):
        assign('non_coding', False)
    if re.search(r'\b(?:cv|resume|profile)\b', low) and re.search(r'\b(?:based|suit|suitable|match|discover|suggest|fit|recommend)\w*\b', low):
        assign('cv_discovery', True)

    roles = _roles(text)
    if non_coding or intent.cv_discovery and not re.search(r'\b(?:engineer|analyst|developer|designer|manager|specialist|associate|technician|writer|recruiter|accountant)\b', low):
        roles = []
        if 'cv_discovery' in changed or non_coding:
            assign('roles_requested', [])
            assign('role_families', [])
    if roles:
        additive = bool(previous and re.search(r'\b(?:also|add|include too)\b', low))
        assign('roles_requested', list(dict.fromkeys((intent.roles_requested if additive else []) + roles)))
        assign('role_families', list(dict.fromkeys(item['family'] for title in intent.roles_requested if (item := family_for_title(title)))))

    excluded = _places(text, True)
    locations = [place for place in _places(text) if place.lower() not in {item.lower() for item in excluded}]
    # A common compact request uses "remote + Kolkata" without a preposition.
    compact = re.search(r'\bremote\s*(?:\+|or|and)\s+(?!in\b)([A-Za-z][A-Za-z -]*?)' + _BOUNDARY, text, re.I)
    if compact:
        locations.extend(_list(compact.group(1)))
    if excluded:
        assign('excluded_locations', list(dict.fromkeys(intent.excluded_locations + excluded)))
    if locations:
        assign('locations', list(dict.fromkeys((intent.locations if re.search(r'\b(?:also|add)\b', low) else []) + locations)))

    if re.search(r'\b(?:remote[- ]only|only remote|work from home only)\b', low):
        assign('remote_allowed', True); assign('hybrid_allowed', False); assign('onsite_allowed', False)
    elif re.search(r'\b(?:no|not|exclude|without) remote\b', low):
        assign('remote_allowed', False)
    elif re.search(r'\bremote\b|\bwork from home\b', low):
        assign('remote_allowed', True)
        if locations:
            assign('onsite_allowed', True); assign('hybrid_allowed', True)
        elif not re.search(r'\b(?:also|include|allow|hybrid|onsite)\b', low):
            assign('onsite_allowed', False); assign('hybrid_allowed', False)
    for mode in ('hybrid', 'onsite'):
        if re.search(r'\b(?:only ' + mode + '|' + mode + r'[- ]only)\b', low):
            for other in ('remote', 'hybrid', 'onsite'):
                assign(other + '_allowed', mode == other)
        elif re.search(r'\b(?:no|exclude|without) ' + mode + r'\b', low):
            assign(mode + '_allowed', False)

    score = re.search(r'\b(?:minimum(?: match)?(?: score)?|min(?:imum)? score|scores?|match(?: score)?)\s*(?:of|is|:|=|above|over|at least|>=|greater than)?\s*(\d+(?:\.\d+)?)', low)
    if not score:
        score = re.search(r'\b(?:above|over|at least|minimum)\s+(\d+(?:\.\d+)?)\s*(?:%|match|score)', low)
    if not score:
        score = re.search(r'\b(\d+(?:\.\d+)?)\s*\+\s*(?:%\s*)?(?:matches|scores?)\b', low)
    if score:
        value = float(score.group(1))
        assign('minimum_match_score', max(0, min(100, value)))
        if value > 100:
            intent.warnings.append('Match score was limited to 100.')
    elif re.search(r'\b(?:strong|high|best) (?:profile )?match(?:es)?\b', low):
        assign('minimum_match_score', 75)
    if re.search(r'\b(?:relaxed|relax strict|not strict|no strict)\b', low):
        assign('strict_mode', False)
    elif re.search(r'\b(?:strict|strictly)\b', low):
        assign('strict_mode', True)

    year = re.search(r'\b(20\d{2})\s*(?:graduates?|graduation|batch|pass[- ]?outs?)\b|\b(?:graduation|graduated|batch|graduating)(?: year| in| of|:)?\s*(20\d{2})\b', low)
    if year:
        assign('graduation_year', int(year.group(1) or year.group(2)))
    years = re.search(r'\b(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b', low)
    if years:
        bounds = sorted([float(years.group(1)), float(years.group(2))])
        assign('experience_min', bounds[0]); assign('experience_max', bounds[1])
    else:
        maximum = re.search(r'\b(?:up to|at most|max(?:imum)?|less than)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b', low)
        minimum = re.search(r'\b(?:at least|min(?:imum)?)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b|\b(\d+(?:\.\d+)?)\+\s*(?:years?|yrs?)\b', low)
        if maximum:
            assign('experience_max', float(maximum.group(1)))
        if minimum:
            assign('experience_min', float(minimum.group(1) or minimum.group(2)))
    if re.search(r'\b(?:freshers?|entry[- ]level|no experience)\b', low):
        assign('fresher_preference', True)
        if re.search(r'\bfreshers? only\b|\bno experience\b', low):
            assign('experience_min', 0); assign('experience_max', 0)
    for word, field in [('internships?', 'internship_allowed'), ('contracts?', 'contract_allowed')]:
        if re.search(r'\b(?:no|exclude|excluding|without) ' + word + r'\b', low):
            assign(field, False)
        elif re.search(r'\b(?:include|allow|also) ' + word + r'\b', low):
            assign(field, True)
    if re.search(r'\bfull[- ]time only\b', low):
        assign('internship_allowed', False); assign('contract_allowed', False)
    companies = re.search(r'\b(?:at|companies?\s*[:=])\s+([A-Za-z][A-Za-z0-9 &.,-]*?)(?=,?\s+(?:posted|with|in|for|minimum|only|excluding)\b|$)', text, re.I)
    if companies and not companies.group(1).lower().startswith(('least ', 'most ')):
        assign('company_preferences', _list(companies.group(1)))
    industry = re.search(r'\b(?:industry|sector)\s*[:=]\s*([A-Za-z ,&-]+?)(?=\s+(?:in|with|for|posted)\b|$)', text, re.I)
    if industry:
        assign('industry_preferences', _list(industry.group(1)))
    freshness = re.search(r'\b(?:last|past|within)\s+(\d+)\s+(days?|weeks?|months?|hours?)\b', low)
    if freshness:
        assign('freshness_preference', f'{freshness.group(1)} {freshness.group(2)}')
    elif re.search(r'\b(?:today|latest|recent|newest)\b', low):
        assign('freshness_preference', '1 day' if 'today' in low else 'recent')
    intent.filter_only = previous is not None and changed == {'minimum_match_score'}
    if intent.experience_min is not None and intent.experience_max is not None and intent.experience_min > intent.experience_max:
        intent.warnings.append('Experience minimum exceeds maximum; review these filters.')
    if re.search(r'\b(?:salary|lpa|compensation|visa|sponsorship)\b', low):
        intent.warnings.append('Salary and visa/sponsorship constraints are not structured filters in this parser; review these requirements manually.')
    return intent
