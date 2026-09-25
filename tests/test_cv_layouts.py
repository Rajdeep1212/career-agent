"""Realistic Indian CV layouts (anonymized fixtures in tests/fixtures/resumes/)."""
import unittest
from pathlib import Path

from app.services.cv_parser import parse_profile_from_text

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'resumes'


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

class SkillListSplittingTests(unittest.TestCase):
    """Commas inside parentheses belong to the bracketed group, not the outer list."""

    def _skills(self, line):
        return parse_profile_from_text('Jane Doe\nSkills\n' + line).skills

    def test_parenthesized_groups_become_separate_skills(self):
        skills = self._skills('MLOps & Cloud: Docker, MLflow, GCP (BigQuery, Looker Studio), AWS (S3, EC2)')
        for skill in ('Docker', 'MLflow', 'GCP', 'BigQuery', 'Looker Studio', 'AWS', 'S3', 'EC2'):
            self.assertIn(skill, skills)

    def test_no_unbalanced_brackets_are_ever_emitted(self):
        for line in ('GCP (BigQuery, Looker Studio), AWS (S3, EC2)', 'GCP (BigQuery, Looker Studio',
                     'Tools: Tableau), Excel', 'Python [Pandas; NumPy] | SQL {Joins}'):
            for skill in self._skills(line):
                self.assertNotRegex(skill, r'[()\[\]{}]', line)

    def test_proficiency_qualifiers_are_not_skills(self):
        skills = self._skills('Python (Advanced), SQL (Intermediate), Excel')
        self.assertEqual(sorted(skills, key=str.casefold), ['Excel', 'Python', 'SQL'])

class SkillCanonicalizationTests(unittest.TestCase):
    """One name per skill; composite items made of known skills are split."""

    def _skills(self, line):
        return parse_profile_from_text('Jane Doe\nSkills\n' + line).skills

    def test_duplicate_spellings_collapse_to_one_skill(self):
        skills = self._skills('React, React.js, Git, Git/GitHub, Hugging Face Transformers, LLM Fine-tuning, Fine-tuning')
        for skill in ('React', 'Git', 'GitHub', 'Hugging Face', 'Transformers', 'Fine-tuning', 'LLM'):
            self.assertIn(skill, skills)
        for duplicate in ('React.js', 'Git/GitHub', 'Hugging Face Transformers', 'LLM Fine-tuning'):
            self.assertNotIn(duplicate, skills)
        self.assertEqual(len(skills), len({skill.casefold() for skill in skills}))

    def test_known_compound_names_and_unknown_skills_are_kept(self):
        skills = self._skills('CI/CD, Zoho Books, Looker Studio')
        self.assertEqual(sorted(skills, key=str.casefold), ['CI/CD', 'Looker Studio', 'Zoho Books'])

    def test_job_text_uses_the_same_names(self):
        from app.services.skills import extract_skills
        self.assertEqual(extract_skills('React.js and ReactJS apps on GitHub'), ['GitHub', 'React'])

class SectionDetectionTests(unittest.TestCase):
    """Combined headings, skill sub-labels, research internships and domain evidence."""

    @classmethod
    def setUpClass(cls):
        cls.profile = parse_fixture('in_btech_multiline.txt')

    def test_combined_heading_starts_a_new_section(self):
        self.assertEqual(self.profile.certifications,
                         ['Google Cloud Digital Leader', 'Winner, inter-college hackathon 2024'])
        self.assertFalse(any('CERTIFICATION' in line.upper() or 'Digital Leader' in line
                             for line in self.profile.projects))

    def test_skill_sub_labels_stay_in_the_skills_section(self):
        for skill in ('Python', 'C++', 'PyTorch', 'BigQuery', 'Looker Studio', 'S3', 'EC2', 'GitHub'):
            self.assertIn(skill, self.profile.skills)

    def test_research_internship_is_listed_as_an_internship(self):
        self.assertTrue(any(line.startswith('Research Intern') for line in self.profile.internships))
        self.assertTrue(any(line.startswith('Research Intern') for line in self.profile.research))

    def test_board_names_are_not_domain_knowledge(self):
        self.assertNotIn('Education', self.profile.domain_knowledge)
        self.assertIn('Computer Science', self.profile.domain_knowledge)
        evidence = self.profile.evidence.get('domain_knowledge', [])
        self.assertFalse(any('WBBSE' in line or 'WBCHSE' in line for line in evidence))

    def test_real_education_domain_is_still_detected(self):
        from app.models.schemas import CandidateProfile
        from app.services.candidate_intelligence import analyze_candidate
        profile = analyze_candidate(CandidateProfile(experience=['Teaching assistant in primary Education outreach']))
        self.assertIn('Education', profile.domain_knowledge)


class BulletTests(unittest.TestCase):
    """Backlog: list markers such as ● must not reach section entries."""

    def test_common_bullet_characters_are_removed(self):
        text = '''Jane Doe
Certifications
● Google Cloud Digital Leader
▪ AWS Cloud Practitioner
◦ Azure AI-900
– Oracle OCI Foundations
* Kaggle Learn: Intro to ML'''
        self.assertEqual(parse_profile_from_text(text).certifications,
                         ['Google Cloud Digital Leader', 'AWS Cloud Practitioner', 'Azure AI-900',
                          'Oracle OCI Foundations', 'Kaggle Learn: Intro to ML'])

    def test_hyphenated_words_and_negative_numbers_are_kept(self):
        text = '''Jane Doe
Projects
C-sharp tooling for -5 dB audio'''
        self.assertEqual(parse_profile_from_text(text).projects, ['C-sharp tooling for -5 dB audio'])



class InternshipDateTests(unittest.TestCase):
    """Backlog: a date line below an internship title belongs to that internship."""

    def test_research_internship_keeps_its_dates(self):
        internship = parse_fixture('in_btech_multiline.txt').internships[0]
        self.assertEqual(internship, 'Research Intern — Indian Institute of Technology (IIT) Example (Jul 2025 – Dec 2025)')

    def test_internships_section_merges_date_lines(self):
        text = '''Jane Doe
Internships
ML Intern, Example Labs
Jun 2024 – Aug 2024
Built a text classifier
Data Intern, Example Retail, 2023'''
        self.assertEqual(parse_profile_from_text(text).internships,
                         ['ML Intern, Example Labs (Jun 2024 – Aug 2024)', 'Built a text classifier',
                          'Data Intern, Example Retail, 2023'])

    def test_experience_internship_takes_ongoing_dates(self):
        text = '''Jane Doe
Experience
Software Engineering Intern, Example Soft
May 2025 - Present
Backend Developer, Example Corp'''
        profile = parse_profile_from_text(text)
        self.assertEqual(profile.internships, ['Software Engineering Intern, Example Soft (May 2025 - Present)'])
        self.assertEqual(profile.experience, ['Backend Developer, Example Corp'])


if __name__ == '__main__':
    unittest.main()
