"""Expiry and assignment analytics (Stage 1, European-only) behind ``assignment_watch.py`` and
``expiry_checklist.py``: the request batch, settlement and act-by times, the dividend
(extrinsic-versus-dividend) and pin tiers, the docket implications and the exit codes.

New for Aegis Desk (plan sections 8, 9 and 11; skill 8); standard library only, no I/O, no wall
clock, no network: every instant, quote and calendar is a parameter, so both tools grade one book
with one set of functions and print the same numbers. Money is ``Decimal`` throughout; nothing
here is ever a binary float.

The request batch (every PR 2b tool)
------------------------------------
:func:`request_symbols` is the one ``quote_cache.get_quotes`` batch of a run: the compact symbol of
every OPEN option leg whose last trading day is on or after today's America/New_York date, plus
the underlying of every such leg, plus the symbol of every open stock-like leg, sorted and
de-duplicated. Chains are never fetched. An open option row whose last trading day has passed is
``csv_recon.py``'s to reconcile and is left out of every sheet (a sheet-level warning names it).

Dividend tier (short calls; the rulebook's EXDIV geometry, bounded by the contract's life)
------------------------------------------------------------------------------------------
For every open SHORT CALL whose underlying has an ex-dividend date inside the window ``(today,
today + 5 trading days]`` (``market_clock.trading_days_between``; exclusive of today, inclusive
of tomorrow) AND whose last trading day is on or after the LAST CUM-DIVIDEND DAY, the trading day
before the ex-date (``market_clock.previous_trading_day``; the calendar's full closures move it).
A dividend-capture early exercise of a short call happens at the close of that cum-dividend day
(OCC/Cboe: shares acquired through an exercise on the cum-dividend day carry the dividend; from
the ex-date on they do not), so a call whose last trading day falls before it no longer exists
when the exercise would happen: it carries no dividend-related early-assignment risk, nothing is
listed, and its assignment risk at expiry is the pin tier's (``ITM`` / ``PIN HIGH``) to grade.
When the cum-dividend day IS the last trading day (the ex-date is the first trading day after
it) the leg is graded, because a dividend-capture exercise would come at expiration, and carries
:data:`BOUNDARY_NOTE` (``ex-dividend <date> is the first trading day after the last trading day
<date>: a dividend-capture exercise would come at expiration``, printed as an extra line). The
earlier behaviour followed the addendum and was withdrawn: it bounded the window by trading days
from today only, graded the fixture's Nov-20 call against the Nov-24 ex-date with a note that the
ex-date lay beyond the last trading day, and on an untagged row raised a HIGH alert (exit 10)
for a risk that cannot occur. The geometry: ``extrinsic_bid = bid - max(0, S_mid - K)`` with
the call's bid and the underlying's mid stamped within :data:`SPOT_ALIGNMENT_S` seconds of it.
The call's bid is the side ``quote_qc`` lets a rule use (``QuoteQC.mark_liq_long``: present and
fresh; a wide or zero-bid quote graded E still has a usable bid, which is why the rule is bid-side;
a stale, missing, undated or crossed quote has none); the underlying's mid must be a usable mark
(grade A-D). ``extrinsic_bid < amount`` is ``HIGH``, ``< 2 x amount`` is ``WATCH``, otherwise ``LOW``;
``#assign-ok`` on the row turns ``HIGH`` into ``NOTED`` (the owner accepted assignment; the
arithmetic is still printed). A dividend whose status is ``unknown``, a symbol absent from the
calendar, no calendar file at all, or a listed ex-date that has already passed (the next one is
not in the file) is ``UNAVAILABLE``: unknown is never zero. A quote the rules may not use (grade
E or F, missing, unaligned) makes the leg ``UNAVAILABLE`` too, with the dividend facts kept. A
``none`` status (a confirmed absence), an ex-date beyond the window, or a contract gone before the
cum-dividend day is nothing to list.

Pin tier (every option leg on its last or next-to-last trading day)
-------------------------------------------------------------------
``|S_mid - K| / S_mid x 100`` quantised to two decimals (``ROUND_HALF_EVEN``); the bands are
closed on that printed value so the number a reader sees and the tier agree: ``<= 1 %`` is
``PIN HIGH``, ``<= 3 %`` is ``PIN WATCH``; beyond that an in-the-money leg (the $0.01 rule:
``S >= K + 0.01`` for a call, ``S <= K - 0.01`` for a put) is ``ITM`` and an out-of-the-money leg
is ``PIN LOW``. Every in-the-money leg carries the line ``in the money: automatic exercise or
assignment expected at the close`` on its last trading day (dated, the day before). The pin tier
needs only the underlying's quote; without a usable one it is ``UNAVAILABLE``.

Act-by, settlement, implications
--------------------------------
The act-by instant is the owner's ``broker_exercise_cutoff_et`` (``desk.toml``) on the leg's LAST
TRADING DAY, printed in ET and UTC (``2026-11-20 16:30 ET`` = ``21:30 UTC``). On an early-close day
(13:00 ET, ``market_clock.is_early_close``) the act-by is the EARLIER of the configured cutoff and
the session close: a deadline after the market has closed is a wrong instruction time, not a
conservative one. Such an act-by is marked ``early close 13:00 ET: confirm your broker's cutoff``
(``ActBy.early_close``, ``act_by_early_close`` in the JSON) because the broker's own early-close
cutoff is not in ``desk.toml``; a cutoff already before the close stands and is marked the same way.
The calendar's closures are full closures and move the last trading day itself.
Settlement is ``am`` when the OCC root is in the calendar's ``am_settled_roots`` (the last trading
day is then the day before the settlement morning), else ``pm``. Each leg's docket implication
is one sentence: shares called away for a short call (with the group's coverage), cash needed for
a short put (``K x 100 x contracts``), the delivery of a long put or the cash of a long call. A
vertical's legs carry the between-the-strikes check: ``never into expiration with the stock
between the strikes``.

Exit codes
----------
``assignment_exit_code``: 10 when any item fired (``HIGH``, ``PIN HIGH``, ``ITM`` on its last
trading day, a vertical between the strikes on a due leg), 20 when any leg or group is
UNAVAILABLE and nothing fired, 0 otherwise. ``checklist_exit_code``: 20 when a quarantined group
exists or a listed item is UNAVAILABLE (the sheet is incomplete), 10 when items are listed, 0
when nothing is due. UNAVAILABLE is a verdict, never HOLD.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import calendar_events, contract, grouping, market_clock, occ, quote_cache
from .calendar_events import CalendarFile
from .grouping import PositionGroup
from .market_clock import SessionInfo
from .positions_csv import PositionRow, PositionsFile
from .quote_qc import USABLE_GRADES, QuoteQC

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

DIVIDEND_WINDOW_TRADING_DAYS: int = 5
"""Trading days after today (inclusive of tomorrow) inside which an ex-dividend is graded, for a
contract whose last trading day is on or after the last cum-dividend day (module notes)."""
PIN_HIGH_PCT: Decimal = Decimal("1")
PIN_WATCH_PCT: Decimal = Decimal("3")
PCT_QUANTUM: Decimal = Decimal("0.01")
PENNY: Decimal = Decimal("0.01")
"""The $0.01 in-the-money rule."""
SPOT_ALIGNMENT_S: int = 60
"""Seconds the underlying's as-of may differ from the option's for the extrinsic arithmetic."""
SHARES_PER_CONTRACT: Decimal = grouping.SHARES_PER_CONTRACT

SETTLEMENT_PM: str = "pm"
SETTLEMENT_AM: str = "am"

TIER_HIGH: str = "HIGH"
TIER_WATCH: str = "WATCH"
TIER_LOW: str = "LOW"
TIER_NOTED: str = "NOTED"
TIER_UNAVAILABLE: str = "UNAVAILABLE"
DIVIDEND_TIERS: Tuple[str, ...] = (TIER_HIGH, TIER_WATCH, TIER_LOW, TIER_NOTED, TIER_UNAVAILABLE)

PIN_HIGH: str = "PIN HIGH"
PIN_WATCH: str = "PIN WATCH"
PIN_LOW: str = "PIN LOW"
PIN_ITM: str = "ITM"
PIN_UNAVAILABLE: str = TIER_UNAVAILABLE
PIN_TIERS: Tuple[str, ...] = (PIN_HIGH, PIN_WATCH, PIN_LOW, PIN_ITM, PIN_UNAVAILABLE)

MODE_WEEK: str = "week"
MODE_TOMORROW: str = "tomorrow"
MODE_FINAL_CALL: str = "final-call"
MODES: Tuple[str, ...] = (MODE_WEEK, MODE_TOMORROW, MODE_FINAL_CALL)

ITM_LINE_TODAY: str = "in the money: automatic exercise or assignment expected at the close"
ITM_LINE_LATER: str = "in the money: automatic exercise or assignment expected at the close of %s"
REASON_UNKNOWN_DIVIDEND: str = "unknown dividend is never zero"
ASSIGN_OK_NOTE: str = "#assign-ok (owner accepted assignment)"
BETWEEN_LINE: str = "never into expiration with the stock between the strikes"
BOUNDARY_NOTE: str = ("ex-dividend %s is the first trading day after the last trading day %s: a dividend-capture exercise "
                      "would come at expiration")
"""The dividend-tier note of a short call whose last trading day is the last cum-dividend day (module notes)."""
EARLY_CLOSE_MARKER: str = "early close %s ET: confirm your broker's cutoff"
WARNING_EXPIRED: str = "%d expired open option leg%s left to csv_recon.py: %s"

WEEKDAYS: Tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_ZERO = Decimal(0)
_TWO = Decimal(2)


# --------------------------------------------------------------------------------------------
# Rendering helpers (shared by both tools so every number prints the same way)
# --------------------------------------------------------------------------------------------


def plain(value: Decimal) -> str:
    """``105.000`` -> ``105``, ``6.500`` -> ``6.5``, ``2`` -> ``2``: a strike or a count for prose."""
    value = plain_decimal(value)
    return str(value)


def plain_decimal(value: Decimal) -> Decimal:
    """The same number without trailing zeros (``Decimal('105.000')`` -> ``Decimal('105')``), so
    that arithmetic on it carries no spurious precision (``106.43 - 105`` is ``1.43``)."""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def weekday_date(day: datetime.date) -> str:
    """``Fri 2026-11-20``: the weekday is spelled by this module, never by the locale."""
    return "%s %s" % (WEEKDAYS[day.weekday()], day.isoformat())


def _money(value: Optional[Decimal]) -> str:
    return quote_cache.format_money(value)


def _plural(count: int, noun: str) -> str:
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def _usable(qc: Optional[QuoteQC]) -> bool:
    return qc is not None and qc.usable_for_rules


def _quote_reason(symbol: str, qc: Optional[QuoteQC]) -> str:
    if qc is None:
        return "%s quote unavailable (no quote)" % symbol
    detail = "; ".join(qc.reasons) if qc.reasons else qc.label
    return "%s quote unavailable (%s %s: %s)" % (symbol, qc.grade, qc.status, detail)


def distance_pct(spot: Decimal, strike: Decimal) -> Decimal:
    """``|S - K| / S x 100`` quantised to two decimals, ``ROUND_HALF_EVEN``."""
    return (abs(spot - strike) / spot * 100).quantize(PCT_QUANTUM, rounding=ROUND_HALF_EVEN)


def in_the_money(right: str, spot: Decimal, strike: Decimal) -> bool:
    """The $0.01 rule: ``S >= K + 0.01`` for a call, ``S <= K - 0.01`` for a put."""
    if right == occ.CALL:
        return spot >= strike + PENNY
    return spot <= strike - PENNY


# --------------------------------------------------------------------------------------------
# Settlement, last trading day, act-by
# --------------------------------------------------------------------------------------------


def is_am_settled(row: PositionRow, cal: Optional[CalendarFile]) -> bool:
    """True when the OCC root (or its base root) is in the calendar's ``am_settled_roots``."""
    if row.occ is None:
        return False
    return (calendar_events.is_am_settled(cal, row.occ.root)
            or calendar_events.is_am_settled(cal, row.occ.base_root))


