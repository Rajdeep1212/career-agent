"""The target profile the MCP tools score against: data/eval/target_profile.md, written by the user.

This is the only candidate information an MCP tool may use. It is not the CV and is not derived
from it. The file is free Markdown; these `key: value` lines are read when present (a leading
list marker is fine):

    roles: AI Engineer, Machine Learning Engineer
    locations: Pune, Bengaluru
    graduation year: 2025
    experience years: 0
    degree: B.Tech
    skills: Python, SQL

Skills are also taken from the rest of the text with the shared skill vocabulary. Nothing else
is read: no name, projects, employers or evidence.
"""
import re
from pathlib import Path

from app.models.schemas import CandidateProfile
from app.services.skills import canonical_skill, extract_skills

_LINE = re.compile(r"^\s*(?:[-*+]\s*)?\**([A-Za-z][A-Za-z _]{1,30}?)\**\s*:\s*(.+?)\s*$")
_KEYS = {"roles": "roles", "role": "roles", "target roles": "roles", "locations": "locations", "location": "locations",
         "cities": "locations", "city": "locations", "graduation year": "graduation_year", "graduation": "graduation_year",
         "batch": "graduation_year", "experience years": "experience_years", "experience": "experience_years",
         "degree": "degree", "skills": "skills"}


def _items(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;]", value) if item.strip()]


def load(path: Path) -> CandidateProfile | None:
    """The target profile, or None when the file does not exist."""
    path = Path(path)
    if not path.exists():
        return None
    fields: dict[str, str] = {}
    free_text = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line)
        key = _KEYS.get(match.group(1).strip().casefold()) if match else None
        if key and match:
            fields.setdefault(key, match.group(2))
        else:
            free_text.append(line)
    year = re.search(r"\b(19|20)\d{2}\b", fields.get("graduation_year", ""))
    years = re.search(r"\d+(?:\.\d+)?", fields.get("experience_years", ""))
    skills = [canonical_skill(item) for item in _items(fields.get("skills", ""))] + extract_skills("\n".join(free_text))
    return CandidateProfile(skills=list(dict.fromkeys(skills)), preferred_roles=_items(fields.get("roles", "")),
                            preferred_locations=_items(fields.get("locations", "")),
                            graduation_year=int(year.group(0)) if year else None,
                            experience_years=float(years.group(0)) if years else None, degree=fields.get("degree"))
