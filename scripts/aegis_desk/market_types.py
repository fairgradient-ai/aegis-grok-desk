"""Plain market dataclasses shared by the market-data client and every consumer of its data.

New for Aegis Desk (plan sections 8 and 9); standard library only, no network, no I/O. The
network client (``aegis_desk.alpaca_client``) turns each Alpaca answer into the desk's own
normalised RECORD (a JSON-native dict whose keys are the field names below) and builds these
records with the ``from_record`` constructors; ``quote_cache`` stores ``Quote.raw`` / ``Bar.raw``
(the record) and rebuilds with the same constructors, so a reload and a fetch go through one
code path and no consumer ever sees a vendor shape.

Conventions (plan section 8, brief non-negotiable 4)
----------------------------------------------------
* **Money is ``Decimal`` built from the JSON text.** :func:`to_decimal` turns a JSON number into
  ``Decimal(str(value))`` (the shortest round-trip text of the float ``json.loads`` produced,
  e.g. ``104.2`` -> ``Decimal("104.2")``, numerically equal to the vendor's ``104.20``) and a
  string into ``Decimal(value)``. A client that parses with ``json.loads(..., parse_float=Decimal)``
  keeps the vendor's digits; both paths compare equal and no float arithmetic ever happens.
  ``Quote.greeks`` is kept only as an optional dict for records that carry one; the client never
  fills it (vendor Greeks and implied volatility are ignored: the desk computes its own).
* **Timestamps are aware UTC datetimes.** A record stamps quotes in epoch MILLISECONDS
  (``trade_date``, ``bid_date``, ``ask_date``): :func:`from_epoch_ms` converts with
  ``datetime.fromtimestamp(ms / 1000, tz=timezone.utc)``; the clock record stamps in epoch SECONDS
  (:func:`from_epoch_s`). Alpaca sends RFC 3339 text with up to nanoseconds
  (``2026-11-03T19:31:12.123456789Z``): :func:`from_rfc3339` reads it (fraction truncated to the
  microsecond) and :func:`to_epoch_ms` writes the record's integer milliseconds. A vendor ``0`` (or
  a negative, or an instant at or before the epoch) is not a date, it is "undated": the
  converters return ``None`` so freshness grading reports the side as undated instead of judging
  a 1970 instant stale.
* **Missing or null fields are ``None``, never 0.** ``from_record`` constructors leave absent
  and ``null`` fields ``None``; a field that IS present but cannot be read (a price of ``"abc"``,
  a boolean where a number belongs, ``NaN``) raises :class:`MarketTypesError` naming the field,
  because a quiet ``None`` for a present-but-garbled value would be a silent negative (brief
  non-negotiable 5). Callers decide how to report it (the client maps it to an explicit status).

Marks (plan section 8)
----------------------
``Quote.mid`` is ``(bid + ask) / 2`` when both sides are present and at least one is positive,
else ``None`` (a 0/0 market has no mid). ``Quote.as_of`` is ``min(bid_date, ask_date)`` when
both exist, else whichever exists, else ``None``: tau and freshness are measured from the quote's
as-of, never from ``trade_date`` (the last trade is display only and never a mark). An Alpaca
quote carries one stamp ``t`` for both sides, so its record sets ``bid_date`` and ``ask_date``
to the same instant.
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

QUOTE_TYPES = ("stock", "option")
"""Values of ``Quote.type`` the client writes: ``stock`` for an equity or ETF quoted on the stock feed,
``option`` for a listed option (a record's other labels are kept as sent, never rejected)."""

OPTION_TYPES = ("call", "put")
"""Values of ``Quote.option_type``; anything else present is a :class:`MarketTypesError`."""

CALENDAR_STATUSES = ("open", "closed")
"""Values of ``CalendarDay.status``."""

_HHMM_RE = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2}))?$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RFC3339_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(?:([Zz])|([+-])(\d{2}):(\d{2}))$")
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


