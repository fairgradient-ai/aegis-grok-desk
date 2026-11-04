"""``scripts/eod_summary.py`` run in-process against copied fixture desk roots: the Morning Desk Sheet (default) and
the EOD Desk Brief (``--brief``) composed from the records the other tools wrote (``state/doctor.json``,
``data/positions/<D>.recon.json``, ``reports/expiry/<D>.week.json``, ``levels/<D>.json``, ``data/book/<D>.json``, the
journals and the heartbeat history), linted strictly before anything prints, never writing a file; the UNAVAILABLE
lines and exit 20, the exit codes 0, 1 and 2 (never 10), the JSON payload (``message`` and ``emitted``, re-linted)
and the goldens recomputed with the landed tools (PR 2d decisions D1-13 to D1-16, overrides O3, O5, O12, O15)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

import book as book_tool
import book_check as book_check_tool
import csv_recon as csv_recon_tool
import desk_doctor as desk_doctor_tool
import eod_summary as tool  # the tool scripts/eod_summary.py
import expiry_checklist as expiry_tool
import trigger_levels as trigger_levels_tool
from aegis_desk import alert_lint, contract, desk_paths, heartbeat, state, summaries

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
MORNING_STEP_NOW = "2026-11-03T12:31:00Z"
MORNING_NOW = "2026-11-03T12:35:00Z"
BRIEF_SLOT_NOW = "2026-11-03T22:12:00Z"
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
FOOTER_1231 = alert_lint.footer_line(contract.parse_utc_instant("2026-11-03T12:31:00Z"))
# the staged records' data instant (the 2026-11-02 close the reconciliation, the docket and the levels read), not the
# 12:31 UTC the tools ran at: the footer says how old the data is
FOOTER_2059 = alert_lint.footer_line(contract.parse_utc_instant("2026-11-02T20:59:55Z"))
FOOTER_1931 = alert_lint.footer_line(contract.parse_utc_instant("2026-11-03T19:31:12Z"))
MORNING_SHEET = [
    "MORNING DESK SHEET 2026-11-03",
    "doctor (state/doctor.json, quick check at 12:31 UTC): PASS, 1 warning: token_source",
    "positions.csv reconciliation (data/positions/2026-11-03.recon.json): CLEAN, 0 proposed edits (csv_recon.py --diff "
    "lists them)",
    "open signals: 0",
    "expiry docket (reports/expiry/2026-11-03.week.json): 0 contracts due",
    "last night's levels (levels/2026-11-02.json): AG-20261020-01 1 resting-eligible of 3 price levels; AG-20261022-02 1 "
    "resting-eligible of 5 price levels; AG-20261028-03 0 resting-eligible of 3 price levels",
    FOOTER_2059,
]
PL_GOLDEN = ("realized today $0.00, month to date $0.00, year to date $425.00 | unrealized $4,034.00 (liquidation), "
             "$4,097.00 (mid) | day $400.00")
BRIEF = [
    "EOD DESK BRIEF 2026-11-03",
    "P/L (data/book/2026-11-03.json): " + PL_GOLDEN,
    "AG-20261020-01 WMT covered call | unrealized $1,124.00 (liquidation), $1,145.00 (mid) | day $84.00 (row 3), -$50.00 "
    "(row 4) | ok",
    "AG-20261022-02 NVDA covered call | unrealized $1,490.00 (liquidation), $1,515.00 (mid) | day $264.00 (row 5), "
    "-$70.00 (row 6) | ok",
    "AG-20261028-03 SPY protective put | unrealized $1,420.00 (liquidation), $1,437.00 (mid) | day $222.00 (row 7), "
    "-$50.00 (row 8) | ok",
    "signals today: 0 (0 EXIT SIGNAL, 0 WATCH, 0 UNAVAILABLE)",
    "open signals: 0",
    "positions.csv reconciliation (data/positions/2026-11-03.recon.json): CLEAN, 0 proposed edits (csv_recon.py --diff "
    "lists them)",
    "expiry docket: none recorded today (reports/expiry/2026-11-03.week.json missing)",
    "routines: 0 of 4 ran today; late: none; missed: preopen 07:30 ET, book-check 10:05 ET, book-check 12:05 ET, "
    "book-check 14:05 ET",
    "to do: CONFIG CHANGED pending: desk.toml, positions.csv, adopted rulebook (acknowledge with ack.py config "
    "--owner-tapped)",
    FOOTER_1931,
]


def run_module(module: Any, argv: Sequence[str]) -> Tuple[int, str]:
    """``main(argv)`` with stdout captured; a usage error's ``SystemExit`` is its exit code (2)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = module.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-eod-summary-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, *extra: str, now: str = MORNING_NOW, json_mode: bool = True) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now] + list(extra)
        return argv + (["--json"] if json_mode else [])

    def run_json(self, *extra: str, now: str = MORNING_NOW) -> Tuple[int, Dict[str, Any]]:
        code, out = run_module(tool, self.argv(*extra, now=now))
        self.assertEqual([], order_shaped(out))
        return code, json.loads(out)

    def run_text(self, *extra: str, now: str = MORNING_NOW) -> Tuple[int, str]:
        code, out = run_module(tool, self.argv(*extra, now=now, json_mode=False))
        self.assertEqual([], order_shaped(out))
        return code, out

    def step(self, module: Any, now: str, *extra: str, fixture_set: Optional[str] = None,
             expected: Sequence[int] = (0,)) -> Dict[str, Any]:
        argv = list(extra) + ["--json", "--offline", "--desk-root", self.root, "--now", now]
        if fixture_set is not None:
            argv += ["--fixture-set", fixture_set]
        code, out = run_module(module, argv)
        self.assertIn(code, expected, out)
        return json.loads(out)

    def stage_morning(self) -> None:
        """D1-16 (5): last night's snapshot and levels, then the 07:31 ET doctor, recon and week docket."""
        self.step(book_tool, PREV_EOD_NOW, "--snapshot", fixture_set="pre_open")
        self.step(trigger_levels_tool, PREV_EOD_NOW, "--from-snapshot")
        with mock.patch.object(desk_doctor_tool, "LOCAL_OFFSET_S", 0):
            self.step(desk_doctor_tool, MORNING_STEP_NOW, "--quick")
        self.step(csv_recon_tool, MORNING_STEP_NOW, fixture_set="pre_open")
        self.step(expiry_tool, MORNING_STEP_NOW, "--week", fixture_set="pre_open")

    def stage_brief(self) -> None:
        """D1-16 (4): the previous and today's snapshots, then today's reconciliation."""
        self.step(book_tool, PREV_EOD_NOW, "--snapshot", fixture_set="pre_open")
        self.step(book_tool, REGULAR_NOW, "--snapshot", fixture_set="regular")
        self.step(csv_recon_tool, REGULAR_NOW, fixture_set="regular")

    def tree(self) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(folder, name)
                with open(path, "rb") as handle:
                    found[os.path.relpath(path, self.root)] = hashlib.sha256(handle.read()).hexdigest()
        return found

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def write_json(self, rel: str, document: Any) -> None:
        self.write(rel, json.dumps(document, indent=2) + "\n")

    def read_json(self, rel: str) -> Any:
        with open(os.path.join(self.root, *rel.split("/")), encoding="utf-8") as handle:
            return json.load(handle)

    def heartbeat(self, routine_id: str, started: str, scheduled_et: str, exit_code: int = 0) -> None:
        moment = contract.parse_utc_instant(started)
        heartbeat.write_heartbeat(self.paths, routine_id, now=moment, exit_code=exit_code,
                                  status=contract.status_for(exit_code) if exit_code else "ok", started_utc=moment,
                                  finished_utc=moment, message_sha256=None, scheduled_et=scheduled_et,
                                  tool_version="0.1.0")

    def assertRelinted(self, payload: Dict[str, Any]) -> None:
        self.assertTrue(payload["lint"]["ok"], payload["lint"]["problems"])
        result = alert_lint.load_emitted(payload["emitted"]).check(payload["message"])
        self.assertTrue(result.ok, result.problems)


