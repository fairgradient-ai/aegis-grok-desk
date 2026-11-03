"""Research calendar: ``research/calendar.toml`` read strictly, and what the desk asks of it.

New for Aegis Desk (plan section 8 "research/calendar.toml", skill 7); standard library only.
The file is owner- or Events-sourced and every entry is dated and sourced: the next earnings
date, the next ex-dividend date with its amount, a dividend yield, the AM-settled index roots
with their settlement values, and exchange closures the embedded NYSE table may not know. The
reader is strict (:func:`aegis_desk.toml_compat.load`, never ``tomllib``): unknown keys, wrong
types and contradictory statuses are :class:`CalendarError` (a ``ValueError`` whose ``str`` is
``"<path>: <reason>"``), so a typo never becomes a silent zero.

Shape (every value typed by the vendored reader: TOML floats are ``Decimal``, local dates are
``datetime.date``, offset date-times are aware ``datetime.datetime``)::

    schema_version = 1
    [symbols.WMT]
    earnings = { date = 2026-11-17, time = "pre", status = "confirmed", source = "...", checked_utc = 2026-11-01T12:00:00Z }
    ex_dividend = { date = 2026-12-11, amount = 0.2350, status = "estimated", source = "...", checked_utc = 2026-11-01T12:00:00Z }
    dividend_yield = 0.0090
    [symbols.NVDA]
    ex_dividend = { status = "none", source = "...", checked_utc = 2026-11-01T12:00:00Z }
    [settlement]
    am_settled_roots = ["SPX", "NDX", "RUT", "VIX", "DJX"]
    [[settlement.values]]
    root = "SPX"
    date = 2026-11-20
    value = 6523.17
    source = "..."
    checked_utc = 2026-11-23T12:00:00Z
    [[closures]]
    date = 2026-12-24
    kind = "early_close"      # full | early_close
    source = "..."

Rules
-----
* ``status`` is ``confirmed`` | ``estimated`` | ``unknown`` | ``none``; ``time`` (earnings) is
  ``pre`` | ``post`` | ``unknown`` and defaults to ``unknown``. ``confirmed`` and ``estimated``
  need a ``date`` (and, for ``ex_dividend``, an ``amount`` > 0); ``none`` (a confirmed absence)
  takes no date, time or amount; ``unknown`` may carry a date but never an amount. ``source`` is
  a non-empty string and ``checked_utc`` an offset date-time (a local date-time has no zone and
  is refused) on every entry. ``dividend_yield`` is a fraction in ``[0, 1)``.
* Absent is unknown, never zero: a symbol table may omit any of its three keys, a symbol may be
  absent altogether, and every lookup then answers ``None`` / status ``unknown``.
* Symbol table names and roots are written upper-case exactly as the vendor quotes them
  (``[symbols.WMT]``); lookups normalise their argument. A symbol with a dot is quoted in
  the header (``[symbols."BRK.B"]``): unquoted, TOML reads ``BRK.B`` as a nested table.
* ``settlement.values`` are the official settlement values of AM-settled index options, one per
  ``(root, date)``; ``settlement_value`` returns ``None`` when a value is missing, which
  ``csv_recon`` reports as ``SETTLEMENT_UNKNOWN``.
* Closures: ``kind = "full"`` is a day without a session; ``kind = "early_close"`` a 13:00 ET
  session. Weekend dates and duplicates are errors. :meth:`CalendarFile.extra_closures` returns
  the FULL closures in exactly the mapping shape ``market_clock``'s ``extra_holidays`` takes
  (``{date: label}``; the label is the embedded table's own name when it already lists the day,
  else :data:`CLOSURE_LABEL`). Early closes are deliberately NOT injected: ``market_clock``
  treats every injected date as a full closure (``is_trading_day`` is false for it), so passing
  an early close would turn a 13:00 ET session into a holiday for ``session_at``, ``dte``,
  ``last_trading_day`` and ``exercise_cutoff_utc``. The embedded table already knows the
  scheduled early closes; an ``early_close`` entry it does not know is a load ``warning`` (the
  clock has no early-close injection) and is listed by :meth:`CalendarFile.early_closes`.
* ``dividend_before(symbol, expiry, as_of)`` answers for the window ``(as_of, expiry]`` on ET
  dates: the entry when its ex-date falls inside, ``(None, None, <status>)`` when the next
  ex-date is after the expiry (no dividend before expiry, at the entry's confidence),
  ``(None, None, "none")`` for a confirmed absence, and ``(None, None, "unknown")`` for a
  missing symbol or entry, status ``unknown``, or an ex-date that has already passed (the
  listed dividend is spent and the next one is not in the file). Status ``unknown`` is never
  treated as zero by a consumer.
* :func:`freshness` lints the file for a run at ``now``: ``PAST`` for a dated earnings or
  ex-dividend entry before today's ET date (reported once; the entry must be replaced anyway);
  ``STALE`` for an entry whose ``checked_utc`` is more than ``max_age_days`` old; ``MISSING``
  for a held symbol absent from the file; ``UNKNOWN_DIVIDEND`` for a held symbol in the
  short-call scope whose ``ex_dividend`` is absent or ``unknown``. Settlement values are facts
  and closures are a record: neither is stale or past.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from . import market_clock, toml_compat
from .toml_compat import TomlError

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

SCHEMA_VERSION: int = 1
STATUS_CONFIRMED: str = "confirmed"
STATUS_ESTIMATED: str = "estimated"
STATUS_UNKNOWN: str = "unknown"
STATUS_NONE: str = "none"
STATUSES: Tuple[str, ...] = (STATUS_CONFIRMED, STATUS_ESTIMATED, STATUS_UNKNOWN, STATUS_NONE)
DATED_STATUSES: Tuple[str, ...] = (STATUS_CONFIRMED, STATUS_ESTIMATED)
TIME_PRE: str = "pre"
TIME_POST: str = "post"
TIME_UNKNOWN: str = "unknown"
TIMES: Tuple[str, ...] = (TIME_PRE, TIME_POST, TIME_UNKNOWN)
KIND_FULL: str = "full"
KIND_EARLY_CLOSE: str = "early_close"
CLOSURE_KINDS: Tuple[str, ...] = (KIND_FULL, KIND_EARLY_CLOSE)
CLOSURE_LABEL: str = "calendar closure"
"""The ``extra_holidays`` label of a full closure the embedded NYSE table does not name."""
FINDING_STALE: str = "STALE"
FINDING_MISSING: str = "MISSING"
FINDING_UNKNOWN_DIVIDEND: str = "UNKNOWN_DIVIDEND"
FINDING_PAST: str = "PAST"
FINDING_KINDS: Tuple[str, ...] = (FINDING_STALE, FINDING_MISSING, FINDING_UNKNOWN_DIVIDEND, FINDING_PAST)
DEFAULT_MAX_AGE_DAYS: int = 7
SYMBOL_RE = re.compile(r"^[A-Z0-9.$^:_-]{1,32}$")
"""The upper-case form of the client's symbol pattern: what a table name or root must match."""

