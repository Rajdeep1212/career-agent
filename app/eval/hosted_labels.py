"""Hosted labels for a batch, and the agreement numbers against the owner's gold labels (Q4, docs/M2_PLAN.md section 5).

What a hosted labeller is sent: rubric r1, the owner's hand-written target profile (without its heading line), and
the blind listing fields of each job. Nothing else. `build_prompts()` refuses to build if any string of the
CV-derived profile, or the name in it, appears in a prompt: hosted models never see the CV or text extracted from it.

Each job is labelled twice, once in the batch's order and once in the reverse order, so an effect of position
shows up as disagreement between the two. A reply is one JSON line per job with the reason before the grade.

The metrics are plain functions over lists of grades 0-3. Hosted labels are for agreement only; the owner's labels
stay the reference.
"""
import json
import math
import textwrap
from dataclasses import dataclass
from pathlib import Path

from app.eval.label_page import BLIND_FIELDS, RUBRIC
from app.mcp.tools import _cv_strings

PROMPT_FIELDS = tuple(name for name in BLIND_FIELDS if name != "application_url")     # a link is no use to a labeller
ORDERS = ("forward", "reverse")
MAX_DESCRIPTION = 6000
GRADES = (0, 1, 2, 3)


class PrivacyError(RuntimeError):
    """A prompt would have carried text from the CV-derived profile; nothing was built."""


@dataclass(frozen=True)
class Prompt:
    order: str
    chunk: int
    item_ids: list[str]
    text: str

    @property
    def name(self) -> str:
        return f"{self.order}_{self.chunk:02d}"


def target_text(path: Path) -> str:
    """The target profile as sent: every line except top-level headings, which only carry the owner's name."""
    lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if not line.startswith("# ")]
    return "\n".join(lines).strip()


def _job_block(item: dict) -> str:
    lines = [f"### item_id: {item['item_id']}"]
    for name in PROMPT_FIELDS:
        if name != "description":
            lines.append(f"{name}: {item.get(name) if item.get(name) not in (None, '') else 'not stated'}")
    description = str(item.get("description") or "").strip() or "(the listing has no description)"
    if len(description) > MAX_DESCRIPTION:
        description = description[:MAX_DESCRIPTION] + f" [listing cut at {MAX_DESCRIPTION} characters]"
    wrapped = "\n".join(textwrap.fill(line, width=160, break_long_words=True) if line.strip() else ""
                        for line in description.splitlines())
    return "\n".join(lines) + "\ndescription:\n" + wrapped


def _prompt_text(target: str, items: list[dict]) -> str:
    grades = "\n".join(f"{grade}: {meaning}" for grade, meaning in RUBRIC["grades"].items())
    jobs = "\n\n".join(_job_block(item) for item in items)
    return f"""You are labelling job listings for one candidate. Use only what is written below.

## The candidate's target profile (written by the candidate)

{target}

## Rubric {RUBRIC['version']}: "{RUBRIC['question']}" answered from the candidate's point of view

{grades}

## How to answer

Judge each job on its own, in the order given. For each job, first write one or two sentences of reason that
quote or point to what the listing says, then give the grade. Do not look anything up and do not use any tool
other than reading this file.

Reply with exactly one JSON object per line, one line per job, {len(items)} lines, and nothing else:
{{"item_id": "...", "reason": "...", "grade": 0}}

## The jobs ({len(items)})

{jobs}
"""


def load_items(batch: Path) -> list[dict]:
    rows = [json.loads(line) for line in (Path(batch) / "blind.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return [{"item_id": row["item_id"], **{name: row.get(name) for name in PROMPT_FIELDS}} for row in rows]


def build_prompts(batch: Path, target_profile: Path, *, chunk_size: int = 14) -> list[Prompt]:
    """The prompts for both orders. Raises PrivacyError, without repeating the text, if one holds a CV string."""
    items, target = load_items(batch), target_text(target_profile)
    prompts = []
    for order in ORDERS:
        ordered = items if order == "forward" else items[::-1]
        for number, start in enumerate(range(0, len(ordered), chunk_size), start=1):
            chunk = ordered[start:start + chunk_size]
            prompts.append(Prompt(order, number, [item["item_id"] for item in chunk], _prompt_text(target, chunk)))
    secrets = _cv_strings()
    for prompt in prompts:
        folded = prompt.text.casefold()
        if any(secret in folded for secret in secrets):
            raise PrivacyError(f"Prompt {prompt.name} contains text from the CV-derived profile (or the name in it); "
                               "no prompt was built. Remove it from the target profile or the listing first.")
    return prompts


def parse_reply(text: str, item_ids: list[str]) -> list[dict]:
    """One checked record per job, in the order asked; any problem refuses the whole reply."""
    found: dict[str, dict] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"line {number} is not JSON") from exc
        if not isinstance(record, dict) or list(record) != ["item_id", "reason", "grade"]:
            raise ValueError(f"line {number} must have item_id, reason, grade in that order")
        if record["item_id"] not in item_ids or record["item_id"] in found:
            raise ValueError(f"line {number}: unknown or repeated item_id")
        if not isinstance(record["reason"], str) or not record["reason"].strip():
            raise ValueError(f"line {number}: the reason is empty")
        if isinstance(record["grade"], bool) or record["grade"] not in GRADES:
            raise ValueError(f"line {number}: the grade must be 0, 1, 2 or 3")
        found[record["item_id"]] = {"item_id": record["item_id"], "reason": record["reason"].strip(), "grade": record["grade"]}
    missing = [item for item in item_ids if item not in found]
    if missing:
        raise ValueError(f"{len(missing)} jobs have no line: {', '.join(missing[:5])}")
    return [found[item] for item in item_ids]


def exact_agreement(first: list[int], second: list[int]) -> float:
    return sum(a == b for a, b in zip(first, second, strict=True)) / len(first)


def confusion(first: list[int], second: list[int]) -> list[list[int]]:
    """matrix[a][b]: jobs graded a by the first labeller and b by the second."""
    matrix = [[0] * len(GRADES) for _ in GRADES]
    for a, b in zip(first, second, strict=True):
        matrix[a][b] += 1
    return matrix


def quadratic_weighted_kappa(first: list[int], second: list[int]) -> float:
    """Cohen's kappa with quadratic weights; nan when a labeller used a single grade for everything."""
    matrix, total, top = confusion(first, second), len(first), (len(GRADES) - 1) ** 2
    rows = [sum(row) for row in matrix]
    columns = [sum(matrix[a][b] for a in GRADES) for b in GRADES]
    observed = sum((a - b) ** 2 / top * matrix[a][b] for a in GRADES for b in GRADES)
    expected = sum((a - b) ** 2 / top * rows[a] * columns[b] / total for a in GRADES for b in GRADES)
    return math.nan if expected == 0 else 1 - observed / expected


def _dcg(grades: list[int], k: int) -> float:
    return sum((2 ** grade - 1) / math.log2(position + 1) for position, grade in enumerate(grades[:k], start=1))


def ndcg_at_k(ranked: list[int], k: int, pool: list[int] | None = None) -> float:
    """NDCG@k with gain 2^grade - 1. `ranked` holds the grades in ranked order; the ideal is the best order of
    `pool` (every judged job that could have been ranked), or of `ranked` itself when no pool is given."""
    ideal = _dcg(sorted(pool if pool is not None else ranked, reverse=True), k)
    return 0.0 if ideal == 0 else _dcg(ranked, k) / ideal
