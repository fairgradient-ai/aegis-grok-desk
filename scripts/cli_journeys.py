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
adversarial input must never produce an instruction to trade. The screen is the harness's own two
patterns (order endpoints and calls) plus every forbidden phrase of the alert lint's table
(``aegis_desk.alert_lint.FORBIDDEN_PATTERNS``, the one table in the repository that spells them out), so no
journey's stdout or stderr can carry the language an alert may not carry.

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
``expected_json`` keys that must be present (value ``ANY``), equal, or (a :class:`TextMatch`) a string
that starts with a given text and contains given parts; a key may be a dotted path into the JSON output
(``payload.requests_made``, ``payload.symbols.0.label``). A JSON ``null`` is compared as ``None``.

The PR 2c journeys exercise the rules engine on the main fixture root with its adopted example rulebook
(``rules/``): ``exit_check`` on the regular, expiry-day and stale sets (with and without a next run after
the close, which decides whether one observation suffices), a candidate rulebook evaluated twice with
``--dry-run`` (the second run finds no rule state), ``book_check`` (CONFIG CHANGED before ``ack.py config``;
the pre-close line; the posting chain: ``ack.py config --owner-tapped``, ``book_check.py --post`` at 14:50 ET
and again a minute later, which is quiet, then the heartbeat and the open signals read back), the alert
lint on every passing and red-team fixture of ``tests/fixtures/alerts`` (paths relative to the repository
root, the journeys' working directory), ``ack.py list`` on a fresh copy, ``rules_diff`` of the 1.1.0
candidate, and ``rules_adopt`` after the close (read back through ``ack.py config``) and inside regular
hours (refused). The fixture roots hold no runtime state, so every journey that needs acknowledged hashes
runs ``ack.py config --owner-tapped`` as its first step.

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

from aegis_desk import alert_lint

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
TIMEOUT_SECONDS = 60
ANY = object()
FORBIDDEN_OUTPUT: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"\b(?:place|submit|cancel|replace)_order\b"),
    re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\s+/v\d+/accounts\b"),
) + alert_lint.FORBIDDEN_PATTERNS
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
# PR 2c instants on the regular and expiry-day sets: the pre-close summary at 15:35 ET (the 14:31 ET quotes are
# then past their 25-minute bound), an adoption after the 2026-11-03 close, and the next routine run after the
# 2026-11-20 close (one observation then suffices for an M or G rule).
PRE_CLOSE_NOW = "2026-11-03T20:35:00Z"
ADOPT_NOW = "2026-11-03T22:00:00Z"
EXPIRY_DAY_NEXT_RUN = "2026-11-20T21:35:00Z"
CANDIDATE_RULES = "tests/fixtures/rules/candidate-1.1.0.toml"
CANDIDATE_SHA256 = "131490c88814cc3a5f17823933275c53d0a68956ce928b610aa904ccc6f355e1"
ALERT_FIXTURES = "tests/fixtures/alerts"
ALERT_PASSES: Tuple[str, ...] = ("pass_exit_signal", "pass_watch", "pass_unavailable", "pass_composed", "pass_pre_close")
ALERT_FAILURES: Tuple[Tuple[str, str], ...] = (
    ("fail_2x_credit", "number_not_emitted"), ("fail_21_dte", "number_not_emitted"),
    ("fail_pct_52_4", "number_not_emitted"), ("fail_you_should", "forbidden_language"),
    ("fail_missing_footer", "footer_missing"), ("fail_order_field", "order_field"),
    ("fail_imperative_start", "imperative_start"), ("fail_split_phrase", "forbidden_language"),
    ("fail_homoglyph", "non_ascii"),
)
"""The alert lint's fixtures (``tests/fixtures/alerts``): the passing messages, and each red-team message with the
code of its first finding."""


@dataclasses.dataclass(frozen=True)
class TextMatch:
    """A string expectation for one JSON key: the value is a string that starts with ``prefix`` and contains every
    string of ``contains`` (a JSON null or any other type never matches)."""

    prefix: str = ""
    contains: Tuple[str, ...] = ()

    def matches(self, value: Any) -> bool:
        return isinstance(value, str) and value.startswith(self.prefix) and all(part in value for part in self.contains)


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


