"""NYSE market clock: America/New_York time, holidays, sessions and expiry arithmetic (stdlib only).

New for Aegis Desk (plan section 9); nothing here is ported or adapted. The Grok Bot computer has
an undocumented Python without ``tzdata``, so this module carries its own America/New_York rule
and its own NYSE holiday tables and depends on nothing but the standard library. It is pure:
timezone-aware UTC datetimes in, timezone-aware UTC datetimes out, ``datetime.date`` for dates,
no I/O, no wall clock, no global mutable state. Consumers (``book.py``, ``quote_qc.py``,
``routine.py``, ``desk_doctor.py``) pass the instant they are reasoning about.

Two tiers of knowledge, stated so that nothing is silently wrong
----------------------------------------------------------------
1. **Explicit NYSE tables** (:data:`NYSE_HOLIDAYS_2026`, :data:`NYSE_HOLIDAYS_2027`), transcribed
   from :data:`SOURCE` and authoritative within :data:`TABLE_YEARS`. They expire on
   :data:`TABLE_EXPIRES`; :func:`table_status` reports ``ok`` / ``expiring_soon`` (within
   :data:`TABLE_WARN_DAYS` = 60 days, the doctor's FAIL window) / ``expired``. Classifying "now"
   (:func:`session_at`, :func:`previous_close_utc`) past ``TABLE_EXPIRES`` raises
   :class:`TableExpiredError`: the desk fails closed rather than assert an open market from an
   unverified schedule. Callers turn that into their UNAVAILABLE / exit-20 path.
2. **The rule generator** (:func:`generate_nyse_calendar`) encodes the NYSE conventions below and
   reproduces the explicit tables exactly (``tests/engine/test_market_clock.py``). Date
   arithmetic that must reach beyond the tables (a LEAPS leg's DTE, a 2025 ``opened`` date) uses
   the generator; :func:`calendar_coverage` says which tier answered (``"table"`` or ``"rule"``)
   so consumers can label far-dated numbers. Unscheduled closures (a day of mourning, a storm)
   cannot be generated: inject them through ``extra_holidays`` and reconcile against the
   vendor's clock with :func:`reconcile_vendor_state`, where a vendor "closed" beats a local
   "open" and the disagreement is returned for logging. Two known past unscheduled closures are
   kept as data (:data:`UNSCHEDULED_CLOSURES`) so historical trading-day counts stay right.

NYSE conventions applied (as published on :data:`SOURCE`; NYSE Rule 7.2 for observance)
----------------------------------------------------------------------------------------
* Full-day holidays: New Year's Day (Jan 1), Martin Luther King, Jr. Day (third Monday of
  January), Washington's Birthday / Presidents' Day (third Monday of February), Good Friday
  (Easter Sunday - 2 days, Easter by the anonymous Gregorian computus), Memorial Day (last
  Monday of May), Juneteenth National Independence Day (June 19; observed by the NYSE from 2022),
  Independence Day (July 4), Labor Day (first Monday of September), Thanksgiving Day (fourth
  Thursday of November), Christmas Day (Dec 25).
* Observance of a fixed-date holiday: Saturday -> the preceding Friday; Sunday -> the following
  Monday; the label gets the suffix ``" (observed)"``. Exception (Rule 7.2): when New Year's Day
  falls on a Saturday the preceding Friday (Dec 31) is NOT a holiday because it ends a yearly
  accounting period (the NYSE was open on Friday 2021-12-31). A Sunday Jan 1 is observed on
  Monday Jan 2.
* Early closes at 13:00 ET (13:15 ET for late-close products): the day after Thanksgiving
  (always a Friday); Christmas Eve (Dec 24) when Dec 24 is a weekday AND Dec 25 itself is the
  holiday (so Dec 25 on Tue-Fri); the day before Independence Day (July 3) under the same rule
  (July 4 on Tue-Fri). When the holiday is weekend-shifted no early close follows (2027 has a
  single early close, 2027-11-26). There is no early close on New Year's Eve.
* Regular session 09:30-16:00 ET. Late-close products (certain index and ETF options, 16:15 ET
  regular, 13:15 ET early) are the OWNER'S CONFIGURATION (``product="late_close"``), never
  inferred from a symbol here. An expiring late-close contract trades until 16:00 ET on its last
  trading day (Cboe rule), which is why :func:`exercise_cutoff_utc` always uses the equity close.

Time zone (embedded US rule, in force since 2007; years before 2007 raise ValueError)
-----------------------------------------------------------------------------------
Daylight time starts the second Sunday of March at 02:00 local standard time (07:00 UTC) and
ends the first Sunday of November at 02:00 local daylight time (06:00 UTC). ET is UTC-4 (EDT)
inside that window and UTC-5 (EST) outside it. :func:`to_et` returns a datetime with the fixed
offset :data:`EDT` or :data:`EST`. :func:`from_et` maps a naive wall-clock ET datetime to UTC
with PEP 495 ``fold=0`` semantics, identical to ``zoneinfo``: an ambiguous time in the repeated
01:00-01:59 hour is the first (EDT) occurrence; a non-existent time in the skipped 02:00-02:59
hour is resolved with the pre-transition (EST) offset. Neither hour is ever a trading hour.

Session vocabulary (:func:`session_at`): ``weekend`` (Saturday/Sunday in ET), ``holiday`` (a
full closure, scheduled or injected), ``pre`` (a trading day before 09:30 ET), ``open`` (09:30
inclusive to the product's close exclusive on a regular day), ``early_close`` (the same window
on an early-close day: the market IS trading and closes early), ``closed`` (a trading day at or
after the close). The close instant is exclusive: at 16:00:00 ET the session is ``closed``.

Expiry arithmetic. PM-settled contracts (listed equity and most ETF options) stop trading and
settle on the expiration date itself when it is a trading day, else on the preceding trading day
(the OCC moves the expiration; a legacy Saturday-dated symbol resolves to the preceding Friday or
earlier). AM-settled contracts (e.g. the monthly SPX) settle on the opening quotation of that
same adjusted date and stop trading one trading day earlier. :func:`dte` measures BOTH units to
the last trading day (plan section 8: "DTE on the America/New_York date to the last trading
day"), the last day the owner's plan can be executed in the market. :func:`exercise_cutoff_utc`
is the instant tau is measured to: the last trading day's close (16:00 ET, 13:00 ET on an
early-close day) or an earlier broker cutoff for PM settlement; 09:30 ET on the settlement
morning for AM settlement.

Input errors (programming errors) raise ``ValueError``: a naive datetime where an instant is
required, an aware datetime where a wall-clock time is required, an unknown ``product``,
``unit`` or ``type``, a non-trading day given to :func:`session_close_et`. Market states are
never exceptions: a holiday is a ``SessionInfo`` with ``session == "holiday"``.

NOTE (table verification, 2026-11-02): every 2026 and 2027 entry follows from the rules above
and matches the NYSE calendar as the author knows it; no entry is believed uncertain. The two
points worth re-reading on :data:`SOURCE` when the 2028 table is added are the ABSENT early
closes of 2027 (no 2027-07-02 because Independence Day is observed Monday 2027-07-05; no
2027-12-23 because Christmas is observed Friday 2027-12-24), both the Rule-7.2 shape the NYSE
applied in 2021.
"""

