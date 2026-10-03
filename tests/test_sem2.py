"""SEM2: the rankers, detectors, metrics and bootstrap of docs/SEMANTIC_PLAN.md. Fictional listings; no model is loaded."""
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.eval import semantic
from app.models.career import SearchIntent
from app.models.schemas import CandidateProfile, JobPosting, JobSearchPreferences
from app.services import eligibility
from app.services.eligibility import fresher_detectors

FACTS = {"graduation_year": 2025, "cgpa": 7.64}


def outcomes(title, text):
    return [(item.outcome, item.quote) for item in fresher_detectors(title, text, FACTS)]


class TextTests(unittest.TestCase):
    def test_tokens_are_lower_case_runs_of_letters_digits_plus_and_hash(self):
        self.assertEqual(semantic.tokenize("C++ and C# for ML-Ops, Python3!"), ["c++", "and", "c#", "for", "ml", "ops", "python3"])

    def test_job_text_is_the_title_and_the_first_2000_characters(self):
        text = semantic.job_text({"title": "AI Engineer", "description": "x" * 5000})
        self.assertEqual(text, "AI Engineer\n" + "x" * 2000)
        self.assertEqual(semantic.job_text({"title": "Data Scientist", "description": None}), "Data Scientist\n")


class RankingTests(unittest.TestCase):
    def test_bm25_puts_the_matching_job_first_and_breaks_ties_by_item_id(self):
        jobs = {"b": "Sales executive for retail", "a": "Python FastAPI backend engineer with SQL", "c": "Sales executive for retail"}
        order = semantic.order_by(semantic.bm25_scores("Python backend SQL", jobs))
        self.assertEqual(order, ["a", "b", "c"])          # b and c score the same: item_id decides

    def test_rrf_uses_k_60(self):
        self.assertEqual(semantic.RRF_K, 60)
        fused = semantic.rrf([["a", "b"], ["b", "a"]])
        self.assertAlmostEqual(fused["a"], 1 / 61 + 1 / 62)
        self.assertAlmostEqual(fused["b"], 1 / 62 + 1 / 61)

    def test_dense_scores_are_cosine_similarity(self):
        class Stub:
            model = "stub"

            def query(self, text):
                return np.array([1.0, 0.0])

            def passages(self, texts):
                return np.array([[0.0, 2.0], [3.0, 0.0], [1.0, 1.0]])
        scores = semantic.dense_scores(Stub(), "profile", {"x": "a", "y": "b", "z": "c"})
        self.assertEqual(semantic.order_by(scores), ["y", "z", "x"])
        self.assertAlmostEqual(scores["z"], 1 / math.sqrt(2))

    def test_the_eligibility_gate_sorts_by_tier_then_relevance(self):
        relevance = {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.1}
        tiers = {"a": "excluded", "b": "uncertain", "c": "eligible", "d": "eligible"}
        self.assertEqual(semantic.gated_order(relevance, tiers), ["c", "d", "b", "a"])

    def test_v1_order_is_the_shown_order_then_the_excluded_sample_by_v1_score(self):
        jobs = [{"item_id": "s2", "total_score": 10, "surfaced_by": [{"search": "A", "rank": 2, "stage": "post_filter"}]},
                {"item_id": "x1", "total_score": 5, "surfaced_by": [{"search": "A", "rank": None, "stage": "excluded_sample"}]},
                {"item_id": "s1", "total_score": 50, "surfaced_by": [{"search": "A", "rank": 1, "stage": "post_filter"}]},
                {"item_id": "x2", "total_score": 9, "surfaced_by": [{"search": "A", "rank": None, "stage": "excluded_sample"}]},
                {"item_id": "b1", "total_score": 99, "surfaced_by": [{"search": "B", "rank": 1, "stage": "post_filter"}]}]
        self.assertEqual(semantic.v1_order(jobs, "A"), ["s1", "s2", "x2", "x1"])
        self.assertEqual(semantic.pool(jobs, "A"), {"s1", "s2", "x1", "x2"})


