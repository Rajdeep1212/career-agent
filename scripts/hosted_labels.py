"""Hosted labels and the agreement report for one batch (docs/ROADMAP_QUEUE.md Q4).

Usage (from the repository root, in the app's Python environment):
    python scripts/hosted_labels.py prompts --batch data/eval/batches/<batch id>
    python scripts/hosted_labels.py ingest  --batch data/eval/batches/<batch id> --labeller subagent:<model>
    python scripts/hosted_labels.py report  --batch data/eval/batches/<batch id>

`prompts` writes one prompt file per chunk and order under data/eval/hosted/<batch id>/. Each holds the rubric,
the target profile and blind listing fields only; the build stops if any CV text is found in one. This script
calls no model: a labeller reads a prompt file and its reply is saved beside it as <name>.reply.txt.
`ingest` checks every reply and writes data/eval/labels/<batch id>.hosted.jsonl. `report` writes
docs/eval/agreement_<batch id>.md. See app/eval/hosted_labels.py.
"""
import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.eval import hosted_labels as hosted  # noqa: E402
from app.eval.label_page import RUBRIC, label_quality  # noqa: E402

EVAL = Path(settings.data_dir) / "eval"


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompts(batch: Path, target: Path) -> int:
    folder = EVAL / "hosted" / batch.name
    built = hosted.build_prompts(batch, target)
    folder.mkdir(parents=True, exist_ok=True)
    for prompt in built:
        (folder / f"{prompt.name}.prompt.md").write_text(prompt.text, encoding="utf-8", newline="\n")
    manifest = {"batch_id": batch.name, "target_profile_sha256": _sha(target), "blind_sha256": _sha(batch / "blind.jsonl"),
                "rubric_version": RUBRIC["version"],
                "prompts": [{"name": prompt.name, "order": prompt.order, "chunk": prompt.chunk, "item_ids": prompt.item_ids}
                            for prompt in built]}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{len(built)} prompts in {folder} ({sum(len(prompt.text) for prompt in built)} characters); no CV text found in any")
    return 0


def ingest(batch: Path, labeller: str) -> int:
    folder = EVAL / "hosted" / batch.name
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    records, stamp = [], datetime.now(timezone.utc).isoformat(timespec="seconds")
    for entry in manifest["prompts"]:
        reply = folder / f"{entry['name']}.reply.txt"
        try:
            parsed = hosted.parse_reply(reply.read_text(encoding="utf-8"), entry["item_ids"])
        except (OSError, ValueError) as exc:
            print(f"{entry['name']}: refused ({exc})")
            return 1
        records += [{"batch_id": batch.name, "item_id": item["item_id"], "label": item["grade"], "reason": item["reason"],
                     "scale": "graded_0_3", "rubric_version": manifest["rubric_version"], "labeller": labeller, "set": "batch",
                     "order": entry["order"], "chunk": entry["chunk"], "labelled_at": stamp,
                     "blind_sha256": manifest["blind_sha256"], "target_profile_sha256": manifest["target_profile_sha256"]}
                    for item in parsed]
    out = EVAL / "labels" / f"{batch.name}.hosted.jsonl"
    if out.exists():
        print(f"{out} already exists; it is not overwritten")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records), encoding="utf-8",
                   newline="\n")
    print(f"{len(records)} hosted labels written to {out}")
    return 0


def _number(value: float) -> str:
    return "undefined" if math.isnan(value) else f"{value:.3f}"


