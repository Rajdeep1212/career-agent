"""Write the apply list for a labelled batch: every job the owner graded 3 or 2.

Usage (from the repository root, in the app's Python environment):
    python scripts/export_apply_list.py --batch data/eval/batches/gold-20261001-r1

Reads the batch key (jobs.jsonl), the owner's gold labels and the saved hosted labels; sends nothing anywhere.
Writes data/eval/apply_list_<batch id>.csv (data/ is git-ignored) with the owner's grade, the hosted grade and its
one-line reason (batch order), title, company, location, link and posted date, sorted by the owner's grade, then
the hosted grade, then the newest posting. Prints the grade-3 jobs as a table.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402

COLUMNS = ["my_grade", "hosted_grade", "hosted_reason", "title", "company", "location", "link", "posted_date"]


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the apply list (grades 3 and 2) for a labelled batch.")
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--labels", type=Path, default=Path(settings.data_dir) / "eval" / "labels")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    batch_id = args.batch.name
    jobs = {row["item_id"]: row["job"] for row in _lines(args.batch / "jobs.jsonl")}
    gold = {row["item_id"]: row["label"] for row in _lines(args.labels / f"{batch_id}.jsonl")}        # the latest line counts
    hosted = {row["item_id"]: row for row in _lines(args.labels / f"{batch_id}.hosted.jsonl") if row["order"] == "forward"}
    rows = []
    for item, grade in gold.items():
        if grade < 2:
            continue
        job, other = jobs[item], hosted.get(item, {})
        rows.append({"my_grade": grade, "hosted_grade": other.get("label", ""), "hosted_reason": other.get("reason", ""),
                     "title": job.get("title"), "company": job.get("company"), "location": job.get("location"),
                     "link": job.get("application_url"), "posted_date": (job.get("posted_date") or "")[:10]})
    # Newest first, then a stable sort by the two grades keeps that order within a tie.
    rows.sort(key=lambda row: row["posted_date"], reverse=True)
    rows.sort(key=lambda row: (-row["my_grade"], -(row["hosted_grade"] if row["hosted_grade"] != "" else -1)))
    out = args.out or Path(settings.data_dir) / "eval" / f"apply_list_{batch_id}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:          # the BOM lets Excel read the text correctly
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print("| Title | Company | Location | Posted | Hosted grade |")
    print("|---|---|---|---|---|")
    for row in rows:
        if row["my_grade"] == 3:
            print(f"| {row['title']} | {row['company']} | {row['location']} | {row['posted_date']} | {row['hosted_grade']} |")
    print(f"{len(rows)} jobs written to {out} ({len([row for row in rows if row['my_grade'] == 3])} at grade 3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