class DetectorTests(unittest.TestCase):
    def test_years_required(self):
        self.assertEqual(outcomes("Engineer", "Requires 5+ years of experience in Java.")[0][0], "excluded")
        self.assertEqual(outcomes("Engineer", "Bachelors + 2 years of related experience OR Masters + 0 years.")[0][0], "excluded")
        self.assertEqual(outcomes("Engineer", "We want 1-3 years of experience with Python.")[0][0], "uncertain")
        self.assertEqual(outcomes("Engineer", "Freshers welcome; 0-2 years of experience."), [])          # starts at 0
        self.assertEqual(outcomes("Engineer", "Freshers welcome with 2+ years of experience.")[0][0], "uncertain")
        self.assertEqual(outcomes("Engineer", "Founded 25 years ago. Freshers welcome."), [])

    def test_seniority(self):
        for title in ("Staff Software Engineer", "Principal Engineer", "Engineering Manager", "Lead Data Scientist",
                      "Head of Data", "Director, AI"):
            self.assertEqual(outcomes(title, "Python.")[0][0], "excluded", title)
        self.assertEqual(outcomes("Analyst", "Assistant Vice President Expectations: lead a team.")[0][0], "excluded")
        self.assertEqual(outcomes("Senior Machine Learning Engineer", "Python.")[0][0], "uncertain")
        self.assertEqual(outcomes("Senior Machine Learning Engineer", "Requires 6+ years of experience.")[0][0], "excluded")
        self.assertEqual(outcomes("Graduate Engineer Trainee", "Python."), [])

    def test_batch_and_gpa_cutoffs(self):
        self.assertEqual(outcomes("Intern", "Must have 8.0 or above GPA.")[0], ("excluded", "Must have 8.0 or above GPA."))
        self.assertEqual(outcomes("Engineer", "Minimum CGPA 7.0 is required."), [])
        self.assertEqual(outcomes("Engineer", "Open to the 2026 batch only.")[0][0], "excluded")
        self.assertEqual(outcomes("Engineer", "Open to 2024 and 2025 batch graduates."), [])

    def test_students_only_and_returnships(self):
        self.assertEqual(outcomes("AI Resident", "Open for students in the final year of their undergraduate degree.")[0][0], "excluded")
        self.assertEqual(outcomes("Developer", "A returnship for developers on a career break of 1-5 years.")[0][0], "excluded")
        self.assertEqual(outcomes("Data Intern", "Help the team with dashboards.")[0][0], "uncertain")

    def test_every_finding_quotes_one_sentence_of_the_listing(self):
        for outcome, quote in outcomes("Staff Engineer", "Great team. Requires 7.5+ years of experience. Hybrid."):
            self.assertTrue(quote and len(quote) <= 200)
        self.assertIn("Requires 7.5+ years of experience.", [quote for _, quote in outcomes("Engineer", "Great team. Requires 7.5+ years of experience. Hybrid.")])

    def test_the_app_does_not_use_the_new_detectors(self):
        job = JobPosting(company="Example Corp", title="Staff Engineer", location="Pune, India", description="Python.")
        with patch.object(eligibility, "fresher_detectors", side_effect=AssertionError("wired into the app")):
            eligibility.evaluate_eligibility(CandidateProfile(), job, JobSearchPreferences(), SearchIntent())

    def test_tiers_combine_the_existing_check_with_the_detectors(self):
        self.assertEqual(semantic.tier("eligible", []), "eligible")
        self.assertEqual(semantic.tier("eligible", ["uncertain"]), "uncertain")
        self.assertEqual(semantic.tier("uncertain", ["excluded"]), "excluded")
        self.assertEqual(semantic.tier("excluded", []), "excluded")


class MetricTests(unittest.TestCase):
    def test_precision_at_5_counts_grade_2_and_3(self):
        self.assertEqual(semantic.precision_at_k([3, 2, 1, 0, 2, 3], 5), 0.6)
        self.assertEqual(semantic.precision_at_k([3, 3], 5), 0.4)

    def test_the_bootstrap_is_paired_seeded_and_zero_for_identical_rankers(self):
        pools = {"A": ["a", "b", "c", "d"], "B": ["e", "f", "g"]}
        grades = {"a": 3, "b": 0, "c": 2, "d": 1, "e": 3, "f": 0, "g": 2}
        orders = {"v1": {"A": ["a", "b", "c", "d"], "B": ["e", "f", "g"]}, "same": {"A": ["a", "b", "c", "d"], "B": ["e", "f", "g"]},
                  "better": {"A": ["a", "c", "d", "b"], "B": ["e", "g", "f"]}}
        result = semantic.bootstrap(pools, orders, grades, semantic.ndcg10, resamples=200, seed=7)
        self.assertEqual(result["same"]["mean"], (0.0, 0.0, 0.0))
        point, low, high = result["better"]["mean"]
        self.assertGreater(point, 0)
        self.assertLessEqual(low, point)
        self.assertLessEqual(point, high)
        self.assertEqual(result, semantic.bootstrap(pools, orders, grades, semantic.ndcg10, resamples=200, seed=7))
        self.assertEqual(semantic.BOOTSTRAP_RESAMPLES, 1000)
        self.assertEqual(semantic.SEED, 20261003)

    def test_the_decision_rule(self):
        self.assertTrue(semantic.ships(gold_mean=(0.05, 0.01, 0.09), hosted_mean=(0.0, -0.05, 0.04)))
        self.assertFalse(semantic.ships(gold_mean=(0.05, -0.01, 0.09), hosted_mean=(0.02, -0.01, 0.06)))     # interval touches zero
        self.assertFalse(semantic.ships(gold_mean=(0.05, 0.01, 0.09), hosted_mean=(-0.01, -0.05, 0.03)))     # loses on hosted
        self.assertEqual(semantic.PRIMARY, "hybrid x eligibility")


class EmbedderTests(unittest.TestCase):
    def test_too_little_free_memory_stops_before_loading_the_model(self):
        with patch.object(semantic, "free_ram_bytes", return_value=900 * 1024 ** 2), \
                self.assertRaises(MemoryError) as caught:
            semantic.FastEmbedder()
        self.assertIn("1 GB", str(caught.exception))

    def test_the_model_cache_is_under_hf_home(self):
        with patch.dict("os.environ", {"HF_HOME": "F:/huggingface"}):
            self.assertEqual(semantic.cache_dir(), Path("F:/huggingface/fastembed"))

    def test_vectors_are_saved_with_ids_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v.npz"
            semantic.save_vectors(path, ["a", "b"], "stub", np.array([[1.0, 0.0], [0.0, 1.0]]))
            ids, model, matrix = semantic.load_vectors(path)
            self.assertEqual((ids, model, matrix.shape), (["a", "b"], "stub", (2, 2)))


if __name__ == "__main__":
    unittest.main()
