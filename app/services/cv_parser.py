"""Conservative, local resume extraction with original supporting text."""
import posixpath
import re
import zipfile
from pathlib import Path

# lxml is installed with python-docx (requirements.txt).
from lxml import etree

from app.models.schemas import CandidateProfile
# The vocabulary lives in app/services/skills.py and app/core/skills/.
from app.services.skills import canonical_skill, extract_skills, split_composite

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
    r"|\b(?:10th|12th|secondary|ssc|hsc|sslc|cbse|icse|isc|matriculation|matric|puc|intermediate)\b"
    # State boards, e.g. WBBSE, WBCHSE, BSEB, RBSE, MPBSE, GSEB, PSEB, HBSE, CHSE.
    r"|\b(?:wbbse|wbchse|bseb|rbse|mpbse|gseb|pseb|hbse|chse|upmsp|state\s+board)\b",
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


def check_cv_filename(filename: str | None) -> str:
    """The lower-case extension of an accepted CV file name, or a ValueError the user can act on."""
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".doc":
        raise ValueError(".doc files (Word 97-2003) are not supported. Save the CV as .docx or PDF and upload it again.")
    if suffix not in (".pdf", ".docx"):
        raise ValueError("Upload your CV as a PDF or .docx file.")
    return suffix


def extract_cv_text(path: str, filename: str) -> str:
    """Text of a PDF or .docx CV. The content must match the extension (checked by its first bytes)."""
    suffix = check_cv_filename(filename)
    with open(path, "rb") as stream:
        head = stream.read(8)
    if suffix == ".docx" and head.startswith(b"%PDF"):
        raise ValueError("This file is a PDF but is named .docx. Rename it to .pdf, or upload the original .docx.")
    if suffix == ".pdf" and head.startswith(b"PK\x03\x04"):
        raise ValueError("This file is a Word .docx but is named .pdf. Rename it to .docx, or upload a PDF.")
    return extract_docx_text(path) if suffix == ".docx" else extract_pdf_text(path)


# .docx is a zip of XML parts. python-docx's Document() reads every part with an uncounted
# ZipFile.read, so the parts a CV needs are read here with a byte budget and parsed with a
# parser that resolves no entities, loads no DTD and makes no network requests.
MAX_DOCX_UNPACKED_BYTES = 50 * 1024 * 1024  # declared total, checked before anything is unpacked
MAX_DOCX_ENTRIES = 1000
MAX_DOCX_COMPRESSION_RATIO = 100  # per entry, for entries declared at 1 MB or more
MAX_DOCX_READ_BYTES = 20 * 1024 * 1024  # actual bytes unpacked across the parts read
_RATIO_CHECK_MIN_BYTES = 1024 * 1024
DOCX_XML_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_UNREADABLE = "Cannot read this .docx. Save it again from Word, or upload a PDF."
_TOO_LARGE = "This .docx is too large when unpacked. Save a smaller copy, or upload a PDF."
# Skipped inside a paragraph: text boxes (read as their own blocks), Word's duplicate fallback
# copy of a shape, and deleted tracked changes.
_SKIP_IN_PARAGRAPH = {_W + "txbxContent", _MC + "Fallback", _W + "del"}


