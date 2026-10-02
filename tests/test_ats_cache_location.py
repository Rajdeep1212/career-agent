"""Q2d: the detection and probe cache lives in .cache/ats/, outside data/, so a background run can
never change the data directory that test_demo_mode_is_fully_isolated watches."""
import unittest
from pathlib import Path

from app.core.config import settings
from app.sources import ats_detect

ROOT = Path(__file__).resolve().parents[1]


class CacheLocationTests(unittest.TestCase):
    def test_the_cache_is_in_dot_cache_ats_and_not_under_the_data_directory(self):
        self.assertEqual(ats_detect.CACHE_DIR, ROOT / ".cache" / "ats")
        self.assertNotIn(Path(settings.data_dir).resolve(), [ats_detect.CACHE_DIR, *ats_detect.CACHE_DIR.parents])
        self.assertNotIn(ROOT / "data", ats_detect.CACHE_DIR.parents)

    def test_the_cache_directory_is_git_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".cache/", ignored)

    def test_no_script_still_points_at_the_old_place(self):
        for script in ("detect_ats.py", "probe_boards.py"):
            self.assertNotIn("data/ats_detect_cache", (ROOT / "scripts" / script).read_text(encoding="utf-8"), script)


if __name__ == "__main__":
    unittest.main()
