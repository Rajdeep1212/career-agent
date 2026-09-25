"""Shared, versioned skill vocabulary used for both resumes and job posts.

A released vocabulary file is never edited. Changes go into a new
``app/core/skills/v{N}.json`` and ``VOCABULARY_VERSION`` is raised, so stored
feature snapshots can name the vocabulary they were computed with.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

VOCABULARY_VERSION = 1
_VOCABULARY_DIR = Path(__file__).resolve().parents[1] / "core" / "skills"


@lru_cache(maxsize=None)
def load_vocabulary(version: int = VOCABULARY_VERSION) -> dict:
    return json.loads((_VOCABULARY_DIR / f"v{version}.json").read_text(encoding="utf-8"))


def contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", text, re.IGNORECASE))


def _contains_strict(text: str, term: str) -> bool:
    # Case-sensitive, and not joined to &, /, - or an apostrophe ("R&D" is not R).
    return bool(re.search(r"(?<![\w&/.'’-])" + re.escape(term) + r"(?![\w&/'’-])(?!\.\w)", text))


def _patterns(entry: dict) -> list[tuple[str, bool]]:
    """(term, strict) pairs that identify this skill in free text."""
    mode = entry.get("match", "normal")
    terms = [] if mode == "alias_only" else [(entry["name"], mode == "strict")]
    return terms + [(alias, False) for alias in entry.get("aliases", [])]


@lru_cache(maxsize=None)
def _index(version: int = VOCABULARY_VERSION) -> tuple[dict[str, list[str]], dict[str, str]]:
    categories: dict[str, list[str]] = {}
    canonical: dict[str, str] = {}
    for entry in load_vocabulary(version)["skills"]:
        categories.setdefault(entry["category"], []).append(entry["name"])
        for term in [entry["name"], *entry.get("aliases", [])]:
            canonical[term.casefold()] = entry["name"]
    return categories, canonical


SKILL_CATEGORIES = _index()[0]
KNOWN_SKILLS = [name for names in SKILL_CATEGORIES.values() for name in names]


def canonical_skill(value: str) -> str:
    """The vocabulary's name for a known skill or alias; other skills are kept as written."""
    return _index()[1].get(value.strip().casefold(), value.strip())


def _term_pattern(term: str, strict: bool) -> re.Pattern:
    if strict:
        return re.compile(r"(?<![\w&/.'’-])" + re.escape(term) + r"(?![\w&/'’-])(?!\.\w)")
    return re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", re.IGNORECASE)


def split_composite(item: str) -> list[str] | None:
    """Known skills making up a whole list item, e.g. "Git/GitHub" or "LLM Fine-tuning".

    Returns None when any word is not a known skill, so unknown skills and
    known compound names such as "CI/CD" are left as written.
    """
    covered = [False] * len(item)
    found: list[tuple[int, str]] = []
    for entry in load_vocabulary()["skills"]:
        for term, strict in _patterns(entry):
            for match in _term_pattern(term, strict).finditer(item):
                found.append((match.start(), entry["name"]))
                covered[match.start():match.end()] = [True] * (match.end() - match.start())
    leftover = "".join(" " if covered[index] else char for index, char in enumerate(item))
    if not found or re.sub(r"\b(?:and|with)\b|[\s/&+,-]", "", leftover, flags=re.IGNORECASE):
        return None
    return list(dict.fromkeys(name for _, name in sorted(found)))


def extract_skills(text: str) -> list[str]:
    """Vocabulary matches with token boundaries, sorted by canonical name."""
    found = set()
    for entry in load_vocabulary()["skills"]:
        for term, strict in _patterns(entry):
            if (_contains_strict(text, term) if strict else contains_phrase(text, term)):
                found.add(entry["name"])
                break
    return sorted(found, key=str.casefold)