from __future__ import annotations

from collections.abc import Mapping as _MappingABC
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Dict, Iterable, Mapping, Optional, Tuple, Union

# --------------------------------------------------------------------------------------------
# Constants and vocabulary
# --------------------------------------------------------------------------------------------

SOURCE: str = "https://www.nyse.com/markets/hours-calendars"
"""Where the explicit holiday and early-close tables were transcribed from."""

TABLE_YEARS: Tuple[int, ...] = (2026, 2027)
"""Years covered by an explicit (authoritative) table; other years use the rule generator."""

TABLE_EXPIRES: date = date(2027, 12, 31)
"""Last date the explicit tables cover; classifying an instant past it raises TableExpiredError."""

TABLE_WARN_DAYS: int = 60
"""``table_status`` reports ``expiring_soon`` when TABLE_EXPIRES is this many days away or less."""

ENTRY_FULL: str = "full"
"""Calendar-entry type: the market is closed all day."""

ENTRY_EARLY_CLOSE: str = "early_close"
"""Calendar-entry type: the market trades and closes early (13:00 ET)."""

SESSION_PRE: str = "pre"
SESSION_OPEN: str = "open"
SESSION_CLOSED: str = "closed"
SESSION_HOLIDAY: str = "holiday"
SESSION_EARLY_CLOSE: str = "early_close"
SESSION_WEEKEND: str = "weekend"
SESSIONS: Tuple[str, ...] = (
    SESSION_PRE, SESSION_OPEN, SESSION_CLOSED, SESSION_HOLIDAY, SESSION_EARLY_CLOSE, SESSION_WEEKEND,
)
"""Every value ``SessionInfo.session`` can take."""

LIVE_SESSIONS: Tuple[str, ...] = (SESSION_OPEN, SESSION_EARLY_CLOSE)
"""Sessions in which the market is trading."""

PRODUCT_EQUITY: str = "equity"
"""Regular 16:00 ET close (13:00 ET early close): listed equity and ETF options."""

PRODUCT_LATE_CLOSE: str = "late_close"
"""16:15 ET close (13:15 ET early close): the owner's configured late-close products."""

PRODUCTS: Tuple[str, ...] = (PRODUCT_EQUITY, PRODUCT_LATE_CLOSE)

UNIT_CALENDAR: str = "calendar"
UNIT_TRADING: str = "trading"
DTE_UNITS: Tuple[str, ...] = (UNIT_CALENDAR, UNIT_TRADING)

STATUS_OK: str = "ok"
STATUS_EXPIRING_SOON: str = "expiring_soon"
STATUS_EXPIRED: str = "expired"

COVERAGE_TABLE: str = "table"
COVERAGE_RULE: str = "rule"

REGULAR_OPEN_ET: time = time(9, 30)
REGULAR_CLOSE_ET: time = time(16, 0)
EARLY_CLOSE_ET: time = time(13, 0)
LATE_CLOSE_ET: time = time(16, 15)
LATE_EARLY_CLOSE_ET: time = time(13, 15)
AM_SETTLEMENT_ET: time = REGULAR_OPEN_ET
"""AM-settled contracts settle on the opening quotation: tau runs to 09:30 ET."""

UTC = timezone.utc
EST = timezone(timedelta(hours=-5), "EST")
"""Fixed-offset Eastern Standard Time (UTC-5)."""
EDT = timezone(timedelta(hours=-4), "EDT")
"""Fixed-offset Eastern Daylight Time (UTC-4)."""

FIRST_DST_RULE_YEAR: int = 2007
"""The embedded US DST rule took effect in 2007; earlier instants are refused."""

JUNETEENTH_FIRST_YEAR: int = 2022
"""The NYSE first observed Juneteenth National Independence Day in 2022."""

OBSERVED_SUFFIX: str = " (observed)"
UNSCHEDULED_LABEL: str = "Unscheduled closure"
"""Label for an injected ``extra_holidays`` date that carries no name of its own."""

# Labels exactly as the tables and the generator print them (the NYSE's own wording).
NAME_NEW_YEAR = "New Year's Day"
NAME_MLK = "Martin Luther King, Jr. Day"
NAME_WASHINGTON = "Washington's Birthday"
NAME_GOOD_FRIDAY = "Good Friday"
NAME_MEMORIAL = "Memorial Day"
NAME_JUNETEENTH = "Juneteenth National Independence Day"
NAME_INDEPENDENCE = "Independence Day"
NAME_LABOR = "Labor Day"
NAME_THANKSGIVING = "Thanksgiving Day"
NAME_CHRISTMAS = "Christmas Day"
NAME_DAY_AFTER_THANKSGIVING = "Day after Thanksgiving"
NAME_CHRISTMAS_EVE = "Christmas Eve"
NAME_INDEPENDENCE_EVE = "Day before Independence Day"

_MONDAY, _THURSDAY, _SATURDAY, _SUNDAY = 0, 3, 5, 6


class MarketClockError(Exception):
    """Base class for market-clock failures that are not input errors."""


