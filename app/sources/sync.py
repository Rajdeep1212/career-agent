"""Daily Company Radar sync: python -m app.sources.sync [--company ID ...] [--dry-run] [--force]

For each enabled, reviewed company (at most one successful run per day unless
--force), the company's adapter reads its official board, the listing is
recorded in data/radar.sqlite3 and the run is logged with counts taken from
the parsed data. Companies are synced one after another through one
PoliteFetcher, so every host sees at most one request per 1.5 s.
"""
import argparse
import asyncio
import sys
from dataclasses import asdict, dataclass
from datetime import date

import httpx

from app.core.config import settings
from app.models.schemas import JobPosting
from app.sources.adapters import FetchResult, SourceError, ashby, greenhouse, lever, sitemap, smartrecruiters
from app.sources.fetcher import HostBlocked, PoliteFetcher, RobotsDisallowed
from app.sources.registry import CompanyEntry, RadarConfig, RadarConfigError, load_config, syncable
from app.storage import radar_store

DETAIL_CAP = 40        # SmartRecruiters detail requests per company per run
PAGE_CHECK_CAP = 20    # job-page checks for jobs missing from a capped sitemap, per company per run
MAX_BYTES = 20_000_000  # Greenhouse boards with content=true can be large


@dataclass
class Outcome:
    company_id: str
    status: str                  # ok | partial | error | skipped
    fetched: int | None = None
    india: int | None = None
    listed: int | None = None
    new: int | None = None
    closed: int | None = None
    http_status: int | None = None
    note: str = ""


async def _smartrecruiters(company: CompanyEntry, fetcher: PoliteFetcher, known: dict[str, JobPosting]) -> tuple[FetchResult, int]:
    """Listing plus descriptions: stored ones are reused, new ones fetched up to DETAIL_CAP. Returns (result, failures)."""
    result = await smartrecruiters.fetch(company, fetcher)
    jobs, requested, failures = [], 0, 0
    for job in result.jobs:
        stored = known.get(str(job.source_job_id))
        if stored is not None and stored.description:
            jobs.append(stored)
        elif requested < DETAIL_CAP:
            requested += 1
            try:
                jobs.append(await smartrecruiters.fetch_detail(company, fetcher, job))
            except (SourceError, httpx.HTTPError):
                failures += 1
                jobs.append(job)
        else:
            result.deferred += 1
            jobs.append(job)
    result.jobs = jobs
    return result, failures


async def _fetch(company: CompanyEntry, fetcher: PoliteFetcher, *, known: dict[str, JobPosting], skip: set[str],
                 today: date) -> tuple[FetchResult, int]:
    kind = company.source.type
    if kind == "greenhouse":
        return await greenhouse.fetch(company, fetcher), 0
    if kind == "lever":
        return await lever.fetch(company, fetcher), 0
    if kind == "ashby":
        return await ashby.fetch(company, fetcher), 0
    if kind == "smartrecruiters":
        return await _smartrecruiters(company, fetcher, known)
    if kind in ("workday", "sitemap_jsonld"):
        return await sitemap.fetch(company, fetcher, known=known, skip=skip, today=today), 0
    raise SourceError(f"No adapter for source type '{kind}' yet.")


async def _check_missing(company: CompanyEntry, fetcher: PoliteFetcher, today: date) -> tuple[int, int]:
    """Page checks for jobs a capped sitemap no longer lists. Returns (closed, failed)."""
    closed = failed = 0
    for job in radar_store.missing_from_window(company.id)[:PAGE_CHECK_CAP]:
        try:
            reason = await sitemap.check_page(company, fetcher, job, today=today)
        except (SourceError, RobotsDisallowed, httpx.HTTPError):
            failed += 1
            continue
        if reason:
            radar_store.mark_closed(company.id, str(job.source_job_id), reason, today=today)
            closed += 1
    return closed, failed


