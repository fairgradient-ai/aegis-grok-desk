"""The book of record: every position marked from quotes, with IV, Greeks, P/L, payoff metrics and the snapshot.

Plan section 8 ``book.json`` and the brief's book section; standard library only. :func:`load_book` is the one
library entry point: from an open :class:`aegis_desk.quote_cache.MarketData` and the desk files it loads
``positions.csv`` (``today`` = the session's ET date), groups the rows, reads ``research/calendar.toml`` when
present, reads the previous trading day's snapshot from ``data/book/``, fetches ONE quotes batch through
``quote_cache.get_quotes`` (never a chain), grades every quote with ``quote_qc.assess`` and returns a
:class:`BookRun` whose ``document`` is the book dict. ``scripts/book.py`` prints it; PR 2c's tools reuse it
without importing the tool. :func:`build_book` is the pure assembly step underneath (every input a parameter,
no I/O), :func:`book_to_json` its dict with EXACTLY the keys below (Decimals, dates and aware datetimes stay
Python objects; ``state.json_dumps`` / ``contract.json_dumps`` render them as the plan's decimal strings).

Keys
----
``generated_at, session, as_of_label, data_quality {grades {A..F: n}, usable_legs, unavailable_legs,
bound_min}, sources [{name, delay_min, receipt_utc}], rates {risk_free_rate, source, as_of}, config_sha
{desk_toml, positions_csv, calendar_toml|null}, legs [...], groups [...], underlyings {SYM: {...}},
portfolio {...}, warnings []`` (:data:`BOOK_KEYS`, :data:`LEG_KEYS`, :data:`GROUP_KEYS`,
:data:`UNDERLYING_KEYS`, :data:`PORTFOLIO_KEYS`). Every row of ``positions.csv`` is a leg, closed rows
included (``status`` tells them apart; a closed leg has no quote and no marks); a group also carries
``underlying_stop`` (the minimum over its rows) for the USTOP rule. Every leg also carries ``open_quantity``
and ``remaining_cost_basis`` (two keys beyond the brief's list): ``quantity`` and ``cost_basis_total`` are the
row's record of opening, while every mark and P/L figure (``unrealized_*``, ``day_pl``, ``position_greeks``)
applies to the OPEN part, so a consumer marks the open part from the block (``open_quantity x multiplier x
mark - remaining_cost_basis``) without re-deriving it; a partially closed row says so in its warnings. Every
leg also carries ``fresh_bid`` and ``fresh_ask`` (two more keys, after ``remaining_cost_basis``): the sides
``quote_qc`` keeps fresh (``QuoteQC.mark_liq_long`` / ``mark_liq_short``, each present only when that side is
fresh) for any grade A-E, on stock-like legs too; ``None`` without a verdict, for a closed leg, for a leg past
its last trading day and for a grade F. Unlike ``mark_liq`` (a usable grade A-D only) they survive a grade E
(a wide or zero-bid quote): PR 2c's rules may test a short leg's take-profit against the fresh ask of a wide
quote, a wide spread only delaying a take-profit. A fresh ask of ``0`` stays ``0`` (``QuoteQC`` keeps it: a 0/0
quote graded E, or a stale zero bid beside a fresh zero ask graded D): it is no offer, not a price to close at,
so a take-profit read of it needs ``fresh_ask > 0``. Neither key marks a leg: only ``mark_source == "mid"``
does ("Marks"). A snapshot written before these two keys existed is still a day-P/L basis (the match reads
neither).

The request batch (every PR 2b tool uses this rule, :func:`request_batch`)
--------------------------------------------------------------------------
ONE ``get_quotes`` call for the sorted, de-duplicated set of {the compact symbol of every open option leg
whose last trading day is on or after today's ET date} + {the underlying of every such leg} + {the symbol of
every open stock-like leg}. An open option row past its last trading day is ``expired`` (not quoted, no
marks: the reconciliation must settle it first) and makes its group unavailable.

Definitions (plan section 8)
----------------------------
* Session: :func:`load_book` classifies ``market.now`` with the calendar's full closures
  (``market_clock.session_at(now, extra_holidays=cal.extra_closures())``, ``previous_close_utc`` likewise), the
  one rule every PR 2b market tool follows; ``quote_cache.open_market_data`` does not read the calendar, so its
  ``MarketData.session`` only supplies the ET date (``today``), which no closure can move. An injected full
  closure (``[[closures]] kind = "full"`` on today's ET date: a day of mourning, an outage the embedded NYSE
  table does not know) makes the day a non-trading day for the book, exactly as the DTE, last-trading-day
  and previous-snapshot arithmetic already treat it: no quote fetch, the closed-day label (``as_of_label``
  ``NYSE closed``, ``session`` ``holiday``, ``holiday_name`` the calendar's label ``calendar closure``), every
  open leg unavailable (nothing is usable for rules), exit 20; the ET date and the request batch are
  unchanged. ``BookRun.session`` and ``BookRun.prior_close_utc`` carry that classification for the tool's
  session line and snapshot date and for PR 2c's consumers.
* Marks: ``mark_mid`` = (bid + ask) / 2, ``mark_liq`` = bid for a long leg, ask for a short leg
  (:func:`aegis_desk.pnl.leg_marks`). ``mark_source`` is ``"mid"`` exactly when the grade is usable (A-D)
  with both sides fresh (a mid and the leg's liquidation side), ``"unavailable"`` otherwise: a grade-E leg
  keeps its mid for display only (and its fresh sides, "Keys"), and a grade-D leg with one side stale has no
  mid, only its fresh side (``mark_liq`` too, for display, when that is the side the leg liquidates at);
  ``unrealized_*`` are then ``None`` and the group is unavailable. A consumer gates a P/L or loss reading on
  ``mark_source``, never on ``quote_grade``. ``last`` and model prices never feed P/L.
* ``tau_years`` = seconds from the option quote's as-of (``min(bid_date, ask_date)``) to
  ``market_clock.exercise_cutoff_utc(expiry, am_settled, broker_cutoff_et=None)`` (16:00 ET on the last
  trading day for PM settlement, 09:30 ET on the settlement morning for AM settlement) over
  ``units.SECONDS_PER_YEAR``, no floor. Under one hour: ``iv_status = "expiring"``, IV and Greeks ``None``.
* Spot = the underlying's ``mark_mid`` (a usable grade) stamped within 60 seconds of the option's as-of;
  otherwise ``iv_status`` is ``"spot_unavailable"`` (no usable underlying mark) or ``"spot_unaligned"``
  (the stamps differ) and IV and Greeks are ``None``. The last trade is never used.
* IV = ``iv.implied_vol(mark_mid, right, S, K, tau, r, q)`` with ``r = cfg.rates.risk_free_rate_f`` and
  ``q`` the symbol's ``dividend_yield`` from ``calendar.toml``, else 0.0 with the leg warning
  :data:`WARNING_Q_ZERO` (the Stage-1 European approximation). Any solver status but ``ok`` is carried in
  ``iv_status`` with Greeks ``None``. Greeks are ``greeks.european_greeks`` in desk units (per share);
  ``position_greeks`` scales by the open signed quantity x multiplier. A group's ``net_greeks`` adds the
  stock legs' delta (one per share) to its option legs' position Greeks and is ``None`` when any open option
  leg lacks Greeks.
* ``settlement`` is ``"am"`` when the OCC root is in the calendar's ``am_settled_roots`` (else ``"pm"``);
  ``dte_*`` via ``market_clock.dte`` to the last trading day; ``itm`` by the one-cent rule at the underlying's
  usable mid (``S >= K + 0.01`` for a call, ``S <= K - 0.01`` for a put).
* ``extrinsic_bid = fresh_bid - payoff.intrinsic(right, K, S)``, calls and puts alike: exactly the geometry of
  ``expiry.dividend_tier`` (the assignment watch), so the two tools never print different numbers for one leg.
  ``fresh_bid`` is the leg's fresh bid of any grade A-E (a wide or zero-bid quote graded E still has one),
  ``S`` the underlying's usable mid (grade A-D) stamped within :data:`SPOT_ALIGNMENT_SECONDS` (the watch's own
  ``expiry.SPOT_ALIGNMENT_S``, 60 s) of the option quote's as-of, ``K`` the strike without trailing zeros
  (``expiry.plain_decimal``: ``1.52 - (106.43 - 105)`` is ``0.09`` in both tools, never ``0.090``). ``None``
  without a fresh bid or a usable spot, when either stamp is missing, or when the stamps differ by more than
  60 s.
* Day P/L: ``open quantity x multiplier x (mark_now - mark_prev_eod)`` on the mid basis, the previous mark
  coming from the desk's own snapshot ``data/book/<previous trading day>.json`` (never a vendor
  ``prevclose``). A leg is matched by (group id, account, contract or ticker, quantity), the same row
  number breaking a tie. The previous mark is the matched leg's ``mark_mid`` ONLY when that leg's
  ``mark_source`` was ``"mid"``: a grade-E display mid (``mark_source`` ``"unavailable"``, ``unrealized_*``
  ``None``) is never a basis, so such a leg has no day P/L (``day P/L unavailable: no mark in the previous
  snapshot``), exactly as a leg absent from the snapshot. A leg opened today measures from its entry price
  per share; a leg closed today reports ``realized_day_pl``. A missing or corrupt previous snapshot gives
  ``portfolio.day_pl`` ``None`` and the warning :func:`day_pl_warning`. ``underlyings[SYM].prev_eod_mid`` and
  ``move_pct_from_prev_eod`` come from the previous snapshot's ``underlyings`` block
  (``day_change_source`` is always ``"desk snapshot"``).
* Groups: ``data_quality`` is ``"ok"`` only when every open leg has ``mark_source == "mid"`` and the group
  is not quarantined; an unavailable group has ``None`` totals (UNAVAILABLE is never summed as zero) and
  the payoff metrics still (they need no quote). A closed-only group is ``"ok"`` with nothing to mark.
  ``pct_captured_*`` follow :func:`aegis_desk.pnl.pct_captured` on each basis.
* Portfolio: ``unrealized_pl_*`` and ``day_pl`` are sums over the groups with ``data_quality == "ok"``,
  each excluded group named in ``warnings``; they are ``None`` when open groups exist but none is ok (and
  ``day_pl`` is ``None`` without a previous snapshot). ``day_pl`` applies the same rule one step further:
  an ok group with an open leg whose ``day_pl`` is ``None`` (not in the previous snapshot, no usable
  previous mark) is excluded from the day P/L total as a whole, named in ``warnings`` as ``<group>
  excluded from the day P/L total: <leg> (row n) <reason>``; a group's day P/L is never partially summed,
  the total is ``None`` when open groups exist but none has a day P/L, and ``0.00`` for an empty book. ``net_delta`` is ``None`` whenever any open group is
  unavailable or lacks ``net_greeks``: a partial delta total is worse than none. Realized today / MTD / YTD
  are :func:`aegis_desk.pnl.realized_by_period` over every loaded row. ``concentration.by_underlying`` is
  ``sum |open quantity x multiplier x mark_mid| / declared_equity`` in percent (2 dp) per underlying, and
  ``None`` for an underlying with an unmarked open leg. ``beta_weighted_delta`` is the Stage-2 placeholder
  ``{"value": null, "r2_flag": "unavailable"}``. ``quarantined_groups`` lists the groups the parser or the
  grouping quarantined with their reasons; they are listed, not fatal, and make the book exit 20.

Exit codes (:func:`exit_code_for`): 0 when every open group is ``ok`` and nothing is quarantined, 20 when
any group is unavailable or quarantined (the market data being unavailable shows up as unavailable legs);
file and configuration errors are :class:`BookInputError` (exit 1 in the tool). Snapshot of record:
:func:`write_snapshot` writes ``data/book/<ET date>.json`` (the whole book dict) with
``state.atomic_write_json`` under ``state.locked(paths.lock_path)``; :func:`write_report` writes
``reports/book.md``.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import calendar_events, expiry, grouping, iv, market_clock, payoff, pnl, positions_csv, quote_cache, quote_qc, state, units
from . import greeks as greeks_module
from .calendar_events import CalendarFile
from .desk_config import DeskConfig
from .desk_paths import DeskPaths
from .grouping import PositionGroup
from .market_clock import SessionInfo
from .market_types import Quote
from .payoff import REASON_CUSTOM, REASON_MIXED_EXPIRIES, REASON_NO_OPEN_LEGS, REASON_QUARANTINED, quantize_money
from .pnl import MARK_SOURCE_MID, MARK_SOURCE_UNAVAILABLE
from .positions_csv import PositionRow, PositionsFile
from .quote_cache import CacheResult, MarketData
from .quote_qc import QuoteQC

BOOK_KEYS: Tuple[str, ...] = ("generated_at", "session", "as_of_label", "data_quality", "sources", "rates", "config_sha",
                              "legs", "groups", "underlyings", "portfolio", "warnings")
DATA_QUALITY_KEYS: Tuple[str, ...] = ("grades", "usable_legs", "unavailable_legs", "bound_min")
SOURCE_KEYS: Tuple[str, ...] = ("name", "delay_min", "receipt_utc")
RATES_KEYS: Tuple[str, ...] = ("risk_free_rate", "source", "as_of")
CONFIG_SHA_KEYS: Tuple[str, ...] = ("desk_toml", "positions_csv", "calendar_toml")
LEG_KEYS: Tuple[str, ...] = (
    "row_number", "trade_id", "group_id", "account", "instrument_type", "symbol", "occ", "underlying", "right", "strike",
    "expiry", "settlement", "dte_calendar", "dte_trading", "quantity", "multiplier", "cost_basis_total",
    "entry_price_per_share", "status", "quote_grade", "quote_status", "bid", "ask", "bid_date", "ask_date", "as_of",
    "spread_pct", "mark_mid", "mark_liq", "mark_source", "tau_years", "iv", "iv_status", "greeks", "position_greeks",
    "unrealized_pl_mid", "unrealized_pl_liq", "day_pl", "realized_day_pl", "extrinsic_bid", "itm", "warnings",
    "open_quantity", "remaining_cost_basis", "fresh_bid", "fresh_ask",
)
GROUP_KEYS: Tuple[str, ...] = (
    "group_id", "trade_id", "account", "underlying", "structure", "basis_kind", "net_cost", "max_profit", "max_loss",
    "breakevens", "unbounded_up", "unbounded_down", "unrealized_pl_liq", "unrealized_pl_mid", "pct_captured_liq",
    "pct_captured_mid", "realized_pl", "net_greeks", "legs", "data_quality", "warnings", "underlying_stop",
)
UNDERLYING_KEYS: Tuple[str, ...] = ("mark_mid", "bid", "ask", "as_of", "quote_grade", "prev_eod_mid",
                                    "move_pct_from_prev_eod", "day_change_source")
PORTFOLIO_KEYS: Tuple[str, ...] = ("unrealized_pl_liq", "unrealized_pl_mid", "day_pl", "realized_pl_today", "realized_pl_mtd",
                                   "realized_pl_ytd", "net_delta", "beta_weighted_delta", "concentration", "open_groups",
                                   "quarantined_groups")
GREEK_KEYS: Tuple[str, ...] = ("delta", "gamma", "theta", "vega", "rho")

DATA_QUALITY_OK: str = "ok"
DATA_QUALITY_UNAVAILABLE: str = "unavailable"
QUOTE_STATUS_CLOSED: str = "closed"
QUOTE_STATUS_EXPIRED: str = "expired"
IV_STATUS_OK: str = iv.STATUS_OK
IV_STATUS_EXPIRING: str = "expiring"
IV_STATUS_SPOT_UNALIGNED: str = "spot_unaligned"
IV_STATUS_SPOT_UNAVAILABLE: str = "spot_unavailable"
IV_STATUS_UNAVAILABLE: str = "unavailable"
IV_STATUS_EXPIRED: str = "expired"
IV_STATUS_CLOSED: str = "closed"
IV_STATUS_NOT_OPTION: str = "not_option"
SETTLEMENT_AM: str = "am"
SETTLEMENT_PM: str = "pm"
DAY_CHANGE_SOURCE: str = "desk snapshot"
EXPIRING_SECONDS: int = 3600
"""Under this many seconds to the exercise cutoff no IV is implied (``iv_status = "expiring"``)."""
SPOT_ALIGNMENT_SECONDS: int = expiry.SPOT_ALIGNMENT_S
"""The underlying quote must be stamped within this many seconds of the option quote to be its spot, for the IV
and for ``extrinsic_bid``: the assignment watch's own constant (``expiry.SPOT_ALIGNMENT_S``), one rule for both."""
ONE_CENT: Decimal = Decimal("0.01")
BETA_WEIGHTED_DELTA: Dict[str, Any] = {"value": None, "r2_flag": "unavailable"}
WARNING_NO_CALENDAR: str = ("no research/calendar.toml: dividend yields unknown (q = 0 for every option, Stage-1 "
                            "approximation) and every root treated as PM-settled")
