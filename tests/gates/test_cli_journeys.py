"""``scripts/cli_journeys.py``, the journey harness: the catalogue of published commands and the
multi-step journeys that run several commands on ONE copy of a fixture desk root (the snapshot chain).

The gate runs the whole catalogue (``bash scripts/check.sh``, step 8); these tests pin the harness's
own contract cheaply: every journey names an existing script and an exit code of the contract's table,
tool journeys run ``--json --offline --now <instant>`` against a temporary copy of a fixture root, the
three gate journeys stay exactly three (``tests/gates/test_check_sh.py`` expects ``ok: 3 command
journeys passed`` on a fixture tree), the PR 2b journeys of the addendum are listed with their
expectations, and ``steps`` run in order on one copy (proved with two ``heartbeat.py`` commands, which
cost nothing: the second sees the first's record), stop at the first failing step and leave no copy
behind. The PR 2c journeys (the rules engine: ``exit_check``, ``book_check``, ``ack``, ``alert_lint``,
``rules_diff``, ``rules_adopt``) are pinned with the values the tools computed on the fixture root, their
chains (``ack.py config --owner-tapped`` first, ``book_check.py --post`` twice, an adoption read back) are
checked step by step, every red-team and passing alert fixture has its own journey, ``FORBIDDEN_OUTPUT``
holds every forbidden phrase of the alert lint's table (a command whose output carries one fails, checked
with the table's own examples so no phrase is spelled here), and a :class:`TextMatch` expectation checks a
string's start and parts (``payload.message`` starts with ``CONFIG CHANGED``; the pre-close line is in it). Each ``StepsTest`` test points ``tempfile.tempdir`` at a private root of its own, so the harness's
copies land there and a suite or ``cli_journeys.py`` run sharing the machine's temp directory cannot
change the listings it compares. No order-shaped word appears in a journey's name or command line. The PR 2d journeys
(the level sheet and its plan card, the morning sheet and the evening brief, the doctor, desk_init, the opening bell, the
routine audit and one development run of every Stage-1 routine of the manifest) are pinned with their commands, instants,
setup chains and the values the tools computed; every desk tool has at least one journey and the two generators have none
(``check.sh`` steps 9 and 10 run them), and a :class:`TextMatch` can also require parts to be absent. The PR 3b journeys
(the Structuring Desk's ``roll_analysis``, ``propose`` and ``exit_ticket``) are pinned with their commands, instants and
values; each structuring chain writes and acknowledges ``structuring.toml`` on its copy first (``desk_init.py --answers``,
then ``ack.py config --owner-tapped``), two fail-closed journeys run without it, and no routine journey runs them.
The README's try-it, ``opening_bell.py --demo --json``, is the one ``checkout_only`` journey: it runs as published (no
``--desk-root``, ``--offline`` or ``--now`` added, since the tool names its own fixture desk and instant) and, like a tool
journey, only against this checkout.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

from aegis_desk import alert_lint, contract

import cli_journeys as harness

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORDER_SHAPED = re.compile(r"\b(?:buy|sell|sold|bought|place|submit|limit order|market order|good till|time in force)\b",
                          re.IGNORECASE)
GATE_JOURNEYS = ("check_manifests --json", "check_no_write_paths --json", "check_grok_template --json")
HEARTBEAT_WRITE = ("heartbeat.py", "--routine", "book-check", "--exit-code", "0", "--status", "ok", "--scheduled-et", "10:05")
HEARTBEAT_LIST = ("heartbeat.py", "--list")
CANDIDATE = "tests/fixtures/rules/candidate-1.1.0.toml"
CANDIDATE_SHA256 = "131490c88814cc3a5f17823933275c53d0a68956ce928b610aa904ccc6f355e1"
ALERT_FIXTURES = "tests/fixtures/alerts"
FAIL_FIXTURE_CODES = {
    "fail_2x_credit": "number_not_emitted", "fail_21_dte": "number_not_emitted", "fail_pct_52_4": "number_not_emitted",
    "fail_you_should": "forbidden_language", "fail_missing_footer": "footer_missing", "fail_order_field": "order_field",
    "fail_imperative_start": "imperative_start", "fail_split_phrase": "forbidden_language", "fail_homoglyph": "non_ascii",
    "fail_imperative_execute": "imperative_start", "fail_imperative_fill": "imperative_start",
    "fail_order_kind": "forbidden_language",
    # PR 3b: the structuring footer and its cross-refusals, the leg grammar, the SL table.
    # "your plan: close" is no exception under the structuring footer (PR 3b review L2-7): its first finding
    "fail_structuring_under_rules_footer": "non_ascii", "fail_rules_text_under_structuring_footer": "imperative_start",
    "fail_structuring_leg_grammar": "order_field", "fail_structuring_go_with": "forbidden_language",
    "fail_structuring_at_price": "order_field",
}
PASS_FIXTURES = ("pass_exit_signal", "pass_watch", "pass_unavailable", "pass_composed", "pass_pre_close",
                 "pass_structuring_ticket", "pass_structuring_roll", "pass_structuring_proposal")
J6 = "ack config, book_check --post expiry_day at 19:50 (4 signals) and 19:51 (quiet), heartbeat --list, ack list"
J8_LATE = "ack config, book_check --summary regular at 20:35 -> UNAVAILABLE (quotes 63 min old), pre-close line"
J8 = "ack config, book_check --summary regular at 19:50 -> pre-close line"
J12 = "rules_adopt 1.1.0 after the close, then ack config reads rules/ADOPTED (1.1.0)"
PRE_CLOSE_LINE = "pre-close | verdicts: 0 EXIT SIGNAL, 0 UNAVAILABLE, 2 WATCH, 1 HOLD"
PRE_CLOSE_LINE_LATE = "pre-close | verdicts: 0 EXIT SIGNAL, 3 UNAVAILABLE, 0 WATCH, 0 HOLD"
VERIFY_LIVE = "verify live quotes in your broker before acting"
# The PR 2c journeys (addendum, journeys 1-12): the script of the journey's own command, its exit code and keys of
# its expected JSON. Every value was computed by the tools on a copy of the main fixture root; where it differs from
# the addendum's hand-written list (journeys 6 and 8) the computed value stands.
PR_2C: Dict[str, Tuple[str, int, Dict[str, Any]]] = {
    "exit_check regular -> WMT and NVDA WATCH (DTE21), SPY HOLD": (
        "exit_check.py", 0, {"payload.group_verdicts.AG-20261020-01": "WATCH", "payload.group_verdicts.AG-20261022-02": "WATCH",
                             "payload.group_verdicts.AG-20261028-03": "HOLD", "payload.verdicts.0.fired.0.rule_id": "DTE21",
                             "payload.verdicts.0.fired.0.value": 13, "payload.verdicts.1.pending.0.rule_id": "DELTA_WATCH",
                             "payload.portfolio.fired.0.rule_id": "CONC", "payload.portfolio.fired.0.value": "26.32",
                             "payload.no_history.rule_ids": ["MOVE5_DOWN", "MOVE5_UP", "DAILY"]}),
    "exit_check expiry_day, next run after the close -> EXIT_SIGNAL (WMT TP80 95.32 %, NVDA DELTA_SIGNAL)": (
        "exit_check.py", 10, {"status": "fired", "payload.verdicts.0.verdict": "EXIT_SIGNAL",
                              "payload.verdicts.0.fired.0.rule_id": "TP80", "payload.verdicts.0.fired.0.value": "95.32",
                              "payload.verdicts.0.fired.0.threshold": "80", "payload.verdicts.0.fired.0.quote_grade": "E",
                              "payload.verdicts.1.verdict": "EXIT_SIGNAL", "payload.verdicts.1.fired.2.rule_id": "DELTA_SIGNAL",
                              "payload.counts.EXIT_SIGNAL": 2, "payload.single_observation": True}),
    "exit_check expiry_day, no next run -> WATCH (TP80 pending: observation 1 of 2)": (
        "exit_check.py", 0, {"payload.verdicts.0.verdict": "WATCH", "payload.verdicts.0.pending.0.rule_id": "TP80",
                             "payload.verdicts.0.pending.0.observations": 1, "payload.verdicts.0.pending.0.confirmations": 2,
                             "payload.counts.WATCH": 3, "payload.single_observation": False}),
    "exit_check stale -> UNAVAILABLE (all three groups)": (
        "exit_check.py", 20, {"status": "unavailable", "payload.counts.UNAVAILABLE": 3,
                              "payload.verdicts.0.unavailable.0.rule_id": "TP80", "payload.verdicts.0.fired.0.rule_id": "DTE21"}),
    "exit_check --dry-run --rules candidate-1.1.0 regular twice (no rule state written)": (
        "exit_check.py", 0, {"payload.dry_run": True, "payload.rulebook.version": "1.1.0", "payload.rulebook.adopted": False,
                             "payload.state.status": "missing", "payload.state.written": False}),
    J6: ("ack.py", 0, {"payload.count": 4, "payload.signals.0.signal_id": "SIG-20261120-001",
                       "payload.signals.3.signal_id": "SIG-20261120-004", "payload.signals.0.posts": 1,
                       "payload.signals.3.posts": 1}),
    "book_check regular dry run without ack -> CONFIG CHANGED first": (
        "book_check.py", 0, {"payload.dry_run": True, "payload.written": [], "payload.parts.0.kind": "config_changed",
                             "payload.config_changed.0.old": None,
                             "payload.message": harness.TextMatch(prefix="CONFIG CHANGED | desk.toml: not acknowledged")}),
    J8_LATE: ("book_check.py", 20, {"status": "unavailable", "payload.counts.UNAVAILABLE": 3, "payload.quote_age_min": 63,
                                    "payload.config_changed": [], "payload.parts.3.kind": "pre_close",
                                    "payload.message": harness.TextMatch(contains=(PRE_CLOSE_LINE_LATE, VERIFY_LIVE))}),
    J8: ("book_check.py", 0, {"payload.config_changed": [], "payload.parts.3.kind": "pre_close",
                              "payload.message": harness.TextMatch(contains=(PRE_CLOSE_LINE, VERIFY_LIVE))}),
    "ack list on a fresh copy -> 0 open signals": ("ack.py", 0, {"payload.action": "list", "payload.count": 0}),
    "rules_diff candidate-1.1.0 regular -> 0 verdicts changed (TP50 changed)": (
        "rules_diff.py", 0, {"payload.verdicts_changed": 0, "payload.rules.changed": ["TP50"], "payload.changes": []}),
    "rules_diff candidate-1.1.0 expiry_day -> 0 verdicts changed (TP50 changed)": (
        "rules_diff.py", 0, {"payload.verdicts_changed": 0, "payload.rules.changed": ["TP50"], "payload.changes": []}),
    J12: ("ack.py", 0, {"payload.rulebook_version": "1.1.0", "payload.acknowledged_hashes.rulebook": CANDIDATE_SHA256}),
    "rules_adopt 1.1.0 during regular hours without --allow-rth -> refused (regular_trading_hours)": (
        "rules_adopt.py", 1, {"status": "error", "payload.adopted": False, "payload.during_rth": True,
                              "payload.refusals.0.code": "regular_trading_hours", "payload.written": []}),
}


# The PR 2d journeys (decisions D1-16 and D2-18 with the addendum's O17, plus the brief's routine_audit journeys and one
# journey per Stage-1 routine): the script of the journey's own command, its exit code, its fixture root and keys of
# its expected JSON. Every value was computed by the tools on a copy of the fixture root (opening_bell serialises its
# money fields as it prints them: the put mid is "17.30", as D2-18 (9) has it).
TP80_RESTING = ("TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); resting-eligible: "
                "yes (below the current ask 3.50)")
TP50_CANNOT_FIRE = ("TP50 cannot fire on this position: time to expiry 13 trading days (your threshold: above 21 trading days), "
                    "and the days only fall")
TP80_CROSSED = ("TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); threshold already "
                "crossed (rule fired)")
PL_LINE = ("realized today $0.00, month to date $0.00, year to date $425.00 | unrealized $4,034.00 (liquidation), $4,097.00 "
           "(mid) | day $400.00")
RECON_MISSING = "UNAVAILABLE: reconciliation data/positions/2026-11-03.recon.json missing"
RECON_CLEAN = "positions.csv reconciliation (data/positions/2026-11-03.recon.json): CLEAN, 0 proposed edits"
DOCKET_NONE_DUE = "expiry docket (reports/expiry/2026-11-03.week.json): 0 contracts due"
LEVELS_LAST_NIGHT = ("last night's levels (levels/2026-11-02.json): AG-20261020-01 1 resting-eligible of 3 price levels; "
                     "AG-20261022-02 1 resting-eligible of 5 price levels; AG-20261028-03 0 resting-eligible of 3 price levels")
DEV_HEADER = "development run: the checkout is not verified (--allow-dirty)"
J2D_LEVELS = "trigger_levels regular -> TP80 0.65 resting-eligible, TP50 cannot fire"
J2D_CROSSED = "book_check --post expiry_day, then trigger_levels expiry_day -> UNAVAILABLE (TP80 already crossed)"
J2D_PLAN = "trigger_levels --plan AG-20261020-01 regular -> plans/AG-20261020-01.md"
J2D_BRIEF = "book --snapshot pre_open and regular, csv_recon regular, then eod_summary --brief -> P/L line"
J2D_MORNING = ("book --snapshot pre_open, trigger_levels --from-snapshot, desk_doctor --quick, csv_recon and expiry_checklist "
               "--week pre_open, then eod_summary -> MORNING DESK SHEET (no early close on 2026-11-03)")
J2D_BARE = "eod_summary on a bare copy -> UNAVAILABLE (reconciliation missing)"
J2D_SENTINEL = "ack config, routine book-check 15:35 expiry_day -> EXIT SIGNAL"
J2D_PREOPEN = ("book --snapshot pre_open, trigger_levels --from-snapshot, then routine preopen 07:30 pre_open -> "
               "MORNING DESK SHEET")
J2D_EVENING = "book --snapshot pre_open, routine eod 16:35 after_close, then routine eod-brief 17:10 -> EOD DESK BRIEF"
PR_2D: Dict[str, Tuple[str, int, str, Dict[str, Any]]] = {
    J2D_LEVELS: ("trigger_levels.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "tool": "trigger_levels", "payload.lint.ok": True, "payload.et_date": "2026-11-03",
        "payload.written": ["levels/2026-11-03.md", "levels/2026-11-03.json"], "payload.plan": None,
        "payload.text": harness.TextMatch(prefix="THRESHOLD LEVELS 2026-11-03 | rules 1.0.0, sha cc09859c",
                                          contains=(TP80_RESTING, TP50_CANNOT_FIRE))}),
    J2D_CROSSED: ("trigger_levels.py", 20, harness.FIXTURE_DESK, {
        "status": "unavailable", "payload.lint.ok": True, "payload.written": ["levels/2026-11-20.md", "levels/2026-11-20.json"],
        "payload.text": harness.TextMatch(prefix="THRESHOLD LEVELS 2026-11-20", contains=(TP80_CROSSED,))}),
    J2D_PLAN: ("trigger_levels.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "payload.written": ["levels/2026-11-03.md", "levels/2026-11-03.json", "plans/AG-20261020-01.md"],
        "payload.plan.group_id": "AG-20261020-01", "payload.plan.path": "plans/AG-20261020-01.md", "payload.plan.lint.ok": True,
        "payload.plan.text": harness.TextMatch(prefix="PLAN CARD AG-20261020-01 | WMT covered call | rules 1.0.0, sha cc09859c",
                                               contains=(TP80_RESTING, "dates that matter: last trading day 2026-11-20"))}),
    J2D_BRIEF: ("eod_summary.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "tool": "eod_summary", "payload.mode": "brief", "payload.lint.ok": True, "payload.unavailable": [],
        "payload.message": harness.TextMatch(prefix="EOD DESK BRIEF 2026-11-03\nP/L (data/book/2026-11-03.json): " + PL_LINE)}),
    J2D_MORNING: ("eod_summary.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "payload.mode": "morning", "payload.lint.ok": True, "payload.unavailable": [], "payload.exit_code": 0,
        "payload.message": harness.TextMatch(
            prefix="MORNING DESK SHEET 2026-11-03\ndoctor (state/doctor.json, quick check at 12:31 UTC): PASS",
            contains=(RECON_CLEAN, "open signals: 0", DOCKET_NONE_DUE, LEVELS_LAST_NIGHT),
            excludes=("early close", "UNAVAILABLE:"))}),
    J2D_BARE: ("eod_summary.py", 20, harness.FIXTURE_DESK, {
        "status": "unavailable", "payload.mode": "morning", "payload.lint.ok": True, "payload.exit_code": 20,
        "payload.message": harness.TextMatch(prefix="MORNING DESK SHEET 2026-11-03", contains=(RECON_MISSING,))}),
    "desk_doctor --quick regular -> PASS": ("desk_doctor.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "tool": "desk_doctor", "payload.verdict": "PASS", "payload.mode": "quick",
        "payload.checks.0.id": "interpreter", "payload.checks.2.id": "key_source", "payload.checks.2.status": "WARN",
        "payload.checks.3.id": "desk_files", "payload.checks.3.status": "PASS"}),
    "desk_doctor --selftest -> PASS": ("desk_doctor.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "payload.verdict": "PASS", "payload.checks.1.id": "selftest", "payload.checks.1.status": "PASS"}),
    J2D_SENTINEL: ("routine.py", 10, harness.FIXTURE_DESK, {
        "status": "fired", "tool": "routine", "payload.exit_code": 10, "payload.development": True,
        "payload.steps.0.tool": "book_check", "payload.steps.0.exit_code": 10, "payload.lint.ok": True,
        "payload.heartbeat": "state/heartbeat/book-check.json",
        "payload.message": harness.TextMatch(prefix=DEV_HEADER, contains=(
            "EXIT SIGNAL AG-20261020-01 | WMT covered call | your rule TP80",
            "pre-close | verdicts: 2 EXIT SIGNAL, 0 UNAVAILABLE, 1 WATCH, 0 HOLD | open signals: 4"))}),
    "routine book-check 14:05 regular -> CONFIG CHANGED, WATCH": ("routine.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "payload.steps.0.exit_code": 0, "payload.lint.ok": True, "payload.lateness_min": 45,
        "payload.message": harness.TextMatch(prefix="development run", contains=(
            "CONFIG CHANGED | desk.toml: not acknowledged -> sha f08a18dc",
            "WATCH AG-20261020-01 | WMT covered call | your rule DTE21", "run 45 min late"))}),
    "routine final-call 15:20 expiry_day -> FINAL CALL": ("routine.py", 10, harness.FIXTURE_DESK, {
        "status": "fired", "payload.steps.0.exit_code": 10, "payload.steps.1.exit_code": 0, "payload.steps.2.exit_code": 10,
        "payload.lint.ok": True, "payload.heartbeat": "state/heartbeat/final-call.json",
        "payload.message": harness.TextMatch(prefix="development run", contains=(
            "FINAL CALL Fri 2026-11-20: 2 contracts on their last trading day; act by 16:00 ET (21:00 UTC)",
            "assignment watch Fri 2026-11-20 (session open): FIRED"))}),
    "routine nope -> usage": ("routine.py", 2, harness.FIXTURE_DESK, {"status": "usage", "error.code": "invalid_arguments"}),
    "desk_init answers -> desk.toml": ("desk_init.py", 0, harness.FIXTURE_INIT, {
        "status": "ok", "tool": "desk_init", "payload.written": ["desk.toml"],
        "payload.desk_toml.sha256": "8245fc1428ddc9590d39ec32332eeff8dba0f6173142936a6fbae84cea89c18e"}),
    "desk_init on the main root -> desk_toml_exists": ("desk_init.py", 1, harness.FIXTURE_DESK, {
        "status": "error", "error.code": "desk_toml_exists"}),
    "opening_bell SPY regular": ("opening_bell.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "tool": "opening_bell", "payload.quote.mid": "648.12", "payload.expiration": "2026-12-18",
        "payload.dte_calendar": 45, "payload.strike": "640", "payload.call.mid": "28.65", "payload.put.mid": "17.30",
        "payload.expected_move.straddle": "36.76", "payload.expected_move.one_sigma": "56.98", "payload.requests_made": 4}),
    "routine_audit --budget -> lean 41, active 61 runs per week": ("routine_audit.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "tool": "routine_audit", "payload.mode": "budget", "payload.budget": {"lean": 41, "active": 61}}),
    "routine_audit --rules-text -> three Auto-review texts within 1000 characters": ("routine_audit.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "payload.mode": "rules_text", "payload.limit_chars": 1000,
        "payload.lengths": {"allow": 511, "ask_first_writes": 487, "ask_first_owner_authority": 333},
        "payload.routine_ids": ["preopen", "book-check", "final-call", "eod", "eod-brief", "post-expiry"]}),
    J2D_PREOPEN: ("routine.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "tool": "routine", "payload.exit_code": 0, "payload.lint.ok": True, "payload.slot": "07:30",
        "payload.heartbeat": "state/heartbeat/preopen.json", "payload.steps.0.tool": "desk_doctor",
        "payload.steps.3.tool": "eod_summary", "payload.steps.3.exit_code": 0, "payload.incidents": [], "payload.withheld": [],
        "payload.message": harness.TextMatch(prefix=DEV_HEADER + "\nMORNING DESK SHEET 2026-11-03",
                                             contains=(RECON_CLEAN, DOCKET_NONE_DUE, LEVELS_LAST_NIGHT),
                                             excludes=("UNAVAILABLE:", "INCIDENT", "WITHHELD"))}),
    J2D_EVENING: ("routine.py", 0, harness.FIXTURE_DESK, {
        "status": "ok", "payload.exit_code": 0, "payload.lint.ok": True, "payload.slot": "17:10",
        "payload.heartbeat": "state/heartbeat/eod-brief.json", "payload.steps.0.tool": "eod_summary",
        "payload.message": harness.TextMatch(prefix=DEV_HEADER + "\nEOD DESK BRIEF 2026-11-03\nP/L (data/book/2026-11-03.json): "
                                             + PL_LINE, contains=("routines: 1 of 7 ran today",),
                                             excludes=("UNAVAILABLE:", "INCIDENT", "WITHHELD"))}),
    "routine post-expiry 09:30 post_expiry Saturday -> EDITS_PENDING": ("routine.py", 10, harness.FIXTURE_DESK, {
        "status": "fired", "payload.exit_code": 10, "payload.steps.0.tool": "csv_recon", "payload.steps.0.exit_code": 10,
        "payload.heartbeat": "state/heartbeat/post-expiry.json", "payload.lint.ok": True,
        "payload.message": harness.TextMatch(prefix=DEV_HEADER + "\nEDITS_PENDING (2026-11-21 ET): 2 expired legs, 3 proposed edits")}),
    "routine book-check 10:05 on Thanksgiving -> quiet (closed day)": ("routine.py", 0, harness.FIXTURE_DESK, {
        "status": "quiet", "payload.closed_day": True, "payload.quiet": True, "payload.steps": [], "payload.message": None,
        "payload.heartbeat": "state/heartbeat/book-check.json"}),
}
# The instants and, for the routines, the slots the PR 2d journeys run at (the fixture sets' own instants).
PR_2D_INSTANTS: Dict[str, Tuple[str, str]] = {
    J2D_LEVELS: (harness.REGULAR_NOW, ""), J2D_CROSSED: ("2026-11-20T19:55:00Z", ""), J2D_PLAN: (harness.REGULAR_NOW, ""),
    J2D_BRIEF: (harness.REGULAR_NOW, ""), J2D_MORNING: ("2026-11-03T12:35:00Z", ""), J2D_BARE: (harness.REGULAR_NOW, ""),
    J2D_SENTINEL: (harness.EXPIRY_DAY_NOW, "15:35"),
    "routine book-check 14:05 regular -> CONFIG CHANGED, WATCH": (harness.REGULAR_NOW, "14:05"),
    "routine final-call 15:20 expiry_day -> FINAL CALL": (harness.EXPIRY_DAY_NOW, "15:20"),
    J2D_PREOPEN: ("2026-11-03T12:35:00Z", "07:30"), J2D_EVENING: ("2026-11-03T22:10:00Z", "17:10"),
    "routine post-expiry 09:30 post_expiry Saturday -> EDITS_PENDING": (harness.POST_EXPIRY_NOW, "09:30"),
    "routine book-check 10:05 on Thanksgiving -> quiet (closed day)": ("2026-11-26T15:05:00Z", "10:05"),
}


# The PR 3b journeys (the Structuring Desk's three tools). tests/fixtures/desk holds no structuring.toml (designer C's
# G13): each structuring chain writes it on its copy with desk_init (the fixture answers) and acknowledges it with
# ack.py config --owner-tapped, two and one minute before the tool runs; two fail-closed journeys run without it. Every
# value was computed by the tools on a copy of the main fixture root.
STRUCTURING_ANSWERS = "tests/fixtures/init/structuring-answers.json"
STRUCTURING_SHA256 = "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c"
STRUCTURING_FOOTER_1931 = ("computed by the desk's tools from your criteria · posted by an AI assistant · a personalized "
                           "suggestion for positions you hold, not an order · you decide and act in your brokerage account · "
                           "data delayed 15 min, as of 19:31 UTC")
J3B_ROLL_WMT = "desk_init structuring, ack config, roll_analysis WMT regular -> BEST (the Jan 115 call)"
J3B_ROLL_NVDA = "desk_init structuring, ack config, roll_analysis NVDA regular -> NONE_QUALIFIES"
J3B_ROLL_STALE = "desk_init structuring, ack config, roll_analysis WMT stale -> UNAVAILABLE after one quotes batch"
J3B_PROPOSE_SPY = "desk_init structuring, ack config, propose SPY protective_put on the main root -> NOTHING_TO_PROPOSE"
J3B_TICKET_WMT = ("desk_init structuring, ack config, exit_ticket WMT ROLL, new 2027-01-15 115 call -> TICKET, net +10.00, "
                  "peak 700.00")
J3B_TICKET_NVDA = ("desk_init structuring, ack config, exit_ticket NVDA ROLL, new 2027-01-15 160 call -> TICKET with two "
                   "failed criteria")
J3B_TICKET_SPY = "desk_init structuring, ack config, exit_ticket SPY MONETIZE_PUT -> TICKET, cash +980.00"
J3B_TICKET_EXPIRY = ("desk_init structuring, ack config, exit_ticket WMT ROLL on expiration day -> TICKET, the held call's "
                     "ask at grade E")
J3B_NO_CRITERIA = "ack config, exit_ticket WMT CLOSE without structuring.toml -> UNAVAILABLE (criteria_missing)"
J3B_NEVER_ACKED = "roll_analysis on a copy never acknowledged -> UNAVAILABLE (config_unacknowledged)"
WMT_ROLL = ("exit_ticket.py", "--trade", "AG-20261020-01", "--action", "ROLL", "--new-expiration", "2027-01-15",
            "--new-strike", "115")
# name -> (the journey's own command, its instant, the instants of the desk_init and ack steps or None).
PR_3B_COMMANDS: Dict[str, Tuple[Tuple[str, ...], str, Any]] = {
    J3B_ROLL_WMT: (("roll_analysis.py", "--trade", "AG-20261020-01"), harness.REGULAR_NOW,
                   ("2026-11-03T19:48:00Z", "2026-11-03T19:49:00Z")),
    J3B_ROLL_NVDA: (("roll_analysis.py", "--trade", "AG-20261022-02"), harness.REGULAR_NOW,
                    ("2026-11-03T19:48:00Z", "2026-11-03T19:49:00Z")),
    J3B_ROLL_STALE: (("roll_analysis.py", "--trade", "AG-20261020-01", "--fixture-set", "stale"), harness.REGULAR_NOW,
                     ("2026-11-03T19:48:00Z", "2026-11-03T19:49:00Z")),
    J3B_PROPOSE_SPY: (("propose.py", "--trade", "AG-20261028-03", "--structure", "protective_put"), harness.REGULAR_NOW,
                      ("2026-11-03T19:48:00Z", "2026-11-03T19:49:00Z")),
    J3B_TICKET_WMT: (WMT_ROLL, harness.REGULAR_NOW, ("2026-11-03T19:48:00Z", "2026-11-03T19:49:00Z")),
    J3B_TICKET_NVDA: (("exit_ticket.py", "--trade", "AG-20261022-02", "--action", "ROLL", "--new-expiration", "2027-01-15",
                       "--new-strike", "160"), harness.REGULAR_NOW, ("2026-11-03T19:48:00Z", "2026-11-03T19:49:00Z")),
    J3B_TICKET_SPY: (("exit_ticket.py", "--trade", "AG-20261028-03", "--action", "MONETIZE_PUT"), harness.REGULAR_NOW,
                     ("2026-11-03T19:48:00Z", "2026-11-03T19:49:00Z")),
    J3B_TICKET_EXPIRY: (WMT_ROLL + ("--fixture-set", "expiry_day"), harness.EXPIRY_DAY_NOW,
                        ("2026-11-20T19:48:00Z", "2026-11-20T19:49:00Z")),
    J3B_NO_CRITERIA: (("exit_ticket.py", "--trade", "AG-20261020-01", "--action", "CLOSE"), harness.REGULAR_NOW, None),
    J3B_NEVER_ACKED: (("roll_analysis.py", "--trade", "AG-20261020-01"), harness.REGULAR_NOW, None),
}
PR_3B: Dict[str, Tuple[str, int, Dict[str, Any]]] = {
    J3B_ROLL_WMT: ("roll_analysis.py", 0, {
        "status": "ok", "tool": "roll_analysis", "payload.verdict": "BEST", "payload.best": "WMT270115C00115000",
        "payload.candidates.0.score": "0.0467", "payload.candidates.0.net_credit": "0.05",
        "payload.candidates.0.net_credit_total": "10.00", "payload.passed": 1, "payload.failed": 19,
        "payload.after.max_loss": "19006.00", "payload.after.max_profit": "3994.00", "payload.after.breakeven": "95.03",
        "payload.requests.made": 7, "payload.requests.chains": 2, "payload.lint.ok": True, "payload.emitted.footer": "structuring",
        "payload.files.json": "reports/rolls/AG-20261020-01-20261103T195000Z.json", "payload.journal": "journal/structuring.jsonl",
        "payload.message": harness.TextMatch(
            prefix="### STRUCTURING AG-20261020-01 | WMT covered call: the roll analysis of the short 2026-11-20 105 call",
            contains=("**BEST CANDIDATE 1 of 20** by your objective extrinsic_per_day: **0.0467**",
                      "- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | ask 3.50 | quote grade B, 18 min old",
                      "- leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55 | quote grade B, 18 min old",
                      "| ex-dividend | 2026-11-24 (cum 2026-11-23), inside the new call's life | accepted by #assign-ok | "
                      "\U0001F7E1 |",
                      "| max loss | 19,016.00 | 19,006.00 |",
                      STRUCTURING_FOOTER_1931))}),
    J3B_ROLL_NVDA: ("roll_analysis.py", 0, {
        "status": "ok", "payload.verdict": "NONE_QUALIFIES", "payload.best": None, "payload.passed": 0, "payload.failed": 18,
        "payload.unpriced": 2,
        "payload.candidates.0.id": "NVDA270115C00160000", "payload.candidates.0.status": "FAILS_CRITERION",
        "payload.candidates.0.score": "0.0299", "payload.requests.made": 7, "payload.lint.ok": True,
        "payload.message": harness.TextMatch(
            prefix="### STRUCTURING AG-20261022-02 | NVDA covered call",
            contains=("**no candidate passed every criterion: the desk names no best candidate**",
                      "| 1 | 2027-01-15 | 160 | debit 0.05 | 0.0299 | \U0001F534 credit_only, ex_dividend |",
                      "- 1, 2, 3 fail ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls inside the new "
                      "call's life", STRUCTURING_FOOTER_1931),
            excludes=("BEST CANDIDATE",))}),
    J3B_ROLL_STALE: ("roll_analysis.py", 20, {
        "status": "unavailable", "payload.verdict": "UNAVAILABLE", "payload.cause": "spot_unusable",
        "payload.requests.made": 2, "payload.files": None, "payload.journal": "journal/structuring.jsonl",
        "payload.lint.ok": True,
        "payload.message": harness.TextMatch(prefix="UNAVAILABLE | AG-20261020-01 roll analysis | no usable quote of the "
                                                    "underlying", contains=("data delayed 15 min, as of 19:50 UTC",))}),
    J3B_PROPOSE_SPY: ("propose.py", 0, {
        "status": "ok", "tool": "propose", "payload.verdict": "NOTHING_TO_PROPOSE", "payload.cause": "not_shares_only",
        "payload.requests.made": 0, "payload.lint.ok": True, "payload.best": None, "payload.candidates": [],
        "payload.message": harness.TextMatch(prefix="NOTHING_TO_PROPOSE | AG-20261028-03 protective put proposal | the 100 SPY "
                                                    "shares already carry 1 long put")}),
    J3B_TICKET_WMT: ("exit_ticket.py", 0, {
        "status": "ok", "tool": "exit_ticket", "payload.verdict": "TICKET",
        "payload.ticket_id": "TK-AG-20261020-01-ROLL-20261103T195000Z", "payload.cash.net_conservative": "10.00",
        "payload.cash.net_mid": "35.00", "payload.cash.peak_needed": "700.00",
        "payload.replacement.candidate.status": "ELIGIBLE", "payload.approval.required_level": 1, "payload.requests.made": 4,
        "payload.lint.ok": True, "payload.emitted.footer": "structuring", "payload.files.written": True,
        "payload.journal": "journal/structuring.jsonl",
        "payload.message": harness.TextMatch(
            prefix="### TICKET AG-20261020-01 | WMT covered call: a roll of the short call (action ROLL)",
            contains=("| 1 of desk.toml (margin) | 19:50 UTC | 20:20 UTC (30 min) | 19:31 UTC, 15-min delayed |",
                      "- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | quote "
                      "grade B",
                      "- leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60 | quote "
                      "grade B",
                      "| net cash | +10.00 | +35.00 |", "| the most the steps need at once | 700.00 |  |",
                      "| max loss | 19,016.00 | 19,006.00 |",
                      STRUCTURING_FOOTER_1931))}),
    J3B_TICKET_NVDA: ("exit_ticket.py", 0, {
        "status": "ok", "payload.verdict": "TICKET", "payload.ticket_id": "TK-AG-20261022-02-ROLL-20261103T195000Z",
        "payload.cash.net_conservative": "-10.00", "payload.cash.peak_needed": "660.00",
        "payload.replacement.candidate.status": "FAILS_CRITERION", "payload.requests.made": 4, "payload.lint.ok": True,
        "payload.files.written": True,
        "payload.message": harness.TextMatch(prefix="### TICKET AG-20261022-02 | NVDA covered call", contains=(
            "| net per share | debit 0.05 (10.00 in all) | credit only | \U0001F534 |",
            "| ex-dividend | 2026-12-03 (cum 2026-12-02), inside the new call's life | excluded by your structuring.toml | "
            "\U0001F534 |",
            "| max loss | 27,630.00 | 27,640.00 |",
            STRUCTURING_FOOTER_1931))}),
    J3B_TICKET_SPY: ("exit_ticket.py", 0, {
        "status": "ok", "payload.verdict": "TICKET", "payload.ticket_id": "TK-AG-20261028-03-MONETIZE_PUT-20261103T195000Z",
        "payload.cash.net_conservative": "980.00", "payload.cash.net_mid": "995.00", "payload.cash.peak_needed": "0.00",
        "payload.realized.0.conservative": "-150.00", "payload.puts.0.itm": False, "payload.requests.made": 2,
        "payload.lint.ok": True, "payload.files.written": True,
        "payload.message": harness.TextMatch(
            prefix="### TICKET AG-20261028-03 | SPY protective put: the long put closed for its value (action MONETIZE_PUT)",
            contains=("| 2 of desk.toml (ira) | 19:50 UTC | 20:20 UTC (30 min) | 19:31 UTC, 15-min delayed |",
                      "- leg 1 of 1: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95 | quote "
                      "grade B",
                      "| net cash | +980.00 | +995.00 |", "| the most the steps need at once | 0.00 |  |",
                      "| after the leg | your 100 SPY shares carry no put: their downside is unhedged |",
                      STRUCTURING_FOOTER_1931))}),
    J3B_TICKET_EXPIRY: ("exit_ticket.py", 0, {
        "status": "ok", "payload.verdict": "TICKET", "payload.ticket_id": "TK-AG-20261020-01-ROLL-20261120T195000Z",
        "payload.legs.0.grade": "E", "payload.legs.0.conservative": "0.16", "payload.cash.net_conservative": "558.00",
        "payload.cash.peak_needed": "32.00", "payload.replacement.candidate.status": "FAILS_CRITERION",
        "payload.requests.made": 4, "payload.lint.ok": True,
        "payload.message": harness.TextMatch(prefix="### TICKET AG-20261020-01 | WMT covered call", contains=(
            "- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 0.12, ask 0.16, mid 0.14 | quote grade E | "
            "quote grade E: the conservative side stands",
            "| earnings | 2026-11-17 has passed, the next is unknown: counted inside the new call's life | excluded by your "
            "structuring.toml | \U0001F534 |",
            STRUCTURING_FOOTER_1931))}),
    J3B_NO_CRITERIA: ("exit_ticket.py", 20, {
        "status": "unavailable", "payload.verdict": "UNAVAILABLE", "payload.cause": "criteria_missing", "payload.files": None,
        "payload.journal": "journal/structuring.jsonl", "payload.lint.ok": True,
        "payload.message": harness.TextMatch(prefix="UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing "
                                                    "until you set your criteria (SETUP step 9)")}),
    J3B_NEVER_ACKED: ("roll_analysis.py", 20, {
        "status": "unavailable", "payload.verdict": "UNAVAILABLE", "payload.cause": "config_unacknowledged",
        "payload.files": None, "payload.lint.ok": True,
        "payload.message": harness.TextMatch(prefix="UNAVAILABLE | CONFIG CHANGED: desk.toml: not acknowledged -> sha f08a18dc",
                                             contains=("acknowledge with ack.py config --owner-tapped",))}),
}


# The README's try-it (``opening_bell.py --demo``): the demo line, then the regular set's snapshot of SPY, computed by the
# tool on a copy of the main fixture root.
DEMO_JOURNEY = "opening_bell --demo -> synthetic sample snapshot (no desk root, no instant)"
DEMO_LINE = "Demo: synthetic sample data from the desk's test fixtures, not real market prices; nothing is written"
DEMO_EXPECTED: Dict[str, Any] = {
    "status": "ok", "tool": "opening_bell", "payload.demo": True, "payload.fixture_set": "regular", "payload.symbol": "SPY",
    "payload.quote.mid": "648.12", "payload.expiration": "2026-12-18", "payload.expected_move.straddle": "36.76",
    "payload.requests_made": 4, "payload.lint.ok": True,
    "payload.message": harness.TextMatch(prefix=DEMO_LINE + "\nSPY 648.12 mid (bid 648.10, ask 648.14), as of 19:31 UTC",
                                         contains=("not a signal",)),
}

def by_name() -> Dict[str, harness.Journey]:
    return {journey.name: journey for journey in harness.JOURNEYS}


def commands(journey: harness.Journey) -> List[Tuple[Tuple[str, ...], int]]:
    """Every command of a journey with its expected exit: the steps first, then the journey's own."""
    return [(step.argv, step.expected_exit) for step in journey.steps] + [(journey.argv, journey.expected_exit)]