class MarketTypesError(ValueError):
    """A vendor field that is present but cannot be read; ``field`` names it."""

    def __init__(self, field_name: str, message: str) -> None:
        self.field = field_name
        self.message = message
        super().__init__("%s: %s" % (field_name, message))


# --------------------------------------------------------------------------------------------
# Scalar helpers
# --------------------------------------------------------------------------------------------


def to_decimal(value: Any, field_name: str = "value") -> Optional[Decimal]:
    """``Decimal`` from a JSON number or numeric string; ``None`` for ``None`` and blank strings.

    ``Decimal(str(v))`` for ``int``/``float`` (never binary-float arithmetic), ``Decimal(v)`` for
    ``str``, a ``Decimal`` passes through. Booleans, non-finite values (``NaN``, ``Infinity``) and
    unparsable text raise :class:`MarketTypesError`.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise MarketTypesError(field_name, "boolean %r is not a number" % (value,))
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise MarketTypesError(field_name, "non-finite number %r" % (value,))
        result = Decimal(str(value))
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            result = Decimal(text)
        except InvalidOperation:
            raise MarketTypesError(field_name, "%r is not a decimal number" % (value,)) from None
    else:
        raise MarketTypesError(field_name, "unsupported type %s for a number" % type(value).__name__)
    if not result.is_finite():
        raise MarketTypesError(field_name, "non-finite number %r" % (value,))
    return result


def to_int(value: Any, field_name: str = "value") -> Optional[int]:
    """``int`` from a JSON integer, an integral float/Decimal or a digit string; ``None`` for
    ``None`` and blank strings; anything fractional, boolean or unparsable raises."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise MarketTypesError(field_name, "boolean %r is not an integer" % (value,))
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value != math.floor(value):
            raise MarketTypesError(field_name, "%r is not an integer" % (value,))
        return int(value)
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise MarketTypesError(field_name, "%s is not an integer" % (value,))
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            raise MarketTypesError(field_name, "%r is not an integer" % (value,)) from None
    raise MarketTypesError(field_name, "unsupported type %s for an integer" % type(value).__name__)


