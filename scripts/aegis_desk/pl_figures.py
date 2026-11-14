"""The P/L figures the desk views print beside the book's own: totals, today's P/L per group and the percentages.

The EOD Desk Brief and the book view (``book.py --view``) need one total for the book, one "today" number per
position and a percentage beside each, and the model that relays them never does arithmetic. This module computes
them from a book document (the live ``book.load_book`` dict or a snapshot read back from ``data/book/``, whose money
fields may be decimal strings) with ``Decimal`` only, half-even to the cent and percentages to two places
(``payoff.quantize_money``, ``pnl.quantize_pct``). It adds nothing to the book or its snapshot: the book stays the
record, these are views of it. Standard library only, no I/O, no clock.

Definitions
-----------
* A group's **cost** is its ``net_cost`` when that is a debit (above zero). A credit group has no cost to measure a
  percentage against, so its percentages are ``None``.
* A group's **P/L %** is ``unrealized_pl_mid / cost x 100`` (``None`` without a mid P/L or a cost).
* A group's **day P/L** is the sum of its open legs' ``day_pl`` when the group is ``ok`` and EVERY open leg has
  one, the rule the book's own portfolio total follows (``book._day_pl_total``): a group's day P/L is never
  partially summed. Otherwise ``None``.
* A group's **value now** is the sum over its open legs of ``open_quantity x multiplier x mark_mid`` (signed: a
  short leg subtracts); its **value at the start of the day** is ``value now - day P/L``, which is the previous
  close's value for a leg held overnight and the entry value for a leg opened today. **Today %** is ``day P/L /
  start value x 100`` when the start value is above zero, else ``None``.
* The book's **total P/L** is ``portfolio.unrealized_pl_mid + portfolio.realized_pl_ytd`` (and the same on the
  liquidation basis): what the open positions show plus what was closed this calendar year. Its **total %** is the
  open positions' mid P/L over the cost of the ok, open debit groups (the groups the book's totals sum), so it
  says how the open positions stand against what they cost; realized P/L has no cost to measure against.
* The book's **today %** is ``portfolio.day_pl`` over the start value of the groups that total counts (the ok
  groups whose every open leg has a day P/L).
* An unavailable group's **reason** reads its own ``row <n> <symbol>: no usable mark (<grade> <status>)`` warnings
  (``book.group`` writes them) as ``row <n> had no usable quote (grade <grade>, <status>)``, the symbol left out,
  joined by ``; ``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

from .payoff import quantize_money
from .pnl import quantize_pct

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
OK = "ok"
_NO_MARK_RE = re.compile(r"^row (?P<row>[0-9]+) \S+: no usable mark \((?P<why>[^)]*)\)$")


def decimal_of(value: Any) -> Optional[Decimal]:
    """A Decimal of a book field (a Decimal, an int or a decimal string), ``None`` for anything else."""
    if value is None or isinstance(value, bool) or isinstance(value, float):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _pct(numerator: Optional[Decimal], denominator: Optional[Decimal]) -> Optional[Decimal]:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return quantize_pct(numerator / denominator * _HUNDRED)


@dataclass(frozen=True)
class GroupFigures:
    """The derived figures of one group of a book document (module notes)."""

    group_id: str
    cost: Optional[Decimal]
    pl_pct: Optional[Decimal]
    day_pl: Optional[Decimal]
    day_pct: Optional[Decimal]
    start_value: Optional[Decimal]
    reason: Optional[str]


@dataclass(frozen=True)
class BookFigures:
    """The derived figures of a book document: the totals and one :class:`GroupFigures` per group."""

    total_pl_mid: Optional[Decimal]
    total_pl_liq: Optional[Decimal]
    total_pct: Optional[Decimal]
    open_cost: Optional[Decimal]
    day_pct: Optional[Decimal]
    groups: Dict[str, GroupFigures] = field(default_factory=dict)


def _open_legs(document: Mapping[str, Any], group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    by_row = {leg.get("row_number"): leg for leg in document.get("legs") or [] if isinstance(leg, Mapping)}
    return [by_row[row] for row in group.get("legs") or [] if row in by_row and by_row[row].get("status") == "open"]


def unavailable_reason(group: Mapping[str, Any]) -> Optional[str]:
    """``row 2 had no usable quote (grade E, stale)``: the group's own no-usable-mark warnings in words, without the
    symbol; ``None`` when it has none."""
    reasons = []
    for warning in group.get("warnings") or []:
        match = _NO_MARK_RE.match(warning) if isinstance(warning, str) else None
        if match is None:
            continue
        grade, _, status = match.group("why").partition(" ")
        words = [("grade %s" % grade) if len(grade) == 1 and grade.isalpha() else "", status.replace("_", " ")]
        detail = ", ".join(word for word in words if word)
        reasons.append("row %s had no usable quote%s" % (match.group("row"), " (%s)" % detail if detail else ""))
    return "; ".join(reasons) or None


def group_figures(document: Mapping[str, Any], group: Mapping[str, Any]) -> GroupFigures:
    """:class:`GroupFigures` of ``group`` (module notes)."""
    legs = _open_legs(document, group)
    net_cost = decimal_of(group.get("net_cost"))
    cost = net_cost if net_cost is not None and net_cost > 0 else None
    ok = group.get("data_quality") == OK
    pl_pct = _pct(decimal_of(group.get("unrealized_pl_mid")), cost) if ok else None
    day_pl = start = day_pct = None
    if ok and legs:
        days = [decimal_of(leg.get("day_pl")) for leg in legs]
        if all(value is not None for value in days):
            day_pl = quantize_money(sum((value for value in days if value is not None), _ZERO))
            value_now = _ZERO
            for leg in legs:
                quantity, multiplier = decimal_of(leg.get("open_quantity")), decimal_of(leg.get("multiplier"))
                mark = decimal_of(leg.get("mark_mid"))
                if quantity is None or multiplier is None or mark is None:
                    value_now = None
                    break
                value_now += quantity * multiplier * mark
            if value_now is not None:
                start = quantize_money(value_now - day_pl)
                day_pct = _pct(day_pl, start)
    reason = None if ok else unavailable_reason(group)
    return GroupFigures(str(group.get("group_id")), cost, pl_pct, day_pl, day_pct, start, reason)


def book_figures(document: Mapping[str, Any]) -> BookFigures:
    """:class:`BookFigures` of a book document (module notes)."""
    portfolio = document.get("portfolio") or {}
    groups = [group for group in document.get("groups") or [] if isinstance(group, Mapping)]
    figures = {str(group.get("group_id")): group_figures(document, group) for group in groups}
    realized = decimal_of(portfolio.get("realized_pl_ytd"))
    mid, liq = decimal_of(portfolio.get("unrealized_pl_mid")), decimal_of(portfolio.get("unrealized_pl_liq"))
    total_mid = quantize_money(mid + realized) if mid is not None and realized is not None else None
    total_liq = quantize_money(liq + realized) if liq is not None and realized is not None else None
    counted = [group for group in groups if group.get("data_quality") == OK and _open_legs(document, group)]
    costs = [figures[str(group.get("group_id"))].cost for group in counted]
    open_cost = quantize_money(sum((cost for cost in costs if cost is not None), _ZERO)) if counted else None
    total_pct = _pct(mid, open_cost) if open_cost else None
    day = decimal_of(portfolio.get("day_pl"))
    day_pct = None
    if day is not None:
        starts = [figures[str(group.get("group_id"))] for group in counted]
        included = [item for item in starts if item.day_pl is not None]
        if included and all(item.start_value is not None for item in included):
            day_pct = _pct(day, sum((item.start_value for item in included if item.start_value is not None), _ZERO))
    return BookFigures(total_mid, total_liq, total_pct, open_cost, day_pct, figures)


def sign_of(value: Optional[Decimal]) -> Optional[int]:
    """``1`` for a gain, ``-1`` for a loss, ``0`` for exactly zero, ``None`` for unavailable."""
    if value is None:
        return None
    return 1 if value > 0 else (-1 if value < 0 else 0)


__all__ = ["BookFigures", "GroupFigures", "book_figures", "decimal_of", "group_figures", "sign_of",
           "unavailable_reason"]

