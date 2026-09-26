"""Extract required experience separately from education and other durations.

experience_clauses() keeps the quoted listing text for each duration so that
eligibility decisions can cite their evidence, and marks soft wording
("preferred", "a plus", "nice to have") instead of treating it as a requirement.
"""
import re
from dataclasses import dataclass

_DURATION = re.compile(r"\b(?P<minimum>\d+(?:\.\d+)?)\s*(?:(?:-|to)\s*(?P<maximum>\d+(?:\.\d+)?)\s*|(?P<plus>\+)\s*)?(?:years?|yrs?)\b")
_SOFT = re.compile(r"\b(?:preferred|preferably|desirable|optional|nice to have|good to have|a plus|is a plus|bonus|ideally|not required|not mandatory)\b")
_CLAUSE_BREAK = re.compile(r"[.;\n!?]")
_CEILING = re.compile(r"(?:\bup\s*to|\bupto|\bmaximum(?:\s+of)?|\bmax\.?|\bless than|\bunder|\bnot more than)\s*$")


@dataclass(frozen=True)
class ExperienceClause:
    minimum: float
    maximum: float | None
    soft: bool
    quote: str


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = max((m.end() for m in _CLAUSE_BREAK.finditer(text, 0, start)), default=0)
    right = _CLAUSE_BREAK.search(text, end)
    return left, right.start() if right else len(text)


def quote_around(text: str, start: int, end: int, limit: int = 140) -> str:
    """The clause containing text[start:end], trimmed to about `limit` characters."""
    left, right = _clause_bounds(text, start, end)
    clause = " ".join(text[left:right].split())
    if len(clause) <= limit:
        return clause
    focus = " ".join(text[start:end].split())
    at = clause.find(focus)
    begin = max(0, min(at - (limit - len(focus)) // 2, len(clause) - limit)) if at >= 0 else 0
    return ("…" if begin else "") + clause[begin:begin + limit].strip() + ("…" if begin + limit < len(clause) else "")


def experience_clauses(text: str) -> list[ExperienceClause]:
    # Replacements keep string length, so match positions index the original text.
    low = text.lower().replace("–", "-").replace("—", "-")
    clauses = []
    for match in _DURATION.finditer(low):
        before = low[max(0, match.start() - 70):match.start()]
        after = low[match.end():match.end() + 100]
        # A nearby duration is not itself an experience requirement.
        if re.match(r"\s*(?:[- ]?(?:degree|diploma|course|program|contract|tenure)|ago|old|warranty)\b", after):
            continue
        required_prefix = re.search(r"(?:minimum(?: of)?|at least|requires?|required|experience\s*[:=]?)\s*$", before)
        experience_suffix = re.match(r"\s*(?:of\s+)?(?:(?:relevant|professional|work|working|industry|overall|hands.on|prior)\s+)*(?:experience|working)\b", after)
        compact = not before.strip() and not after.strip(" .;\n")
        experience_range = match.group("maximum") is not None
        if not (required_prefix or experience_suffix or compact or experience_range or match.group("plus")):
            continue
        minimum = float(match.group("minimum"))
        maximum = float(match.group("maximum")) if match.group("maximum") else None
        if maximum is None and not match.group("plus") and _CEILING.search(before):
            # "Up to 2 years" is a ceiling, not a minimum.
            minimum, maximum = 0.0, minimum
        if minimum > 70 or maximum is not None and (maximum > 70 or maximum < minimum):
            continue
        left, right = _clause_bounds(low, match.start(), match.end())
        soft = bool(_SOFT.search(low[left:right]))
        clauses.append(ExperienceClause(minimum, maximum, soft, quote_around(text, match.start(), match.end())))
    return clauses


def graduation_year_clauses(text: str) -> list[tuple[list[int], str]]:
    """(years, quoted sentence) for sentences that restrict graduation batches."""
    found = []
    for sentence in re.split(r'[.!;\n]', text):
        if re.search(r'graduat|batch|cohort|pass(?:ed)?[ -]?out', sentence, re.I):
            years = sorted({int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', sentence)})
            if years:
                found.append((years, " ".join(sentence.split())))
    return found


def extract_requirements(text: str):
    """(minimum, maximum, graduation years) from firm requirements; soft wording is ignored."""
    firm = [(clause.minimum, clause.maximum) for clause in experience_clauses(text) if not clause.soft]
    minimum, maximum = max(firm, key=lambda pair: (pair[0], pair[1] if pair[1] is not None else float('inf')), default=(0, None))
    years = sorted({year for clause_years, _ in graduation_year_clauses(text) for year in clause_years})
    return minimum, maximum, years
