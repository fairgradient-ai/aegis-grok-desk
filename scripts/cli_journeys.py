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

The PR 2d journeys run the threshold-level sheet (``trigger_levels``, its plan card, the crossed level once
``book_check --post`` has recorded the fire), the morning sheet and the evening brief (``eod_summary`` after the steps
that write the artifacts it reads, and on a bare copy), the doctor (quick and selftest), ``desk_init`` on its own fixture
root (``tests/fixtures/init``, which holds no ``desk.toml`` until the journey writes one on its copy) and on the main root
(refused), the opening bell, the routine audit, and ``routine.py`` once per Stage-1 routine of the manifest at its
slot, with ``--allow-dirty`` (a development checkout is never the release tag; the dirty-checkout refusal is a tool
test). A :class:`TextMatch` may also name parts that must be absent (the morning sheet carries no early-close line on an
ordinary day). The generators have no journey: ``check.sh`` steps 9 and 10 run their ``--check`` on the tree under test.

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

The PR 3b journeys run the Aegis Structuring Desk's three tools on the main root: ``roll_analysis`` (the WMT roll's best
candidate, the NVDA roll where none qualifies, the stale set), ``propose`` (the SPY protective put on a group that already
holds a put: NOTHING_TO_PROPOSE) and ``exit_ticket`` (two ROLL tickets, a MONETIZE_PUT ticket and a ROLL on expiration
day). The fixture root holds no ``structuring.toml``, so each chain writes it on its copy with ``desk_init.py --answers``
(the fixture answers) and acknowledges it with ``ack.py config --owner-tapped`` first; two journeys prove the tools fail
closed without it (criteria missing; a copy never acknowledged). The alert lint journeys cover the structuring fixtures
too: their emitted documents carry ``"footer": "structuring"``.

One journey runs the README's try-it exactly as published, ``opening_bell.py --demo --json``: the tool names this
checkout's own fixture desk and instant, so the harness adds no ``--desk-root``, ``--offline`` or ``--now``
(``checkout_only``); like a tool journey it runs only when the tree under test is this checkout.

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
# desk_init's own root: the owner's answers and the expected rendering, no desk.toml (the journey writes one on its copy).
FIXTURE_INIT = "tests/fixtures/init"
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
ALERT_PASSES: Tuple[str, ...] = ("pass_exit_signal", "pass_watch", "pass_unavailable", "pass_composed", "pass_pre_close",
                                 "pass_structuring_ticket", "pass_structuring_roll", "pass_structuring_proposal")
ALERT_FAILURES: Tuple[Tuple[str, str], ...] = (
    ("fail_2x_credit", "number_not_emitted"), ("fail_21_dte", "number_not_emitted"),
    ("fail_pct_52_4", "number_not_emitted"), ("fail_you_should", "forbidden_language"),
    ("fail_missing_footer", "footer_missing"), ("fail_order_field", "order_field"),
    ("fail_imperative_start", "imperative_start"), ("fail_split_phrase", "forbidden_language"),
    ("fail_homoglyph", "non_ascii"), ("fail_imperative_execute", "imperative_start"),
    ("fail_imperative_fill", "imperative_start"), ("fail_order_kind", "forbidden_language"),
    ("fail_structuring_under_rules_footer", "footer_missing"),
    ("fail_rules_text_under_structuring_footer", "imperative_start"),
    ("fail_structuring_leg_grammar", "order_field"), ("fail_structuring_go_with", "forbidden_language"),
    ("fail_structuring_at_price", "order_field"),
)
"""The alert lint's fixtures (``tests/fixtures/alerts``): the passing messages, and each red-team message with the
code of its first finding."""
# PR 2d: the threshold-level sheet and its plan card, the morning sheet and the evening brief, the doctor, the routine
# entry point, desk_init and the opening bell. The goldens below were computed by the tools on copies of the fixture roots.
TP80_RESTING = ("TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); resting-eligible: "
                "yes (below the current ask 3.50)")
TP50_CANNOT_FIRE = ("TP50 cannot fire on this position: time to expiry 13 trading days (your threshold: above 21 trading days), "
                    "and the days only fall")
TP80_CROSSED = ("TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); threshold already "
                "crossed (rule fired)")
PL_LINE = ("realized today $0.00, month to date $0.00, year to date $425.00 | unrealized $4,034.00 (liquidation), $4,097.00 "
           "(mid) | day $400.00")
RECON_CLEAN = "positions.csv reconciliation (data/positions/2026-11-03.recon.json): CLEAN, 0 proposed edits"
DOCKET_NONE_DUE = "expiry docket (reports/expiry/2026-11-03.week.json): 0 contracts due"
LEVELS_LAST_NIGHT = ("last night's levels (levels/2026-11-02.json): AG-20261020-01 1 resting-eligible of 3 price levels; "
                     "AG-20261022-02 1 resting-eligible of 5 price levels; AG-20261028-03 0 resting-eligible of 3 price levels")
