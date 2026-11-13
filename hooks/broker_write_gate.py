#!/usr/bin/env python3
"""broker_write_gate.py: the PreToolUse backstop that denies broker-write calls. Stdlib only; no network.

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased changes, commit 75a4c79
(hooks/exchange_write_gate.py): the PreToolUse decision object and the quiet handling of reads and malformed input.
The deny decision, the Grok Build, Claude Code and Cursor event shapes, the MCP group and the broker patterns are
Aegis Desk's own.

What it is
----------
Layer 5 of the desk's no-trade guarantee, and only a backstop. The guarantee rests on layers 1 and 2: the repository
holds no broker write code (proved by ``scripts/check_no_write_paths.py``) and the desk computer holds no
trade-capable credential. This hook stops a model from writing a broker call of its own through a shell or an MCP
tool. ``hooks/hooks.json`` registers it for Claude Code and Grok Build (a shell group and an MCP group);
``hooks/cursor-hooks.json`` registers it for Cursor with ``--runtime cursor``. Grok Bot loads no plugin hooks, so the
gate does not run there; its Auto-review rules are the runtime control. Whether each runtime runs the hook and honours
the deny is checked at release.

Input (one JSON object on stdin)
--------------------------------
* Claude Code: ``hook_event_name`` ``PreToolUse``, ``tool_name`` (``Bash``, ``PowerShell``, ``Monitor``,
  ``mcp__<server>__<tool>`` or ``mcp__plugin_<plugin>_<server>__<tool>``) and ``tool_input`` (an object; the shell
  tools carry ``command``).
* Grok Build: ``hookEventName`` ``pre_tool_use``, ``toolName`` (``run_terminal_command`` for the shell,
  ``<server>__<tool>`` for MCP), ``toolInput``, ``toolInputTruncated``, plus the snake_case aliases ``tool_name``,
  ``tool_input`` and ``hook_event_name`` ``PreToolUse``. Every name present is evaluated, with every input present.
* Cursor (``--runtime cursor``): ``beforeShellExecution`` sends a top-level ``command``; ``beforeMCPExecution`` sends
  ``mcp_server_name``, ``tool_name`` and ``tool_input`` as a JSON string (decoded; text that does not decode is read
  as one string), plus ``url`` and ``mcp_server_url`` for an HTTP or SSE server or ``command`` (the server's launch
  command) for a stdio server. The checked name is ``<mcp_server_name>__<tool_name>``. A top-level ``command`` is read
  as a shell call only on ``beforeShellExecution`` (or when the event names no tool and no server), never on an MCP
  call, where it is the launch command and not the call. A ``preToolUse`` event is read like Claude Code's.

Only the pre-tool events are judged (``PreToolUse``, ``pre_tool_use``, ``preToolUse``, ``beforeShellExecution``,
``beforeMCPExecution``). Any other event name gets no opinion, because exit 2 blocks events such as ``Stop`` or
``UserPromptSubmit``. The shell tools are ``Bash``, ``PowerShell``, ``Monitor`` (Claude Code's background monitor runs
a shell command), ``run_terminal_command`` and ``Shell``, and any other tool whose input holds a string ``command`` and
whose name is not an MCP name is read the same way; any other name that contains ``__`` is an MCP tool; every other
tool (``Write``, ``Edit``, ``Read`` ...) gets no opinion.

Decisions
---------
The gate reads only the call: the command text, the MCP tool name and the MCP input. It never opens a file the command
names and never follows an import, so a write inside a script the command runs is not seen.

Shell commands are split, quote-aware, into simple commands on unquoted ``;``, ``&``, ``|``, newlines, parentheses,
backticks and ``$(``. A command made only of read commands (:data:`READ_COMMANDS`: ``grep``, ``git``, ``cat``,
``ls``, ``find`` ...) gets no opinion, so reading, searching and committing write samples is free. ``git`` reads only
through :data:`GIT_READ_SUBCOMMANDS` (``status``, ``log``, ``show``, ``diff``, ``grep``, ``blame``, ``commit`` ...,
after git's global options): ``rebase -x``, ``submodule foreach``, ``bisect run``, ``difftool --extcmd``,
``filter-branch`` and every other subcommand are scanned whole. A read command stops being one when it carries an
assignment prefix (``LESSOPEN=... less``), an option that runs a program (:data:`READ_EXEC_OPTIONS`: ``find -exec``,
``git -c``, ``git --config-env``, ``git grep -O``, ``rg --pre``, ``sort --compress-program`` ...; a long one also when
abbreviated, as git and the GNU tools accept, and git's ``-O`` inside a short-option cluster) or a ``!`` (``git``
aliases, ``less``), and any command with a ``$(`` or a backtick inside double quotes is not read-only.
Any other command is scanned whole, so a pipe into a shell, an interpreter, a wrapper or a variable (``| bash``,
``bash -c``, ``python3 -c``, ``xargs``, ``env``, ``nice sh``, ``$SHELL``) is covered. The scan reads the text and a
copy with quotes, backslashes and ``$IFS`` removed. It denies:

* S1 an order call: ``place_order(``, ``submitOrder(``, ``create_order(``, ``send_order(``, ``cancel_order_by_id(``,
  ``.sell(``, ``order_buy_limit(`` and the like (:data:`ORDER_CALL_RE`), with no target needed;
* S2 an HTTP client (``curl``, ``wget``, HTTPie, ``Invoke-WebRequest`` ...; a URL scheme counts) with a write marker
  (``-X POST|PUT|PATCH|DELETE``, ``-XPOST``, ``--request``, ``--method``, ``-Method``, a body flag such as ``-d``,
  ``--data*``, ``-F``, ``--json``, ``-T``, ``--post-data``, ``--body-data``, ``-Body``, or an HTTPie method or data
  item; read in every spelling the client accepts: curl short-option clusters such as ``-sXPOST`` and ``-sd@file``,
  abbreviated long options such as ``--data-b`` or ``--post-d``, PowerShell's abbreviated and ``:``-bound
  parameters such as ``-Meth Post`` and ``-Method:Post``, and brace expansion such as ``{curl,-XPOST,...}``) and a
  target;
* S3 an HTTP library write (``.post(``, ``.put(``, ``.patch(``, ``.delete(``, ``->post(``, ``.post_form(``,
  ``.request("POST"``, ``Request(... data=``, ``urlopen(... data=``, a ``urlopen`` or ``Request`` call with a second
  positional argument (the request body), ``method: "POST"``, ``Net::HTTP::Post``, ``CURLOPT_POST``) and a target;
* S4 any HTTP client or library call that names a broker host, reads included (market-data reads too: the desk's
  tools read market data from Python, never through a shell or an MCP tool); an account or order path there is
  labelled as one.

A target is a broker host (:data:`BROKER_HOSTS`, any subdomain) or an account or order path (``/v<n>/accounts``,
``/accounts/.../orders``, an ``/order`` or ``/orders`` path segment).

MCP calls deny when, after removing a leading ``mcp__``, the tool name split into whole tokens (non-alphanumerics and
camelCase boundaries) holds one of :data:`MCP_VERBS` (``order``, ``orders``, ``trade``, ``trades``, ``cancel``,
``replace``, ``submit``, ``buy``, ``sell``, ``liquidate``, ``exercise``) or a token that runs a verb into its object
(:data:`COMPOUND_VERB_RE`: ``PLACEORDER``, ``placeorder``, ``selltoclose``, ``closeallpositions``), so the server
names ``snaptrade`` and ``alpaca`` pass; when the tool closes a position (``close`` with ``position`` or
``positions``: ``close_position``, ``close_all_positions``) or opens one (a tool name that starts with ``open`` and
names a position: ``open_position``; ``get_open_position`` passes); or when the input holds a key equal to ``order``,
``orders``, ``trade`` or ``trades`` (any case; ``orderBy`` passes) whose value is an object, a list or text other than
a sort direction (``{"order": "desc"}`` passes), an identifier-shaped value with a verb token (``sell_to_close``,
``alpaca_place_order``, a URL ending in ``/orders``), or any string the shell scan denies. A string value that holds
a JSON object or array (``"params_json": "{...}"``) is decoded and walked too. Known false positives, by design: an
MCP tool whose name or identifier input carries one of the verbs (a review tool's ``submit``, a ``replace_text``
tool, a server name written ``SnapTrade``, a one-word search for ``order``), free text that quotes a write sample (a
pull request body), and on the shell side a command that writes a file holding a write sample (a heredoc), a read
whose flag is a curl body flag (``curl -G --data-urlencode``, ``wget -T``) on a broker host, a git subcommand outside
the read list whose text holds a write sample (``git stash push -m``), and any HTTP call to a broker host (a broker's own web page and its
market-data reads included).
An input more than :data:`MAX_DEPTH` levels deep, with more than :data:`MAX_NODES` nodes or with more than
:data:`MAX_TEXT` characters of strings is denied as unreadable, and so is stdin JSON nested too deeply to parse; a shell
command longer than :data:`MAX_TEXT` characters is denied too unless it only reads, so a scan always ends well inside
the 10-second hook timeout. A call whose ``toolInputTruncated`` (or ``tool_input_truncated``) is
``true`` is denied when it is a shell or MCP call, even when the visible command reads: the gate cannot read all of
it.

Not covered: aliases and functions defined earlier, variables, encodings such as base64, a script written first and
run later, a broker command-line tool that speaks no HTTP, a broker host hidden behind a proxy or a local address
without an account or order path, a shell-running tool that the runtime's matcher does not send to the gate, an MCP
tool that changes a position under a name none of the rules above names, and every route the patterns do not name.

Output and exit codes
---------------------
* Deny: exit 2. stdout carries one JSON line with Grok Build's canonical ``{"decision": "deny", "reason": R}`` and
  Claude Code's ``"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": R}``; with ``--runtime cursor`` it is ``{"permission": "deny", "user_message": R,
  "agent_message": R}`` (the field names Cursor documents). R also goes to stderr. R is ``Aegis Desk denied this call: it looks like a broker write (<label>). This desk never
  places, modifies or cancels orders; you act in your brokerage account.``: printable ASCII, at most 250 characters,
  the label cut to :data:`MAX_LABEL` characters and the boundary clause never cut. Exit 2 is the deny in all three
  runtimes.
* No opinion: exit 0 with nothing on stdout or stderr. The runtime's own permission flow decides. The gate never
  approves anything. In Cursor mode a ``beforeShellExecution`` or ``beforeMCPExecution`` event always gets an explicit
  answer, because Cursor documents that a ``failClosed`` hook with no output fails and blocks the call: no opinion
  there is exit 0 with ``{"permission": "ask"}`` on stdout, so Cursor asks you, as it does without the hook. The gate
  never answers ``allow``.
* Malformed input (documented fail-open): stdin that is not UTF-8 or not JSON, JSON that is not an object, a missing
  tool name, or a shell call without a string command gets no opinion (exit 0, no output). An uncaught error would
  exit 1, which Claude Code and Grok Build treat as a non-blocking error (they fail open, as they do on a hook
  timeout) and Cursor's ``failClosed`` treats as a deny. In Cursor mode a parsed permission event with a malformed
  field gets the explicit ``ask``; stdin that does not parse stays silent, which Cursor's ``failClosed`` blocks.
* Arguments: none, or exactly ``--runtime claude|grok|cursor`` (also ``--runtime=<name>``; claude and grok are the
  default). Anything else prints ``broker_write_gate: unknown arguments [...]`` on stderr and exits 1, never 2: an
  argument error must not read as a deny of every call, which is why argparse (whose usage error exits 2) is not used.
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

PRE_TOOL_EVENTS = frozenset({"PreToolUse", "pre_tool_use", "preToolUse", "beforeShellExecution", "beforeMCPExecution"})
EVENT_KEYS: Tuple[str, ...] = ("hook_event_name", "hookEventName")
NAME_KEYS: Tuple[str, ...] = ("tool_name", "toolName")
INPUT_KEYS: Tuple[str, ...] = ("tool_input", "toolInput")
TRUNCATED_KEYS: Tuple[str, ...] = ("toolInputTruncated", "tool_input_truncated")
SHELL_TOOLS = frozenset({"Bash", "PowerShell", "Monitor", "run_terminal_command", "Shell"})
CURSOR_PERMISSION_EVENTS: Tuple[str, ...] = ("beforeShellExecution", "beforeMCPExecution")
CURSOR_ASK: Dict[str, str] = {"permission": "ask"}
"""Cursor's no-opinion answer: a fail-closed hook that prints nothing fails, so Cursor mode always answers."""
RUNTIMES: Dict[str, str] = {"claude": "default", "grok": "default", "cursor": "cursor"}

