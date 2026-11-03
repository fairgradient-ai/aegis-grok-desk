"""``scripts/cli_journeys.py``, the journey harness: the catalogue of published commands and the
multi-step journeys that run several commands on ONE copy of a fixture desk root (the snapshot chain).

The gate runs the whole catalogue (``bash scripts/check.sh``, step 8); these tests pin the harness's
own contract cheaply: every journey names an existing script and an exit code of the contract's table,
tool journeys run ``--json --offline --now <instant>`` against a temporary copy of a fixture root, the
three gate journeys stay exactly three (``tests/gates/test_check_sh.py`` expects ``ok: 3 command
journeys passed`` on a fixture tree), the PR 2b journeys of the addendum are listed with their
expectations, and ``steps`` run in order on one copy (proved with two ``heartbeat.py`` commands, which
cost nothing: the second sees the first's record), stop at the first failing step and leave no copy
behind. Each ``StepsTest`` test points ``tempfile.tempdir`` at a private root of its own, so the harness's
copies land there and a suite or ``cli_journeys.py`` run sharing the machine's temp directory cannot
change the listings it compares. No order-shaped word appears in a journey's name or command line.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import os
import re
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Tuple
from unittest import mock

from aegis_desk import contract

import cli_journeys as harness

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORDER_SHAPED = re.compile(r"\b(?:buy|sell|sold|bought|place|submit|limit order|market order|good till|time in force)\b",
                          re.IGNORECASE)
GATE_JOURNEYS = ("check_manifests --json", "check_no_write_paths --json", "check_grok_template --json")
HEARTBEAT_WRITE = ("heartbeat.py", "--routine", "book-check", "--exit-code", "0", "--status", "ok", "--scheduled-et", "10:05")
HEARTBEAT_LIST = ("heartbeat.py", "--list")


def by_name() -> Dict[str, harness.Journey]:
    return {journey.name: journey for journey in harness.JOURNEYS}


def commands(journey: harness.Journey) -> List[Tuple[Tuple[str, ...], int]]:
    """Every command of a journey with its expected exit: the steps first, then the journey's own."""
    return [(step.argv, step.expected_exit) for step in journey.steps] + [(journey.argv, journey.expected_exit)]


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
