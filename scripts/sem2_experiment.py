"""SEM2: run the five rankers of docs/SEMANTIC_PLAN.md on the judged pools of searches A and B and apply the decision rule.

Usage (from the repository root, in the app's Python environment):
    python scripts/sem2_experiment.py

Reads the batch, the gold labels, the saved hosted labels and the target profile; embeds locally with FastEmbed (the
model is fetched once into %HF_HOME%\\fastembed); writes data/eval/vectors/<batch>.npz and docs/eval/sem2_results.md.
Sends nothing to a hosted model and changes nothing in the app.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.eval import semantic  # noqa: E402
from app.eval.hosted_labels import target_text  # noqa: E402
from app.eval.label_page import label_quality  # noqa: E402
from app.mcp import target_profile  # noqa: E402
from app.models.schemas import JobPosting, JobSearchPreferences  # noqa: E402
from app.services.eligibility import evaluate_eligibility, fresher_detectors  # noqa: E402
from app.services.search_intent import interpret_search_request  # noqa: E402

EVAL = Path(settings.data_dir) / "eval"
SEARCHES = {"A": "AI Engineer jobs for freshers in India", "B": "Software Engineer fresher jobs in India"}
RANKERS = ["v1", "BM25", "dense", "hybrid", semantic.PRIMARY]


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _cell(value) -> str:
    return str(value).replace("|", "\\|")


def _interval(values) -> str:
    point, low, high = values
    return f"{point:+.3f} [{low:+.3f}, {high:+.3f}]"


def main(argv: list[str] | None = None, *, embedder=None) -> int:
    parser = argparse.ArgumentParser(description="Run the SEM2 ranking experiment.")
    parser.add_argument("--batch", type=Path, default=EVAL / "batches" / "gold-20261001-r1")
    parser.add_argument("--labels", type=Path, default=EVAL / "labels")
    parser.add_argument("--target", type=Path, default=EVAL / "target_profile.md")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "eval" / "sem2_results.md")
    args = parser.parse_args(argv)
    batch_id = args.batch.name
    jobs = _lines(args.batch / "jobs.jsonl")
    gold_lines = _lines(args.labels / f"{batch_id}.jsonl")
    quality = label_quality(gold_lines, len(jobs))
    if not quality["accepted"]:
        print(f"The gold labels are not usable: {quality['message']}")
        return 1
    gold = {line["item_id"]: line["label"] for line in gold_lines}
    hosted = {line["item_id"]: line["label"] for line in _lines(args.labels / f"{batch_id}.hosted.jsonl") if line["order"] == "forward"}
    query = target_text(args.target)
    profile = target_profile.load(args.target)
    cgpa = re.search(r"\bCGPA\s*([0-9]+(?:\.[0-9]+)?)", query)
    facts = {"graduation_year": profile.graduation_year if profile else None, "cgpa": float(cgpa.group(1)) if cgpa else None}
    texts = {job["item_id"]: semantic.job_text(job["job"]) for job in jobs}

    embedder = embedder or semantic.FastEmbedder()
    ids = sorted(texts)
    vectors = embedder.passages([texts[item] for item in ids])
    semantic.save_vectors(EVAL / "vectors" / f"{batch_id}.npz", ids, embedder.model, vectors)

    class Cached:                                   # the job vectors are computed once for both searches
        model = embedder.model

        def query(self, text):
            return embedder.query(text)

        def passages(self, chunk):
            return [vectors[ids.index(texts_inverse[text])] for text in chunk]
    texts_inverse = {text: item for item, text in texts.items()}

    pools, orders, tiers_by_search = {}, {name: {} for name in RANKERS}, {}
    for search, wording in SEARCHES.items():
        members = semantic.pool(jobs, search)
        pools[search] = sorted(members)
        pool_texts = {item: texts[item] for item in members}
        bm25 = semantic.bm25_scores(query, pool_texts)
        dense = semantic.dense_scores(Cached(), query, pool_texts)
        hybrid = semantic.rrf([semantic.order_by(bm25), semantic.order_by(dense)])
        intent = interpret_search_request(wording)
        tiers = {}
        for job in jobs:
            if job["item_id"] in members:
                posting = JobPosting.model_validate(job["job"])
                status = evaluate_eligibility(profile, posting, JobSearchPreferences(), intent).status
                findings = fresher_detectors(posting.title, posting.description, facts)
                tiers[job["item_id"]] = semantic.tier(status, [finding.outcome for finding in findings])
        tiers_by_search[search] = tiers
        orders["v1"][search] = semantic.v1_order(jobs, search)
        orders["BM25"][search] = semantic.order_by(bm25)
        orders["dense"][search] = semantic.order_by(dense)
        orders["hybrid"][search] = semantic.order_by(hybrid)
        orders[semantic.PRIMARY][search] = semantic.gated_order(hybrid, tiers)

    results = {}
    for label_name, labels in (("gold", gold), ("hosted", hosted)):
        for metric_name, metric in (("NDCG@10", semantic.ndcg10), ("P@5", semantic.precision5)):
            points = {name: {search: semantic._score(orders[name][search], pools[search], labels, metric) for search in pools}
                      for name in RANKERS}
            results[(label_name, metric_name)] = (points, semantic.bootstrap(pools, orders, labels, metric))
    gold_mean = results[("gold", "NDCG@10")][1][semantic.PRIMARY]["mean"]
    hosted_mean = results[("hosted", "NDCG@10")][1][semantic.PRIMARY]["mean"]
    verdict = semantic.ships(gold_mean=gold_mean, hosted_mean=hosted_mean)

    text = [f"# SEM2 results: semantic ranking on batch {batch_id}", "",
            f"Generated by `python scripts/sem2_experiment.py` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, exactly as "
            "`docs/SEMANTIC_PLAN.md` specifies; no parameter was changed after results were seen. Nothing is wired into the app.", "",
            "## Verdict", "",
            (f"**{semantic.PRIMARY} ships: it beats v1 on the gold labels (averaged NDCG@10 gain {_interval(gold_mean)}, interval above "
             f"zero) and does not lose on the hosted labels ({_interval(hosted_mean)}).**" if verdict else
             f"**v1 stays. {semantic.PRIMARY} does not meet the rule: averaged NDCG@10 gain on the gold labels "
             f"{_interval(gold_mean)}; on the hosted labels {_interval(hosted_mean)}. The rule needs the gold interval entirely "
             "above zero and the hosted point estimate at least zero.**"), "",
            "> **The eligibility component is in-sample.** Its detectors were designed after reading the 16 Q4 disagreements on this "
            "same batch, so the hybrid x eligibility rows overstate how well it would do on new jobs. The out-of-sample test is a "
            "fresh batch after the next index sync (queue row SEM3).", "",
            "## Setup", "",
            f"- Pools: A {len(pools['A'])} judged jobs, B {len(pools['B'])} judged jobs; every ranker orders the same pool.",
            "- Query: the target profile without its heading line. Job text: title plus the first 2,000 characters of the description.",
            f"- Dense model `{embedder.model}` (FastEmbed, ONNX Runtime, local). BM25: `rank_bm25` defaults, fitted on each pool. "
            "Hybrid: reciprocal rank fusion, k = 60. Ties broken by item id.",
            "- Metrics: NDCG@10 (gain 2^grade - 1) and Precision@5 (grade 2 or 3 counts as relevant). Differences are each ranker "
            "minus v1, with a paired bootstrap 95% interval over jobs (1,000 resamples, seed 20261003, within each search).",
            f"- Gold labels: Rajdeep, run 3 ({quality['summary']}). Hosted labels: batch order.", "",
            "Claim level: these are measurements on one labelled batch (106 jobs), not calibrated probabilities.", ""]
    for (label_name, metric_name), (points, boot) in results.items():
        text += [f"## {metric_name}, {label_name} labels", "",
                 "| Ranker | A | B | A minus v1 [95% interval] | B minus v1 [95% interval] | Mean of A and B minus v1 [95% interval] |",
                 "|---|---|---|---|---|---|"]
        for name in RANKERS:
            diff = boot[name]
            text.append(f"| {_cell(name)} | {points[name]['A']:.3f} | {points[name]['B']:.3f} | "
                        + (" — | — | — |" if name == "v1" else f"{_interval(diff['A'])} | {_interval(diff['B'])} | {_interval(diff['mean'])} |"))
        text.append("")
    text += ["## Eligibility tiers (in-sample)", "",
             "| Search | Tier | Jobs | Gold grade 3 | Gold grade 2 | Hosted grade 2 or 3 |", "|---|---|---|---|---|---|"]
    for search, tiers in tiers_by_search.items():
        for name in ("eligible", "uncertain", "excluded"):
            members = [item for item, value in tiers.items() if value == name]
            text.append(f"| {search} | {name} | {len(members)} | {sum(gold[item] == 3 for item in members)} | "
                        f"{sum(gold[item] == 2 for item in members)} | {sum(hosted[item] >= 2 for item in members)} |")
    text += ["", "## What a better ranker cannot fix (from the plan)", "",
             "| Search | Pool | Grade 3 | Of which shown by v1 | Grade 2 or 3 | Hosted grade 3 |", "|---|---|---|---|---|---|",
             "| A | 23 | 10 | 9 | 15 | 1 |", "| B | 49 | 4 | 2 | 25 | 0 |", "| C | 13 | 0 | 0 | 1 | 0 |", "| D (control) | 21 | 0 | 0 | 0 | 0 |", "",
             "In B no ranker can put more than 4 grade-3 jobs in the top 10. Finding more good jobs is a sourcing problem."]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(text) + "\n", encoding="utf-8", newline="\n")
    print(("SHIPS" if verdict else "v1 stays") + f": gold {_interval(gold_mean)}, hosted {_interval(hosted_mean)}")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