READ_COMMANDS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "git", "cat", "less", "more", "head", "tail", "ls", "find", "wc", "diff",
    "cmp", "file", "stat", "jq", "sort", "uniq", "cut", "tr", "echo", "printf", "pwd", "cd", "test", "true", "false",
    "which", "type", "nl", "column", "tree", "du", "df", "date", "sha256sum", "shasum", "md5sum", "basename",
    "dirname", "realpath", "readlink", "read",
})
"""Commands that read and run nothing; a command made only of these gets no opinion."""
GIT_READ_SUBCOMMANDS = frozenset({
    "status", "log", "show", "diff", "grep", "blame", "ls-files", "ls-tree", "rev-parse", "rev-list", "describe",
    "branch", "tag", "commit", "shortlog", "cat-file", "show-ref",
})
"""The git subcommands that run no command given on the command line; any other git subcommand is scanned whole."""
GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"})
READ_EXEC_OPTIONS: Dict[str, Tuple[str, ...]] = {
    "find": ("-exec", "-execdir", "-ok", "-okdir"),
    "git": ("-c", "--config-env", "-O", "--open-files-in-pager", "--upload-pack", "--receive-pack", "--exec",
            "--exec-path", "--ext-diff", "--textconv"),
    "rg": ("--pre",),
    "ag": ("--pager",),
    "sort": ("--compress-program",),
}
"""Options with which a read command runs a program; such a command is scanned whole. A long option also counts when
it is abbreviated to MIN_ABBREVIATION or more characters (git and GNU tools accept unambiguous prefixes), and git's
``-O`` counts inside a cluster of short options (``-nO<pager>``)."""
ATTACHED_OPTIONS = frozenset({"-O"})
CLUSTER_EXEC_LETTERS: Dict[str, str] = {"git": "O"}
MIN_ABBREVIATION = 5
BANG_COMMANDS = frozenset({"git", "less", "more"})
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HTTPIE_COMMANDS = frozenset({"http", "https", "xh", "xhs", "httpie"})
HTTPIE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
HTTPIE_DATA_RE = re.compile(r"^[A-Za-z0-9_.\[\]@-]+(?::=|=)(?!=)")

