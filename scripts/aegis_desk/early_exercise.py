"""The early-exercise line of an open option leg, beside the expiry tiers (roadmap 2.3).

The owner's answers to the roadmap's open questions 2 and 3: the American lattice is the model of record, and each leg
gets one early-exercise reading, read by side, with a dividend note and, for a long put, a wait line. This module is the
one place that decides those lines; ``assignment_watch.py`` prints them under the leg's expiry tiers. It may add a
warning; it never removes, downgrades or replaces a tier, and it changes no exit code.

The reading (:func:`assess`)
----------------------------
The leg's implied volatility inverts the American lattice price of its mid (:func:`aegis_desk.american.implied_vol`)
with the desk's rate and the calendar's sourced yield (:func:`aegis_desk.calendar_events.sourced_yield`), at the
underlying's usable mid stamped within 60 seconds of the option quote (the assignment watch's own alignment), with the
time to the exercise cutoff the book uses. Then, from the lattice's flags at that volatility:

* a **long** leg whose ``exercise_now`` is true gets an exercise note (the lattice values immediate exercise above
  holding); one whose ``near_boundary`` is true a line that says it is near the boundary, which is never an exercise
  signal and never a hold signal;
* a **short** leg whose ``exercise_now`` or ``near_boundary`` is true gets an early-assignment warning (a missed
  warning is the costly error for a short leg).

A leg with neither flag gets no early-exercise line. When the reading cannot be made (no usable mid, no aligned spot,
under one hour to the cutoff, no sourced yield, or a mid the American model cannot solve), the line says early
exercise was not evaluated and why: UNAVAILABLE, never a guess.

The dividend note (:func:`dividend_note`)
-----------------------------------------
The lattice prices the dividend as a continuous yield, so the drop on an ex-date is not seen. Any leg, call or put, gets
the note when its life contains the calendar's known ex-date (after today, on or before its last trading day), when the
dividend status is ``unknown`` (or the calendar holds no entry for it), or when its last trading day is 80 or more
calendar days after the known ex-date (a second ex-date then likely falls inside its life). For a call the note adds
that the dividend tier is the test of early assignment. A confirmed absence (status ``none``) gets no note.

The put wait line (:func:`wait_line`)
-------------------------------------
A long put the lattice flags (``exercise_now``, or ``near_boundary``: at a volatility implied from a mid above the
intrinsic value the tree holds at its root, so a quoted deep in-the-money put reads near the boundary rather than past
it) and whose life contains a known ex-date with a stated cash amount ``D`` is not flagged for exercise when ``D > K (e^(r d) - 1)``, ``K`` the strike, ``r`` the desk's rate and ``d`` the years
from today to the ex-date (calendar days over 365): holding just past the ex-date is worth at least
``e^(-r d) (K + D) - S`` against ``K - S`` for exercising now, so waiting dominates. The test needs no model; like the
dividend tier, it reads only a stated amount. Without a stated amount the exercise note stands, with the dividend
note. A short put keeps its early-assignment warning.

Pure: no I/O, no clock; every input is passed in.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from . import american, bsm, calendar_events, iv, market_clock, units
from .calendar_events import CalendarFile

SECOND_EX_DATE_DAYS: int = 80
"""A last trading day this many calendar days after the known ex-date likely spans the next one too."""
SPOT_ALIGNMENT_S: int = 60
"""The underlying's mid must be stamped within this many seconds of the option quote (the assignment watch's own)."""
EXPIRING_SECONDS: int = 3600
"""Under this many seconds to the exercise cutoff no volatility is implied (the book's own limit)."""

STATUS_FLAGGED: str = "flagged"
STATUS_CLEAR: str = "clear"
STATUS_WAIT: str = "wait"
STATUS_UNAVAILABLE: str = "unavailable"

LONG_EXERCISE_TEXT: str = ("early exercise: the American model values exercising this long %s now above holding it "
                           "(early-exercise value %s a share)")
LONG_BOUNDARY_TEXT: str = ("early exercise: this long %s is near its early-exercise boundary, where the model can tell "
                           "neither exercising from holding; neither is signalled")
SHORT_TEXT: str = ("early assignment: the American model places this short %s %s its early-exercise boundary "
                   "(early-exercise value %s a share), so an early assignment is possible")
WAIT_TEXT: str = ("early exercise: not flagged: the stated %s dividend on %s exceeds the interest on the %s strike until "
                  "then (%s); the model prices the dividend as a yield and does not see this")
UNAVAILABLE_TEXT: str = "early exercise not evaluated: %s"
NOTE_INSIDE: str = "dividend note: the dividend is priced as a continuous yield, so the drop on the ex-date %s is not seen here"
NOTE_UNKNOWN: str = ("dividend note: the dividend is unknown in research/calendar.toml, and a continuous yield does not "
                     "see a drop on an ex-date")
NOTE_SECOND: str = ("dividend note: the last trading day is %d or more days after the ex-date %s, so a second ex-date "
                    "likely falls inside the leg's life, and a continuous yield does not see its drop")
CALL_TIER_TAIL: str = "; for a call the dividend tier is the test of early assignment"


@dataclass(frozen=True)
class EarlyExercise:
    """One leg's reading (module notes): ``status`` is ``flagged``, ``wait``, ``clear`` or ``unavailable``; ``lines``
    are the texts to print under the leg (without indentation)."""

    status: str
    lines: List[str]
    reason: Optional[str] = None
    exercise_now: Optional[bool] = None
    near_boundary: Optional[bool] = None
    early_exercise_premium: Optional[float] = None
    iv: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "exercise_now": self.exercise_now,
                "near_boundary": self.near_boundary, "early_exercise_premium": self.early_exercise_premium,
                "iv": self.iv, "lines": list(self.lines)}


