"""radar_store: listing status transitions, run logs with parsed counts, migrations."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import JobPosting
from app.storage import db, radar_store

D1, D2, D3, D4 = date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28), date(2026, 9, 29)


def _job(job_id, title="ML Engineer"):
    return JobPosting(company="Example", title=title, location="Bengaluru, India", source="Greenhouse",
                      source_job_id=job_id, application_url=f"https://job-boards.greenhouse.io/example/jobs/{job_id}")


class RadarStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "radar.sqlite3"
        patcher = patch.object(radar_store, "DB_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    def _statuses(self):
        return {job.source_job_id: job.verification_state for job in radar_store.list_jobs(include_closed=True)}

    def test_listed_jobs_are_active_and_new_ones_are_counted(self):
        result = radar_store.record_listing("example", [_job("1"), _job("2")], complete=True, today=D1)
        self.assertEqual((result.listed, result.new, result.closed), (2, 2, 0))
        self.assertEqual(self._statuses(), {"1": "ACTIVE_VERIFIED", "2": "ACTIVE_VERIFIED"})
        active = radar_store.list_jobs()
        self.assertTrue(all("official board" in job.verification_reason for job in active))

    def test_a_job_missing_on_two_separate_days_closes(self):
        radar_store.record_listing("example", [_job("1"), _job("2")], complete=True, today=D1)
        radar_store.record_listing("example", [_job("1")], complete=True, today=D2)
        self.assertEqual(self._statuses()["2"], "ACTIVE_VERIFIED")
        result = radar_store.record_listing("example", [_job("1")], complete=True, today=D3)
        self.assertEqual(result.closed, 1)
        self.assertEqual(self._statuses()["2"], "CLOSED")

    def test_same_day_rerun_does_not_count_twice(self):
        radar_store.record_listing("example", [_job("1"), _job("2")], complete=True, today=D1)
        radar_store.record_listing("example", [_job("1")], complete=True, today=D2)
        radar_store.record_listing("example", [_job("1")], complete=True, today=D2)
        self.assertEqual(self._statuses()["2"], "ACTIVE_VERIFIED")

    def test_a_reappearing_job_reopens(self):
        radar_store.record_listing("example", [_job("1")], complete=True, today=D1)
        radar_store.record_listing("example", [], complete=True, today=D2)
        radar_store.record_listing("example", [], complete=True, today=D3)
        self.assertEqual(self._statuses()["1"], "CLOSED")
        radar_store.record_listing("example", [_job("1")], complete=True, today=D4)
        self.assertEqual(self._statuses()["1"], "ACTIVE_VERIFIED")

    def test_incomplete_sources_never_close_by_absence(self):
        radar_store.record_listing("example", [_job("1")], complete=False, today=D1)
        for day in (D2, D3, D4):
            radar_store.record_listing("example", [], complete=False, today=day)
        self.assertEqual(self._statuses()["1"], "ACTIVE_VERIFIED")
        self.assertEqual([job.source_job_id for job in radar_store.missing_from_window("example")], ["1"])
        radar_store.mark_closed("example", "1", "Job page returned HTTP 404.", today=D4)
        self.assertEqual(self._statuses()["1"], "CLOSED")

    def test_other_companies_are_untouched(self):
        radar_store.record_listing("example", [_job("1")], complete=True, today=D1)
        radar_store.record_listing("other", [], complete=True, today=D2)
        radar_store.record_listing("other", [], complete=True, today=D3)
        self.assertEqual(self._statuses()["1"], "ACTIVE_VERIFIED")

    def test_runs_log_counts_from_the_parsed_lists(self):
        run = radar_store.start_run("example", today=D1)
        radar_store.finish_run(run, status="ok", fetched=40, india=7, new=7, closed=0, listed=7, http_status=200)
        [logged] = radar_store.runs(day=D1)
        self.assertEqual((logged["company_id"], logged["fetched_count"], logged["india_count"], logged["status"]),
                         ("example", 40, 7, "ok"))
        self.assertTrue(radar_store.ran_today("example", today=D1))
        self.assertFalse(radar_store.ran_today("example", today=D2))

    def test_schema_is_created_by_migrations(self):
        radar_store.list_jobs()
        with closing(sqlite3.connect(self.path)) as conn:
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(versions, ["radar_v1", "radar_v2"])
        self.assertLessEqual({"radar_jobs", "radar_job_sources", "radar_sync_runs", "radar_rejected"}, tables)

    def test_known_jobs_and_rejected_ids(self):
        radar_store.record_listing("example", [_job("1"), _job("2")], complete=True, today=D1)
        radar_store.mark_closed("example", "2", "gone", today=D1)
        self.assertEqual(set(radar_store.known_jobs("example")), {"1"})
        radar_store.remember_rejected("example", ["9", "9", "8"], today=D1)
        self.assertEqual(radar_store.rejected_ids("example"), {"8", "9"})
        self.assertEqual(radar_store.rejected_ids("other"), set())


if __name__ == "__main__":
    unittest.main()
