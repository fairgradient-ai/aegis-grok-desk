"""P/L arithmetic for the book of record: marks, unrealized, realized, day P/L, percent captured.

Plan section 8 money conventions and the brief's worked examples; standard library only, and every value is
a :class:`decimal.Decimal` quantised half-even to the cent (this module never converts to float: a test
greps for it). Signed cash flows throughout: ``quantity`` is signed (negative is short), ``cost_basis_total``
is the signed cash flow of opening (paid positive, received negative), ``close_price_per_unit`` is the
unsigned price per share a closed quantity left at (0 = expired worthless).

* :func:`leg_marks` picks a row's marks from its :class:`aegis_desk.quote_qc.QuoteQC`: ``mark_mid`` is the
  quote's mid, ``mark_liq`` the liquidation side (the bid for a long row, the ask for a short row).
  ``basis_available`` is true only for a usable grade (A-D) with both marks present; ``mark_source`` is
  then ``"mid"``, otherwise ``"unavailable"``. A grade-E quote keeps its mid for DISPLAY (``mark_mid`` set,
  ``mark_liq`` None) but is never a basis; a grade-F quote or a missing verdict carries no marks. With
  exactly one stale side (grade D, ``one_side_stale``) the mid is not invented and only the fresh side's
  own mark is kept, so the basis is unavailable.
* :func:`unrealized`: ``open quantity x multiplier x mark - remaining cost basis``. Long 2 calls paid 640
  marked 4.00: ``2 x 100 x 4.00 - 640 = +160``; short 2 calls received 640 marked 1.60: ``-2 x 100 x
  1.60 - (-640) = +320``. A partially closed row marks what is still open against its pro-rata basis.
* :func:`realized_for_row`: for the closed quantity ``c`` at ``x`` per share, ``sign(quantity) x c x
  multiplier x x - closed cost basis`` (the pro-rata part of the total). Long 2 calls paid 640 closed at
  4.00: +160; short 2 calls received 640 closed at 1.60: +320; a short put that expired worthless keeps
  its whole credit; the fixture's closed short put (``-1 x 1 x 100 x 0.55 - (-480) = +425``). The
  assignment convention: the option row is closed at 0 and keeps its premium; the shares are booked at
  the strike on their own row.
* :func:`day_pl`: ``open quantity x multiplier x (mark_now - mark_prev)`` on one basis, ``mark_prev``
  being the desk's own previous snapshot mark (never a vendor ``prevclose``). A row opened today measures
  from its entry price per share. ``None`` without a mark, without a previous mark (and not opened today),
  or for a closed row.
* :func:`cost_to_close`: the signed cash flow of closing the open legs now, ``-sum(open quantity x
  multiplier x mark)`` (paying is positive: a short premium group pays to close, a debit group receives).
* :func:`pct_captured`: credit groups ``(credit - cost_to_close) / credit x 100`` with ``credit =
  -net_cost``; stock-basis and debit groups measure ``unrealized / max_profit x 100`` (the covered call
  and collar are measured on group P/L against group max profit, plan section 8), ``None`` without a
  positive max profit. Two decimals.
* :func:`realized_by_period`: :class:`Realized` sums of :func:`realized_for_row` over rows whose ``closed``
  date is today, in the month, in the year (ET dates).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Iterable, Mapping, Optional

from .grouping import PositionGroup
from .payoff import quantize_money
from .positions_csv import PositionRow
from .quote_qc import QuoteQC

MARK_SOURCE_MID: str = "mid"
MARK_SOURCE_UNAVAILABLE: str = "unavailable"
MARK_SOURCES: tuple = (MARK_SOURCE_MID, MARK_SOURCE_UNAVAILABLE)
PCT_QUANTUM: Decimal = Decimal("0.01")
_HUNDRED = Decimal(100)
_ZERO = Decimal(0)


@dataclass(frozen=True)
class LegMarks:
    """A row's marks (module notes). ``grade`` echoes the verdict's grade (``None`` without a verdict)."""

    mark_mid: Optional[Decimal]
    mark_liq: Optional[Decimal]
    basis_available: bool
    grade: Optional[str] = None

    @property
    def mark_source(self) -> str:
        return MARK_SOURCE_MID if self.basis_available else MARK_SOURCE_UNAVAILABLE


@dataclass(frozen=True)
class Realized:
    """Realized P/L by period (plan section 8: realized today / MTD / YTD)."""

    today: Decimal
    mtd: Decimal
    ytd: Decimal


def quantize_pct(value: Decimal) -> Decimal:
    """Two decimals, half-even: the convention for every percentage the book reports."""
    return value.quantize(PCT_QUANTUM, rounding=ROUND_HALF_EVEN)


def leg_marks(row: PositionRow, qc: Optional[QuoteQC]) -> LegMarks:
    """The marks of ``row`` from its quote verdict (module notes)."""
    if qc is None:
        return LegMarks(None, None, False, None)
    if not qc.usable_for_rules:
        return LegMarks(qc.mark_mid, None, False, qc.grade)  # E shows its mid; F has none; neither is a basis
    liq = qc.mark_liq_long if row.is_long else qc.mark_liq_short
    return LegMarks(qc.mark_mid, liq, qc.mark_mid is not None and liq is not None, qc.grade)


def unrealized(row: PositionRow, mark: Optional[Decimal]) -> Optional[Decimal]:
    """``open quantity x multiplier x mark - remaining cost basis``, to the cent; ``None`` without a mark
    or for a closed row."""
    if mark is None or row.is_closed:
        return None
    return quantize_money(row.open_quantity * row.multiplier * mark - row.remaining_cost_basis)


def realized_for_row(row: PositionRow) -> Optional[Decimal]:
    """Realized P/L of the row's closed quantity (module notes); ``None`` when nothing was closed."""
    if row.closed_quantity is None or row.close_price_per_unit is None:
        return None
    return quantize_money(row.sign * row.closed_quantity * row.multiplier * row.close_price_per_unit
                          - row.closed_cost_basis)


