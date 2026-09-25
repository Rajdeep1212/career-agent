"""Real-app profile and preference persistence tests with disposable storage."""
import asyncio
import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient

from app.models.schemas import CandidateProfile, JobSearchPreferences


class ProfileSettingsAPITests(unittest.TestCase):
    origin = {'Origin': 'http://localhost:8010'}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(gc.collect)
        directory = Path(temporary.name)
        from app import main
        from app.storage import attachment_store, career_store, history, preference_store, profile_store
        self.main = main
        self.profile_store = profile_store
        self.preference_store = preference_store
        for module, name, value in (
            (profile_store, 'PROFILE_PATH', directory / 'profile.json'),
            (preference_store, 'PREFERENCES_PATH', directory / 'preferences.json'),
            (attachment_store, 'DB_PATH', directory / 'attachments.sqlite3'),
            (attachment_store, 'UPLOAD_DIR', directory / 'uploads'),
            (career_store, 'DB_PATH', directory / 'career.sqlite3'),
            (history, 'DB_PATH', directory / 'history.sqlite3'),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app, base_url='http://localhost:8010', raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.profile = CandidateProfile(
            name='Stored Candidate',
            graduation_year=2022,
            degree='B.Com',
            skills=['Excel', 'SQL'],
            projects=['Revenue dashboard'],
            research=['Market survey'],
            preferred_locations=['Pune'],
            preferred_roles=['Business Analyst'],
            education=['B.Com, 2022'],
            experience=['Analyst at ExampleCo'],
            certifications=['Bookkeeping Certificate'],
            evidence={'skills': ['Revenue dashboard used Excel']},
            experience_years=2,
            experience_level='entry_level',
            work_mode_preferences=['remote', 'hybrid'],
            parsing_warnings=['Review parsed dates'],
        )
        profile_store.save_profile(self.profile)

    def test_get_current_profile_and_derived_preferences_without_origin(self):
        profile = self.client.get('/profile/current')
        preferences = self.client.get('/preferences/current')

        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()['skills'], ['Excel', 'SQL'])
        self.assertEqual(preferences.status_code, 200)
        self.assertEqual(preferences.json()['preferred_locations'], ['Pune'])
        self.assertEqual(preferences.json()['required_graduation_year'], 2022)
        self.assertEqual(preferences.json()['allowed_work_modes'], ['remote', 'hybrid'])

    def test_partial_profile_update_preserves_cv_evidence_and_persists(self):
        response = self.client.put(
            '/profile/current',
            json={
                'preferred_locations': ['Berlin'],
                'preferred_roles': ['Finance Analyst'],
                'experience_level': 'experienced',
            },
            headers=self.origin,
        )

        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated['preferred_locations'], ['Berlin'])
        self.assertEqual(updated['preferred_roles'], ['Finance Analyst'])
        self.assertEqual(updated['skills'], ['Excel', 'SQL'])
        self.assertEqual(updated['projects'], ['Revenue dashboard'])
        self.assertEqual(updated['research'], ['Market survey'])
        self.assertEqual(updated['evidence'], {'skills': ['Revenue dashboard used Excel']})
        self.assertEqual(updated['parsing_warnings'], ['Review parsed dates'])

        reloaded = self.profile_store.load_profile()
        self.assertEqual(reloaded.preferred_locations, ['Berlin'])
        self.assertEqual(reloaded.education, ['B.Com, 2022'])
        self.assertEqual(self.client.get('/profile/current').json(), updated)

    def test_partial_preference_update_preserves_untouched_fields_and_persists(self):
        self.preference_store.save_preferences(JobSearchPreferences(
            allowed_work_modes=['remote', 'onsite'],
            preferred_locations=['Pune'],
            preferred_role_families=['Analytics'],
            allow_zero_to_two_when_fresher_friendly=False,
            verification_limit=7,
        ))

        response = self.client.put(
            '/preferences/current',
            json={
                'preferred_locations': ['London'],
                'minimum_match_score': 72,
                'require_active_application': True,
            },
            headers=self.origin,
        )

        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated['preferred_locations'], ['London'])
        self.assertEqual(updated['minimum_match_score'], 72)
        self.assertTrue(updated['require_active_application'])
        self.assertEqual(updated['allowed_work_modes'], ['remote', 'onsite'])
        self.assertFalse(updated['allow_zero_to_two_when_fresher_friendly'])
        self.assertEqual(updated['verification_limit'], 7)
        self.assertEqual(self.preference_store.preferences_for(self.profile_store.load_profile()).model_dump(), updated)

    def test_invalid_and_private_updates_are_rejected_without_persistence(self):
        profile_before = self.client.get('/profile/current').json()
        profile_cases = [
            {'experience_years': -1},
            {'graduation_year': 1800},
            {'experience_level': 'wizard'},
            {'schema_version': 999},
            {'access_token': 'private'},
            {'_profile_key': 'private'},
        ]
        for payload in profile_cases:
            with self.subTest(profile=payload):
                response = self.client.put('/profile/current', json=payload, headers=self.origin)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get('/profile/current').json(), profile_before)

        preferences_before = self.client.get('/preferences/current').json()
        preference_cases = [
            {'minimum_match_score': 101},
            {'max_required_experience_years': 71},
            {'required_graduation_year': 1800},
            {'allowed_work_modes': []},
            {'allowed_work_modes': ['remote', 'teleport']},
            {'freshness_preference': '1 week'},
            {'refresh_token': 'private'},
        ]
        for payload in preference_cases:
            with self.subTest(preferences=payload):
                response = self.client.put('/preferences/current', json=payload, headers=self.origin)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get('/preferences/current').json(), preferences_before)

    def test_mutations_require_exact_local_origin_while_reads_do_not(self):
        self.assertEqual(self.client.get('/profile/current').status_code, 200)
        self.assertEqual(self.client.get('/preferences/current').status_code, 200)

        for path, payload in (
            ('/profile/current', {'preferred_locations': ['Paris']}),
            ('/preferences/current', {'minimum_match_score': 60}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.put(path, json=payload).status_code, 403)
                self.assertEqual(self.client.put(
                    path, json=payload, headers={'Origin': 'https://attacker.example'},
                ).status_code, 403)
                self.assertEqual(self.client.put(path, json=payload, headers=self.origin).status_code, 200)

    def test_career_agent_loads_latest_saved_profile_and_preferences(self):
        self.assertEqual(self.client.put(
            '/profile/current',
            json={'preferred_roles': ['Data Analyst'], 'experience_years': 3},
            headers=self.origin,
        ).status_code, 200)
        self.assertEqual(self.client.put(
            '/preferences/current',
            json={
                'preferred_locations': ['Paris'],
                'allowed_work_modes': ['remote'],
                'max_required_experience_years': 4,
                'minimum_match_score': 63,
                'require_active_application': True,
            },
            headers=self.origin,
        ).status_code, 200)

        from app.services.career_agent import CareerAgent
        result = asyncio.run(CareerAgent(providers=[]).search('Find jobs'))

        self.assertEqual(result['profile']['preferred_roles'], ['Data Analyst'])
        self.assertEqual(result['profile']['experience_years'], 3)
        self.assertEqual(result['preferences']['preferred_locations'], ['Paris'])
        self.assertEqual(result['preferences']['allowed_work_modes'], ['remote'])
        self.assertEqual(result['preferences']['minimum_match_score'], 63)
        self.assertTrue(result['preferences']['require_active_application'])
        self.assertEqual(result['intent']['locations'], ['Paris'])
        self.assertEqual(result['intent']['minimum_match_score'], 63)

    def test_cv_upload_remains_compatible_and_preserves_profile_preferences(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), 'NEW CANDIDATE\nB.Tech 2025\nPython FastAPI SQL')
            content = pdf.tobytes()

        self.assertEqual(self.client.post(
            '/cv/upload', files={'file': ('resume.pdf', content, 'application/pdf')},
        ).status_code, 403)
        uploaded = self.client.post(
            '/cv/upload',
            files={'file': ('resume.pdf', content, 'application/pdf')},
            headers=self.origin,
        )

        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        profile = uploaded.json()['profile']
        self.assertEqual(profile['name'], 'New Candidate')
        self.assertIn('Python', profile['skills'])
        self.assertEqual(profile['preferred_locations'], ['Pune'])
        self.assertEqual(profile['preferred_roles'], ['Business Analyst'])


if __name__ == '__main__':
    unittest.main()
