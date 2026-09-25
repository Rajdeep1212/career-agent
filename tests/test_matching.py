import unittest
from app.models.schemas import CandidateProfile, JobPosting, JobSearchPreferences
from app.models.career import SearchIntent


class MatchingTests(unittest.TestCase):
    def candidate(self, **kwargs):
        return CandidateProfile(graduation_year=2025, skills=['Python','SQL','Postman'], **kwargs)

    def job(self, description='', **kwargs):
        return JobPosting(company='Example',title='QA Engineer',location='Kolkata',description=description, **kwargs)

    def test_experience_and_batch_rules(self):
        from app.services.eligibility import evaluate_eligibility
        prefs=JobSearchPreferences(required_graduation_year=2025)
        for text, expected in [('0-1 years',True),('0-2 years freshers welcome',True),('1-2 years',True),
                               ('minimum 2 years',False),('2+ years',False),('2026 batch only',False),
                               ('2025/2026 graduates',True),('graduate trainee',True)]:
            with self.subTest(text=text):
                result=evaluate_eligibility(self.candidate(),self.job(text),prefs,SearchIntent())
                self.assertEqual(result.eligible,expected)
                if not expected:self.assertTrue(result.hard_rejections)

    def test_seniority_from_candidate_not_fixed_globally(self):
        from app.services.eligibility import evaluate_eligibility
        job=self.job('minimum 3 years').model_copy(update={'title':'Senior QA Engineer'})
        self.assertFalse(evaluate_eligibility(self.candidate(),job,JobSearchPreferences(),SearchIntent()).eligible)
        self.assertTrue(evaluate_eligibility(self.candidate(experience_years=5),job,JobSearchPreferences(max_required_experience_years=5),SearchIntent()).eligible)

    def test_closed_and_strict_unknown(self):
        from app.services.eligibility import evaluate_eligibility
        self.assertFalse(evaluate_eligibility(self.candidate(),self.job(verification_state='CLOSED'),JobSearchPreferences(),SearchIntent()).eligible)
        self.assertFalse(evaluate_eligibility(self.candidate(),self.job(),JobSearchPreferences(),SearchIntent(strict_mode=True)).eligible)
        normal=evaluate_eligibility(self.candidate(),self.job(),JobSearchPreferences(),SearchIntent())
        self.assertTrue(normal.eligible)
        self.assertTrue(normal.warnings)

    def test_transferable_skills_and_no_empty_skills_perfect_score(self):
        from app.services.eligibility import evaluate_eligibility
        from app.services.matching import match_job
        profile=self.candidate()
        intent=SearchIntent(roles_requested=['QA Engineer'],locations=['Kolkata'])
        job=self.job(skills=['API Testing','Selenium'])
        eligibility=evaluate_eligibility(profile,job,JobSearchPreferences(),intent)
        match=match_job(profile,job,intent,eligibility)
        self.assertIn('Postman',match.transferable_skills)
        self.assertIn('Selenium',match.missing_skills)
        empty=match_job(profile,self.job(),intent,eligibility)
        self.assertLess(empty.skill_score,35)
        self.assertTrue(match.explanation)

    def test_experienced_default_and_explicit_experience_limit(self):
        from app.services.eligibility import evaluate_eligibility
        profile=self.candidate(experience_years=5)
        self.assertTrue(evaluate_eligibility(profile,self.job('3 years experience'),JobSearchPreferences(),SearchIntent()).eligible)
        self.assertFalse(evaluate_eligibility(profile,self.job('3 years experience'),JobSearchPreferences(max_required_experience_years=1),SearchIntent()).eligible)

    def test_freshness_and_industry_filters_are_not_ignored(self):
        from app.services.eligibility import evaluate_eligibility
        intent=SearchIntent(freshness_preference='7 days', industry_preferences=['Healthcare'])
        result=evaluate_eligibility(self.candidate(),self.job(posted_date='2020-01-01',industry='Retail'),JobSearchPreferences(),intent)
        self.assertFalse(result.eligible)
        self.assertTrue(any('posted' in s.lower() for s in result.hard_rejections))
