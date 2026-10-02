"""Merge a careers patch into seeds/companies_seed.csv (docs/ROADMAP_QUEUE.md Q2c3).

Usage (from the repository root, in the app's Python environment):
    python scripts/merge_careers_patch.py --patch seeds/careers_patch_2026-10-02.csv --dry-run
    python scripts/merge_careers_patch.py --patch seeds/careers_patch_2026-10-02.csv --filled-list data/eval/filled.txt

Fills careers_url only where the seed's cell is empty, from patch rows with status verified, check or
fetch_blocked. An existing value is never overwritten, except by another spelling of the same board.
`ats` is not changed. No request is sent. See app/sources/seed_patch.py.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sources.company_seed import EXPECTED_ROWS, read_seed, update_rows  # noqa: E402
from app.sources.seed_patch import plan_merge, read_patch  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge a careers patch into the company seed list.")
    parser.add_argument("--csv", type=Path, default=ROOT / "seeds" / "companies_seed.csv")
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--expect-rows", type=int, default=EXPECTED_ROWS)
    parser.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    parser.add_argument("--filled-list", type=Path, help="write the newly filled companies here, one per line")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    seed = read_seed(args.csv, expected_rows=args.expect_rows)
    if seed.problems:
        print(f"{args.csv.name}: {len(seed.problems)} problems; fix them first")
        for problem in seed.problems:
            print(f"  {problem}")
        return 1
    plan = plan_merge(seed, read_patch(args.patch))
    print(f"filled {len(plan.filled)}, respelled {len(plan.respelled)}, kept {len(plan.kept)}, skipped {len(plan.skipped)}, "
          f"unchanged {len(plan.unchanged)}, refused {len(plan.refused)}")
    for company, url in plan.respelled.items():
        print(f"  respelled {company}: {url}")
    for company, (existing, unused) in plan.kept.items():
        print(f"  kept {company}: {existing} (patch: {unused})")
    for company, why in plan.refused.items():
        print(f"  refused {company}: {why}")
    if args.dry_run:
        return 0
    if plan.updates():
        update_rows(args.csv, plan.updates())
    after = read_seed(args.csv, expected_rows=args.expect_rows)
    if after.problems:
        print(f"the merged seed has {len(after.problems)} problems:")
        for problem in after.problems:
            print(f"  {problem}")
        return 1
    if args.filled_list:
        args.filled_list.parent.mkdir(parents=True, exist_ok=True)
        args.filled_list.write_text("".join(f"{company}\n" for company in plan.filled), encoding="utf-8")
    print(f"{args.csv.name}: {len([row for row in after.rows if row.careers_url])} rows now have a careers_url")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
