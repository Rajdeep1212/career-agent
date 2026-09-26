"""The daily digest: today's new Radar jobs for the saved roles, eligible before uncertain."""
import gc
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.models.schemas import CandidateProfile
from app.sources import digest
from app.sources.adapters import posting
from app.storage import db, preference_store, profile_store, radar_store
from radar_helpers import company

TODAY = datetime.now(timezone.utc).astimezone().date()


class DigestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        for module, name, value in ((radar_store, "DB_PATH", self.root / "radar.sqlite3"),
                                    (profile_store, "PROFILE_PATH", self.root / "profile.json"),
                                    (preference_store, "PREFERENCES_PATH", self.root / "preferences.json"),
                                    (settings, "data_dir", str(self.root))):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CandidateProfile(name="Test Student", graduation_year=2025, experience_years=0,
                                                    skills=["Python", "PyTorch"], preferred_roles=["Machine Learning Engineer"],
                                                    preferred_locations=["Bengaluru"]))
        entry = company({"type": "greenhouse", "board": "acme"}, id="acme", name="Acme")
        jobs = [
            ("1", "Machine Learning Engineer", "Freshers welcome. Python and PyTorch. <script>x</script>"),
            ("2", "Machine Learning Engineer II", "1-2 years of experience preferred."),
            ("3", "Senior Machine Learning Engineer", "8+ years of experience required."),
            ("4", "Accountant", "Freshers welcome."),
        ]
        radar_store.record_listing("acme", [posting(entry, job_id=job_id, title=title, location="Bengaluru, India",
                                                    description=text, url=f"https://job-boards.greenhouse.io/acme/jobs/{job_id}")
                                            for job_id, title, text in jobs], complete=True, today=TODAY)

    def test_digest_tiers_and_counts(self):
        result = digest.build_digest(today=TODAY)
        self.assertEqual((result.new_jobs, result.off_role, result.excluded), (4, 1, 1))
        self.assertEqual([item.title for item in result.eligible], ["Machine Learning Engineer"])
        self.assertEqual([item.title for item in result.uncertain], ["Machine Learning Engineer II"])
        self.assertTrue(result.uncertain[0].summary.startswith("uncertain: quoted '1-2 years of experience preferred'"))

    def test_jobs_first_seen_before_today_are_not_new(self):
        self.assertEqual(digest.build_digest(today=date(TODAY.year + 1, 1, 1)).new_jobs, 0)

    def test_html_file_is_written_escaped(self):
        path = digest.write_digest(digest.build_digest(today=TODAY))
        self.assertEqual(path, self.root / "digests" / f"{TODAY.isoformat()}.html")
        text = path.read_text(encoding="utf-8")
        self.assertIn("Eligible (1)", text)
        self.assertIn("heuristic fit", text)

    def test_rendering_escapes_listing_text(self):
        item = digest.DigestItem(title="<script>alert(1)</script>", company="A&B", location="Pune", url="https://x.example/?a=1&b=2",
                                 posted=None, status="eligible", summary="eligible: quoted '<b>'", score=40)
        text = digest.render_html(digest.Digest(day="2026-09-26", eligible=[item]))
        self.assertNotIn("<script>alert", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn('href="https://x.example/?a=1&amp;b=2"', text)

    def test_empty_index_gives_an_empty_digest(self):
        with patch.object(radar_store, "DB_PATH", self.root / "missing.sqlite3"):
            self.assertEqual(digest.build_digest(today=TODAY).new_jobs, 0)


if __name__ == "__main__":
    unittest.main()
