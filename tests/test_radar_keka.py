"""Keka adapter against two real tenant responses recorded once on 9 Oct 2026 (disprz, gokwik); the network is never used.

The whole suite runs with live sockets and DNS refused (tests/_offline.py), and FakeFetcher answers only the URLs it is given.
"""
import json
import unittest
from datetime import date

from app.sources import board_rule, sync
from app.sources.adapters import SourceError, keka
from app.sources.registry import CompanyEntry, SourceSpec
from radar_helpers import FakeFetcher, company, fixture_text

TODAY = date(2026, 10, 9)                       # the day the fixtures were recorded


def url(tenant: str) -> str:
    return f"https://{tenant}.keka.com/careers/api/jobs/default/active"


def entry(tenant: str, name: str = "Example Corp") -> CompanyEntry:
    return company({"type": "keka", "tenant": tenant}, name=name, unofficial=True)


RECORD = {"id": 7, "title": "Graduate Engineer Trainee", "description": "<p>Freshers welcome.</p>", "experience": "0-1",
          "jobLocations": [{"name": "Pune", "city": "Pune", "state": "MH", "countryCode": "IN", "countryName": "India"}],
          "publishedOn": "2026-10-01T06:00:00Z", "publishedSinceDays": 8, "salaryRangeFormat": ""}


class KekaTests(unittest.IsolatedAsyncioTestCase):
    async def test_disprz_keeps_the_india_jobs_and_maps_each_field(self):
        fetcher = FakeFetcher({url("disprz"): fixture_text("keka_disprz.json")})
        result = await keka.fetch(entry("disprz", "Disprz"), fetcher, today=TODAY)
        self.assertEqual((result.fetched, result.india, result.complete, result.date_mismatches), (5, 3, True, 0))
        self.assertEqual([job.source_job_id for job in result.jobs], ["140799", "135014", "131529"])    # the two Manila jobs are left out
        first = result.jobs[0]
        self.assertEqual((first.company, first.title, first.location), ("Disprz", "AI Content Operations Intern", "Chennai, TN, India"))
        self.assertEqual(first.posted_date, "2026-09-04T05:59:54.06Z")                 # publishedOn, as the feed gave it
        self.assertEqual(str(first.application_url), "https://disprz.keka.com/careers/jobdetails/140799")
        self.assertEqual((first.source, first.official_application), ("Company Radar", True))
        self.assertEqual([(ref.source, ref.source_job_id) for ref in first.sources], [("Keka", "140799")])
        self.assertIn("Experience: 0-1", first.description)
        self.assertNotIn("<div>", first.description)
        self.assertEqual(result.jobs[1].salary, "INR 6,00,000.00 - 10,00,000.00")      # the feed's own text; none is made up
        self.assertIsNone(first.salary)

    async def test_gokwik_every_job_is_in_india_and_labelled_keka(self):
        fetcher = FakeFetcher({url("gokwik"): fixture_text("keka_gokwik.json")})
        result = await keka.fetch(entry("gokwik", "GoKwik"), fetcher, today=TODAY)
        self.assertEqual((result.fetched, result.india, result.date_mismatches), (25, 25, 0))
        self.assertEqual(len({job.source_job_id for job in result.jobs}), 25)
        self.assertTrue(all([ref.source for ref in job.sources] == ["Keka"] for job in result.jobs))
        self.assertEqual(result.jobs[0].posted_date, "2026-10-08T06:55:05.333Z")
        self.assertEqual(result.jobs[2].location, "Bangalore, KA, India")

    async def test_robots_txt_is_checked_because_the_feed_is_not_a_documented_api(self):
        fetcher = FakeFetcher({url("disprz"): "[]"})
        await keka.fetch(entry("disprz"), fetcher, today=TODAY)
        self.assertEqual(fetcher.calls, [(url("disprz"), True)])

    async def test_an_empty_board_is_a_complete_listing_of_no_jobs(self):
        result = await keka.fetch(entry("empty"), FakeFetcher({url("empty"): "[]"}), today=TODAY)
        self.assertEqual((result.jobs, result.fetched, result.india, result.complete), ([], 0, 0, True))

    async def test_a_malformed_record_is_skipped_and_counted_never_guessed(self):
        records = [RECORD, "not a job", {"title": "No id", "jobLocations": RECORD["jobLocations"]},
                   {"id": 8, "title": "", "jobLocations": RECORD["jobLocations"]},
                   {**RECORD, "id": 9, "jobLocations": None},                              # no location: not shown as an India job
                   {**RECORD, "id": 10, "publishedOn": "last week", "publishedSinceDays": 3}]   # unreadable date: no date, not one made from the day count
        result = await keka.fetch(entry("odd"), FakeFetcher({url("odd"): json.dumps(records)}), today=TODAY)
        self.assertEqual((result.fetched, result.india, result.malformed), (6, 2, 3))
        self.assertEqual([(job.source_job_id, job.posted_date) for job in result.jobs], [("7", "2026-10-01T06:00:00Z"), ("10", None)])

    async def test_a_date_that_disagrees_with_the_day_count_is_counted(self):
        records = [RECORD, {**RECORD, "id": 8, "publishedSinceDays": 40}]
        result = await keka.fetch(entry("odd"), FakeFetcher({url("odd"): json.dumps(records)}), today=TODAY)
        self.assertEqual((result.india, result.date_mismatches), (2, 1))
        self.assertEqual(result.jobs[1].posted_date, "2026-10-01T06:00:00Z")           # publishedOn stays the posted date

    async def test_an_answer_that_is_not_a_list_or_not_200_is_an_error(self):
        with self.assertRaises(SourceError):
            await keka.fetch(entry("odd"), FakeFetcher({url("odd"): '{"error": "x"}'}), today=TODAY)
        with self.assertRaises(SourceError) as caught:
            await keka.fetch(entry("gone"), FakeFetcher({}), today=TODAY)
        self.assertEqual(caught.exception.http_status, 404)

    def test_the_tenant_must_be_a_plain_subdomain_label(self):
        self.assertEqual(SourceSpec(type="keka", tenant="gokwik").tenant, "gokwik")
        for bad in ("", "evil.example/x", "a.b", "UPPER", "-x"):
            with self.assertRaises(ValueError, msg=bad):
                SourceSpec(type="keka", tenant=bad)


class KekaSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_sync_reads_a_keka_company_through_the_adapter(self):
        fetcher = FakeFetcher({url("disprz"): fixture_text("keka_disprz.json")})
        outcome = await sync.sync_company(entry("disprz", "Disprz"), fetcher, today=TODAY, dry_run=True)
        self.assertEqual((outcome.status, outcome.fetched, outcome.india, outcome.listed, outcome.note), ("ok", 5, 3, 3, ""))

    async def test_odd_records_are_reported_in_the_note_and_the_run_is_still_ok(self):
        records = [RECORD, "not a job", {**RECORD, "id": 8, "publishedSinceDays": 40}]
        outcome = await sync.sync_company(entry("odd"), FakeFetcher({url("odd"): json.dumps(records)}), today=TODAY, dry_run=True)
        self.assertEqual((outcome.status, outcome.india), ("ok", 2))
        self.assertEqual(outcome.note, "1 records could not be read; 1 posted dates disagree with the source's day count")


class KekaBoardRuleTests(unittest.IsolatedAsyncioTestCase):
    """The one pollable rule (board_rule.judge) is unchanged; Keka only gains a reader."""

    async def test_the_recorded_boards_are_judged_by_the_same_rule(self):
        fetcher = FakeFetcher({url("disprz"): fixture_text("keka_disprz.json"), url("gokwik"): fixture_text("keka_gokwik.json")})
        disprz = await board_rule.read_board("keka", "disprz", fetcher)
        self.assertEqual((disprz.total, disprz.india), (5, 3))
        self.assertEqual(board_rule.judge(disprz, name_ok=True, today=TODAY), ("pollable", "3 of 5 jobs in India; newest posting 2026-09-04"))
        gokwik = await board_rule.read_board("keka", "gokwik", fetcher)
        self.assertEqual(board_rule.judge(gokwik, name_ok=True, today=TODAY), ("pollable", "25 of 25 jobs in India; newest posting 2026-10-08"))
        self.assertEqual(fetcher.calls, [(url("disprz"), True), (url("gokwik"), True)])

    async def test_an_empty_or_old_board_is_not_pollable(self):
        old = [{**RECORD, "publishedOn": "2025-12-01T00:00:00Z"}]
        fetcher = FakeFetcher({url("empty"): "[]", url("old"): json.dumps(old)})
        self.assertEqual(board_rule.judge(await board_rule.read_board("keka", "empty", fetcher), name_ok=True, today=TODAY)[0], "stale_no_india")
        self.assertEqual(board_rule.judge(await board_rule.read_board("keka", "old", fetcher), name_ok=True, today=TODAY)[0], "stale_no_india")


if __name__ == "__main__":
    unittest.main()