def to_str(value: Any, field_name: str = "value") -> Optional[str]:
    """A string as sent (``None`` stays ``None``); numeric codes are rendered; booleans raise."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise MarketTypesError(field_name, "boolean %r is not text" % (value,))
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    raise MarketTypesError(field_name, "unsupported type %s for text" % type(value).__name__)


def _epoch_number(value: Any, field_name: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise MarketTypesError(field_name, "boolean %r is not an epoch stamp" % (value,))
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            raise MarketTypesError(field_name, "%r is not an epoch stamp" % (value,)) from None
    else:
        raise MarketTypesError(field_name, "unsupported type %s for an epoch stamp" % type(value).__name__)
    if not math.isfinite(number):
        raise MarketTypesError(field_name, "non-finite epoch stamp %r" % (value,))
    if number <= 0:
        return None  # the vendor's 0 for "no stamp": undated, never 1970
    return number


def _from_timestamp(seconds: float, field_name: str, value: Any) -> datetime.datetime:
    try:
        return datetime.datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise MarketTypesError(field_name, "epoch stamp %r is out of range" % (value,)) from None


def from_epoch_ms(value: Any, field_name: str = "value") -> Optional[datetime.datetime]:
    """Aware UTC datetime from epoch milliseconds (``fromtimestamp(ms / 1000, tz=UTC)``);
    ``None`` for ``None``, blank, zero and negative stamps."""
    number = _epoch_number(value, field_name)
    if number is None:
        return None
    return _from_timestamp(number / 1000, field_name, value)


def from_epoch_s(value: Any, field_name: str = "value") -> Optional[datetime.datetime]:
    """Aware UTC datetime from epoch seconds (the clock record's stamp, a rate-limit reset); same ``None`` rule."""
    number = _epoch_number(value, field_name)
    if number is None:
        return None
    return _from_timestamp(number, field_name, value)


def from_rfc3339(value: Any, field_name: str = "value") -> Optional[datetime.datetime]:
    """Aware UTC datetime from RFC 3339 text (``2026-11-03T19:31:12.123456789Z`` or an offset such
    as ``-05:00``); the fraction is truncated to the microsecond. ``None`` for ``None``, blank
    text and an instant at or before the Unix epoch (Alpaca's zero time ``0001-01-01T00:00:00Z``
    means "no stamp": undated, never year 1). Anything else that is not RFC 3339 raises."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise MarketTypesError(field_name, "unsupported type %s for an RFC 3339 time" % type(value).__name__)
    text = value.strip()
    if not text:
        return None
    match = _RFC3339_RE.match(text)
    if not match:
        raise MarketTypesError(field_name, "%r is not an RFC 3339 time" % (value,))
    year, month, day, hour, minute, second = (int(match.group(index)) for index in range(1, 7))
    fraction = (match.group(7) or "")[:6].ljust(6, "0")
    if match.group(8):
        offset = datetime.timedelta(0)
    else:
        sign = -1 if match.group(9) == "-" else 1
        hours, minutes = int(match.group(10)), int(match.group(11))
        if hours > 23 or minutes > 59:
            raise MarketTypesError(field_name, "%r carries an invalid UTC offset" % (value,))
        offset = sign * datetime.timedelta(hours=hours, minutes=minutes)
    try:
        local = datetime.datetime(year, month, day, hour, minute, second, int(fraction),
                                  tzinfo=datetime.timezone(offset))
        instant = local.astimezone(UTC)
    except (ValueError, OverflowError):
        raise MarketTypesError(field_name, "%r is not a valid time" % (value,)) from None
    if instant <= _EPOCH:
        return None
    return instant


def to_epoch_ms(instant: datetime.datetime) -> int:
    """Integer epoch milliseconds of an aware datetime (exact integer arithmetic, sub-millisecond
    part truncated): what a record's ``bid_date``/``ask_date``/``trade_date`` holds."""
    if not isinstance(instant, datetime.datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("to_epoch_ms needs an aware datetime, got %r" % (instant,))
    return (instant - _EPOCH) // datetime.timedelta(milliseconds=1)


def parse_iso_date(value: Any, field_name: str = "value") -> Optional[datetime.date]:
    """``datetime.date`` from ``YYYY-MM-DD`` (a ``date`` passes through); ``None`` for ``None``/blank."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        raise MarketTypesError(field_name, "expected a date, got a datetime %r" % (value,))
    if isinstance(value, datetime.date):
        return value
    if not isinstance(value, str):
        raise MarketTypesError(field_name, "unsupported type %s for a date" % type(value).__name__)
    text = value.strip()
    if not text:
        return None
    if not _ISO_DATE_RE.match(text):
        raise MarketTypesError(field_name, "%r is not a YYYY-MM-DD date" % (value,))
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise MarketTypesError(field_name, "%r is not a valid date" % (value,)) from None


def parse_hhmm(value: Any, field_name: str = "value") -> Optional[datetime.time]:
    """``datetime.time`` from the calendar's ``HH:MM`` (or ``HH:MM:SS``); ``None`` for ``None``/blank."""
    if value is None:
        return None
    if isinstance(value, datetime.time):
        return value
    if not isinstance(value, str):
        raise MarketTypesError(field_name, "unsupported type %s for a clock time" % type(value).__name__)
    text = value.strip()
    if not text:
        return None
    match = _HHMM_RE.match(text)
    if not match:
        raise MarketTypesError(field_name, "%r is not an HH:MM clock time" % (value,))
    try:
        return datetime.time(int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    except ValueError:
        raise MarketTypesError(field_name, "%r is not a valid clock time" % (value,)) from None


def _object(raw: Any, what: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise MarketTypesError(what, "expected a JSON object, got %s" % type(raw).__name__)
    return raw


# --------------------------------------------------------------------------------------------
# Quote and Bar
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    """One quote or chain entry, built from the desk's normalised record (module notes).

    Prices are ``Optional[Decimal]``, sizes and volumes ``Optional[int]``, the three stamps
    aware UTC datetimes, ``expiration_date`` a ``date``. Options carry ``underlying``,
    ``strike``, ``open_interest``, ``contract_size``, ``expiration_date``, ``option_type`` and
    ``root_symbol`` (from the OCC symbol and the vendor's contract listing); fields the vendor does
    not publish stay ``None`` (``description``, ``exch``, ``change``, ``prevclose``, the 52-week
    range, ``root_symbols``, ``expiration_type``, ``greeks`` ...). ``raw`` is the record as built
    (what ``quote_cache`` stores). Only ``symbol`` is required, so tests and the cache can build a
    quote from a few keyword arguments.
    """

    symbol: str
    description: Optional[str] = None
    exch: Optional[str] = None
    type: Optional[str] = None
    last: Optional[Decimal] = None
    change: Optional[Decimal] = None
    volume: Optional[int] = None
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    change_percentage: Optional[Decimal] = None
    average_volume: Optional[int] = None
    last_volume: Optional[int] = None
    trade_date: Optional[datetime.datetime] = None
    prevclose: Optional[Decimal] = None
    week_52_high: Optional[Decimal] = None
    week_52_low: Optional[Decimal] = None
    bidsize: Optional[int] = None
    bidexch: Optional[str] = None
    bid_date: Optional[datetime.datetime] = None
    asksize: Optional[int] = None
    askexch: Optional[str] = None
    ask_date: Optional[datetime.datetime] = None
    root_symbols: Optional[str] = None
    underlying: Optional[str] = None
    strike: Optional[Decimal] = None
    open_interest: Optional[int] = None
    contract_size: Optional[int] = None
    expiration_date: Optional[datetime.date] = None
    expiration_type: Optional[str] = None
    option_type: Optional[str] = None
    root_symbol: Optional[str] = None
    greeks: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, raw: Dict[str, Any]) -> "Quote":
        """Build from a normalised record (module notes: Decimal from text, epoch ms, None for missing)."""
        raw = _object(raw, "quote")
        symbol = to_str(raw.get("symbol"), "symbol")
        if symbol is None or not symbol.strip():
            raise MarketTypesError("symbol", "missing or blank")
        greeks = raw.get("greeks")
        if greeks is not None and not isinstance(greeks, dict):
            raise MarketTypesError("greeks", "expected an object or null, got %s" % type(greeks).__name__)
        option_type = to_str(raw.get("option_type"), "option_type")
        if option_type is not None and option_type not in OPTION_TYPES:
            raise MarketTypesError("option_type", "%r is not one of %s" % (option_type, ", ".join(OPTION_TYPES)))
        return cls(
            symbol=symbol,
            description=to_str(raw.get("description"), "description"),
            exch=to_str(raw.get("exch"), "exch"),
            type=to_str(raw.get("type"), "type"),
            last=to_decimal(raw.get("last"), "last"),
            change=to_decimal(raw.get("change"), "change"),
            volume=to_int(raw.get("volume"), "volume"),
            open=to_decimal(raw.get("open"), "open"),
            high=to_decimal(raw.get("high"), "high"),
            low=to_decimal(raw.get("low"), "low"),
            close=to_decimal(raw.get("close"), "close"),
            bid=to_decimal(raw.get("bid"), "bid"),
            ask=to_decimal(raw.get("ask"), "ask"),
            change_percentage=to_decimal(raw.get("change_percentage"), "change_percentage"),
            average_volume=to_int(raw.get("average_volume"), "average_volume"),
            last_volume=to_int(raw.get("last_volume"), "last_volume"),
            trade_date=from_epoch_ms(raw.get("trade_date"), "trade_date"),
            prevclose=to_decimal(raw.get("prevclose"), "prevclose"),
            week_52_high=to_decimal(raw.get("week_52_high"), "week_52_high"),
            week_52_low=to_decimal(raw.get("week_52_low"), "week_52_low"),
            bidsize=to_int(raw.get("bidsize"), "bidsize"),
            bidexch=to_str(raw.get("bidexch"), "bidexch"),
            bid_date=from_epoch_ms(raw.get("bid_date"), "bid_date"),
            asksize=to_int(raw.get("asksize"), "asksize"),
            askexch=to_str(raw.get("askexch"), "askexch"),
            ask_date=from_epoch_ms(raw.get("ask_date"), "ask_date"),
            root_symbols=to_str(raw.get("root_symbols"), "root_symbols"),
            underlying=to_str(raw.get("underlying"), "underlying"),
            strike=to_decimal(raw.get("strike"), "strike"),
            open_interest=to_int(raw.get("open_interest"), "open_interest"),
            contract_size=to_int(raw.get("contract_size"), "contract_size"),
            expiration_date=parse_iso_date(raw.get("expiration_date"), "expiration_date"),
            expiration_type=to_str(raw.get("expiration_type"), "expiration_type"),
            option_type=option_type,
            root_symbol=to_str(raw.get("root_symbol"), "root_symbol"),
            greeks=greeks,
            raw=raw,
        )

    @property
    def is_option(self) -> bool:
        """True for a listed option (``type == "option"``)."""
        return self.type == "option"

    @property
    def mid(self) -> Optional[Decimal]:
        """``(bid + ask) / 2`` when both sides are present and ``bid > 0 or ask > 0``, else ``None``."""
        if self.bid is None or self.ask is None:
            return None
        if not (self.bid > 0 or self.ask > 0):
            return None
        return (self.bid + self.ask) / 2

    @property
    def as_of(self) -> Optional[datetime.datetime]:
        """The quote's as-of: ``min(bid_date, ask_date)``, or the one side that is dated, or ``None``."""
        if self.bid_date is not None and self.ask_date is not None:
            return min(self.bid_date, self.ask_date)
        return self.bid_date if self.bid_date is not None else self.ask_date


@dataclass(frozen=True)
class Bar:
    """One daily bar: ``date`` (the bar's America/New_York trading date) plus Decimal OHLC and an integer volume."""

    date: datetime.date
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    volume: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, raw: Dict[str, Any]) -> "Bar":
        raw = _object(raw, "day")
        day = parse_iso_date(raw.get("date"), "date")
        if day is None:
            raise MarketTypesError("date", "missing")
        return cls(
            date=day,
            open=to_decimal(raw.get("open"), "open"),
            high=to_decimal(raw.get("high"), "high"),
            low=to_decimal(raw.get("low"), "low"),
            close=to_decimal(raw.get("close"), "close"),
            volume=to_int(raw.get("volume"), "volume"),
            raw=raw,
        )


# --------------------------------------------------------------------------------------------
# Calendar and clock
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionWindow:
    """A ``{"start": "HH:MM", "end": "HH:MM"}`` session window of the calendar (naive ET times)."""

    start: datetime.time
    end: datetime.time

    @classmethod
    def from_record(cls, raw: Any, field_name: str = "session") -> Optional["SessionWindow"]:
        """``None`` for a missing or empty window; both bounds are required once one is given."""
        if raw is None:
            return None
        raw = _object(raw, field_name)
        if not raw:
            return None
        start = parse_hhmm(raw.get("start"), field_name + ".start")
        end = parse_hhmm(raw.get("end"), field_name + ".end")
        if start is None or end is None:
            raise MarketTypesError(field_name, "a session window needs both start and end")
        return cls(start, end)


@dataclass(frozen=True)
class CalendarDay:
    """One calendar day: ``status`` is ``open`` or ``closed``; the three session windows (naive ET
    ``HH:MM``) are ``None`` on a closed day."""

    date: datetime.date
    status: str
    description: Optional[str] = None
    premarket: Optional[SessionWindow] = None
    open: Optional[SessionWindow] = None
    postmarket: Optional[SessionWindow] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, raw: Dict[str, Any]) -> "CalendarDay":
        raw = _object(raw, "day")
        day = parse_iso_date(raw.get("date"), "date")
        if day is None:
            raise MarketTypesError("date", "missing")
        status = to_str(raw.get("status"), "status")
        if status not in CALENDAR_STATUSES:
            raise MarketTypesError("status", "%r is not one of %s" % (status, ", ".join(CALENDAR_STATUSES)))
        return cls(
            date=day,
            status=status,
            description=to_str(raw.get("description"), "description"),
            premarket=SessionWindow.from_record(raw.get("premarket"), "premarket"),
            open=SessionWindow.from_record(raw.get("open"), "open"),
            postmarket=SessionWindow.from_record(raw.get("postmarket"), "postmarket"),
            raw=raw,
        )


@dataclass(frozen=True)
class ClockInfo:
    """The market clock: ``date`` (ET), ``state`` (``open`` | ``closed``), ``timestamp`` epoch SECONDS
    in the record, and the next state change as ET ``HH:MM`` with the state it leads to."""

    date: datetime.date
    state: str
    description: Optional[str] = None
    timestamp_utc: Optional[datetime.datetime] = None
    next_change: Optional[str] = None
    next_state: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, raw: Dict[str, Any]) -> "ClockInfo":
        """Build from the clock record (``date``, ``state``, optional ``description``, ``timestamp``
        in epoch seconds, ``next_change``, ``next_state``)."""
        inner = _object(raw, "clock")
        day = parse_iso_date(inner.get("date"), "date")
        if day is None:
            raise MarketTypesError("date", "missing")
        state = to_str(inner.get("state"), "state")
        if state is None or not state.strip():
            raise MarketTypesError("state", "missing")
        return cls(
            date=day,
            state=state,
            description=to_str(inner.get("description"), "description"),
            timestamp_utc=from_epoch_s(inner.get("timestamp"), "timestamp"),
            next_change=to_str(inner.get("next_change"), "next_change"),
            next_state=to_str(inner.get("next_state"), "next_state"),
            raw=inner,
        )


