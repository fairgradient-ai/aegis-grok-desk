"""Reconciliation of ``positions.csv``: expiries and assignments inferred from official closes and
settlement values, the quote, coverage, roll-link and acknowledgement checks, the proposed edits,
the positions snapshot and the verdict (plan section 8 ``csv_recon``; standard library only).

The library behind ``scripts/csv_recon.py`` (the pre-open and Saturday reconciliation) and
``scripts/positions_check.py`` (the first-observation plausibility check). Every function here is
pure except :func:`snapshot_positions`; the tools fetch histories and quotes through
``aegis_desk.quote_cache`` and inject the results, so the engine is tested without a client. Nothing
here ever writes ``positions.csv``: the owner edits the book of record by hand from the proposed
lines, which are rendered as exact CSV lines in the file's own header order. A first cell that
starts with ``#`` (an added row's ``#from:`` note when ``note`` is the first column) is quoted, so a
pasted line is never read as a comment by the parser's first-non-space-character rule.

Expired legs
------------
An open option row is expired when its last trading day (``market_clock.last_trading_day`` with
the calendar's ``am_settled_roots`` and full closures) is before today's ET date, or is today once
the session is ``closed``. PM-settled physical roots read the official close of the last trading
day from the daily history, requested ONCE per underlying and range: ``start`` is the last trading
day and ``end = min(next_trading_day(start), last_closed)`` where ``last_closed`` is the ET date of
the most recent session that has ended (``market_clock.previous_close_utc``), so the request never
names a day whose bar cannot exist yet (Saturday asks for the single expiry-day bar; Tuesday for
the expiry day and the Monday after it). AM-settled roots fetch no history: they take the
calendar's ``[[settlement.values]]`` entry for the settlement day.

Settlement outcomes
-------------------
* ``EXPIRED_OTM``: the close is not in the money by the $0.01 rule (call: ``close >= K + 0.01``;
  put: ``close <= K - 0.01``). Edit: the row closed at 0 on the settlement day.
* ``PROBABLE_ASSIGNED`` (short, in the money) / ``PROBABLE_EXERCISED`` (long, in the money). Edits
  follow the one documented convention: the option row closes at 0 (its premium stays in option
  P/L) and the stock is booked at the strike: a short put or a long call ADDS a long stock row
  (``quantity = 100 x c``, ``cost_basis_total = K x 100 x c``, ``opened`` = the settlement day,
  the same ``trade_id``, note ``#from:<trade_id> assigned|exercised``); a short call or a long put
  delivers ``100 x c`` shares from the group's long stock rows (FIFO by ``opened``) at
  ``close_price_per_unit = K`` (a whole row closes; a larger row records a partial close), and
  any shares not held become an ADDED short stock row at the strike marked "confirm with your
  broker".
* ``PIN_UNCERTAIN``: ``|close - K| <= pin_band`` (``desk.toml`` ``[market_data] pin_band``), OR
  the first bar after the expiry day opened on the other side of the strike from the close (the
  documented proxy for an after-hours move; an open exactly on the strike counts). "Confirm with
  your broker"; no edit; an UNAVAILABLE item.
* ``CASH_SETTLED``: an AM-settled root with a calendar value. Edit: the row closed at
  ``max(0, intrinsic at the settlement value)``. Never ASSIGNED or EXERCISED.
* ``SETTLEMENT_UNKNOWN``: an AM-settled root without a calendar value (UNAVAILABLE), from the
  settlement morning's 09:30 ET on (the value then exists and the owner records it).
* ``SETTLEMENT_PENDING``: an AM-settled root between its last trading day's close and the settlement
  morning's 09:30 ET (the Thursday post-close ``eod`` recon and the Friday ``preopen`` recon of an
  SPX monthly): the leg has stopped trading but its settlement value cannot exist yet, so nothing
  is asked of the owner. Listed and counted as an expired leg; neither UNAVAILABLE nor an edit. A
  value the calendar already records is used (``CASH_SETTLED``) whatever the instant.
* ``EXPIRED_UNRECORDED``: no history bar for the expiry day (not fetched, a failed fetch, an
  empty answer): no valuation, the owner records the outcome (UNAVAILABLE).

A row that already records a partial close at another price cannot take a second close price in
one line: its outcome is reported, no edit is proposed and the group is an UNAVAILABLE item
(``EDIT_UNREPRESENTABLE``).

Other checks
------------
* ``QUOTE_MISSING``: ONE quote batch for the unexpired open option legs and their underlyings
  (never the open stock legs; never chains). A symbol the vendor did not match is ``QUOTE_MISSING``
  (UNAVAILABLE: the leg cannot be marked and the symbol as written is unknown to the vendor); an
  unmatched index underlying is the warning ``UNQUOTABLE_INDEX`` (the sandbox quotes no index).
  Any other reason (``market_closed``, ``quota_guard``, ``per_run_cap``, ``fetch_failed:...``) is
  "quote check not run: <reason>", informational, and never changes the verdict.
* ``UNCOVERED_SHORT_CALL`` (warning): a group with a closed stock row while a short call stays open
  and fewer shares than the calls need remain.
* ``ROLL_LINK`` (warning): ``#roll-of:AG-...`` or ``#from:AG-...`` naming no group in the file.
* ``ACTED_UNCHANGED`` (warning): ``state/acks.jsonl`` (written by the ``ack`` tool of PR 2c:
  ``{utc, signal_id, trade_id, action, until, rows_sha}``) holds an ``acted`` record whose ET date
  is at least one trading day before today while the group's rows are unchanged, ``rows_sha``
  being :func:`group_rows_sha`: the SHA-256 of the group's physical CSV lines joined by ``"\\n"``.
* ``QUARANTINED`` (UNAVAILABLE): a group the parser or the grouping quarantined.

Verdict
-------
``ERRORS`` (file-level CSV errors, exit 1) > ``UNAVAILABLE`` (any UNAVAILABLE item, exit 20) >
``EDITS_PENDING`` (proposed edits exist, exit 10) > ``CLEAN`` (exit 0). Warnings and
informational findings never change the verdict. Money is :class:`decimal.Decimal` throughout;
nothing here is a binary float.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import os
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from aegis_desk import calendar_events, market_clock, occ, state
from aegis_desk.calendar_events import CalendarFile
from aegis_desk.desk_paths import DeskPaths
from aegis_desk.grouping import PositionGroup
from aegis_desk.market_types import Bar, Quote
from aegis_desk.positions_csv import INSTRUMENT_EQUITY, STATUS_CLOSED, PositionRow, PositionsFile, RowError

OUTCOME_EXPIRED_OTM: str = "EXPIRED_OTM"
OUTCOME_PROBABLE_ASSIGNED: str = "PROBABLE_ASSIGNED"
OUTCOME_PROBABLE_EXERCISED: str = "PROBABLE_EXERCISED"
OUTCOME_PIN_UNCERTAIN: str = "PIN_UNCERTAIN"
OUTCOME_CASH_SETTLED: str = "CASH_SETTLED"
OUTCOME_SETTLEMENT_UNKNOWN: str = "SETTLEMENT_UNKNOWN"
OUTCOME_EXPIRED_UNRECORDED: str = "EXPIRED_UNRECORDED"
OUTCOME_SETTLEMENT_PENDING: str = "SETTLEMENT_PENDING"
OUTCOMES: Tuple[str, ...] = (OUTCOME_EXPIRED_OTM, OUTCOME_PROBABLE_ASSIGNED, OUTCOME_PROBABLE_EXERCISED,
                             OUTCOME_PIN_UNCERTAIN, OUTCOME_CASH_SETTLED, OUTCOME_SETTLEMENT_UNKNOWN,
                             OUTCOME_EXPIRED_UNRECORDED, OUTCOME_SETTLEMENT_PENDING)
UNAVAILABLE_OUTCOMES: Tuple[str, ...] = (OUTCOME_PIN_UNCERTAIN, OUTCOME_SETTLEMENT_UNKNOWN, OUTCOME_EXPIRED_UNRECORDED)
EDIT_OUTCOMES: Tuple[str, ...] = (OUTCOME_EXPIRED_OTM, OUTCOME_PROBABLE_ASSIGNED, OUTCOME_PROBABLE_EXERCISED,
                                  OUTCOME_CASH_SETTLED)

VERDICT_CLEAN: str = "CLEAN"
VERDICT_EDITS_PENDING: str = "EDITS_PENDING"
VERDICT_UNAVAILABLE: str = "UNAVAILABLE"
VERDICT_ERRORS: str = "ERRORS"
VERDICTS: Tuple[str, ...] = (VERDICT_CLEAN, VERDICT_EDITS_PENDING, VERDICT_UNAVAILABLE, VERDICT_ERRORS)
VERDICT_EXIT_CODES: Dict[str, int] = {VERDICT_CLEAN: 0, VERDICT_EDITS_PENDING: 10, VERDICT_UNAVAILABLE: 20,
                                      VERDICT_ERRORS: 1}

SETTLEMENT_PM: str = "pm"
SETTLEMENT_AM: str = "am"
EDIT_UPDATE: str = "update"
EDIT_ADD: str = "add"

FINDING_QUOTE_MISSING: str = "QUOTE_MISSING"
FINDING_QUOTE_CHECK_NOT_RUN: str = "QUOTE_CHECK_NOT_RUN"
FINDING_UNQUOTABLE_INDEX: str = "UNQUOTABLE_INDEX"
FINDING_UNCOVERED_SHORT_CALL: str = "UNCOVERED_SHORT_CALL"
FINDING_ROLL_LINK: str = "ROLL_LINK"
FINDING_ACTED_UNCHANGED: str = "ACTED_UNCHANGED"
FINDING_QUARANTINED: str = "QUARANTINED"
FINDING_EDIT_UNREPRESENTABLE: str = "EDIT_UNREPRESENTABLE"
FINDING_ACKS_UNREADABLE: str = "ACKS_UNREADABLE"
FINDING_KINDS: Tuple[str, ...] = (FINDING_QUOTE_MISSING, FINDING_QUOTE_CHECK_NOT_RUN, FINDING_UNQUOTABLE_INDEX,
                                  FINDING_UNCOVERED_SHORT_CALL, FINDING_ROLL_LINK, FINDING_ACTED_UNCHANGED,
                                  FINDING_QUARANTINED, FINDING_EDIT_UNREPRESENTABLE, FINDING_ACKS_UNREADABLE)
LEVEL_UNAVAILABLE: str = "unavailable"
LEVEL_WARNING: str = "warning"
LEVEL_INFO: str = "info"
LEVELS: Tuple[str, ...] = (LEVEL_UNAVAILABLE, LEVEL_WARNING, LEVEL_INFO)

PLAUSIBILITY_OK: str = "ok"
PLAUSIBILITY_ERROR: str = "error"
PLAUSIBILITY_WARNING: str = "warning"
PLAUSIBILITY_NOT_CHECKED: str = "not_checked"
PLAUSIBILITY_UNQUOTABLE: str = "unquotable"
PLAUSIBILITY_EXPIRED: str = "expired"
PLAUSIBILITY_STATUSES: Tuple[str, ...] = (PLAUSIBILITY_OK, PLAUSIBILITY_ERROR, PLAUSIBILITY_WARNING,
                                          PLAUSIBILITY_NOT_CHECKED, PLAUSIBILITY_UNQUOTABLE, PLAUSIBILITY_EXPIRED)
PLAUSIBILITY_OPTION_FACTOR: Decimal = Decimal(20)
PLAUSIBILITY_STOCK_FACTOR: Decimal = Decimal(10)
MESSAGE_UNIT_MISMATCH: str = "unit mismatch?"

ACK_ACTION_ACTED: str = "acted"
REASON_NOT_FETCHED: str = "not fetched"
REASON_NO_BARS: str = "no_bars"
REASON_UNMATCHED: str = "unmatched"
REASON_NOT_ATTEMPTED: str = "not attempted"
CONFIRM_WITH_BROKER: str = "confirm with your broker"
ROW_GROUP_PREFIX: str = "ROW-"
SHARES_PER_CONTRACT: Decimal = Decimal(100)
CENT: Decimal = Decimal("0.01")
_ZERO = Decimal(0)
_ONE = Decimal(1)
_CLOSE_COLUMNS: Tuple[str, ...] = ("status", "closed", "closed_quantity", "close_price_per_unit")


# --------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CsvTable:
    """The raw cells of ``positions.csv`` by physical line number (the parser's row numbers): the
    header (lower-cased, stripped), each data row's cells as written, and each row's raw line."""

    header: List[str]
    header_line: int
    cells: Dict[int, List[str]]
    lines: Dict[int, str]

    def column(self, name: str) -> Optional[int]:
        try:
            return self.header.index(name)
        except ValueError:
            return None

    def cell(self, row_number: int, name: str) -> str:
        index = self.column(name)
        cells = self.cells.get(row_number, [])
        if index is None or index >= len(cells):
            return ""
        return cells[index]


@dataclass(frozen=True)
class ProposedEdit:
    """One line the owner may paste: ``update`` replaces the row ``row_number`` with ``cells``,
    ``add`` is a new row; ``fields`` are the cells that carry the change (column -> text)."""

    kind: str
    row_number: Optional[int]
    fields: Dict[str, str]
    reason: str
    outcome: str
    group_id: str
    cells: Tuple[str, ...]

    def csv_line(self) -> str:
        return render_csv_line(self.cells)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "row_number": self.row_number,
            "fields": dict(self.fields),
            "reason": self.reason,
            "outcome": self.outcome,
            "group_id": self.group_id,
            "cells": list(self.cells),
            "csv_line": self.csv_line(),
        }


@dataclass(frozen=True)
class Finding:
    """A check's result that is not a settlement: ``kind`` (:data:`FINDING_KINDS`) and ``level``
    (:data:`LEVELS`; only ``unavailable`` changes the verdict)."""

    kind: str
    level: str
    message: str
    group_id: Optional[str] = None
    row_number: Optional[int] = None
    symbol: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "level": self.level, "message": self.message, "group_id": self.group_id,
                "row_number": self.row_number, "symbol": self.symbol}


@dataclass(frozen=True)
class LegSettlement:
    """The outcome of one expired leg (module notes). ``quantity`` is the signed open quantity;
    ``close`` is the expiry-day close (PM) or the settlement value (AM); ``close_price`` is the
    ``close_price_per_unit`` the proposed edit writes, ``None`` when nothing is proposed."""

    row_number: int
    group_id: str
    trade_id: Optional[str]
    symbol: str
    underlying: str
    quantity: Decimal
    right: str
    strike: Decimal
    expiration: datetime.date
    last_trading_day: datetime.date
    settle_date: datetime.date
    settlement: str
    outcome: str
    close: Optional[Decimal]
    next_open: Optional[Decimal]
    close_price: Optional[Decimal]
    message: str
    unavailable: bool
    history_reason: Optional[str] = None
    edits: Tuple[ProposedEdit, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "row_number": self.row_number,
            "group_id": self.group_id,
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "underlying": self.underlying,
            "quantity": self.quantity,
            "right": self.right,
            "strike": self.strike,
            "expiration": self.expiration.isoformat(),
            "last_trading_day": self.last_trading_day.isoformat(),
            "settle_date": self.settle_date.isoformat(),
            "settlement": self.settlement,
            "outcome": self.outcome,
            "close": self.close,
            "next_open": self.next_open,
            "close_price": self.close_price,
            "message": self.message,
            "unavailable": self.unavailable,
            "history_reason": self.history_reason,
            "edits": [edit.as_dict() for edit in self.edits],
        }


@dataclass(frozen=True)
class HistoryRequest:
    """One daily-bar request: the symbol and the inclusive range (module notes)."""

    symbol: str
    start: datetime.date
    end: datetime.date

    @property
    def key(self) -> Tuple[str, datetime.date, datetime.date]:
        return (self.symbol, self.start, self.end)


@dataclass(frozen=True)
class ExpiredLeg:
    """An open option row past its last trading day, with the dates its settlement is read at."""

    row: PositionRow
    group: PositionGroup
    am_settled: bool
    last_trading_day: datetime.date
    settle_date: datetime.date
    history_key: Optional[Tuple[str, datetime.date, datetime.date]]


@dataclass(frozen=True)
class SettlementPlan:
    """What :func:`reconcile` needs fetched: the expired legs and the history requests (one per
    underlying and range), plus the instant's ET date, session and calendar closures."""

    now: datetime.datetime
    et_date: datetime.date
    session: str
    extra_holidays: Dict[datetime.date, str]
    cal: Optional[CalendarFile]
    legs: List[ExpiredLeg]
    history_requests: List[HistoryRequest]
    expired_rows: FrozenSet[int]


@dataclass(frozen=True)
class QuoteCheck:
    """The one quote batch of the reconciliation: what was asked, what came back, why not."""

    symbols: Tuple[str, ...]
    quoted: Tuple[str, ...]
    missing: Dict[str, str]
    not_run_reason: Optional[str]
    requests_made: int
    as_of: Optional[datetime.datetime]

    def as_dict(self) -> Dict[str, Any]:
        return {"symbols": list(self.symbols), "quoted": list(self.quoted), "missing": dict(self.missing),
                "not_run_reason": self.not_run_reason, "requests_made": self.requests_made, "as_of": self.as_of}


@dataclass
class Reconciliation:
    """The result of :func:`reconcile`: the verdict and everything it rests on."""

    verdict: str
    exit_code: int
    et_date: datetime.date
    session: str
    settlements: List[LegSettlement]
    edits: List[ProposedEdit]
    findings: List[Finding]
    quote_check: Optional[QuoteCheck]
    quarantined: Dict[str, List[str]]
    file_errors: List[str]
    counts: Dict[str, int]
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "et_date": self.et_date.isoformat(),
            "session": self.session,
            "settlements": [item.as_dict() for item in self.settlements],
            "edits": [edit.as_dict() for edit in self.edits],
            "findings": [finding.as_dict() for finding in self.findings],
            "quote_check": self.quote_check.as_dict() if self.quote_check is not None else None,
            "quarantined": {group_id: list(errors) for group_id, errors in self.quarantined.items()},
            "file_errors": list(self.file_errors),
            "counts": dict(self.counts),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PlausibilityCheck:
    """One open row's first-observation check (``positions_check``): the entry price per share
    against the quote's mid; ``status`` is one of :data:`PLAUSIBILITY_STATUSES`."""

    row_number: int
    group_id: str
    trade_id: Optional[str]
    symbol: str
    instrument_type: str
    entry: Decimal
    mark: Optional[Decimal]
    status: str
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {"row_number": self.row_number, "group_id": self.group_id, "trade_id": self.trade_id, "symbol": self.symbol,
                "instrument_type": self.instrument_type, "entry": self.entry, "mark": self.mark, "status": self.status,
                "message": self.message}


@dataclass(frozen=True)
class Snapshot:
    """The copy of record written by :func:`snapshot_positions`."""

    csv_path: str
    sha_path: str
    sha256: str


# --------------------------------------------------------------------------------------------
# CSV text helpers
# --------------------------------------------------------------------------------------------


def _physical_lines(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def csv_table(text: str) -> CsvTable:
    """The raw cells of ``positions.csv`` text keyed by physical 1-based line number, with the
    parser's line rules (BOM dropped, blank and ``#`` lines skipped, the first kept line is the
    header). A line the ``csv`` module cannot read is left out (the parser reports it)."""
    if text.startswith("﻿"):
        text = text[1:]
    kept = [(number, line) for number, line in enumerate(_physical_lines(text), 1)
            if line.strip() and not line.lstrip().startswith("#")]
    if not kept:
        return CsvTable([], 0, {}, {})
    header_number, header_line = kept[0]
    try:
        header = [cell.strip().lower() for cell in next(csv.reader([header_line]))]
    except (csv.Error, StopIteration):
        return CsvTable([], header_number, {}, {})
    cells: Dict[int, List[str]] = {}
    lines: Dict[int, str] = {}
    for number, line in kept[1:]:
        if "\x00" in line:
            continue
        try:
            record = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            continue
        if all(not cell.strip() for cell in record):
            continue
        cells[number] = list(record)
        lines[number] = line
    return CsvTable(header, header_number, cells, lines)


def render_csv_line(cells: Sequence[str]) -> str:
    """One CSV line (no terminator) in the ``csv`` module's minimal quoting: a cell holding a
    comma, a quote or a line break is quoted, every other cell is written as it is, EXCEPT a first
    cell whose first non-space character is ``#``, which is always quoted: unquoted it would make
    the whole line a comment to the parser (module notes)."""
    cells = list(cells)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(cells)
    line = buffer.getvalue().rstrip("\n")
    if cells and cells[0].lstrip().startswith("#") and not line.startswith('"'):
        # Minimal quoting left the first cell bare (no comma, quote or line break in it), so the line
        # begins with exactly that cell: quote it.
        line = '"%s"%s' % (cells[0], line[len(cells[0]):])
    return line


def group_rows_sha(table: CsvTable, group_id: str) -> Optional[str]:
    """SHA-256 (hex) of the group's physical CSV lines in file order joined by ``"\\n"``: the
    ``rows_sha`` the ``ack`` tool records and ``ACTED_UNCHANGED`` compares. A row with a blank
    ``trade_id`` belongs to ``ROW-<line>``. ``None`` when no row carries ``group_id``."""
    index = table.column("trade_id")
    lines: List[str] = []
    for number in sorted(table.cells):
        cells = table.cells[number]
        trade_id = cells[index].strip() if index is not None and index < len(cells) else ""
        key = trade_id or "%s%d" % (ROW_GROUP_PREFIX, number)
        if key == group_id:
            lines.append(table.lines[number])
    if not lines:
        return None
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def plain_number(value: Decimal) -> str:
    """``150.000`` -> ``150``, ``622.500`` -> ``622.5``: a quantity or strike for prose and cells."""
    if value == value.to_integral_value():
        return str(int(value))
    return str(value.normalize())


def cell_decimal(value: Decimal) -> Decimal:
    """``value`` with exactly the digits :func:`money_text` writes into a cell (``23.170`` -> ``23.17``)."""
    return Decimal(money_text(value))


def money_text(value: Decimal) -> str:
    """A money cell: ``0`` for zero (expired worthless), otherwise at least two decimals and never
    fewer digits than the value carries (``150.000`` -> ``150.00``, ``0.125`` stays)."""
    if value == 0:
        return "0"
    cents = value.quantize(CENT)
    if cents == value:
        return str(cents)
    return str(value.normalize())


# --------------------------------------------------------------------------------------------
# Expiry rules
# --------------------------------------------------------------------------------------------


def is_am_settled_root(cal: Optional[CalendarFile], row: PositionRow) -> bool:
    """True when the row's OCC root is in the calendar's ``am_settled_roots`` (no calendar: PM)."""
    if row.occ is None:
        return False
    return calendar_events.is_am_settled(cal, row.occ.root)


def is_expired(row: PositionRow, *, et_date: datetime.date, session: str, am_settled: bool,
               extra_holidays: market_clock.ExtraHolidays = ()) -> bool:
    """The expired-leg rule: an open option row whose last trading day is before ``et_date``, or
    is ``et_date`` while the session is ``closed`` (after that day's close)."""
    if row.occ is None or not row.is_open:
        return False
    last = market_clock.last_trading_day(row.occ.expiration, am_settled, extra_holidays)
    if last < et_date:
        return True
    return last == et_date and session == market_clock.SESSION_CLOSED


def history_range(last_trading_day: datetime.date, now: datetime.datetime,
                  extra_holidays: market_clock.ExtraHolidays = ()) -> Tuple[datetime.date, datetime.date]:
    """``(start, end)`` of the daily-bar request for a PM-settled leg whose last trading day is
    ``last_trading_day``: ``end`` is the next trading day clamped to the ET date of the last
    session that has closed at ``now``, never before ``start``."""
    last_closed = market_clock.to_et(market_clock.previous_close_utc(now, extra_holidays=extra_holidays)).date()
    end = min(market_clock.next_trading_day(last_trading_day, extra_holidays), last_closed)
    if end < last_trading_day:
        end = last_trading_day
    return last_trading_day, end


def intrinsic(right: str, strike: Decimal, value: Decimal) -> Decimal:
    """``max(0, value - K)`` for a call, ``max(0, K - value)`` for a put, per share (Decimal)."""
    difference = value - strike if right == occ.CALL else strike - value
    return difference if difference > 0 else _ZERO


def in_the_money(right: str, strike: Decimal, close: Decimal) -> bool:
    """The $0.01 rule: a call with ``close >= K + 0.01``, a put with ``close <= K - 0.01``."""
    if right == occ.CALL:
        return close >= strike + CENT
    return close <= strike - CENT


def crossed_strike(strike: Decimal, close: Decimal, next_open: Optional[Decimal]) -> bool:
    """True when the next session opened on the other side of the strike from ``close`` (or on
    the strike itself): the after-hours proxy of the pin rule. ``None`` (no later bar) is False."""
    if next_open is None:
        return False
    if next_open == strike:
        return True
    return (next_open > strike) != (close > strike)


def expiry_bar(bars: Iterable[Bar], day: datetime.date) -> Optional[Bar]:
    """The bar dated ``day`` (the first one, should the vendor repeat it), or ``None``."""
    for item in bars:
        if item.date == day:
            return item
    return None


def bar_after(bars: Iterable[Bar], day: datetime.date) -> Optional[Bar]:
    """The earliest bar dated after ``day``, or ``None``."""
    later = [item for item in bars if item.date > day]
    if not later:
        return None
    return min(later, key=lambda item: item.date)


def _leg_fields(row: PositionRow) -> Dict[str, Any]:
    parsed = row.occ
    if parsed is None:
        raise ValueError("row %d is not an option row" % row.row_number)
    return {
        "row_number": row.row_number,
        "group_id": row.group_id,
        "trade_id": row.trade_id,
        "symbol": row.compact_symbol,
        "underlying": row.underlying,
        "quantity": row.open_quantity,
        "right": parsed.right,
        "strike": parsed.strike,
        "expiration": parsed.expiration,
    }


def _contract_text(row: PositionRow) -> str:
    """``short 2200 put x 1``: the side word, the strike, the right and the unsigned open count."""
    parsed = row.occ
    assert parsed is not None
    return "%s %s %s x %s" % (row.side, plain_number(parsed.strike), parsed.right.lower(),
                              plain_number(abs(row.open_quantity)))


def settle_pm(row: PositionRow, bars: Iterable[Bar], *, pin_band: Decimal,
              extra_holidays: market_clock.ExtraHolidays = (), history_reason: Optional[str] = None) -> LegSettlement:
    """Settle a PM-settled physical leg on its last trading day's close (module notes). ``bars``
    are the daily bars served for the leg's range; ``history_reason`` says why they may be
    missing. The edits are attached by :func:`reconcile` (see :func:`propose_edits`)."""
    parsed = row.occ
    if parsed is None:
        raise ValueError("row %d is not an option row" % row.row_number)
    if not isinstance(pin_band, Decimal) or pin_band < 0:
        raise ValueError("pin_band must be a non-negative Decimal, got %r" % (pin_band,))
    last = market_clock.last_trading_day(parsed.expiration, False, extra_holidays)
    fields = _leg_fields(row)
    common = dict(fields, last_trading_day=last, settle_date=last, settlement=SETTLEMENT_PM)
    bars = list(bars)
    day_bar = expiry_bar(bars, last)
    if day_bar is None or day_bar.close is None:
        if not bars:
            reason = history_reason or REASON_NO_BARS
        else:
            reason = history_reason or "no bar for %s" % last.isoformat()
        message = "no history for %s %s (%s): record the outcome of the %s by hand" % (
            row.underlying, last.isoformat(), reason, _contract_text(row))
        return LegSettlement(outcome=OUTCOME_EXPIRED_UNRECORDED, close=None, next_open=None, close_price=None,
                             message=message, unavailable=True, history_reason=reason, **common)
    close = day_bar.close
    later = bar_after(bars, last)
    next_open = later.open if later is not None else None
    strike = parsed.strike
    distance = abs(close - strike)
    when = "%s closed %s on %s" % (row.underlying, money_text(close), last.isoformat())
    crossed = crossed_strike(strike, close, next_open)
    if distance <= pin_band or crossed:
        parts = ["%s, %s from the %s strike (pin band %s)" % (when, money_text(distance), plain_number(strike),
                                                             money_text(pin_band))]
        if later is not None and next_open is not None:
            parts.append("%s opened %s%s" % (later.date.isoformat(), money_text(next_open),
                                             " across the strike" if crossed else ""))
        message = "; ".join(parts) + ": %s, no edit proposed" % CONFIRM_WITH_BROKER
        return LegSettlement(outcome=OUTCOME_PIN_UNCERTAIN, close=close, next_open=next_open, close_price=None,
                             message=message, unavailable=True, **common)
    if in_the_money(parsed.right, strike, close):
        outcome = OUTCOME_PROBABLE_ASSIGNED if row.is_short else OUTCOME_PROBABLE_EXERCISED
        message = "%s: the %s %s finished %s in the money" % (when, plain_number(strike), parsed.right.lower(),
                                                              money_text(intrinsic(parsed.right, strike, close)))
        if later is not None and next_open is not None:
            message += "; %s opened %s, same side" % (later.date.isoformat(), money_text(next_open))
        message += "; %s (edits proposed)" % ("short: assignment probable" if row.is_short else "long: exercise probable")
        return LegSettlement(outcome=outcome, close=close, next_open=next_open, close_price=cell_decimal(_ZERO),
                             message=message, unavailable=False, **common)
    message = "%s: the %s %s expired out of the money (edit proposed)" % (when, plain_number(strike), parsed.right.lower())
    return LegSettlement(outcome=OUTCOME_EXPIRED_OTM, close=close, next_open=next_open, close_price=cell_decimal(_ZERO),
                         message=message, unavailable=False, **common)


def settle_am(row: PositionRow, value: Optional[Decimal], *,
              extra_holidays: market_clock.ExtraHolidays = (), now: Optional[datetime.datetime] = None) -> LegSettlement:
    """Settle an AM-settled index leg on the calendar's settlement value for its settlement day:
    ``CASH_SETTLED`` at the intrinsic value; without a value ``SETTLEMENT_PENDING`` while ``now`` is
    before the settlement morning's 09:30 ET (the value cannot exist yet; module notes), else
    ``SETTLEMENT_UNKNOWN``. Without ``now`` the strict rule applies. Never ASSIGNED or EXERCISED."""
    parsed = row.occ
    if parsed is None:
        raise ValueError("row %d is not an option row" % row.row_number)
    settle = market_clock.settlement_day(parsed.expiration, True, extra_holidays)
    last = market_clock.last_trading_day(parsed.expiration, True, extra_holidays)
    common = dict(_leg_fields(row), last_trading_day=last, settle_date=settle, settlement=SETTLEMENT_AM)
    if value is None:
        struck_at = market_clock.from_et(datetime.datetime.combine(settle, market_clock.AM_SETTLEMENT_ET))
        if now is not None and now < struck_at:
            message = ("%s is AM-settled and settles at the open of %s (%s ET); the value is not published yet: "
                       "nothing to record until then (%s)" % (parsed.root, settle.isoformat(),
                                                               market_clock.AM_SETTLEMENT_ET.strftime("%H:%M"),
                                                               _contract_text(row)))
            return LegSettlement(outcome=OUTCOME_SETTLEMENT_PENDING, close=None, next_open=None, close_price=None,
                                 message=message, unavailable=False, **common)
        message = ("%s is AM-settled and research/calendar.toml has no [[settlement.values]] entry for %s on %s: "
                   "record the official settlement value (%s)" % (parsed.root, parsed.root, settle.isoformat(),
                                                                   _contract_text(row)))
        return LegSettlement(outcome=OUTCOME_SETTLEMENT_UNKNOWN, close=None, next_open=None, close_price=None,
                             message=message, unavailable=True, **common)
    worth = intrinsic(parsed.right, parsed.strike, value)
    message = "%s settled at %s on %s: the %s %s is worth %s per share (edit proposed)" % (
        parsed.root, money_text(value), settle.isoformat(), plain_number(parsed.strike), parsed.right.lower(),
        money_text(worth))
    return LegSettlement(outcome=OUTCOME_CASH_SETTLED, close=value, next_open=None, close_price=cell_decimal(worth),
                         message=message, unavailable=False, **common)


# --------------------------------------------------------------------------------------------
# Proposed edits
# --------------------------------------------------------------------------------------------


def _update_edit(table: CsvTable, row_number: int, fields: Dict[str, str], reason: str, outcome: str,
                 group_id: str) -> Tuple[Optional[ProposedEdit], Optional[Finding]]:
    missing = [name for name in fields if table.column(name) is None]
    if missing or row_number not in table.cells:
        what = ("the header has no %s column" % ", ".join(missing)) if missing else "the row's cells are not readable"
        return None, Finding(FINDING_EDIT_UNREPRESENTABLE, LEVEL_UNAVAILABLE,
                             "row %d: cannot write the %s edit (%s): record it by hand" % (row_number, outcome, what),
                             group_id, row_number)
    cells = list(table.cells[row_number]) + [""] * (len(table.header) - len(table.cells[row_number]))
    for name, value in fields.items():
        index = table.column(name)
        assert index is not None
        cells[index] = value
    return ProposedEdit(EDIT_UPDATE, row_number, dict(fields), reason, outcome, group_id, tuple(cells)), None


def _add_edit(table: CsvTable, fields: Dict[str, str], reason: str, outcome: str,
              group_id: str) -> Tuple[Optional[ProposedEdit], Optional[Finding]]:
    missing = [name for name, value in fields.items() if value and table.column(name) is None]
    if missing:
        return None, Finding(FINDING_EDIT_UNREPRESENTABLE, LEVEL_UNAVAILABLE,
                             "cannot write the %s stock row (the header has no %s column): record it by hand"
                             % (outcome, ", ".join(missing)), group_id)
    cells = [""] * len(table.header)
    kept: Dict[str, str] = {}
    for name, value in fields.items():
        if not value:
            continue
        index = table.column(name)
        assert index is not None
        cells[index] = value
        kept[name] = value
    return ProposedEdit(EDIT_ADD, None, kept, reason, outcome, group_id, tuple(cells)), None


def _partial_finding(row: PositionRow, outcome: str, what: str) -> Finding:
    return Finding(FINDING_EDIT_UNREPRESENTABLE, LEVEL_UNAVAILABLE,
                   "row %d: a partial close is already recorded at another price; record the %s of the remaining %s "
                   "%s by hand (one row per close price)" % (row.row_number, outcome, plain_number(abs(row.open_quantity)),
                                                             what), row.group_id, row.row_number)


def _close_fields(row: PositionRow, settle_text: str, price: Decimal) -> Dict[str, str]:
    return {"status": STATUS_CLOSED, "closed": settle_text, "closed_quantity": plain_number(abs(row.quantity)),
            "close_price_per_unit": money_text(price)}


def _stock_instrument_type(group: PositionGroup) -> str:
    for leg in group.legs:
        if leg.is_stock_like:
            return leg.instrument_type
    return INSTRUMENT_EQUITY


def _stock_row_fields(row: PositionRow, group: PositionGroup, table: CsvTable, shares: Decimal, strike: Decimal,
                      settle_text: str, verb: str, confirm: bool) -> Dict[str, str]:
    """The cells of an added stock row; ``verb`` is ``assigned`` or ``exercised`` (the note tag)."""
    cost = strike * shares  # carries the sign of the share count
    if row.trade_id:
        note = "#from:%s %s" % (row.trade_id, verb)
    else:
        note = "%s (from row %d)" % (verb, row.row_number)
    if confirm:
        note += " (%s)" % CONFIRM_WITH_BROKER
    return {
        "instrument_type": _stock_instrument_type(group),
        "symbol": row.underlying,
        "quantity": plain_number(shares),
        "account": table.cell(row.row_number, "account").strip(),
        "trade_id": row.trade_id or "",
        "opened": settle_text,
        "cost_basis_total": money_text(cost),
        "status": "open",
        "note": note,
    }


def _deliver_shares(row: PositionRow, group: PositionGroup, table: CsvTable, shares: Decimal, strike: Decimal,
                    settle_text: str, outcome: str, moved: str, verb: str) -> Tuple[List[ProposedEdit], List[Finding]]:
    """Shares ``moved`` ("called away": short call assigned; "delivered": long put exercised): close
    them out of the group's long stock rows FIFO at the strike; what is not held becomes a short
    stock row to confirm with the broker, tagged with ``verb`` (assigned or exercised)."""
    edits: List[ProposedEdit] = []
    findings: List[Finding] = []
    remaining = shares
    holders = [leg for leg in group.stock_legs if leg.open_quantity > 0]
    holders.sort(key=lambda leg: (leg.opened or datetime.date.max, leg.row_number))
    for stock in holders:
        if remaining <= 0:
            break
        take = min(stock.open_quantity, remaining)
        remaining -= take
        if stock.is_partially_closed:
            findings.append(_partial_finding(stock, outcome, "shares at the %s strike" % plain_number(strike)))
            continue
        reason = "shares %s at the %s strike: %s closed at %s on %s" % (moved, plain_number(strike), plain_number(take),
                                                                       money_text(strike), settle_text)
        if take == stock.open_quantity:
            fields = _close_fields(stock, settle_text, strike)
        else:
            fields = {"closed": settle_text, "closed_quantity": plain_number(take), "close_price_per_unit": money_text(strike)}
        edit, finding = _update_edit(table, stock.row_number, fields, reason, outcome, row.group_id)
        if edit is not None:
            edits.append(edit)
        if finding is not None:
            findings.append(finding)
    if remaining > 0:
        reason = ("%s shares %s at the %s strike are not held in the group: a short stock row at the strike, %s"
                  % (plain_number(remaining), moved, plain_number(strike), CONFIRM_WITH_BROKER))
        fields = _stock_row_fields(row, group, table, -remaining, strike, settle_text, verb, True)
        edit, finding = _add_edit(table, fields, reason, outcome, row.group_id)
        if edit is not None:
            edits.append(edit)
        if finding is not None:
            findings.append(finding)
    return edits, findings


def propose_edits(settlement: LegSettlement, row: PositionRow, group: PositionGroup,
                  table: CsvTable) -> Tuple[Tuple[ProposedEdit, ...], Tuple[Finding, ...]]:
    """The CSV edits for one settled leg (module notes), rendered in the file's header order, and
    the findings for what cannot be written as a single line. Outcomes without an edit
    (``PIN_UNCERTAIN``, ``SETTLEMENT_UNKNOWN``, ``EXPIRED_UNRECORDED``) give ``((), ())``."""
    if settlement.outcome not in EDIT_OUTCOMES or settlement.close_price is None:
        return (), ()
    parsed = row.occ
    if parsed is None:
        return (), ()
    if row.is_partially_closed:
        return (), (_partial_finding(row, settlement.outcome, "contracts"),)
    settle_text = settlement.settle_date.isoformat()
    strike = parsed.strike
    if settlement.outcome == OUTCOME_EXPIRED_OTM:
        reason = "expired out of the money: closed at 0 on %s" % settle_text
    elif settlement.outcome == OUTCOME_CASH_SETTLED:
        reason = "cash-settled at %s: closed at %s per share on %s" % (money_text(settlement.close or _ZERO),
                                                                      money_text(settlement.close_price), settle_text)
    else:
        reason = ("closed at 0 on %s: the premium stays in option P/L; the stock is booked at the %s strike"
                  % (settle_text, plain_number(strike)))
    edits: List[ProposedEdit] = []
    findings: List[Finding] = []
    edit, finding = _update_edit(table, row.row_number, _close_fields(row, settle_text, settlement.close_price), reason,
                                 settlement.outcome, row.group_id)
    if edit is not None:
        edits.append(edit)
    if finding is not None:
        findings.append(finding)
    if settlement.outcome in (OUTCOME_PROBABLE_ASSIGNED, OUTCOME_PROBABLE_EXERCISED):
        shares = abs(row.open_quantity) * SHARES_PER_CONTRACT
        verb = "assigned" if settlement.outcome == OUTCOME_PROBABLE_ASSIGNED else "exercised"
        delivers = (row.is_short and parsed.is_call) or (row.is_long and parsed.is_put)
        if delivers:
            moved = "called away" if row.is_short else "delivered"
            more_edits, more_findings = _deliver_shares(row, group, table, shares, strike, settle_text, settlement.outcome,
                                                        moved, verb)
            edits.extend(more_edits)
            findings.extend(more_findings)
        else:
            reason = "%s: %s shares booked at the %s strike on %s" % (verb, plain_number(shares), plain_number(strike),
                                                                     settle_text)
            fields = _stock_row_fields(row, group, table, shares, strike, settle_text, verb, False)
            edit, finding = _add_edit(table, fields, reason, settlement.outcome, row.group_id)
            if edit is not None:
                edits.append(edit)
            if finding is not None:
                findings.append(finding)
    return tuple(edits), tuple(findings)


# --------------------------------------------------------------------------------------------
# The plan: expired legs and history requests
# --------------------------------------------------------------------------------------------


def plan_settlement(positions: PositionsFile, groups: Sequence[PositionGroup], *, now: datetime.datetime,
                    session: market_clock.SessionInfo, cal: Optional[CalendarFile]) -> SettlementPlan:
    """The expired legs of the loaded groups (quarantined groups excluded) and the history
    requests they need, one per underlying and range (module notes)."""
    extra = calendar_events.extra_closures(cal)
    legs: List[ExpiredLeg] = []
    history_requests: List[HistoryRequest] = []
    seen: Set[Tuple[str, datetime.date, datetime.date]] = set()
    for group in groups:
        if group.is_quarantined:
            continue
        for row in group.open_option_legs:
            if row.occ is None:
                continue
            am_settled = is_am_settled_root(cal, row)
            if not is_expired(row, et_date=session.et_date, session=session.session, am_settled=am_settled,
                              extra_holidays=extra):
                continue
            last = market_clock.last_trading_day(row.occ.expiration, am_settled, extra)
            settle = market_clock.settlement_day(row.occ.expiration, am_settled, extra)
            key: Optional[Tuple[str, datetime.date, datetime.date]] = None
            if not am_settled:
                start, end = history_range(last, now, extra)
                request = HistoryRequest(row.underlying, start, end)
                key = request.key
                if key not in seen:
                    seen.add(key)
                    history_requests.append(request)
            legs.append(ExpiredLeg(row, group, am_settled, last, settle, key))
    return SettlementPlan(now, session.et_date, session.session, extra, cal, legs, history_requests,
                          frozenset(leg.row.row_number for leg in legs))


def quote_check_symbols(groups: Sequence[PositionGroup], expired_rows: Iterable[int]) -> List[str]:
    """The reconciliation's one quote batch: the compact symbol and the underlying of every
    unexpired open option leg (quarantined groups excluded), sorted and de-duplicated."""
    expired = set(expired_rows)
    symbols: Set[str] = set()
    for group in groups:
        if group.is_quarantined:
            continue
        for row in group.open_option_legs:
            if row.row_number in expired:
                continue
            symbols.add(row.compact_symbol)
            symbols.add(row.underlying)
    return sorted(symbols)


def plausibility_symbols(groups: Sequence[PositionGroup], expired_rows: Iterable[int]) -> List[str]:
    """``positions_check``'s batch: :func:`quote_check_symbols` plus every open stock-like leg."""
    symbols = set(quote_check_symbols(groups, expired_rows))
    for group in groups:
        if group.is_quarantined:
            continue
        for row in group.stock_legs:
            symbols.add(row.compact_symbol)
    return sorted(symbols)


# --------------------------------------------------------------------------------------------
# Findings: quotes, coverage, roll links, acknowledgements
# --------------------------------------------------------------------------------------------


def _legs_by_symbol(groups: Sequence[PositionGroup]) -> Dict[str, List[PositionRow]]:
    out: Dict[str, List[PositionRow]] = {}
    for group in groups:
        if group.is_quarantined:
            continue
        for row in group.open_option_legs:
            out.setdefault(row.compact_symbol, []).append(row)
            out.setdefault(row.underlying, []).append(row)
    return out


def quote_findings(symbols: Sequence[str], result: Any,
                   groups: Sequence[PositionGroup]) -> Tuple[List[Finding], QuoteCheck]:
    """Findings of the quote check and its :class:`QuoteCheck`. ``result`` is what
    ``quote_cache.get_quotes`` returned (``quotes``, ``missing``, ``requests_made``), or ``None``
    when no batch was sent (module notes for the QUOTE_MISSING rule)."""
    wanted = list(symbols)
    if not wanted:
        return [], QuoteCheck((), (), {}, None, 0, None)
    if result is None:
        finding = Finding(FINDING_QUOTE_CHECK_NOT_RUN, LEVEL_INFO, "quote check not run: %s" % REASON_NOT_ATTEMPTED)
        return [finding], QuoteCheck(tuple(wanted), (), {}, REASON_NOT_ATTEMPTED, 0, None)
    quotes: Dict[str, Quote] = dict(result.quotes)
    missing: Dict[str, str] = {symbol: str(reason) for symbol, reason in dict(result.missing).items()}
    requests_made = int(getattr(result, "requests_made", 0) or 0)
    quoted = tuple(symbol for symbol in wanted if symbol in quotes)
    stamps = [quote.as_of for quote in quotes.values() if quote.as_of is not None]
    as_of = max(stamps) if stamps else None
    findings: List[Finding] = []
    if not quoted and missing:
        reasons = sorted(set(missing.values()))
        if len(reasons) == 1 and reasons[0] != REASON_UNMATCHED:
            reason = reasons[0]
            findings.append(Finding(FINDING_QUOTE_CHECK_NOT_RUN, LEVEL_INFO, "quote check not run: %s" % reason))
            return findings, QuoteCheck(tuple(wanted), (), missing, reason, requests_made, as_of)
    legs = _legs_by_symbol(groups)
    for symbol in wanted:
        if symbol in quotes:
            continue
        reason = missing.get(symbol, "not served")
        rows = legs.get(symbol, [])
        if reason != REASON_UNMATCHED:
            findings.append(Finding(FINDING_QUOTE_CHECK_NOT_RUN, LEVEL_INFO, "quote check not run for %s: %s" % (symbol, reason),
                                    rows[0].group_id if rows else None, rows[0].row_number if rows else None, symbol))
            continue
        option_rows = [row for row in rows if row.compact_symbol == symbol]
        if option_rows:
            for row in option_rows:
                findings.append(Finding(FINDING_QUOTE_MISSING, LEVEL_UNAVAILABLE,
                                        "row %d %s: the vendor did not match the symbol; the leg cannot be marked (check the "
                                        "OCC symbol)" % (row.row_number, symbol), row.group_id, row.row_number, symbol))
            continue
        if any(row.is_index_underlying for row in rows):
            findings.append(Finding(FINDING_UNQUOTABLE_INDEX, LEVEL_WARNING,
                                    "%s: index underlying, unquotable on the sandbox (rows %s)"
                                    % (symbol, ", ".join(str(row.row_number) for row in rows)),
                                    rows[0].group_id if rows else None, None, symbol))
            continue
        findings.append(Finding(FINDING_QUOTE_MISSING, LEVEL_UNAVAILABLE,
                                "underlying %s of row(s) %s: the vendor did not match the symbol"
                                % (symbol, ", ".join(str(row.row_number) for row in rows) or "?"),
                                rows[0].group_id if rows else None, None, symbol))
    return findings, QuoteCheck(tuple(wanted), quoted, missing, None, requests_made, as_of)


def coverage_findings(groups: Sequence[PositionGroup]) -> List[Finding]:
    """``UNCOVERED_SHORT_CALL`` warnings: a closed (or partially closed) stock row while a short
    call stays open and fewer shares than the calls need remain in the group."""
    findings: List[Finding] = []
    for group in groups:
        if group.is_quarantined:
            continue
        short_calls = [leg for leg in group.open_option_legs if leg.is_call and leg.is_short]
        if not short_calls:
            continue
        stock_rows = [leg for leg in group.legs if leg.is_stock_like]
        if not stock_rows:
            continue
        closed_stock = [leg for leg in stock_rows if leg.is_closed or leg.is_partially_closed]
        if not closed_stock:
            continue
        open_shares = sum((leg.open_quantity for leg in stock_rows if leg.is_open), _ZERO)
        needed = SHARES_PER_CONTRACT * sum((abs(leg.open_quantity) for leg in short_calls), _ZERO)
        if open_shares >= needed:
            continue
        rows_text = ", ".join("row %d" % leg.row_number for leg in closed_stock)
        calls_text = ", ".join("row %d" % leg.row_number for leg in short_calls)
        findings.append(Finding(FINDING_UNCOVERED_SHORT_CALL, LEVEL_WARNING,
                                "%s: uncovered short call: stock %s %s closed while the short call (%s) stays open; %s of %s "
                                "shares remain" % (group.group_id, rows_text, "is" if len(closed_stock) == 1 else "are",
                                                   calls_text, plain_number(open_shares), plain_number(needed)),
                                group.group_id, short_calls[0].row_number, short_calls[0].compact_symbol))
    return findings


def roll_findings(rows: Iterable[PositionRow], known_ids: Iterable[str]) -> List[Finding]:
    """``ROLL_LINK`` warnings: ``#roll-of:`` and ``#from:`` tags must name a group in the file."""
    known = set(known_ids)
    findings: List[Finding] = []
    for row in rows:
        for tag, value in (("roll-of", row.roll_of), ("from", row.from_trade)):
            if value and value not in known:
                findings.append(Finding(FINDING_ROLL_LINK, LEVEL_WARNING,
                                        "row %d (%s): #%s:%s names no group in positions.csv" % (row.row_number, row.group_id,
                                                                                                 tag, value),
                                        row.group_id, row.row_number, row.compact_symbol))
    return findings


def _parse_instant(value: Any) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def ack_findings(records: Iterable[Any], status: str, *, table: CsvTable, et_date: datetime.date,
                 group_ids: Iterable[str], extra_holidays: market_clock.ExtraHolidays = ()) -> List[Finding]:
    """``ACTED_UNCHANGED`` nags from ``state/acks.jsonl`` (``records, status`` as
    ``state.read_jsonl`` returns them): the latest ``acted`` record of a group whose ET date is at
    least one trading day before ``et_date`` while :func:`group_rows_sha` still equals its
    ``rows_sha``. A corrupt journal or a malformed acted record is an ``ACKS_UNREADABLE`` warning."""
    findings: List[Finding] = []
    if status == state.STATUS_CORRUPT:
        findings.append(Finding(FINDING_ACKS_UNREADABLE, LEVEL_WARNING,
                                "state/acks.jsonl was corrupt and has been quarantined: the ACTED_UNCHANGED check did not run"))
        return findings
    latest: Dict[str, Tuple[datetime.datetime, Dict[str, Any]]] = {}
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            findings.append(Finding(FINDING_ACKS_UNREADABLE, LEVEL_WARNING, "acks.jsonl record %d is not an object" % index))
            continue
        if record.get("action") != ACK_ACTION_ACTED:
            continue
        trade_id = record.get("trade_id")
        moment = _parse_instant(record.get("utc"))
        if not isinstance(trade_id, str) or not trade_id or moment is None:
            findings.append(Finding(FINDING_ACKS_UNREADABLE, LEVEL_WARNING,
                                    "acks.jsonl record %d (acted) lacks a trade_id or a UTC instant" % index))
            continue
        current = latest.get(trade_id)
        if current is None or moment > current[0]:
            latest[trade_id] = (moment, record)
    known = set(group_ids)
    for trade_id in sorted(latest):
        moment, record = latest[trade_id]
        if trade_id not in known:
            continue
        sha = group_rows_sha(table, trade_id)
        if sha is None or sha != record.get("rows_sha"):
            continue
        acted_date = market_clock.to_et(moment).date()
        days = market_clock.trading_days_between(acted_date, et_date, extra_holidays)
        if days < 1:
            continue
        findings.append(Finding(FINDING_ACTED_UNCHANGED, LEVEL_WARNING,
                                "%s: acted on %s (%d trading day%s ago) but its rows in positions.csv are unchanged: record the "
                                "close or the new position" % (trade_id, acted_date.isoformat(), days, "" if days == 1 else "s"),
                                trade_id, None, None))
    return findings


# --------------------------------------------------------------------------------------------
# The verdict and the reconciliation
# --------------------------------------------------------------------------------------------


def verdict_for(*, file_errors: bool, unavailable: bool, edits: bool) -> str:
    """``ERRORS > UNAVAILABLE > EDITS_PENDING > CLEAN``."""
    if file_errors:
        return VERDICT_ERRORS
    if unavailable:
        return VERDICT_UNAVAILABLE
    if edits:
        return VERDICT_EDITS_PENDING
    return VERDICT_CLEAN


def reconcile(*, plan: SettlementPlan, positions: PositionsFile, groups: Sequence[PositionGroup], table: CsvTable,
              pin_band: Decimal, histories: Mapping[Any, Any], quotes: Any,
              acks: Tuple[Iterable[Any], str]) -> Reconciliation:
    """Run every check on fetched inputs (module notes). ``histories`` maps a
    :attr:`HistoryRequest.key` to an object with ``bars`` and ``missing_reason`` (what
    ``quote_cache.get_history`` returns); ``quotes`` is the ``get_quotes`` result for
    :func:`quote_check_symbols` or ``None``; ``acks`` is ``state.read_jsonl(acks_jsonl)``."""
    file_errors = [str(error) for error in positions.file_errors]
    quarantined: Dict[str, List[str]] = {group_id: [str(error) for error in errors]
                                         for group_id, errors in positions.quarantined.items()}
    for group in groups:
        if group.is_quarantined:
            quarantined.setdefault(group.group_id, []).extend(group.errors)
    findings: List[Finding] = []
    for group_id in sorted(quarantined):
        findings.append(Finding(FINDING_QUARANTINED, LEVEL_UNAVAILABLE,
                                "%s: quarantined: %s" % (group_id, "; ".join(quarantined[group_id])), group_id))
    settlements: List[LegSettlement] = []
    edits: List[ProposedEdit] = []
    for leg in plan.legs:
        row = leg.row
        assert row.occ is not None
        if leg.am_settled:
            value = calendar_events.settlement_value(plan.cal, row.occ.root, leg.settle_date)
            settlement = settle_am(row, value, extra_holidays=plan.extra_holidays, now=plan.now)
        else:
            served = histories.get(leg.history_key) if leg.history_key is not None else None
            if served is None:
                settlement = settle_pm(row, [], pin_band=pin_band, extra_holidays=plan.extra_holidays,
                                       history_reason=REASON_NOT_FETCHED)
            else:
                bars = list(getattr(served, "bars", []) or [])
                reason = getattr(served, "missing_reason", None)
                settlement = settle_pm(row, bars, pin_band=pin_band, extra_holidays=plan.extra_holidays,
                                       history_reason=str(reason) if reason else None)
        leg_edits, leg_findings = propose_edits(settlement, row, leg.group, table)
        settlement = replace(settlement, edits=tuple(leg_edits))
        settlements.append(settlement)
        edits.extend(leg_edits)
        findings.extend(leg_findings)
    symbols = quote_check_symbols(groups, plan.expired_rows)
    quote_items, check = quote_findings(symbols, quotes, groups)
    findings.extend(quote_items)
    findings.extend(coverage_findings(groups))
    known_ids = set(positions.group_ids) | set(positions.quarantined) | set(quarantined)
    findings.extend(roll_findings(positions.rows, known_ids))
    records, status = acks
    findings.extend(ack_findings(records, status, table=table, et_date=plan.et_date, group_ids=known_ids,
                                 extra_holidays=plan.extra_holidays))
    unavailable_items = sum(1 for item in settlements if item.unavailable) + sum(
        1 for finding in findings if finding.level == LEVEL_UNAVAILABLE)
    verdict = verdict_for(file_errors=bool(file_errors), unavailable=unavailable_items > 0, edits=bool(edits))
    live_groups = [group for group in groups if not group.is_quarantined]
    counts = {
        "groups": len(live_groups) + len(quarantined),
        "open_groups": sum(1 for group in live_groups if group.open_legs),
        "closed_groups": sum(1 for group in live_groups if not group.open_legs),
        "quarantined_groups": len(quarantined),
        "expired_legs": len(settlements),
        "edits": len(edits),
        "unavailable": unavailable_items,
        "warnings": sum(1 for finding in findings if finding.level == LEVEL_WARNING),
    }
    return Reconciliation(verdict, VERDICT_EXIT_CODES[verdict], plan.et_date, plan.session, settlements, edits, findings,
                          check, quarantined, file_errors, counts)


# --------------------------------------------------------------------------------------------
# Plausibility (positions_check)
# --------------------------------------------------------------------------------------------


def _within(entry: Decimal, mark: Decimal, factor: Decimal) -> bool:
    return mark / factor <= entry <= mark * factor


def _money(value: Decimal) -> str:
    """At least two decimals, never fewer digits than written (the quote_cache prose rule)."""
    text = str(value)
    if "." not in text:
        return text + ".00"
    digits = len(text) - text.index(".") - 1
    return text + "0" * (2 - digits) if digits < 2 else text


def plausibility(rows: Iterable[PositionRow], quotes: Mapping[str, Quote], *, missing: Mapping[str, str],
                 expired_rows: Iterable[int], sandbox: bool) -> List[PlausibilityCheck]:
    """The first-observation check of every OPEN row (module notes): an option whose entry
    premium per share lies outside ``[mid / 20, mid x 20]`` is the row ERROR ``unit mismatch?``;
    a stock-like row outside ``[mid / 10, mid x 10]`` is a WARNING. Expired legs are labelled
    (``csv_recon`` settles them); an unquoted index underlying on the sandbox is ``unquotable``;
    any other unquoted row is ``not_checked`` with the cache's reason."""
    expired = set(expired_rows)
    checks: List[PlausibilityCheck] = []
    for row in rows:
        if not row.is_open:
            continue
        symbol = row.compact_symbol
        entry = row.entry_price_per_share
        base = dict(row_number=row.row_number, group_id=row.group_id, trade_id=row.trade_id, symbol=symbol,
                    instrument_type=row.instrument_type, entry=entry)
        if row.row_number in expired:
            checks.append(PlausibilityCheck(mark=None, status=PLAUSIBILITY_EXPIRED,
                                            message="expired %s: settled by csv_recon, not quoted" % symbol, **base))
            continue
        quote = quotes.get(symbol)
        if quote is None:
            reason = missing.get(symbol, "not served")
            if sandbox and row.is_index_underlying:
                checks.append(PlausibilityCheck(mark=None, status=PLAUSIBILITY_UNQUOTABLE,
                                                message="index underlying %s: unquotable on the sandbox (%s)" % (symbol, reason),
                                                **base))
            else:
                checks.append(PlausibilityCheck(mark=None, status=PLAUSIBILITY_NOT_CHECKED,
                                                message="plausibility not checked: no quote for %s (%s)" % (symbol, reason),
                                                **base))
            continue
        mid = quote.mid
        if mid is None or mid <= 0:
            checks.append(PlausibilityCheck(mark=None, status=PLAUSIBILITY_NOT_CHECKED,
                                            message="plausibility not checked: %s has no two-sided quote" % symbol, **base))
            continue
        if row.is_option:
            if _within(entry, mid, PLAUSIBILITY_OPTION_FACTOR):
                status, message = PLAUSIBILITY_OK, "entry %s vs mark %s: ok" % (_money(entry), _money(mid))
            else:
                status = PLAUSIBILITY_ERROR
                message = "%s entry %s vs mark %s" % (MESSAGE_UNIT_MISMATCH, _money(entry), _money(mid))
        elif _within(entry, mid, PLAUSIBILITY_STOCK_FACTOR):
            status, message = PLAUSIBILITY_OK, "entry %s vs mark %s: ok" % (_money(entry), _money(mid))
        else:
            status = PLAUSIBILITY_WARNING
            message = "entry %s vs mark %s: outside [mark / 10, mark x 10]" % (_money(entry), _money(mid))
        checks.append(PlausibilityCheck(mark=mid, status=status, message=message, **base))
    return checks


def plausibility_quarantine(checks: Iterable[PlausibilityCheck],
                            rows: Iterable[PositionRow]) -> Dict[str, List[RowError]]:
    """The row errors of the ``error`` checks, grouped by ``group_id`` (they quarantine the group
    exactly as a parser error would)."""
    by_number = {row.row_number: row for row in rows}
    quarantine: Dict[str, List[RowError]] = {}
    for check in checks:
        if check.status != PLAUSIBILITY_ERROR:
            continue
        row = by_number.get(check.row_number)
        column = "open_price_per_share" if row is not None and row.open_price_per_share is not None else "cost_basis_total"
        quarantine.setdefault(check.group_id, []).append(RowError(check.row_number, column, check.message, check.trade_id))
    return quarantine


# --------------------------------------------------------------------------------------------
# The positions snapshot
# --------------------------------------------------------------------------------------------


def snapshot_positions(paths: DeskPaths, data: bytes, et_date: datetime.date) -> Snapshot:
    """Copy the ``positions.csv`` bytes to ``data/positions/<ET date>.csv`` and write
    ``<ET date>.sha256`` (``<hex>  <ET date>.csv``) atomically under the desk lock. The source
    file is never opened for writing."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("snapshot_positions() takes the file bytes, got %s" % type(data).__name__)
    name = et_date.isoformat()
    csv_path = os.path.join(paths.positions_snapshots_dir, name + ".csv")
    sha_path = os.path.join(paths.positions_snapshots_dir, name + ".sha256")
    digest = hashlib.sha256(bytes(data)).hexdigest()
    with state.locked(paths.lock_path):
        state.atomic_write_bytes(csv_path, bytes(data))
        state.atomic_write_text(sha_path, "%s  %s.csv\n" % (digest, name))
    return Snapshot(csv_path, sha_path, digest)


__all__ = [
    "ACK_ACTION_ACTED",
    "CENT",
    "CONFIRM_WITH_BROKER",
    "CsvTable",
    "EDIT_ADD",
    "EDIT_OUTCOMES",
    "EDIT_UPDATE",
    "ExpiredLeg",
    "FINDING_ACKS_UNREADABLE",
    "FINDING_ACTED_UNCHANGED",
    "FINDING_EDIT_UNREPRESENTABLE",
    "FINDING_KINDS",
    "FINDING_QUARANTINED",
    "FINDING_QUOTE_CHECK_NOT_RUN",
    "FINDING_QUOTE_MISSING",
    "FINDING_ROLL_LINK",
    "FINDING_UNCOVERED_SHORT_CALL",
    "FINDING_UNQUOTABLE_INDEX",
    "Finding",
    "HistoryRequest",
    "LEVELS",
    "LEVEL_INFO",
    "LEVEL_UNAVAILABLE",
    "LEVEL_WARNING",
    "LegSettlement",
    "MESSAGE_UNIT_MISMATCH",
    "OUTCOMES",
    "OUTCOME_CASH_SETTLED",
    "OUTCOME_EXPIRED_OTM",
    "OUTCOME_EXPIRED_UNRECORDED",
    "OUTCOME_PIN_UNCERTAIN",
    "OUTCOME_PROBABLE_ASSIGNED",
    "OUTCOME_PROBABLE_EXERCISED",
    "OUTCOME_SETTLEMENT_PENDING",
    "OUTCOME_SETTLEMENT_UNKNOWN",
    "PLAUSIBILITY_ERROR",
    "PLAUSIBILITY_EXPIRED",
    "PLAUSIBILITY_NOT_CHECKED",
    "PLAUSIBILITY_OK",
    "PLAUSIBILITY_OPTION_FACTOR",
    "PLAUSIBILITY_STATUSES",
    "PLAUSIBILITY_STOCK_FACTOR",
    "PLAUSIBILITY_UNQUOTABLE",
    "PLAUSIBILITY_WARNING",
    "PlausibilityCheck",
    "ProposedEdit",
    "QuoteCheck",
    "REASON_NOT_ATTEMPTED",
    "REASON_NOT_FETCHED",
    "REASON_NO_BARS",
    "REASON_UNMATCHED",
    "Reconciliation",
    "SETTLEMENT_AM",
    "SETTLEMENT_PM",
    "SHARES_PER_CONTRACT",
    "SettlementPlan",
    "Snapshot",
    "UNAVAILABLE_OUTCOMES",
    "VERDICTS",
    "VERDICT_CLEAN",
    "VERDICT_EDITS_PENDING",
    "VERDICT_ERRORS",
    "VERDICT_EXIT_CODES",
    "VERDICT_UNAVAILABLE",
    "ack_findings",
    "bar_after",
    "cell_decimal",
    "coverage_findings",
    "crossed_strike",
    "csv_table",
    "expiry_bar",
    "group_rows_sha",
    "history_range",
    "in_the_money",
    "intrinsic",
    "is_am_settled_root",
    "is_expired",
    "money_text",
    "plain_number",
    "plan_settlement",
    "plausibility",
    "plausibility_quarantine",
    "plausibility_symbols",
    "propose_edits",
    "quote_check_symbols",
    "quote_findings",
    "reconcile",
    "render_csv_line",
    "roll_findings",
    "settle_am",
    "settle_pm",
    "snapshot_positions",
    "verdict_for",
]
