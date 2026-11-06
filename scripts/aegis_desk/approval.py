"""The approval-level check: the first consumer of ``desk.toml``'s ``approval_level`` (PR 3b decision S7 over designer
C's D4). Pure: no I/O, no quotes, no clock; standard library only.

The desk's convention
---------------------
:data:`CONVENTION_INTRO` and :data:`CONVENTION` (levels 0-5) are stated word for word by the docs and the skills, with
:data:`CONVENTION_CLOSING`. The owner states one level in this convention, the lowest across their accounts; it is
self-attested and read from the ACKNOWLEDGED ``desk.toml`` (``owner.approval_level``). The check is always called
:data:`CHECK_NAME`, never anything that sounds like a judgment of the owner.

Steps (:data:`STEP_TABLE`, :data:`STEP_LEVELS`)
-----------------------------------------------
A ticket or a proposal is a sequence of :class:`Leg` (``effect`` open | close, ``side`` long | short,
``instrument`` call | put | shares, ``quantity`` contracts or shares) applied to the :class:`Holdings` of ONE account
and ONE underlying (:func:`holdings_of`: every open row of that account and underlying, whatever its group). Each
step's level is read against the state BEFORE the leg, and every prefix state is checked, so an intermediate exposure
in the leg order counts:

* closing a short option, or a long option or shares when no short call is left uncovered after it: level 0;
* closing shares or a long option when a short call is left uncovered after it: level 4 (``close_uncovers``);
* opening a short call: level 1 when the free shares before it (``shares - 100 x short calls already open``) are at
  least ``100 x quantity``, else level 4;
* opening a short put: level 1 (cash-secured by the convention: the desk does not see your cash);
* opening a long call or a long put: level 2.

A short call is covered by 100 shares of the same account and underlying (:attr:`Holdings.uncovered_calls`; a long
call never covers by the convention, so a short call opened against one reads as without enough free shares: the
desk proposes no spread, and the check errs high). The level a ticket needs is the highest step's
(:attr:`ApprovalResult.required_level`); it passes when that is at most the owner's level. A refused check names the
first step at that level (:attr:`ApprovalResult.refusal`); :attr:`ApprovalResult.exposure_step` names the first step
after which a short call is uncovered (the structuring tools refuse such an order as ``intermediate_exposure``
whatever the owner's level: the canonical orders below never produce one).

Leg orders (S7) the builders emit: CLOSE closes shorts, then longs; UNWIND_STOCK closes shorts, then the shares, then
longs (protection last); ROLL of a short call closes the old call, then opens the new one; ROLL of a long put opens
the new put, then closes the old one; a collar proposal opens the put, then the call; MONETIZE_PUT closes the put.

Fixed lines
-----------
The renderers take integers and words and append every number they print to ``emitted`` (the alert lint's list),
so the text builders never re-word them: :func:`leg_order_line`, :func:`other_order_line` (printed only when the
reversed order would exceed the owner's level: :func:`reverse_breach`), :data:`MULTI_LEG_NOTE` (any ticket with more
than one leg), :func:`approval_line` and :func:`refused_line`; :func:`approval_lines` composes the first four in that
order for a passing check, :func:`refusal_line` the REFUSED line of a refused one; :func:`holdings_words` and
:func:`step_words` give the state and step words.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import grouping

LEVEL_RANGE: Tuple[int, int] = (0, 5)
CHECK_NAME: str = "the approval-level check"
CONVENTION_INTRO: str = ("Approval levels by the desk's convention (brokers number and define their levels differently; "
                         "state yours in this convention, and give the lowest level across your accounts):")
CONVENTION: Dict[int, str] = {
    0: "no options approval: the desk shows closing tickets only",
    1: "covered calls on shares held in the same account, and cash-secured puts",
    2: "level 1 plus long calls and long puts (protective puts, collars)",
    3: "level 2 plus defined-risk spreads",
    4: "level 3 plus uncovered short options",
    5: "level 4 plus uncovered index options and other advanced strategies",
}
"""Designer C's D4 convention; level 2 says "long calls and long puts" where the design said a trade verb, which the
desk's forbidden-language screens refuse in every form."""
CONVENTION_CLOSING: str = ("By the desk's convention, a closing transaction needs no approval (level 0); the broker's "
                           "own rules apply.")

