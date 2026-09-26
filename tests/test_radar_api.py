"""Company Radar endpoints: status, a background Sync now, and today's new jobs."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import radar as radar_api
from app.main import app
from app.sources import sync
from app.storage import db, radar_store

LOCAL = {"Origin": "http://localhost:8010"}


class RadarApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for target, name, value in ((radar_store, "DB_PATH", Path(directory.name) / "radar.sqlite3"),
                                    (radar_api, "_STATE", {"running": False, "started_at": None, "finished_at": None,
                                                           "result": None, "error": None})):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        self.client = TestClient(app, base_url="http://localhost:8010")
        self.addCleanup(self.client.close)

    def test_status_before_any_sync_creates_nothing(self):
        body = self.client.get("/radar/status").json()
        self.assertIsNone(body["last_sync"])
        self.assertFalse(body["manual_sync"]["running"])
        self.assertGreater(body["companies"], 0)
        self.assertFalse(radar_store.DB_PATH.exists())

    def test_sync_now_needs_the_local_origin(self):
        self.assertEqual(self.client.post("/radar/sync").status_code, 403)

    def test_sync_now_runs_in_the_background_once_at_a_time(self):
        release, started = threading.Event(), threading.Event()

        async def fake_cycle(force=False):
            started.set()
            release.wait(5)
            return {"companies": 2, "ok": 2, "new": 5}

        with patch.object(sync, "run_cycle", fake_cycle):
            self.assertEqual(self.client.post("/radar/sync", headers=LOCAL).status_code, 202)
            self.assertTrue(started.wait(5))
            self.assertEqual(self.client.post("/radar/sync", headers=LOCAL).status_code, 409)
            self.assertTrue(self.client.get("/radar/status").json()["manual_sync"]["running"])
            release.set()
            for _ in range(100):
                state = self.client.get("/radar/status").json()["manual_sync"]
                if not state["running"]:
                    break
                threading.Event().wait(0.05)
        self.assertEqual(state["result"], {"companies": 2, "ok": 2, "new": 5})
        self.assertIsNone(state["error"])

    def test_failures_are_reported_not_raised(self):
        async def failing_cycle(force=False):
            raise RuntimeError("boom")

        with patch.object(sync, "run_cycle", failing_cycle):
            radar_api._run_in_background()
        self.assertEqual(radar_api._STATE["error"], "RuntimeError")

    def test_new_today_returns_the_digest(self):
        body = self.client.get("/radar/new-today").json()
        self.assertEqual(set(body), {"day", "new_jobs", "off_role", "excluded", "eligible", "uncertain"})


if __name__ == "__main__":
    unittest.main()
