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


class TitleWordTests(unittest.TestCase):
    """B3: QA/tester/analyst normalisation must act on whole tokens only."""

    def test_words_containing_qa_are_not_corrupted(self):
        self.assertEqual(words('Qatar analytics'), {'qatar', 'analytics'})

    def test_whole_token_normalisation_is_kept(self):
        self.assertEqual(words('QA Engineer'), {'testing'})
        self.assertEqual(words('Software Tester'), {'software', 'testing'})
        self.assertEqual(words('Data Analyst'), {'data', 'analysis'})

    def test_qatar_title_gets_no_qa_transferable_skills(self):
        from app.services.matching import match_job
        from app.models.career import EligibilityResult
        profile = CandidateProfile(skills=['Postman', 'Python'])
        job = JobPosting(company='Example', title='Sales Manager, Qatar', location='Doha')
        self.assertEqual(match_job(profile, job, SearchIntent(), EligibilityResult()).transferable_skills, [])


class AmbiguousSkillTests(unittest.TestCase):
    """B4 (part): single-letter skills must not match inside 'R&D'."""

    def test_r_and_d_is_not_the_r_language(self):
        self.assertNotIn('R', extract_skills('Join our R&D team.'))

    def test_r_in_a_skill_list_is_still_found(self):
        self.assertIn('R', extract_skills('Python, R, SQL'))
        self.assertIn('R', extract_skills('Statistics using R programming'))


class GraduationYearTests(unittest.TestCase):
    """B5: school-level years (Class X/XII, SSC, HSC, CBSE) must not hide the degree year."""

    def _year(self, education):
        return parse_profile_from_text('Jane Doe\nEducation\n' + education + '\nSkills: Python').graduation_year

    def test_class_xii_line_is_ignored(self):
        self.assertEqual(self._year('B.Tech CSE 2025\nClass XII 2021'), 2025)

    def test_common_indian_school_labels_are_ignored(self):
        for school in ('Class X, CBSE, 2019', 'Class 12 (ISC) 2021', 'Higher Secondary (WBCHSE), 2021',
                       'SSC 2019', 'HSC 2021', '12th Standard, 2021', 'Senior Secondary 2021'):
            self.assertEqual(self._year('B.Tech Computer Science, 2021 - 2025\n' + school), 2025, school)

    def test_two_digit_range_end_year(self):
        self.assertEqual(self._year('B.Tech CSE, Example Institute, 2021-25'), 2025)

    def test_multiple_degrees_are_still_not_guessed(self):
        self.assertIsNone(self._year('B.Com 2020\nMBA 2023\nClass XII 2017'))


class SkillVocabularyTests(unittest.TestCase):
    """B4: one versioned vocabulary for CVs and jobs, including GenAI skills."""

    GENAI_JOB = ('Build RAG pipelines with LangChain and LangGraph, FAISS or Pinecone vector databases, '
                 'fine-tuning with LoRA on Hugging Face Transformers, served with vLLM.')

    def test_genai_terms_are_extracted_from_job_text(self):
        skills = extract_skills(self.GENAI_JOB)
        for skill in ('RAG', 'LangChain', 'LangGraph', 'FAISS', 'Pinecone', 'Vector Database',
                      'Fine-tuning', 'LoRA', 'Hugging Face', 'Transformers', 'vLLM'):
            self.assertIn(skill, skills)

    def test_aliases_map_to_canonical_names(self):
        skills = extract_skills('sklearn, Postgres, k8s and Golang services')
        self.assertEqual(skills, ['Go', 'Kubernetes', 'PostgreSQL', 'scikit-learn'])

    def test_ambiguous_words_need_their_skill_form(self):
        self.assertEqual(extract_skills("We go live next week with a spark of energy."), [])

    def test_cv_explicit_skills_use_canonical_names(self):
        profile = parse_profile_from_text('Jane Doe\nSkills: sklearn, LangChain, Go, Zoho Books')
        self.assertIn('scikit-learn', profile.skills)
        self.assertNotIn('sklearn', profile.skills)
        self.assertIn('LangChain', profile.skills)
        self.assertIn('Go', profile.skills)
        self.assertIn('Zoho Books', profile.skills)

    def test_job_and_cv_share_the_vocabulary(self):
        from app.models.career import EligibilityResult
        from app.services.matching import match_job
        job = normalize_item(_item('GenAI Engineer', self.GENAI_JOB))
        self.assertIn('LangChain', job.skills)
        profile = parse_profile_from_text('Jane Doe\nSkills: LangChain, FAISS, Python')
        match = match_job(profile, job, SearchIntent(), EligibilityResult())
        self.assertIn('LangChain', match.matched_skills)
        self.assertIn('FAISS', match.matched_skills)

    def test_vocabulary_is_versioned_and_consistent(self):
        from app.services import skills
        self.assertEqual(skills.VOCABULARY_VERSION, 1)
        seen = {}
        for entry in skills.load_vocabulary()['skills']:
            for term in [entry['name'], *entry.get('aliases', [])]:
                self.assertNotIn(term.casefold(), seen, f'{term} is defined twice')
                seen[term.casefold()] = entry['name']


if __name__ == '__main__':
    unittest.main()
