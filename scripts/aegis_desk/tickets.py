"""The Structuring Desk's tickets: the action plan for a group the owner holds, checked, priced and worded (PR 3b).

Designer Q's D11 under the addendum S1, S5-S9 and S11; standard library only, no wall clock (``now`` is the
:class:`aegis_desk.quote_cache.MarketData`'s), the network reached only through ``quote_cache`` (one group-scoped quotes
batch; a ROLL adds the one chain of the replacement's expiration through ``structuring.run_replacement``).
``exit_ticket.py`` calls :func:`run_ticket` and :func:`render_ticket`; nothing here prints, and nothing writes but the
report and journal helpers of :mod:`aegis_desk.structuring_text` the tool calls.

Action plans and leg orders (S7, :func:`plan_legs`)
---------------------------------------------------
One :class:`PlanLeg` per open row (a lot keeps its own cost), rows in file order within a class:

* ``CLOSE``: every open option leg, the short ones first, then the long ones; the shares stay;
* ``UNWIND_STOCK``: the short options, then the shares, then the long options (protection is closed last);
* ``MONETIZE_PUT``: the long puts alone (a protective put or a collar);
* ``LET_ASSIGN``: no leg (the short options are left to expiry; :class:`LetAssignLeg` per short option);
* ``ROLL``: the held leg :func:`replaced_row` names and the owner's :class:`Replacement` (the held leg's root and
  right, the owner's expiration and strike, as many contracts): a covered call's short call closes the old call, then
  opens the new one; a protective put's long put opens the new put, then closes the old one.

Approval and exposure (S5, S7): :func:`check_plan` runs ``aegis_desk.approval.check_legs`` over the plan from the
account's holdings of the underlying (``approval.holdings_of``: every open row of the account, whatever the group), so
every prefix state is checked. A step above the owner's acknowledged ``desk.toml`` level is REFUSED
``approval_level``; a step that leaves a short call uncovered is REFUSED ``intermediate_exposure`` whatever the level
(an invariant breach: the canonical orders above never produce one, and the tests prove both reversed orders do).
Both are decided from ``positions.csv`` before any quote is asked for.

Quotes, cash and realized (designer Q's D4 and D11)
---------------------------------------------------
A held option leg closes at its conservative fresh side (``structuring.held_leg``: a short leg's fresh ask, a long
leg's fresh bid, from any grade A-E; grade E adds ``structuring.GRADE_E_NOTE``); shares close at the stock's fresh bid
under the same rule; a new leg is priced through ``structuring.run_replacement`` (one code path with the roll
analysis) and needs a usable grade with both sides fresh (else UNAVAILABLE :data:`CAUSE_REPLACEMENT_UNPRICED`). Cash per
step (:func:`step_cash`): ``-`` the ask to close a short or open a long, ``+`` the bid to close a long, close shares or
open a short, times 100 per contract and 1 per share, at the conservative side and at the mid (``None`` when a mid
is missing); :func:`cash_summary` sums them and names the most cash the steps need at once (``max(0, -min prefix
sum)``). :func:`realized_estimate` is ``pnl.realized_for_row``'s arithmetic on the open quantity at either price.

LET_ASSIGN and MONETIZE_PUT: :func:`let_assign_leg` reads the moneyness at the spot mid by the one-cent rule
(``expiry.in_the_money``), the act-by of the alternative (closing the short leg, which only the session allows: the
last trading day's close, 16:00 ET or 13:00 ET on an early close, or ``desk.toml``'s broker cutoff when earlier; the
broker's exercise cutoff itself stays in the OCC line, reviews L1-2 and L2-5), the
cash if assigned (``+/- strike x 100 x contracts``), the realized amount if assigned (:func:`assignment_realized`: the
premium kept, plus for a call the shares delivered FIFO at the strike) and, for a call, the dividend window;
:func:`put_moneyness` the put's moneyness and the intrinsic and time value of its bid. A ticket is computed only
while the session trades (S5: else UNAVAILABLE ``market_closed``); it is valid until ``min(now + 30 min, the close)``
(``structuring.valid_until``) and named ``TK-<trade_id>-<ACTION>-<YYYYMMDDTHHMMSSZ>`` (S9).

S6: a ticket carries no positions.csv line; after a fill Aegis Position Control proposes them.

Text (designer C's D6 with S11): :func:`ticket_lines` builds every line with a ``structuring_text.Writer`` and B1's
builders (``ticket_header``, ``leg_line``, ``share_leg_line``, ``quote_segment``, ``cash_line``, ``realized_line``,
``replacement_lines``, ``let_assign_line``, ``validity_line``, the fixed lines) and F1's approval lines;
:func:`render_ticket` closes it with the structuring footer and lints it (``structuring_text.finish``).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import approval, expiry, grouping, levels, market_clock, payoff, structuring, structuring_text
from .grouping import PositionGroup
from .positions_csv import PositionRow
from .quote_cache import MarketData
from .quote_qc import QuoteQC
from .structuring_config import StructuringConfig

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

VERDICT_TICKET: str = "TICKET"
VERDICT_NOT_APPLICABLE: str = structuring.VERDICT_NOT_APPLICABLE
VERDICT_UNAVAILABLE: str = structuring.VERDICT_UNAVAILABLE
VERDICT_REFUSED: str = "REFUSED"
VERDICTS: Tuple[str, ...] = (VERDICT_TICKET, VERDICT_NOT_APPLICABLE, VERDICT_UNAVAILABLE, VERDICT_REFUSED)

REFUSED_APPROVAL: str = "approval_level"
REFUSED_EXPOSURE: str = "intermediate_exposure"
REFUSED_CODES: Tuple[str, ...] = (REFUSED_APPROVAL, REFUSED_EXPOSURE)

CAUSE_REPLACEMENT_UNPRICED: str = "replacement_unpriced"
"""The named replacement is listed but its quote cannot be priced (``structuring.UNPRICED_CODES``): UNAVAILABLE."""

INTENT_CLOSE_SHORT: str = "close_short"
INTENT_CLOSE_LONG: str = "close_long"
INTENT_CLOSE_SHARES: str = "close_shares"
INTENT_OPEN_SHORT: str = "open_short"
INTENT_OPEN_LONG: str = "open_long"
INTENTS: Tuple[str, ...] = (INTENT_CLOSE_SHORT, INTENT_CLOSE_LONG, INTENT_CLOSE_SHARES, INTENT_OPEN_SHORT,
                            INTENT_OPEN_LONG)
_INTENT_PARTS: Dict[str, Tuple[str, str, int]] = {
    INTENT_CLOSE_SHORT: (approval.EFFECT_CLOSE, approval.SIDE_SHORT, -1),
    INTENT_CLOSE_LONG: (approval.EFFECT_CLOSE, approval.SIDE_LONG, 1),
    INTENT_CLOSE_SHARES: (approval.EFFECT_CLOSE, approval.SIDE_LONG, 1),
    INTENT_OPEN_SHORT: (approval.EFFECT_OPEN, approval.SIDE_SHORT, 1),
    INTENT_OPEN_LONG: (approval.EFFECT_OPEN, approval.SIDE_LONG, -1),
}
"""``(effect, side, cash sign)``: a leg that pays (``-1``) takes the ask, one that receives (``+1``) the bid."""

UNAVAILABLE_WORDS: Dict[str, str] = {
    structuring.CAUSE_GROUP_QUARANTINED: "the group is quarantined in positions.csv (positions_check.py shows why)",
    structuring.CAUSE_POSITIONS_QUARANTINED: structuring._UNAVAILABLE_WORDS[structuring.CAUSE_POSITIONS_QUARANTINED],
    structuring.CAUSE_SPOT_UNUSABLE: "no usable quote of the underlying",
    structuring.CAUSE_HELD_LEG_UNUSABLE: "no fresh conservative side for a leg the ticket closes",
    structuring.CAUSE_MARKET_CLOSED: structuring.TICKET_CLOSED_WORDS,
}
"""The reason words of the UNAVAILABLE causes this module decides (a ROLL's own come from ``run_replacement``)."""
NO_MID_WORDS: str = structuring_text.NO_MID_WORDS
SESSION_CLOSE_WORDS: str = "a short option left to expiry can be closed only while the session trades"
"""Why a LET_ASSIGN ticket's act-by is the session close: the alternative to assignment is closing the leg."""
SHARE_TICK: Decimal = levels.STOCK_TICK

_ZERO = Decimal(0)
_CENT = Decimal("0.01")
_CONTEXT = Context(prec=structuring.DECIMAL_DIGITS, rounding=ROUND_HALF_EVEN)


class TicketUsageError(ValueError):
    """A ROLL replacement the command line cannot name (usage, exit 2 in the tool): ``--new-expiration`` and
    ``--new-strike`` missing or given with another action, or an expiration not later than the held leg's."""


# --------------------------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Replacement:
    """The contract a ROLL opens: the held leg's root and right, the owner's expiration and strike, as many
    contracts as the held leg."""

    root: str
    right: str
    strike: Decimal
    expiration: datetime.date
    contracts: int


@dataclass(frozen=True)
class PlanLeg:
    """One step of an action plan, in the order the owner enters them: ``intent`` (:data:`INTENTS`), ``instrument``
    (``approval.INSTRUMENTS``), ``quantity`` (whole contracts, or the exact open shares: a ``Decimal`` when the
    holding is fractional) and the held ``row_number`` (``None`` for the new leg of a ROLL). ``symbol`` is the
    contract's symbol for the JSON only (the text prints the leg grammar)."""

    step: int
    intent: str
    instrument: str
    quantity: Any
    row_number: Optional[int]
    symbol: Optional[str]
    root: str
    right: Optional[str]
    strike: Optional[Decimal]
    expiration: Optional[datetime.date]
    multiplier: int

    @property
    def effect(self) -> str:
        return _INTENT_PARTS[self.intent][0]

    @property
    def side(self) -> str:
        return _INTENT_PARTS[self.intent][1]

    @property
    def approval_leg(self) -> approval.Leg:
        return approval.Leg(self.effect, self.side, self.instrument, self.quantity)

    def as_dict(self) -> Dict[str, Any]:
        return {"step": self.step, "intent": self.intent, "effect": self.effect, "side": self.side,
                "instrument": self.instrument, "quantity": self.quantity, "row_number": self.row_number,
                "contract": self.symbol, "root": self.root, "right": self.right, "strike": self.strike,
                "expiration": self.expiration, "multiplier": self.multiplier}


def _whole(value: Decimal, row: PositionRow) -> int:
    quantity = abs(value)
    if quantity != quantity.to_integral_value() or quantity <= 0:
        raise ValueError("row %d holds %s: a ticket counts whole contracts" % (row.row_number, value))
    return int(quantity)


def _shares_of(row: PositionRow) -> Any:
    """The exact open share quantity a ticket closes (BD4: positions.csv accepts fractional shares): an ``int`` when
    whole, else the exact ``Decimal``."""
    quantity = abs(row.open_quantity)
    if quantity <= 0:
        raise ValueError("row %d holds no shares to close" % row.row_number)
    return int(quantity) if quantity == quantity.to_integral_value() else quantity


def _row_leg(step: int, intent: str, row: PositionRow) -> PlanLeg:
    if row.is_stock_like:
        return PlanLeg(step, intent, approval.INSTRUMENT_SHARES, _shares_of(row), row.row_number,
                       row.compact_symbol, row.underlying, None, None, None, row.multiplier)
    assert row.occ is not None
    instrument = approval.INSTRUMENT_CALL if row.is_call else approval.INSTRUMENT_PUT
    return PlanLeg(step, intent, instrument, _whole(row.open_quantity, row), row.row_number, row.occ.compact,
                   row.occ.root, row.occ.right, expiry.plain_decimal(row.occ.strike), row.occ.expiration, row.multiplier)


def replaced_row(group: PositionGroup) -> Optional[PositionRow]:
    """The held leg a ROLL replaces: the short call of a covered call or the long put of a protective put, when the
    group holds it on exactly one row; else ``None``."""
    if group.structure == grouping.STRUCTURE_COVERED_CALL:
        rows = [row for row in group.open_legs if row.is_option and row.is_call and row.is_short]
    elif group.structure == grouping.STRUCTURE_PROTECTIVE_PUT:
        rows = [row for row in group.open_legs if row.is_option and row.is_put and row.is_long]
    else:
        return None
    return rows[0] if len(rows) == 1 else None


def plan_legs(group: PositionGroup, action: str, replacement: Optional[Replacement] = None) -> List[PlanLeg]:
    """The legs of ``action`` on ``group`` in the S7 order (module notes); ``ValueError`` for an unknown action, a ROLL
    without its replacement or held leg, short shares or a quantity that is not whole."""
    if action not in structuring.ACTIONS:
        raise ValueError("action must be one of %s, got %r" % (", ".join(structuring.ACTIONS), action))
    rows = sorted(group.open_legs, key=lambda row: row.row_number)
    if any(row.is_stock_like and row.is_short for row in rows):
        raise ValueError("%s holds short shares: outside the desk's tickets" % (group.trade_id or group.group_id))
    shorts = [row for row in rows if row.is_option and row.is_short]
    longs = [row for row in rows if row.is_option and row.is_long]
    shares = [row for row in rows if row.is_stock_like]
    ordered: List[Tuple[str, PositionRow]] = []
    if action == structuring.ACTION_CLOSE:
        ordered = [(INTENT_CLOSE_SHORT, row) for row in shorts] + [(INTENT_CLOSE_LONG, row) for row in longs]
    elif action == structuring.ACTION_UNWIND_STOCK:
        ordered = ([(INTENT_CLOSE_SHORT, row) for row in shorts] + [(INTENT_CLOSE_SHARES, row) for row in shares]
                   + [(INTENT_CLOSE_LONG, row) for row in longs])
    elif action == structuring.ACTION_MONETIZE_PUT:
        ordered = [(INTENT_CLOSE_LONG, row) for row in longs if row.is_put]
    elif action == structuring.ACTION_ROLL:
        held = replaced_row(group)
        if replacement is None or held is None:
            raise ValueError("a ROLL needs the held leg it replaces and the replacement")
        if held.is_short:
            new = PlanLeg(2, INTENT_OPEN_SHORT, approval.INSTRUMENT_CALL, replacement.contracts, None, None,
                          replacement.root, replacement.right, replacement.strike, replacement.expiration, held.multiplier)
            return [_row_leg(1, INTENT_CLOSE_SHORT, held), new]
        new = PlanLeg(1, INTENT_OPEN_LONG, approval.INSTRUMENT_PUT, replacement.contracts, None, None, replacement.root,
                      replacement.right, replacement.strike, replacement.expiration, held.multiplier)
        return [new, _row_leg(2, INTENT_CLOSE_LONG, held)]
    return [_row_leg(index, intent, row) for index, (intent, row) in enumerate(ordered, 1)]


def approval_legs(legs: Sequence[PlanLeg]) -> List[approval.Leg]:
    return [leg.approval_leg for leg in legs]


def check_plan(holdings: approval.Holdings, legs: Sequence[PlanLeg], owner_level: int) -> approval.ApprovalResult:
    """``approval.check_legs`` over every prefix state of the plan (``ValueError`` for an empty plan)."""
    if not legs:
        raise ValueError("a plan with no leg has nothing to check")
    return approval.check_legs(holdings, approval_legs(legs), owner_level)


def _holdings_dict(holdings: approval.Holdings) -> Dict[str, int]:
    return {"shares": holdings.shares, "long_calls": holdings.long_calls, "short_calls": holdings.short_calls,
            "long_puts": holdings.long_puts, "short_puts": holdings.short_puts}


def step_records(result: approval.ApprovalResult) -> List[Dict[str, Any]]:
    """The state after every step: ``{step, kind, level, words, after, uncovered_calls}``."""
    return [{"step": step.index, "kind": step.kind, "level": step.level, "words": step.words,
             "after": _holdings_dict(step.after), "uncovered_calls": step.after.uncovered_calls}
            for step in result.steps]


def _approval_dict(result: Optional[approval.ApprovalResult]) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    refusal = None
    if result.refusal is not None:
        refusal = {"step": result.refusal.index, "kind": result.refusal.kind, "level": result.refusal.level,
                   "words": result.refusal.words}
    return {"check": approval.CHECK_NAME, "ok": result.ok, "required_level": result.required_level,
            "owner_level": result.owner_level, "refusal": refusal, "exposure_step": result.exposure_step}


# --------------------------------------------------------------------------------------------
# Quotes, cash, realized
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LegQuote:
    """A leg's quote as the ticket prints it, and the conservative ``side`` it is priced at (``conservative``)."""

    bid: Optional[Decimal]
    ask: Optional[Decimal]
    mid: Optional[Decimal]
    spread_pct: Optional[Decimal]
    grade: str
    quote_status: Optional[str]
    quote_age_min: Optional[int]
    as_of: Optional[datetime.datetime]
    side: str
    conservative: Decimal
    note: Optional[str] = None


def _age(qc: QuoteQC) -> Optional[int]:
    ages = [age for age in (qc.age_min_bid, qc.age_min_ask) if age is not None]
    return max(ages) if ages else None


def quote_of_held(held: structuring.HeldLeg) -> Optional[LegQuote]:
    """The held option leg's quote at its conservative side (``None`` when that side is not fresh)."""
    if held.close_price is None or held.grade is None:
        return None
    return LegQuote(held.bid, held.ask, held.mid, held.spread_pct, held.grade, held.quote_status, held.quote_age_min,
                    held.as_of, held.close_side, held.close_price, held.note)


def quote_of_shares(qc: Optional[QuoteQC]) -> Optional[LegQuote]:
    """Shares close at the stock's fresh bid from any grade A-E (designer Q's D4 held-leg rule); ``None`` without one."""
    if qc is None or qc.grade == "F" or qc.mark_liq_long is None or qc.mark_liq_long <= 0:
        return None
    note = structuring.GRADE_E_NOTE if qc.grade == "E" else None
    return LegQuote(structuring.price_value(qc.bid), structuring.price_value(qc.ask),
                    structuring.price_value(qc.mark_mid), qc.spread_pct, qc.grade, qc.status, _age(qc), qc.as_of, "bid",
                    structuring.price_value(qc.mark_liq_long), note)


def quote_of_new(leg: structuring.PricedLeg, intent: str) -> LegQuote:
    """A priced new leg: a short opens at its fresh bid, a long at its fresh ask (``ValueError`` when unpriced)."""
    if leg.unpriced is not None or leg.bid is None or leg.ask is None or leg.grade is None:
        raise ValueError("the new leg %s is not priced" % leg.occ)
    side = "bid" if _INTENT_PARTS[intent][2] > 0 else "ask"
    price = leg.bid if side == "bid" else leg.ask
    return LegQuote(leg.bid, leg.ask, leg.mid, leg.spread_pct, leg.grade, leg.quote_status, leg.quote_age_min,
                    leg.as_of, side, price)


def step_cash(intent: str, quantity: int, multiplier: int, conservative: Decimal,
              mid: Optional[Decimal]) -> Tuple[Decimal, Optional[Decimal]]:
    """``(cash at the conservative side, cash at the mid)`` of one step, to the cent (module notes)."""
    if intent not in _INTENT_PARTS:
        raise ValueError("intent must be one of %s, got %r" % (", ".join(INTENTS), intent))
    sign = _INTENT_PARTS[intent][2]
    with localcontext(_CONTEXT):
        size = Decimal(sign * quantity * multiplier)
        return (payoff.quantize_money(size * conservative),
                None if mid is None else payoff.quantize_money(size * mid))


@dataclass(frozen=True)
class Cash:
    """The steps' cash: the totals at the conservative side and at the mid (``None`` when a mid is missing), the
    running total after each step (``prefix``) and the most cash the steps need at once."""

    net_conservative: Decimal
    net_mid: Optional[Decimal]
    peak_needed: Decimal
    prefix: Tuple[Decimal, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {"net_conservative": self.net_conservative, "net_mid": self.net_mid, "peak_needed": self.peak_needed,
                "prefix": list(self.prefix)}


def cash_summary(steps: Sequence[Tuple[Decimal, Optional[Decimal]]]) -> Cash:
    """:class:`Cash` of ``(conservative, mid)`` per step, in order (``ValueError`` for no step)."""
    if not steps:
        raise ValueError("a ticket with no leg has no cash")
    running = _ZERO
    prefix: List[Decimal] = []
    mid_total: Optional[Decimal] = _ZERO
    for conservative, mid in steps:
        running += conservative
        prefix.append(payoff.quantize_money(running))
        mid_total = None if mid is None or mid_total is None else mid_total + mid
    peak = payoff.quantize_money(max(_ZERO, -min(prefix)))
    return Cash(payoff.quantize_money(running), None if mid_total is None else payoff.quantize_money(mid_total), peak,
                tuple(prefix))


@dataclass(frozen=True)
class RealizedLeg:
    """The realized P/L estimate of one closing leg at the conservative side and at the mid (``None`` without one)."""

    row_number: int
    conservative: Decimal
    mid: Optional[Decimal]

    def as_dict(self) -> Dict[str, Any]:
        return {"row_number": self.row_number, "conservative": self.conservative, "mid": self.mid}


def _realized(row: PositionRow, price: Decimal) -> Decimal:
    with localcontext(_CONTEXT):
        return payoff.quantize_money(row.sign * abs(row.open_quantity) * row.multiplier * price - row.remaining_cost_basis)


def realized_estimate(row: PositionRow, conservative: Decimal, mid: Optional[Decimal]) -> RealizedLeg:
    """``sign x open quantity x multiplier x price - remaining cost basis`` at both prices (``pnl.realized_for_row``'s
    arithmetic for the whole open quantity)."""
    return RealizedLeg(row.row_number, _realized(row, conservative), None if mid is None else _realized(row, mid))


def assignment_realized(row: PositionRow, group: PositionGroup) -> Optional[Decimal]:
    """The realized P/L if the short option ``row`` is assigned at its strike: the premium kept (the option closed at
    0), plus for a call the shares delivered at the strike, the group's long lots taken FIFO by their opened date;
    ``None`` when the group holds fewer shares than the call delivers."""
    if row.occ is None or not row.is_short:
        raise ValueError("row %d is not a short option" % row.row_number)
    strike = expiry.plain_decimal(row.occ.strike)
    with localcontext(_CONTEXT):
        total = _ZERO - row.remaining_cost_basis
        if row.is_put:
            return payoff.quantize_money(total)
        remaining = abs(row.open_quantity) * row.multiplier
        lots = sorted((leg for leg in group.open_legs if leg.is_stock_like and leg.is_long),
                      key=lambda leg: (leg.opened or datetime.date.min, leg.row_number))
        if sum((leg.open_quantity for leg in lots), _ZERO) < remaining:
            return None
        for lot in lots:
            if remaining <= 0:
                break
            taken = min(remaining, lot.open_quantity)
            total += taken * strike - lot.remaining_cost_basis * taken / lot.open_quantity
            remaining -= taken
        return payoff.quantize_money(total)


@dataclass(frozen=True)
class TicketLeg:
    """A planned leg with its quote, its cash and the tick of the owner's limit (display only)."""

    plan: PlanLeg
    quote: LegQuote
    cash_conservative: Decimal
    cash_mid: Optional[Decimal]
    tick: Decimal

    def as_dict(self) -> Dict[str, Any]:
        out = self.plan.as_dict()
        quote = self.quote
        out.update({"bid": quote.bid, "ask": quote.ask, "mid": quote.mid, "spread_pct": quote.spread_pct,
                    "grade": quote.grade, "quote_status": quote.quote_status, "quote_age_min": quote.quote_age_min,
                    "as_of": quote.as_of, "conservative_side": quote.side, "conservative": quote.conservative,
                    "note": quote.note, "cash_conservative": self.cash_conservative, "cash_mid": self.cash_mid,
                    "tick": self.tick})
        return out


def _tick(plan: PlanLeg, price: Decimal, levels_config: Any) -> Decimal:
    if plan.instrument == approval.INSTRUMENT_SHARES:
        return SHARE_TICK
    return levels.option_tick(price, levels.tick_class_for(plan.root, levels_config))


# --------------------------------------------------------------------------------------------
# LET_ASSIGN and MONETIZE_PUT
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LetAssignLeg:
    """A short option left to expiry: its moneyness now at the spot mid (the one-cent rule), the act-by, the shares
    and cash an assignment moves and the realized amount it gives; ``dividend`` is the call's dividend window."""

    row_number: int
    symbol: str
    right: str
    strike: Decimal
    expiration: datetime.date
    last_trading_day: datetime.date
    contracts: int
    shares: int
    spot_mid: Decimal
    itm: bool
    act_by: expiry.ActBy
    act_by_text: str
    cash_if_assigned: Decimal
    realized_if_assigned: Optional[Decimal]
    dividend: Optional[expiry.DividendWindow]

    def as_dict(self) -> Dict[str, Any]:
        dividend = None
        if self.dividend is not None:
            window = self.dividend
            dividend = {"known": window.known, "applicable": window.applicable, "status": window.status,
                        "ex_date": window.ex_date, "amount": window.amount, "cum_day": window.cum_day,
                        "reason": window.reason or None}
        return {"row_number": self.row_number, "contract": self.symbol, "right": self.right, "strike": self.strike,
                "expiration": self.expiration, "last_trading_day": self.last_trading_day, "contracts": self.contracts,
                "shares": self.shares, "spot_mid": self.spot_mid, "itm": self.itm, "act_by_utc": self.act_by.utc,
                "act_by_et": self.act_by.et_text, "act_by_passed": self.act_by.passed, "act_by_text": self.act_by_text,
                "cash_if_assigned": self.cash_if_assigned, "realized_if_assigned": self.realized_if_assigned,
                "dividend": dividend}


def let_assign_leg(row: PositionRow, group: PositionGroup, spot_mid: Decimal, *, now: datetime.datetime,
                   today: datetime.date, cutoff_et: datetime.time, calendar: Any,
                   extra_holidays: Mapping[datetime.date, str]) -> LetAssignLeg:
    """The :class:`LetAssignLeg` of the short option ``row`` (module notes)."""
    if row.occ is None or not row.is_short:
        raise ValueError("row %d is not a short option" % row.row_number)
    strike = expiry.plain_decimal(row.occ.strike)
    last_day = expiry.leg_last_trading_day(row, calendar)
    # The alternative to assignment for a short option is closing it, which only the session allows: the last
    # trading day's close (16:00 ET, 13:00 ET on an early close), or desk.toml's broker cutoff when that is earlier.
    # The broker's exercise cutoff itself governs a long holder's instruction (the OCC line keeps it; reviews L1-2
    # and L2-5). expiry.leg_act_by is the one rule, shared with the expiry sheet (FAI-689); the ticket prints the bare
    # act_by_text followed by its own SESSION_CLOSE_WORDS.
    act = expiry.leg_act_by(row, last_day, cutoff_et, now, extra_holidays)
    text = expiry.act_by_text(act, today, market_clock.next_trading_day(today, extra_holidays))
    contracts = _whole(row.open_quantity, row)
    shares = contracts * row.multiplier
    with localcontext(_CONTEXT):
        cash = payoff.quantize_money(strike * shares * (1 if row.is_call else -1))
    dividend = None
    if row.is_call:
        dividend = expiry.dividend_window(row.underlying, last_day, calendar, today, extra_holidays)
    return LetAssignLeg(row.row_number, row.occ.compact, row.occ.right, strike, row.occ.expiration, last_day, contracts,
                        shares, spot_mid, expiry.in_the_money(row.occ.right, spot_mid, strike), act, text, cash,
                        assignment_realized(row, group), dividend)


@dataclass(frozen=True)
class PutMoneyness:
    """The long put MONETIZE_PUT closes: in the money by the one-cent rule at the spot mid, and its bid split into
    intrinsic value (``max(0, K - S)``) and time value (the rest; negative when the bid is below intrinsic)."""

    row_number: int
    strike: Decimal
    expiration: datetime.date
    spot_mid: Decimal
    bid: Decimal
    itm: bool
    intrinsic: Decimal
    time_value: Decimal

    def as_dict(self) -> Dict[str, Any]:
        return {"row_number": self.row_number, "strike": self.strike, "expiration": self.expiration,
                "spot_mid": self.spot_mid, "bid": self.bid, "itm": self.itm, "intrinsic": self.intrinsic,
                "time_value": self.time_value}


def put_moneyness(row: PositionRow, bid: Decimal, spot_mid: Decimal) -> PutMoneyness:
    if row.occ is None or not row.is_put:
        raise ValueError("row %d is not a put" % row.row_number)
    strike = expiry.plain_decimal(row.occ.strike)
    with localcontext(_CONTEXT):
        intrinsic = max(_ZERO, strike - spot_mid)
        return PutMoneyness(row.row_number, strike, row.occ.expiration, spot_mid, bid,
                            expiry.in_the_money(row.occ.right, spot_mid, strike), intrinsic, bid - intrinsic)


# --------------------------------------------------------------------------------------------
# The ticket
# --------------------------------------------------------------------------------------------

TICKET_KEYS: Tuple[str, ...] = (
    "ticket_id", "trade_id", "group_id", "underlying", "structure", "account", "account_type", "action", "verdict",
    "cause", "reason", "refused_code", "computed_utc", "valid_until", "valid_full", "session", "owner_level",
    "criteria", "legs", "approval", "steps", "cash", "realized", "let_assign", "puts", "replacement", "spot",
    "model_note", "requests", "warnings",
)
"""The keys of :meth:`Ticket.as_dict`, the payload body ``exit_ticket.py`` extends with its text and files."""


@dataclass
class Ticket:
    """What :func:`run_ticket` answered (module notes): TICKET, NOT_APPLICABLE, UNAVAILABLE or REFUSED, with its
    cause or refusal code and the desk's reason words, and every number the text prints."""

    action: str
    trade_id: str
    criteria: StructuringConfig
    now: datetime.datetime
    verdict: str = VERDICT_UNAVAILABLE
    cause: Optional[str] = None
    reason: Optional[str] = None
    refused_code: Optional[str] = None
    target: Optional[structuring.Target] = None
    group: Optional[PositionGroup] = None
    plan: List[PlanLeg] = field(default_factory=list)
    legs: List[TicketLeg] = field(default_factory=list)
    holdings: Optional[approval.Holdings] = None
    approval: Optional[approval.ApprovalResult] = None
    owner_level: Optional[int] = None
    cash: Optional[Cash] = None
    realized: List[RealizedLeg] = field(default_factory=list)
    let_assign: List[LetAssignLeg] = field(default_factory=list)
    puts: List[PutMoneyness] = field(default_factory=list)
    replacement: Optional[structuring.Answer] = None
    spot: Optional[QuoteQC] = None
    valid_until: Optional[datetime.datetime] = None
    valid_full: Optional[bool] = None
    ticket_id: Optional[str] = None
    requests: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def account_type(self) -> Optional[str]:
        if self.target is None or self.group is None:
            return None
        for entry in self.target.market.cfg.owner.accounts:
            if entry.name == self.group.account:
                return entry.type
        return None

    @property
    def section_name(self) -> Optional[str]:
        """The ``structuring.toml`` section a ROLL is judged under (``roll`` or ``protective_put``)."""
        if self.action != structuring.ACTION_ROLL or self.group is None:
            return None
        return "protective_put" if self.group.structure == grouping.STRUCTURE_PROTECTIVE_PUT else "roll"

    def as_dict(self) -> Dict[str, Any]:
        """The payload body (:data:`TICKET_KEYS`; Decimals, dates and instants for ``contract.json_dumps``)."""
        group, target = self.group, self.target
        session = None
        if target is not None:
            session = {"session": target.session.session, "et_date": target.session.et_date,
                       "close_utc": target.session.close_utc, "live": target.session.is_live}
        spot = None
        if self.spot is not None:
            spot = {"symbol": None if group is None else group.underlying, "bid": structuring.price_value(self.spot.bid),
                    "ask": structuring.price_value(self.spot.ask), "mid": structuring.price_value(self.spot.mark_mid),
                    "grade": self.spot.grade, "as_of": self.spot.as_of}
        replacement = None
        answer = self.replacement
        if answer is not None and answer.candidate is not None:
            replacement = {"kind": answer.kind, "section": answer.section_name,
                           "objective": None if answer.objective is None else structuring.objective_dict(answer.objective),
                           "candidate": answer.candidate.as_dict(), "held": None if answer.held is None else answer.held.as_dict(),
                           "before": None if answer.before is None else answer.before.as_dict()}
        return {
            "ticket_id": self.ticket_id, "trade_id": self.trade_id,
            "group_id": None if group is None else group.group_id,
            "underlying": None if group is None else group.underlying,
            "structure": None if group is None else group.structure,
            "account": None if group is None else group.account, "account_type": self.account_type,
            "action": self.action, "verdict": self.verdict, "cause": self.cause, "reason": self.reason,
            "refused_code": self.refused_code, "computed_utc": self.now, "valid_until": self.valid_until,
            "valid_full": self.valid_full, "session": session, "owner_level": self.owner_level,
            "criteria": self.criteria.as_dict(), "legs": [leg.as_dict() for leg in self.legs],
            "approval": _approval_dict(self.approval),
            "steps": [] if self.approval is None else step_records(self.approval),
            "cash": None if self.cash is None else self.cash.as_dict(),
            "realized": [item.as_dict() for item in self.realized],
            "let_assign": [item.as_dict() for item in self.let_assign], "puts": [item.as_dict() for item in self.puts],
            "replacement": replacement, "spot": spot,
            "model_note": structuring.MODEL_NOTE if replacement is not None else None,
            "requests": dict(self.requests), "warnings": list(self.warnings),
        }


def _close(ticket: Ticket, market: MarketData, verdict: str, cause: Optional[str] = None, reason: Optional[str] = None,
           refused: Optional[str] = None) -> Ticket:
    ticket.verdict, ticket.cause, ticket.reason, ticket.refused_code = verdict, cause, reason, refused
    if verdict == VERDICT_UNAVAILABLE and reason is None and cause in UNAVAILABLE_WORDS:
        ticket.reason = UNAVAILABLE_WORDS[cause]
    ticket.requests = structuring.requests_block(market)
    return ticket


def _check_flags(action: str, new_expiration: Optional[datetime.date], new_strike: Optional[Decimal]) -> None:
    if action not in structuring.ACTIONS:
        raise ValueError("action must be one of %s, got %r" % (", ".join(structuring.ACTIONS), action))
    given = (new_expiration is not None, new_strike is not None)
    if action == structuring.ACTION_ROLL and given != (True, True):
        raise TicketUsageError("--action ROLL needs both --new-expiration and --new-strike")
    if action != structuring.ACTION_ROLL and any(given):
        raise TicketUsageError("--new-expiration and --new-strike apply to --action ROLL only")
    if new_strike is not None and not (new_strike.is_finite() and new_strike > 0):
        raise TicketUsageError("--new-strike must be a positive number, got %s" % new_strike)


def run_ticket(market: MarketData, trade_id: str, action: str, criteria: StructuringConfig, *,
               new_expiration: Optional[datetime.date] = None, new_strike: Optional[Decimal] = None) -> Ticket:
    """The ticket of ``action`` for ``trade_id`` (module notes): ``structuring.StructuringError`` for an unknown trade or
    a desk file that cannot be used, :class:`TicketUsageError` for a replacement the command line cannot name."""
    _check_flags(action, new_expiration, new_strike)
    ticket = Ticket(action=action, trade_id=trade_id, criteria=criteria, now=market.now,
                    owner_level=market.cfg.owner.approval_level)
    target = structuring.load_target(market, trade_id)
    ticket.target, ticket.group = target, target.group
    if target.unusable or target.group is None:
        return _close(ticket, market, VERDICT_UNAVAILABLE, structuring.CAUSE_GROUP_QUARANTINED)
    if target.others_quarantined:  # the account's coverage cannot be counted (reviews L2-1, L3-1)
        return _close(ticket, market, VERDICT_UNAVAILABLE, structuring.CAUSE_POSITIONS_QUARANTINED)
    group = target.group
    applicability = structuring.ticket_applicability(group, action, criteria)
    if applicability is not None:
        return _close(ticket, market, applicability.verdict, applicability.code, applicability.reason)
    replacement: Optional[Replacement] = None
    if action == structuring.ACTION_ROLL:
        held = replaced_row(group)
        if held is None or held.occ is None:
            words = "long put" if group.structure == grouping.STRUCTURE_PROTECTIVE_PUT else "short call"
            return _close(ticket, market, VERDICT_NOT_APPLICABLE, structuring.NA_STRUCTURE,
                          structuring.NA_TWO_ROWS_WORDS % (trade_id, words))
        assert new_expiration is not None and new_strike is not None
        if new_expiration <= held.occ.expiration:
            raise TicketUsageError("--new-expiration %s must be later than the held leg's expiration %s"
                                   % (new_expiration.isoformat(), held.occ.expiration.isoformat()))
        try:
            contracts = _whole(held.open_quantity, held)
        except ValueError as exc:
            raise structuring.StructuringError(structuring.ERROR_POSITIONS_INVALID, str(exc)) from None
        replacement = Replacement(held.occ.root, held.occ.right, expiry.plain_decimal(new_strike), new_expiration,
                                  contracts)
    try:
        ticket.plan = plan_legs(group, action, replacement)
        if ticket.plan:
            ticket.holdings = approval.holdings_of(target.positions.rows, group.account, group.underlying)
    except ValueError as exc:
        raise structuring.StructuringError(structuring.ERROR_POSITIONS_INVALID, str(exc)) from None
    if ticket.plan:
        assert ticket.holdings is not None and ticket.owner_level is not None
        result = check_plan(ticket.holdings, ticket.plan, ticket.owner_level)
        ticket.approval = result
        if not result.ok:
            return _close(ticket, market, VERDICT_REFUSED, refused=REFUSED_APPROVAL)
        if result.exposure_step is not None:
            return _close(ticket, market, VERDICT_REFUSED, refused=REFUSED_EXPOSURE)
    if not structuring.ticket_session_live(target.session):
        return _close(ticket, market, VERDICT_UNAVAILABLE, structuring.CAUSE_MARKET_CLOSED)
    if action == structuring.ACTION_ROLL:
        assert replacement is not None
        return _roll(ticket, market, replacement)
    return _held_ticket(ticket, market)


def _finish(ticket: Ticket, market: MarketData) -> Ticket:
    if ticket.legs:
        ticket.cash = cash_summary([(leg.cash_conservative, leg.cash_mid) for leg in ticket.legs])
    assert ticket.target is not None
    ticket.valid_until, ticket.valid_full = structuring.valid_until(ticket.now, ticket.target.session)
    ticket.ticket_id = structuring_text.ticket_stem(ticket.trade_id, ticket.action, ticket.now)
    return _close(ticket, market, VERDICT_TICKET)


def _priced(ticket: Ticket, plan: PlanLeg, quote: LegQuote, market: MarketData) -> TicketLeg:
    conservative, mid = step_cash(plan.intent, plan.quantity, plan.multiplier, quote.conservative, quote.mid)
    return TicketLeg(plan, quote, conservative, mid, _tick(plan, quote.conservative, market.cfg.levels))


def _held_ticket(ticket: Ticket, market: MarketData) -> Ticket:
    target, group = ticket.target, ticket.group
    assert target is not None and group is not None
    book = structuring.load_group_book(target)
    if book.cache is not None:
        ticket.warnings.extend("quote cache: %s" % warning for warning in book.cache.warnings)
    if target.calendar is not None:
        ticket.warnings.extend(target.calendar.warnings)
    rows = {row.row_number: row for row in group.open_legs}
    for plan in ticket.plan:
        assert plan.row_number is not None
        row = rows[plan.row_number]
        qc = book.verdicts.get(row.compact_symbol)
        if plan.instrument == approval.INSTRUMENT_SHARES:
            quote = quote_of_shares(qc)
        else:
            quote = quote_of_held(structuring.held_leg(row, qc, book.leg_records.get(row.row_number),
                                                       target.extra_holidays))
        if quote is None:
            return _close(ticket, market, VERDICT_UNAVAILABLE, structuring.CAUSE_HELD_LEG_UNUSABLE)
        ticket.legs.append(_priced(ticket, plan, quote, market))
        ticket.realized.append(realized_estimate(row, quote.conservative, quote.mid))
    if ticket.action in (structuring.ACTION_LET_ASSIGN, structuring.ACTION_MONETIZE_PUT):
        spot = book.spot
        if spot is None or not spot.usable_for_rules or spot.mark_mid is None:
            return _close(ticket, market, VERDICT_UNAVAILABLE, structuring.CAUSE_SPOT_UNUSABLE)
        ticket.spot = spot
        spot_mid = structuring.price_value(spot.mark_mid)
        assert spot_mid is not None
        if ticket.action == structuring.ACTION_LET_ASSIGN:
            cutoff = market.cfg.owner.broker_exercise_cutoff_et
            for row in sorted(group.open_legs, key=lambda leg: leg.row_number):
                if row.is_option and row.is_short:
                    ticket.let_assign.append(let_assign_leg(row, group, spot_mid, now=ticket.now, today=target.today,
                                                            cutoff_et=cutoff, calendar=target.calendar,
                                                            extra_holidays=target.extra_holidays))
        else:
            for leg in ticket.legs:
                assert leg.plan.row_number is not None
                ticket.puts.append(put_moneyness(rows[leg.plan.row_number], leg.quote.conservative, spot_mid))
    return _finish(ticket, market)


def _roll(ticket: Ticket, market: MarketData, replacement: Replacement) -> Ticket:
    answer = structuring.run_replacement(market, ticket.trade_id, replacement.expiration, replacement.strike,
                                         ticket.criteria, require_live=True)
    ticket.replacement = answer
    ticket.warnings.extend(answer.warnings)
    if answer.verdict != structuring.VERDICT_PRICED or answer.candidate is None or answer.held is None:
        return _close(ticket, market, answer.verdict, answer.cause, answer.reason)
    new = answer.candidate.legs[0]
    if new.unpriced is not None:
        words = "%s cannot be priced: %s" % (
            "the %s %s %s" % (new.expiration.isoformat(), expiry.plain(new.strike), new.right.lower()),
            structuring_text.reason_words(structuring_text.Writer(), new.unpriced))
        return _close(ticket, market, VERDICT_UNAVAILABLE, CAUSE_REPLACEMENT_UNPRICED, words)
    held_quote = quote_of_held(answer.held)
    if held_quote is None:  # run_replacement answers UNAVAILABLE first; kept for the invariant
        return _close(ticket, market, VERDICT_UNAVAILABLE, structuring.CAUSE_HELD_LEG_UNUSABLE)
    rows = {row.row_number: row for row in ticket.group.open_legs} if ticket.group is not None else {}
    priced: List[PlanLeg] = []
    for plan in ticket.plan:
        if plan.row_number is None:
            plan = replace(plan, symbol=new.occ)
            ticket.legs.append(_priced(ticket, plan, quote_of_new(new, plan.intent), market))
        else:
            ticket.legs.append(_priced(ticket, plan, held_quote, market))
            ticket.realized.append(realized_estimate(rows[plan.row_number], held_quote.conservative, held_quote.mid))
        priced.append(plan)
    ticket.plan = priced
    if answer.pricing is not None:
        ticket.spot = answer.pricing.spot
    return _finish(ticket, market)


# --------------------------------------------------------------------------------------------
# The text
# --------------------------------------------------------------------------------------------


def ticket_as_of(ticket: Ticket) -> datetime.datetime:
    """The footer's instant: for a TICKET the oldest stamp of the quotes it used (every leg's and the spot's when the
    ticket reads it); for any other answer the instant of the run."""
    if ticket.verdict != VERDICT_TICKET:
        return ticket.now
    stamps = [leg.quote.as_of for leg in ticket.legs if leg.quote.as_of is not None]
    if ticket.spot is not None and ticket.spot.as_of is not None:
        stamps.append(ticket.spot.as_of)
    if ticket.replacement is not None:
        as_of = structuring_text.data_as_of(ticket.replacement)
        if as_of is not None:
            stamps.append(as_of)
    return min(stamps) if stamps else ticket.now


def quiet_reason(ticket: Ticket) -> str:
    """The reason words of an answer that prints no ticket (a disabled section is named)."""
    if ticket.cause == structuring.NA_DISABLED and ticket.section_name is not None:
        return "the %s section is %s" % (ticket.section_name, ticket.reason)
    return ticket.reason or ""


def cash_lines(w: structuring_text.Writer, cash: Cash, realized: Sequence[RealizedLeg]) -> List[str]:
    """The cash block (``structuring_text.cash_block``): the steps' net cash and the closing legs' realized estimate at
    the conservative prices and at the mids (the mids read :data:`NO_MID_WORDS` when a leg has none), and the
    most cash the steps need at once."""
    with localcontext(_CONTEXT):
        conservative = sum((item.conservative for item in realized), _ZERO)
        mids = [item.mid for item in realized]
        mid = sum((value for value in mids if value is not None), _ZERO) if all(value is not None for value in mids) \
            else None
    return structuring_text.cash_block(w, cash.net_conservative, cash.net_mid, conservative, mid, len(realized),
                                       cash.peak_needed)


def _relation(w: structuring_text.Writer, right: str, spot: Decimal, strike: Decimal, itm: bool) -> str:
    """The spot against the strike in the one-cent rule's words."""
    above = right == "CALL"
    if itm:
        return "the spot mid %s is at least %s %s its strike" % (w.price(spot), w.number("0.01"), "above" if above else
                                                                 "below")
    if (spot < strike) if above else (spot > strike):
        return "the spot mid %s is %s its strike" % (w.price(spot), "below" if above else "above")
    return "the spot mid %s is less than %s %s its strike" % (w.price(spot), w.number("0.01"), "above" if above else
                                                             "below")


def _dividend_line(w: structuring_text.Writer, leg: LetAssignLeg, today: datetime.date) -> Optional[str]:
    window = leg.dividend
    if window is None:
        return None
    if window.known and window.applicable:
        reason = structuring.Reason(structuring.KEY_EX_DIVIDEND, structuring.CODE_CROSSES_EX_DIVIDEND,
                                    {"ex_date": window.ex_date, "cum_day": window.cum_day, "amount": window.amount,
                                     "status": window.status}, None)
        return "%s: an early exercise before the ex-date is possible" % structuring_text.reason_words(w, reason)
    if window.known:
        if window.ex_date is None:
            return None  # a confirmed absence: nothing to say
        return "the ex-dividend date %s falls after the call's last trading day %s" % (w.date(window.ex_date),
                                                                                       w.date(leg.last_trading_day))
    passed = window.ex_date is not None and window.ex_date <= today
    reason = structuring.Reason(structuring.KEY_EX_DIVIDEND, structuring.CODE_DIVIDEND_UNKNOWN,
                                {"status": "passed" if passed else window.status, "ex_date": window.ex_date}, None)
    return structuring_text.reason_words(w, reason)


def _let_assign_blocks(w: structuring_text.Writer, ticket: Ticket) -> List[str]:
    """Per short option left to expiry: the OCC line, then the contract's items (its moneyness now, the act-by of the
    alternative, what assignment would do and its dividend window) under the contract in bold."""
    target, group = ticket.target, ticket.group
    assert target is not None and group is not None
    cutoff = target.market.cfg.owner.broker_exercise_cutoff_et
    lines: List[str] = []
    for leg in ticket.let_assign:
        shares = "%s %s shares" % (w.count(leg.shares), group.underlying)
        if leg.right == "CALL":
            assigned = "%s are called away at the strike, cash %s" % (shares, w.amount(leg.cash_if_assigned, signed=True))
            if leg.realized_if_assigned is not None:
                assigned += "; realized on the call and the shares %s" % w.amount(leg.realized_if_assigned, signed=True)
            else:
                assigned += "; the group holds fewer shares than the call delivers, so the desk states no realized amount"
        else:
            assigned = "you receive %s and pay the strike, cash %s" % (shares, w.amount(leg.cash_if_assigned, signed=True))
            if leg.realized_if_assigned is not None:
                assigned += "; realized on the put %s" % w.amount(leg.realized_if_assigned, signed=True)
        rows = [["now", "%s the money: %s" % ("in" if leg.itm else "out of",
                                              _relation(w, leg.right, leg.spot_mid, leg.strike, leg.itm))],
                ["if you decide otherwise", "you %s: %s" % (w.words(leg.act_by_text), SESSION_CLOSE_WORDS)],
                ["if assigned", assigned]]
        dividend = _dividend_line(w, leg, target.today)
        if dividend is not None:
            rows.append(["ex-dividend", dividend])
        lines.extend([structuring_text.let_assign_line(w, leg.strike, leg.right, cutoff), ""])
        lines.append(structuring_text.bold(structuring_text.contract_words(w, leg.expiration, leg.strike, leg.right)
                                           + ":"))
        lines.extend(structuring_text.labelled_items(rows))
        lines.append("")
    return lines


PUT_CAPTION: str = "**the put:**"


def _put_block(w: structuring_text.Writer, ticket: Ticket) -> List[str]:
    """MONETIZE_PUT: each put's moneyness and the intrinsic and time value of its bid, then what the shares carry
    after the leg."""
    group = ticket.group
    assert group is not None
    rows: List[List[str]] = []
    for put in ticket.puts:
        head = "%s the money: %s" % ("in" if put.itm else "out of", _relation(w, "PUT", put.spot_mid, put.strike, put.itm))
        if put.intrinsic == 0:
            words = "%s, so its bid %s is all time value" % (head, w.price(put.bid))
        elif put.time_value >= 0:
            words = "%s; its bid %s holds %s of intrinsic value and %s of time value" % (
                head, w.price(put.bid), w.price(put.intrinsic), w.price(put.time_value))
        else:
            words = "%s; its bid %s is %s below its intrinsic value %s" % (head, w.price(put.bid), w.price(-put.time_value),
                                                                          w.price(put.intrinsic))
        rows.append([structuring_text.contract_words(w, put.expiration, put.strike, "PUT"), words])
    shares = sum((row.open_quantity for row in group.open_legs if row.is_stock_like and row.is_long), _ZERO)
    calls = any(row.is_option and row.is_call and row.is_short for row in group.open_legs)
    rows.append(["after the leg", "your %s %s shares carry no put%s: their downside is unhedged"
                 % (w.number(expiry.plain(shares)), group.underlying, " (the short call stays)" if calls else "")])
    return [PUT_CAPTION] + structuring_text.labelled_items(rows)


def _leg_lines(w: structuring_text.Writer, ticket: Ticket) -> List[str]:
    lines: List[str] = []
    count = len(ticket.legs)
    for index, leg in enumerate(ticket.legs, 1):
        quote, plan = leg.quote, leg.plan
        segment = structuring_text.quote_segment(w, quote.bid, quote.ask, quote.mid, quote.grade, quote.note)
        if plan.instrument == approval.INSTRUMENT_SHARES:
            lines.append(structuring_text.share_leg_line(w, index, count, Decimal(plan.quantity), plan.root, segment))
        else:
            assert plan.expiration is not None and plan.strike is not None and plan.right is not None
            lines.append(structuring_text.leg_line(w, index, count, plan.effect, plan.side, plan.quantity, plan.root,
                                                   plan.expiration, plan.strike, plan.right, segment))
    return lines


def ticket_lines(w: structuring_text.Writer, ticket: Ticket,
                 rulebook_changes: Sequence[Mapping[str, Optional[str]]] = (),
                 delay_min: int = structuring_text.FEED_DELAY_MIN) -> List[str]:
    """The ticket in the desk view (0.2.1, without the footer): the title, the context strip (account, computed,
    valid until, quotes) on one line, the legs, the cash, a ROLL's replacement checks and group, MONETIZE_PUT's put
    items or LET_ASSIGN's items per short option, F1's approval block, then the notes; one line for an answer that
    prints no ticket. ``rulebook_changes`` (an unacknowledged adopted rulebook alone, which does not block) prints
    first."""
    lines: List[str] = []
    if rulebook_changes:
        lines.extend([structuring_text.config_changed_line(w, rulebook_changes), ""])
    what = "%s ticket" % ticket.action
    if ticket.verdict == VERDICT_NOT_APPLICABLE:
        return lines + [structuring_text.not_applicable_line(w, ticket.trade_id, what, quiet_reason(ticket))]
    if ticket.verdict == VERDICT_UNAVAILABLE:
        return lines + [structuring_text.unavailable_line(w, ticket.trade_id, what, ticket.reason or "")]
    group, target = ticket.group, ticket.target
    assert group is not None and target is not None
    if ticket.verdict == VERDICT_REFUSED:
        result = ticket.approval
        assert result is not None
        if ticket.refused_code == REFUSED_APPROVAL:
            return lines + [approval.refusal_line(result, w.trade_id(ticket.trade_id), group.underlying,
                                                  structuring_text.structure_words(group.structure), w.emitted)]
        step = result.exposure_step
        assert step is not None
        return lines + [structuring_text.exposure_refused_line(w, ticket.trade_id, group.underlying, group.structure,
                                                               step, result.steps[step - 1].after.uncovered_calls)]
    assert ticket.valid_until is not None
    first = min(row.row_number for row in group.open_legs)
    account = structuring_text.account_cell(w, group.account, target.market.cfg.owner.accounts, first)
    words = None
    if ticket.action == structuring.ACTION_ROLL and group.structure == grouping.STRUCTURE_PROTECTIVE_PUT:
        words = structuring_text.ROLL_PUT_WORDS
    as_of = ticket_as_of(ticket)
    lines.extend([structuring_text.ticket_header(w, ticket.trade_id, group.underlying, group.structure, ticket.action,
                                                 words), ""])
    lines.extend([structuring_text.strip_line([
        ("account", account), ("computed", w.clock(ticket.now)),
        ("valid until", structuring_text.valid_until_cell(w, ticket.valid_until, bool(ticket.valid_full))),
        ("quotes", structuring_text.quotes_cell(w, as_of, delay_min))]), ""])
    notes = [structuring_text.validity_line(w, ticket.valid_until), structuring_text.quotes_line(w, as_of, delay_min)]
    if ticket.action == structuring.ACTION_LET_ASSIGN:
        lines.extend(_let_assign_blocks(w, ticket))
        return lines + structuring_text.notes_block(notes + [structuring_text.AFTER_EXPIRY_LINE])
    assert ticket.holdings is not None and ticket.owner_level is not None and ticket.cash is not None
    lines.append(structuring_text.LEGS_CAPTION)
    lines.extend(structuring_text.ITEM + leg for leg in _leg_lines(w, ticket))
    lines.append("")
    lines.extend(cash_lines(w, ticket.cash, ticket.realized))
    lines.append("")
    replacement = ticket.replacement if ticket.action == structuring.ACTION_ROLL else None
    if replacement is not None:
        lines.extend(structuring_text.replacement_lines(w, replacement))
        lines.append("")
        notes.append(structuring_text.MODEL_LINE)
        candidate = replacement.candidate
        if candidate is not None and candidate.american_caveat:
            notes.append(structuring_text.american_line(w, candidate))
    if ticket.action == structuring.ACTION_MONETIZE_PUT:
        lines.extend(_put_block(w, ticket))
        lines.append("")
    lines.extend(approval.approval_lines(ticket.holdings, approval_legs(ticket.plan), ticket.owner_level,
                                         group.underlying, w.emitted))
    lines.append("")
    notes.extend([structuring_text.CASH_BALANCE_LINE, structuring_text.AFTER_FILL_LINE, structuring_text.LIMIT_LINE,
                  structuring_text.ACT_LINE])
    return lines + structuring_text.notes_block(notes)


def render_ticket(ticket: Ticket, rulebook_changes: Sequence[Mapping[str, Optional[str]]] = (),
                  delay_min: int = structuring_text.FEED_DELAY_MIN) -> structuring_text.Rendered:
    """The linted text (``structuring_text.finish``: the structuring footer at :func:`ticket_as_of`, the account name
    the owner typed screened, the WITHHELD line on any finding)."""
    w = structuring_text.Writer()
    lines = ticket_lines(w, ticket, rulebook_changes, delay_min)
    return structuring_text.finish(w, lines, ticket_as_of(ticket), structuring.TOOL_EXIT_TICKET,
                                   owner_texts=structuring.owner_texts(ticket.target), delay_min=delay_min)


__all__ = [
    "CAUSE_REPLACEMENT_UNPRICED", "Cash", "INTENTS", "INTENT_CLOSE_LONG", "INTENT_CLOSE_SHARES", "INTENT_CLOSE_SHORT",
    "INTENT_OPEN_LONG", "INTENT_OPEN_SHORT", "LegQuote", "LetAssignLeg", "NO_MID_WORDS", "PlanLeg", "PutMoneyness",
    "REFUSED_APPROVAL", "REFUSED_CODES", "REFUSED_EXPOSURE", "RealizedLeg", "Replacement", "SHARE_TICK", "TICKET_KEYS",
    "Ticket", "TicketLeg", "TicketUsageError", "UNAVAILABLE_WORDS", "VERDICTS", "VERDICT_NOT_APPLICABLE",
    "VERDICT_REFUSED", "VERDICT_TICKET", "VERDICT_UNAVAILABLE", "approval_legs", "assignment_realized", "cash_summary",
    "cash_lines", "check_plan", "let_assign_leg", "plan_legs", "put_moneyness", "quiet_reason", "quote_of_held",
    "quote_of_new", "quote_of_shares", "realized_estimate", "render_ticket", "replaced_row",
    "run_ticket", "step_cash", "step_records", "ticket_as_of", "ticket_lines",
]
