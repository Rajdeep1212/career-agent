"""Regression tests for the defects reproduced in docs/AUDIT_AND_ROADMAP.md, Appendix A."""
import unittest

from app.models.career import SearchIntent
from app.models.schemas import CandidateProfile, JobPosting, JobSearchPreferences
from app.providers.jsearch_provider import normalize_item
from app.services.cv_parser import extract_skills, parse_profile_from_text
from app.services.eligibility import evaluate_eligibility
from app.services.matching import words


def _item(title, description=''):
    return {'job_title': title, 'job_description': description, 'employer_name': 'Example'}


class FresherFlagTests(unittest.TestCase):
    """B1: 'intern' must be a whole word, not a substring of 'international'."""

    def test_international_senior_role_is_not_fresher_friendly(self):
        job = normalize_item(_item('Senior Backend Engineer, International Payments', '5+ years experience.'))
        self.assertFalse(job.fresher_allowed)

    def test_internal_and_internet_are_not_fresher_terms(self):
        for title in ('Internal Tools Engineer', 'Internet Marketing Lead'):
            self.assertFalse(normalize_item(_item(title, '3 years experience.')).fresher_allowed, title)

    def test_real_internship_and_fresher_language_still_count(self):
        for title, description in (('Data Science Intern', ''), ('Python Developer', 'Internship for students.'),
                                   ('Analyst', 'Freshers welcome.'), ('Engineer', 'Open to recent graduates.')):
            self.assertTrue(normalize_item(_item(title, description)).fresher_allowed, title)


if __name__ == '__main__':
    unittest.main()
