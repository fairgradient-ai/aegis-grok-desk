#!/usr/bin/env python3
"""Run the published command journeys as subprocesses in a scrubbed environment. Stdlib only.

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased
changes, commit 75a4c79 (scripts/cli_journeys.py). The reference drove its commands against a
loopback fake exchange; Aegis Desk journeys run with no server at all: the desk tools read the
recorded Tradier fixtures of the offline fixture desk root.

A journey is one command the documentation tells a Bot to run, executed exactly as a Bot would
run it: as a subprocess from the repository root, in an environment that carries only ``PATH``,
``HOME`` and the Python UTF-8 switches (no proxy variables, no market-data token, nothing that
could reach a network), with its exit code and JSON output checked against the contract.
Every journey's output is also screened for order-shaped text (``FORBIDDEN_OUTPUT``): an
adversarial input must never produce an instruction to trade.

Two kinds of journey share the ``Journey`` record. The three repository gates run against the
tree under test (``--root``). Tool journeys (``desk_root`` set) run a desk tool from this checkout
against this checkout's fixture desk root ``tests/fixtures/desk`` with ``--json --offline --now
<the fixture set's instant>``; with ``copy_desk_root`` the harness copies that root to a
``tempfile.mkdtemp`` directory, passes the copy as ``--desk-root`` and removes it afterwards, so a
journey never writes into the repository. Because tool journeys exercise this checkout's tools and
fixtures, they are evidence about this checkout only: they run when the tree under test is this
checkout and are reported as not run against any other tree (the gate's fixture trees), where
the gate journeys alone decide.

``argv`` names a script under ``scripts/`` and its arguments, ``expected_exit`` the exit code and
``expected_json`` keys that must be present (value ``ANY``) or equal; a key may be a dotted path
into the JSON output (``payload.requests_made``, ``payload.symbols.0.label``).

Usage: ``python3 scripts/cli_journeys.py [--root DIR] [--json]``. ``--root`` is the tree the
gates are run against (default: this repository); the scripts always come from this checkout.
Exit 0 when every journey that ran passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
TIMEOUT_SECONDS = 60
ANY = object()
FORBIDDEN_OUTPUT: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"\b(?:place|submit|cancel|replace)_order\b"),
    re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\s+/v\d+/accounts\b"),
)
FIXTURE_DESK = "tests/fixtures/desk"
# The fixture sets' instants (tests/fixtures/desk/fixtures/tradier/README.md): regular and stale
# quotes are read at 14:50 ET on 2026-11-03, after_close at 16:25 ET, holiday on Thanksgiving.
REGULAR_NOW = "2026-11-03T19:50:00Z"
AFTER_CLOSE_NOW = "2026-11-03T21:25:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"


@dataclasses.dataclass(frozen=True)
class Journey:
    name: str
    argv: Tuple[str, ...]
    expected_exit: int = 0
    expected_json: Optional[Dict[str, Any]] = None
    desk_root: Optional[str] = None
    copy_desk_root: bool = False


def tool_journey(name: str, argv: Tuple[str, ...], expected_exit: int = 0,
                 expected_json: Optional[Dict[str, Any]] = None, now: str = REGULAR_NOW) -> Journey:
    """A desk-tool journey: ``--json --offline --now <instant>`` against a copy of the fixture desk."""
    return Journey(name, tuple(argv) + ("--json", "--offline", "--now", now), expected_exit, expected_json,
                   FIXTURE_DESK, True)


JOURNEYS: Tuple[Journey, ...] = (
    Journey("check_manifests --json", ("check_manifests.py", ".", "--json"), 0, {"ok": True, "version": ANY}),
    Journey("check_no_write_paths --json", ("check_no_write_paths.py", ".", "--json"), 0, {"ok": True, "scanned": ANY}),
    Journey("check_grok_template --json", ("check_grok_template.py", ".", "--json"), 0, {"ok": True, "status": ANY}),
    tool_journey("market_clock --at regular --expiry 2026-11-20",
                 ("market_clock.py", "--at", REGULAR_NOW, "--expiry", "2026-11-20"), 0,
                 {"status": "ok", "tool": "market_clock", "payload.session": "open", "payload.minutes_to_close": 70,
                  "payload.expiry.dte_calendar": 17, "payload.expiry.dte_trading": 13}),
    tool_journey("quote_cache regular SPY WMT WMT261120C00105000",
                 ("quote_cache.py", "SPY", "WMT", "WMT261120C00105000"), 0,
                 {"status": "ok", "tool": "quote_cache", "payload.requests_made": 1, "payload.missing": {}}),
    tool_journey("quote_cache stale SPY (served; QC decides staleness)",
                 ("quote_cache.py", "SPY", "--fixture-set", "stale"), 0,
                 {"status": "ok", "payload.missing": {}}),
    tool_journey("quote_qc regular SPY WMT", ("quote_qc.py", "SPY", "WMT", "--fixture-set", "regular"), 0,
                 {"status": "ok", "tool": "quote_qc", "payload.bound_min": 25}),
    tool_journey("quote_qc stale SPY -> UNAVAILABLE", ("quote_qc.py", "SPY", "--fixture-set", "stale"), 20,
                 {"status": "unavailable", "payload.symbols.0.usable_for_rules": False}),
    tool_journey("quote_qc after_close SPY (as of close)", ("quote_qc.py", "SPY", "--fixture-set", "after_close"), 0,
                 {"status": "ok", "payload.symbols.0.label": "as of close 16:00 ET"}, now=AFTER_CLOSE_NOW),
    tool_journey("quote_qc holiday SPY -> UNAVAILABLE", ("quote_qc.py", "SPY", "--fixture-set", "holiday"), 20,
                 {"status": "unavailable"}, now=HOLIDAY_NOW),
    tool_journey("heartbeat book-check",
                 ("heartbeat.py", "--routine", "book-check", "--exit-code", "0", "--status", "ok",
                  "--scheduled-et", "10:05"), 0,
                 {"status": "ok", "tool": "heartbeat", "payload.heartbeat.routine_id": "book-check",
                  "payload.heartbeat.lateness_min": 285, "payload.path": "state/heartbeat/book-check.json"}),
    tool_journey("heartbeat --list", ("heartbeat.py", "--list"), 0, {"status": "ok", "payload.count": 0}),
)


def scrubbed_environment() -> Dict[str, str]:
    """Only what the interpreter needs; no proxies, tokens or locale surprises."""
    env = {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    for key in ("PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def same_tree(root: str) -> bool:
    """True when ``root`` is this checkout (tool journeys are evidence about this checkout only)."""
    return os.path.realpath(root) == os.path.realpath(ROOT)


def lookup(document: Any, key: str) -> Tuple[bool, Any]:
    """``(found, value)`` for a top-level key or a dotted path (list indices as digits)."""
    if isinstance(document, dict) and key in document:
        return True, document[key]
    node = document
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def run_journey(journey: Journey, root: str) -> Dict[str, Any]:
    script = os.path.join(SCRIPTS, journey.argv[0])
    argv = [sys.executable, script] + list(journey.argv[1:])
    problems: List[str] = []
    temporary: Optional[str] = None
    try:
        if journey.desk_root is not None:
            source = os.path.join(ROOT, journey.desk_root)
            if journey.copy_desk_root:
                temporary = tempfile.mkdtemp(prefix="aegis-journey-")
                desk_root = os.path.join(temporary, "desk")
                try:
                    shutil.copytree(source, desk_root)
                except OSError as exc:
                    return {"name": journey.name, "ok": False, "exit": None,
                            "problems": [f"could not copy the fixture desk root {journey.desk_root}: {exc}"]}
            else:
                desk_root = source
            argv += ["--desk-root", desk_root]
        try:
            proc = subprocess.run(argv, cwd=root, env=scrubbed_environment(), capture_output=True,
                                  text=True, timeout=TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"name": journey.name, "ok": False, "exit": None, "problems": [f"could not run: {exc}"]}
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    if proc.returncode != journey.expected_exit:
        problems.append(f"exit {proc.returncode}, expected {journey.expected_exit}; "
                        f"stderr: {proc.stderr.strip()[:400]}; stdout: {proc.stdout.strip()[:400]}")
    payload: Any = None
    if journey.expected_json is not None:
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            problems.append(f"stdout is not JSON: {exc}; stdout: {proc.stdout.strip()[:400]}")
        else:
            if not isinstance(payload, dict):
                problems.append("JSON output must be an object")
            else:
                for key, expected in journey.expected_json.items():
                    found, value = lookup(payload, key)
                    if not found:
                        problems.append(f"JSON key '{key}' missing")
                    elif expected is not ANY and value != expected:
                        problems.append(f"JSON key '{key}' is {value!r}, expected {expected!r}")
    for pattern in FORBIDDEN_OUTPUT:
        if pattern.search(proc.stdout) or pattern.search(proc.stderr):
            problems.append(f"output contains order-shaped text matching /{pattern.pattern}/")
    return {"name": journey.name, "ok": not problems, "exit": proc.returncode, "problems": problems}


def select(root: str) -> Tuple[List[Journey], List[Journey]]:
    """``(to run, not run)``: tool journeys are skipped against a tree that is not this checkout."""
    if same_tree(root):
        return list(JOURNEYS), []
    run_now = [journey for journey in JOURNEYS if journey.desk_root is None]
    skipped = [journey for journey in JOURNEYS if journey.desk_root is not None]
    return run_now, skipped


def run(root: str) -> Dict[str, Any]:
    selected, skipped = select(root)
    results = [run_journey(journey, root) for journey in selected]
    return {"ok": all(r["ok"] for r in results), "root": root, "journeys": results,
            "not_run": [journey.name for journey in skipped]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=ROOT, help="tree the gate journeys run against (default: this repository)")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of prose")
    args = parser.parse_args(argv)
    result = run(os.path.abspath(args.root))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for item in result["journeys"]:
            if not item["ok"]:
                print(f"{item['name']}: FAIL")
                for problem in item["problems"]:
                    print(f"  {problem}")
        if result["ok"]:
            names = ", ".join(item["name"] for item in result["journeys"])
            print(f"ok: {len(result['journeys'])} command journeys passed in a scrubbed environment ({names}); "
                  "no order-shaped output")
        else:
            failed = sum(1 for item in result["journeys"] if not item["ok"])
            print(f"{failed} journey(s) failed")
        if result["not_run"]:
            print(f"note: {len(result['not_run'])} tool journey(s) not run against {result['root']}: they exercise "
                  "this checkout's tools and fixture desk, which say nothing about another tree")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
