import unittest
from app.services.job_requirements import extract_requirements


class RequirementRegressions(unittest.TestCase):
    def test_degree_duration_company_age_and_contract_duration_are_not_experience(self):
        for text in ['Requires a 4 year degree. Freshers welcome.', 'Founded 20 years ago. Entry level role.', 'This is a 2 year contract.', 'Four year degree; 2 years preferred, no experience required.']:
            with self.subTest(text=text):
                self.assertEqual(extract_requirements(text)[:2], (0, None))

    def test_mixed_requirements_keep_highest_required_minimum(self):
        self.assertEqual(extract_requirements('Minimum 5 years experience, including 0-2 years in cloud.')[:2], (5, None))
        self.assertEqual(extract_requirements('3-5 years experience; minimum 7 years overall experience.')[:2], (7, None))

    def test_experience_forms_and_legacy_compact_ranges(self):
        for text, expected in [('0-1 years', (0, 1)), ('1-2 years', (1, 2)), ('2+ years', (2, None)), ('Minimum 2 years', (2, None)), ('Experience: 3 years', (3, None)), ('At least 2 years of professional experience', (2, None)), ('0\u20132 years experience', (0, 2)), ('3 years preferred', (0, None))]:
            with self.subTest(text=text):
                self.assertEqual(extract_requirements(text)[:2], expected)
