"""Fixtures for scripts/check_no_write_paths.py: the AST no-write gate and the prose scanner.

Every rule is watched failing on a minimal module or prose line, and the legitimate shapes (a
GET-only client, a loopback test server, a documented and allow-listed counter-example) are
watched passing, so the gate is neither porous nor noisy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from typing import Dict, List

# isort: off
import tests._path  # noqa: F401
import check_no_write_paths
from tests.gates._trees import fixture_tree, read, write
# isort: on

CLIENT = "scripts/aegis_desk/alpaca_client.py"
OTHER = "scripts/aegis_desk/quote_cache.py"
TEST_MODULE = "tests/smoke/test_loopback.py"
GATE_TEST = "tests/gates/test_probe.py"
HARNESS = "scripts/cli_journeys.py"
TEMPLATE_GATE = "scripts/check_grok_template.py"
ROUTINE = "scripts/routine.py"
DOCTOR = "scripts/desk_doctor.py"
TOOL_TEST = "tests/tools/test_probe_tool.py"
FUTURE = "from __future__ import annotations\n"


def gate_rules(rel: str, source: str) -> List[str]:
    """Sorted rule ids the gate reports for one module written into a copy of the good tree."""
    with fixture_tree() as root:
        write(root, rel, FUTURE + source)
        result = check_no_write_paths.run(root)
    return sorted(rules_by_file(result).get(rel, []))


def rules_by_file(result: dict) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {}
    for finding in result["errors"]:
        found.setdefault(finding["file"], []).append(finding["rule"])
    return found


class NoWritePathsFixtureTests(unittest.TestCase):
    def test_good_tree_passes_with_the_allow_list_used(self) -> None:
        with fixture_tree() as root:
            result = check_no_write_paths.run(root)
        self.assertTrue(result["ok"], json.dumps(result["errors"], indent=2))
        self.assertEqual(1, result["scanned"]["allowlist_used"])
        self.assertGreaterEqual(result["scanned"]["python_files"], 3)
        self.assertGreaterEqual(result["scanned"]["prose_files"], 5)

    def assert_case(self, case: str, rel: str, *rules: str) -> dict:
        with fixture_tree(case) as root:
            result = check_no_write_paths.run(root)
        self.assertFalse(result["ok"])
        found = rules_by_file(result)
        self.assertIn(rel, found, json.dumps(result["errors"], indent=2))
        for rule in rules:
            self.assertIn(rule, found[rel], found[rel])
        for finding in result["errors"]:
            self.assertGreaterEqual(finding["line"], 1, finding)
        return result

    def test_urlopen_with_data_fails(self) -> None:
        result = self.assert_case("urlopen_data", CLIENT, "urlopen-data")
        self.assertEqual([CLIENT], sorted(rules_by_file(result)))

    def test_request_with_post_method_fails(self) -> None:
        self.assert_case("request_post", CLIENT, "request-method")

    def test_second_module_importing_urllib_request_fails(self) -> None:
        self.assert_case("network_import", OTHER, "network-import")

    def test_body_through_the_data_backing_field_fails(self) -> None:
        # ``request._data = b"..."`` bypasses the ``data`` property the mutation rule used to watch;
        # ``get_method`` reads ``_data`` and would send a POST with that body.
        result = self.assert_case("request_data_attribute", CLIENT, "request-mutation")
        self.assertEqual([CLIENT], sorted(rules_by_file(result)))

    def test_request_subclass_with_a_class_body_verb_fails(self) -> None:
        # ``class _PostRequest(Request): method = "POST"`` instantiated instead of Request: the
        # class body is never a call, so only the subclass rule can see the verb.
        result = self.assert_case("request_subclass", CLIENT, "request-subclass")
        self.assertEqual([CLIENT], sorted(rules_by_file(result)))

    def test_subprocess_curl_fails_twice(self) -> None:
        self.assert_case("subprocess_curl", OTHER, "subprocess-import", "subprocess-curl")

    def test_third_party_http_client_fails(self) -> None:
        self.assert_case("third_party_http", OTHER, "third-party-http")

    def test_prose_curl_post_fails_with_file_and_line(self) -> None:
        result = self.assert_case("prose_curl", "docs/FAQ.md", "curl-write", "orders-endpoint", "curl-body")
        lines = {f["line"] for f in result["errors"] if f["file"] == "docs/FAQ.md"}
        self.assertEqual({3}, lines)

    def test_stale_allow_list_entry_fails(self) -> None:
        result = self.assert_case("stale_allowlist", "scripts/no_write_allowlist.txt", "allowlist-stale")
        self.assertEqual(1, result["scanned"]["allowlist_used"])

    def test_cli_reports_file_line_and_exits_one(self) -> None:
        with fixture_tree("prose_curl") as root:
            proc = subprocess.run([sys.executable, check_no_write_paths.__file__, root],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(1, proc.returncode)
        self.assertIn("docs/FAQ.md:3: [curl-write]", proc.stdout)
        self.assertIn("problem(s)", proc.stdout)

    def test_cli_json_mode_on_good_tree(self) -> None:
        with fixture_tree() as root:
            proc = subprocess.run([sys.executable, check_no_write_paths.__file__, root, "--json"],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])


class AstRuleTests(unittest.TestCase):
    """One minimal module per rule, written into a copy of the good tree."""

    def rules_for(self, rel: str, source: str) -> List[str]:
        with fixture_tree() as root:
            write(root, rel, source)
            result = check_no_write_paths.run(root)
        return sorted(rules_by_file(result).get(rel, []))

    def test_get_request_shapes_pass(self) -> None:
        source = FUTURE + (
            "import urllib.request\n"
            "from urllib.request import Request as R, urlopen\n"
            "def a(u):\n    return R(u, None, {}, method=\"GET\")\n"
            "def b(u):\n    return urllib.request.Request(u, headers={})\n"
            "def c(u):\n    return urlopen(u, None, 5)\n"
            "def d(u):\n    return urllib.request.urlopen(R(u), timeout=5)\n"
        )
        self.assertEqual([], self.rules_for(CLIENT, source))

    def test_positional_body_fails(self) -> None:
        source = FUTURE + "import urllib.request\ndef a(u, body):\n    return urllib.request.Request(u, body)\n"
        self.assertEqual(["request-data"], self.rules_for(CLIENT, source))

    def test_non_literal_method_fails(self) -> None:
        source = FUTURE + ("from urllib.request import Request\nMETHOD = \"GET\"\n"
                           "def a(u):\n    return Request(u, method=METHOD)\n")
        self.assertEqual(["request-method"], self.rules_for(CLIENT, source))

    def test_lowercase_get_literal_fails(self) -> None:
        source = FUTURE + "import urllib.request\ndef a(u):\n    return urllib.request.Request(u, method=\"get\")\n"
        self.assertEqual(["request-method"], self.rules_for(CLIENT, source))

    def test_splats_fail(self) -> None:
        source = FUTURE + ("import urllib.request\n"
                           "def a(u, kw):\n    return urllib.request.Request(u, **kw)\n"
                           "def b(args):\n    return urllib.request.urlopen(*args)\n")
        self.assertEqual(["request-splat", "urlopen-splat"], self.rules_for(CLIENT, source))

    def test_urlopen_positional_data_fails(self) -> None:
        source = FUTURE + "import urllib.request as ur\ndef a(u):\n    return ur.urlopen(u, b\"x\")\n"
        self.assertEqual(["urlopen-data"], self.rules_for(CLIENT, source))

    def test_opener_open_with_data_fails(self) -> None:
        source = FUTURE + ("import urllib.request\n"
                           "def a(u):\n    opener = urllib.request.build_opener()\n"
                           "    return opener.open(u, data=b\"x\")\n")
        self.assertEqual(["urlopen-data"], self.rules_for(CLIENT, source))

    def test_request_mutation_fails(self) -> None:
        source = FUTURE + ("import urllib.request\n"
                           "def a(u):\n    r = urllib.request.Request(u)\n    r.method = \"POST\"\n"
                           "    r.data = b\"x\"\n    setattr(r, \"data\", b\"y\")\n    r.add_data(b\"z\")\n    return r\n")
        self.assertEqual(["request-mutation"] * 4, self.rules_for(CLIENT, source))

    def test_get_method_override_fails(self) -> None:
        source = FUTURE + ("import urllib.request\n"
                           "class Sneaky(urllib.request.Request):\n"
                           "    def get_method(self):\n        return \"POST\"\n")
        self.assertEqual(["get-method-override", "request-subclass"], self.rules_for(CLIENT, source))

    def test_data_backing_field_and_set_proxy_fail(self) -> None:
        source = FUTURE + ("import urllib.request\n"
                           "def a(u):\n    r = urllib.request.Request(u, headers={})\n"
                           "    r._data = b\"symbols=SPY\"\n    setattr(r, \"_data\", b\"x\")\n"
                           "    r.set_proxy(\"127.0.0.1:9\", \"http\")\n    return r\n")
        self.assertEqual(["request-mutation"] * 3, self.rules_for(CLIENT, source))
        self.assertEqual([], self.rules_for(CLIENT, FUTURE + "import urllib.request\ndef a(r):\n    r._data = None\n"))

    def test_target_fields_may_not_be_reassigned_in_the_network_module(self) -> None:
        for attr in ("full_url", "_full_url", "selector", "host", "type", "origin_req_host", "_tunnel_host"):
            source = FUTURE + f"import urllib.request\ndef a(r, v):\n    r.{attr} = v\n"
            self.assertEqual(["request-mutation"], self.rules_for(CLIENT, source), attr)
        self.assertEqual(["request-mutation"], self.rules_for(CLIENT, (
            FUTURE + "import urllib.request\ndef a(r, v):\n    setattr(r, \"host\", v)\n")))
        # Outside the network module the same names are ordinary attributes (a Quote has a type).
        self.assertEqual([], self.rules_for(OTHER, FUTURE + "def a(q, v):\n    q.type = v\n    q.host = v\n"))

    def test_request_subclass_fails_and_is_checked_as_a_request(self) -> None:
        source = FUTURE + ("import urllib.request\n"
                           "class P(urllib.request.Request):\n    method = \"POST\"\n"
                           "def a(u):\n    return P(u, headers={})\n")
        self.assertEqual(["request-subclass"], self.rules_for(CLIENT, source))
        source = FUTURE + ("import urllib.request\n"
                           "class S(urllib.request.Request):\n"
                           "    def __init__(self, url, body):\n        super().__init__(url, body)\n")
        self.assertEqual(["request-subclass"], self.rules_for(CLIENT, source))
        source = FUTURE + ("import urllib.request\nR = urllib.request.Request\n"
                           "class A(R):\n    pass\n"
                           "class B(A):\n    pass\n"
                           "def a(u):\n    return B(u, data=b\"x\")\n")
        self.assertEqual(["request-data", "request-subclass", "request-subclass"], self.rules_for(CLIENT, source))
        # The redirect handler the real client subclasses is not a Request.
        source = FUTURE + "import urllib.request\nclass H(urllib.request.HTTPRedirectHandler):\n    pass\n"
        self.assertEqual([], self.rules_for(CLIENT, source))

    def test_http_client_rules(self) -> None:
        source = FUTURE + ("import http.client\n"
                           "def a(host):\n    c = http.client.HTTPSConnection(host)\n"
                           "    c.request(\"GET\", \"/v2/clock\")\n"
                           "    c.request(\"POST\", \"/v1/accounts/1/orders\")\n"
                           "    c.request(\"GET\", \"/x\", b\"body\")\n"
                           "    c.putrequest(\"DELETE\", \"/x\")\n"
                           "    c.endheaders(b\"body\")\n"
                           "    c.send(b\"raw\")\n")
        self.assertEqual(["http-body", "http-body", "http-method", "http-method", "http-send"],
                         self.rules_for(CLIENT, source))

    def test_bare_parent_imports_are_ambiguous(self) -> None:
        source = FUTURE + "import urllib\nimport http\n"
        self.assertEqual(["ambiguous-import", "ambiguous-import"], self.rules_for(CLIENT, source))

    def test_urllib_parse_and_error_are_fine_everywhere(self) -> None:
        source = FUTURE + "import urllib.parse\nimport urllib.error\nfrom urllib.parse import urlencode\n"
        self.assertEqual([], self.rules_for(OTHER, source))

    def test_from_urllib_import_request_in_another_module_fails(self) -> None:
        source = FUTURE + "from urllib import request\n"
        self.assertEqual(["network-import"], self.rules_for(OTHER, source))

    def test_socket_and_ssl_outside_the_client_fail(self) -> None:
        source = FUTURE + "import socket\nimport ssl\n"
        self.assertEqual(["network-import", "network-import"], self.rules_for(OTHER, source))

    def test_dynamic_imports(self) -> None:
        source = FUTURE + ("import importlib\n"
                           "a = __import__(\"socket\")\n"
                           "b = importlib.import_module(\"urllib.request\")\n"
                           "def c(name):\n    return importlib.import_module(name)\n"
                           "d = importlib.import_module(\"json\")\n")
        self.assertEqual(["dynamic-import", "network-import", "network-import"], self.rules_for(OTHER, source))

    def test_shell_escapes_fail_everywhere(self) -> None:
        source = FUTURE + ("import os\nimport pty\n"
                           "os.system(\"ls\")\nos.popen(\"ls\")\nos.execvp(\"ls\", [\"ls\"])\npty.spawn(\"sh\")\n")
        self.assertEqual(["shell-escape"] * 4, self.rules_for(OTHER, source))

    def test_from_os_import_system_fails(self) -> None:
        source = FUTURE + "from os import system\n"
        self.assertEqual(["shell-escape"], self.rules_for(OTHER, source))

    def test_subprocess_shell_true_and_wget_fail_in_an_allowed_module(self) -> None:
        source = FUTURE + ("import subprocess\n"
                           "subprocess.run(\"ls\", shell=True)\n"
                           "subprocess.check_output([\"wget\", \"-O-\", \"https://x\"])\n"
                           "subprocess.run([\"git\", \"status\"], check=False)\n")
        self.assertEqual(["subprocess-curl", "subprocess-shell"], self.rules_for("scripts/cli_journeys.py", source))

    def test_curl_inside_an_f_string_fails(self) -> None:
        source = FUTURE + "import subprocess\ndef a(u):\n    subprocess.run([f\"curl {u}\"])\n"
        self.assertEqual(["subprocess-curl"], self.rules_for("scripts/check_grok_template.py", source))

    def test_raw_socket_writes_fail(self) -> None:
        source = FUTURE + "import socket\ndef a(s):\n    s.sendall(b\"x\")\n    s.sendto(b\"x\", (\"h\", 1))\n    s.send(b\"x\")\n"
        self.assertEqual(["socket-write"] * 3, self.rules_for(CLIENT, source))

    def test_third_party_clients_fail(self) -> None:
        for module in ("httpx", "aiohttp", "urllib3", "pycurl", "websocket"):
            self.assertEqual(["third-party-http"], self.rules_for(OTHER, FUTURE + f"import {module}\n"), module)

    def test_star_import_fails(self) -> None:
        self.assertEqual(["star-import"], self.rules_for(OTHER, FUTURE + "from json import *\n"))

    def test_unparseable_module_is_reported(self) -> None:
        self.assertEqual(["syntax"], self.rules_for(OTHER, FUTURE + "def broken(:\n"))

    def test_loopback_test_server_passes(self) -> None:
        source = FUTURE + ("import http.server\nimport socket\nimport threading\nimport unittest\n"
                           "class Handler(http.server.BaseHTTPRequestHandler):\n"
                           "    def do_GET(self):\n        self.send_response(200)\n        self.end_headers()\n"
                           "        self.wfile.write(b\"{}\")\n"
                           "def serve():\n    server = http.server.ThreadingHTTPServer((\"127.0.0.1\", 0), Handler)\n"
                           "    threading.Thread(target=server.serve_forever, daemon=True).start()\n    return server\n")
        self.assertEqual([], self.rules_for(TEST_MODULE, source))

    def test_tests_may_not_import_urllib_request_or_http_client(self) -> None:
        source = FUTURE + "import urllib.request\nimport http.client\nimport ssl\n"
        self.assertEqual(["network-import"] * 3, self.rules_for(TEST_MODULE, source))

    def test_http_server_outside_tests_fails(self) -> None:
        self.assertEqual(["network-import"], self.rules_for(OTHER, FUTURE + "import http.server\n"))

    def test_fixture_trees_are_not_scanned(self) -> None:
        source = FUTURE + "import requests\nimport subprocess\nsubprocess.run([\"curl\", \"-X\", \"POST\", \"x\"])\n"
        self.assertEqual([], self.rules_for("tests/fixtures/anything/bad.py", source))


class AliasAndIndirectionTests(unittest.TestCase):
    """Review probes against the network module: aliases, re-initialisation, namespace writes and
    call-valued callees must all be findings; the good client shape stays clean."""

    def test_assignment_alias_is_followed(self) -> None:
        self.assertEqual(["request-data"], gate_rules(CLIENT, (
            "import urllib.request\nR = urllib.request.Request\n"
            "def a(u):\n    return R(u, data=b\"body\")\n")))

    def test_alias_chain_is_followed_to_a_fixed_point(self) -> None:
        self.assertEqual(["request-method"], gate_rules(CLIENT, (
            "import urllib.request\nA = urllib.request\nB = A.Request\n"
            "def a(u):\n    return B(u, method=\"POST\")\n")))

    def test_aliased_module_urlopen_is_checked(self) -> None:
        self.assertEqual(["urlopen-data"], gate_rules(CLIENT, (
            "import urllib.request\nur = urllib.request\ndef a(u):\n    return ur.urlopen(u, b\"x\")\n")))

    def test_get_method_instance_override_fails(self) -> None:
        self.assertEqual(["request-mutation"], gate_rules(CLIENT, (
            "import urllib.request\ndef a(u):\n    req = urllib.request.Request(u)\n"
            "    req.get_method = lambda: \"POST\"\n    return req\n")))

    def test_request_reinitialisation_fails(self) -> None:
        self.assertEqual(["request-mutation"], gate_rules(CLIENT, (
            "import urllib.request\ndef a(req, u):\n    urllib.request.Request.__init__(req, u, b\"body\")\n")))

    def test_dunder_dict_write_fails(self) -> None:
        self.assertEqual(["reflection"], gate_rules(CLIENT, (
            "import urllib.request\ndef a(req):\n    req.__dict__[\"method\"] = \"DELETE\"\n")))

    def test_class_swap_fails(self) -> None:
        self.assertEqual(["reflection", "request-mutation", "request-subclass"], gate_rules(CLIENT, (
            "import urllib.request\nclass S(urllib.request.Request):\n    pass\n"
            "def a(req):\n    req.__class__ = S\n")))

    def test_call_valued_callee_fails_in_the_network_module(self) -> None:
        self.assertEqual(["indirect-call"], gate_rules(CLIENT, (
            "import urllib.request\ndef a(req, u):\n    return type(req)(u, data=b\"x\")\n")))

    def test_unnamed_callee_with_a_body_fails_everywhere(self) -> None:
        self.assertEqual(["indirect-call"], gate_rules(OTHER, "def a(f, u):\n    return f()(u, data=b\"x\")\n"))
        self.assertEqual(["indirect-call"], gate_rules(OTHER, "def a(table, u):\n    return table[u](b\"x\")\n"))
        self.assertEqual([], gate_rules(OTHER, "def a(f):\n    return f()(1)\n"))

    def test_getattr_resolution_reaches_urlopen(self) -> None:
        self.assertEqual(["urlopen-data"], gate_rules(CLIENT, (
            "import urllib.request\ndef a(u):\n    return getattr(urllib.request, \"urlopen\")(u, b\"x\")\n")))

    def test_non_literal_setattr_and_vars_fail_in_the_network_module(self) -> None:
        self.assertEqual(["reflection"], gate_rules(CLIENT, (
            "import urllib.request\ndef a(req, name, body):\n    setattr(req, name, body)\n")))
        self.assertEqual(["reflection"], gate_rules(CLIENT, (
            "import urllib.request\ndef a(req):\n    return vars(req)\n")))
        self.assertEqual(["reflection"], gate_rules(CLIENT, (
            "import urllib.request\ndef a(req, name):\n    return getattr(req, name)\n")))

    def test_private_http_client_write_path_fails(self) -> None:
        self.assertEqual(["http-send"], gate_rules(CLIENT, (
            "import http.client\ndef a(c):\n    c._send_request(\"POST\", \"/x\", b\"b\", {}, False)\n")))
        self.assertEqual(["http-send", "http-send"], gate_rules(CLIENT, (
            "import http.client\ndef a(c):\n    c._output(b\"x\")\n    c._send_output()\n")))

    def test_sendmsg_sendfile_and_makefile_fail(self) -> None:
        self.assertEqual(["indirect-call", "socket-write", "socket-write"], gate_rules(CLIENT, (
            "import socket\ndef a(s):\n    s.sendmsg([b\"x\"])\n    s.makefile(\"wb\").write(b\"x\")\n")))
        self.assertEqual(["socket-write"], gate_rules(CLIENT, "import socket\ndef a(s, f):\n    s.sendfile(f)\n"))
        self.assertEqual(["socket-write"], gate_rules(TEST_MODULE, "import socket\ndef a(s):\n    s.sendmsg([b\"x\"])\n"))

    def test_partial_over_a_network_callable_fails(self) -> None:
        self.assertEqual(["indirect-call"], gate_rules(CLIENT, (
            "import functools\nimport urllib.request\n"
            "def a(u):\n    return functools.partial(urllib.request.urlopen, u, b\"x\")()\n")))
        # PR 2d: the bare reference to os.system is itself a shell-escape finding (it could ride a run= seam).
        self.assertEqual(["indirect-call", "shell-escape"], gate_rules(OTHER, (
            "import functools\nimport os\nrun = functools.partial(os.system, \"ls\")\n")))
        self.assertEqual([], gate_rules(OTHER, (
            "import functools\nimport json\ndumps = functools.partial(json.dumps, indent=2)\n")))

    def test_operator_factories(self) -> None:
        self.assertEqual(["indirect-call"], gate_rules(OTHER, "import operator\nf = operator.methodcaller(\"sendall\", b\"x\")\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import operator\ndef a(n):\n    return operator.attrgetter(n)\n"))
        self.assertEqual([], gate_rules(OTHER, "import operator\nf = operator.methodcaller(\"upper\")\n"))
        self.assertEqual(["indirect-call"], gate_rules(CLIENT, (
            "import operator\nimport urllib.request\nf = operator.methodcaller(\"upper\")\n")))

    def test_good_client_shape_still_passes(self) -> None:
        with fixture_tree() as root:
            self.assertEqual([], rules_by_file(check_no_write_paths.run(root)).get(CLIENT, []))

    def test_string_form_mock_patch_is_the_test_idiom(self) -> None:
        # Tests may not import http.client, so the loopback test that asserts method == "GET"
        # patches the connection by dotted name; the gate reads the string as data, not code.
        self.assertEqual([], gate_rules(TEST_MODULE, (
            "import unittest\nfrom unittest import mock\n"
            "class T(unittest.TestCase):\n    def test_get_only(self) -> None:\n"
            "        with mock.patch(\"http.client.HTTPConnection.request\") as request:\n"
            "            self.assertIsNotNone(request)\n")))


class StdlibBypassTests(unittest.TestCase):
    """Standard-library modules and calls that reach the network, a shell or code execution
    without touching the four network modules or subprocess."""

    def test_network_capable_modules_fail_everywhere(self) -> None:
        for module in ("xmlrpc.client", "ftplib", "smtplib", "imaplib", "poplib", "nntplib", "telnetlib",
                       "multiprocessing.connection", "webbrowser", "antigravity", "asyncio",
                       "urllib.robotparser", "logging.handlers", "logging.config", "wsgiref.simple_server",
                       "pip", "ensurepip"):
            for rel in (CLIENT, OTHER, TEST_MODULE):
                self.assertEqual(["network-import"], gate_rules(rel, f"import {module}\n"), (module, rel))
        self.assertEqual(["network-import"], gate_rules(OTHER, "from asyncio import open_connection\n"))
        self.assertEqual([], gate_rules(OTHER, "import logging\nimport multiprocessing\nimport urllib.parse\n"))

    def test_network_call_names_fail_on_any_object(self) -> None:
        self.assertEqual(["network-call"], gate_rules(OTHER, "def a(loop, h):\n    return loop.create_connection(h, 443)\n"))
        self.assertEqual(["network-call"], gate_rules(OTHER, "async def a(loop):\n    return await loop.subprocess_exec(None, \"curl\")\n"))

    def test_dynamic_exec_modules_fail_everywhere(self) -> None:
        for module in ("code", "codeop", "runpy", "marshal", "pickle", "shelve", "timeit", "cProfile", "profile",
                       "pdb", "ctypes", "cffi", "zipimport", "importlib.machinery"):
            for rel in (OTHER, TEST_MODULE):
                self.assertEqual(["dynamic-exec"], gate_rules(rel, f"import {module}\n"), (module, rel))
        self.assertEqual([], gate_rules(OTHER, "import codecs\nimport importlib\nimport profilehooks_not_stdlib\n"))

    def test_dynamic_exec_calls_fail_everywhere(self) -> None:
        self.assertEqual(["dynamic-exec"], gate_rules(OTHER, "exec(\"import os\")\n"))
        self.assertEqual(["dynamic-exec"], gate_rules(TEST_MODULE, "x = eval(\"1\")\n"))
        self.assertEqual(["dynamic-exec"], gate_rules(OTHER, "c = compile(\"x\", \"f\", \"exec\")\n"))
        self.assertEqual(["dynamic-exec"], gate_rules(OTHER, "import builtins\nbuiltins.exec(\"x\")\n"))
        self.assertEqual(["dynamic-exec"], gate_rules(OTHER, (
            "import importlib.util\ns = importlib.util.spec_from_file_location(\"m\", \"/tmp/m.py\")\n")))
        # pkgutil loads modules by itself (get_loader, find_loader): its import fails too, and so does resolve_name
        self.assertEqual(["dynamic-exec", "dynamic-exec"],
                         gate_rules(OTHER, "import pkgutil\nf = pkgutil.resolve_name(\"os:system\")\n"))
        self.assertEqual([], gate_rules(OTHER, "import re\np = re.compile(\"x\")\n"))

    def test_review_fixtures_fail(self) -> None:
        for case, rules in (("xmlrpc_import", ["network-call", "network-import"]),
                            ("asyncio_streams", ["network-call", "network-call", "network-import"]),
                            ("exec_string", ["dynamic-exec"]),
                            ("ctypes_import", ["dynamic-exec", "dynamic-exec", "indirect-call"])):
            with fixture_tree(case) as root:
                result = check_no_write_paths.run(root)
            found = rules_by_file(result)
            self.assertEqual([OTHER], sorted(found), case)
            self.assertEqual(rules, sorted(found[OTHER]), case)


class ReflectionTests(unittest.TestCase):
    """Literal reflection is resolved to the name it reaches; non-literal reflection on a module
    or imported object is a finding; non-literal reflection on local objects stays legal."""

    def test_sys_modules_laundering_is_resolved(self) -> None:
        self.assertEqual(["network-call", "urlopen-data"], gate_rules(OTHER, (
            "import sys\ndef a(u):\n    return sys.modules[\"urllib.request\"].urlopen(u, b\"x\")\n")))
        self.assertEqual(["shell-escape"], gate_rules(OTHER, "import sys\ndef a():\n    return sys.modules.get(\"os\").system(\"curl\")\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import sys\ndef a(name):\n    return sys.modules[name]\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import sys\ndef a(name):\n    return sys.modules.get(name)\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import sys\ndef a(m):\n    sys.modules[\"os\"] = m\n"))

    def test_namespace_dictionary_laundering_is_resolved(self) -> None:
        self.assertEqual(["shell-escape"], gate_rules(OTHER, "import os\ndef a():\n    return globals()[\"os\"].system(\"ls\")\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "def a(k):\n    return globals()[k]\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import os\ndef a(f):\n    vars(os)[\"system\"] = f\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import os\nd = vars(os)\n"))
        self.assertEqual([], gate_rules(OTHER, "def a(args):\n    return vars(args)\n"))

    def test_dynamic_import_results_are_resolved(self) -> None:
        self.assertEqual(["shell-escape"], gate_rules(OTHER, "import importlib\ndef a():\n    return importlib.import_module(\"os\").system(\"ls\")\n"))
        self.assertEqual(["shell-escape"], gate_rules(OTHER, "m = __import__(\"os\")\ndef a():\n    return m.system(\"ls\")\n"))
        self.assertEqual([], gate_rules(OTHER, "import importlib\ndef t():\n    return importlib.import_module(\"tomllib\")\n"))

    def test_third_party_clients_reached_by_reflection_fail(self) -> None:
        self.assertEqual(["third-party-http"], gate_rules(OTHER, (
            "import sys\ndef a(u):\n    return sys.modules[\"requests\"].post(u)\n")))
        self.assertEqual(["third-party-http"], gate_rules(OTHER, (
            "import sys\ndef a(u):\n    return sys.modules.get(\"httpx\").post(u)\n")))
        self.assertEqual(["third-party-http"], gate_rules(OTHER, (
            "import importlib\ndef a(u):\n    return importlib.import_module(\"urllib3\").request(\"POST\", u)\n")))
        self.assertEqual(["indirect-call", "third-party-http"], gate_rules(OTHER, (
            "import functools\nimport requests\nsend = functools.partial(requests.post, \"https://x\")\n")))

    def test_getattr_on_modules(self) -> None:
        self.assertEqual(["shell-escape"], gate_rules(OTHER, "import os\ndef a():\n    return getattr(os, \"system\")(\"ls\")\n"))
        self.assertEqual(["shell-escape"], gate_rules(OTHER, "import os\ndef a():\n    return getattr(os, \"sys\" + \"tem\")(\"ls\")\n"))
        self.assertEqual(["shell-escape"], gate_rules(OTHER, "import os\ng = getattr\ndef a():\n    return g(os, \"system\")(\"ls\")\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import os\ndef a(n):\n    return getattr(os, n)(\"ls\")\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import os\ndef a(f):\n    setattr(os, \"system\", f)\n"))
        self.assertEqual(["reflection"], gate_rules(OTHER, "import json\ndef a(n):\n    return getattr(json, n)\n"))
        # Local objects keep dynamic attribute access: the golden tests read Greeks by name.
        self.assertEqual([], gate_rules(TEST_MODULE, "def a(greeks, name):\n    return getattr(greeks, name)\n"))
        self.assertEqual([], gate_rules(OTHER, "def a(obj, name, value):\n    setattr(obj, name, value)\n"))

    def test_reflection_attributes_fail_everywhere(self) -> None:
        for attr in ("__dict__", "__getattribute__", "__setattr__", "__subclasses__", "__globals__", "__builtins__", "__code__"):
            self.assertEqual(["reflection"], gate_rules(OTHER, f"def a(x):\n    return x.{attr}\n"), attr)
        self.assertEqual([], gate_rules(OTHER, "def a(x):\n    return type(x).__name__ + x.__class__.__name__\n"))


class SubprocessScopeTests(unittest.TestCase):
    def test_subprocess_is_allowed_under_tests_gates_only(self) -> None:
        self.assertEqual(["subprocess-import"], gate_rules(TEST_MODULE, "import subprocess\n"))
        self.assertEqual(["subprocess-import"], gate_rules("tests/engine/test_x.py", "from subprocess import run\n"))
        self.assertEqual([], gate_rules(GATE_TEST, "import subprocess\nimport sys\nsubprocess.run([sys.executable, \"-m\", \"unittest\"], check=False)\n"))

    def test_code_passed_as_a_string_is_caught(self) -> None:
        self.assertEqual(["subprocess-code", "subprocess-import"], gate_rules(TEST_MODULE, (
            "import subprocess\nimport sys\n"
            "subprocess.run([sys.executable, \"-c\", \"import urllib.request; urllib.request.urlopen('x', b'y')\"])\n")))
        self.assertEqual(["subprocess-code"], gate_rules(GATE_TEST, "import subprocess\nimport sys\ndef a(code):\n    subprocess.run([sys.executable, \"-c\", code])\n"))
        self.assertEqual(["subprocess-code"], gate_rules(GATE_TEST, "import subprocess\nimport sys\nsubprocess.run([sys.executable], input=\"import socket\\n\")\n"))
        self.assertEqual(["subprocess-code"], gate_rules(GATE_TEST, "import subprocess\nimport sys\nsubprocess.run([sys.executable, \"-c\", \"exec(open('x').read())\"])\n"))
        self.assertEqual([], gate_rules(GATE_TEST, "import subprocess\nsubprocess.run([\"git\", \"-C\", \"/r\", \"-c\", \"commit.gpgsign=false\", \"commit\"], check=True)\n"))
        self.assertEqual([], gate_rules(GATE_TEST, "import subprocess\nimport sys\nsubprocess.run([sys.executable, \"-c\", \"print(1)\"])\n"))

    def test_curl_by_path_concatenation_and_shell_helpers_are_caught(self) -> None:
        self.assertEqual(["subprocess-curl"], gate_rules(HARNESS, "import subprocess\nsubprocess.run([\"/usr/bin/curl\", \"-X\", \"POST\", \"https://x\"])\n"))
        self.assertEqual(["subprocess-curl"], gate_rules(HARNESS, "import subprocess\nsubprocess.run([\"c\" + \"url\", \"https://x\"])\n"))
        self.assertEqual(["subprocess-curl"], gate_rules(HARNESS, "import subprocess\nsubprocess.run([\"./curl\", \"https://x\"])\n"))
        self.assertEqual(["subprocess-shell"], gate_rules(HARNESS, "import subprocess\nsubprocess.getoutput(\"ls\")\n"))
        self.assertEqual([], gate_rules(HARNESS, "import subprocess\nsubprocess.run([\"git\", \"status\"], check=False)\n"))


class SubprocessWrapperTests(unittest.TestCase):
    """PR 2d decision D2-7: no desk tool joins SUBPROCESS_ALLOWED. The pin check reaches git only through
    ``check_grok_template.checkout_state`` (and the template check ``check_grok_template.run``), every other name of
    a subprocess-capable module is a laundering path (rule ``subprocess-wrapper``), and a git or interpreter
    configuration override on a command line outside tests/ fails (rule ``subprocess-config``)."""

    def fixture_rules(self, case: str) -> Dict[str, List[str]]:
        with fixture_tree(case) as root:
            result = check_no_write_paths.run(root)
        self.assertFalse(result["ok"])
        return {rel: sorted(rules) for rel, rules in rules_by_file(result).items()}

    def test_curl_in_the_routine_entry_point_fails_twice(self) -> None:
        self.assertEqual({ROUTINE: ["subprocess-curl", "subprocess-import"]}, self.fixture_rules("subprocess_in_routine"))

    def test_laundering_a_command_through_run_git_fails(self) -> None:
        self.assertEqual({ROUTINE: ["subprocess-wrapper"]}, self.fixture_rules("subprocess_wrapper"))

    def test_desk_tools_may_not_import_subprocess(self) -> None:
        self.assertEqual(["subprocess-import"], gate_rules(DOCTOR, "import subprocess\nsubprocess.run(['git', 'status'], check=False)\n"))
        self.assertEqual(["subprocess-import", "subprocess-shell"], gate_rules(ROUTINE, "import subprocess\nsubprocess.run(\"ls\", shell=True)\n"))
        self.assertEqual(["subprocess-import"], gate_rules("scripts/aegis_desk/checkout.py", "from subprocess import run\n"))

    def test_git_configuration_overrides_fail_outside_tests(self) -> None:
        self.assertEqual(["subprocess-config"], gate_rules(TEMPLATE_GATE, (
            "import subprocess\nsubprocess.run([\"git\", \"-c\", \"alias.x=!sh\", \"x\"])\n")))
        self.assertEqual(["subprocess-config"], gate_rules(HARNESS, (
            "import subprocess\nsubprocess.run([\"git\", \"--config-env\", \"alias.x=X\", \"x\"], check=False)\n")))
        self.assertEqual(["subprocess-config"], gate_rules(HARNESS, (
            "import subprocess\nsubprocess.run([\"git\", \"--config-env=alias.x=X\", \"x\"], check=False)\n")))
        self.assertEqual(["subprocess-config"], gate_rules(HARNESS, (
            "import subprocess\nimport sys\nsubprocess.run([sys.executable, \"-c\", \"print(1)\"], check=False)\n")))
        # The wrapper's own callers inside an allowed module are held to the same rule: run_git's argument list is
        # the command line.
        self.assertEqual(["subprocess-config"], gate_rules(TEMPLATE_GATE, (
            "import subprocess\ndef run_git(root, args):\n    return subprocess.run([\"git\", \"-C\", root] + args, check=False)\n"
            "def a(root):\n    return run_git(root, [\"-c\", \"alias.x=!sh\", \"x\"])\n")))
        # Plain git with literal arguments stays legal in the allowed modules; tests/gates may still configure git.
        self.assertEqual([], gate_rules(TEMPLATE_GATE, (
            "import subprocess\nsubprocess.run([\"git\", \"--no-optional-locks\", \"status\", \"--porcelain\"], check=False)\n")))
        self.assertEqual([], gate_rules(GATE_TEST, (
            "import subprocess\nsubprocess.run([\"git\", \"-c\", \"user.name=t\", \"commit\"], check=False)\n")))

    def test_the_two_safe_exports_pass(self) -> None:
        self.assertEqual([], gate_rules(ROUTINE, (
            "import check_grok_template\ndef a(r):\n    return check_grok_template.checkout_state(r, \"0.1.0\")\n")))
        self.assertEqual([], gate_rules(DOCTOR, (
            "import check_grok_template\nCHECKOUT_RUN = None\ndef a(r):\n"
            "    return check_grok_template.checkout_state(r, \"0.1.0\", run=CHECKOUT_RUN), check_grok_template.run(r)\n")))
        self.assertEqual([], gate_rules(DOCTOR, "from check_grok_template import checkout_state, run\n"))
        self.assertEqual([], gate_rules(TOOL_TEST, "import check_grok_template\ndef a(r):\n    return check_grok_template.run(r)\n"))
        self.assertEqual([], gate_rules(ROUTINE, "import check_grok_template\n"))

    def test_every_other_name_of_a_subprocess_module_fails(self) -> None:
        for source in (
            "from check_grok_template import run_git\n",
            "from check_grok_template import checkout_state, run_git as g\n",
            "import check_grok_template as t\ndef a(r):\n    return t.run_git(r, [\"status\"])\n",
            "import check_grok_template\nf = check_grok_template.run_git\n",
            "import check_grok_template\ndef a(r):\n    return getattr(check_grok_template, \"run_git\")(r, [\"status\"])\n",
            "import check_grok_template\ndef a():\n    return check_grok_template.subprocess.run([\"git\", \"status\"])\n",
            "import check_grok_template\ndef a(r):\n    return check_grok_template.compare_to_release_tag(r, \"0.1.0\", [], [], [])\n",
            "import cli_journeys\ndef a(j, r):\n    return cli_journeys.run_journey(j, r)\n",
            "from cli_journeys import FORBIDDEN_OUTPUT\n",
        ):
            with self.subTest(source=source):
                self.assertEqual(["subprocess-wrapper"], gate_rules(ROUTINE, source))
        # A test module outside tests/gates is held to the rule; the gate tests and the allowed modules are not.
        self.assertEqual(["subprocess-wrapper"], gate_rules(TOOL_TEST, "import check_grok_template\nf = check_grok_template.run_git\n"))
        self.assertEqual([], gate_rules(GATE_TEST, "import check_grok_template\nf = check_grok_template.run_git\n"))
        self.assertEqual([], gate_rules(HARNESS, "import check_grok_template\nf = check_grok_template.run_git\n"))

    def test_a_shell_escape_cannot_ride_the_pin_check_s_run_seam(self) -> None:
        # checkout_state(root, release, run=...) calls run(root, args): a shell escape handed over by reference (never
        # called where the gate could see it) would run behind a safe export, so a bare reference fails too.
        for source in (
            "import check_grok_template\nimport os\ndef a(r):\n    return check_grok_template.checkout_state(r, \"0.1.0\", run=os.execv)\n",
            "import check_grok_template\nimport os\nCHECKOUT_RUN = os.execv\ndef a(r):\n"
            "    return check_grok_template.checkout_state(r, \"0.1.0\", run=CHECKOUT_RUN)\n",
            "import os\nf = os.system\n",
            "import os\nlist(map(os.system, [\"ls\"]))\n",
            "import os as o\nsorted([\"ls\"], key=o.popen)\n",
            "import pty\nhook = pty.spawn\n",
        ):
            with self.subTest(source=source):
                self.assertEqual(["shell-escape"], gate_rules(ROUTINE, source))
        self.assertEqual([], gate_rules(ROUTINE, "import os\np = os.path.join(\"a\", \"b\")\ne = os.environ\n"))

    def test_the_allow_list_is_unchanged_and_the_exports_are_documented(self) -> None:
        self.assertEqual(frozenset({"scripts/check_grok_template.py", "scripts/cli_journeys.py"}),
                         check_no_write_paths.SUBPROCESS_ALLOWED)
        self.assertEqual(frozenset({"check_grok_template.checkout_state", "check_grok_template.run"}),
                         check_no_write_paths.SUBPROCESS_SAFE_EXPORTS)
        for needle in ("SUBPROCESS_SAFE_EXPORTS", "subprocess-wrapper", "subprocess-config"):
            self.assertIn(needle, check_no_write_paths.__doc__)


class ReviewBypassTests(unittest.TestCase):
    """PR 2d review, lens 2: every route the reviewers ran past the gate, and the generalisations of each, fail; the
    shapes the desk uses still pass."""

    def assert_rule(self, rule: str, rel: str, sources: List[str]) -> None:
        for source in sources:
            with self.subTest(source=source):
                self.assertIn(rule, gate_rules(rel, source))

    def test_a_module_cannot_be_loaded_through_a_loader(self) -> None:
        self.assert_rule("dynamic-exec", ROUTINE, [
            "import importlib.util\nweb = importlib.util.find_spec('urllib.request').loader.load_module()\n",
            "import importlib.util\nspec = importlib.util.find_spec('subprocess')\nloader = spec.loader\n",
            "import importlib.util, types\ns = importlib.util.find_spec('subprocess')\nm = types.ModuleType('s')\n"
            "s.loader.exec_module(m)\n",
            "import importlib.util\nspec = importlib.util.find_spec('subprocess')\nl = getattr(spec, 'loader')\n",
            "def a(finder, name):\n    return finder.find_module(name).load_module(name)\n",
            "def a(loader, spec):\n    return loader.create_module(spec)\n",
            "import pkgutil\nm = pkgutil.get_loader('subprocess').load_module()\n",
            "from pkgutil import find_loader\n",
            "import importlib\nm = importlib._bootstrap._load(None)\n",
            "import importlib\nimport subprocess as _s\nimportlib.reload(_s)\n",
            "from importlib import abc\n",
            "import imp\n",
            "import sys\nfinders = sys.meta_path\n",
            "import operator\nf = operator.attrgetter('loader')\n",
        ])
        # the doctor's interpreter check: find_spec answers whether a module exists, and nothing can load it
        self.assertEqual([], gate_rules(DOCTOR, (
            "import importlib.util\nNAMES = ('json', 'csv')\ndef found(name):\n"
            "    return importlib.util.find_spec(name) is not None\nfinder = importlib.util.find_spec\n")))
        self.assertEqual([], gate_rules(OTHER, "import importlib\ndef t():\n    return importlib.import_module('tomllib')\n"))

    def test_sys_modules_is_read_only_by_a_literal_name(self) -> None:
        self.assert_rule("reflection", ROUTINE, [
            "import sys\nfor name, mod in sys.modules.items():\n    pass\n",
            "import sys\nmods = list(sys.modules.values())\n",
            "import sys\nfor name in sys.modules:\n    pass\n",
            "import sys\ntable = sys.modules\n",
            "import sys\ndef a(f):\n    return f(sys.modules)\n",
            "import sys\nm = dict(sys.modules)\n",
            "import sys\nm = getattr(sys, 'modules').copy()\n",
        ])
        self.assertEqual([], gate_rules(ROUTINE, (
            "import sys\nhere = 'json' in sys.modules\nm = sys.modules.get('json')\nj = sys.modules['json']\n")))

    def test_objects_and_frames_that_hand_out_modules_fail(self) -> None:
        self.assert_rule("reflection", ROUTINE, [
            "import gc\nfor m in gc.get_objects():\n    pass\n",
            "import gc\nrefs = gc.get_referents\n",
            "import sys\nf = sys._getframe()\n",
            "import inspect\nf = inspect.currentframe()\n",
            "import inspect, json\nm = inspect.getmodule(json.dumps)\n",
            "def a(frame):\n    return frame.f_globals['x']\n",
            "def a(error):\n    return error.__traceback__.tb_frame\n",
            "import os\nposix = os.getcwd.__self__\n",
        ])

    def test_a_module_reached_through_another_module_fails(self) -> None:
        self.assert_rule("module-reexport", ROUTINE, [
            "import shutil\nshutil.os.system('ls')\n",
            "import os\nos.sys.modules['subprocess'].run(['ls'])\n",
            "import tempfile\nf = tempfile._os.popen\n",
            "import platform\nr = platform.subprocess\n",
            "from aegis_desk import state\ns = state.os.environ\n",
        ])
        self.assertEqual([], gate_rules(ROUTINE, "import os\np = os.path.join('a', 'b')\nd = os.environ.get('HOME')\n"))

    def test_a_command_capable_module_is_never_a_value(self) -> None:
        self.assert_rule("module-value", ROUTINE, [
            "import os\ndef f(m):\n    return m.system('ls')\nf(os)\n",
            "import os\nfor m in (os,):\n    m.system('ls')\n",
            "import subprocess as s\nholder = {'m': s}\n",
            "import sys\nlook = lambda: sys\n",
            "import importlib\ndef f(m):\n    return m\nf(importlib.import_module('os'))\n",
            "import sys\ndef f(m):\n    return m\nf(sys.modules['os'])\n",
        ])
        self.assertEqual([], gate_rules(ROUTINE, (
            "import os\nimport sys\nimport os as o\nalias = os\np = os.path.join('a', 'b')\nw = getattr(os, 'getcwd')()\n"
            "c = o.getcwd()\nh = alias.getcwd()\nsys.stdout.write('x')\n")))

    def test_a_wrapper_module_is_used_only_to_call_a_safe_export(self) -> None:
        self.assert_rule("subprocess-wrapper", ROUTINE, [
            "import check_grok_template\nfor w in (check_grok_template,):\n    w.subprocess.run(['sh'])\n",
            "import check_grok_template\ndef f(m):\n    return m.run_git('.', ['status'])\nf(check_grok_template)\n",
            "import check_grok_template\ng = lambda: check_grok_template\n",
            "import check_grok_template\nwrappers = [check_grok_template]\n",
            "import inspect, check_grok_template\nm = inspect.getmodule(check_grok_template.checkout_state)\n",
            "import check_grok_template\nf = check_grok_template.checkout_state\n",
            "from check_grok_template import checkout_state\nkeep = [checkout_state]\n",
            "import check_grok_template\ndef a(r):\n    return check_grok_template.checkout_state.__wrapped__\n",
            "from scripts.check_grok_template import run_git\n",
            "import scripts.check_grok_template\n",
            "from scripts import check_grok_template\n",
            "import scripts.cli_journeys as harness\n",
            "import importlib\nm = importlib.import_module('check_grok_template')\n",
        ])
        self.assert_rule("subprocess-wrapper", "scripts/aegis_desk/probe.py", [
            "import routine\nroutine.check_grok_template.run_git('.', ['x'])\n",
            "import desk_doctor\nm = desk_doctor.check_grok_template\n",
        ])
        self.assertEqual([], gate_rules(ROUTINE, (
            "import check_grok_template\nfrom check_grok_template import checkout_state\nCHECKOUT_RUN = None\n"
            "def a(r):\n    return check_grok_template.checkout_state(r, '0.1.0', run=CHECKOUT_RUN), checkout_state(r, "
            "'0.1.0'), check_grok_template.run(r)\n")))

    def test_the_allowed_modules_build_no_override_and_no_environment(self) -> None:
        self.assert_rule("subprocess-config", TEMPLATE_GATE, [
            "import subprocess\nFLAG = '-c'\nsubprocess.run(['git', FLAG, 'alias.x=!sh', 'x'])\n",
            "import subprocess\nFLAGS = ['-c', 'alias.x=!sh']\nsubprocess.run(['git'] + FLAGS + ['x'])\n",
            "import subprocess\nsubprocess.run(['git', '--git-dir=/tmp/x', 'status'])\n",
            "import subprocess\nsubprocess.run(['git', '--exec-path', '/tmp', 'status'])\n",
            "import subprocess\nsubprocess.run(['git', '--work-tree=/tmp', 'status'])\n",
            "import subprocess\nsubprocess.run(['git', 'status'], env={'GIT_CONFIG_COUNT': '1'})\n",
            "import os, subprocess\nenv = dict(os.environ)\nsubprocess.run(['git', 'status'], env=env)\n",
            "import subprocess\nKEY = 'GIT_CONFIG_PARAMETERS'\n",
        ])
        self.assert_rule("subprocess-shell", TEMPLATE_GATE, [
            "import subprocess\nsubprocess.run('ls', **{'shell': True})\n",
            "import subprocess\nopts = {'shell': True}\nsubprocess.run('ls', **opts)\n",
            "import subprocess\nargs = ['ls']\nsubprocess.run(*args)\n",
        ])
        self.assert_rule("subprocess-shell", HARNESS, [
            "import subprocess\nsubprocess.run(['sh'], input=b'ls')\n",
            "import subprocess\nsubprocess.run(['/bin/bash', 'script.sh'])\n",
            "import subprocess\nsubprocess.run(['perl', '-e', 'print 1'])\n",
            "import subprocess\nsubprocess.run(['env', 'ls'])\n",
        ])
        # the env builder of the allowed module is the one place a GIT_ name may be written
        self.assertEqual([], gate_rules(TEMPLATE_GATE, (
            "import os, subprocess\ndef git_environment():\n    env = {k: v for k, v in os.environ.items() if not "
            "k.startswith('GIT_')}\n    env['GIT_CONFIG_COUNT'] = '1'\n    return env\n"
            "def run_git(root, args):\n    return subprocess.run(['git', '-C', root] + args, env=git_environment())\n")))
        self.assertEqual([], gate_rules(HARNESS, (
            "import subprocess, sys\ndef scrubbed_environment():\n    return {'PYTHONUTF8': '1'}\n"
            "def a(script):\n    return subprocess.run([sys.executable, script], env=scrubbed_environment())\n")))
        # tests/gates may still run a shell (check.sh) and configure git
        self.assertEqual([], gate_rules(GATE_TEST, "import subprocess\nsubprocess.run(['bash', 'scripts/check.sh'])\n"))

    def test_shipped_code_writes_no_environment_variable(self) -> None:
        self.assert_rule("environment-write", ROUTINE, [
            "import os\nos.environ['GIT_CONFIG_COUNT'] = '1'\n",
            "import os\nos.environ.update({'PATH': '/tmp'})\n",
            "import os\nos.environ.setdefault('HOME', '/tmp')\n",
            "import os\nos.putenv('GIT_DIR', '/tmp')\n",
            "import os\ndel os.environ['PATH']\n",
            "import os\nenv = os.environ\nenv['XDG_CONFIG_HOME'] = '/tmp'\n",
            "import os\nos.environb[b'PATH'] = b'/tmp'\n",
        ])
        self.assertEqual([], gate_rules(TOOL_TEST, (
            "import os\nfrom unittest import mock\ndef t():\n    os.environ['AEGIS_TEST'] = '1'\n"
            "    return mock.patch.dict(os.environ, {'TZ': 'UTC'})\n")))
        self.assertEqual([], gate_rules(ROUTINE, "import os\nhome = os.environ.get('HOME')\nnames = list(os.environ)\n"))


class ProseRuleTests(unittest.TestCase):
    def rules_for(self, rel: str, text: str) -> List[str]:
        with fixture_tree() as root:
            write(root, rel, text)
            result = check_no_write_paths.run(root)
        return sorted(rules_by_file(result).get(rel, []))

    def test_each_write_snippet_shape_is_caught(self) -> None:
        cases = {
            "curl --request PUT https://x": ["curl-write"],
            "curl -s -XPATCH https://x": ["curl-write"],
            "curl https://x --data '{}'": ["curl-data"],
            "curl https://x --data-binary @f": ["curl-data"],
            "curl https://x -d 'a=b'": ["curl-body"],
            "client.place_order(symbol)": ["library-write", "order-call"][1:],
            "api.submit_order(ticket)": ["order-call"],
            "broker.cancel_order(id)": ["order-call"],
            "broker.replace_order(id)": ["order-call"],
            'url = "/v1/accounts/1/orders"': ["orders-endpoint"],
            "https://api.example.com/trading/v1": ["trading-path"],
            "POST /v1/accounts/1/orders HTTP/1.1": ["http-write-verb", "orders-endpoint"],
            "requests.post(url, json=body)": ["library-write"],
            "session.delete(url)": ["library-write"],
        }
        for line, expected in cases.items():
            self.assertEqual(sorted(expected), self.rules_for("docs/NEW.md", f"# Doc\n\n{line}\n"), line)

    def test_boundary_prose_passes(self) -> None:
        text = ("# Doc\n\nThis desk never places, modifies or cancels orders. Options trading is risky; "
                "orders are yours to enter in your brokerage account. The hook denies POST requests to a "
                "brokerage. Quotes come from GET /v2/stocks/quotes/latest.\n")
        self.assertEqual([], self.rules_for("docs/NEW.md", text))

    def test_every_instruction_text_a_bot_loads_is_scanned(self) -> None:
        # Session-loaded files (AGENTS.md, GEMINI.md, CLAUDE.md, the always-applied rule), the
        # Grok Bot description (template) and the commands routines run (routines manifest) are
        # instruction text as much as a skill is.
        for rel in ("agents/desk-lead.md", "skills/aegis-desk-charter/SKILL.md", "SETUP.md", "README.md",
                    "AGENTS.md", "GEMINI.md", "CLAUDE.md", "CONTRIBUTING.md", "rules/aegis-desk.mdc",
                    "template/grok-bot.json", "routines/manifest.toml"):
            with fixture_tree() as root:
                original = read(root, rel) if os.path.isfile(os.path.join(root, rel)) else "# fixture\n"
                write(root, rel, original + "\nRun curl -X DELETE https://x/v1/accounts/1/orders/9\n")
                result = check_no_write_paths.run(root)
            self.assertIn(rel, rules_by_file(result), rel)

    def test_always_loaded_instruction_fixtures_fail(self) -> None:
        for case, rel in (("prose_agents_md", "AGENTS.md"),
                          ("prose_template_description", "template/grok-bot.json"),
                          ("prose_routine_command", "routines/manifest.toml")):
            with fixture_tree(case) as root:
                result = check_no_write_paths.run(root)
            found = rules_by_file(result)
            self.assertEqual([rel], sorted(found), case)
            self.assertEqual(["curl-body", "curl-write", "orders-endpoint"], sorted(found[rel]), case)

    def test_urllib_write_snippets_in_prose_are_caught(self) -> None:
        skill = "skills/aegis-desk-charter/SKILL.md"
        with fixture_tree() as root:
            write(root, skill, read(root, skill) + "\n```python\nreq = urllib.request.Request(url, data=payload, method=\"POST\")\n"
                  "urlopen(req)\n```\n")
            result = check_no_write_paths.run(root)
        self.assertEqual(["urllib-write"], rules_by_file(result).get(skill))
        cases = {
            'conn.request("POST", "/v2/stocks/quotes/latest")': ["urllib-write"],
            "conn.putrequest('PUT', '/x')": ["urllib-write"],
            "urlopen(url, data=body)": ["urllib-write"],
            "Request(url, method='DELETE')": ["urllib-write"],
            'req = urllib.request.Request(url, headers=h, method="GET")': [],
            "urlopen(req, data=None, timeout=5)": [],
            'conn.request("GET", "/v2/clock")': [],
            "A request(s) in prose with no dot before it: request(x)": [],
        }
        for line, expected in cases.items():
            self.assertEqual(expected, self.rules_for("docs/NEW.md", f"# Doc\n\n{line}\n"), line)

    def test_backslash_continued_curl_is_read_as_one_logical_line(self) -> None:
        with fixture_tree() as root:
            write(root, "docs/NEW2.md", "# Doc\n\ncurl https://data.alpaca.markets/v2/stocks/quotes/latest \\\n  -X POST \\\n  -d '{}'\n")
            result = check_no_write_paths.run(root)
        findings = [(f["line"], f["rule"]) for f in result["errors"] if f["file"] == "docs/NEW2.md"]
        self.assertEqual([(3, "curl-body"), (3, "curl-write")], sorted(findings))
        # A markdown hard line break (trailing backslash) in ordinary prose stays quiet.
        self.assertEqual([], self.rules_for("docs/NEW.md", "# Doc\n\nLine one \\\nline two.\n"))

    def test_hooks_directory_is_not_prose_scanned(self) -> None:
        with fixture_tree() as root:
            write(root, "hooks/SAMPLES.md", "curl -X POST https://x/v1/accounts/1/orders\n")
            result = check_no_write_paths.run(root)
        self.assertTrue(result["ok"], result["errors"])

    def test_allow_list_glob_and_syntax_errors(self) -> None:
        with fixture_tree() as root:
            write(root, "docs/SAMPLE_A.md", "denied sample: curl -X POST https://127.0.0.1:9/a\n")
            write(root, "docs/SAMPLE_B.md", "denied sample: curl -X POST https://127.0.0.1:9/b\n")
            write(root, "scripts/no_write_allowlist.txt",
                  "docs/HOOK_SAMPLES.md: curl -X POST https://127\\.0\\.0\\.1:9/\n"
                  "docs/SAMPLE_*.md: ^denied sample: curl -X POST https://127\\.0\\.0\\.1:9/\n"
                  "no separator here\n"
                  "docs/X.md: (unbalanced\n")
            result = check_no_write_paths.run(root)
        found = rules_by_file(result)
        self.assertEqual(["allowlist-syntax", "allowlist-syntax"], sorted(found.get("scripts/no_write_allowlist.txt", [])))
        self.assertNotIn("docs/SAMPLE_A.md", found)
        self.assertNotIn("docs/SAMPLE_B.md", found)
        self.assertEqual(2, result["scanned"]["allowlist_used"])


if __name__ == "__main__":
    unittest.main()
