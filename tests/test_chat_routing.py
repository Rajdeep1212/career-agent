"""Short role-only chat messages should run a search, not ask for more detail."""
import tempfile
import unittest
from pathlib import Path

from app.agent.routing import deterministic_action
from app.agent.runtime import CareerGraphRuntime
from app.models.chat import ChatRunRequest
from test_career_graph import FakeCareerAgent, FakeTools


class RoleQueryRoutingTests(unittest.TestCase):
    def test_short_role_queries_route_to_search(self):
        for message in ('GenAI engineer fresher', 'Data analyst', 'python backend developer remote',
                        'ML intern Bengaluru', 'entry level data scientist', 'QA tester jobs',
                        'Find GenAI engineer fresher jobs in Bengaluru'):
            self.assertEqual(deterministic_action(message), 'search_jobs', message)

    def test_questions_about_roles_stay_career_advice(self):
        for message in ('What skills should a data analyst learn?', 'How do I become a GenAI engineer',
                        'Should I learn Docker as a backend developer?', 'Career advice please',
                        'Is data science a good career for freshers?'):
            self.assertEqual(deterministic_action(message), 'career_advice', message)

    def test_explicit_actions_still_win(self):
        self.assertEqual(deterministic_action('Draft outreach email for the data analyst role'), 'prepare_outreach')
        self.assertEqual(deterministic_action('Mark the data analyst application as applied'), 'update_application')


class RoleQueryGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_only_message_runs_the_search(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        agent = FakeCareerAgent()
        runtime = CareerGraphRuntime(checkpoint_path=Path(temporary.name) / 'graph.sqlite3',
                                     career_agent=agent, tools=FakeTools())
        response = await runtime.run(ChatRunRequest(thread_id='thread-role', turn_id='turn-1',
                                                    message='GenAI engineer fresher'))
        self.assertEqual(agent.calls, 1)
        self.assertEqual(agent.last_query, 'GenAI engineer fresher')
        self.assertEqual(response.recommendation_ids, ['a' * 64])


if __name__ == '__main__':
    unittest.main()