class TableExpiredError(MarketClockError):
    """Raised when "now" lies past :data:`TABLE_EXPIRES`: the clock fails closed."""


# --------------------------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarEntry:
    """One row of the NYSE holiday / early-close table.

    ``type`` is :data:`ENTRY_FULL` (closed all day, ``close_time_et`` is ``None``) or
    :data:`ENTRY_EARLY_CLOSE` (``close_time_et`` is 13:00 ET for equity products; late-close
    products add 15 minutes through :func:`session_close_et`).
    """

    date: date
    name: str
    type: str
    close_time_et: Optional[time]

    def __post_init__(self) -> None:
        if self.type not in (ENTRY_FULL, ENTRY_EARLY_CLOSE):
            raise ValueError(f"CalendarEntry.type must be 'full' or 'early_close', got {self.type!r}")
        if self.type == ENTRY_FULL and self.close_time_et is not None:
            raise ValueError(f"a full-closure entry has no close time ({self.date})")
        if self.type == ENTRY_EARLY_CLOSE and self.close_time_et is None:
            raise ValueError(f"an early-close entry needs close_time_et ({self.date})")


@dataclass(frozen=True)
class SessionInfo:
    """Where the market stands at ``as_of_utc`` for one product (see the module docstring).

    ``session`` is one of :data:`SESSIONS`. ``trading_day`` is True on any day with a session,
    including early-close days. ``open_utc``/``close_utc`` are the session bounds for the
    product on the ET date (``None`` on weekends and holidays); ``close_utc`` is still set after
    the close so freshness can be labelled "as of close HH:MM ET". ``minutes_to_close`` is the
    whole number of minutes until ``close_utc`` while the close lies ahead (``pre``, ``open``,
    ``early_close``) and ``None`` once it has passed or on a non-trading day. ``holiday_name``
    is the calendar label for the ET date, a full holiday OR an early-close day (or the injected
    closure's name); it is a label, never a closure test: use ``session`` / ``trading_day``.
    ``et_date`` is the America/New_York calendar date the instant falls on (the DTE date).
    ``table_status`` is :func:`table_status` at ``et_date``.
    """

    session: str
    trading_day: bool
    close_utc: Optional[datetime]
    minutes_to_close: Optional[int]
    holiday_name: Optional[str]
    as_of_utc: datetime
    et_date: date
    open_utc: Optional[datetime]
    product: str
    table_status: str

    @property
    def is_live(self) -> bool:
        """True while the market is trading (``open`` or ``early_close``)."""
        return self.session in LIVE_SESSIONS

    def as_dict(self) -> Dict[str, object]:
        """JSON-ready view (ISO-8601 strings for instants and dates)."""
        return {
            "session": self.session,
            "trading_day": self.trading_day,
            "close_utc": None if self.close_utc is None else self.close_utc.isoformat(),
            "minutes_to_close": self.minutes_to_close,
            "holiday_name": self.holiday_name,
            "as_of_utc": self.as_of_utc.isoformat(),
            "et_date": self.et_date.isoformat(),
            "open_utc": None if self.open_utc is None else self.open_utc.isoformat(),
            "product": self.product,
            "table_status": self.table_status,
        }


@dataclass(frozen=True)
class Reconciliation:
    """Outcome of :func:`reconcile_vendor_state`.

    ``effective_session`` is the session the desk should act on; ``vendor_session`` is the
    vendor's state mapped into this module's vocabulary (``None`` when the vendor state was
    unrecognised or dated another ET day); ``disagreement`` is a one-line log message when the
    two sources differ (``None`` when they agree).
    """

    effective_session: str
    local_session: str
    vendor_state: Optional[str]
    vendor_session: Optional[str]
    agree: bool
    disagreement: Optional[str]


# --------------------------------------------------------------------------------------------
# Time zone
# --------------------------------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th (1-based) ``weekday`` (Monday=0) of the month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` (Monday=0) of the month."""
    last = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


@lru_cache(maxsize=None)
def dst_window_utc(year: int) -> Tuple[datetime, datetime]:
    """The half-open UTC window ``[start, end)`` of US daylight time in ``year``: the second
    Sunday of March at 07:00Z (02:00 EST) and the first Sunday of November at 06:00Z (02:00 EDT).
    Public so a caller (or an oracle test) can enumerate the transition instants."""
    start_day = _nth_weekday(year, 3, _SUNDAY, 2)
    end_day = _nth_weekday(year, 11, _SUNDAY, 1)
    start = datetime(start_day.year, start_day.month, start_day.day, 7, 0, tzinfo=UTC)
    end = datetime(end_day.year, end_day.month, end_day.day, 6, 0, tzinfo=UTC)
    return start, end


def _require_instant(utc_dt: datetime, what: str = "utc_dt") -> datetime:
    """Validate an aware datetime and return it in UTC (any aware zone is accepted, naive is not)."""
    if not isinstance(utc_dt, datetime):
        raise ValueError(f"{what} must be a datetime, got {type(utc_dt).__name__}")
    if utc_dt.tzinfo is None or utc_dt.tzinfo.utcoffset(utc_dt) is None:
        raise ValueError(f"{what} must be timezone-aware (UTC); a naive datetime is ambiguous")
    utc_dt = utc_dt.astimezone(UTC)
    if utc_dt.year < FIRST_DST_RULE_YEAR:
        raise ValueError(f"the embedded US DST rule starts in {FIRST_DST_RULE_YEAR}; got {utc_dt.isoformat()}")
    return utc_dt


def is_dst(utc_dt: datetime) -> bool:
    """True when US Eastern daylight time is in force at the instant ``utc_dt``."""
    utc_dt = _require_instant(utc_dt)
    start, end = dst_window_utc(utc_dt.year)
    return start <= utc_dt < end


def et_offset(utc_dt: datetime) -> timedelta:
    """The America/New_York UTC offset at ``utc_dt``: -4 h in daylight time, -5 h otherwise."""
    return EDT.utcoffset(None) if is_dst(utc_dt) else EST.utcoffset(None)


def to_et(utc_dt: datetime) -> datetime:
    """``utc_dt`` expressed in Eastern Time with a fixed-offset tzinfo (:data:`EDT` or :data:`EST`)."""
    utc_dt = _require_instant(utc_dt)
    return utc_dt.astimezone(EDT if is_dst(utc_dt) else EST)


