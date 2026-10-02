"""Q2c2: careers boards for seed companies that list no careers_url, found by probing public job APIs
with a few slugs made from the company name. Fictional payloads in the shape each API returns; a fake
web only."""
import contextlib
import csv
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from app.sources.ats_detect import DetectFetcher
from app.sources.board_probe import (REVIEW_COLUMNS, confirmed_updates, name_match, probe_all, probe_company, read_review,
                                     rejudge, slug_candidates, write_review)
from app.sources.company_seed import COLUMNS, SeedRow
from test_ats_detect import Clock, FakeWeb

ROOT = Path(__file__).resolve().parents[1]
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true"
GREENHOUSE_BOARD = "https://boards-api.greenhouse.io/v1/boards/{}"
LEVER = "https://api.lever.co/v0/postings/{}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}"
SMART = "https://api.smartrecruiters.com/v1/companies/{}/postings?limit=100"
SMART_INDIA = "https://api.smartrecruiters.com/v1/companies/{}/postings?country=in&limit=100&offset=0"
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/{}"


def row(company, line=2, city_group="Pune", list_type="startup") -> SeedRow:
    return SeedRow(line=line, company=company, list_type=list_type, category="startup", national_top=False, city_group=city_group,
                   city=city_group, sector="AI", why_listed="Fixture", source_url="https://lists.example/startups",
                   careers_url="", ats="unknown")


def greenhouse_jobs(*locations, company_name="Example Labs"):
    return json.dumps({"jobs": [{"id": index, "title": "Engineer", "location": {"name": place}, "company_name": company_name,
                                 "absolute_url": f"https://boards.greenhouse.io/x/jobs/{index}", "content": "Build things.",
                                 "first_published": "2026-09-20T09:00:00Z"}
                                for index, place in enumerate(locations, 1)], "meta": {"total": len(locations)}})


def lever_postings(*jobs):
    return json.dumps([{"id": f"id-{index}", "text": "Engineer", "categories": {"location": place}, "country": country,
                        "descriptionPlain": text, "hostedUrl": f"https://jobs.lever.co/x/id-{index}", "createdAt": 1790000000000}
                       for index, (place, country, text) in enumerate(jobs, 1)])


class SlugTests(unittest.TestCase):
    def test_two_to_four_slugs_come_from_the_name(self):
        cases = {"Example Labs": ["examplelabs", "example", "example-labs"],
                 "Tata Consultancy Services (TCS)": ["tataconsultancyservices", "tataconsultancy", "tata-consultancy-services", "tcs"],
                 "Growfin.ai": ["growfinai", "growfin", "growfin-ai"],
                 "Bain & Company": ["baincompany", "bain", "bain-company"],
                 "Zeta": ["zeta"],
                 "The Souled Store Pvt. Ltd.": ["thesouledstore", "souledstore", "the-souled-store"]}
        for company, expected in cases.items():
            self.assertEqual(slug_candidates(company), expected, company)

    def test_slugs_are_few_distinct_and_never_tiny(self):
        for company in ("A B", "X", "Example Labs (EL)", "One Two Three Four Five Technologies India (OTTF)"):
            slugs = slug_candidates(company)
            self.assertLessEqual(len(slugs), 4)
            self.assertEqual(len(slugs), len(set(slugs)))
            self.assertTrue(all(len(slug) >= 3 for slug in slugs), slugs)


