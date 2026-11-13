"""Quote quality control: session-aware, per-side freshness and the A-F grade (plan section 8).

New for Aegis Desk; standard library only, no I/O, no wall clock: every instant is a parameter.
:func:`assess` takes one :class:`aegis_desk.market_types.Quote`, the instant the desk is reasoning
about, the :class:`aegis_desk.market_clock.SessionInfo` of that instant, the freshness bound
(``desk_config.freshness_bound_min``: feed delay + quote TTL + grace, about 25 minutes) and, for
runs before the open, the prior close, and returns a :class:`QuoteQC`: the freshness status, the
grade, the marks a rule may use and the reasons for everything that is not an A.

Freshness is judged PER SIDE against a reference instant that depends on the session
------------------------------------------------------------------------------------
* ``open`` / ``early_close`` (the market is trading): the reference is ``now``. A side is fresh
  when ``now - side_date <= bound_min`` minutes, inclusive on the exact instant: with a 25-minute
  bound and ``now`` 19:50:00Z a side stamped 19:25:00Z is fresh and one stamped 19:24:59Z is
  stale. The reported age floors whole minutes (19:31:12Z is 18 minutes old at 19:50:00Z;
  19:24:59Z reads 25 minutes old while failing the bound by one second).
* ``closed`` (a trading day, at or after the close): the reference is the session's close
  (``SessionInfo.close_utc``, 16:00 ET or 13:00 ET on an early-close day). A side is fresh when
  ``close_utc - side_date <= bound_min`` AND ``side_date <= close_utc + 60 s``: the newest quote
  of a closed session is the closing quote; a later postmarket print is not. Label
  ``as of close HH:MM ET``.
* ``pre`` (a trading day, before 09:30 ET): the same rule against ``prior_close_utc``
  (:func:`aegis_desk.market_clock.previous_close_utc`), which the caller must pass. Label
  ``as of prior close YYYY-MM-DD HH:MM ET``. A premarket print is not a closing quote.
* ``weekend`` / ``holiday``: no freshness verdict at all: status ``closed_day``, label
  ``NYSE closed``, grade F with the single reason ``market closed``.

A side whose stamp lies more than :data:`SKEW_TOLERANCE_S` (60 s) AHEAD of its reference cannot be
dated against it (clock skew, or a replayed ``now``): it is reported stale with a reason, never
fresh. Ages are measured to the reference, never negative. A side with no price is ``missing``; a
present price with no stamp is ``undated`` (the vendor's ``0`` stamp, see ``market_types``).

Grades (both sides fresh and ``bid > 0`` for A-D; ``spread_pct = (ask - bid) / mid * 100``)
----------------------------------------------------------------------------------------
``spread_pct`` is quantised to two decimals (``ROUND_HALF_EVEN``) and the bands are closed on
that printed value, so the number a reader sees and the grade always agree:
A ``<= 2``, B ``<= 5``, C ``<= 10``, D ``<= 25`` or exactly one side stale; E ``bid == 0`` or
``spread_pct > 25`` (the mid is displayed but never a mark for class-M rules); F any side
missing, undated or stale beyond the bound, an ask of 0 under a bid above 0 (Alpaca's ``ap`` 0: no
active offer, status ``missing`` with :data:`REASON_NO_OFFER`, never "crossed"), a crossed market
(``ask < bid``), or a closed day.
``usable_for_rules`` is exactly ``grade in A-D``.

Marks: ``mark_mid`` is the quote's mid, ``mark_liq_long`` the bid (what a long liquidates at),
``mark_liq_short`` the ask (the cost to close a short). An F carries no marks. With exactly one
stale side the mid is NOT invented from the fresh side (``mark_mid`` and ``spread_pct`` are
``None``) and only the fresh side's own liquidation mark is kept. ``last`` is never a mark.

Decision order (so every case is deterministic): closed day; a missing side; a zero ask under a
positive bid; an undated side;
both sides stale; one side stale; a crossed market; ``bid == 0``; the spread bands.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, List, Optional, Tuple

from . import market_clock
from .market_clock import SessionInfo
from .market_types import Quote

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

GRADE_A: str = "A"
GRADE_B: str = "B"
GRADE_C: str = "C"
GRADE_D: str = "D"
GRADE_E: str = "E"
GRADE_F: str = "F"
GRADES: Tuple[str, ...] = (GRADE_A, GRADE_B, GRADE_C, GRADE_D, GRADE_E, GRADE_F)
USABLE_GRADES: Tuple[str, ...] = (GRADE_A, GRADE_B, GRADE_C, GRADE_D)
"""Grades whose marks class-M rules may use; E and F are display-only / UNAVAILABLE."""

STATUS_FRESH: str = "fresh"
STATUS_STALE: str = "stale"
STATUS_ONE_SIDE_STALE: str = "one_side_stale"
STATUS_UNDATED: str = "undated"
STATUS_CLOSED_DAY: str = "closed_day"
STATUS_MISSING: str = "missing"
REASON_NO_OFFER: str = "ask missing (no offer)"
"""The reason for an ask of 0 under a positive bid (Alpaca's ``ap`` 0 means no active offer)."""
STATUSES: Tuple[str, ...] = (STATUS_FRESH, STATUS_STALE, STATUS_ONE_SIDE_STALE, STATUS_UNDATED, STATUS_CLOSED_DAY,
                             STATUS_MISSING)
"""Every freshness status ``QuoteQC.status`` can take."""

SPREAD_BANDS: Tuple[Tuple[Decimal, str], ...] = (
    (Decimal("2"), GRADE_A), (Decimal("5"), GRADE_B), (Decimal("10"), GRADE_C), (Decimal("25"), GRADE_D),
)
"""Upper bound (inclusive) of ``spread_pct`` for each of A-D; above the last band is E."""

WIDE_SPREAD_PCT: Decimal = SPREAD_BANDS[-1][0]
SPREAD_QUANTUM: Decimal = Decimal("0.01")
SKEW_TOLERANCE_S: int = 60
"""Seconds a side's stamp may lie after its reference instant (the close, or ``now``) and still
count as that reference's quote: the brief's ``side_date <= close_utc + 60 s``."""

LABEL_CLOSED_DAY: str = "NYSE closed"
LABEL_UNDATED: str = "undated"
REASON_MARKET_CLOSED: str = "market closed"

_SIDE_FRESH = "fresh"
_SIDE_STALE = "stale"
_SIDE_UNDATED = "undated"
_SIDE_MISSING = "missing"
_MODE_LIVE = "live"
_MODE_CLOSE = "close"
_MODE_PRIOR_CLOSE = "prior_close"


@dataclass(frozen=True)
class QuoteQC:
    """The verdict on one quote (module notes). ``age_min_*`` are whole minutes from the side's
    stamp to the reference instant (``None`` when undated, missing or on a closed day);
    ``spread_pct`` is the two-decimal quoted spread when both prices exist (descriptive: it is
    reported for stale and crossed quotes too, and ``None`` with one stale side); the three marks
    are ``None`` whenever they must not be used; ``bid``, ``ask`` and ``as_of`` repeat the quote
    for display."""

    symbol: str
    grade: str
    status: str
    label: str
    age_min_bid: Optional[int]
    age_min_ask: Optional[int]
    spread_pct: Optional[Decimal]
    mark_mid: Optional[Decimal]
    mark_liq_long: Optional[Decimal]
    mark_liq_short: Optional[Decimal]
    usable_for_rules: bool
    reasons: List[str] = field(default_factory=list)
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    as_of: Optional[datetime.datetime] = None

    def as_dict(self) -> Dict[str, Any]:
        """JSON-ready view in the desk convention (``state.json_dumps`` renders Decimals as strings
        and the as-of as ISO-8601 UTC)."""
        return {
            "symbol": self.symbol,
            "grade": self.grade,
            "status": self.status,
            "label": self.label,
            "age_min_bid": self.age_min_bid,
            "age_min_ask": self.age_min_ask,
            "spread_pct": self.spread_pct,
            "mark_mid": self.mark_mid,
            "mark_liq_long": self.mark_liq_long,
            "mark_liq_short": self.mark_liq_short,
            "usable_for_rules": self.usable_for_rules,
            "reasons": list(self.reasons),
            "bid": self.bid,
            "ask": self.ask,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class _Side:
    name: str
    price: Optional[Decimal]
    stamp: Optional[datetime.datetime]
    state: str
    age_min: Optional[int]
    reason: Optional[str]


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _require_instant(value: Any, what: str) -> datetime.datetime:
    if not isinstance(value, datetime.datetime):
        raise ValueError("%s must be an aware datetime, got %s" % (what, type(value).__name__))
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("%s must be timezone-aware (UTC); a naive datetime is ambiguous" % what)
    return value.astimezone(UTC)


def _fmt_delta(delta: datetime.timedelta) -> str:
    seconds = int(abs(delta.total_seconds()))
    if seconds < 120:
        return "%d s" % seconds
    return "%d min" % (seconds // 60)


def quantize_spread(value: Decimal) -> Decimal:
    """Two decimals, ``ROUND_HALF_EVEN``: the printed spread the grade bands are applied to."""
    return value.quantize(SPREAD_QUANTUM, rounding=ROUND_HALF_EVEN)


def spread_pct(bid: Optional[Decimal], ask: Optional[Decimal]) -> Optional[Decimal]:
    """``(ask - bid) / mid * 100`` quantised to two decimals; ``None`` without both prices or
    when the mid is not positive (a 0/0 market). Negative for a crossed market."""
    if bid is None or ask is None:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return quantize_spread((ask - bid) / mid * 100)


def session_label(session: SessionInfo, prior_close_utc: Optional[datetime.datetime],
                  as_of: Optional[datetime.datetime]) -> str:
    """The freshness label for a session: ``as of HH:MM:SS ET`` (live, from the quote's as-of, or
    ``undated``), ``as of close HH:MM ET``, ``as of prior close YYYY-MM-DD HH:MM ET`` or
    ``NYSE closed``."""
    if session.session in (market_clock.SESSION_WEEKEND, market_clock.SESSION_HOLIDAY):
        return LABEL_CLOSED_DAY
    if session.session == market_clock.SESSION_CLOSED:
        if session.close_utc is None:  # pragma: no cover - a trading day always has a close
            raise ValueError("a closed session needs close_utc")
        return "as of close %s ET" % market_clock.to_et(session.close_utc).strftime("%H:%M")
    if session.session == market_clock.SESSION_PRE:
        if prior_close_utc is None:
            raise ValueError("prior_close_utc is required before the open (market_clock.previous_close_utc)")
        return "as of prior close %s ET" % market_clock.to_et(prior_close_utc).strftime("%Y-%m-%d %H:%M")
    if as_of is None:
        return LABEL_UNDATED
    return "as of %s ET" % market_clock.to_et(as_of).strftime("%H:%M:%S")


def _reference(session: SessionInfo, now: datetime.datetime,
               prior_close_utc: Optional[datetime.datetime]) -> Tuple[str, datetime.datetime]:
    if session.is_live:
        return _MODE_LIVE, now
    if session.session == market_clock.SESSION_CLOSED:
        if session.close_utc is None:  # pragma: no cover - a trading day always has a close
            raise ValueError("a closed session needs close_utc")
        return _MODE_CLOSE, session.close_utc
    if session.session == market_clock.SESSION_PRE:
        if prior_close_utc is None:
            raise ValueError("prior_close_utc is required before the open (market_clock.previous_close_utc)")
        return _MODE_PRIOR_CLOSE, _require_instant(prior_close_utc, "prior_close_utc")
    raise ValueError("unknown session %r" % (session.session,))


def _side(name: str, price: Optional[Decimal], stamp: Optional[datetime.datetime], mode: str,
          reference: datetime.datetime, bound: datetime.timedelta) -> _Side:
    if price is None:
        return _Side(name, None, stamp, _SIDE_MISSING, None, "%s missing" % name)
    if stamp is None:
        return _Side(name, price, None, _SIDE_UNDATED, None, "%s undated" % name)
    delta = reference - stamp
    age_min = max(0, int(delta.total_seconds() // 60))
    if delta < datetime.timedelta(seconds=-SKEW_TOLERANCE_S):
        if mode == _MODE_LIVE:
            reason = "%s stamped %s ahead of now (clock skew?)" % (name, _fmt_delta(delta))
        elif mode == _MODE_CLOSE:
            reason = "%s stamped %s after the close: not the closing quote" % (name, _fmt_delta(delta))
        else:
            reason = "%s stamped %s after the prior close: not the closing quote" % (name, _fmt_delta(delta))
        return _Side(name, price, stamp, _SIDE_STALE, age_min, reason)
    if delta > bound:
        bound_min = int(bound.total_seconds() // 60)
        if mode == _MODE_LIVE:
            reason = "%s stale: %s old > bound %d min" % (name, _fmt_delta(delta), bound_min)
        else:
            reason = "%s stale: stamped %s before the close > bound %d min" % (name, _fmt_delta(delta), bound_min)
        return _Side(name, price, stamp, _SIDE_STALE, age_min, reason)
    return _Side(name, price, stamp, _SIDE_FRESH, age_min, None)


def _band(spread: Decimal) -> str:
    for upper, grade in SPREAD_BANDS:
        if spread <= upper:
            return grade
    return GRADE_E


# --------------------------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------------------------


def assess(quote: Quote, *, now: datetime.datetime, session: SessionInfo, bound_min: int,
           prior_close_utc: Optional[datetime.datetime]) -> QuoteQC:
    """Grade ``quote`` at ``now`` in ``session`` (module notes).

    ``bound_min`` is the freshness bound in whole minutes (``desk_config.freshness_bound_min``);
    ``prior_close_utc`` is required when ``session.session == "pre"`` and ignored otherwise.
    Input errors (a naive ``now``, a negative bound, the wrong types) raise ``ValueError``; a
    quote's state is never an exception.
    """
    if not isinstance(quote, Quote):
        raise ValueError("quote must be a market_types.Quote, got %s" % type(quote).__name__)
    if not isinstance(session, SessionInfo):
        raise ValueError("session must be a market_clock.SessionInfo, got %s" % type(session).__name__)
    if isinstance(bound_min, bool) or not isinstance(bound_min, int) or bound_min < 0:
        raise ValueError("bound_min must be a non-negative int of minutes, got %r" % (bound_min,))
    now = _require_instant(now, "now")
    bid, ask, as_of = quote.bid, quote.ask, quote.as_of
    spread = spread_pct(bid, ask)

    if session.session in (market_clock.SESSION_WEEKEND, market_clock.SESSION_HOLIDAY):
        return QuoteQC(quote.symbol, GRADE_F, STATUS_CLOSED_DAY, LABEL_CLOSED_DAY, None, None, spread, None, None,
                       None, False, [REASON_MARKET_CLOSED], bid, ask, as_of)

    mode, reference = _reference(session, now, prior_close_utc)
    label = session_label(session, prior_close_utc, as_of)
    bound = datetime.timedelta(minutes=bound_min)
    bid_side = _side("bid", bid, quote.bid_date, mode, reference, bound)
    ask_side = _side("ask", ask, quote.ask_date, mode, reference, bound)
    reasons: List[str] = [side.reason for side in (bid_side, ask_side) if side.reason]
    ages = (bid_side.age_min, ask_side.age_min)
    states = (bid_side.state, ask_side.state)

    def result(grade: str, status: str, spread_value: Optional[Decimal], mid: Optional[Decimal],
               liq_long: Optional[Decimal], liq_short: Optional[Decimal]) -> QuoteQC:
        return QuoteQC(quote.symbol, grade, status, label, ages[0], ages[1], spread_value, mid, liq_long, liq_short,
                       grade in USABLE_GRADES, reasons, bid, ask, as_of)

    if _SIDE_MISSING in states:
        return result(GRADE_F, STATUS_MISSING, spread, None, None, None)
    if ask == 0 and bid is not None and bid > 0:
        # Alpaca's ``ap`` 0 is no active offer: an empty ask under a live bid, never a crossed market.
        reasons.append(REASON_NO_OFFER)
        return result(GRADE_F, STATUS_MISSING, None, None, None, None)
    if _SIDE_UNDATED in states:
        return result(GRADE_F, STATUS_UNDATED, spread, None, None, None)
    if states == (_SIDE_STALE, _SIDE_STALE):
        return result(GRADE_F, STATUS_STALE, spread, None, None, None)
    assert bid is not None and ask is not None  # both prices present from here on
    if _SIDE_STALE in states:
        # Exactly one side is stale: no mid is invented; the fresh side's own mark stands.
        fresh_bid = bid_side.state == _SIDE_FRESH
        reasons.append("one side stale: mid not computed; marks from the fresh %s only" % ("bid" if fresh_bid else "ask"))
        if ask < bid:
            reasons.append("crossed: ask %s < bid %s" % (ask, bid))
            return result(GRADE_F, STATUS_ONE_SIDE_STALE, None, None, None, None)
        if fresh_bid and bid == 0:
            reasons.append("no bid")
            return result(GRADE_E, STATUS_ONE_SIDE_STALE, None, None, bid, None)
        return result(GRADE_D, STATUS_ONE_SIDE_STALE, None, None, bid if fresh_bid else None,
                      None if fresh_bid else ask)
    if ask < bid:
        reasons.append("crossed: ask %s < bid %s" % (ask, bid))
        return result(GRADE_F, STATUS_FRESH, spread, None, None, None)
    mid = quote.mid
    if bid == 0:
        reasons.append("no bid")
        return result(GRADE_E, STATUS_FRESH, spread, mid, bid, ask)
    assert spread is not None and mid is not None  # bid > 0 and ask >= bid: the mid is positive
    if spread > WIDE_SPREAD_PCT:
        reasons.append("wide: spread %s%% > %s%%" % (spread, WIDE_SPREAD_PCT))
        return result(GRADE_E, STATUS_FRESH, spread, mid, bid, ask)
    return result(_band(spread), STATUS_FRESH, spread, mid, bid, ask)


def unavailable(symbol: str, reason: str, *, label: str, quote: Optional[Quote] = None) -> QuoteQC:
    """The verdict for a symbol that could not be quoted at all (the cache reports why): grade F,
    status ``missing``, no marks. ``quote`` (a stale cached copy) only fills the display fields."""
    bid = ask = as_of = None
    if quote is not None:
        bid, ask, as_of = quote.bid, quote.ask, quote.as_of
    return QuoteQC(symbol, GRADE_F, STATUS_MISSING, label, None, None, spread_pct(bid, ask), None, None, None, False,
                   [reason], bid, ask, as_of)


__all__ = [
    "GRADES",
    "GRADE_A",
    "GRADE_B",
    "GRADE_C",
    "GRADE_D",
    "GRADE_E",
    "GRADE_F",
    "LABEL_CLOSED_DAY",
    "LABEL_UNDATED",
    "QuoteQC",
    "REASON_MARKET_CLOSED",
    "REASON_NO_OFFER",
    "SKEW_TOLERANCE_S",
    "SPREAD_BANDS",
    "SPREAD_QUANTUM",
    "STATUSES",
    "STATUS_CLOSED_DAY",
    "STATUS_FRESH",
    "STATUS_MISSING",
    "STATUS_ONE_SIDE_STALE",
    "STATUS_STALE",
    "STATUS_UNDATED",
    "USABLE_GRADES",
    "WIDE_SPREAD_PCT",
    "assess",
    "quantize_spread",
    "session_label",
    "spread_pct",
    "unavailable",
]