EFFECT_OPEN: str = "open"
EFFECT_CLOSE: str = "close"
EFFECTS: Tuple[str, ...] = (EFFECT_OPEN, EFFECT_CLOSE)
SIDE_LONG: str = "long"
SIDE_SHORT: str = "short"
SIDES: Tuple[str, ...] = (SIDE_LONG, SIDE_SHORT)
INSTRUMENT_CALL: str = "call"
INSTRUMENT_PUT: str = "put"
INSTRUMENT_SHARES: str = "shares"
INSTRUMENTS: Tuple[str, ...] = (INSTRUMENT_CALL, INSTRUMENT_PUT, INSTRUMENT_SHARES)
SHARES_PER_CONTRACT: int = 100

STEP_CLOSE_OPTION: str = "close_option"
STEP_CLOSE_SHARES: str = "close_shares"
STEP_CLOSE_UNCOVERS: str = "close_uncovers"
STEP_OPEN_COVERED_CALL: str = "open_covered_call"
STEP_OPEN_UNCOVERED_CALL: str = "open_uncovered_call"
STEP_OPEN_SHORT_PUT: str = "open_short_put"
STEP_OPEN_LONG: str = "open_long"
STEP_TABLE: Tuple[Tuple[str, str, int], ...] = (
    (STEP_CLOSE_OPTION, "close a short or long option you hold", 0),
    (STEP_CLOSE_SHARES, "close shares that no remaining short call needs", 0),
    (STEP_CLOSE_UNCOVERS, "close shares or a long that leaves a remaining short option uncovered", 4),
    (STEP_OPEN_COVERED_CALL, "open a short call covered by free shares in the same account (shares - 100 x short "
                             "calls already open >= 100 x qty)", 1),
    (STEP_OPEN_UNCOVERED_CALL, "open a short call without enough free shares", 4),
    (STEP_OPEN_SHORT_PUT, "open a short put (cash-secured; the desk does not see your cash)", 1),
    (STEP_OPEN_LONG, "open a long call or long put", 2),
)
"""``(kind, the convention's words, level)`` of every step, in the order the docs print the table."""
STEP_LEVELS: Dict[str, int] = {kind: level for kind, _words, level in STEP_TABLE}

STRUCTURE_LEVELS: Dict[str, Optional[int]] = {
    grouping.STRUCTURE_LONG_STOCK: 0,
    grouping.STRUCTURE_SHORT_STOCK: None,
    grouping.STRUCTURE_LONG_CALL: 2,
    grouping.STRUCTURE_LONG_PUT: 2,
    grouping.STRUCTURE_SHORT_CALL: 4,
    grouping.STRUCTURE_CASH_SECURED_PUT: 1,
    grouping.STRUCTURE_COVERED_CALL: 1,
    grouping.STRUCTURE_PROTECTIVE_PUT: 2,
    grouping.STRUCTURE_COLLAR: 2,
    grouping.STRUCTURE_VERTICAL_CREDIT_CALL: 3,
    grouping.STRUCTURE_VERTICAL_CREDIT_PUT: 3,
    grouping.STRUCTURE_VERTICAL_DEBIT_CALL: 3,
    grouping.STRUCTURE_VERTICAL_DEBIT_PUT: 3,
    grouping.STRUCTURE_CUSTOM: None,
    grouping.STRUCTURE_INVALID: None,
}
"""The level each grouping structure needs by the convention (docs and the proposal line). ``None`` is refused:
custom and invalid groups, and short stock (a margin matter, outside the options convention)."""

MULTI_LEG_NOTE: str = ("entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher "
                       "level")
_CONVENTION_SUFFIX = " by the desk's convention"


def structure_level(structure: str) -> Optional[int]:
    """The level a structure needs (:data:`STRUCTURE_LEVELS`), ``None`` for one the convention refuses or does not
    know."""
    return STRUCTURE_LEVELS.get(structure)


