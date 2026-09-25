"""Conservative, local resume extraction with original supporting text."""
import re
from pathlib import Path

from app.models.schemas import CandidateProfile
# Re-exported: the vocabulary lives in app/services/skills.py and app/core/skills/.
from app.services.skills import KNOWN_SKILLS, SKILL_CATEGORIES, canonical_skill, extract_skills
from app.services.skills import contains_phrase as _contains

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_TEXT_CHARS = 200_000

_HEADINGS = {
    "education": "education", "academic qualifications": "education", "qualifications": "education",
    "academic background": "education", "educational qualifications": "education",
    "skills": "skills", "technical skills": "skills", "core skills": "skills",
    "key skills": "skills", "competencies": "skills", "core competencies": "skills",
    "tools": "skills", "technologies": "skills", "design skills": "skills",
    "experience": "experience", "work experience": "experience", "professional experience": "experience",
    "employment history": "experience", "employment": "experience",
    "internship": "internships", "internships": "internships", "internship experience": "internships",
    "projects": "projects", "academic projects": "projects", "personal projects": "projects",
    "selected projects": "projects", "research": "research", "research experience": "research",
    "publications": "research", "certifications": "certifications", "certificates": "certifications",
    "licenses and certifications": "certifications", "summary": "summary", "profile": "summary",
    "professional summary": "summary", "career objective": "summary", "objective": "summary",
    "languages": "other", "interests": "other", "achievements": "other", "awards": "other",
    "references": "other", "contact": "other", "contact information": "other",
}
_DEGREE = re.compile(
    r"\b(?:Bachelor(?:'s)?(?:\s+(?:of|in)\s+[A-Za-z &]+)?|Master(?:'s)?(?:\s+(?:of|in)\s+[A-Za-z &]+)?"
    r"|B\.?\s?Tech\.?|M\.?\s?Tech\.?|B\.?\s?Com\.?|M\.?\s?Com\.?|B\.?\s?Sc\.?|M\.?\s?Sc\.?"
    r"|B\.?\s?Des\.?|M\.?\s?Des\.?|BBA|MBA|BCA|MCA|BFA|MFA|B\.A\.?|M\.A\.?|Ph\.?D\.?|Diploma)\b",
    re.IGNORECASE,
)
_SCHOOL = re.compile(
    r"\b(?:class|std\.?|standard|grade)\s*(?:x|xii|10|12|10th|12th)\b|\b(?:10th|12th|x|xii)\s+(?:class|std\.?|standard|grade)\b"
    r"|\b(?:ssc|hsc|sslc|cbse|icse|isc|matriculation|matric|puc|higher\s+secondary|senior\s+secondary|secondary\s+school|intermediate)\b",
    re.IGNORECASE,
)


def extract_pdf_text(path: str) -> str:
    import fitz

    if Path(path).stat().st_size > MAX_PDF_BYTES:
        raise ValueError("Resume PDF exceeds 10 MB. Upload a smaller PDF.")
    try:
        with fitz.open(path) as document:
            if not document.is_pdf:
                raise ValueError("Please upload a PDF resume.")
            if document.needs_pass:
                raise ValueError("Password-protected PDF: upload an unlocked copy.")
            if len(document) > MAX_PDF_PAGES:
                raise ValueError("Resume PDF exceeds 100 pages. Upload a shorter resume.")
            pages, total = [], 0
            for page in document:
                content = page.get_text("text")
                total += len(content) + 1
                if total > MAX_TEXT_CHARS:
                    raise ValueError("Resume text exceeds 200,000 characters. Upload a shorter resume.")
                pages.append(content)
            text = "\n".join(pages)
    except (fitz.FileDataError, fitz.EmptyFileError) as error:
        raise ValueError("Cannot read this PDF. Export a new PDF and try again.") from error
    if not text.strip():
        raise ValueError("No readable text in this PDF. It may be scanned; apply OCR or upload a text-based PDF.")
    return text


def _heading(line: str) -> tuple[str | None, str]:
    label, separator, rest = line.partition(":")
    return _HEADINGS.get(label.strip().casefold()), rest.strip() if separator else ""


def _extract_name(lines: list[str]) -> str | None:
    for line in lines[:6]:
        if line.lower() in ("resume", "curriculum vitae", "cv"):
            continue
        if _heading(line)[0]:
            break
        candidate = re.sub(r"^name\s*:\s*", "", line, flags=re.IGNORECASE)
        words = candidate.split()
        if not 2 <= len(words) <= 5 or len(candidate) > 80:
            continue
        if _DEGREE.search(candidate) or extract_skills(candidate):
            continue
        if all(word.replace("-", "").replace("'", "").replace("’", "").replace(".", "").isalpha() for word in words):
            return candidate.title() if candidate.isupper() else candidate
    return None


