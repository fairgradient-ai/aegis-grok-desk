"""``aegis_desk.journal`` and ``scripts/signal_journal.py`` (roadmap 3.1, the owner's answer to open question 6: counts
only): each signal's facts from the desk's own records (the signal journal, the acknowledgements, the end-of-day
snapshots), the counts in fixed words, an UNAVAILABLE day, and ``--close``."""

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
from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple

import book as book_tool
import signal_journal as tool  # the tool scripts/signal_journal.py
from aegis_desk import alert_lint, desk_paths, journal, signals

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
UTC = datetime.timezone.utc
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
EOD_NOW = "2026-11-03T21:35:00Z"
JOURNAL_NOW = "2026-11-04T23:00:00Z"   # Wednesday 18:00 ET: Monday and Tuesday have snapshots, Wednesday none


def run_tool(module: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = module.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


def signal(trade_id: str, structure: str, verdict: str, rule_ids: Sequence[str], posted: str) -> Dict[str, Any]:
    return {"signal_id": None, "trade_id": trade_id, "structure": structure, "verdict": verdict,
            "reasons": [{"rule_id": rule_id, "rule_sha": "0" * 64, "result": "fired", "metric": "dte", "value": 13,
                         "threshold": 21, "unit": None, "verdict": verdict, "plan": "hold_review", "text": "x"}
                        for rule_id in rule_ids],
            "plan": "hold_review", "data_quality": "ok", "quote_age_at_post": 19, "run_lateness_min": 0,
            "posted_at": posted}


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-journal-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        desk_paths.ensure_runtime_dirs(self.paths)
        for fixture_set, now in (("pre_open", PREV_EOD_NOW), ("after_close", EOD_NOW)):
            code, _ = run_tool(book_tool, ["--snapshot", "--offline", "--fixture-set", fixture_set, "--desk-root",
                                           self.root, "--now", now])
            self.assertIn(code, (0, 20))
        self.first = signals.append_signal(self.paths, signal("AG-20261020-01", "covered_call", "WATCH", ["DTE21"],
                                                              "2026-11-02T19:50:00+00:00"))
        self.second = signals.append_signal(self.paths, signal("AG-20261022-02", "covered_call", "WATCH",
                                                               ["DTE21", "DELTA_WATCH"], "2026-11-03T19:50:00+00:00"))
        self.third = signals.append_signal(self.paths, signal("AG-20261028-03", "protective_put", "EXIT_SIGNAL",
                                                              ["MONETIZE"], "2026-11-03T20:05:00+00:00"))
        signals.append_record(self.paths.acks_jsonl, {"utc": "2026-11-02T20:10:00Z", "signal_id": self.first["signal_id"],
                                                      "trade_id": "AG-20261020-01", "action": "ack", "until": None,
                                                      "rows_sha": None, "hashes": None})
        signals.append_record(self.paths.acks_jsonl, {"utc": "2026-11-03T20:30:00Z", "signal_id": self.third["signal_id"],
                                                      "trade_id": "AG-20261028-03", "action": "acted", "until": None,
                                                      "rows_sha": None, "hashes": None})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_desk(self, *extra: str, now: str = JOURNAL_NOW, json_mode: bool = False) -> Tuple[int, str]:
        code, out = run_tool(tool, ["--desk-root", self.root, "--now", now] + list(extra) + (["--json"] if json_mode else []))
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        return code, out


class JournalTest(DeskCopy):
    def test_the_facts_and_the_counts(self) -> None:
        code, out = self.run_desk(json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual(("2026-11-02", "2026-11-04"), (payload["start"], payload["end"]))
        by_id = {item["signal_id"]: item for item in payload["signals"]}
        first = by_id[self.first["signal_id"]]
        self.assertEqual(("acknowledged", ["DTE21"]), (first["owner_mark"], first["rule_ids"]))
        self.assertEqual(["2026-11-02", "2026-11-03", "2026-11-04"], [mark["et_date"] for mark in first["marks"]])
        self.assertIsNotNone(first["marks"][0]["unrealized_pl_mid"])
        self.assertEqual((None, "no snapshot"), (first["marks"][2]["unrealized_pl_mid"], first["marks"][2]["reason"]))
        self.assertEqual("marked acted", by_id[self.third["signal_id"]]["owner_mark"])
        self.assertEqual("open", by_id[self.second["signal_id"]]["owner_mark"])
        self.assertEqual({"signals": 2, "acknowledged": 1, "acted": 0, "snoozed": 0}, payload["counts"]["DTE21"])
        self.assertEqual({"signals": 3, "acknowledged": 1, "acted": 1, "snoozed": 0}, payload["counts"]["all rules"])
        self.assertEqual(0, code, "today's missing snapshot is no gap yet")
        code, out = self.run_desk()
        lines = out.splitlines()
        self.assertIn("DTE21: 2 signals this week; 1 acknowledged, 0 marked acted, 0 snoozed", lines)
        self.assertIn("all rules: 3 signals this week; 1 acknowledged, 1 marked acted, 0 snoozed", lines)
        fact = next(line for line in lines if line.startswith(self.first["signal_id"]))
        self.assertTrue(fact.startswith("%s | AG-20261020-01 covered_call | WATCH (DTE21) posted 2026-11-02 19:50 UTC | "
                                        "acknowledged 2026-11-02 20:10 UTC | P/L at the mid, end of day: 2026-11-02 +$"
                                        % self.first["signal_id"]), fact)
        self.assertIn("2026-11-04 UNAVAILABLE (no snapshot)", fact)

    def test_money_only_on_fact_lines_and_the_text_passes_the_review_lint(self) -> None:
        _code, out = self.run_desk()
        for line in out.splitlines():
            if "$" in line:
                self.assertRegex(line, r"^SIG-\d{8}-\d{3} \| ", line)

    def test_a_past_trading_day_without_a_snapshot_is_unavailable(self) -> None:
        code, out = self.run_desk(now="2026-11-05T23:00:00Z", json_mode=True)
        self.assertEqual(20, code, "Wednesday 2026-11-04 has passed without a snapshot")
        self.assertEqual("unavailable", json.loads(out)["payload"]["verdict"])

    def test_one_day_and_usage(self) -> None:
        code, out = self.run_desk("--date", "2026-11-03", json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual(2, len(payload["signals"]))
        for argv in (["--date", "2026-11-05"], ["--date", "x"], ["--date", "2026-11-03", "--week"],
                     ["--close", "--week"]):
            self.assertEqual(2, self.run_desk(*argv)[0], argv)

    def test_close_writes_the_days_facts_and_posts_nothing(self) -> None:
        code, out = self.run_desk("--close", now="2026-11-03T22:30:00Z", json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual((0, None, "journal/outcomes/2026-11-03.json"), (code, payload["message"], payload["written"]))
        with open(os.path.join(self.root, "journal", "outcomes", "2026-11-03.json"), encoding="utf-8") as handle:
            closed = json.load(handle)
        self.assertEqual(sorted([self.second["signal_id"], self.third["signal_id"]]),
                         sorted(item["signal_id"] for item in closed["signals"]))

    def test_no_signal(self) -> None:
        os.remove(os.path.join(self.root, "journal", "signals.jsonl"))
        code, out = self.run_desk()
        self.assertEqual(0, code)
        self.assertIn("no signal was posted this week", out.splitlines())
        self.assertIn("all rules: 0 signals this week; 0 acknowledged, 0 marked acted, 0 snoozed", out.splitlines())


class LibraryTest(unittest.TestCase):
    def test_owner_marks_take_the_latest_record(self) -> None:
        marks = journal.owner_marks([{"signal_id": "SIG-20261103-001", "action": "snooze", "utc": "a"},
                                     {"signal_id": "SIG-20261103-001", "action": "ack", "utc": "b"},
                                     {"signal_id": "SIG-20261103-002", "action": "config", "utc": "c"}])
        self.assertEqual({"SIG-20261103-001": ("acknowledged", "b")}, marks)

    def test_count_line_words(self) -> None:
        self.assertEqual("TP50: 1 signal on 2026-11-03; 1 acknowledged, 0 marked acted, 0 snoozed",
                         journal.count_line("TP50", journal.Count(1, 1, 0, 0), "on 2026-11-03"))

    def test_a_closed_group_and_an_unreadable_snapshot(self) -> None:
        day = datetime.date(2026, 11, 3)
        record = signal("AG-1", "covered_call", "WATCH", ["TP50"], "2026-11-03T19:50:00+00:00")
        record["signal_id"] = "SIG-20261103-001"

        def reader(when: datetime.date) -> Tuple[str, Any]:
            return ("ok", {"groups": []}) if when == day else ("unreadable", None)

        facts = journal.facts([record], [], reader, day, datetime.date(2026, 11, 4))
        self.assertEqual([("closed: not in the snapshot", None), ("the snapshot does not read", None)],
                         [(mark.reason, mark.value) for mark in facts[0].marks])


if __name__ == "__main__":
    unittest.main()
