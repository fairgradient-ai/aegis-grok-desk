#!/usr/bin/env python3
"""Run the published command journeys as subprocesses in a scrubbed environment. Stdlib only.

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased
changes, commit 75a4c79 (scripts/cli_journeys.py). The reference drove its commands against a
loopback fake exchange; Aegis Desk journeys run with no server at all: the desk tools read the
recorded Tradier fixtures of the offline fixture desk roots.

A journey is one command the documentation tells a Bot to run, executed exactly as a Bot would
run it: as a subprocess from the repository root, in an environment that carries only ``PATH``,
``HOME`` and the Python UTF-8 switches (no proxy variables, no market-data token, nothing that
could reach a network), with its exit code and JSON output checked against the contract.
Every journey's output is also screened for order-shaped text (``FORBIDDEN_OUTPUT``): an
adversarial input must never produce an instruction to trade.

Two kinds of journey share the ``Journey`` record. The three repository gates run against the
tree under test (``--root``). Tool journeys (``desk_root`` set) run a desk tool from this checkout
against one of this checkout's fixture desk roots (``tests/fixtures/desk``, the main book, or
``tests/fixtures/desk_recon``, the reconciliation book) with ``--json --offline --now <the fixture
set's instant>``; with ``copy_desk_root`` the harness copies that root to a ``tempfile.mkdtemp``
directory, passes the copy as ``--desk-root`` and removes it afterwards, so a journey never writes
into the repository. Because tool journeys exercise this checkout's tools and fixtures, they are
evidence about this checkout only: they run when the tree under test is this checkout and are
reported as not run against any other tree (the gate's fixture trees), where the gate journeys
alone decide.

A journey may carry ``steps``: commands run in order on the SAME desk-root copy BEFORE the
journey's own command. The snapshot chain is one: ``book.py --snapshot`` at the previous trading
day's close writes ``data/book/<date>.json``, then ``book.py`` on the next trading day reports the
day P/L measured against that snapshot. Each step is checked like the journey's command (exit code,
JSON keys, order-shaped text); the first failing step fails the journey and the later commands of
the chain are not run. One copy serves the whole chain and is removed afterwards.

``argv`` names a script under ``scripts/`` and its arguments, ``expected_exit`` the exit code and
``expected_json`` keys that must be present (value ``ANY``) or equal; a key may be a dotted path
into the JSON output (``payload.requests_made``, ``payload.symbols.0.label``). A JSON ``null`` is
compared as ``None``.

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
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
TIMEOUT_SECONDS = 60
ANY = object()
FORBIDDEN_OUTPUT: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"\b(?:place|submit|cancel|replace)_order\b"),
    re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\s+/v\d+/accounts\b"),
)
FIXTURE_DESK = "tests/fixtures/desk"
FIXTURE_DESK_RECON = "tests/fixtures/desk_recon"
# The fixture sets' instants (tests/fixtures/desk/fixtures/tradier/README.md and the recon root's
# README): regular and stale quotes are read at 14:50 ET on 2026-11-03, after_close at 16:25 ET,
# pre_open at the 2026-11-02 close (the EOD snapshot), t1 on Thursday 2026-11-19 (T-1 of the Nov-20
# expiry), expiry_day on Friday 2026-11-20, post_expiry on Saturday 2026-11-21 at 09:30 ET, the recon
# root on Tuesday 2026-11-24 at 07:00 ET (pre-open), holiday on Thanksgiving.
REGULAR_NOW = "2026-11-03T19:50:00Z"
AFTER_CLOSE_NOW = "2026-11-03T21:25:00Z"
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
T1_NOW = "2026-11-19T19:50:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
POST_EXPIRY_NOW = "2026-11-21T14:30:00Z"
RECON_NOW = "2026-11-24T12:00:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"


@dataclasses.dataclass(frozen=True)
class Step:
    """One command of a multi-step journey, checked like the journey's own command."""

    argv: Tuple[str, ...]
    expected_exit: int = 0
    expected_json: Optional[Dict[str, Any]] = None


