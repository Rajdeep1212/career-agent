"""Q2c3: a researched careers patch is merged into the seed without overwriting anything the seed already has,
and detection can be re-run on the newly filled rows only. Fictional companies; a fake web."""
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

from app.sources.ats_detect import DetectFetcher, detect_all
from app.sources.company_seed import COLUMNS, read_seed
from app.sources.seed_patch import PATCH_COLUMNS, plan_merge, read_patch
from radar_helpers import fixture_text
from test_ats_detect import Clock, FakeWeb, row

ROOT = Path(__file__).resolve().parents[1]
BASE = ["startup", "startup", "", "Pune", "Pune", "AI", "Fixture", "https://lists.example/a"]
SEED = [["Empty Verified", *BASE, "", "unknown"],
        ["Empty Blocked", *BASE, "", "unknown"],
        ["Empty Check", *BASE, "", "unknown"],
        ["Empty Not Found", *BASE, "", "unknown"],
        ["Empty Defunct", *BASE, "", "unknown"],
        ["Api Board", *BASE, "https://jobs.ashbyhq.com/apiboard", "ashby"],
        ["Old Spelling", *BASE, "https://boards.greenhouse.io/oldspelling", "greenhouse"],
        ["Same Url", *BASE, "https://jobs.lever.co/sameurl", "lever"],
        ["Not In Patch", *BASE, "", "unknown"]]
PATCH = [["Empty Verified", "Pune", "startup", "https://www.emptyverified.example/careers", "verified", "keka", "3", "fetched", "2026-10-02"],
         ["Empty Blocked", "Pune", "startup", "https://www.emptyblocked.example/careers", "fetch_blocked", "unknown", "", "403", "2026-10-02"],
         ["Empty Check", "Pune", "startup", "https://www.emptycheck.example/careers", "check", "own_site", "1", "confirm", "2026-10-02"],
         ["Empty Not Found", "Pune", "startup", "", "not_found", "", "", "no careers link", "2026-10-02"],
         ["Empty Defunct", "Pune", "startup", "", "defunct_or_merged", "", "", "acquired by Other Co", "2026-10-02"],
         ["Api Board", "Pune", "startup", "https://apiboard.freshteam.com/jobs", "verified", "freshteam", "40", "fetched", "2026-10-02"],
         ["Old Spelling", "Pune", "startup", "https://job-boards.greenhouse.io/oldspelling", "verified", "greenhouse", "14", "fetched", "2026-10-02"],
         ["Same Url", "Pune", "startup", "https://jobs.lever.co/sameurl", "verified", "lever", "17", "fetched", "2026-10-02"]]


def write_csv(path: Path, header, rows) -> Path:
    path.write_text("\n".join([",".join(header)] + [",".join(cells) for cells in rows]) + "\n", encoding="utf-8")
    return path


class MergeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.seed = write_csv(self.directory / "companies_seed.csv", COLUMNS, SEED)
        self.patch = write_csv(self.directory / "careers_patch.csv", PATCH_COLUMNS, PATCH)

    def plan(self):
        return plan_merge(read_seed(self.seed, expected_rows=len(SEED)), read_patch(self.patch))

    def test_only_empty_cells_are_filled_and_only_from_verified_check_or_blocked_rows(self):
        plan = self.plan()
        self.assertEqual(plan.filled, {"Empty Verified": "https://www.emptyverified.example/careers",
                                       "Empty Blocked": "https://www.emptyblocked.example/careers",
                                       "Empty Check": "https://www.emptycheck.example/careers"})
        self.assertEqual(plan.skipped, {"Empty Not Found": "not_found", "Empty Defunct": "defunct_or_merged"})

    def test_an_existing_value_is_kept_unless_the_patch_names_the_same_board(self):
        plan = self.plan()
        self.assertEqual(plan.kept, {"Api Board": ("https://jobs.ashbyhq.com/apiboard", "https://apiboard.freshteam.com/jobs")})
        self.assertEqual(plan.respelled, {"Old Spelling": "https://job-boards.greenhouse.io/oldspelling"})
        self.assertEqual(plan.unchanged, ["Same Url"])

    def test_the_updates_never_touch_ats(self):
        updates = self.plan().updates()
        self.assertEqual(set(updates), {"Empty Verified", "Empty Blocked", "Empty Check", "Old Spelling"})
        self.assertTrue(all(set(cells) == {"careers_url"} for cells in updates.values()))

    def test_a_patch_row_for_an_unknown_company_or_a_wrong_header_is_an_error(self):
        write_csv(self.patch, PATCH_COLUMNS, PATCH + [["Stranger", "Pune", "startup", "https://x.example/careers", "verified", "", "", "", "2026-10-02"]])
        with self.assertRaises(ValueError) as caught:
            self.plan()
        self.assertIn("Stranger", str(caught.exception))
        write_csv(self.patch, PATCH_COLUMNS[:-1], [])
        with self.assertRaises(ValueError):
            read_patch(self.patch)

    def test_a_never_fetched_or_malformed_patch_url_is_refused(self):
        for url in ("https://www.linkedin.com/company/emptyverified/jobs", "emptyverified.example/careers"):
            write_csv(self.patch, PATCH_COLUMNS, [["Empty Verified", "Pune", "startup", url, "verified", "", "", "", "2026-10-02"]])
            plan = self.plan()
            self.assertEqual(plan.filled, {})
            self.assertIn("Empty Verified", plan.refused)

    def test_the_script_merges_and_leaves_every_other_byte(self):
        spec = importlib.util.spec_from_file_location("merge_careers_patch", ROOT / "scripts" / "merge_careers_patch.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        before = self.seed.read_text(encoding="utf-8").splitlines()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = script.main(["--csv", str(self.seed), "--patch", str(self.patch), "--expect-rows", str(len(SEED)),
                                "--filled-list", str(self.directory / "filled.txt")])
        after = self.seed.read_text(encoding="utf-8").splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(after[1], ",".join(["Empty Verified", *BASE, "https://www.emptyverified.example/careers", "unknown"]))
        self.assertEqual(after[7], ",".join(["Old Spelling", *BASE, "https://job-boards.greenhouse.io/oldspelling", "greenhouse"]))
        self.assertEqual([after[index] for index in (0, 4, 5, 6, 8, 9)], [before[index] for index in (0, 4, 5, 6, 8, 9)])
        self.assertEqual((self.directory / "filled.txt").read_text(encoding="utf-8").splitlines(),
                         ["Empty Verified", "Empty Blocked", "Empty Check"])
        self.assertIn("filled 3, respelled 1, kept 1, skipped 2", output.getvalue())
        self.assertIn("Api Board", output.getvalue())


class DetectOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_rows_outside_the_list_are_answered_from_the_cache_and_never_requested(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        clock = Clock()
        cached, uncached, fresh = "https://www.cached.example/careers", "https://www.uncached.example/careers", "https://www.fresh.example/careers"
        routes = {cached: (200, fixture_text("careers_plain.html")), uncached: (200, fixture_text("careers_plain.html")),
                  fresh: (200, fixture_text("careers_greenhouse_embed.html"))}

        def fetcher(web):
            return DetectFetcher(get=web.get, clock=clock.time, sleep=clock.sleep, cache_dir=Path(directory.name), now=clock.utcnow)
        await detect_all([row("Cached Co", cached)], fetcher(FakeWeb(routes)))
        web = FakeWeb(routes)
        rows = [row("Cached Co", cached), row("Uncached Co", uncached, line=3), row("Fresh Co", fresh, line=4)]
        results = await detect_all(rows, fetcher(web), only={"Fresh Co"})
        self.assertEqual([found.status for found in results[:2]], ["not_detected", "not_read"])
        self.assertEqual(results[1].note, "not requested in this run and not in the cache")
        self.assertEqual(results[2].ats, "greenhouse")
        self.assertFalse([call for call in web.calls if "cached.example" in call])
        self.assertIn(fresh, web.calls)


if __name__ == "__main__":
    unittest.main()
