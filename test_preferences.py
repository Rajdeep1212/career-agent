import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.core import preferences
from app.models.schemas import CandidateProfile


class PreferenceTests(unittest.TestCase):
    def test_candidate_year_and_experience_drive_defaults(self):
        from app.storage import preference_store
        with tempfile.TemporaryDirectory() as directory, patch.object(preference_store, 'PREFERENCES_PATH', Path(directory)/'preferences.json'):
            p = preference_store.preferences_for(CandidateProfile(graduation_year=2022, experience_years=3, preferred_locations=['Paris']))
            self.assertEqual(p.required_graduation_year, 2022)
            self.assertEqual(p.max_required_experience_years, 3)
            self.assertEqual(p.preferred_locations, ['Paris'])

    def test_fictional_default(self):
        self.assertIn('demo', preferences.DEFAULT_PROFILE.name.lower())
