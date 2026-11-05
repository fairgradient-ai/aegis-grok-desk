"""The release check's stub MCP server, ``tests/fixtures/release/blackhole_mcp.py``, spoken to as a client would.

The server is launched as a subprocess (stdio, newline-delimited JSON-RPC 2.0) and driven one message at a time:
``initialize``, the ``notifications/initialized`` notification, ``tools/list`` and ``tools/call``; each response
must arrive before the next request is sent, as a real client waits for it, and must be strict JSON (no ``NaN`` or
``Infinity``). It answers errors in JSON-RPC's own codes, also for a line nested too deep to parse and for an id that
is not a string, a finite number or null, exits 0 on end of input, writes nothing to stderr, and leaves its working
directory empty. Its source is
read by AST: it imports nothing but ``json``, ``sys`` and ``typing``, so it can open no connection, run no command
and write no file. The Claude Code ``--mcp-config`` file beside it points at it by a path relative to the repository root.
"""

from __future__ import annotations

import ast
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from typing import Any, Dict, List, Optional, Tuple

# isort: off
import tests._path  # noqa: F401
from tests.gates._trees import ROOT
# isort: on

RELEASE = os.path.join(ROOT, "tests", "fixtures", "release")
STUB_REL = "tests/fixtures/release/blackhole_mcp.py"
STUB = os.path.join(ROOT, *STUB_REL.split("/"))
CONFIG = os.path.join(RELEASE, "mcp-config.json")
README = os.path.join(RELEASE, "README.md")
TIMEOUT = 20
NOTHING_DONE = "blackhole: nothing was done"