WARNING_Q_ZERO: str = "q = 0: no dividend yield in calendar.toml"
SNAPSHOT_STATUS_OK: str = state.STATUS_OK
SNAPSHOT_STATUS_MISSING: str = state.STATUS_MISSING
SNAPSHOT_STATUS_CORRUPT: str = state.STATUS_CORRUPT
SNAPSHOT_STATUS_MALFORMED: str = "malformed"
REPORT_FILE: str = "book.md"
LABEL_NO_QUOTE: str = "no quote"
_ZERO = Decimal(0)
_HUNDRED = Decimal(100)


class BookInputError(Exception):
    """A desk file the book needs cannot be used (``positions_missing``, ``positions_invalid``,
    ``calendar_invalid``): the tool's exit 1. ``code`` names the error, ``message`` says what to fix."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class Book:
    """The built book; :func:`book_to_json` is its dict (the twelve keys in :data:`BOOK_KEYS`)."""

    generated_at: datetime.datetime
    session: str
    as_of_label: str
    data_quality: Dict[str, Any]
    sources: List[Dict[str, Any]]
    rates: Dict[str, Any]
    config_sha: Dict[str, Optional[str]]
    legs: List[Dict[str, Any]]
    groups: List[Dict[str, Any]]
    underlyings: Dict[str, Dict[str, Any]]
    portfolio: Dict[str, Any]
    warnings: List[str]


@dataclass
class BookRun:
    """What :func:`load_book` returns: the book, its dict, every input it loaded, the session it was graded in
    (``session`` / ``prior_close_utc``: ``market.now`` classified with the calendar's full closures, module
    notes "Session"; ``market.session`` is the calendar-blind classification) and the exit code."""

    market: MarketData
    session: SessionInfo
    prior_close_utc: datetime.datetime
    book: Book
    document: Dict[str, Any]
    positions: PositionsFile
    groups: List[PositionGroup]
    calendar: Optional[CalendarFile]
    symbols: List[str]
    quotes: Optional[CacheResult]
    prev_snapshot_date: datetime.date
    prev_snapshot_status: str
    exit_code: int


def book_to_json(book: Book) -> Dict[str, Any]:
    """The book dict with exactly :data:`BOOK_KEYS` (JSON-able through ``state.json_dumps``)."""
    return {key: getattr(book, key) for key in BOOK_KEYS}


def day_pl_warning(previous_date: datetime.date) -> str:
    """The brief's warning when the previous trading day's snapshot is missing."""
    return "day P/L unavailable: no snapshot for %s (the desk's first day?)" % previous_date.isoformat()


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _decimal(value: Any) -> Optional[Decimal]:
    """A Decimal from a snapshot value (a Decimal in process, a string on disk); ``None`` otherwise."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, str)):
        try:
            result = Decimal(str(value).strip())
        except ArithmeticError:
            return None
        return result if result.is_finite() else None
    return None


