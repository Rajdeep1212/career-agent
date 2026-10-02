"""Validate the company seed list and detect each company's ATS (docs/ROADMAP_QUEUE.md Q2c).

Usage (from the repository root, in the app's Python environment):
    python scripts/detect_ats.py --validate-only
    python scripts/detect_ats.py

Reads seeds/companies_seed.csv and writes docs/eval/ats_detection.md. Detection only: no database is
opened and nothing is added to the Company Radar. Requests are polite (robots.txt, one request per
host every --delay seconds, stop on 429) and answers are cached under data/ats_detect_cache/, so a
re-run within --max-age-hours sends nothing new. See app/sources/ats_detect.py for the method.
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
from app.sources.ats_detect import STATUSES, Detection, DetectFetcher, detect_all, pollable, render_report  # noqa: E402
from app.sources.board_probe import read_review  # noqa: E402
from app.sources.company_seed import EXPECTED_ROWS, read_seed  # noqa: E402


def _progress(found: Detection) -> None:
    detail = f"{found.ats} {found.key}".strip() or found.note
    print(f"  line {found.line}: {found.company}: {found.status} {detail}".rstrip())


def main(argv: list[str] | None = None, *, fetcher: DetectFetcher | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the company seed list and detect each company's ATS.")
    parser.add_argument("--csv", type=Path, default=ROOT / "seeds" / "companies_seed.csv")
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "eval" / "ats_detection.md")
    parser.add_argument("--expect-rows", type=int, default=EXPECTED_ROWS)
    parser.add_argument("--validate-only", action="store_true", help="check the CSV and send no request")
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between requests to one host")
    parser.add_argument("--max-age-hours", type=float, default=168.0, help="reuse cached answers younger than this")
    parser.add_argument("--baseline-pollable", type=int, default=8,
                        help="pollable companies before the slug probe (8 in the Q2c run, commit 5fbdd0a)")
    parser.add_argument("--only-file", type=Path,
                        help="company names, one per line: read these live and answer every other row from the cache only")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    seed = read_seed(args.csv, expected_rows=args.expect_rows)
    print(f"{args.csv.name}: {len(seed.rows)} rows, {len(seed.problems)} problems")
    for problem in seed.problems:
        print(f"  {problem}")
    if args.validate_only or not seed.rows:
        return 1 if seed.problems else 0
    if settings.demo_mode:
        print("DEMO_MODE is on: detection sends real requests and does not run in demo mode.")
        return 2

    fetcher = fetcher or DetectFetcher(delay=args.delay, max_age_hours=args.max_age_hours)
    only = None
    if args.only_file:
        only = {line.strip() for line in args.only_file.read_text(encoding="utf-8").splitlines() if line.strip()}
        unknown = sorted(only - {row.company for row in seed.rows})
        if unknown:
            print(f"--only-file names companies that are not in the seed list: {', '.join(unknown)}")
            return 1
    results = asyncio.run(detect_all(seed.rows, fetcher, progress=_progress, only=only))
    try:
        seed_name = args.csv.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        seed_name = args.csv.name
    # The slug-probe review file (scripts/probe_boards.py) sits beside the seed list; its section is added when it exists.
    review_path = args.csv.with_name("companies_probe_review.csv")
    review = read_review(review_path) if review_path.exists() else None
    summary_path = review_path.with_suffix(".meta.json")
    probe = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(seed, results, run_at=datetime.now(timezone.utc), requests=fetcher.requests,
                                         cache_hits=fetcher.cache_hits, seed_name=seed_name, review=review, probe=probe,
                                         live_rows=None if only is None else len(only),
                                         baseline_pollable=args.baseline_pollable), encoding="utf-8")
    print(f"pollable {len(pollable(results))}")
    print(" | ".join(f"{status} {len([found for found in results if found.status == status])}" for status in STATUSES))
    print(f"requests sent {fetcher.requests}, cache hits {fetcher.cache_hits}; report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
