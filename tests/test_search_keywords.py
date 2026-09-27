"""Skills named in a search prompt become requested keywords, kept across refinements like roles."""
import unittest

from app.services.search_intent import interpret_search_request as parse


class KeywordExtractionTests(unittest.TestCase):
    def test_skills_after_a_role_are_kept_as_keywords(self):
        intent = parse('GenAI jobs using LangChain and RAG')
        self.assertEqual(intent.keywords, ['LangChain', 'RAG'])
        self.assertEqual(intent.role_families, ['ai'])

    def test_a_skill_only_role_becomes_a_keyword(self):
        intent = parse('LangChain jobs in Pune')
        self.assertEqual(intent.keywords, ['LangChain'])
        self.assertEqual(intent.roles_requested, [])
        self.assertEqual(intent.locations, ['Pune'])

    def test_skills_after_a_location_are_not_part_of_it(self):
        intent = parse('Data Analyst jobs in Bengaluru using LangChain')
        self.assertEqual((intent.locations, intent.keywords), (['Bengaluru'], ['LangChain']))

    def test_skills_covered_by_the_role_are_not_repeated(self):
        intent = parse('python developer with django')
        self.assertEqual(intent.roles_requested, ['Python Developer'])
        self.assertEqual(intent.keywords, ['Django'])

    def test_negated_skills_are_not_requested(self):
        intent = parse('Machine learning engineer jobs, no Java')
        self.assertNotIn('Java', intent.keywords)
        self.assertTrue(any('Excluded skills' in warning for warning in intent.warnings))

    def test_plain_role_search_has_no_keywords(self):
        intent = parse('Find QA Engineer and Data Analyst jobs in Kolkata')
        self.assertEqual(intent.keywords, [])
        self.assertIsNone(intent.keywords_cleared)


class KeywordRefinementTests(unittest.TestCase):
    def setUp(self):
        self.first = parse('GenAI jobs using LangChain and RAG')

    def test_other_refinements_keep_keywords(self):
        intent = parse('now in Pune', self.first)
        self.assertEqual(intent.keywords, ['LangChain', 'RAG'])
        self.assertIsNone(intent.keywords_cleared)

    def test_also_appends(self):
        self.assertEqual(parse('also FastAPI', self.first).keywords, ['LangChain', 'RAG', 'FastAPI'])

    def test_new_skills_replace(self):
        self.assertEqual(parse('jobs using PyTorch', self.first).keywords, ['PyTorch'])

    def test_a_role_change_clears_them_and_says_so(self):
        intent = parse('data analyst jobs', self.first)
        self.assertEqual(intent.keywords, [])
        self.assertEqual(intent.keywords_cleared, 'new role')
        self.assertIsNone(parse('in Pune', intent).keywords_cleared)

    def test_a_cv_search_clears_them(self):
        intent = parse('jobs suitable for my CV', self.first)
        self.assertEqual(intent.keywords, [])
        self.assertEqual(intent.keywords_cleared, 'CV-based search')

    def test_a_skill_only_turn_replaces_the_previous_role(self):
        intent = parse('LangChain jobs', parse('data analyst jobs'))
        self.assertEqual((intent.roles_requested, intent.keywords), ([], ['LangChain']))

    def test_changing_keywords_is_not_a_filter_only_turn(self):
        self.assertFalse(parse('jobs using PyTorch', self.first).filter_only)


if __name__ == '__main__':
    unittest.main()