def _table(header: list[str], rows) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + [
        "| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in row) + " |" for row in rows] + [""]


def _matrix(first: list[int], second: list[int], name: str) -> list[str]:
    matrix = hosted.confusion(first, second)
    return _table([f"Rajdeep \\ {name}", "0", "1", "2", "3", "Total"],
                  [[grade, *matrix[grade], sum(matrix[grade])] for grade in hosted.GRADES]
                  + [["Total", *[sum(matrix[a][b] for a in hosted.GRADES) for b in hosted.GRADES], len(first)]])


def report(batch: Path, out: Path) -> int:
    jobs = _lines(batch / "jobs.jsonl")
    order = [job["item_id"] for job in jobs]
    gold_lines = _lines(EVAL / "labels" / f"{batch.name}.jsonl")
    quality = label_quality(gold_lines, len(order))
    if not quality["accepted"]:
        print(f"The gold labels are not scored. {quality['message']}")
        return 1
    gold = {line["item_id"]: line["label"] for line in gold_lines}
    host_lines = _lines(EVAL / "labels" / f"{batch.name}.hosted.jsonl")
    by_order = {name: {line["item_id"]: line for line in host_lines if line["order"] == name} for name in hosted.ORDERS}
    if set(gold) != set(order) or any(set(labels) != set(order) for labels in by_order.values()):
        print("the gold or hosted labels do not cover the batch exactly")
        return 1
    g = [gold[item] for item in order]
    forward = [by_order["forward"][item]["label"] for item in order]
    reverse = [by_order["reverse"][item]["label"] for item in order]
    consensus = [(a + b) // 2 for a, b in zip(forward, reverse)]      # the mean of the two orders, rounded down
    sampled = {job["item_id"] for job in jobs if all(entry["stage"] == "excluded_sample" for entry in job["surfaced_by"])}
    stamps = sorted(datetime.fromisoformat(line["labelled_at"]) for line in gold_lines)
    gaps = sorted((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
    labeller = host_lines[0]["labeller"]

    def spread(grades) -> list[int]:
        counts = Counter(grades)
        return [counts.get(grade, 0) for grade in hosted.GRADES]

    text = [f"# Label agreement for batch {batch.name} (Q4)", "",
            f"Generated by `python scripts/hosted_labels.py report` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. "
            f"Rubric {RUBRIC['version']} (\"{RUBRIC['question']}\", 0-3). {len(order)} jobs: {len(order) - len(sampled)} the searches "
            f"showed and {len(sampled)} seeded samples of jobs the eligibility check excluded.", "",
            f"Gold labels: Rajdeep, blind, in the local labelling page. Hosted labels: `{labeller}`, given the rubric, the target "
            "profile and the blind listing fields only (no CV text; `app/eval/hosted_labels.py` refuses a prompt that holds any), "
            "each job labelled twice, once in the batch order and once in reverse, reason written before the grade. Hosted labels are "
            "for agreement only.", "",
            "## Label distribution", ""]
    text += _table(["Labels", "0", "1", "2", "3", "Total"],
                   [["Rajdeep, all", *spread(g), len(g)],
                    ["Rajdeep, shown by a search", *spread(gold[i] for i in order if i not in sampled), len(order) - len(sampled)],
                    ["Rajdeep, seeded excluded sample", *spread(gold[i] for i in order if i in sampled), len(sampled)],
                    ["Hosted, batch order", *spread(forward), len(forward)], ["Hosted, reverse order", *spread(reverse), len(reverse)],
                    ["Hosted, excluded sample (batch order)", *spread(by_order["forward"][i]["label"] for i in order if i in sampled),
                     len(sampled)]])
    text += [f"The gold labels were recorded in {(stamps[-1] - stamps[0]).total_seconds():.0f} seconds in total, a median of "
             f"{gaps[len(gaps) // 2]:.0f} seconds between labels, in page order, with no label changed afterwards.", "",
             "## Agreement", ""]
    pairs = [("Rajdeep vs hosted, batch order", g, forward), ("Rajdeep vs hosted, reverse order", g, reverse),
             ("Rajdeep vs hosted, both orders combined (mean, rounded down)", g, consensus),
             ("Hosted batch order vs hosted reverse order", forward, reverse)]
    text += _table(["Pair", "Exact match", "Quadratic weighted kappa", "Differ by 2 or more"],
                   [[name, f"{hosted.exact_agreement(a, b):.3f} ({sum(x == y for x, y in zip(a, b))} of {len(a)})",
                     _number(hosted.quadratic_weighted_kappa(a, b)), sum(abs(x - y) >= 2 for x, y in zip(a, b))] for name, a, b in pairs])
    text += ["## Confusion matrix: Rajdeep (rows) against hosted, batch order (columns)", ""] + _matrix(g, forward, "hosted")
    text += ["## Confusion matrix: Rajdeep (rows) against hosted, reverse order (columns)", ""] + _matrix(g, reverse, "hosted")

    text += ["## NDCG@10 of the current ranker against the gold labels", "",
             "The baseline SEM1 must beat. Ranking: the order the search showed its results in. Gain 2^grade - 1, log2 discount. "
             "\"Shown\" takes the ideal order from the jobs the search showed; \"judged pool\" also counts the sampled excluded "
             "jobs of that search as jobs the ranker could have shown. Claim level L0: a measurement on one labelled batch.", ""]
    rows = []
    for search in ("A", "B"):
        shown = sorted((entry["rank"], job["item_id"]) for job in jobs for entry in job["surfaced_by"]
                       if entry["search"] == search and entry["stage"] != "excluded_sample")
        pool = [job["item_id"] for job in jobs if any(entry["search"] == search for entry in job["surfaced_by"])]
        for name, labels in (("Rajdeep", gold), ("hosted, batch order", {i: by_order["forward"][i]["label"] for i in order})):
            ranked = [labels[item] for _, item in shown]
            rows.append([search, name, len(ranked), f"{hosted.ndcg_at_k(ranked, 10):.3f}",
                         f"{hosted.ndcg_at_k(ranked, 10, pool=[labels[item] for item in pool]):.3f}",
                         " ".join(str(grade) for grade in ranked[:10])])
    text += _table(["Search", "Labels", "Jobs shown", "NDCG@10 (shown)", "NDCG@10 (judged pool)", "Grades of the top 10, in rank order"],
                   rows)

    far = [item for item in order if max(abs(gold[item] - by_order[name][item]["label"]) for name in hosted.ORDERS) >= 2]
    by_id = {job["item_id"]: job for job in jobs}
    text += [f"## Jobs where the labels differ by 2 or more ({len(far)})", "",
             "Sorted by the size of the gap. \"Stage\" says whether a search showed the job or it is a sampled excluded job.", ""]
    far.sort(key=lambda item: (-max(abs(gold[item] - by_order[name][item]["label"]) for name in hosted.ORDERS), by_id[item]["job"]["title"]))
    text += _table(["Title", "Company", "Stage", "Rajdeep", "Hosted (batch / reverse)", "Hosted reason (batch order)"],
                   [[by_id[item]["job"]["title"], by_id[item]["job"]["company"],
                     "excluded sample" if item in sampled else "shown: " + ",".join(sorted({entry["search"] for entry in by_id[item]["surfaced_by"]})),
                     gold[item], f"{by_order['forward'][item]['label']} / {by_order['reverse'][item]['label']}",
                     by_order["forward"][item]["reason"]] for item in far])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(text), encoding="utf-8", newline="\n")
    print(f"report: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hosted labels and the agreement report for one batch.")
    parser.add_argument("command", choices=("prompts", "ingest", "report"))
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--target", type=Path, default=EVAL / "target_profile.md")
    parser.add_argument("--labeller", default="subagent:unknown")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "prompts":
        return prompts(args.batch, args.target)
    if args.command == "ingest":
        return ingest(args.batch, args.labeller)
    return report(args.batch, args.out or ROOT / "docs" / "eval" / f"agreement_{args.batch.name}.md")


if __name__ == "__main__":
    raise SystemExit(main())
