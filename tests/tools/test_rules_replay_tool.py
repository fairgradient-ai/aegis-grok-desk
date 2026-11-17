"""``scripts/rules_replay.py`` (roadmap 3.2): a candidate rulebook replayed over the desk's end-of-day snapshots against
the adopted one. The adopted rulebook replayed against itself differs on no day; a candidate that waits until 10
trading days instead of 21 turns the WMT covered call's WATCH into HOLD on both days; a torn snapshot is UNAVAILABLE
for its day; nothing is written."""

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
from typing import Any, Dict, Sequence, Tuple

import book as book_tool
import rules_replay as tool  # the tool scripts/rules_replay.py
from aegis_desk import alert_lint

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
NOW = "2026-11-04T23:00:00Z"
LIMITS = ("limits: end-of-day snapshots only: an intraday crossing is invisible; each day is one closed session with a "
          "scratch rule state, so confirmations across days, re-arm and cooldown are not carried; no profit or loss is "
          "computed")


def run_tool(module: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = module.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-replay-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        for fixture_set, now in (("pre_open", "2026-11-02T21:30:00Z"), ("after_close", "2026-11-03T21:35:00Z")):
            run_tool(book_tool, ["--snapshot", "--offline", "--fixture-set", fixture_set, "--desk-root", self.root,
                                 "--now", now])
        rules = os.path.join(self.root, "rules")
        with open(os.path.join(rules, "exit_rules-1.0.0.toml"), encoding="utf-8") as handle:
            text = handle.read()
        self.same = os.path.join(rules, "candidate-1.0.0.toml")
        with open(self.same, "w", encoding="utf-8") as handle:
            handle.write(text)
        self.later = os.path.join(rules, "candidate-1.1.0.toml")
        changed = text.replace('rulebook_version = "1.0.0"', 'rulebook_version = "1.1.0"', 1).replace(
            '{ metric = "dte", op = "<=", value = 21, dte_unit = "trading" }',
            '{ metric = "dte", op = "<=", value = 10, dte_unit = "trading" }', 1)
        self.assertNotEqual(text, changed)
        with open(self.later, "w", encoding="utf-8") as handle:
            handle.write(changed)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def tree(self) -> Dict[str, int]:
        found = {}
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(folder, name)
                found[os.path.relpath(path, self.root)] = os.path.getsize(path)
        return found

    def run_desk(self, *extra: str, json_mode: bool = False) -> Tuple[int, str]:
        code, out = run_tool(tool, ["--desk-root", self.root, "--now", NOW] + list(extra)
                             + (["--json"] if json_mode else []))
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        return code, out


class ReplayTest(DeskCopy):
    def test_the_adopted_rulebook_against_itself_differs_on_no_day(self) -> None:
        code, out = self.run_desk("--candidate", self.same)
        self.assertEqual((0, ["RULES REPLAY | candidate rules 1.0.0 against your adopted rules 1.0.0 | 2 end-of-day "
                              "snapshots", "Mon 2026-11-02 | no verdict differs", "Tue 2026-11-03 | no verdict differs",
                              "verdict differed from your adopted rule on 0 of 2 trading days", LIMITS]),
                         (code, out.splitlines()))

    def test_a_later_threshold_changes_two_verdicts_on_both_days_and_writes_nothing(self) -> None:
        before = self.tree()
        code, out = self.run_desk("--candidate", self.later)
        lines = out.splitlines()
        self.assertEqual(10, code)
        self.assertEqual(["Mon 2026-11-02 | AG-20261020-01 WMT covered_call: WATCH -> HOLD",
                          "Tue 2026-11-03 | AG-20261020-01 WMT covered_call: WATCH -> HOLD",
                          "verdict differed from your adopted rule on 2 of 2 trading days (2026-11-02, 2026-11-03)"],
                         lines[1:4], "the NVDA covered call stays WATCH by another rule")
        self.assertEqual(before, self.tree())
        code, out = self.run_desk("--candidate", self.later, json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual(("differs", {"days": 2, "days_differed": 2, "days_unavailable": 0}, "1.1.0"),
                         (payload["verdict"], payload["counts"], payload["candidate"]["version"]))
        self.assertEqual(out and payload["message"], "\n".join(lines))
        self.assertNotIn("$", payload["message"], "no money figure on a replay verdict")

    def test_the_range_and_a_torn_snapshot(self) -> None:
        code, out = self.run_desk("--candidate", self.later, "--days", "1")
        self.assertEqual(["Tue 2026-11-03 | AG-20261020-01 WMT covered_call: WATCH -> HOLD"], out.splitlines()[1:2])
        code, out = self.run_desk("--candidate", self.later, "--from", "2026-11-02", "--to", "2026-11-02")
        self.assertIn("verdict differed from your adopted rule on 1 of 1 trading days (2026-11-02)", out.splitlines())
        with open(os.path.join(self.root, "data", "book", "2026-11-03.json"), "w", encoding="utf-8") as handle:
            handle.write("{torn")
        code, out = self.run_desk("--candidate", self.same, json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual((20, "unavailable"), (code, payload["verdict"]))
        self.assertEqual(["ok", "unavailable"], [day["status"] for day in payload["days"]])

    def test_no_snapshot_and_usage(self) -> None:
        shutil.rmtree(os.path.join(self.root, "data", "book"))
        code, out = self.run_desk("--candidate", self.same)
        self.assertEqual(20, code)
        self.assertIn("UNAVAILABLE | no end-of-day snapshot under data/book/ in the range", out)
        for argv in ([], ["--candidate", self.same, "--days", "0"], ["--candidate", self.same, "--days", "2", "--from",
                                                                       "2026-11-02"],
                     ["--candidate", self.same, "--to", "2026-11-02"],
                     ["--candidate", self.same, "--from", "2026-11-03", "--to", "2026-11-02"]):
            self.assertEqual(2, self.run_desk(*argv)[0], argv)
        code, out = self.run_desk("--candidate", os.path.join(self.root, "missing.toml"), json_mode=True)
        self.assertEqual(1, code)


if __name__ == "__main__":
    unittest.main()