BROKER_HOSTS: Tuple[str, ...] = (
    "snaptrade.com", "alpaca.markets", "tastytrade.com", "tastyworks.com", "schwabapi.com",
    "schwab.com", "robinhood.com", "interactivebrokers.com", "ibkr.com", "etrade.com", "webull.com", "public.com",
    "tradestation.com", "fidelity.com", "vanguard.com", "firstrade.com",
)
BROKER_HOST_RE = re.compile(r"(?i)(?<![a-z0-9-])(?:%s)(?![a-z0-9-])" % "|".join(re.escape(host) for host in BROKER_HOSTS))
BROKER_URL_RE = re.compile(BROKER_HOST_RE.pattern + r"(?::\d{1,5})?(/[^\s'\"<>`|;&(){},\\]{0,500})?")
ACCOUNT_PATH_RE = re.compile(r"(?i)/v\d+/accounts\b|/accounts/[^\s'\"]{0,200}?/orders?\b|/orders?\b(?![\w.-])")
HTTP_CLIENT_RE = re.compile(r"(?i)(?<![\w.-])(?:curl|wget2?|https?|xhs?|httpie|aria2c|invoke-webrequest|"
                            r"invoke-restmethod|iwr|irm)(?:\.exe)?(?![\w.-])")
METHOD_FLAG_RE = re.compile(r"(?:^|\s)(?:-X|--request|(?i:-Method)|--method)(?:\s*=\s*|\s+|)['\"]?"
                            r"(?i:POST|PUT|PATCH|DELETE)\b")