def alert_lint_journey(fixture: str, expected_exit: int, expected_json: Dict[str, Any]) -> "Journey":
    """``alert_lint.py`` on one fixture message and its emitted-numbers file (relative to the repository root)."""
    if expected_exit == 0:
        name = "alert_lint %s -> ok" % fixture
    else:
        name = "alert_lint %s -> exit 1 (%s)" % (fixture, expected_json["payload.findings.0.code"])
    argv = ("alert_lint.py", "--message-file", "%s/%s.txt" % (ALERT_FIXTURES, fixture),
            "--emitted-file", "%s/%s.emitted.json" % (ALERT_FIXTURES, fixture))
    return tool_journey(name, argv, expected_exit, expected_json)


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
    # PR 2c: the rules engine on the main root's adopted example rulebook 1.0.0. exit_check never writes rule state;
    # its exit code is the group verdict table (the portfolio and underlying sections never set it).
    tool_journey("exit_check regular -> WMT and NVDA WATCH (DTE21), SPY HOLD", ("exit_check.py", "--fixture-set", "regular"), 0,
                 {"status": "ok", "tool": "exit_check", "payload.group_verdicts.AG-20261020-01": "WATCH",
                  "payload.group_verdicts.AG-20261022-02": "WATCH", "payload.group_verdicts.AG-20261028-03": "HOLD",
                  "payload.counts.WATCH": 2, "payload.counts.HOLD": 1, "payload.counts.UNAVAILABLE": 0,
                  "payload.verdicts.0.fired.0.rule_id": "DTE21", "payload.verdicts.0.fired.0.value": 13,
                  "payload.verdicts.1.fired.0.rule_id": "DTE21", "payload.verdicts.1.pending.0.rule_id": "DELTA_WATCH",
                  "payload.portfolio.fired.0.rule_id": "CONC", "payload.portfolio.fired.0.value": "26.32",
                  "payload.no_history.rule_ids": ["MOVE5_DOWN", "MOVE5_UP", "DAILY"], "payload.state.written": False}),
    tool_journey("exit_check expiry_day, next run after the close -> EXIT_SIGNAL (WMT TP80 95.32 %, NVDA DELTA_SIGNAL)",
                 ("exit_check.py", "--fixture-set", "expiry_day", "--next-run-utc", EXPIRY_DAY_NEXT_RUN), 10,
                 {"status": "fired", "payload.group_verdicts.AG-20261028-03": "WATCH",
                  "payload.verdicts.0.verdict": "EXIT_SIGNAL", "payload.verdicts.0.fired.0.rule_id": "TP80",
                  "payload.verdicts.0.fired.0.value": "95.32", "payload.verdicts.0.fired.0.threshold": "80",
                  "payload.verdicts.0.fired.0.unit": "percent", "payload.verdicts.0.fired.0.quote_grade": "E",
                  "payload.verdicts.1.verdict": "EXIT_SIGNAL", "payload.verdicts.1.fired.2.rule_id": "DELTA_SIGNAL",
                  "payload.verdicts.1.fired.2.value": "0.96", "payload.counts.EXIT_SIGNAL": 2, "payload.counts.WATCH": 1,
                  "payload.single_observation": True}, now=EXPIRY_DAY_NOW),
    tool_journey("exit_check expiry_day, no next run -> WATCH (TP80 pending: observation 1 of 2)",
                 ("exit_check.py", "--fixture-set", "expiry_day"), 0,
                 {"status": "ok", "payload.verdicts.0.verdict": "WATCH", "payload.verdicts.0.pending.0.rule_id": "TP80",
                  "payload.verdicts.0.pending.0.result": "pending", "payload.verdicts.0.pending.0.value": "95.32",
                  "payload.verdicts.0.pending.0.observations": 1, "payload.verdicts.0.pending.0.confirmations": 2,
                  "payload.counts.WATCH": 3, "payload.counts.EXIT_SIGNAL": 0, "payload.single_observation": False,
                  "payload.next_run_utc": None}, now=EXPIRY_DAY_NOW),
    tool_journey("exit_check stale -> UNAVAILABLE (all three groups)", ("exit_check.py", "--fixture-set", "stale"), 20,
                 {"status": "unavailable", "payload.counts.UNAVAILABLE": 3,
                  "payload.group_verdicts.AG-20261028-03": "UNAVAILABLE", "payload.verdicts.0.unavailable.0.rule_id": "TP80",
                  "payload.verdicts.0.fired.0.rule_id": "DTE21"}),
    tool_journey("exit_check --dry-run --rules candidate-1.1.0 regular twice (no rule state written)",
                 ("exit_check.py", "--dry-run", "--rules", CANDIDATE_RULES, "--fixture-set", "regular"), 0,
                 {"status": "ok", "payload.dry_run": True, "payload.rulebook.version": "1.1.0", "payload.rulebook.adopted": False,
                  "payload.state.status": "missing", "payload.state.written": False, "payload.counts.WATCH": 2,
                  "payload.counts.HOLD": 1},
                 steps=(tool_step(("exit_check.py", "--dry-run", "--rules", CANDIDATE_RULES, "--fixture-set", "regular"), 0,
                                  {"status": "ok", "payload.dry_run": True, "payload.state.written": False}),)),
    # book_check: one copy for the whole posting chain. The first --post records four signals (the WMT and NVDA
    # covered calls EXIT_SIGNAL, the SPY put WATCH, the portfolio WATCH); a minute later nothing changed and the
    # rules' cooldown holds the re-post: quiet, no journal write. The heartbeat and the open signals are read back.
    tool_journey("ack config, book_check --post expiry_day at 19:50 (4 signals) and 19:51 (quiet), heartbeat --list, ack list",
                 ("ack.py", "list"), 0,
                 {"status": "ok", "tool": "ack", "payload.count": 4, "payload.signals.0.signal_id": "SIG-20261120-001",
                  "payload.signals.1.signal_id": "SIG-20261120-002", "payload.signals.2.signal_id": "SIG-20261120-003",
                  "payload.signals.3.signal_id": "SIG-20261120-004", "payload.signals.0.verdict": "EXIT_SIGNAL",
                  "payload.signals.0.posts": 1, "payload.signals.3.posts": 1},
                 now="2026-11-20T19:52:00Z",
                 steps=(tool_step(("ack.py", "config", "--owner-tapped"), 0,
                                  {"status": "ok", "payload.action": "config", "payload.rulebook_version": "1.0.0"},
                                  now="2026-11-20T19:49:00Z"),
                        tool_step(("book_check.py", "--post", "--routine", "book-check", "--fixture-set", "expiry_day",
                                   "--next-run-utc", EXPIRY_DAY_NEXT_RUN), 10,
                                  {"status": "fired", "payload.posted": True, "payload.config_changed": [],
                                   "payload.lint.ok": True, "payload.signals.0.signal_id": "SIG-20261120-001",
                                   "payload.signals.0.verdict": "EXIT_SIGNAL", "payload.signals.1.verdict": "EXIT_SIGNAL",
                                   "payload.signals.2.verdict": "WATCH", "payload.signals.3.signal_id": "SIG-20261120-004",
                                   "payload.signals.3.trade_id": "portfolio", "payload.heartbeat": "state/heartbeat/book-check.json",
                                   "payload.written": ["journal/alerts.jsonl", "journal/2026-11-20.md", "journal/signals.jsonl",
                                                       "state/rule_state.json", "state/desk_state.json",
                                                       "state/heartbeat/book-check.json"],
                                   "payload.message": TextMatch(prefix="EXIT SIGNAL AG-20261020-01 | WMT covered call | "
                                                                       "your rule TP80")},
                                  now=EXPIRY_DAY_NOW),
                        tool_step(("book_check.py", "--post", "--routine", "book-check", "--fixture-set", "expiry_day",
                                   "--next-run-utc", EXPIRY_DAY_NEXT_RUN), 0,
                                  {"status": "quiet", "payload.quiet": True, "payload.message": None, "payload.signals": [],
                                   "payload.parts": [], "payload.open_signals": 4,
                                   "payload.written": ["state/rule_state.json", "state/desk_state.json",
                                                       "state/heartbeat/book-check.json"]},
                                  now="2026-11-20T19:51:00Z"),
                        tool_step(("heartbeat.py", "--list"), 0,
                                  {"status": "ok", "payload.count": 1, "payload.heartbeats.0.routine_id": "book-check",
                                   "payload.heartbeats.0.exit_code": 0, "payload.heartbeats.0.status": "quiet"},
                                  now="2026-11-20T19:52:00Z"))),
    tool_journey("book_check regular dry run without ack -> CONFIG CHANGED first", ("book_check.py", "--fixture-set", "regular"), 0,
                 {"status": "ok", "tool": "book_check", "payload.dry_run": True, "payload.posted": False, "payload.written": [],
                  "payload.parts.0.kind": "config_changed", "payload.config_changed.0.name": "desk_toml",
                  "payload.config_changed.0.old": None, "payload.config_changed.2.name": "rulebook", "payload.lint.ok": True,
                  "payload.message": TextMatch(prefix="CONFIG CHANGED | desk.toml: not acknowledged")}),
    tool_journey("ack config, book_check --summary regular at 20:35 -> UNAVAILABLE (quotes 63 min old), pre-close line",
                 ("book_check.py", "--summary", "--fixture-set", "regular"), 20,
                 {"status": "unavailable", "payload.dry_run": True, "payload.config_changed": [], "payload.counts.UNAVAILABLE": 3,
                  "payload.quote_age_min": 63, "payload.parts.3.kind": "pre_close", "payload.lint.ok": True,
                  "payload.message": TextMatch(contains=("pre-close | verdicts: 0 EXIT SIGNAL, 3 UNAVAILABLE, 0 WATCH, 0 HOLD",
                                                         "verify live quotes in your broker before acting"))},
                 now=PRE_CLOSE_NOW,
                 steps=(tool_step(("ack.py", "config", "--owner-tapped"), 0, {"status": "ok", "payload.action": "config"},
                                  now="2026-11-03T20:34:00Z"),)),
    tool_journey("ack config, book_check --summary regular at 19:50 -> pre-close line",
                 ("book_check.py", "--summary", "--fixture-set", "regular"), 0,
                 {"status": "ok", "payload.dry_run": True, "payload.config_changed": [], "payload.counts.WATCH": 2,
                  "payload.counts.HOLD": 1, "payload.parts.3.kind": "pre_close", "payload.lint.ok": True,
                  "payload.message": TextMatch(contains=("pre-close | verdicts: 0 EXIT SIGNAL, 0 UNAVAILABLE, 2 WATCH, 1 HOLD",
                                                         "verify live quotes in your broker before acting"))},
                 steps=(tool_step(("ack.py", "config", "--owner-tapped"), 0, {"status": "ok", "payload.action": "config"},
                                  now="2026-11-03T19:49:00Z"),)),
)
# The alert lint: every passing fixture exits 0, every red-team fixture exits 1 with its finding.
JOURNEYS += tuple(alert_lint_journey(fixture, 0, {"status": "ok", "tool": "alert_lint", "payload.ok": True, "payload.problems": []})
                  for fixture in ALERT_PASSES)
