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


class InternshipExclusionTests(unittest.TestCase):
    """B2: excluding internships must not reject 'Internal Tools Engineer'."""

    def _rejections(self, **job):
        posting = JobPosting(company='Example', location='India', **job)
        return evaluate_eligibility(CandidateProfile(experience_years=0), posting, JobSearchPreferences(),
                                    SearchIntent(internship_allowed=False)).hard_rejections

    def test_internal_title_is_not_an_internship(self):
        self.assertNotIn('Internships were excluded.', self._rejections(title='Internal Tools Engineer'))

    def test_real_internships_are_still_excluded(self):
        self.assertIn('Internships were excluded.', self._rejections(title='Software Engineering Intern'))
        self.assertIn('Internships were excluded.', self._rejections(title='Analyst', employment_type='INTERN'))


if __name__ == '__main__':
    unittest.main()
