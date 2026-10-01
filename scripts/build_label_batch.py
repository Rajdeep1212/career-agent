"""Build a relevance-label batch from a frozen copy of the index (docs/M2_PLAN.md §5, docs/eval/searches.md).

Usage (from the repository root, in the app's Python environment):
    python scripts/build_label_batch.py --frozen data/eval/frozen/<UTC time> --seed 20261001
    python scripts/build_label_batch.py --frozen data/eval/frozen/<UTC time> --seed 20261001 --dry-run

Runs the fixed search list through the app's own search with the Radar provider only (no requests) and
writes data/eval/batches/<batch id>/:
    jobs.jsonl   one line per distinct job: the listing, which searches surfaced it and at what rank,
                 and the heuristic output (claim level L0). The key; a labeller never sees it.
    blind.jsonl  the same jobs in seeded order with listing fields only.
    meta.json    what the batch was built from, per-search counts, and the file hashes.

Labels are per job. Besides the jobs each search shows, a seeded sample of the jobs its eligibility
check excluded is added, so the labels can also judge the filter. Freshness is judged on the day the
copy was frozen (India time); jobs it hides as stale or closed are not in the batch.

The frozen copy is opened read-only and must match its manifest before and after. The search's own
writes (session, history, cache) go to a scratch folder that is removed. A batch is never overwritten.
The profile appears only as its version hash.
"""
import argparse
import asyncio
import hashlib
import json
import os
import random
import shutil
import stat
import sys
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.models.schemas import JobPosting  # noqa: E402
from app.providers import radar_provider  # noqa: E402
from app.services import career_agent  # noqa: E402
from app.services.freshness import IST  # noqa: E402
from app.services.skills import VOCABULARY_VERSION  # noqa: E402
from app.storage import career_events, career_store, db, history, radar_store, search_cache  # noqa: E402
from app.storage.preference_store import preferences_for  # noqa: E402
from app.storage.profile_store import load_profile  # noqa: E402

RUBRIC_VERSION = "r1"
# (id, query, role, excluded jobs to sample). Fixed by the owner on 2026-10-01; see docs/eval/searches.md.
SEARCHES = [
    ("A", "AI Engineer jobs for freshers in India", "target", 15),
    ("B", "Software Engineer fresher jobs in India", "target", 15),
    ("C", "Data Analyst jobs for freshers in India", "target", 15),
    ("D", "Sales Executive jobs in India", "control", 10),
]
# Listing fields only: nothing the ranker or the eligibility check produced.
BLIND_FIELDS = ("title", "company", "location", "work_mode", "employment_type", "posted_date", "salary", "description",
                "application_url")
Search = tuple[str, str, str, int]


def default_root() -> Path:
    return Path(settings.data_dir) / "eval" / "batches"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(frozen: Path) -> dict:
    manifest = json.loads((frozen / "manifest.json").read_text(encoding="utf-8"))
    if _file_sha(frozen / manifest["file"]) != manifest["sha256"]:
        raise ValueError(f"The frozen copy in {frozen} does not match its manifest.")
    return manifest


