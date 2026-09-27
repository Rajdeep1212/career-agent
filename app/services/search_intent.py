"""Deterministic English intent parsing with structured, non-destructive refinements.

This deliberately has no model/API dependency. Ambiguous prose should be reviewed
through SearchIntent.warnings; role titles and locations need not be in a catalog.
"""
import re

from app.models.career import SearchIntent
from app.services.role_discovery import family_for_title, load_role_catalog
from app.services.skills import KNOWN_SKILLS, canonical_skill, extract_skills, split_composite


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


_NEGATED = re.compile(r'\b(?:no|not|without|except|excluding|exclude|avoid)\s+[^,;]*?(?=[,;]|\.(?:\s|$)|\s+(?:in|with|for|at|using|posted)\b|$)', re.I)


def _is_skill(text: str) -> bool:
    """A known vocabulary skill (or a list of them) that is not a role-catalog title or alias."""
    if family_for_title(text):
        return False
    known = {name.casefold() for name in KNOWN_SKILLS}
    return canonical_skill(text).casefold() in known or split_composite(text) is not None


def _keywords(text: str, role_text: str) -> tuple[list[str], list[str]]:
    """(requested skills, negated skills); skills already named by the role are left out."""
    negated = [match.group(0) for match in _NEGATED.finditer(text)]
    requested = extract_skills(_NEGATED.sub(' ', text))
    covered = set(extract_skills(role_text))
    return [skill for skill in requested if skill not in covered], extract_skills(' '.join(negated))


def _roles(text: str) -> list[tuple[str, str]]:
    """(text as written, normalized title) for each requested role."""
    # Remove constraints before retaining free-form requested role text.
    head = re.split(r'\b(?:in|near|exclude|excluding|avoid|except|with|for|at|posted|remote|minimum|score|scores)\b', text, maxsplit=1, flags=re.I)[0]
    head = re.sub(r'^(?:(?:please|now|instead|also|can you|i want|i need|find|search|show|me|include|add|look for|looking for|suggest|only)\s+)+', '', head, flags=re.I)
    head = re.sub(r'\b(?:jobs?|roles?|positions?|opportunities)\b.*$', '', head, flags=re.I).strip(' ,.:')
    head = re.sub(r'\bfreshers?\b', '', head, flags=re.I).strip()
    generic = r'^(?:|great|suitable|relevant|any|all|more|non[- ]?coding|strong matches|best matches|freshers?|entry[- ]level)$'
    if head and not re.fullmatch(generic, head, re.I) and not re.search(r'\b(?:scores?|matches?|cv|resume|graduates?|years?|only|no|above|below|at least|strong|strict|relaxed)\b', head, re.I):
        results = []
        for written in _list(head):
            title = {'testing': 'QA Engineer', 'agentic ai': 'Agentic AI Engineer'}.get(written.lower(), written)
            catalog = family_for_title(title)
            exact = next((known for known in catalog['titles'] if known.lower() == title.lower()), None) if catalog else None
            results.append((written, exact or (catalog['titles'][0] if catalog and title.lower() in catalog['aliases'] else title)))
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
    return list(dict.fromkeys((text[start:end], title) for start, end, title in sorted(picked)))


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

    additive = bool(previous and re.search(r'\b(?:also|add|include too)\b', low))
    pairs = _roles(text)
    skill_only = [written for written, _ in pairs if _is_skill(written)]
    pairs = [(written, title) for written, title in pairs if written not in skill_only]
    roles = list(dict.fromkeys(title for _, title in pairs))
    reset = False
    if non_coding or intent.cv_discovery and not re.search(r'\b(?:engineer|analyst|developer|designer|manager|specialist|associate|technician|writer|recruiter|accountant)\b', low):
        roles = []
        if 'cv_discovery' in changed or non_coding:
            reset = True
            assign('roles_requested', [])
            assign('role_families', [])
    if roles:
        assign('roles_requested', list(dict.fromkeys((intent.roles_requested if additive else []) + roles)))
        assign('role_families', list(dict.fromkeys(item['family'] for title in intent.roles_requested if (item := family_for_title(title)))))
    elif skill_only and not additive:
        # "LangChain jobs" names skills, not a role: earlier roles no longer apply.
        assign('roles_requested', [])
        assign('role_families', [])

    intent.keywords_cleared = None
    keywords, negated = _keywords(text, ' '.join(written for written, _ in pairs) + ' ' + ' '.join(roles))
    if keywords:
        assign('keywords', list(dict.fromkeys((intent.keywords if additive else []) + keywords)))
    elif intent.keywords and (reset or roles):
        intent.keywords_cleared = 'CV-based search' if reset else 'new role'
        assign('keywords', [])
    if negated:
        intent.warnings.append(f'Excluded skills ({", ".join(negated)}) are not a filter in this parser; listings that mention them are still shown.')

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
        assign('locations_from_preferences', False)

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