def settlement_of(row: PositionRow, cal: Optional[CalendarFile]) -> str:
    return SETTLEMENT_AM if is_am_settled(row, cal) else SETTLEMENT_PM


def leg_last_trading_day(row: PositionRow, cal: Optional[CalendarFile]) -> datetime.date:
    """``market_clock.last_trading_day`` of the leg with the calendar's closures injected."""
    if row.occ is None:
        raise ValueError("row %d is not an option leg" % row.row_number)
    return market_clock.last_trading_day(row.occ.expiration, is_am_settled(row, cal), calendar_events.extra_closures(cal))


@dataclass(frozen=True)
class ActBy:
    """The instruction deadline on the leg's last trading day (module notes): ``cutoff_et`` is the time
    used (the configured ``broker_cutoff_et``, or the session close when that is earlier on an
    early-close day, ``early_close`` True)."""

    last_trading_day: datetime.date
    cutoff_et: datetime.time
    utc: datetime.datetime
    passed: bool
    early_close: bool = False
    broker_cutoff_et: Optional[datetime.time] = None

    @property
    def et_text(self) -> str:
        return "%s %s ET" % (self.last_trading_day.isoformat(), self.cutoff_et.strftime("%H:%M"))

    @property
    def utc_text(self) -> str:
        return "%s UTC" % self.utc.astimezone(UTC).strftime("%H:%M")

    @property
    def early_close_text(self) -> Optional[str]:
        """``early close 13:00 ET: confirm your broker's cutoff`` on an early-close day, else ``None``."""
        if not self.early_close:
            return None
        close = market_clock.session_close_et(self.last_trading_day)
        return EARLY_CLOSE_MARKER % close.strftime("%H:%M")