BODY_FLAG_RE = re.compile(r"(?:^|\s)(?:-d|--data(?:-binary|-raw|-urlencode|-ascii)?|-F|--form(?:-string)?|--json|-T|"
                          r"--upload-file|--post-data|--post-file|--body-data|--body-file|(?i:-Body|-InFile))"
                          r"(?=$|[\s='\"@{])")
CLIENT_SPLIT_RE = re.compile(r"([;&|\n()`])|[\s,{}]+")
"""Words for :func:`client_write`: whitespace, commas and braces separate words (brace expansion included); the
captured characters end a simple command."""
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CURL_BODY_LONG: Tuple[str, ...] = ("data", "data-binary", "data-raw", "data-urlencode", "data-ascii", "form",
                                   "form-string", "json", "upload-file")
CURL_SHORT_BODY = "dFT"
CURL_SHORT_VALUE = "AbcCDeEHKmoPQrtuUwxyYz"
WGET_BODY_LONG: Tuple[str, ...] = ("post-data", "post-file", "body-data", "body-file")
POWERSHELL_BODY: Tuple[str, ...] = ("body", "infile", "form")
POWERSHELL_METHOD: Tuple[str, ...] = ("method", "custommethod")
ORDER_CALL_RE = re.compile(
    r"(?i)\b(?:place|submit|cancel|replace|modify|amend|create|send)[a-z0-9_]{0,40}?orders?(?:_by_[a-z0-9_]{1,40})?"
    r"\s*\("
    r"|\.(?:buy|sell)(?:_?to_?(?:open|close))?\s*\("
    r"|\border_?(?:buy|sell)[a-z0-9_]{0,40}\s*\(")
LIBRARY_WRITE_RE = re.compile(
    r"(?:\.|->)(?:post|put|patch|delete)\s*\("
    r"|\.request\s*\(\s*['\"](?i:POST|PUT|PATCH|DELETE)['\"]"
    r"|\bRequest\s*\(\s*['\"](?i:POST|PUT|PATCH|DELETE)['\"]"
    r"|\bRequest\s*\([^)]{0,500}?\b(?:data\s*=|method\s*=\s*['\"](?i:POST|PUT|PATCH|DELETE))"
    r"|\burlopen\s*\([^)]{0,500}?\bdata\s*="
    r"|\b(?:urlopen|Request)\s*\(\s*(?!['\"](?i:GET|HEAD|OPTIONS)['\"])(?:'[^']{0,500}'|\"[^\"]{0,500}\"|"
    r"[\w.\[\]]{1,200}(?:\([^()]{0,500}\))?)\s*,\s*(?![A-Za-z_]\w*\s*=)[^\s)]"
    r"|\.post_form\s*\("
    r"|(?i:\bmethod)['\"]?\s*[:=]\s*['\"](?i:POST|PUT|PATCH|DELETE)['\"]"
    r"|Net::HTTP::(?:Post|Put|Patch|Delete)\b"
    r"|\bCURLOPT_(?:POST|POSTFIELDS|CUSTOMREQUEST|UPLOAD)\b")
LINE_CONTINUATION_RE = re.compile(r"\\\r?\n")
IFS_RE = re.compile(r"\$\{IFS\}|\$IFS\b")
QUOTING_RE = re.compile(r"[\\'\"]")

