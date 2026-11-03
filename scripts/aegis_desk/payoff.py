"""Payoff at expiry: intrinsic value, leg and group payoff, the piecewise-linear profile and closed forms.

A port of the Aegis payoff kernels (``services/quant-engine``, FairGradient) with the closed forms of the
named structures as goldens (plan section 8, the brief's payoff section). Standard library only, and every
number is a :class:`decimal.Decimal`: nothing here is ever a binary float.

Definitions
-----------
* :func:`intrinsic` is per share: ``max(spot - strike, 0)`` for a call, ``max(strike - spot, 0)`` for a put.
* :func:`leg_payoff` is the value of one row at expiry, per group (not per share): ``open quantity x
  multiplier x intrinsic`` for an option row, ``open quantity x spot`` for a stock-like row. The OPEN
  (remaining) quantity is used so that a partially closed row contributes what is still at risk, in step
  with the group's ``net_cost`` (the sum of the open legs' remaining cost bases).
* :func:`group_payoff` is the P/L at the common expiry: the open legs' payoff less ``net_cost``; ``None``
  when the option legs do not share one expiry or nothing is open.
* :func:`grid_profile` evaluates the payoff on the grid ``{0} + every strike + 3 x max(strikes, entry
  spots)``. The payoff is piecewise linear in the spot, so the extreme over each segment sits at an
  endpoint: bounded extremes come from the kinks (0 and the strikes). The slope beyond the highest strike
  (shares plus 100 x the signed call contracts, computed exactly) decides ``unbounded_up`` (slope > 0,
  ``max_profit`` is ``None``) and ``unbounded_down`` (slope < 0, ``max_loss`` is ``None``: undefined
  risk). ``max_loss`` is reported as a positive number (0 for a structure that cannot lose). Breakevens
  are the zero crossings of each segment by linear interpolation, plus the exact crossing of the tail
  beyond the highest strike (so a breakeven beyond three times the anchor is not missed); a spot of 0 is
  never a breakeven.
* :func:`payoff_profile` applies the structure policy of the brief: a quarantined group, a group without
  open legs, mixed expiries and a ``custom`` structure have every metric ``None`` with ``reason`` set
  (:data:`REASON_QUARANTINED`, :data:`REASON_NO_OPEN_LEGS`, :data:`REASON_MIXED_EXPIRIES`,
  :data:`REASON_CUSTOM`); the named structures get the grid profile.
* :func:`closed_form_profile` is the closed form of each named structure, per share: covered call
  ``max_profit = (K - s0 + p) x n``, ``max_loss = (s0 - p) x n``, BE ``s0 - p``; cash-secured put
  ``p x m x c``, ``(K - p) x m x c``, BE ``K - p``; protective put ``max_loss = (s0 - K + d) x n``,
  unbounded up, BE ``s0 + d``; collar ``(Kc - s0 - d + p) x n``, ``(s0 - Kp + d - p) x n``, BE ``s0 + d -
  p``; credit vertical ``c``, ``w - c``, BE ``K_short + c`` (calls) / ``K_short - c`` (puts); debit vertical
  ``w - d``, ``d``, BE ``K_long + d`` / ``K_long - d``; long call ``max_loss = d``, BE ``K + d``; long put
  ``max_loss = d``, ``max_profit = (K - d) x m x c``, BE ``K - d``; short call ``max_profit = p``, unbounded
  down, BE ``K + p``; long stock ``max_loss = s0 x n``, BE ``s0``; short stock ``max_profit = s0 x |n|``, BE
  ``s0`` (``s0`` stock cost per share, ``p`` credit per share, ``d`` debit per share, ``w`` width; totals
  scale by the shares ``n`` or the contracts ``c x m``). They are computed in total money (``n x K -
  net_cost`` and so on, dividing only for the breakeven price) so that they equal the grid result to the
  cent, which the tests assert for every structure. ``None`` for a structure without a closed form
  (``custom``, a covered call whose shares exceed ``100 x |calls|``, a protective put or collar whose
  contracts do not match the shares). A closed-form breakeven is reported only when it is positive: a
  covered call whose credit exceeds the stock cost has ``s0 - p < 0`` and no breakeven at all (the payoff
  is above zero at every spot), exactly as the grid reports.

Money is quantised half-even to the cent (:func:`quantize_money`); a breakeven is printed in cents when it is
a whole number of cents, otherwise it keeps its exact digits when it terminates within four decimals and is
rounded half-even to four decimals beyond that (:func:`quantize_price`).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from . import grouping, occ
from .grouping import PositionGroup
from .positions_csv import PositionRow

CENT: Decimal = Decimal("0.01")
PRICE_QUANTUM: Decimal = Decimal("0.0001")
GRID_TAIL_FACTOR: Decimal = Decimal(3)
"""The tail probe of the grid sits at this multiple of the largest strike or entry spot."""

REASON_CUSTOM: str = grouping.WARNING_CUSTOM
REASON_MIXED_EXPIRIES: str = "mixed expiries: the option legs share no expiry, so there is no payoff at expiry"
REASON_NO_OPEN_LEGS: str = "no open legs"
REASON_QUARANTINED: str = "quarantined group"
REASONS: Tuple[str, ...] = (REASON_CUSTOM, REASON_MIXED_EXPIRIES, REASON_NO_OPEN_LEGS, REASON_QUARANTINED)

_ZERO = Decimal(0)


@dataclass(frozen=True)
class PayoffProfile:
    """The expiry profile of a group (module notes). ``grid`` holds ``(spot, payoff)`` pairs (payoff to
    the cent); ``reason`` is set, and every metric ``None``, when no profile can be computed."""

    max_profit: Optional[Decimal]
    max_loss: Optional[Decimal]
    breakevens: List[Decimal]
    unbounded_up: bool
    unbounded_down: bool
    grid: List[Tuple[Decimal, Decimal]]
    reason: Optional[str] = None


def quantize_money(value: Decimal) -> Decimal:
    """Half-even to the cent: the convention for every money total the book reports."""
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def quantize_price(value: Decimal) -> Decimal:
    """A price: in cents when it is a whole number of cents (``643.700`` prints ``643.70``), otherwise exact
    when it terminates within four decimals (``3.425``), else half-even to four decimals (``103.3333``)."""
    cents = value.quantize(CENT, rounding=ROUND_HALF_EVEN)
    if cents == value:
        return cents
    rounded = value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
    return value if value == rounded else rounded


def _require_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("%s must be a Decimal, got %s" % (name, type(value).__name__))
    if not value.is_finite():
        raise ValueError("%s must be finite, got %s" % (name, value))
    return value


def _blank(reason: str) -> PayoffProfile:
    return PayoffProfile(None, None, [], False, False, [], reason)


# --------------------------------------------------------------------------------------------
# Kernels
# --------------------------------------------------------------------------------------------


def intrinsic(right: str, strike: Decimal, spot: Decimal) -> Decimal:
    """Intrinsic value per share at ``spot``: ``max(spot - strike, 0)`` (call) or ``max(strike - spot, 0)``."""
    if right not in occ.RIGHTS:
        raise ValueError("right must be %s, got %r" % (" or ".join(occ.RIGHTS), right))
    strike = _require_decimal("strike", strike)
    spot = _require_decimal("spot", spot)
    if strike <= 0:
        raise ValueError("strike must be positive, got %s" % strike)
    if spot < 0:
        raise ValueError("spot must not be negative, got %s" % spot)
    value = spot - strike if right == occ.CALL else strike - spot
    return value if value > 0 else _ZERO


def leg_payoff(row: PositionRow, spot: Decimal) -> Decimal:
    """The value of the row's OPEN quantity at expiry when the underlying is at ``spot`` (module notes)."""
    spot = _require_decimal("spot", spot)
    quantity = row.open_quantity
    if quantity == 0:
        return _ZERO
    if row.is_option:
        if row.occ is None:  # pragma: no cover - the parser never builds an option row without its symbol
            raise ValueError("option row %d has no OCC symbol" % row.row_number)
        return quantity * row.multiplier * intrinsic(row.occ.right, row.occ.strike, spot)
    return quantity * spot


