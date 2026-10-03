"""Quick-add match evaluation: 50 pairs of index jobs to label, and the scorer (docs/ROADMAP_QUEUE.md TRK2).

Usage (from the repository root, in the app's Python environment):
    python scripts/tracker_match_pairs.py build     # writes data/eval/tracker_pairs/pairs.csv (label column empty)
    python scripts/tracker_match_pairs.py score     # precision and recall once every pair is labelled

A pair is two different jobs from the Company Radar index, read-only and offline. "Likely" pairs are at the same
company with the same or a very similar title; "near misses" are at the same company with a somewhat similar title, or
have the same title at two companies. Which kind a pair is stays out of pairs.csv (it is in pairs_key.csv), so the
labels are not led by the sampler. Fill `label` with `same` (one job, listed twice) or `different`.

The scorer asks the quick-add matcher (app/tracker/matching.py) whether job A, pasted by hand, would be offered job B.
Two index jobs never share a URL, so this measures the title-and-company step; the URL step is covered by the recorded
URLs in tests/fixtures/tracker/. Until every pair is labelled it prints "pairs ready, not labelled" and no number.
"""
import argparse
import csv
import random
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.models.schemas import JobPosting  # noqa: E402
from app.services.job_identity import TITLE_SIMILARITY, company_key, title_key  # noqa: E402
from app.tracker import matching  # noqa: E402

SEED = 20261003
PAIRS = 50
NEAR_MISS_FLOOR = 0.60          # below this two titles are not worth a label
SAME_TITLE_CAP = 12             # jobs per title looked at across companies
OUT = Path(settings.data_dir) / "eval" / "tracker_pairs" / "pairs.csv"
COLUMNS = ["pair_id", "a_job_id", "a_company", "a_title", "a_location", "a_url", "b_job_id", "b_company", "b_title", "b_location",
           "b_url", "label"]
KEY_COLUMNS = ["pair_id", "kind", "title_similarity"]
LABELS = {"same": True, "different": False, "1": True, "0": False, "y": True, "n": False, "yes": True, "no": False}
Pair = tuple[str, str, float, str]              # (key of A, key of B, title similarity, group it was found in)


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    matcher = SequenceMatcher(None, a, b)
    if matcher.real_quick_ratio() < NEAR_MISS_FLOOR or matcher.quick_ratio() < NEAR_MISS_FLOOR:
        return 0.0
    return matcher.ratio()


def candidates(index: list[tuple[str, JobPosting]]) -> tuple[list[Pair], list[Pair]]:
    """(likely, near misses), each in a fixed order."""
    jobs = sorted(index, key=lambda entry: entry[0])
    by_company: dict[str, list[tuple[str, str]]] = {}
    by_title: dict[str, list[tuple[str, str]]] = {}
    for key, job in jobs:
        by_company.setdefault(company_key(job.company), []).append((key, title_key(job.title)))
        by_title.setdefault(title_key(job.title), []).append((key, company_key(job.company)))
    likely: list[Pair] = []
    near: list[Pair] = []
    for company, listed in sorted(by_company.items()):
        for i, (key_a, title_a) in enumerate(listed):
            for key_b, title_b in listed[i + 1:]:
                similarity = _similarity(title_a, title_b)
                if similarity >= TITLE_SIMILARITY:
                    likely.append((key_a, key_b, similarity, company))
                elif similarity >= NEAR_MISS_FLOOR:
                    near.append((key_a, key_b, similarity, company))
    for title, listed in sorted(by_title.items()):
        listed = listed[:SAME_TITLE_CAP]
        for i, (key_a, company_a) in enumerate(listed):
            near.extend((key_a, key_b, 1.0, f"title:{title}") for key_b, company_b in listed[i + 1:] if company_b != company_a)
    return likely, near


def _spread(pool: list[Pair], wanted: int, rng: random.Random) -> list[Pair]:
    """Up to `wanted` pairs, taken in turns from each company or title so that one large board does not fill the sample."""
    groups: dict[str, list[Pair]] = {}
    for pair in pool:
        groups.setdefault(pair[3], []).append(pair)
    queues = [groups[name] for name in sorted(groups)]
    for queue in queues:
        rng.shuffle(queue)
    rng.shuffle(queues)
    taken: list[Pair] = []
    while len(taken) < wanted and any(queues):
        for queue in queues:
            if queue and len(taken) < wanted:
                taken.append(queue.pop())
    return taken