def _read_part(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """A part's bytes, counting what is actually unpacked rather than trusting the zip header."""
    chunks, total = [], 0
    with archive.open(name) as stream:
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError(_TOO_LARGE)
            chunks.append(chunk)
    return b"".join(chunks)


def _check_archive(archive: zipfile.ZipFile) -> None:
    entries = archive.infolist()
    if len(entries) > MAX_DOCX_ENTRIES:
        raise ValueError(f"Cannot read this .docx: it has too many parts (over {MAX_DOCX_ENTRIES:,}).")
    if sum(entry.file_size for entry in entries) > MAX_DOCX_UNPACKED_BYTES:
        raise ValueError(_TOO_LARGE)
    for entry in entries:
        if entry.file_size >= _RATIO_CHECK_MIN_BYTES and entry.file_size > MAX_DOCX_COMPRESSION_RATIO * max(entry.compress_size, 1):
            raise ValueError(_TOO_LARGE)
    names = set(archive.namelist())
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise ValueError("Cannot read this .docx: it is not a Word document. Save it again from Word, or upload a PDF.")


def _parse_part(data: bytes):
    # Word never writes a DOCTYPE; one here can only be an entity-expansion or XXE attempt.
    if re.search(rb"<!DOCTYPE", data[:4096], re.IGNORECASE):
        raise ValueError("Cannot read this .docx: it contains an XML DOCTYPE, which Word never writes.")
    try:
        root = etree.fromstring(data, DOCX_XML_PARSER)
    except etree.XMLSyntaxError as error:
        raise ValueError(_UNREADABLE) from error
    if root.getroottree().docinfo.doctype:
        raise ValueError("Cannot read this .docx: it contains an XML DOCTYPE, which Word never writes.")
    return root


def _paragraph_text(paragraph, boxes: list) -> str:
    parts: list[str] = []

    def walk(element) -> None:
        for child in element:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag == _W + "txbxContent":
                boxes.append(child)
            elif tag in _SKIP_IN_PARAGRAPH:
                continue
            elif tag == _W + "t":
                parts.append(child.text or "")
            elif tag == _W + "tab":
                parts.append("\t")
            elif tag in (_W + "br", _W + "cr"):
                parts.append("\n")
            else:
                walk(child)

    walk(paragraph)
    return "".join(parts)


def _blocks(container, lines: list[str]) -> None:
    """Paragraphs and tables in document order; tables row by row, nested tables in place."""
    for child in container:
        tag = child.tag
        if tag == _W + "p":
            boxes: list = []
            lines.append(_paragraph_text(child, boxes))
            for box in boxes:
                _blocks(box, lines)
        elif tag == _W + "tbl":
            for row in _unwrapped(child, _W + "tr"):
                for cell in _unwrapped(row, _W + "tc"):
                    merge = cell.find(f"{_W}tcPr/{_W}vMerge")
                    if merge is not None and merge.get(_W + "val") != "restart":
                        continue  # continuation of a vertically merged cell, already read
                    _blocks(cell, lines)
        elif tag in (_W + "sdt", _W + "customXml"):
            _blocks(_content(child), lines)


def _content(wrapper):
    """The children of a content control (w:sdt) or custom XML wrapper."""
    content = wrapper.find(_W + "sdtContent") if wrapper.tag == _W + "sdt" else wrapper
    return content if content is not None else []


def _unwrapped(parent, tag: str):
    """Direct `tag` children of a table or row, including those wrapped in content controls."""
    for child in parent:
        if child.tag == tag:
            yield child
        elif child.tag in (_W + "sdt", _W + "customXml"):
            yield from _unwrapped(_content(child), tag)


def _header_footer_lines(archive, document, kind: str, budget: list[int]) -> list[str]:
    """Header or footer text over all sections, each distinct non-empty part once."""
    names = set(archive.namelist())
    rels_name = "word/_rels/document.xml.rels"
    if rels_name not in names:
        return []
    targets = {}
    for rel in _parse_part(_read_budgeted(archive, rels_name, budget)).iter(_RELS):
        if rel.get("TargetMode") != "External":
            target = rel.get("Target", "")
            targets[rel.get("Id")] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("word", target))
    seen_parts: set[str] = set()
    seen_text: set[tuple[str, ...]] = set()
    found: list[str] = []
    for reference in document.iter(f"{_W}{kind}Reference"):
        part = targets.get(reference.get(_R_ID))
        if not part or part in seen_parts or part not in names:
            continue
        seen_parts.add(part)
        part_lines: list[str] = []
        _blocks(_parse_part(_read_budgeted(archive, part, budget)), part_lines)
        text = tuple(line for line in part_lines if line.strip())
        if text and text not in seen_text:
            seen_text.add(text)
            found.extend(text)
    return found


def _read_budgeted(archive, name: str, budget: list[int]) -> bytes:
    data = _read_part(archive, name, budget[0])
    budget[0] -= len(data)
    return data


