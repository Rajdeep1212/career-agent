"""Evidence-backed enrichment for parsed and manually entered profiles."""
import re

from app.models.schemas import CandidateProfile
from app.services.cv_parser import _SCHOOL, SKILL_CATEGORIES, _contains

_DOMAINS = [
    "Accounting", "Finance", "Banking", "Healthcare", "Education", "Retail",
    "Manufacturing", "Supply Chain", "Marketing", "Human Resources", "Hospitality",
    "Agriculture", "Civil Engineering", "Mechanical Engineering", "Computer Science",
    "Graphic Design", "User Experience", "Natural Language Processing",
]


def analyze_candidate(profile: CandidateProfile) -> CandidateProfile:
    """Enrich a copy without adding unstated degrees, experience, or preferences."""
    result = profile.model_copy(deep=True)
    categories: dict[str, list[str]] = {}
    for skill in result.skills:
        category = next((key for key, values in SKILL_CATEGORIES.items()
                         if skill.casefold() in {value.casefold() for value in values}), "other")
        categories.setdefault(category, []).append(skill)
    result.skill_categories = categories
    sources = list(dict.fromkeys(
        result.education + result.experience + result.internships + result.projects + result.research
        + result.certifications + result.skills + ([result.degree] if result.degree else [])
    ))
    # School/board lines are not domain evidence, and "Education" inside an
    # education line names an institution ("Board of Secondary Education").
    education = set(result.education)
    sources = [source for source in sources if not (source in education and _SCHOOL.search(source))]
    domain_evidence = []
    for domain in _DOMAINS:
        candidates = [source for source in sources if source not in education] if domain == "Education" else sources
        matches = [source for source in candidates if _contains(source, domain)]
        if matches:
            if domain.casefold() not in {value.casefold() for value in result.domain_knowledge}:
                result.domain_knowledge.append(domain)
            domain_evidence.extend(matches)
    if domain_evidence:
        result.evidence["domain_knowledge"] = list(dict.fromkeys(domain_evidence))
    if result.skills:
        result.evidence.setdefault("skills", result.skills[:])
    for field in ("education", "experience", "internships", "projects", "research", "certifications"):
        values = getattr(result, field)
        if values:
            result.evidence.setdefault(field, values[:])
    if result.experience_level == "unknown":
        if result.experience_years is not None:
            result.experience_level = "entry_level" if result.experience_years <= 2 else "experienced"
        else:
            summary = " ".join(result.evidence.get("summary", []))
            if re.search(r"\b(?:fresher|entry[- ]level|recent graduate)\b", summary, re.IGNORECASE):
                result.experience_level = "entry_level"
    if result.experience_years is None:
        warning = "Total professional experience is not stated; it has not been inferred from dates or internships."
        if warning not in result.parsing_warnings:
            result.parsing_warnings.append(warning)
    return result
