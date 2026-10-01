"""Find careers boards for seed companies that list no careers_url (docs/ROADMAP_QUEUE.md Q2c2).

Usage (from the repository root, in the app's Python environment):
    python scripts/probe_boards.py
    python scripts/probe_boards.py --apply-confirmed

For each row of seeds/companies_seed.csv without a careers_url, tries up to four slugs made from the
company name against five public job APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workable) and
writes every hit to seeds/companies_probe_review.csv, with a run summary beside it. No web page is
read and no database is opened. Requests are polite (one per host every --delay seconds, a 429 stops
that API) and cached under data/ats_detect_cache/, so a re-run sends nothing new.

--apply-confirmed writes careers_url and ats into the seed for companies with exactly one confirmed
board. Probable rows are never applied; they wait for review. See app/sources/board_probe.py.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.sources.ats_detect import DetectFetcher  # noqa: E402
from app.sources.board_probe import confirmed_updates, probe_all, write_review  # noqa: E402
from app.sources.company_seed import EXPECTED_ROWS, read_seed, update_rows  # noqa: E402


def _progress(row, hits) -> None:
    found = "; ".join(f"{hit.status} {hit.ats} {hit.slug} ({hit.jobs_india}/{hit.jobs_total})" for hit in hits) or "no board"
    print(f"  line {row.line}: {row.company}: {found}", flush=True)


def main(argv: list[str] | None = None, *, fetcher: DetectFetcher | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe public job APIs for seed companies without a careers_url.")
    parser.add_argument("--csv", type=Path, default=ROOT / "seeds" / "companies_seed.csv")
    parser.add_argument("--review", type=Path, default=ROOT / "seeds" / "companies_probe_review.csv")
    parser.add_argument("--expect-rows", type=int, default=EXPECTED_ROWS)
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between requests to one host")
    parser.add_argument("--max-age-hours", type=float, default=168.0, help="reuse cached answers younger than this")
    parser.add_argument("--apply-confirmed", action="store_true", help="write confirmed boards into the seed list")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    seed = read_seed(args.csv, expected_rows=args.expect_rows)
    print(f"{args.csv.name}: {len(seed.rows)} rows, {len(seed.problems)} problems")
    for problem in seed.problems:
        print(f"  {problem}")
    if seed.problems:
        return 1
    if settings.demo_mode:
        print("DEMO_MODE is on: the probe sends real requests and does not run in demo mode.")
        return 2

    rows = [row for row in seed.rows if not row.careers_url]
    fetcher = fetcher or DetectFetcher(delay=args.delay, max_age_hours=args.max_age_hours)
    run = asyncio.run(probe_all(rows, fetcher, progress=_progress))
    write_review(args.review, run.hits)
    confirmed = len([hit for hit in run.hits if hit.status == "confirmed"])
    updates = confirmed_updates(run.hits)
    summary = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "companies": run.companies,
               "with_hit": run.with_hit, "confirmed": confirmed, "probable": len(run.hits) - confirmed,
               "single_confirmed_companies": len(updates), "slugs_tried": run.slugs_tried, "requests": fetcher.requests,
               "cache_hits": fetcher.cache_hits, "delay_seconds": fetcher.delay, "stopped": run.stopped, "not_sent": run.not_sent,
               "errors": run.errors}
    args.review.with_suffix(".meta.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"{run.companies} companies probed, {run.with_hit} with a board: confirmed {confirmed}, probable {len(run.hits) - confirmed}")
    print(f"requests sent {fetcher.requests}, cache hits {fetcher.cache_hits}; stopped {run.stopped or 'none'}; errors {run.errors or 'none'}")
    if args.apply_confirmed:
        changed = update_rows(args.csv, updates) if updates else []
        print(f"seed updated: {', '.join(changed) or 'nothing to change'}")
    print(f"review file: {args.review}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
