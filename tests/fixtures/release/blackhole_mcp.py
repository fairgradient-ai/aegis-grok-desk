"""A stub MCP server for the release deny check. It does nothing, by design.

The release check (docs/RELEASE.md) asks a runtime to call this server's one tool, ``place_order``, and observes
the desk's hook refuse the call before it reaches the server: under the server name ``blackhole`` the tool is
``blackhole__place_order`` in Grok Build and ``mcp__blackhole__place_order`` in Claude Code. If a hook ever failed
open, the call would still change nothing: the server answers ``blackhole: nothing was done`` and has no other
behaviour.

The protocol is MCP over stdio: JSON-RPC 2.0, one message per line on stdin and stdout, UTF-8. ``initialize``
answers the client's protocol version when it is one of ``SUPPORTED`` and the newest otherwise, with only the
``tools`` capability; ``ping`` answers ``{}``; ``tools/list`` lists the one tool; ``tools/call`` of that tool, with
any arguments or none, answers the text above with ``isError`` false. Notifications (no ``id``) get no answer.
Errors use JSON-RPC's codes: -32700 for a line that is not JSON (``NaN`` and ``Infinity`` included) or is nested too
deep to decode, -32600 for anything but a request object (a batch included) and for an id that is not a string, a
finite number or null, -32601 for an unknown method, -32602 for an unknown or missing tool name. An error to a line
whose id cannot be trusted carries the id null. Every answer is strict JSON. End of input is a clean exit 0.

It imports only ``json``, ``sys`` and ``typing``: no network, no subprocess, no file is opened or written,
nothing goes to stderr. It lives under ``tests/fixtures/``, outside the repository's code and prose scans, and is never shipped as
a desk tool.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

SERVER_INFO = {"name": "blackhole", "version": "1"}
SUPPORTED = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
TOOL_NAME = "place_order"
RESULT_TEXT = "blackhole: nothing was done"
TOOL = {
    "name": TOOL_NAME,
    "description": ("This tool does nothing. It exists only to prove that the Aegis Desk hook can deny a call to it; "
                    "it contacts no one and changes nothing."),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
}


def not_json(token: str) -> Any:
    """``json.loads`` accepts ``NaN``, ``Infinity`` and ``-Infinity``; JSON does not."""
    raise ValueError("%s is not JSON" % token)


def valid_id(ident: Any) -> bool:
    """JSON-RPC's id: a string, a number or null. A number no double holds (``1e400``) is refused too."""
    if ident is None or isinstance(ident, str):
        return True
    if isinstance(ident, bool):
        return False
    if isinstance(ident, int):
        return True
    return isinstance(ident, float) and float("-inf") < ident < float("inf")


def error(ident: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def result(ident: Any, value: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "result": value}


def answer(message: Any) -> Optional[Dict[str, Any]]:
    """The response to one decoded message, or ``None`` for a notification."""
    if not isinstance(message, dict):
        return error(None, -32600, "Invalid Request: one JSON-RPC request object per line")
    if "id" not in message:
        return None
    ident = message["id"]
    if not valid_id(ident):
        return error(None, -32600, "Invalid Request: the id must be a string, a finite number or null")
    method = message.get("method")
    if not isinstance(method, str):
        return error(ident, -32600, "Invalid Request: no method")
    params = message.get("params")
    params = params if isinstance(params, dict) else {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED else SUPPORTED[-1]
        return result(ident, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                              "serverInfo": SERVER_INFO})
    if method == "ping":
        return result(ident, {})
    if method == "tools/list":
        return result(ident, {"tools": [TOOL]})
    if method == "tools/call":
        name = params.get("name")
        if name != TOOL_NAME:
            shown = json.dumps(name) if isinstance(name, str) else "no tool name given"
            return error(ident, -32602, "Unknown tool: %s" % shown)
        return result(ident, {"content": [{"type": "text", "text": RESULT_TEXT}], "isError": False})
    return error(ident, -32601, "Method not found: %s" % method)


def serve(stdin: Any, stdout: Any) -> int:
    """Answer each line of ``stdin`` (bytes) on ``stdout`` (bytes) until end of input."""
    while True:
        line = stdin.readline()
        if not line:
            return 0
        if not line.strip():
            continue
        try:
            message = json.loads(line.decode("utf-8"), parse_constant=not_json)
        except (ValueError, RecursionError):  # not UTF-8, not JSON, or nested deeper than the decoder holds
            response: Optional[Dict[str, Any]] = error(None, -32700, "Parse error")
        else:
            response = answer(message)
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n")
            stdout.flush()


if __name__ == "__main__":
    sys.exit(serve(sys.stdin.buffer, sys.stdout.buffer))