JOURNEYS += tuple(alert_lint_journey(fixture, 1, {"status": "error", "tool": "alert_lint", "payload.ok": False,
                                                  "payload.findings.0.code": code})
                  for fixture, code in ALERT_FAILURES)
JOURNEYS += (
    tool_journey("ack list on a fresh copy -> 0 open signals", ("ack.py", "list"), 0,
                 {"status": "ok", "tool": "ack", "payload.action": "list", "payload.count": 0, "payload.signals": []}),
    tool_journey("rules_diff candidate-1.1.0 regular -> 0 verdicts changed (TP50 changed)",
                 ("rules_diff.py", "--candidate", CANDIDATE_RULES, "--fixture-set", "regular"), 0,
                 {"status": "ok", "tool": "rules_diff", "payload.verdicts_changed": 0, "payload.rules.changed": ["TP50"],
                  "payload.changes": [], "payload.state": "scratch", "payload.candidate.version": "1.1.0"}),
    tool_journey("rules_diff candidate-1.1.0 expiry_day -> 0 verdicts changed (TP50 changed)",
                 ("rules_diff.py", "--candidate", CANDIDATE_RULES, "--fixture-set", "expiry_day"), 0,
                 {"status": "ok", "tool": "rules_diff", "payload.verdicts_changed": 0, "payload.rules.changed": ["TP50"],
                  "payload.changes": [], "payload.state": "scratch"}, now=EXPIRY_DAY_NOW),
    tool_journey("rules_adopt 1.1.0 after the close, then ack config reads rules/ADOPTED (1.1.0)",
                 ("ack.py", "config", "--owner-tapped"), 0,
                 {"status": "ok", "payload.rulebook_version": "1.1.0", "payload.acknowledged_hashes.rulebook": CANDIDATE_SHA256},
                 now="2026-11-03T22:01:00Z",
                 steps=(tool_step(("rules_adopt.py", "--file", CANDIDATE_RULES, "--version", "1.1.0", "--owner-phrase",
                                   "adopt rules 1.1.0"), 0,
                                  {"status": "ok", "tool": "rules_adopt", "payload.adopted": True, "payload.version": "1.1.0",
                                   "payload.file": "rules/exit_rules-1.1.0.toml", "payload.during_rth": False,
                                   "payload.refusals": [], "payload.digest.text": "no snapshot: digest unavailable"},
                                  now=ADOPT_NOW),)),
    tool_journey("rules_adopt 1.1.0 during regular hours without --allow-rth -> refused (regular_trading_hours)",
                 ("rules_adopt.py", "--file", CANDIDATE_RULES, "--version", "1.1.0", "--owner-phrase", "adopt rules 1.1.0"), 1,
                 {"status": "error", "tool": "rules_adopt", "payload.adopted": False, "payload.during_rth": True,
                  "payload.refusals.0.code": "regular_trading_hours", "payload.written": []}),
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
                    elif isinstance(expected, TextMatch):
                        if not expected.matches(value):
                            problems.append(f"JSON key '{key}' is {value!r:.400}, expected {expected!r}")
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
