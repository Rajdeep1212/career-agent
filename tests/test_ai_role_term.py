"""A bare "AI" in a job title is not the AI role family; "AI jobs" typed as a search still is."""
import unittest
from datetime import date

from app.providers.radar_provider import RadarProvider
from app.services.career_agent import CareerAgent
from app.services.role_discovery import family_for_job_title
from app.services.search_intent import interpret_search_request
from app.sources.adapters import posting
from app.storage import radar_store
from radar_helpers import company
from test_local_locations import LocalSearchCoverageTests
from test_radar_provider import FRESHER, FailingProvider


def family(title):
    found = family_for_job_title(title)
    return found['family'] if found else None


class JobTitleTests(unittest.TestCase):
    def test_ai_as_a_label_on_a_non_technical_role_is_not_the_ai_family(self):
        for title in ('Product Manager II - AI', 'AI Social Media Content Intern', 'Sales Executive, AI Products',
                      'AI Content Writer', 'HR Business Partner - AI & Data', 'AI Strategy Lead', 'AI Lead',
                      'Consulting and Advisory Lead - Data & AI'):
            self.assertNotEqual(family(title), 'ai', title)

    def test_ai_and_ml_engineering_and_research_titles_are(self):
        for title in ('AI Engineer', 'AI Engineer - FDE (Forward Deployed Engineer)', 'Lead AI Forward Engineer',
                      'ML Engineer', 'Machine Learning Engineer', 'AI Research Assistant', 'Applied AI Scientist',
                      'AI/ML Developer', 'Generative AI Engineer', 'NLP Engineer', 'LLM Engineer',
                      # Found in the real index (2026-09-29): abbreviations, residencies, AI + ML together.
                      'SDE II - AI', 'AI/Data Resident', 'AI/ML Expert', 'GLO AI-ML specialist',
                      # "agentic" is a strong AI-specific signal, unlike "lead" which readmits management titles.
                      'Agentic AI Lead', 'Agentic AI Product Manager'):
            self.assertEqual(family(title), 'ai', title)


class PromptTests(unittest.TestCase):
    def test_typed_ai_still_means_the_ai_family(self):
        self.assertEqual(interpret_search_request('AI jobs in Pune').role_families, ['ai'])

    def test_aiml_is_a_name_for_the_ai_family(self):
        for text in ('aiml roles', 'AIML jobs in Bengaluru', 'ai ml jobs'):
            intent = interpret_search_request(text)
            self.assertEqual(intent.role_families, ['ai'], text)
            self.assertEqual(intent.roles_requested, ['Machine Learning Engineer'], text)


class SearchTests(LocalSearchCoverageTests):
    def setUp(self):
        super().setUp()
        entry = company({"type": "greenhouse", "board": "c"}, id="c", name="Other")
        radar_store.record_listing(entry.id, [
            posting(entry, job_id=str(index), title=title, location='Pune, India', description=FRESHER,
                    url=f'https://job-boards.greenhouse.io/c/jobs/{index}')
            for index, title in enumerate(['Product Manager II - AI', 'AI Social Media Content Intern', 'AI Engineer'])],
            complete=True, today=date(2026, 9, 28))

    async def test_a_genai_search_keeps_ai_engineers_and_drops_ai_labelled_other_roles(self):
        response = await CareerAgent([RadarProvider(), FailingProvider()]).search('GenAI jobs in Pune', include_seen=True)
        titles = {result['title'] for result in response['results']}
        self.assertIn('AI Engineer', titles)
        self.assertNotIn('Product Manager II - AI', titles)
        self.assertNotIn('AI Social Media Content Intern', titles)

    async def test_later_saved_locations_are_searched_with_no_requests(self):
        pass   # covered in test_local_locations

    def test_the_plan_preview_still_counts_only_paid_requests(self):
        pass   # covered in test_local_locations


if __name__ == '__main__':
    unittest.main()
