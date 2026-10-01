"""Q2c: which applicant-tracking system a seed company's careers page uses. Recorded fixtures and a
fake web only; fictional companies."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.sources.ats_detect import DetectFetcher, detect_all, detect_company, match_page, match_url, render_report
from app.sources.company_seed import COLUMNS, SeedFile, SeedRow
from radar_helpers import fixture_text

ROOT = Path(__file__).resolve().parents[1]
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs?content=true"
LEVER_API = "https://api.lever.co/v0/postings/examplelabs?mode=json"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/example"


def row(company="Example Corp", careers_url="", ats="unknown", line=2, list_type="startup") -> SeedRow:
    return SeedRow(line=line, company=company, list_type=list_type, category="startup", national_top=False, city_group="",
                   city="", sector="AI", why_listed="Fixture", source_url="https://lists.example/startups",
                   careers_url=careers_url, ats=ats)


class FakeWeb:
    """Serves canned responses by URL and records every request; never touches the network.

    A route is (status, body) or (status, body, final_url) for a page reached through a redirect."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, *, timeout, headers, max_bytes):
        self.calls.append(url)
        status, body, *final = self.routes.get(url, (404, ""))
        return httpx.Response(status, text=body, request=httpx.Request("GET", final[0] if final else url))


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.moment = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds

    def utcnow(self):
        return self.moment


class MatchTests(unittest.TestCase):
    def test_board_urls_name_the_ats_and_the_board(self):
        cases = {"https://boards.greenhouse.io/examplecorp": ("greenhouse", "examplecorp"),
                 "https://job-boards.greenhouse.io/examplecorp/jobs/123": ("greenhouse", "examplecorp"),
                 "https://boards.greenhouse.io/embed/job_board?for=examplecorp": ("greenhouse", "examplecorp"),
                 "https://jobs.lever.co/examplelabs": ("lever", "examplelabs"),
                 "https://jobs.eu.lever.co/examplelabs/": ("lever", "examplelabs"),
                 "https://jobs.ashbyhq.com/example": ("ashby", "example"),
                 "https://jobs.smartrecruiters.com/ExampleCorp": ("smartrecruiters", "ExampleCorp"),
                 "https://example.wd3.myworkdayjobs.com/en-US/Careers": ("workday", "example.wd3.myworkdayjobs.com"),
                 "https://example.zohorecruit.in/jobs/Careers": ("zohorecruit", "example.zohorecruit.in"),
                 "https://example.darwinbox.in/ms/candidate/careers": ("darwinbox", "example.darwinbox.in"),
                 "https://example.keka.com/careers": ("keka", "example.keka.com"),
                 "https://example.eightfold.ai/careers": ("eightfold", "example.eightfold.ai"),
                 "https://apply.workable.com/example/": ("workable", "example")}
        for url, expected in cases.items():
            with self.subTest(url=url):
                hit = match_url(url)
                self.assertIsNotNone(hit, url)
                self.assertEqual((hit.ats, hit.key), expected)
        self.assertEqual(match_url("https://jobs.eu.lever.co/examplelabs").region, "eu")

    def test_an_ordinary_careers_url_matches_nothing(self):
        for url in ("https://www.example.com/careers", "https://careers.example.com/", "https://www.greenhouse.io/",
                    "https://jobs.lever.co/", "https://example.com/blog/why-we-chose-lever"):
            self.assertIsNone(match_url(url), url)

    def test_a_page_is_matched_by_the_board_it_embeds_or_links(self):
        embed = match_page(fixture_text("careers_greenhouse_embed.html"))
        self.assertEqual((embed.ats, embed.key), ("greenhouse", "examplecorp"))
        self.assertIn("greenhouse.io/embed/job_board/js?for=examplecorp", embed.quote)
        links = match_page(fixture_text("careers_lever_links.html"))
        self.assertEqual((links.ats, links.key), ("lever", "examplelabs"))
        self.assertIsNone(match_page(fixture_text("careers_plain.html")))


class DetectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        self.clock = Clock()

    def fetcher(self, web, **options):
        return DetectFetcher(get=web.get, clock=self.clock.time, sleep=self.clock.sleep, cache_dir=self.cache,
                             now=self.clock.utcnow, **options)

    async def test_a_board_url_is_confirmed_by_its_public_api_without_reading_any_page(self):
        web = FakeWeb({GREENHOUSE_API: (200, fixture_text("greenhouse_jobs.json"))})
        found = await detect_company(row(careers_url="https://job-boards.greenhouse.io/examplecorp", ats="greenhouse"),
                                     self.fetcher(web))
        self.assertEqual((found.status, found.ats, found.key, found.method), ("confirmed", "greenhouse", "examplecorp", "url_pattern"))
        self.assertEqual(found.fetched, len(json.loads(fixture_text("greenhouse_jobs.json"))["jobs"]))
        self.assertEqual(found.india, 2)
        self.assertEqual(web.calls, [GREENHOUSE_API])

    async def test_a_careers_page_that_links_a_board_is_read_after_robots_and_then_confirmed(self):
        page = "https://www.examplelabs.com/careers"
        web = FakeWeb({page: (200, fixture_text("careers_lever_links.html")), LEVER_API: (200, fixture_text("lever_postings.json"))})
        found = await detect_company(row(careers_url=page), self.fetcher(web))
        self.assertEqual((found.status, found.ats, found.key, found.method), ("confirmed", "lever", "examplelabs", "page_link"))
        self.assertEqual(found.fetched, len(json.loads(fixture_text("lever_postings.json"))))
        self.assertIn("jobs.lever.co/examplelabs", found.evidence)
        self.assertEqual(web.calls, ["https://www.examplelabs.com/robots.txt", page, LEVER_API])

    async def test_a_redirect_to_a_board_is_detected_from_the_final_address(self):
        page = "https://careers.example.com/"
        web = FakeWeb({page: (200, "<html>jobs</html>", "https://example.wd3.myworkdayjobs.com/en-US/Careers")})
        found = await detect_company(row(careers_url=page), self.fetcher(web))
        self.assertEqual((found.status, found.ats, found.key, found.method),
                         ("detected", "workday", "example.wd3.myworkdayjobs.com", "redirect"))
        self.assertIsNone(found.fetched)

    async def test_a_board_whose_api_does_not_answer_is_detected_not_confirmed(self):
        web = FakeWeb({})
        found = await detect_company(row(careers_url="https://jobs.ashbyhq.com/example"), self.fetcher(web))
        self.assertEqual((found.status, found.ats, found.fetched), ("detected", "ashby", None))
        self.assertIn("HTTP 404", found.note)
        self.assertEqual(web.calls, [ASHBY_API])

    async def test_a_page_without_a_board_is_not_detected(self):
        page = "https://www.example.com/careers"
        found = await detect_company(row(careers_url=page), self.fetcher(FakeWeb({page: (200, fixture_text("careers_plain.html"))})))
        self.assertEqual((found.status, found.ats, found.method), ("not_detected", "", ""))

    async def test_robots_disallow_means_the_page_is_not_read(self):
        page = "https://www.example.com/careers"
        web = FakeWeb({"https://www.example.com/robots.txt": (200, "User-agent: *\nDisallow: /careers\n"),
                       page: (200, fixture_text("careers_greenhouse_embed.html"))})
        found = await detect_company(row(careers_url=page), self.fetcher(web))
        self.assertEqual(found.status, "not_read")
        self.assertIn("robots.txt", found.note)
        self.assertEqual(web.calls, ["https://www.example.com/robots.txt"])

    async def test_an_error_status_or_a_failed_request_is_not_read(self):
        page = "https://www.example.com/careers"
        found = await detect_company(row(careers_url=page), self.fetcher(FakeWeb({page: (403, "Forbidden")})))
        self.assertEqual((found.status, found.note), ("not_read", "careers page returned HTTP 403"))

        class Broken(FakeWeb):
            async def get(self, url, **kwargs):
                if url == page:
                    raise httpx.ConnectTimeout("slow")
                return await super().get(url, **kwargs)
        found = await detect_company(row(careers_url=page), self.fetcher(Broken({})))
        self.assertEqual((found.status, found.note), ("not_read", "careers page could not be read (ConnectTimeout)"))

    async def test_a_429_stops_every_later_request_to_that_host(self):
        first, second = "https://www.example.com/careers", "https://www.example.com/jobs"
        web = FakeWeb({first: (429, "slow down"), second: (200, fixture_text("careers_plain.html"))})
        results = await detect_all([row(careers_url=first), row("Second Corp", careers_url=second, line=3)], self.fetcher(web))
        self.assertEqual([found.status for found in results], ["not_read", "not_read"])
        self.assertIn("429", results[0].note)
        self.assertNotIn(second, web.calls)

    async def test_no_careers_url_and_never_fetched_sites_send_no_request(self):
        web = FakeWeb({})
        fetcher = self.fetcher(web)
        missing = await detect_company(row(), fetcher)
        blocked = await detect_company(row(careers_url="https://www.linkedin.com/company/example/jobs"), fetcher)
        self.assertEqual((missing.status, blocked.status), ("no_careers_url", "not_read"))
        self.assertIn("never fetched", blocked.note)
        self.assertEqual(web.calls, [])
        self.assertEqual(fetcher.requests, 0)

    async def test_a_second_run_answers_from_the_cache_until_it_expires(self):
        page = "https://www.examplelabs.com/careers"
        routes = {page: (200, fixture_text("careers_lever_links.html")), LEVER_API: (200, fixture_text("lever_postings.json"))}
        first = await detect_company(row(careers_url=page), self.fetcher(FakeWeb(routes), max_age_hours=24))
        web = FakeWeb(routes)
        fetcher = self.fetcher(web, max_age_hours=24)
        again = await detect_company(row(careers_url=page), fetcher)
        self.assertEqual(again, first)
        self.assertEqual((web.calls, fetcher.requests, fetcher.cache_hits), ([], 0, 2))
        self.clock.moment += timedelta(hours=25)
        web = FakeWeb(routes)
        await detect_company(row(careers_url=page), self.fetcher(web, max_age_hours=24))
        self.assertIn(page, web.calls)

    async def test_a_cached_redirect_keeps_its_final_address(self):
        page = "https://careers.example.com/"
        routes = {page: (200, "<html>jobs</html>", "https://example.wd3.myworkdayjobs.com/en-US/Careers")}
        await detect_company(row(careers_url=page), self.fetcher(FakeWeb(routes)))
        web = FakeWeb(routes)
        found = await detect_company(row(careers_url=page), self.fetcher(web))
        self.assertEqual((found.status, found.ats, found.method, web.calls), ("detected", "workday", "redirect", []))


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_report_counts_come_from_the_results(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        clock = Clock()
        page = "https://www.example.com/careers"
        web = FakeWeb({GREENHOUSE_API: (200, fixture_text("greenhouse_jobs.json")), page: (200, fixture_text("careers_plain.html"))})
        rows = [row("Example Corp", "https://boards.greenhouse.io/examplecorp", ats="lever", line=2),
                row("Plain Services", page, line=3, list_type="mnc_gcc"), row("No Page Ltd", line=4), row("Also None", line=5)]
        fetcher = DetectFetcher(get=web.get, clock=clock.time, sleep=clock.sleep, cache_dir=Path(directory.name), now=clock.utcnow)
        results = await detect_all(rows, fetcher)
        report = render_report(SeedFile(rows=rows, problems=[], sha256="ab" * 32), results, run_at=clock.moment,
                               requests=fetcher.requests, cache_hits=fetcher.cache_hits, seed_name="seeds/companies_seed.csv")
        self.assertIn("| confirmed | 1 |", report)
        self.assertIn("| not_detected | 1 |", report)
        self.assertIn("| no_careers_url | 2 |", report)
        self.assertIn("| Total | 4 |", report)
        self.assertIn("| Example Corp | greenhouse | examplecorp | url_pattern | 4 | 2 |", report)
        self.assertIn("| Example Corp | lever | greenhouse |", report)       # the list said lever; the URL says greenhouse
        self.assertIn("Plain Services", report)
        self.assertIn(f"Requests sent: {fetcher.requests}", report)
        self.assertIn("abababababababab", report)
        self.assertIn("L0", report)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("detect_ats", ROOT / "scripts" / "detect_ats.py")
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def seed(self, rows):
        path = self.directory / "companies_seed.csv"
        path.write_text("\n".join([",".join(COLUMNS)] + [",".join(cells) for cells in rows]) + "\n", encoding="utf-8")
        return path

    def run_main(self, *arguments, **options):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.script.main([str(argument) for argument in arguments], **options)
        return code, output.getvalue()

    def test_validate_only_reports_problems_and_sends_nothing(self):
        good = ["Example Corp", "startup", "startup", "", "", "", "AI", "Fixture", "https://lists.example/a", "", "unknown"]
        code, output = self.run_main("--csv", self.seed([good]), "--expect-rows", 1, "--validate-only")
        self.assertEqual(code, 0)
        self.assertIn("1 rows, 0 problems", output)
        code, output = self.run_main("--csv", self.seed([good[:1] + ["unicorn"] + good[2:]]), "--expect-rows", 1, "--validate-only")
        self.assertEqual(code, 1)
        self.assertIn("line 2: list_type 'unicorn'", output)

    def test_a_run_writes_the_report_and_refuses_to_run_in_demo_mode(self):
        board = ["Example Corp", "startup", "startup", "", "", "", "AI", "Fixture", "https://lists.example/a",
                 "https://boards.greenhouse.io/examplecorp", "greenhouse"]
        web = FakeWeb({GREENHOUSE_API: (200, fixture_text("greenhouse_jobs.json"))})
        clock = Clock()
        fetcher = DetectFetcher(get=web.get, clock=clock.time, sleep=clock.sleep, cache_dir=self.directory / "cache", now=clock.utcnow)
        report = self.directory / "ats_detection.md"
        code, output = self.run_main("--csv", self.seed([board]), "--expect-rows", 1, "--report", report, fetcher=fetcher)
        self.assertEqual(code, 0)
        self.assertIn("| confirmed | 1 |", report.read_text(encoding="utf-8"))
        self.assertIn("confirmed 1", output)
        from unittest.mock import patch
        with patch.object(self.script.settings, "demo_mode", True):
            code, output = self.run_main("--csv", self.seed([board]), "--expect-rows", 1, "--report", report, fetcher=fetcher)
        self.assertEqual(code, 2)
        self.assertIn("DEMO_MODE", output)


if __name__ == "__main__":
    unittest.main()
