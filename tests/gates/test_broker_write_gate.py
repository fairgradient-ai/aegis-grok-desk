"""``hooks/broker_write_gate.py``: the PreToolUse backstop, run the way the runtimes run it (a fresh interpreter, the
event on stdin) and through its pure ``decide()``.

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased changes, commit 75a4c79
(scripts/test_exchange_write_gate.py): the process-contract and repository-tools tests. The vectors, the Claude Code,
Grok Build and Cursor event shapes, the deny contract and the registration checks are Aegis Desk's own.

The vectors are module-level tuples. Each process run passes its payload to ``subprocess.run`` as a variable, never a
literal: the repository's AST gate refuses a literal curl or Python-code string inside a subprocess call. The module
is imported through ``sys.path`` (never through a loader, which the AST gate refuses too). Nothing here depends on file
modes, so the tests pass as a non-root user; nothing is written outside the temporary directories they create.
"""
from __future__ import annotations

import ast
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple

# isort: off
import tests._path  # noqa: F401
from tests.gates._trees import ROOT
# isort: on

HOOKS = os.path.join(ROOT, "hooks")
if HOOKS not in sys.path:
    sys.path.insert(0, HOOKS)

import broker_write_gate as gate  # noqa: E402
from aegis_desk import routines  # noqa: E402

GATE = os.path.join(HOOKS, "broker_write_gate.py")
BOUNDARY = "This desk never places, modifies or cancels orders; you act in your brokerage account."
SAMPLE = "curl -X POST https://127.0.0.1:9/v1/accounts/AEGIS-TEST/orders -d class=option"
SAMPLE_REASON = ("Aegis Desk denied this call: it looks like a broker write (an HTTP write to /v1/accounts). "
                 "This desk never places, modifies or cancels orders; you act in your brokerage account.")
CURSOR = ("--runtime", "cursor")
CURSOR_ASK = b'{"permission": "ask"}\n'
CURSOR_EVENTS = ("beforeShellExecution", "beforeMCPExecution")
ALLOWED_IMPORTS = {"__future__", "json", "re", "sys", "typing"}
# Loopback, discard port: harmless if a hook ever failed open. SNAPTRADE is D08's own endpoint (no account path).
LOOPBACK = "https://127.0.0.1:9/v1/accounts/AEGIS-TEST/orders"
SNAPTRADE = "https://api.snaptrade.com/api/v1/trade/place"


# --------------------------------------------------------------------------------------------
# Event shapes
# --------------------------------------------------------------------------------------------


def claude_bash(command: str, tool: str = "Bash") -> Dict[str, Any]:
    """Claude Code PreToolUse for its shell tools (Bash; PowerShell on Windows)."""
    return {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"command": command, "description": "probe"}, "cwd": "/repo", "permission_mode": "default"}


def claude_mcp(name: str, tool_input: Any) -> Dict[str, Any]:
    """Claude Code PreToolUse for an MCP tool: ``mcp__<server>__<tool>`` with an object input."""
    return {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": name, "tool_input": tool_input,
            "cwd": "/repo"}


def grok_full(command: str, truncated: bool = False) -> Dict[str, Any]:
    """Grok Build pre_tool_use as it is sent: camelCase keys plus the additive snake_case aliases."""
    return {"hookEventName": "pre_tool_use", "toolName": "run_terminal_command", "toolInput": {"command": command},
            "toolInputTruncated": truncated, "hook_event_name": "PreToolUse", "tool_name": "run_terminal_command",
            "tool_input": {"command": command}, "tool_use_id": "t1", "cwd": "/repo"}


def grok_camel(command: str, truncated: bool = False) -> Dict[str, Any]:
    """Grok Build's camelCase keys only."""
    return {"hookEventName": "pre_tool_use", "toolName": "run_terminal_command", "toolInput": {"command": command},
            "toolInputTruncated": truncated, "cwd": "/repo"}


def grok_snake(command: str) -> Dict[str, Any]:
    """Grok Build's snake_case aliases only."""
    return {"hook_event_name": "PreToolUse", "tool_name": "run_terminal_command", "tool_input": {"command": command},
            "tool_use_id": "t1"}


def grok_mcp(name: str, tool_input: Any, truncated: bool = False) -> Dict[str, Any]:
    """Grok Build pre_tool_use for an MCP call: ``<server>__<tool>``, both key spellings."""
    return {"hookEventName": "pre_tool_use", "toolName": name, "toolInput": tool_input,
            "toolInputTruncated": truncated, "hook_event_name": "PreToolUse", "tool_name": name,
            "tool_input": tool_input, "tool_use_id": "t2"}


def grok_mcp_camel(name: str, tool_input: Any) -> Dict[str, Any]:
    return {"hookEventName": "pre_tool_use", "toolName": name, "toolInput": tool_input, "toolInputTruncated": False}


def cursor_shell(command: str) -> Dict[str, Any]:
    """Cursor beforeShellExecution: a top-level command."""
    return {"hook_event_name": "beforeShellExecution", "command": command, "cwd": "/repo", "sandbox": False}


def cursor_mcp(server: str, tool: str, tool_input: str) -> Dict[str, Any]:
    """Cursor beforeMCPExecution: the tool input is a JSON string."""
    return {"hook_event_name": "beforeMCPExecution", "mcp_server_name": server, "tool_name": tool,
            "tool_input": tool_input}


def cursor_mcp_stdio(server: str, tool: str, tool_input: str, launch: str) -> Dict[str, Any]:
    """Cursor beforeMCPExecution for a stdio server: Cursor adds the server's launch command as ``command``."""
    event = cursor_mcp(server, tool, tool_input)
    event["command"] = launch
    return event


def cursor_mcp_http(server: str, tool: str, tool_input: str, url: str) -> Dict[str, Any]:
    """Cursor beforeMCPExecution for an HTTP or SSE server: Cursor adds ``url`` and ``mcp_server_url``."""
    event = cursor_mcp(server, tool, tool_input)
    event["url"] = url
    event["mcp_server_url"] = url
    return event


