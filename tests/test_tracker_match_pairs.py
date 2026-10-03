"""TRK2: the quick-add match evaluation (scripts/tracker_match_pairs.py). A fictional index in a temporary directory."""
import csv
import gc
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import JobPosting
from app.storage import radar_store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tracker_match_pairs as pairs  # noqa: E402

TITLES = ("Machine Learning Engineer", "Machine Learning Engineer II", "Data Engineer", "Senior Data Engineer", "Backend Engineer",
          "Backend Engineer - Payments", "Graduate Software Engineer", "Software Engineer", "Product Analyst", "Data Analyst")
CITIES = ("Bengaluru, India", "Pune, India", "Hyderabad, India")


class PairsCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        patcher = patch.object(radar_store, "DB_PATH", self.root / "radar.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = self.root / "tracker_pairs" / "pairs.csv"

    def fill(self, companies=6):
        for c in range(companies):
            jobs = [JobPosting(company=f"Company {c}", title=title, location=CITIES[(c + n + round_) % 3], description="x",
                               application_url=f"https://jobs.example.com/c{c}/{round_}{n}", source="Company Radar", source_job_id=f"{round_}{n}")
                    for round_ in range(2) for n, title in enumerate(TITLES)]
            radar_store.record_listing(f"c{c}", jobs, complete=True, today=date(2026, 10, 3))

    def rows(self, path=None):
        with open(path or self.out, encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))


class BuildTests(PairsCase):
    def test_build_writes_50_blind_pairs_of_both_kinds_and_is_repeatable(self):
        self.fill()
        report = pairs.build(self.out)
        rows = self.rows()
        self.assertEqual((report["pairs"], len(rows)), (50, 50))
        self.assertEqual(list(rows[0]), pairs.COLUMNS)
        self.assertTrue(all(row["label"] == "" for row in rows))
        self.assertNotIn("kind", rows[0])                                   # the sampler's guess is kept out of the labelling file
        self.assertTrue(all(row["a_job_id"] != row["b_job_id"] for row in rows))
        self.assertEqual(len({frozenset((row["a_job_id"], row["b_job_id"])) for row in rows}), 50)
        key = self.rows(self.out.with_name("pairs_key.csv"))
        self.assertEqual({row["kind"] for row in key}, {"likely", "near_miss"})
        self.assertEqual(report["likely"] + report["near_miss"], 50)
        first = self.out.read_bytes()
        self.out.unlink()
        pairs.build(self.out)
        self.assertEqual(self.out.read_bytes(), first)                      # seed 20261003

    def test_a_small_index_gives_fewer_pairs_and_none_are_invented(self):
        radar_store.record_listing("c0", [
            JobPosting(company="Company 0", title=title, location="Pune, India", description="x", source_job_id=str(n),
                       application_url=f"https://jobs.example.com/{n}", source="Company Radar")
            for n, title in enumerate(("Data Engineer", "Data Engineer II", "Chef"))], complete=True, today=date(2026, 10, 3))
        self.assertEqual(pairs.build(self.out)["pairs"], 1)

    def test_a_file_that_holds_labels_is_never_overwritten(self):
        self.fill()
        pairs.build(self.out)
        rows = self.rows()
        rows[0]["label"] = "same"
        with open(self.out, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=pairs.COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        before = self.out.read_bytes()
        with self.assertRaises(SystemExit):
            pairs.build(self.out)
        self.assertEqual(self.out.read_bytes(), before)


class ScoreTests(PairsCase):
    def write(self, rows):
        self.out.parent.mkdir(parents=True, exist_ok=True)
        with open(self.out, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=pairs.COLUMNS)
            writer.writeheader()
            for n, (a_title, b_title, a_company, b_company, label) in enumerate(rows):
                writer.writerow({"pair_id": n + 1, "a_job_id": f"a{n}", "a_company": a_company, "a_title": a_title, "a_location": "Pune",
                                 "a_url": f"https://jobs.example.com/a{n}", "b_job_id": f"b{n}", "b_company": b_company, "b_title": b_title,
                                 "b_location": "Pune", "b_url": f"https://jobs.example.com/b{n}", "label": label})

    def test_unlabelled_pairs_are_reported_as_ready_and_no_number_is_made_up(self):
        self.assertEqual(pairs.score(self.out)["message"], "pairs not built")
        self.fill()
        pairs.build(self.out)
        report = pairs.score(self.out)
        self.assertEqual(report["message"], "pairs ready, not labelled (0 of 50 labelled)")
        self.assertNotIn("precision", report)

    def test_precision_and_recall_come_from_the_labels(self):
        self.write([
            ("Data Engineer", "Data Engineer", "Acme", "Acme Pvt. Ltd.", "same"),          # predicted, same: true positive
            ("Data Engineer", "Data Engineer", "Acme", "Acme", "different"),               # predicted, different: false positive
            ("ML Engineer", "Machine Learning Engineer (Platform)", "Acme", "Acme", "same"),   # not predicted, same: false negative
            ("Chef", "Data Engineer", "Acme", "Acme", "different"),                        # not predicted, different: true negative
            ("Data Analyst", "Data Analyst", "Globex", "Globex", "SAME"),                  # true positive
        ])
        report = pairs.score(self.out)
        self.assertEqual((report["labelled"], report["true_positive"], report["false_positive"], report["false_negative"]), (5, 2, 1, 1))
        self.assertAlmostEqual(report["precision"], 2 / 3)
        self.assertAlmostEqual(report["recall"], 2 / 3)
        self.assertIn("precision 0.667 (2/3)", report["message"])
        self.assertIn("recall 0.667 (2/3)", report["message"])

    def test_an_unknown_label_stops_the_scorer(self):
        self.write([("Data Engineer", "Data Engineer", "Acme", "Acme", "maybe")])
        with self.assertRaises(SystemExit):
            pairs.score(self.out)


if __name__ == "__main__":
    unittest.main()