def _money(value: float) -> str:
    return format(Decimal(repr(value)).quantize(Decimal("0.01")), "f")


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def dividend_note(symbol: str, right: str, last_trading_day: datetime.date, cal: Optional[CalendarFile],
                  today: datetime.date) -> Optional[str]:
    """The dividend note of a leg, or ``None`` (module notes)."""
    entry = calendar_events.events_for(cal, symbol).ex_dividend
    tail = CALL_TIER_TAIL if right == bsm.CALL else ""
    if entry is None or entry.status == calendar_events.STATUS_UNKNOWN:
        return NOTE_UNKNOWN + tail
    if entry.status == calendar_events.STATUS_NONE or entry.date is None:
        return None
    if today < entry.date <= last_trading_day:
        return NOTE_INSIDE % entry.date.isoformat() + tail
    if (last_trading_day - entry.date).days >= SECOND_EX_DATE_DAYS:
        return NOTE_SECOND % (SECOND_EX_DATE_DAYS, entry.date.isoformat()) + tail
    return None


def wait_line(symbol: str, strike: Decimal, last_trading_day: datetime.date, rate: float, cal: Optional[CalendarFile],
              today: datetime.date) -> Optional[str]:
    """The long put's wait line, or ``None`` when its test does not hold (module notes)."""
    entry = calendar_events.events_for(cal, symbol).ex_dividend
    if (entry is None or entry.date is None or entry.amount is None
            or entry.status not in calendar_events.DATED_STATUSES or not today < entry.date <= last_trading_day):
        return None
    years = (entry.date - today).days / units.DAYS_PER_YEAR
    interest = float(strike) * (math.exp(rate * years) - 1.0)
    if not float(entry.amount) > interest:
        return None
    return WAIT_TEXT % (_plain(entry.amount), entry.date.isoformat(), _plain(strike), _money(interest))