def day_pl(row: PositionRow, mark_now: Optional[Decimal], mark_prev: Optional[Decimal],
           opened_today: bool) -> Optional[Decimal]:
    """``open quantity x multiplier x (mark_now - mark_prev)``; a row opened today measures from its entry
    price per share (module notes)."""
    if mark_now is None or row.is_closed:
        return None
    reference = row.entry_price_per_share if opened_today else mark_prev
    if reference is None:
        return None
    return quantize_money(row.open_quantity * row.multiplier * (mark_now - reference))


def cost_to_close(legs: Iterable[PositionRow], marks: Mapping[int, Optional[Decimal]]) -> Optional[Decimal]:
    """The signed cash flow of closing the open legs at ``marks`` (row number -> mark): paying is positive.
    ``None`` when an open leg has no mark."""
    total = _ZERO
    for leg in legs:
        if not leg.is_open or leg.open_quantity == 0:
            continue
        mark = marks.get(leg.row_number)
        if mark is None:
            return None
        total -= leg.open_quantity * leg.multiplier * mark
    return quantize_money(total)


def group_unrealized(legs: Iterable[PositionRow], marks: Mapping[int, Optional[Decimal]]) -> Optional[Decimal]:
    """The sum of :func:`unrealized` over the open legs; ``None`` when any open leg has no mark (an
    unavailable leg is never summed as zero)."""
    total = _ZERO
    for leg in legs:
        if not leg.is_open:
            continue
        value = unrealized(leg, marks.get(leg.row_number))
        if value is None:
            return None
        total += value
    return quantize_money(total)


def pct_captured(group: PositionGroup, cost_to_close_total: Optional[Decimal],
                 max_profit: Optional[Decimal] = None) -> Optional[Decimal]:
    """Percent captured (module notes): of the credit for a credit group, of ``max_profit`` otherwise."""
    if cost_to_close_total is None:
        return None
    if group.is_credit:
        credit = -group.net_cost
        if credit <= 0:
            return None
        return quantize_pct((credit - cost_to_close_total) / credit * _HUNDRED)
    if max_profit is None or max_profit <= 0:
        return None
    gain = -cost_to_close_total - group.net_cost
    return quantize_pct(gain / max_profit * _HUNDRED)


def realized_by_period(rows: Iterable[PositionRow], today_et: datetime.date) -> Realized:
    """Realized P/L closed today, month to date and year to date (ET dates), to the cent."""
    if not isinstance(today_et, datetime.date) or isinstance(today_et, datetime.datetime):
        raise ValueError("today_et must be a datetime.date, got %r" % (today_et,))
    today = mtd = ytd = _ZERO
    for row in rows:
        value = realized_for_row(row)
        if value is None or row.closed is None:
            continue
        if row.closed.year != today_et.year:
            continue
        ytd += value
        if row.closed.month == today_et.month:
            mtd += value
            if row.closed == today_et:
                today += value
    return Realized(quantize_money(today), quantize_money(mtd), quantize_money(ytd))


__all__ = [
    "LegMarks",
    "MARK_SOURCES",
    "MARK_SOURCE_MID",
    "MARK_SOURCE_UNAVAILABLE",
    "PCT_QUANTUM",
    "Realized",
    "cost_to_close",
    "day_pl",
    "group_unrealized",
    "leg_marks",
    "pct_captured",
    "quantize_money",
    "quantize_pct",
    "realized_by_period",
    "realized_for_row",
    "unrealized",
]
