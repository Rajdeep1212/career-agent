"""Q3: the local labelling page. Blind, keyboard 0-3, resumable, append-only labels, nothing loaded from the network.
Five fictional jobs in a temporary batch; no socket is opened."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from app.eval.label_page import BLIND_FIELDS, RUBRIC, LabelSession, handle

ROOT = Path(__file__).resolve().parents[1]
HOST = {"Host": "127.0.0.1:8765"}
JSON = {**HOST, "Content-Type": "application/json"}


def blind_line(index: int) -> dict:
    return {"item_id": f"item{index:02d}", "title": f"Graduate Engineer {index}", "company": "Example Corp", "location": "Pune, India",
            "work_mode": "hybrid", "employment_type": "Full-time", "posted_date": "2026-09-20", "salary": None,
            "description": f"Freshers welcome <b>bold</b>. Python and SQL. Job number {index}.",
            "application_url": f"https://job-boards.greenhouse.io/b/jobs/{index}",
            # Fields that must never reach the labeller, even if a batch file carried them:
            "total_score": 91, "eligibility_status": "eligible", "surfaced_by": [{"search": "A"}], "official_application": True}


class LabelCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.batch = self.root / "batches" / "test-batch"
        self.batch.mkdir(parents=True)
        (self.batch / "blind.jsonl").write_text("".join(json.dumps(blind_line(index)) + "\n" for index in range(5)), encoding="utf-8")
        (self.batch / "meta.json").write_text(json.dumps({"batch_id": "test-batch", "rubric_version": "r1", "frozen": {"stamp": "S"},
                                                          "files": {"blind.jsonl": "ab" * 32}}), encoding="utf-8")
        self.labels = self.root / "labels"

    def session(self) -> LabelSession:
        return LabelSession(self.batch, self.labels)

    def get(self, path, session=None):
        return handle(session or self.session(), "GET", path, HOST, b"", port=8765)

    def post(self, body, session=None, headers=JSON):
        return handle(session or self.session(), "POST", "/api/label", headers, json.dumps(body).encode(), port=8765)

    def state(self, path="/api/state", session=None) -> dict:
        response = self.get(path, session)
        self.assertEqual(response.status, 200)
        return json.loads(response.body)

    def lines(self) -> list[dict]:
        path = self.labels / "test-batch.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class StateTests(LabelCase):
    def test_the_first_job_is_shown_blind_with_its_position(self):
        state = self.state()
        self.assertEqual((state["total"], state["labelled"], state["index"], state["position"], state["done"]), (5, 0, 0, "1 of 5", False))
        self.assertEqual(set(state["item"]), {"item_id", *BLIND_FIELDS})
        self.assertEqual(set(BLIND_FIELDS), {"title", "company", "location", "work_mode", "employment_type", "posted_date", "salary",
                                             "description", "application_url"})
        text = json.dumps(state).casefold()
        for hidden in ("total_score", "eligibility", "surfaced_by", "official_application", "search", "fit"):
            self.assertNotIn(hidden, text)
        self.assertIsNone(state["current_label"])

    def test_the_rubric_is_r1_word_for_word_with_one_line_per_grade(self):
        plan = (ROOT / "docs" / "M2_PLAN.md").read_text(encoding="utf-8")
        self.assertEqual((RUBRIC["version"], sorted(RUBRIC["grades"])), ("r1", ["0", "1", "2", "3"]))
        for grade, meaning in RUBRIC["grades"].items():
            self.assertIn(f"| {grade} | {meaning} |", plan)
        self.assertEqual(self.state()["rubric"], RUBRIC)

    def test_a_batch_with_another_rubric_version_is_refused(self):
        (self.batch / "meta.json").write_text(json.dumps({"batch_id": "test-batch", "rubric_version": "r2"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.session()


class LabelTests(LabelCase):
    def test_five_jobs_are_labelled_and_the_labels_survive_a_refresh(self):
        session = self.session()
        for index, grade in enumerate([3, 0, 2, 1, 3]):
            current = self.state(session=session)
            self.assertEqual((current["index"], current["position"]), (index, f"{index + 1} of 5"))
            response = self.post({"item_id": current["item"]["item_id"], "grade": grade}, session)
            self.assertEqual(response.status, 200)
        after = self.state()                                   # a new session: the page was refreshed or the server restarted
        self.assertEqual((after["labelled"], after["done"], after["item"], after["position"]), (5, True, None, "5 of 5"))
        self.assertEqual([(line["item_id"], line["label"]) for line in self.lines()],
                         [("item00", 3), ("item01", 0), ("item02", 2), ("item03", 1), ("item04", 3)])
        first = self.lines()[0]
        self.assertEqual({key: first[key] for key in ("batch_id", "scale", "rubric_version", "labeller", "set", "relabel_of", "snapshot_stamp")},
                         {"batch_id": "test-batch", "scale": "graded_0_3", "rubric_version": "r1", "labeller": "owner", "set": "gold",
                          "relabel_of": None, "snapshot_stamp": "S"})
        self.assertRegex(first["labelled_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertEqual(set(first), {"batch_id", "item_id", "label", "scale", "rubric_version", "labeller", "set", "labelled_at",
                                      "relabel_of", "snapshot_stamp", "blind_sha256", "seconds_on_job"})

    def test_a_half_done_batch_resumes_at_the_first_unlabelled_job(self):
        self.post({"item_id": "item00", "grade": 2})
        self.post({"item_id": "item01", "grade": 1})
        resumed = self.state()
        self.assertEqual((resumed["labelled"], resumed["index"], resumed["item"]["item_id"], resumed["position"]), (2, 2, "item02", "3 of 5"))

    def test_going_back_one_job_shows_its_label_and_a_new_grade_is_appended_not_overwritten(self):
        self.post({"item_id": "item00", "grade": 2})
        self.post({"item_id": "item01", "grade": 1})
        back = self.state("/api/state?at=1")
        self.assertEqual((back["item"]["item_id"], back["current_label"], back["position"]), ("item01", 1, "2 of 5"))
        response = self.post({"item_id": "item01", "grade": 3})
        following = json.loads(response.body)
        self.assertEqual((following["index"], following["labelled"]), (2, 2))          # on to the first unlabelled job
        lines = self.lines()
        self.assertEqual([(line["item_id"], line["label"], line["relabel_of"]) for line in lines],
                         [("item00", 2, None), ("item01", 1, None), ("item01", 3, 2)])     # relabel_of: the line it replaces
        self.assertEqual(self.state("/api/state?at=1")["current_label"], 3)
        self.assertEqual(self.state("/api/state?at=99")["index"], 2)                       # out of range: the first unlabelled

    def test_bad_input_writes_nothing(self):
        for body in ({"item_id": "item00", "grade": 4}, {"item_id": "item00", "grade": -1}, {"item_id": "item00", "grade": "2"},
                     {"item_id": "item00", "grade": True}, {"item_id": "nope", "grade": 2}, {"grade": 2}, ["item00", 2]):
            self.assertEqual(self.post(body).status, 400, body)
        self.assertEqual(handle(self.session(), "POST", "/api/label", JSON, b"not json", port=8765).status, 400)
        self.assertEqual(self.post({"item_id": "item00", "grade": 2}, headers=HOST).status, 415)        # a form post from another page
        self.assertEqual(self.lines(), [])
        self.assertFalse(self.labels.exists())


class PageTests(LabelCase):
    def test_the_page_is_one_local_document_with_nothing_from_the_network(self):
        response = self.get("/")
        page = response.body.decode("utf-8")
        self.assertEqual((response.status, response.headers["Content-Type"]), (200, "text/html; charset=utf-8"))
        self.assertEqual(re.findall(r"https?://", page), [])
        for external in ("<link", "@import", "src=", "url(", "fonts.googleapis", "cdn"):
            self.assertNotIn(external, page.casefold(), external)
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_the_page_shows_the_rubric_the_keys_and_never_builds_html_from_listing_text(self):
        page = self.get("/").body.decode("utf-8")
        for grade, meaning in RUBRIC["grades"].items():
            self.assertIn(meaning, page)
        for needed in ("Would I apply?", "ArrowLeft", "textContent"):
            self.assertIn(needed, page)
        self.assertNotIn("innerHTML", page)
        self.assertNotIn("Example Corp", page)             # jobs arrive through /api/state, not baked into the page

    def test_only_this_machine_and_only_known_paths(self):
        self.assertEqual(handle(self.session(), "GET", "/api/state", {"Host": "evil.example"}, b"", port=8765).status, 403)
        self.assertEqual(handle(self.session(), "GET", "/api/state", {"Host": "localhost:8765"}, b"", port=8765).status, 200)
        self.assertEqual(self.get("/api/jobs").status, 404)
        self.assertEqual(handle(self.session(), "DELETE", "/api/label", JSON, b"", port=8765).status, 405)
        script = (ROOT / "scripts" / "label_page.py").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1"', script)
        self.assertNotIn("0.0.0.0", script)


if __name__ == "__main__":
    unittest.main()