def group_payoff(group: PositionGroup, spot: Decimal) -> Optional[Decimal]:
    """The group's P/L at the common expiry: the open legs' payoff less ``net_cost`` (to the cent); ``None``
    when nothing is open or the option legs do not share one expiry."""
    if not group.open_legs or len(group.expiries) > 1:
        return None
    total = sum((leg_payoff(leg, spot) for leg in group.open_legs), _ZERO) - group.net_cost
    return quantize_money(total)


# --------------------------------------------------------------------------------------------
# The grid profile
# --------------------------------------------------------------------------------------------


def _tail_slope(legs: Sequence[PositionRow]) -> Decimal:
    """d(payoff)/d(spot) beyond the highest strike: every call is in the money, every put is worthless."""
    slope = _ZERO
    for leg in legs:
        if leg.is_option:
            if leg.occ is not None and leg.occ.is_call:
                slope += leg.open_quantity * leg.multiplier
        else:
            slope += leg.open_quantity
    return slope


def grid_profile(legs: Sequence[PositionRow], net_cost: Decimal) -> PayoffProfile:
    """The piecewise-linear profile of ``legs`` (their open quantities) against ``net_cost`` (module notes).

    Works for any set of legs whose option legs share one expiry, named structure or not; the policy of
    the brief (``custom`` has no metrics) is applied by :func:`payoff_profile`, not here.
    """
    net_cost = _require_decimal("net_cost", net_cost)
    open_legs = [leg for leg in legs if leg.is_open and leg.open_quantity != 0]
    if not open_legs:
        return _blank(REASON_NO_OPEN_LEGS)
    expiries = {leg.expiration for leg in open_legs if leg.is_option and leg.expiration is not None}
    if len(expiries) > 1:
        return _blank(REASON_MIXED_EXPIRIES)
    strikes = sorted({leg.strike for leg in open_legs if leg.is_option and leg.strike is not None})
    entry_spots = [abs(leg.remaining_cost_basis / leg.open_quantity) for leg in open_legs if leg.is_stock_like]
    anchor = max(strikes + entry_spots)
    kinks = sorted({_ZERO} | set(strikes))
    tail = anchor * GRID_TAIL_FACTOR

    def value_at(spot: Decimal) -> Decimal:
        return sum((leg_payoff(leg, spot) for leg in open_legs), _ZERO) - net_cost

    exact = [(spot, value_at(spot)) for spot in kinks]
    tail_value = value_at(tail)
    slope = _tail_slope(open_legs)
    unbounded_up = slope > 0
    unbounded_down = slope < 0
    kink_values = [value for _, value in exact]
    max_profit = None if unbounded_up else quantize_money(max(kink_values))
    max_loss = None if unbounded_down else quantize_money(max(_ZERO, -min(kink_values)))

    crossings: List[Decimal] = []
    for (x0, y0), (x1, y1) in zip(exact, exact[1:]):
        if y0 == 0:
            if x0 > 0:
                crossings.append(x0)
        elif (y0 < 0 < y1) or (y0 > 0 > y1):
            crossings.append(x0 + (x1 - x0) * (-y0) / (y1 - y0))
    last_x, last_y = exact[-1]
    if last_y == 0:
        if last_x > 0:
            crossings.append(last_x)
    elif slope != 0 and ((last_y < 0 < slope) or (last_y > 0 > slope)):
        crossings.append(last_x - last_y / slope)
    breakevens = sorted({quantize_price(value) for value in crossings})
    grid = [(spot, quantize_money(value)) for spot, value in exact] + [(tail, quantize_money(tail_value))]
    return PayoffProfile(max_profit, max_loss, breakevens, unbounded_up, unbounded_down, grid, None)


