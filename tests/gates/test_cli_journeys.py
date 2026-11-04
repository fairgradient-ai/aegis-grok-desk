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
change the listings it compares. No order-shaped word appears in a journey's name or command line.
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
}
PASS_FIXTURES = ("pass_exit_signal", "pass_watch", "pass_unavailable", "pass_composed", "pass_pre_close")
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
        gate = [journey for journey in harness.JOURNEYS if journey.desk_root is None]
        self.assertEqual(list(GATE_JOURNEYS), [journey.name for journey in gate])
        self.assertTrue(all(not journey.steps for journey in gate), "gate journeys are single commands")
        with tempfile.TemporaryDirectory(prefix="aegis-other-tree-") as other:
            selected, skipped = harness.select(other)
        self.assertEqual(list(GATE_JOURNEYS), [journey.name for journey in selected])
        self.assertEqual(len(harness.JOURNEYS) - len(GATE_JOURNEYS), len(skipped))
        selected, skipped = harness.select(ROOT)
        self.assertEqual((len(harness.JOURNEYS), []), (len(selected), skipped))

    def test_tool_journeys_run_offline_with_an_instant_on_a_copy_of_a_fixture_root(self) -> None:
        tool_journeys = [journey for journey in harness.JOURNEYS if journey.desk_root is not None]
        self.assertGreater(len(tool_journeys), 9)
        for journey in tool_journeys:
            with self.subTest(journey=journey.name):
                self.assertTrue(journey.copy_desk_root, "a tool journey never runs inside the repository's fixture root")
                self.assertTrue(os.path.isdir(os.path.join(ROOT, journey.desk_root)), journey.desk_root)
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
            "expiry_checklist --final-call expiry_day (act by 16:30 ET)": (
                "expiry_checklist.py", 10, {"payload.legs.0.pin.tier": "PIN HIGH", "payload.legs.1.pin.tier": "PIN WATCH",
                                            "payload.legs.1.act_by_utc": "2026-11-20T21:30:00+00:00"}),
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