MCP_PREFIX = "mcp__"
MCP_VERBS = frozenset({"order", "orders", "trade", "trades", "cancel", "replace", "submit", "buy", "sell", "liquidate",
                       "exercise"})
COMPOUND_VERB_RE = re.compile(r"^(?:place|submit|cancel|replace|modify|amend|create|send|new)orders?$"
                              r"|^(?:buy|sell)(?:to)?(?:open|close)$|^(?:close|liquidate)(?:all)?positions?$")
"""A token that runs an order verb and its object together (``PLACEORDER``, ``selltoclose``)."""
POSITION_TOKENS = frozenset({"position", "positions"})
MCP_KEYS = frozenset({"order", "orders", "trade", "trades"})
SORT_DIRECTIONS = frozenset({"asc", "desc", "ascending", "descending"})
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")
TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
MAX_DEPTH = 32
MAX_NODES = 10000
MAX_TEXT = 200000
"""The most characters scanned in one shell command, or in all the strings of one MCP input, within the timeout."""

TRUNCATED_SHELL = "a truncated shell command (the gate cannot read all of it)"
TRUNCATED_MCP = "a truncated MCP tool input (the gate cannot read all of it)"
TOO_DEEP = "an MCP input too deep to read"
TOO_LARGE = "an MCP input too large to read"
TOO_LONG = "a shell command too long to read"
UNPARSABLE = "an input nested too deeply to read"
REASON_PREFIX = "Aegis Desk denied this call: it looks like a broker write ("
BOUNDARY = "This desk never places, modifies or cancels orders; you act in your brokerage account."
MAX_LABEL = 80
MAX_REASON = 250


# --------------------------------------------------------------------------------------------
# Shell commands
# --------------------------------------------------------------------------------------------


def split_segments(command: str) -> Tuple[List[str], bool]:
    """``(simple commands, substitution inside double quotes)``: the line split on unquoted ``;``, ``&``, ``|``,
    newlines, parentheses, backticks and ``$(``; single and double quotes and backslashes are honoured."""
    segments: List[str] = []
    current: List[str] = []
    quote = ""
    hidden = False
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if quote == "'":
            current.append(char)
            if char == "'":
                quote = ""
        elif quote == '"':
            current.append(char)
            if char == "\\" and index + 1 < length:
                current.append(command[index + 1])
                index += 1
            elif char == '"':
                quote = ""
            elif char == "`" or (char == "$" and command[index + 1:index + 2] == "("):
                hidden = True
        elif char == "\\" and index + 1 < length:
            current.append(char)
            current.append(command[index + 1])
            index += 1
        elif char in ("'", '"'):
            quote = char
            current.append(char)
        elif char in ";&|\n()`":
            segments.append("".join(current))
            current = []
        elif char == "$" and command[index + 1:index + 2] == "(":
            segments.append("".join(current))
            current = []
            index += 1
        else:
            current.append(char)
        index += 1
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()], hidden


def command_name(word: str) -> str:
    """The program a word names: quotes and backslashes removed, the path's base name, lower case, no ``.exe``."""
    bare = QUOTING_RE.sub("", word)
    bare = re.split(r"[/\\]", bare)[-1].lower() if bare else ""
    return bare[:-4] if bare.endswith(".exe") else bare


def _exec_option(name: str, bare: str) -> bool:
    head = bare.split("=", 1)[0]
    for option in READ_EXEC_OPTIONS.get(name, ()):
        if head == option or (option in ATTACHED_OPTIONS and bare.startswith(option)):
            return True
        if option.startswith("--") and head.startswith("--") and len(head) >= MIN_ABBREVIATION and option.startswith(head):
            return True
    letters = CLUSTER_EXEC_LETTERS.get(name, "")
    cluster = re.match(r"^-([A-Za-z]+)", bare) if letters and not bare.startswith("--") else None
    return cluster is not None and any(letter in cluster.group(1) for letter in letters)


def _runs_a_program(name: str, words: Sequence[str]) -> bool:
    return any(_exec_option(name, QUOTING_RE.sub("", word)) for word in words)


def git_subcommand(words: Sequence[str]) -> Optional[str]:
    """The git subcommand after git's global options (``-C <dir>``, ``--no-pager`` ...), or ``None``."""
    index = 0
    while index < len(words):
        bare = QUOTING_RE.sub("", words[index])
        if not bare.startswith("-"):
            return bare
        index += 2 if bare in GIT_VALUE_OPTIONS else 1
    return None


def is_read(segment: str) -> bool:
    """True when the simple command only reads (module notes)."""
    words = segment.split()
    if not words:
        return True
    if ASSIGNMENT_RE.match(words[0]):
        return False
    name = command_name(words[0])
    if name not in READ_COMMANDS:
        return False
    if name == "git":
        subcommand = git_subcommand(words[1:])
        if subcommand is not None and subcommand not in GIT_READ_SUBCOMMANDS:
            return False
    if _runs_a_program(name, words[1:]):
        return False
    return not (name in BANG_COMMANDS and "!" in segment)


