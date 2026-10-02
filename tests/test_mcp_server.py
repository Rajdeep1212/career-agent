"""Q2d: the stdio MCP server speaks newline-delimited JSON-RPC 2.0 and exposes exactly three read-only tools."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.mcp import server, tools

ROOT = Path(__file__).resolve().parents[1]


def talk(*messages) -> list[dict]:
    stdin = io.BytesIO(b"".join(json.dumps(message).encode("utf-8") + b"\n" for message in messages))
    stdout = io.BytesIO()
    server.serve(stdin, stdout)
    return [json.loads(line) for line in stdout.getvalue().decode("utf-8").splitlines()]


def request(identity, method, params=None) -> dict:
    return {"jsonrpc": "2.0", "id": identity, "method": method, **({"params": params} if params is not None else {})}


class ProtocolTests(unittest.TestCase):
    def test_initialize_announces_tools_and_echoes_a_known_protocol_version(self):
        reply, = talk(request(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                                "clientInfo": {"name": "test", "version": "0"}}))
        self.assertEqual((reply["jsonrpc"], reply["id"]), ("2.0", 1))
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(reply["result"]["capabilities"], {"tools": {}})
        self.assertEqual(reply["result"]["serverInfo"]["name"], "career-agent")
        unknown, = talk(request(1, "initialize", {"protocolVersion": "1999-01-01"}))
        self.assertEqual(unknown["result"]["protocolVersion"], server.PROTOCOL_VERSIONS[0])

    def test_exactly_three_read_only_tools_are_listed(self):
        reply, = talk(request(2, "tools/list"))
        listed = reply["result"]["tools"]
        self.assertEqual([tool["name"] for tool in listed], ["search_jobs", "explain_fit", "list_applications"])
        for tool in listed:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
        self.assertNotIn("cv", json.dumps([tool["inputSchema"] for tool in listed]).casefold())

    def test_a_tool_call_returns_its_json_as_text(self):
        with patch.dict(tools.HANDLERS, {"list_applications": lambda **arguments: {"count": 0, "applications": []}}):
            reply, = talk(request(3, "tools/call", {"name": "list_applications", "arguments": {}}))
        content = reply["result"]["content"]
        self.assertEqual((len(content), content[0]["type"], reply["result"]["isError"]), (1, "text", False))
        self.assertEqual(json.loads(content[0]["text"]), {"count": 0, "applications": []})

    def test_a_tool_error_is_a_result_with_is_error_not_a_crash(self):
        reply, = talk(request(4, "tools/call", {"name": "delete_everything", "arguments": {}}))
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("Unknown tool", reply["result"]["content"][0]["text"])
        with patch.dict(tools.HANDLERS, {"list_applications": lambda **arguments: 1 / 0}):
            reply, = talk(request(5, "tools/call", {"name": "list_applications", "arguments": {}}))
        self.assertTrue(reply["result"]["isError"])
        self.assertNotIn("Traceback", reply["result"]["content"][0]["text"])

    def test_notifications_get_no_reply_and_unknown_methods_and_bad_json_get_errors(self):
        replies = talk({"jsonrpc": "2.0", "method": "notifications/initialized"}, request(6, "ping"), request(7, "resources/list"))
        self.assertEqual([reply["id"] for reply in replies], [6, 7])
        self.assertEqual(replies[0]["result"], {})
        self.assertEqual(replies[1]["error"]["code"], -32601)
        stdout = io.BytesIO()
        server.serve(io.BytesIO(b"this is not json\n\n" + json.dumps(request(8, "ping")).encode() + b"\n"), stdout)
        lines = [json.loads(line) for line in stdout.getvalue().decode().splitlines()]
        self.assertEqual((lines[0]["error"]["code"], lines[0]["id"], lines[1]["id"]), (-32700, None, 8))


class LauncherTests(unittest.TestCase):
    def test_the_launcher_answers_over_real_stdio_from_any_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = [request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
                        {"jsonrpc": "2.0", "method": "notifications/initialized"}, request(2, "tools/list"),
                        request(3, "tools/call", {"name": "search_jobs", "arguments": {"query": "AI Engineer jobs in India"}}),
                        request(4, "tools/call", {"name": "list_applications", "arguments": {}})]
            done = subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp_server.py")], cwd=directory, timeout=120,
                                  input=b"".join(json.dumps(message).encode() + b"\n" for message in messages), capture_output=True,
                                  env={**os.environ, "DATA_DIR": directory, "DEMO_MODE": "false"})
            self.assertEqual(done.returncode, 0, done.stderr.decode(errors="replace")[-800:])
            replies = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines()]
            self.assertEqual([reply["id"] for reply in replies], [1, 2, 3, 4])      # stdout holds protocol messages only
            self.assertEqual(len(replies[1]["result"]["tools"]), 3)
            empty = json.loads(replies[2]["result"]["content"][0]["text"])
            self.assertEqual((empty["matched"], empty["jobs"]), (0, []))
            self.assertEqual(json.loads(replies[3]["result"]["content"][0]["text"])["count"], 0)
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), [])     # the server created nothing


if __name__ == "__main__":
    unittest.main()
