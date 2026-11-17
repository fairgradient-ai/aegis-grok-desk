"""Scenario and event stress of the book (roadmap 2.8): the book re-priced under spot and volatility moves.

A computation, not a forecast. Every stressed price comes from :func:`aegis_desk.book.leg_price`, the leg pricer of
the book's model of record (the American lattice of :mod:`aegis_desk.american`), at the leg's own implied volatility,
the book's risk-free rate and the symbol's sourced dividend yield: the scenario picks no model of its own.

The grid (:data:`GRID_SPOT_PCT`, :data:`GRID_VOL_POINTS`; proposed here, counsel signs its labels)
-----------------------------------------------------------------------------------------------
Spot moves of -10, -5, -2, 0, +2, +5 and +10 % of each underlying's mark, crossed with volatility moves of -5, 0 and +5
volatility points added to every option leg's implied volatility (never below :data:`VOL_FLOOR`): 21 cells. Every
underlying moves by the same percentage in a cell; there is no correlation model.

What a cell holds (:func:`stress`)
---------------------------------
For each open group in scope, the change of its value from the book's marks: a stock leg's open quantity x its
multiplier x the spot move, an option leg's open quantity x multiplier x (the stressed model price - the model price at
the book's inputs). Measuring the change between two model prices, never between a model price and the quote's mid, is
what makes the stressed book equal the book at a zero move: ``stressed_pl_mid`` is the group's ``unrealized_pl_mid``
plus the change, exactly. A group's change is split into ``stock`` and ``options``, so a hedge through an event shows
what each side does. The portfolio change is the sum of the groups', and ``None`` when any group in scope is
excluded (a partial total is worse than none, as for the book's net delta).

A group is excluded, with its reason, when the book marks it unavailable, an open option leg has no implied
volatility, no quote stamp or no time to expiry, its underlying has no usable mark, or the symbol's dividend yield is
unknown.

Horizons
--------
Without an event the cells are instantaneous: each leg keeps its time to expiry. An event stress
(:func:`event_horizon`) prices every leg at the close of the first trading day after the event date: for earnings,
the day after the release; for an ex-dividend date, the day after the ex-date. Each option leg's time to expiry is
shortened by the time from its quote's stamp to that close, and a leg that expires before it is valued at its
intrinsic value at the moved spot. The ex-dividend stress moves spot only by the grid: spot is never cut by the cash
amount, because the continuous yield already lowers the forward and a cut would count the dividend twice; when a short
call is in scope, a note says that whether it is assigned early is the dividend tier's question
(``assignment_watch.py``), not this stress's. No stress
models a jump the size of the dividend.

Pure: the book document and the calendar are passed in.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import book as book_module
from . import calendar_events, market_clock, units

GRID_SPOT_PCT: Tuple[int, ...] = (-10, -5, -2, 0, 2, 5, 10)
"""The spot moves of the grid, in percent of each underlying's mark (proposed here)."""
GRID_VOL_POINTS: Tuple[int, ...] = (-5, 0, 5)
"""The volatility moves of the grid, in volatility points added to each leg's implied volatility (proposed here)."""
VOL_FLOOR: float = 0.01
"""The lowest stressed volatility (1 %): a -5 point move never takes a leg's volatility to zero or below."""
EVENT_EARNINGS: str = "earnings"
EVENT_EX_DIVIDEND: str = "ex_dividend"
EVENTS: Tuple[str, ...] = (EVENT_EARNINGS, EVENT_EX_DIVIDEND)
LABEL: str = "a computation on the book's model of record, not a forecast"
NOTE_NO_DIVIDEND_CUT: str = ("spot is not cut by the dividend: the sourced yield already lowers the forward, so a cut "
                             "would count the dividend twice")
NOTE_DIVIDEND_TIER: str = ("whether a short call is assigned early is the dividend tier's test in assignment_watch.py "
                           "(its extrinsic bid against the dividend), not this stress")
NOTE_EARNINGS: str = ("the volatility moves stand in for the change of implied volatility across the release; no move "
                      "beyond the grid is modelled")
NOTE_EXPIRES: str = "%s expires before the horizon: valued at its intrinsic value at the moved spot"
REASON_UNAVAILABLE: str = "the book marks it unavailable"
REASON_NO_SPOT: str = "%s has no usable mark"
REASON_NO_IV: str = "%s has no implied volatility (%s)"
REASON_NO_TAU: str = "%s has no quote stamp or time to expiry"
REASON_DIVIDEND: str = "the dividend yield of %s is unknown"
_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)


