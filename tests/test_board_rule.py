"""Q2c4: one rule decides whether a board is pollable, for the slug probe and for detection alike."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.sources.ats_detect import DetectFetcher
from app.sources.board_rule import STALE_DAYS, Board, BoardError, judge, newest, read_board
from test_ats_detect import Clock, FakeWeb

TODAY = date(2026, 10, 1)


def board(total=5, india=2, posted=("2026-09-20",), name="Example Labs") -> Board:
    return Board(total=total, india=india, name=name, texts=[], key="examplelabs", posted=list(posted))


class JudgeTests(unittest.TestCase):
    def test_pollable_needs_a_verified_name_an_india_job_and_a_recent_posting(self):
        status, evidence = judge(board(), name_ok=True, today=TODAY)
        self.assertEqual(status, "pollable")
        self.assertEqual(evidence, "2 of 5 jobs in India; newest posting 2026-09-20")

    def test_no_india_job_or_an_old_or_missing_date_is_stale_no_india(self):
        cases = {"no India job among 5; newest posting 2026-09-20": board(india=0),
                 "2 of 5 jobs in India; newest posting 2018-01-17 is more than 180 days old": board(posted=("2018-01-17", "2017-01-01")),
                 "2 of 5 jobs in India; no posting date given": board(posted=(None,)),
                 "the board lists no job": board(total=0, india=0, posted=())}
        for evidence, facts in cases.items():
            self.assertEqual(judge(facts, name_ok=True, today=TODAY), ("stale_no_india", evidence))

    def test_the_180_day_edge(self):
        self.assertEqual(STALE_DAYS, 180)
        self.assertEqual(judge(board(posted=("2026-04-04",)), name_ok=True, today=TODAY)[0], "pollable")          # 180 days
        self.assertEqual(judge(board(posted=("2026-04-03",)), name_ok=True, today=TODAY)[0], "stale_no_india")    # 181 days

    def test_an_unverified_name_is_never_pollable(self):
        self.assertEqual(judge(board(), name_ok=False, today=TODAY)[0], "name_mismatch")
        self.assertEqual(judge(board(india=0), name_ok=False, today=TODAY)[0], "name_mismatch")

    def test_newest_reads_iso_text_and_epoch_milliseconds(self):
        self.assertEqual(newest(["2026-09-20T10:00:00Z", 1500000000000, None, "not a date"]), date(2026, 9, 20))
        self.assertIsNone(newest([None, ""]))


class ReadBoardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.clock = Clock()
        self.cache = Path(directory.name)

    def fetcher(self, routes):
        web = FakeWeb(routes)
        return DetectFetcher(get=web.get, clock=self.clock.time, sleep=self.clock.sleep, cache_dir=self.cache, now=self.clock.utcnow)

    async def test_a_board_is_total_india_and_posting_dates(self):
        jobs = {"jobs": [{"id": 1, "location": {"name": "Pune, India"}, "company_name": "Example Labs", "first_published": "2026-09-20"},
                         {"id": 2, "location": {"name": "London"}, "company_name": "Example Labs", "updated_at": "2026-08-01"}]}
        url = "https://boards-api.greenhouse.io/v1/boards/examplelabs/jobs?content=true"
        found = await read_board("greenhouse", "examplelabs", self.fetcher({url: (200, json.dumps(jobs))}))
        self.assertEqual((found.total, found.india, found.name, newest(found.posted)), (2, 1, "Example Labs", date(2026, 9, 20)))

    async def test_a_404_and_a_server_error_are_told_apart(self):
        with self.assertRaises(BoardError) as missing:
            await read_board("ashby", "nobody", self.fetcher({}))
        self.assertEqual(missing.exception.http_status, 404)
        self.assertIn("HTTP 404", str(missing.exception))
        url = "https://api.ashbyhq.com/posting-api/job-board/down"
        with self.assertRaises(BoardError) as down:
            await read_board("ashby", "down", self.fetcher({url: (503, "maintenance")}))
        self.assertEqual(down.exception.http_status, 503)

    async def test_an_eu_lever_site_is_read_from_the_eu_api(self):
        url = "https://api.eu.lever.co/v0/postings/examplelabs?mode=json"
        posting = [{"id": "a", "categories": {"location": "Pune, India"}, "createdAt": 1790000000000, "descriptionPlain": "x"}]
        found = await read_board("lever", "examplelabs", self.fetcher({url: (200, json.dumps(posting))}), region="eu")
        self.assertEqual((found.total, found.india), (1, 1))


if __name__ == "__main__":
    unittest.main()