class SchemaAndUsageTest(DeskCopy):
    def test_schema_describes_the_payload(self) -> None:
        code, out = run_module(tool, ["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("eod_summary", body["tool"])
        self.assertEqual("none", body["network_access"])
        self.assertEqual(sorted(tool.PAYLOAD_KEYS), sorted(body["envelope"]["payload"]))
        self.assertEqual([], order_shaped(out))

    def test_usage_errors(self) -> None:
        for argv in (["--fixture-set", "regular"], ["--plan", WMT], ["--json", "--js"]):
            code, out = run_module(tool, ["--offline", "--desk-root", self.root, "--json"] + argv)
            self.assertEqual(2, code, argv)
            self.assertEqual("invalid_arguments", json.loads(out)["error"]["code"], argv)
        parser = tool.build_parser("0.1.0")
        flags = {option for action in parser._actions for option in action.option_strings}
        self.assertIn("--brief", flags)
        self.assertNotIn("--fixture-set", flags, "eod_summary never fetches, so it takes no fixture set")

    def test_desk_and_config_errors_are_exit_1(self) -> None:
        os.remove(self.paths.desk_toml)
        code, body = self.run_json()
        self.assertEqual((1, "config_missing"), (code, body["error"]["code"]))
        self.write("desk.toml", "[desk]\nmachine_role = \"owner_private\"\n")
        code, body = self.run_json("--brief")
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))
        shutil.copy(os.path.join(FIXTURE_DESK, "desk.toml"), self.paths.desk_toml)
        self.write("research/calendar.toml", "schema_version = 1\n[symbols.WMT]\nnot_a_key = 1\n")
        code, body = self.run_json()
        self.assertEqual((1, "calendar_invalid"), (code, body["error"]["code"]))
        code, out = run_module(tool, ["--offline", "--desk-root", os.path.join(self.root, "nope"), "--json"])
        self.assertEqual(2, code, "a desk root that does not exist is the parser's usage error")