def tool_call(name: str, tool_input: Any) -> Dict[str, Any]:
    """Claude Code PreToolUse for any tool by name (Monitor and other shell-running tools)."""
    return {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": tool_input}


def nested(depth: int) -> Dict[str, Any]:
    value: Dict[str, Any] = {"symbol": "SPY"}
    for _ in range(depth):
        value = {"a": value}
    return value


# --------------------------------------------------------------------------------------------
# Vectors: (id, gate arguments, event)
# --------------------------------------------------------------------------------------------

DENIED: Tuple[Tuple[str, Tuple[str, ...], Any], ...] = (
    ("D01", (), claude_bash(SAMPLE)),
    ("D02", (), claude_bash('curl -s -X POST "https://paper-api.alpaca.markets/v1/accounts/VA000000/orders" -H '
                            '"APCA-API-SECRET-KEY: $APCA_API_SECRET_KEY" -d "class=option&symbol=SPY"')),
    ("D03", (), claude_bash("curl -XDELETE https://api.alpaca.markets/v1/accounts/6YA00000/orders/123456")),
    ("D04", (), claude_bash('curl --request PUT https://api.alpaca.markets/v1/accounts/6YA00000/orders/123456 '
                            '--data "price=1.05"')),
    ("D05", (), claude_bash('curl https://paper-api.alpaca.markets/v1/accounts/VA000000/orders --data-urlencode '
                            '"side=sell_to_close"')),
    ("D06", (), claude_bash('wget --method=POST --body-data="side=buy" https://api.alpaca.markets/v1/accounts/6YA00000/orders')),
    ("D07", (), claude_bash("http POST https://paper-api.alpaca.markets/v2/orders symbol=NVDA qty=1 side=sell")),
    ("D08", (), claude_bash("curl -X POST https://api.snaptrade.com/api/v1/trade/place -d @order.json")),
    ("D09", (), claude_bash("python3 -c 'import requests; requests.post(\"https://api.alpaca.markets/v1/accounts/X/orders\", "
                            "data=d)'")),
    ("D10", (), claude_bash('python3 -c "client.place_order(account_id, order)"')),
    ("D11", (), claude_bash("python3 - <<'PY'\nfrom snaptrade_client import SnapTrade\n"
                            "snaptrade.trading.place_order(user_id=u, user_secret=s, trade_id=t)\nPY")),
    ("D12", (), claude_bash("node -e \"fetch('https://api.alpaca.markets/v1/accounts/X/orders', {method: 'POST', body})\"")),
    ("D13", (), claude_bash("echo '" + SAMPLE + "' | bash")),
    ("D14", (), claude_bash('bash -c "' + SAMPLE + '"')),
    ("D15", (), claude_bash("cd /tmp && " + SAMPLE)),
    ("D16", (), claude_bash('python3 -c "api.cancel_order(order_id)"')),
    ("D17", (), claude_bash('python3 -c "api.replaceOrder(order_id, price=1.2)"')),
    ("D18", (), claude_bash("ruby -e 'client.submit_order(symbol: \"NVDA\", qty: 1, side: \"sell\")'")),
    ("D19", (), claude_bash("Invoke-RestMethod -Method Post -Uri https://api.alpaca.markets/v1/accounts/X/orders",
                            tool="PowerShell")),
    ("D20", ("--runtime", "grok"), grok_camel(SAMPLE)),
    ("D21", (), grok_snake('curl -s -X POST "https://paper-api.alpaca.markets/v1/accounts/VA000000/orders" -d '
                           '"side=sell_to_close"')),
    ("D22", (), grok_full("ls", truncated=True)),
    ("D23", CURSOR, cursor_shell(SAMPLE)),
    ("D24", (), grok_mcp("blackhole__place_order", {"symbol": "SPY"})),
    ("D25", ("--runtime", "claude"), claude_mcp("mcp__blackhole__place_order", {})),
    ("D26", (), claude_mcp("mcp__plugin_broker_alpaca__submit_order", {"symbol": "NVDA"})),
    ("D27", (), grok_mcp("robinhood__sell_stock", {"symbol": "NVDA", "quantity": 1})),
    ("D28", (), grok_mcp("broker__buyOption", {"occ": "NVDA261120C00150000"})),
    ("D29", (), grok_mcp("alpaca__cancelOrder", {"id": 1})),
    ("D30", (), claude_mcp("mcp__snaptrade__trade_preview", {})),
    ("D31", (), claude_mcp("mcp__schwab__replace_order", {"order_id": "1"})),
    ("D32", (), grok_mcp("zapier__run_action", {"action": "alpaca_place_order", "params": {"symbol": "SPY"}})),
    ("D33", (), claude_mcp("mcp__broker__execute", {"symbol": "NVDA", "side": "sell_to_close"})),
    ("D34", (), claude_mcp("mcp__broker__execute", {"order": {"symbol": "NVDA"}})),
    ("D35", (), claude_mcp("mcp__shell__run", {"command": SAMPLE})),
    ("D36", (), grok_mcp("github__get_file_contents", {"path": "README.md"}, truncated=True)),
    ("D37", CURSOR, cursor_mcp("broker", "place_order", '{"symbol": "SPY"}')),
    ("D38", ("--runtime=cursor",), cursor_mcp("zapier", "run_action", '{"action": "sell_to_close"}')),
    ("D39", (), claude_bash("find . -name x -exec " + SAMPLE + " \\;")),
    ("D40", (), claude_bash("git -c alias.p='!" + SAMPLE + "' p")),
    # A read command feeding, wrapping or hiding a write is still scanned whole.
    ("D41", (), claude_bash('echo "$(' + SAMPLE + ')"')),
    ("D42", (), claude_bash("echo '" + SAMPLE + "' | nice sh")),
    ("D43", (), claude_bash("LESSOPEN='|" + SAMPLE + "' less notes.txt")),
    ("D44", (), claude_bash("c\\url -X POST 127.0.0.1:9/v1/accounts/AEGIS-TEST/orders -d class=option")),
    ("D45", (), claude_bash("echo '" + SAMPLE + "' | python3.12")),
    ("D46", (), claude_bash("printf '%s' '" + SAMPLE + "' | $SHELL")),
    ("D47", (), claude_bash("curl.exe -X POST 127.0.0.1:9/v1/accounts/AEGIS-TEST/orders -d class=option",
                            tool="PowerShell")),
    ("D48", (), claude_bash("curl${IFS}-X${IFS}POST${IFS}127.0.0.1:9/v1/accounts/AEGIS-TEST/orders${IFS}-d${IFS}x=1")),
    ("D49", (), claude_bash("cat <<'EOF' | sh\n" + SAMPLE + "\nEOF")),
    ("D50", (), claude_bash("perl -MLWP::UserAgent -e 'LWP::UserAgent->new->post(\"https://api.alpaca.markets/v1/accounts/X/"
                            "orders\", {side => \"sell\"})'")),
    ("D51", (), claude_bash("http https://api.alpaca.markets/v1/accounts/X/orders symbol=SPY side=sell_to_close")),
    ("D52", (), claude_bash("Invoke-WebRequest -Uri https://api.alpaca.markets/v1/accounts/X/orders -Method POST -Body $b",
                            tool="PowerShell")),
    ("D53", (), claude_bash("curl https://api.alpaca.markets/v1/accounts/6YA00000/positions")),
    ("D54", (), grok_mcp_camel("alpaca__cancelOrder", {"id": 1})),
    ("D55", CURSOR, {"hook_event_name": "preToolUse", "tool_name": "Shell",
                     "tool_input": {"command": SAMPLE, "working_directory": "/repo"}}),
    ("D56", CURSOR, cursor_mcp("shell", "run", "not json: " + SAMPLE)),
    ("D57", (), claude_mcp("mcp__broker__execute", nested(40))),
    ("D58", (), claude_mcp("mcp__fetch__fetch", {"url": "https://api.alpaca.markets/v1/accounts/6YA00000/orders"})),
    ("D59", (), claude_bash("git grep -O'sh -c \"" + SAMPLE + "\"' x")),
    ("D60", (), claude_bash("env ALPACA=1 " + SAMPLE)),
    ("D61", (), claude_mcp("mcp__broker__execute", {"rows": list(range(20000))})),
    ("D62", (), claude_bash("rg --pre=sh -n x '" + SAMPLE + "'")),
    ("D63", (), {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
                 "tool_input_truncated": True}),
    # Cursor's stdio MCP shape carries the server's launch command as "command"; the tool is still judged.
    ("D64", CURSOR, cursor_mcp_stdio("broker", "place_order", '{"symbol": "SPY"}', "npx -y @alpaca/mcp-server")),
    ("D65", CURSOR, cursor_mcp_stdio("zapier", "run_action", '{"symbol": "SPY", "side": "sell_to_close"}',
                                     "uvx zapier-mcp --stdio")),
    ("D66", CURSOR, cursor_mcp_http("broker", "place_order", '{"symbol": "SPY"}', "https://mcp.example.test/sse")),
    # A git subcommand that runs a command given on the command line is scanned whole.
    ("D67", (), claude_bash("git rebase -x '" + SAMPLE + "' HEAD~1")),
    ("D68", (), claude_bash("git rebase --exe '" + SAMPLE + "' HEAD~1")),
    ("D69", (), claude_bash("git submodule foreach '" + SAMPLE + "'")),
    ("D70", (), claude_bash("git bisect run " + SAMPLE)),
    ("D71", (), claude_bash("git difftool -y --extcmd='" + SAMPLE + "' HEAD~1")),
    ("D72", (), claude_bash("git filter-branch -f --tree-filter '" + SAMPLE + "' HEAD")),
    ("D73", (), claude_bash("git grep --open='" + SAMPLE + "' x")),
    ("D74", (), claude_bash("sort --compress-prog=\"sh -c '" + SAMPLE + "'\" notes.txt")),
    ("D75", (), claude_bash("git -C /repo rebase -x '" + SAMPLE + "' HEAD~1")),
    ("D76", (), claude_bash("git grep -nO'" + SAMPLE + "' x")),
    # A broker host outside its market-data paths, whatever the syntax of the write marker.
    ("D77", (), claude_bash("curl -sXPOST " + SNAPTRADE + " -sd@order.json")),
    ("D78", (), claude_bash("curl -sX POST " + SNAPTRADE + " -H 'Content-Type: application/json' -sd@order.json")),
    ("D79", (), claude_bash("Invoke-RestMethod -Method:Post -Uri " + SNAPTRADE + " -InFile:order.json",
                            tool="PowerShell")),
    ("D80", (), claude_bash("Invoke-RestMethod -Meth Post -Uri " + SNAPTRADE + " -Bod $b", tool="PowerShell")),
    ("D81", (), claude_bash("python3 -c \"import urllib.request as u; u.urlopen('" + SNAPTRADE +
                            "', open('order.json','rb').read())\"")),
    ("D82", (), claude_bash("ruby -rnet/http -e 'Net::HTTP.post_form(URI(\"" + SNAPTRADE + "\"), \"q\" => \"1\")'")),
    ("D83", (), claude_bash("{curl,-XPOST," + SNAPTRADE + ",-d@order.json}")),
    ("D84", (), claude_bash("curl -sXDELETE https://paper-api.alpaca.markets/v2/positions/NVDA")),
    # The same markers on an account or order path of a host the gate does not know.
    ("D85", (), claude_bash("curl -sXPOST " + LOOPBACK + " -sd@order.json")),
    ("D86", (), claude_bash("curl -sdclass=option " + LOOPBACK)),
    ("D87", (), claude_bash("Invoke-RestMethod -Method:Post -Uri " + LOOPBACK, tool="PowerShell")),
    ("D88", (), claude_bash("Invoke-WebRequest -Uri " + LOOPBACK + " -Meth Post -Bod $b", tool="PowerShell")),
    ("D89", (), claude_bash("wget --post-d=class=option " + LOOPBACK)),
    ("D90", (), claude_bash("python3 -c \"import urllib.request as u; u.urlopen(u.Request('" + LOOPBACK +
                            "', b'class=option'))\"")),
    ("D91", (), claude_bash("{curl,-XPOST," + LOOPBACK + ",-dclass=option}")),
    # Claude Code's Monitor tool runs a shell command; any other non-MCP tool with a command string is read the same way.
    ("D92", (), tool_call("Monitor", {"command": SAMPLE, "description": "probe", "timeout_ms": 60000})),
    ("D93", (), {"hookEventName": "pre_tool_use", "toolName": "run_command", "toolInput": {"command": SAMPLE}}),
    # Broker MCP tools that change positions without an order verb, and inputs that hide one in JSON text.
    ("D94", (), claude_mcp("mcp__alpaca__close_position", {"symbol_or_asset_id": "NVDA", "percentage": 100})),
    ("D95", (), claude_mcp("mcp__alpaca__close_all_positions", {"cancel_orders": False})),
    ("D96", (), claude_mcp("mcp__alpaca__exercise_options_position", {"symbol_or_contract_id": "NVDA261120C00150000"})),
    ("D97", (), claude_mcp("mcp__ibkr__liquidate", {})),
    ("D98", (), claude_mcp("mcp__broker__PLACEORDER", {"side": "short"})),
    ("D99", (), claude_mcp("mcp__broker__placeorder", {})),
    ("D100", (), claude_mcp("mcp__zapier__run_action",
                            {"action": "run", "params_json": json.dumps({"side": "sell_to_close", "symbol": "SPY"})})),
    ("D101", (), grok_mcp("broker__open_position", {"direction": "short"})),
    ("D102", (), claude_mcp("mcp__x__run", {"payload": json.dumps([{"order": {"symbol": "SPY"}}])})),
    ("D103", (), claude_bash('python3 -c "client.create_order(symbol=s)"')),
    ("D104", (), claude_bash('python3 -c "api.send_order(o)"')),
    # A market-data read typed in a shell on a broker host is denied too: the desk reads market data from Python.
    ("D105", (), claude_bash('curl -s "https://data.alpaca.markets/v2/stocks/quotes/latest?symbols=SPY" -H '
                             '"APCA-API-KEY-ID: $APCA_API_KEY_ID"')),
    ("D106", (), claude_bash("curl -s https://paper-api.alpaca.markets/v2/clock")),
    ("D107", (), claude_bash("Invoke-RestMethod -Uri https://data.alpaca.markets/v2/stocks/bars -Headers $h "
                             "-TimeoutSec 10", tool="PowerShell")),
)

NO_OPINION: Tuple[Tuple[str, Tuple[str, ...], Any], ...] = (
    ("N01", (), claude_bash('grep -rn "curl -X POST" docs/ skills/')),
    ("N02", (), claude_bash("git log --oneline -5")),
    ("N03", (), claude_bash('git grep -n "place_order(" -- scripts/')),
    ("N04", (), claude_bash("cat docs/aegis-grok-build.md")),
    ("N05", (), claude_bash('rg -n "/v1/accounts" docs | head -20')),
    ("N06", (), claude_bash("python3 scripts/routine.py book-check --slot 10:05")),
    ("N07", (), claude_bash("python3 scripts/desk_doctor.py --quick --desk-root /workspace/aegis-desk")),
    ("N08", (), claude_bash("bash scripts/check.sh")),
    ("N09", (), claude_bash("python3 scripts/quote_cache.py --symbols SPY --desk-root /workspace/aegis-desk")),
    ("N10", (), claude_bash("curl -s https://www.example.com/status.json")),
    ("N11", (), claude_bash("python3 scripts/opening_bell.py --symbol SPY --offline --desk-root /tmp/bell")),
    ("N12", (), claude_mcp("mcp__github__list_pull_requests",
                           {"owner": "fairgradient-ai", "repo": "aegis-grok-desk", "state": "open"})),
    ("N13", (), claude_mcp("mcp__Linear__list_issues", {"team": "FairGradient", "orderBy": "updatedAt"})),
    ("N14", (), grok_mcp("gmail__search_threads", {"query": "order confirmation from my broker"})),
    ("N15", (), {"hook_event_name": "PreToolUse", "tool_name": "Write",
                 "tool_input": {"file_path": "x.py", "content": "client.place_order(o)"}}),
    ("N16", (), {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}),
    ("N17", (), []),
    ("N18", CURSOR, cursor_shell("git status")),
    ("N19", (), grok_full("cat positions.csv", truncated=False)),
    ("N20", (), claude_bash("python3 scripts/positions_check.py --desk-root /workspace/aegis-desk --json")),
    ("N21", (), claude_bash('echo "TP50 fires at an option price of 1.71"')),
    ("N22", (), claude_bash('git commit -m "docs: show the denied curl -X POST sample for /v1/accounts"')),
    ("N23", (), claude_bash("ls ~/.grok && cat ~/.grok/config.toml")),
    ("N24", (), grok_snake("python3 scripts/routine.py eod --slot 16:35")),
    ("N25", (), claude_bash("python3 -m unittest tests.gates.test_broker_write_gate")),
    ("N26", CURSOR, cursor_mcp("github", "list_issues", '{"state": "open"}')),
    ("N27", (), {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": SAMPLE},
                 "tool_response": {"stdout": ""}}),
    ("N28", (), {"hook_event_name": "UserPromptSubmit", "prompt": SAMPLE}),
    ("N29", (), {"hookEventName": "post_tool_use", "toolName": "run_terminal_command", "toolInput": {"command": SAMPLE}}),
    ("N30", (), claude_bash("find docs -name '*.md' -newer CHANGELOG.md")),
    ("N31", (), claude_bash("git -c color.ui=never log -1 --format=%H")),
    ("N32", (), claude_mcp("mcp__snaptrade__list_accounts", {})),
    ("N33", (), grok_mcp("alpaca__get_quotes", {"symbols": "SPY,NVDA"})),
    ("N34", (), claude_mcp("mcp__plugin_alpaca_market__get_clock", {})),
    ("N35", (), claude_bash('git commit -m "docs: refresh the brief $(date +%F)"')),
    ("N36", (), claude_bash('grep -rn "sell_to_close" scripts/ | wc -l')),
    ("N37", (), claude_bash("cat hooks/hooks.json | jq .")),
    ("N38", (), grok_camel("git diff -- docs/aegis-grok-build.md")),
    ("N39", CURSOR, cursor_shell('rg -n "place_order(" hooks/ tests/')),
    ("N40", (), {"hook_event_name": "Stop", "stop_hook_active": False}),
    ("N41", (), claude_mcp("mcp__Linear__list_issues", {"orderBy": "createdAt", "query": "exit alert"})),
    ("N42", (), {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "docs/TOOLS.md"}}),
    ("N43", (), {"hookEventName": "pre_tool_use", "toolName": "write_file", "toolInput": {"content": SAMPLE},
                 "toolInputTruncated": True}),
    ("N44", (), claude_mcp("mcp__github__get_file_contents", {"path": "hooks/broker_write_gate.py"})),
    ("N45", (), claude_bash("python3 hooks/broker_write_gate.py --runtime cursor < event.json")),
    # A sort direction under an "order" key is a read.
    ("N46", (), claude_mcp("mcp__github__search_issues",
                           {"query": "repo:fairgradient-ai/aegis-grok-desk is:open", "sort": "updated", "order": "desc"})),
    ("N47", (), claude_mcp("mcp__github__search_pull_requests", {"query": "is:open", "order": "asc"})),
    ("N48", (), grok_mcp("github__search_code", {"query": "check_shell", "order": "desc"})),
    ("N49", (), claude_mcp("mcp__x__list", {"params": json.dumps({"order": "descending", "page": 2})})),
    # Position reads stay reads.
    ("N50", (), claude_mcp("mcp__alpaca__get_open_position", {"symbol_or_asset_id": "NVDA"})),
    ("N51", (), claude_mcp("mcp__alpaca__get_all_positions", {})),
    # git's read subcommands, with its global options.
    ("N52", (), claude_bash("git -C /repo status --short")),
    ("N53", (), claude_bash("git --no-pager log -1 --grep '" + SAMPLE + "'")),
    ("N54", (), claude_bash("git show HEAD:docs/aegis-cursor.md | head -5")),
    ("N55", (), claude_bash("git blame -L 1,5 README.md && git diff --stat")),
    # A Monitor that reads, reads of a host that is not a broker's, and Cursor's stdio and HTTP reads.
    ("N56", (), tool_call("Monitor", {"command": "tail -f /workspace/aegis-desk/journal/alerts.jsonl | grep --line-buffered "
                                                 "EXIT", "description": "alerts", "timeout_ms": 60000})),
    ("N57", (), claude_bash("Invoke-RestMethod -Uri https://www.example.com/status.json -Headers $h "
                            "-TimeoutSec 10", tool="PowerShell")),
    ("N58", (), claude_bash('curl -s "https://data.example.org/v2/stocks/quotes/latest?symbols=SPY"')),
    ("N59", CURSOR, cursor_mcp_stdio("github", "list_issues", '{"state": "open"}',
                                     "npx -y @modelcontextprotocol/server-github")),
    ("N60", CURSOR, cursor_mcp_http("linear", "list_issues", '{"orderBy": "updatedAt"}', "https://mcp.linear.app/sse")),
)