def _explicit_skills(lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        value = line.split(":", 1)[-1]
        for item in re.split(r"[,;|•]", value):
            item = item.strip(" \t-–")
            if item and len(item) <= 60 and len(item.split()) <= 6 and not re.search(r"[.!?]$", item):
                result.append(item)
    return result


def parse_profile_from_text(text: str) -> CandidateProfile:
    if not text.strip():
        raise ValueError("Resume has no readable text. Paste text or upload a text-based PDF.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Resume text exceeds 200,000 characters. Use a shorter resume.")
    lines = [line.strip().strip("• ") for line in text.splitlines() if line.strip()]
    sections: dict[str, list[str]] = {key: [] for key in set(_HEADINGS.values())}
    section = "summary"
    for line in lines:
        heading, value = _heading(line)
        if heading:
            section = heading
            if value:
                sections[section].append(value)
        else:
            sections[section].append(line)

    education = sections["education"] or [line for line in lines if _DEGREE.search(line)]
    degrees = [match.group(0).strip().rstrip(".") for line in education if (match := _DEGREE.search(line))]
    degree = degrees[0] if degrees else None
    years = []
    for line in education:
        # Indian CVs list Class X/XII results in Education; they are not the degree year.
        if _SCHOOL.search(line) and not _DEGREE.search(line):
            continue
        # "2021-25" ends in 2025; keep the four-digit form for the lookup below.
        line = re.sub(r"\b((?:19|20)(\d{2}))\s*[-–]\s*(\d{2})\b(?!\d)", lambda m: f"{m[1]} - {m[1][:2]}{m[3]}", line)
        candidates = re.findall(r"\b(?:19|20)\d{2}\b", line)
        if candidates and not re.search(r"\b(?:present|ongoing|current)\b", line, re.IGNORECASE):
            years.append(int(candidates[-1]))
    distinct_years = set(years)
    graduation_year = next(iter(distinct_years)) if len(distinct_years) == 1 and len(degrees) <= 1 else None
    warnings = []
    if graduation_year is None:
        warnings.append("Graduation year is missing or ambiguous; please confirm it.")
    if len(degrees) > 1:
        warnings.append("Multiple qualifications found; confirm the primary degree and graduation year.")
    explicit = [canonical_skill(skill) for skill in _explicit_skills(sections["skills"])]
    skills_by_key = {skill.casefold(): skill for skill in explicit}
    skill_text = "\n".join(value for section_lines in sections.values() for value in section_lines)
    skills_by_key.update({skill.casefold(): skill for skill in extract_skills(skill_text)})
    internships, experience = sections["internships"][:], []
    for line in sections["experience"]:
        (internships if re.search(r"\bintern(?:ship)?\b", line, re.IGNORECASE) else experience).append(line)
    evidence = {key: value[:] for key, value in sections.items() if value and key != "other"}
    if education:
        evidence["education"] = education[:]
    explicit_years = re.findall(r"\b(\d+(?:\.\d+)?)\s*\+?\s*years?\s+(?:of\s+)?(?:professional\s+|work\s+|relevant\s+)?experience\b", text, re.IGNORECASE)
    valid_years = {float(year) for year in explicit_years if 0 <= float(year) <= 70}
    experience_years = next(iter(valid_years)) if len(valid_years) == 1 else None
    if len(valid_years) > 1:
        warnings.append("Multiple experience durations found; confirm total professional experience.")
    relocation = None
    if re.search(r"\b(?:not (?:open|willing) to relocat\w*|no relocation)\b", text, re.IGNORECASE):
        relocation = False
    elif re.search(r"\b(?:open|willing) to relocat\w*\b", text, re.IGNORECASE):
        relocation = True
    profile = CandidateProfile(
        name=_extract_name(lines), degree=degree, graduation_year=graduation_year,
        skills=sorted(skills_by_key.values(), key=str.casefold),
        education=education, experience=experience, internships=internships,
        projects=sections["projects"], research=sections["research"], certifications=sections["certifications"],
        evidence=evidence, experience_years=experience_years, relocation_preference=relocation,
        parsing_warnings=warnings,
    )
    if not profile.name:
        profile.parsing_warnings.append("Candidate name could not be identified confidently; please confirm it.")
    from app.services.candidate_intelligence import analyze_candidate
    return analyze_candidate(profile)