class MorningSheetTest(DeskCopy):
    def test_a_bare_desk_is_unavailable_and_nothing_is_written(self) -> None:
        before = self.tree()
        code, out = self.run_text(now=REGULAR_NOW)
        self.assertEqual(20, code)
        self.assertEqual([
            "MORNING DESK SHEET 2026-11-03",
            "UNAVAILABLE: doctor result state/doctor.json missing",
            "UNAVAILABLE: reconciliation data/positions/2026-11-03.recon.json missing",
            "open signals: 0",
            "UNAVAILABLE: expiry docket reports/expiry/2026-11-03.week.json missing",
            "UNAVAILABLE: levels sheet levels/2026-11-02.json missing",
            alert_lint.footer_line(contract.parse_utc_instant(REGULAR_NOW)),
        ], out.rstrip("\n").split("\n"))
        self.assertEqual(before, self.tree(), "eod_summary writes nothing, not even a runtime directory")
        code, body = self.run_json(now=REGULAR_NOW)
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        self.assertEqual(["doctor result", "reconciliation", "expiry docket", "levels sheet"],
                         [item["label"] for item in body["payload"]["unavailable"]])
        self.assertRelinted(body["payload"])

    def test_the_staged_morning_sheet(self) -> None:
        self.stage_morning()
        before = self.tree()
        code, out = self.run_text()
        self.assertEqual(0, code, out)
        self.assertEqual(MORNING_SHEET, out.rstrip("\n").split("\n"))
        self.assertFalse([line for line in out.split("\n") if line.startswith("early close")],
                         "2026-11-03 is no early-close day (O17)")
        self.assertEqual(before, self.tree())
        code, body = self.run_json()
        self.assertEqual((0, "ok"), (code, body["status"]))
        payload = body["payload"]
        self.assertEqual(list(tool.PAYLOAD_KEYS), sorted(payload, key=tool.PAYLOAD_KEYS.index))
        self.assertEqual(out.rstrip("\n"), payload["message"])
        self.assertEqual(("morning", "2026-11-03", [], 0), (payload["mode"], payload["et_date"],
                                                            payload["unavailable"], payload["exit_code"]))
        self.assertEqual("2026-11-02T20:59:55+00:00", payload["emitted"]["as_of"], "the records' data instant")
        self.assertEqual(("tradier:sandbox", 15), (body["source"], body["delay_min"]))
        self.assertEqual([("doctor result", "state/doctor.json", "ok"),
                          ("reconciliation", "data/positions/2026-11-03.recon.json", "ok"),
                          ("expiry docket", "reports/expiry/2026-11-03.week.json", "ok"),
                          ("levels sheet", "levels/2026-11-02.json", "ok")],
                         [(item["label"], item["path"], item["status"]) for item in payload["artifacts"]])
        self.assertRelinted(payload)

    def test_late_records_and_a_corrupt_one(self) -> None:
        self.stage_morning()
        os.replace(os.path.join(self.root, "levels", "2026-11-02.json"), os.path.join(self.root, "levels", "2026-10-30.json"))
        record = self.read_json("state/doctor.json")
        self.write_json("state/doctor.json", dict(record, run_utc="2026-11-02T21:40:00+00:00"))
        self.write("reports/expiry/2026-11-03.week.json", "{not json")
        code, out = self.run_text()
        self.assertEqual(20, code)
        lines = out.split("\n")
        self.assertIn("UNAVAILABLE: doctor result is from 2026-11-02 (upstream late)", lines)
        self.assertIn("UNAVAILABLE: levels sheet is from 2026-10-30 (upstream late)", lines)
        self.assertIn("UNAVAILABLE: expiry docket reports/expiry/2026-11-03.week.json cannot be read (moved aside)",
                      lines)
        moved = [name for name in os.listdir(os.path.join(self.root, "reports", "expiry")) if ".corrupt-" in name]
        self.assertEqual(1, len(moved), "the state layer moved the corrupt record aside")

    def test_signals_and_a_partial_journal(self) -> None:
        self.stage_morning()
        self.step(book_check_tool, "2026-11-20T19:50:00Z", "--post", "--next-run-utc", "2026-11-20T21:35:00Z",
                  fixture_set="expiry_day", expected=(10,))
        with open(os.path.join(self.root, "journal", "signals.jsonl"), "a", encoding="utf-8") as handle:
            handle.write("{torn record\n")
        code, out = self.run_text()
        self.assertEqual(0, code, out)
        lines = out.split("\n")
        self.assertIn("open signals: 4", lines)
        self.assertIn("SIG-20261120-001 | AG-20261020-01 covered call | EXIT SIGNAL | your plan: close | first posted "
                      "2026-11-20 19:50 UTC | posted 1 of 4 times; next re-post 1 run after the first post | not "
                      "acknowledged", lines)
        self.assertIn("SIG-20261120-004 | portfolio all open groups | WATCH | your plan: review and decide | first "
                      "posted 2026-11-20 19:50 UTC | posted 1 time; no re-post ladder for WATCH | not acknowledged",
                      lines)
        self.assertIn(summaries.PARTIAL_JOURNAL_LINE % "journal/signals.jsonl", lines)

    def test_the_early_close_line_from_the_week_docket(self) -> None:
        self.stage_morning()
        day = "2026-11-27"
        act = "act by Fri 2026-11-27 13:00 ET (18:00 UTC) [today]; early close 13:00 ET: confirm your broker's cutoff"
        doctor_record = self.read_json("state/doctor.json")
        self.write_json("state/doctor.json", dict(doctor_record, run_utc="2026-11-27T12:31:00+00:00"))
        recon = self.read_json("data/positions/2026-11-03.recon.json")
        recon["generated_at"] = "2026-11-27T12:31:00+00:00"
        recon["payload"]["et_date"] = day
        self.write_json("data/positions/2026-11-27.recon.json", recon)
        docket = self.read_json("reports/expiry/2026-11-03.week.json")
        docket["generated_at"] = "2026-11-27T12:31:00+00:00"
        leg = {"trade_id": WMT, "group_id": WMT, "underlying": "WMT", "structure": "covered_call",
               "last_trading_day": day, "dte_trading": 0, "act_by_text": act, "act_by_passed": False}
        docket["payload"].update(today=day, legs=[leg])
        docket["payload"]["counts"].update(listed=1, unavailable=0)
        self.write_json("reports/expiry/2026-11-27.week.json", docket)
        levels = self.read_json("levels/2026-11-02.json")
        levels["et_date"] = "2026-11-25"
        self.write_json("levels/2026-11-25.json", levels)
        code, out = self.run_text(now="2026-11-27T12:35:00Z")
        self.assertEqual(0, code, out)
        lines = out.split("\n")
        self.assertEqual("expiry docket (reports/expiry/2026-11-27.week.json): 1 contract due", lines[4])
        self.assertEqual("AG-20261020-01 WMT covered call | last trading day 2026-11-27 (0 trading days) | " + act,
                         lines[5])
        self.assertEqual("early close today at 13:00 ET: 1 contract on its last trading day; " + act, lines[6])
        self.assertTrue(lines[7].startswith("last night's levels (levels/2026-11-25.json): "), lines[7])

    def test_a_failing_lint_prints_nothing_and_exits_1(self) -> None:
        self.stage_morning()
        original = summaries.morning_sheet

        def broken(inputs: summaries.MorningInputs) -> summaries.Summary:
            summary = original(inputs)
            summary.lines.append("expect 7.25 at the open")
            return summary

        with mock.patch.object(summaries, "morning_sheet", broken):
            code, body = self.run_json()
            self.assertEqual((1, "lint_failed"), (code, body["error"]["code"]))
            self.assertIn("number_not_emitted", body["error"]["message"])
            code, out = self.run_text()
            self.assertEqual(1, code)
            self.assertNotIn("7.25", out)