def act_by(last_trading_day: datetime.date, cutoff_et: datetime.time, now: datetime.datetime,
           extra_holidays: market_clock.ExtraHolidays = ()) -> ActBy:
    """``cutoff_et`` (a naive ET time, ``desk.toml``'s ``broker_exercise_cutoff_et``) on
    ``last_trading_day`` as an aware UTC instant, or the session close when ``last_trading_day`` is an
    early-close day and the close comes first (module notes); ``passed`` when ``now`` is at or after
    it. ``extra_holidays`` are the calendar's full closures (they never make a day an early close)."""
    if not isinstance(cutoff_et, datetime.time) or cutoff_et.tzinfo is not None:
        raise ValueError("cutoff_et must be a naive datetime.time in ET, got %r" % (cutoff_et,))
    used = cutoff_et
    early = market_clock.is_early_close(last_trading_day, extra_holidays)
    if early:
        close = market_clock.session_close_et(last_trading_day, extra_holidays=extra_holidays)
        if close < used:
            used = close
    instant = market_clock.from_et(datetime.datetime.combine(last_trading_day, used))
    return ActBy(last_trading_day, used, instant, now >= instant, early, cutoff_et)


def act_by_text(act: ActBy, today: datetime.date, next_trading_day: datetime.date) -> str:
    """``act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]``; the bracket names ``today``,
    ``next trading day`` and ``passed`` when they apply, and is absent otherwise."""
    markers: List[str] = []
    if act.last_trading_day == today:
        markers.append("today")
    elif act.last_trading_day == next_trading_day:
        markers.append("next trading day")
    if act.passed:
        markers.append("passed")
    text = "act by %s %s ET (%s)" % (weekday_date(act.last_trading_day), act.cutoff_et.strftime("%H:%M"), act.utc_text)
    if markers:
        text += " [%s]" % ", ".join(markers)
    early = act.early_close_text
    if early is not None:
        text += "; " + early
    return text


# --------------------------------------------------------------------------------------------
# The request batch
# --------------------------------------------------------------------------------------------


def request_symbols(rows: Iterable[PositionRow], today: datetime.date, cal: Optional[CalendarFile]) -> List[str]:
    """The one quote batch of a run (module notes): sorted, de-duplicated."""
    wanted = set()
    for row in rows:
        if not row.is_open:
            continue
        if row.is_option:
            if row.occ is None:
                continue
            if leg_last_trading_day(row, cal) >= today:
                wanted.add(row.compact_symbol)
                wanted.add(row.underlying)
        else:
            wanted.add(row.compact_symbol)
    return sorted(wanted)


# --------------------------------------------------------------------------------------------
# Dividend tier
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DividendTier:
    """The EXDIV geometry of one short call (module notes). The dividend facts are kept on an
    UNAVAILABLE verdict whenever they are known; ``reason`` says what is missing."""

    tier: str
    dividend_status: str
    ex_date: Optional[datetime.date]
    trading_days_to_ex: Optional[int]
    amount: Optional[Decimal]
    bid: Optional[Decimal]
    spot_mid: Optional[Decimal]
    intrinsic: Optional[Decimal]
    extrinsic_bid: Optional[Decimal]
    itm: Optional[bool]
    downgraded_from: Optional[str]
    reason: str
    notes: Tuple[str, ...] = ()
    option_grade: Optional[str] = None

    @property
    def fired(self) -> bool:
        return self.tier == TIER_HIGH

    @property
    def unavailable(self) -> bool:
        return self.tier == TIER_UNAVAILABLE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "dividend_status": self.dividend_status,
            "ex_date": self.ex_date.isoformat() if self.ex_date is not None else None,
            "trading_days_to_ex": self.trading_days_to_ex,
            "amount": self.amount,
            "bid": self.bid,
            "spot_mid": self.spot_mid,
            "intrinsic": self.intrinsic,
            "extrinsic_bid": self.extrinsic_bid,
            "itm": self.itm,
            "downgraded_from": self.downgraded_from,
            "reason": self.reason,
            "notes": list(self.notes),
            "option_grade": self.option_grade,
            "fired": self.fired,
            "unavailable": self.unavailable,
        }