def variants(text: str) -> List[str]:
    """The text, and a copy without line continuations, ``$IFS``, quotes and backslashes (when it differs)."""
    plain = QUOTING_RE.sub("", IFS_RE.sub(" ", LINE_CONTINUATION_RE.sub("", text)))
    return [text] if plain == text else [text, plain]


def httpie_write(text: str) -> bool:
    """An HTTPie (``http``, ``https``, ``xh``) command with a method word or a data item (``name=value``)."""
    for segment in split_segments(text)[0]:
        words = segment.split()
        if not words or command_name(words[0]) not in HTTPIE_COMMANDS:
            continue
        for word in words[1:]:
            bare = QUOTING_RE.sub("", word)
            if bare.upper() in HTTPIE_METHODS or (not bare.startswith("-") and HTTPIE_DATA_RE.match(bare)):
                return True
    return False


def _long_option(word: str, options: Sequence[str]) -> bool:
    """``--name`` (before any ``=``) is one of ``options`` or an abbreviation of three or more characters."""
    name = word[2:].split("=", 1)[0].lower()
    return any(name == option or (len(name) >= 3 and option.startswith(name)) for option in options)


def _write_method(value: str) -> bool:
    return QUOTING_RE.sub("", value).strip().upper() in WRITE_METHODS


def _curl_word(word: str, following: str) -> bool:
    """A curl write marker: a body option, or a write method, long (abbreviations too) or in a short-option cluster."""
    if word.startswith("--"):
        if _long_option(word, CURL_BODY_LONG):
            return True
        if _long_option(word, ("request",)):
            return _write_method(word.split("=", 1)[1] if "=" in word else following)
        return False
    if not re.match(r"^-[A-Za-z]", word):
        return False
    letters = word[1:]
    for index, letter in enumerate(letters):
        if letter in CURL_SHORT_BODY:
            return True
        if letter == "X":
            return _write_method(letters[index + 1:] or following)
        if letter in CURL_SHORT_VALUE or not letter.isalpha():
            return False
    return False


def _wget_word(word: str, following: str) -> bool:
    if not word.startswith("--"):
        return False
    if _long_option(word, WGET_BODY_LONG):
        return True
    return _long_option(word, ("method",)) and _write_method(word.split("=", 1)[1] if "=" in word else following)


def _powershell_word(word: str, following: str) -> bool:
    """A PowerShell body or write-method parameter, abbreviated or bound with ``:`` (``-Meth Post``, ``-Method:Post``)."""
    if not re.match(r"^-[A-Za-z]", word):
        return False
    name, _, bound = word[1:].partition(":")
    name = name.lower()
    if len(name) < 2:
        return False
    if any(option.startswith(name) for option in POWERSHELL_BODY):
        return True
    return any(option.startswith(name) for option in POWERSHELL_METHOD) and _write_method(bound or following)


CLIENT_CHECKS: Dict[str, Callable[[str, str], bool]] = {
    "curl": _curl_word, "wget": _wget_word, "wget2": _wget_word, "invoke-webrequest": _powershell_word,
    "invoke-restmethod": _powershell_word, "iwr": _powershell_word, "irm": _powershell_word,
}


def client_write(text: str) -> bool:
    """A write marker in the words after any curl, wget or PowerShell web cmdlet, up to the end of its simple command,
    in one pass over the text; brace expansion (``{curl,-XPOST,...}``) is read as words too."""
    segments: List[List[str]] = [[]]
    for piece in CLIENT_SPLIT_RE.split(text):
        if not piece:
            continue
        if len(piece) == 1 and piece in ";&|\n()`":
            segments.append([])
        else:
            segments[-1].append(piece)
    for words in segments:
        check: Optional[Callable[[str, str], bool]] = None
        for index, word in enumerate(words):
            client = CLIENT_CHECKS.get(command_name(word))
            if client is not None:
                check = client
            elif check is not None and check(word, words[index + 1] if index + 1 < len(words) else ""):
                return True
    return False


def broker_call(text: str) -> Optional[str]:
    """The first broker-host URL in ``text``, as ``host/path``."""
    match = BROKER_URL_RE.search(text)
    if match is None:
        return None
    path = match.group(1) or ""
    host = BROKER_HOST_RE.match(text, match.start())
    return "%s%s" % (host.group(0) if host else match.group(0), path[:60])


def _finding(text: str) -> Optional[str]:
    call = ORDER_CALL_RE.search(text)
    if call is not None:
        return "an order call (%s)" % call.group(0).rstrip("( \t\r\n")
    host = BROKER_HOST_RE.search(text)
    path = ACCOUNT_PATH_RE.search(text)
    target = host or path
    client = HTTP_CLIENT_RE.search(text) is not None
    library = LIBRARY_WRITE_RE.search(text) is not None
    if target is not None:
        if client and (METHOD_FLAG_RE.search(text) or BODY_FLAG_RE.search(text) or httpie_write(text)
                       or client_write(text)):
            return "an HTTP write to %s" % target.group(0)
        if library:
            return "an HTTP library write to %s" % target.group(0)
    if (client or library) and host is not None:
        if path is not None:
            return "a brokerage account path (%s)" % path.group(0)
        call_target = broker_call(text)
        if call_target is not None:
            return "a call to a broker host (%s)" % call_target
    return None


