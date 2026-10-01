"""Q2c: the company seed list (seeds/companies_seed.csv) is validated, never silently repaired."""
import tempfile
import unittest
from pathlib import Path

from app.sources.company_seed import COLUMNS, EXPECTED_ROWS, fetch_allowed, read_seed, update_rows

ROOT = Path(__file__).resolve().parents[1]
GOOD = {"company": "Example Labs", "list_type": "startup", "category": "startup", "national_top": "yes",
        "city_group": "Pune", "city": "Pune", "sector": "AI", "why_listed": "Listed in a public startup list",
        "source_url": "https://lists.example/startups", "careers_url": "https://jobs.lever.co/examplelabs", "ats": "lever"}


class ReadSeedTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "companies_seed.csv"

    def write(self, rows, header=COLUMNS):
        lines = [",".join(header)] + [",".join(str(row.get(name, "")) for name in header) for row in rows]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def problems(self, **changes):
        self.write([{**GOOD, **changes}])
        return read_seed(self.path, expected_rows=1).problems

    def test_a_valid_file_has_rows_and_no_problems(self):
        self.write([GOOD, {**GOOD, "company": "Other Corp", "national_top": "", "careers_url": "", "ats": "unknown"}])
        seed = read_seed(self.path, expected_rows=2)
        self.assertEqual(seed.problems, [])
        self.assertEqual([row.company for row in seed.rows], ["Example Labs", "Other Corp"])
        self.assertEqual((seed.rows[0].line, seed.rows[0].national_top, seed.rows[1].national_top), (2, True, False))
        self.assertEqual(len(seed.sha256), 64)

    def test_a_wrong_header_is_reported_and_no_row_is_read(self):
        self.write([GOOD], header=[name for name in COLUMNS if name != "source_url"])
        seed = read_seed(self.path, expected_rows=1)
        self.assertEqual(seed.rows, [])
        self.assertIn("header", seed.problems[0])
        self.assertIn("source_url", seed.problems[0])

    def test_the_row_count_is_checked(self):
        self.write([GOOD])
        self.assertEqual(read_seed(self.path, expected_rows=2).problems, ["expected 2 rows, found 1"])

    def test_each_bad_value_is_reported_with_its_line(self):
        cases = {"company": ("", "company is empty"), "list_type": ("unicorn", "list_type 'unicorn'"),
                 "category": ("", "category ''"), "national_top": ("true", "national_top 'true'"),
                 "source_url": ("", "source_url is missing"), "careers_url": ("example.com/careers", "careers_url"),
                 "ats": ("workdy", "ats 'workdy'")}
        for column, (value, expected) in cases.items():
            with self.subTest(column=column):
                problems = self.problems(**{column: value})
                self.assertEqual(len(problems), 1, problems)
                self.assertTrue(problems[0].startswith("line 2: "), problems[0])
                self.assertIn(expected, problems[0])

    def test_the_same_company_twice_is_reported(self):
        self.write([GOOD, {**GOOD, "company": "Example Labs Pvt. Ltd."}])
        problems = read_seed(self.path, expected_rows=2).problems
        self.assertEqual(len(problems), 1)
        self.assertIn("line 3", problems[0])
        self.assertIn("same company as line 2", problems[0])

    def test_a_careers_url_on_a_never_fetched_site_is_reported(self):
        problems = self.problems(careers_url="https://www.linkedin.com/company/example/jobs", ats="unknown")
        self.assertEqual(len(problems), 1)
        self.assertIn("never fetched", problems[0])

    def test_never_fetched_sites(self):
        for url in ("https://www.linkedin.com/jobs", "https://in.indeed.com/x", "https://www.naukri.com/x", "https://wellfound.com/x",
                    "https://www.foundit.in/x", "https://www.instahyre.com/x", "https://internshala.com/x",
                    "https://www.geeksforgeeks.org/jobs", "https://leetcode.com/x", "https://www.glassdoor.co.in/x",
                    "https://www.ambitionbox.com/x", "not a url"):
            self.assertFalse(fetch_allowed(url), url)
        for url in ("https://careers.example.com/", "https://boards.greenhouse.io/example", "https://notlinkedin.com/jobs"):
            self.assertTrue(fetch_allowed(url), url)


class UpdateRowsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "companies_seed.csv"
        self.lines = [",".join(COLUMNS),
                      'Example Labs,startup,startup,yes,Pune,Pune,AI,"Listed, twice",https://lists.example/a,,unknown',
                      "Other Corp,startup,scaleup,,,,Fintech,Listed,https://lists.example/b,https://old.example/careers,greenhouse"]
        self.path.write_bytes(("\r\n".join(self.lines) + "\r\n").encode("utf-8"))

    def test_only_the_named_cells_change_and_every_other_byte_stays(self):
        changed = update_rows(self.path, {"Other Corp": {"careers_url": "https://www.other.example/careers/", "ats": "unknown"}})
        self.assertEqual(changed, ["Other Corp"])
        expected = self.lines[:2] + ["Other Corp,startup,scaleup,,,,Fintech,Listed,https://lists.example/b,https://www.other.example/careers/,unknown"]
        self.assertEqual(self.path.read_bytes(), ("\r\n".join(expected) + "\r\n").encode("utf-8"))

    def test_a_quoted_row_keeps_its_quoting(self):
        update_rows(self.path, {"Example Labs": {"careers_url": "https://jobs.lever.co/examplelabs", "ats": "lever"}})
        self.assertIn('Example Labs,startup,startup,yes,Pune,Pune,AI,"Listed, twice",https://lists.example/a,https://jobs.lever.co/examplelabs,lever',
                      self.path.read_text(encoding="utf-8"))

    def test_an_unknown_company_or_column_changes_nothing(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            update_rows(self.path, {"Missing Ltd": {"ats": "lever"}})
        with self.assertRaises(ValueError):
            update_rows(self.path, {"Other Corp": {"company": "Renamed"}})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(update_rows(self.path, {"Other Corp": {"ats": "greenhouse"}}), [])
        self.assertEqual(self.path.read_bytes(), before)


class RepositorySeedTests(unittest.TestCase):
    def test_the_committed_seed_is_valid(self):
        seed = read_seed(ROOT / "seeds" / "companies_seed.csv")
        self.assertEqual(seed.problems, [])
        self.assertEqual(len(seed.rows), EXPECTED_ROWS)
        self.assertEqual(EXPECTED_ROWS, 356)
        self.assertTrue(all(row.source_url for row in seed.rows))


if __name__ == "__main__":
    unittest.main()