def _dividend_unavailable(reason: str, status: str = calendar_events.STATUS_UNKNOWN, ex_date: Optional[datetime.date] = None,
                          days: Optional[int] = None, amount: Optional[Decimal] = None,
                          notes: Tuple[str, ...] = (), option_grade: Optional[str] = None) -> DividendTier:
    return DividendTier(TIER_UNAVAILABLE, status, ex_date, days, amount, None, None, None, None, None, None, reason, notes,
                        option_grade)


def dividend_tier(row: PositionRow, option_qc: Optional[QuoteQC], underlying_qc: Optional[QuoteQC],
                  cal: Optional[CalendarFile], today: datetime.date,
                  extra_holidays: market_clock.ExtraHolidays = ()) -> Optional[DividendTier]:
    """The dividend tier of ``row`` (module notes), or ``None`` when there is nothing to list: the
    row is not an open short call, the dividend is a confirmed absence, the contract's last trading
    day falls before the last cum-dividend day (the trading day before the ex-date, when a
    dividend-capture exercise happens: the contract is gone by then), or the ex-date lies beyond
    the five-trading-day window. A cum-dividend day equal to the last trading day is graded and
    noted (:data:`BOUNDARY_NOTE`). ``extra_holidays`` are the calendar's full closures: they move
    the cum-dividend day and the trading-day count (``build_sheet`` passes ``cal``'s own)."""
    if not (row.is_open and row.is_option and row.is_short and row.is_call) or row.occ is None:
        return None
    symbol = row.underlying
    unknown = "dividend unknown: %s (%s)"
    if cal is None:
        return _dividend_unavailable(unknown % ("no research/calendar.toml", REASON_UNKNOWN_DIVIDEND))
    events = cal.events_for(symbol)
    if not events.listed:
        return _dividend_unavailable(unknown % ("%s is not in calendar.toml" % symbol, REASON_UNKNOWN_DIVIDEND))
    entry = events.ex_dividend
    if entry is None:
        return _dividend_unavailable(unknown % ("no ex_dividend entry for %s" % symbol, REASON_UNKNOWN_DIVIDEND))
    if entry.status == calendar_events.STATUS_NONE:
        return None
    if entry.status == calendar_events.STATUS_UNKNOWN or entry.date is None or entry.amount is None:
        return _dividend_unavailable(unknown % ('%s ex_dividend status "%s"' % (symbol, entry.status), REASON_UNKNOWN_DIVIDEND),
                                     entry.status, entry.date)
    if entry.date <= today:
        return _dividend_unavailable(
            unknown % ("the %s ex-dividend %s has passed; the next is not in calendar.toml" % (symbol, entry.date.isoformat()),
                       REASON_UNKNOWN_DIVIDEND), entry.status, entry.date, None, entry.amount)
    cum_day = market_clock.previous_trading_day(entry.date, extra_holidays)  # the last cum-dividend day
    last_day = leg_last_trading_day(row, cal)
    if cum_day > last_day:
        return None  # the contract is gone before the day a dividend-capture exercise happens
    days = market_clock.trading_days_between(today, entry.date, extra_holidays)
    if days > DIVIDEND_WINDOW_TRADING_DAYS:
        return None
    notes: List[str] = []
    if cum_day == last_day:
        notes.append(BOUNDARY_NOTE % (entry.date.isoformat(), last_day.isoformat()))
    grade = option_qc.grade if option_qc is not None else None
    if option_qc is not None and option_qc.grade not in USABLE_GRADES and option_qc.mark_liq_long is not None:
        notes.append("option quote grade %s (%s): the bid-side arithmetic stands"
                     % (option_qc.grade, "; ".join(option_qc.reasons) if option_qc.reasons else option_qc.label))
    facts = (entry.status, entry.date, days, entry.amount, tuple(notes), grade)
    if option_qc is None or option_qc.mark_liq_long is None:
        return _dividend_unavailable(_quote_reason(row.compact_symbol, option_qc), *facts)
    if not _usable(underlying_qc) or underlying_qc is None or underlying_qc.mark_mid is None:
        return _dividend_unavailable(_quote_reason(symbol, underlying_qc), *facts)
    if option_qc.as_of is not None and underlying_qc.as_of is not None:
        skew = abs((underlying_qc.as_of - option_qc.as_of).total_seconds())
        if skew > SPOT_ALIGNMENT_S:
            return _dividend_unavailable("spot unaligned: %s stamped %d s from %s (limit %d s)"
                                         % (symbol, int(skew), row.compact_symbol, SPOT_ALIGNMENT_S), *facts)
    strike = plain_decimal(row.occ.strike)
    spot = underlying_qc.mark_mid
    bid = option_qc.mark_liq_long
    intrinsic = max(_ZERO, spot - strike)
    extrinsic = bid - intrinsic
    itm = in_the_money(occ.CALL, spot, strike)
    amount = entry.amount
    if extrinsic < amount:
        tier = TIER_HIGH
    elif extrinsic < _TWO * amount:
        tier = TIER_WATCH
    else:
        tier = TIER_LOW
    downgraded: Optional[str] = None
    if tier == TIER_HIGH and row.assign_ok:
        tier, downgraded = TIER_NOTED, TIER_HIGH
    return DividendTier(tier, entry.status, entry.date, days, amount, bid, spot, intrinsic, extrinsic, itm, downgraded, "",
                        tuple(notes), grade)