# The Aegis Structuring Desk's commands (PR 3b; designer C's D8 with the addendum's S2 command lines): the three tools,
# the structuring intake, the lint in structuring mode and reads of what they write. Each gets no opinion in the Claude
# Code and the Grok Build shapes; the gate's bytes did not change for them.
DESK_ROOT = "--desk-root /workspace/aegis-desk"
SCRIPTS_DIR = "python3 /workspace/aegis-grok-desk/scripts/"
STRUCTURING_COMMANDS: Tuple[str, ...] = (
    SCRIPTS_DIR + "roll_analysis.py --trade AG-20261020-01 " + DESK_ROOT,
    SCRIPTS_DIR + "roll_analysis.py --trade AG-20261022-02 " + DESK_ROOT + " --json",
    SCRIPTS_DIR + "propose.py --trade AG-20261028-03 --structure covered_call " + DESK_ROOT,
    SCRIPTS_DIR + "propose.py --trade AG-20261028-03 --structure protective_put " + DESK_ROOT,
    SCRIPTS_DIR + "propose.py --trade AG-20261028-03 --structure collar " + DESK_ROOT + " --json",
    SCRIPTS_DIR + "exit_ticket.py --trade AG-20261020-01 --action CLOSE " + DESK_ROOT,
    SCRIPTS_DIR + "exit_ticket.py --trade AG-20261020-01 --action UNWIND_STOCK " + DESK_ROOT,
    SCRIPTS_DIR + "exit_ticket.py --trade AG-20261020-01 --action LET_ASSIGN " + DESK_ROOT,
    SCRIPTS_DIR + "exit_ticket.py --trade AG-20261028-03 --action MONETIZE_PUT " + DESK_ROOT,
    SCRIPTS_DIR + "exit_ticket.py --trade AG-20261020-01 --action ROLL --new-expiration 2027-01-15 --new-strike 115 "
    + DESK_ROOT,
    SCRIPTS_DIR + "exit_ticket.py --trade AG-20261028-03 --action ROLL --new-expiration 2027-03-19 --new-strike 610 "
    + DESK_ROOT + " --json",
    SCRIPTS_DIR + "desk_init.py --answers /workspace/aegis-desk/setup/structuring-answers.json " + DESK_ROOT,
    SCRIPTS_DIR + "desk_init.py --answers /workspace/aegis-desk/setup/structuring-answers.json --replace --owner-tapped "
    + DESK_ROOT,
    SCRIPTS_DIR + "alert_lint.py --message-file /tmp/text.txt --emitted-file /tmp/text.json --footer structuring",
    "cat /workspace/aegis-desk/reports/rolls/AG-20261020-01-20261103T195000Z.md",
    "ls /workspace/aegis-desk/reports/proposals/",
    "ls -la /workspace/aegis-desk/tickets/",
    "cat /workspace/aegis-desk/tickets/TK-AG-20261020-01-ROLL-20261103T195000Z.md",
    "tail -n 3 /workspace/aegis-desk/journal/structuring.jsonl",
)
NO_OPINION += tuple(("N%02d" % (61 + 2 * index + offset), (), shape(command))
                    for index, command in enumerate(STRUCTURING_COMMANDS)
                    for offset, shape in enumerate((claude_bash, grok_full)))