def extract_docx_text(path: str) -> str:
    if Path(path).stat().st_size > MAX_PDF_BYTES:
        raise ValueError("Resume .docx exceeds 10 MB. Upload a smaller file.")
    try:
        with zipfile.ZipFile(path) as archive:
            _check_archive(archive)
            budget = [MAX_DOCX_READ_BYTES]
            document = _parse_part(_read_budgeted(archive, "word/document.xml", budget))
            body = document.find(_W + "body")
            lines: list[str] = []
            if body is not None:
                _blocks(body, lines)
            header = _header_footer_lines(archive, document, "header", budget)
            footer = _header_footer_lines(archive, document, "footer", budget)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, NotImplementedError) as error:
        # BadZipFile also covers a CRC mismatch, e.g. a part whose size header lies.
        raise ValueError(_UNREADABLE) from error
    text = "\n".join(header + lines + footer)
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Resume text exceeds 200,000 characters. Upload a shorter resume.")
    if not text.strip():
        raise ValueError("No readable text in this .docx. Add the CV text, or upload a text-based PDF.")
    return text


def _heading(line: str) -> tuple[str | None, str]:
    label, separator, rest = line.partition(":")
    key = label.strip().casefold()
    section = _HEADINGS.get(key)
    if section is None:
        # Combined headings such as "ACHIEVEMENTS & CERTIFICATIONS".
        parts = [part for part in re.split(r"\s*(?:&|\band\b|/|,|\+)\s*", key) if part]
        mapped = [_HEADINGS.get(part) for part in parts]
        if len(parts) > 1 and all(mapped):
            section = next((value for value in mapped if value != "other"), "other")
    return section, rest.strip() if separator else ""


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


_OPEN, _CLOSE, _LIST_SEPARATORS = "([{", ")]}", ",;|•"
_PROFICIENCY = {"basic", "beginner", "intermediate", "advanced", "proficient", "expert", "familiar", "fluent", "native"}


def _split_top_level(value: str) -> list[str]:
    """Split a skills list on separators outside brackets."""
    parts, current, depth = [], "", 0
    for char in value:
        if char in _OPEN:
            depth += 1
        elif char in _CLOSE:
            depth = max(0, depth - 1)
        if depth == 0 and char in _LIST_SEPARATORS:
            parts.append(current)
            current = ""
        else:
            current += char
    return parts + [current]


def _expand_group(item: str) -> list[str]:
    """"GCP (BigQuery, Looker Studio)" becomes GCP, BigQuery and Looker Studio."""
    start = next((index for index, char in enumerate(item) if char in _OPEN), None)
    if start is None:
        return [item]
    depth, end = 0, len(item)
    for index in range(start, len(item)):
        if item[index] in _OPEN:
            depth += 1
        elif item[index] in _CLOSE:
            depth -= 1
            if depth == 0:
                end = index
                break
    inner = [part for piece in _split_top_level(item[start + 1:end]) for part in _expand_group(piece)]
    return [item[:start], *inner, *_expand_group(item[end + 1:])]


def _canonical_skills(item: str) -> list[str]:
    """One vocabulary name per skill: "React.js" is React, "Git/GitHub" is Git and GitHub."""
    name = canonical_skill(item)
    if name != item.strip():
        return [name]
    return split_composite(item) or [name]