def snapshot_day(manifest: dict) -> date:
    """The frozen copy's own "today": the India calendar day it was frozen on."""
    frozen_at = datetime.strptime(manifest["frozen_at_utc"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return frozen_at.astimezone(IST).date()


def _run_search(search: Search, seed: int, today: date) -> tuple[dict, list[dict]]:
    """One search: its meta entry, and an entry per job it contributes to the batch."""
    search_id, query, role, sample_size = search
    response = asyncio.run(career_agent.CareerAgent(providers=[radar_provider.RadarProvider()])
                           .search(query, include_seen=True, today=today, return_excluded=True))
    intent, diagnostics = response["intent"], response["diagnostics"]
    if intent["locations"] != ["India"] or intent.get("locations_from_preferences"):
        raise ValueError(f"Search {search_id} must name India in its text and nothing else; got {intent['locations']}.")
    if diagnostics["errors"] or diagnostics["provider_requests"]:
        raise RuntimeError(f"Search {search_id} did not run cleanly offline: {diagnostics['errors']}")
    shown, excluded = response["results"], response["excluded"]
    # Stale and closed jobs are not labelled at all; the sample judges the eligibility check only.
    pool = {result["id"]: result for result in excluded if result["excluded_by"] == "eligibility"}
    sampled = sorted(random.Random(f"{seed}:{search_id}").sample(sorted(pool), min(sample_size, len(pool))))
    entries = [dict(search=search_id, stage="post_filter", rank=rank, result=result) for rank, result in enumerate(shown, start=1)]
    entries += [dict(search=search_id, stage="excluded_sample", rank=None, result=pool[job_id]) for job_id in sampled]
    # Postings carry mixed UTC offsets; the calendar date is what the cap cut-off needs.
    posted = [str(result["posted_date"])[:10] for result in shown + excluded if result.get("posted_date")]
    cap = radar_provider.MAX_RESULTS
    statuses = [result["eligibility_status"] for result in shown]
    meta = dict(id=search_id, query=query, role=role, locations=intent["locations"], role_families=intent.get("role_families"),
                index_matches=diagnostics["provider_results"], match_cap=cap, cap_hit=diagnostics["provider_results"] >= cap,
                oldest_posted_date=min(posted) if posted else None, without_posted_date=len(shown) + len(excluded) - len(posted),
                stale_hidden=diagnostics["stale_hidden"], excluded_by_eligibility=len(pool), results=len(shown),
                check_before_applying=diagnostics["check_before_applying"],
                eligible=statuses.count("eligible"), uncertain=statuses.count("uncertain"),
                excluded_sample_requested=sample_size, excluded_sampled=len(sampled))
    return meta, entries


def _record(result: dict, seed: int) -> dict:
    """The key line of one job: the listing as the index had it, apart from what the heuristics said."""
    listing = JobPosting.model_validate(result).model_dump(mode="json")
    return dict(item_id=_sha256(f"{seed}:{result['id']}")[:12], job_id=result["id"], job=listing,
                job_sha256=_sha256(_canonical(listing)), surfaced_by=[], total_score=result["total_score"],
                score_claim_level="L0 (deterministic heuristic score)", eligibility_status=result["eligibility_status"],
                eligibility_summary=result["eligibility_summary"], freshness=result["freshness"])


def _collect(frozen: Path, scratch: Path, searches: list[Search], seed: int, today: date) -> tuple[list[dict], list[dict]]:
    """Run every search against the frozen copy; return the per-search meta and one record per distinct job."""
    manifest = json.loads((frozen / "manifest.json").read_text(encoding="utf-8"))
    index = frozen / manifest["file"]
    with ExitStack() as stack:
        for module, path in ((radar_store, index), (career_store, scratch / "agent.sqlite3"),
                             (history, scratch / "job_history.sqlite3"), (search_cache, scratch / "search_cache.sqlite3")):
            stack.enter_context(patch.object(module, "DB_PATH", path))
        stack.enter_context(db.read_only(index))
        db.reset_cache()
        stack.callback(db.reset_cache)
        metas, jobs = [], {}
        for search in searches:
            meta, entries = _run_search(search, seed, today)
            metas.append(meta)
            for entry in entries:
                record = jobs.setdefault(entry["result"]["id"], _record(entry["result"], seed))
                record["surfaced_by"].append({key: entry[key] for key in ("search", "stage", "rank")})
    return metas, sorted(jobs.values(), key=lambda record: record["item_id"])


def _counts(records: list[dict]) -> dict:
    shown = sum(any(entry["stage"] == "post_filter" for entry in record["surfaced_by"]) for record in records)
    return dict(distinct_jobs=len(records), post_filter_jobs=shown, excluded_sample_jobs=len(records) - shown)


def build(frozen: Path, root: Path | None = None, *, seed: int, batch_id: str | None = None,
          searches: list[Search] | None = None, dry_run: bool = False) -> Path | dict:
    """Build the batch into `root/<batch_id>/` and return that folder; a dry run returns the counts only."""
    if settings.demo_mode:
        raise RuntimeError("Batches are not built in demo mode: it holds no real index.")
    frozen, root = Path(frozen), Path(root) if root else default_root()
    searches = list(searches or SEARCHES)
    batch_id = batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = _manifest(frozen)
    folder, scratch = root / batch_id, root / f".work_{batch_id}"
    if folder.exists():
        raise FileExistsError(f"A batch already exists at {folder}; it is never overwritten.")
    created_root = not root.exists()
    scratch.mkdir(parents=True)
    try:
        today = snapshot_day(manifest)
        metas, records = _collect(frozen, scratch, searches, seed, today)
        _manifest(frozen)
        meta = dict(
            today=today.isoformat(), max_age_days=settings.max_age_days, check_age_days=settings.check_age_days,
            batch_id=batch_id, created_at_utc=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            frozen=dict(stamp=frozen.name, sha256=manifest["sha256"]), label_unit="job", rubric_version=RUBRIC_VERSION,
            seed=seed, ranker_version=career_events.ranker_version(), vocabulary_version=str(VOCABULARY_VERSION),
            cv_version=career_events.profile_hash(load_profile().model_dump(mode="json")),
            preferences_sha256=_sha256(_canonical(preferences_for(load_profile()).model_dump(mode="json"))),
            searches=metas, counts=_counts(records))
        if dry_run:
            return meta
        order = sorted(records, key=lambda record: record["job_id"])
        random.Random(f"{seed}:order").shuffle(order)
        files = {"jobs.jsonl": records,
                 "blind.jsonl": [dict(item_id=record["item_id"], **{field: record["job"].get(field) for field in BLIND_FIELDS})
                                 for record in order]}
        folder.mkdir()
        for name, rows in files.items():
            (folder / name).write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                                       encoding="utf-8", newline="\n")
        meta["files"] = {name: _file_sha(folder / name) for name in sorted(files)}
        (folder / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        for path in folder.iterdir():
            os.chmod(path, stat.S_IREAD)
        return folder
    finally:
        import gc
        gc.collect()   # Windows keeps a closed SQLite file locked until its connection object is collected
        shutil.rmtree(scratch, ignore_errors=True)
        if created_root and not any(root.iterdir()):
            root.rmdir()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a relevance-label batch from a frozen index copy.")
    parser.add_argument("--frozen", required=True, type=Path, help="folder made by scripts/freeze_index.py")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--dry-run", action="store_true", help="report the counts; write no batch")
    arguments = parser.parse_args()
    built = build(arguments.frozen, seed=arguments.seed, batch_id=arguments.batch_id, dry_run=arguments.dry_run)
    report = built if isinstance(built, dict) else json.loads((built / "meta.json").read_text(encoding="utf-8"))
    if not isinstance(built, dict):
        print(f"Batch: {built}")
    for entry in report["searches"]:
        print(f"{entry['id']} | {entry['query']} | index matches {entry['index_matches']} (cap hit: "
              f"{'yes' if entry['cap_hit'] else 'no'}, oldest {entry['oldest_posted_date']}) | hidden as stale "
              f"{entry['stale_hidden']} | results {entry['results']} (check before applying {entry['check_before_applying']}) "
              f"| excluded {entry['excluded_by_eligibility']}, sampled {entry['excluded_sampled']}")
    print(f"today: {report['today']}")
    print(f"counts: {report['counts']}")
