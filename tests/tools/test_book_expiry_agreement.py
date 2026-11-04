"""``scripts/book.py`` and ``scripts/assignment_watch.py`` run in-process on ONE copied fixture desk root whose
``research/calendar.toml`` puts the WMT ex-dividend on Mon 2026-11-23 (the last cum-dividend day is then Fri
2026-11-20, the Nov-20 call's last trading day, so the assignment watch grades the call): the two tools print the
same ``extrinsic_bid`` for the WMT call, the fresh bid of any grade A-E less the intrinsic value at the underlying's
usable mid stamped within 60 s of the option (``aegis_desk.expiry.dividend_tier``'s geometry). Nothing under
``tests/fixtures/`` is ever written."""

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
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import assignment_watch
import book as book_tool  # the tool module scripts/book.py, not the library aegis_desk.book

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
T1_NOW = "2026-11-19T19:50:00Z"
EXPIRY_NOW = "2026-11-20T19:50:00Z"
WMT_CALL = "WMT261120C00105000"
EX_TUESDAY = "date = 2026-11-24, amount = 0.2350"
EX_MONDAY = "date = 2026-11-23, amount = 0.2350"


def run_json(main: Callable[[Optional[Sequence[str]]], int], argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(list(argv))
    return code, json.loads(buffer.getvalue())


class BookAndAssignmentWatchAgreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-book-expiry-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        path = os.path.join(self.root, "research", "calendar.toml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(EX_TUESDAY), "the copy's calendar lists the WMT ex-dividend once")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(EX_TUESDAY, EX_MONDAY))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, fixture_set: str, now: str) -> List[str]:
        return ["--json", "--offline", "--fixture-set", fixture_set, "--now", now, "--desk-root", self.root]

    def both(self, fixture_set: str, now: str) -> Tuple[int, Dict[str, Any], int, Dict[str, Any]]:
        """``(book exit, book WMT call leg, watch exit, watch WMT call leg)`` from the same copy, the same instant."""
        book_code, book_body = run_json(book_tool.main, self.argv(fixture_set, now))
        watch_code, watch_body = run_json(assignment_watch.main, self.argv(fixture_set, now))
        legs = [leg for leg in book_body["payload"]["legs"] if leg["occ"] == WMT_CALL and leg["status"] == "open"]
        watched = [leg for leg in watch_body["payload"]["legs"] if leg["symbol"] == WMT_CALL]
        self.assertEqual((1, 1), (len(legs), len(watched)))
        return book_code, legs[0], watch_code, watched[0]

    def test_t1_both_tools_print_the_same_extrinsic_bid(self) -> None:
        book_code, leg, watch_code, watched = self.both("t1", T1_NOW)
        self.assertEqual((0, 10), (book_code, watch_code), "the book is all A-D; the watch fires on the NVDA pin")
        dividend = watched["dividend"]
        self.assertEqual(("NOTED", "1.52", "106.43", "1.43", "0.09"),
                         (dividend["tier"], dividend["bid"], dividend["spot_mid"], dividend["intrinsic"],
                          dividend["extrinsic_bid"]))
        self.assertEqual(Decimal(dividend["extrinsic_bid"]), Decimal(leg["extrinsic_bid"]),
                         "bid 1.52 - (106.43 - 105) in both tools")
        self.assertEqual(dividend["extrinsic_bid"], leg["extrinsic_bid"], "and printed the same way: 0.09")
        self.assertEqual(("C", "1.52", "1.6", "1.6", "mid"),
                         (leg["quote_grade"], leg["fresh_bid"], leg["fresh_ask"], leg["mark_liq"], leg["mark_source"]))
        self.assertEqual((dividend["bid"], dividend["itm"]), (leg["fresh_bid"], leg["itm"]))

    def test_expiry_day_grade_e_call_keeps_its_fresh_sides_and_the_same_extrinsic_bid(self) -> None:
        book_code, leg, watch_code, watched = self.both("expiry_day", EXPIRY_NOW)
        self.assertEqual((20, 10), (book_code, watch_code), "the grade-E leg leaves its group unavailable in the book")
        self.assertEqual(("E", "0.12", "0.16", None, "unavailable"),
                         (leg["quote_grade"], leg["fresh_bid"], leg["fresh_ask"], leg["mark_liq"], leg["mark_source"]))
        dividend = watched["dividend"]
        self.assertEqual(("NOTED", "E", "0.12", "104.93", "0", "0.12"),
                         (dividend["tier"], dividend["option_grade"], dividend["bid"], dividend["spot_mid"],
                          dividend["intrinsic"], dividend["extrinsic_bid"]))
        self.assertEqual("0.12", leg["extrinsic_bid"], "spot 104.93 is below the strike: the whole bid is extrinsic")
        self.assertEqual(Decimal(dividend["extrinsic_bid"]), Decimal(leg["extrinsic_bid"]))
        self.assertEqual(dividend["extrinsic_bid"], leg["extrinsic_bid"])
        self.assertEqual((dividend["bid"], dividend["itm"]), (leg["fresh_bid"], leg["itm"]))


if __name__ == "__main__":
    unittest.main()