def payoff_profile(group: PositionGroup) -> PayoffProfile:
    """The group's profile under the brief's policy (module notes)."""
    if group.is_quarantined:
        return _blank(REASON_QUARANTINED)
    if not group.open_legs:
        return _blank(REASON_NO_OPEN_LEGS)
    if len(group.expiries) > 1:
        return _blank(REASON_MIXED_EXPIRIES)
    if group.structure in (grouping.STRUCTURE_CUSTOM, grouping.STRUCTURE_INVALID):
        return _blank(REASON_CUSTOM)
    return grid_profile(group.open_legs, group.net_cost)


# --------------------------------------------------------------------------------------------
# Closed forms
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _ContractLeg:
    """One contract of the pattern: the open quantity and remaining basis of every row with that symbol."""

    symbol: str
    right: str
    strike: Decimal
    expiration: datetime.date
    quantity: Decimal
    basis: Decimal
    multiplier: int

    @property
    def contracts(self) -> Decimal:
        return abs(self.quantity)

    @property
    def shares(self) -> Decimal:
        return self.contracts * self.multiplier


def _aggregate(legs: Sequence[PositionRow]) -> Tuple[Decimal, Decimal, List[_ContractLeg]]:
    """``(shares, stock basis, contract legs)`` of the open legs, rows of one contract merged."""
    shares = _ZERO
    stock_basis = _ZERO
    merged: Dict[str, List[object]] = {}
    for leg in legs:
        if not leg.is_open or leg.open_quantity == 0:
            continue
        if leg.is_option and leg.occ is not None:
            entry = merged.setdefault(leg.compact_symbol, [leg.occ, _ZERO, _ZERO, leg.multiplier])
            entry[1] = entry[1] + leg.open_quantity  # type: ignore[operator]
            entry[2] = entry[2] + leg.remaining_cost_basis  # type: ignore[operator]
        else:
            shares += leg.open_quantity
            stock_basis += leg.remaining_cost_basis
    contracts = [_ContractLeg(symbol, parsed.right, parsed.strike, parsed.expiration, quantity, basis, multiplier)  # type: ignore[union-attr]
                 for symbol, (parsed, quantity, basis, multiplier) in merged.items() if quantity != 0]
    return shares, stock_basis, contracts


