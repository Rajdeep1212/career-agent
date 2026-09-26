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


class ExperienceClauseTests(unittest.TestCase):
    """Each duration keeps its quoted clause and whether the wording is soft."""

    def test_firm_clause_is_quoted(self):
        from app.services.job_requirements import experience_clauses
        [clause] = experience_clauses('Great team. 5+ years experience required in production ML. Apply now.')
        self.assertEqual((clause.minimum, clause.maximum, clause.soft), (5, None, False))
        self.assertEqual(clause.quote, '5+ years experience required in production ML')

    def test_soft_wording_before_or_after_the_duration(self):
        from app.services.job_requirements import experience_clauses
        for text in ('1-2 years of experience preferred.', 'Nice to have: 1 year of experience with Rasa.',
                     'A plus: 2+ years of experience in NLP.', 'Hands-on RAG; 1-3 years preferred.'):
            with self.subTest(text=text):
                clauses = experience_clauses(text)
                self.assertTrue(clauses and all(clause.soft for clause in clauses))
                self.assertEqual(extract_requirements(text)[:2], (0, None))

    def test_long_clauses_are_trimmed_around_the_duration(self):
        from app.services.job_requirements import experience_clauses
        text = 'We are looking for ' + 'motivated engineers who enjoy shipping ' * 8 + 'with at least 3 years of experience in Python and more ' * 3
        quote = experience_clauses(text)[0].quote
        self.assertLessEqual(len(quote), 145)
        self.assertIn('3 years', quote)



class CeilingTests(unittest.TestCase):
    """'Up to N years' is a ceiling, not a minimum."""

    def test_upper_bounds_are_not_minimums(self):
        for text in ('Up to 2 years of experience with OpenCV.', 'Maximum 3 years of experience.',
                     'Less than 2 years of experience.', 'Upto 1 year experience'):
            with self.subTest(text=text):
                self.assertEqual(extract_requirements(text)[0], 0)

    def test_upper_bound_keeps_its_maximum(self):
        from app.services.job_requirements import experience_clauses
        [clause] = experience_clauses('Up to 2 years of experience with OpenCV.')
        self.assertEqual((clause.minimum, clause.maximum), (0, 2))


if __name__ == '__main__':
    unittest.main()
