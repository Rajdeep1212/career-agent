"""Q2d: the development agents in .claude/agents/ are read-only; the main session is the only writer."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".claude" / "agents"
READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", path
    fields = {}
    for line in lines[1:lines.index("---", 1)]:
        name, _, value = line.partition(":")
        fields[name.strip()] = value.strip()
    return fields


class DevAgentTests(unittest.TestCase):
    def test_the_three_agents_exist_and_are_named_after_their_files(self):
        self.assertEqual(sorted(path.stem for path in AGENTS.glob("*.md")), ["researcher", "reviewer", "test-writer"])
        for path in AGENTS.glob("*.md"):
            fields = frontmatter(path)
            self.assertEqual(fields["name"], path.stem)
            self.assertTrue(fields["description"])

    def test_every_agent_has_an_explicit_read_only_tool_list(self):
        for path in AGENTS.glob("*.md"):
            tools = {tool.strip() for tool in frontmatter(path).get("tools", "").split(",") if tool.strip()}
            self.assertTrue(tools, f"{path.name} has no tools line, so it would inherit every tool")
            self.assertLessEqual(tools, READ_ONLY_TOOLS, f"{path.name} may only use {sorted(READ_ONLY_TOOLS)}")

    def test_the_runbook_says_when_to_use_them(self):
        runbook = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
        line = next((line for line in runbook.splitlines() if line.strip().startswith("- Dev agents (`.claude/agents/`)")), "")
        for name in ("reviewer", "test-writer", "researcher", "read-only", "only writer"):
            self.assertIn(name, line)


if __name__ == "__main__":
    unittest.main()