class StubServer:
    """One running stub: send a message, read the next response line with a timeout."""

    def __init__(self, cwd: str) -> None:
        env = {"PYTHONDONTWRITEBYTECODE": "1"}
        for key in ("PATH", "SYSTEMROOT"):
            if key in os.environ:
                env[key] = os.environ[key]
        self.proc = subprocess.Popen([sys.executable, STUB], cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in iter(self.proc.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(None)

    def send(self, message: Any) -> None:
        self.send_raw(json.dumps(message).encode("utf-8") + b"\n")

    def send_raw(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def receive(self) -> Dict[str, Any]:
        line = self.lines.get(timeout=TIMEOUT)
        if line is None:
            raise AssertionError("the stub closed its output")
        return json.loads(line.decode("utf-8"), parse_constant=not_json)

    def request(self, ident: Any, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "id": ident, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        response = self.receive()
        if response.get("id") != ident:
            raise AssertionError("response to %r carries id %r: %r" % (ident, response.get("id"), response))
        return response

    def close(self) -> Tuple[int, bytes]:
        assert self.proc.stdin is not None and self.proc.stderr is not None
        self.proc.stdin.close()
        code = self.proc.wait(timeout=TIMEOUT)
        self.reader.join(timeout=TIMEOUT)
        leftover: List[bytes] = []
        while True:
            try:
                item = self.lines.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                leftover.append(item)
        if leftover:
            raise AssertionError("unexpected output after the last response: %r" % leftover)
        stderr = self.proc.stderr.read()
        self.proc.stdout.close()  # type: ignore[union-attr]
        self.proc.stderr.close()
        return code, stderr

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=TIMEOUT)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def not_json(token: str) -> Any:
    """``json.loads`` accepts ``NaN``, ``Infinity`` and ``-Infinity``; JSON does not, and neither does a client."""
    raise AssertionError("the stub wrote %s, which is not JSON" % token)


def initialize_params(version: str = "2025-06-18") -> Dict[str, Any]:
    return {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "aegis-test", "version": "1"}}


class StubProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-stub-mcp-")
        self.addCleanup(self._tmp.cleanup)
        self.cwd = os.path.realpath(self._tmp.name)
        self.server = StubServer(self.cwd)
        self.addCleanup(self.server.kill)

    def finish(self) -> None:
        code, stderr = self.server.close()
        self.assertEqual(0, code)
        self.assertEqual(b"", stderr)
        self.assertEqual([], os.listdir(self.cwd), "the stub wrote into its working directory")

    def test_the_handshake_list_and_call_a_client_makes(self) -> None:
        response = self.server.request(1, "initialize", initialize_params())
        result = response["result"]
        self.assertEqual("2.0", response["jsonrpc"])
        self.assertEqual("2025-06-18", result["protocolVersion"])
        self.assertIn("tools", result["capabilities"])
        self.assertEqual("blackhole", result["serverInfo"]["name"])
        self.server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        tools = self.server.request(2, "tools/list", {})["result"]["tools"]
        self.assertEqual(["place_order"], [tool["name"] for tool in tools])
        description = tools[0]["description"]
        self.assertIn("does nothing", description)
        self.assertIn("only to prove", description)
        self.assertIn("deny", description)
        self.assertEqual("object", tools[0]["inputSchema"]["type"])

        called = self.server.request(3, "tools/call", {"name": "place_order",
                                                       "arguments": {"symbol": "SPY", "quantity": 1}})
        self.assertEqual({"content": [{"type": "text", "text": NOTHING_DONE}], "isError": False}, called["result"])
        self.assertNotIn("error", called)
        self.finish()

    def test_a_call_without_arguments_does_nothing_too(self) -> None:
        self.server.request(1, "initialize", initialize_params())
        called = self.server.request("call-1", "tools/call", {"name": "place_order"})
        self.assertEqual(NOTHING_DONE, called["result"]["content"][0]["text"])
        self.finish()

    def test_protocol_versions(self) -> None:
        for version in ("2024-11-05", "2025-03-26", "2025-06-18"):
            self.assertEqual(version, self.server.request(version, "initialize",
                                                          initialize_params(version))["result"]["protocolVersion"])
        answered = self.server.request(9, "initialize", initialize_params("1999-01-01"))["result"]["protocolVersion"]
        self.assertIn(answered, ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"))
        self.finish()

    def test_ping(self) -> None:
        self.assertEqual({}, self.server.request(1, "ping")["result"])
        self.finish()

    def test_errors_use_json_rpc_codes(self) -> None:
        self.assertEqual(-32601, self.server.request(1, "resources/list", {})["error"]["code"])
        unknown = self.server.request(2, "tools/call", {"name": "something_else", "arguments": {}})
        self.assertEqual(-32602, unknown["error"]["code"])
        self.assertEqual(-32602, self.server.request(3, "tools/call", {"arguments": {}})["error"]["code"])
        self.server.send_raw(b"{not json\n")
        parse = self.server.receive()
        self.assertEqual((None, -32700), (parse["id"], parse["error"]["code"]))
        self.server.send([{"jsonrpc": "2.0", "id": 4, "method": "ping"}])
        self.assertEqual(-32600, self.server.receive()["error"]["code"])
        self.server.send({"jsonrpc": "2.0", "id": 5})
        invalid = self.server.receive()
        self.assertEqual((5, -32600), (invalid["id"], invalid["error"]["code"]))
        self.finish()

    def test_a_line_nested_too_deep_is_a_parse_error(self) -> None:
        # Valid-looking JSON the decoder cannot hold (it raises RecursionError, not ValueError): a parse error, and the
        # server goes on answering.
        for line in (b"[" * 200000 + b"\n", b'{"a":' * 100000 + b"1" + b"}" * 100000 + b"\n"):
            self.server.send_raw(line)
            parse = self.server.receive()
            self.assertEqual((None, -32700), (parse["id"], parse["error"]["code"]))
        self.assertEqual({}, self.server.request(1, "ping")["result"])
        self.finish()

    def test_an_id_that_is_not_a_string_a_finite_number_or_null_is_refused(self) -> None:
        # NaN and Infinity are not JSON at all; 1e400 is JSON whose value no double holds; an object, an array or a
        # boolean is not an id JSON-RPC allows. None of them is echoed back.
        for raw, code in ((b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}', -32700),
                          (b'{"jsonrpc":"2.0","id":-Infinity,"method":"ping"}', -32700),
                          (b'{"jsonrpc":"2.0","id":1e400,"method":"ping"}', -32600),
                          (b'{"jsonrpc":"2.0","id":{"a":1},"method":"ping"}', -32600),
                          (b'{"jsonrpc":"2.0","id":[1],"method":"ping"}', -32600),
                          (b'{"jsonrpc":"2.0","id":true,"method":"ping"}', -32600),
                          (b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}', -32700)):
            with self.subTest(raw=raw):
                self.server.send_raw(raw + b"\n")
                refused = self.server.receive()
                self.assertEqual((None, code), (refused["id"], refused["error"]["code"]))
                self.assertNotIn("result", refused)
        for ident in ("a", 0, -7, 2.5, None):
            self.assertEqual({}, self.server.request(ident, "ping")["result"])
        self.finish()

    def test_notifications_and_blank_lines_get_no_answer(self) -> None:
        self.server.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}})
        self.server.send({"jsonrpc": "2.0", "method": "an/unknown/notification"})
        self.server.send_raw(b"\n   \n")
        self.assertEqual({}, self.server.request(7, "ping")["result"])
        self.finish()

    def test_end_of_input_right_away_is_a_clean_exit(self) -> None:
        self.finish()


class StubSourceTests(unittest.TestCase):
    def tree(self) -> ast.Module:
        with open(STUB, encoding="utf-8") as handle:
            return ast.parse(handle.read(), filename=STUB)

    def test_imports_only_json_sys_and_typing(self) -> None:
        imported = set()
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertEqual({"__future__", "json", "sys", "typing"}, imported)

    def test_no_open_exec_or_dynamic_import(self) -> None:
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("open", "exec", "eval", "compile", "__import__", "getattr"))
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("system", "popen", "modules", "__dict__"))

    def test_python_floor_preamble(self) -> None:
        body = self.tree().body
        self.assertIsInstance(body[0], ast.Expr)
        self.assertIsInstance(body[0].value, ast.Constant)
        self.assertIsInstance(body[1], ast.ImportFrom)
        self.assertEqual("__future__", body[1].module)
        self.assertEqual(["annotations"], [alias.name for alias in body[1].names])


class ReleaseFixtureFilesTests(unittest.TestCase):
    def test_mcp_config_points_at_the_stub_from_the_repository_root(self) -> None:
        with open(CONFIG, encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertEqual(["blackhole"], sorted(config["mcpServers"]))
        server = config["mcpServers"]["blackhole"]
        self.assertEqual("stdio", server["type"])
        self.assertEqual("python3", server["command"])
        self.assertEqual([STUB_REL], server["args"])
        self.assertFalse(os.path.isabs(server["args"][0]))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, *server["args"][0].split("/"))))
        self.assertNotIn("env", server)

    def test_readme_says_what_the_fixtures_are_for(self) -> None:
        with open(README, encoding="utf-8") as handle:
            text = handle.read()
        for needle in ("blackhole_mcp.py", "mcp-config.json", "docs/RELEASE.md", "does nothing"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
