"""Gold labels made too fast are not gold. The first run of batch gold-20261001-r1 was 106 labels in 339 seconds,
64 of them grade 3, and could not be scored against. The page now times every job and refuses to call a label
file complete when too many were quick or when one grade takes more than half."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.eval.label_page import FAST_SECONDS, LabelSession, handle, label_quality

ROOT = Path(__file__).resolve().parents[1]
HOST = {"Host": "127.0.0.1:8765"}
JSON = {**HOST, "Content-Type": "application/json"}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class QualityCase(unittest.TestCase):
    total = 5

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.batch = self.root / "batches" / "test-batch"
        self.batch.mkdir(parents=True)
        rows = [{"item_id": f"item{index:03d}", "title": f"Job {index}", "company": "Example Corp", "description": "Text."}
                for index in range(self.total)]
        (self.batch / "blind.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        (self.batch / "jobs.jsonl").write_text("".join(json.dumps({"item_id": row["item_id"], "job": {"title": row["title"], "company": "Example Corp"},
                                                                   "surfaced_by": [{"search": "A", "rank": 1, "stage": "post_filter"}]}) + "\n"
                                                       for row in rows), encoding="utf-8")
        (self.batch / "meta.json").write_text(json.dumps({"batch_id": "test-batch", "rubric_version": "r1", "frozen": {"stamp": "S"}}),
                                              encoding="utf-8")
        self.labels = self.root / "labels"
        self.clock = FakeClock()
        self.session = LabelSession(self.batch, self.labels, clock=self.clock)

    def label_all(self, grades, seconds):
        """Show each job, wait `seconds`, then label it."""
        for grade, wait in zip(grades, seconds, strict=True):
            item = self.session.state()["item"]
            self.clock.now += wait
            self.session.label(item["item_id"], grade)

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in (self.labels / "test-batch.jsonl").read_text(encoding="utf-8").splitlines()]


class TimingTests(QualityCase):
    def test_the_seconds_between_showing_a_job_and_labelling_it_are_recorded(self):
        self.label_all([3, 0], [12.5, 3])
        self.assertEqual([line["seconds_on_job"] for line in self.lines()], [12.5, 3.0])

    def test_a_label_for_a_job_the_server_never_showed_is_untimed(self):
        self.session.label("item000", 2)                 # for example after a server restart, before the page reloaded
        self.assertIsNone(self.lines()[0]["seconds_on_job"])
        self.assertEqual(label_quality(self.lines(), self.total)["fast"], 1)       # untimed counts as quick

    def test_going_back_keeps_the_longest_look_at_a_job(self):
        self.label_all([3], [40])
        self.session.state(at=0)
        self.clock.now += 2
        self.session.label("item000", 2)
        quality = label_quality(self.lines(), self.total)
        self.assertEqual((quality["labelled"], quality["fast"]), (1, 0))


class RuleTests(QualityCase):
    def test_careful_varied_labels_are_accepted(self):
        self.label_all([3, 0, 2, 1, 2], [30, 20, 45, 9, 8])
        quality = label_quality(self.lines(), self.total)
        self.assertEqual((quality["complete"], quality["accepted"], quality["fast"], quality["problems"]), (True, True, 0, []))
        self.assertEqual(FAST_SECONDS, 8)

    def test_an_unfinished_file_is_not_complete(self):
        self.label_all([3, 0], [30, 30])
        quality = label_quality(self.lines(), self.total)
        self.assertEqual((quality["complete"], quality["accepted"]), (False, False))
        self.assertIn("3 of 5 jobs have no label", quality["problems"][0])

    def test_too_many_quick_labels_are_refused_and_said_plainly(self):
        self.label_all([3, 0, 2, 1, 2], [30, 20, 45, 9, 7.9])
        quality = label_quality(self.lines(), self.total)
        self.assertEqual((quality["complete"], quality["accepted"], quality["fast"]), (True, False, 1))
        self.assertIn("1 of 5 jobs were labelled in under 8 seconds", quality["problems"][0])
        self.assertIn("NOT complete", quality["message"])

    def test_more_than_half_at_one_grade_is_refused(self):
        self.label_all([3, 3, 3, 1, 2], [30, 30, 30, 30, 30])
        quality = label_quality(self.lines(), self.total)
        self.assertFalse(quality["accepted"])
        self.assertIn("3 of 5 labels are grade 3, more than half", quality["problems"][0])

    def test_the_page_says_so_at_the_end(self):
        self.label_all([3, 3, 3, 3, 3], [1, 1, 1, 1, 1])
        state = json.loads(handle(self.session, "GET", "/api/state", HOST, b"", port=8765).body)
        self.assertEqual((state["done"], state["quality"]["accepted"]), (True, False))
        self.assertIn("NOT complete", state["quality"]["message"])
        self.assertIn("quality.message", handle(self.session, "GET", "/", HOST, b"", port=8765).body.decode("utf-8"))


class LimitOf106Tests(QualityCase):
    total = 106

    def test_ten_quick_labels_in_106_pass_and_eleven_do_not(self):
        grades = [index % 4 for index in range(106)]
        self.label_all(grades, [5] * 10 + [20] * 96)
        self.assertTrue(label_quality(self.lines(), 106)["accepted"])
        self.session.state(at=10)
        self.clock.now += 1
        self.session.label("item011", grades[11])             # item011 was slow; its longest look still counts
        self.assertTrue(label_quality(self.lines(), 106)["accepted"])

    def test_eleven_quick_labels_in_106_are_refused(self):
        self.label_all([index % 4 for index in range(106)], [5] * 11 + [20] * 95)
        quality = label_quality(self.lines(), 106)
        self.assertEqual((quality["accepted"], quality["fast"], quality["fast_limit"]), (False, 11, 10))

    def test_the_first_run_would_have_been_refused_on_both_rules(self):
        # 106 labels in 339 seconds with 64 at grade 3, as recorded in docs/eval/gold_labels.md.
        self.label_all([3] * 64 + [2] * 22 + [1] * 12 + [0] * 8, [3.2] * 106)
        problems = " ".join(label_quality(self.lines(), 106)["problems"])
        self.assertIn("106 of 106 jobs were labelled in under 8 seconds", problems)
        self.assertIn("64 of 106 labels are grade 3, more than half", problems)


class ShapeTests(QualityCase):
    """Run 2 failed the same way as run 1. The page now shows the grade counts after every 20 labels,
    so a lopsided shape is visible while there is still time to slow down."""
    total = 45

    def test_the_counts_per_grade_appear_after_every_twenty_labels(self):
        grades = [index % 4 for index in range(45)]
        shapes = []
        for grade in grades:
            item = self.session.state()["item"]
            self.clock.now += 20
            shapes.append(self.session.label(item["item_id"], grade)["shape"])
        self.assertEqual([index + 1 for index, shape in enumerate(shapes) if shape], [20, 40, 45])     # and at the end
        self.assertEqual(shapes[19], "After 20 labels: 5 at grade 0, 5 at grade 1, 5 at grade 2, 5 at grade 3.")
        self.assertEqual(shapes[39], "After 40 labels: 10 at grade 0, 10 at grade 1, 10 at grade 2, 10 at grade 3.")
        self.assertEqual(self.session.state()["grade_counts"], {"0": 12, "1": 11, "2": 11, "3": 11})

    def test_a_changed_label_is_counted_once_at_its_new_grade(self):
        self.label_all([3] * 20, [20] * 20)
        self.session.state(at=0)
        self.clock.now += 20
        state = self.session.label("item000", 0)
        self.assertEqual(state["shape"], "After 20 labels: 1 at grade 0, 0 at grade 1, 0 at grade 2, 19 at grade 3.")

    def test_the_page_has_a_line_for_it_that_stays_until_the_next_one(self):
        page = handle(self.session, "GET", "/", HOST, b"", port=8765).body.decode("utf-8")
        self.assertIn('id="shape"', page)
        self.assertIn("if (next.shape)", page)          # only replaced when a new one arrives


class FlaggedTests(QualityCase):
    """Run 3 had 23 jobs under 8 seconds. The labeller re-reads exactly those: --check lists them, the page opens one
    by number (?job=14) and N goes to the next flagged job. Nothing here changes a label."""

    def test_the_quick_jobs_are_listed_by_job_number_with_title_company_seconds_and_grade(self):
        self.label_all([3, 0, 2, 1, 2], [30, 3, 45, 1.5, 9])
        self.assertEqual(self.session.flagged(), [
            {"job": 2, "item_id": "item001", "title": "Job 1", "company": "Example Corp", "seconds": 3.0, "grade": 0},
            {"job": 4, "item_id": "item003", "title": "Job 3", "company": "Example Corp", "seconds": 1.5, "grade": 1}])

    def test_a_longer_second_look_clears_the_flag_and_an_untimed_label_is_flagged(self):
        self.label_all([3, 0], [30, 3])
        self.session.state(at=1)
        self.clock.now += 20
        self.session.label("item001", 1)
        self.assertEqual(self.session.flagged(), [])
        self.session.label("item004", 2)                   # never shown by the server: no timing
        self.assertEqual([(job["job"], job["seconds"]) for job in self.session.flagged()], [(5, None)])

    def test_the_state_names_the_next_flagged_job_after_the_current_one(self):
        self.label_all([3, 0, 2, 1, 2], [3, 30, 4, 30, 5])     # jobs 1, 3 and 5 are quick
        self.assertEqual(self.session.state(at=0)["flagged_jobs"], [1, 3, 5])
        self.assertEqual(self.session.state(at=0)["next_flagged"], 3)
        self.assertEqual(self.session.state(at=2)["next_flagged"], 5)
        self.assertEqual(self.session.state(at=4)["next_flagged"], 1)        # after the last one, back to the first
        self.assertEqual(self.session.state()["next_flagged"], 1)            # the batch is done: from the start

    def test_with_nothing_flagged_there_is_no_next(self):
        self.label_all([3, 0], [30, 30])
        state = self.session.state(at=0)
        self.assertEqual((state["flagged_jobs"], state["next_flagged"]), ([], None))

    def test_opening_a_job_by_number_and_the_n_key_are_on_the_page(self):
        page = handle(self.session, "GET", "/", HOST, b"", port=8765).body.decode("utf-8")
        self.assertIn('new URLSearchParams(location.search).get("job")', page)
        self.assertIn('event.key === "n" || event.key === "N"', page)
        self.assertIn("next.next_flagged", page)
        self.assertIn("N for the next quick job", page)
        # ?job=14 is answered by the existing ?at=13: the page asks for job number minus one.
        self.label_all([3, 0, 2, 1, 2], [30, 30, 30, 30, 30])
        state = json.loads(handle(self.session, "GET", "/api/state?at=3", HOST, b"", port=8765).body)
        self.assertEqual((state["position"], state["item"]["item_id"]), ("4 of 5", "item003"))

    def test_nothing_is_written_by_looking(self):
        self.label_all([3, 0, 2, 1, 2], [30, 3, 45, 1.5, 9])
        before = (self.labels / "test-batch.jsonl").read_bytes()
        for at in range(5):
            self.session.state(at=at)
        self.session.flagged()
        self.assertEqual((self.labels / "test-batch.jsonl").read_bytes(), before)


class ScriptTests(QualityCase):
    def test_check_lists_the_quick_jobs_sorted_by_job_number(self):
        page = self.load("label_page")
        self.label_all([3, 0, 2, 1, 2], [2, 30, 7.9, 30, 1])
        code, output = self.run_script(page, ["--batch", str(self.batch), "--labels", str(self.labels), "--check"])
        self.assertEqual(code, 1)
        lines = output.splitlines()
        start = lines.index("Labelled in under 8 seconds (3): job, title, company, seconds, grade")
        self.assertEqual(lines[start + 1:start + 4], ["  job 1: Job 0, Example Corp, 2.0 s, grade 3",
                                                      "  job 3: Job 2, Example Corp, 7.9 s, grade 2",
                                                      "  job 5: Job 4, Example Corp, 1.0 s, grade 2"])
        self.assertIn("http://127.0.0.1:8765/?job=1", output)


    def load(self, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_script(self, module, arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = module.main(arguments)
        return code, output.getvalue()

    def test_check_prints_the_count_of_quick_labels_and_fails_a_bad_file(self):
        page = self.load("label_page")
        self.label_all([3, 3, 3, 3, 3], [1, 1, 1, 20, 20])
        code, output = self.run_script(page, ["--batch", str(self.batch), "--labels", str(self.labels), "--check"])
        self.assertEqual(code, 1)
        self.assertIn("3 of 5 labelled in under 8 seconds", output)
        self.assertIn("NOT complete", output)

    def test_check_passes_a_careful_file(self):
        page = self.load("label_page")
        self.label_all([3, 0, 2, 1, 2], [30, 20, 45, 9, 8])
        code, output = self.run_script(page, ["--batch", str(self.batch), "--labels", str(self.labels), "--check"])
        self.assertEqual(code, 0)
        self.assertIn("0 of 5 labelled in under 8 seconds", output)
        self.assertIn("Grades so far: 1 at grade 0, 1 at grade 1, 2 at grade 2, 1 at grade 3.", output)

    def test_the_agreement_report_describes_the_gold_file_as_it_is(self):
        # Found on run 3: the report said "no label changed afterwards" for a file with 25 re-reads, and gave the
        # wall-clock span across several sittings as if it were labelling time.
        hosted = self.load("hosted_labels")
        self.label_all([3, 0, 2, 1, 2], [30, 20, 45, 9, 8])
        self.session.state(at=4)
        self.clock.now += 12
        self.session.label("item004", 1)
        rows = [{"item_id": f"item{index:03d}", "label": grade, "order": order, "reason": "r", "labeller": "subagent:test"}
                for order in ("forward", "reverse") for index, grade in enumerate([3, 0, 2, 1, 1])]
        (self.labels / "test-batch.hosted.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        out = self.root / "report.md"
        with patch.object(hosted, "EVAL", self.root):
            code, _ = self.run_script(hosted, ["report", "--batch", str(self.batch), "--out", str(out)])
        report = out.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertNotIn("no label changed", report)
        self.assertIn("6 label lines for 5 jobs: 1 job was re-read and graded again", report)
        self.assertIn("median 20.0 seconds per job (the longest look at each)", report)
        self.assertIn("0 of 5 under 8 seconds", report)

    def test_the_agreement_report_refuses_gold_labels_that_fail_the_guard(self):
        hosted = self.load("hosted_labels")
        self.label_all([3, 3, 3, 3, 3], [1, 1, 1, 1, 1])
        (self.labels / "test-batch.hosted.jsonl").write_text("", encoding="utf-8")
        out = self.root / "report.md"
        with patch.object(hosted, "EVAL", self.root):
            code, output = self.run_script(hosted, ["report", "--batch", str(self.batch), "--out", str(out)])
        self.assertEqual(code, 1)
        self.assertIn("NOT complete", output)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
