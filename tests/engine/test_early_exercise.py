"""``aegis_desk.early_exercise``: the early-exercise line of a leg beside the expiry tiers (roadmap 2.3), its dividend
note and the long put's wait line, read by side, UNAVAILABLE when it cannot be evaluated."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import math
import unittest
from decimal import Decimal as D
from typing import Any

from aegis_desk import calendar_events, early_exercise as E

UTC = datetime.timezone.utc
AS_OF = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC)
TODAY = datetime.date(2026, 11, 3)
DEC18 = datetime.date(2026, 12, 18)
RATE = 0.0412
CAL = calendar_events.parse_calendar_text("""schema_version = 2
[symbols.ZZZ]
ex_dividend = { date = 2026-12-18, amount = 1.85, yield = 0.0115, status = "estimated", source = "x", checked_utc = 2026-11-01T12:00:00Z }
[symbols.YYY]
ex_dividend = { status = "none", source = "x", checked_utc = 2026-11-01T12:00:00Z }
[symbols.UUU]
ex_dividend = { yield = 0.01, status = "unknown", source = "x", checked_utc = 2026-11-01T12:00:00Z }
[symbols.SSS]
ex_dividend = { date = 2026-11-10, amount = 0.20, yield = 0.008, status = "confirmed", source = "x", checked_utc = 2026-11-01T12:00:00Z }
[symbols.NNN]
ex_dividend = { date = 2026-12-01, amount = 0.20, status = "confirmed", source = "x", checked_utc = 2026-11-01T12:00:00Z }
""")


def reading(symbol: str = "YYY", right: str = "PUT", short: bool = False, strike: str = "620", mid: Any = "50.05",
            spot: Any = "570", last: datetime.date = DEC18, spot_as_of: datetime.datetime = AS_OF,
            as_of: datetime.datetime = AS_OF) -> E.EarlyExercise:
    return E.assess(symbol=symbol, right=right, short=short, strike=D(strike), expiration=last, last_trading_day=last,
                    am_settled=False, option_mid=None if mid is None else D(mid), option_as_of=as_of,
                    spot_mid=None if spot is None else D(spot), spot_as_of=spot_as_of, rate=RATE, cal=CAL, today=TODAY)


class SideTest(unittest.TestCase):
    def test_a_long_put_near_the_boundary_is_neither_an_exercise_nor_a_hold_signal(self) -> None:
        result = reading()
        self.assertEqual((E.STATUS_FLAGGED, False, True), (result.status, result.exercise_now, result.near_boundary))
        self.assertEqual([E.LONG_BOUNDARY_TEXT % "put"], result.lines)
        self.assertIn("neither is signalled", result.lines[0])

    def test_a_short_put_there_carries_an_early_assignment_warning(self) -> None:
        result = reading(short=True)
        self.assertEqual(E.STATUS_FLAGGED, result.status)
        self.assertTrue(result.lines[0].startswith("early assignment: the American model places this short put near"))
        self.assertIn("early-exercise value %s a share" % format(D(repr(result.early_exercise_premium)).quantize(D("0.01")), "f"),
                      result.lines[0])

    def test_an_at_the_money_leg_is_clear_and_prints_nothing(self) -> None:
        for short in (False, True):
            result = reading(right="CALL", short=short, strike="100", mid="5.00", spot="100")
            self.assertEqual((E.STATUS_CLEAR, []), (result.status, result.lines))

    def test_a_long_option_exercised_now_gets_the_exercise_note(self) -> None:
        self.assertEqual("early exercise: the American model values exercising this long put now above holding it "
                         "(early-exercise value 0.42 a share)", E.LONG_EXERCISE_TEXT % ("put", "0.42"))


class DividendNoteTest(unittest.TestCase):
    def test_the_three_cases_and_the_call_tail(self) -> None:
        self.assertEqual(E.NOTE_INSIDE % "2026-12-18", E.dividend_note("ZZZ", "PUT", DEC18, CAL, TODAY))
        self.assertEqual(E.NOTE_INSIDE % "2026-12-18" + E.CALL_TIER_TAIL, E.dividend_note("ZZZ", "CALL", DEC18, CAL, TODAY))
        self.assertIsNone(E.dividend_note("ZZZ", "PUT", datetime.date(2026, 12, 17), CAL, TODAY), "ends before it")
        self.assertEqual(E.NOTE_UNKNOWN, E.dividend_note("UUU", "PUT", DEC18, CAL, TODAY))
        self.assertEqual(E.NOTE_UNKNOWN, E.dividend_note("AAA", "PUT", DEC18, CAL, TODAY), "no entry is unknown")
        self.assertEqual(E.NOTE_UNKNOWN, E.dividend_note("ZZZ", "PUT", DEC18, None, TODAY), "no calendar is unknown")
        self.assertIsNone(E.dividend_note("YYY", "CALL", DEC18, CAL, TODAY), "a confirmed absence")
        # SSS goes ex on 2026-11-10: a leg 80 or more days after it likely spans the next ex-date too
        self.assertEqual(E.NOTE_INSIDE % "2026-11-10", E.dividend_note("SSS", "PUT", datetime.date(2027, 3, 19), CAL, TODAY))
        later = datetime.date(2026, 11, 11)
        self.assertEqual(E.NOTE_SECOND % (80, "2026-11-10"),
                         E.dividend_note("SSS", "PUT", datetime.date(2027, 1, 29), CAL, later))
        self.assertIsNone(E.dividend_note("SSS", "PUT", datetime.date(2027, 1, 28), CAL, later), "79 days")

    def test_the_note_rides_under_the_reading(self) -> None:
        result = reading(symbol="ZZZ", strike="100", mid="5.00", spot="100", right="CALL")
        self.assertEqual(E.STATUS_CLEAR, result.status)
        self.assertEqual([E.NOTE_INSIDE % "2026-12-18" + E.CALL_TIER_TAIL], result.lines)


class WaitLineTest(unittest.TestCase):
    def test_the_stated_dividend_against_the_interest_on_the_strike(self) -> None:
        # d = 45 days: the interest on a 100 strike is 100 (e^(0.0412 x 45/365) - 1) = 0.51 < 1.85
        interest = 100 * (math.exp(RATE * 45 / 365.0) - 1)
        self.assertEqual(E.WAIT_TEXT % ("1.85", "2026-12-18", "100", "%.2f" % interest),
                         E.wait_line("ZZZ", D("100"), DEC18, RATE, CAL, TODAY))
        self.assertIsNone(E.wait_line("ZZZ", D("620"), DEC18, RATE, CAL, TODAY), "3.16 of interest exceeds 1.85")
        self.assertIsNone(E.wait_line("ZZZ", D("100"), datetime.date(2026, 12, 17), RATE, CAL, TODAY), "ex after expiry")
        self.assertIsNone(E.wait_line("UUU", D("100"), DEC18, RATE, CAL, TODAY), "no stated amount")
        self.assertIsNone(E.wait_line("YYY", D("100"), DEC18, RATE, CAL, TODAY))

    def test_it_replaces_a_long_puts_flag_and_never_a_short_puts_warning(self) -> None:
        long = reading(symbol="ZZZ", strike="100", mid="50.01", spot="50")
        self.assertEqual(E.STATUS_WAIT, long.status)
        self.assertTrue(long.lines[0].startswith("early exercise: not flagged: the stated 1.85 dividend on 2026-12-18"))
        self.assertEqual(E.NOTE_INSIDE % "2026-12-18", long.lines[1])
        short = reading(symbol="ZZZ", strike="100", mid="50.01", spot="50", short=True)
        self.assertEqual(E.STATUS_FLAGGED, short.status)
        self.assertTrue(short.lines[0].startswith("early assignment:"))
        flagged = reading(symbol="ZZZ", strike="620", mid="50.05", spot="570")
        self.assertEqual((E.STATUS_FLAGGED, E.LONG_BOUNDARY_TEXT % "put"), (flagged.status, flagged.lines[0]),
                         "the dividend does not exceed the interest: the flag stands")


class UnavailableTest(unittest.TestCase):
    def test_every_reason_is_said_and_nothing_is_guessed(self) -> None:
        cases = {
            "no usable mid for the option": reading(mid=None),
            "no usable mid for YYY": reading(spot=None),
            "the YYY quote is stamped more than 60 seconds from the option's": reading(
                spot_as_of=AS_OF - datetime.timedelta(seconds=61)),
            "under one hour to the exercise cutoff": reading(  # 20:30 UTC is 15:30 ET, half an hour before the close
                last=TODAY, as_of=datetime.datetime(2026, 11, 3, 20, 30, tzinfo=UTC),
                spot_as_of=datetime.datetime(2026, 11, 3, 20, 30, tzinfo=UTC)),
            "no sourced dividend yield for NNN (unknown is never zero)": reading(symbol="NNN"),
            "the American model solves no volatility from the mid (below_intrinsic)": reading(mid="49.00"),
        }
        for reason, result in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual((E.STATUS_UNAVAILABLE, reason), (result.status, result.reason))
                self.assertEqual(E.UNAVAILABLE_TEXT % reason, result.lines[0])
                self.assertIsNone(result.exercise_now)
        self.assertEqual(E.NOTE_INSIDE % "2026-12-01", reading(symbol="NNN").lines[1], "the note still rides along")

    def test_the_record(self) -> None:
        record = reading().as_dict()
        self.assertEqual({"status", "reason", "exercise_now", "near_boundary", "early_exercise_premium", "iv", "lines"},
                         set(record))


if __name__ == "__main__":
    unittest.main()
