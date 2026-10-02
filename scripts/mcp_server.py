"""Start the read-only Career Agent MCP server on stdio (docs/ROADMAP_QUEUE.md Q2d).

Claude Desktop starts this file with the job-agent Python. In claude_desktop_config.json
(Claude Desktop: Settings > Developer > Edit Config), with your own paths:

    {"mcpServers": {"career-agent": {
        "command": "C:\\Users\\<you>\\anaconda3\\envs\\job-agent\\python.exe",
        "args": ["C:\\path\\to\\repo\\scripts\\mcp_server.py"]}}}

It works from any working directory: the repository root is put on sys.path and made the working
directory, so `.env` and the data directory resolve as they do for the app.

Tools: search_jobs, explain_fit, list_applications. All read-only; none returns CV text.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.mcp.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
