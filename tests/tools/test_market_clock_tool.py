"""``scripts/market_clock.py`` run in-process (``main(argv)``): sessions, DTE and cutoffs at fixed
instants, the table-expiry refusal (exit 20) and the usage errors. The tool reads no desk file, so a
run leaves the copied fixture desk byte-for-byte unchanged."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

from aegis_desk import market_clock as clock

import market_clock as tool  # the tool module scripts/market_clock.py, not the library

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"      # 14:50 EST, open
AFTER_CLOSE_NOW = "2026-11-03T21:25:00Z"  # 16:25 EST, closed
PRE_OPEN_NOW = "2026-11-03T13:00:00Z"     # 08:00 EST, pre
HOLIDAY_NOW = "2026-11-26T15:00:00Z"      # Thanksgiving
WEEKEND_NOW = "2026-11-07T15:00:00Z"      # Saturday
EARLY_CLOSE_NOW = "2026-11-27T17:00:00Z"  # 12:00 EST on the 13:00 ET close
EDT_NOW = "2026-10-30T18:05:00Z"          # 14:05 EDT
EXPIRED_NOW = "2028-01-05T15:00:00Z"      # past TABLE_EXPIRES


def run_tool(argv: Sequence[str]) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tool.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out, _ = run_tool(argv)
    return code, json.loads(out)


def usage_exit(argv: Sequence[str]) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            tool.main(list(argv))
        except SystemExit as exc:
            return int(exc.code), out.getvalue(), err.getvalue()
    raise AssertionError("expected a usage exit for %r" % (list(argv),))


def snapshot(root: str) -> List[Tuple[str, int]]:
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            found.append((os.path.relpath(path, root), os.path.getsize(path)))
    return sorted(found)


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-clock-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, *extra: str, json_mode: bool = True) -> List[str]:
        argv = list(extra) + ["--offline", "--desk-root", self.root]
        if json_mode:
            argv.append("--json")
        return argv


class SessionTest(DeskCopy):
    def test_regular_hours_with_expiry(self) -> None:
        before = snapshot(self.root)
        code, body = run_json(self.argv("--at", REGULAR_NOW, "--expiry", "2026-11-20"))
        self.assertEqual(0, code)
        self.assertEqual("market_clock", body["tool"])
        self.assertEqual("ok", body["status"])
        self.assertEqual("read-only", body["mode"])
        self.assertEqual("2026-11-03T19:50:00+00:00", body["generated_at"])
        self.assertIsNone(body["source"])
        self.assertIsNone(body["delay_min"])
        payload = body["payload"]
        expected_session = clock.session_at(datetime.datetime(2026, 11, 3, 19, 50, tzinfo=datetime.timezone.utc)).as_dict()
        for key, value in expected_session.items():
            self.assertEqual(value, payload[key], key)
        self.assertEqual("open", payload["session"])
        self.assertTrue(payload["trading_day"])
        self.assertEqual(70, payload["minutes_to_close"])
        self.assertEqual("2026-11-03T21:00:00+00:00", payload["close_utc"])
        self.assertEqual("2026-11-03T14:30:00+00:00", payload["open_utc"])
        self.assertEqual("2026-11-03", payload["et_date"])
        self.assertEqual("2026-11-03T14:50:00-05:00", payload["et"])
        self.assertEqual("EST", payload["et_zone"])
        self.assertEqual("2026-11-04", payload["next_trading_day"])
        self.assertEqual("2026-11-02T21:00:00+00:00", payload["previous_close_utc"])
        self.assertEqual("ok", payload["table_status"])
        self.assertEqual("equity", payload["product"])
        self.assertEqual({
            "expiry": "2026-11-20", "am_settled": False, "settlement_day": "2026-11-20", "last_trading_day": "2026-11-20",
            "dte_calendar": 17, "dte_trading": 13, "exercise_cutoff_utc": "2026-11-20T21:00:00+00:00",
            "exercise_cutoff_et": "2026-11-20T16:00:00-05:00", "calendar_coverage": "table",
        }, payload["expiry"])
        self.assertEqual(before, snapshot(self.root))  # a pure tool: nothing written under the desk root

    def test_prose_output(self) -> None:
        code, out, err = run_tool(self.argv("--at", REGULAR_NOW, "--expiry", "2026-11-20", json_mode=False))
        self.assertEqual(0, code)
        self.assertEqual("", err)
        self.assertIn("2026-11-03 14:50:00 ET (EST)", out)
        self.assertIn("2026-11-03T19:50:00+00:00", out)
        self.assertIn("session open", out)
        self.assertIn("16:00 ET in 70 min", out)
        self.assertIn("next trading day 2026-11-04", out)
        self.assertIn("holiday table ok", out)
        self.assertIn("expiry 2026-11-20 (PM-settled)", out)
        self.assertIn("last trading day 2026-11-20", out)
        self.assertIn("DTE 17 calendar, 13 trading", out)
        self.assertIn("exercise cutoff 2026-11-20T21:00:00+00:00 (2026-11-20 16:00 ET", out)
        for forbidden in ("order", "Order", "buy", "sell"):
            self.assertNotIn(forbidden, out)

    def test_after_close(self) -> None:
        code, body = run_json(self.argv("--at", AFTER_CLOSE_NOW))
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual("closed", payload["session"])
        self.assertIsNone(payload["minutes_to_close"])
        self.assertEqual("2026-11-03T21:00:00+00:00", payload["close_utc"])
        self.assertEqual("2026-11-03T21:00:00+00:00", payload["previous_close_utc"])
        self.assertIsNone(payload["expiry"])
        code, out, _ = run_tool(self.argv("--at", AFTER_CLOSE_NOW, json_mode=False))
        self.assertIn("session closed", out)
        self.assertIn("closed 16:00 ET, 25 min ago", out)
        self.assertNotIn("expiry", out)

    def test_pre_open(self) -> None:
        code, body = run_json(self.argv("--at", PRE_OPEN_NOW))
        self.assertEqual(0, code)
        self.assertEqual("pre", body["payload"]["session"])
        self.assertEqual(480, body["payload"]["minutes_to_close"])
        self.assertEqual("2026-11-02T21:00:00+00:00", body["payload"]["previous_close_utc"])
        code, out, _ = run_tool(self.argv("--at", PRE_OPEN_NOW, json_mode=False))
        self.assertIn("session pre", out)
        self.assertIn("opens 09:30 ET", out)

    def test_holiday_names_the_closure(self) -> None:
        code, body = run_json(self.argv("--at", HOLIDAY_NOW, "--expiry", "2026-11-26"))
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual("holiday", payload["session"])
        self.assertFalse(payload["trading_day"])
        self.assertEqual("Thanksgiving Day", payload["holiday_name"])
        self.assertIsNone(payload["close_utc"])
        self.assertEqual("2026-11-27", payload["next_trading_day"])
        self.assertEqual("2026-11-25T21:00:00+00:00", payload["previous_close_utc"])
        # A holiday-dated expiry settles on the preceding trading day.
        self.assertEqual("2026-11-25", payload["expiry"]["last_trading_day"])
        self.assertEqual("2026-11-25", payload["expiry"]["settlement_day"])
        self.assertEqual(-1, payload["expiry"]["dte_calendar"])
        self.assertEqual(0, payload["expiry"]["dte_trading"])  # no trading day lies in (Nov 25, Nov 26]
        code, out, _ = run_tool(self.argv("--at", HOLIDAY_NOW, json_mode=False))
        self.assertIn("session holiday (Thanksgiving Day)", out)
        self.assertIn("no session", out)

    def test_weekend(self) -> None:
        code, body = run_json(self.argv("--at", WEEKEND_NOW))
        self.assertEqual(0, code)
        self.assertEqual("weekend", body["payload"]["session"])
        self.assertIsNone(body["payload"]["holiday_name"])
        self.assertEqual("2026-11-09", body["payload"]["next_trading_day"])
        self.assertEqual("2026-11-06T21:00:00+00:00", body["payload"]["previous_close_utc"])
        code, out, _ = run_tool(self.argv("--at", WEEKEND_NOW, json_mode=False))
        self.assertIn("session weekend", out)

    def test_early_close(self) -> None:
        code, body = run_json(self.argv("--at", EARLY_CLOSE_NOW))
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual("early_close", payload["session"])
        self.assertEqual("Day after Thanksgiving", payload["holiday_name"])
        self.assertEqual(60, payload["minutes_to_close"])
        self.assertEqual("2026-11-27T18:00:00+00:00", payload["close_utc"])
        code, out, _ = run_tool(self.argv("--at", EARLY_CLOSE_NOW, json_mode=False))
        self.assertIn("session early_close (Day after Thanksgiving)", out)
        self.assertIn("13:00 ET in 60 min", out)

    def test_late_close_product(self) -> None:
        code, body = run_json(self.argv("--at", REGULAR_NOW, "--product", "late_close"))
        self.assertEqual(0, code)
        self.assertEqual("late_close", body["payload"]["product"])
        self.assertEqual(85, body["payload"]["minutes_to_close"])
        self.assertEqual("2026-11-03T21:15:00+00:00", body["payload"]["close_utc"])
        code, body = run_json(self.argv("--at", EARLY_CLOSE_NOW, "--product", "late_close"))
        self.assertEqual(75, body["payload"]["minutes_to_close"])

    def test_daylight_time_instant(self) -> None:
        code, body = run_json(self.argv("--at", EDT_NOW, "--expiry", "2026-11-20"))
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual("2026-10-30T14:05:00-04:00", payload["et"])
        self.assertEqual("EDT", payload["et_zone"])
        self.assertEqual("open", payload["session"])
        self.assertEqual(115, payload["minutes_to_close"])
        self.assertEqual("2026-10-30T20:00:00+00:00", payload["close_utc"])
        self.assertEqual(21, payload["expiry"]["dte_calendar"])
        self.assertEqual(15, payload["expiry"]["dte_trading"])
        code, out, _ = run_tool(self.argv("--at", EDT_NOW, json_mode=False))
        self.assertIn("2026-10-30 14:05:00 ET (EDT)", out)


class ExpiryTest(DeskCopy):
    def test_am_settled_moves_the_last_trading_day_and_the_cutoff(self) -> None:
        code, body = run_json(self.argv("--at", REGULAR_NOW, "--expiry", "2026-11-20", "--am-settled"))
        self.assertEqual(0, code)
        self.assertEqual({
            "expiry": "2026-11-20", "am_settled": True, "settlement_day": "2026-11-20", "last_trading_day": "2026-11-19",
            "dte_calendar": 16, "dte_trading": 12, "exercise_cutoff_utc": "2026-11-20T14:30:00+00:00",
            "exercise_cutoff_et": "2026-11-20T09:30:00-05:00", "calendar_coverage": "table",
        }, body["payload"]["expiry"])
        code, out, _ = run_tool(self.argv("--at", REGULAR_NOW, "--expiry", "2026-11-20", "--am-settled", json_mode=False))
        self.assertIn("expiry 2026-11-20 (AM-settled)", out)
        self.assertIn("last trading day 2026-11-19", out)
        self.assertIn("DTE 16 calendar, 12 trading", out)
        self.assertIn("09:30 ET", out)

    def test_early_close_expiry_cutoff_is_13_00_et(self) -> None:
        code, body = run_json(self.argv("--at", REGULAR_NOW, "--expiry", "2026-11-27"))
        self.assertEqual(0, code)
        self.assertEqual("2026-11-27T18:00:00+00:00", body["payload"]["expiry"]["exercise_cutoff_utc"])
        self.assertEqual("2026-11-27T13:00:00-05:00", body["payload"]["expiry"]["exercise_cutoff_et"])
        self.assertEqual("2026-11-27", body["payload"]["expiry"]["last_trading_day"])

    def test_saturday_dated_expiry_resolves_to_the_friday(self) -> None:
        code, body = run_json(self.argv("--at", REGULAR_NOW, "--expiry", "2026-11-21"))
        self.assertEqual(0, code)
        self.assertEqual("2026-11-20", body["payload"]["expiry"]["last_trading_day"])
        self.assertEqual(17, body["payload"]["expiry"]["dte_calendar"])

    def test_expiry_beyond_the_tables_is_labelled_rule_coverage(self) -> None:
        code, body = run_json(self.argv("--at", REGULAR_NOW, "--expiry", "2028-01-21"))
        self.assertEqual(0, code)
        self.assertEqual("rule", body["payload"]["expiry"]["calendar_coverage"])
        self.assertEqual("2028-01-21", body["payload"]["expiry"]["last_trading_day"])


class InstantResolutionTest(DeskCopy):
    def test_now_is_the_default_for_at(self) -> None:
        code, body = run_json(self.argv("--now", AFTER_CLOSE_NOW))
        self.assertEqual(0, code)
        self.assertEqual("closed", body["payload"]["session"])
        self.assertEqual("2026-11-03T21:25:00+00:00", body["payload"]["as_of_utc"])
        self.assertEqual("2026-11-03T21:25:00+00:00", body["generated_at"])

    def test_at_wins_over_now(self) -> None:
        code, body = run_json(self.argv("--at", REGULAR_NOW, "--now", AFTER_CLOSE_NOW))
        self.assertEqual(0, code)
        self.assertEqual("open", body["payload"]["session"])
        self.assertEqual("2026-11-03T19:50:00+00:00", body["payload"]["as_of_utc"])

    def test_wall_clock_when_neither_is_given(self) -> None:
        moment = datetime.datetime(2026, 11, 3, 15, 0, 0, tzinfo=datetime.timezone.utc)
        with mock.patch("aegis_desk.state.utc_now", return_value=moment):
            code, body = run_json(self.argv())
        self.assertEqual(0, code)
        self.assertEqual("2026-11-03T15:00:00+00:00", body["payload"]["as_of_utc"])
        self.assertEqual("open", body["payload"]["session"])


class RefusalAndUsageTest(DeskCopy):
    def test_expired_table_is_exit_20(self) -> None:
        code, body = run_json(self.argv("--at", EXPIRED_NOW))
        self.assertEqual(20, code)
        self.assertEqual("unavailable", body["status"])
        self.assertEqual("holiday_table_expired", body["error"]["code"])
        self.assertTrue(body["error"]["message"].startswith("holiday table expired"), body["error"]["message"])
        self.assertIn(clock.TABLE_EXPIRES.isoformat(), body["error"]["message"])
        self.assertNotIn("payload", body)
        code, out, err = run_tool(self.argv("--at", EXPIRED_NOW, json_mode=False))
        self.assertEqual(20, code)
        self.assertTrue(out.startswith("UNAVAILABLE: holiday table expired"), out)
        self.assertEqual("", err)

    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("market_clock", body["tool"])
        self.assertEqual("none", body["network_access"])
        payload = body["envelope"]["payload"]
        for key in ("session", "minutes_to_close", "et", "next_trading_day", "table_status", "expiry"):
            self.assertIn(key, payload)
        self.assertIn("dte_trading", payload["expiry"])

    def test_usage_errors(self) -> None:
        cases = [
            self.argv("--am-settled"),
            self.argv("--expiry", "2026-13-01"),
            self.argv("--expiry", "20261120"),
            self.argv("--expiry", "2026-11-2"),
            self.argv("--expiry", "2026-11-20T00:00:00Z"),
            self.argv("--at", "2026-11-03T19:50:00"),
            self.argv("--at", "2026-11-03T19:50:00+01:00"),
            self.argv("--at", "yesterday"),
            self.argv("--product", "futures"),
            self.argv("--now", "2026-11-03"),
            ["--json", "--offline"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                code, out, err = usage_exit(argv)
                self.assertEqual(2, code)
                body = json.loads(out)
                self.assertEqual(("usage", "invalid_arguments", "market_clock"),
                                 (body["status"], body["error"]["code"], body["tool"]))
                self.assertEqual("", err)
        code, out, err = usage_exit(self.argv("--am-settled", json_mode=False))
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("--am-settled", err)


if __name__ == "__main__":
    unittest.main()
