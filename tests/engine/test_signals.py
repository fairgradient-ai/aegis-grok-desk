"""Signals and alerts journals (``aegis_desk.signals``): ``SIG-<ET date>-<NNN>`` ids numbered per America/New_York day
from ``journal/signals.jsonl`` (addendum decision 5), never reused (a quarantined journal still counts), records with
exactly the plan's keys appended under the journal's own lock, ``journal/alerts.jsonl`` plus the day's Markdown
journal, open signals read against ``state/acks.jsonl`` records, and the re-post ladder."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import glob
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional

from aegis_desk import desk_paths, signals, state

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULE_PATH = os.path.join(ROOT, "scripts", "aegis_desk", "signals.py")
UTC = datetime.timezone.utc
POSTED = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)


def record(posted_at: datetime.datetime = POSTED, trade_id: str = "AG-20261020-01", verdict: str = "EXIT_SIGNAL",
           signal_id: Optional[str] = None) -> Dict[str, Any]:
    return {"signal_id": signal_id, "trade_id": trade_id, "structure": "covered_call", "verdict": verdict,
            "reasons": [{"rule_id": "TP80", "value": Decimal("95.32"), "text": "credit captured 95.32 %"}],
            "plan": "close", "data_quality": "unavailable", "quote_age_at_post": 18, "run_lateness_min": 0,
            "posted_at": posted_at}


def ack(action: str, signal_id: Optional[str], until: Optional[str] = None) -> Dict[str, Any]:
    return {"utc": "2026-11-20T20:00:00+00:00", "signal_id": signal_id, "trade_id": None, "action": action,
            "until": until, "rows_sha": None, "hashes": None}


class DeskCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="aegis-signals-test-")
        root = os.path.join(os.path.realpath(self._tmp), "desk")
        os.makedirs(root)
        self.paths = desk_paths.for_root(root)
        desk_paths.ensure_runtime_dirs(self.paths)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def journal_lines(self, name: str) -> List[Dict[str, Any]]:
        with open(os.path.join(self.paths.journal_dir, name), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]


class IdTest(DeskCase):
    def test_format_and_parse(self) -> None:
        self.assertEqual("SIG-20261120-001", signals.format_signal_id(datetime.date(2026, 11, 20), 1))
        self.assertEqual("SIG-20261120-999", signals.format_signal_id(datetime.date(2026, 11, 20), 999))
        self.assertEqual((datetime.date(2026, 11, 20), 7), signals.parse_signal_id("SIG-20261120-007"))
        for bad in ("SIG-20261120-07", "SIG-20261320-001", "sig-20261120-001", "SIG-20261120-000", " SIG-20261120-001",
                    None, 7):
            with self.subTest(bad=bad):
                self.assertIsNone(signals.parse_signal_id(bad))
        for number in (0, 1000, True):
            with self.assertRaises(ValueError):
                signals.format_signal_id(datetime.date(2026, 11, 20), number)

    def test_first_id_of_a_day(self) -> None:
        self.assertEqual("SIG-20261120-001", signals.next_signal_id(self.paths, datetime.date(2026, 11, 20)))

    def test_ids_are_numbered_per_et_day(self) -> None:
        first = signals.append_signal(self.paths, record())
        second = signals.append_signal(self.paths, record(trade_id="AG-20261022-02"))
        evening = signals.append_signal(self.paths, record(datetime.datetime(2026, 11, 21, 3, 0, tzinfo=UTC)))
        next_day = signals.append_signal(self.paths, record(datetime.datetime(2026, 11, 21, 15, 0, tzinfo=UTC)))
        self.assertEqual(["SIG-20261120-001", "SIG-20261120-002", "SIG-20261120-003", "SIG-20261121-001"],
                         [item["signal_id"] for item in (first, second, evening, next_day)])
        self.assertEqual("SIG-20261120-004", signals.next_signal_id(self.paths, datetime.date(2026, 11, 20)))
        self.assertEqual("SIG-20261121-002", signals.next_signal_id(self.paths, datetime.date(2026, 11, 21)))

    def test_next_is_the_highest_number_plus_one(self) -> None:
        signals.append_signal(self.paths, record(signal_id="SIG-20261120-001"))
        signals.append_signal(self.paths, record(signal_id="SIG-20261120-007"))
        self.assertEqual("SIG-20261120-008", signals.next_signal_id(self.paths, datetime.date(2026, 11, 20)))
        self.assertEqual("SIG-20261120-008", signals.append_signal(self.paths, record())["signal_id"])

    def test_the_day_is_capped(self) -> None:
        signals.append_signal(self.paths, record(signal_id="SIG-20261120-999"))
        with self.assertRaises(signals.SignalJournalError):
            signals.next_signal_id(self.paths, datetime.date(2026, 11, 20))
        with self.assertRaises(signals.SignalJournalError):
            signals.append_signal(self.paths, record())

    def test_explicit_ids_are_checked(self) -> None:
        signals.append_signal(self.paths, record(signal_id="SIG-20261120-001"))
        with self.assertRaises(signals.SignalJournalError):
            signals.append_signal(self.paths, record(signal_id="SIG-20261120-001"))
        with self.assertRaises(ValueError):
            signals.append_signal(self.paths, record(signal_id="SIG-20261121-002"))  # not the ET date of posted_at
        with self.assertRaises(ValueError):
            signals.append_signal(self.paths, record(signal_id="SIG-2026112-002"))

    def test_a_bad_line_never_lets_an_id_be_reused(self) -> None:
        signals.append_signal(self.paths, record())
        with open(signals.signals_path(self.paths), "a", encoding="utf-8") as handle:
            handle.write("{not json}\n")
        self.assertEqual("SIG-20261120-002", signals.next_signal_id(self.paths, datetime.date(2026, 11, 20)))
        self.assertEqual([], glob.glob(signals.signals_path(self.paths) + state.QUARANTINE_SUFFIX + "*"),
                         "an append-only journal is never moved aside for one bad line (the lens-2 review)")
        self.assertEqual("SIG-20261120-002", signals.append_signal(self.paths, record())["signal_id"])
        self.assertEqual("SIG-20261120-003", signals.append_signal(self.paths, record())["signal_id"])

    def test_an_id_legible_in_a_bad_line_or_an_old_quarantined_copy_is_never_reused(self) -> None:
        with open(signals.signals_path(self.paths), "a", encoding="utf-8") as handle:
            handle.write('{"signal_id": "SIG-20261120-004", "trade_id": \n')
        with open(signals.signals_path(self.paths) + state.QUARANTINE_SUFFIX + "20261104T000000Z", "w",
                  encoding="utf-8") as handle:
            handle.write('{"signal_id": "SIG-20261120-007"}\n')
        self.assertEqual("SIG-20261120-008", signals.next_signal_id(self.paths, datetime.date(2026, 11, 20)))



class TolerantJournalTest(DeskCase):
    """A bad line in an append-only journal is skipped and reported; the journal is never quarantined by a read (so
    a dry run, ack.py list or ack.py show writes nothing), and a record appended after a torn line stays readable."""

    def test_a_bad_line_is_skipped_and_reported_and_the_file_stays(self) -> None:
        first = signals.append_signal(self.paths, record())
        with open(signals.signals_path(self.paths), "a", encoding="utf-8") as handle:
            handle.write('{"broken": \n')
        second = signals.append_signal(self.paths, record(trade_id="AG-20261022-02"))
        records, status = signals.read_signals(self.paths)
        self.assertEqual(([first["signal_id"], second["signal_id"]], signals.STATUS_PARTIAL),
                         ([item["signal_id"] for item in records], status))
        self.assertTrue(os.path.exists(signals.signals_path(self.paths)))
        self.assertEqual([], glob.glob(signals.signals_path(self.paths) + state.QUARANTINE_SUFFIX + "*"))
        self.assertEqual(2, len(signals.open_signals(self.paths, [])), "the open signals survive one bad line")
        lines, status, bad = signals.read_journal(signals.signals_path(self.paths))
        self.assertEqual((2, signals.STATUS_PARTIAL, [2]), (len(lines), status, [number for number, _text in bad]))

    def test_a_record_appended_after_a_torn_line_is_kept(self) -> None:
        signals.append_signal(self.paths, record())
        with open(signals.signals_path(self.paths), "a", encoding="utf-8") as handle:
            handle.write('{"signal_id": "SIG-2026')  # a crash between the write and its newline
        later = signals.append_signal(self.paths, record(trade_id="AG-20261022-02"))
        signals.append_alert(self.paths, later["signal_id"], "text", True, posted_at=POSTED)
        records, _status = signals.read_signals(self.paths)
        self.assertIn(later["signal_id"], [item["signal_id"] for item in records])

    def test_parse_signal_id_refuses_a_trailing_newline(self) -> None:
        self.assertIsNone(signals.parse_signal_id("SIG-20261120-001\n"))
        self.assertIsNone(signals.parse_signal_id("SIG-20261120-00\u0661"))


class RecordTest(DeskCase):
    def test_the_record_on_disk_has_exactly_the_plan_keys(self) -> None:
        stored = signals.append_signal(self.paths, record())
        self.assertEqual(signals.SIGNAL_KEYS, tuple(stored))
        self.assertEqual(("signal_id", "trade_id", "structure", "verdict", "reasons", "plan", "data_quality",
                          "quote_age_at_post", "run_lateness_min", "posted_at"), signals.SIGNAL_KEYS)
        [line] = self.journal_lines(signals.SIGNALS_FILE)
        self.assertEqual(set(signals.SIGNAL_KEYS), set(line))
        self.assertEqual("SIG-20261120-001", line["signal_id"])
        self.assertEqual("2026-11-20T19:50:00+00:00", line["posted_at"])
        self.assertEqual("95.32", line["reasons"][0]["value"])
        records, status = signals.read_signals(self.paths)
        self.assertEqual(("ok", 1), (status, len(records)))

    def test_bad_records_are_refused(self) -> None:
        cases = []
        missing = record()
        del missing["plan"]
        cases.append(missing)
        cases.append(dict(record(), extra=1))
        cases.append(dict(record(), verdict="HOLD"))
        cases.append(dict(record(), posted_at=POSTED.replace(tzinfo=None)))
        cases.append(dict(record(), posted_at="yesterday"))
        cases.append(dict(record(), reasons="TP80"))
        cases.append(dict(record(), trade_id=""))
        cases.append(dict(record(), quote_age_at_post=-1))
        cases.append(dict(record(), run_lateness_min=True))
        cases.append(dict(record(), plan="unknown_plan"))
        for bad in cases:
            with self.subTest(bad=sorted(bad.items(), key=str)[:2]):
                with self.assertRaises(ValueError):
                    signals.append_signal(self.paths, bad)
        self.assertFalse(os.path.exists(signals.signals_path(self.paths)))

    def test_posted_at_may_be_an_iso_string(self) -> None:
        stored = signals.append_signal(self.paths, record(posted_at="2026-11-20T19:50:00+00:00"))  # type: ignore[arg-type]
        self.assertEqual("SIG-20261120-001", stored["signal_id"])

    def test_the_journal_lock_is_not_the_desk_lock(self) -> None:
        with state.locked(self.paths.lock_path):
            stored = signals.append_signal(self.paths, record())
            signals.append_alert(self.paths, stored["signal_id"], "text\nfooter", True, posted_at=POSTED)
        self.assertEqual(1, len(self.journal_lines(signals.ALERTS_FILE)))

    def test_the_journal_directory_is_created(self) -> None:
        shutil.rmtree(self.paths.journal_dir)
        self.assertEqual("SIG-20261120-001", signals.append_signal(self.paths, record())["signal_id"])


class AlertJournalTest(DeskCase):
    def test_alert_records_and_the_day_journal(self) -> None:
        stored = signals.append_alert(self.paths, "SIG-20261120-001", "EXIT SIGNAL AG-20261020-01 | ...\nfooter", True,
                                      posted_at=POSTED)
        self.assertEqual(signals.ALERT_KEYS, tuple(stored))
        self.assertEqual(("signal_id", "text", "posted_at", "lint_ok"), signals.ALERT_KEYS)
        signals.append_alert(self.paths, None, "pre-close | ...\nfooter", True,
                             posted_at=datetime.datetime(2026, 11, 21, 2, 0, tzinfo=UTC))
        signals.append_alert(self.paths, None, "a message the lint refused", False, posted_at=POSTED)
        lines = self.journal_lines(signals.ALERTS_FILE)
        self.assertEqual([("SIG-20261120-001", True), (None, True), (None, False)],
                         [(line["signal_id"], line["lint_ok"]) for line in lines])
        with open(signals.day_journal_path(self.paths, datetime.date(2026, 11, 20)), encoding="utf-8") as handle:
            day = handle.read()
        self.assertTrue(day.startswith("# Desk journal 2026-11-20 (America/New_York)\n"))
        self.assertIn("## 19:50 UTC SIG-20261120-001 (lint ok)\n\nEXIT SIGNAL AG-20261020-01 | ...\nfooter\n", day)
        # 02:00 UTC on Nov 21 is 21:00 ET on Nov 20: the Nov-20 day journal, the heading in UTC
        self.assertIn("## 02:00 UTC message (lint ok)\n\npre-close | ...\nfooter\n", day)
        self.assertFalse(os.path.exists(signals.day_journal_path(self.paths, datetime.date(2026, 11, 21))))
        self.assertIn("## 19:50 UTC message (lint failed: not posted)\n\na message the lint refused\n", day)
        self.assertEqual(1, day.count("# Desk journal"))

    def test_bad_alerts_are_refused(self) -> None:
        for args in (("SIG-2026-1", "text", True), (None, "", True), (None, "text", "yes"), (None, 5, True)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    signals.append_alert(self.paths, args[0], args[1], args[2], posted_at=POSTED)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            signals.append_alert(self.paths, None, "text", True, posted_at=POSTED.replace(tzinfo=None))

    def test_posted_since(self) -> None:
        opening = datetime.datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        self.assertFalse(signals.posted_since(self.paths, opening))
        signals.append_alert(self.paths, None, "early", True, posted_at=opening - datetime.timedelta(minutes=1))
        signals.append_alert(self.paths, None, "refused", False, posted_at=POSTED)
        self.assertFalse(signals.posted_since(self.paths, opening))
        signals.append_alert(self.paths, None, "late", True, posted_at=opening)
        self.assertTrue(signals.posted_since(self.paths, opening))


class OpenSignalsTest(DeskCase):
    def post(self, posted_at: datetime.datetime, trade_id: str, verdict: str = "EXIT_SIGNAL", posts: int = 1) -> str:
        signal_id = signals.append_signal(self.paths, record(posted_at, trade_id, verdict))["signal_id"]
        for index in range(posts):
            signals.append_alert(self.paths, signal_id, "text", True,
                                 posted_at=posted_at + datetime.timedelta(minutes=15 * index))
        return signal_id

    def test_open_signals_against_the_acks(self) -> None:
        wmt = self.post(POSTED, "AG-20261020-01", posts=3)
        nvda = self.post(POSTED + datetime.timedelta(minutes=1), "AG-20261022-02")
        spy = self.post(POSTED + datetime.timedelta(minutes=2), "AG-20261028-03", verdict="WATCH")
        found = signals.open_signals(self.paths, [])
        self.assertEqual([wmt, nvda, spy], [item.signal_id for item in found])
        first = found[0]
        self.assertEqual(("AG-20261020-01", "covered_call", "EXIT_SIGNAL", "close", POSTED, 3, "open", None),
                         (first.trade_id, first.structure, first.verdict, first.plan, first.posted_at, first.posts,
                          first.ack_state, first.snoozed_until))
        self.assertEqual(POSTED + datetime.timedelta(minutes=30), first.last_posted_at)
        self.assertEqual(7, first.next_repost_after_runs)
        self.assertEqual((1, 1), (found[1].posts, found[1].next_repost_after_runs))
        acks = [ack("ack", wmt), ack("snooze", nvda, "2026-11-20T21:00:00+00:00"), ack("config", None)]
        found = signals.open_signals(self.paths, acks)
        self.assertEqual([nvda, spy], [item.signal_id for item in found])
        self.assertEqual(("snoozed", "2026-11-20T21:00:00+00:00"), (found[0].ack_state, found[0].snoozed_until))
        found = signals.open_signals(self.paths, acks + [ack("acted", nvda), ack("snooze", wmt, "x")])
        self.assertEqual([spy], [item.signal_id for item in found])

    def test_a_later_signal_supersedes_an_earlier_one_of_the_same_trade(self) -> None:
        watch = self.post(POSTED, "AG-20261020-01", verdict="WATCH")
        exit_signal = self.post(POSTED + datetime.timedelta(minutes=30), "AG-20261020-01")
        self.assertEqual([exit_signal], [item.signal_id for item in signals.open_signals(self.paths, [])])
        self.assertEqual([], signals.open_signals(self.paths, [ack("ack", exit_signal)]))
        self.assertNotEqual(watch, exit_signal)

    def test_posts_count_only_linted_alerts(self) -> None:
        signal_id = self.post(POSTED, "AG-20261020-01", posts=0)
        signals.append_alert(self.paths, signal_id, "refused", False, posted_at=POSTED)
        [found] = signals.open_signals(self.paths, [])
        self.assertEqual((0, None, 1), (found.posts, found.last_posted_at, found.next_repost_after_runs))

    def test_empty_journals(self) -> None:
        self.assertEqual([], signals.open_signals(self.paths, []))


class LadderTest(unittest.TestCase):
    def test_the_ladder(self) -> None:
        self.assertEqual((1, 3, 7), signals.LADDER_RUN_OFFSETS)
        due = {(posts, runs): signals.repost_due(posts, runs) for posts in range(0, 6) for runs in range(0, 10)}
        for (posts, runs), value in due.items():
            if posts == 0:
                expected = True
            elif posts > 3:
                expected = False
            else:
                expected = runs >= signals.LADDER_RUN_OFFSETS[posts - 1]
            self.assertEqual(expected, value, (posts, runs))
        for bad in ((-1, 0), (1, -1), (True, 1)):
            with self.assertRaises(ValueError):
                signals.repost_due(*bad)

    def test_no_print_in_the_module(self) -> None:
        with open(MODULE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("print(", source)
        self.assertNotIn("float(", source)


if __name__ == "__main__":
    unittest.main()
