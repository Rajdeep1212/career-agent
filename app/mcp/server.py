"""Stdio MCP server: newline-delimited JSON-RPC 2.0, three read-only tools (app/mcp/tools.py).

Started by scripts/mcp_server.py. Local only: it reads from stdin and writes protocol messages to
stdout, one JSON object per line; it opens no socket. Logs go to stderr. A tool failure is returned
as a result with isError true, never as a traceback.
"""
import json
import sys
from typing import BinaryIO

from app.mcp import tools

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]      # newest first
SERVER_INFO = {"name": "career-agent", "version": "0.1.0"}
_PARSE_ERROR, _INVALID_REQUEST, _METHOD_NOT_FOUND = -32700, -32600, -32601


def _log(text: str) -> None:
    print(f"career-agent mcp: {text}", file=sys.stderr, flush=True)


def _tool_result(params) -> dict:
    params = params if isinstance(params, dict) else {}
    name, arguments = params.get("name"), params.get("arguments")
    try:
        result = tools.call(str(name), arguments if isinstance(arguments, dict) else {})
    except Exception as exc:        # never a traceback on the wire
        _log(f"tool {name} failed: {type(exc).__name__}")
        result = {"error": f"The tool failed ({type(exc).__name__}); nothing was returned."}
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            "isError": isinstance(result, dict) and "error" in result}


def handle(message) -> dict | None:
    """The reply to one decoded message; None for a notification."""
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": _INVALID_REQUEST, "message": "Invalid request"}}
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params")
    if method == "initialize":
        wanted = params.get("protocolVersion") if isinstance(params, dict) else None
        result: dict = {"protocolVersion": wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                        "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools.TOOLS}
    elif method == "tools/call":
        result = _tool_result(params)
    else:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": _METHOD_NOT_FOUND, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def serve(stdin: BinaryIO, stdout: BinaryIO) -> None:
    """Answer messages from `stdin` on `stdout`, one JSON object per line, until `stdin` ends."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            reply: dict | None = {"jsonrpc": "2.0", "id": None, "error": {"code": _PARSE_ERROR, "message": "Parse error"}}
        else:
            reply = handle(message)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


def main() -> int:
    wire = sys.stdout.buffer
    sys.stdout = sys.stderr         # a stray print must never land between protocol messages
    _log("started (stdio, read-only)")
    serve(sys.stdin.buffer, wire)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
