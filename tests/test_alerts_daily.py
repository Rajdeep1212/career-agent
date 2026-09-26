"""Alert ingestion runs with the daily sync (IMAP, then the .eml dropbox) and feeds the digest."""
import gc
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.models.schemas import CandidateProfile
from app.sources import digest, sync
from app.sources.alerts import daily
from app.sources.alerts.ingest import IngestOutcome
from app.storage import alert_store, db, preference_store, profile_store, radar_store

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "alerts"


class AlertsDailyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        self.dropbox = self.root / "alert_dropbox"
        self.dropbox.mkdir()
        for target, name, value in ((alert_store, "DB_PATH", self.root / "alerts.sqlite3"),
                                    (radar_store, "DB_PATH", self.root / "radar.sqlite3"),
                                    (profile_store, "PROFILE_PATH", self.root / "profile.json"),
                                    (preference_store, "PREFERENCES_PATH", self.root / "preferences.json"),
                                    (settings, "alerts_dropbox_dir", str(self.dropbox)),
                                    (settings, "alerts_imap_app_password", "")):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    def test_without_imap_or_files_it_is_skipped_quietly(self):
        outcome = daily.run_alerts()
        self.assertEqual(outcome.status, "skipped")

    def test_dropbox_is_used_when_imap_is_not_configured(self):
        shutil.copy(FIXTURES / "indeed_alert.eml", self.dropbox)
        outcome = daily.run_alerts()
        self.assertEqual((outcome.status, outcome.messages, outcome.new), ("ok", 1, 2))

    def test_imap_error_is_reported_but_the_dropbox_still_runs(self):
        shutil.copy(FIXTURES / "indeed_alert.eml", self.dropbox)
        with patch.object(daily, "ingest_imap", lambda **kwargs: IngestOutcome(status="error", note="IMAP login failed")):
            outcome = daily.run_alerts()
        self.assertEqual((outcome.status, outcome.new), ("error", 2))
        self.assertIn("IMAP login failed", outcome.note)

    def test_cli_dry_run_prints_parsed_jobs(self):
        shutil.copy(FIXTURES / "naukri_alert.eml", self.dropbox)
        with patch("builtins.print") as printed:
            self.assertEqual(daily.main(["--dry-run"]), 0)
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("Data Analyst | Example Retail Pvt. Ltd. | Pune", output)
        self.assertIn("dry run", output)
        self.assertTrue((self.dropbox / "naukri_alert.eml").exists())

    def test_sync_cli_runs_alert_ingestion_unless_disabled(self):
        calls = []

        async def fake_run(**kwargs):
            return []

        async def fake_daily(**kwargs):
            from app.sources.daily_jsearch import DailyOutcome
            return DailyOutcome("skipped", note="RAPIDAPI_KEY is not set")

        def fake_alerts(**kwargs):
            calls.append("alerts")
            return IngestOutcome(messages=2, parsed_jobs=5, new=4, matched_official=1)

        with patch.object(sync, "run_sync", fake_run), patch.object(sync, "run_daily_jsearch", fake_daily), \
             patch.object(sync, "run_alerts", fake_alerts), patch.object(sync, "load_config", lambda: None), \
             patch.object(sync, "write_digest", lambda d: "digest.html"), patch("builtins.print") as printed:
            sync.main([])
            sync.main(["--no-alerts"])
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertEqual(calls, ["alerts"])
        self.assertIn("Alert emails: ok, 2 emails, 5 jobs (4 new, 1 matched an official job)", output)

    def test_new_unmatched_alert_jobs_appear_in_the_digest(self):
        profile_store.save_profile(CandidateProfile(graduation_year=2025, experience_years=0, preferred_roles=["Python Developer"]))
        shutil.copy(FIXTURES / "indeed_alert.eml", self.dropbox)
        daily.run_alerts()
        today = datetime.now(timezone.utc).astimezone().date()
        result = digest.build_digest(today=today)
        items = result.eligible + result.uncertain
        self.assertIn(("Python Developer", "Indeed alert"), [(item.title, item.source) for item in items])
        self.assertEqual(result.new_jobs, 2)


if __name__ == "__main__":
    unittest.main()