# A desk tool wrapped as an MCP tool (the desk ships no MCP server): an input key named trade_id gets no opinion.
NO_OPINION += (
    ("N99", (), claude_mcp("mcp__aegis__roll_analysis", {"trade_id": "AG-20261020-01"})),
)

# Malformed input gets no opinion (documented fail-open): (id, arguments, raw stdin bytes).
MALFORMED: Tuple[Tuple[str, Tuple[str, ...], bytes], ...] = (
    ("M01", (), b"not json"),
    ("M02", (), b""),
    ("M03", (), b"\xff"),
    ("M04", (), b'{"tool_name": "Bash", "tool_input": {"command": 5}}'),
    ("M05", (), b"null"),
    ("M06", (), b'"curl -X POST https://127.0.0.1:9/v1/accounts/AEGIS-TEST/orders -d class=option"'),
    ("M07", (), b'{"tool_input": {"command": "curl -X POST https://127.0.0.1:9/v1/accounts/AEGIS-TEST/orders -d x"}}'),
    ("M08", (), b'{"tool_name": "Bash", "tool_input": "curl -X POST https://127.0.0.1:9/v1/accounts/AEGIS-TEST/orders"}'),
    ("M09", (), b'{"tool_name": 7, "tool_input": {"command": "curl -X POST https://127.0.0.1:9/v1/accounts/A/orders"}}'),
    ("M10", (), b'\xef\xbb\xbf{"tool_name": "Bash", "tool_input": {"command": "ls"}}'),
    ("M11", (), b'{"hook_event_name": ["PreToolUse"], "tool_name": "Bash", "tool_input": {"command": "curl -X POST '
                b'https://127.0.0.1:9/v1/accounts/AEGIS-TEST/orders -d x"}}'),
    ("M12", CURSOR, b'{"hook_event_name": "beforeShellExecution", "command": 5}'),
    ("M13", (), b"[1, 2, 3"),
)


def encode(event: Any) -> bytes:
    return json.dumps(event).encode("utf-8")


def quiet_stdout(args: Sequence[str], payload: bytes) -> bytes:
    """What a call with no opinion prints: nothing, except Cursor's two permission events, which always get an explicit
    ``{"permission": "ask"}`` (Cursor reads no output from a fail-closed hook as a failure that blocks)."""
    if runtime_of(args) != "cursor":
        return b""
    try:
        event = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return b""
    if isinstance(event, dict) and event.get("hook_event_name") in CURSOR_EVENTS:
        return CURSOR_ASK
    return b""


