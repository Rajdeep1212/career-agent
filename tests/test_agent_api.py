import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


class CareerAgentAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost:8010")
        self.addCleanup(self.client.close)

    def test_agent_search_forwards_structured_state_and_preserves_legacy_action(self):
        result = {
            "session_id": "session-123",
            "diagnostics": {"generated_queries": 2},
            "results": [],
            "result_count": 0,
        }
        search = AsyncMock(return_value=result)
        with patch("app.services.career_agent.CareerAgent.search", search):
            response = self.client.post(
                "/agent/search",
                json={
                    "query": "not strict",
                    "include_seen": True,
                    "session_id": "session-123",
                    "strict_mode": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["agent_action"], "search_verify_rank")
        self.assertEqual(response.json()["diagnostics"], {"generated_queries": 2})
        search.assert_awaited_once_with(
            "not strict",
            include_seen=True,
            session_id="session-123",
            strict_mode=False,
            refresh=False,
        )

    def test_unknown_session_is_a_client_error(self):
        with patch(
            "app.services.career_agent.CareerAgent.search",
            new=AsyncMock(side_effect=ValueError("Search session was not found. Start a new search.")),
        ):
            response = self.client.post(
                "/agent/search",
                json={"query": "show stronger matches", "session_id": "missing"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Search session was not found. Start a new search.")


if __name__ == "__main__":
    unittest.main()