class EodBriefTest(DeskCopy):
    def test_the_staged_brief(self) -> None:
        self.stage_brief()
        before = self.tree()
        code, out = self.run_text("--brief", now=REGULAR_NOW)
        self.assertEqual(0, code, out)
        self.assertEqual(BRIEF, out.rstrip("\n").split("\n"))
        self.assertIn(PL_GOLDEN, out, "the D1-16 (4) golden")
        self.assertEqual(before, self.tree())
        code, body = self.run_json("--brief", now=REGULAR_NOW)
        self.assertEqual((0, "ok"), (code, body["status"]))
        payload = body["payload"]
        self.assertEqual(out.rstrip("\n"), payload["message"])
        self.assertEqual("brief", payload["mode"])
        self.assertEqual({"count": 0, "EXIT_SIGNAL": 0, "WATCH": 0, "UNAVAILABLE": 0}, payload["signals_today"])
        self.assertEqual(("lean", None, 4, 0, 4), (payload["routines"]["profile"], payload["routines"]["through_et"],
                                                   payload["routines"]["counts"]["expected"],
                                                   payload["routines"]["counts"]["ran"],
                                                   payload["routines"]["counts"]["missed"]))
        self.assertEqual(["desk_toml", "positions_csv", "rulebook"], [item["name"] for item in payload["config_changed"]])
        self.assertEqual(0, payload["calendar_findings"])
        self.assertEqual("2026-11-03T19:31:12+00:00", payload["emitted"]["as_of"])
        self.assertRelinted(payload)

    def test_a_bare_desk(self) -> None:
        code, out = self.run_text("--brief", now=REGULAR_NOW)
        self.assertEqual(20, code)
        lines = out.split("\n")
        self.assertEqual("UNAVAILABLE: book snapshot data/book/2026-11-03.json missing", lines[1])
        self.assertIn("UNAVAILABLE: reconciliation data/positions/2026-11-03.recon.json missing", lines)
        self.assertIn("expiry docket: none recorded today (reports/expiry/2026-11-03.week.json missing)", lines)

    def test_an_older_snapshot_is_upstream_late(self) -> None:
        self.step(book_tool, PREV_EOD_NOW, "--snapshot", fixture_set="pre_open")
        code, out = self.run_text("--brief", now=REGULAR_NOW)
        self.assertEqual(20, code)
        self.assertIn("UNAVAILABLE: book snapshot is from 2026-11-02 (upstream late)", out.split("\n"))

    def test_routines_counts_the_slots_through_the_briefs_own_and_never_itself(self) -> None:
        self.stage_brief()
        self.heartbeat("preopen", "2026-11-03T12:33:00Z", "07:30")
        self.heartbeat("book-check", "2026-11-03T15:36:00Z", "10:05")
        self.heartbeat("book-check", "2026-11-03T15:37:00Z", "10:05")
        self.heartbeat("eod-brief", "2026-11-03T22:11:00Z", "17:10")
        code, body = self.run_json("--brief", now=BRIEF_SLOT_NOW)
        self.assertEqual(0, code, body)
        lines = body["payload"]["message"].split("\n")
        self.assertIn("routines: 2 of 7 ran today; late: book-check 10:05 ET (31 min late); missed: book-check 12:05 ET, "
                      "book-check 14:05 ET, final-call 15:20 ET, book-check 15:35 ET; pending: eod 16:35 ET", lines)
        routines = body["payload"]["routines"]
        self.assertEqual("17:10", routines["through_et"])
        self.assertEqual({"expected": 7, "ran": 2, "late": 1, "missed": 4, "pending": 1}, routines["counts"])
        self.assertNotIn("eod-brief", body["payload"]["message"])

    def test_a_manifest_that_cannot_be_read_is_unavailable(self) -> None:
        self.stage_brief()
        with mock.patch.object(tool, "manifest_file", lambda: os.path.join(self.root, "no-manifest.toml")):
            code, out = self.run_text("--brief", now=REGULAR_NOW)
        self.assertEqual(20, code)
        self.assertIn("UNAVAILABLE: routine manifest routines/manifest.toml cannot be read (routine_audit.py --budget "
                      "shows why)", out.split("\n"))

    def test_expiry_day_signals_docket_and_to_do(self) -> None:
        self.step(book_check_tool, "2026-11-20T19:50:00Z", "--post", "--next-run-utc", "2026-11-20T21:35:00Z",
                  fixture_set="expiry_day", expected=(10,))
        self.step(book_tool, "2026-11-20T19:55:00Z", "--snapshot", fixture_set="expiry_day", expected=(20,))
        self.step(csv_recon_tool, "2026-11-20T19:55:00Z", fixture_set="expiry_day", expected=(0, 10, 20))
        self.step(expiry_tool, "2026-11-20T19:55:00Z", "--week", fixture_set="expiry_day", expected=(10, 20))
        code, body = self.run_json("--brief", now="2026-11-20T19:56:00Z")
        self.assertEqual(0, code, body)
        payload = body["payload"]
        lines = payload["message"].split("\n")
        self.assertIn("signals today: 4 (2 EXIT SIGNAL, 2 WATCH, 0 UNAVAILABLE)", lines)
        self.assertIn("open signals: 4", lines)
        self.assertTrue(lines[1].startswith("P/L (data/book/2026-11-20.json): realized today "), lines[1])
        self.assertIn("day P/L unavailable: no snapshot for 2026-11-19 (the desk's first day?)", lines[1])
        self.assertTrue(lines[2].startswith("AG-20261020-01 WMT covered call | unrealized unavailable (liquidation), "
                                            "unavailable (mid) | day unavailable | unavailable"), lines[2])
        docket_head = next(line for line in lines if line.startswith("expiry docket (reports/expiry/2026-11-20.week.json)"))
        self.assertTrue(docket_head.startswith("expiry docket (reports/expiry/2026-11-20.week.json): 2 contracts due"),
                        docket_head)
        todo = lines[-2]
        for signal_id in ("SIG-20261120-001", "SIG-20261120-002", "SIG-20261120-003", "SIG-20261120-004"):
            self.assertIn("%s not acknowledged (ack.py show %s)" % (signal_id, signal_id), todo)
        self.assertEqual({"count": 4, "EXIT_SIGNAL": 2, "WATCH": 2, "UNAVAILABLE": 0}, payload["signals_today"])
        self.assertRelinted(payload)