def _pct_of(numerator: Decimal, denominator: Decimal) -> Optional[Decimal]:
    if denominator <= 0:
        return None
    return pnl.quantize_pct(numerator / denominator * _HUNDRED)


def _greeks_dict(values: greeks_module.Greeks) -> Dict[str, float]:
    return {"delta": values.delta, "gamma": values.gamma, "theta": values.theta, "vega": values.vega, "rho": values.rho}


def _usable_spot(verdict: Optional[QuoteQC]) -> Optional[Decimal]:
    if verdict is None or not verdict.usable_for_rules:
        return None
    return verdict.mark_mid


def _spot_aligned(option: Optional[QuoteQC], under: Optional[QuoteQC]) -> bool:
    """Both quotes stamped and the underlying's as-of within :data:`SPOT_ALIGNMENT_SECONDS` of the option's."""
    if option is None or under is None or option.as_of is None or under.as_of is None:
        return False
    return abs((under.as_of - option.as_of).total_seconds()) <= SPOT_ALIGNMENT_SECONDS


def _extrinsic_bid(right: str, strike: Decimal, fresh_bid: Optional[Decimal], option: Optional[QuoteQC],
                   under: Optional[QuoteQC]) -> Optional[Decimal]:
    """``fresh_bid - intrinsic`` at the underlying's usable mid stamped within the alignment window, the strike
    without trailing zeros as ``expiry.dividend_tier`` has it (module notes); ``None`` without a fresh bid or a
    usable spot, when either stamp is missing, or when the stamps lie more than the window apart."""
    spot = _usable_spot(under)
    if fresh_bid is None or spot is None or not _spot_aligned(option, under):
        return None
    return fresh_bid - payoff.intrinsic(right, expiry.plain_decimal(strike), spot)


def _leg_key(group_id: Any, account: Any, instrument: Any, quantity: Any) -> Optional[Tuple[str, str, str, Decimal]]:
    value = _decimal(quantity)
    if value is None or not isinstance(instrument, str):
        return None
    return (str(group_id), str(account), instrument, value)


class _PreviousSnapshot:
    """The previous trading day's book, indexed for the day P/L and the underlyings' previous mid."""

    def __init__(self, document: Optional[Mapping[str, Any]]) -> None:
        self.present = isinstance(document, Mapping)
        self._legs: Dict[Tuple[str, str, str, Decimal], List[Dict[str, Any]]] = {}
        self._underlyings: Dict[str, Optional[Decimal]] = {}
        if not self.present:
            return
        assert document is not None
        for leg in document.get("legs") or []:
            if not isinstance(leg, Mapping):
                continue
            key = _leg_key(leg.get("group_id"), leg.get("account"), leg.get("occ") or leg.get("symbol"), leg.get("quantity"))
            if key is not None:
                self._legs.setdefault(key, []).append(dict(leg))
        underlyings = document.get("underlyings")
        if isinstance(underlyings, Mapping):
            for symbol, entry in underlyings.items():
                if isinstance(entry, Mapping):
                    self._underlyings[str(symbol).upper()] = _decimal(entry.get("mark_mid"))

    def mark_for(self, leg: PositionRow) -> Tuple[Optional[Decimal], bool]:
        """``(previous mark_mid, found)``: the matching leg's mid when it was a basis (``mark_source``
        ``"mid"``); ``None`` when the leg had no usable mark (a grade-E display mid is not a basis)."""
        key = _leg_key(leg.group_id, leg.account, leg.compact_symbol, leg.quantity)
        candidates = self._legs.get(key) if key is not None else None
        if not candidates:
            return None, False
        chosen = next((item for item in candidates if item.get("row_number") == leg.row_number), candidates[0])
        candidates.remove(chosen)  # two lots of one contract are matched once each
        if chosen.get("mark_source") != MARK_SOURCE_MID:
            return None, True  # display-only (grade E) or no mark at all: never a previous mark
        return _decimal(chosen.get("mark_mid")), True

    def underlying_mid(self, symbol: str) -> Optional[Decimal]:
        return self._underlyings.get(symbol.upper())


# --------------------------------------------------------------------------------------------
# The request batch
# --------------------------------------------------------------------------------------------


