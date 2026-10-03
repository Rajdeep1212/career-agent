"""The apply list: every job the owner graded 2 or 3 in a gold batch, with the hosted grade and reason beside it.
Local files only; fictional jobs."""
import contextlib
import csv
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("export_apply_list", ROOT / "scripts" / "export_apply_list.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApplyListTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.batch = self.root / "batches" / "b1"
        self.batch.mkdir(parents=True)
        jobs = [("i1", "Graduate AI Engineer", "2026-09-20"), ("i2", "Data Analyst", "2026-09-25"), ("i3", "Sales Lead", "2026-09-26"),
                ("i4", "Backend Engineer", "2026-09-10"), ("i5", "ML Engineer", "2026-09-28")]
        (self.batch / "jobs.jsonl").write_text("".join(json.dumps({"item_id": item, "job": {
            "title": title, "company": "Example Corp", "location": "Pune, India", "posted_date": posted,
            "application_url": f"https://example.com/{item}", "description": "secret listing text"}}) + "\n" for item, title, posted in jobs),
            encoding="utf-8")
        labels = self.root / "labels"
        labels.mkdir()
        gold = [("i1", 3), ("i2", 2), ("i3", 0), ("i4", 3), ("i5", 3), ("i2", 1), ("i2", 2)]       # i2 relabelled: the latest line counts
        (labels / "b1.jsonl").write_text("".join(json.dumps({"item_id": item, "label": grade}) + "\n" for item, grade in gold),
                                         encoding="utf-8")
        hosted = [("i1", 1, "forward", "Asks for 1-3 years."), ("i1", 0, "reverse", "Other order."), ("i2", 2, "forward", "Plausible."),
                  ("i3", 0, "forward", "Sales."), ("i4", 2, "forward", "Python backend."), ("i5", 2, "forward", "Entry-level ML.")]
        (labels / "b1.hosted.jsonl").write_text("".join(json.dumps({"item_id": item, "label": grade, "order": order, "reason": reason}) + "\n"
                                                        for item, grade, order, reason in hosted), encoding="utf-8")
        self.labels = labels

    def test_the_list_holds_grades_2_and_3_sorted_by_my_grade_then_hosted_grade_then_newest(self):
        out = self.root / "apply.csv"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = load().main(["--batch", str(self.batch), "--labels", str(self.labels), "--out", str(out)])
        self.assertEqual(code, 0)
        with out.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(list(rows[0]), ["my_grade", "hosted_grade", "hosted_reason", "title", "company", "location", "link", "posted_date"])
        self.assertEqual([(row["title"], row["my_grade"], row["hosted_grade"]) for row in rows],
                         [("ML Engineer", "3", "2"), ("Backend Engineer", "3", "2"), ("Graduate AI Engineer", "3", "1"),
                          ("Data Analyst", "2", "2")])
        self.assertEqual(rows[2]["hosted_reason"], "Asks for 1-3 years.")        # the batch-order label and its reason
        self.assertNotIn("secret listing text", out.read_text(encoding="utf-8"))
        printed = output.getvalue()
        self.assertIn("| ML Engineer | Example Corp | Pune, India | 2026-09-28 | 2 |", printed)
        self.assertNotIn("Data Analyst", printed)                                  # only grade 3 is printed
        self.assertIn("4 jobs written", printed)


if __name__ == "__main__":
    unittest.main()