class NameMatchTests(unittest.TestCase):
    def test_exact_partial_and_none(self):
        self.assertEqual(name_match("Example Labs", "Example Labs"), "exact")
        self.assertEqual(name_match("Example Labs", "Example Labs Pvt. Ltd."), "exact")
        self.assertEqual(name_match("Tata Consultancy Services (TCS)", "TCS"), "exact")
        self.assertEqual(name_match("Example Labs", "Example"), "exact")              # the name without a common suffix
        self.assertEqual(name_match("Example Labs", "Example Health Group"), "partial")
        self.assertEqual(name_match("Example Labs", "Sample Works"), "none")
        self.assertEqual(name_match("Example Labs", ""), "none")
        self.assertEqual(name_match("Ola", "Olam Agri"), "none")                      # a short name inside another word


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.clock = Clock()

    def fetcher(self, web):
        return DetectFetcher(get=web.get, delay=2.0, clock=self.clock.time, sleep=self.clock.sleep, cache_dir=self.directory / "cache",
                             now=self.clock.utcnow)

    async def test_a_greenhouse_board_with_the_same_name_and_an_india_job_is_confirmed(self):
        web = FakeWeb({GREENHOUSE.format("examplelabs"): (200, greenhouse_jobs("Pune, India", "London", "Remote - India"))})
        hits = await probe_company(row("Example Labs"), self.fetcher(web))
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual((hit.ats, hit.slug, hit.status, hit.jobs_total, hit.jobs_india), ("greenhouse", "examplelabs", "pollable", 3, 2))
        self.assertEqual(hit.board_url, "https://boards.greenhouse.io/examplelabs")
        self.assertIn("board name 'Example Labs'", hit.evidence)

    async def test_only_public_job_apis_are_queried_and_robots_is_not_fetched_for_them(self):
        web = FakeWeb({})
        await probe_company(row("Example Labs"), self.fetcher(web))
        hosts = {call.split("/")[2] for call in web.calls}
        self.assertEqual(hosts, {"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com", "api.smartrecruiters.com",
                                 "apply.workable.com"})
        self.assertFalse([call for call in web.calls if call.endswith("robots.txt")])
        self.assertEqual(len(web.calls), 3 * 5)                         # three slugs, five APIs, nothing found

    async def test_a_board_of_another_company_or_without_india_jobs_is_only_probable(self):
        other = FakeWeb({GREENHOUSE.format("examplelabs"): (200, greenhouse_jobs("Pune, India", company_name="Sample Works"))})
        hit = (await probe_company(row("Example Labs"), self.fetcher(other)))[0]
        self.assertEqual((hit.status, hit.jobs_india), ("name_mismatch", 1))
        self.assertIn("does not match", hit.evidence)
        self.directory = self.directory / "second"
        abroad = FakeWeb({GREENHOUSE.format("examplelabs"): (200, greenhouse_jobs("London", "Indianapolis, Indiana"))})
        hit = (await probe_company(row("Example Labs"), self.fetcher(abroad)))[0]
        self.assertEqual((hit.status, hit.jobs_total, hit.jobs_india), ("stale_no_india", 2, 0))
        self.assertIn("no India job", hit.evidence)

    async def test_the_board_name_is_asked_for_when_the_jobs_do_not_carry_it(self):
        jobs = json.dumps({"jobs": [{"id": 1, "title": "Engineer", "location": {"name": "Pune, India"}, "content": "",
                                     "first_published": "2026-09-20T09:00:00Z"}]})
        web = FakeWeb({GREENHOUSE.format("examplelabs"): (200, jobs),
                       GREENHOUSE_BOARD.format("examplelabs"): (200, json.dumps({"name": "Example Labs", "content": ""}))})
        hit = (await probe_company(row("Example Labs"), self.fetcher(web)))[0]
        self.assertEqual(hit.status, "pollable")
        self.assertIn(GREENHOUSE_BOARD.format("examplelabs"), web.calls)

    async def test_lever_and_ashby_need_the_company_name_in_the_job_descriptions(self):
        named = lever_postings(("Bangalore", "IN", "Example Labs builds evaluation tools."), ("Remote - US", "US", "Figma."))
        hit = (await probe_company(row("Example Labs"), self.fetcher(FakeWeb({LEVER.format("examplelabs"): (200, named)}))))[0]
        self.assertEqual((hit.ats, hit.status, hit.jobs_total, hit.jobs_india), ("lever", "pollable", 2, 1))
        self.assertIn("1 of 2 job descriptions", hit.evidence)
        self.assertEqual(hit.board_url, "https://jobs.lever.co/examplelabs")
        self.directory = self.directory / "second"
        unnamed = lever_postings(("Bangalore", "IN", "We build evaluation tools."))
        hit = (await probe_company(row("Example Labs"), self.fetcher(FakeWeb({LEVER.format("examplelabs"): (200, unnamed)}))))[0]
        self.assertEqual(hit.status, "name_mismatch")
        self.directory = self.directory / "third"
        ashby = json.dumps({"jobs": [{"id": "a", "title": "Engineer", "location": "Bengaluru", "isListed": True,
                                      "descriptionPlain": "Join Example Labs.", "jobUrl": "https://jobs.ashbyhq.com/examplelabs/a",
                                      "publishedAt": "2026-09-20T06:00:00.000+00:00"},
                                     {"id": "b", "title": "Hidden", "location": "Bengaluru", "isListed": False, "descriptionPlain": ""}]})
        hit = (await probe_company(row("Example Labs"), self.fetcher(FakeWeb({ASHBY.format("examplelabs"): (200, ashby)}))))[0]
        self.assertEqual((hit.ats, hit.status, hit.jobs_total, hit.jobs_india), ("ashby", "pollable", 1, 1))

    async def test_smartrecruiters_and_workable_use_the_account_name(self):
        posting = {"id": "1", "name": "Engineer", "company": {"identifier": "ExampleLabs", "name": "Example Labs"},
                   "location": {"city": "Pune", "country": "in"}, "releasedDate": "2026-09-18T09:00:00.000Z"}
        abroad = {**posting, "id": "2", "location": {"city": "Berlin", "country": "de"}}
        web = FakeWeb({SMART.format("examplelabs"): (200, json.dumps({"totalFound": 2, "content": [posting, abroad]})),
                       SMART_INDIA.format("examplelabs"): (200, json.dumps({"totalFound": 1, "content": [posting]}))})
        hit = (await probe_company(row("Example Labs"), self.fetcher(web)))[0]
        self.assertEqual((hit.ats, hit.status, hit.jobs_total, hit.jobs_india), ("smartrecruiters", "pollable", 2, 1))
        self.assertEqual(hit.board_url, "https://jobs.smartrecruiters.com/ExampleLabs")
        self.directory = self.directory / "second"
        account = {"name": "Example Labs", "description": "", "jobs": [
            {"title": "Engineer", "shortcode": "A1", "country": "India", "city": "Pune", "url": "https://apply.workable.com/j/A1",
             "published_on": "2026-09-18"},
            {"title": "Designer", "shortcode": "B2", "country": "Germany", "city": "Berlin", "url": "https://apply.workable.com/j/B2"}]}
        web = FakeWeb({WORKABLE.format("example-labs"): (200, json.dumps(account))})
        hit = (await probe_company(row("Example Labs"), self.fetcher(web)))[0]
        self.assertEqual((hit.ats, hit.slug, hit.status, hit.jobs_total, hit.jobs_india), ("workable", "example-labs", "pollable", 2, 1))
        self.assertEqual(hit.board_url, "https://apply.workable.com/example-labs")

    async def test_a_board_whose_newest_posting_is_over_180_days_old_is_only_probable(self):
        # Found in the live run: SmartRecruiters accounts abandoned years ago still return their last posting.
        def account(released):
            posting = {"id": "1", "name": "Engineer", "company": {"identifier": "ExampleLabs", "name": "Example Labs"},
                       "location": {"city": "Pune", "country": "in"}, "releasedDate": released}
            page = json.dumps({"totalFound": 1, "content": [posting]})
            return FakeWeb({SMART.format("examplelabs"): (200, page), SMART_INDIA.format("examplelabs"): (200, page)})
        old = (await probe_company(row("Example Labs"), self.fetcher(account("2018-01-17T09:00:00.000Z"))))[0]
        self.assertEqual(old.status, "stale_no_india")
        self.assertIn("newest posting 2018-01-17 is more than 180 days old", old.evidence)
        self.directory = self.directory / "second"
        recent = (await probe_company(row("Example Labs"), self.fetcher(account("2026-09-18T09:00:00.000Z"))))[0]   # run day: 2026-10-01
        self.assertEqual(recent.status, "pollable")
        self.assertIn("newest posting 2026-09-18", recent.evidence)
        self.directory = self.directory / "third"
        stale = lever_postings(("Bangalore", "IN", "Example Labs builds evaluation tools."))
        stale = json.dumps([{**job, "createdAt": 1500000000000} for job in json.loads(stale)])                    # 2017-07-14
        hit = (await probe_company(row("Example Labs"), self.fetcher(FakeWeb({LEVER.format("examplelabs"): (200, stale)}))))[0]
        self.assertEqual(hit.status, "stale_no_india")
        self.assertIn("newest posting 2017-07-14", hit.evidence)

    async def test_an_empty_board_or_account_is_not_a_hit(self):
        web = FakeWeb({WORKABLE.format("examplelabs"): (200, json.dumps({"name": "Example Labs", "jobs": []})),
                       GREENHOUSE.format("examplelabs"): (200, json.dumps({"jobs": []})),
                       SMART.format("examplelabs"): (200, json.dumps({"totalFound": 0, "content": []})),
                       LEVER.format("examplelabs"): (200, "[]")})
        self.assertEqual(await probe_company(row("Example Labs"), self.fetcher(web)), [])

    async def test_a_confirmed_board_ends_the_search_for_that_company(self):
        web = FakeWeb({GREENHOUSE.format("examplelabs"): (200, greenhouse_jobs("Pune, India"))})
        await probe_company(row("Example Labs"), self.fetcher(web))
        self.assertEqual(len(web.calls), 5)                             # the first slug only

    async def test_a_429_stops_that_api_for_the_rest_of_the_run_and_is_reported(self):
        web = FakeWeb({LEVER.format("examplelabs"): (429, "slow down"),
                       GREENHOUSE.format("othercorp"): (200, greenhouse_jobs("Pune, India", company_name="Other Corp"))})
        run = await probe_all([row("Example Labs"), row("Other Corp", line=3)], self.fetcher(web))
        self.assertEqual([call for call in web.calls if "lever" in call], [LEVER.format("examplelabs")])
        self.assertIn("lever", run.stopped)
        self.assertGreater(run.not_sent["lever"], 0)
        self.assertEqual([(hit.company, hit.status) for hit in run.hits], [("Other Corp", "pollable")])
        self.assertEqual((run.companies, run.with_hit), (2, 1))

    async def test_a_skipped_api_is_never_requested_and_is_reported_as_stopped(self):
        web = FakeWeb({})
        run = await probe_all([row("Example Labs")], self.fetcher(web), skip={"workable": "answered HTTP 429 in the first run"})
        self.assertFalse([call for call in web.calls if "workable" in call])
        self.assertEqual((run.stopped, run.not_sent), ({"workable": "answered HTTP 429 in the first run"}, {"workable": 3}))

    async def test_requests_to_one_api_are_two_seconds_apart(self):
        slept = []

        async def sleep(seconds):
            slept.append(round(seconds, 2))
            self.clock.now += seconds
        web = FakeWeb({})
        fetcher = DetectFetcher(get=web.get, delay=2.0, clock=self.clock.time, sleep=sleep, cache_dir=self.directory / "cache",
                                now=self.clock.utcnow)
        await probe_company(row("Example Labs"), fetcher)
        self.assertTrue(slept)
        self.assertTrue(all(0 < seconds <= 2.0 for seconds in slept), slept)
        self.assertGreaterEqual(self.clock.now - 1000.0, 2.0 * 2)       # three slugs: two waits per API at least

    async def test_a_second_run_sends_nothing(self):
        routes = {GREENHOUSE.format("examplelabs"): (200, greenhouse_jobs("Pune, India"))}
        first = await probe_all([row("Example Labs"), row("Nothing Here", line=3)], self.fetcher(FakeWeb(routes)))
        web = FakeWeb(routes)
        again = await probe_all([row("Example Labs"), row("Nothing Here", line=3)], self.fetcher(web))
        self.assertEqual((web.calls, again.hits), ([], first.hits))


class ReviewFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_review_file_lists_every_hit_and_only_single_confirmed_boards_update_the_seed(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        clock = Clock()
        named = lever_postings(("Pune, India", "IN", "Twoboards is hiring."))
        web = FakeWeb({GREENHOUSE.format("examplelabs"): (200, greenhouse_jobs("Pune, India")),
                       GREENHOUSE.format("maybecorp"): (200, greenhouse_jobs("London", company_name="Maybe Corp")),
                       GREENHOUSE.format("twoboards"): (200, greenhouse_jobs("Pune, India", company_name="Twoboards")),
                       LEVER.format("twoboards"): (200, named)})
        fetcher = DetectFetcher(get=web.get, delay=2.0, clock=clock.time, sleep=clock.sleep, cache_dir=Path(directory.name) / "cache",
                                now=clock.utcnow)
        run = await probe_all([row("Example Labs"), row("Maybe Corp", line=3), row("Twoboards", line=4), row("No Board", line=5)],
                              fetcher)
        path = Path(directory.name) / "review.csv"
        write_review(path, run.hits)
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(list(rows[0]), REVIEW_COLUMNS)
        self.assertEqual(REVIEW_COLUMNS, ["company", "city", "ats", "slug", "status", "jobs_total", "jobs_india", "evidence"])
        self.assertEqual([(item["company"], item["ats"], item["status"]) for item in rows],
                         [("Example Labs", "greenhouse", "pollable"), ("Maybe Corp", "greenhouse", "stale_no_india"),
                          ("Twoboards", "greenhouse", "pollable"), ("Twoboards", "lever", "pollable")])
        self.assertEqual(rows[0]["city"], "Pune")
        self.assertEqual(read_review(path), rows)
        self.assertEqual(confirmed_updates(run.hits),
                         {"Example Labs": {"careers_url": "https://boards.greenhouse.io/examplelabs", "ats": "greenhouse"}})


class RejudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_rows_are_judged_again_from_the_cache_without_any_request(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        clock = Clock()
        routes = {GREENHOUSE.format("examplelabs"): (200, greenhouse_jobs("Pune, India")),
                  GREENHOUSE.format("maybecorp"): (200, greenhouse_jobs("London", company_name="Maybe Corp"))}

        def fetcher(web):
            return DetectFetcher(get=web.get, delay=2.0, clock=clock.time, sleep=clock.sleep, cache_dir=Path(directory.name),
                                 now=clock.utcnow)
        rows = [row("Example Labs"), row("Maybe Corp", line=3)]
        await probe_all(rows, fetcher(FakeWeb(routes)))
        review = [{"company": "Example Labs", "city": "Pune", "ats": "greenhouse", "slug": "examplelabs", "status": "probable",
                   "jobs_total": "1", "jobs_india": "1", "evidence": "old wording"},
                  {"company": "Maybe Corp", "city": "Pune", "ats": "greenhouse", "slug": "maybecorp", "status": "probable",
                   "jobs_total": "1", "jobs_india": "0", "evidence": "old wording"},
                  {"company": "Maybe Corp", "city": "Pune", "ats": "lever", "slug": "gone", "status": "probable",
                   "jobs_total": "4", "jobs_india": "0", "evidence": "old wording"}]
        web = FakeWeb(routes)
        offline = fetcher(web)
        hits = await rejudge(review, rows, offline)
        self.assertEqual([hit.status for hit in hits], ["pollable", "stale_no_india", "probable"])
        self.assertIn("not re-read", hits[2].evidence)
        self.assertEqual((hits[2].jobs_total, web.calls, offline.offline), (4, [], False))


class ScriptTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("probe_boards", ROOT / "scripts" / "probe_boards.py")
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.seed = self.directory / "companies_seed.csv"
        rows = [["Example Labs", "startup", "startup", "", "Pune", "Pune", "AI", "Fixture", "https://lists.example/a", "", "unknown"],
                ["Maybe Corp", "startup", "startup", "", "Pune", "Pune", "AI", "Fixture", "https://lists.example/a", "", "unknown"],
                ["Has Page", "startup", "startup", "", "Pune", "Pune", "AI", "Fixture", "https://lists.example/a",
                 "https://www.haspage.example/careers", "unknown"]]
        self.seed.write_text("\n".join([",".join(COLUMNS)] + [",".join(cells) for cells in rows]) + "\n", encoding="utf-8")
        self.web = FakeWeb({GREENHOUSE.format("examplelabs"): (200, greenhouse_jobs("Pune, India")),
                            GREENHOUSE.format("maybecorp"): (200, greenhouse_jobs("London", company_name="Maybe Corp"))})
        clock = Clock()
        self.fetcher = DetectFetcher(get=self.web.get, delay=2.0, clock=clock.time, sleep=clock.sleep,
                                     cache_dir=self.directory / "cache", now=clock.utcnow)

    def run_main(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.script.main(["--csv", str(self.seed), "--expect-rows", "3", "--review", str(self.directory / "review.csv"),
                                     *arguments], fetcher=self.fetcher)
        return code, output.getvalue()

    def test_only_rows_without_a_careers_url_are_probed_and_the_seed_is_left_alone(self):
        before = self.seed.read_bytes()
        code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertFalse([call for call in self.web.calls if "haspage" in call])
        self.assertEqual(self.seed.read_bytes(), before)
        self.assertEqual([item["company"] for item in read_review(self.directory / "review.csv")], ["Example Labs", "Maybe Corp"])
        self.assertIn("pollable 1, stale_no_india 1, name_mismatch 0", output)
        summary = json.loads((self.directory / "review.meta.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["companies"], summary["with_hit"], summary["pollable"], summary["stale_no_india"]), (2, 2, 1, 1))
        self.assertEqual(summary["requests"], self.fetcher.requests)

    def test_apply_confirmed_changes_only_confirmed_rows(self):
        code, output = self.run_main("--apply-confirmed")
        self.assertEqual(code, 0)
        lines = self.seed.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[1].endswith(",https://boards.greenhouse.io/examplelabs,greenhouse"))
        self.assertTrue(lines[2].endswith(",,unknown"))
        self.assertIn("seed updated: Example Labs", output)

    def test_a_cached_re_run_keeps_the_first_runs_request_counts(self):
        self.run_main()
        sent = self.fetcher.requests
        clock = Clock()
        self.fetcher = DetectFetcher(get=self.web.get, delay=2.0, clock=clock.time, sleep=clock.sleep,
                                     cache_dir=self.directory / "cache", now=clock.utcnow)
        code, _ = self.run_main("--skip", "workable=answered HTTP 429 in the first run")
        summary = json.loads((self.directory / "review.meta.json").read_text(encoding="utf-8"))
        self.assertEqual((code, summary["requests"], summary["first_run"]["requests"]), (0, 0, sent))
        self.assertEqual(summary["stopped"], {"workable": "answered HTTP 429 in the first run"})

    def test_demo_mode_is_refused(self):
        from unittest.mock import patch
        with patch.object(self.script.settings, "demo_mode", True):
            code, output = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(self.web.calls, [])


if __name__ == "__main__":
    unittest.main()