def shell_finding(text: str) -> Optional[str]:
    """The S1-S4 finding in ``text`` (or in its unquoted copy), else ``None``."""
    for candidate in variants(text):
        found = _finding(candidate)
        if found is not None:
            return found
    return None


def check_shell(command: str) -> Optional[str]:
    """A command made only of reads gets no opinion; any other command is scanned whole."""
    segments, hidden = split_segments(command)
    if not hidden and all(is_read(segment) for segment in segments):
        return None
    if len(command) > MAX_TEXT:
        return TOO_LONG
    return shell_finding(command)


# --------------------------------------------------------------------------------------------
# MCP calls
# --------------------------------------------------------------------------------------------


def tokens(name: str) -> List[str]:
    """Whole lower-case tokens of a name: split on non-alphanumerics and camelCase boundaries."""
    return [part.lower() for part in TOKEN_RE.findall(name)]


class Unreadable(Exception):
    """The MCP input is deeper than MAX_DEPTH or larger than MAX_NODES."""


def _json_text(item: str) -> Any:
    """A string that holds a JSON object or array, decoded; anything else is ``None``."""
    stripped = item.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        decoded = json.loads(stripped)
    except ValueError:
        return None
    except RecursionError:
        raise Unreadable(TOO_DEEP)
    return decoded if isinstance(decoded, (dict, list)) else None