# --------------------------------------------------------------------------------------------
# Pin tier
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PinTier:
    """Where the underlying's mid stands against the strike on a last or next-to-last trading day."""

    tier: str
    spot_mid: Optional[Decimal]
    strike: Decimal
    distance_pct: Optional[Decimal]
    itm: Optional[bool]
    on_last_trading_day: bool
    line: Optional[str]
    reason: str

    @property
    def fired(self) -> bool:
        return self.tier == PIN_HIGH or (self.tier == PIN_ITM and self.on_last_trading_day)

    @property
    def unavailable(self) -> bool:
        return self.tier == PIN_UNAVAILABLE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "spot_mid": self.spot_mid,
            "strike": plain(self.strike),
            "distance_pct": self.distance_pct,
            "itm": self.itm,
            "on_last_trading_day": self.on_last_trading_day,
            "line": self.line,
            "reason": self.reason,
            "fired": self.fired,
            "unavailable": self.unavailable,
        }


def pin_tier(row: PositionRow, underlying_qc: Optional[QuoteQC], today: datetime.date, next_trading_day: datetime.date,
             last_trading_day: datetime.date) -> Optional[PinTier]:
    """The pin tier of ``row`` (module notes), or ``None`` when its last trading day is neither
    ``today`` nor ``next_trading_day``."""
    if not (row.is_open and row.is_option) or row.occ is None:
        return None
    if last_trading_day not in (today, next_trading_day):
        return None
    on_last = last_trading_day == today
    strike = row.occ.strike
    if not _usable(underlying_qc) or underlying_qc is None or underlying_qc.mark_mid is None:
        return PinTier(PIN_UNAVAILABLE, None, strike, None, None, on_last, None, _quote_reason(row.underlying, underlying_qc))
    spot = underlying_qc.mark_mid
    pct = distance_pct(spot, plain_decimal(strike))
    itm = in_the_money(row.occ.right, spot, plain_decimal(strike))
    if pct <= PIN_HIGH_PCT:
        tier = PIN_HIGH
    elif pct <= PIN_WATCH_PCT:
        tier = PIN_WATCH
    elif itm:
        tier = PIN_ITM
    else:
        tier = PIN_LOW
    line: Optional[str] = None
    if itm:
        line = ITM_LINE_TODAY if on_last else ITM_LINE_LATER % weekday_date(last_trading_day)
    return PinTier(tier, spot, strike, pct, itm, on_last, line, "")


# --------------------------------------------------------------------------------------------
# Verticals and implications
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VerticalCheck:
    """The between-the-strikes check of a vertical's legs (one check, shared by both legs)."""

    low_strike: Decimal
    high_strike: Decimal
    spot_mid: Optional[Decimal]
    between: Optional[bool]
    message: str

    @property
    def fired(self) -> bool:
        return self.between is True

    @property
    def unavailable(self) -> bool:
        return self.between is None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "low_strike": plain(self.low_strike),
            "high_strike": plain(self.high_strike),
            "spot_mid": self.spot_mid,
            "between": self.between,
            "message": self.message,
            "fired": self.fired,
            "unavailable": self.unavailable,
        }


def vertical_check(group: PositionGroup, underlying_qc: Optional[QuoteQC]) -> Optional[VerticalCheck]:
    """``None`` for anything but a vertical with two distinct strikes among its open legs."""
    if group.structure not in grouping.VERTICAL_STRUCTURES:
        return None
    legs = [leg for leg in group.open_option_legs if leg.occ is not None]
    strikes = sorted({leg.occ.strike for leg in legs if leg.occ is not None})
    if len(strikes) != 2:
        return None
    low, high = strikes
    if not _usable(underlying_qc) or underlying_qc is None or underlying_qc.mark_mid is None:
        return VerticalCheck(low, high, None, None, "mid unavailable: the between-the-strikes check cannot run (%s)"
                             % _quote_reason(group.underlying, underlying_qc))
    spot = underlying_qc.mark_mid
    strikes_text = "%s and %s" % (plain(low), plain(high))
    right = legs[0].occ.right if legs[0].occ is not None else occ.CALL
    if low < spot < high:
        return VerticalCheck(low, high, spot, True, "the underlying %s is between the strikes %s: %s"
                             % (_money(spot), strikes_text, BETWEEN_LINE))
    if spot == low or spot == high:
        return VerticalCheck(low, high, spot, False, "the underlying %s is at the strike %s: pin risk on that leg"
                             % (_money(spot), plain(spot)))
    above = spot > high
    both_itm = above if right == occ.CALL else not above
    return VerticalCheck(low, high, spot, False, "the underlying %s is %s both strikes %s (both legs %s the money)"
                         % (_money(spot), "above" if above else "below", strikes_text, "in" if both_itm else "out of"))


def _shares_held(group: PositionGroup) -> Decimal:
    return sum((leg.open_quantity for leg in group.stock_legs), _ZERO)


def _coverage(held: Decimal, shares: Decimal, verb: str) -> str:
    if held >= shares:
        return "%sthe %s shares held" % (verb + " " if verb else "", plain(held))
    if held > _ZERO:
        return "%s of the %s shares are held" % (plain(held), plain(shares))
    return "no shares in the group: short stock would result"


def implication(row: PositionRow, group: PositionGroup) -> str:
    """One sentence on what expiration does to the group when the leg finishes in the money."""
    if row.occ is None:
        return ""
    contracts = abs(row.open_quantity)
    shares = contracts * SHARES_PER_CONTRACT
    strike = plain_decimal(row.occ.strike)
    underlying = row.underlying
    cash = _money(strike * SHARES_PER_CONTRACT * contracts)
    held = _shares_held(group)
    if row.is_call and row.is_short:
        return "shares called away if assigned: %s %s at %s (%s)" % (plain(shares), underlying, plain(strike),
                                                                   _coverage(held, shares, "covered by"))
    if row.is_put and row.is_short:
        return "cash needed if assigned: %s for %s %s shares at %s (%s x 100 x %s)" % (
            cash, plain(shares), underlying, plain(strike), plain(strike), plain(contracts))
    if row.is_put:
        return "exercise would deliver %s %s shares at %s (%s)" % (plain(shares), underlying, plain(strike),
                                                                 _coverage(held, shares, ""))
    return "exercise would take %s %s shares at %s for %s cash (%s x 100 x %s)" % (
        plain(shares), underlying, plain(strike), cash, plain(strike), plain(contracts))