def instant(argv: Sequence[str]) -> str:
    return argv[list(argv).index("--now") + 1]


def fake_run(stdout: str, stderr: str = "", returncode: int = 0) -> Any:
    """A stand-in for ``subprocess.run`` that answers every command with the given output."""

    def run(command: Any, **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return run


def journey_copies(root: str) -> List[str]:
    """The harness's ``aegis-journey-*`` copies directly under ``root`` (a test's private temp root)."""
    return sorted(name for name in os.listdir(root) if name.startswith("aegis-journey-"))


class CatalogueTest(unittest.TestCase):
    def test_every_command_names_an_existing_script_and_a_contract_exit_code(self) -> None:
        for journey in harness.JOURNEYS:
            for argv, exit_code in commands(journey):
                with self.subTest(journey=journey.name, argv=argv):
                    self.assertTrue(argv[0].endswith(".py"), argv)
                    self.assertTrue(os.path.isfile(os.path.join(harness.SCRIPTS, argv[0])), "%s is not under scripts/" % argv[0])
                    self.assertIn(exit_code, contract.EXIT_CODES)
                    self.assertNotIn("--desk-root", argv, "the harness adds --desk-root itself")

    def test_journey_names_are_distinct(self) -> None:
        names = [journey.name for journey in harness.JOURNEYS]
        self.assertEqual(len(names), len(set(names)), names)

    def test_gate_journeys_are_exactly_three_and_alone_against_another_tree(self) -> None:
        gate = [journey for journey in harness.JOURNEYS if journey.desk_root is None and not journey.checkout_only]
        self.assertEqual(list(GATE_JOURNEYS), [journey.name for journey in gate])
        self.assertTrue(all(not journey.steps for journey in gate), "gate journeys are single commands")
        with tempfile.TemporaryDirectory(prefix="aegis-other-tree-") as other:
            selected, skipped = harness.select(other)
        self.assertEqual(list(GATE_JOURNEYS), [journey.name for journey in selected])
        self.assertEqual(len(harness.JOURNEYS) - len(GATE_JOURNEYS), len(skipped))
        selected, skipped = harness.select(ROOT)
        self.assertEqual((len(harness.JOURNEYS), []), (len(selected), skipped))

    def test_the_demo_journey_runs_the_published_try_it_command_as_is(self) -> None:
        # README "Try it now": python3 scripts/opening_bell.py --demo. The tool names this checkout's own fixture desk and
        # instant, so the harness adds no --desk-root, --offline or --now; it runs only against this checkout.
        demo = [journey for journey in harness.JOURNEYS if journey.checkout_only]
        self.assertEqual([DEMO_JOURNEY], [journey.name for journey in demo])
        journey = demo[0]
        self.assertEqual((("opening_bell.py", "--demo", "--json"), 0, None, False, ()),
                         (journey.argv, journey.expected_exit, journey.desk_root, journey.copy_desk_root, journey.steps))
        for key, value in DEMO_EXPECTED.items():
            self.assertIn(key, journey.expected_json)
            self.assertEqual(value, journey.expected_json[key], key)
        with tempfile.TemporaryDirectory(prefix="aegis-other-tree-") as other:
            _selected, skipped = harness.select(other)
        self.assertIn(journey, skipped)

    def test_tool_journeys_run_offline_with_an_instant_on_a_copy_of_a_fixture_root(self) -> None:
        tool_journeys = [journey for journey in harness.JOURNEYS if journey.desk_root is not None]
        self.assertGreater(len(tool_journeys), 9)
        for journey in tool_journeys:
            with self.subTest(journey=journey.name):
                self.assertTrue(journey.copy_desk_root, "a tool journey never runs inside the repository's fixture root")
                self.assertTrue(os.path.isdir(os.path.join(ROOT, journey.desk_root)), journey.desk_root)
                if journey.desk_root == harness.FIXTURE_INIT:
                    # desk_init's own root: answers.json and the expected rendering, no desk.toml until the journey
                    # writes one on its copy.
                    self.assertFalse(os.path.exists(os.path.join(ROOT, journey.desk_root, "desk.toml")))
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, journey.desk_root, "answers.json")))
                else:
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, journey.desk_root, "desk.toml")), journey.desk_root)
                for argv, _exit in commands(journey):
                    for flag in ("--json", "--offline", "--now"):
                        self.assertIn(flag, argv, (journey.name, argv))
                    instant = argv[argv.index("--now") + 1]
                    contract.parse_utc_instant(instant, "--now")

    def test_no_order_shaped_text_in_the_catalogue(self) -> None:
        for journey in harness.JOURNEYS:
            text = " ".join([journey.name] + [" ".join(argv) for argv, _exit in commands(journey)])
            self.assertIsNone(ORDER_SHAPED.search(text), text)
            for pattern in alert_lint.FORBIDDEN_PATTERNS:
                self.assertIsNone(pattern.search(text), (journey.name, pattern.pattern))

    def test_the_pr_2b_journeys_and_their_expectations_are_listed(self) -> None:
        listed = by_name()
        expected = {
            "csv_recon --diff recon 2026-11-24 -> UNAVAILABLE (4 edits)": (
                "csv_recon.py", 20, {"payload.verdict": "UNAVAILABLE", "payload.counts.edits": 4,
                                     "payload.counts.unavailable": 4}),
            "csv_recon recon twice (closed history served from the cache)": (
                "csv_recon.py", 20, {"payload.verdict": "UNAVAILABLE", "payload.history.0.served_from": "cache"}),
            "csv_recon regular -> CLEAN": ("csv_recon.py", 0, {"payload.verdict": "CLEAN"}),
            "csv_recon post_expiry Saturday -> EDITS_PENDING (3 edits)": (
                "csv_recon.py", 10, {"payload.verdict": "EDITS_PENDING", "payload.counts.edits": 3}),
            "csv_recon holiday -> UNAVAILABLE (2 edits)": (
                "csv_recon.py", 20, {"payload.verdict": "UNAVAILABLE", "payload.counts.edits": 2}),
            "positions_check regular -> CLEAN": (
                "positions_check.py", 0, {"payload.verdict": "CLEAN", "payload.counts.open_groups": 3}),
            "book regular": ("book.py", 0, {"payload.portfolio.unrealized_pl_mid": "4097.00",
                                            "payload.portfolio.realized_pl_ytd": "425.00"}),
            "book stale -> UNAVAILABLE": ("book.py", 20, {"status": "unavailable"}),
            "book --snapshot pre_open 2026-11-02 then book regular 2026-11-03 (day P/L from the snapshot)": (
                "book.py", 0, {"payload.portfolio.day_pl": "400.00"}),
            "calendar_check -> clean": ("calendar_check.py", 0, {"payload.verdict": "clean"}),
            "calendar_check --max-age-days 1 -> UNAVAILABLE (STALE)": (
                "calendar_check.py", 20, {"payload.verdict": "unavailable", "payload.counts.STALE": 6}),
            "assignment_watch regular -> quiet": ("assignment_watch.py", 0, {"payload.verdict": "quiet"}),
            "assignment_watch t1 -> FIRED (NVDA PIN HIGH; the WMT call expires before its ex-dividend)": (
                "assignment_watch.py", 10, {"payload.verdict": "fired", "payload.legs.0.dividend": None,
                                            "payload.legs.0.pin.tier": "PIN WATCH",
                                            "payload.legs.1.pin.tier": "PIN HIGH", "payload.legs.1.pin.distance_pct": "0.52"}),
            "expiry_checklist --week t1 (2 contracts due)": (
                "expiry_checklist.py", 10, {"payload.mode": "week", "payload.counts.listed": 2}),
            "expiry_checklist --tomorrow t1 (T-1 preview)": ("expiry_checklist.py", 10, {"payload.mode": "tomorrow"}),
            "expiry_checklist --final-call expiry_day (short legs act by the 16:00 ET close)": (
                "expiry_checklist.py", 10, {"payload.legs.0.pin.tier": "PIN HIGH", "payload.legs.1.pin.tier": "PIN WATCH",
                                            "payload.legs.1.act_by_utc": "2026-11-20T21:00:00+00:00",
                                            "payload.legs.1.act_by_basis": "session_close"}),
            "expiry_checklist --week regular (nothing due)": ("expiry_checklist.py", 0, {"payload.mode": "week"}),
        }
        for name, (script, exit_code, keys) in expected.items():
            with self.subTest(journey=name):
                self.assertIn(name, listed)
                journey = listed[name]
                self.assertEqual((script, exit_code), (journey.argv[0], journey.expected_exit))
                self.assertIsNotNone(journey.expected_json)
                for key, value in keys.items():
                    self.assertEqual(value, journey.expected_json[key], key)

    def test_the_pr_2c_journeys_and_their_expectations_are_listed(self) -> None:
        listed = by_name()
        for name, (script, exit_code, keys) in PR_2C.items():
            with self.subTest(journey=name):
                self.assertIn(name, listed)
                journey = listed[name]
                self.assertEqual((script, exit_code, harness.FIXTURE_DESK), (journey.argv[0], journey.expected_exit,
                                                                            journey.desk_root))
                self.assertIsNotNone(journey.expected_json)
                for key, value in keys.items():
                    self.assertIn(key, journey.expected_json)
                    self.assertEqual(value, journey.expected_json[key], key)

    def test_the_pr_2c_instants_are_the_fixture_sets_own(self) -> None:
        listed = by_name()
        expected = {
            "exit_check regular -> WMT and NVDA WATCH (DTE21), SPY HOLD": ("regular", harness.REGULAR_NOW),
            "exit_check expiry_day, next run after the close -> EXIT_SIGNAL (WMT TP80 95.32 %, NVDA DELTA_SIGNAL)": (
                "expiry_day", harness.EXPIRY_DAY_NOW),
            "exit_check expiry_day, no next run -> WATCH (TP80 pending: observation 1 of 2)": ("expiry_day", harness.EXPIRY_DAY_NOW),
            "exit_check stale -> UNAVAILABLE (all three groups)": ("stale", harness.REGULAR_NOW),
            "book_check regular dry run without ack -> CONFIG CHANGED first": ("regular", harness.REGULAR_NOW),
            J8_LATE: ("regular", "2026-11-03T20:35:00Z"),
            J8: ("regular", harness.REGULAR_NOW),
            "rules_diff candidate-1.1.0 regular -> 0 verdicts changed (TP50 changed)": ("regular", harness.REGULAR_NOW),
            "rules_diff candidate-1.1.0 expiry_day -> 0 verdicts changed (TP50 changed)": ("expiry_day", harness.EXPIRY_DAY_NOW),
        }
        for name, (fixture_set, now) in expected.items():
            with self.subTest(journey=name):
                argv = listed[name].argv
                self.assertEqual(fixture_set, argv[argv.index("--fixture-set") + 1])
                self.assertEqual(now, instant(argv))
        with_next_run = listed["exit_check expiry_day, next run after the close -> EXIT_SIGNAL (WMT TP80 95.32 %, NVDA DELTA_SIGNAL)"]
        self.assertEqual("2026-11-20T21:35:00Z", with_next_run.argv[with_next_run.argv.index("--next-run-utc") + 1])
        self.assertNotIn("--next-run-utc", listed["exit_check expiry_day, no next run -> WATCH (TP80 pending: observation 1 of 2)"].argv)
        self.assertNotIn("--summary", listed["book_check regular dry run without ack -> CONFIG CHANGED first"].argv)
        self.assertFalse(listed["book_check regular dry run without ack -> CONFIG CHANGED first"].steps, "no ack step")
        for name in ("rules_adopt 1.1.0 during regular hours without --allow-rth -> refused (regular_trading_hours)", J12):
            for argv, _exit in commands(listed[name]):
                self.assertNotIn("--allow-rth", argv)

    def test_the_dry_run_journey_runs_the_candidate_twice_on_one_copy(self) -> None:
        journey = by_name()["exit_check --dry-run --rules candidate-1.1.0 regular twice (no rule state written)"]
        self.assertEqual(1, len(journey.steps))
        self.assertEqual(journey.argv, journey.steps[0].argv, "the same command twice: the second finds no rule state")
        self.assertEqual(0, journey.steps[0].expected_exit)
        self.assertEqual(CANDIDATE, journey.argv[journey.argv.index("--rules") + 1])
        self.assertIn("--dry-run", journey.argv)
        self.assertEqual(False, journey.steps[0].expected_json["payload.state.written"])

    def test_the_posting_chain_acknowledges_then_posts_twice_on_one_copy(self) -> None:
        journey = by_name()[J6]
        self.assertEqual(["ack.py", "book_check.py", "book_check.py", "heartbeat.py"], [step.argv[0] for step in journey.steps])
        ack, first, second, heartbeats = journey.steps
        self.assertEqual(("ack.py", "config", "--owner-tapped"), ack.argv[:3])
        self.assertEqual(("2026-11-20T19:49:00Z", 0), (instant(ack.argv), ack.expected_exit))
        for step, now in ((first, harness.EXPIRY_DAY_NOW), (second, "2026-11-20T19:51:00Z")):
            self.assertEqual(("book_check.py", "--post", "--routine", "book-check", "--fixture-set", "expiry_day",
                              "--next-run-utc", "2026-11-20T21:35:00Z"), step.argv[:8])
            self.assertEqual(now, instant(step.argv))
        self.assertEqual(first.argv[:8], second.argv[:8], "the same command again, one minute later")
        self.assertEqual(10, first.expected_exit)
        self.assertEqual({"payload.lint.ok": True, "payload.signals.0.signal_id": "SIG-20261120-001",
                          "payload.signals.0.verdict": "EXIT_SIGNAL", "payload.signals.1.verdict": "EXIT_SIGNAL",
                          "payload.signals.2.verdict": "WATCH", "payload.signals.3.signal_id": "SIG-20261120-004",
                          "payload.signals.3.trade_id": "portfolio", "payload.heartbeat": "state/heartbeat/book-check.json"},
                         {key: first.expected_json[key] for key in (
                             "payload.lint.ok", "payload.signals.0.signal_id", "payload.signals.0.verdict",
                             "payload.signals.1.verdict", "payload.signals.2.verdict", "payload.signals.3.signal_id",
                             "payload.signals.3.trade_id", "payload.heartbeat")})
        self.assertIn("journal/signals.jsonl", first.expected_json["payload.written"])
        self.assertEqual(0, second.expected_exit)
        self.assertEqual({"status": "quiet", "payload.quiet": True, "payload.message": None, "payload.signals": [],
                          "payload.parts": [], "payload.written": ["state/rule_state.json", "state/desk_state.json",
                                                                   "state/heartbeat/book-check.json"]},
                         {key: second.expected_json[key] for key in (
                             "status", "payload.quiet", "payload.message", "payload.signals", "payload.parts",
                             "payload.written")})
        self.assertEqual(HEARTBEAT_LIST, heartbeats.argv[:2])
        self.assertEqual((1, "book-check", 0), (heartbeats.expected_json["payload.count"],
                                                heartbeats.expected_json["payload.heartbeats.0.routine_id"],
                                                heartbeats.expected_json["payload.heartbeats.0.exit_code"]))
        self.assertEqual(("ack.py", "list"), journey.argv[:2])

    def test_the_summary_and_adoption_chains_start_with_their_setup_step(self) -> None:
        listed = by_name()
        for name, ack_now, now in ((J8_LATE, "2026-11-03T20:34:00Z", "2026-11-03T20:35:00Z"),
                                   (J8, "2026-11-03T19:49:00Z", harness.REGULAR_NOW)):
            with self.subTest(journey=name):
                journey = listed[name]
                self.assertEqual(1, len(journey.steps))
                self.assertEqual(("ack.py", "config", "--owner-tapped"), journey.steps[0].argv[:3])
                self.assertEqual(ack_now, instant(journey.steps[0].argv))
                self.assertEqual(("book_check.py", "--summary"), journey.argv[:2])
                self.assertEqual(now, instant(journey.argv))
        adoption = listed[J12]
        self.assertEqual(1, len(adoption.steps))
        step = adoption.steps[0]
        self.assertEqual(("rules_adopt.py", "--file", CANDIDATE, "--version", "1.1.0", "--owner-phrase", "adopt rules 1.1.0"),
                         step.argv[:7])
        self.assertEqual(("2026-11-03T22:00:00Z", 0), (instant(step.argv), step.expected_exit))
        self.assertEqual({"payload.adopted": True, "payload.version": "1.1.0", "payload.file": "rules/exit_rules-1.1.0.toml",
                          "payload.during_rth": False},
                         {key: step.expected_json[key] for key in ("payload.adopted", "payload.version", "payload.file",
                                                                   "payload.during_rth")})
        self.assertEqual(("ack.py", "config", "--owner-tapped"), adoption.argv[:3])
        refused = listed["rules_adopt 1.1.0 during regular hours without --allow-rth -> refused (regular_trading_hours)"]
        self.assertEqual(step.argv[:7], refused.argv[:7])
        self.assertEqual(harness.REGULAR_NOW, instant(refused.argv))

    def test_the_pr_2d_journeys_and_their_expectations_are_listed(self) -> None:
        listed = by_name()
        for name, (script, exit_code, desk_root, keys) in PR_2D.items():
            with self.subTest(journey=name):
                self.assertIn(name, listed)
                journey = listed[name]
                self.assertEqual((script, exit_code, desk_root), (journey.argv[0], journey.expected_exit, journey.desk_root))
                self.assertIsNotNone(journey.expected_json)
                for key, value in keys.items():
                    self.assertIn(key, journey.expected_json)
                    self.assertEqual(value, journey.expected_json[key], key)

    def test_the_pr_2d_commands_instants_and_slots(self) -> None:
        listed = by_name()
        answers = "tests/fixtures/init/answers.json"
        expected = {
            J2D_LEVELS: ("trigger_levels.py", "--fixture-set", "regular"),
            J2D_CROSSED: ("trigger_levels.py", "--fixture-set", "expiry_day"),
            J2D_PLAN: ("trigger_levels.py", "--plan", "AG-20261020-01", "--fixture-set", "regular"),
            J2D_BRIEF: ("eod_summary.py", "--brief"),
            J2D_MORNING: ("eod_summary.py",),
            J2D_BARE: ("eod_summary.py",),
            "desk_doctor --quick regular -> PASS": ("desk_doctor.py", "--quick"),
            "desk_doctor --selftest -> PASS": ("desk_doctor.py", "--selftest"),
            J2D_SENTINEL: ("routine.py", "book-check", "--slot", "15:35", "--allow-dirty", "--fixture-set", "expiry_day"),
            "routine book-check 14:05 regular -> CONFIG CHANGED, WATCH": (
                "routine.py", "book-check", "--slot", "14:05", "--allow-dirty", "--fixture-set", "regular"),
            "routine final-call 15:20 expiry_day -> FINAL CALL": (
                "routine.py", "final-call", "--slot", "15:20", "--allow-dirty", "--fixture-set", "expiry_day"),
            "routine nope -> usage": ("routine.py", "nope"),
            "desk_init answers -> desk.toml": ("desk_init.py", "--answers", answers),
            "desk_init on the main root -> desk_toml_exists": ("desk_init.py", "--answers", answers),
            "opening_bell SPY regular": ("opening_bell.py", "--symbol", "SPY"),
            "routine_audit --budget -> lean 41, active 61 runs per week": ("routine_audit.py", "--budget"),
            "routine_audit --rules-text -> three Auto-review texts within 1000 characters": ("routine_audit.py", "--rules-text"),
            J2D_PREOPEN: ("routine.py", "preopen", "--slot", "07:30", "--allow-dirty", "--fixture-set", "pre_open"),
            J2D_EVENING: ("routine.py", "eod-brief", "--slot", "17:10", "--allow-dirty"),
            "routine post-expiry 09:30 post_expiry Saturday -> EDITS_PENDING": (
                "routine.py", "post-expiry", "--slot", "09:30", "--allow-dirty", "--fixture-set", "post_expiry"),
            "routine book-check 10:05 on Thanksgiving -> quiet (closed day)": (
                "routine.py", "book-check", "--slot", "10:05", "--allow-dirty", "--fixture-set", "holiday"),
        }
        self.assertEqual(sorted(PR_2D), sorted(expected))
        for name, argv in expected.items():
            with self.subTest(journey=name):
                now, slot = PR_2D_INSTANTS.get(name, (harness.REGULAR_NOW, ""))
                self.assertEqual(harness.tool_argv(argv, now), listed[name].argv)
                if slot:
                    self.assertEqual(slot, argv[argv.index("--slot") + 1])

    def test_the_pr_2d_chains_run_their_setup_steps_first_on_one_copy(self) -> None:
        listed = by_name()
        pre_open_snapshot = (("book.py", "--snapshot", "--fixture-set", "pre_open"), harness.PREV_EOD_NOW, 0)
        levels_from_snapshot = (("trigger_levels.py", "--from-snapshot"), harness.PREV_EOD_NOW, 0)
        morning = "2026-11-03T12:31:00Z"
        chains = {
            J2D_CROSSED: [(("book_check.py", "--post", "--next-run-utc", "2026-11-20T21:35:00Z", "--fixture-set", "expiry_day"),
                           harness.EXPIRY_DAY_NOW, 10)],
            J2D_BRIEF: [pre_open_snapshot, (("book.py", "--snapshot", "--fixture-set", "regular"), harness.REGULAR_NOW, 0),
                        (("csv_recon.py", "--fixture-set", "regular"), harness.REGULAR_NOW, 0)],
            J2D_MORNING: [pre_open_snapshot, levels_from_snapshot, (("desk_doctor.py", "--quick"), morning, 0),
                          (("csv_recon.py", "--fixture-set", "pre_open"), morning, 0),
                          (("expiry_checklist.py", "--week", "--fixture-set", "pre_open"), morning, 0)],
            J2D_SENTINEL: [(("ack.py", "config", "--owner-tapped"), "2026-11-20T19:49:00Z", 0)],
            J2D_PREOPEN: [pre_open_snapshot, levels_from_snapshot],
            J2D_EVENING: [pre_open_snapshot, (("routine.py", "eod", "--slot", "16:35", "--allow-dirty", "--fixture-set",
                                               "after_close"), "2026-11-03T21:35:00Z", 0)],
        }
        for name in PR_2D:
            with self.subTest(journey=name):
                steps = listed[name].steps
                chain = chains.get(name, [])
                self.assertEqual([harness.tool_argv(argv, now) for argv, now, _exit in chain], [step.argv for step in steps])
                self.assertEqual([item[2] for item in chain], [step.expected_exit for step in steps])
        self.assertEqual({"status": "fired", "payload.posted": True}, listed[J2D_CROSSED].steps[0].expected_json)
        doctor, recon, docket = listed[J2D_MORNING].steps[2:]
        self.assertEqual(("PASS", "state/doctor.json"), (doctor.expected_json["payload.verdict"],
                                                         doctor.expected_json["payload.record"]))
        self.assertEqual("CLEAN", recon.expected_json["payload.verdict"])
        self.assertEqual(0, docket.expected_json["payload.counts.listed"])
        eod = listed[J2D_EVENING].steps[1]
        self.assertEqual(("state/heartbeat/eod.json", "trigger_levels"), (eod.expected_json["payload.heartbeat"],
                                                                         eod.expected_json["payload.steps.1.tool"]))

    def test_the_pr_3b_journeys_and_their_expectations_are_listed(self) -> None:
        listed = by_name()
        for name, (script, exit_code, keys) in PR_3B.items():
            with self.subTest(journey=name):
                self.assertIn(name, listed)
                journey = listed[name]
                self.assertEqual((script, exit_code, harness.FIXTURE_DESK), (journey.argv[0], journey.expected_exit,
                                                                            journey.desk_root))
                self.assertIsNotNone(journey.expected_json)
                for key, value in keys.items():
                    self.assertIn(key, journey.expected_json)
                    self.assertEqual(value, journey.expected_json[key], key)

    def test_the_pr_3b_commands_instants_and_setup_steps(self) -> None:
        listed = by_name()
        self.assertEqual(sorted(PR_3B), sorted(PR_3B_COMMANDS))
        for name, (argv, now, setup) in PR_3B_COMMANDS.items():
            with self.subTest(journey=name):
                journey = listed[name]
                self.assertEqual(harness.tool_argv(argv, now), journey.argv)
                if setup is None:
                    expected = [] if name == J3B_NEVER_ACKED else [harness.tool_argv(("ack.py", "config", "--owner-tapped"),
                                                                                      "2026-11-03T19:49:00Z")]
                else:
                    expected = [harness.tool_argv(("desk_init.py", "--answers", STRUCTURING_ANSWERS), setup[0]),
                                harness.tool_argv(("ack.py", "config", "--owner-tapped"), setup[1])]
                self.assertEqual(expected, [step.argv for step in journey.steps])
                self.assertEqual([0] * len(expected), [step.expected_exit for step in journey.steps])
                if setup is not None:
                    init, ack = journey.steps
                    self.assertEqual({"status": "ok", "payload.written": ["structuring.toml"],
                                      "payload.structuring_toml.sha256": STRUCTURING_SHA256}, init.expected_json)
                    self.assertEqual(STRUCTURING_SHA256, ack.expected_json["payload.acknowledged_hashes.structuring_toml"])
        self.assertFalse(os.path.exists(os.path.join(ROOT, harness.FIXTURE_DESK, "structuring.toml")),
                         "the fixture desk holds no structuring.toml (designer C's G13): the chains write it on their copy")
        self.assertEqual(STRUCTURING_SHA256, harness.STRUCTURING_SHA256)
        self.assertEqual(STRUCTURING_ANSWERS, harness.STRUCTURING_ANSWERS)

    def test_the_structuring_tools_never_run_in_a_routine_journey(self) -> None:
        for journey in harness.JOURNEYS:
            for argv, _exit in commands(journey):
                if argv[0] == "routine.py":
                    self.assertFalse({"exit_ticket.py", "roll_analysis.py", "propose.py"} & set(argv), argv)
        scripts = {argv[0] for journey in harness.JOURNEYS for argv, _exit in commands(journey)}
        self.assertTrue({"exit_ticket.py", "roll_analysis.py", "propose.py"} <= scripts)

    def test_every_stage_1_routine_has_a_development_journey_at_a_named_slot(self) -> None:
        from aegis_desk import routines

        ran = set()
        for journey in harness.JOURNEYS:
            for argv, _exit in commands(journey):
                if argv[0] != "routine.py" or argv[1] == "nope":
                    continue
                with self.subTest(journey=journey.name, argv=argv):
                    self.assertIn("--allow-dirty", argv, "a development run: the checkout is never the release tag")
                    self.assertIn("--slot", argv)
                ran.add(argv[1])
        self.assertEqual(set(routines.load_manifest().routine_ids), ran)

    def test_every_tool_has_a_journey_and_the_generators_have_none(self) -> None:
        tools = set()
        for name in os.listdir(harness.SCRIPTS):
            if name.endswith(".py"):
                with open(os.path.join(harness.SCRIPTS, name), encoding="utf-8") as handle:
                    source = handle.read()
                if "def " + "build_parser(" in source and "def main(" in source:
                    tools.add(name)
        self.assertEqual(26, len(tools))
        scripts = {argv[0] for journey in harness.JOURNEYS for argv, _exit in commands(journey)}
        self.assertEqual(set(), tools - scripts, "every desk tool runs in at least one journey")
        # check.sh steps 9 and 10 run the generators' --check on the tree under test; a journey without a desk root
        # would run them against the gate fixture trees too, which hold nothing to render (decision D2-18).
        self.assertEqual(set(), scripts & {"gen_tools_doc.py", "gen_role_skills.py"})

    def test_every_alert_fixture_has_its_own_journey(self) -> None:
        on_disk = sorted(os.path.basename(path)[:-len(".txt")] for path in glob.glob(os.path.join(ROOT, ALERT_FIXTURES, "*.txt")))
        self.assertEqual(sorted(PASS_FIXTURES + tuple(FAIL_FIXTURE_CODES)), on_disk)
        lint_journeys = {journey.argv[journey.argv.index("--message-file") + 1]: journey
                         for journey in harness.JOURNEYS if journey.argv[0] == "alert_lint.py"}
        self.assertEqual(len(on_disk), len(lint_journeys))
        for fixture in on_disk:
            with self.subTest(fixture=fixture):
                journey = lint_journeys["%s/%s.txt" % (ALERT_FIXTURES, fixture)]
                self.assertEqual("%s/%s.emitted.json" % (ALERT_FIXTURES, fixture),
                                 journey.argv[journey.argv.index("--emitted-file") + 1])
                if fixture in FAIL_FIXTURE_CODES:
                    self.assertEqual(1, journey.expected_exit)
                    self.assertEqual({"status": "error", "payload.ok": False,
                                      "payload.findings.0.code": FAIL_FIXTURE_CODES[fixture]},
                                     {key: journey.expected_json[key] for key in ("status", "payload.ok", "payload.findings.0.code")})
                else:
                    self.assertEqual(0, journey.expected_exit)
                    self.assertEqual({"status": "ok", "payload.ok": True, "payload.problems": []},
                                     {key: journey.expected_json[key] for key in ("status", "payload.ok", "payload.problems")})

    def test_the_snapshot_chain_runs_the_snapshot_first_on_the_same_copy(self) -> None:
        journey = by_name()["book --snapshot pre_open 2026-11-02 then book regular 2026-11-03 (day P/L from the snapshot)"]
        self.assertEqual(1, len(journey.steps))
        step = journey.steps[0]
        self.assertEqual(("book.py", "--snapshot"), step.argv[:2])
        self.assertIn("pre_open", step.argv)
        self.assertEqual("2026-11-02T21:30:00Z", step.argv[step.argv.index("--now") + 1])
        self.assertEqual(0, step.expected_exit)
        self.assertEqual("2026-11-03T19:50:00Z", journey.argv[journey.argv.index("--now") + 1])
        self.assertEqual(harness.FIXTURE_DESK, journey.desk_root)

    def test_the_recon_root_journeys_copy_the_recon_fixture_root(self) -> None:
        listed = by_name()
        for name in ("csv_recon --diff recon 2026-11-24 -> UNAVAILABLE (4 edits)",
                     "csv_recon recon twice (closed history served from the cache)"):
            journey = listed[name]
            self.assertEqual(("tests/fixtures/desk_recon", True), (journey.desk_root, journey.copy_desk_root))
            for argv, _exit in commands(journey):
                self.assertEqual("2026-11-24T12:00:00Z", argv[argv.index("--now") + 1])
        self.assertIn("--diff", listed["csv_recon --diff recon 2026-11-24 -> UNAVAILABLE (4 edits)"].argv)