def gate_environment() -> Dict[str, str]:
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    for key in ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def run_gate(payload: bytes, args: Sequence[str] = (), cwd: str = ROOT,
             script: str = GATE) -> "subprocess.CompletedProcess[bytes]":
    """One hook run: a fresh interpreter, ``payload`` on stdin."""
    command = [sys.executable, script] + list(args)
    return subprocess.run(command, input=payload, capture_output=True, timeout=60, env=gate_environment(), cwd=cwd,
                          check=False)


class VectorTablesTest(unittest.TestCase):
    def test_the_tables_cover_every_shape(self) -> None:
        self.assertGreaterEqual(len(DENIED), 25)
        self.assertGreaterEqual(len(NO_OPINION), 15)
        ids = [item[0] for item in DENIED + NO_OPINION + MALFORMED]
        self.assertEqual(len(ids), len(set(ids)))
        names = [(event.get("tool_name"), event.get("toolName"), event.get("hook_event_name"))
                 for _id, _args, event in DENIED if isinstance(event, dict)]
        tools = {name for triple in names for name in triple[:2] if name}
        for shell in ("Bash", "PowerShell", "run_terminal_command", "Shell"):
            self.assertIn(shell, tools)
        self.assertIn("beforeShellExecution", {triple[2] for triple in names})
        self.assertIn("beforeMCPExecution", {triple[2] for triple in names})
        self.assertTrue(any(name.startswith("mcp__") for name in tools))
        self.assertTrue(any("__" in name and not name.startswith("mcp__") for name in tools))
        self.assertTrue(any(isinstance(event, dict) and event.get("toolInputTruncated") is True
                            for _id, _args, event in DENIED))
        self.assertIn("Monitor", tools)
        self.assertTrue(any(isinstance(event, dict) and event.get("hook_event_name") == "beforeMCPExecution"
                            and "command" in event for _id, _args, event in DENIED), "Cursor's stdio MCP shape")


# --------------------------------------------------------------------------------------------
# decide(): the pure decision
# --------------------------------------------------------------------------------------------


def runtime_of(args: Sequence[str]) -> str:
    return "cursor" if tuple(args) in (CURSOR, ("--runtime=cursor",)) else "default"