async def sync_company(company: CompanyEntry, fetcher: PoliteFetcher, *, today: date, dry_run: bool = False) -> Outcome:
    run_id = None if dry_run else radar_store.start_run(company.id, today=today)
    known = {} if dry_run else radar_store.known_jobs(company.id)
    skip = set() if dry_run else radar_store.rejected_ids(company.id)
    try:
        result, failures = await _fetch(company, fetcher, known=known, skip=skip, today=today)
        outcome = Outcome(company.id, "ok", fetched=result.fetched, india=len(result.jobs), http_status=result.http_status)
        if dry_run:
            outcome.listed = len(result.jobs)
            return outcome
        listing = radar_store.record_listing(company.id, result.jobs, complete=result.complete, today=today)
        radar_store.remember_rejected(company.id, result.rejected, today=today)
        closed, failed_checks = listing.closed, 0
        if not result.complete and company.source.type in ("workday", "sitemap_jsonld"):
            checked_closed, failed_checks = await _check_missing(company, fetcher, today)
            closed += checked_closed
        outcome.listed, outcome.new, outcome.closed = listing.listed, listing.new, closed
        notes = []
        if result.deferred:
            notes.append(f"{result.deferred} new jobs left for the next run (per-run cap)")
        if failures:
            notes.append(f"{failures} detail requests failed")
        if failed_checks:
            notes.append(f"{failed_checks} job-page checks failed")
        if notes:
            outcome.status, outcome.note = "partial", "; ".join(notes)
    except (SourceError, HostBlocked, RobotsDisallowed, httpx.HTTPError, ValueError) as exc:
        status = exc.http_status if isinstance(exc, SourceError) else None
        outcome = Outcome(company.id, "error", http_status=status, note=str(exc)[:300] or type(exc).__name__)
    if run_id is not None:
        radar_store.finish_run(run_id, status=outcome.status, fetched=outcome.fetched, india=outcome.india,
                               listed=outcome.listed, new=outcome.new, closed=outcome.closed,
                               http_status=outcome.http_status, error=outcome.note or None)
    return outcome


async def run_sync(*, company_ids: list[str] | None = None, dry_run: bool = False, force: bool = False,
                   today: date | None = None, fetcher: PoliteFetcher | None = None,
                   config: RadarConfig | None = None) -> list[Outcome]:
    """Sync every enabled, reviewed company (or the named ones); each at most once a day unless forced."""
    if settings.demo_mode:
        return []
    today = today or date.today()
    companies = syncable(config or load_config())
    outcomes = []
    if company_ids:
        wanted = set(company_ids)
        outcomes += [Outcome(company_id, "skipped", note="unknown, disabled or not reviewed")
                     for company_id in company_ids if company_id not in {company.id for company in companies}]
        companies = [company for company in companies if company.id in wanted]
    fetcher = fetcher or PoliteFetcher(max_bytes=MAX_BYTES)
    for company in companies:
        if not dry_run and not force and radar_store.ran_today(company.id, today=today):
            outcomes.append(Outcome(company.id, "skipped", note="already synced today"))
            continue
        outcomes.append(await sync_company(company, fetcher, today=today, dry_run=dry_run))
    return outcomes


def _print(outcomes: list[Outcome], requests: int, dry_run: bool) -> None:
    print(f"{'company':28} {'status':8} {'fetched':>7} {'india':>6} {'new':>5} {'closed':>6}  note")
    for outcome in outcomes:
        row = asdict(outcome)
        cells = [str(row[key]) if row[key] is not None else "-" for key in ("fetched", "india", "new", "closed")]
        print(f"{outcome.company_id:28} {outcome.status:8} {cells[0]:>7} {cells[1]:>6} {cells[2]:>5} {cells[3]:>6}  {outcome.note}")
    totals = {status: sum(1 for outcome in outcomes if outcome.status == status) for status in ("ok", "partial", "error", "skipped")}
    print(f"\n{len(outcomes)} companies: " + ", ".join(f"{count} {status}" for status, count in totals.items())
          + f"; {requests} requests" + (" (dry run: nothing saved)" if dry_run else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.sources.sync", description="Sync the Company Radar index from official job boards.")
    parser.add_argument("--company", action="append", dest="companies", metavar="ID", help="only this company id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and print counts without saving anything")
    parser.add_argument("--force", action="store_true", help="sync companies already synced today")
    args = parser.parse_args(argv)
    if settings.demo_mode:
        print("DEMO_MODE is on: the Company Radar sync never runs in the demo.")
        return 0
    try:
        config = load_config()
    except RadarConfigError as exc:
        print(exc)
        return 2
    fetcher = PoliteFetcher(max_bytes=MAX_BYTES)
    outcomes = asyncio.run(run_sync(company_ids=args.companies, dry_run=args.dry_run, force=args.force,
                                    fetcher=fetcher, config=config))
    _print(outcomes, fetcher.requests, args.dry_run)
    return 1 if outcomes and all(outcome.status == "error" for outcome in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