def _explicit_skills(lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        value = line.split(":", 1)[-1]
        for group in _split_top_level(value):
            for item in _expand_group(group):
                # Never emit an unbalanced or stray bracket.
                item = re.sub(r"[()\[\]{}]", " ", item)
                item = " ".join(item.split()).strip(" \t-–")
                if (item and item.casefold() not in _PROFICIENCY and len(item) <= 60
                        and len(item.split()) <= 6 and not re.search(r"[.!?]$", item)):
                    result.append(item)
    return result


_BULLET = re.compile(r"^(?:[•●▪◦‣∙·○■□➢➤►✓✔]|[-–—*](?=\s))\s*")
_ONGOING = re.compile(r"\b(?:present|ongoing|current|pursuing)\b", re.IGNORECASE)


def _years(line: str) -> list[int]:
    # "2021-25" ends in 2025.
    line = re.sub(r"\b((?:19|20)\d{2})\s*[-–]\s*(\d{2})\b(?!\d)", lambda m: f"{m[1]} - {m[1][:2]}{m[2]}", line)
    return [int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", line)]


_MONTH_YEAR = r"(?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2}"
_DATE_LINE = re.compile(rf"^{_MONTH_YEAR}(?:\s*(?:-|–|—|to)\s*(?:{_MONTH_YEAR}|present|current|ongoing|now))?$", re.IGNORECASE)


def _merge_date_lines(lines: list[str]) -> list[str]:
    """A date-only line ("Jul 2025 – Dec 2025") belongs to the entry above it."""
    merged: list[str] = []
    for line in lines:
        if merged and _DATE_LINE.match(line):
            merged[-1] = f"{merged[-1]} ({line})"
        else:
            merged.append(line)
    return merged


def _education_blocks(lines: list[str]) -> list[dict]:
    """Group each Education line with the nearest qualification heading above it.

    Dates often sit on their own line below the degree ("Aug 2021 – Jul 2025"),
    and Indian CVs list Class X/XII (school) results in the same section.
    """
    blocks: list[dict] = []
    for line in lines:
        kind = "degree" if _DEGREE.search(line) else "school" if _SCHOOL.search(line) else None
        if kind is None and blocks:
            blocks[-1]["lines"].append(line)
        elif blocks and blocks[-1]["kind"] == "unknown" and kind == "degree" and len(blocks) == 1:
            # Dates written above the degree belong to it.
            blocks[-1].update(kind=kind, lines=blocks[-1]["lines"] + [line])
        else:
            blocks.append({"kind": kind or "unknown", "lines": [line]})
    return blocks


def _graduation_year(education: list[str]) -> int | None:
    """The single degree's end year; school blocks never count."""
    blocks = _education_blocks(education)
    degrees = [block for block in blocks if block["kind"] == "degree"]
    if not degrees:
        if any(block["kind"] == "school" for block in blocks):
            return None
        degrees = blocks
    if len(degrees) != 1:
        return None
    lines = degrees[0]["lines"]
    if any(_ONGOING.search(line) for line in lines):
        return None
    years = [year for line in lines for year in _years(line)]
    return max(years) if years else None


def parse_profile_from_text(text: str) -> CandidateProfile:
    if not text.strip():
        raise ValueError("Resume has no readable text. Paste text or upload a text-based PDF.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Resume text exceeds 200,000 characters. Use a shorter resume.")
    # Leading list markers (•, ●, ▪, ◦, –, -, * and similar) are formatting, not content.
    lines = [_BULLET.sub("", line.strip()) for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if line]
    sections: dict[str, list[str]] = {key: [] for key in set(_HEADINGS.values())}
    section = "summary"
    for line in lines:
        heading, value = _heading(line)
        if heading in ("skills", "other") and value and section == "skills":
            # A sub-label inside Skills ("Languages: Python, C++") is not a new section.
            sections[section].append(value)
        elif heading:
            section = heading
            if value:
                sections[section].append(value)
        else:
            sections[section].append(line)

    education = sections["education"] or [line for line in lines if _DEGREE.search(line)]
    degrees = [match.group(0).strip().rstrip(".") for line in education if (match := _DEGREE.search(line))]
    degree = degrees[0] if degrees else None
    graduation_year = _graduation_year(education)
    warnings = []
    if graduation_year is None:
        warnings.append("Graduation year is missing or ambiguous; please confirm it.")
    if len(degrees) > 1:
        warnings.append("Multiple qualifications found; confirm the primary degree and graduation year.")
    explicit = [name for skill in _explicit_skills(sections["skills"]) for name in _canonical_skills(skill)]
    skills_by_key = {skill.casefold(): skill for skill in explicit}
    skill_text = "\n".join(value for section_lines in sections.values() for value in section_lines)
    skills_by_key.update({skill.casefold(): skill for skill in extract_skills(skill_text)})
    internships, experience = _merge_date_lines(sections["internships"]), []
    for line in _merge_date_lines(sections["experience"]):
        (internships if re.search(r"\bintern(?:ship)?\b", line, re.IGNORECASE) else experience).append(line)
    # A research internship stays under research and is also an internship.
    internships += [line for line in _merge_date_lines(sections["research"])
                    if re.search(r"\bintern(?:ship)?s?\b", line, re.IGNORECASE) and line not in internships]
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
