"""SEM2: the experiment script end to end on a fictional batch, with a stub embedder instead of the model."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TARGET = "# Target profile\n\nroles: AI Engineer\ngraduation year: 2025\nexperience years: 0\nskills: Python, SQL\n\nCGPA 7.64. Python and SQL.\n"
JOBS = [("a1", "A", "post_filter", 1, "Graduate AI Engineer", "Freshers welcome. Python and SQL."),
        ("a2", "A", "post_filter", 2, "Staff AI Engineer", "Requires 8+ years of experience. Python."),
        ("a3", "A", "excluded_sample", None, "AI Engineer", "Python and SQL for a fresher."),
        ("b1", "B", "post_filter", 1, "Sales Executive", "Sell software."),
        ("b2", "B", "post_filter", 2, "Software Engineer", "Python backend, entry-level."),
        ("b3", "B", "excluded_sample", None, "Data Engineer", "SQL pipelines, 1-3 years of experience.")]


class Stub:
    model = "stub-model"

    def query(self, text):
        return np.array([1.0, 1.0])

    def passages(self, texts):
        return np.array([[1.0, 1.0] if "Python" in text else [1.0, -1.0] for text in texts])


class ExperimentScriptTests(unittest.TestCase):
    def test_the_report_has_every_ranker_both_label_sets_the_verdict_and_the_in_sample_warning(self):
        spec = importlib.util.spec_from_file_location("sem2_experiment", ROOT / "scripts" / "sem2_experiment.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch, labels = root / "batches" / "b1", root / "labels"
            batch.mkdir(parents=True)
            labels.mkdir()
            (batch / "jobs.jsonl").write_text("".join(json.dumps({
                "item_id": item, "total_score": 50 - index,
                "surfaced_by": [{"search": search, "rank": rank, "stage": stage}],
                "job": {"company": "Example Corp", "title": title, "location": "Pune, India", "description": text}}) + "\n"
                for index, (item, search, stage, rank, title, text) in enumerate(JOBS)), encoding="utf-8")
            gold = {"a1": 3, "a2": 1, "a3": 2, "b1": 0, "b2": 3, "b3": 2}
            hosted = {"a1": 2, "a2": 0, "a3": 2, "b1": 0, "b2": 2, "b3": 1}
            (labels / "b1.jsonl").write_text("".join(json.dumps({"item_id": item, "label": grade, "seconds_on_job": 20.0}) + "\n"
                                                     for item, grade in gold.items()), encoding="utf-8")
            (labels / "b1.hosted.jsonl").write_text("".join(json.dumps({"item_id": item, "label": grade, "order": "forward"}) + "\n"
                                                            for item, grade in hosted.items()), encoding="utf-8")
            target = root / "target_profile.md"
            target.write_text(TARGET, encoding="utf-8")
            out = root / "sem2.md"
            output = io.StringIO()
            with patch.object(script, "EVAL", root), contextlib.redirect_stdout(output):
                code = script.main(["--batch", str(batch), "--labels", str(labels), "--target", str(target), "--out", str(out)],
                                   embedder=Stub())
            self.assertEqual(code, 0)
            report = out.read_text(encoding="utf-8")
            for heading in ("## NDCG@10, gold labels", "## P@5, gold labels", "## NDCG@10, hosted labels", "## P@5, hosted labels"):
                self.assertIn(heading, report)
            for name in ("| v1 |", "| BM25 |", "| dense |", "| hybrid |", "| hybrid x eligibility |"):
                self.assertEqual(report.count(name), 4, name)
            self.assertIn("**The eligibility component is in-sample.**", report)
            self.assertTrue("ships:" in report or "v1 stays." in report)
            self.assertIn("## What a better ranker cannot fix", report)
            self.assertTrue((root / "vectors" / "b1.npz").exists())
            self.assertIn("report:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
