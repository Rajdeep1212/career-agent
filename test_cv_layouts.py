"""Realistic Indian CV layouts (anonymized fixtures in tests/fixtures/resumes/)."""
import unittest
from pathlib import Path

from app.services.cv_parser import parse_profile_from_text

FIXTURES = Path(__file__).resolve().parent / 'tests' / 'fixtures' / 'resumes'


def parse_fixture(name):
    return parse_profile_from_text((FIXTURES / name).read_text(encoding='utf-8'))


class GraduationYearLayoutTests(unittest.TestCase):
    """The degree's end year, whether dates share its line or sit below it."""

    def test_degree_and_dates_on_separate_lines(self):
        self.assertEqual(parse_fixture('in_btech_multiline.txt').graduation_year, 2025)

    def test_degree_and_year_range_on_the_same_line(self):
        self.assertEqual(parse_fixture('in_btech_sameline.txt').graduation_year, 2025)

    def test_year_only_range_below_the_degree(self):
        self.assertEqual(parse_fixture('in_bca_yearrange.txt').graduation_year, 2025)

    def test_school_block_dates_never_become_the_degree_year(self):
        text = 'Jane Doe\nEducation\nHigher Secondary (12th)\nJul 2018 – May 2020\nWBCHSE | 83%\nSkills: Python'
        self.assertIsNone(parse_profile_from_text(text).graduation_year)

    def test_undated_degree_is_not_given_a_school_year(self):
        text = 'Jane Doe\nEducation\nB.Tech., Computer Science\nKolkata\nSecondary (10th)\nMay 2018\nSkills: Python'
        self.assertIsNone(parse_profile_from_text(text).graduation_year)


if __name__ == '__main__':
    unittest.main()