def request_batch(groups: Sequence[PositionGroup], today: datetime.date, calendar: Optional[CalendarFile]) -> List[str]:
    """The sorted, de-duplicated quote symbols of the book at ``today`` (module notes): every open option leg
    whose last trading day is on or after ``today`` with its underlying, and every open stock-like leg."""
    extra = calendar_events.extra_closures(calendar)
    symbols = set()
    for group in groups:
        for leg in group.open_legs:
            if leg.is_option and leg.occ is not None:
                am_settled = calendar_events.is_am_settled(calendar, leg.occ.root)
                if market_clock.last_trading_day(leg.occ.expiration, am_settled, extra) >= today:
                    symbols.add(leg.compact_symbol)
                    symbols.add(leg.underlying)
            else:
                symbols.add(leg.compact_symbol)
    return sorted(symbols)


# --------------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------------


class _Builder:
    def __init__(self, *, now: datetime.datetime, cfg: DeskConfig, positions: PositionsFile,
                 groups: Sequence[PositionGroup], quotes: Mapping[str, Quote], qcs: Mapping[str, QuoteQC],
                 calendar: Optional[CalendarFile], prev_snapshot: Optional[Mapping[str, Any]], session: SessionInfo,
                 prior_close_utc: Optional[datetime.datetime], bound_min: Optional[int],
                 receipt_utc: Optional[datetime.datetime], cache_warnings: Sequence[str],
                 prev_snapshot_date: Optional[datetime.date], prev_snapshot_status: Optional[str]) -> None:
        self.now = now
        self.cfg = cfg
        self.positions = positions
        self.groups = list(groups)
        self.quotes = quotes
        self.qcs = qcs
        self.calendar = calendar
        self.session = session
        self.today = session.et_date
        self.extra = calendar_events.extra_closures(calendar)
        self.prior_close_utc = prior_close_utc
        self.bound_min = bound_min
        self.receipt_utc = receipt_utc
        self.cache_warnings = list(cache_warnings)
        self.prev = _PreviousSnapshot(prev_snapshot)
        self.prev_date = prev_snapshot_date if prev_snapshot_date is not None else market_clock.previous_trading_day(
            self.today, self.extra)
        self.prev_status = prev_snapshot_status
        self.rate = cfg.rates.risk_free_rate_f
        self.leg_records: Dict[int, Dict[str, Any]] = {}
        self.leg_marks: Dict[int, pnl.LegMarks] = {}
        self.day_pl_gaps: Dict[int, str] = {}
        self.warnings: List[str] = []
        self.group_of_row: Dict[int, PositionGroup] = {}
        for group in self.groups:
            for leg in group.legs:
                self.group_of_row[leg.row_number] = group

    # -- legs -------------------------------------------------------------------------------
    def leg(self, leg: PositionRow) -> Dict[str, Any]:
        rec: Dict[str, Any] = {key: None for key in LEG_KEYS}
        rec.update({
            "row_number": leg.row_number, "trade_id": leg.trade_id, "group_id": leg.group_id, "account": leg.account,
            "instrument_type": leg.instrument_type, "symbol": leg.symbol,
            "occ": leg.occ.compact if leg.occ is not None else None, "underlying": leg.underlying, "right": leg.right,
            "strike": leg.strike, "expiry": leg.expiration, "quantity": leg.quantity, "multiplier": leg.multiplier,
            "cost_basis_total": leg.cost_basis_total, "entry_price_per_share": leg.entry_price_per_share,
            "open_quantity": leg.open_quantity, "remaining_cost_basis": leg.remaining_cost_basis,
            "status": leg.status, "mark_source": MARK_SOURCE_UNAVAILABLE, "iv_status": IV_STATUS_NOT_OPTION,
            "warnings": list(leg.warnings),
        })
        am_settled = False
        if leg.is_option and leg.occ is not None:
            am_settled = calendar_events.is_am_settled(self.calendar, leg.occ.root)
            rec["settlement"] = SETTLEMENT_AM if am_settled else SETTLEMENT_PM
            rec["dte_calendar"] = market_clock.dte(leg.occ.expiration, self.today, market_clock.UNIT_CALENDAR, am_settled,
                                                   self.extra)
            rec["dte_trading"] = market_clock.dte(leg.occ.expiration, self.today, market_clock.UNIT_TRADING, am_settled,
                                                  self.extra)
            rec["iv_status"] = IV_STATUS_UNAVAILABLE
        if leg.closed is not None and leg.closed == self.today:
            rec["realized_day_pl"] = pnl.realized_for_row(leg)
        if leg.is_partially_closed:
            rec["warnings"].append("partially closed: %s of %s %s remain open; marks and P/L apply to the open quantity"
                                   % (_plain(abs(leg.open_quantity)), _plain(abs(leg.quantity)),
                                      "contracts" if leg.is_option else "shares"))
        if leg.is_closed:
            rec["quote_status"] = QUOTE_STATUS_CLOSED
            if leg.is_option:
                rec["iv_status"] = IV_STATUS_CLOSED
            self.leg_marks[leg.row_number] = pnl.LegMarks(None, None, False, None)
            return rec
        if leg.is_option and leg.occ is not None:
            last_day = market_clock.last_trading_day(leg.occ.expiration, am_settled, self.extra)
            if last_day < self.today:
                rec["quote_status"] = QUOTE_STATUS_EXPIRED
                rec["iv_status"] = IV_STATUS_EXPIRED
                rec["warnings"].append("expired: the last trading day %s has passed; settle the row with csv_recon before "
                                       "the book can value it" % last_day.isoformat())
                self.leg_marks[leg.row_number] = pnl.LegMarks(None, None, False, None)
                return rec
        verdict = self.qcs.get(leg.compact_symbol)
        marks = pnl.leg_marks(leg, verdict)
        self.leg_marks[leg.row_number] = marks
        quote = self.quotes.get(leg.compact_symbol)
        if verdict is None:
            rec["quote_grade"] = quote_qc.GRADE_F
            rec["quote_status"] = quote_qc.STATUS_MISSING
            rec["warnings"].append("no quote requested for %s" % leg.compact_symbol)
        else:
            rec.update({"quote_grade": verdict.grade, "quote_status": verdict.status, "bid": verdict.bid, "ask": verdict.ask,
                        "as_of": verdict.as_of, "spread_pct": verdict.spread_pct})
            if verdict.grade != quote_qc.GRADE_F:  # the fresh sides of any grade A-E (module notes, "Keys")
                rec["fresh_bid"] = verdict.mark_liq_long
                rec["fresh_ask"] = verdict.mark_liq_short
            if quote is not None:
                rec["bid_date"] = quote.bid_date
                rec["ask_date"] = quote.ask_date
            if not marks.basis_available:
                for reason in verdict.reasons:
                    if reason not in rec["warnings"]:
                        rec["warnings"].append(reason)
        rec["mark_mid"] = marks.mark_mid
        rec["mark_liq"] = marks.mark_liq
        rec["mark_source"] = marks.mark_source
        if marks.basis_available:
            rec["unrealized_pl_mid"] = pnl.unrealized(leg, marks.mark_mid)
            rec["unrealized_pl_liq"] = pnl.unrealized(leg, marks.mark_liq)
            self._day_pl(leg, rec, marks)
        if leg.is_option and leg.occ is not None:
            self._option_analytics(leg, rec, marks, verdict, am_settled)
        return rec

    def _day_pl(self, leg: PositionRow, rec: Dict[str, Any], marks: pnl.LegMarks) -> None:
        if leg.opened == self.today:
            rec["day_pl"] = pnl.day_pl(leg, marks.mark_mid, None, True)
            return
        if not self.prev.present:
            return
        prev_mark, found = self.prev.mark_for(leg)
        if not found:
            detail = "not in the previous snapshot %s" % self.prev_date.isoformat()
        elif prev_mark is None:
            detail = "no mark in the previous snapshot %s" % self.prev_date.isoformat()
        else:
            rec["day_pl"] = pnl.day_pl(leg, marks.mark_mid, prev_mark, False)
            return
        rec["warnings"].append("day P/L unavailable: %s" % detail)
        self.warnings.append("day P/L unavailable for %s (row %d): %s" % (leg.compact_symbol, leg.row_number, detail))
        self.day_pl_gaps[leg.row_number] = "%s (row %d) %s" % (leg.compact_symbol, leg.row_number, detail)

    def _option_analytics(self, leg: PositionRow, rec: Dict[str, Any], marks: pnl.LegMarks, verdict: Optional[QuoteQC],
                          am_settled: bool) -> None:
        assert leg.occ is not None
        right, strike = leg.occ.right, leg.occ.strike
        under = self.qcs.get(leg.underlying)
        spot = _usable_spot(under)
        if spot is not None:
            if right == "CALL":
                rec["itm"] = spot >= strike + ONE_CENT
            else:
                rec["itm"] = spot <= strike - ONE_CENT
        rec["extrinsic_bid"] = _extrinsic_bid(right, strike, rec["fresh_bid"], verdict, under)
        as_of = verdict.as_of if verdict is not None else None
        seconds: Optional[float] = None
        if as_of is not None:
            cutoff = market_clock.exercise_cutoff_utc(leg.occ.expiration, am_settled, None, self.extra)
            seconds = (cutoff - as_of).total_seconds()
            rec["tau_years"] = seconds / units.SECONDS_PER_YEAR
        if not marks.basis_available or marks.mark_mid is None or seconds is None:
            rec["iv_status"] = IV_STATUS_UNAVAILABLE
            return
        if seconds < EXPIRING_SECONDS:
            rec["iv_status"] = IV_STATUS_EXPIRING
            return
        if under is None or spot is None:
            rec["iv_status"] = IV_STATUS_SPOT_UNAVAILABLE
            return
        if not _spot_aligned(verdict, under):
            rec["iv_status"] = IV_STATUS_SPOT_UNALIGNED
            return
        dividend_yield = calendar_events.dividend_yield(self.calendar, leg.underlying)
        if dividend_yield is None:
            q = 0.0
            rec["warnings"].append(WARNING_Q_ZERO)
        else:
            q = float(dividend_yield)
        tau = rec["tau_years"]
        result = iv.implied_vol(float(marks.mark_mid), right, float(spot), float(strike), tau, self.rate, q)
        if not result.ok or result.sigma is None:
            rec["iv_status"] = result.status
            return
        rec["iv"] = result.sigma
        rec["iv_status"] = IV_STATUS_OK
        per_share = greeks_module.european_greeks(right, float(spot), float(strike), tau, self.rate, q, result.sigma)
        rec["greeks"] = _greeks_dict(per_share)
        rec["position_greeks"] = _greeks_dict(greeks_module.position_greeks(per_share, float(leg.open_quantity),
                                                                            leg.multiplier))

    # -- groups -----------------------------------------------------------------------------
    def group(self, group: PositionGroup) -> Dict[str, Any]:
        open_rows = [leg.row_number for leg in group.open_legs]
        available = (not group.is_quarantined) and all(self.leg_marks[row].basis_available for row in open_rows)
        profile = payoff.payoff_profile(group)
        rec: Dict[str, Any] = {key: None for key in GROUP_KEYS}
        rec.update({
            "group_id": group.group_id, "trade_id": group.trade_id, "account": group.account, "underlying": group.underlying,
            "structure": group.structure, "basis_kind": group.basis_kind, "net_cost": group.net_cost,
            "max_profit": profile.max_profit, "max_loss": profile.max_loss, "breakevens": list(profile.breakevens),
            "unbounded_up": profile.unbounded_up, "unbounded_down": profile.unbounded_down,
            "realized_pl": quantize_money(sum((pnl.realized_for_row(leg) or _ZERO for leg in group.legs), _ZERO)),
            "legs": [leg.row_number for leg in group.legs], "underlying_stop": group.underlying_stop,
            "data_quality": DATA_QUALITY_OK if available else DATA_QUALITY_UNAVAILABLE,
        })
        warnings = list(group.warnings)
        if group.is_quarantined:
            warnings.append(REASON_QUARANTINED)
            warnings.extend(group.errors)
        elif profile.reason is not None and profile.reason not in warnings:
            warnings.append(profile.reason)
        for row in open_rows:
            marks = self.leg_marks[row]
            if not marks.basis_available:
                leg_rec = self.leg_records[row]
                warnings.append("row %d %s: no usable mark (%s %s)" % (row, leg_rec["occ"] or leg_rec["symbol"],
                                                                      leg_rec["quote_grade"] or "-", leg_rec["quote_status"]))
        rec["warnings"] = warnings
        if available and open_rows:
            mids = {row: self.leg_marks[row].mark_mid for row in open_rows}
            liqs = {row: self.leg_marks[row].mark_liq for row in open_rows}
            rec["unrealized_pl_mid"] = pnl.group_unrealized(group.open_legs, mids)
            rec["unrealized_pl_liq"] = pnl.group_unrealized(group.open_legs, liqs)
            rec["pct_captured_mid"] = pnl.pct_captured(group, pnl.cost_to_close(group.open_legs, mids), profile.max_profit)
            rec["pct_captured_liq"] = pnl.pct_captured(group, pnl.cost_to_close(group.open_legs, liqs), profile.max_profit)
            rec["net_greeks"] = self._net_greeks(group)
        return rec

    def _net_greeks(self, group: PositionGroup) -> Optional[Dict[str, float]]:
        total = {key: 0.0 for key in GREEK_KEYS}
        for leg in group.open_legs:
            if leg.is_option:
                position = self.leg_records[leg.row_number]["position_greeks"]
                if position is None:
                    return None
                for key in GREEK_KEYS:
                    total[key] += position[key]
            else:
                total["delta"] += float(leg.open_quantity) * leg.multiplier
        return total

    # -- blocks -----------------------------------------------------------------------------
    def underlyings(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for symbol in sorted({leg.underlying for leg in self.positions.rows if leg.is_open}):
            verdict = self.qcs.get(symbol)
            mid = _usable_spot(verdict)
            prev = self.prev.underlying_mid(symbol)
            move = None
            if mid is not None and prev is not None and prev > 0:
                move = pnl.quantize_pct((mid - prev) / prev * _HUNDRED)
            out[symbol] = {
                "mark_mid": mid,
                "bid": verdict.bid if verdict is not None else None,
                "ask": verdict.ask if verdict is not None else None,
                "as_of": verdict.as_of if verdict is not None else None,
                "quote_grade": verdict.grade if verdict is not None else None,
                "prev_eod_mid": prev,
                "move_pct_from_prev_eod": move,
                "day_change_source": DAY_CHANGE_SOURCE,
            }
        return out

    def data_quality(self) -> Dict[str, Any]:
        grades = {grade: 0 for grade in quote_qc.GRADES}
        usable = unavailable = 0
        for leg in self.positions.rows:
            if not leg.is_open:
                continue
            rec = self.leg_records[leg.row_number]
            if rec["quote_grade"] in grades:
                grades[rec["quote_grade"]] += 1
            if rec["mark_source"] == MARK_SOURCE_MID:
                usable += 1
            else:
                unavailable += 1
        return {"grades": grades, "usable_legs": usable, "unavailable_legs": unavailable, "bound_min": self.bound_min}

    def portfolio(self, group_recs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        open_recs = [rec for rec in group_recs if any(self.positions_row(row).is_open for row in rec["legs"])]
        ok_open = [rec for rec in open_recs if rec["data_quality"] == DATA_QUALITY_OK]
        excluded = [rec for rec in open_recs if rec["data_quality"] != DATA_QUALITY_OK]
        for rec in excluded:
            self.warnings.append("%s excluded from the portfolio totals: %s" % (rec["group_id"], "; ".join(rec["warnings"])
                                                                                 or DATA_QUALITY_UNAVAILABLE))
        nothing_usable = bool(open_recs) and not ok_open
        unrealized_mid = unrealized_liq = day_pl = net_delta = None
        if not nothing_usable:
            unrealized_mid = quantize_money(sum((rec["unrealized_pl_mid"] or _ZERO for rec in ok_open), _ZERO))
            unrealized_liq = quantize_money(sum((rec["unrealized_pl_liq"] or _ZERO for rec in ok_open), _ZERO))
            if self.prev.present:
                day_pl = self._day_pl_total(ok_open, bool(open_recs))
            if not excluded and all(rec["net_greeks"] is not None for rec in ok_open):
                net_delta = sum(rec["net_greeks"]["delta"] for rec in ok_open)
        if not self.prev.present:
            day_pl = None
        realized = pnl.realized_by_period(self.positions.rows, self.today)
        quarantined: List[Dict[str, Any]] = [{"group_id": group_id, "reasons": [str(error) for error in errors]}
                                             for group_id, errors in self.positions.quarantined.items()]
        quarantined.extend({"group_id": group.group_id, "reasons": list(group.errors)}
                           for group in self.groups if group.is_quarantined)
        return {
            "unrealized_pl_liq": unrealized_liq,
            "unrealized_pl_mid": unrealized_mid,
            "day_pl": day_pl,
            "realized_pl_today": realized.today,
            "realized_pl_mtd": realized.mtd,
            "realized_pl_ytd": realized.ytd,
            "net_delta": net_delta,
            "beta_weighted_delta": dict(BETA_WEIGHTED_DELTA),
            "concentration": {"declared_equity": self.cfg.owner.declared_equity, "by_underlying": self.concentration()},
            "open_groups": len(open_recs),
            "quarantined_groups": quarantined,
        }

    def _day_pl_total(self, ok_open: Sequence[Dict[str, Any]], open_groups: bool) -> Optional[Decimal]:
        """The portfolio day P/L over the ok groups whose every open leg has a day P/L (module notes): an ok
        group with a gap is excluded as a whole and named; ``None`` when open groups exist but none counts."""
        included: List[Dict[str, Any]] = []
        for rec in ok_open:
            gaps = [self.day_pl_gaps.get(row, "no day P/L") for row in rec["legs"]
                    if self.positions_row(row).is_open and self.leg_records[row]["day_pl"] is None]
            if gaps:
                self.warnings.append("%s excluded from the day P/L total: %s" % (rec["group_id"], "; ".join(gaps)))
            else:
                included.append(rec)
        if open_groups and not included:
            return None
        total = _ZERO
        for rec in included:
            for row in rec["legs"]:
                value = self.leg_records[row]["day_pl"]
                if value is not None:
                    total += value
        return quantize_money(total)

    def positions_row(self, row_number: int) -> PositionRow:
        for leg in self.positions.rows:
            if leg.row_number == row_number:
                return leg
        raise KeyError(row_number)  # pragma: no cover - every group leg is a loaded row

    def concentration(self) -> Dict[str, Optional[Decimal]]:
        exposure: Dict[str, Optional[Decimal]] = {}
        for leg in self.positions.rows:
            if not leg.is_open:
                continue
            marks = self.leg_marks[leg.row_number]
            current = exposure.get(leg.underlying, _ZERO)
            if not marks.basis_available or marks.mark_mid is None or current is None:
                exposure[leg.underlying] = None
                continue
            exposure[leg.underlying] = current + abs(leg.open_quantity * leg.multiplier * marks.mark_mid)
        equity = self.cfg.owner.declared_equity
        return {symbol: (None if value is None else _pct_of(value, equity)) for symbol, value in sorted(exposure.items())}

    def as_of_label(self) -> str:
        stamps = [verdict.as_of for verdict in self.qcs.values() if verdict.as_of is not None]
        as_of = max(stamps) if stamps else None
        prior = self.prior_close_utc
        if prior is None and self.session.session == market_clock.SESSION_PRE:
            prior = market_clock.previous_close_utc(self.now, extra_holidays=self.extra)
        return quote_qc.session_label(self.session, prior, as_of)

    def build(self) -> Book:
        if self.calendar is None:
            self.warnings.append(WARNING_NO_CALENDAR)
        else:
            self.warnings.extend(self.calendar.warnings)
        self.warnings.extend("quote cache: %s" % warning for warning in self.cache_warnings)
        if not self.prev.present:
            if self.prev_status == SNAPSHOT_STATUS_CORRUPT:
                self.warnings.append("day P/L unavailable: the snapshot for %s was corrupt and has been quarantined"
                                     % self.prev_date.isoformat())
            elif self.prev_status == SNAPSHOT_STATUS_MALFORMED:
                self.warnings.append("day P/L unavailable: the snapshot for %s is not a book document"
                                     % self.prev_date.isoformat())
            else:
                self.warnings.append(day_pl_warning(self.prev_date))
        for leg in self.positions.rows:
            self.leg_records[leg.row_number] = self.leg(leg)
        legs = [self.leg_records[leg.row_number] for leg in self.positions.rows]
        group_recs = [self.group(group) for group in self.groups]
        underlyings = self.underlyings()
        portfolio = self.portfolio(group_recs)
        market = self.cfg.market_data
        return Book(
            generated_at=self.now,
            session=self.session.session,
            as_of_label=self.as_of_label(),
            data_quality=self.data_quality(),
            sources=[{"name": quote_cache.source_label(self.cfg), "delay_min": market.feed_delay_min,
                      "receipt_utc": self.receipt_utc}],
            rates={"risk_free_rate": self.cfg.rates.risk_free_rate, "source": self.cfg.rates.source,
                   "as_of": self.cfg.rates.as_of},
            config_sha={"desk_toml": self.cfg.sha256, "positions_csv": self.positions.sha256,
                        "calendar_toml": self.calendar.sha256 if self.calendar is not None else None},
            legs=legs,
            groups=group_recs,
            underlyings=underlyings,
            portfolio=portfolio,
            warnings=self.warnings,
        )


def build_book(*, now: datetime.datetime, cfg: DeskConfig, paths: DeskPaths, positions: PositionsFile,
               groups: Sequence[PositionGroup], quotes: Mapping[str, Quote], qcs: Mapping[str, QuoteQC],
               calendar: Optional[CalendarFile], prev_snapshot: Optional[Mapping[str, Any]], session: SessionInfo,
               prior_close_utc: Optional[datetime.datetime] = None, bound_min: Optional[int] = None,
               receipt_utc: Optional[datetime.datetime] = None, cache_warnings: Sequence[str] = (),
               prev_snapshot_date: Optional[datetime.date] = None, prev_snapshot_status: Optional[str] = None) -> Book:
    """Assemble the book from its inputs (module notes); pure, no I/O.

    ``quotes`` and ``qcs`` are keyed by the quote symbol (the compact OCC form for options); a symbol absent
    from ``qcs`` is an unavailable leg. ``paths`` is accepted for the brief's signature and not read.
    ``prev_snapshot`` is the previous trading day's book dict (in process or as read back from its file);
    ``prev_snapshot_date`` / ``prev_snapshot_status`` label the day P/L warning (``missing`` by default).
    ``session`` and ``prior_close_utc`` are the caller's classification of ``now``; :func:`load_book` makes it
    with the calendar's full closures (module notes, "Session") and the document's ``session`` and
    ``as_of_label`` follow it.
    """
    del paths  # the assembly reads nothing from the desk root
    builder = _Builder(now=now, cfg=cfg, positions=positions, groups=groups, quotes=quotes, qcs=qcs, calendar=calendar,
                       prev_snapshot=prev_snapshot, session=session, prior_close_utc=prior_close_utc, bound_min=bound_min,
                       receipt_utc=receipt_utc, cache_warnings=cache_warnings, prev_snapshot_date=prev_snapshot_date,
                       prev_snapshot_status=prev_snapshot_status)
    return builder.build()


def exit_code_for(document: Mapping[str, Any]) -> int:
    """0 when every group is ``ok`` and nothing is quarantined; 20 otherwise (UNAVAILABLE, never HOLD)."""
    if document["portfolio"]["quarantined_groups"]:
        return 20
    if any(group["data_quality"] != DATA_QUALITY_OK for group in document["groups"]):
        return 20
    return 0


# --------------------------------------------------------------------------------------------
# Snapshot of record and the report
# --------------------------------------------------------------------------------------------


def snapshot_path(paths: DeskPaths, et_date: datetime.date) -> str:
    """``data/book/<ET date>.json`` under the desk root."""
    return os.path.join(paths.book_dir, "%s.json" % et_date.isoformat())


def previous_snapshot_date(et_date: datetime.date, extra_holidays: market_clock.ExtraHolidays = ()) -> datetime.date:
    """The previous trading day: the date of the snapshot the day P/L is measured against."""
    return market_clock.previous_trading_day(et_date, extra_holidays)


def read_previous_snapshot(paths: DeskPaths, et_date: datetime.date,
                           extra_holidays: market_clock.ExtraHolidays = ()) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(document, status)`` of the previous trading day's snapshot: ``ok``, ``missing``, ``corrupt`` (the
    file is quarantined by ``state.read_json``) or ``malformed`` (not a book document)."""
    document, status = state.read_json(snapshot_path(paths, previous_snapshot_date(et_date, extra_holidays)))
    if status != state.STATUS_OK:
        return None, status
    if not (isinstance(document, dict) and isinstance(document.get("legs"), list)
            and isinstance(document.get("underlyings"), dict)):
        return None, SNAPSHOT_STATUS_MALFORMED
    return document, SNAPSHOT_STATUS_OK


def write_snapshot(paths: DeskPaths, document: Mapping[str, Any], et_date: datetime.date) -> str:
    """Write the book dict to ``data/book/<ET date>.json`` atomically under the desk lock; returns the path."""
    path = snapshot_path(paths, et_date)
    with state.locked(paths.lock_path):
        state.atomic_write_json(path, dict(document))
    return path


def _plain(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(int(value)) if value == value.to_integral_value() else str(value)
    return str(value)


def _money(value: Any) -> str:
    if value is None:
        return "-"
    return str(value)


def _price(value: Any) -> str:
    if value is None:
        return "-"
    return quote_cache.format_money(value if isinstance(value, Decimal) else Decimal(str(value)))


def _pct(value: Any) -> str:
    return "-" if value is None else "%s%%" % value


def _iv(value: Any) -> str:
    return "-" if value is None else "%.1f%%" % (float(value) * 100)


def _dte(rec: Mapping[str, Any]) -> str:
    if rec["dte_calendar"] is None:
        return "-"
    return "%s/%s" % (rec["dte_calendar"], rec["dte_trading"])


def _stamp(value: Any) -> str:
    if isinstance(value, datetime.datetime):
        return state.format_utc(value)
    return "-" if value is None else str(value)


def group_line(group: Mapping[str, Any]) -> str:
    """One prose line per group: structure, net cost, P/L on both bases, payoff metrics, data quality."""
    max_profit = "unbounded" if group["unbounded_up"] else _money(group["max_profit"])
    max_loss = "unbounded" if group["unbounded_down"] else _money(group["max_loss"])
    breakevens = "/".join(str(value) for value in group["breakevens"]) or "-"
    return "%s %s %s | %s | net cost %s | P/L mid %s liq %s | max profit %s max loss %s BE %s | %s" % (
        group["group_id"], group["structure"], group["underlying"], group["account"], _money(group["net_cost"]),
        _money(group["unrealized_pl_mid"]), _money(group["unrealized_pl_liq"]), max_profit, max_loss, breakevens,
        group["data_quality"])


def leg_line(leg: Mapping[str, Any]) -> str:
    """One prose line per leg: contract, quantity, entry, marks and grade, P/L, day P/L, IV and DTE."""
    text = "  row %d %s %s @ %s | mark %s liq %s %s | P/L mid %s liq %s | day %s" % (
        leg["row_number"], leg["occ"] or leg["symbol"], _plain(leg["quantity"]), _price(leg["entry_price_per_share"]),
        _price(leg["mark_mid"]), _price(leg["mark_liq"]), leg["quote_grade"] or "-", _money(leg["unrealized_pl_mid"]),
        _money(leg["unrealized_pl_liq"]), _money(leg["day_pl"]))
    if leg["status"] != "open":
        text += " | %s" % leg["status"]
    elif leg["occ"] is not None:
        text += " | IV %s | DTE %s" % (_iv(leg["iv"]), _dte(leg))
    return text


def portfolio_line(document: Mapping[str, Any]) -> str:
    portfolio = document["portfolio"]
    delta = portfolio["net_delta"]
    return ("portfolio | unrealized mid %s liq %s | day %s | realized today %s MTD %s YTD %s | net delta %s | "
            "open groups %d | quarantined %d") % (
        _money(portfolio["unrealized_pl_mid"]), _money(portfolio["unrealized_pl_liq"]), _money(portfolio["day_pl"]),
        _money(portfolio["realized_pl_today"]), _money(portfolio["realized_pl_mtd"]), _money(portfolio["realized_pl_ytd"]),
        "-" if delta is None else "%.2f" % delta, portfolio["open_groups"], len(portfolio["quarantined_groups"]))


def render_lines(document: Mapping[str, Any]) -> List[str]:
    """The prose table: one line per group followed by its legs, then the portfolio line."""
    legs_by_row = {leg["row_number"]: leg for leg in document["legs"]}
    lines: List[str] = []
    for group in document["groups"]:
        lines.append(group_line(group))
        for row in group["legs"]:
            if row in legs_by_row:
                lines.append(leg_line(legs_by_row[row]))
    lines.append(portfolio_line(document))
    return lines


def footer_from_document(document: Mapping[str, Any]) -> str:
    """``Tradier sandbox, delayed 15 min, as of HH:MM UTC`` from the document's own source block."""
    source = document["sources"][0] if document["sources"] else {"name": "unknown", "delay_min": 0}
    vendor, _, host = str(source["name"]).partition(":")
    stamps = [leg["as_of"] for leg in document["legs"] if isinstance(leg.get("as_of"), datetime.datetime)]
    as_of = max(stamps) if stamps else document["generated_at"]
    if not isinstance(as_of, datetime.datetime):
        as_of = datetime.datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
    return "%s %s, delayed %s min, as of %s UTC" % (vendor.capitalize(), host, source["delay_min"],
                                                     as_of.astimezone(datetime.timezone.utc).strftime("%H:%M"))


def render_report(document: Mapping[str, Any]) -> str:
    """``reports/book.md``: the book as Markdown tables (groups, legs, portfolio, warnings, footer)."""
    portfolio = document["portfolio"]
    lines = ["# Book of record", "",
             "Generated %s; session %s; %s." % (_stamp(document["generated_at"]), document["session"],
                                               document["as_of_label"]),
             "", "## Portfolio", "", "| Measure | Value |", "| --- | --- |",
             "| Unrealized P/L (mid) | %s |" % _money(portfolio["unrealized_pl_mid"]),
             "| Unrealized P/L (liquidation) | %s |" % _money(portfolio["unrealized_pl_liq"]),
             "| Day P/L | %s |" % _money(portfolio["day_pl"]),
             "| Realized today / MTD / YTD | %s / %s / %s |" % (_money(portfolio["realized_pl_today"]),
                                                               _money(portfolio["realized_pl_mtd"]),
                                                               _money(portfolio["realized_pl_ytd"])),
             "| Net delta | %s |" % ("-" if portfolio["net_delta"] is None else "%.2f" % portfolio["net_delta"]),
             "| Open groups | %d |" % portfolio["open_groups"],
             "| Quarantined groups | %d |" % len(portfolio["quarantined_groups"]),
             "", "## Groups", "",
             "| Group | Structure | Underlying | Net cost | P/L mid | P/L liq | Max profit | Max loss | Breakevens | Data |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for group in document["groups"]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            group["group_id"], group["structure"], group["underlying"], _money(group["net_cost"]),
            _money(group["unrealized_pl_mid"]), _money(group["unrealized_pl_liq"]),
            "unbounded" if group["unbounded_up"] else _money(group["max_profit"]),
            "unbounded" if group["unbounded_down"] else _money(group["max_loss"]),
            "/".join(str(value) for value in group["breakevens"]) or "-", group["data_quality"]))
    lines += ["", "## Legs", "",
              "| Row | Group | Contract | Qty | Entry | Mark mid | Mark liq | Grade | P/L mid | P/L liq | Day P/L | IV | DTE |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for leg in document["legs"]:
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            leg["row_number"], leg["group_id"], leg["occ"] or leg["symbol"], _plain(leg["quantity"]),
            _price(leg["entry_price_per_share"]), _price(leg["mark_mid"]), _price(leg["mark_liq"]),
            leg["quote_grade"] or "-", _money(leg["unrealized_pl_mid"]), _money(leg["unrealized_pl_liq"]),
            _money(leg["day_pl"]), _iv(leg["iv"]), _dte(leg)))
    if document["warnings"]:
        lines += ["", "## Warnings", ""] + ["- %s" % warning for warning in document["warnings"]]
    lines += ["", footer_from_document(document), ""]
    return "\n".join(lines)


