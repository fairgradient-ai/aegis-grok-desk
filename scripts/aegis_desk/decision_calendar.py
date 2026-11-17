"""The decision calendar (roadmap 2.11): the owner's dated decision points over the coming weeks, in date order.

It lists, for the positions held and the symbols they are on:

* each open option leg's act-by instant (:func:`aegis_desk.expiry.leg_act_by`, the expiry sheet's own rule: a short
  leg by the session close or the broker's cutoff, whichever comes first) and its contract's last trading day;
* each held symbol's ex-dividend date with its cash amount, and its earnings date with its time, from
  ``research/calendar.toml``, each with the status and source the file gives it (owner-entered, or sourced from
  Alpaca's corporate actions after the owner's tap).

A date nobody sourced is never guessed: a held symbol whose dividend or earnings entry is ``unknown``, or missing, is a
gap (UNAVAILABLE), listed after the points; ``none`` (no dividend, no earnings: an ETF's earnings) lists nothing. Points
before today or past the horizon are left out. Pure: positions, the calendar and the clock are passed in.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import calendar_events, expiry, market_clock

DEFAULT_HORIZON_DAYS: int = 45
"""Calendar days ahead of today's ET date the calendar covers by default."""
MAX_HORIZON_DAYS: int = 400
KIND_ACT_BY: str = "act_by"
KIND_LAST_TRADING_DAY: str = "last_trading_day"
KIND_EX_DIVIDEND: str = "ex_dividend"
KIND_EARNINGS: str = "earnings"
KINDS: Tuple[str, ...] = (KIND_ACT_BY, KIND_LAST_TRADING_DAY, KIND_EX_DIVIDEND, KIND_EARNINGS)
_ORDER = {KIND_EARNINGS: 0, KIND_EX_DIVIDEND: 1, KIND_ACT_BY: 2, KIND_LAST_TRADING_DAY: 3}
REASON_NO_CALENDAR: str = "research/calendar.toml is missing"
REASON_NOT_LISTED: str = "research/calendar.toml does not list %s"
REASON_UNKNOWN: str = "research/calendar.toml holds it as unknown"
TIME_WORDS: Dict[str, str] = {calendar_events.TIME_PRE: "before the open", calendar_events.TIME_POST: "after the close",
                              calendar_events.TIME_UNKNOWN: "time unknown"}


@dataclass(frozen=True)
class Point:
    """One dated decision point: ``time_et`` is ``HH:MM`` for an instant (an act-by), ``None`` for a date."""

    date: datetime.date
    kind: str
    symbol: str
    detail: str
    time_et: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"date": self.date.isoformat(), "time_et": self.time_et, "kind": self.kind, "symbol": self.symbol,
                "detail": self.detail, "status": self.status, "source": self.source}


@dataclass(frozen=True)
class Gap:
    """A held symbol's date the calendar cannot give: UNAVAILABLE, never guessed."""

    symbol: str
    kind: str
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "kind": self.kind, "reason": self.reason}


@dataclass(frozen=True)
class DecisionCalendar:
    start: datetime.date
    end: datetime.date
    points: Tuple[Point, ...]
    gaps: Tuple[Gap, ...]


def _event_points(cal: Optional[calendar_events.CalendarFile], symbol: str, start: datetime.date,
                  end: datetime.date) -> Tuple[List[Point], List[Gap]]:
    points: List[Point] = []
    gaps: List[Gap] = []
    if cal is None:
        return [], [Gap(symbol, kind, REASON_NO_CALENDAR) for kind in (KIND_EARNINGS, KIND_EX_DIVIDEND)]
    events = calendar_events.events_for(cal, symbol)
    for kind, entry in ((KIND_EARNINGS, events.earnings), (KIND_EX_DIVIDEND, events.ex_dividend)):
        if entry is None:
            gaps.append(Gap(symbol, kind, REASON_NOT_LISTED % ("an earnings entry for %s" % symbol
                                                                if kind == KIND_EARNINGS
                                                                else "an ex-dividend entry for %s" % symbol)))
            continue
        if entry.status == calendar_events.STATUS_NONE:
            continue
        if entry.status == calendar_events.STATUS_UNKNOWN or entry.date is None:
            gaps.append(Gap(symbol, kind, REASON_UNKNOWN))
            continue
        if not start <= entry.date <= end:
            continue
        if kind == KIND_EARNINGS:
            detail = "%s earnings %s" % (symbol, TIME_WORDS.get(entry.time, entry.time))
        else:
            detail = "%s ex-dividend, %s per share" % (symbol, entry.amount)
        points.append(Point(entry.date, kind, symbol, detail, None, entry.status, entry.source))
    return points, gaps


def build(rows: Sequence[Any], cal: Optional[calendar_events.CalendarFile], now: datetime.datetime,
          broker_cutoff_et: datetime.time, horizon_days: int = DEFAULT_HORIZON_DAYS) -> DecisionCalendar:
    """The calendar of the open ``rows`` (``positions_csv`` rows) from today's ET date to ``horizon_days`` ahead."""
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or not 1 <= horizon_days <= MAX_HORIZON_DAYS:
        raise ValueError("horizon_days must be an integer 1-%d, got %r" % (MAX_HORIZON_DAYS, horizon_days))
    today = market_clock.to_et(now).date()
    end = today + datetime.timedelta(days=horizon_days)
    extra = calendar_events.extra_closures(cal)
    points: List[Point] = []
    gaps: List[Gap] = []
    contracts: Dict[str, Tuple[datetime.date, str]] = {}
    open_rows = [row for row in rows if row.is_open]
    for row in open_rows:
        if row.occ is None:
            continue
        am = calendar_events.is_am_settled(cal, row.occ.root)
        last = market_clock.last_trading_day(row.occ.expiration, am, extra)
        if not today <= last <= end:
            continue
        act = expiry.leg_act_by(row, last, broker_cutoff_et, now, extra)
        side = "short" if row.is_short else "long"
        points.append(Point(last, KIND_ACT_BY, row.underlying,
                            "%s %s %s (row %d, %s)" % (row.compact_symbol, side, abs(row.open_quantity),
                                                       row.row_number, act.basis.replace("_", " ")),
                            act.cutoff_et.strftime("%H:%M")))
        contracts[row.compact_symbol] = (last, row.underlying)
    for contract, (last, underlying) in contracts.items():
        points.append(Point(last, KIND_LAST_TRADING_DAY, underlying, "%s last trading day" % contract))
    for symbol in sorted({row.underlying for row in open_rows}):
        found, missing = _event_points(cal, symbol, today, end)
        points.extend(found)
        gaps.extend(missing)
    points.sort(key=lambda point: (point.date, _ORDER[point.kind], point.time_et or "", point.symbol, point.detail))
    return DecisionCalendar(today, end, tuple(points), tuple(gaps))


__all__ = [
    "DEFAULT_HORIZON_DAYS",
    "DecisionCalendar",
    "Gap",
    "KINDS",
    "KIND_ACT_BY",
    "KIND_EARNINGS",
    "KIND_EX_DIVIDEND",
    "KIND_LAST_TRADING_DAY",
    "MAX_HORIZON_DAYS",
    "Point",
    "build",
]
