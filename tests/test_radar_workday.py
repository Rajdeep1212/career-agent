"""Workday JSON feed (cxs) against real tenant responses recorded once on 9 Oct 2026; the network is never used.

Each recorded list was cut to its first three jobs and each detail's description to its first 300 characters; `total`
is as the tenant gave it. The recorded details are of the first listed job, none of them in India, so the tests that
need an India detail change the country and location of a recorded one. FakeFetcher answers only the URLs it is given.
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.sources import sync
from app.sources.adapters import SourceError, workday
from app.sources.registry import CompanyEntry
from app.storage import db, radar_store
from radar_helpers import FakeFetcher, company, fixture_text

INDIA = "c4f78be1a8f14da0ab49ce1162348a5e"     # Workday's id for India, the same in every recorded tenant


def entry(host: str, site: str, name: str = "Example Corp") -> CompanyEntry:
    return company({"type": "workday", "host": host, "sites": [site], "cxs": True}, name=name, unofficial=True)


def list_url(host: str, site: str) -> str:
    return f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}/jobs"


def detail_url(host: str, site: str, path: str) -> str:
    return f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}{path}"


def recorded(name: str) -> dict:
    return json.loads(fixture_text(f"workday_{name}.json"))


def in_india(detail: dict, location: str) -> dict:
    """A recorded detail, moved to India."""
    info = {**detail["jobPostingInfo"], "location": location, "additionalLocations": [], "country": {"descriptor": "India", "id": INDIA}}
    return {**detail, "jobPostingInfo": info}


ACCENTURE = ("accenture.wd103.myworkdayjobs.com", "AccentureCareers")
UNIPHORE = ("uniphore.wd503.myworkdayjobs.com", "Uniphore")
UNIPHORE_INDIA_JOB = "/job/India---Bangalore/Director--Security-Engineering--Application---Cloud-Security-_JR101213"
PLAIN = ("example.wd1.myworkdayjobs.com", "External")


def pages(total: int):
    """A board of `total` Bangalore jobs with no country filter, served 20 at a time."""
    def route(body):
        ids = range(body["offset"], min(body["offset"] + body["limit"], total))
        return {"total": total, "facets": [], "jobPostings": [
            {"title": f"Engineer {n}", "externalPath": f"/job/India---Bangalore/Engineer_JR{n}", "locationsText": "India - Bangalore"} for n in ids]}
    return route


class WorkdayTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_listing_is_asked_again_for_india_when_the_tenant_offers_a_country_filter(self):
        listing, detail = recorded("accenture_list"), recorded("accenture_detail")
        first = listing["jobPostings"][0]["externalPath"]
        fetcher = FakeFetcher({list_url(*ACCENTURE): lambda body: listing, detail_url(*ACCENTURE, first): in_india(detail, "Bengaluru, BDC7A")})
        result = await workday.fetch(entry(*ACCENTURE, name="Accenture"), fetcher)
        self.assertEqual(fetcher.bodies, [{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
                                          {"appliedFacets": {"locationCountry": [INDIA]}, "limit": 20, "offset": 0, "searchText": ""}])
        # The fake returns the same recorded page whatever the filter: 3 jobs listed, 1 with a readable detail.
        self.assertEqual((result.fetched, result.india, result.deferred, result.rejected), (3, 1, 0, []))
        self.assertFalse(result.complete)                                   # total 2000 is Workday's ceiling: absence never closes a job
        job = result.jobs[0]
        self.assertEqual((job.company, job.title, job.source_job_id), ("Accenture", "Banking Strategy Process Consultant", "R00351594"))
        self.assertEqual(job.posted_date, "2026-10-08")                     # startDate from the detail, never the "Posted Today" text
        self.assertEqual(str(job.application_url), "https://accenture.wd103.myworkdayjobs.com/AccentureCareers" + first)
        self.assertEqual(job.location, "Bengaluru, BDC7A, India")           # the detail's country, added to its first location
        self.assertEqual((job.employment_type, job.source, job.official_application), ("Full time", "Company Radar", True))
        self.assertEqual([(ref.source, ref.source_job_id) for ref in job.sources], [("Workday", "R00351594")])
        self.assertTrue(job.description.startswith("We Are:"))
        self.assertNotIn("<", job.description)

    async def test_a_filtered_listing_is_still_checked_against_each_detail(self):
        listing, detail = recorded("accenture_list"), recorded("accenture_detail")          # the recorded detail: New York and 14 more US offices
        fetcher = FakeFetcher({list_url(*ACCENTURE): lambda body: listing, detail_url(*ACCENTURE, listing["jobPostings"][0]["externalPath"]): detail})
        result = await workday.fetch(entry(*ACCENTURE), fetcher)
        self.assertEqual((result.jobs, result.rejected), ([], ["R00351594"]))

    async def test_the_country_filter_is_found_under_the_name_the_tenant_gives_it(self):
        for name, parameter in (("sec", "Location_Country"), ("thomsonreuters", "Location_Country"), ("dxctechnology", "locationCountry")):
            fetcher = FakeFetcher({list_url(*PLAIN): lambda body, name=name: recorded(f"{name}_list")})
            await workday.fetch(entry(*PLAIN), fetcher)
            self.assertEqual(fetcher.bodies[1]["appliedFacets"], {parameter: [INDIA]}, name)

    async def test_without_a_country_filter_only_india_locations_are_opened_and_the_detail_decides(self):
        listing, detail = recorded("uniphore_list"), recorded("dxctechnology_detail")
        url = detail_url(*UNIPHORE, UNIPHORE_INDIA_JOB)
        fetcher = FakeFetcher({list_url(*UNIPHORE): lambda body: listing, url: in_india(detail, "India - Bangalore")})
        result = await workday.fetch(entry(*UNIPHORE, name="Uniphore"), fetcher)
        self.assertEqual(len(fetcher.bodies), 1)                            # no filter offered: asked once
        self.assertEqual(fetcher.calls, [(list_url(*UNIPHORE), True), (url, True)])      # the two Palo Alto jobs are never opened
        self.assertEqual((result.fetched, result.india, result.complete), (3, 1, False))     # 3 read of the 33 the board reports
        self.assertEqual((result.jobs[0].source_job_id, result.jobs[0].location), ("JR101213", "India - Bangalore"))

        abroad = FakeFetcher({list_url(*UNIPHORE): lambda body: listing, url: detail})   # the recorded detail: Philippines
        result = await workday.fetch(entry(*UNIPHORE), abroad)
        self.assertEqual((result.jobs, result.rejected), ([], ["JR101213"]))

    async def test_pages_of_twenty_are_read_until_the_total(self):
        fetcher = FakeFetcher({list_url(*PLAIN): pages(45)})
        result = await workday.fetch(entry(*PLAIN), fetcher, detail_cap=0)
        self.assertEqual([(body["limit"], body["offset"]) for body in fetcher.bodies], [(20, 0), (20, 20), (20, 40)])
        self.assertEqual((result.fetched, result.deferred, result.complete), (45, 45, True))

    async def test_an_answer_shorter_than_its_total_is_not_a_complete_listing(self):
        for listing in ({"total": 450, "jobPostings": []}, {"total": True, "jobPostings": []}, {"jobPostings": []}):
            result = await workday.fetch(entry(*PLAIN), FakeFetcher({list_url(*PLAIN): lambda body, listing=listing: listing}))
            self.assertEqual((result.fetched, result.complete), (0, False), listing)
        empty = await workday.fetch(entry(*PLAIN), FakeFetcher({list_url(*PLAIN): pages(0)}))
        self.assertEqual((empty.fetched, empty.complete), (0, True))

    async def test_a_location_that_only_reads_like_india_does_not_outvote_the_details_country(self):
        detail = recorded("dxctechnology_detail")
        detail["jobPostingInfo"].update(location="Indian Land, SC", additionalLocations=[],
                                        country={"descriptor": "United States of America"})
        url = detail_url(*PLAIN, "/job/India---Bangalore/Engineer_JR0")
        result = await workday.fetch(entry(*PLAIN), FakeFetcher({list_url(*PLAIN): pages(1), url: detail}))
        self.assertEqual((result.jobs, result.rejected), ([], ["JR0"]))
        detail["jobPostingInfo"]["additionalLocations"] = ["Pune"]          # a further location in India still counts
        result = await workday.fetch(entry(*PLAIN), FakeFetcher({list_url(*PLAIN): pages(1), url: detail}))
        self.assertEqual([job.source_job_id for job in result.jobs], ["JR0"])

    async def test_a_long_board_stops_at_the_page_cap_and_is_not_complete(self):
        fetcher = FakeFetcher({list_url(*PLAIN): pages(1000)})
        result = await workday.fetch(entry(*PLAIN), fetcher, detail_cap=0)
        self.assertEqual((len(fetcher.bodies), result.fetched, result.complete), (workday.MAX_PAGES, 20 * workday.MAX_PAGES, False))

    async def test_known_jobs_are_reused_and_new_ones_stop_at_the_cap(self):
        detail = in_india(recorded("dxctechnology_detail"), "India - Bangalore")
        routes = {list_url(*PLAIN): pages(5), **{detail_url(*PLAIN, f"/job/India---Bangalore/Engineer_JR{n}"): detail for n in range(5)}}
        fetcher = FakeFetcher(routes)
        stored = (await workday.fetch(entry(*PLAIN), FakeFetcher(routes), detail_cap=1)).jobs[0]
        result = await workday.fetch(entry(*PLAIN), fetcher, known={"JR0": stored}, skip={"JR1"}, detail_cap=2)
        self.assertEqual([job.source_job_id for job in result.jobs], ["JR0", "JR2", "JR3"])
        self.assertIs(result.jobs[0], stored)
        self.assertEqual((result.deferred, len(fetcher.calls)), (1, 3))     # the list, JR2 and JR3; JR1 was rejected before, JR4 waits

    async def test_the_posted_date_is_only_ever_the_details_start_date(self):
        detail = in_india(recorded("dxctechnology_detail"), "India - Bangalore")
        detail["jobPostingInfo"]["startDate"] = "soon"
        fetcher = FakeFetcher({list_url(*PLAIN): pages(1), detail_url(*PLAIN, "/job/India---Bangalore/Engineer_JR0"): detail})
        result = await workday.fetch(entry(*PLAIN), fetcher)
        self.assertEqual([job.posted_date for job in result.jobs], [None])

    async def test_a_detail_that_cannot_be_read_is_left_for_the_next_run(self):
        fetcher = FakeFetcher({list_url(*PLAIN): pages(2), detail_url(*PLAIN, "/job/India---Bangalore/Engineer_JR0"): "not json"})
        result = await workday.fetch(entry(*PLAIN), fetcher)
        self.assertEqual((result.fetched, result.jobs, result.rejected), (2, [], []))

    async def test_a_malformed_record_is_skipped_and_counted(self):
        good = {"title": "Engineer", "externalPath": "/job/India---Bangalore/Engineer_JR1"}
        odd_paths = [{**good, "externalPath": path} for path in ("/job/../../x", "/job/a\\b", "/job/a?x=1", "/job/a#b", "/job/a b", "/other/a")]
        listing = {"total": 10, "jobPostings": [good, "not a job", {"title": "No path"}, {**good, "title": " "}, *odd_paths]}
        fetcher = FakeFetcher({list_url(*PLAIN): lambda body: listing})
        result = await workday.fetch(entry(*PLAIN), fetcher, detail_cap=0)
        self.assertEqual((result.fetched, result.malformed, result.deferred), (10, 9, 1))

    async def test_an_answer_that_is_not_a_job_list_or_not_200_is_an_error(self):
        with self.assertRaises(SourceError):
            await workday.fetch(entry(*PLAIN), FakeFetcher({list_url(*PLAIN): lambda body: {"error": "x"}}))
        with self.assertRaises(SourceError) as caught:
            await workday.fetch(entry(*PLAIN), FakeFetcher({}))
        self.assertEqual(caught.exception.http_status, 404)


class WorkdaySyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(radar_store, "DB_PATH", Path(directory.name) / "radar.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    async def test_a_job_missing_from_a_short_answer_is_page_checked_not_closed(self):
        paths = [f"/job/India---Bangalore/Engineer_JR{n}" for n in range(2)]
        detail = in_india(recorded("dxctechnology_detail"), "India - Bangalore")
        details = {detail_url(*PLAIN, path): detail for path in paths}
        co = entry(*PLAIN)
        first = await sync.sync_company(co, FakeFetcher({list_url(*PLAIN): pages(2), **details}), today=date(2026, 10, 9))
        self.assertEqual((first.status, first.new), ("ok", 2))
        short = {"total": 2, "jobPostings": [{"title": "Engineer 0", "externalPath": paths[0]}]}       # one of the two the board reports
        fetcher = FakeFetcher({list_url(*PLAIN): lambda body: short, f"https://{PLAIN[0]}/{PLAIN[1]}{paths[1]}": fixture_text("workday_job_page.html")})
        for day in (10, 11, 12):
            outcome = await sync.sync_company(co, fetcher, today=date(2026, 10, day))
            self.assertEqual((outcome.closed, len(radar_store.list_jobs())), (0, 2), day)
        self.assertIn((f"https://{PLAIN[0]}/{PLAIN[1]}{paths[1]}", True), fetcher.calls)

    async def test_the_sync_reads_an_opted_in_company_through_the_json_feed(self):
        url = detail_url(*UNIPHORE, UNIPHORE_INDIA_JOB)
        fetcher = FakeFetcher({list_url(*UNIPHORE): lambda body: recorded("uniphore_list"),
                               url: in_india(recorded("dxctechnology_detail"), "India - Bangalore")})
        outcome = await sync.sync_company(entry(*UNIPHORE, name="Uniphore"), fetcher, today=None, dry_run=True)
        self.assertEqual((outcome.status, outcome.fetched, outcome.india, outcome.listed, outcome.note), ("ok", 3, 1, 1, ""))


if __name__ == "__main__":
    unittest.main()