def from_et(naive_et_dt: datetime) -> datetime:
    """Map a naive wall-clock Eastern datetime to the aware UTC instant (PEP 495 ``fold=0``).

    An ambiguous wall time (01:00-01:59 on the fall-back Sunday) is the first occurrence (EDT);
    a skipped wall time (02:00-02:59 on the spring-forward Sunday) is resolved with the EST
    offset, exactly as ``zoneinfo`` does. An aware input raises ValueError: pass
    ``to_et(x).replace(tzinfo=None)`` if you really hold an aware ET value.
    """
    if not isinstance(naive_et_dt, datetime):
        raise ValueError(f"naive_et_dt must be a datetime, got {type(naive_et_dt).__name__}")
    if naive_et_dt.tzinfo is not None:
        raise ValueError("from_et takes a NAIVE wall-clock ET datetime; got an aware datetime")
    if naive_et_dt.year < FIRST_DST_RULE_YEAR:
        raise ValueError(f"the embedded US DST rule starts in {FIRST_DST_RULE_YEAR}; got {naive_et_dt.isoformat()}")
    dst_candidate = (naive_et_dt - EDT.utcoffset(None)).replace(tzinfo=UTC)
    std_candidate = (naive_et_dt - EST.utcoffset(None)).replace(tzinfo=UTC)
    if is_dst(dst_candidate):
        return dst_candidate
    # Either a plain standard-time instant, or a skipped wall time (02:00-02:59 on the
    # spring-forward day) that fold=0 resolves with the pre-transition standard offset.
    return std_candidate


def _as_date(value: Union[date, datetime], what: str = "date") -> date:
    """A ``date`` as given, or the America/New_York date of an aware datetime."""
    if isinstance(value, datetime):
        return to_et(_require_instant(value, what)).date()
    if isinstance(value, date):
        return value
    raise ValueError(f"{what} must be a datetime.date or an aware datetime, got {type(value).__name__}")


# --------------------------------------------------------------------------------------------
# Holiday tables (authoritative) and the rule generator
# --------------------------------------------------------------------------------------------

NYSE_HOLIDAYS_2026: Tuple[CalendarEntry, ...] = (
    CalendarEntry(date(2026, 1, 1), NAME_NEW_YEAR, ENTRY_FULL, None),
    CalendarEntry(date(2026, 1, 19), NAME_MLK, ENTRY_FULL, None),
    CalendarEntry(date(2026, 2, 16), NAME_WASHINGTON, ENTRY_FULL, None),
    CalendarEntry(date(2026, 4, 3), NAME_GOOD_FRIDAY, ENTRY_FULL, None),
    CalendarEntry(date(2026, 5, 25), NAME_MEMORIAL, ENTRY_FULL, None),
    CalendarEntry(date(2026, 6, 19), NAME_JUNETEENTH, ENTRY_FULL, None),
    CalendarEntry(date(2026, 7, 3), NAME_INDEPENDENCE + OBSERVED_SUFFIX, ENTRY_FULL, None),
    CalendarEntry(date(2026, 9, 7), NAME_LABOR, ENTRY_FULL, None),
    CalendarEntry(date(2026, 11, 26), NAME_THANKSGIVING, ENTRY_FULL, None),
    CalendarEntry(date(2026, 11, 27), NAME_DAY_AFTER_THANKSGIVING, ENTRY_EARLY_CLOSE, EARLY_CLOSE_ET),
    CalendarEntry(date(2026, 12, 24), NAME_CHRISTMAS_EVE, ENTRY_EARLY_CLOSE, EARLY_CLOSE_ET),
    CalendarEntry(date(2026, 12, 25), NAME_CHRISTMAS, ENTRY_FULL, None),
)
"""NYSE 2026: ten full holidays (Independence Day observed Friday July 3) and two 13:00 ET closes."""

NYSE_HOLIDAYS_2027: Tuple[CalendarEntry, ...] = (
    CalendarEntry(date(2027, 1, 1), NAME_NEW_YEAR, ENTRY_FULL, None),
    CalendarEntry(date(2027, 1, 18), NAME_MLK, ENTRY_FULL, None),
    CalendarEntry(date(2027, 2, 15), NAME_WASHINGTON, ENTRY_FULL, None),
    CalendarEntry(date(2027, 3, 26), NAME_GOOD_FRIDAY, ENTRY_FULL, None),
    CalendarEntry(date(2027, 5, 31), NAME_MEMORIAL, ENTRY_FULL, None),
    CalendarEntry(date(2027, 6, 18), NAME_JUNETEENTH + OBSERVED_SUFFIX, ENTRY_FULL, None),
    CalendarEntry(date(2027, 7, 5), NAME_INDEPENDENCE + OBSERVED_SUFFIX, ENTRY_FULL, None),
    CalendarEntry(date(2027, 9, 6), NAME_LABOR, ENTRY_FULL, None),
    CalendarEntry(date(2027, 11, 25), NAME_THANKSGIVING, ENTRY_FULL, None),
    CalendarEntry(date(2027, 11, 26), NAME_DAY_AFTER_THANKSGIVING, ENTRY_EARLY_CLOSE, EARLY_CLOSE_ET),
    CalendarEntry(date(2027, 12, 24), NAME_CHRISTMAS + OBSERVED_SUFFIX, ENTRY_FULL, None),
)
"""NYSE 2027: ten full holidays (Juneteenth Fri June 18, Independence Day Mon July 5, Christmas Fri
Dec 24, all observed) and ONE 13:00 ET close (Nov 26): no Dec 23 or July 2 early close because the
adjacent holidays are weekend-shifted."""

EXPLICIT_TABLES: Dict[int, Tuple[CalendarEntry, ...]] = {
    2026: NYSE_HOLIDAYS_2026,
    2027: NYSE_HOLIDAYS_2027,
}
"""Year -> authoritative table. Keys equal :data:`TABLE_YEARS`."""