class StressUnavailable(Exception):
    """An event stress that cannot be priced: the calendar holds no usable date for the event."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Shock:
    """One cell's moves: ``spot_pct`` percent of each underlying's mark, ``vol_points`` added to each volatility."""

    spot_pct: int
    vol_points: int

    def label(self) -> str:
        return "spot %+d%% vol %+d" % (self.spot_pct, self.vol_points)


def grid() -> Tuple[Shock, ...]:
    """The 21 cells, spot move by spot move, volatility move by volatility move."""
    return tuple(Shock(spot, vol) for spot in GRID_SPOT_PCT for vol in GRID_VOL_POINTS)


@dataclass(frozen=True)
class Horizon:
    """When the stressed book is priced: ``utc`` ``None`` for an instantaneous cell; for an event, the close of the
    first trading day after ``event_date``."""

    utc: Optional[datetime.datetime] = None
    event: Optional[str] = None
    symbol: Optional[str] = None
    event_date: Optional[datetime.date] = None
    event_time: Optional[str] = None
    horizon_date: Optional[datetime.date] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"utc": self.utc, "event": self.event, "symbol": self.symbol,
                "event_date": None if self.event_date is None else self.event_date.isoformat(),
                "event_time": self.event_time,
                "horizon_date": None if self.horizon_date is None else self.horizon_date.isoformat()}


@dataclass(frozen=True)
class GroupStress:
    group_id: str
    change: Decimal
    stock: Decimal
    options: Decimal
    stressed_pl_mid: Optional[Decimal]

    def as_dict(self) -> Dict[str, Any]:
        return {"group_id": self.group_id, "change": self.change, "stock": self.stock, "options": self.options,
                "stressed_pl_mid": self.stressed_pl_mid}


@dataclass(frozen=True)
class Cell:
    shock: Shock
    change: Optional[Decimal]
    stressed_pl_mid: Optional[Decimal]
    groups: Tuple[GroupStress, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {"spot_pct": self.shock.spot_pct, "vol_points": self.shock.vol_points, "change": self.change,
                "stressed_pl_mid": self.stressed_pl_mid, "groups": [item.as_dict() for item in self.groups]}


@dataclass(frozen=True)
class Exclusion:
    group_id: str
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {"group_id": self.group_id, "reason": self.reason}


@dataclass(frozen=True)
class Stress:
    """The whole computation: the horizon, the groups in scope (``stressed`` and ``excluded``), the cells, the notes."""

    horizon: Horizon
    stressed: Tuple[str, ...]
    excluded: Tuple[Exclusion, ...]
    cells: Tuple[Cell, ...]
    notes: Tuple[str, ...]

    def cell(self, spot_pct: int, vol_points: int) -> Cell:
        for item in self.cells:
            if (item.shock.spot_pct, item.shock.vol_points) == (spot_pct, vol_points):
                return item
        raise KeyError((spot_pct, vol_points))


def _close_utc(day: datetime.date, extra: market_clock.ExtraHolidays) -> datetime.datetime:
    close = market_clock.session_close_et(day, extra_holidays=extra)
    return market_clock.from_et(datetime.datetime.combine(day, close))


def event_horizon(cal: Optional[calendar_events.CalendarFile], symbol: str, event: str, today: datetime.date,
                  extra: market_clock.ExtraHolidays = ()) -> Horizon:
    """The horizon of an ``event`` stress of ``symbol`` (module notes); :class:`StressUnavailable` when the calendar
    holds no confirmed or estimated date on or after ``today``."""
    if event not in EVENTS:
        raise ValueError("event must be one of %s, got %r" % (", ".join(EVENTS), event))
    symbol = symbol.upper()
    events = calendar_events.events_for(cal, symbol)
    entry = events.earnings if event == EVENT_EARNINGS else events.ex_dividend
    words = "earnings date" if event == EVENT_EARNINGS else "ex-dividend date"
    if entry is None:
        raise StressUnavailable("research/calendar.toml holds no %s for %s" % (words, symbol))
    if entry.status == calendar_events.STATUS_NONE:
        raise StressUnavailable("research/calendar.toml says %s has no %s" % (symbol, words))
    if entry.status not in (calendar_events.STATUS_CONFIRMED, calendar_events.STATUS_ESTIMATED) or entry.date is None:
        raise StressUnavailable("the %s of %s is unknown in research/calendar.toml" % (words, symbol))
    if entry.date < today:
        raise StressUnavailable("the %s of %s, %s, is past" % (words, symbol, entry.date.isoformat()))
    horizon_day = market_clock.next_trading_day(entry.date, extra)
    return Horizon(_close_utc(horizon_day, extra), event, symbol, entry.date,
                   getattr(entry, "time", None) if event == EVENT_EARNINGS else None, horizon_day)


def _open_legs(document: Mapping[str, Any], group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    rows = set(group["legs"])
    return [leg for leg in document["legs"] if leg["row_number"] in rows and leg["status"] == "open"]


def _label(leg: Mapping[str, Any]) -> str:
    return "row %d %s" % (leg["row_number"], leg["occ"] or leg["symbol"])


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def stress(document: Mapping[str, Any], cal: Optional[calendar_events.CalendarFile],
           shocks: Sequence[Shock] = (), horizon: Horizon = Horizon(), symbol: Optional[str] = None) -> Stress:
    """Every cell of ``shocks`` (the grid when empty) over the open groups of ``document`` (on ``symbol`` only when
    given), at ``horizon`` (module notes)."""
    cells_in = tuple(shocks) or grid()
    rate = float(document["rates"]["risk_free_rate"])
    underlyings = document["underlyings"]
    scope = [group for group in document["groups"] if _open_legs(document, group)
             and (symbol is None or group["underlying"] == symbol.upper())]
    excluded: List[Exclusion] = []
    priced: List[Tuple[Mapping[str, Any], List[Mapping[str, Any]], Decimal, float]] = []
    notes: List[str] = []
    for group in scope:
        legs = _open_legs(document, group)
        spot = (underlyings.get(group["underlying"]) or {}).get("mark_mid")
        reason = None
        if group["data_quality"] != book_module.DATA_QUALITY_OK:
            reason = REASON_UNAVAILABLE
        elif spot is None:
            reason = REASON_NO_SPOT % group["underlying"]
        else:
            dividend = calendar_events.sourced_yield(cal, group["underlying"])
            if any(leg["occ"] is not None for leg in legs) and (not dividend.known or dividend.value is None):
                reason = REASON_DIVIDEND % group["underlying"]
            for leg in legs:
                if reason is not None or leg["occ"] is None:
                    continue
                if leg["iv"] is None:
                    reason = REASON_NO_IV % (_label(leg), leg["iv_status"])
                elif leg["tau_years"] is None or leg["as_of"] is None:
                    reason = REASON_NO_TAU % _label(leg)
        if reason is not None:
            excluded.append(Exclusion(group["group_id"], reason))
            continue
        q = calendar_events.sourced_yield(cal, group["underlying"]).value
        priced.append((group, legs, spot, 0.0 if q is None else float(q)))
        if horizon.utc is not None:
            for leg in legs:
                if leg["occ"] is not None and _tau_at(leg, horizon) <= 0.0:
                    note = NOTE_EXPIRES % _label(leg)
                    if note not in notes:
                        notes.append(note)
    if horizon.event == EVENT_EX_DIVIDEND:
        notes.append(NOTE_NO_DIVIDEND_CUT)
        if any(leg["occ"] is not None and leg["right"] == "CALL" and leg["open_quantity"] < 0
               for _group, legs, _spot, _q in priced for leg in legs):
            notes.append(NOTE_DIVIDEND_TIER)
    elif horizon.event == EVENT_EARNINGS:
        notes.append(NOTE_EARNINGS)
    base = {leg["row_number"]: book_module.leg_price(leg["right"], float(spot), float(leg["strike"]), leg["tau_years"],
                                                      rate, q, leg["iv"])
            for group, legs, spot, q in priced for leg in legs if leg["occ"] is not None}
    cells: List[Cell] = []
    for shock in cells_in:
        groups: List[GroupStress] = []
        for group, legs, spot, q in priced:
            moved = spot * (1 + Decimal(shock.spot_pct) / _HUNDRED)
            stock = Decimal(0)
            options = 0.0
            for leg in legs:
                size = leg["open_quantity"] * leg["multiplier"]
                if leg["occ"] is None:
                    stock += size * (moved - spot)
                    continue
                tau = _tau_at(leg, horizon)
                if tau <= 0.0:
                    value = float(_intrinsic(leg["right"], Decimal(leg["strike"]), moved))
                else:
                    sigma = max(leg["iv"] + shock.vol_points * units.VOL_POINT, VOL_FLOOR)
                    value = book_module.leg_price(leg["right"], float(moved), float(leg["strike"]), tau, rate, q, sigma)
                options += float(size) * (value - base[leg["row_number"]])
            stock_d, options_d = _money(stock), _money(Decimal(repr(options)))
            change = stock_d + options_d
            pl = group["unrealized_pl_mid"]
            groups.append(GroupStress(group["group_id"], change, stock_d, options_d, None if pl is None else pl + change))
        total = None if excluded or not groups else sum((item.change for item in groups), Decimal("0.00"))
        pls = [item.stressed_pl_mid for item in groups]
        stressed_total = (None if total is None or any(value is None for value in pls)
                          else sum((value for value in pls if value is not None), Decimal("0.00")))
        cells.append(Cell(shock, total, stressed_total, tuple(groups)))
    return Stress(horizon, tuple(group["group_id"] for group, _l, _s, _q in priced), tuple(excluded), tuple(cells),
                  tuple(notes))


def _tau_at(leg: Mapping[str, Any], horizon: Horizon) -> float:
    """The leg's time to expiry at the horizon: its own when instantaneous, else shortened by the time from its quote's
    stamp to the horizon."""
    tau = float(leg["tau_years"])
    if horizon.utc is None:
        return tau
    return tau - (horizon.utc - leg["as_of"]).total_seconds() / units.SECONDS_PER_YEAR


def _intrinsic(right: str, strike: Decimal, spot: Decimal) -> Decimal:
    return max(spot - strike, Decimal(0)) if right == "CALL" else max(strike - spot, Decimal(0))


__all__ = [
    "Cell",
    "EVENTS",
    "EVENT_EARNINGS",
    "EVENT_EX_DIVIDEND",
    "Exclusion",
    "GRID_SPOT_PCT",
    "GRID_VOL_POINTS",
    "GroupStress",
    "Horizon",
    "LABEL",
    "Shock",
    "Stress",
    "StressUnavailable",
    "VOL_FLOOR",
    "event_horizon",
    "grid",
    "stress",
]