def _profile(max_profit: Optional[Decimal], max_loss: Optional[Decimal], breakeven: Optional[Decimal],
             unbounded_up: bool, unbounded_down: bool) -> PayoffProfile:
    # A spot of zero or below is never a breakeven (module notes): the grid never reports one either.
    breakevens = [quantize_price(breakeven)] if breakeven is not None and breakeven > 0 else []
    return PayoffProfile(None if max_profit is None else quantize_money(max_profit),
                         None if max_loss is None else quantize_money(max(_ZERO, max_loss)),
                         breakevens, unbounded_up, unbounded_down, [], None)


def closed_form_profile(group: PositionGroup) -> Optional[PayoffProfile]:
    """The closed form of a named structure (module notes), or ``None`` when the structure has none."""
    if group.is_quarantined or not group.open_legs or len(group.expiries) > 1:
        return None
    shares, stock_basis, contracts = _aggregate(group.open_legs)
    net_cost = group.net_cost
    structure = group.structure
    if structure == grouping.STRUCTURE_LONG_STOCK and shares > 0 and not contracts:
        return _profile(None, net_cost, net_cost / shares, True, False)
    if structure == grouping.STRUCTURE_SHORT_STOCK and shares < 0 and not contracts:
        return _profile(-net_cost, None, net_cost / shares, False, True)
    if len(contracts) == 1 and shares == 0:
        leg = contracts[0]
        per_share = net_cost / leg.shares  # signed: the debit (long) or minus the credit (short) per share
        if structure == grouping.STRUCTURE_LONG_CALL:
            return _profile(None, net_cost, leg.strike + per_share, True, False)
        if structure == grouping.STRUCTURE_LONG_PUT:
            return _profile(leg.strike * leg.shares - net_cost, net_cost, leg.strike - per_share, False, False)
        if structure == grouping.STRUCTURE_SHORT_CALL:
            return _profile(-net_cost, None, leg.strike - per_share, False, True)
        if structure == grouping.STRUCTURE_CASH_SECURED_PUT:
            return _profile(-net_cost, leg.strike * leg.shares + net_cost, leg.strike + per_share, False, False)
        return None
    if structure in grouping.VERTICAL_STRUCTURES and len(contracts) == 2 and shares == 0:
        short = next((leg for leg in contracts if leg.quantity < 0), None)
        long = next((leg for leg in contracts if leg.quantity > 0), None)
        if short is None or long is None or short.shares != long.shares:
            return None
        width = abs(long.strike - short.strike) * long.shares
        per_share = net_cost / long.shares
        is_call = long.right == occ.CALL
        if net_cost < 0:
            credit = -per_share
            breakeven = short.strike + credit if is_call else short.strike - credit
            return _profile(-net_cost, width + net_cost, breakeven, False, False)
        breakeven = long.strike + per_share if is_call else long.strike - per_share
        return _profile(width - net_cost, net_cost, breakeven, False, False)
    if shares <= 0 or not contracts:
        return None
    calls = [leg for leg in contracts if leg.right == occ.CALL and leg.quantity < 0]
    puts = [leg for leg in contracts if leg.right == occ.PUT and leg.quantity > 0]
    if structure == grouping.STRUCTURE_COVERED_CALL and len(calls) == 1 and len(contracts) == 1:
        call = calls[0]
        if call.shares != shares:
            return None  # shares beyond the calls: the grid reports the unbounded upside
        breakeven = net_cost / shares
        return _profile(call.strike * shares - net_cost, net_cost, breakeven if breakeven <= call.strike else None,
                        False, False)
    if structure == grouping.STRUCTURE_PROTECTIVE_PUT and len(puts) == 1 and len(contracts) == 1:
        put = puts[0]
        if put.shares != shares:
            return None
        breakeven = net_cost / shares
        return _profile(None, net_cost - put.strike * shares, breakeven if breakeven >= put.strike else None,
                        True, False)
    if structure == grouping.STRUCTURE_COLLAR and len(calls) == 1 and len(puts) == 1 and len(contracts) == 2:
        call, put = calls[0], puts[0]
        if call.shares != shares or put.shares != shares:
            return None
        breakeven = net_cost / shares
        inside = put.strike <= breakeven <= call.strike
        return _profile(call.strike * shares - net_cost, net_cost - put.strike * shares,
                        breakeven if inside else None, False, False)
    return None


__all__ = [
    "CENT",
    "GRID_TAIL_FACTOR",
    "PRICE_QUANTUM",
    "PayoffProfile",
    "REASONS",
    "REASON_CUSTOM",
    "REASON_MIXED_EXPIRIES",
    "REASON_NO_OPEN_LEGS",
    "REASON_QUARANTINED",
    "closed_form_profile",
    "grid_profile",
    "group_payoff",
    "intrinsic",
    "leg_payoff",
    "payoff_profile",
    "quantize_money",
    "quantize_price",
]