def _has_labels(path: Path) -> bool:
    if not path.exists():
        return False
    with open(path, encoding="utf-8", newline="") as handle:
        return any((row.get("label") or "").strip() for row in csv.DictReader(handle))


def build(out: Path = OUT) -> dict:
    out = Path(out)
    if _has_labels(out):
        raise SystemExit(f"{out} already holds labels and is not overwritten. Move it away first.")
    index = matching.index_jobs()
    jobs = dict(index)
    likely, near = candidates(index)
    rng = random.Random(SEED)
    half = PAIRS // 2
    chosen_likely = _spread(likely, half, rng)
    chosen_near = _spread(near, PAIRS - len(chosen_likely), rng)
    if len(chosen_likely) + len(chosen_near) < PAIRS:          # too few near misses: top up with likely pairs
        rest = [pair for pair in likely if pair not in set(chosen_likely)]
        chosen_likely += _spread(rest, PAIRS - len(chosen_likely) - len(chosen_near), rng)
    chosen = [("likely", pair) for pair in chosen_likely] + [("near_miss", pair) for pair in chosen_near]
    rng.shuffle(chosen)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as pairs_file, \
            open(out.with_name("pairs_key.csv"), "w", encoding="utf-8", newline="") as key_file:
        writer, key_writer = csv.DictWriter(pairs_file, fieldnames=COLUMNS), csv.DictWriter(key_file, fieldnames=KEY_COLUMNS)
        writer.writeheader()
        key_writer.writeheader()
        for number, (kind, (key_a, key_b, similarity, _)) in enumerate(chosen, start=1):
            a, b = jobs[key_a], jobs[key_b]
            writer.writerow({"pair_id": number, "a_job_id": key_a, "a_company": a.company, "a_title": a.title, "a_location": a.location,
                             "a_url": a.application_url or "", "b_job_id": key_b, "b_company": b.company, "b_title": b.title,
                             "b_location": b.location, "b_url": b.application_url or "", "label": ""})
            key_writer.writerow({"pair_id": number, "kind": kind, "title_similarity": f"{similarity:.3f}"})
    return {"pairs": len(chosen), "likely": len(chosen_likely), "near_miss": len(chosen_near), "index_jobs": len(index),
            "likely_available": len(likely), "near_miss_available": len(near), "path": str(out)}


def _predicted(row: dict[str, str]) -> bool:
    """Would quick-adding job A by hand be offered job B (matched or probable)?"""
    b = JobPosting(company=row["b_company"], title=row["b_title"], location=row["b_location"])
    found = matching.match(row["a_url"], title=row["a_title"], company=row["a_company"], location=row["a_location"],
                           index=[(row["b_job_id"], b)])
    return found["status"] != "none"


def score(path: Path = OUT) -> dict:
    path = Path(path)
    if not path.exists():
        return {"message": "pairs not built"}
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    labels: list[bool | None] = []
    for line, row in enumerate(rows, start=2):
        text = (row.get("label") or "").strip().lower()
        if text and text not in LABELS:
            raise SystemExit(f"{path} line {line}: label must be 'same' or 'different', not {row['label']!r}.")
        labels.append(LABELS[text] if text else None)
    labelled = sum(label is not None for label in labels)
    if labelled < len(rows) or not rows:
        return {"pairs": len(rows), "labelled": labelled, "message": f"pairs ready, not labelled ({labelled} of {len(rows)} labelled)"}
    predicted = [_predicted(row) for row in rows]
    tp = sum(1 for guess, label in zip(predicted, labels) if guess and label)
    fp = sum(1 for guess, label in zip(predicted, labels) if guess and not label)
    fn = sum(1 for guess, label in zip(predicted, labels) if not guess and label)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    shown_p = f"precision {precision:.3f} ({tp}/{tp + fp})" if precision is not None else "precision n/a (nothing predicted)"
    shown_r = f"recall {recall:.3f} ({tp}/{tp + fn})" if recall is not None else "recall n/a (no pair labelled same)"
    return {"pairs": len(rows), "labelled": labelled, "true_positive": tp, "false_positive": fp, "false_negative": fn,
            "true_negative": len(rows) - tp - fp - fn, "precision": precision, "recall": recall,
            "message": f"{shown_p}, {shown_r} on {len(rows)} labelled pairs (matched or probable counts as predicted)"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quick-add match evaluation pairs.")
    parser.add_argument("command", choices=("build", "score"))
    parser.add_argument("--file", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.command == "build":
        report = build(args.file)
        print(", ".join(f"{key} {value}" for key, value in report.items()))
    print(score(args.file)["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
