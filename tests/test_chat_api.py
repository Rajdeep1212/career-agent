import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class ChatAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from app import main
        from app.api import chat
        self.main = main
        patcher = patch.object(chat, "CHECKPOINT_PATH", Path(self.temp.name) / "graph.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app, base_url="http://localhost:8010", raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_chat_mutations_require_exact_local_origin(self):
        payload = {"thread_id": "api-thread", "turn_id": "turn-1", "message": "Career advice"}
        self.assertEqual(self.client.post("/chat/run", json=payload).status_code, 403)
        self.assertEqual(self.client.post(
            "/chat/run", json=payload, headers={"Origin": "https://attacker.example"}
        ).status_code, 403)
        response = self.client.post(
            "/chat/run", json=payload, headers={"Origin": "http://localhost:8010"}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_chat_run_cannot_set_internal_synthetic_evaluation_marker(self):
        response = self.client.post(
            "/chat/run",
            json={
                "thread_id": "api-thread",
                "turn_id": "turn-marker",
                "message": "Career advice",
                "_synthetic_evaluation": True,
            },
            headers={"Origin": "http://localhost:8010"},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_resume_rejects_unknown_thread_without_side_effect(self):
        response = self.client.post(
            "/chat/resume",
            json={
                "thread_id": "missing-thread", "turn_id": "turn-1",
                "draft_id": 1, "confirmed": True,
            },
            headers={"Origin": "http://localhost:8010"},
        )
        self.assertIn(response.status_code, (404, 409))


if __name__ == "__main__":
    unittest.main()
