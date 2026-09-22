import tempfile
import unittest
from pathlib import Path

import fitz

from app.models.schemas import CandidateProfile
from app.services.cv_parser import extract_pdf_text, parse_profile_from_text


class CandidateExtractionTests(unittest.TestCase):
    def test_generic_business_resume_preserves_sections_and_evidence(self):
        profile = parse_profile_from_text('''Asha Mehta
Education
Bachelor of Commerce, City University, 2019 - 2022
Skills
Excel, Financial Modeling, Stakeholder Management, Zoho Books
Experience
Business Analyst, Acme Ltd, 2023 - 2025
Supported inventory planning and financial reporting.
Projects
Retail inventory dashboard
Certifications
Lean Six Sigma Yellow Belt
''')
        self.assertEqual(profile.name, 'Asha Mehta')
        self.assertEqual(profile.graduation_year, 2022)
        self.assertIn('Commerce', profile.degree)
        self.assertIn('Zoho Books', profile.skills)
        self.assertIn('Retail inventory dashboard', profile.projects)
        self.assertTrue(profile.experience)
        self.assertTrue(profile.certifications)
        self.assertTrue(profile.evidence.get('education'))

    def test_no_fabricated_research_or_graduation(self):
        profile = parse_profile_from_text('''TEST MEMBER
Skills: Python, SQL
Research
Example University: studied water quality.
Projects
Community map launched in 2025
''')
        self.assertEqual(profile.name, 'Test Member')
        self.assertIsNone(profile.graduation_year)
        self.assertEqual(profile.research, ['Example University: studied water quality.'])
        self.assertFalse(any('NLP' in line or 'LLM' in line for line in profile.research))
        self.assertTrue(profile.parsing_warnings)

    def test_legacy_inline_degree_and_year(self):
        profile = parse_profile_from_text('TEST MEMBER\nB.Tech 2025\nPython SQL')
        self.assertEqual(profile.graduation_year, 2025)
        self.assertEqual(profile.degree, 'B.Tech')
        self.assertEqual(profile.skills, ['Python', 'SQL'])

    def test_design_skills_and_internships(self):
        profile = parse_profile_from_text('''Riya Das
Education: Bachelor of Design, 2024
Skills: Figma, User Research, Typography, Communication
Internships
Design Intern at Studio North, June 2023 - August 2023
Research: Interviewed commuters about bus signage.
''')
        self.assertIn('Figma', profile.skill_categories.get('design', []))
        self.assertIn('Communication', profile.skill_categories.get('transferable', []))
        self.assertEqual(len(profile.internships), 1)
        self.assertEqual(profile.research, ['Interviewed commuters about bus signage.'])
        self.assertIsNone(profile.experience_years)

    def test_skill_boundaries_prevent_substring_matches(self):
        from app.services.cv_parser import extract_skills
        skills = extract_skills('Digital strategy, scoping and flask-shaped packaging; C++ and SQL')
        self.assertNotIn('Git', skills)
        self.assertNotIn('R', skills)
        self.assertIn('C++', skills)
        self.assertIn('SQL', skills)

    def test_conflicting_education_dates_are_not_guessed(self):
        profile = parse_profile_from_text('Asha Mehta\nEducation\nB.Com 2020\nMBA 2023')
        self.assertIsNone(profile.graduation_year)
        self.assertTrue(any('graduation' in warning.lower() for warning in profile.parsing_warnings))

    def test_section_labels_are_not_candidate_skills(self):
        profile = parse_profile_from_text('Asha Mehta\nResearch\nWater quality field survey\nSkills: Excel')
        self.assertNotIn('Research', profile.skills)

    def test_undated_primary_degree_does_not_take_secondary_degree_year(self):
        profile = parse_profile_from_text('Asha Mehta\nEducation\nMBA ongoing\nB.Com 2022')
        self.assertIsNone(profile.graduation_year)

    def test_unknown_skill_with_period_is_preserved(self):
        profile = parse_profile_from_text('Asha Mehta\nSkills: Acme.js, Excel')
        self.assertIn('Acme.js', profile.skills)

    def test_explicit_experience_summary_and_relocation(self):
        profile = parse_profile_from_text('Asha Mehta\nSummary\nBusiness analyst with 3 years of experience.\nOpen to relocation.\nSkills: Excel')
        self.assertEqual(profile.experience_years, 3)
        self.assertEqual(profile.experience_level, 'experienced')
        self.assertTrue(profile.relocation_preference)

    def test_profile_analysis_does_not_mutate_or_invent_facts(self):
        from app.services.candidate_intelligence import analyze_candidate
        original = CandidateProfile(skills=['Python', 'Excel', 'Figma', 'Negotiation', 'Bookbinding'])
        enriched = analyze_candidate(original)
        self.assertEqual(original.skill_categories, {})
        self.assertIn('Bookbinding', enriched.skills)
        self.assertIn('Negotiation', enriched.skill_categories['transferable'])
        self.assertEqual(enriched.experience_level, 'unknown')
        self.assertIsNone(enriched.experience_years)
        self.assertIsNone(enriched.graduation_year)

    def test_pdf_text_extraction_and_scanned_pdf_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resume.pdf'
            with fitz.open() as document:
                document.new_page().insert_text((72, 72), 'TEST MEMBER - Python SQL')
                document.save(path)
            self.assertIn('TEST MEMBER', extract_pdf_text(str(path)))
            path.unlink()  # extraction must release the Windows file handle
            with fitz.open() as document:
                document.new_page()
                document.save(path)
            with self.assertRaisesRegex(ValueError, '(?i)(scan|OCR|text)'):
                extract_pdf_text(str(path))
            path.unlink()

    def test_empty_and_excessive_resume_text_rejected(self):
        for text in ('   ', 'a' * 200_001):
            with self.subTest(length=len(text)):
                with self.assertRaises(ValueError):
                    parse_profile_from_text(text)

    def test_invalid_oversize_and_overlong_pdfs_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resume.pdf'
            path.write_bytes(b'not a PDF')
            with self.assertRaisesRegex(ValueError, '(?i)PDF'):
                extract_pdf_text(str(path))
            with path.open('wb') as output:
                output.truncate(10 * 1024 * 1024 + 1)
            with self.assertRaisesRegex(ValueError, '(?i)(size|MB|large)'):
                extract_pdf_text(str(path))
            path.unlink()
            with fitz.open() as document:
                for _ in range(101):
                    document.new_page()
                document.save(path)
            with self.assertRaisesRegex(ValueError, '(?i)pages'):
                extract_pdf_text(str(path))
            path.unlink()


if __name__ == '__main__':
    unittest.main()