def walk(value: Any) -> Iterator[Tuple[str, Any, Any]]:
    """``("key", key, child)`` and ``("value", scalar, None)`` triples of a JSON value, iteratively, within the limits.
    A string value that holds a JSON object or array is walked too, one level deeper, after it is yielded."""
    stack: List[Tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > MAX_DEPTH:
            raise Unreadable(TOO_DEEP)
        if nodes > MAX_NODES:
            raise Unreadable(TOO_LARGE)
        if isinstance(item, dict):
            for key, child in item.items():
                nodes += 1
                yield "key", key, child
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            for child in item:
                stack.append((child, depth + 1))
        else:
            yield "value", item, None
            if isinstance(item, str):
                decoded = _json_text(item)
                if decoded is not None:
                    stack.append((decoded, depth + 1))


def decode_input(tool_input: Any) -> Any:
    """A JSON-string input decoded (Cursor); text that does not decode stays one string."""
    if isinstance(tool_input, str):
        try:
            return json.loads(tool_input)
        except (ValueError, RecursionError):
            return tool_input
    return tool_input


def order_verb(name: str) -> Optional[str]:
    """The first whole token of ``name`` that is an order verb or runs one into its object, else ``None``."""
    for token in tokens(name):
        if token in MCP_VERBS or COMPOUND_VERB_RE.match(token):
            return token
    return None


def position_verb(bare: str) -> Optional[str]:
    """A tool that closes a position (``close`` with ``position(s)``) or opens one (its tool name starts ``open``)."""
    parts = tokens(bare.rsplit("__", 1)[-1])
    if not any(part in POSITION_TOKENS for part in parts):
        return None
    if "close" in parts:
        return "close"
    return "open" if parts[0] == "open" else None


def order_shaped(value: Any) -> bool:
    """What an order key holds when it is an order: an object, a list, or text other than a sort direction."""
    if isinstance(value, (dict, list)):
        return True
    return isinstance(value, str) and value.strip().lower() not in SORT_DIRECTIONS


def check_mcp(name: str, tool_input: Any) -> Optional[str]:
    bare = name[len(MCP_PREFIX):] if name.startswith(MCP_PREFIX) else name
    verb = order_verb(bare) or position_verb(bare)
    if verb is not None:
        return "an MCP tool named %s (%s)" % (bare, verb)
    scanned = 0
    try:
        for kind, item, child in walk(decode_input(tool_input)):
            if not isinstance(item, str):
                continue
            if kind == "key":
                if item.lower() in MCP_KEYS and order_shaped(child):
                    return "an MCP input key %r" % item
                continue
            scanned += len(item)
            if scanned > MAX_TEXT:
                return TOO_LARGE
            if IDENTIFIER_RE.match(item) and order_verb(item) is not None:
                return "an MCP input value %r" % item
            found = shell_finding(item)
            if found is not None:
                return "%s in the MCP input" % found
    except Unreadable as exc:
        return str(exc)
    return None


def is_mcp(name: str) -> bool:
    return "__" in name.strip("_")


# --------------------------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------------------------


def _present(event: Dict[str, Any], keys: Sequence[str]) -> List[Any]:
    found: List[Any] = []
    for key in keys:
        value = event.get(key)
        if value is not None and value not in found:
            found.append(value)
    return found


def pre_tool_event(event: Dict[str, Any]) -> bool:
    """False when an event name is present and is not a pre-tool event (a non-string name included)."""
    for key in EVENT_KEYS:
        if key in event:
            value = event[key]
            if not (isinstance(value, str) and value in PRE_TOOL_EVENTS):
                return False
    return True


def decide_tool_call(event: Dict[str, Any]) -> Optional[str]:
    """Claude Code and Grok Build (and Cursor's preToolUse): every tool name present, with every input present."""
    names = [name for name in _present(event, NAME_KEYS) if isinstance(name, str) and name]
    inputs = _present(event, INPUT_KEYS)
    truncated = any(event.get(key) is True for key in TRUNCATED_KEYS)
    has_command = any(isinstance(item, dict) and isinstance(item.get("command"), str) for item in inputs)
    for name in names:
        if name in SHELL_TOOLS or (has_command and not is_mcp(name)):
            if truncated:
                return TRUNCATED_SHELL
            for tool_input in inputs:
                command = tool_input.get("command") if isinstance(tool_input, dict) else None
                if isinstance(command, str):
                    found = check_shell(command)
                    if found is not None:
                        return found
        elif is_mcp(name):
            if truncated:
                return TRUNCATED_MCP
            for tool_input in inputs or [None]:
                found = check_mcp(name, tool_input)
                if found is not None:
                    return found
    return None


def decide_cursor(event: Dict[str, Any]) -> Optional[str]:
    """Cursor: beforeShellExecution's top-level command is a shell call. A beforeMCPExecution call, or any event that
    names a tool or an MCP server, is checked as ``<server>__<tool>``: its top-level ``command`` is then a stdio
    server's launch command, not the call, and is not read."""
    hook = event.get("hook_event_name")
    tool = event.get("tool_name")
    named = isinstance(tool, str) and bool(tool)
    command = event.get("command")
    if hook == "beforeShellExecution" or (hook != "beforeMCPExecution" and not named and "mcp_server_name" not in event):
        return check_shell(command) if isinstance(command, str) else None
    if not named:
        return None
    server = event.get("mcp_server_name")
    if isinstance(server, str) and server:
        return check_mcp("%s__%s" % (server, tool), event.get("tool_input"))
    if hook == "beforeMCPExecution":
        return check_mcp(tool, event.get("tool_input"))
    return decide_tool_call(event)


def decide(event: Any, runtime: str = "default") -> Optional[str]:
    """The deny label for ``event``, or ``None`` for no opinion (module notes)."""
    if not isinstance(event, dict) or not pre_tool_event(event):
        return None
    if runtime == "cursor":
        return decide_cursor(event)
    return decide_tool_call(event)


def reason_text(label: str) -> str:
    """The deny reason: printable ASCII, the label cut to MAX_LABEL characters, the boundary clause whole."""
    shown = "".join(char if 32 <= ord(char) < 127 else "?" for char in " ".join(label.split()))
    if len(shown) > MAX_LABEL:
        shown = shown[:MAX_LABEL - 3] + "..."
    return "%s%s). %s" % (REASON_PREFIX, shown, BOUNDARY)


def deny_payload(reason: str, runtime: str) -> Dict[str, Any]:
    if runtime == "cursor":
        return {"permission": "deny", "user_message": reason, "agent_message": reason}
    return {"decision": "deny", "reason": reason,
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def parse_runtime(args: Sequence[str]) -> Optional[str]:
    """``default`` or ``cursor`` for the accepted argument forms, else ``None``."""
    if not args:
        return "default"
    if len(args) == 2 and args[0] == "--runtime":
        return RUNTIMES.get(args[1])
    if len(args) == 1 and args[0].startswith("--runtime="):
        return RUNTIMES.get(args[0][len("--runtime="):])
    return None


def read_event() -> Tuple[bool, Any]:
    """``(parsed, event)``; ``(False, None)`` for malformed stdin, ``(True, UNPARSABLE)`` for JSON nested too deeply."""
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return False, None
    try:
        return True, json.loads(stream.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False, None
    except RecursionError:
        return True, UNPARSABLE


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    runtime = parse_runtime(args)
    if runtime is None:
        sys.stderr.write("broker_write_gate: unknown arguments %r\n" % (args,))
        return 1
    parsed, event = read_event()
    if not parsed:
        return 0
    label = UNPARSABLE if event is UNPARSABLE else decide(event, runtime)
    if label is None:
        if runtime == "cursor" and isinstance(event, dict) and event.get("hook_event_name") in CURSOR_PERMISSION_EVENTS:
            sys.stdout.write(json.dumps(CURSOR_ASK) + "\n")
            sys.stdout.flush()
        return 0
    reason = reason_text(label)
    sys.stdout.write(json.dumps(deny_payload(reason, runtime)) + "\n")
    sys.stdout.flush()
    sys.stderr.write(reason + "\n")
    sys.stderr.flush()
    return 2


if __name__ == "__main__":
    sys.exit(main())
