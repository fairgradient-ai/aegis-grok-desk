"""``aegis_desk.vol_context`` and ``scripts/vol_context.py``: the desk's own implied volatility per symbol against its
recorded history (roadmap step 2.9). The gate the roadmap sets: history from the desk's own snapshots only, and no
verdict reads the context (no module but the tool imports it)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import ast
import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, List, Sequence, Tuple

import vol_context as tool  # the tool scripts/vol_context.py
from aegis_desk import desk_paths
from aegis_desk import vol_context as V

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
NOW = "2026-11-03T21:00:00Z"
SPY_IVS = (0.15, 0.17, 0.16, 0.19, 0.18, 0.20, 0.17)


def leg(underlying: str, iv: Any, status: Any = "ok", instrument: str = "option") -> dict:
    return {"instrument_type": instrument, "underlying": underlying, "iv": iv, "iv_status": status}


def call(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = tool.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


class DeskCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-vol-context-")
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def snapshot(self, day: datetime.date, legs: List[dict]) -> None:
        os.makedirs(self.paths.book_dir, exist_ok=True)
        with open(os.path.join(self.paths.book_dir, "%s.json" % day.isoformat()), "w", encoding="utf-8") as handle:
            json.dump({"legs": legs, "underlyings": {}}, handle)

    def week(self) -> None:
        for index, iv in enumerate(SPY_IVS):
            self.snapshot(datetime.date(2026, 10, 26) + datetime.timedelta(days=index),
                          [leg("SPY", iv), leg("SPY", iv + 0.02), leg("SPY", iv - 0.02), leg("SPY", None, None, "etf"),
                           leg("WMT", 0.21, "expiring" if index == 6 else "ok")])

    def files(self) -> List[str]:
        found = []
        for folder, _dirs, names in os.walk(self.root):
            found.extend(os.path.relpath(os.path.join(folder, name), self.root) for name in names)
        return sorted(found)


class LibraryTest(DeskCase):
    def test_a_days_value_is_the_median_of_its_solved_option_legs(self) -> None:
        legs = [leg("SPY", 0.18), leg("SPY", 0.22), leg("SPY", 0.20), leg("SPY", 0.90, "expiring"),
                leg("SPY", None), leg("SPY", True), leg("SPY", -0.1), leg("SPY", 0.5, "ok", "etf"), leg("WMT", 0.3)]
        self.assertEqual({"SPY": 0.20, "WMT": 0.3}, V.symbol_ivs({"legs": legs}))
        self.assertEqual({}, V.symbol_ivs({}))

    def test_the_latest_day_against_the_earlier_ones(self) -> None:
        self.week()
        history = V.load_history(self.paths)
        self.assertEqual((7, datetime.date(2026, 11, 1)), (len(history.days), history.latest))
        spy = V.context("SPY", history)
        self.assertEqual((V.STATUS_OK, None, 6, 2), (spy.status, spy.reason, spy.history_days, spy.lower_days))
        self.assertAlmostEqual(0.17, spy.iv)
        self.assertAlmostEqual(100.0 / 3, spy.rank_pct)
        self.assertEqual((0.15, 0.20, datetime.date(2026, 10, 26)), (spy.low, spy.high, spy.first_day))
        # WMT's latest leg is expiring: no value on the latest day
        self.assertEqual((V.STATUS_UNAVAILABLE, V.REASON_NO_VALUE), (V.context("WMT", history).status,
                                                                      V.context("WMT", history).reason))
        self.assertEqual(["SPY"], [item.symbol for item in V.contexts(history)])
        self.assertEqual(4, len(V.load_history(self.paths, through=datetime.date(2026, 10, 29)).days))

    def test_too_little_history_and_no_snapshot_are_unavailable(self) -> None:
        self.assertEqual(V.REASON_NO_SNAPSHOTS, V.context("SPY", V.load_history(self.paths)).reason)
        for index in range(V.MIN_HISTORY_DAYS):  # the latest day plus one too few earlier days
            self.snapshot(datetime.date(2026, 10, 26) + datetime.timedelta(days=index), [leg("SPY", 0.2)])
        item = V.context("SPY", V.load_history(self.paths))
        self.assertEqual((V.STATUS_UNAVAILABLE, V.REASON_SHORT_HISTORY, V.MIN_HISTORY_DAYS - 1),
                         (item.status, item.reason, item.history_days))

    def test_an_unreadable_snapshot_is_skipped_and_named(self) -> None:
        self.week()
        os.makedirs(self.paths.book_dir, exist_ok=True)
        with open(os.path.join(self.paths.book_dir, "2026-11-02.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with open(os.path.join(self.paths.book_dir, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("ignored")
        history = V.load_history(self.paths)
        self.assertEqual((7, ("2026-11-02.json",)), (len(history.days), history.skipped))


class ToolTest(DeskCase):
    def test_the_text_and_the_exit_codes(self) -> None:
        code, out = call(["--desk-root", self.root, "--now", NOW])
        self.assertEqual(20, code)
        self.assertEqual("UNAVAILABLE | the book volatility context | the desk has written no end-of-day book snapshot "
                         "yet", out.split("\n")[0])
        self.week()
        code, out = call(["--desk-root", self.root, "--now", NOW])
        self.assertEqual(0, code, out)
        self.assertEqual(["VOL CONTEXT | SPY | implied volatility 17.0 % on 2026-11-01 | 2 of 6 earlier days lower (33 %) "
                          "| range 15.0 % to 20.0 % since 2026-10-26", tool.INFORMATION_LINE, tool.SOURCE_LINE, ""],
                         out.split("\n"))
        code, out = call(["--desk-root", self.root, "--now", NOW, "--symbol", "wmt"])
        self.assertEqual(20, code)
        self.assertTrue(out.startswith("UNAVAILABLE | WMT volatility context | the latest snapshot holds no option leg"))

    def test_the_json_payload_and_the_schema(self) -> None:
        self.week()
        code, out = call(["--desk-root", self.root, "--now", NOW, "--json"])
        body = json.loads(out)
        self.assertEqual((0, "ok", "vol_context"), (code, body["status"], body["tool"]))
        payload = body["payload"]
        self.assertEqual(set(tool.OUTPUT), set(payload))
        self.assertEqual(("ok", 7, "2026-11-01", 5, []), (payload["verdict"], payload["snapshots"], payload["latest"],
                                                          payload["min_history_days"], payload["skipped"]))
        self.assertEqual({"symbol": "SPY", "status": "ok", "lower_days": 2, "history_days": 6},
                         {k: payload["contexts"][0][k] for k in ("symbol", "status", "lower_days", "history_days")})
        code, out = call(["--schema"])
        schema = json.loads(out)
        self.assertEqual((0, "read-only", "none"), (code, schema["mode"], schema["network_access"]))
        self.assertEqual(set(tool.OUTPUT), set(schema["envelope"]["payload"]))

    def test_usage_errors_and_nothing_written(self) -> None:
        code, _out = call(["--desk-root", self.root, "--symbol", "not a symbol"])
        self.assertEqual(2, code)
        self.week()
        before = self.files()
        call(["--desk-root", self.root, "--now", NOW])
        call(["--desk-root", self.root, "--now", NOW, "--json"])
        self.assertEqual(before, self.files())


class NoVerdictReadsItTest(unittest.TestCase):
    """The roadmap's gate for step 2.9: volatility context never changes a verdict. No module of the desk imports
    ``aegis_desk.vol_context`` but the tool that prints it, so no rule, alert, ticket or ranking can read it."""

    ALLOWED = {os.path.join("scripts", "vol_context.py"), os.path.join("scripts", "aegis_desk", "vol_context.py"),
               os.path.join("scripts", "gen_tools_doc.py")}  # the doc generator reads every tool's --schema

    def importers(self) -> List[str]:
        found = []
        for folder, dirs, names in os.walk(os.path.join(ROOT, "scripts")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(folder, name)
                rel = os.path.relpath(path, ROOT)
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=rel)
                for node in ast.walk(tree):
                    names_in = []
                    if isinstance(node, ast.Import):
                        names_in = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names_in = [(node.module or "") + "." + alias.name for alias in node.names] + [node.module or ""]
                    if any(n.endswith("vol_context") for n in names_in) and rel not in self.ALLOWED:
                        found.append(rel)
        return sorted(set(found))

    def test_only_the_tool_imports_the_context(self) -> None:
        self.assertEqual([], self.importers())


if __name__ == "__main__":
    unittest.main()