@dataclasses.dataclass(frozen=True)
class Journey:
    name: str
    argv: Tuple[str, ...]
    expected_exit: int = 0
    expected_json: Optional[Dict[str, Any]] = None
    desk_root: Optional[str] = None
    copy_desk_root: bool = False
    steps: Tuple[Step, ...] = ()


def tool_argv(argv: Sequence[str], now: str) -> Tuple[str, ...]:
    """A tool command line with the offline flags: ``--json --offline --now <instant>``."""
    return tuple(argv) + ("--json", "--offline", "--now", now)


def tool_step(argv: Sequence[str], expected_exit: int = 0, expected_json: Optional[Dict[str, Any]] = None,
              now: str = REGULAR_NOW) -> Step:
    """A step of a tool journey: ``--json --offline --now <instant>`` on the journey's desk-root copy."""
    return Step(tool_argv(argv, now), expected_exit, expected_json)


def tool_journey(name: str, argv: Sequence[str], expected_exit: int = 0,
                 expected_json: Optional[Dict[str, Any]] = None, now: str = REGULAR_NOW,
                 desk_root: str = FIXTURE_DESK, steps: Sequence[Step] = ()) -> Journey:
    """A desk-tool journey: ``--json --offline --now <instant>`` against a copy of a fixture desk root
    (the main root unless ``desk_root`` says otherwise), after its ``steps`` on the same copy."""
    return Journey(name, tool_argv(argv, now), expected_exit, expected_json, desk_root, True, tuple(steps))


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
    # PR 2b: the book of record. The reconciliation on its own fixture root (every settlement branch at
    # the Tuesday pre-open), then on the main root (CLEAN, the post-expiry Saturday, Thanksgiving).
    tool_journey("csv_recon --diff recon 2026-11-24 -> UNAVAILABLE (4 edits)",
                 ("csv_recon.py", "--diff", "--fixture-set", "recon"), 20,
                 {"status": "unavailable", "tool": "csv_recon", "payload.verdict": "UNAVAILABLE",
                  "payload.counts.edits": 4, "payload.counts.unavailable": 4,
                  "payload.quote_check.quoted": ["SPY", "SPY261218P00620000"]},
                 now=RECON_NOW, desk_root=FIXTURE_DESK_RECON),
    tool_journey("csv_recon recon twice (closed history served from the cache)",
                 ("csv_recon.py", "--fixture-set", "recon"), 20,
                 {"status": "unavailable", "payload.verdict": "UNAVAILABLE", "payload.counts.edits": 4,
                  "payload.history.0.symbol": "WMT", "payload.history.0.served_from": "cache"},
                 now=RECON_NOW, desk_root=FIXTURE_DESK_RECON,
                 steps=(tool_step(("csv_recon.py", "--fixture-set", "recon"), 20,
                                  {"payload.verdict": "UNAVAILABLE", "payload.history.0.symbol": "WMT",
                                   "payload.history.0.served_from": "fetch"}, now=RECON_NOW),)),
    tool_journey("csv_recon regular -> CLEAN", ("csv_recon.py", "--fixture-set", "regular"), 0,
                 {"status": "ok", "payload.verdict": "CLEAN", "payload.counts.edits": 0,
                  "payload.quote_check.not_run_reason": None}),
    tool_journey("csv_recon post_expiry Saturday -> EDITS_PENDING (3 edits)",
                 ("csv_recon.py", "--fixture-set", "post_expiry"), 10,
                 {"status": "fired", "payload.verdict": "EDITS_PENDING", "payload.counts.edits": 3,
                  "payload.quote_check.not_run_reason": "market_closed"}, now=POST_EXPIRY_NOW),
    tool_journey("csv_recon holiday -> UNAVAILABLE (2 edits)", ("csv_recon.py", "--fixture-set", "holiday"), 20,
                 {"status": "unavailable", "payload.verdict": "UNAVAILABLE", "payload.counts.edits": 2}, now=HOLIDAY_NOW),
    tool_journey("positions_check regular -> CLEAN", ("positions_check.py", "--fixture-set", "regular"), 0,
                 {"status": "ok", "tool": "positions_check", "payload.verdict": "CLEAN", "payload.counts.open_groups": 3}),
    # The book: the goldens of the main root, the stale set, and the snapshot chain on one copy.
    tool_journey("book regular", ("book.py", "--fixture-set", "regular"), 0,
                 {"status": "ok", "tool": "book", "payload.portfolio.unrealized_pl_mid": "4097.00",
                  "payload.portfolio.realized_pl_ytd": "425.00", "payload.portfolio.day_pl": None}),
    tool_journey("book stale -> UNAVAILABLE", ("book.py", "--fixture-set", "stale"), 20,
                 {"status": "unavailable", "payload.portfolio.unrealized_pl_mid": None}),
    tool_journey("book --snapshot pre_open 2026-11-02 then book regular 2026-11-03 (day P/L from the snapshot)",
                 ("book.py", "--fixture-set", "regular"), 0,
                 {"status": "ok", "payload.portfolio.day_pl": "400.00"},
                 steps=(tool_step(("book.py", "--snapshot", "--fixture-set", "pre_open"), 0, {"status": "ok"},
                                  now=PREV_EOD_NOW),)),
    # The calendar lint (no market data: no --fixture-set flag), the assignment and expiry tools.
    tool_journey("calendar_check -> clean", ("calendar_check.py",), 0,
                 {"status": "ok", "tool": "calendar_check", "payload.verdict": "clean"}),
    tool_journey("calendar_check --max-age-days 1 -> UNAVAILABLE (STALE)", ("calendar_check.py", "--max-age-days", "1"), 20,
                 {"status": "unavailable", "payload.verdict": "unavailable", "payload.counts.STALE": 6}),
    tool_journey("assignment_watch regular -> quiet", ("assignment_watch.py", "--fixture-set", "regular"), 0,
                 {"status": "ok", "tool": "assignment_watch", "payload.verdict": "quiet"}),
    # The WMT call expires Fri 2026-11-20 and the WMT ex-dividend Tue 2026-11-24 has its last cum-dividend day on
    # Mon 2026-11-23, after the call's last trading day: no dividend tier is listed (a JSON null) and the NVDA pin fires.
    tool_journey("assignment_watch t1 -> FIRED (NVDA PIN HIGH; the WMT call expires before its ex-dividend)",
                 ("assignment_watch.py", "--fixture-set", "t1"), 10,
                 {"status": "fired", "payload.verdict": "fired", "payload.legs.0.dividend": None,
                  "payload.legs.0.pin.tier": "PIN WATCH", "payload.legs.1.pin.tier": "PIN HIGH",
                  "payload.legs.1.pin.distance_pct": "0.52"}, now=T1_NOW),
    tool_journey("expiry_checklist --week t1 (2 contracts due)", ("expiry_checklist.py", "--week", "--fixture-set", "t1"), 10,
                 {"status": "fired", "tool": "expiry_checklist", "payload.mode": "week", "payload.counts.listed": 2}, now=T1_NOW),
    tool_journey("expiry_checklist --tomorrow t1 (T-1 preview)", ("expiry_checklist.py", "--tomorrow", "--fixture-set", "t1"), 10,
                 {"status": "fired", "payload.mode": "tomorrow", "payload.counts.listed": 2}, now=T1_NOW),
    tool_journey("expiry_checklist --final-call expiry_day (act by 16:30 ET)",
                 ("expiry_checklist.py", "--final-call", "--fixture-set", "expiry_day"), 10,
                 {"status": "fired", "payload.mode": "final-call", "payload.legs.0.pin.tier": "PIN HIGH",
                  "payload.legs.1.pin.tier": "PIN WATCH", "payload.legs.1.act_by_utc": "2026-11-20T21:30:00+00:00"},
                 now=EXPIRY_DAY_NOW),
    tool_journey("expiry_checklist --week regular (nothing due)", ("expiry_checklist.py", "--week", "--fixture-set", "regular"), 0,
                 {"status": "ok", "payload.mode": "week", "payload.counts.listed": 0}),
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