class StepsTest(unittest.TestCase):
    """``setUp`` points ``tempfile.tempdir`` at a private root (``harness.run_journey`` makes its copy with
    ``tempfile.mkdtemp`` at call time, so the copy lands there); ``tearDown`` removes the root."""

    def setUp(self) -> None:
        self.private = tempfile.mkdtemp(prefix="aegis-steps-root-")
        patcher = mock.patch.object(tempfile, "tempdir", self.private)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self.private, ignore_errors=True)

    def run_recording_copies(self, journey: harness.Journey) -> Tuple[Dict[str, Any], List[str]]:
        """``harness.run_journey`` with every directory it makes through ``tempfile.mkdtemp`` recorded."""
        made: List[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def recording_mkdtemp(*args: Any, **kwargs: Any) -> str:
            path = real_mkdtemp(*args, **kwargs)
            made.append(path)
            return path

        with mock.patch.object(tempfile, "mkdtemp", recording_mkdtemp):
            result = harness.run_journey(journey, ROOT)
        return result, made

    def assert_one_copy_in_the_private_root_and_removed(self, made: List[str]) -> None:
        self.assertEqual([self.private], [os.path.dirname(path) for path in made], "the chain's one copy, made here")
        self.assertTrue(os.path.basename(made[0]).startswith("aegis-journey-"), made)
        self.assertFalse(os.path.exists(made[0]), "the copy is removed after the chain")

    def test_steps_run_in_order_on_one_copy_before_the_journey_command(self) -> None:
        step = harness.tool_step(HEARTBEAT_WRITE, 0, {"status": "ok", "payload.heartbeat.routine_id": "book-check"})
        journey = harness.tool_journey("heartbeat twice on one copy", HEARTBEAT_LIST, 0,
                                       {"status": "ok", "payload.count": 1}, steps=(step,))
        before = journey_copies(self.private)
        result, made = self.run_recording_copies(journey)
        self.assertTrue(result["ok"], result)
        self.assertEqual(0, result["exit"])
        self.assertEqual([0], [item["exit"] for item in result["steps"]])
        self.assertEqual([[]], [item["problems"] for item in result["steps"]])
        self.assert_one_copy_in_the_private_root_and_removed(made)
        self.assertEqual(before, journey_copies(self.private), "the copy is removed after the chain")

    def test_separate_journeys_do_not_share_a_copy(self) -> None:
        # The catalogue's own ``heartbeat --list`` expects count 0 on a fresh copy: a record written by
        # another journey never leaks into it.
        listed = by_name()["heartbeat --list"]
        self.assertEqual(0, listed.expected_json["payload.count"])
        result = harness.run_journey(listed, ROOT)
        self.assertTrue(result["ok"], result)

    def test_a_failing_step_fails_the_journey_and_the_later_commands_are_not_run(self) -> None:
        wrong = harness.tool_step(HEARTBEAT_LIST, 20)  # the list command exits 0: the expectation is wrong on purpose
        journey = harness.tool_journey("a chain whose first step fails", HEARTBEAT_LIST, 0, {"status": "ok"}, steps=(wrong,))
        before = journey_copies(self.private)
        result, made = self.run_recording_copies(journey)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["exit"], "the journey's own command was not run")
        self.assertEqual(1, len(result["steps"]))
        self.assertEqual(0, result["steps"][0]["exit"])
        self.assertTrue(any(problem.startswith("step 1 of 2") for problem in result["problems"]), result["problems"])
        self.assertTrue(any("not run" in problem for problem in result["problems"]), result["problems"])
        self.assert_one_copy_in_the_private_root_and_removed(made)
        self.assertEqual(before, journey_copies(self.private))

    def test_a_step_missing_json_key_is_reported_with_the_step(self) -> None:
        step = harness.tool_step(HEARTBEAT_LIST, 0, {"payload.no_such_key": 1})
        journey = harness.tool_journey("a chain whose first step lacks a key", HEARTBEAT_LIST, 0, {"status": "ok"}, steps=(step,))
        result = harness.run_journey(journey, ROOT)
        self.assertFalse(result["ok"])
        self.assertTrue(any("step 1 of 2" in problem and "payload.no_such_key" in problem for problem in result["problems"]),
                        result["problems"])

    def test_a_journey_without_steps_reports_an_empty_steps_list(self) -> None:
        journey = harness.tool_journey("heartbeat --list alone", HEARTBEAT_LIST, 0, {"status": "ok", "payload.count": 0})
        result = harness.run_journey(journey, ROOT)
        self.assertEqual(([], True, 0), (result["steps"], result["ok"], result["exit"]))

    def test_tool_step_and_tool_journey_append_the_offline_flags(self) -> None:
        step = harness.tool_step(("book.py", "--snapshot"), 0, None, now="2026-11-02T21:30:00Z")
        self.assertEqual(("book.py", "--snapshot", "--json", "--offline", "--now", "2026-11-02T21:30:00Z"), step.argv)
        self.assertEqual((0, None), (step.expected_exit, step.expected_json))
        journey = harness.tool_journey("x", ("book.py",), 20, {"status": "unavailable"}, desk_root="tests/fixtures/desk_recon",
                                       steps=(step,))
        self.assertEqual(("book.py", "--json", "--offline", "--now", harness.REGULAR_NOW), journey.argv)
        self.assertEqual(("tests/fixtures/desk_recon", True, (step,), 20), (journey.desk_root, journey.copy_desk_root,
                                                                            journey.steps, journey.expected_exit))
        plain = harness.tool_journey("y", ("book.py",))
        self.assertEqual((harness.FIXTURE_DESK, (), 0, None), (plain.desk_root, plain.steps, plain.expected_exit,
                                                               plain.expected_json))


