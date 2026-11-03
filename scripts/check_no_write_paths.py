#!/usr/bin/env python3
"""Prove by AST that the shipped code has no broker write path. Stdlib Python only; no network.

This is layer 1 of the no-trade guarantee: the repository must contain no code that can send
anything but a GET request to anyone, and no instruction text that teaches a Bot to do so.
Two scans run, both reporting ``file:line``.

Part (a): AST gate over ``scripts/**/*.py``, ``hooks/**/*.py`` and ``tests/**/*.py``
(``tests/fixtures/`` is excluded: fixture trees are inputs to the checkers, not code).

How a callee is named
---------------------
Calls are identified by AST, never by substring. A callee is resolved to a dotted name through
the module's import aliases (``from urllib.request import Request as R`` makes ``R(...)`` the
call ``urllib.request.Request``), through assignment aliases (``R = urllib.request.Request``,
``ur = urllib.request``; chains are followed to a fixed point) and through the standard
library's reflection surface whenever its operands are string literals or literal
concatenations: ``getattr(X, "name")``, ``__import__("m")``, ``importlib.import_module("m")``,
``sys.modules["m"]``, ``sys.modules.get("m")``, ``globals()["name"]``, ``locals()["name"]``
and ``vars(X)["name"]``. Each of those forms with a non-literal operand is rejected outright
(rule ``reflection``) when it reaches a module or an imported object, because a gate that
cannot read a name cannot prove what it calls. Attribute access to ``__dict__``,
``__getattribute__``, ``__setattr__``, ``__delattr__``, ``__subclasses__``, ``__globals__``,
``__builtins__``, ``__code__``, ``__loader__``, ``__spec__``, ``__mro__``, ``__bases__`` and
``__import__`` is rejected everywhere, and so is assigning ``__class__`` or ``get_method`` on
any object (either swaps the method that decides a request's verb).

Closed lists
------------
* Network modules (``urllib.request``, ``http.client``, ``socket``, ``ssl``) may be imported
  only by ``scripts/aegis_desk/tradier_client.py``. A test module may import ``http.server``,
  ``socketserver``, ``socket`` and ``threading`` to run a loopback fake server. A bare
  ``import urllib`` or ``import http`` is rejected as ambiguous: import ``urllib.parse`` or
  ``urllib.error`` explicitly. A network module reached by reflection (``sys.modules[...]``)
  from any other file fails the same way (rule ``network-call``).
* Standard-library modules that open connections or send data by themselves fail everywhere,
  the network client included (``FORBIDDEN_NETWORK_MODULES``): ``xmlrpc``, ``ftplib``,
  ``smtplib``, ``imaplib``, ``poplib``, ``nntplib``, ``telnetlib``,
  ``multiprocessing.connection``, ``webbrowser``, ``antigravity``, ``asyncio`` (streams,
  transports and subprocess APIs the gate cannot follow), ``urllib.robotparser`` (fetches
  URLs), ``logging.handlers`` and ``logging.config`` (HTTP, SMTP and socket log handlers,
  instantiated by class name), ``wsgiref``, ``pip`` and ``ensurepip``. The method names
  ``open_connection``, ``create_connection``, ``create_unix_connection``,
  ``create_datagram_endpoint``, ``create_subprocess_exec``, ``create_subprocess_shell``,
  ``subprocess_exec``, ``subprocess_shell``, ``sock_connect``, ``sock_sendall``,
  ``sock_sendto``, ``start_tls`` and ``connect_ex`` fail everywhere, whatever object they hang
  off (rule ``network-call``).
* Modules that run, compile, load or deserialise code, or call into foreign libraries, fail
  everywhere (rule ``dynamic-exec``, ``DYNAMIC_EXEC_MODULES``): ``code``, ``codeop``,
  ``runpy``, ``marshal``, ``pickle``, ``shelve``, ``timeit``, ``cProfile``, ``profile``,
  ``pdb``, ``ctypes``, ``cffi``, ``zipimport`` and ``importlib.machinery``; so do the calls
  ``exec()``, ``eval()``, ``compile()``, ``importlib.util.spec_from_file_location()``,
  ``importlib.util.module_from_spec()`` and ``pkgutil.resolve_name()``. Dynamic imports
  (``__import__``, ``importlib.import_module``) of a restricted module are rejected; a dynamic
  import whose target is not a string literal is rejected because the gate cannot read it.
* ``subprocess`` may be imported only by the repository gates and harnesses named in
  ``SUBPROCESS_ALLOWED`` (they spawn the interpreter and ``git``) and by test modules under
  ``tests/gates/``. Every ``subprocess`` call anywhere is inspected: a string argument naming
  ``curl`` or ``wget`` (after constant folding, so ``"c" + "url"`` and ``"/usr/bin/curl"``
  count), a ``shell=True`` keyword, ``getoutput``/``getstatusoutput`` (they run a shell), a
  string argument carrying Python source that imports a restricted module or calls
  ``urlopen``/``Request``/``exec``/``__import__``, and a ``-c`` whose code is not a literal
  all fail. ``os.system``, ``os.popen``, ``os.exec*``, ``os.spawn*``, ``os.posix_spawn*``,
  ``os.startfile``, ``platform.popen`` and ``pty.spawn`` fail everywhere, also when reached
  through ``getattr`` or a module alias.
* Third-party HTTP clients (``requests``, ``httpx``, ``aiohttp``, ``urllib3``, ``pycurl``,
  ``websocket``/``websockets``, ``httplib2``) fail everywhere, imported or reached through a
  resolved reflection (``sys.modules["requests"].post(...)``); the repository is stdlib-only.
* Every ``urllib.request.Request(...)`` call must pass ``method="GET"`` as a string literal, or
  no ``method`` at all (urllib then defaults to GET because no body is allowed), and must not
  pass ``data`` (positionally or by keyword) other than the literal ``None``. ``*args`` and
  ``**kwargs`` splats fail because they cannot be read. ``urlopen(...)`` must never receive
  ``data`` (second positional argument or keyword), and an opener's ``.open(...)`` is held to
  the same rule in network modules. Calling ``Request.__init__`` directly (a re-initialisation
  with a body) fails.
* In a module that imports ``http.client`` every ``.request(...)``/``.putrequest(...)`` call
  must pass the literal ``"GET"`` and no body; ``.endheaders(message_body)`` and ``.send(...)``
  fail. The private write path ``._send_request()``/``._send_output()``/``._output()`` and the
  raw socket writes ``.sendall()``, ``.sendto()``, ``.sendmsg()``, ``.sendfile()`` and
  ``.makefile()`` fail everywhere; ``.send()`` fails in a module that imports ``socket``.
* In a network module, assigning ``.method``/``.data``/``._data`` on an object (or ``setattr``
  with those literal names), calling ``.add_data(...)`` or defining ``get_method`` fails: each
  can turn a GET into a write after construction (``_data`` is the field behind the ``data``
  property that ``get_method`` reads). Assigning a request's parsed-target fields
  (``full_url``, ``selector``, ``host``, ``type``, ...) or calling ``.set_proxy(...)`` fails
  there too: each re-points a built GET at another scheme, host or path. There, ``vars()``,
  ``getattr``/``setattr``/``delattr``
  with a non-literal name, ``functools.partial``/``partialmethod`` and
  ``operator.methodcaller``/``attrgetter`` in any form, and a call whose callee is itself a call
  (``type(req)(url, data=...)``) fail too (rule ``indirect-call``): a GET-only client names what
  it calls. In every module, ``partial`` over a network, shell, exec or reflection callable
  fails, ``methodcaller``/``attrgetter`` may not name a write-capable attribute, and a call
  whose callee the gate cannot name fails when it passes ``data=``, ``method=`` or a bytes
  literal.
* Subclassing ``urllib.request.Request`` fails everywhere (rule ``request-subclass``): a class
  body or an ``__init__`` can predefine the verb or the body where the call-site rules cannot
  see them (``class P(Request): method = "POST"``, ``super().__init__(url, body)``). A subclass
  found this way, through an alias or through another such class, is also treated as
  ``Request`` wherever it is instantiated, so those calls are held to the literal ``"GET"``,
  no-body rule as well.

Part (b): prose scanner over ``skills/**``, ``agents/**``, ``docs/**``, ``rules/**``,
``template/**``, ``routines/**``, ``SETUP.md``, ``README.md``, ``AGENTS.md``, ``GEMINI.md``,
``CLAUDE.md`` and ``CONTRIBUTING.md`` (every text a Bot loads on a session or imports as its
description) for imperative write snippets, matched with word-boundary regular expressions
(``PROSE_RULES``) against logical lines: a line ending in a backslash is joined with the next,
so a ``curl`` command split over continuation lines is read whole and reported at its first
physical line. Intentional counter-examples (for example the hook's documented denied sample)
are allow-listed in ``scripts/no_write_allowlist.txt`` as ``path: pattern`` lines: the path is
an exact repository-relative path or an ``fnmatch`` glob, the pattern is a regular expression
searched against the offending (logical) line. An allow-list entry that matches nothing is
stale and fails the check, so every exception stays live and reviewed. ``hooks/`` is not
prose-scanned: its gate script is AST-scanned instead.

What the gate cannot see, it rejects: a name built at runtime from anything but literals is a
finding, not a resolution, so the proof covers exactly the code that names what it calls.
Shipped code that needs a runtime-built name is a reviewed change to this file, never a reason
to loosen a rule in place.

Usage: ``python3 scripts/check_no_write_paths.py [repo-root] [--json]``. Exit 0 when clean,
1 when any finding or stale allow-list entry remains, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

NETWORK_CLIENT = "scripts/aegis_desk/tradier_client.py"
NETWORK_MODULES = ("urllib.request", "http.client", "socket", "ssl")
AMBIGUOUS_PARENTS = ("urllib", "http")
TEST_LOOPBACK_MODULES = ("http.server", "socketserver", "socket", "threading")
SERVER_MODULES = ("http.server", "socketserver")
# Standard-library modules that open connections or send data by themselves. Nothing in a
# read-only desk needs them, so they fail everywhere, the network client included.
FORBIDDEN_NETWORK_MODULES = (
    "xmlrpc", "ftplib", "smtplib", "imaplib", "poplib", "nntplib", "telnetlib",
    "multiprocessing.connection", "webbrowser", "antigravity", "asyncio", "urllib.robotparser",
    "logging.handlers", "logging.config", "wsgiref", "pip", "ensurepip",
)
# Method names that open a connection or spawn a process through an event loop or a socket.
NETWORK_CALL_ATTRS = frozenset({
    "open_connection", "create_connection", "create_unix_connection", "create_datagram_endpoint",
    "create_subprocess_exec", "create_subprocess_shell", "subprocess_exec", "subprocess_shell",
    "sock_connect", "sock_sendall", "sock_sendto", "start_tls", "connect_ex",
})
SHELL_MODULE = "subprocess"
# Repository gates and harnesses that legitimately spawn the interpreter or git. Adding a
# path here is a reviewed change to the no-trade guarantee; a desk tool never belongs here.
SUBPROCESS_ALLOWED = frozenset({
    "scripts/check_grok_template.py",
    "scripts/cli_journeys.py",
})
# Test modules that drive the gates as subprocesses live here and nowhere else.
SUBPROCESS_TEST_PREFIX = "tests/gates/"
SHELL_HELPERS = ("subprocess.getoutput", "subprocess.getstatusoutput")
THIRD_PARTY_HTTP = (
    "requests", "httpx", "aiohttp", "urllib3", "pycurl", "websocket", "websockets", "httplib2",
)
SHELL_ESCAPE_EXACT = ("os.system", "os.popen", "os.startfile", "pty.spawn", "platform.popen")
SHELL_ESCAPE_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn")
DYNAMIC_IMPORTERS = ("__import__", "importlib.import_module", "importlib.__import__")
# Modules that run, compile, load or deserialise code, or call into foreign libraries.
DYNAMIC_EXEC_MODULES = (
    "code", "codeop", "runpy", "marshal", "pickle", "shelve", "timeit", "cProfile", "profile",
    "pdb", "ctypes", "cffi", "zipimport", "importlib.machinery",
)
DYNAMIC_EXEC_CALLS = (
    "exec", "eval", "compile", "importlib.util.spec_from_file_location",
    "importlib.util.module_from_spec", "pkgutil.resolve_name",
)
# Attribute names that reach an object's namespace or class machinery directly.
REFLECTION_ATTRS = frozenset({
    "__dict__", "__getattribute__", "__setattr__", "__delattr__", "__subclasses__", "__globals__",
    "__builtins__", "__code__", "__loader__", "__spec__", "__mro__", "__bases__", "__import__",
})
# Builtins whose aliases are tracked (``g = getattr; g(os, "system")``).
TRACKED_BUILTINS = frozenset({
    "exec", "eval", "compile", "getattr", "setattr", "delattr", "vars", "globals", "locals",
    "__import__", "type",
})
PARTIAL_FACTORIES = ("functools.partial", "functools.partialmethod")
OPERATOR_FACTORIES = ("operator.methodcaller", "operator.attrgetter")
REQUEST = "urllib.request.Request"
URLOPEN = "urllib.request.urlopen"
# Methods that write to a connection, with the rule each one fails under; they fail everywhere.
WRITE_ATTRS = {
    "sendall": "socket-write", "sendto": "socket-write", "sendmsg": "socket-write",
    "sendfile": "socket-write", "makefile": "socket-write",
    "_send_request": "http-send", "_send_output": "http-send", "_output": "http-send",
    "add_data": "request-mutation", "set_proxy": "request-mutation",
}
HTTP_CLIENT_ATTRS = ("request", "putrequest", "endheaders", "send")
# Attributes whose assignment (or setattr) can change a request's verb or body after construction;
# ``_data`` is the field behind urllib.request.Request's ``data`` property, which get_method reads.
MUTATION_ATTRS = ("method", "data", "_data", "get_method", "__class__")
# urllib.request.Request's parsed-target fields: assigning one re-points a built GET at another
# scheme, host or path, so none may be assigned (or set through setattr) in the network module.
TARGET_ATTRS = ("full_url", "_full_url", "selector", "host", "type", "origin_req_host", "_tunnel_host")
# Attribute names operator.methodcaller/attrgetter may not name: each reaches a write or an escape.
SENSITIVE_ATTRS = frozenset({
    "open", "request", "putrequest", "endheaders", "send", "urlopen", "Request", "system", "popen",
    "run", "Popen", "call", "check_call", "check_output", "exec", "eval", "compile",
    "import_module", "__import__", "get_method", "method", "data",
}) | frozenset(WRITE_ATTRS) | REFLECTION_ATTRS | NETWORK_CALL_ATTRS
CURL_WGET_RE = re.compile(r"(?<![\w-])(curl|wget)(?![\w-])")
# Python source smuggled into a subprocess argument (``python -c "import urllib.request; ..."``).
CODE_RE = re.compile(
    r"(?:^|[;\s(])(?:import|from)\s+(?:urllib|http|socket|ssl|xmlrpc|ftplib|smtplib|asyncio|ctypes|"
    r"subprocess|os|importlib|requests|httpx)\b|__import__\s*\(|import_module\s*\(|\burlopen\s*\(|"
    r"\bRequest\s*\(|\bexec\s*\(|\beval\s*\("
)

PY_ROOTS = ("scripts", "hooks", "tests")
EXCLUDED_DIRS = frozenset({"__pycache__", ".git", "node_modules", ".venv"})
FIXTURE_ROOT = "tests/fixtures"
PROSE_ROOTS = ("skills", "agents", "docs", "rules", "template", "routines")
PROSE_FILES = ("SETUP.md", "README.md", "AGENTS.md", "GEMINI.md", "CLAUDE.md", "CONTRIBUTING.md")
ALLOWLIST = "scripts/no_write_allowlist.txt"

# (rule id, compiled regex, message). Word boundaries keep "orders" prose and "trading" prose
# out of scope; only the snippet shapes a Bot could copy into a shell or a request fail.
PROSE_RULES: Tuple[Tuple[str, "re.Pattern[str]", str], ...] = (
    ("curl-write", re.compile(r"\bcurl\b[^\n]*?\s(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b"),
     "curl write request"),
    ("curl-body", re.compile(r"\bcurl\b[^\n]*?\s-d(?:\s|['\"@{])"), "curl -d request body"),
    ("curl-data", re.compile(r"(?<![\w-])--data(?:-binary|-raw|-urlencode|-ascii)?(?![\w-])"),
     "curl request body flag"),
    ("order-call", re.compile(r"\b(?:place|submit|cancel|replace)_order\("), "broker order call"),
    ("orders-endpoint", re.compile(r"/orders\b"), "orders endpoint path"),
    ("trading-path", re.compile(r"\btrading/"), "trading path segment"),
    ("http-write-verb", re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\s+/v\d+/accounts\b"),
     "brokerage account write request line"),
    ("library-write", re.compile(r"\b(?:requests|httpx|session|client)\.(?:post|put|patch|delete)\("),
     "HTTP library write call"),
    ("urllib-write", re.compile(
        r"\b(?:Request|urlopen|\.request|\.putrequest)\([^)]*?"
        r"(?:\bdata\s*=(?!\s*None\b)|\bmethod\s*=\s*['\"](?:POST|PUT|PATCH|DELETE)['\"]|"
        r"['\"](?:POST|PUT|PATCH|DELETE)['\"])"),
     "urllib/http.client write request"),
)


class Finding(dict):
    """One failure, serialisable as-is: file, line, rule, message, text."""

    def __init__(self, file: str, line: int, rule: str, message: str, text: str = "") -> None:
        super().__init__(file=file, line=line, rule=rule, message=message, text=text.strip())

    def render(self) -> str:
        suffix = f": {self['text']}" if self["text"] else ""
        return f"{self['file']}:{self['line']}: [{self['rule']}] {self['message']}{suffix}"


def module_matches(name: Optional[str], module: str) -> bool:
    return name is not None and (name == module or name.startswith(module + "."))


def is_none(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def is_literal(node: Optional[ast.AST], value: str) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == value


def is_bytes_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (bytes, bytearray))


def keyword(call: ast.Call, name: str) -> Optional[ast.keyword]:
    for kw in call.keywords:
        if kw.arg == name:
            return kw
    return None


def has_splat(call: ast.Call) -> bool:
    return any(isinstance(arg, ast.Starred) for arg in call.args) or any(
        kw.arg is None for kw in call.keywords
    )


def literal_str(node: Optional[ast.AST]) -> Optional[str]:
    """A string literal, a ``+`` chain of string literals, or an f-string made of literals only."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_str(node.left), literal_str(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts = [literal_str(value) for value in node.values]
        return None if any(part is None for part in parts) else "".join(parts)
    return None


