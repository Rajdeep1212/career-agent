"""Alert ingestion from the .eml dropbox: dedup, re-run safety, and matching to official Radar jobs."""
import gc
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.sources.adapters import posting
from app.sources.alerts import ingest
from app.storage import alert_store, db, radar_store
from radar_helpers import company

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "alerts"


class DropboxIngestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        self.dropbox = self.root / "alert_dropbox"
        self.dropbox.mkdir()
        for target, name, value in ((alert_store, "DB_PATH", self.root / "alerts.sqlite3"),
                                    (radar_store, "DB_PATH", self.root / "radar.sqlite3"),
                                    (settings, "alerts_dropbox_dir", str(self.dropbox))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    def _drop(self, *names):
        for name in names:
            shutil.copy(FIXTURES / name, self.dropbox / name)

    def test_dropped_files_are_parsed_stored_and_moved(self):
        self._drop("linkedin_alert.eml", "naukri_alert.eml", "indeed_alert.eml")
        outcome = ingest.ingest_dropbox()
        self.assertEqual((outcome.messages, outcome.parsed_jobs, outcome.new, outcome.duplicates), (3, 7, 7, 0))
        self.assertEqual(sorted(p.name for p in (self.dropbox / "processed").iterdir()),
                         ["indeed_alert.eml", "linkedin_alert.eml", "naukri_alert.eml"])
        self.assertEqual(list(self.dropbox.glob("*.eml")), [])
        self.assertEqual({row["source"] for row in alert_store.rows()}, {"LinkedIn alert", "Naukri alert", "Indeed alert"})

    def test_rerun_and_redropped_files_are_not_processed_twice(self):
        self._drop("linkedin_alert.eml")
        ingest.ingest_dropbox()
        self.assertEqual(ingest.ingest_dropbox().messages, 0)
        self._drop("linkedin_alert.eml")
        again = ingest.ingest_dropbox()
        self.assertEqual((again.messages, again.already_processed, again.new), (0, 1, 0))
        self.assertEqual(len(alert_store.list_jobs()), 3)

    def test_the_same_job_from_two_platforms_is_stored_once(self):
        self._drop("linkedin_alert.eml")
        ingest.ingest_dropbox()
        copy = (FIXTURES / "linkedin_alert.eml").read_bytes().replace(b"<synthetic-linkedin-0001@example.com>", b"<other@example.com>")
        copy = copy.replace(b"4012345678", b"4088888888")   # same job, another LinkedIn id (e.g. a repost)
        outcome = ingest.ingest_raw([("dropbox", copy)])
        self.assertEqual(outcome.duplicates, 3)
        stored = next(job for job in alert_store.list_jobs() if job.title == "Machine Learning Engineer")
        self.assertEqual([ref.source_job_id for ref in stored.sources], ["linkedin:4088888888"])

    def test_jobs_matching_an_official_radar_job_are_linked_to_it(self):
        entry = company({"type": "greenhouse", "board": "example"}, id="example-analytics", name="Example Analytics")
        radar_store.record_listing(entry.id, [posting(entry, job_id="7001", title="Machine Learning Engineer",
                                                      location="Bengaluru, Karnataka, India", description="Freshers welcome.",
                                                      url="https://job-boards.greenhouse.io/example/jobs/7001")],
                                   complete=True, today=date(2026, 9, 25))
        self._drop("linkedin_alert.eml")
        outcome = ingest.ingest_dropbox()
        self.assertEqual(outcome.matched_official, 1)
        self.assertEqual([row["source"] for row in radar_store.job_sources("example-analytics:7001")], ["LinkedIn alert"])
        linked = [row for row in alert_store.rows() if row["radar_key"]]
        self.assertEqual([row["key"] for row in linked], ["linkedin:4012345678"])

    def test_dry_run_saves_and_moves_nothing(self):
        self._drop("indeed_alert.eml")
        with patch("builtins.print"):
            outcome = ingest.ingest_dropbox(dry_run=True)
        self.assertEqual((outcome.messages, outcome.parsed_jobs), (1, 2))
        self.assertTrue((self.dropbox / "indeed_alert.eml").exists())
        self.assertFalse(alert_store.DB_PATH.exists())

    def test_non_alert_and_broken_files_do_not_stop_the_run(self):
        (self.dropbox / "a_note.eml").write_bytes(b"From: me@example.com\r\nSubject: hi\r\n\r\nhello")
        self._drop("naukri_alert.eml")
        outcome = ingest.ingest_dropbox()
        self.assertEqual((outcome.messages, outcome.new), (2, 2))


if __name__ == "__main__":
    unittest.main()