TOP_LEVEL_KEYS: Tuple[str, ...] = ("schema_version", "symbols", "settlement", "closures")
SYMBOL_KEYS: Tuple[str, ...] = ("earnings", "ex_dividend", "dividend_yield")
EARNINGS_KEYS: Tuple[str, ...] = ("date", "time", "status", "source", "checked_utc")
EX_DIVIDEND_KEYS: Tuple[str, ...] = ("date", "amount", "status", "source", "checked_utc")
SETTLEMENT_KEYS: Tuple[str, ...] = ("am_settled_roots", "values")
SETTLEMENT_VALUE_KEYS: Tuple[str, ...] = ("root", "date", "value", "source", "checked_utc")
CLOSURE_KEYS: Tuple[str, ...] = ("date", "kind", "source")

DateLike = Union[datetime.date, datetime.datetime]


class CalendarError(ValueError):
    """``calendar.toml`` cannot be used; ``path`` names the file and ``reason`` is one line."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = str(path)
        self.reason = " ".join(str(reason).split())
        super().__init__("%s: %s" % (self.path, self.reason))


# --------------------------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EarningsEntry:
    """One ``earnings`` entry; ``date`` is ``None`` for ``none`` and may be for ``unknown``."""

    status: str
    source: str
    checked_utc: datetime.datetime
    date: Optional[datetime.date] = None
    time: str = TIME_UNKNOWN


@dataclass(frozen=True)
class DividendEntry:
    """One ``ex_dividend`` entry; ``amount`` is set only for ``confirmed`` and ``estimated``."""

    status: str
    source: str
    checked_utc: datetime.datetime
    date: Optional[datetime.date] = None
    amount: Optional[Decimal] = None


@dataclass(frozen=True)
class SymbolEvents:
    """What the file says about one symbol; ``listed`` is false for a symbol it does not name."""

    symbol: str
    listed: bool
    earnings: Optional[EarningsEntry] = None
    ex_dividend: Optional[DividendEntry] = None
    dividend_yield: Optional[Decimal] = None


@dataclass(frozen=True)
class SettlementValue:
    root: str
    date: datetime.date
    value: Decimal
    source: str
    checked_utc: datetime.datetime


@dataclass(frozen=True)
class Closure:
    date: datetime.date
    kind: str
    source: str


@dataclass(frozen=True)
class DividendView:
    """:meth:`CalendarFile.dividend_before`: ``amount`` and ``date`` are set only when an
    ex-date falls inside the window; ``status`` is the knowledge quality (module notes)."""

    amount: Optional[Decimal]
    date: Optional[datetime.date]
    status: str


@dataclass(frozen=True)
class Finding:
    """One :func:`freshness` finding; ``kind`` is one of :data:`FINDING_KINDS`."""

    kind: str
    symbol: Optional[str]
    message: str


@dataclass(frozen=True)
class CalendarFile:
    """The parsed calendar (module notes). ``sha256`` is the hex digest of the file's bytes;
    ``warnings`` are load-time notices that are not errors."""

    path: str
    schema_version: int
    symbols: Dict[str, SymbolEvents]
    am_settled_roots: Tuple[str, ...]
    settlement_values: Tuple[SettlementValue, ...]
    closures: Tuple[Closure, ...]
    sha256: str
    warnings: Tuple[str, ...] = ()

    def events_for(self, symbol: str) -> SymbolEvents:
        """The symbol's entries, or an unlisted :class:`SymbolEvents` (everything ``None``)."""
        key = _normalise_symbol(symbol)
        found = self.symbols.get(key)
        return found if found is not None else SymbolEvents(key, False)

    def dividend_yield(self, symbol: str) -> Optional[Decimal]:
        """The symbol's ``dividend_yield`` or ``None`` (absent is unknown, never zero)."""
        return self.events_for(symbol).dividend_yield

    def dividend_before(self, symbol: str, expiry: DateLike, as_of: DateLike) -> DividendView:
        """The ex-dividend inside ``(as_of, expiry]`` on ET dates (module notes)."""
        expiry_date = _as_date(expiry, "expiry")
        as_of_date = _as_date(as_of, "as_of")
        entry = self.events_for(symbol).ex_dividend
        if entry is None or entry.status == STATUS_UNKNOWN:
            return DividendView(None, None, STATUS_UNKNOWN)
        if entry.status == STATUS_NONE:
            return DividendView(None, None, STATUS_NONE)
        if entry.date is None or entry.date <= as_of_date:
            return DividendView(None, None, STATUS_UNKNOWN)  # undated, or the listed dividend is spent
        if entry.date <= expiry_date:
            return DividendView(entry.amount, entry.date, entry.status)
        return DividendView(None, None, entry.status)

    def is_am_settled(self, root: str) -> bool:
        """True when ``root`` is listed in ``am_settled_roots`` (exact root: ``SPXW`` is not ``SPX``)."""
        return _normalise_symbol(root) in self.am_settled_roots

    def settlement_value(self, root: str, day: datetime.date) -> Optional[Decimal]:
        """The recorded settlement value of ``root`` struck on ``day``, or ``None``."""
        key = _normalise_symbol(root)
        if not _is_local_date(day):
            raise ValueError("day must be a datetime.date, got %r" % (day,))
        for value in self.settlement_values:
            if value.root == key and value.date == day:
                return value.value
        return None

    def extra_closures(self) -> Dict[datetime.date, str]:
        """The full closures as ``market_clock``'s ``extra_holidays`` mapping (module notes)."""
        out: Dict[datetime.date, str] = {}
        for closure in self.closures:
            if closure.kind != KIND_FULL:
                continue
            entry = market_clock.calendar_entry(closure.date)
            out[closure.date] = entry.name if entry is not None and entry.type == market_clock.ENTRY_FULL else CLOSURE_LABEL
        return out

    def early_closes(self) -> Dict[datetime.date, str]:
        """The ``early_close`` closures as ``{date: source}`` (for display; never injected)."""
        return {closure.date: closure.source for closure in self.closures if closure.kind == KIND_EARLY_CLOSE}


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return _is_int(value) or isinstance(value, Decimal)