DEV_HEADER = "development run: the checkout is not verified (--allow-dirty)"
# The morning staging: the doctor, the reconciliation and the week docket at 07:31 ET, the sheet at 07:35 ET; the routines
# at their slots (preopen 07:35 ET, eod 16:35 ET, eod-brief 17:10 ET, book-check 10:05 ET on Thanksgiving).
MORNING_STAGE_NOW = "2026-11-03T12:31:00Z"
MORNING_NOW = "2026-11-03T12:35:00Z"
EOD_NOW = "2026-11-03T21:35:00Z"
EOD_BRIEF_NOW = "2026-11-03T22:10:00Z"
HOLIDAY_SLOT_NOW = "2026-11-26T15:05:00Z"
# PR 3b: the Structuring Desk's criteria. tests/fixtures/desk holds no structuring.toml (designer C's G13); a structuring
# chain writes it on its copy from desk_init's fixture answers and acknowledges it before the tool runs.
STRUCTURING_ANSWERS = "tests/fixtures/init/structuring-answers.json"
STRUCTURING_SHA256 = "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c"
STRUCTURING_FOOTER = ("computed by the desk's tools from your criteria · posted by an AI assistant · a personalized "
                      "suggestion for positions you hold, not an order · you decide and act in your brokerage account · "
                      "data delayed 15 min, as of 19:31 UTC")
"""The structuring footer at the regular and expiry-day sets' quote stamp (19:31:12 UTC)."""


@dataclasses.dataclass(frozen=True)
class TextMatch:
    """A string expectation for one JSON key: the value is a string that starts with ``prefix``, contains every
    string of ``contains`` and none of ``excludes`` (a JSON null or any other type never matches)."""

    prefix: str = ""
    contains: Tuple[str, ...] = ()
    excludes: Tuple[str, ...] = ()

    def matches(self, value: Any) -> bool:
        return (isinstance(value, str) and value.startswith(self.prefix) and all(part in value for part in self.contains)
                and not any(part in value for part in self.excludes))


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
    checkout_only: bool = False


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


def demo_journey(name: str, argv: Sequence[str], expected_exit: int = 0,
                 expected_json: Optional[Dict[str, Any]] = None) -> Journey:
    """A command that names this checkout's own fixture desk and instant (``opening_bell.py --demo``, the README's
    try-it), run exactly as published: the harness adds no ``--desk-root``, ``--offline`` or ``--now``. It is evidence
    about this checkout only, so it runs only when the tree under test is this checkout."""
    return Journey(name, tuple(argv), expected_exit, expected_json, None, False, (), True)


