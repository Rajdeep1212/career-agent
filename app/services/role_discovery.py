"""Offline, extensible role discovery. Suggestions are leads, not qualifications."""
import json
import re
from functools import lru_cache
from pathlib import Path

from app.models.career import RoleSuggestion, SearchIntent
from app.models.schemas import CandidateProfile


@lru_cache(maxsize=1)
def load_role_catalog() -> list[dict]:
    return json.loads((Path(__file__).resolve().parents[1] / 'core' / 'role_catalog.json').read_text(encoding='utf-8'))


def contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I))


def _family_matches(title: str) -> list[tuple[str, dict]]:
    return [(term, item) for item in load_role_catalog() for term in item['titles'] + item['aliases']
            if contains_phrase(title, term)]


def family_for_title(title: str) -> dict | None:
    """The role family a typed search names ("AI jobs" is the AI family)."""
    matches = _family_matches(title)
    return max(matches, key=lambda match: len(match[0]))[1] if matches else None


# A bare "AI" or "ML" in a job title is often a label on another role ("Product Manager II - AI",
# "AI Social Media Content Intern"). In a title it names the AI family only next to a technical role
# word, next to an AI-specific signal word ("Agentic AI Lead"), or when the title names both AI and
# ML ("AI/ML Expert"). "lead"/"manager" alone are not signal words: they would readmit every
# management or product title that happens to mention AI ("Consulting and Advisory Lead - Data & AI").
_LABEL_TERMS = {'ai', 'ml'}
_TECHNICAL_ROLE = re.compile(r'\b(?:engineers?|engineering|developers?|scientists?|research(?:ers?)?|architects?|'
                             r'sde|swe|mle|residents?|residency)\b', re.I)
_AI_SIGNAL = re.compile(r'\bagentic\b', re.I)


def family_for_job_title(title: str) -> dict | None:
    """The role family of a job posting's title."""
    matches = _family_matches(title)
    labels = {term.casefold() for term, _ in matches} & _LABEL_TERMS
    technical = bool(_TECHNICAL_ROLE.search(title)) or bool(_AI_SIGNAL.search(title)) or labels == _LABEL_TERMS
    matches = [(term, item) for term, item in matches if term.casefold() not in _LABEL_TERMS or technical]
    return max(matches, key=lambda match: len(match[0]))[1] if matches else None


def _evidence_rows(profile: CandidateProfile) -> list[tuple[str, str]]:
    rows = []
    for field in ('skills', 'projects', 'research', 'experience', 'internships', 'certifications', 'education', 'domain_knowledge'):
        rows.extend((field, str(value)) for value in getattr(profile, field, []) if value)
    for category, values in profile.skill_categories.items():
        rows.extend((f'skill category {category}', value) for value in values)
    for category, values in profile.evidence.items():
        rows.extend((f'evidence {category}', value) for value in values)
    return rows


def _suggestion(item: dict, profile: CandidateProfile, titles: list[str] | None = None) -> RoleSuggestion:
    evidence = []
    signals = set()
    for field, value in _evidence_rows(profile):
        matched = [signal for signal in item['signals'] if contains_phrase(value, signal)]
        if matched:
            signals.update(matched)
            evidence.append(f'{field}: {value}')
    evidence = list(dict.fromkeys(evidence))
    suitability = 'strong' if len(signals) >= 3 else 'possible' if evidence else 'exploratory'
    reason = ('Candidate evidence overlaps this family; review each job’s requirements.' if evidence
              else 'Exploratory option: the profile does not yet provide supporting evidence for this family.')
    return RoleSuggestion(family=item['family'], titles=titles or item['titles'][:3], reason=reason,
                          evidence=evidence, suitability=suitability)


def suggest_role_families(profile: CandidateProfile) -> list[RoleSuggestion]:
    """Return grounded families, or broad exploratory options for an empty profile."""
    suggestions = [_suggestion(item, profile) for item in load_role_catalog()]
    grounded = [item for item in suggestions if item.evidence]
    return sorted(grounded or suggestions, key=lambda item: (-len(item.evidence), item.family))


def expand_roles(intent: SearchIntent, profile: CandidateProfile) -> list[RoleSuggestion]:
    """Respect explicit titles, preserving unfamiliar titles without inventing evidence."""
    suggestions = []
    for title in intent.roles_requested:
        item = family_for_title(title)
        if item:
            suggestions.append(_suggestion(item, profile, [title] + [value for value in item['titles'][:2] if value.lower() != title.lower()]))
        else:
            suggestions.append(RoleSuggestion(family='custom', titles=[title], suitability='exploratory',
                                              reason='Requested role preserved; no catalog evidence mapping is available.'))
    for family in intent.role_families:
        if any(item.family == family for item in suggestions):
            continue
        item = next((item for item in load_role_catalog() if item['family'] == family), None)
        if item:
            suggestions.append(_suggestion(item, profile))
    if not suggestions:
        present = {item.family for item in suggestions}
        suggestions.extend(item for item in suggest_role_families(profile) if item.family not in present)
    if intent.non_coding:
        coding = {item['family'] for item in load_role_catalog() if item['coding']}
        suggestions = [item for item in suggestions if item.family not in coding]
    # A bounded set of titles keeps exploratory searches from displacing the
    # requested role or spending the query budget on one broad catalog family.
    suggestions = suggestions[:6]
    titles = [list(dict.fromkeys(item.titles)) for item in suggestions]
    remaining = 6 - len(suggestions)
    for suggestion, candidates in zip(suggestions, titles):
        suggestion.titles = candidates[:1]
    for depth in (1, 2):
        for suggestion, candidates in zip(suggestions, titles):
            if remaining and len(candidates) > depth:
                suggestion.titles.append(candidates[depth])
                remaining -= 1
    return suggestions