def write_report(paths: DeskPaths, document: Mapping[str, Any]) -> str:
    """Write :func:`render_report` to ``reports/book.md`` atomically; returns the path."""
    path = os.path.join(paths.reports_dir, REPORT_FILE)
    state.atomic_write_text(path, render_report(document))
    return path


# --------------------------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------------------------


def _missing_label(session: SessionInfo, prior_close_utc: datetime.datetime, copy: Optional[Quote]) -> str:
    if session.session in (market_clock.SESSION_WEEKEND, market_clock.SESSION_HOLIDAY):
        return quote_qc.LABEL_CLOSED_DAY
    if copy is not None:
        return quote_qc.session_label(session, prior_close_utc, copy.as_of)
    return LABEL_NO_QUOTE


def assess_quotes(result: CacheResult, symbols: Sequence[str], *, now: datetime.datetime, session: SessionInfo,
                  bound_min: int, prior_close_utc: datetime.datetime) -> Tuple[Dict[str, Quote], Dict[str, QuoteQC]]:
    """``(quotes, verdicts)`` for every requested symbol, graded at ``now`` in ``session`` (the classification
    the caller made with the calendar's full closures, :func:`load_book`; never ``MarketData.session``):
    served quotes through ``quote_qc.assess`` with ``bound_min`` and ``prior_close_utc``, the rest
    ``quote_qc.unavailable`` with the cache's reason and the session's missing-quote label (``NYSE closed`` on a
    weekend or a holiday, an injected closure included; a stale copy supplies the display fields only)."""
    quotes: Dict[str, Quote] = dict(result.quotes)
    verdicts: Dict[str, QuoteQC] = {}
    for symbol in symbols:
        quote = result.quotes.get(symbol)
        if quote is not None:
            verdicts[symbol] = quote_qc.assess(quote, now=now, session=session, bound_min=bound_min,
                                               prior_close_utc=prior_close_utc)
            continue
        copy = result.stale.get(symbol)
        if copy is not None:
            quotes[symbol] = copy
        verdicts[symbol] = quote_qc.unavailable(symbol, "cache: %s" % result.missing.get(symbol, "not served"),
                                                label=_missing_label(session, prior_close_utc, copy), quote=copy)
    return quotes, verdicts


