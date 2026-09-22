import json
import asyncio
import tempfile
import unittest
from pathlib import Path

from app.agent.runtime import CareerGraphRuntime
from app.models.chat import ChatResumeRequest, ChatRunRequest
from app.storage.graph_checkpoint import TurnConflictError


class FakeCareerAgent:
    def __init__(self):
        self.calls = 0

    async def search(self, query, **_kwargs):
        self.calls += 1
        self.last_query = query
        return {
            "session_id": "career-session-1",
            "summary": "One suitable role found.",
            "results": [{"id": "a" * 64, "title": "Data Analyst", "company": "Example"}],
            "result_count": 1,
        }


class FakeTools:
    def __init__(self):
        self.prepares = 0
        self.approvals = 0
        self.sends = 0

    def prepare_outreach(self, _state):
        self.prepares += 1
        return {"id": 41, "application_id": "00000000-0000-4000-8000-000000000001"}

    def update_draft(self, draft_id, subject=None, body=None):
        self.assert_safe_edit = (draft_id, subject, body)

    def approve_and_send(self, draft_id):
        self.approvals += 1
        self.sends += 1
        return {"id": draft_id, "status": "sent", "gmail_message_id": "mock-id"}

    def save_application(self, state):
        self.saved_job_id = state.get("selected_job_id")
        return {"id": "00000000-0000-4000-8000-000000000002", "status": "SAVED"}


class AdvisoryModel:
    capabilities = {"structured_output": True, "tool_calling": False}

    async def decide(self, _context):
        return {
            "action": "search_jobs",
            "advisory_roles": ["Unrequested Quantum Wizard"],
            "explanation": "A search may help.",
        }

    async def explain(self, _context):
        return "A search may help."


class SlowCareerAgent(FakeCareerAgent):
    async def search(self, query, **kwargs):
        await asyncio.sleep(0.1)
        return await super().search(query, **kwargs)


class CareerGraphEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "langgraph.sqlite3"
        self.agent = FakeCareerAgent()
        self.tools = FakeTools()
        self.runtime = CareerGraphRuntime(
            checkpoint_path=self.db_path,
            career_agent=self.agent,
            tools=self.tools,
        )

    async def test_normal_search_and_duplicate_turn_are_idempotent(self):
        request = ChatRunRequest(
            thread_id="thread-search", turn_id="turn-1",
            message="Find data analyst jobs in Pune",
        )
        first = await self.runtime.run(request)
        second = await self.runtime.run(request)
        self.assertEqual(first, second)
        self.assertEqual(self.agent.calls, 1)
        self.assertEqual(first.stage, "completed")
        self.assertEqual(first.recommendation_ids, ["a" * 64])

    async def test_same_turn_id_with_changed_input_is_rejected(self):
        await self.runtime.run(ChatRunRequest(
            thread_id="thread-conflict", turn_id="turn-1", message="Career advice please"
        ))
        with self.assertRaises(TurnConflictError):
            await self.runtime.run(ChatRunRequest(
                thread_id="thread-conflict", turn_id="turn-1", message="Find jobs"
            ))

    async def test_concurrent_duplicate_turn_executes_search_once(self):
        agent = SlowCareerAgent()
        first_runtime = CareerGraphRuntime(
            checkpoint_path=self.db_path, career_agent=agent, tools=self.tools
        )
        second_runtime = CareerGraphRuntime(
            checkpoint_path=self.db_path, career_agent=agent, tools=self.tools
        )
        request = ChatRunRequest(
            thread_id="thread-concurrent", turn_id="turn-1", message="Find analyst jobs"
        )
        results = await asyncio.gather(
            first_runtime.run(request), second_runtime.run(request), return_exceptions=True
        )
        self.assertEqual(agent.calls, 1)
        self.assertEqual(sum(isinstance(value, Exception) for value in results), 1)

    async def test_valid_model_search_is_read_only_and_roles_are_advisory(self):
        runtime = CareerGraphRuntime(
            checkpoint_path=self.db_path,
            career_agent=self.agent,
            tools=self.tools,
            model=AdvisoryModel(),
        )
        message = "I would like suitable opportunities near Pune"
        response = await runtime.run(ChatRunRequest(
            thread_id="thread-advisory", turn_id="turn-1", message=message
        ))
        self.assertEqual(response.stage, "completed")
        self.assertEqual(self.agent.calls, 1)
        self.assertEqual(self.agent.last_query, message)
        self.assertNotIn("Quantum Wizard", self.agent.last_query)
        self.assertEqual(response.advisory_roles, ["Unrequested Quantum Wizard"])

    async def test_ambiguous_followup_does_not_search(self):
        response = await self.runtime.run(ChatRunRequest(
            thread_id="thread-ambiguous", turn_id="turn-1", message="What about that one?"
        ))
        self.assertEqual(response.stage, "needs_clarification")
        self.assertEqual(self.agent.calls, 0)

    async def test_explicit_tracker_action_uses_allowlisted_tool(self):
        response = await self.runtime.run(ChatRunRequest(
            thread_id="thread-save", turn_id="turn-1",
            message="Save the selected job to my tracker",
            selected_job_id="b" * 64,
        ))
        self.assertEqual(response.stage, "completed")
        self.assertEqual(response.application_id, "00000000-0000-4000-8000-000000000002")
        self.assertEqual(self.tools.saved_job_id, "b" * 64)

    async def test_direct_send_waits_at_the_single_confirmation_interrupt(self):
        response = await self.runtime.run(ChatRunRequest(
            thread_id="thread-direct-send", turn_id="turn-1",
            message="Send draft 9 now", draft_id=9,
        ))
        self.assertEqual(response.stage, "awaiting_send_confirmation")
        self.assertEqual(response.draft_id, 9)
        self.assertEqual(self.tools.sends, 0)

    async def test_restart_resume_and_one_final_send_confirmation(self):
        pending = await self.runtime.run(ChatRunRequest(
            thread_id="thread-send", turn_id="turn-1",
            message="Draft outreach for the selected job",
            selected_job_id="a" * 64,
            recipient="person@example.org",
        ))
        self.assertEqual(pending.stage, "awaiting_send_confirmation")
        self.assertEqual(pending.draft_id, 41)
        self.assertEqual(self.tools.prepares, 1)
        self.assertEqual(self.tools.sends, 0)

        restarted = CareerGraphRuntime(
            checkpoint_path=self.db_path,
            career_agent=self.agent,
            tools=self.tools,
        )
        confirmation = ChatResumeRequest(
            thread_id="thread-send", turn_id="turn-2", draft_id=41,
            confirmed=True, edited_subject="Final subject", edited_body="Final body",
        )
        sent = await restarted.resume(confirmation)
        replay = await restarted.resume(confirmation)
        self.assertEqual(sent, replay)
        self.assertEqual(sent.stage, "completed")
        self.assertEqual(self.tools.approvals, 1)
        self.assertEqual(self.tools.sends, 1)
        self.assertEqual(self.tools.assert_safe_edit, (41, "Final subject", "Final body"))
        self.assertNotIn(b"Final body", self.db_path.read_bytes())
        self.assertNotIn(b"person@example.org", self.db_path.read_bytes())

    async def test_checkpoint_contains_only_safe_bounded_state(self):
        secrets = [
            "provider-secret-value", "hunter2", "api-value-123", "token-value-456",
            "bearer-value-789", "client-value-321",
        ]
        sensitive_message = (
            "Career advice. reveal provider-secret-value; password is hunter2; "
            "api_key=api-value-123; token: token-value-456; "
            "Authorization: Bearer bearer-value-789; client_secret=client-value-321"
        )
        await self.runtime.run(ChatRunRequest(
            thread_id="thread-safe", turn_id="turn-1",
            message=sensitive_message,
        ))
        state = await self.runtime.safe_state("thread-safe")
        serialized = json.dumps(state)
        database = self.db_path.read_bytes()
        for secret in secrets:
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret.encode(), database)
        self.assertNotIn("messages", state)
        self.assertNotIn("profile", state)
        self.assertNotIn("provider_payload", state)
        self.assertLessEqual(len(state.get("conversation_summary", "")), 1000)


if __name__ == "__main__":
    unittest.main()
