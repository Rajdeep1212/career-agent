"""Three-way eligibility: every decision quotes the listing text it rests on."""
import unittest

from app.models.career import SearchIntent
from app.models.schemas import CandidateProfile, JobPosting, JobSearchPreferences
from app.services.eligibility import evaluate_eligibility

FRESHER = CandidateProfile(graduation_year=2025, experience_years=0, skills=["Python"])


def _check(description, title="Machine Learning Engineer", profile=FRESHER, intent=None, **job):
    posting = JobPosting(company="Example", title=title, location="Bengaluru, India", description=description, **job)
    return evaluate_eligibility(profile, posting, JobSearchPreferences(), intent or SearchIntent())


class ExcludedTests(unittest.TestCase):
    def test_firm_minimum_well_above_fresher_range(self):
        result = _check("Great team. 5+ years experience required in production ML.")
        self.assertEqual(result.status, "excluded")
        self.assertFalse(result.eligible)
        self.assertEqual(result.summary, "excluded: quoted '5+ years experience required in production ML' "
                                         "(Requires at least 5 years; your limit is 1.)")

    def test_explicit_senior_title(self):
        result = _check("Build ranking systems.", title="Senior Machine Learning Engineer")
        self.assertEqual(result.status, "excluded")
        self.assertIn("quoted 'Senior Machine Learning Engineer'", result.summary)

    def test_batch_list_without_the_candidate_year(self):
        result = _check("Open to 2023 and 2024 pass-outs only. Training provided.")
        self.assertEqual(result.status, "excluded")
        self.assertIn("quoted 'Open to 2023 and 2024 pass-outs only'", result.summary)


class UncertainTests(unittest.TestCase):
    def test_preferred_experience_is_not_a_requirement(self):
        result = _check("Build churn models. 1-2 years of experience preferred.")
        self.assertEqual(result.status, "uncertain")
        self.assertTrue(result.eligible)
        self.assertEqual(result.summary, "uncertain: quoted '1-2 years of experience preferred' (Experience is preferred, not required.)")

    def test_no_experience_line_at_all(self):
        result = _check("Build and deploy ML models with PyTorch and MLflow on AWS.")
        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.summary, "uncertain: The listing states no experience requirement.")

    def test_slightly_above_the_limit(self):
        result = _check("2+ years experience with LLMs and Python required.")
        self.assertEqual(result.status, "uncertain")
        self.assertIn("quoted '2+ years experience with LLMs and Python required'", result.summary)

    def test_ambiguous_lead_title(self):
        result = _check("Freshers welcome.", title="Lead Generation Associate")
        self.assertEqual(result.status, "uncertain")


class EligibleTests(unittest.TestCase):
    def test_explicit_fresher_language(self):
        result = _check("Hybrid, freshers considered. Build RAG pipelines with LangChain.")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.summary, "eligible: quoted 'Hybrid, freshers considered' (Explicit entry-level or graduate language.)")

    def test_matching_batch_counts_as_fresher_evidence(self):
        result = _check("2025 graduates eligible. Python or Java.")
        self.assertEqual(result.status, "eligible")
        self.assertIn("Graduation year 2025 is accepted.", result.positive_signals)

    def test_internship_is_entry_level_evidence(self):
        result = _check("Six-month internship working on computer vision.", title="AI/ML Intern")
        self.assertEqual(result.status, "eligible")
        self.assertIn("quoted 'AI/ML Intern'", result.summary)

    def test_range_within_limit(self):
        result = _check("Experience: 0-1 years. Python and REST APIs.")
        self.assertEqual(result.status, "eligible")


class TransparencyTests(unittest.TestCase):
    def test_every_decision_is_labeled_heuristic_and_listed(self):
        result = _check("Senior title aside, 5+ years experience required.", title="Senior Engineer")
        self.assertEqual(result.claim_level, "L0")
        self.assertEqual({item.outcome for item in result.evidence}, {"excluded"})
        self.assertEqual(len(result.hard_rejections), 2)

    def test_user_constraints_still_exclude(self):
        intent = SearchIntent(internship_allowed=False)
        result = _check("Six-month internship.", title="AI/ML Intern", intent=intent, employment_type="INTERN")
        self.assertEqual(result.status, "excluded")
        self.assertIn("Internships were excluded.", result.summary)



class LocationTests(unittest.TestCase):
    def _at(self, location, *, requested=("Bengaluru",), from_preferences=False, work_mode="unknown"):
        intent = SearchIntent(locations=list(requested), locations_from_preferences=from_preferences)
        posting = JobPosting(company="Example", title="ML Engineer", location=location, work_mode=work_mode,
                             description="Freshers welcome.")
        return evaluate_eligibility(FRESHER, posting, JobSearchPreferences(), intent)

    def test_city_aliases_match(self):
        self.assertEqual(self._at("Bangalore, Karnataka, India").status, "eligible")
        self.assertEqual(self._at("Gurgaon, Haryana, India", requested=("Gurugram",)).status, "eligible")
        self.assertEqual(self._at("Bombay, India", requested=("Mumbai",)).status, "eligible")

    def test_country_only_listing_is_uncertain(self):
        result = self._at("India")
        self.assertEqual(result.status, "uncertain")
        self.assertIn("quoted 'India'", result.summary)

    def test_explicitly_requested_city_excludes_other_cities(self):
        result = self._at("Hyderabad, Telangana, India")
        self.assertEqual(result.status, "excluded")
        self.assertIn("quoted 'Hyderabad, Telangana, India'", result.summary)

    def test_saved_locations_only_make_other_cities_uncertain(self):
        result = self._at("Hyderabad, Telangana, India", from_preferences=True)
        self.assertEqual(result.status, "uncertain")
        self.assertIn("saved locations", result.summary)

    def test_remote_jobs_ignore_location(self):
        self.assertEqual(self._at("Hyderabad, India", work_mode="remote").status, "eligible")

if __name__ == "__main__":
    unittest.main()