def assess(*, symbol: str, right: str, short: bool, strike: Decimal, expiration: datetime.date,
           last_trading_day: datetime.date, am_settled: bool, option_mid: Optional[Decimal],
           option_as_of: Optional[datetime.datetime], spot_mid: Optional[Decimal],
           spot_as_of: Optional[datetime.datetime], rate: float, cal: Optional[CalendarFile], today: datetime.date,
           extra_holidays: market_clock.ExtraHolidays = ()) -> EarlyExercise:
    """The leg's early-exercise reading and its lines (module notes)."""
    note = dividend_note(symbol, right, last_trading_day, cal, today)
    tail = [note] if note is not None else []

    def unavailable(reason: str) -> EarlyExercise:
        return EarlyExercise(STATUS_UNAVAILABLE, [UNAVAILABLE_TEXT % reason] + tail, reason=reason)

    if option_mid is None or option_as_of is None:
        return unavailable("no usable mid for the option")
    if spot_mid is None or spot_as_of is None:
        return unavailable("no usable mid for %s" % symbol)
    if abs((spot_as_of - option_as_of).total_seconds()) > SPOT_ALIGNMENT_S:
        return unavailable("the %s quote is stamped more than %d seconds from the option's" % (symbol, SPOT_ALIGNMENT_S))
    seconds = (market_clock.exercise_cutoff_utc(expiration, am_settled, None, extra_holidays)
               - option_as_of).total_seconds()
    if seconds < EXPIRING_SECONDS:
        return unavailable("under one hour to the exercise cutoff")
    dividend = calendar_events.sourced_yield(cal, symbol)
    if not dividend.known or dividend.value is None:
        return unavailable("no sourced dividend yield for %s (unknown is never zero)" % symbol)
    tau = seconds / units.SECONDS_PER_YEAR
    q = float(dividend.value)
    solved = american.implied_vol(float(option_mid), right, float(spot_mid), float(strike), tau, rate, q)
    if solved.status != iv.STATUS_OK or solved.sigma is None:
        return unavailable("the American model solves no volatility from the mid (%s)" % solved.status)
    value = american.value(right, float(spot_mid), float(strike), tau, rate, q, solved.sigma)
    kind = "call" if right == bsm.CALL else "put"
    facts = dict(exercise_now=value.exercise_now, near_boundary=value.near_boundary,
                 early_exercise_premium=value.early_exercise_premium, iv=solved.sigma)
    premium = _money(value.early_exercise_premium)
    if short:
        if value.exercise_now or value.near_boundary:
            where = "past" if value.exercise_now else "near"
            return EarlyExercise(STATUS_FLAGGED, [SHORT_TEXT % (kind, where, premium)] + tail, **facts)
        return EarlyExercise(STATUS_CLEAR, tail, **facts)
    if right == bsm.PUT and (value.exercise_now or value.near_boundary):
        wait = wait_line(symbol, strike, last_trading_day, rate, cal, today)
        if wait is not None:
            return EarlyExercise(STATUS_WAIT, [wait] + tail, **facts)
    if value.exercise_now:
        return EarlyExercise(STATUS_FLAGGED, [LONG_EXERCISE_TEXT % (kind, premium)] + tail, **facts)
    if value.near_boundary:
        return EarlyExercise(STATUS_FLAGGED, [LONG_BOUNDARY_TEXT % kind] + tail, **facts)
    return EarlyExercise(STATUS_CLEAR, tail, **facts)


__all__ = [
    "CALL_TIER_TAIL",
    "EXPIRING_SECONDS",
    "EarlyExercise",
    "LONG_BOUNDARY_TEXT",
    "LONG_EXERCISE_TEXT",
    "NOTE_INSIDE",
    "NOTE_SECOND",
    "NOTE_UNKNOWN",
    "SECOND_EX_DATE_DAYS",
    "SHORT_TEXT",
    "SPOT_ALIGNMENT_S",
    "STATUS_CLEAR",
    "STATUS_FLAGGED",
    "STATUS_UNAVAILABLE",
    "STATUS_WAIT",
    "UNAVAILABLE_TEXT",
    "WAIT_TEXT",
    "assess",
    "dividend_note",
    "wait_line",
]