# --------------------------------------------------------------------------------------------
# Request receipt
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestReceipt:
    """What the client records for every attempt, success or failure (the cache's ledger hook).

    ``status`` is ``"ok"`` or the client's error status; the ``ratelimit_*`` fields come from
    vendor's ``X-RateLimit-Limit`` / ``-Remaining`` / ``-Reset`` headers when present
    (``ratelimit_allowed`` the limit, ``ratelimit_available`` the remaining count,
    ``ratelimit_used`` their difference, ``ratelimit_expiry_utc`` the reset instant).
    """

    path: str
    symbols_count: int
    receipt_utc: datetime.datetime
    status: str
    http_status: Optional[int] = None
    ratelimit_allowed: Optional[int] = None
    ratelimit_used: Optional[int] = None
    ratelimit_available: Optional[int] = None
    ratelimit_expiry_utc: Optional[datetime.datetime] = None
    elapsed_ms: Optional[int] = None


__all__ = [
    "Bar",
    "CALENDAR_STATUSES",
    "CalendarDay",
    "ClockInfo",
    "MarketTypesError",
    "OPTION_TYPES",
    "QUOTE_TYPES",
    "Quote",
    "RequestReceipt",
    "SessionWindow",
    "UTC",
    "from_epoch_ms",
    "from_epoch_s",
    "from_rfc3339",
    "parse_hhmm",
    "parse_iso_date",
    "to_decimal",
    "to_int",
    "to_str",
    "to_epoch_ms",
]