UNSCHEDULED_CLOSURES: Tuple[CalendarEntry, ...] = (
    CalendarEntry(date(2018, 12, 5), "National Day of Mourning (President George H. W. Bush)", ENTRY_FULL, None),
    CalendarEntry(date(2025, 1, 9), "National Day of Mourning (President Jimmy Carter)", ENTRY_FULL, None),
)
"""Past unscheduled full closures the rule generator cannot know (NYSE announcements). Merged into
rule-generated years so historical trading-day counts are right; future ones come via
``extra_holidays`` and the vendor cross-check."""


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday by the anonymous (Meeus/Jones/Butcher) computus algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - the algorithm's own letter
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def good_friday(year: int) -> date:
    """Good Friday: two days before Easter Sunday."""
    return easter_sunday(year) - timedelta(days=2)


def observed_date(holiday: date) -> date:
    """NYSE observance of a fixed-date holiday: Saturday -> Friday before, Sunday -> Monday after.

    New Year's Day is NOT handled here (Rule 7.2 exempts a Saturday Jan 1; see the module
    docstring and :func:`generate_nyse_calendar`).
    """
    if holiday.weekday() == _SATURDAY:
        return holiday - timedelta(days=1)
    if holiday.weekday() == _SUNDAY:
        return holiday + timedelta(days=1)
    return holiday


def _observed_entry(holiday: date, name: str) -> CalendarEntry:
    actual = observed_date(holiday)
    label = name if actual == holiday else name + OBSERVED_SUFFIX
    return CalendarEntry(actual, label, ENTRY_FULL, None)


@lru_cache(maxsize=None)
def generate_nyse_calendar(year: int) -> Tuple[CalendarEntry, ...]:
    """The NYSE holiday and early-close table for ``year`` from the conventions in the docstring.

    Chronologically sorted; names match the explicit tables exactly (tested for 2026 and 2027).
    It does not include unscheduled closures.
    """
    entries = []
    new_year = date(year, 1, 1)
    if new_year.weekday() == _SUNDAY:
        entries.append(CalendarEntry(new_year + timedelta(days=1), NAME_NEW_YEAR + OBSERVED_SUFFIX, ENTRY_FULL, None))
    elif new_year.weekday() != _SATURDAY:
        entries.append(CalendarEntry(new_year, NAME_NEW_YEAR, ENTRY_FULL, None))
    # A Saturday Jan 1 is not observed on Friday Dec 31 (NYSE Rule 7.2, yearly accounting period).
    entries.append(CalendarEntry(_nth_weekday(year, 1, _MONDAY, 3), NAME_MLK, ENTRY_FULL, None))
    entries.append(CalendarEntry(_nth_weekday(year, 2, _MONDAY, 3), NAME_WASHINGTON, ENTRY_FULL, None))
    entries.append(CalendarEntry(good_friday(year), NAME_GOOD_FRIDAY, ENTRY_FULL, None))
    entries.append(CalendarEntry(_last_weekday(year, 5, _MONDAY), NAME_MEMORIAL, ENTRY_FULL, None))
    if year >= JUNETEENTH_FIRST_YEAR:
        entries.append(_observed_entry(date(year, 6, 19), NAME_JUNETEENTH))
    entries.append(_observed_entry(date(year, 7, 4), NAME_INDEPENDENCE))
    entries.append(CalendarEntry(_nth_weekday(year, 9, _MONDAY, 1), NAME_LABOR, ENTRY_FULL, None))
    thanksgiving = _nth_weekday(year, 11, _THURSDAY, 4)
    entries.append(CalendarEntry(thanksgiving, NAME_THANKSGIVING, ENTRY_FULL, None))
    entries.append(CalendarEntry(thanksgiving + timedelta(days=1), NAME_DAY_AFTER_THANKSGIVING,
                                 ENTRY_EARLY_CLOSE, EARLY_CLOSE_ET))
    entries.append(_observed_entry(date(year, 12, 25), NAME_CHRISTMAS))
    # Eve early closes: only when the eve is a weekday and the holiday itself is the closure
    # (no weekend shift), i.e. the holiday falls on Tuesday..Friday.
    for eve, holiday, label in (
        (date(year, 7, 3), date(year, 7, 4), NAME_INDEPENDENCE_EVE),
        (date(year, 12, 24), date(year, 12, 25), NAME_CHRISTMAS_EVE),
    ):
        if observed_date(holiday) == holiday and eve.weekday() < _SATURDAY:
            entries.append(CalendarEntry(eve, label, ENTRY_EARLY_CLOSE, EARLY_CLOSE_ET))
    return tuple(sorted(entries, key=lambda entry: (entry.date, entry.type)))


def calendar_coverage(d: Union[date, datetime]) -> str:
    """``"table"`` when ``d``'s year has an explicit NYSE table, else ``"rule"`` (generated)."""
    return COVERAGE_TABLE if _as_date(d).year in EXPLICIT_TABLES else COVERAGE_RULE


@lru_cache(maxsize=None)
def holidays_for_year(year: int) -> Tuple[CalendarEntry, ...]:
    """Every calendar entry of ``year``: the explicit table, or the generator plus known
    unscheduled closures. Sorted by date."""
    if year in EXPLICIT_TABLES:
        return EXPLICIT_TABLES[year]
    extra = tuple(entry for entry in UNSCHEDULED_CLOSURES if entry.date.year == year)
    return tuple(sorted(generate_nyse_calendar(year) + extra, key=lambda entry: (entry.date, entry.type)))


@lru_cache(maxsize=None)
def _entries_by_date(year: int) -> Dict[date, CalendarEntry]:
    return {entry.date: entry for entry in holidays_for_year(year)}


def calendar_entry(d: Union[date, datetime]) -> Optional[CalendarEntry]:
    """The table entry on ``d`` (full holiday or early close) or ``None``."""
    d = _as_date(d)
    return _entries_by_date(d.year).get(d)


