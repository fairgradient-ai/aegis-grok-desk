"""``scripts/scenario.py`` (roadmap 2.8): the book re-priced on the proposed grid, and an event stress of one symbol,
run in-process on a copy of the fixture desk. The book is marked as ``book.py`` marks it (one quotes request); nothing
is written but the quote cache."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Dict, Sequence, Tuple

import scenario as tool  # the tool scripts/scenario.py
from aegis_desk import alert_lint

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
GRID_TEXT = [
    "SCENARIO | the book re-priced, as of 14:31:12 ET | a computation on the book's model of record, not a forecast",
    "P/L change | spot -10% | vol -5 -7308.50 | vol +0 -7055.59 | vol +5 -6786.36",
    "P/L change | spot -5% | vol -5 -3936.33 | vol +0 -3649.39 | vol +5 -3378.53",
    "P/L change | spot -2% | vol -5 -1678.57 | vol +0 -1465.82 | vol +5 -1249.81",
    "P/L change | spot +0% | vol -5 -148.12 | vol +0 +0.00 | vol +5 +172.16",
    "P/L change | spot +2% | vol -5 +1361.52 | vol +0 +1451.62 | vol +5 +1581.97",
    "P/L change | spot +5% | vol -5 +3547.97 | vol +0 +3579.44 | vol +5 +3660.93",
    "P/L change | spot +10% | vol -5 +6993.51 | vol +0 +6990.26 | vol +5 +7026.98",
    "AG-20261020-01 covered_call WMT | spot -10%: stock -2084.60, options +594.50, net -1490.10 | spot +10%: stock "
    "+2084.60, options -1438.99, net +645.61",
    "AG-20261022-02 covered_call NVDA | spot -10%: stock -2978.50, options +618.07, net -2360.43 | spot +10%: stock "
    "+2978.50, options -2236.22, net +742.28",
    "AG-20261028-03 protective_put SPY | spot -10%: stock -6481.20, options +3276.14, net -3205.06 | spot +10%: stock "
    "+6481.20, options -878.83, net +5602.37",
    "grid: spot -10 to +10 %, volatility -5 to +5 points (the desk's proposed grid); risk-free rate 4.12 %; each "
    "symbol's sourced dividend yield",
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
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-scenario-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def offline(self, *extra: str, json_mode: bool = False, fixture_set: str = "regular") -> Tuple[int, str]:
        code, out = run_tool(["--offline", "--desk-root", self.root, "--now", REGULAR_NOW, "--fixture-set", fixture_set]
                             + list(extra) + (["--json"] if json_mode else []))
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        return code, out

    def tree(self) -> Dict[str, int]:
        found = {}
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                rel = os.path.relpath(os.path.join(dirpath, name), self.root)
                if not rel.startswith(os.path.join("data", "cache")) and rel != os.path.join("state", ".lock"):
                    found[rel] = os.path.getsize(os.path.join(dirpath, name))
        return found


class SchemaAndUsageTest(DeskCopy):
    def test_schema(self) -> None:
        code, out = run_tool(["--schema"])
        body = json.loads(out)
        self.assertEqual((0, "scenario"), (code, body["tool"]))
        self.assertEqual(sorted(tool.OUTPUT), sorted(body["envelope"]["payload"]))
        self.assertIn("/v2/stocks/quotes/latest", body["network_access"])

    def test_usage_errors(self) -> None:
        for argv in (["--event", "earnings"], ["--event", "split", "--symbol", "WMT"], ["--symbol", "W M T"],
                     ["--fixture-set", "regular"]):
            code, _ = run_tool(argv + ["--desk-root", self.root, "--json"])
            self.assertEqual(2, code, argv)


class OfflineTest(DeskCopy):
    def test_the_grid_goldens_and_nothing_written(self) -> None:
        before = self.tree()
        code, out = self.offline()
        self.assertEqual((0, GRID_TEXT), (code, out.splitlines()))
        self.assertEqual(before, self.tree(), "only the quote cache changes")

    def test_json(self) -> None:
        code, out = self.offline(json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual((0, "ok", 21, [], None), (code, payload["verdict"], len(payload["cells"]), payload["excluded"],
                                                   payload["reason"]))
        zero = next(cell for cell in payload["cells"] if (cell["spot_pct"], cell["vol_points"]) == (0, 0))
        self.assertEqual(("0.00", "4097.00"), (zero["change"], zero["stressed_pl_mid"]), "equal to the book")
        self.assertIsNone(payload["horizon"]["utc"])
        self.assertTrue(any("day P/L unavailable" in warning for warning in payload["warnings"]))

    def test_an_earnings_stress(self) -> None:
        code, out = self.offline("--event", "earnings", "--symbol", "wmt")
        lines = out.splitlines()
        self.assertEqual(0, code)
        self.assertEqual("EVENT STRESS | WMT earnings 2026-11-17 (pre) | priced at the 16:00 ET close of 2026-11-18, one "
                         "trading day after | a computation on the book's model of record, not a forecast", lines[0])
        self.assertTrue(lines[-3].startswith("AG-20261020-01 covered_call WMT | spot -10%"), lines[-3])
        code, out = self.offline("--event", "earnings", "--symbol", "WMT", json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual(("earnings", "2026-11-18", ["AG-20261020-01"]),
                         (payload["horizon"]["event"], payload["horizon"]["horizon_date"], payload["stressed"]))

    def test_unavailable_answers_are_exit_20(self) -> None:
        code, out = self.offline("--event", "earnings", "--symbol", "SPY")
        self.assertEqual((20, "UNAVAILABLE | event stress SPY | research/calendar.toml says SPY has no earnings date"),
                         (code, out.splitlines()[0]))
        code, out = self.offline("--symbol", "AAPL", json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual((20, "unavailable", "no open group on AAPL in the book"),
                         (code, payload["verdict"], payload["reason"]))
        shutil.rmtree(os.path.join(self.root, "data", "cache"))  # a fresh cache: the regular quotes are not served
        code, out = self.offline(fixture_set="stale")
        self.assertEqual(20, code)
        self.assertIn("excluded | AG-20261020-01 | the book marks it unavailable", out.splitlines())
        self.assertIn("P/L change | spot +0% | vol -5 - | vol +0 - | vol +5 -", out.splitlines())

    def test_upcoming_events_are_stressed_or_quiet(self) -> None:
        code, out = self.offline("--upcoming", "2")
        self.assertEqual((0, "quiet: no held symbol has an earnings or ex-dividend date within 2 trading days"),
                         (code, out.rstrip("\n")))
        code, out = self.offline("--upcoming", "2", json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual((0, "quiet", None, []), (code, payload["verdict"], payload["message"], payload["stresses"]))
        code, out = run_tool(["--offline", "--desk-root", self.root, "--now", "2026-11-19T19:50:00Z", "--fixture-set",
                              "t1", "--upcoming", "2", "--json"])
        payload = json.loads(out)["payload"]
        self.assertEqual((10, "stressed", ["NVDA"]), (code, payload["verdict"],
                                                      [item["horizon"]["symbol"] for item in payload["stresses"]]))
        self.assertTrue(payload["message"].startswith("EVENT STRESS | NVDA earnings 2026-11-19 (post)"))
        self.assertEqual("2026-11-19T19:31:12+00:00", payload["as_of"])
        for argv in (["--upcoming", "0"], ["--upcoming", "11"], ["--upcoming", "2", "--symbol", "WMT"]):
            self.assertEqual(2, run_tool(argv + ["--desk-root", self.root, "--json"])[0], argv)

    def test_from_snapshot_stresses_the_last_close_and_sends_nothing(self) -> None:
        code, out = run_tool(["--from-snapshot", "--desk-root", self.root, "--now", "2026-11-07T15:00:00Z", "--json"])
        self.assertEqual((20, "snapshot_missing"), (code, json.loads(out)["error"]["code"]))
        import book as book_tool
        with contextlib.redirect_stdout(io.StringIO()):
            book_tool.main(["--snapshot", "--offline", "--fixture-set", "pre_open", "--desk-root", self.root,
                            "--now", "2026-11-02T21:30:00Z"])
        ledger = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        before = os.path.getsize(ledger) if os.path.exists(ledger) else 0
        code, out = run_tool(["--from-snapshot", "--desk-root", self.root, "--now", "2026-11-07T15:00:00Z"])
        lines = out.splitlines()
        self.assertEqual(0, code, out)
        self.assertEqual("SCENARIO | the book re-priced, the end-of-day snapshot of 2026-11-02, as of close 16:00 ET | "
                         "a computation on the book's model of record, not a forecast", lines[0])
        self.assertEqual("P/L change | spot +0% | vol -5 -158.39 | vol +0 +0.00 | vol +5 +179.46", lines[4])
        self.assertEqual(before, os.path.getsize(ledger) if os.path.exists(ledger) else 0, "no request")
        self.assertEqual(2, run_tool(["--from-snapshot", "--offline", "--fixture-set", "regular", "--desk-root",
                                      self.root, "--json"])[0])

    def test_an_unusable_positions_file_is_exit_1(self) -> None:
        os.remove(os.path.join(self.root, "positions.csv"))
        code, out = self.offline(json_mode=True)
        self.assertEqual((1, "positions_missing"), (code, json.loads(out)["error"]["code"]))


if __name__ == "__main__":
    unittest.main()
