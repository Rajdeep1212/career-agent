"""Q2c2: the ATS detection report states the slug-probe result and the pollable companies by list and city."""
import unittest
from datetime import datetime, timezone

from app.sources.ats_detect import Detection, pollable, render_report
from app.sources.company_seed import SeedFile, SeedRow


def seed_row(line, company, list_type, city_group, careers_url="", ats="unknown") -> SeedRow:
    return SeedRow(line=line, company=company, list_type=list_type, category="startup", national_top=False, city_group=city_group,
                   city=city_group, sector="AI", why_listed="Fixture", source_url="https://lists.example/a", careers_url=careers_url,
                   ats=ats)


def found(row: SeedRow, status, ats="", fetched=None, india=None, method="url_pattern") -> Detection:
    return Detection(line=row.line, company=row.company, list_type=row.list_type, careers_url=row.careers_url, declared_ats=row.ats,
                     status=status, ats=ats, key="board" if ats else "", method=method if ats else "", fetched=fetched, india=india)


class ProbeReportTests(unittest.TestCase):
    def setUp(self):
        self.rows = [seed_row(2, "Example Labs", "startup", "Pune", "https://boards.greenhouse.io/examplelabs", "greenhouse"),
                     seed_row(3, "Two City Corp", "mnc_gcc", "Pune;Chennai", "https://jobs.lever.co/twocity", "lever"),
                     seed_row(4, "Widget Works", "startup", "", "https://apply.workable.com/widgetworks", "workable"),
                     seed_row(5, "Page Board", "startup", "Chennai", "https://job-boards.greenhouse.io/pageboard", "greenhouse"),
                     seed_row(6, "No Page", "startup", "Pune")]
        self.results = [found(self.rows[0], "pollable", "greenhouse", 4, 2), found(self.rows[1], "pollable", "lever", 9, 9),
                        found(self.rows[2], "detected", "workable"), found(self.rows[3], "detected", "greenhouse", 3, 1, "html_board"),
                        found(self.rows[4], "no_careers_url")]

    def test_pollable_means_a_board_that_passed_the_rule(self):
        self.assertEqual([item.company for item in pollable(self.results)], ["Example Labs", "Two City Corp"])

    def report(self, **options):
        return render_report(SeedFile(rows=self.rows, problems=[], sha256="cd" * 32), self.results,
                             run_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc), requests=3, cache_hits=5,
                             seed_name="seeds/companies_seed.csv", **options)

    def test_the_report_without_a_probe_has_no_probe_section(self):
        self.assertNotIn("Slug probe", self.report())

    def test_the_probe_section_counts_the_review_rows_and_the_pollable_companies(self):
        review = [{"company": "Example Labs", "city": "Pune", "ats": "greenhouse", "slug": "examplelabs", "status": "pollable",
                   "jobs_total": "4", "jobs_india": "2", "evidence": "board name 'Example Labs' matches; 2 of 4 jobs in India"},
                  {"company": "Widget Works", "city": "", "ats": "workable", "slug": "widgetworks", "status": "pollable",
                   "jobs_total": "6", "jobs_india": "1", "evidence": "board name 'Widget Works' matches; 1 of 6 jobs in India"},
                  {"company": "Maybe Corp", "city": "Pune", "ats": "lever", "slug": "maybe", "status": "stale_no_india",
                   "jobs_total": "12", "jobs_india": "0", "evidence": "no India job among 12"}]
        summary = {"run_at": "2026-10-01T11:00:00+00:00", "companies": 262, "with_hit": 3, "slugs_tried": 374, "requests": 1800,
                   "cache_hits": 0, "delay_seconds": 2.0, "stopped": {"lever": "api.lever.co answered HTTP 429"},
                   "not_sent": {"lever": 40}, "errors": {"ashby": 2}}
        report = self.report(review=review, probe=summary, baseline_pollable=1)
        self.assertIn("## Slug probe", report)
        self.assertIn("| greenhouse | 1 | 0 | 0 |", report)
        self.assertIn("| workable | 1 | 0 | 0 |", report)
        self.assertIn("| lever | 0 | 1 | 0 |", report)
        self.assertIn("| Total | 2 | 1 | 0 |", report)
        # Boards that answered, detection and the probe together, one row per distinct board.
        self.assertIn("## Boards that answered, by ATS", report)
        self.assertIn("| lever | 2 | 1 | Two City Corp |", report)
        self.assertIn("| greenhouse | 2 | 2 | Example Labs |", report)
        self.assertIn("| workable | 1 | 1 | Widget Works |", report)
        self.assertIn("262 companies", report)
        self.assertIn("Requests sent: 1800", report)
        self.assertIn("lever: api.lever.co answered HTTP 429 (40 slugs not tried)", report)
        self.assertIn("ashby 2", report)
        self.assertIn("Pollable companies: before 1, after 2", report)
        self.assertIn("| startup | 1 |", report)
        self.assertIn("| mnc_gcc | 1 |", report)
        self.assertIn("| Pune | 2 |", report)                  # a company in two cities counts in both
        self.assertIn("| Chennai | 1 |", report)
        self.assertIn("Widget Works", report)                  # confirmed by the probe, no adapter yet
        self.assertIn("Page Board", report)                    # confirmed from the board page only


if __name__ == "__main__":
    unittest.main()