DEMO_LINE = "Demo: synthetic sample data from the desk's test fixtures, not real market prices; nothing is written"
"""The first line of ``opening_bell.py --demo`` (its ``DEMO_LINE``)."""

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
    # A short option is closed only while the market trades: its act-by is the session close (16:00 ET), earlier than
    # the fixture's 16:30 ET broker cutoff (FAI-689).
    tool_journey("expiry_checklist --final-call expiry_day (short legs act by the 16:00 ET close)",
                 ("expiry_checklist.py", "--final-call", "--fixture-set", "expiry_day"), 10,
                 {"status": "fired", "payload.mode": "final-call", "payload.legs.0.pin.tier": "PIN HIGH",
                  "payload.legs.1.pin.tier": "PIN WATCH", "payload.legs.1.act_by_utc": "2026-11-20T21:00:00+00:00",
                  "payload.legs.1.act_by_basis": "session_close"},
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


# PR 2d. The level sheet (D1-16 1-3), the summaries (D1-16 4-6), the doctor, the routine entry point, desk_init and the
# opening bell (D2-18 1-9), the routine audit, and one development run per Stage-1 routine. Routine runs pass
# --allow-dirty: a development checkout is never the release tag, and the dirty-checkout refusal is a tool test.
JOURNEYS += (
    tool_journey("trigger_levels regular -> TP80 0.65 resting-eligible, TP50 cannot fire",
                 ("trigger_levels.py", "--fixture-set", "regular"), 0,
                 {"status": "ok", "tool": "trigger_levels", "payload.lint.ok": True, "payload.et_date": "2026-11-03",
                  "payload.written": ["levels/2026-11-03.md", "levels/2026-11-03.json"], "payload.plan": None,
                  "payload.text": TextMatch(prefix="THRESHOLD LEVELS 2026-11-03 | rules 1.0.0, sha cc09859c",
                                            contains=(TP80_RESTING, TP50_CANNOT_FIRE))}),
    tool_journey("book_check --post expiry_day, then trigger_levels expiry_day -> UNAVAILABLE (TP80 already crossed)",
                 ("trigger_levels.py", "--fixture-set", "expiry_day"), 20,
                 {"status": "unavailable", "payload.lint.ok": True,
                  "payload.written": ["levels/2026-11-20.md", "levels/2026-11-20.json"],
                  "payload.text": TextMatch(prefix="THRESHOLD LEVELS 2026-11-20", contains=(TP80_CROSSED,))},
                 now="2026-11-20T19:55:00Z",
                 steps=(tool_step(("book_check.py", "--post", "--next-run-utc", EXPIRY_DAY_NEXT_RUN, "--fixture-set",
                                   "expiry_day"), 10, {"status": "fired", "payload.posted": True}, now=EXPIRY_DAY_NOW),)),
    tool_journey("trigger_levels --plan AG-20261020-01 regular -> plans/AG-20261020-01.md",
                 ("trigger_levels.py", "--plan", "AG-20261020-01", "--fixture-set", "regular"), 0,
                 {"status": "ok", "payload.written": ["levels/2026-11-03.md", "levels/2026-11-03.json", "plans/AG-20261020-01.md"],
                  "payload.plan.group_id": "AG-20261020-01", "payload.plan.path": "plans/AG-20261020-01.md",
                  "payload.plan.lint.ok": True,
                  "payload.plan.text": TextMatch(prefix="PLAN CARD AG-20261020-01 | WMT covered call | rules 1.0.0, sha cc09859c",
                                                 contains=(TP80_RESTING, "dates that matter: last trading day 2026-11-20"))}),
    tool_journey("book --snapshot pre_open and regular, csv_recon regular, then eod_summary --brief -> P/L line",
                 ("eod_summary.py", "--brief"), 0,
                 {"status": "ok", "tool": "eod_summary", "payload.mode": "brief", "payload.lint.ok": True,
                  "payload.unavailable": [],
                  "payload.message": TextMatch(prefix="EOD DESK BRIEF 2026-11-03\nP/L (data/book/2026-11-03.json): " + PL_LINE)},
                 steps=(tool_step(("book.py", "--snapshot", "--fixture-set", "pre_open"), 0, {"status": "ok"}, now=PREV_EOD_NOW),
                        tool_step(("book.py", "--snapshot", "--fixture-set", "regular"), 0, {"status": "ok"}),
                        tool_step(("csv_recon.py", "--fixture-set", "regular"), 0, {"payload.verdict": "CLEAN"}))),
    tool_journey("book --snapshot pre_open, trigger_levels --from-snapshot, desk_doctor --quick, csv_recon and expiry_checklist "
                 "--week pre_open, then eod_summary -> MORNING DESK SHEET (no early close on 2026-11-03)",
                 ("eod_summary.py",), 0,
                 {"status": "ok", "payload.mode": "morning", "payload.lint.ok": True, "payload.unavailable": [],
                  "payload.exit_code": 0,
                  "payload.message": TextMatch(
                      prefix="MORNING DESK SHEET 2026-11-03\ndoctor (state/doctor.json, quick check at 12:31 UTC): PASS",
                      contains=(RECON_CLEAN, "open signals: 0", DOCKET_NONE_DUE, LEVELS_LAST_NIGHT),
                      excludes=("early close", "UNAVAILABLE:"))},
                 now=MORNING_NOW,
                 steps=(tool_step(("book.py", "--snapshot", "--fixture-set", "pre_open"), 0, {"status": "ok"}, now=PREV_EOD_NOW),
                        tool_step(("trigger_levels.py", "--from-snapshot"), 0,
                                  {"status": "ok", "payload.written": ["levels/2026-11-02.md", "levels/2026-11-02.json"]},
                                  now=PREV_EOD_NOW),
                        tool_step(("desk_doctor.py", "--quick"), 0,
                                  {"payload.verdict": "PASS", "payload.record": "state/doctor.json"}, now=MORNING_STAGE_NOW),
                        tool_step(("csv_recon.py", "--fixture-set", "pre_open"), 0, {"payload.verdict": "CLEAN"},
                                  now=MORNING_STAGE_NOW),
                        tool_step(("expiry_checklist.py", "--week", "--fixture-set", "pre_open"), 0,
                                  {"payload.mode": "week", "payload.counts.listed": 0}, now=MORNING_STAGE_NOW))),
    tool_journey("eod_summary on a bare copy -> UNAVAILABLE (reconciliation missing)", ("eod_summary.py",), 20,
                 {"status": "unavailable", "payload.mode": "morning", "payload.lint.ok": True, "payload.exit_code": 20,
                  "payload.message": TextMatch(prefix="MORNING DESK SHEET 2026-11-03", contains=(
                      "UNAVAILABLE: reconciliation data/positions/2026-11-03.recon.json missing",))}),
    # market_status (check 9) is not pinned: it depends on the runner's timezone and only ever WARNs.
    tool_journey("desk_doctor --quick regular -> PASS", ("desk_doctor.py", "--quick"), 0,
                 {"status": "ok", "tool": "desk_doctor", "payload.verdict": "PASS", "payload.mode": "quick",
                  "payload.checks.0.id": "interpreter", "payload.checks.2.id": "token_source",
                  "payload.checks.2.status": "WARN", "payload.checks.3.id": "desk_files",
                  "payload.checks.3.status": "PASS"}),
    tool_journey("desk_doctor --selftest -> PASS", ("desk_doctor.py", "--selftest"), 0,
                 {"status": "ok", "payload.verdict": "PASS", "payload.checks.1.id": "selftest",
                  "payload.checks.1.status": "PASS"}),
    tool_journey("ack config, routine book-check 15:35 expiry_day -> EXIT SIGNAL",
                 ("routine.py", "book-check", "--slot", "15:35", "--allow-dirty", "--fixture-set", "expiry_day"), 10,
                 {"status": "fired", "tool": "routine", "payload.exit_code": 10, "payload.development": True,
                  "payload.steps.0.tool": "book_check", "payload.steps.0.exit_code": 10, "payload.lint.ok": True,
                  "payload.heartbeat": "state/heartbeat/book-check.json",
                  "payload.message": TextMatch(prefix=DEV_HEADER, contains=(
                      "EXIT SIGNAL AG-20261020-01 | WMT covered call | your rule TP80",
                      "pre-close | verdicts: 2 EXIT SIGNAL, 0 UNAVAILABLE, 1 WATCH, 0 HOLD | open signals: 4"))},
                 now=EXPIRY_DAY_NOW,
                 steps=(tool_step(("ack.py", "config", "--owner-tapped"), 0, {"status": "ok"}, now="2026-11-20T19:49:00Z"),)),
    tool_journey("routine book-check 14:05 regular -> CONFIG CHANGED, WATCH",
                 ("routine.py", "book-check", "--slot", "14:05", "--allow-dirty", "--fixture-set", "regular"), 0,
                 {"status": "ok", "payload.steps.0.exit_code": 0, "payload.lint.ok": True, "payload.lateness_min": 45,
                  "payload.message": TextMatch(prefix="development run", contains=(
                      "CONFIG CHANGED | desk.toml: not acknowledged -> sha 0af433cf",
                      "WATCH AG-20261020-01 | WMT covered call | your rule DTE21", "run 45 min late"))}),
    tool_journey("routine final-call 15:20 expiry_day -> FINAL CALL",
                 ("routine.py", "final-call", "--slot", "15:20", "--allow-dirty", "--fixture-set", "expiry_day"), 10,
                 {"status": "fired", "payload.steps.0.exit_code": 10, "payload.steps.1.exit_code": 0,
                  "payload.steps.2.exit_code": 10, "payload.lint.ok": True, "payload.heartbeat": "state/heartbeat/final-call.json",
                  "payload.message": TextMatch(prefix="development run", contains=(
                      "FINAL CALL Fri 2026-11-20: 2 contracts on their last trading day; act by 16:00 ET (21:00 UTC)",
                      "assignment watch Fri 2026-11-20 (session open): FIRED"))},
                 now=EXPIRY_DAY_NOW),
    tool_journey("routine nope -> usage", ("routine.py", "nope"), 2, {"status": "usage", "error.code": "invalid_arguments"}),
    tool_journey("desk_init answers -> desk.toml", ("desk_init.py", "--answers", "tests/fixtures/init/answers.json"), 0,
                 {"status": "ok", "tool": "desk_init", "payload.written": ["desk.toml"],
                  "payload.desk_toml.sha256": "f011fe058582203498b9082ec247cbfd388ac5b241d486ee26584c333eb91a94"},
                 desk_root=FIXTURE_INIT),
    tool_journey("desk_init on the main root -> desk_toml_exists",
                 ("desk_init.py", "--answers", "tests/fixtures/init/answers.json"), 1,
                 {"status": "error", "error.code": "desk_toml_exists"}),
    tool_journey("opening_bell SPY regular", ("opening_bell.py", "--symbol", "SPY"), 0,
                 {"status": "ok", "tool": "opening_bell", "payload.quote.mid": "648.12", "payload.expiration": "2026-12-18",
                  "payload.dte_calendar": 45, "payload.strike": "640", "payload.call.mid": "28.65", "payload.put.mid": "17.30",
                  "payload.expected_move.straddle": "36.76", "payload.expected_move.one_sigma": "56.98",
                  "payload.requests_made": 3}),
    demo_journey("opening_bell --demo -> synthetic sample snapshot (no desk root, no instant)",
                 ("opening_bell.py", "--demo", "--json"), 0,
                 {"status": "ok", "tool": "opening_bell", "payload.demo": True, "payload.fixture_set": "regular",
                  "payload.symbol": "SPY", "payload.quote.mid": "648.12", "payload.expiration": "2026-12-18",
                  "payload.expected_move.straddle": "36.76", "payload.requests_made": 3, "payload.lint.ok": True,
                  "payload.message": TextMatch(prefix=DEMO_LINE + "\nSPY 648.12 mid (bid 648.10, ask 648.14), as of 19:31 UTC",
                                               contains=("not a signal",))}),
    tool_journey("routine_audit --budget -> lean 41, active 61 runs per week", ("routine_audit.py", "--budget"), 0,
                 {"status": "ok", "tool": "routine_audit", "payload.mode": "budget",
                  "payload.budget": {"lean": 41, "active": 61}}),
    tool_journey("routine_audit --rules-text -> three Auto-review texts within 1000 characters",
                 ("routine_audit.py", "--rules-text"), 0,
                 {"status": "ok", "payload.mode": "rules_text", "payload.limit_chars": 1000,
                  "payload.lengths": {"allow": 506, "ask_first_writes": 315, "ask_first_owner_authority": 333},
                  "payload.routine_ids": ["preopen", "book-check", "final-call", "eod", "eod-brief", "post-expiry"]}),
    tool_journey("book --snapshot pre_open, trigger_levels --from-snapshot, then routine preopen 07:30 pre_open -> "
                 "MORNING DESK SHEET",
                 ("routine.py", "preopen", "--slot", "07:30", "--allow-dirty", "--fixture-set", "pre_open"), 0,
                 {"status": "ok", "tool": "routine", "payload.exit_code": 0, "payload.lint.ok": True, "payload.slot": "07:30",
                  "payload.heartbeat": "state/heartbeat/preopen.json", "payload.steps.0.tool": "desk_doctor",
                  "payload.steps.3.tool": "eod_summary", "payload.steps.3.exit_code": 0, "payload.incidents": [],
                  "payload.withheld": [],
                  "payload.message": TextMatch(prefix=DEV_HEADER + "\nMORNING DESK SHEET 2026-11-03",
                                               contains=(RECON_CLEAN, DOCKET_NONE_DUE, LEVELS_LAST_NIGHT),
                                               excludes=("UNAVAILABLE:", "INCIDENT", "WITHHELD"))},
                 now=MORNING_NOW,
                 steps=(tool_step(("book.py", "--snapshot", "--fixture-set", "pre_open"), 0, {"status": "ok"}, now=PREV_EOD_NOW),
                        tool_step(("trigger_levels.py", "--from-snapshot"), 0, {"status": "ok"}, now=PREV_EOD_NOW))),
    tool_journey("book --snapshot pre_open, routine eod 16:35 after_close, then routine eod-brief 17:10 -> EOD DESK BRIEF",
                 ("routine.py", "eod-brief", "--slot", "17:10", "--allow-dirty"), 0,
                 {"status": "ok", "payload.exit_code": 0, "payload.lint.ok": True, "payload.slot": "17:10",
                  "payload.heartbeat": "state/heartbeat/eod-brief.json", "payload.steps.0.tool": "eod_summary",
                  "payload.message": TextMatch(
                      prefix=DEV_HEADER + "\nEOD DESK BRIEF 2026-11-03\nP/L (data/book/2026-11-03.json): " + PL_LINE,
                      contains=("routines: 1 of 7 ran today",), excludes=("UNAVAILABLE:", "INCIDENT", "WITHHELD"))},
                 now=EOD_BRIEF_NOW,
                 steps=(tool_step(("book.py", "--snapshot", "--fixture-set", "pre_open"), 0, {"status": "ok"}, now=PREV_EOD_NOW),
                        tool_step(("routine.py", "eod", "--slot", "16:35", "--allow-dirty", "--fixture-set", "after_close"), 0,
                                  {"status": "ok", "payload.heartbeat": "state/heartbeat/eod.json",
                                   "payload.steps.1.tool": "trigger_levels", "payload.lint.ok": True,
                                   "payload.message": TextMatch(prefix=DEV_HEADER + "\nTHRESHOLD LEVELS 2026-11-03")},
                                  now=EOD_NOW))),
    tool_journey("routine post-expiry 09:30 post_expiry Saturday -> EDITS_PENDING",
                 ("routine.py", "post-expiry", "--slot", "09:30", "--allow-dirty", "--fixture-set", "post_expiry"), 10,
                 {"status": "fired", "payload.exit_code": 10, "payload.steps.0.tool": "csv_recon",
                  "payload.steps.0.exit_code": 10, "payload.heartbeat": "state/heartbeat/post-expiry.json",
                  "payload.lint.ok": True,
                  "payload.message": TextMatch(prefix=DEV_HEADER + "\nEDITS_PENDING (2026-11-21 ET): 2 expired legs, 3 proposed "
                                                                   "edits")},
                 now=POST_EXPIRY_NOW),
    tool_journey("routine book-check 10:05 on Thanksgiving -> quiet (closed day)",
                 ("routine.py", "book-check", "--slot", "10:05", "--allow-dirty", "--fixture-set", "holiday"), 0,
                 {"status": "quiet", "payload.closed_day": True, "payload.quiet": True, "payload.steps": [],
                  "payload.message": None, "payload.heartbeat": "state/heartbeat/book-check.json"},
                 now=HOLIDAY_SLOT_NOW),
)


def structuring_steps(init_now: str, ack_now: str) -> Tuple[Step, ...]:
    """``desk_init.py`` writes structuring.toml on the copy from the fixture answers (the copy's desk.toml stays), then
    ``ack.py config --owner-tapped`` acknowledges it with desk.toml, positions.csv and the rulebook."""
    return (tool_step(("desk_init.py", "--answers", STRUCTURING_ANSWERS), 0,
                      {"status": "ok", "payload.written": ["structuring.toml"],
                       "payload.structuring_toml.sha256": STRUCTURING_SHA256}, now=init_now),
            tool_step(("ack.py", "config", "--owner-tapped"), 0,
                      {"status": "ok", "payload.action": "config",
                       "payload.acknowledged_hashes.structuring_toml": STRUCTURING_SHA256}, now=ack_now))


# PR 3b: the Aegis Structuring Desk's three tools, only on the owner's request (never a routine step). The chains write and
# acknowledge structuring.toml two and one minute before the tool; two fail-closed journeys run without it.
REGULAR_STRUCTURING = structuring_steps("2026-11-03T19:48:00Z", "2026-11-03T19:49:00Z")
EXPIRY_DAY_STRUCTURING = structuring_steps("2026-11-20T19:48:00Z", "2026-11-20T19:49:00Z")
WMT_ROLL_115 = ("exit_ticket.py", "--trade", "AG-20261020-01", "--action", "ROLL", "--new-expiration", "2027-01-15",
                "--new-strike", "115")
JOURNEYS += (
    tool_journey("desk_init structuring, ack config, roll_analysis WMT regular -> BEST (the Jan 115 call)",
                 ("roll_analysis.py", "--trade", "AG-20261020-01"), 0,
                 {"status": "ok", "tool": "roll_analysis", "payload.verdict": "BEST", "payload.best": "WMT270115C00115000",
                  "payload.candidates.0.score": "0.0467", "payload.candidates.0.net_credit": "0.05",
                  "payload.candidates.0.net_credit_total": "10.00", "payload.passed": 1, "payload.failed": 19,
                  "payload.after.max_loss": "19006.00", "payload.after.max_profit": "3994.00",
                  "payload.after.breakeven": "95.03", "payload.requests.made": 4, "payload.requests.chains": 2,
                  "payload.lint.ok": True, "payload.emitted.footer": "structuring",
                  "payload.files.json": "reports/rolls/AG-20261020-01-20261103T195000Z.json",
                  "payload.journal": "journal/structuring.jsonl",
                  "payload.message": TextMatch(
                      prefix="STRUCTURING | AG-20261020-01 roll analysis | WMT covered call | criteria structuring.toml "
                             "(sha 5155427c) | Tradier sandbox, 15-min delayed, as of 19:31 UTC",
                      contains=("BEST CANDIDATE 1 of 20 by your objective extrinsic_per_day",
                                "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | ask 3.50 | quote grade B, 18 min old",
                                "leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55 | quote grade B, 18 min old",
                                "accepted ex_dividend: the ex-dividend date 2026-11-24 (cum 2026-11-23) falls inside the new "
                                "call's life (by #assign-ok)",
                                "after both legs: group delta 134.07 (now 101.71), max loss 19006.00 (now 19016.00), max profit "
                                "3994.00 (now 1984.00), breakeven 95.03 (now 95.08)",
                                STRUCTURING_FOOTER))},
                 steps=REGULAR_STRUCTURING),
    tool_journey("desk_init structuring, ack config, roll_analysis NVDA regular -> NONE_QUALIFIES",
                 ("roll_analysis.py", "--trade", "AG-20261022-02"), 0,
                 {"status": "ok", "payload.verdict": "NONE_QUALIFIES", "payload.best": None, "payload.passed": 0,
                  "payload.failed": 18, "payload.unpriced": 2, "payload.candidates.0.id": "NVDA270115C00160000",
                  "payload.candidates.0.status": "FAILS_CRITERION", "payload.candidates.0.score": "0.0299",
                  "payload.requests.made": 4, "payload.lint.ok": True,
                  "payload.message": TextMatch(
                      prefix="STRUCTURING | AG-20261022-02 roll analysis | NVDA covered call",
                      contains=("no candidate passed every criterion: the desk names no best candidate",
                                "FAILS_CRITERION credit_only: a net debit of 0.05",
                                "FAILS_CRITERION ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls "
                                "inside the new call's life", STRUCTURING_FOOTER),
                      excludes=("BEST CANDIDATE",))},
                 steps=REGULAR_STRUCTURING),
    tool_journey("desk_init structuring, ack config, roll_analysis WMT stale -> UNAVAILABLE after 1 request",
                 ("roll_analysis.py", "--trade", "AG-20261020-01", "--fixture-set", "stale"), 20,
                 {"status": "unavailable", "payload.verdict": "UNAVAILABLE", "payload.cause": "spot_unusable",
                  "payload.requests.made": 1, "payload.files": None, "payload.journal": "journal/structuring.jsonl",
                  "payload.lint.ok": True,
                  "payload.message": TextMatch(
                      prefix="UNAVAILABLE | AG-20261020-01 roll analysis | no usable quote of the underlying",
                      contains=("data delayed 15 min, as of 19:50 UTC",))},
                 steps=REGULAR_STRUCTURING),
    tool_journey("desk_init structuring, ack config, propose SPY protective_put on the main root -> NOTHING_TO_PROPOSE",
                 ("propose.py", "--trade", "AG-20261028-03", "--structure", "protective_put"), 0,
                 {"status": "ok", "tool": "propose", "payload.verdict": "NOTHING_TO_PROPOSE",
                  "payload.cause": "not_shares_only", "payload.requests.made": 0, "payload.lint.ok": True,
                  "payload.best": None, "payload.candidates": [],
                  "payload.message": TextMatch(
                      prefix="NOTHING_TO_PROPOSE | AG-20261028-03 protective put proposal | the 100 SPY shares already "
                             "carry 1 long put")},
                 steps=REGULAR_STRUCTURING),
    tool_journey("desk_init structuring, ack config, exit_ticket WMT ROLL, new 2027-01-15 115 call -> TICKET, net +10.00, "
                 "peak 700.00", WMT_ROLL_115, 0,
                 {"status": "ok", "tool": "exit_ticket", "payload.verdict": "TICKET",
                  "payload.ticket_id": "TK-AG-20261020-01-ROLL-20261103T195000Z",
                  "payload.cash.net_conservative": "10.00", "payload.cash.net_mid": "35.00",
                  "payload.cash.peak_needed": "700.00", "payload.replacement.candidate.status": "ELIGIBLE",
                  "payload.approval.required_level": 1, "payload.requests.made": 2, "payload.lint.ok": True,
                  "payload.emitted.footer": "structuring", "payload.files.written": True,
                  "payload.journal": "journal/structuring.jsonl",
                  "payload.message": TextMatch(
                      prefix="TICKET AG-20261020-01 | WMT covered call | a roll of the short call (action ROLL) | account 1 "
                             "of desk.toml (margin)",
                      contains=("leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425",
                                "leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60",
                                "cash: +10.00 at the conservative prices (mid +35.00); the most the steps need at once: 700.00",
                                "after both legs: group delta 134.07, max loss 19006.00, max profit 3994.00, breakeven 95.03",
                                STRUCTURING_FOOTER))},
                 steps=REGULAR_STRUCTURING),
    tool_journey("desk_init structuring, ack config, exit_ticket NVDA ROLL, new 2027-01-15 160 call -> TICKET with two "
                 "FAILS_CRITERION lines",
                 ("exit_ticket.py", "--trade", "AG-20261022-02", "--action", "ROLL", "--new-expiration", "2027-01-15",
                  "--new-strike", "160"), 0,
                 {"status": "ok", "payload.verdict": "TICKET", "payload.ticket_id": "TK-AG-20261022-02-ROLL-20261103T195000Z",
                  "payload.cash.net_conservative": "-10.00", "payload.cash.peak_needed": "660.00",
                  "payload.replacement.candidate.status": "FAILS_CRITERION", "payload.requests.made": 2,
                  "payload.lint.ok": True, "payload.files.written": True,
                  "payload.message": TextMatch(prefix="TICKET AG-20261022-02 | NVDA covered call", contains=(
                      "FAILS_CRITERION credit_only: a net debit of 0.05",
                      "FAILS_CRITERION ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls inside the new "
                      "call's life",
                      "after both legs: group delta 137.35, max loss 27640.00, max profit 4360.00, breakeven 138.20",
                      STRUCTURING_FOOTER))},
                 steps=REGULAR_STRUCTURING),
    tool_journey("desk_init structuring, ack config, exit_ticket SPY MONETIZE_PUT -> TICKET, cash +980.00",
                 ("exit_ticket.py", "--trade", "AG-20261028-03", "--action", "MONETIZE_PUT"), 0,
                 {"status": "ok", "payload.verdict": "TICKET",
                  "payload.ticket_id": "TK-AG-20261028-03-MONETIZE_PUT-20261103T195000Z",
                  "payload.cash.net_conservative": "980.00", "payload.cash.net_mid": "995.00",
                  "payload.cash.peak_needed": "0.00", "payload.realized.0.conservative": "-150.00",
                  "payload.puts.0.itm": False, "payload.requests.made": 1, "payload.lint.ok": True,
                  "payload.files.written": True,
                  "payload.message": TextMatch(
                      prefix="TICKET AG-20261028-03 | SPY protective put | the long put closed for its value (action "
                             "MONETIZE_PUT) | account 2 of desk.toml (ira)",
                      contains=("leg 1 of 1: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95",
                                "cash: +980.00 at the conservative prices (mid +995.00); the most the steps need at once: 0.00",
                                "after the leg, your 100 SPY shares carry no put: their downside is unhedged",
                                STRUCTURING_FOOTER))},
                 steps=REGULAR_STRUCTURING),
    tool_journey("desk_init structuring, ack config, exit_ticket WMT ROLL on expiration day -> TICKET, the held call's ask "
                 "at grade E", WMT_ROLL_115 + ("--fixture-set", "expiry_day"), 0,
                 {"status": "ok", "payload.verdict": "TICKET", "payload.ticket_id": "TK-AG-20261020-01-ROLL-20261120T195000Z",
                  "payload.legs.0.grade": "E", "payload.legs.0.conservative": "0.16",
                  "payload.cash.net_conservative": "558.00", "payload.cash.peak_needed": "32.00",
                  "payload.replacement.candidate.status": "FAILS_CRITERION", "payload.requests.made": 2,
                  "payload.lint.ok": True,
                  "payload.message": TextMatch(prefix="TICKET AG-20261020-01 | WMT covered call", contains=(
                      "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 0.12, ask 0.16, mid 0.14 | quote "
                      "grade E, Tradier sandbox, 15-min delayed, as of 19:31 UTC | quote grade E: the conservative side "
                      "stands",
                      "FAILS_CRITERION earnings: the listed earnings date 2026-11-17 has passed, so the next one is unknown "
                      "and counts as inside the new call's life",
                      STRUCTURING_FOOTER))},
                 now=EXPIRY_DAY_NOW, steps=EXPIRY_DAY_STRUCTURING),
    # Failing closed (addendum S8): every action, CLOSE included, needs an acknowledged structuring.toml.
    tool_journey("ack config, exit_ticket WMT CLOSE without structuring.toml -> UNAVAILABLE (criteria_missing)",
                 ("exit_ticket.py", "--trade", "AG-20261020-01", "--action", "CLOSE"), 20,
                 {"status": "unavailable", "payload.verdict": "UNAVAILABLE", "payload.cause": "criteria_missing",
                  "payload.files": None, "payload.journal": "journal/structuring.jsonl", "payload.lint.ok": True,
                  "payload.message": TextMatch(
                      prefix="UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your "
                             "criteria (SETUP step 9)")},
                 steps=(tool_step(("ack.py", "config", "--owner-tapped"), 0, {"status": "ok", "payload.action": "config"},
                                  now="2026-11-03T19:49:00Z"),)),
    tool_journey("roll_analysis on a copy never acknowledged -> UNAVAILABLE (config_unacknowledged)",
                 ("roll_analysis.py", "--trade", "AG-20261020-01"), 20,
                 {"status": "unavailable", "payload.verdict": "UNAVAILABLE", "payload.cause": "config_unacknowledged",
                  "payload.files": None, "payload.lint.ok": True,
                  "payload.message": TextMatch(prefix="UNAVAILABLE | CONFIG CHANGED: desk.toml: not acknowledged -> sha "
                                                      "0af433cf", contains=("acknowledge with ack.py config --owner-tapped",))}),
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
    """``(to run, not run)``: tool and demo journeys are skipped against a tree that is not this checkout."""
    if same_tree(root):
        return list(JOURNEYS), []
    run_now = [journey for journey in JOURNEYS if journey.desk_root is None and not journey.checkout_only]
    skipped = [journey for journey in JOURNEYS if journey.desk_root is not None or journey.checkout_only]
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