def week_bounds(today: datetime.date) -> Tuple[datetime.date, datetime.date]:
    """Monday and Sunday of the ET week that holds ``today``."""
    monday = today - datetime.timedelta(days=today.weekday())
    return monday, monday + datetime.timedelta(days=6)


# --------------------------------------------------------------------------------------------
# The sheet
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LegSheet:
    """One open option leg with its contract facts, implication, act-by and tiers."""

    row: PositionRow = field(repr=False, compare=False)
    group: PositionGroup = field(repr=False, compare=False)
    symbol: str = ""
    underlying: str = ""
    settlement: str = SETTLEMENT_PM
    last_trading_day: datetime.date = datetime.date(1970, 1, 1)
    dte_trading: int = 0
    act_by: ActBy = field(default=None)  # type: ignore[assignment]
    act_by_text: str = ""
    implication: str = ""
    vertical: Optional[VerticalCheck] = None
    dividend: Optional[DividendTier] = None
    pin: Optional[PinTier] = None
    due_today: bool = False
    due_tomorrow: bool = False
    in_week: bool = False

    @property
    def row_number(self) -> int:
        return self.row.row_number

    @property
    def trade_id(self) -> Optional[str]:
        return self.row.trade_id

    @property
    def group_id(self) -> str:
        return self.row.group_id

    @property
    def account(self) -> str:
        return self.row.account

    @property
    def structure(self) -> str:
        return self.group.structure

    @property
    def right(self) -> str:
        return self.row.occ.right if self.row.occ is not None else ""

    @property
    def strike(self) -> Decimal:
        return self.row.occ.strike if self.row.occ is not None else _ZERO

    @property
    def expiration(self) -> datetime.date:
        return self.row.occ.expiration if self.row.occ is not None else self.last_trading_day

    @property
    def quantity(self) -> Decimal:
        return self.row.open_quantity

    @property
    def contracts(self) -> Decimal:
        return abs(self.row.open_quantity)

    @property
    def side(self) -> str:
        return self.row.side

    @property
    def assign_ok(self) -> bool:
        return self.row.assign_ok

    @property
    def in_pin_window(self) -> bool:
        return self.due_today or self.due_tomorrow

    @property
    def fired(self) -> bool:
        return ((self.dividend is not None and self.dividend.fired)
                or (self.pin is not None and self.pin.fired)
                or (self.vertical is not None and self.vertical.fired))

    @property
    def unavailable_reasons(self) -> List[str]:
        reasons: List[str] = []
        if self.dividend is not None and self.dividend.unavailable:
            reasons.append(self.dividend.reason)
        if self.pin is not None and self.pin.unavailable:
            reasons.append(self.pin.reason)
        if self.vertical is not None and self.vertical.unavailable:
            reasons.append(self.vertical.message)
        return reasons

    @property
    def head(self) -> str:
        """``AG-20261020-01 WMT261120C00105000 short 2``."""
        return "%s %s %s %s" % (self.trade_id or self.group_id, self.symbol, self.side, plain(self.contracts))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "row_number": self.row_number,
            "trade_id": self.trade_id,
            "group_id": self.group_id,
            "account": self.account,
            "structure": self.structure,
            "symbol": self.symbol,
            "underlying": self.underlying,
            "right": self.right,
            "strike": plain(self.strike),
            "expiration": self.expiration.isoformat(),
            "settlement": self.settlement,
            "last_trading_day": self.last_trading_day.isoformat(),
            "dte_trading": self.dte_trading,
            "quantity": self.quantity,
            "contracts": self.contracts,
            "side": self.side,
            "assign_ok": self.assign_ok,
            "act_by_utc": self.act_by.utc,
            "act_by_et": self.act_by.et_text,
            "act_by_utc_text": self.act_by.utc_text,
            "act_by_passed": self.act_by.passed,
            "act_by_early_close": self.act_by.early_close,
            "act_by_text": self.act_by_text,
            "implication": self.implication,
            "vertical": self.vertical.as_dict() if self.vertical is not None else None,
            "dividend": self.dividend.as_dict() if self.dividend is not None else None,
            "pin": self.pin.as_dict() if self.pin is not None else None,
            "due_today": self.due_today,
            "due_tomorrow": self.due_tomorrow,
            "in_week": self.in_week,
            "fired": self.fired,
            "unavailable_reasons": self.unavailable_reasons,
        }


@dataclass(frozen=True)
class ExpirySheet:
    """Every open option leg still ahead of its last trading day, graded at one instant."""

    today: datetime.date
    next_trading_day: datetime.date
    week_start: datetime.date
    week_end: datetime.date
    legs: List[LegSheet]
    expired: List[PositionRow]
    quarantined: Dict[str, List[str]]
    warnings: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "today": self.today.isoformat(),
            "next_trading_day": self.next_trading_day.isoformat(),
            "week_start": self.week_start.isoformat(),
            "week_end": self.week_end.isoformat(),
            "legs": [leg.as_dict() for leg in self.legs],
            "expired": [{"row_number": row.row_number, "symbol": row.compact_symbol,
                         "expiration": row.expiration.isoformat() if row.expiration is not None else None}
                        for row in self.expired],
            "quarantined": {key: list(value) for key, value in self.quarantined.items()},
            "warnings": list(self.warnings),
        }