class ReviewFixTest(DeskCopy):
    def test_a_partial_total_names_the_group_it_leaves_out(self) -> None:
        """A real expiry_day snapshot: the WMT group is unavailable (call quote grade E), so the book's totals are the
        other groups' only; the P/L line says so (ids only, never the book's reason text)."""
        self.step(book_tool, "2026-11-20T19:56:00Z", "--snapshot", fixture_set="expiry_day", expected=(20,))
        snapshot = self.read_json("data/book/2026-11-20.json")
        self.assertIsNotNone(snapshot["portfolio"]["unrealized_pl_liq"], "the totals are non-null and partial")
        self.assertIn("AG-20261020-01 excluded from the portfolio totals: ", " | ".join(snapshot["warnings"]))
        self.step(csv_recon_tool, "2026-11-20T19:56:30Z", fixture_set="expiry_day", expected=(0, 10, 20))
        code, body = self.run_json("--brief", now="2026-11-20T19:57:00Z")
        lines = body["payload"]["message"].split("\n")
        self.assertTrue(lines[1].startswith("P/L (data/book/2026-11-20.json): "), lines[1])
        self.assertTrue(lines[1].endswith(" | totals without AG-20261020-01 (data unavailable)"), lines[1])
        self.assertNotIn("no usable mark (E fresh)", lines[1])
        self.assertRelinted(body["payload"])
        self.assertIn(code, (0, 20))

    def test_the_brief_names_today_s_incident_files_never_their_contents(self) -> None:
        self.stage_brief()
        for name, body in (("20261103T150500.000000Z-book-check-trigger_levels.md", "a traceback"),
                           ("20261102T150500.000000Z-book-check-trigger_levels.md", "yesterday's"),
                           ("20261103T200000.000000Z-" + "se" + "ll-now.md", "a file of another name"),
                           ("20261103T213500.000000Z-dirty-checkout.md", "the pin"),
                           ("20261103T213600.000000Z-eod-trigger_levels-withheld.md", "codes")):
            self.write("incidents/" + name, "# " + body + "\n")
        code, body = self.run_json("--brief", now=REGULAR_NOW)
        lines = body["payload"]["message"].split("\n")
        self.assertIn("incidents today: 4 (book-check trigger_levels, dirty checkout, eod trigger_levels withheld, 1 "
                      "other file); details in incidents/", lines)
        self.assertTrue(lines[-2].endswith("; incidents/: 4 files today"), lines[-2])
        self.assertNotIn("se" + "ll", body["payload"]["message"])
        self.assertNotIn("traceback", body["payload"]["message"])
        self.assertEqual([{"file": "incidents/20261103T150500.000000Z-book-check-trigger_levels.md",
                           "routine": "book-check", "what": "trigger_levels"},
                          {"file": None, "routine": None, "what": None},
                          {"file": "incidents/20261103T213500.000000Z-dirty-checkout.md", "routine": None,
                           "what": "dirty checkout"},
                          {"file": "incidents/20261103T213600.000000Z-eod-trigger_levels-withheld.md", "routine": "eod",
                           "what": "trigger_levels withheld"}], body["payload"]["incidents"])
        self.assertRelinted(body["payload"])
        self.assertEqual(0, code)
        code, morning = self.run_json(now=REGULAR_NOW)
        self.assertIsNone(morning["payload"]["incidents"], "the morning sheet reads no incident file")


if __name__ == "__main__":
    unittest.main()
