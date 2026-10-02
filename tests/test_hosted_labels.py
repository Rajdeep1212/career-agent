"""Q4: hosted labels for a batch. Prompts carry the rubric, the target profile and blind listing fields only;
no CV text may be sent. Fictional jobs and a CV full of sentinel strings; no model is called."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.eval import hosted_labels as hosted
from app.eval.label_page import BLIND_FIELDS, RUBRIC
from app.models.schemas import CandidateProfile
from app.storage import profile_store

CV_LINE = "SENTINEL-PRIVATE-CV-LINE: built a fraud detector with Kafka at SentinelCorp"
CV_PROFILE = CandidateProfile(name="Sentinel Candidate Name", skills=["Python"], projects=[CV_LINE],
                              experience=["Intern at SentinelCorp doing sentinel things"])
TARGET = """# Target profile — Sentinel Candidate Name

roles: AI Engineer
skills: Python, SQL

I want an entry-level Python role.
"""


def blind_line(index: int, description: str | None = None) -> dict:
    return {"item_id": f"item{index:02d}", "title": f"Graduate Engineer {index}", "company": "Example Corp", "location": "Pune, India",
            "work_mode": "hybrid", "employment_type": "Full-time", "posted_date": "2026-09-20", "salary": None,
            "description": description or f"Freshers welcome. Python and SQL. Job number {index}.",
            "application_url": f"https://job-boards.greenhouse.io/b/jobs/{index}",
            "total_score": 91, "eligibility_status": "eligible", "surfaced_by": [{"search": "A"}]}


class PromptCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.batch = self.root / "batches" / "test-batch"
        self.batch.mkdir(parents=True)
        self.write_blind([blind_line(index) for index in range(5)])
        (self.batch / "jobs.jsonl").write_text(json.dumps({"item_id": "item00", "match": CV_LINE}) + "\n", encoding="utf-8")
        (self.batch / "meta.json").write_text(json.dumps({"batch_id": "test-batch", "rubric_version": "r1"}), encoding="utf-8")
        self.target = self.root / "target_profile.md"
        self.target.write_text(TARGET, encoding="utf-8")
        patcher = patch.object(profile_store, "PROFILE_PATH", self.root / "current_profile.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        profile_store.save_profile(CV_PROFILE)

    def write_blind(self, rows):
        (self.batch / "blind.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def prompts(self, size=2):
        return hosted.build_prompts(self.batch, self.target, chunk_size=size)


class PromptTests(PromptCase):
    def test_both_orders_cover_every_job_once_and_the_second_is_the_first_reversed(self):
        prompts = self.prompts()
        self.assertEqual(sorted({prompt.order for prompt in prompts}), ["forward", "reverse"])
        forward = [item for prompt in prompts if prompt.order == "forward" for item in prompt.item_ids]
        reverse = [item for prompt in prompts if prompt.order == "reverse" for item in prompt.item_ids]
        self.assertEqual(forward, [f"item{index:02d}" for index in range(5)])
        self.assertEqual(reverse, forward[::-1])
        self.assertEqual([len(prompt.item_ids) for prompt in prompts if prompt.order == "forward"], [2, 2, 1])

    def test_a_prompt_holds_the_rubric_the_target_profile_and_blind_fields_only(self):
        text = self.prompts()[0].text
        for grade, meaning in RUBRIC["grades"].items():
            self.assertIn(f"{grade}: {meaning}", text)
        self.assertIn("roles: AI Engineer", text)
        self.assertIn("I want an entry-level Python role.", text)
        self.assertIn("Graduate Engineer 0", text)
        self.assertIn("Job number 0.", text)
        for hidden in ("total_score", "eligibility", "surfaced_by", "search", "91", "fit score"):
            self.assertNotIn(hidden, text)
        self.assertEqual(set(hosted.PROMPT_FIELDS), set(BLIND_FIELDS) - {"application_url"})

    def test_reasons_come_before_the_grade_in_the_requested_format(self):
        text = self.prompts()[0].text
        self.assertIn('{"item_id": "...", "reason": "...", "grade": 0}', text)
        self.assertLess(text.index('"reason"'), text.index('"grade"'))

    def test_no_cv_text_reaches_a_prompt(self):
        everything = "\n".join(prompt.text for prompt in self.prompts()).casefold()
        self.assertNotIn("sentinel", everything)            # not the CV profile, not the batch key, not the name in the heading
        self.assertNotIn("candidate name", everything)

    def test_a_cv_string_in_a_listing_or_in_the_target_profile_stops_the_build(self):
        self.write_blind([blind_line(0, f"Copied by mistake: {CV_LINE}")])
        with self.assertRaises(hosted.PrivacyError) as caught:
            self.prompts()
        self.assertNotIn("SENTINEL", str(caught.exception))          # the error does not repeat the text
        self.write_blind([blind_line(0)])
        self.target.write_text(TARGET + "\nIntern at SentinelCorp doing sentinel things\n", encoding="utf-8")
        with self.assertRaises(hosted.PrivacyError):
            self.prompts()
        self.target.write_text(TARGET + "\nMy name is Sentinel Candidate Name.\n", encoding="utf-8")
        with self.assertRaises(hosted.PrivacyError):
            self.prompts()

    def test_long_descriptions_are_cut_and_lines_stay_short_enough_to_read(self):
        self.write_blind([blind_line(0, "word " * 5000)])
        prompt = self.prompts()[0]
        self.assertIn("[listing cut at", prompt.text)
        self.assertLess(max(len(line) for line in prompt.text.splitlines()), 400)


class ParseTests(unittest.TestCase):
    IDS = ["item00", "item01"]

    def test_a_reply_is_one_json_line_per_job_with_the_reason_first(self):
        reply = '```json\n{"item_id": "item00", "reason": "Entry-level Python role.", "grade": 3}\n\n' \
                '{"item_id": "item01", "reason": "Sales.", "grade": 0}\n```'
        self.assertEqual(hosted.parse_reply(reply, self.IDS),
                         [{"item_id": "item00", "reason": "Entry-level Python role.", "grade": 3},
                          {"item_id": "item01", "reason": "Sales.", "grade": 0}])

    def test_bad_replies_are_refused_whole(self):
        good = '{"item_id": "item00", "reason": "ok", "grade": 2}'
        cases = {"grade before the reason": '{"item_id": "item01", "grade": 2, "reason": "ok"}',
                 "grade out of range": '{"item_id": "item01", "reason": "ok", "grade": 4}',
                 "grade as text": '{"item_id": "item01", "reason": "ok", "grade": "2"}',
                 "empty reason": '{"item_id": "item01", "reason": " ", "grade": 2}',
                 "unknown job": '{"item_id": "item99", "reason": "ok", "grade": 2}',
                 "the same job twice": good, "not json": "I think this is a 2.", "a missing job": ""}
        for name, second in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                hosted.parse_reply(good + "\n" + second, self.IDS)


class MetricTests(unittest.TestCase):
    def test_exact_agreement_and_the_confusion_matrix(self):
        gold, other = [0, 1, 2, 3, 3], [0, 2, 2, 3, 1]
        self.assertEqual(hosted.exact_agreement(gold, other), 0.6)
        matrix = hosted.confusion(gold, other)
        self.assertEqual((matrix[0][0], matrix[1][2], matrix[2][2], matrix[3][3], matrix[3][1]), (1, 1, 1, 1, 1))
        self.assertEqual(sum(sum(row) for row in matrix), 5)

    def test_quadratic_weighted_kappa(self):
        self.assertEqual(hosted.quadratic_weighted_kappa([0, 1, 2, 3], [0, 1, 2, 3]), 1.0)
        self.assertEqual(hosted.quadratic_weighted_kappa([0, 0, 3, 3], [3, 3, 0, 0]), -1.0)
        self.assertAlmostEqual(hosted.quadratic_weighted_kappa([0, 3, 0, 3], [0, 0, 3, 3]), 0.0)
        # Worked by hand: observed weighted disagreement (1 + 1) / 9 = 2/9; expected (10 + 2 + 2 + 10) / 9 / 4 = 2/3; 1 - 1/3.
        self.assertAlmostEqual(hosted.quadratic_weighted_kappa([0, 1, 2, 3], [1, 1, 2, 2]), 2 / 3)
        self.assertTrue(math.isnan(hosted.quadratic_weighted_kappa([3, 3, 3], [3, 3, 3])))      # no variation: undefined

    def test_ndcg_at_k(self):
        self.assertEqual(hosted.ndcg_at_k([3, 2, 1, 0], 10), 1.0)
        self.assertEqual(hosted.ndcg_at_k([0, 0, 0], 10), 0.0)
        ranked, ideal = [0, 3], [3, 0]
        dcg = (2 ** 3 - 1) / math.log2(3)
        self.assertAlmostEqual(hosted.ndcg_at_k(ranked, 10), dcg / 7)
        self.assertAlmostEqual(hosted.ndcg_at_k([1, 3, 2], 2), ((2 ** 1 - 1) + (2 ** 3 - 1) / math.log2(3)) / (7 + 3 / math.log2(3)))
        self.assertAlmostEqual(hosted.ndcg_at_k([2], 10, pool=[3, 2]), 3 / (7 + 3 / math.log2(3)))     # a better job was left out
        self.assertEqual(ideal, sorted(ranked, reverse=True))


if __name__ == "__main__":
    unittest.main()