def build_sheet(positions: PositionsFile, groups: Sequence[PositionGroup], *, now: datetime.datetime, session: SessionInfo,
                cal: Optional[CalendarFile], broker_cutoff_et: datetime.time, qcs: Dict[str, QuoteQC]) -> ExpirySheet:
    """Grade every open option leg of ``groups`` at ``now`` (module notes). ``qcs`` maps every
    requested symbol to its :class:`QuoteQC`; a symbol absent from it counts as unavailable."""
    extra = calendar_events.extra_closures(cal)
    today = session.et_date
    next_day = market_clock.next_trading_day(today, extra)
    week_start, week_end = week_bounds(today)
    quarantined: Dict[str, List[str]] = {group_id: [str(error) for error in errors]
                                         for group_id, errors in positions.quarantined.items()}
    for group in groups:
        if group.is_quarantined:
            quarantined.setdefault(group.group_id, []).extend(group.errors)
    legs: List[LegSheet] = []
    expired: List[PositionRow] = []
    for group in groups:
        if group.group_id in quarantined:
            continue
        for row in group.open_option_legs:
            if row.occ is None:
                continue
            last_day = leg_last_trading_day(row, cal)
            if last_day < today:
                expired.append(row)
                continue
            due_today = last_day == today
            due_tomorrow = last_day == next_day
            in_window = due_today or due_tomorrow
            underlying_qc = qcs.get(row.underlying)
            act = act_by(last_day, broker_cutoff_et, now, extra)
            legs.append(LegSheet(
                row=row,
                group=group,
                symbol=row.compact_symbol,
                underlying=row.underlying,
                settlement=settlement_of(row, cal),
                last_trading_day=last_day,
                dte_trading=market_clock.trading_days_between(today, last_day, extra),
                act_by=act,
                act_by_text=act_by_text(act, today, next_day),
                implication=implication(row, group),
                vertical=vertical_check(group, underlying_qc) if in_window else None,
                dividend=dividend_tier(row, qcs.get(row.compact_symbol), underlying_qc, cal, today, extra),
                pin=pin_tier(row, underlying_qc, today, next_day, last_day),
                due_today=due_today,
                due_tomorrow=due_tomorrow,
                in_week=week_start <= last_day <= week_end,
            ))
    warnings: List[str] = []
    if expired:
        warnings.append(WARNING_EXPIRED % (len(expired), "" if len(expired) == 1 else "s",
                                           ", ".join("row %d %s" % (row.row_number, row.compact_symbol) for row in expired)))
    return ExpirySheet(today, next_day, week_start, week_end, legs, expired, quarantined, warnings)


# --------------------------------------------------------------------------------------------
# The leg object as the tools' --schema describes it (one dictionary, so it never drifts from as_dict)
# --------------------------------------------------------------------------------------------


LEG_OUTPUT: Dict[str, Any] = {
    "row_number": "integer", "trade_id": "string | null", "group_id": "string", "account": "string", "structure": "string",
    "symbol": "compact OCC symbol", "underlying": "string", "right": "CALL | PUT", "strike": "decimal string (plain)",
    "expiration": "YYYY-MM-DD", "settlement": "pm | am", "last_trading_day": "YYYY-MM-DD",
    "dte_trading": "integer trading days to the last trading day", "quantity": "decimal string (signed open)",
    "contracts": "decimal string", "side": "long | short", "assign_ok": "boolean (#assign-ok on the row)",
    "act_by_utc": "ISO-8601 UTC", "act_by_et": "YYYY-MM-DD HH:MM ET", "act_by_utc_text": "HH:MM UTC",
    "act_by_passed": "boolean",
    "act_by_early_close": "boolean (the act-by is the 13:00 ET close of an early-close day: confirm the broker's cutoff)",
    "act_by_text": "string", "implication": "string",
    "vertical": {"low_strike": "string", "high_strike": "string", "spot_mid": "decimal string | null",
                 "between": "boolean | null", "message": "string", "fired": "boolean", "unavailable": "boolean"},
    "dividend": {"tier": " | ".join(DIVIDEND_TIERS), "dividend_status": "confirmed | estimated | unknown",
                 "ex_date": "YYYY-MM-DD | null", "trading_days_to_ex": "integer | null", "amount": "decimal string | null",
                 "bid": "decimal string | null", "spot_mid": "decimal string | null", "intrinsic": "decimal string | null",
                 "extrinsic_bid": "decimal string | null", "itm": "boolean | null", "downgraded_from": "HIGH | null",
                 "reason": "string (UNAVAILABLE)", "notes": ["string"], "option_grade": "A-F | null",
                 "fired": "boolean", "unavailable": "boolean"},
    "pin": {"tier": " | ".join(PIN_TIERS), "spot_mid": "decimal string | null", "strike": "string",
            "distance_pct": "decimal string (2 dp) | null", "itm": "boolean | null", "on_last_trading_day": "boolean",
            "line": "string | null", "reason": "string (UNAVAILABLE)", "fired": "boolean", "unavailable": "boolean"},
    "due_today": "boolean", "due_tomorrow": "boolean", "in_week": "boolean", "fired": "boolean",
    "unavailable_reasons": ["string"],
}
"""The leg object of ``assignment_watch.py`` and ``expiry_checklist.py`` as ``--schema`` describes it: the keys
of :meth:`LegSheet.as_dict` (the dividend, pin and vertical blocks included), so ``docs/TOOLS.md`` never drifts
from the JSON."""


# --------------------------------------------------------------------------------------------
# Selections and exit codes
# --------------------------------------------------------------------------------------------


def checklist_legs(sheet: ExpirySheet, mode: str) -> List[LegSheet]:
    """The legs a checklist mode lists: the week's, the next trading day's or today's."""
    if mode == MODE_WEEK:
        return [leg for leg in sheet.legs if leg.in_week]
    if mode == MODE_TOMORROW:
        return [leg for leg in sheet.legs if leg.due_tomorrow]
    if mode == MODE_FINAL_CALL:
        return [leg for leg in sheet.legs if leg.due_today]
    raise ValueError("mode must be one of %s, got %r" % (MODES, mode))


def assignment_exit_code(sheet: ExpirySheet) -> int:
    """10 when any leg fired, 20 when any leg or group is UNAVAILABLE, 0 otherwise."""
    if any(leg.fired for leg in sheet.legs):
        return contract.EXIT_FIRED
    if sheet.quarantined or any(leg.unavailable_reasons for leg in sheet.legs):
        return contract.EXIT_UNAVAILABLE
    return contract.EXIT_QUIET


