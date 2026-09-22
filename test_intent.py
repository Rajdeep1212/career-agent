"""Offline contract tests: all candidate evidence is fictional."""
import importlib
import unittest

from app.models.career import SearchIntent
from app.models.schemas import CandidateProfile


class IntentTests(unittest.TestCase):
    def parse(self, text, previous=None):
        module = importlib.util.find_spec('app.services.search_intent')
        self.assertIsNotNone(module, 'search intent implementation is required')
        return importlib.import_module('app.services.search_intent').interpret_search_request(text, previous)

    def test_multiple_roles_and_locations(self):
        result = self.parse('Find QA Engineer and Data Analyst jobs in Kolkata or Pune, excluding Delhi')
        self.assertEqual(set(result.roles_requested), {'QA Engineer', 'Data Analyst'})
        self.assertEqual(result.locations, ['Kolkata', 'Pune'])
        self.assertEqual(result.excluded_locations, ['Delhi'])

    def test_arbitrary_role_and_location(self):
        result = self.parse('Find Quantum Photonics Technician jobs in Reykjavik, excluding Akureyri')
        self.assertIn('Quantum Photonics Technician', result.roles_requested)
        self.assertEqual(result.locations, ['Reykjavik'])
        self.assertEqual(result.excluded_locations, ['Akureyri'])

    def test_remote_only(self):
        result = self.parse('Remote only QA jobs')
        self.assertTrue(result.remote_allowed)
        self.assertFalse(result.onsite_allowed)
        self.assertFalse(result.hybrid_allowed)

    def test_remote_or_city(self):
        result = self.parse('Data analyst jobs remote or in Kolkata')
        self.assertEqual(result.locations, ['Kolkata'])
        self.assertTrue(result.remote_allowed and result.onsite_allowed)

    def test_non_coding_cv_discovery(self):
        result = self.parse('Suggest non-coding jobs suitable for my CV')
        self.assertTrue(result.non_coding)
        self.assertTrue(result.cv_discovery)
        self.assertEqual(result.roles_requested, [])

    def test_filters(self):
        result = self.parse('Find Data Analyst jobs for 2025 graduates with 0-2 years experience, minimum score 75, no internships, no contract, at Acme and ExampleCorp, posted in last 7 days')
        self.assertEqual(result.graduation_year, 2025)
        self.assertEqual((result.experience_min, result.experience_max), (0, 2))
        self.assertEqual(result.minimum_match_score, 75)
        self.assertFalse(result.internship_allowed)
        self.assertFalse(result.contract_allowed)
        self.assertEqual(result.company_preferences, ['Acme', 'ExampleCorp'])
        self.assertEqual(result.freshness_preference, '7 days')

    def test_strong_match(self):
        self.assertEqual(self.parse('Only strong matches').minimum_match_score, 75)

    def test_score_only_refinement_preserves_state(self):
        previous = self.parse('Find QA Engineer jobs in Kolkata for freshers')
        result = self.parse('Only show scores above 80', previous)
        self.assertTrue(result.filter_only)
        self.assertEqual(result.roles_requested, previous.roles_requested)
        self.assertEqual(result.locations, ['Kolkata'])
        self.assertEqual(result.minimum_match_score, 80)
        self.assertEqual(previous.minimum_match_score, 0)

    def test_location_refinement_does_not_erase_roles(self):
        previous = self.parse('Find QA Engineer jobs in Kolkata')
        result = self.parse('Instead in Oslo and Bergen, excluding Tromso', previous)
        self.assertEqual(result.roles_requested, previous.roles_requested)
        self.assertEqual(result.locations, ['Oslo', 'Bergen'])
        self.assertFalse(result.filter_only)

    def test_role_refinement_replaces_unless_additive(self):
        previous = self.parse('Find QA Engineer jobs in Kolkata')
        self.assertEqual(self.parse('Instead find Data Analyst jobs', previous).roles_requested, ['Data Analyst'])
        self.assertEqual(set(self.parse('Also include Business Analyst roles', previous).roles_requested), {'QA Engineer', 'Business Analyst'})

    def test_negated_remote_and_fresher(self):
        result = self.parse('Find business analyst jobs in Kochi, no remote, freshers only')
        self.assertFalse(result.remote_allowed)
        self.assertTrue(result.fresher_preference)
        self.assertEqual(result.experience_max, 0)

    def test_parser_warnings_present(self):
        self.assertTrue(self.parse('Find great jobs').warnings)

    def test_exclusion_refinement_keeps_original_role(self):
        previous = self.parse('Find Data Analyst jobs in Kolkata')
        result = self.parse('Exclude Mumbai and Delhi', previous)
        self.assertEqual(result.roles_requested, previous.roles_requested)
        self.assertEqual(result.excluded_locations, ['Mumbai', 'Delhi'])

    def test_compact_score_refinement(self):
        previous = self.parse('Find Data Analyst jobs in Kolkata')
        result = self.parse('Show only 75+ matches', previous)
        self.assertEqual(result.minimum_match_score, 75)
        self.assertTrue(result.filter_only)

    def test_remote_plus_kolkata(self):
        result = self.parse('Find QA Engineer jobs remote + Kolkata')
        self.assertEqual(result.locations, ['Kolkata'])
        self.assertTrue(result.onsite_allowed and result.remote_allowed)

    def test_location_punctuation_not_included(self):
        result = self.parse('Find Data Analyst jobs in Kolkata, Pune, no remote')
        self.assertEqual(result.locations, ['Kolkata', 'Pune'])

    def test_clear_roles_for_explicit_discovery_refinement(self):
        previous = self.parse('Find Python Developer jobs in Kolkata')
        result = self.parse('Instead suggest non-coding roles based on my CV', previous)
        self.assertEqual(result.roles_requested, [])
        self.assertEqual(result.role_families, [])
        self.assertTrue(result.cv_discovery and result.non_coding)

    def test_unsupported_salary_gets_warning(self):
        result = self.parse('Find Data Analyst jobs with salary above 8 LPA')
        self.assertTrue(any('salary' in warning.lower() for warning in result.warnings))


