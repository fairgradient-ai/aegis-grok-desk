"""``aegis_desk.decision_calendar`` and ``scripts/decision_calendar.py`` (roadmap 2.11): the owner's dated decision
points in date order (act-by instants and last trading days of the open option legs, the held symbols' ex-dividend and
earnings dates with their sources), and a date nobody sourced as UNAVAILABLE."""

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
from typing import Sequence, Tuple

import decision_calendar as tool  # the tool scripts/decision_calendar.py
from aegis_desk import calendar_events, contract
from aegis_desk import decision_calendar as DC
from aegis_desk.positions_csv import load_positions

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
TEXT = [
    "DECISION CALENDAR | 2026-11-03 to 2026-12-18 | your dated decision points",
    "Tue 2026-11-17 | earnings | WMT earnings before the open (confirmed; owner-entered (fixture))",
    "Thu 2026-11-19 | earnings | NVDA earnings after the close (confirmed; owner-entered (fixture))",
    "Fri 2026-11-20 16:00 ET | act by | NVDA261120C00150000 short 2 (row 6, session close)",
    "Fri 2026-11-20 16:00 ET | act by | WMT261120C00105000 short 2 (row 4, session close)",
    "Fri 2026-11-20 | last trading day | NVDA261120C00150000 last trading day",
    "Fri 2026-11-20 | last trading day | WMT261120C00105000 last trading day",
    "Tue 2026-11-24 | ex-dividend | WMT ex-dividend, 0.2350 per share (confirmed; owner-entered (fixture))",
    "Thu 2026-12-03 | ex-dividend | NVDA ex-dividend, 0.01 per share (confirmed; owner-entered (fixture))",
    "Fri 2026-12-18 | ex-dividend | SPY ex-dividend, 1.85 per share (estimated; owner-entered (fixture))",
    "Fri 2026-12-18 16:30 ET | act by | SPY261218P00620000 long 1 (row 8, broker cutoff)",
    "Fri 2026-12-18 | last trading day | SPY261218P00620000 last trading day",
]


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = tool.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-decision-calendar-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.calendar_path = os.path.join(self.root, "research", "calendar.toml")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_desk(self, *extra: str, json_mode: bool = False) -> Tuple[int, str]:
        return run_tool(["--desk-root", self.root, "--now", REGULAR_NOW] + list(extra) + (["--json"] if json_mode else []))

    def rewrite_calendar(self, old: str, new: str) -> None:
        with open(self.calendar_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(old), old)
        with open(self.calendar_path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))


class ToolTest(DeskCopy):
    def test_schema_and_usage(self) -> None:
        code, out = run_tool(["--schema"])
        body = json.loads(out)
        self.assertEqual((0, "decision_calendar", "none"), (code, body["tool"], body["network_access"]))
        self.assertEqual(sorted(tool.OUTPUT), sorted(body["envelope"]["payload"]))
        for argv in (["--days", "0"], ["--days", "401"], ["--days", "x"]):
            self.assertEqual(2, self.run_desk(*argv)[0], argv)

    def test_the_regular_goldens_and_nothing_written(self) -> None:
        before = sorted(os.walk(self.root))
        code, out = self.run_desk()
        self.assertEqual((0, TEXT), (code, out.splitlines()))
        self.assertEqual(before, sorted(os.walk(self.root)))

    def test_json(self) -> None:
        code, out = self.run_desk("--days", "20", json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual((0, "ok", "2026-11-23", []), (code, payload["verdict"], payload["end"], payload["gaps"]))
        self.assertEqual(["earnings", "earnings", "act_by", "act_by", "last_trading_day", "last_trading_day"],
                         [point["kind"] for point in payload["points"]])
        self.assertEqual(("16:00", None), (payload["points"][2]["time_et"], payload["points"][4]["time_et"]))

    def test_an_unknown_date_is_unavailable_never_guessed(self) -> None:
        self.rewrite_calendar('earnings = { date = 2026-11-19, time = "post", status = "confirmed"',
                              'earnings = { status = "unknown"')
        code, out = self.run_desk()
        self.assertEqual(20, code)
        self.assertEqual("UNAVAILABLE | NVDA earnings date | research/calendar.toml holds it as unknown",
                         out.splitlines()[-1])
        self.assertNotIn("NVDA earnings after the close", out)

    def test_no_calendar_is_unavailable_for_every_held_symbol(self) -> None:
        os.remove(self.calendar_path)
        code, out = self.run_desk(json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual((20, 6), (code, len(payload["gaps"])))
        self.assertEqual({DC.REASON_NO_CALENDAR}, {gap["reason"] for gap in payload["gaps"]})
        self.assertEqual(6, len(payload["points"]), "the act-by points need no calendar")

    def test_desk_file_errors(self) -> None:
        os.remove(os.path.join(self.root, "positions.csv"))
        code, out = self.run_desk(json_mode=True)
        self.assertEqual((1, "positions_missing"), (code, json.loads(out)["error"]["code"]))
        os.remove(os.path.join(self.root, "desk.toml"))
        code, out = self.run_desk(json_mode=True)
        self.assertEqual((1, "config_missing"), (code, json.loads(out)["error"]["code"]))


class EngineTest(DeskCopy):
    def test_the_window_and_the_order(self) -> None:
        now = contract.parse_utc_instant(REGULAR_NOW)
        positions = load_positions(os.path.join(self.root, "positions.csv"), today=datetime.date(2026, 11, 3))
        cal = calendar_events.load_calendar(self.calendar_path)
        short = DC.build(positions.rows, cal, now, datetime.time(16, 30), horizon_days=14)
        self.assertEqual(((datetime.date(2026, 11, 3), datetime.date(2026, 11, 17)), ("WMT",)),
                         ((short.start, short.end), tuple(point.symbol for point in short.points)))
        full = DC.build(positions.rows, cal, now, datetime.time(16, 30))
        self.assertEqual(sorted(full.points, key=lambda point: point.date), list(full.points))
        self.assertTrue(all(point.status is None for point in full.points if point.kind == DC.KIND_ACT_BY))
        with self.assertRaises(ValueError):
            DC.build(positions.rows, cal, now, datetime.time(16, 30), horizon_days=0)


if __name__ == "__main__":
    unittest.main()