def checklist_exit_code(sheet: ExpirySheet, mode: str) -> int:
    """20 when the sheet is incomplete (a quarantined group, an UNAVAILABLE listed item), 10 when
    items are listed, 0 when nothing is due."""
    legs = checklist_legs(sheet, mode)
    if sheet.quarantined or any(leg.unavailable_reasons for leg in legs):
        return contract.EXIT_UNAVAILABLE
    if legs:
        return contract.EXIT_FIRED
    return contract.EXIT_QUIET


# --------------------------------------------------------------------------------------------
# Lines (both tools print exactly these)
# --------------------------------------------------------------------------------------------


def dividend_line(leg: LegSheet) -> str:
    """``NOTED AG-... WMT261120C00105000 short 2: ex-dividend ... ; act by ...``."""
    tier = leg.dividend
    if tier is None:
        raise ValueError("leg %s has no dividend tier" % leg.symbol)
    parts: List[str] = []
    if tier.unavailable:
        parts.append(tier.reason)
    if tier.ex_date is not None and tier.trading_days_to_ex is not None and tier.amount is not None:
        parts.append("ex-dividend %s in %s (%s), amount %s" % (tier.ex_date.isoformat(),
                                                               _plural(tier.trading_days_to_ex, "trading day"),
                                                               tier.dividend_status, _money(tier.amount)))
    if not tier.unavailable and tier.bid is not None and tier.spot_mid is not None and tier.extrinsic_bid is not None:
        parts.append("extrinsic (bid) %s = bid %s - max(0, %s - %s)" % (_money(tier.extrinsic_bid), _money(tier.bid),
                                                                        _money(tier.spot_mid), plain(leg.strike)))
        parts.append("in the money" if tier.itm else "out of the money")
    if tier.downgraded_from is not None:
        parts.append("%s downgraded to %s: %s" % (tier.downgraded_from, tier.tier, ASSIGN_OK_NOTE))
    parts.append(leg.act_by_text)
    return "%s %s: %s" % (tier.tier, leg.head, "; ".join(parts))


def pin_line(leg: LegSheet) -> str:
    """``PIN HIGH AG-... NVDA261120C00150000 short 2: 0.52 % from the strike (mid ... vs ...), ...``."""
    tier = leg.pin
    if tier is None:
        raise ValueError("leg %s has no pin tier" % leg.symbol)
    if tier.unavailable:
        body = "pin tier not graded: %s" % tier.reason
    else:
        body = "%s %% from the strike (mid %s vs %s), %s" % (tier.distance_pct, _money(tier.spot_mid), plain(tier.strike),
                                                             "in the money" if tier.itm else "out of the money")
    return "%s %s: %s; last trading day %s; %s" % (tier.tier, leg.head, body, weekday_date(leg.last_trading_day),
                                                   leg.act_by_text)


def docket_line(leg: LegSheet) -> str:
    """``AG-... WMT261120C00105000 short 2 | covered_call | pm-settled | last trading day ... | ... | act by ...``."""
    if leg.dte_trading == 0:
        when = "today"
    else:
        when = _plural(leg.dte_trading, "trading day")
    return "%s | %s | %s-settled | last trading day %s (%s) | %s | %s" % (
        leg.head, leg.structure, leg.settlement, weekday_date(leg.last_trading_day), when, leg.implication, leg.act_by_text)


def extra_lines(leg: LegSheet) -> List[str]:
    """Indented follow-up lines: the in-the-money sentence, the vertical check, dividend notes."""
    lines: List[str] = []
    if leg.pin is not None and leg.pin.line is not None:
        lines.append("  " + leg.pin.line)
    if leg.vertical is not None:
        lines.append("  " + leg.vertical.message)
    if leg.dividend is not None:
        lines.extend("  note: " + note for note in leg.dividend.notes)
    return lines


__all__ = [
    "ASSIGN_OK_NOTE",
    "BETWEEN_LINE",
    "BOUNDARY_NOTE",
    "DIVIDEND_TIERS",
    "DIVIDEND_WINDOW_TRADING_DAYS",
    "ActBy",
    "DividendTier",
    "EARLY_CLOSE_MARKER",
    "ExpirySheet",
    "ITM_LINE_LATER",
    "ITM_LINE_TODAY",
    "LEG_OUTPUT",
    "LegSheet",
    "MODES",
    "MODE_FINAL_CALL",
    "MODE_TOMORROW",
    "MODE_WEEK",
    "PENNY",
    "PIN_HIGH",
    "PIN_HIGH_PCT",
    "PIN_ITM",
    "PIN_LOW",
    "PIN_TIERS",
    "PIN_UNAVAILABLE",
    "PIN_WATCH",
    "PIN_WATCH_PCT",
    "PinTier",
    "REASON_UNKNOWN_DIVIDEND",
    "SETTLEMENT_AM",
    "SETTLEMENT_PM",
    "SHARES_PER_CONTRACT",
    "SPOT_ALIGNMENT_S",
    "TIER_HIGH",
    "TIER_LOW",
    "TIER_NOTED",
    "TIER_UNAVAILABLE",
    "TIER_WATCH",
    "VerticalCheck",
    "WARNING_EXPIRED",
    "WEEKDAYS",
    "act_by",
    "act_by_text",
    "assignment_exit_code",
    "build_sheet",
    "checklist_exit_code",
    "checklist_legs",
    "distance_pct",
    "dividend_line",
    "dividend_tier",
    "docket_line",
    "extra_lines",
    "implication",
    "in_the_money",
    "is_am_settled",
    "leg_last_trading_day",
    "pin_line",
    "pin_tier",
    "plain",
    "plain_decimal",
    "request_symbols",
    "settlement_of",
    "vertical_check",
    "week_bounds",
    "weekday_date",
]