def table_status(today: Union[date, datetime], warn_days: int = TABLE_WARN_DAYS) -> str:
    """``"expired"`` after :data:`TABLE_EXPIRES`, ``"expiring_soon"`` within ``warn_days`` of it
    (inclusive), else ``"ok"``. ``desk_doctor`` FAILs on anything but ``ok``."""
    today = _as_date(today, "today")
    if isinstance(warn_days, bool) or not isinstance(warn_days, int) or warn_days < 0:
        raise ValueError(f"warn_days must be a non-negative int, got {warn_days!r}")
    remaining = (TABLE_EXPIRES - today).days
    if remaining < 0:
        return STATUS_EXPIRED
    if remaining <= warn_days:
        return STATUS_EXPIRING_SOON
    return STATUS_OK


# --------------------------------------------------------------------------------------------
# Trading days
# --------------------------------------------------------------------------------------------

ExtraHolidays = Union[Iterable[date], Mapping[date, str]]


def _extra(extra_holidays: ExtraHolidays) -> Dict[date, str]:
    """Normalise ``extra_holidays`` (dates, or date -> name) to a date -> label dict."""
    if extra_holidays is None:
        return {}
    if isinstance(extra_holidays, _MappingABC):
        items = [(k, str(v)) for k, v in extra_holidays.items()]
    else:
        items = [(k, UNSCHEDULED_LABEL) for k in extra_holidays]
    out: Dict[date, str] = {}
    for key, label in items:
        if isinstance(key, datetime) or not isinstance(key, date):
            raise ValueError(f"extra_holidays entries must be datetime.date values, got {key!r}")
        out[key] = label
    return out


def is_trading_day(d: Union[date, datetime], extra_holidays: ExtraHolidays = ()) -> bool:
    """True when the NYSE holds a session on ``d`` (early-close days included)."""
    d = _as_date(d)
    if d.weekday() >= _SATURDAY or d in _extra(extra_holidays):
        return False
    entry = calendar_entry(d)
    return entry is None or entry.type != ENTRY_FULL


def is_early_close(d: Union[date, datetime], extra_holidays: ExtraHolidays = ()) -> bool:
    """True when ``d`` is a trading day with a 13:00 ET close."""
    d = _as_date(d)
    if not is_trading_day(d, extra_holidays):
        return False
    entry = calendar_entry(d)
    return entry is not None and entry.type == ENTRY_EARLY_CLOSE


def holiday_name(d: Union[date, datetime], extra_holidays: ExtraHolidays = ()) -> Optional[str]:
    """The calendar label on ``d``: an injected closure's name, a full holiday's name or an
    early-close day's name; ``None`` on an ordinary day. A label, not a closure test."""
    d = _as_date(d)
    extra = _extra(extra_holidays)
    if d in extra:
        return extra[d]
    entry = calendar_entry(d)
    return None if entry is None else entry.name


def session_close_et(d: Union[date, datetime], product: str = PRODUCT_EQUITY,
                     extra_holidays: ExtraHolidays = ()) -> time:
    """The close on trading day ``d``: 16:00 (13:00 early) for ``equity``, 16:15 (13:15 early)
    for ``late_close``. Raises ValueError on a non-trading day or an unknown product."""
    d = _as_date(d)
    if product not in PRODUCTS:
        raise ValueError(f"product must be one of {PRODUCTS}, got {product!r}")
    if not is_trading_day(d, extra_holidays):
        raise ValueError(f"{d.isoformat()} is not a trading day: no session close")
    early = is_early_close(d, extra_holidays)
    if product == PRODUCT_LATE_CLOSE:
        return LATE_EARLY_CLOSE_ET if early else LATE_CLOSE_ET
    return EARLY_CLOSE_ET if early else REGULAR_CLOSE_ET


def previous_trading_day(d: Union[date, datetime], extra_holidays: ExtraHolidays = ()) -> date:
    """The last trading day strictly before ``d``."""
    d = _as_date(d) - timedelta(days=1)
    while not is_trading_day(d, extra_holidays):
        d -= timedelta(days=1)
    return d


def next_trading_day(d: Union[date, datetime], extra_holidays: ExtraHolidays = ()) -> date:
    """The first trading day strictly after ``d``."""
    d = _as_date(d) + timedelta(days=1)
    while not is_trading_day(d, extra_holidays):
        d += timedelta(days=1)
    return d


def trading_days_between(a: Union[date, datetime], b: Union[date, datetime],
                         extra_holidays: ExtraHolidays = ()) -> int:
    """Number of trading days in the half-open interval ``(a, b]``: ``a`` excluded, ``b`` included.

    So ``trading_days_between(today, expiry)`` is the trading-day DTE: 1 on the day before a
    trading-day expiry, 0 on the expiry itself. ``a == b`` gives 0. When ``b < a`` the result is
    ``-trading_days_between(b, a)`` (antisymmetric), which :func:`dte` uses for expired legs.
    """
    a, b = _as_date(a, "a"), _as_date(b, "b")
    if b < a:
        return -trading_days_between(b, a, extra_holidays)
    extra = _extra(extra_holidays)
    count = 0
    d = a + timedelta(days=1)
    while d <= b:
        if is_trading_day(d, extra):
            count += 1
        d += timedelta(days=1)
    return count


# --------------------------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------------------------