def _as_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(value)


def _is_local_date(value: Any) -> bool:
    return isinstance(value, datetime.date) and not isinstance(value, datetime.datetime)


def _is_instant(value: Any) -> bool:
    return isinstance(value, datetime.datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _require_utc(value: Any, what: str = "now") -> datetime.datetime:
    if not _is_instant(value):
        raise ValueError("%s must be an aware datetime (UTC), got %r" % (what, value))
    return value.astimezone(UTC)


def _as_date(value: Any, what: str) -> datetime.date:
    """A ``date`` as given, or the America/New_York date of an aware datetime."""
    if _is_local_date(value):
        return value
    if _is_instant(value):
        return market_clock.to_et(value).date()
    raise ValueError("%s must be a datetime.date or an aware datetime, got %r" % (what, value))


def _normalise_symbol(symbol: Any) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-blank string, got %r" % (symbol,))
    return symbol.strip().upper()


def _symbol_list(values: Any, what: str) -> List[str]:
    """Upper-cased, stripped, de-duplicated symbols in order; a lone string is one symbol."""
    if isinstance(values, str):
        values = [values]
    out: List[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("%s must hold non-blank strings, got %r" % (what, value))
        key = value.strip().upper()
        if key not in out:
            out.append(key)
    return out


class _Reader:
    """One parse of one document; every ``fail`` carries the file path."""

    def __init__(self, path: str) -> None:
        self.path = path

    def fail(self, reason: str) -> CalendarError:
        return CalendarError(self.path, reason)

    def check_keys(self, table: Dict[str, Any], allowed: Tuple[str, ...], where: str) -> None:
        for key in table:
            if key not in allowed:
                raise self.fail("unknown key '%s' in %s (allowed: %s)" % (key, where, ", ".join(allowed)))

    def table(self, value: Any, where: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise self.fail("%s must be a table (got %s)" % (where, type(value).__name__))
        return value

    def tables(self, value: Any, where: str, header: str) -> List[Dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise self.fail("%s must be an array of tables (%s entries)" % (where, header))
        return value

    def require(self, table: Dict[str, Any], key: str, where: str) -> Any:
        if key not in table:
            raise self.fail("missing key '%s' in %s" % (key, where))
        return table[key]

    def string(self, table: Dict[str, Any], key: str, where: str) -> str:
        value = self.require(table, key, where)
        if not isinstance(value, str) or not value.strip():
            raise self.fail("%s %s must be a non-empty string (got %r)" % (where, key, value))
        return value

    def choice(self, table: Dict[str, Any], key: str, where: str, choices: Tuple[str, ...]) -> str:
        value = self.require(table, key, where)
        if not isinstance(value, str) or value not in choices:
            raise self.fail("%s %s must be one of %s (got %r)" % (where, key, ", ".join(choices), value))
        return value

    def local_date(self, table: Dict[str, Any], key: str, where: str) -> datetime.date:
        value = self.require(table, key, where)
        if not _is_local_date(value):
            raise self.fail("%s %s must be a local date such as 2026-11-17 (got %r)" % (where, key, value))
        return value

    def instant(self, table: Dict[str, Any], key: str, where: str) -> datetime.datetime:
        value = self.require(table, key, where)
        if not _is_instant(value):
            raise self.fail("%s %s must be an offset date-time such as 2026-11-01T12:00:00Z (got %r: no UTC offset)"
                            % (where, key, value))
        return value.astimezone(UTC)

    def positive_number(self, table: Dict[str, Any], key: str, where: str, hint: str = "") -> Decimal:
        value = self.require(table, key, where)
        if not _is_number(value):
            raise self.fail("%s %s must be a number (got %r)" % (where, key, value))
        result = _as_decimal(value)
        if not result > 0:
            raise self.fail("%s %s must be a number > 0 (got %s)%s" % (where, key, result, hint))
        return result

    def forbid(self, table: Dict[str, Any], key: str, where: str, status: str) -> None:
        if key in table:
            raise self.fail("%s status \"%s\" takes no %s (got %r)" % (where, status, key, table[key]))

    def upper_symbol(self, value: Any, where: str, what: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise self.fail("%s %s must be a non-empty string (got %r)" % (where, what, value))
        if value != value.strip().upper():
            raise self.fail("%s %s must be upper-case (%s)" % (where, what, value.strip().upper()))
        if not SYMBOL_RE.fullmatch(value):
            raise self.fail("%s %s %r is not a valid symbol (%s)" % (where, what, value, SYMBOL_RE.pattern))
        return value


# --------------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------------


def _parse_earnings(reader: _Reader, raw: Any, where: str) -> EarningsEntry:
    table = reader.table(raw, where)
    reader.check_keys(table, EARNINGS_KEYS, where)
    status = reader.choice(table, "status", where, STATUSES)
    source = reader.string(table, "source", where)
    checked = reader.instant(table, "checked_utc", where)
    if status == STATUS_NONE:
        reader.forbid(table, "date", where, status)
        reader.forbid(table, "time", where, status)
        return EarningsEntry(status, source, checked)
    if status in DATED_STATUSES and "date" not in table:
        raise reader.fail("%s status \"%s\" needs a date (an undated event is status \"unknown\")" % (where, status))
    day = reader.local_date(table, "date", where) if "date" in table else None
    time_of_day = reader.choice(table, "time", where, TIMES) if "time" in table else TIME_UNKNOWN
    return EarningsEntry(status, source, checked, day, time_of_day)


def _parse_dividend(reader: _Reader, raw: Any, where: str) -> DividendEntry:
    table = reader.table(raw, where)
    reader.check_keys(table, EX_DIVIDEND_KEYS, where)
    status = reader.choice(table, "status", where, STATUSES)
    source = reader.string(table, "source", where)
    checked = reader.instant(table, "checked_utc", where)
    if status == STATUS_NONE:
        reader.forbid(table, "date", where, status)
        reader.forbid(table, "amount", where, status)
        return DividendEntry(status, source, checked)
    if status in DATED_STATUSES:
        if "date" not in table:
            raise reader.fail("%s status \"%s\" needs a date (an undated dividend is status \"unknown\")" % (where, status))
        if "amount" not in table:
            raise reader.fail("%s status \"%s\" needs an amount (use status \"none\" for no dividend)" % (where, status))
        day = reader.local_date(table, "date", where)
        amount = reader.positive_number(table, "amount", where, ": use status \"none\" for no dividend")
        return DividendEntry(status, source, checked, day, amount)
    if "amount" in table:
        raise reader.fail("%s status \"unknown\" takes no amount (an amount with an unknown dividend is contradictory: "
                          "use \"estimated\", got %r)" % (where, table["amount"]))
    day = reader.local_date(table, "date", where) if "date" in table else None
    return DividendEntry(status, source, checked, day)


def _parse_symbols(reader: _Reader, raw: Any) -> Dict[str, SymbolEvents]:
    table = reader.table(raw, "symbols")
    out: Dict[str, SymbolEvents] = {}
    for key, value in table.items():
        where = "[symbols.%s]" % key
        symbol = reader.upper_symbol(key, where, "symbol")
        entries = reader.table(value, where)
        reader.check_keys(entries, SYMBOL_KEYS, where)
        earnings = _parse_earnings(reader, entries["earnings"], where + " earnings") if "earnings" in entries else None
        ex_dividend = (_parse_dividend(reader, entries["ex_dividend"], where + " ex_dividend")
                       if "ex_dividend" in entries else None)
        dividend_yield: Optional[Decimal] = None
        if "dividend_yield" in entries:
            value_yield = entries["dividend_yield"]
            if not _is_number(value_yield):
                raise reader.fail("%s dividend_yield must be a number (got %r)" % (where, value_yield))
            dividend_yield = _as_decimal(value_yield)
            if not Decimal(0) <= dividend_yield < Decimal(1):
                raise reader.fail("%s dividend_yield must be a fraction between 0 and 1 (0.0090 is 0.90 %%), got %s"
                                  % (where, dividend_yield))
        out[symbol] = SymbolEvents(symbol, True, earnings, ex_dividend, dividend_yield)
    return out


def _parse_settlement(reader: _Reader, raw: Any) -> Tuple[Tuple[str, ...], Tuple[SettlementValue, ...]]:
    where = "[settlement]"
    table = reader.table(raw, where)
    reader.check_keys(table, SETTLEMENT_KEYS, where)
    roots: List[str] = []
    if "am_settled_roots" in table:
        listed = table["am_settled_roots"]
        if not isinstance(listed, list):
            raise reader.fail("%s am_settled_roots must be a list of root symbols (got %r)" % (where, listed))
        for item in listed:
            root = reader.upper_symbol(item, where + " am_settled_roots", "root")
            if root in roots:
                raise reader.fail("%s am_settled_roots lists %s twice" % (where, root))
            roots.append(root)
    values: List[SettlementValue] = []
    if "values" in table:
        seen: Dict[Tuple[str, datetime.date], int] = {}
        for index, item in enumerate(reader.tables(table["values"], where + " values", "[[settlement.values]]"), 1):
            where_value = "[[settlement.values]] #%d" % index
            reader.check_keys(item, SETTLEMENT_VALUE_KEYS, where_value)
            root = reader.upper_symbol(reader.require(item, "root", where_value), where_value, "root")
            day = reader.local_date(item, "date", where_value)
            value = reader.positive_number(item, "value", where_value)
            source = reader.string(item, "source", where_value)
            checked = reader.instant(item, "checked_utc", where_value)
            if (root, day) in seen:
                raise reader.fail("%s lists %s %s twice (first at #%d)" % (where_value, root, day.isoformat(), seen[(root, day)]))
            seen[(root, day)] = index
            values.append(SettlementValue(root, day, value, source, checked))
    return tuple(roots), tuple(values)


def _parse_closures(reader: _Reader, raw: Any) -> Tuple[Closure, ...]:
    closures: List[Closure] = []
    seen: Dict[datetime.date, int] = {}
    for index, item in enumerate(reader.tables(raw, "closures", "[[closures]]"), 1):
        where = "[[closures]] #%d" % index
        reader.check_keys(item, CLOSURE_KEYS, where)
        day = reader.local_date(item, "date", where)
        kind = reader.choice(item, "kind", where, CLOSURE_KINDS)
        source = reader.string(item, "source", where)
        if day.weekday() >= 5:
            raise reader.fail("%s %s is a %s: the NYSE holds no session to close" % (where, day.isoformat(), day.strftime("%A")))
        if day in seen:
            raise reader.fail("%s lists %s twice (first at #%d)" % (where, day.isoformat(), seen[day]))
        seen[day] = index
        closures.append(Closure(day, kind, source))
    return tuple(closures)


def _closure_warnings(closures: Tuple[Closure, ...]) -> Tuple[str, ...]:
    warnings: List[str] = []
    for closure in closures:
        if closure.kind != KIND_EARLY_CLOSE:
            continue
        entry = market_clock.calendar_entry(closure.date)
        if entry is None or entry.type != market_clock.ENTRY_EARLY_CLOSE:
            warnings.append("closure %s early_close is not in the embedded NYSE table: market_clock cannot inject an "
                            "early close, so that day is treated as a regular session until the table is refreshed "
                            "(source: %s)" % (closure.date.isoformat(), closure.source))
    return tuple(warnings)


def _parse_document(document: Any, path: str, sha256: str) -> CalendarFile:
    reader = _Reader(path)
    table = reader.table(document, "the document")
    reader.check_keys(table, TOP_LEVEL_KEYS, "the top level")
    if "schema_version" not in table:
        raise reader.fail("missing key 'schema_version' at the top level (write schema_version = %d)" % SCHEMA_VERSION)
    version = table["schema_version"]
    if not _is_int(version) or version != SCHEMA_VERSION:
        raise reader.fail("schema_version must be %d (got %r)" % (SCHEMA_VERSION, version))
    symbols = _parse_symbols(reader, table["symbols"]) if "symbols" in table else {}
    roots, values = _parse_settlement(reader, table["settlement"]) if "settlement" in table else ((), ())
    closures = _parse_closures(reader, table["closures"]) if "closures" in table else ()
    return CalendarFile(path, SCHEMA_VERSION, symbols, roots, values, closures, sha256, _closure_warnings(closures))


def _toml_reason(exc: TomlError) -> str:
    position = ""
    if getattr(exc, "line", None) is not None:
        position = " (line %s, col %s)" % (exc.line, exc.col)
    return "invalid TOML: %s%s" % (getattr(exc, "message", str(exc)), position)


def parse_calendar_text(text: str, path: str = "<text>") -> CalendarFile:
    """Parse calendar TOML text (for tests and in-memory callers); ``path`` labels errors."""
    if not isinstance(text, str):
        raise CalendarError(path, "calendar text must be a str, got %s" % type(text).__name__)
    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        document = toml_compat.loads(text)
    except TomlError as exc:
        raise CalendarError(path, _toml_reason(exc)) from None
    return _parse_document(document, path, sha256)


def load_calendar(path: str) -> CalendarFile:
    """Read and parse ``research/calendar.toml``; raises :class:`CalendarError` for anything
    that stops the file from being used (unreadable, not UTF-8, invalid TOML, a rule above)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise CalendarError(str(path), "cannot read calendar.toml: %s" % (exc.strerror or exc)) from None
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CalendarError(str(path), "calendar.toml is not valid UTF-8: %s" % exc) from None
    try:
        document = toml_compat.loads(text)
    except TomlError as exc:
        raise CalendarError(str(path), _toml_reason(exc)) from None
    return _parse_document(document, str(path), sha256)


def load_calendar_if_present(path: str) -> Optional[CalendarFile]:
    """:func:`load_calendar`, or ``None`` when no file exists at ``path`` (a tool then treats
    every event as unknown); a file that exists but cannot be used still raises."""
    if not os.path.exists(path):
        return None
    return load_calendar(path)


# --------------------------------------------------------------------------------------------
# Module-level lookups (``cal`` may be ``None``: no calendar file means everything is unknown)
# --------------------------------------------------------------------------------------------


def events_for(cal: Optional[CalendarFile], symbol: str) -> SymbolEvents:
    if cal is None:
        return SymbolEvents(_normalise_symbol(symbol), False)
    return cal.events_for(symbol)


def dividend_yield(cal: Optional[CalendarFile], symbol: str) -> Optional[Decimal]:
    return events_for(cal, symbol).dividend_yield


def dividend_before(cal: Optional[CalendarFile], symbol: str, expiry: DateLike, as_of: DateLike) -> DividendView:
    if cal is None:
        _as_date(expiry, "expiry")
        _as_date(as_of, "as_of")
        _normalise_symbol(symbol)
        return DividendView(None, None, STATUS_UNKNOWN)
    return cal.dividend_before(symbol, expiry, as_of)


def is_am_settled(cal: Optional[CalendarFile], root: str) -> bool:
    """Without a calendar every root is PM-settled (the brief's default); a wrong default for an
    index root is why the fixture calendars list ``am_settled_roots``."""
    if cal is None:
        _normalise_symbol(root)
        return False
    return cal.is_am_settled(root)


def settlement_value(cal: Optional[CalendarFile], root: str, day: datetime.date) -> Optional[Decimal]:
    if cal is None:
        _normalise_symbol(root)
        return None
    return cal.settlement_value(root, day)


def extra_closures(cal: Optional[CalendarFile]) -> Dict[datetime.date, str]:
    return {} if cal is None else cal.extra_closures()


def early_closes(cal: Optional[CalendarFile]) -> Dict[datetime.date, str]:
    return {} if cal is None else cal.early_closes()


# --------------------------------------------------------------------------------------------
# Freshness lint
# --------------------------------------------------------------------------------------------


def _age_text(age: datetime.timedelta) -> str:
    days = age.days
    return "%d day%s" % (days, "" if days == 1 else "s")


def freshness(cal: Optional[CalendarFile], now: datetime.datetime, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
              held_symbols: Iterable[str] = (), short_call_symbols: Optional[Iterable[str]] = None) -> List[Finding]:
    """The calendar's findings for a run at ``now`` (module notes), sorted by symbol.

    ``held_symbols`` are the underlyings the book holds (``MISSING`` when absent from the file);
    ``short_call_symbols`` narrows ``UNKNOWN_DIVIDEND`` to the held symbols carrying a short
    call, and defaults to every held symbol (the conservative reading: unknown is never zero).
    ``cal`` may be ``None`` (no file): every held symbol is then ``MISSING``.
    """
    now = _require_utc(now)
    if not _is_int(max_age_days) or max_age_days < 0:
        raise ValueError("max_age_days must be a non-negative integer, got %r" % (max_age_days,))
    held = _symbol_list(held_symbols, "held_symbols")
    scope = set(held) if short_call_symbols is None else set(_symbol_list(short_call_symbols, "short_call_symbols"))
    today = market_clock.to_et(now).date()
    max_age = datetime.timedelta(days=max_age_days)
    listed: Dict[str, SymbolEvents] = cal.symbols if cal is not None else {}
    findings: List[Finding] = []
    for symbol in sorted(set(listed) | set(held)):
        events = listed.get(symbol)
        if events is None:
            tail = "; a short call's dividend risk cannot be graded" if symbol in scope else ""
            findings.append(Finding(FINDING_MISSING, symbol,
                                    "%s is held but absent from calendar.toml: earnings and dividend unknown, never zero%s"
                                    % (symbol, tail)))
            continue
        for name, entry in (("earnings", events.earnings), ("ex_dividend", events.ex_dividend)):
            if entry is None:
                continue
            if entry.date is not None and entry.date < today:
                findings.append(Finding(FINDING_PAST, symbol, "%s %s %s (%s) has passed: update or remove the entry"
                                        % (symbol, name, entry.date.isoformat(), entry.status)))
                continue
            age = now - entry.checked_utc
            if age > max_age:
                findings.append(Finding(FINDING_STALE, symbol, "%s %s checked %s is %s old (limit %s)"
                                        % (symbol, name, entry.checked_utc.isoformat(), _age_text(age), _age_text(max_age))))
        if symbol in held and symbol in scope:
            dividend = events.ex_dividend
            if dividend is None or dividend.status == STATUS_UNKNOWN:
                detail = "no ex_dividend entry" if dividend is None else "ex_dividend status \"unknown\""
                findings.append(Finding(FINDING_UNKNOWN_DIVIDEND, symbol,
                                        "%s: dividend unknown (%s): a short call's assignment risk cannot be graded; "
                                        "unknown is never zero" % (symbol, detail)))
    return findings


__all__ = [
    "CLOSURE_KEYS",
    "CLOSURE_KINDS",
    "CLOSURE_LABEL",
    "CalendarError",
    "CalendarFile",
    "Closure",
    "DATED_STATUSES",
    "DEFAULT_MAX_AGE_DAYS",
    "DividendEntry",
    "DividendView",
    "EARNINGS_KEYS",
    "EX_DIVIDEND_KEYS",
    "EarningsEntry",
    "FINDING_KINDS",
    "FINDING_MISSING",
    "FINDING_PAST",
    "FINDING_STALE",
    "FINDING_UNKNOWN_DIVIDEND",
    "Finding",
    "KIND_EARLY_CLOSE",
    "KIND_FULL",
    "SCHEMA_VERSION",
    "SETTLEMENT_KEYS",
    "SETTLEMENT_VALUE_KEYS",
    "STATUSES",
    "STATUS_CONFIRMED",
    "STATUS_ESTIMATED",
    "STATUS_NONE",
    "STATUS_UNKNOWN",
    "SYMBOL_KEYS",
    "SYMBOL_RE",
    "SettlementValue",
    "SymbolEvents",
    "TIMES",
    "TIME_POST",
    "TIME_PRE",
    "TIME_UNKNOWN",
    "TOP_LEVEL_KEYS",
    "dividend_before",
    "dividend_yield",
    "early_closes",
    "events_for",
    "extra_closures",
    "freshness",
    "is_am_settled",
    "load_calendar",
    "load_calendar_if_present",
    "parse_calendar_text",
    "settlement_value",
]
