"""Requested skills are a separate, explained ranking component (L0 heuristic), never a filter."""
import unittest

from app.models.career import SearchIntent
from app.models.schemas import CandidateProfile, JobPosting, JobSearchPreferences
from app.services.eligibility import evaluate_eligibility
from app.services.matching import match_job
from test_search_cache import _Isolated

PROFILE = CandidateProfile(graduation_year=2025, skills=['Python', 'PyTorch', 'SQL'],
                           projects=['RAG chatbot with LangChain and FAISS'])


def job(description, title='Machine Learning Engineer', **kwargs):
    kwargs.setdefault('posted_date', '2020-01-01')
    return JobPosting(company='Example', title=title, location='Bengaluru', description=description, **kwargs)


def match(posting, intent):
    return match_job(PROFILE, posting, intent, evaluate_eligibility(PROFILE, posting, JobSearchPreferences(), intent))


class RequestedSkillsMatchingTests(unittest.TestCase):
    def test_without_keywords_scores_are_unchanged(self):
        posting = job('Build RAG pipelines with LangChain. Python required. Freshers welcome.', skills=['Python', 'LangChain'])
        result = match(posting, SearchIntent(roles_requested=['Machine Learning Engineer'], locations=['Bengaluru']))
        self.assertEqual(result.overall_score, 66)
        self.assertEqual(result.components, {'skills': 18, 'role': 25, 'transferable': 0, 'experience': 10, 'location': 10,
                                             'projects_research': 3, 'education': 0, 'verification': 0, 'freshness': 0})

    def test_listings_with_the_requested_skills_rank_higher_and_say_why(self):
        intent = SearchIntent(roles_requested=['Machine Learning Engineer'], locations=['Bengaluru'],
                              keywords=['LangChain', 'RAG', 'FAISS'])
        with_skills = match(job('Build RAG pipelines with LangChain. Python required.', skills=['Python']), intent)
        without = match(job('Train vision models. Python required.', skills=['Python']), intent)
        self.assertEqual(with_skills.components['requested_skills'], 7)
        self.assertEqual(without.components['requested_skills'], 0)
        self.assertGreater(with_skills.overall_score, without.overall_score)
        self.assertIn('Requested skills in listing: LangChain, RAG', with_skills.strengths)
        self.assertIn('Requested skills not mentioned: FAISS', with_skills.gaps)
        self.assertIn('Requested skills not mentioned: LangChain, RAG, FAISS', without.gaps)

    def test_requested_skills_do_not_depend_on_the_cv(self):
        intent = SearchIntent(keywords=['Kubernetes'])
        result = match(job('Deploy services on Kubernetes.'), intent)
        self.assertEqual(result.components['requested_skills'], 10)
        self.assertNotIn('Kubernetes', result.matched_skills)

    def test_the_total_stays_capped_at_100(self):
        intent = SearchIntent(roles_requested=['Machine Learning Engineer'], locations=['Bengaluru'], keywords=['Python'])
        posting = job('Python, PyTorch, SQL. Freshers welcome.', skills=['Python', 'PyTorch', 'SQL'], posted_date=None,
                      verification_state='ACTIVE_VERIFIED')
        self.assertLessEqual(match(posting, intent).overall_score, 100)


class Aggregator:
    name = 'Aggregator'

    async def search_planned(self, planned):
        return [JobPosting(company='Example', title='Applied Scientist', location='Bengaluru, India',
                           description='Freshers welcome. Build RAG systems with LangChain.',
                           application_url='https://aggregator.example/jobs/1')]


class OffRoleTests(_Isolated):
    async def test_a_listing_with_a_requested_skill_is_not_dropped_as_off_role(self):
        from unittest.mock import AsyncMock, patch

        from app.services import application_verifier
        from app.services.career_agent import CareerAgent
        patcher = patch.object(application_verifier, 'safe_get', AsyncMock(side_effect=OSError('offline')))
        patcher.start()
        self.addCleanup(patcher.stop)
        agent = CareerAgent([Aggregator()])
        plain = await agent.search('Data Analyst jobs in Bengaluru', include_seen=True, refresh=True)
        self.assertEqual(plain['results'], [])
        response = await agent.search('Data Analyst jobs in Bengaluru using LangChain', include_seen=True, refresh=True)
        self.assertEqual([result['title'] for result in response['results']], ['Applied Scientist'])


if __name__ == '__main__':
    unittest.main()