def session_at(utc_dt: datetime, product: str = PRODUCT_EQUITY,
               extra_holidays: ExtraHolidays = ()) -> SessionInfo:
    """Classify the instant ``utc_dt`` (see the session vocabulary in the module docstring).

    Raises :class:`TableExpiredError` when the ET date lies past :data:`TABLE_EXPIRES` (fail
    closed) and ValueError for a naive datetime or an unknown product.
    """
    utc_dt = _require_instant(utc_dt)
    if product not in PRODUCTS:
        raise ValueError(f"product must be one of {PRODUCTS}, got {product!r}")
    et_date = to_et(utc_dt).date()
    if et_date > TABLE_EXPIRES:
        raise TableExpiredError(
            f"the NYSE holiday table expired on {TABLE_EXPIRES.isoformat()} and the ET date is "
            f"{et_date.isoformat()}: refresh the tables from {SOURCE} and advance TABLE_EXPIRES"
        )
    status = table_status(et_date)
    label = holiday_name(et_date, extra_holidays)
    if et_date.weekday() >= _SATURDAY:
        return SessionInfo(SESSION_WEEKEND, False, None, None, label, utc_dt, et_date, None, product, status)
    if not is_trading_day(et_date, extra_holidays):
        return SessionInfo(SESSION_HOLIDAY, False, None, None, label, utc_dt, et_date, None, product, status)
    open_utc = from_et(datetime.combine(et_date, REGULAR_OPEN_ET))
    close_utc = from_et(datetime.combine(et_date, session_close_et(et_date, product, extra_holidays)))
    if utc_dt < open_utc:
        session = SESSION_PRE
    elif utc_dt < close_utc:
        session = SESSION_EARLY_CLOSE if is_early_close(et_date, extra_holidays) else SESSION_OPEN
    else:
        session = SESSION_CLOSED
    minutes = None
    if utc_dt < close_utc:
        minutes = int((close_utc - utc_dt).total_seconds() // 60)
    return SessionInfo(session, True, close_utc, minutes, label, utc_dt, et_date, open_utc, product, status)


def previous_close_utc(utc_dt: datetime, product: str = PRODUCT_EQUITY,
                       extra_holidays: ExtraHolidays = ()) -> datetime:
    """The close instant of the most recent session that has ENDED at or before ``utc_dt``:
    today's close once it has passed, otherwise the previous trading day's close. This is the
    "as of close HH:MM ET" reference for freshness before the open and on non-trading days."""
    info = session_at(utc_dt, product, extra_holidays)
    if info.trading_day and info.close_utc is not None and info.close_utc <= info.as_of_utc:
        return info.close_utc
    prev = previous_trading_day(info.et_date, extra_holidays)
    return from_et(datetime.combine(prev, session_close_et(prev, product, extra_holidays)))


def expected_quote_age_min(session_info: SessionInfo, feed_delay_min: int) -> Optional[int]:
    """How old the newest quote of a healthy ``feed_delay_min``-delayed feed is expected to be.

    ``open`` / ``early_close``: ``feed_delay_min`` (the caller adds its TTL and grace to form
    the freshness bound). ``closed``: ``max(feed_delay_min, minutes since the close)`` because
    the newest quote is the closing quote. ``pre``, ``weekend``, ``holiday``: ``None``: no quote
    from the current session exists; judge freshness against :func:`previous_close_utc`.
    """
    if not isinstance(session_info, SessionInfo):
        raise ValueError(f"session_info must be a SessionInfo, got {type(session_info).__name__}")
    if isinstance(feed_delay_min, bool) or not isinstance(feed_delay_min, int) or feed_delay_min < 0:
        raise ValueError(f"feed_delay_min must be a non-negative int, got {feed_delay_min!r}")
    if session_info.session in LIVE_SESSIONS:
        return feed_delay_min
    if session_info.session == SESSION_CLOSED and session_info.close_utc is not None:
        since_close = int((session_info.as_of_utc - session_info.close_utc).total_seconds() // 60)
        return max(feed_delay_min, since_close)
    return None


# Vendor clock states (the market-data client's clock record says ``open`` or ``closed``; the pre- and post-market
# labels some feeds use, and the plain calendar statuses) -> this module's sessions.
VENDOR_STATE_MAP: Dict[str, str] = {
    "open": SESSION_OPEN,
    "premarket": SESSION_PRE,
    "pre": SESSION_PRE,
    "postmarket": SESSION_CLOSED,
    "closed": SESSION_CLOSED,
}


def reconcile_vendor_state(local: SessionInfo, vendor: Union[str, Mapping[str, object], None]) -> Reconciliation:
    """Combine the local classification with a vendor clock; the more-closed view wins.

    ``vendor`` is a state string (``open`` / ``closed``, or a ``premarket`` / ``postmarket``
    label) or a clock record (``alpaca_client.ClockResult``'s ``{"date", "state", ...}``, also
    accepted inside a ``{"clock": {...}}`` wrapper). A vendor ``closed`` while the desk believes the market is trading makes
    the effective session ``closed`` (an unscheduled closure or halt the tables cannot know); a
    vendor ``open`` never opens a locally closed day. A vendor object dated another ET day, or
    an unrecognised state, is ignored (local wins) and reported. Every difference is returned in
    ``disagreement`` for the caller to log.
    """
    if not isinstance(local, SessionInfo):
        raise ValueError(f"local must be a SessionInfo, got {type(local).__name__}")
    state: Optional[str] = None
    vendor_date: Optional[str] = None
    if isinstance(vendor, _MappingABC):
        inner = vendor.get("clock", vendor)
        if isinstance(inner, _MappingABC):
            raw_state = inner.get("state")
            state = None if raw_state is None else str(raw_state)
            raw_date = inner.get("date")
            vendor_date = None if raw_date is None else str(raw_date)
    elif isinstance(vendor, str):
        state = vendor
    normalised = None if state is None else state.strip().lower()
    local_live = local.session in LIVE_SESSIONS
    if vendor_date is not None and vendor_date != local.et_date.isoformat():
        return Reconciliation(local.session, local.session, state, None, False,
                              f"vendor clock dated {vendor_date} but the ET date is {local.et_date.isoformat()}; "
                              f"vendor state {state!r} ignored, local {local.session!r} kept")
    vendor_session = None if normalised is None else VENDOR_STATE_MAP.get(normalised)
    if vendor_session is None:
        return Reconciliation(local.session, local.session, state, None, False,
                              f"vendor state {state!r} unrecognised; local {local.session!r} kept")
    vendor_live = vendor_session in LIVE_SESSIONS
    if local_live == vendor_live:
        return Reconciliation(local.session, local.session, state, vendor_session, True, None)
    if local_live:
        return Reconciliation(SESSION_CLOSED, local.session, state, vendor_session, False,
                              f"local {local.session!r} but vendor {state!r}: treating the market as closed "
                              f"(unscheduled closure or halt?)")
    return Reconciliation(local.session, local.session, state, vendor_session, False,
                          f"vendor {state!r} but local {local.session!r}: local closed view kept")


# --------------------------------------------------------------------------------------------
# Expiry arithmetic
# --------------------------------------------------------------------------------------------


def settlement_day(expiry: Union[date, datetime], am_settled: bool,
                   extra_holidays: ExtraHolidays = ()) -> date:
    """The date the settlement value is struck: ``expiry`` when it is a trading day, else the
    preceding trading day (the exchange moves a holiday or weekend expiration back). PM
    contracts settle on that day's close, AM contracts on that morning's opening quotation."""
    d = _as_date(expiry, "expiry")
    if not isinstance(am_settled, bool):
        raise ValueError(f"am_settled must be a bool, got {am_settled!r}")
    while not is_trading_day(d, extra_holidays):
        d -= timedelta(days=1)
    return d


def last_trading_day(expiry: Union[date, datetime], am_settled: bool,
                     extra_holidays: ExtraHolidays = ()) -> date:
    """PM-settled: the settlement day itself (the expiry if it trades, else the preceding trading
    day; a legacy Saturday-dated symbol resolves to the Friday or earlier). AM-settled: the
    trading day before the settlement morning (a Friday SPX monthly last trades Thursday; when
    the Friday is a holiday it settles Thursday and last trades Wednesday)."""
    settle = settlement_day(expiry, am_settled, extra_holidays)
    if not am_settled:
        return settle
    return previous_trading_day(settle, extra_holidays)


def dte(expiry: Union[date, datetime], as_of: Union[date, datetime], unit: str = UNIT_CALENDAR,
        am_settled: bool = False, extra_holidays: ExtraHolidays = ()) -> int:
    """Days to the LAST TRADING DAY of ``expiry`` from the ET date ``as_of``.

    ``unit="calendar"``: plain day difference. ``unit="trading"``: trading days in
    ``(as_of, last_trading_day]`` (:func:`trading_days_between`). Both are 0 on the last trading
    day and negative once it has passed (by that many days). ``as_of`` may be a date or an aware
    datetime (its America/New_York date is used). ``am_settled`` moves the end date one trading
    day earlier (see :func:`last_trading_day`).
    """
    if unit not in DTE_UNITS:
        raise ValueError(f"unit must be one of {DTE_UNITS}, got {unit!r}")
    end = last_trading_day(expiry, am_settled, extra_holidays)
    start = _as_date(as_of, "as_of")
    if unit == UNIT_CALENDAR:
        return (end - start).days
    return trading_days_between(start, end, extra_holidays)


def exercise_cutoff_utc(expiry: Union[date, datetime], am_settled: bool,
                        broker_cutoff_et: Optional[time] = None,
                        extra_holidays: ExtraHolidays = ()) -> datetime:
    """The instant tau is measured to (plan section 8).

    PM-settled: the last trading day's equity close (16:00 ET, 13:00 ET on an early-close day;
    expiring late-close contracts also stop at 16:00 ET), or ``broker_cutoff_et`` on that day
    when it is EARLIER than the close (a later cutoff such as the OCC's 17:30 ET does not extend
    the market). AM-settled: 09:30 ET on the settlement morning; the broker cutoff is ignored
    because exercise is automatic and the settlement value is struck at the open regardless.
    ``broker_cutoff_et`` is a naive ``datetime.time`` in ET.
    """
    if broker_cutoff_et is not None:
        if not isinstance(broker_cutoff_et, time) or broker_cutoff_et.tzinfo is not None:
            raise ValueError(f"broker_cutoff_et must be a naive datetime.time in ET, got {broker_cutoff_et!r}")
    if am_settled:
        settle = settlement_day(expiry, True, extra_holidays)
        return from_et(datetime.combine(settle, AM_SETTLEMENT_ET))
    ltd = last_trading_day(expiry, False, extra_holidays)
    cutoff = session_close_et(ltd, PRODUCT_EQUITY, extra_holidays)
    if broker_cutoff_et is not None and broker_cutoff_et < cutoff:
        cutoff = broker_cutoff_et
    return from_et(datetime.combine(ltd, cutoff))


__all__ = [
    "AM_SETTLEMENT_ET",
    "COVERAGE_RULE",
    "COVERAGE_TABLE",
    "CalendarEntry",
    "DTE_UNITS",
    "EARLY_CLOSE_ET",
    "EDT",
    "ENTRY_EARLY_CLOSE",
    "ENTRY_FULL",
    "EST",
    "EXPLICIT_TABLES",
    "FIRST_DST_RULE_YEAR",
    "JUNETEENTH_FIRST_YEAR",
    "LATE_CLOSE_ET",
    "LATE_EARLY_CLOSE_ET",
    "LIVE_SESSIONS",
    "MarketClockError",
    "NYSE_HOLIDAYS_2026",
    "NYSE_HOLIDAYS_2027",
    "OBSERVED_SUFFIX",
    "PRODUCTS",
    "PRODUCT_EQUITY",
    "PRODUCT_LATE_CLOSE",
    "REGULAR_CLOSE_ET",
    "REGULAR_OPEN_ET",
    "Reconciliation",
    "SESSIONS",
    "SESSION_CLOSED",
    "SESSION_EARLY_CLOSE",
    "SESSION_HOLIDAY",
    "SESSION_OPEN",
    "SESSION_PRE",
    "SESSION_WEEKEND",
    "SOURCE",
    "STATUS_EXPIRED",
    "STATUS_EXPIRING_SOON",
    "STATUS_OK",
    "SessionInfo",
    "TABLE_EXPIRES",
    "TABLE_WARN_DAYS",
    "TABLE_YEARS",
    "TableExpiredError",
    "UNIT_CALENDAR",
    "UNIT_TRADING",
    "UNSCHEDULED_CLOSURES",
    "UNSCHEDULED_LABEL",
    "UTC",
    "VENDOR_STATE_MAP",
    "calendar_coverage",
    "calendar_entry",
    "dst_window_utc",
    "dte",
    "easter_sunday",
    "et_offset",
    "exercise_cutoff_utc",
    "expected_quote_age_min",
    "from_et",
    "generate_nyse_calendar",
    "good_friday",
    "holiday_name",
    "holidays_for_year",
    "is_dst",
    "is_early_close",
    "is_trading_day",
    "last_trading_day",
    "next_trading_day",
    "observed_date",
    "previous_close_utc",
    "previous_trading_day",
    "reconcile_vendor_state",
    "session_at",
    "session_close_et",
    "settlement_day",
    "table_status",
    "to_et",
    "trading_days_between",
]
