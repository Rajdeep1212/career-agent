"""Sync engine: adapter dispatch, status transitions, run logs, same-day idempotence; the network is never used."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.sources import sync
from app.sources.registry import RadarConfig
from app.storage import db, radar_store
from radar_helpers import FakeFetcher, company, fixture_text

D1, D2, D3 = date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28)
GH = "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"
LEVER = "https://api.lever.co/v0/postings/example?mode=json"
SR = "https://api.smartrecruiters.com/v1/companies/Example/postings?country=in&limit=100&offset={offset}"
SR_DETAIL = "https://api.smartrecruiters.com/v1/companies/Example/postings/744000001"
WD = "https://example.wd1.myworkdayjobs.com/External"
WD_ML = WD + "/job/India-Bengaluru/Machine-Learning-Engineer_JR100001"
WD_GRAD = WD + "/job/Hyderabad-India/Graduate-Software-Engineer_JR100002"
EMPTY_SITEMAP = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'


def config(*entries) -> RadarConfig:
    return RadarConfig(companies=list(entries))


GREENHOUSE = company({"type": "greenhouse", "board": "example"}, id="gh")
LEVER_CO = company({"type": "lever", "site": "example"}, id="lv")
SMART = company({"type": "smartrecruiters", "company": "Example"}, id="sr")
WORKDAY = company({"type": "workday", "host": "example.wd1.myworkdayjobs.com", "sites": ["External"], "sitemap_capped": True}, id="wd")


def routes(**overrides):
    table = {GH: fixture_text("greenhouse_jobs.json"), LEVER: fixture_text("lever_postings.json"),
             SR.format(offset=0): fixture_text("smartrecruiters_page1.json"),
             SR.format(offset=100): fixture_text("smartrecruiters_page2.json"),
             SR_DETAIL: fixture_text("smartrecruiters_detail.json"),
             WD + "/siteMap.xml": fixture_text("workday_sitemap.xml"),
             WD_ML: fixture_text("workday_job_page.html"), WD_GRAD: fixture_text("workday_expired_page.html")}
    table.update(overrides)
    return table


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(radar_store, "DB_PATH", Path(directory.name) / "radar.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    async def _run(self, fetcher, today, *entries, **kwargs):
        return {outcome.company_id: outcome for outcome in
                await sync.run_sync(config=config(*entries), fetcher=fetcher, today=today, **kwargs)}

    async def test_every_adapter_fills_the_index_with_counts_from_parsed_data(self):
        outcomes = await self._run(FakeFetcher(routes()), D1, GREENHOUSE, LEVER_CO, SMART, WORKDAY)
        self.assertEqual({key: (o.fetched, o.india, o.new) for key, o in outcomes.items()},
                         {"gh": (4, 2, 2), "lv": (3, 2, 2), "sr": (3, 3, 3), "wd": (4, 1, 1)})
        self.assertEqual(outcomes["gh"].status, "ok")
        self.assertEqual(outcomes["sr"].status, "partial")         # two detail pages are missing from the fixtures
        self.assertIn("2 detail requests failed", outcomes["sr"].note)
        self.assertEqual(len(radar_store.list_jobs()), 8)
        logged = {run["company_id"]: (run["status"], run["fetched_count"], run["india_count"]) for run in radar_store.runs(day=D1)}
        self.assertEqual(logged["gh"], ("ok", 4, 2))
        self.assertEqual(logged["wd"], ("ok", 4, 1))
        self.assertEqual(radar_store.rejected_ids("wd"), {"JR100002"})
        detailed = radar_store.known_jobs("sr")["744000001"]
        self.assertIn("2025 graduates eligible", detailed.description)

    async def test_same_day_rerun_is_skipped_unless_forced(self):
        await self._run(FakeFetcher(routes()), D1, GREENHOUSE)
        fetcher = FakeFetcher(routes())
        outcomes = await self._run(fetcher, D1, GREENHOUSE)
        self.assertEqual((outcomes["gh"].status, fetcher.calls), ("skipped", []))
        forced = await self._run(FakeFetcher(routes()), D1, GREENHOUSE, force=True)
        self.assertEqual((forced["gh"].status, forced["gh"].new), ("ok", 0))

    async def test_complete_source_closes_a_job_after_two_missing_days(self):
        await self._run(FakeFetcher(routes()), D1, GREENHOUSE)
        empty = routes(**{GH: '{"jobs": []}'})
        first = await self._run(FakeFetcher(empty), D2, GREENHOUSE)
        self.assertEqual((first["gh"].closed, len(radar_store.list_jobs())), (0, 2))
        second = await self._run(FakeFetcher(empty), D3, GREENHOUSE)
        self.assertEqual((second["gh"].closed, len(radar_store.list_jobs())), (2, 0))

    async def test_capped_sitemap_checks_pages_instead_of_closing(self):
        await self._run(FakeFetcher(routes()), D1, WORKDAY)
        still_open = FakeFetcher(routes(**{WD + "/siteMap.xml": EMPTY_SITEMAP}))
        outcome = (await self._run(still_open, D2, WORKDAY))["wd"]
        self.assertEqual((outcome.closed, len(radar_store.list_jobs())), (0, 1))
        self.assertIn(WD_ML, [url for url, _ in still_open.calls])
        gone = FakeFetcher(routes(**{WD + "/siteMap.xml": EMPTY_SITEMAP, WD_ML: (404, "")}))
        outcome = (await self._run(gone, D3, WORKDAY))["wd"]
        self.assertEqual((outcome.closed, radar_store.list_jobs()), (1, []))
        closed = radar_store.list_jobs(include_closed=True)[0]
        self.assertIn("HTTP 404", closed.verification_reason)

    async def test_known_sitemap_jobs_and_rejections_are_not_reopened(self):
        await self._run(FakeFetcher(routes()), D1, WORKDAY)
        fetcher = FakeFetcher(routes())
        await self._run(fetcher, D2, WORKDAY)
        self.assertEqual([url for url, _ in fetcher.calls], [WD + "/siteMap.xml"])

    async def test_known_smartrecruiters_descriptions_are_kept_without_new_requests(self):
        await self._run(FakeFetcher(routes()), D1, SMART)
        fetcher = FakeFetcher(routes())
        await self._run(fetcher, D2, SMART)
        self.assertNotIn(SR_DETAIL, [url for url, _ in fetcher.calls])
        self.assertIn("2025 graduates eligible", radar_store.known_jobs("sr")["744000001"].description)

    async def test_errors_are_logged_and_do_not_stop_other_companies(self):
        outcomes = await self._run(FakeFetcher(routes(**{GH: (503, "")})), D1, GREENHOUSE, LEVER_CO)
        self.assertEqual((outcomes["gh"].status, outcomes["gh"].http_status), ("error", 503))
        self.assertEqual(outcomes["lv"].status, "ok")
        self.assertEqual(radar_store.runs(day=D1)[0]["error"], "Greenhouse board 'example' returned HTTP 503.")
        self.assertFalse(radar_store.ran_today("gh", today=D1))   # an error is retried the same day

    async def test_rate_limit_is_an_error_for_that_company(self):
        outcomes = await self._run(FakeFetcher(routes(**{GH: 429})), D1, GREENHOUSE)
        self.assertEqual(outcomes["gh"].status, "error")

    async def test_dry_run_saves_nothing(self):
        outcomes = await self._run(FakeFetcher(routes()), D1, GREENHOUSE, dry_run=True)
        self.assertEqual((outcomes["gh"].fetched, outcomes["gh"].india), (4, 2))
        self.assertEqual((radar_store.list_jobs(), radar_store.runs()), ([], []))

    async def test_unknown_or_unreviewed_companies_are_reported(self):
        unreviewed = company({"type": "lever", "site": "example"}, id="unrev", reviewed=False)
        outcomes = await self._run(FakeFetcher(routes()), D1, GREENHOUSE, unreviewed, company_ids=["gh", "unrev", "nope"])
        self.assertEqual({key: o.status for key, o in outcomes.items()}, {"gh": "ok", "unrev": "skipped", "nope": "skipped"})

    async def test_demo_mode_never_syncs(self):
        with patch.object(sync.settings, "demo_mode", True):
            self.assertEqual(await sync.run_sync(config=config(GREENHOUSE), fetcher=FakeFetcher(routes()), today=D1), [])

    def test_cli_prints_a_summary(self):
        async def fake_run(**kwargs):
            return [sync.Outcome("gh", "ok", fetched=4, india=2, listed=2, new=2, closed=0)]
        with patch.object(sync, "run_sync", fake_run), patch.object(sync, "load_config", lambda: config(GREENHOUSE)), \
                patch("builtins.print") as printed:
            self.assertEqual(sync.main(["--dry-run"]), 0)
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("1 companies: 1 ok", output)
        self.assertIn("dry run", output)


if __name__ == "__main__":
    unittest.main()