class DiscoveryTests(unittest.TestCase):
    def service(self):
        module = importlib.util.find_spec('app.services.role_discovery')
        self.assertIsNotNone(module, 'role discovery implementation is required')
        return importlib.import_module('app.services.role_discovery')

    def test_evidence_comes_from_profile(self):
        profile = CandidateProfile(skills=['Excel', 'SQL'], projects=['Built a sales dashboard using Power BI'])
        roles = self.service().suggest_role_families(profile)
        analytics = next(role for role in roles if role.family == 'analytics')
        self.assertEqual(analytics.suitability, 'strong')
        self.assertTrue(analytics.evidence)
        for item in analytics.evidence:
            self.assertTrue(any(value in item for value in profile.skills + profile.projects))
        self.assertNotIn('Python', ' '.join(analytics.evidence))

    def test_empty_profile_does_not_invent_evidence(self):
        roles = self.service().suggest_role_families(CandidateProfile())
        self.assertTrue(roles)
        self.assertTrue(all(not role.evidence and role.suitability == 'exploratory' for role in roles))

    def test_broad_catalog_covers_non_ai_careers(self):
        service = self.service()
        for skill, family in [('Figma', 'design'), ('Bookkeeping', 'finance'), ('Recruitment', 'hr'), ('Salesforce', 'sales'), ('Customer service', 'support'), ('AutoCAD', 'engineering'), ('SEO', 'marketing'), ('Logistics', 'operations'), ('Selenium', 'qa')]:
            with self.subTest(family=family):
                roles = service.suggest_role_families(CandidateProfile(skills=[skill]))
                self.assertTrue(any(role.family == family and role.evidence for role in roles))

    def test_requested_unknown_role_preserved(self):
        intent = SearchIntent(roles_requested=['Quantum Photonics Technician'])
        roles = self.service().expand_roles(intent, CandidateProfile())
        self.assertIn('Quantum Photonics Technician', [title for role in roles for title in role.titles])
        self.assertTrue(all(role.suitability == 'exploratory' for role in roles))

    def test_non_coding_excludes_coding_families(self):
        roles = self.service().expand_roles(SearchIntent(non_coding=True, cv_discovery=True), CandidateProfile(skills=['Python', 'Excel', 'Communication']))
        self.assertTrue(roles)
        self.assertFalse(any(role.family in {'software', 'ai', 'data_engineering', 'devops'} for role in roles))

    def test_preferences_are_not_candidate_skill_evidence(self):
        roles = self.service().expand_roles(SearchIntent(roles_requested=['Machine Learning Engineer']), CandidateProfile(preferred_roles=['Machine Learning Engineer']))
        self.assertTrue(all(not role.evidence and role.suitability == 'exploratory' for role in roles))