def load_book(market: MarketData) -> BookRun:
    """From an open :class:`MarketData` and the desk files to the book (module notes).

    The session the book is graded in is ``market.now`` classified with the calendar's full closures (module
    notes, "Session"), computed once ``research/calendar.toml`` is loaded; ``market.session`` (classified
    without the calendar) only supplies the ET date, which no closure can move, so ``positions.csv`` is loaded
    first. Raises :class:`BookInputError` (``positions_missing``, ``positions_invalid``, ``calendar_invalid``)
    for a desk file that cannot be used; ``OSError`` from the cache or the desk root propagates (the tool's
    ``io_error``). Nothing is written: the snapshot and the report are the caller's explicit steps.
    """
    today = market.session.et_date  # the America/New_York date of now: the same whatever the calendar closes
    try:
        positions = positions_csv.load_positions(market.paths.positions_csv, today=today)
    except FileNotFoundError:
        raise BookInputError("positions_missing", "positions.csv not found at %s" % market.paths.positions_csv) from None
    if positions.file_errors:
        raise BookInputError("positions_invalid", "positions.csv cannot be used: %s"
                             % "; ".join(str(error) for error in positions.file_errors))
    groups = grouping.group_positions(positions.rows)
    try:
        calendar = calendar_events.load_calendar_if_present(market.paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        raise BookInputError("calendar_invalid", str(exc)) from None
    extra = calendar_events.extra_closures(calendar)
    session = market_clock.session_at(market.now, extra_holidays=extra)
    prior_close = market_clock.previous_close_utc(market.now, extra_holidays=extra)
    prev_date = previous_snapshot_date(today, extra)
    previous, prev_status = read_previous_snapshot(market.paths, today, extra)
    symbols = request_batch(groups, today, calendar)
    result: Optional[CacheResult] = None
    quotes: Dict[str, Quote] = {}
    verdicts: Dict[str, QuoteQC] = {}
    if symbols:
        result = quote_cache.get_quotes(symbols, now=market.now, cfg=market.cache, client=market.client,
                                        paths=market.paths, allow_fetch=session.trading_day, run=market.run)
        quotes, verdicts = assess_quotes(result, symbols, now=market.now, session=session, bound_min=market.bound_min,
                                         prior_close_utc=prior_close)
    built = build_book(now=market.now, cfg=market.cfg, paths=market.paths, positions=positions, groups=groups,
                       quotes=quotes, qcs=verdicts, calendar=calendar, prev_snapshot=previous, session=session,
                       prior_close_utc=prior_close, bound_min=market.bound_min,
                       receipt_utc=result.receipt_utc if result is not None else None,
                       cache_warnings=result.warnings if result is not None else (),
                       prev_snapshot_date=prev_date, prev_snapshot_status=prev_status)
    document = book_to_json(built)
    return BookRun(market=market, session=session, prior_close_utc=prior_close, book=built, document=document,
                   positions=positions, groups=groups, calendar=calendar, symbols=symbols, quotes=result,
                   prev_snapshot_date=prev_date, prev_snapshot_status=prev_status, exit_code=exit_code_for(document))


__all__ = [
    "BETA_WEIGHTED_DELTA",
    "BOOK_KEYS",
    "Book",
    "BookInputError",
    "BookRun",
    "CONFIG_SHA_KEYS",
    "DATA_QUALITY_KEYS",
    "DATA_QUALITY_OK",
    "DATA_QUALITY_UNAVAILABLE",
    "DAY_CHANGE_SOURCE",
    "EXPIRING_SECONDS",
    "GREEK_KEYS",
    "GROUP_KEYS",
    "IV_STATUS_CLOSED",
    "IV_STATUS_EXPIRED",
    "IV_STATUS_EXPIRING",
    "IV_STATUS_NOT_OPTION",
    "IV_STATUS_OK",
    "IV_STATUS_SPOT_UNALIGNED",
    "IV_STATUS_SPOT_UNAVAILABLE",
    "IV_STATUS_UNAVAILABLE",
    "LEG_KEYS",
    "MARK_SOURCE_MID",
    "MARK_SOURCE_UNAVAILABLE",
    "ONE_CENT",
    "PORTFOLIO_KEYS",
    "QUOTE_STATUS_CLOSED",
    "QUOTE_STATUS_EXPIRED",
    "RATES_KEYS",
    "REASON_CUSTOM",
    "REASON_MIXED_EXPIRIES",
    "REASON_NO_OPEN_LEGS",
    "REASON_QUARANTINED",
    "REPORT_FILE",
    "SETTLEMENT_AM",
    "SETTLEMENT_PM",
    "SNAPSHOT_STATUS_CORRUPT",
    "SNAPSHOT_STATUS_MALFORMED",
    "SNAPSHOT_STATUS_MISSING",
    "SNAPSHOT_STATUS_OK",
    "SOURCE_KEYS",
    "SPOT_ALIGNMENT_SECONDS",
    "UNDERLYING_KEYS",
    "WARNING_NO_CALENDAR",
    "WARNING_Q_ZERO",
    "assess_quotes",
    "book_to_json",
    "build_book",
    "day_pl_warning",
    "exit_code_for",
    "footer_from_document",
    "group_line",
    "leg_line",
    "load_book",
    "portfolio_line",
    "previous_snapshot_date",
    "read_previous_snapshot",
    "render_lines",
    "render_report",
    "request_batch",
    "snapshot_path",
    "write_report",
    "write_snapshot",
]