def check_command(argv: Sequence[str], expected_exit: int, expected_json: Optional[Dict[str, Any]], root: str,
                  extra: Sequence[str] = ()) -> Tuple[Optional[int], List[str]]:
    """Run one command of a journey and check it: ``(exit code, problems)``; the exit code is ``None``
    when the command could not run at all. ``extra`` is appended to the command line (``--desk-root``)."""
    script = os.path.join(SCRIPTS, argv[0])
    command = [sys.executable, script] + list(argv[1:]) + list(extra)
    try:
        proc = subprocess.run(command, cwd=root, env=scrubbed_environment(), capture_output=True,
                              text=True, timeout=TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, [f"could not run: {exc}"]
    problems: List[str] = []
    if proc.returncode != expected_exit:
        problems.append(f"exit {proc.returncode}, expected {expected_exit}; "
                        f"stderr: {proc.stderr.strip()[:400]}; stdout: {proc.stdout.strip()[:400]}")
    if expected_json is not None:
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            problems.append(f"stdout is not JSON: {exc}; stdout: {proc.stdout.strip()[:400]}")
        else:
            if not isinstance(payload, dict):
                problems.append("JSON output must be an object")
            else:
                for key, expected in expected_json.items():
                    found, value = lookup(payload, key)
                    if not found:
                        problems.append(f"JSON key '{key}' missing")
                    elif expected is not ANY and value != expected:
                        problems.append(f"JSON key '{key}' is {value!r}, expected {expected!r}")
    for pattern in FORBIDDEN_OUTPUT:
        if pattern.search(proc.stdout) or pattern.search(proc.stderr):
            problems.append(f"output contains order-shaped text matching /{pattern.pattern}/")
    return proc.returncode, problems


def run_journey(journey: Journey, root: str) -> Dict[str, Any]:
    """Run the journey's steps, then its command, on one desk-root copy; the result carries ``exit``
    (``None`` when the command was not run), ``problems`` and one ``steps`` entry per step that ran."""
    temporary: Optional[str] = None
    extra: List[str] = []
    steps: List[Dict[str, Any]] = []

    def outcome(exit_code: Optional[int], problems: List[str]) -> Dict[str, Any]:
        return {"name": journey.name, "ok": not problems, "exit": exit_code, "problems": problems, "steps": steps}

    try:
        if journey.desk_root is not None:
            source = os.path.join(ROOT, journey.desk_root)
            if journey.copy_desk_root:
                temporary = tempfile.mkdtemp(prefix="aegis-journey-")
                desk_root = os.path.join(temporary, "desk")
                try:
                    shutil.copytree(source, desk_root)
                except OSError as exc:
                    return outcome(None, [f"could not copy the fixture desk root {journey.desk_root}: {exc}"])
            else:
                desk_root = source
            extra = ["--desk-root", desk_root]
        total = len(journey.steps) + 1
        for index, step in enumerate(journey.steps, 1):
            code, problems = check_command(step.argv, step.expected_exit, step.expected_json, root, extra)
            steps.append({"argv": list(step.argv), "exit": code, "problems": problems})
            if problems:
                label = f"step {index} of {total} ({' '.join(step.argv)})"
                return outcome(None, [f"{label}: {problem}" for problem in problems]
                               + [f"{total - index} later command(s) of the chain not run"])
        code, problems = check_command(journey.argv, journey.expected_exit, journey.expected_json, root, extra)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    return outcome(code, problems)


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