class PromptRegressionTests(unittest.TestCase):
    def test_six_requested_examples_and_remote_match(self):
        from app.services.search_intent import interpret_search_request as parse
        from app.services.role_discovery import expand_roles
        from app.services.search_planner import plan_search_queries
        profile = CandidateProfile(skills=['Python', 'SQL', 'Postman'], experience_level='fresher')
        data = parse('Find Data Analyst jobs suitable for my CV.')
        self.assertEqual(data.roles_requested, ['Data Analyst'])
        self.assertEqual({r.family for r in expand_roles(data, profile)}, {'analytics'})
        qa = parse('Find QA/testing roles where my Python/API skills help.')
        self.assertEqual(qa.roles_requested, ['QA Engineer'])
        business = parse('Find Business Analyst jobs in Kolkata.')
        self.assertEqual(business.roles_requested, ['Business Analyst'])
        self.assertEqual(business.locations, ['Kolkata'])
        ai = parse('Find Agentic AI fresher roles.')
        self.assertEqual(ai.roles_requested, ['Agentic AI Engineer'])
        self.assertTrue(ai.fresher_preference)
        self.assertFalse(any('fresher fresher' in q.query for q in plan_search_queries(profile, ai, expand_roles(ai, profile))))
        noncoding = parse('Find non-coding technical roles suitable for me.')
        self.assertTrue(noncoding.non_coding)
        self.assertEqual(noncoding.roles_requested, [])
        discovery = parse("I don't know what job suits me. Analyze my resume and find opportunities.")
        self.assertTrue(discovery.cv_discovery)
        self.assertEqual(discovery.roles_requested, [])
        remote = parse('Find remote jobs with strong profile match')
        self.assertTrue(remote.remote_allowed)
        self.assertFalse(remote.onsite_allowed or remote.hybrid_allowed)
        self.assertEqual(remote.minimum_match_score, 75)
        self.assertTrue(all('remote' in q.query for q in plan_search_queries(profile, remote, expand_roles(remote, profile))))
        refined = parse('Show jobs above 75%', data)
        self.assertTrue(refined.filter_only)
        self.assertEqual(refined.roles_requested, ['Data Analyst'])

    def test_negated_strict_refinement_preserves_role(self):
        from app.services.search_intent import interpret_search_request as parse
        previous = parse('Find QA Engineer jobs in Kolkata, strict mode')
        for prompt in ['not strict', 'relax strict mode', 'no strict mode']:
            with self.subTest(prompt=prompt):
                intent = parse(prompt, previous)
                self.assertFalse(intent.strict_mode)
                self.assertEqual(intent.roles_requested, ['QA Engineer'])
                self.assertEqual(intent.locations, ['Kolkata'])

    def test_role_expansion_has_at_most_six_distinct_titles(self):
        from app.services.role_discovery import expand_roles
        roles = expand_roles(SearchIntent(cv_discovery=True), CandidateProfile())
        self.assertTrue(roles)
        self.assertLessEqual(len({t.casefold() for r in roles for t in r.titles}), 6)

    def test_unknown_explicit_role_survives_cv_discovery(self):
        from app.services.search_intent import interpret_search_request as parse
        from app.services.role_discovery import expand_roles
        intent = parse('Find Quantum Photonics Technician jobs suitable for my CV')
        roles = expand_roles(intent, CandidateProfile(skills=['Python']))
        self.assertEqual([title for role in roles for title in role.titles], ['Quantum Photonics Technician'])


if __name__ == '__main__':
    unittest.main()
