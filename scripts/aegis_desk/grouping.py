"""Leg grouping: the rows of ``positions.csv`` become structures (plan section 8; standard library only).

Rows share a group by ``PositionRow.group_id`` (the ``trade_id``, or ``ROW-<n>`` for a row without
one). Every row of a group must share ``account`` and ``underlying`` (the ticker for stock-like rows,
the OCC base root for options); a mismatch is a group-level error that quarantines the group
(``structure = "invalid"``, the reasons in ``errors``). The structure is decided by the pattern of
OPEN legs; closed rows stay in the group for realized P/L. A group whose legs are all closed keeps the
structure its legs had (classified on the original quantities) with ``open_legs == []``.

Legs of the same contract (two open rows of one OCC symbol, matched FIFO by the P/L layer) are one
leg of the pattern with their open quantities summed; partially closed rows count their remaining
quantity. ``net_cost`` is the sum of the open legs' remaining cost bases (positive debit, negative
credit); for a closed-only group it is the original net cost of its legs, there being nothing open to
mark. ``basis_kind`` is ``stock`` when the pattern holds a stock-like leg (the structure is measured
on group P/L), else ``credit`` (``net_cost < 0``) or ``debit``.

Structures (the brief's table)
------------------------------
``long_stock`` / ``short_stock``: one stock-like leg. ``long_call`` / ``long_put``: one long option.
``short_call`` (undefined risk) / ``cash_secured_put`` (warning ``cash reserve not verified``: the
reserve is not in the CSV): one short option. ``covered_call``: long shares and ONE short call leg
of any expiry with ``shares >= 100 x |calls|``; shares beyond that are the warning ``uncovered
shares`` and the structure stays ``covered_call``; fewer shares than the calls need is ``custom``
with a warning. ``protective_put``: long shares plus long put leg(s). ``collar``: long shares, one
long put leg and one short call leg (shares cover the calls). ``vertical_credit_call`` /
``vertical_credit_put`` / ``vertical_debit_call`` / ``vertical_debit_put``: two option legs of the
same right and expiry, equal unsigned quantity, opposite signs; credit when ``net_cost < 0``.
``custom``: anything else (mixed expiries, three or more option legs, mixed rights without stock,
short stock with options): marked and reported, structure-specific metrics are ``None`` with the
warning ``custom structure``. Warnings are not raised for a closed-only group.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from aegis_desk import occ, units
from aegis_desk.positions_csv import PositionRow

STRUCTURE_LONG_STOCK: str = "long_stock"
STRUCTURE_SHORT_STOCK: str = "short_stock"
STRUCTURE_LONG_CALL: str = "long_call"
STRUCTURE_LONG_PUT: str = "long_put"
STRUCTURE_SHORT_CALL: str = "short_call"
STRUCTURE_CASH_SECURED_PUT: str = "cash_secured_put"
STRUCTURE_COVERED_CALL: str = "covered_call"
STRUCTURE_PROTECTIVE_PUT: str = "protective_put"
STRUCTURE_COLLAR: str = "collar"
STRUCTURE_VERTICAL_CREDIT_CALL: str = "vertical_credit_call"
STRUCTURE_VERTICAL_CREDIT_PUT: str = "vertical_credit_put"
STRUCTURE_VERTICAL_DEBIT_CALL: str = "vertical_debit_call"
STRUCTURE_VERTICAL_DEBIT_PUT: str = "vertical_debit_put"
STRUCTURE_CUSTOM: str = "custom"
STRUCTURE_INVALID: str = "invalid"
STRUCTURES: Tuple[str, ...] = (
    STRUCTURE_LONG_STOCK, STRUCTURE_SHORT_STOCK, STRUCTURE_LONG_CALL, STRUCTURE_LONG_PUT, STRUCTURE_SHORT_CALL,
    STRUCTURE_CASH_SECURED_PUT, STRUCTURE_COVERED_CALL, STRUCTURE_PROTECTIVE_PUT, STRUCTURE_COLLAR,
    STRUCTURE_VERTICAL_CREDIT_CALL, STRUCTURE_VERTICAL_CREDIT_PUT, STRUCTURE_VERTICAL_DEBIT_CALL,
    STRUCTURE_VERTICAL_DEBIT_PUT, STRUCTURE_CUSTOM, STRUCTURE_INVALID,
)
"""Every value ``PositionGroup.structure`` can take."""

VERTICAL_STRUCTURES: Tuple[str, ...] = (STRUCTURE_VERTICAL_CREDIT_CALL, STRUCTURE_VERTICAL_CREDIT_PUT,
                                        STRUCTURE_VERTICAL_DEBIT_CALL, STRUCTURE_VERTICAL_DEBIT_PUT)
STOCK_STRUCTURES: Tuple[str, ...] = (STRUCTURE_LONG_STOCK, STRUCTURE_SHORT_STOCK, STRUCTURE_COVERED_CALL,
                                     STRUCTURE_PROTECTIVE_PUT, STRUCTURE_COLLAR)

BASIS_CREDIT: str = "credit"
BASIS_DEBIT: str = "debit"
BASIS_STOCK: str = "stock"
BASIS_KINDS: Tuple[str, str, str] = (BASIS_CREDIT, BASIS_DEBIT, BASIS_STOCK)

SHARES_PER_CONTRACT: Decimal = Decimal(units.CONTRACT_MULTIPLIER)

WARNING_CASH_RESERVE: str = "cash reserve not verified"
WARNING_CUSTOM: str = "custom structure"
_ZERO = Decimal(0)


@dataclass(frozen=True)
class _OptionLeg:
    """One contract of the pattern: the net signed quantity of every row with that symbol."""

    symbol: str
    right: str
    strike: Decimal
    expiration: datetime.date
    quantity: Decimal


@dataclass
class PositionGroup:
    """The rows sharing one ``group_id`` and what they form (module notes)."""

    group_id: str
    trade_id: Optional[str]
    account: str
    underlying: str
    structure: str
    legs: List[PositionRow]
    open_legs: List[PositionRow]
    closed_legs: List[PositionRow]
    net_cost: Decimal
    basis_kind: str
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def open_option_legs(self) -> List[PositionRow]:
        return [leg for leg in self.open_legs if leg.is_option]

    @property
    def stock_legs(self) -> List[PositionRow]:
        """The open stock-like legs."""
        return [leg for leg in self.open_legs if leg.is_stock_like]

    @property
    def is_credit(self) -> bool:
        return self.basis_kind == BASIS_CREDIT

    @property
    def is_closed(self) -> bool:
        """No open leg (every row is closed)."""
        return not self.open_legs

    @property
    def is_quarantined(self) -> bool:
        return bool(self.errors)

    @property
    def quarantine_reasons(self) -> Tuple[str, ...]:
        return tuple(self.errors)

    @property
    def underlying_stop(self) -> Optional[Decimal]:
        """The minimum ``underlying_stop`` among the rows, or ``None`` when no row has one."""
        stops = [leg.underlying_stop for leg in self.legs if leg.underlying_stop is not None]
        return min(stops) if stops else None

    @property
    def expiries(self) -> List[datetime.date]:
        """Sorted distinct expirations of the option legs in the pattern (open legs, or every leg of
        a closed-only group)."""
        legs = self.open_legs or self.legs
        return sorted({leg.expiration for leg in legs if leg.is_option and leg.expiration is not None})

    @property
    def row_numbers(self) -> List[int]:
        return [leg.row_number for leg in self.legs]

    def as_dict(self) -> Dict[str, object]:
        return {
            "group_id": self.group_id,
            "trade_id": self.trade_id,
            "account": self.account,
            "underlying": self.underlying,
            "structure": self.structure,
            "basis_kind": self.basis_kind,
            "net_cost": self.net_cost,
            "legs": self.row_numbers,
            "open_legs": [leg.row_number for leg in self.open_legs],
            "closed_legs": [leg.row_number for leg in self.closed_legs],
            "underlying_stop": self.underlying_stop,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def _plain(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else str(value)


def _pattern(legs: Iterable[PositionRow], use_open: bool) -> Tuple[Decimal, List[_OptionLeg], Decimal, bool]:
    """``(shares, option legs, net cost, has stock)`` of the legs: open quantities and remaining cost
    bases when ``use_open``, the original quantities and totals otherwise."""
    shares = _ZERO
    net_cost = _ZERO
    has_stock = False
    options: Dict[str, List[object]] = {}
    for leg in legs:
        quantity = leg.open_quantity if use_open else leg.quantity
        net_cost += leg.remaining_cost_basis if use_open else leg.cost_basis_total
        if leg.is_option and leg.occ is not None:
            entry = options.setdefault(leg.compact_symbol, [leg.occ, _ZERO])
            entry[1] = entry[1] + quantity  # type: ignore[operator]
        else:
            has_stock = True
            shares += quantity
    option_legs = [_OptionLeg(symbol, parsed.right, parsed.strike, parsed.expiration, quantity)  # type: ignore[union-attr]
                   for symbol, (parsed, quantity) in options.items() if quantity != 0]
    return shares, option_legs, net_cost, has_stock


def _uncovered(shares: Decimal, calls: Decimal) -> str:
    covered = calls * SHARES_PER_CONTRACT
    return "uncovered shares: %s of %s shares are not covered by the %s short call%s" % (
        _plain(shares - covered), _plain(shares), _plain(calls), "" if calls == 1 else "s")


def _not_covered(shares: Decimal, calls: Decimal) -> str:
    return "short calls not fully covered: %s shares cover %s of %s calls" % (
        _plain(shares), _plain(shares // SHARES_PER_CONTRACT), _plain(calls))


def _put_coverage(shares: Decimal, puts: Decimal) -> str:
    return "protective puts cover %s of %s shares" % (_plain(puts * SHARES_PER_CONTRACT), _plain(shares))


def classify(shares: Decimal, options: List[_OptionLeg], net_cost: Decimal) -> Tuple[str, List[str]]:
    """The structure and its warnings for a pattern (module notes)."""
    warnings: List[str] = []
    calls = [leg for leg in options if leg.right == occ.CALL]
    puts = [leg for leg in options if leg.right == occ.PUT]
    short_calls = [leg for leg in calls if leg.quantity < 0]
    long_calls = [leg for leg in calls if leg.quantity > 0]
    short_puts = [leg for leg in puts if leg.quantity < 0]
    long_puts = [leg for leg in puts if leg.quantity > 0]
    if not options:
        if shares > 0:
            return STRUCTURE_LONG_STOCK, warnings
        if shares < 0:
            return STRUCTURE_SHORT_STOCK, warnings
        return STRUCTURE_CUSTOM, [WARNING_CUSTOM]
    if shares == 0:
        if len(options) == 1:
            leg = options[0]
            if leg.right == occ.CALL:
                return (STRUCTURE_LONG_CALL if leg.quantity > 0 else STRUCTURE_SHORT_CALL), warnings
            if leg.quantity > 0:
                return STRUCTURE_LONG_PUT, warnings
            return STRUCTURE_CASH_SECURED_PUT, [WARNING_CASH_RESERVE]
        if len(options) == 2:
            first, second = options
            if (first.right == second.right and first.expiration == second.expiration
                    and abs(first.quantity) == abs(second.quantity) and (first.quantity > 0) != (second.quantity > 0)
                    and first.strike != second.strike):
                if first.right == occ.CALL:
                    return (STRUCTURE_VERTICAL_CREDIT_CALL if net_cost < 0 else STRUCTURE_VERTICAL_DEBIT_CALL), warnings
                return (STRUCTURE_VERTICAL_CREDIT_PUT if net_cost < 0 else STRUCTURE_VERTICAL_DEBIT_PUT), warnings
        return STRUCTURE_CUSTOM, [WARNING_CUSTOM]
    if shares > 0 and not long_calls and not short_puts:
        if len(short_calls) == 1 and not long_puts:
            calls_held = abs(short_calls[0].quantity)
            if shares < calls_held * SHARES_PER_CONTRACT:
                return STRUCTURE_CUSTOM, [_not_covered(shares, calls_held), WARNING_CUSTOM]
            if shares > calls_held * SHARES_PER_CONTRACT:
                warnings.append(_uncovered(shares, calls_held))
            return STRUCTURE_COVERED_CALL, warnings
        if long_puts and not short_calls:
            puts_held = sum((abs(leg.quantity) for leg in long_puts), _ZERO)
            if puts_held * SHARES_PER_CONTRACT != shares:
                warnings.append(_put_coverage(shares, puts_held))
            return STRUCTURE_PROTECTIVE_PUT, warnings
        if len(long_puts) == 1 and len(short_calls) == 1:
            calls_held = abs(short_calls[0].quantity)
            if shares < calls_held * SHARES_PER_CONTRACT:
                return STRUCTURE_CUSTOM, [_not_covered(shares, calls_held), WARNING_CUSTOM]
            if shares > calls_held * SHARES_PER_CONTRACT:
                warnings.append(_uncovered(shares, calls_held))
            puts_held = abs(long_puts[0].quantity)
            if puts_held * SHARES_PER_CONTRACT != shares:
                warnings.append(_put_coverage(shares, puts_held))
            return STRUCTURE_COLLAR, warnings
    return STRUCTURE_CUSTOM, [WARNING_CUSTOM]


def _distinct(values: Iterable[str]) -> List[str]:
    seen: List[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _build_group(group_id: str, legs: List[PositionRow]) -> PositionGroup:
    open_legs = [leg for leg in legs if leg.is_open]
    closed_legs = [leg for leg in legs if leg.is_closed]
    errors: List[str] = []
    accounts = _distinct(leg.account for leg in legs)
    if len(accounts) > 1:
        errors.append("account mismatch: %s" % ", ".join(accounts))
    underlyings = _distinct(leg.underlying for leg in legs)
    if len(underlyings) > 1:
        errors.append("underlying mismatch: %s" % ", ".join(underlyings))
    pattern_legs = open_legs if open_legs else legs
    shares, options, net_cost, has_stock = _pattern(pattern_legs, use_open=bool(open_legs))
    if has_stock:
        basis_kind = BASIS_STOCK
    elif net_cost < 0:
        basis_kind = BASIS_CREDIT
    else:
        basis_kind = BASIS_DEBIT
    if errors:
        structure, warnings = STRUCTURE_INVALID, []
    else:
        structure, warnings = classify(shares, options, net_cost)
        if not open_legs:
            warnings = []
    trade_ids = _distinct(leg.trade_id for leg in legs if leg.trade_id is not None)
    return PositionGroup(
        group_id=group_id,
        trade_id=trade_ids[0] if trade_ids else None,
        account=accounts[0],
        underlying=underlyings[0],
        structure=structure,
        legs=list(legs),
        open_legs=open_legs,
        closed_legs=closed_legs,
        net_cost=net_cost,
        basis_kind=basis_kind,
        warnings=warnings,
        errors=errors,
    )


def group_positions(rows: Iterable[PositionRow]) -> List[PositionGroup]:
    """Group ``rows`` by ``group_id`` in order of first appearance and classify each (module notes)."""
    by_group: Dict[str, List[PositionRow]] = {}
    for row in rows:
        by_group.setdefault(row.group_id, []).append(row)
    return [_build_group(group_id, legs) for group_id, legs in by_group.items()]


__all__ = [
    "BASIS_CREDIT",
    "BASIS_DEBIT",
    "BASIS_KINDS",
    "BASIS_STOCK",
    "PositionGroup",
    "SHARES_PER_CONTRACT",
    "STOCK_STRUCTURES",
    "STRUCTURES",
    "STRUCTURE_CASH_SECURED_PUT",
    "STRUCTURE_COLLAR",
    "STRUCTURE_COVERED_CALL",
    "STRUCTURE_CUSTOM",
    "STRUCTURE_INVALID",
    "STRUCTURE_LONG_CALL",
    "STRUCTURE_LONG_PUT",
    "STRUCTURE_LONG_STOCK",
    "STRUCTURE_PROTECTIVE_PUT",
    "STRUCTURE_SHORT_CALL",
    "STRUCTURE_SHORT_STOCK",
    "STRUCTURE_VERTICAL_CREDIT_CALL",
    "STRUCTURE_VERTICAL_CREDIT_PUT",
    "STRUCTURE_VERTICAL_DEBIT_CALL",
    "STRUCTURE_VERTICAL_DEBIT_PUT",
    "VERTICAL_STRUCTURES",
    "WARNING_CASH_RESERVE",
    "WARNING_CUSTOM",
    "classify",
    "group_positions",
]