class ForbiddenOutputTest(unittest.TestCase):
    """``FORBIDDEN_OUTPUT`` holds the alert lint's whole forbidden-phrase table besides the harness's own two
    patterns; each table example (the table's own text, so no phrase is spelled here) in a command's stdout or
    stderr fails the command. ``subprocess.run`` is replaced so no tool runs."""

    def test_the_lint_table_is_part_of_the_screen(self) -> None:
        for pattern in alert_lint.FORBIDDEN_PATTERNS:
            self.assertIn(pattern, harness.FORBIDDEN_OUTPUT)
        self.assertEqual(len(alert_lint.FORBIDDEN_PATTERNS) + 2, len(harness.FORBIDDEN_OUTPUT))
        self.assertTrue(any("_order" in pattern.pattern for pattern in harness.FORBIDDEN_OUTPUT[:2]))
        self.assertTrue(any("accounts" in pattern.pattern for pattern in harness.FORBIDDEN_OUTPUT[:2]))

    def test_each_lint_example_in_stdout_or_stderr_fails_the_command(self) -> None:
        for entry in alert_lint.FORBIDDEN_LANGUAGE:
            for stdout, stderr in ((json.dumps({"status": "ok", "payload": {"text": "x " + entry.example + " y"}}), ""),
                                   (json.dumps({"status": "ok"}), "note: " + entry.example)):
                with self.subTest(entry=entry.code, stream="stderr" if stderr else "stdout"):
                    with mock.patch("subprocess.run", fake_run(stdout, stderr)):
                        code, problems = harness.check_command(HEARTBEAT_LIST, 0, {"status": "ok"}, ROOT)
                    self.assertEqual(0, code)
                    self.assertTrue(any(problem.startswith("output contains order-shaped text") for problem in problems),
                                    (entry.code, problems))

    def test_clean_output_passes_the_screen(self) -> None:
        with mock.patch("subprocess.run", fake_run(json.dumps({"status": "ok", "payload": {"message": "WATCH AG-20261020-01"}}))):
            self.assertEqual((0, []), harness.check_command(HEARTBEAT_LIST, 0, {"status": "ok"}, ROOT))