def string_values(node: ast.AST) -> Iterable[str]:
    """Every string reachable inside an expression: literals, folded ``+`` chains of literals
    and the literal parts of f-strings (so ``f"curl {url}"`` yields ``"curl "``)."""
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value
        elif isinstance(child, ast.BinOp) and isinstance(child.op, ast.Add):
            folded = literal_str(child)
            if folded is not None:
                yield folded


def subscript_key(node: ast.Subscript) -> ast.AST:
    key = node.slice
    if type(key).__name__ == "Index":  # the Python 3.8 AST shape; harmless on 3.9 and later
        key = key.value  # type: ignore[attr-defined]
    return key


def strip_builtins(name: Optional[str]) -> Optional[str]:
    if name is not None and name.startswith("builtins."):
        return name[len("builtins."):]
    return name


def last_segment(name: str) -> str:
    return name.rsplit(".", 1)[-1]


class ModuleGate:
    """AST gate for one Python module.

    ``names`` maps a local name to the dotted path it stands for: import aliases (``from
    urllib.request import Request as R`` makes ``R`` ``urllib.request.Request``), assignment
    aliases (``ur = urllib.request``) and tracked builtins. :meth:`resolve` chains attribute
    access, literal ``getattr``, literal dynamic imports and literal ``sys.modules`` /
    namespace-dictionary lookups on top of that map and reports whether the chain is *rooted*
    in an import or a module lookup (as opposed to a local object).
    """

    def __init__(self, rel: str, source: str) -> None:
        self.rel = rel
        self.lines = source.splitlines()
        self.findings: List[Finding] = []
        self.names: Dict[str, str] = {}
        self.is_test = rel.startswith("tests/")
        self.is_network_client = rel == NETWORK_CLIENT
        self.subprocess_allowed = rel in SUBPROCESS_ALLOWED or rel.startswith(SUBPROCESS_TEST_PREFIX)
        self.network_module = False
        self.http_client_module = False
        self.socket_module = False
        self.reported: Set[Tuple[int, str]] = set()

    # -- reporting -------------------------------------------------------------------------
    def fail(self, node: ast.AST, rule: str, message: str) -> None:
        """Record one finding per rule per line: a line that trips a rule through two routes
        (``from subprocess import run`` names the module and a member; ``getattr(os,
        "system")("ls")`` is a reference and a call) is one defect to fix."""
        line = getattr(node, "lineno", 0) or 0
        if (line, rule) in self.reported:
            return
        self.reported.add((line, rule))
        text = self.lines[line - 1] if 0 < line <= len(self.lines) else ""
        self.findings.append(Finding(self.rel, line, rule, message, text))

    # -- resolution ------------------------------------------------------------------------
    def resolve(self, node: Optional[ast.AST]) -> Tuple[Optional[str], bool]:
        """``(dotted name, rooted)`` for a Name/Attribute chain, or ``(None, False)``.

        ``rooted`` is True when the chain bottoms out in an import-bound name or a module
        lookup, so the object is a module or something taken from one, not a local value.
        """
        if isinstance(node, ast.Name):
            bound = self.names.get(node.id)
            return (bound, True) if bound is not None else (node.id, False)
        if isinstance(node, ast.Attribute):
            base, rooted = self.resolve(node.value)
            return (None, False) if base is None else (f"{base}.{node.attr}", rooted)
        if isinstance(node, ast.Call):
            func = strip_builtins(self.resolve(node.func)[0])
            if func == "getattr" and len(node.args) >= 2:
                base, rooted = self.resolve(node.args[0])
                attr = literal_str(node.args[1])
                if base is not None and attr is not None:
                    return f"{base}.{attr}", rooted
            elif func in DYNAMIC_IMPORTERS and node.args:
                target = literal_str(node.args[0])
                if target is not None:
                    return target, True
            elif func == "sys.modules.get" and node.args:
                target = literal_str(node.args[0])
                if target is not None:
                    return target, True
            return None, False
        if isinstance(node, ast.Subscript):
            key = literal_str(subscript_key(node))
            value = node.value
            if isinstance(value, ast.Call):
                func = strip_builtins(self.resolve(value.func)[0])
                if func in ("globals", "locals") or (func == "vars" and not value.args):
                    if key is None:
                        return None, False
                    bound = self.names.get(key)
                    return (bound, True) if bound is not None else (key, False)
                if func == "vars" and len(value.args) == 1 and key is not None:
                    base, rooted = self.resolve(value.args[0])
                    return (None, False) if base is None else (f"{base}.{key}", rooted)
                return None, False
            base, rooted = self.resolve(value)
            if base == "sys.modules" and key is not None:
                return key, True
            if base is not None and base.endswith(".__dict__") and key is not None:
                return f"{base[:-len('.__dict__')]}.{key}", rooted
            return None, False
        return None, False

    def qualname(self, node: Optional[ast.AST]) -> Optional[str]:
        return self.resolve(node)[0]

    # -- imports ---------------------------------------------------------------------------
    def bind(self, local: str, target: str) -> None:
        self.names[local] = target

    def imported(self, node: ast.AST, module: str, bare: bool = True) -> None:
        """Apply the import rules to one dotted module (or module attribute) name.

        ``bare`` is False for the module part of a ``from X import y`` statement, which names a
        specific member and is therefore never an ambiguous parent import.
        """
        top = module.split(".")[0]
        if bare and module in AMBIGUOUS_PARENTS:
            self.fail(node, "ambiguous-import",
                      f"bare 'import {module}' is ambiguous; import the submodule you need "
                      "(urllib.parse, urllib.error, http.HTTPStatus) explicitly")
            return
        if top in THIRD_PARTY_HTTP:
            self.fail(node, "third-party-http",
                      f"'{module}' is a third-party HTTP client; the repository is stdlib-only and "
                      "GET-only through scripts/aegis_desk/tradier_client.py")
            return
        for forbidden in FORBIDDEN_NETWORK_MODULES:
            if module_matches(module, forbidden):
                self.fail(node, "network-import",
                          f"'{forbidden}' can open a connection or send data by itself; it is not "
                          f"allowed anywhere (the only network path is {NETWORK_CLIENT})")
                return
        for forbidden in DYNAMIC_EXEC_MODULES:
            if module_matches(module, forbidden):
                self.fail(node, "dynamic-exec",
                          f"'{forbidden}' runs, loads or deserialises code the gate cannot read; "
                          "not allowed anywhere")
                return
        for restricted in NETWORK_MODULES:
            if module_matches(module, restricted):
                self.network_module = True
                if restricted == "http.client":
                    self.http_client_module = True
                if restricted == "socket":
                    self.socket_module = True
                if self.is_network_client:
                    return
                if self.is_test and restricted in TEST_LOOPBACK_MODULES:
                    return
                self.fail(node, "network-import",
                          f"only {NETWORK_CLIENT} may import '{restricted}'")
                return
        for server in SERVER_MODULES:
            if module_matches(module, server):
                self.network_module = True
                if not self.is_test:
                    self.fail(node, "network-import",
                              f"'{server}' may only be imported by a test module running a "
                              "loopback fake server")
                return
        if module_matches(module, SHELL_MODULE):
            if not self.subprocess_allowed:
                self.fail(node, "subprocess-import",
                          "only the repository gates listed in SUBPROCESS_ALLOWED and test modules "
                          f"under {SUBPROCESS_TEST_PREFIX} may import 'subprocess'")
            return
        if module in SHELL_ESCAPE_EXACT or module.startswith(SHELL_ESCAPE_PREFIXES):
            self.fail(node, "shell-escape", f"'{module}' can run an arbitrary command; not allowed")
            return
        if module in DYNAMIC_EXEC_CALLS:
            self.fail(node, "dynamic-exec", f"'{module}' loads or runs code the gate cannot read; not allowed")

    def visit_imports(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    self.bind(local, alias.name if alias.asname else alias.name.split(".")[0])
                    self.imported(node, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue  # relative import inside this package
                self.imported(node, node.module, bare=False)
                for alias in node.names:
                    if alias.name == "*":
                        self.fail(node, "star-import",
                                  f"'from {node.module} import *' hides what is imported")
                        continue
                    target = f"{node.module}.{alias.name}"
                    self.bind(alias.asname or alias.name, target)
                    self.imported(node, target)

    def visit_aliases(self, tree: ast.AST) -> None:
        """Bind ``name = <resolvable chain>`` so later calls through the alias are checked.

        Only chains rooted in an import or module lookup, and tracked builtins, are bound; a
        local value (``request = Request(url)``) is a call result and never an alias. Chains
        (``a = urllib.request; b = a.Request``) are followed to a fixed point.
        """
        assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
        for _ in range(8):
            changed = False
            for node in assignments:
                if node.value is None:
                    continue
                name, rooted = self.resolve(node.value)
                if name is None or not (rooted or strip_builtins(name) in TRACKED_BUILTINS):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and self.names.get(target.id) != name:
                        self.bind(target.id, name)
                        changed = True
            if not changed:
                break

    def visit_classes(self, tree: ast.AST) -> None:
        """A class deriving from ``urllib.request.Request`` (directly, through an alias or through
        another such class) is a finding: a class body or an ``__init__`` can predefine the verb
        or the body where the call-site rules cannot see them. The class name is also bound to
        ``Request`` so that instantiating it is checked by the ``Request()`` rules."""
        classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        for _ in range(8):
            changed = False
            for node in classes:
                if self.names.get(node.name) == REQUEST:
                    continue
                if any(self.qualname(base) == REQUEST for base in node.bases):
                    self.fail(node, "request-subclass",
                              f"class {node.name} subclasses urllib.request.Request; a class body or __init__ can "
                              "predefine a verb or body behind the gate's back, so a GET-only client constructs "
                              "Request directly")
                    self.bind(node.name, REQUEST)
                    changed = True
            if not changed:
                break

    # -- calls -----------------------------------------------------------------------------
    def is_sensitive_callee(self, name: str) -> bool:
        """A callable that reaches the network, a shell, code execution or reflection."""
        plain = strip_builtins(name) or name
        if name in (REQUEST, URLOPEN) or plain in DYNAMIC_IMPORTERS or plain in DYNAMIC_EXEC_CALLS:
            return True
        if name.split(".")[0] in THIRD_PARTY_HTTP:
            return True
        if plain in TRACKED_BUILTINS or name in SHELL_ESCAPE_EXACT or name.startswith(SHELL_ESCAPE_PREFIXES):
            return True
        if module_matches(name, SHELL_MODULE):
            return True
        for module in NETWORK_MODULES + FORBIDDEN_NETWORK_MODULES + DYNAMIC_EXEC_MODULES:
            if module_matches(name, module):
                return True
        return last_segment(name) in WRITE_ATTRS or last_segment(name) in NETWORK_CALL_ATTRS

    def check_dynamic_import(self, call: ast.Call, name: str) -> None:
        target = call.args[0] if call.args else None
        literal = literal_str(target)
        if literal is not None:
            self.imported(call, literal)
        else:
            self.fail(call, "dynamic-import",
                      f"{name}() with a non-literal module name cannot be checked by the gate")

    def check_subprocess(self, call: ast.Call, name: str) -> None:
        if not module_matches(name, SHELL_MODULE):
            return
        if name in SHELL_HELPERS:
            self.fail(call, "subprocess-shell", f"{name}() runs its command through a shell")
        strings = [text for node in list(call.args) + [kw.value for kw in call.keywords]
                   for text in string_values(node)]
        if any(CURL_WGET_RE.search(text) for text in strings):
            self.fail(call, "subprocess-curl",
                      "a subprocess argument names curl or wget; the only network path "
                      f"is {NETWORK_CLIENT}")
        if any(CODE_RE.search(text) for text in strings):
            self.fail(call, "subprocess-code",
                      "a subprocess argument carries Python source that imports a restricted module "
                      "or calls into the network; code passed as a string bypasses this gate")
        for node in list(call.args) + [kw.value for kw in call.keywords]:
            if isinstance(node, (ast.List, ast.Tuple)):
                elements = node.elts
                for index, element in enumerate(elements):
                    if is_literal(element, "-c") and (index + 1 >= len(elements)
                                                      or literal_str(elements[index + 1]) is None):
                        self.fail(call, "subprocess-code",
                                  "code passed to -c must be a string literal the gate can read")
        shell = keyword(call, "shell")
        if shell is not None and not (isinstance(shell.value, ast.Constant) and shell.value.value is False):
            self.fail(call, "subprocess-shell", "shell=True hands the command line to a shell")

    def check_reference(self, node: ast.AST, name: str) -> None:
        """A resolved name that is called, or merely obtained through ``getattr``."""
        plain = strip_builtins(name) or name
        if name.split(".")[0] in THIRD_PARTY_HTTP:
            self.fail(node, "third-party-http",
                      f"{name} reaches a third-party HTTP client; the repository is stdlib-only and "
                      f"GET-only through {NETWORK_CLIENT}")
            return
        if name in SHELL_ESCAPE_EXACT or name.startswith(SHELL_ESCAPE_PREFIXES):
            self.fail(node, "shell-escape", f"{name}() can run an arbitrary command; not allowed")
        if plain in DYNAMIC_EXEC_CALLS:
            self.fail(node, "dynamic-exec", f"{plain}() runs or loads code the gate cannot read; not allowed")
        for module in FORBIDDEN_NETWORK_MODULES:
            if module_matches(name, module):
                self.fail(node, "network-call", f"{name} opens a connection or sends data by itself; not allowed anywhere")
                return
        for module in DYNAMIC_EXEC_MODULES:
            if module_matches(name, module):
                self.fail(node, "dynamic-exec", f"{name} runs, loads or deserialises code the gate cannot read")
                return
        for module in NETWORK_MODULES:
            if module_matches(name, module) and not self.is_network_client and not (
                    self.is_test and module in TEST_LOOPBACK_MODULES):
                self.fail(node, "network-call",
                          f"{name} is reached outside {NETWORK_CLIENT}; only that module may use '{module}'")
                return

    def check_request(self, call: ast.Call) -> None:
        if has_splat(call):
            self.fail(call, "request-splat", "Request() with *args/**kwargs cannot be proven GET-only")
        if len(call.args) >= 2 and not is_none(call.args[1]):
            self.fail(call, "request-data", "Request() must not receive a body (second positional argument)")
        data = keyword(call, "data")
        if data is not None and not is_none(data.value):
            self.fail(call, "request-data", "Request() must not receive data=")
        method = keyword(call, "method")
        if method is not None and not is_literal(method.value, "GET"):
            self.fail(call, "request-method", 'Request() method must be the string literal "GET"')

    def check_urlopen(self, call: ast.Call, what: str) -> None:
        if has_splat(call):
            self.fail(call, "urlopen-splat", f"{what} with *args/**kwargs cannot be proven GET-only")
        if len(call.args) >= 2 and not is_none(call.args[1]):
            self.fail(call, "urlopen-data", f"{what} must never receive data (second positional argument)")
        data = keyword(call, "data")
        if data is not None and not is_none(data.value):
            self.fail(call, "urlopen-data", f"{what} must never receive data=")

    def check_http_client_call(self, call: ast.Call, attr: str) -> None:
        if attr in ("request", "putrequest"):
            method = call.args[0] if call.args else None
            method_kw = keyword(call, "method")
            if method_kw is not None:
                method = method_kw.value
            if not is_literal(method, "GET"):
                self.fail(call, "http-method", f'.{attr}() must pass the string literal "GET"')
            if attr == "request":
                body = call.args[2] if len(call.args) >= 3 else None
                body_kw = keyword(call, "body")
                if body_kw is not None:
                    body = body_kw.value
                if body is not None and not is_none(body):
                    self.fail(call, "http-body", ".request() must not send a body")
            if has_splat(call):
                self.fail(call, "http-splat", f".{attr}() with *args/**kwargs cannot be proven GET-only")
        elif attr == "endheaders":
            body = call.args[0] if call.args else None
            body_kw = keyword(call, "message_body")
            if body_kw is not None:
                body = body_kw.value
            if body is not None and not is_none(body):
                self.fail(call, "http-body", ".endheaders() must not send a message body")
        elif attr == "send":
            self.fail(call, "http-send", ".send() writes raw bytes to the connection")

    def check_getattr(self, call: ast.Call, plain: str) -> None:
        """``getattr``/``delattr``: a non-literal name on a module or imported object (or on
        anything inside the network module) cannot be checked; a literal name is resolved and
        the result checked as a reference."""
        if len(call.args) < 2:
            return
        target, rooted = self.resolve(call.args[0])
        attr = literal_str(call.args[1])
        if attr is None:
            if self.network_module or rooted:
                self.fail(call, "reflection",
                          f"{plain}() with a non-literal attribute name on "
                          f"{'a module or imported object' if rooted else 'an object in the network module'} "
                          "cannot be checked by the gate")
            return
        if target is not None:
            self.check_reference(call, f"{target}.{attr}")

    def check_setattr(self, call: ast.Call) -> None:
        if len(call.args) < 2:
            return
        target, rooted = self.resolve(call.args[0])
        attr = literal_str(call.args[1])
        if attr is None:
            if self.network_module or rooted:
                self.fail(call, "reflection", "setattr() with a non-literal attribute name cannot be checked by the gate")
            return
        if self.network_module and (attr in MUTATION_ATTRS or attr in TARGET_ATTRS):
            self.fail(call, "request-mutation",
                      f"setattr of .{attr} can turn a GET into a write or re-point it; not allowed in the network module")
        elif rooted:
            self.fail(call, "reflection",
                      f"setattr() on {target} rebinds a module or imported object; later calls would "
                      "resolve to something the gate did not read")

    def check_vars(self, call: ast.Call) -> None:
        if not call.args:
            return
        target, rooted = self.resolve(call.args[0])
        if self.network_module:
            self.fail(call, "reflection", "vars() exposes an instance dictionary; a GET-only client never needs it")
        elif rooted:
            self.fail(call, "reflection", f"vars() on {target} exposes a module namespace to runtime lookups")

    def check_partial(self, call: ast.Call, name: str) -> None:
        if not call.args:
            return
        target, _rooted = self.resolve(call.args[0])
        if self.network_module:
            self.fail(call, "indirect-call",
                      f"{name}() hides the arguments of {target or 'a callable'}; a GET-only client calls what it needs directly")
            return
        if target is None:
            if keyword(call, "data") is not None or keyword(call, "method") is not None or any(
                    is_bytes_literal(arg) for arg in call.args[1:]):
                self.fail(call, "indirect-call", f"{name}() over an unnamed callable binds data=/method=/a bytes body")
            return
        if self.is_sensitive_callee(target):
            self.fail(call, "indirect-call", f"{name}() over {target} hides its arguments from the gate")

    def check_operator_factory(self, call: ast.Call, name: str) -> None:
        # methodcaller(name, *args): only the first argument names an attribute; attrgetter
        # takes nothing but (possibly dotted) attribute names.
        names = call.args[:1] if name == "operator.methodcaller" else call.args
        for arg in names:
            attr = literal_str(arg)
            if attr is None:
                self.fail(call, "reflection", f"{name}() with a non-literal attribute name cannot be checked by the gate")
            elif self.network_module:
                self.fail(call, "indirect-call", f"{name}() hides the call it makes; a GET-only client calls what it needs directly")
            elif any(segment in SENSITIVE_ATTRS for segment in attr.split(".")):
                self.fail(call, "indirect-call", f"{name}({attr!r}) reaches a write-capable attribute")

    def visit_call(self, call: ast.Call) -> None:
        name, _rooted = self.resolve(call.func)
        plain = strip_builtins(name)
        attr = call.func.attr if isinstance(call.func, ast.Attribute) else None
        if name is None:
            if self.network_module and isinstance(call.func, ast.Call):
                self.fail(call, "indirect-call", "the callee is itself a call; a GET-only client names what it calls")
            elif keyword(call, "data") is not None or keyword(call, "method") is not None or any(
                    is_bytes_literal(arg) for arg in call.args):
                self.fail(call, "indirect-call",
                          "a call whose callee the gate cannot name passes data=, method= or a bytes body")
        else:
            if plain in DYNAMIC_IMPORTERS:
                self.check_dynamic_import(call, plain)
            if name == "sys.modules.get" and (not call.args or literal_str(call.args[0]) is None):
                self.fail(call, "reflection", "sys.modules.get() with a non-literal module name cannot be checked by the gate")
            self.check_subprocess(call, name)
            self.check_reference(call, name)
            if name == REQUEST:
                self.check_request(call)
            elif name == URLOPEN:
                self.check_urlopen(call, "urlopen()")
            elif name.endswith(".__init__") and module_matches(name, "urllib.request"):
                self.fail(call, "request-mutation", "calling Request.__init__ directly re-initialises a request, body included")
            elif plain in ("getattr", "delattr"):
                self.check_getattr(call, plain)
            elif plain == "setattr":
                self.check_setattr(call)
            elif plain == "vars":
                self.check_vars(call)
            elif name in PARTIAL_FACTORIES:
                self.check_partial(call, name)
            elif name in OPERATOR_FACTORIES:
                self.check_operator_factory(call, name)
        if attr in WRITE_ATTRS:
            self.fail(call, WRITE_ATTRS[attr], f".{attr}() writes to a connection or turns a request into a write")
        if attr == "send" and self.socket_module and not self.http_client_module:
            self.fail(call, "socket-write", ".send() is a raw socket write")
        if attr in NETWORK_CALL_ATTRS:
            self.fail(call, "network-call", f".{attr}() opens a connection or spawns a process; not allowed anywhere")
        if self.network_module and attr == "open":
            self.check_urlopen(call, "opener.open()")
        if self.http_client_module and attr in HTTP_CLIENT_ATTRS:
            self.check_http_client_call(call, attr)

    def visit_attribute(self, node: ast.Attribute) -> None:
        if node.attr in REFLECTION_ATTRS:
            self.fail(node, "reflection", f".{node.attr} reaches an object's namespace or class machinery directly; not allowed")
        elif node.attr == "__class__" and self.network_module:
            self.fail(node, "reflection", ".__class__ can swap the class that decides a request's verb; not allowed in the network module")

    def visit_subscript(self, node: ast.Subscript) -> None:
        key = literal_str(subscript_key(node))
        if key is not None:
            return
        value = node.value
        if isinstance(value, ast.Call):
            func = strip_builtins(self.resolve(value.func)[0])
            if func in ("globals", "locals", "vars"):
                self.fail(node, "reflection", f"{func}()[...] with a non-literal key cannot be checked by the gate")
            return
        base, _rooted = self.resolve(value)
        if base == "sys.modules":
            self.fail(node, "reflection", "sys.modules[...] with a non-literal key cannot be checked by the gate")

    def visit_assignment(self, node: ast.AST) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]  # type: ignore[attr-defined]
        value = getattr(node, "value", None)
        for target in targets:
            if isinstance(target, ast.Attribute):
                if target.attr in ("get_method", "__class__"):
                    self.fail(node, "request-mutation",
                              f"assigning .{target.attr} swaps the method that decides a request's verb; not allowed")
                elif target.attr in ("method", "data", "_data") and self.network_module:
                    ok = (target.attr == "method" and is_literal(value, "GET")) or (
                        target.attr in ("data", "_data") and is_none(value)
                    )
                    if not ok:
                        self.fail(node, "request-mutation",
                                  f"assigning .{target.attr} after construction can turn a GET into a write")
                elif target.attr in TARGET_ATTRS and self.network_module:
                    self.fail(node, "request-mutation",
                              f"assigning .{target.attr} re-points a built request at another scheme, host or "
                              "path; a GET-only client builds the URL once and leaves the request alone")
            elif isinstance(target, ast.Subscript):
                inner = target.value
                if isinstance(inner, ast.Call) and strip_builtins(self.resolve(inner.func)[0]) in ("vars", "globals", "locals"):
                    self.fail(node, "reflection", "assigning through a namespace dictionary rebinds names behind the gate's back")
                elif self.resolve(inner)[0] == "sys.modules":
                    self.fail(node, "reflection", "assigning into sys.modules injects a module the gate did not read")

    def visit_nodes(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self.visit_call(node)
            elif isinstance(node, ast.Attribute):
                self.visit_attribute(node)
            elif isinstance(node, ast.Subscript):
                self.visit_subscript(node)
            elif isinstance(node, ast.FunctionDef) and node.name == "get_method" and self.network_module:
                self.fail(node, "get-method-override",
                          "defining get_method can turn a GET request into a write")
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                self.visit_assignment(node)

    # -- entry -----------------------------------------------------------------------------
    def run(self) -> List[Finding]:
        try:
            tree = ast.parse("\n".join(self.lines) + "\n", filename=self.rel)
        except SyntaxError as exc:
            self.findings.append(Finding(self.rel, exc.lineno or 0, "syntax",
                                         f"cannot parse: {exc.msg}", exc.text or ""))
            return self.findings
        self.visit_imports(tree)
        self.visit_aliases(tree)
        self.visit_classes(tree)
        self.visit_aliases(tree)
        self.visit_nodes(tree)
        return self.findings


def python_files(root: str) -> List[str]:
    found: List[str] = []
    for top in PY_ROOTS:
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in EXCLUDED_DIRS and not f"{rel_dir}/{d}".startswith(FIXTURE_ROOT)
            )
            if rel_dir == FIXTURE_ROOT or rel_dir.startswith(FIXTURE_ROOT + "/"):
                continue
            for name in sorted(filenames):
                if name.endswith(".py"):
                    found.append(f"{rel_dir}/{name}")
    return found


def prose_files(root: str) -> List[str]:
    found: List[str] = []
    for top in PROSE_ROOTS:
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            for name in sorted(filenames):
                found.append(f"{rel_dir}/{name}")
    for rel in PROSE_FILES:
        if os.path.isfile(os.path.join(root, rel)):
            found.append(rel)
    return found


def read_lines(path: str) -> Optional[List[str]]:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()
    except UnicodeDecodeError:
        return None  # binary asset (image, archive): nothing a Bot could read as an instruction


def logical_lines(lines: List[str]) -> List[Tuple[int, str]]:
    """Join backslash-continued lines; each logical line carries its first physical number."""
    joined: List[Tuple[int, str]] = []
    start, buffer = 0, ""
    for number, line in enumerate(lines, 1):
        if not buffer:
            start = number
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        joined.append((start, buffer + line))
        buffer = ""
    if buffer:
        joined.append((start, buffer.rstrip()))
    return joined


class Allowlist:
    """``path: pattern`` lines; tracks which entries were used so stale ones can fail."""

    def __init__(self, root: str) -> None:
        self.entries: List[Tuple[int, str, "re.Pattern[str]"]] = []
        self.used: Set[int] = set()
        self.errors: List[Finding] = []
        path = os.path.join(root, ALLOWLIST)
        if not os.path.isfile(path):
            return
        lines = read_lines(path) or []
        for number, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ": " not in line:
                self.errors.append(Finding(ALLOWLIST, number, "allowlist-syntax",
                                           "expected '<path>: <regex>'", raw))
                continue
            rel, pattern = line.split(": ", 1)
            try:
                compiled = re.compile(pattern.strip())
            except re.error as exc:
                self.errors.append(Finding(ALLOWLIST, number, "allowlist-syntax",
                                           f"invalid regex: {exc}", raw))
                continue
            self.entries.append((number, rel.strip(), compiled))

    def allows(self, rel: str, text: str) -> bool:
        for number, path_glob, pattern in self.entries:
            if (rel == path_glob or fnmatch.fnmatchcase(rel, path_glob)) and pattern.search(text):
                self.used.add(number)
                return True
        return False

    def stale(self) -> List[Finding]:
        return [
            Finding(ALLOWLIST, number, "allowlist-stale",
                    f"entry for '{path_glob}' matched nothing; remove it", f"{path_glob}: {pattern.pattern}")
            for number, path_glob, pattern in self.entries
            if number not in self.used
        ]


def scan_python(root: str) -> Tuple[List[Finding], int]:
    findings: List[Finding] = []
    files = python_files(root)
    for rel in files:
        lines = read_lines(os.path.join(root, rel))
        if lines is None:
            findings.append(Finding(rel, 0, "encoding", "Python source is not valid UTF-8"))
            continue
        findings.extend(ModuleGate(rel, "\n".join(lines)).run())
    return findings, len(files)


def scan_prose(root: str, allowlist: Allowlist) -> Tuple[List[Finding], int]:
    findings: List[Finding] = []
    files = prose_files(root)
    scanned = 0
    for rel in files:
        lines = read_lines(os.path.join(root, rel))
        if lines is None:
            continue
        scanned += 1
        for number, line in logical_lines(lines):
            for rule, pattern, message in PROSE_RULES:
                if pattern.search(line) and not allowlist.allows(rel, line):
                    findings.append(Finding(rel, number, rule, f"{message}; allow-list it in {ALLOWLIST} "
                                            "only as a documented counter-example", line))
    return findings, scanned


def run(root: str) -> Dict[str, Any]:
    root = os.path.abspath(root)
    allowlist = Allowlist(root)
    ast_findings, python_count = scan_python(root)
    prose_findings, prose_count = scan_prose(root, allowlist)
    findings = ast_findings + prose_findings + allowlist.errors + allowlist.stale()
    findings.sort(key=lambda f: (f["file"], f["line"], f["rule"]))
    return {
        "ok": not findings,
        "errors": [dict(f) for f in findings],
        "scanned": {"python_files": python_count, "prose_files": prose_count,
                    "allowlist_entries": len(allowlist.entries), "allowlist_used": len(allowlist.used)},
        "network_client": NETWORK_CLIENT,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of prose")
    args = parser.parse_args(argv)
    result = run(args.root)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["errors"]:
        for finding in result["errors"]:
            print(Finding(**finding).render())
        print(f"{len(result['errors'])} problem(s)")
    else:
        scanned = result["scanned"]
        print(
            f"ok: AST gate over {scanned['python_files']} Python files (GET-only network path "
            f"{NETWORK_CLIENT}); prose scan over {scanned['prose_files']} files; "
            f"{scanned['allowlist_used']} allow-listed counter-example(s)"
        )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