class DecideTest(unittest.TestCase):
    def test_every_denied_vector_gets_a_label(self) -> None:
        for vector_id, args, event in DENIED:
            with self.subTest(vector=vector_id):
                label = gate.decide(event, runtime_of(args))
                self.assertIsInstance(label, str)
                self.assertTrue(label)
                self.assertLessEqual(len(gate.reason_text(label)), 250)

    def test_every_no_opinion_vector_gets_none(self) -> None:
        for vector_id, args, event in NO_OPINION:
            with self.subTest(vector=vector_id):
                self.assertIsNone(gate.decide(event, runtime_of(args)))

    def test_the_gate_reads_only_the_call_never_a_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-gate-file-") as directory:
            script = os.path.join(directory, "send.sh")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(SAMPLE + "\n")
            for command in ("bash %s" % script, "sh %s" % script, "cat %s" % script):
                with self.subTest(command=command):
                    self.assertIsNone(gate.decide(claude_bash(command)))

    def test_whole_tokens_only_in_mcp_names(self) -> None:
        for name in ("mcp__snaptrade__list_accounts", "alpaca__get_option_chains", "mcp__sellside__research",
                     "mcp__orderly__status", "mcp__tradingview__chart"):
            with self.subTest(name=name):
                self.assertIsNone(gate.decide(claude_mcp(name, {})))
        for name in ("mcp__x__list_orders", "x__trade_history", "mcp__x__SubmitReview", "x__sell"):
            with self.subTest(name=name):
                self.assertIsNotNone(gate.decide(claude_mcp(name, {})))

    def test_mcp_keys_match_whole_and_case_insensitive(self) -> None:
        for key in ("order", "Orders", "TRADE", "trades"):
            with self.subTest(key=key):
                self.assertIsNotNone(gate.decide(claude_mcp("mcp__x__run", {key: {}})))
        for key in ("orderBy", "order_id", "trade_id", "sort_order"):
            with self.subTest(key=key):
                self.assertIsNone(gate.decide(claude_mcp("mcp__x__run", {key: "1"})))

    def test_limits_of_the_mcp_walk(self) -> None:
        self.assertIsNone(gate.decide(claude_mcp("mcp__x__run", nested(gate.MAX_DEPTH - 2))))
        self.assertIn("too deep", gate.decide(claude_mcp("mcp__x__run", nested(gate.MAX_DEPTH + 1))) or "")
        self.assertIsNone(gate.decide(claude_mcp("mcp__x__run", {"rows": list(range(gate.MAX_NODES // 2))})))
        self.assertIn("too large", gate.decide(claude_mcp("mcp__x__run", {"rows": list(range(gate.MAX_NODES))})) or "")

    def test_text_too_long_to_scan_is_denied_unless_it_only_reads(self) -> None:
        long_text = "x" * (gate.MAX_TEXT + 1)
        self.assertEqual("a shell command too long to read", gate.decide(claude_bash("python3 -c 'print(1)' # " + long_text)))
        self.assertIsNone(gate.decide(claude_bash("python3 -c 'print(1)' # " + "x" * (gate.MAX_TEXT - 100))))
        self.assertIsNone(gate.decide(claude_bash('git commit -m "' + long_text + '"')), "a read-only command is not scanned")
        self.assertEqual("an MCP input too large to read",
                         gate.decide(claude_mcp("mcp__x__run", {"a": long_text[: gate.MAX_TEXT // 2 + 1],
                                                                "b": long_text[: gate.MAX_TEXT // 2 + 1]})))
        self.assertIsNone(gate.decide(claude_mcp("mcp__x__run", {"a": "x y " * (gate.MAX_TEXT // 8)})))

    def test_labels_name_what_was_seen(self) -> None:
        cases = (
            (claude_bash(SAMPLE), "an HTTP write to /v1/accounts"),
            (claude_bash('python3 -c "client.place_order(account_id, order)"'), "an order call (place_order)"),
            (claude_bash("curl https://api.alpaca.markets/v1/accounts/6YA00000/positions"),
             "a brokerage account path (/v1/accounts)"),
            (claude_mcp("mcp__blackhole__place_order", {}), "an MCP tool named blackhole__place_order (order)"),
            (claude_mcp("mcp__broker__execute", {"order": {}}), "an MCP input key 'order'"),
            (claude_mcp("mcp__broker__execute", {"side": "sell_to_close"}), "an MCP input value 'sell_to_close'"),
            (grok_full("ls", truncated=True), "a truncated shell command (the gate cannot read all of it)"),
            (grok_mcp("github__get_file_contents", {}, truncated=True),
             "a truncated MCP tool input (the gate cannot read all of it)"),
        )
        for event, label in cases:
            with self.subTest(label=label):
                self.assertEqual(label, gate.decide(event))

    def test_git_is_read_only_through_its_read_subcommands(self) -> None:
        for sub in ("status", "log", "show", "diff", "grep", "blame", "ls-files", "rev-parse", "describe", "branch",
                    "tag", "commit -m"):
            command = "git %s '%s'" % (sub, SAMPLE)
            with self.subTest(command=command):
                self.assertIsNone(gate.decide(claude_bash(command)))
        for sub in ("rebase -x", "submodule foreach", "bisect run", "difftool --extcmd", "filter-branch --tree-filter",
                    "stash push -m", "worktree add", "config alias.p"):
            command = "git %s '%s'" % (sub, SAMPLE)
            with self.subTest(command=command):
                self.assertEqual("an HTTP write to /v1/accounts", gate.decide(claude_bash(command)))
        self.assertIsNone(gate.decide(claude_bash("git")))
        self.assertIsNone(gate.decide(claude_bash("git fetch --prune && git push")), "scanned whole, nothing found")

    def test_an_abbreviated_exec_option_counts(self) -> None:
        for command in ("git grep --open=less x", "git log --ext x", "git show --textc HEAD",
                        "sort --compress=sh big.txt", "git grep -nOless x"):
            with self.subTest(command=command):
                self.assertFalse(gate.is_read(command))
        for command in ("git log --exit-code", "git grep -n x", "sort -n notes.txt", "git log --oneline"):
            with self.subTest(command=command):
                self.assertTrue(gate.is_read(command))

    def test_a_broker_host_is_denied_even_for_a_market_data_read(self) -> None:
        for command, label in (
                ("curl -s https://paper-api.alpaca.markets/v2/clock", "a call to a broker host (alpaca.markets/v2/clock)"),
                ('curl -s "https://data.alpaca.markets/v2/stocks/quotes/latest?symbols=SPY"',
                 "a call to a broker host (alpaca.markets/v2/stocks/quotes/latest?symbols=SPY)"),
                ("python3 -c \"import urllib.request as u; u.urlopen('https://data.alpaca.markets/v2/stocks/bars')\"",
                 "a call to a broker host (alpaca.markets/v2/stocks/bars)"),
                ("curl -s https://api.alpaca.markets/v1/watchlists", "a call to a broker host (alpaca.markets/v1/watchlists)"),
                ("curl -s " + SNAPTRADE, "a call to a broker host (snaptrade.com/api/v1/trade/place)"),
                ("curl -sXPOST " + SNAPTRADE + " -sd@order.json", "an HTTP write to snaptrade.com"),
                ("curl -s https://api.alpaca.markets/v1/accounts/6YA00000/positions",
                 "a brokerage account path (/v1/accounts)"),
                ("curl -s https://www.example.com/status.json https://api.alpaca.markets/v2/account",
                 "a call to a broker host (alpaca.markets/v2/account)")):
            with self.subTest(command=command):
                self.assertEqual(label, gate.decide(claude_bash(command)))
        for command in ("curl -s https://www.example.com/status.json",
                        "python3 -c \"import urllib.request as u; u.urlopen('https://www.example.com/status.json')\""):
            with self.subTest(command=command):
                self.assertIsNone(gate.decide(claude_bash(command)))

    def test_write_markers_in_every_spelling(self) -> None:
        for marker in ("-sXPOST", "-sX POST", "-X PATCH", "-fsSLd x=1", "-sd@o", "-T o.json", "-sF a=b", "--json {}",
                       "--data-b @o", "--req=DELETE"):
            command = "curl %s %s" % (marker, LOOPBACK)
            with self.subTest(command=command):
                self.assertIsNotNone(gate.decide(claude_bash(command)))
        for marker in ("-fsSL", "-sS -H 'Accept: application/json'", "-o out.json", "-sSLo out.json", "-I"):
            command = "curl %s %s" % (marker, LOOPBACK)
            with self.subTest(command=command):
                self.assertIsNone(gate.decide(claude_bash(command)))
        for marker in ("-Method:Post", "-Meth Post", "-method put", "-Bod $b", "-InF o.json", "-CustomMethod DELETE"):
            command = "Invoke-RestMethod -Uri %s %s" % (LOOPBACK, marker)
            with self.subTest(command=command):
                self.assertIsNotNone(gate.decide(claude_bash(command, tool="PowerShell")))
        for marker in ("-Method Get", "-Headers $h", "-TimeoutSec 10", "-OutFile q.json"):
            command = "Invoke-RestMethod -Uri %s %s" % (LOOPBACK, marker)
            with self.subTest(command=command):
                self.assertIsNone(gate.decide(claude_bash(command, tool="PowerShell")))

    def test_mcp_position_changes_and_compound_names(self) -> None:
        for name in ("mcp__alpaca__close_position", "x__closeAllPositions", "x__open_position", "x__exercise",
                     "x__liquidate_all", "x__SUBMITORDER", "x__cancelorders", "x__selltoclose"):
            with self.subTest(name=name):
                self.assertIsNotNone(gate.decide(claude_mcp(name, {})))
        for name in ("mcp__alpaca__get_open_position", "x__list_open_positions", "x__get_positions", "x__close_issue",
                     "x__open_file", "x__list_closed_positions", "x__orderly_status"):
            with self.subTest(name=name):
                self.assertIsNone(gate.decide(claude_mcp(name, {})))

    def test_order_keys_holding_a_sort_direction_are_reads(self) -> None:
        for value in ("asc", "desc", "ASC", "Descending", "ascending"):
            with self.subTest(value=value):
                self.assertIsNone(gate.decide(claude_mcp("mcp__github__search_issues", {"order": value})))
        for value in ({"symbol": "SPY"}, [{"symbol": "SPY"}], "SPY", "buy 1 SPY"):
            with self.subTest(value=value):
                self.assertEqual("an MCP input key 'order'", gate.decide(claude_mcp("mcp__x__run", {"order": value})))

    def test_json_text_inside_an_mcp_input_is_read_too(self) -> None:
        self.assertEqual("an MCP input value 'sell_to_close'",
                         gate.decide(claude_mcp("mcp__x__run", {"p": json.dumps({"side": "sell_to_close"})})))
        self.assertEqual("an MCP input key 'trades'",
                         gate.decide(claude_mcp("mcp__x__run", {"p": json.dumps({"trades": [1]})})))
        self.assertIsNone(gate.decide(claude_mcp("mcp__x__run", {"p": json.dumps({"order": "asc"})})))
        self.assertIsNone(gate.decide(claude_mcp("mcp__x__run", {"p": "{not json"})))
        self.assertEqual("an MCP input too deep to read",
                         gate.decide(claude_mcp("mcp__x__run", {"p": json.dumps(nested(gate.MAX_DEPTH + 1))})))
        self.assertEqual("an MCP input too deep to read",
                         gate.decide(claude_mcp("mcp__x__run", {"p": "[" * 5000 + "]" * 5000})))

    def test_cursor_reads_the_mcp_tool_whatever_the_server_type(self) -> None:
        stdio = cursor_mcp_stdio("broker", "place_order", "{}", "npx -y @alpaca/mcp-server")
        self.assertEqual("an MCP tool named broker__place_order (order)", gate.decide(stdio, "cursor"))
        self.assertEqual("an MCP input value 'sell_to_close'",
                         gate.decide(cursor_mcp_stdio("x", "run", '{"side": "sell_to_close"}', "x"), "cursor"))
        self.assertIsNone(gate.decide(cursor_mcp_stdio("github", "list_issues", "{}", SAMPLE), "cursor"),
                          "the launch command is not the call")
        self.assertEqual("an HTTP write to /v1/accounts", gate.decide(cursor_shell(SAMPLE), "cursor"))

    def test_unknown_runtime_shapes_get_no_opinion(self) -> None:
        self.assertIsNone(gate.decide("PreToolUse"))
        self.assertIsNone(gate.decide(None))
        self.assertIsNone(gate.decide({"hook_event_name": "PreToolUse"}))
        self.assertIsNone(gate.decide(cursor_shell(SAMPLE)), "a top-level command is read only in Cursor mode")


# --------------------------------------------------------------------------------------------
# The process contract
# --------------------------------------------------------------------------------------------


class ProcessContractTest(unittest.TestCase):
    def assert_denied(self, proc: "subprocess.CompletedProcess[bytes]", cursor: bool, vector: str) -> str:
        self.assertEqual(2, proc.returncode, (vector, proc.stdout, proc.stderr))
        lines = proc.stdout.decode("utf-8").splitlines()
        self.assertEqual(1, len(lines), (vector, proc.stdout))
        body = json.loads(lines[0])
        reason = proc.stderr.decode("utf-8")
        self.assertTrue(reason.endswith("\n"))
        reason = reason[:-1]
        if cursor:
            self.assertEqual({"permission": "deny", "user_message": reason, "agent_message": reason}, body)
        else:
            self.assertEqual(("deny", reason), (body["decision"], body["reason"]))
            self.assertEqual({"hookEventName": "PreToolUse", "permissionDecision": "deny",
                              "permissionDecisionReason": reason}, body["hookSpecificOutput"])
            self.assertNotIn("permission", body)
        self.assertTrue(reason.startswith("Aegis Desk denied this call: it looks like a broker write ("), reason)
        self.assertTrue(reason.endswith("). " + BOUNDARY), reason)
        self.assertLessEqual(len(reason), 250)
        self.assertTrue(all(32 <= ord(char) < 127 for char in reason), reason)
        return reason

    def test_denied_vectors_exit_2_with_the_deny_output(self) -> None:
        for vector_id, args, event in DENIED:
            with self.subTest(vector=vector_id):
                payload = encode(event)
                proc = run_gate(payload, args)
                reason = self.assert_denied(proc, runtime_of(args) == "cursor", vector_id)
                self.assertEqual(gate.reason_text(gate.decide(event, runtime_of(args)) or ""), reason)

    def test_no_opinion_vectors_exit_0_and_print_nothing(self) -> None:
        for vector_id, args, event in NO_OPINION:
            with self.subTest(vector=vector_id):
                payload = encode(event)
                proc = run_gate(payload, args)
                self.assertEqual((0, quiet_stdout(args, payload), b""), (proc.returncode, proc.stdout, proc.stderr),
                                 vector_id)

    def test_malformed_input_fails_open_silently(self) -> None:
        for vector_id, args, payload in MALFORMED:
            with self.subTest(vector=vector_id):
                proc = run_gate(payload, args)
                self.assertEqual((0, quiet_stdout(args, payload), b""), (proc.returncode, proc.stdout, proc.stderr),
                                 vector_id)

    def test_cursor_permission_events_always_get_an_explicit_answer(self) -> None:
        for event in (cursor_shell("git status"), cursor_mcp("github", "list_issues", '{"state": "open"}'),
                      cursor_shell(5), {"hook_event_name": "beforeMCPExecution"}):
            with self.subTest(event=event):
                payload = encode(event)
                proc = run_gate(payload, CURSOR)
                self.assertEqual((0, CURSOR_ASK, b""), (proc.returncode, proc.stdout, proc.stderr))
                self.assertEqual({"permission": "ask"}, json.loads(proc.stdout.decode("utf-8")))
        self.assertEqual((0, b"", b""), tuple(getattr(run_gate(b"not json", CURSOR), key)
                                              for key in ("returncode", "stdout", "stderr")),
                         "unparsable stdin stays silent, so Cursor's failClosed blocks it")
        for args in ((), ("--runtime", "grok")):
            proc = run_gate(encode(cursor_shell("git status")), args)
            self.assertEqual((0, b"", b""), (proc.returncode, proc.stdout, proc.stderr), args)

    def test_input_nested_too_deeply_to_parse_is_denied(self) -> None:
        payload = b'{"hook_event_name": "PreToolUse", "tool_name": "mcp__x__run", "tool_input": ' + \
            b"[" * 200000 + b"]" * 200000 + b"}"
        proc = run_gate(payload)
        reason = self.assert_denied(proc, False, "deep")
        self.assertIn("nested too deeply", reason)

    def test_the_release_check_sample_reason_is_exact(self) -> None:
        payload = encode(claude_bash(SAMPLE))
        proc = run_gate(payload)
        self.assertEqual(SAMPLE_REASON, self.assert_denied(proc, False, "D01"))
        self.assertEqual(SAMPLE_REASON, gate.reason_text(gate.decide(claude_bash(SAMPLE)) or ""))

    def test_a_long_label_is_cut_never_the_boundary_clause(self) -> None:
        name = "mcp__" + "x" * 300 + "__place_order"
        payload = encode(claude_mcp(name, {}))
        reason = self.assert_denied(run_gate(payload), False, "long")
        label = reason[len("Aegis Desk denied this call: it looks like a broker write ("):-len("). " + BOUNDARY)]
        self.assertEqual(gate.MAX_LABEL, len(label))
        self.assertTrue(label.endswith("..."))

    def test_unprintable_text_never_reaches_the_reason(self) -> None:
        payload = encode(claude_mcp("mcp__xé\n\x1b__place_order", {}))
        reason = self.assert_denied(run_gate(payload), False, "unprintable")
        self.assertNotIn("\x1b", reason)

    def test_other_events_get_no_opinion_even_with_a_write(self) -> None:
        for event_name in ("PostToolUse", "Stop", "SubagentStop", "UserPromptSubmit", "SessionStart", "Notification",
                           "post_tool_use", "afterShellExecution", "beforeReadFile", "stop"):
            event = claude_bash(SAMPLE)
            event["hook_event_name"] = event_name
            with self.subTest(event=event_name):
                payload = encode(event)
                for args in ((), CURSOR):
                    proc = run_gate(payload, args)
                    self.assertEqual((0, b"", b""), (proc.returncode, proc.stdout, proc.stderr))


class TruncationTest(unittest.TestCase):
    def test_a_truncated_call_is_denied_even_when_it_reads(self) -> None:
        for event in (grok_full("cat README.md", truncated=True), grok_camel("git status", truncated=True),
                      grok_mcp("github__list_issues", {"state": "open"}, truncated=True)):
            with self.subTest(event=event.get("toolName")):
                self.assertIn("truncated", gate.decide(event) or "")

    def test_truncation_of_other_tools_gets_no_opinion(self) -> None:
        event = {"hookEventName": "pre_tool_use", "toolName": "read_file", "toolInput": {"path": "x"},
                 "toolInputTruncated": True}
        self.assertIsNone(gate.decide(event))

    def test_only_a_true_flag_counts(self) -> None:
        for flag in (False, None, "true", 1):
            event = grok_full("cat README.md")
            event["toolInputTruncated"] = flag
            with self.subTest(flag=flag):
                self.assertIsNone(gate.decide(event))


class ArgumentsTest(unittest.TestCase):
    def test_unknown_arguments_exit_1_never_2(self) -> None:
        payload = encode(claude_bash(SAMPLE))
        for args in (("--bogus",), ("--runtime",), ("--runtime", "vscode"), ("--runtime=",), ("-h",), ("--help",),
                     ("--runtime", "cursor", "--json"), ("cursor",)):
            with self.subTest(args=args):
                proc = run_gate(payload, args)
                self.assertEqual(1, proc.returncode)
                self.assertEqual(b"", proc.stdout)
                self.assertEqual("broker_write_gate: unknown arguments %r\n" % (list(args),),
                                 proc.stderr.decode("utf-8"))
        proc = run_gate(payload, ("--bogus",))
        self.assertEqual("broker_write_gate: unknown arguments ['--bogus']\n", proc.stderr.decode("utf-8"))

    def test_accepted_runtime_spellings(self) -> None:
        payload = encode(claude_bash(SAMPLE))
        for args in ((), ("--runtime", "claude"), ("--runtime", "grok"), ("--runtime=claude",), ("--runtime=grok",)):
            with self.subTest(args=args):
                self.assertEqual(2, run_gate(payload, args).returncode)
        payload = encode(cursor_shell(SAMPLE))
        for args in (CURSOR, ("--runtime=cursor",)):
            with self.subTest(args=args):
                proc = run_gate(payload, args)
                self.assertEqual(2, proc.returncode)
                self.assertEqual("deny", json.loads(proc.stdout.decode("utf-8"))["permission"])


# --------------------------------------------------------------------------------------------
# The repository's own commands get no opinion
# --------------------------------------------------------------------------------------------


def repository_tools() -> List[str]:
    return sorted(os.path.basename(path) for path in glob.glob(os.path.join(ROOT, "scripts", "*.py")))


def routine_commands() -> List[Tuple[str, str]]:
    """``(prompt, command)`` for every routine and slot of ``routines/manifest.toml``."""
    manifest = routines.load_manifest(os.path.join(ROOT, "routines", "manifest.toml"))
    found = []
    for routine in manifest.routines:
        for slot in routine.slots():
            prompt = routine.prompt.replace("{slot}", slot)
            match = re.search(r"python3 \S+routine\.py .*?--slot \S+", prompt)
            assert match is not None, prompt
            found.append((prompt, match.group(0)))
    return found


class RepositoryCommandsTest(unittest.TestCase):
    def test_every_tool_help_and_schema_gets_no_opinion(self) -> None:
        tools = repository_tools()
        self.assertGreater(len(tools), 20)
        for name in tools:
            for flag in ("--help", "--schema"):
                command = "python3 scripts/%s %s" % (name, flag)
                with self.subTest(command=command):
                    self.assertIsNone(gate.decide(claude_bash(command)))
                    self.assertIsNone(gate.decide(grok_full(command)))
                    self.assertIsNone(gate.decide(cursor_shell(command), "cursor"))

    def test_the_structuring_desk_commands_get_no_opinion_and_an_mcp_trade_key_is_denied(self) -> None:
        def shell_tool(event: Any, command: str) -> Any:
            """The shell tool name of a no-opinion vector that runs ``command`` (None for any other vector)."""
            if isinstance(event, dict) and isinstance(event.get("tool_input"), dict):
                if event["tool_input"].get("command") == command:
                    return event.get("tool_name")
            return None

        for command in STRUCTURING_COMMANDS:
            with self.subTest(command=command):
                shells = sorted(name for name in (shell_tool(event, command) for _id, _args, event in NO_OPINION) if name)
                self.assertEqual(["Bash", "run_terminal_command"], shells, "the Claude Code and the Grok Build shapes")
        for name in ("exit_ticket.py", "roll_analysis.py", "propose.py"):
            self.assertTrue(any(name in command for command in STRUCTURING_COMMANDS), name)
        # The documented false positive (designer C's D8): an MCP input key named trade is denied; trade_id is a read.
        self.assertIsNotNone(gate.decide(claude_mcp("mcp__aegis__roll_analysis", {"trade": "AG-20261020-01"})))
        self.assertIsNotNone(gate.decide(grok_mcp("aegis__exit_ticket", {"trade": "AG-20261020-01", "action": "CLOSE"})))
        self.assertIsNone(gate.decide(grok_mcp("aegis__exit_ticket", {"trade_id": "AG-20261020-01", "action": "CLOSE"})))

    def test_every_routine_prompt_and_command_gets_no_opinion(self) -> None:
        found = routine_commands()
        self.assertGreater(len(found), 6)
        for prompt, command in found:
            with self.subTest(command=command):
                for text in (prompt, command, "cd /workspace/aegis-grok-desk && " + command):
                    self.assertIsNone(gate.decide(claude_bash(text)))
                    self.assertIsNone(gate.decide(grok_full(text)))
                payload = encode(grok_full(command))
                proc = run_gate(payload)
                self.assertEqual((0, b"", b""), (proc.returncode, proc.stdout, proc.stderr))


# --------------------------------------------------------------------------------------------
# Hermetic: what the module may import, and what a run leaves behind
# --------------------------------------------------------------------------------------------


class HermeticTest(unittest.TestCase):
    def tree(self) -> ast.Module:
        with open(GATE, encoding="utf-8") as handle:
            return ast.parse(handle.read(), filename=GATE)

    def test_imports_are_json_re_sys_and_typing_only(self) -> None:
        imported = set()
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertLessEqual(imported, ALLOWED_IMPORTS)

    def test_no_file_access_and_no_argparse(self) -> None:
        names = {node.id for node in ast.walk(self.tree()) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(self.tree()) if isinstance(node, ast.Attribute)}
        self.assertNotIn("open", names)
        self.assertNotIn("argparse", names)
        self.assertFalse({"environ", "getenv", "chdir", "listdir", "remove", "unlink"} & attributes)

    def test_a_run_leaves_its_working_directory_untouched(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-gate-cwd-") as directory:
            for event, args in ((claude_bash(SAMPLE), ()), (claude_bash("git status"), ()),
                                (cursor_shell(SAMPLE), CURSOR)):
                payload = encode(event)
                run_gate(payload, args, cwd=directory)
            self.assertEqual([], os.listdir(directory))

    def test_the_module_starts_with_the_adaptation_header(self) -> None:
        docstring = ast.get_docstring(self.tree()) or ""
        self.assertIn("Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased "
                      "changes, commit 75a4c79", " ".join(docstring.split()))
        self.assertIn("hooks/exchange_write_gate.py", docstring)
        self.assertIn("fail-open", docstring)


# --------------------------------------------------------------------------------------------
# Registration: hooks/hooks.json, hooks/cursor-hooks.json and the manifests
# --------------------------------------------------------------------------------------------

SHELL_MATCHER = "Bash|PowerShell|Monitor|run_terminal_command"
MCP_MATCHER = ".+__.+"
HOOK_COMMAND = 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/broker_write_gate.py"'
CURSOR_COMMAND = 'python3 "${CURSOR_PLUGIN_ROOT}/hooks/broker_write_gate.py" --runtime cursor'


def load(rel: str) -> Any:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return json.load(handle)


def registered_argv(command: str, variable: str) -> List[str]:
    """The registered shell-form command with its plugin-root variable expanded, run by this interpreter."""
    argv = shlex.split(command.replace("${%s}" % variable, ROOT))
    assert argv[0] == "python3", argv
    return [sys.executable] + argv[1:]


class RegistrationTest(unittest.TestCase):
    def test_hooks_json_has_the_two_pre_tool_use_groups(self) -> None:
        hooks = load("hooks/hooks.json")
        self.assertEqual(["PreToolUse"], sorted(hooks["hooks"]))
        groups = hooks["hooks"]["PreToolUse"]
        self.assertEqual([SHELL_MATCHER, MCP_MATCHER], [group["matcher"] for group in groups])
        for group in groups:
            self.assertEqual([{"type": "command", "command": HOOK_COMMAND, "timeout": 10}], group["hooks"])
        self.assertNotIn("args", json.dumps(hooks))
        self.assertIsNone(re.search(r"\bv\d+\.\d+\.\d+\b", hooks["description"]))

    def test_the_matchers_select_the_shell_and_mcp_tools(self) -> None:
        for name in ("Bash", "PowerShell", "Monitor", "run_terminal_command"):
            self.assertTrue(re.search(SHELL_MATCHER, name))
            self.assertIn(name, gate.SHELL_TOOLS)
            self.assertFalse(re.search(MCP_MATCHER, name))
        for name in ("mcp__github__list_issues", "blackhole__place_order", "mcp__plugin_x_y__z"):
            self.assertTrue(re.search(MCP_MATCHER, name))
        for name in ("Write", "Edit", "Read", "WebFetch"):
            self.assertFalse(re.search(SHELL_MATCHER, name) or re.search(MCP_MATCHER, name))

    def test_cursor_hooks_cover_shell_and_mcp_fail_closed(self) -> None:
        cursor = load("hooks/cursor-hooks.json")
        self.assertEqual(1, cursor["version"])
        self.assertEqual(["beforeMCPExecution", "beforeShellExecution"], sorted(cursor["hooks"]))
        for entries in cursor["hooks"].values():
            self.assertEqual([{"command": CURSOR_COMMAND, "timeout": 10, "failClosed": True}], entries)

    def test_hooks_are_declared_once_per_runtime(self) -> None:
        for rel in ("plugin.json", ".grok-plugin/plugin.json", ".claude-plugin/plugin.json"):
            with self.subTest(manifest=rel):
                self.assertNotIn("hooks", load(rel))
        self.assertEqual("./hooks/cursor-hooks.json", load(".cursor-plugin/plugin.json")["hooks"])

    def test_the_registered_commands_run_the_gate(self) -> None:
        argv = registered_argv(HOOK_COMMAND, "CLAUDE_PLUGIN_ROOT")
        for event in (claude_bash(SAMPLE), grok_full(SAMPLE), grok_mcp("blackhole__place_order", {"symbol": "SPY"})):
            payload = encode(event)
            proc = subprocess.run(argv, input=payload, capture_output=True, timeout=60, env=gate_environment(),
                                  cwd=ROOT, check=False)
            self.assertEqual(2, proc.returncode, proc.stderr)
        argv = registered_argv(CURSOR_COMMAND, "CURSOR_PLUGIN_ROOT")
        for event in (cursor_shell(SAMPLE), cursor_mcp("blackhole", "place_order", '{"symbol": "SPY"}')):
            payload = encode(event)
            proc = subprocess.run(argv, input=payload, capture_output=True, timeout=60, env=gate_environment(),
                                  cwd=ROOT, check=False)
            self.assertEqual(2, proc.returncode, proc.stderr)
            self.assertEqual("deny", json.loads(proc.stdout.decode("utf-8"))["permission"])
        payload = encode(cursor_shell("git status"))
        proc = subprocess.run(argv, input=payload, capture_output=True, timeout=60, env=gate_environment(), cwd=ROOT,
                              check=False)
        self.assertEqual((0, CURSOR_ASK, b""), (proc.returncode, proc.stdout, proc.stderr))


if __name__ == "__main__":
    unittest.main()
