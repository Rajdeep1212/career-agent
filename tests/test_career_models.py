import unittest
from app.models.schemas import CandidateProfile, JobSearchPreferences


class CareerModelTests(unittest.TestCase):
    def test_legacy_profile_gains_optional_intelligence(self):
        profile = CandidateProfile.model_validate({'name': 'Example Student', 'skills': ['Excel']})
        self.assertEqual(profile.model_dump().get('education'), [])
        self.assertIsNone(profile.model_dump().get('experience_years'))
        self.assertEqual(profile.skills, ['Excel'])

    def test_generic_preferences_do_not_force_one_graduation_year(self):
        prefs = JobSearchPreferences()
        self.assertIsNone(prefs.required_graduation_year)
        self.assertFalse(prefs.require_active_application)

    def test_intent_and_results_validate_bounds(self):
        from app.models.career import SearchIntent
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            SearchIntent(minimum_match_score=101)