def _count(value: Any, what: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        raise ValueError("%s must be a %s integer, got %r" % (what, "positive" if positive else "non-negative", value))
    return value


def _level(value: Any, what: str) -> int:
    low, high = LEVEL_RANGE
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("%s must be an integer %d-%d, got %r" % (what, low, high, value))
    return value


def _emit(emitted: Optional[List[str]], value: int) -> str:
    text = str(value)
    if emitted is not None:
        emitted.append(text)
    return text


def _plural(count: int, word: str) -> str:
    return word if count == 1 else word + "s"


# --------------------------------------------------------------------------------------------
# Holdings and legs
# --------------------------------------------------------------------------------------------


_FIELDS: Dict[Tuple[str, str], str] = {
    (SIDE_LONG, INSTRUMENT_SHARES): "shares", (SIDE_LONG, INSTRUMENT_CALL): "long_calls",
    (SIDE_SHORT, INSTRUMENT_CALL): "short_calls", (SIDE_LONG, INSTRUMENT_PUT): "long_puts",
    (SIDE_SHORT, INSTRUMENT_PUT): "short_puts",
}


@dataclass(frozen=True)
class Holdings:
    """What one account holds of one underlying: whole shares and option contracts (non-negative integers)."""

    shares: int = 0
    long_calls: int = 0
    short_calls: int = 0
    long_puts: int = 0
    short_puts: int = 0

    def __post_init__(self) -> None:
        for name in ("shares", "long_calls", "short_calls", "long_puts", "short_puts"):
            _count(getattr(self, name), "Holdings.%s" % name)

    @property
    def uncovered_calls(self) -> int:
        """Short calls beyond what the shares cover (100 shares per contract)."""
        return max(0, self.short_calls - self.shares // SHARES_PER_CONTRACT)

    @property
    def free_shares(self) -> int:
        """``shares - 100 x short calls already open`` (never below zero): what a new short call may be covered by."""
        return max(0, self.shares - SHARES_PER_CONTRACT * self.short_calls)

    def apply(self, leg: "Leg") -> "Holdings":
        """The holdings after ``leg`` (``ValueError`` when it closes more than is held)."""
        name = _FIELDS[(leg.side, leg.instrument)]
        held = getattr(self, name)
        if leg.effect == EFFECT_CLOSE:
            if leg.quantity > held:
                raise ValueError("the leg closes %d %s but %d %s held" % (
                    leg.quantity, _leg_noun(leg, leg.quantity), held, "is" if held == 1 else "are"))
            return replace(self, **{name: held - leg.quantity})
        return replace(self, **{name: held + leg.quantity})


@dataclass(frozen=True)
class Leg:
    """One leg: ``effect`` (open | close), ``side`` (long | short), ``instrument`` (call | put | shares) and
    ``quantity`` (contracts, or shares). Shares are only ever closed, and only long (the desk opens no shares)."""

    effect: str
    side: str
    instrument: str
    quantity: int

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise ValueError("a leg's effect must be one of %s, got %r" % (", ".join(EFFECTS), self.effect))
        if self.side not in SIDES:
            raise ValueError("a leg's side must be one of %s, got %r" % (", ".join(SIDES), self.side))
        if self.instrument not in INSTRUMENTS:
            raise ValueError("a leg's instrument must be one of %s, got %r" % (", ".join(INSTRUMENTS), self.instrument))
        _count(self.quantity, "a leg's quantity", positive=True)
        if self.instrument == INSTRUMENT_SHARES and (self.effect != EFFECT_CLOSE or self.side != SIDE_LONG):
            raise ValueError("a shares leg only closes long shares: the desk opens no shares and holds no short shares")


def _leg_noun(leg: Leg, count: int) -> str:
    if leg.instrument == INSTRUMENT_SHARES:
        return "shares" if count != 1 else "share"
    return "%s %s" % (leg.side, _plural(count, leg.instrument))


def holdings_of(rows: Iterable[Any], account: str, underlying: str) -> Holdings:
    """The :class:`Holdings` of every OPEN ``positions_csv.PositionRow`` of ``account`` and ``underlying`` (whatever
    its group). ``ValueError`` for short shares or a quantity that is not whole."""
    counts = {"shares": 0, "long_calls": 0, "short_calls": 0, "long_puts": 0, "short_puts": 0}
    for row in rows:
        if not row.is_open or row.account != account or row.underlying != underlying:
            continue
        quantity = abs(row.open_quantity)
        if quantity != quantity.to_integral_value():
            raise ValueError("row %d holds %s: the approval-level check counts whole shares and contracts"
                             % (row.row_number, row.open_quantity))
        if row.is_stock_like:
            if row.is_short:
                raise ValueError("row %d holds short shares: outside the desk's convention" % row.row_number)
            counts["shares"] += int(quantity)
        elif row.is_option:
            side = SIDE_LONG if row.is_long else SIDE_SHORT
            counts[_FIELDS[(side, INSTRUMENT_CALL if row.is_call else INSTRUMENT_PUT)]] += int(quantity)
    return Holdings(**counts)


# --------------------------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StepLevel:
    """One step of a checked order: its 1-based ``index``, the ``leg``, the step ``kind`` (:data:`STEP_LEVELS`), its
    ``level``, the step ``words`` (:func:`step_words`) and the holdings ``before`` and ``after`` it."""

    index: int
    leg: Leg
    kind: str
    level: int
    words: str
    before: Holdings
    after: Holdings


@dataclass(frozen=True)
class ApprovalResult:
    """``ok`` when ``required_level`` (the highest step's) is at most ``owner_level``; ``refusal`` is the first step at
    the required level when it is not; ``exposure_step`` the index of the first step after which a short call is
    uncovered (``None`` when none is)."""

    ok: bool
    required_level: int
    owner_level: int
    steps: Tuple[StepLevel, ...]
    refusal: Optional[StepLevel]
    exposure_step: Optional[int]


@dataclass(frozen=True)
class ReverseBreach:
    """What the reversed order would do: after the leg numbered ``leg_index`` in the canonical order, ``uncovered_calls``
    short calls uncovered, a step of ``level``."""

    leg_index: int
    uncovered_calls: int
    level: int


def _step_kind(leg: Leg, before: Holdings, after: Holdings) -> str:
    if leg.effect == EFFECT_CLOSE:
        if leg.side == SIDE_SHORT:
            return STEP_CLOSE_OPTION
        if after.uncovered_calls > 0:
            return STEP_CLOSE_UNCOVERS
        return STEP_CLOSE_SHARES if leg.instrument == INSTRUMENT_SHARES else STEP_CLOSE_OPTION
    if leg.side == SIDE_LONG:
        return STEP_OPEN_LONG
    if leg.instrument == INSTRUMENT_PUT:
        return STEP_OPEN_SHORT_PUT
    if before.shares - SHARES_PER_CONTRACT * before.short_calls >= SHARES_PER_CONTRACT * leg.quantity:
        return STEP_OPEN_COVERED_CALL
    return STEP_OPEN_UNCOVERED_CALL


def step_words(step: StepLevel, emitted: Optional[List[str]] = None) -> str:
    """The step's words (``closing your 2 short calls``, ``opening 1 new long put``), every number added to
    ``emitted``."""
    leg, quantity = step.leg, step.leg.quantity
    if step.kind == STEP_CLOSE_SHARES or (step.kind == STEP_CLOSE_UNCOVERS and leg.instrument == INSTRUMENT_SHARES):
        words = "closing %s of your shares" % _emit(emitted, quantity)
    elif step.kind in (STEP_CLOSE_OPTION, STEP_CLOSE_UNCOVERS):
        words = "closing your %s %s %s" % (_emit(emitted, quantity), leg.side, _plural(quantity, leg.instrument))
    elif step.kind == STEP_OPEN_COVERED_CALL:
        words = "opening %s new short %s, covered by %s free shares" % (
            _emit(emitted, quantity), _plural(quantity, "call"), _emit(emitted, step.before.free_shares))
    elif step.kind == STEP_OPEN_UNCOVERED_CALL:
        words = "opening %s new short %s without enough free shares (%s free, %s needed)" % (
            _emit(emitted, quantity), _plural(quantity, "call"), _emit(emitted, step.before.free_shares),
            _emit(emitted, SHARES_PER_CONTRACT * quantity))
    elif step.kind == STEP_OPEN_SHORT_PUT:
        words = "opening %s new short %s (cash-secured; the desk does not see your cash)" % (
            _emit(emitted, quantity), _plural(quantity, "put"))
    else:
        words = "opening %s new long %s" % (_emit(emitted, quantity), _plural(quantity, leg.instrument))
    if step.kind == STEP_CLOSE_UNCOVERS:
        uncovered = step.after.uncovered_calls
        words += ", which leaves %s short %s uncovered" % (_emit(emitted, uncovered), _plural(uncovered, "call"))
    return words


def check_legs(state: Holdings, legs: Sequence[Leg], owner_level: int) -> ApprovalResult:
    """The approval-level check of ``legs`` applied in order to ``state`` against ``owner_level`` (module notes);
    ``ValueError`` for an empty order, a leg that closes more than is held, or a level outside 0-5."""
    if not isinstance(state, Holdings):
        raise ValueError("check_legs() takes Holdings, got %s" % type(state).__name__)
    level = _level(owner_level, "the owner's approval level")
    if not legs or not all(isinstance(leg, Leg) for leg in legs):
        raise ValueError("check_legs() takes a non-empty sequence of Leg")
    steps: List[StepLevel] = []
    current = state
    for index, leg in enumerate(legs, 1):
        after = current.apply(leg)
        kind = _step_kind(leg, current, after)
        step = StepLevel(index, leg, kind, STEP_LEVELS[kind], "", current, after)
        steps.append(replace(step, words=step_words(step)))
        current = after
    required = max(step.level for step in steps)
    ok = required <= level
    refusal = None if ok else next(step for step in steps if step.level == required)
    exposure = next((step.index for step in steps if step.after.uncovered_calls > 0), None)
    return ApprovalResult(ok, required, level, tuple(steps), refusal, exposure)


def reverse_breach(state: Holdings, legs: Sequence[Leg], owner_level: int) -> Optional[ReverseBreach]:
    """What the reversed order would breach when it would exceed ``owner_level`` (``None`` otherwise, and for one leg):
    the canonical number of the first leg after which, in the reversed order, the step's level exceeds the owner's,
    with the uncovered short calls it leaves (only coverage depends on the order, so such a step always leaves one)."""
    if len(legs) < 2:
        return None
    try:
        reversed_result = check_legs(state, list(reversed(legs)), owner_level)
    except ValueError:
        return None
    if reversed_result.ok:
        return None
    for step in reversed_result.steps:
        if step.level > reversed_result.owner_level and step.after.uncovered_calls > 0:
            return ReverseBreach(len(legs) - step.index + 1, step.after.uncovered_calls, step.level)
    return None


# --------------------------------------------------------------------------------------------
# The fixed lines
# --------------------------------------------------------------------------------------------


def holdings_words(holdings: Holdings, underlying: str, emitted: Optional[List[str]] = None) -> str:
    """``you hold 200 WMT shares and 2 short calls``: the shares, then the long calls, short calls (with the
    uncovered count), long puts and short puts held, or ``no option``."""
    if holdings.shares:
        shares = "%s %s %s" % (_emit(emitted, holdings.shares), underlying, _plural(holdings.shares, "share"))
    else:
        shares = "no %s shares" % underlying
    options: List[str] = []
    for count, words in ((holdings.long_calls, "long call"), (holdings.short_calls, "short call"),
                         (holdings.long_puts, "long put"), (holdings.short_puts, "short put")):
        if count:
            part = "%s %s" % (_emit(emitted, count), _plural(count, words))
            if words == "short call" and holdings.uncovered_calls:
                part += " (%s uncovered)" % _emit(emitted, holdings.uncovered_calls)
            options.append(part)
    items = [shares] + (options or ["no option"])
    return "you hold " + (", ".join(items[:-1]) + " and " + items[-1])


def leg_order_line(steps: Sequence[Tuple[int, str]], emitted: Optional[List[str]] = None) -> str:
    """``leg order: leg 1 first; after leg 1 <words> (level k by the desk's convention); after leg 2 <words> (level
    k)`` from ``(level, state words after the leg)`` pairs; one leg prints ``leg order: after leg 1 <words> (level k
    by the desk's convention)``."""
    if not steps:
        raise ValueError("a leg order needs at least one leg")
    parts: List[str] = []
    if len(steps) > 1:
        parts.append("leg %s first" % _emit(emitted, 1))
    for index, (level, words) in enumerate(steps, 1):
        number = _emit(emitted, index)
        level_text = _emit(emitted, _level(level, "a step's level"))
        parts.append("after leg %s %s (level %s%s)" % (number, words, level_text, _CONVENTION_SUFFIX if index == 1 else ""))
    return "leg order: " + "; ".join(parts)


def other_order_line(leg_index: int, uncovered_calls: int, level: int = 4, emitted: Optional[List[str]] = None) -> str:
    """``in the other order, after leg <i> you would hold <n> uncovered calls (level <k>)``."""
    _count(leg_index, "the leg number", positive=True)
    _count(uncovered_calls, "the uncovered calls", positive=True)
    return "in the other order, after leg %s you would hold %s uncovered %s (level %s)" % (
        _emit(emitted, leg_index), _emit(emitted, uncovered_calls), _plural(uncovered_calls, "call"),
        _emit(emitted, _level(level, "the level")))


def approval_line(required_level: int, owner_level: int, emitted: Optional[List[str]] = None) -> str:
    """``approval: the highest step needs level <k> by the desk's convention, within your self-attested level <L>
    (desk.toml); your broker's own approval for this account decides`` (``ValueError`` when k > L: that is REFUSED)."""
    required, owner = _level(required_level, "the required level"), _level(owner_level, "the owner's level")
    if required > owner:
        raise ValueError("level %d is above the owner's level %d: print the REFUSED line" % (required, owner))
    return ("approval: the highest step needs level %s by the desk's convention, within your self-attested level %s "
            "(desk.toml); your broker's own approval for this account decides"
            % (_emit(emitted, required), _emit(emitted, owner)))


def multi_leg_note(leg_count: int) -> Optional[str]:
    """:data:`MULTI_LEG_NOTE` for a ticket with more than one leg, else ``None``."""
    return MULTI_LEG_NOTE if _count(leg_count, "the leg count", positive=True) > 1 else None


def refused_line(subject: str, underlying: str, structure_words: str, words: str, level: int, owner_level: int,
                 emitted: Optional[List[str]] = None) -> str:
    """``REFUSED <trade_id or SYMBOL> | <UNDERLYING> <structure words> | <step words> needs level <k> by the desk's
    convention; desk.toml records your self-attested level <L> | the desk shows nothing above your level; your
    broker's own approval decides what you can enter`` (``ValueError`` unless k > L)."""
    needed, owner = _level(level, "the level"), _level(owner_level, "the owner's level")
    if needed <= owner:
        raise ValueError("level %d is within the owner's level %d: nothing is refused" % (needed, owner))
    return ("REFUSED %s | %s %s | %s needs level %s by the desk's convention; desk.toml records your self-attested "
            "level %s | the desk shows nothing above your level; your broker's own approval decides what you can enter"
            % (subject, underlying, structure_words, words, _emit(emitted, needed), _emit(emitted, owner)))


def refusal_line(result: ApprovalResult, subject: str, underlying: str, structure_words: str,
                 emitted: Optional[List[str]] = None) -> str:
    """The REFUSED line of a refused :class:`ApprovalResult` (its refusing step's words and level)."""
    if result.ok or result.refusal is None:
        raise ValueError("the check passed: nothing is refused")
    return refused_line(subject, underlying, structure_words, step_words(result.refusal, emitted),
                        result.refusal.level, result.owner_level, emitted)


def approval_lines(state: Holdings, legs: Sequence[Leg], owner_level: int, underlying: str,
                   emitted: Optional[List[str]] = None) -> List[str]:
    """The approval block of a passing order, in the order a ticket prints it: the leg-order line, the other-order
    line (when the reversed order would exceed the owner's level), the multi-leg note (more than one leg) and the
    approval line. ``ValueError`` when the check is refused (print :func:`refusal_line`)."""
    result = check_legs(state, legs, owner_level)
    if not result.ok:
        raise ValueError("the check is refused at level %d: print the REFUSED line" % result.required_level)
    pairs = [(step.level, holdings_words(step.after, underlying, emitted)) for step in result.steps]
    lines = [leg_order_line(pairs, emitted)]
    breach = reverse_breach(state, legs, owner_level)
    if breach is not None:
        lines.append(other_order_line(breach.leg_index, breach.uncovered_calls, breach.level, emitted))
    note = multi_leg_note(len(result.steps))
    if note is not None:
        lines.append(note)
    lines.append(approval_line(result.required_level, result.owner_level, emitted))
    return lines


__all__ = [
    "ApprovalResult",
    "CHECK_NAME",
    "CONVENTION",
    "CONVENTION_CLOSING",
    "CONVENTION_INTRO",
    "EFFECTS",
    "EFFECT_CLOSE",
    "EFFECT_OPEN",
    "Holdings",
    "INSTRUMENTS",
    "INSTRUMENT_CALL",
    "INSTRUMENT_PUT",
    "INSTRUMENT_SHARES",
    "LEVEL_RANGE",
    "Leg",
    "MULTI_LEG_NOTE",
    "ReverseBreach",
    "SHARES_PER_CONTRACT",
    "SIDES",
    "SIDE_LONG",
    "SIDE_SHORT",
    "STEP_CLOSE_OPTION",
    "STEP_CLOSE_SHARES",
    "STEP_CLOSE_UNCOVERS",
    "STEP_LEVELS",
    "STEP_OPEN_COVERED_CALL",
    "STEP_OPEN_LONG",
    "STEP_OPEN_SHORT_PUT",
    "STEP_OPEN_UNCOVERED_CALL",
    "STEP_TABLE",
    "STRUCTURE_LEVELS",
    "StepLevel",
    "approval_line",
    "approval_lines",
    "check_legs",
    "holdings_of",
    "holdings_words",
    "leg_order_line",
    "multi_leg_note",
    "other_order_line",
    "refusal_line",
    "refused_line",
    "reverse_breach",
    "step_words",
    "structure_level",
]