class TextMatchTest(unittest.TestCase):
    def test_prefix_and_parts(self) -> None:
        both = harness.TextMatch(prefix="CONFIG CHANGED |", contains=("pre-close |", "verify live"))
        self.assertTrue(both.matches("CONFIG CHANGED | desk.toml\npre-close | verdicts\nverify live quotes"))
        self.assertFalse(both.matches("WATCH x\nCONFIG CHANGED | pre-close | verify live"), "the prefix is the start")
        self.assertFalse(both.matches("CONFIG CHANGED | pre-close |"), "every part must be present")
        self.assertFalse(both.matches(None), "a JSON null is not a string")
        self.assertFalse(both.matches(["CONFIG CHANGED | pre-close | verify live"]))
        self.assertTrue(harness.TextMatch().matches(""), "no prefix and no parts: any string")
        self.assertEqual(harness.TextMatch(prefix="a"), harness.TextMatch(prefix="a"))

    def test_excluded_parts_must_be_absent(self) -> None:
        sheet = harness.TextMatch(prefix="MORNING DESK SHEET", contains=("open signals: 0",), excludes=("early close",))
        self.assertTrue(sheet.matches("MORNING DESK SHEET 2026-11-03\nopen signals: 0"))
        self.assertFalse(sheet.matches("MORNING DESK SHEET 2026-11-27\nearly close today at 13:00 ET\nopen signals: 0"))
        self.assertEqual((), harness.TextMatch().excludes)

    def test_check_command_reports_a_text_mismatch_with_the_key(self) -> None:
        document = json.dumps({"status": "ok", "payload": {"message": "WATCH AG-20261020-01 | WMT covered call"}})
        with mock.patch("subprocess.run", fake_run(document)):
            _code, problems = harness.check_command(HEARTBEAT_LIST, 0, {"payload.message": harness.TextMatch(prefix="CONFIG")}, ROOT)
            _code, passing = harness.check_command(HEARTBEAT_LIST, 0, {"payload.message": harness.TextMatch(
                prefix="WATCH AG-20261020-01", contains=("covered call",))}, ROOT)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("payload.message", problems[0])
        self.assertIn("CONFIG", problems[0])
        self.assertEqual([], passing)


class LookupTest(unittest.TestCase):
    def test_dotted_paths_and_list_indices(self) -> None:
        document = {"status": "ok", "payload": {"legs": [{"pin": {"tier": "PIN HIGH"}}], "day_pl": None, "a.b": 1}}
        self.assertEqual((True, "ok"), harness.lookup(document, "status"))
        self.assertEqual((True, "PIN HIGH"), harness.lookup(document, "payload.legs.0.pin.tier"))
        self.assertEqual((True, None), harness.lookup(document, "payload.day_pl"), "a JSON null is found, not missing")
        self.assertEqual((True, 1), harness.lookup(document["payload"], "a.b"), "a literal key wins over a dotted path")
        self.assertEqual((False, None), harness.lookup(document, "payload.legs.1.pin"))
        self.assertEqual((False, None), harness.lookup(document, "payload.nope"))


if __name__ == "__main__":
    unittest.main()
