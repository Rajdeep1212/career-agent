"""Sitemap + JSON-LD adapter (Workday, Phenom, custom sites); the network is never used."""
import unittest
from datetime import date

from app.sources.adapters import SourceError, sitemap
from radar_helpers import FakeFetcher, company, fixture_text

WD = "https://example.wd1.myworkdayjobs.com/External"
WD_SITEMAP = WD + "/siteMap.xml"
WD_ML = WD + "/job/India-Bengaluru/Machine-Learning-Engineer_JR100001"
WD_GRAD = WD + "/job/Hyderabad-India/Graduate-Software-Engineer_JR100002"
WORKDAY = {"type": "workday", "host": "example.wd1.myworkdayjobs.com", "sites": ["External"], "sitemap_capped": True}

PH = "https://careers.example.com/in/en"
PHENOM = {"type": "sitemap_jsonld", "sitemaps": [PH + "/sitemap_index.xml"], "job_url_pattern": "/job/"}
PH_ROUTES = {PH + "/sitemap_index.xml": fixture_text("phenom_sitemap_index.xml"), PH + "/sitemap1.xml": fixture_text("phenom_sitemap1.xml"),
             PH + "/job/R0001/Supply-Chain-Analyst": fixture_text("phenom_job_india.html"),
             PH + "/job/R0002/Electro-instrumentiste": fixture_text("phenom_job_france.html")}
TODAY = date(2026, 9, 26)


def workday_fetcher() -> FakeFetcher:
    return FakeFetcher({WD_SITEMAP: fixture_text("workday_sitemap.xml"), WD_ML: fixture_text("workday_job_page.html"),
                        WD_GRAD: fixture_text("workday_expired_page.html")})


class WorkdayTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_india_slugs_are_opened_and_counts_come_from_the_sitemap(self):
        fetcher = workday_fetcher()
        result = await sitemap.fetch(company(WORKDAY), fetcher, today=TODAY)
        # 4 job URLs listed (the non-job page is ignored); US and Indiana slugs are never opened.
        self.assertEqual((result.fetched, result.india, result.complete), (4, 1, False))
        self.assertEqual([url for url, _ in fetcher.calls], [WD_SITEMAP, WD_ML, WD_GRAD])
        self.assertTrue(all(check for _, check in fetcher.calls))   # robots.txt is honoured for every request
        self.assertEqual(result.rejected, ["JR100002"])              # validThrough has passed
        job = result.jobs[0]
        self.assertEqual((job.source_job_id, job.title, job.location), ("JR100001", "Machine Learning Engineer", "Bengaluru, India"))
        self.assertEqual(str(job.application_url), WD_ML)
        self.assertTrue(job.fresher_allowed and job.official_application)
        self.assertIn("RAG", job.skills)

    async def test_known_jobs_are_reused_and_the_page_cap_defers_the_rest(self):
        known = (await sitemap.fetch(company(WORKDAY), workday_fetcher(), today=TODAY)).jobs[0]
        fetcher = workday_fetcher()
        result = await sitemap.fetch(company(WORKDAY), fetcher, known={"JR100001": known}, detail_cap=0, today=TODAY)
        self.assertEqual([job.source_job_id for job in result.jobs], ["JR100001"])
        self.assertEqual((result.deferred, [url for url, _ in fetcher.calls]), (1, [WD_SITEMAP]))

    async def test_skipped_ids_are_not_reopened(self):
        fetcher = workday_fetcher()
        await sitemap.fetch(company(WORKDAY), fetcher, skip={"JR100002"}, today=TODAY)
        self.assertNotIn(WD_GRAD, [url for url, _ in fetcher.calls])

    async def test_page_check_closes_missing_and_expired_jobs(self):
        listed = (await sitemap.fetch(company(WORKDAY), workday_fetcher(), today=TODAY)).jobs[0]
        self.assertIsNone(await sitemap.check_page(company(WORKDAY), workday_fetcher(), listed, today=TODAY))
        gone = FakeFetcher({WD_ML: (404, "")})
        self.assertIn("HTTP 404", await sitemap.check_page(company(WORKDAY), gone, listed, today=TODAY) or "")
        expired = FakeFetcher({WD_ML: fixture_text("workday_expired_page.html")})
        self.assertIn("2026-08-01", await sitemap.check_page(company(WORKDAY), expired, listed, today=TODAY) or "")
        with self.assertRaises(SourceError):
            await sitemap.check_page(company(WORKDAY), FakeFetcher({WD_ML: (503, "")}), listed, today=TODAY)

    async def test_uncapped_workday_sitemap_is_complete(self):
        result = await sitemap.fetch(company({**WORKDAY, "sitemap_capped": False}), workday_fetcher(), today=TODAY)
        self.assertTrue(result.complete)

    async def test_a_dtd_is_refused(self):
        fetcher = FakeFetcher({WD_SITEMAP: '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><urlset/>'})
        with self.assertRaises(SourceError):
            await sitemap.fetch(company(WORKDAY), fetcher, today=TODAY)


class SitemapJsonLdTests(unittest.IsolatedAsyncioTestCase):
    async def test_sitemap_index_and_india_decided_by_json_ld_country(self):
        fetcher = FakeFetcher(PH_ROUTES)
        result = await sitemap.fetch(company(PHENOM), fetcher, today=TODAY)
        self.assertEqual((result.fetched, result.india, result.complete), (2, 1, True))
        self.assertEqual(result.rejected, ["R0002"])
        job = result.jobs[0]
        self.assertEqual((job.source_job_id, job.location), ("R0001", "Mumbai, India"))
        self.assertEqual(job.posted_date, "2026-09-22")

    async def test_unreadable_page_is_neither_listed_nor_rejected(self):
        routes = {**PH_ROUTES, PH + "/job/R0001/Supply-Chain-Analyst": "<html>no json-ld</html>"}
        result = await sitemap.fetch(company(PHENOM), FakeFetcher(routes), today=TODAY)
        self.assertEqual((result.india, result.rejected), (0, ["R0002"]))

    async def test_missing_sitemap_is_a_source_error(self):
        with self.assertRaises(SourceError) as caught:
            await sitemap.fetch(company(PHENOM), FakeFetcher({}), today=TODAY)
        self.assertEqual(caught.exception.http_status, 404)

    def test_job_ids(self):
        custom = company({"type": "sitemap_jsonld", "sitemaps": ["https://x.example/s.xml"], "job_url_pattern": "/job-details/"})
        self.assertEqual(sitemap.job_id(custom, "https://x.example/job-details/senior-engineer-12345/"), "senior-engineer-12345")
        self.assertEqual(sitemap.job_id(company(WORKDAY), WD + "/job/2-Locations/Analyst_R-778"), "R-778")

    def test_json_ld_in_a_graph_and_list(self):
        html = ('<script type="application/ld+json">{"@graph": [{"@type": "Organization"}, '
                '{"@type": ["JobPosting"], "title": "X"}]}</script>')
        self.assertEqual((sitemap.job_posting_ld(html) or {}).get("title"), "X")
        self.assertIsNone(sitemap.job_posting_ld('<script type="application/ld+json">{broken</script>'))


if __name__ == "__main__":
    unittest.main()
