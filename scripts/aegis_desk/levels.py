"""Threshold levels: every adopted rule of every open group as a price, a band or a date the owner can watch.

Plan section 8 ("Threshold levels") and the PR 2d decisions D1-2 to D1-10; standard library only, no I/O. The input
is an evaluation (:func:`aegis_desk.exit_rules.evaluate`), the book document it was made from, the rulebook, the
research calendar and the ``desk.toml [levels]`` section; the output is a :class:`Sheet`: the text of the level sheet,
its JSON records and the strict emitted document :func:`aegis_desk.alert_lint.check` proves it against. A level is
where the rule's OWN reading turns; every status comes from the evaluation's ``RuleResult.result`` and is never
re-decided here. The model that relays a sheet never does arithmetic: every number in it was printed by a formatter
that emitted it (:func:`fmt_level_price` and the formatters of :mod:`aegis_desk.alerts`).

Ticks and rounding (D1-2, D1-3)
-------------------------------
:data:`OPTION_TICKS` holds the per-class grid, chosen by the UNROUNDED value (``standard`` 0.05 below 3.00 and 0.10
at or above, ``penny`` 0.01 / 0.05, the Penny Interval Program, ``penny_all`` 0.01 at every price). A leg's class is
its OCC root's: ``penny_all`` when ``desk.toml [levels] penny_all_classes`` names it, ``penny`` when ``penny_classes``
does, else ``standard``, the coarsest grid, whose prices are valid on every class's grid (the desk cannot learn a
class offline, so it never prints an off-tick price). A group uses the coarsest class of its option legs. Vertical
net prices round on :data:`SPREAD_TICK` (0.01, the complex-order increment), stock prices on 0.01 (0.0001 below
1.00). Closing a short leg or a credit vertical rounds DOWN; closing a long leg or a debit vertical rounds UP, so a
take-profit level always lies inside the rule's firing region and a loss alert fires no later than the rule. Underlying levels from a model round TOWARD the current spot. The unrounded value is printed when
rounding changed it: ``0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %))``, else ``(= ...)``; an exact value
of four decimals or fewer prints as it is (trailing zeros dropped, at least two decimals), one with more prints six
(:func:`exact_value`: a level's own value rounded toward its side of the grid, and more digits while the shown value
would land on the level, so ``0.649993`` is never shown as ``0.6500``); model roots print to four decimals unless that
would show them on or across the level. The JSON ``exact`` is the unrounded value. A single option's take-profit
level that rounds down to ``0.00`` is no price (an ask of 0 is no offer): the line says the rule cannot fire on this
position below the class's lowest tick.

Lines (D1-4 to D1-8)
--------------------
One line per rule in rulebook order (a rule that reads long option legs, ``auto_exercise_itm``, prints one line per
open long option leg when the group has several, each naming its row; the evaluation's status stands on the line of
the leg it reports, the other legs' lines carry their level only), each starting with the rule id (``your rule <ID>`` when the id's leading letters
would read as an imperative sentence start to the lint, as ``ROLL30``'s do): option and spread prices from the closed
forms of the rule's metric (``pct_captured``, ``loss_multiple_of_credit``, ``pct_of_debit``, ``pct_of_max_profit``,
``pct_of_max_loss``, ``unrealized_pl`` on a single option leg; a group P/L metric on a stock-basis structure has no
single price); exact underlying prices found on the stock grid with the rule's own reading (``stock_above_short_call``,
``stock_below_long_put``, ``itm``, ``auto_exercise_itm`` on long legs, ``spot_vs_stop_pct``, ``move_pct_from_prev_eod``);
bands (``pct_from_strike`` by ``expiry.distance_pct``, ``between_strikes``); the model level of ``abs_delta`` (the
spot where ``|delta|``, read as the rule reads it, two decimals half-even, crosses: the root of ``|delta(S)| = T -
0.005`` for ``>=``, Brent's method over ``aegis_desk.greeks`` with the leg's IV, the date fixed, the book's rate and
the calendar's dividend yield); dates (``dte``, ``days_to_earnings``, ``days_to_ex_dividend``). A ``dte`` condition with
``>`` or ``>=`` that is false prints ``cannot fire on this position``, a false ``dte <=`` condition adds ``from <date>``.
Suffixes: ``threshold already crossed (rule fired)`` (fired or cooldown), ``... (pending confirmation: observation N
of M)``, for a take-profit price ``resting-eligible: yes (below the current ask A)`` (above the bid for a long, the net
cost to close or the net value for a vertical) or ``resting-eligible: unknown (...)``, and
:data:`ALERT_LEVEL_TEXT` for every other one; an unavailable rule whose level can still be computed appends ``the desk cannot read this
rule now: <reason>``. Underlying, band and date lines carry their distance when not crossed. A group whose ``data_quality`` is not ``ok`` (and is not
quarantined) says so first: ``<id> data unavailable: row N <OCC>: no usable mark (quote grade E, fresh)``. Suppressed
rules collect in one ``suppressed by your row tags:`` line; underlying and portfolio rules go in the ``## underlyings and portfolio``
section, where every rule waiting for the previous end-of-day snapshot shares one ``no level`` line.

The words of order tickets never appear: no side, no duration, no order kind; ``order_shape_enabled`` changes
nothing in this release (the tool adds :data:`ORDER_SHAPE_WARNING` to its warnings).
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import (
    alert_lint,
    alerts,
    bsm,
    calendar_events,
    exit_rules,
    expiry,
    greeks,
    grouping,
    iv,
    market_clock,
    occ,
    pnl,
)

CLASS_STANDARD: str = "standard"
CLASS_PENNY: str = "penny"
CLASS_PENNY_ALL: str = "penny_all"
TICK_CLASSES: Tuple[str, ...] = (CLASS_STANDARD, CLASS_PENNY, CLASS_PENNY_ALL)
OPTION_TICKS: Dict[str, Tuple[Decimal, Decimal]] = {
    CLASS_STANDARD: (Decimal("0.05"), Decimal("0.10")),
    CLASS_PENNY: (Decimal("0.01"), Decimal("0.05")),
    CLASS_PENNY_ALL: (Decimal("0.01"), Decimal("0.01")),
}
"""``(tick below 3.00, tick at or above 3.00)`` per option class (D1-2)."""
TICK_BREAK: Decimal = Decimal("3.00")
SPREAD_TICK: Decimal = Decimal("0.01")
STOCK_TICK: Decimal = Decimal("0.01")
SUB_DOLLAR_STOCK_TICK: Decimal = Decimal("0.0001")
ROUND_DOWN: str = "down"
ROUND_UP: str = "up"
DIRECTIONS: Tuple[str, ...] = (ROUND_DOWN, ROUND_UP)
CLASS_WORDS: Dict[str, str] = {CLASS_STANDARD: "standard class: desk.toml [levels] names no penny class for %s",
                               CLASS_PENNY: "penny class", CLASS_PENNY_ALL: "penny class at every price"}
CLASS_NAMES: Dict[str, str] = {CLASS_STANDARD: "standard class", CLASS_PENNY: "penny class",
                               CLASS_PENNY_ALL: "penny class at every price"}
_COARSENESS: Dict[str, int] = {CLASS_PENNY_ALL: 0, CLASS_PENNY: 1, CLASS_STANDARD: 2}

KIND_OPTION_PRICE: str = "option_price"
KIND_SPREAD_PRICE: str = "spread_price"
KIND_UNDERLYING_EXACT: str = "underlying_exact"
KIND_UNDERLYING_BAND: str = "underlying_band"
KIND_UNDERLYING_MODEL: str = "underlying_model"
KIND_DATE: str = "date"
KIND_NONE: str = "none"
LINE_KINDS: Tuple[str, ...] = (KIND_OPTION_PRICE, KIND_SPREAD_PRICE, KIND_UNDERLYING_EXACT, KIND_UNDERLYING_BAND,
                               KIND_UNDERLYING_MODEL, KIND_DATE, KIND_NONE)
STATUS_RESTING: str = "resting_eligible"
STATUS_ALERT: str = "alert_level"
STATUS_FIRED: str = "crossed_fired"
STATUS_PENDING: str = "crossed_pending"
STATUS_CANNOT_FIRE: str = "cannot_fire"
STATUS_NOT_APPLICABLE: str = "not_applicable"
STATUS_NO_LEVEL: str = "no_level"
STATUS_UNKNOWN: str = "unknown"
LINE_STATUSES: Tuple[str, ...] = (STATUS_RESTING, STATUS_ALERT, STATUS_FIRED, STATUS_PENDING, STATUS_CANNOT_FIRE,
                                  STATUS_NOT_APPLICABLE, STATUS_NO_LEVEL, STATUS_UNKNOWN)

SHEET_HEAD: str = "THRESHOLD LEVELS"
CARD_HEAD: str = "PLAN CARD"
SECTION_TITLE: str = "underlyings and portfolio"
SUPPRESSED_PREFIX: str = "suppressed by your row tags: "
CROSSED_FIRED_TEXT: str = "threshold already crossed (rule fired)"
CROSSED_PENDING_TEXT: str = "threshold already crossed (pending confirmation: observation %s of %s)"
ALERT_LEVEL_TEXT: str = "alert level, not an order: a limit at this price executes immediately"
RESTING_TEXT: str = "resting-eligible: yes (%s)"
RESTING_UNKNOWN_TEXT: str = "resting-eligible: unknown (%s)"
UNREADABLE_TEXT: str = "the desk cannot read this rule now: %s"
CANNOT_FIRE_DTE_TEXT: str = ("cannot fire on this position: time to expiry %s (your threshold: %s %s), and the days only "
                             "fall")
CANNOT_FIRE_SHORT_TEXT: str = "cannot fire on this position: it reads long option legs only"
BELOW_TICK_TEXT: str = ("cannot fire on this position: the option price would have to fall to %s (= %s), below the "
                        "lowest tick %s of the %s")
"""A single option's take-profit level that rounds down to 0.00: no ask at or below the exact value is a price."""
DATA_UNAVAILABLE_TEXT: str = "%s data unavailable: %s"
NO_MARK_TEXT: str = "row %d %s: no usable mark (quote grade %s, %s)"
NO_HISTORY_TEXT: str = "no level: no previous end-of-day snapshot (none for %s)"
NO_DELTA_TEXT: str = "no level: |delta| cannot reach %s at this IV"
ORDER_SHAPE_WARNING: str = ("desk.toml [levels] order_shape_enabled is true, but this release has no order-shape "
                            "template: the sheet prints alert levels only")
CALENDAR_NAME: str = "research/calendar.toml"
PLAN_DTE_DAYS: int = 21
"""The trading-day DTE the plan card names (plan section 8: the 21-trading-day date that matters)."""

DATES_KEYS: Tuple[str, ...] = ("fires_on", "from", "to", "last_trading_day", "event_date", "first_observed")
MODEL_KEYS: Tuple[str, ...] = ("iv", "tau_years", "r", "q", "delta_boundary")
LINE_KEYS: Tuple[str, ...] = ("rule_id", "kind", "status", "level", "exact", "rounding", "close_side", "current", "dates",
                              "model", "text")
GROUP_KEYS: Tuple[str, ...] = ("group_id", "trade_id", "underlying", "structure", "data_quality", "tick_class", "lines",
                               "suppressed", "price_levels", "resting_eligible")
SECTION_KEYS: Tuple[str, ...] = ("scope", "subject", "rule_ids", "lines")
SCOPE_NO_HISTORY: str = "no_history"

OPTION_METRICS: Tuple[str, ...] = ("pct_captured", "loss_multiple_of_credit", "pct_of_debit", "pct_of_max_profit",
                                   "pct_of_max_loss", "unrealized_pl")
EXACT_METRICS: Tuple[str, ...] = ("stock_above_short_call", "stock_below_long_put", "itm", "auto_exercise_itm",
                                  "spot_vs_stop_pct", "move_pct_from_prev_eod")
BAND_METRICS: Tuple[str, ...] = ("pct_from_strike", "between_strikes")
DATE_METRICS: Tuple[str, ...] = ("dte", "days_to_earnings", "days_to_ex_dividend")
OPTION_ONLY_STRUCTURES: Tuple[str, ...] = ("short_call", "cash_secured_put", "long_call", "long_put") + tuple(
    grouping.VERTICAL_STRUCTURES)

_HUNDRED = Decimal(100)
_ONE = Decimal(1)
_CENT = Decimal("0.01")
_FOUR = Decimal("0.0001")
EXACT_PLACES: int = 4
"""An exact value of at most this many decimals prints as it is."""
LONG_PLACES: int = 6
"""An exact value with more decimals prints this many (more while the shown value would land on its level)."""
MAX_PLACES: int = 12
_HALF_DELTA = Decimal("0.005")
_MAX_GRID_STEPS = 200000
_ROW_RE = re.compile(r"^row ([0-9]+) ")
_CROSSED = (exit_rules.RESULT_FIRED, exit_rules.RESULT_PENDING, exit_rules.RESULT_COOLDOWN)
_RESULT_LISTS = ("fired", "pending", "cooldown", "unavailable", "no_history", "suppressed", "clear")


class UnknownGroup(LookupError):
    """``--plan`` named a group (or trade) id the evaluation does not hold."""


# --------------------------------------------------------------------------------------------
# Ticks, rounding and the price formatter
# --------------------------------------------------------------------------------------------


def _decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise TypeError("%s must be a Decimal (or an int), never a binary float: got %r" % (what, value))
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite():
        raise ValueError("%s must be finite" % what)
    return result


def _direction(direction: str) -> str:
    if direction not in DIRECTIONS:
        raise ValueError("a rounding direction is down or up, not %r" % (direction,))
    return direction


def round_to_tick(value: Any, tick: Any, direction: str) -> Decimal:
    """``value`` floored (``down``) or ceiled (``up``) to a multiple of ``tick``, then to the cent (0.0001 when the
    tick is finer than a cent and the result is below 1.00)."""
    amount = _decimal(value, "a price")
    step = _decimal(tick, "a tick")
    if step <= 0:
        raise ValueError("a tick must be positive")
    rounding = ROUND_FLOOR if _direction(direction) == ROUND_DOWN else ROUND_CEILING
    result = (amount / step).to_integral_value(rounding=rounding) * step
    places = _FOUR if step < _CENT and abs(result) < _ONE else _CENT
    return result.quantize(places)


def option_tick(amount: Any, tick_class: str) -> Decimal:
    """The tick of an option of ``tick_class`` at the UNROUNDED ``amount`` (D1-2)."""
    if tick_class not in OPTION_TICKS:
        raise ValueError("unknown tick class %r (one of %s)" % (tick_class, ", ".join(TICK_CLASSES)))
    low, high = OPTION_TICKS[tick_class]
    return low if _decimal(amount, "a price") < TICK_BREAK else high


def round_option_price(value: Any, tick_class: str, direction: str) -> Decimal:
    return round_to_tick(value, option_tick(value, tick_class), direction)


def round_spread_price(value: Any, direction: str) -> Decimal:
    return round_to_tick(value, SPREAD_TICK, direction)


def round_stock_price(value: Any, direction: str) -> Decimal:
    amount = _decimal(value, "a price")
    return round_to_tick(amount, STOCK_TICK if amount >= _ONE else SUB_DOLLAR_STOCK_TICK, direction)


def _stock_tick(amount: Decimal) -> Decimal:
    return STOCK_TICK if amount >= _ONE else SUB_DOLLAR_STOCK_TICK


def tick_class_for(root: str, config: Any) -> str:
    """The tick class of an OCC root under ``desk.toml [levels]`` (``config`` a ``LevelsSection`` or ``None``)."""
    if root in tuple(getattr(config, "penny_all_classes", ()) or ()):
        return CLASS_PENNY_ALL
    if root in tuple(getattr(config, "penny_classes", ()) or ()):
        return CLASS_PENNY
    return CLASS_STANDARD


def _price_text(value: Decimal) -> str:
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent > -2:
        value = value.quantize(_CENT)
    return format(value, "f")


def fmt_level_price(value: Any, emitted: List[str]) -> str:
    """A price as the sheet prints it: a Decimal only, at least two decimals, every decimal kept, no ``$``; the numeric
    tokens of the text are appended to ``emitted``."""
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise TypeError("a level price is a Decimal, not %r" % (value,))
    if not value.is_finite():
        raise ValueError("a level price must be finite")
    text = _price_text(value)
    emitted.extend(token.text for token in alert_lint.numeric_tokens(text))
    return text


def exact_value(value: Any, direction: Optional[str] = None, level: Optional[Decimal] = None) -> Decimal:
    """An unrounded value as printed: as it is (trailing zeros dropped) when it has at most :data:`EXACT_PLACES`
    decimals, else :data:`LONG_PLACES` of them, half-even, or, for the value a level was rounded from (``direction``),
    rounded the same way (down: floor, up: ceiling), so the shown value stays on the value's side of every grid point;
    with ``level`` given, more decimals while the shown value would equal the level the true value is not (``0.6000003``
    rounded down to 0.60 never shows as ``0.60``)."""
    amount = _decimal(value, "an exact value")
    normal = amount.normalize()
    exponent = normal.as_tuple().exponent
    if isinstance(exponent, int) and exponent >= -EXACT_PLACES:
        return normal
    if direction is None:
        rounding = ROUND_HALF_EVEN
    else:
        rounding = ROUND_FLOOR if _direction(direction) == ROUND_DOWN else ROUND_CEILING
    places = LONG_PLACES
    while True:
        shown = amount.quantize(Decimal(1).scaleb(-places), rounding=rounding).normalize()
        if level is None or shown != level or places >= MAX_PLACES:
            return shown
        places += 1


def fmt_exact_price(value: Any, emitted: List[str], direction: Optional[str] = None,
                    level: Optional[Decimal] = None) -> str:
    """An exact (unrounded) value printed through :func:`exact_value` and :func:`fmt_level_price` (``0.610`` ->
    ``0.61``)."""
    return fmt_level_price(exact_value(_decimal(value, "an exact price"), direction, level), emitted)


def model_shown(root: Decimal, level: Decimal, direction: str) -> Decimal:
    """A model root as printed: four decimals half-even (D1-3), unless those four would sit on the level or round to
    another one, then :func:`exact_value` toward the level's side."""
    shown = root.quantize(_FOUR if root >= _ONE else Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    if root == level or (shown != level and round_stock_price(shown, direction) == level):
        return shown
    return exact_value(root, direction, level)


# --------------------------------------------------------------------------------------------
# Dates and the delta model
# --------------------------------------------------------------------------------------------


def first_date_within(anchor: datetime.date, days: int, unit: str,
                      extra_holidays: market_clock.ExtraHolidays = ()) -> datetime.date:
    """The first ET date ``d`` whose count to ``anchor`` is at most ``days``: calendar days ``(anchor - d)``, or
    trading days in ``(d, anchor]`` (``market_clock.trading_days_between``, the dte and the days-to-event readings)."""
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("days must be a non-negative integer, got %r" % (days,))
    if unit == market_clock.UNIT_CALENDAR:
        return anchor - datetime.timedelta(days=days)
    if unit != market_clock.UNIT_TRADING:
        raise ValueError("unit must be calendar or trading, got %r" % (unit,))
    day, count = anchor, 0
    while True:
        step = count + (1 if market_clock.is_trading_day(day, extra_holidays) else 0)
        if step > days:
            return day
        day -= datetime.timedelta(days=1)
        count = step


def delta_boundary(threshold: Decimal, op: str) -> Decimal:
    """Where the rule's reading of ``|delta|`` (two decimals half-even) turns: ``T - 0.005`` for ``>=`` and ``<``,
    ``T + 0.005`` for ``>`` and ``<=``."""
    if op in (">=", "<"):
        return threshold - _HALF_DELTA
    if op in (">", "<="):
        return threshold + _HALF_DELTA
    raise ValueError("a delta level needs a comparison, not %r" % (op,))


def delta_level(right: str, spot: Any, strike: Any, tau_years: Any, rate: Any, dividend_yield: Any, sigma: Any,
                threshold: Decimal, op: str) -> Optional[Decimal]:
    """The spot (unrounded, ``Decimal(repr(root))``) where ``|delta|`` reaches the reading boundary, with the IV and the
    date fixed; ``None`` when ``|delta|`` cannot reach it inside ``[spot / 100, spot x 100]``."""
    boundary = bsm.as_float("the delta boundary", delta_boundary(threshold, op))
    s0 = bsm.as_float("spot", spot)
    k = bsm.as_float("strike", strike)
    tau = bsm.as_float("tau", tau_years)
    r = bsm.as_float("rate", rate)
    q = bsm.as_float("dividend yield", dividend_yield)
    vol = bsm.as_float("sigma", sigma)

    def gap(spot_at: float) -> float:
        return abs(greeks.european_greeks(right, spot_at, k, tau, r, q, vol).delta) - boundary

    low, high = s0 / 100.0, s0 * 100.0
    try:
        f_low, f_high = gap(low), gap(high)
        if f_low == 0.0:
            return Decimal(repr(low))
        if f_high == 0.0:
            return Decimal(repr(high))
        if (f_low < 0.0) == (f_high < 0.0):
            return None
        root = iv.brent_root(gap, low, high, fa=f_low, fb=f_high)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return Decimal(repr(root.root))


# --------------------------------------------------------------------------------------------
# The records
# --------------------------------------------------------------------------------------------


@dataclass
class LevelLine:
    """One line of the sheet (``rule_id`` ``None`` for the shared no-history line and a quarantine reason)."""

    rule_id: Optional[str]
    kind: str
    status: str
    text: str
    level: Any = None
    exact: Any = None
    rounding: Optional[str] = None
    close_side: Optional[str] = None
    current: Optional[Decimal] = None
    dates: Dict[str, Optional[datetime.date]] = field(default_factory=lambda: {key: None for key in DATES_KEYS})
    model: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"rule_id": self.rule_id, "kind": self.kind, "status": self.status, "level": self.level,
                "exact": self.exact, "rounding": self.rounding, "close_side": self.close_side, "current": self.current,
                "dates": dict(self.dates), "model": None if self.model is None else dict(self.model), "text": self.text}


@dataclass
class GroupLevels:
    group_id: str
    trade_id: Optional[str]
    underlying: Optional[str]
    structure: Optional[str]
    data_quality: Optional[str]
    tick_class: Optional[str]
    header: str
    lines: List[LevelLine]
    suppressed: List[str]

    @property
    def price_levels(self) -> int:
        """Lines with a level (a price or a band)."""
        return sum(1 for line in self.lines if line.level is not None)

    @property
    def resting_eligible(self) -> int:
        return sum(1 for line in self.lines if line.status == STATUS_RESTING)

    def text_lines(self) -> List[str]:
        out = [self.header] + [line.text for line in self.lines]
        if self.suppressed:
            out.append(SUPPRESSED_PREFIX + ", ".join(list_item(rule_id) for rule_id in self.suppressed))
        return out

    def as_dict(self) -> Dict[str, Any]:
        return {"group_id": self.group_id, "trade_id": self.trade_id, "underlying": self.underlying,
                "structure": self.structure, "data_quality": self.data_quality, "tick_class": self.tick_class,
                "lines": [line.as_dict() for line in self.lines], "suppressed": list(self.suppressed),
                "price_levels": self.price_levels, "resting_eligible": self.resting_eligible}


@dataclass
class SectionLevels:
    """One line of the underlyings and portfolio section: its scope, subject and the rule ids it covers."""

    scope: str
    subject: Optional[str]
    rule_ids: List[str]
    lines: List[LevelLine]

    def as_dict(self) -> Dict[str, Any]:
        return {"scope": self.scope, "subject": self.subject, "rule_ids": list(self.rule_ids),
                "lines": [line.as_dict() for line in self.lines]}


@dataclass
class _Emitted:
    text: str
    emitted_numbers: List[str]
    rule_ids: List[str]
    identifiers: List[str]
    clocks: List[str]
    as_of: datetime.datetime

    def emitted_document(self) -> Dict[str, Any]:
        """The strict emitted document (``alert_lint.EMITTED_DOCUMENT_KEYS``) the text is linted against."""
        return {"emitted_numbers": list(self.emitted_numbers), "rule_ids": list(self.rule_ids),
                "identifiers": list(self.identifiers), "clocks": list(self.clocks),
                "as_of": self.as_of.astimezone(datetime.timezone.utc).isoformat()}

    def lint(self) -> alert_lint.LintResult:
        """``alert_lint.check`` in strict mode against the text's own emitted document."""
        return alert_lint.check(self.text, self.emitted_numbers, rule_ids=self.rule_ids, identifiers=self.identifiers,
                                clocks=self.clocks, as_of=self.as_of)


@dataclass
class Sheet(_Emitted):
    """The level sheet (module notes): ``text`` ends with the footer; ``groups`` and ``sections`` are its records."""

    et_date: datetime.date = datetime.date(1970, 1, 1)
    groups: List[GroupLevels] = field(default_factory=list)
    sections: List[SectionLevels] = field(default_factory=list)

    def group(self, group_id: str) -> Optional[GroupLevels]:
        return next((group for group in self.groups if group.group_id == group_id), None)


@dataclass
class PlanCard(_Emitted):
    """The exit-plan card of one group (``plans/<group id>.md``)."""

    group_id: str = ""


# --------------------------------------------------------------------------------------------
# The writer: every printed number through a formatter that emits it
# --------------------------------------------------------------------------------------------


class _Writer:
    def __init__(self) -> None:
        self.emitted: List[str] = []
        self.rule_ids: List[str] = []
        self.identifiers: List[str] = []
        self.clocks: List[str] = []

    @staticmethod
    def _add(bucket: List[str], text: str) -> str:
        if text not in bucket:
            bucket.append(text)
        return text

    def rule(self, rule_id: str) -> str:
        return self._add(self.rule_ids, rule_id)

    def ident(self, text: str) -> str:
        return self._add(self.identifiers, text)

    def date(self, day: datetime.date) -> str:
        return self.ident(day.isoformat())

    def clock(self, text: str) -> str:
        return self._add(self.clocks, text)

    def reason(self, text: str) -> str:
        return alerts.fmt_reason(text, self.emitted, self.rule_ids, self.identifiers, self.clocks)

    def price(self, value: Decimal) -> str:
        return fmt_level_price(value, self.emitted)

    def exact(self, value: Decimal, direction: Optional[str] = None, level: Optional[Decimal] = None) -> str:
        return fmt_exact_price(value, self.emitted, direction, level)

    def count(self, value: int) -> str:
        return alerts.fmt_count(value, self.emitted)

    def days(self, value: Any, unit: Optional[str]) -> str:
        return alerts.fmt_days(value, self.emitted, unit)

    def pct(self, value: Any) -> str:
        return alerts.fmt_pct(value, self.emitted)

    def threshold(self, value: Any, unit: Optional[str], dte_unit: Optional[str] = None) -> str:
        return alerts.fmt_threshold(value, unit, self.emitted, dte_unit)

    def strike(self, value: Decimal) -> str:
        return alerts.fmt_strike(value, self.emitted)

    def money(self, value: Decimal) -> str:
        return alerts.fmt_money(value, self.emitted)

    def dollars(self, value: Decimal) -> str:
        return alerts.fmt_price(value, self.emitted)

    def delta(self, value: Any) -> str:
        return alerts.fmt_delta(value, self.emitted)

    def number(self, value: Any) -> str:
        return alerts.fmt_number(value, self.emitted)

    def multiple(self, value: Any) -> str:
        return alerts.fmt_multiple(value, self.emitted)

    def footer(self, as_of: datetime.datetime, delay_min: int) -> str:
        return alerts.fmt_footer(as_of, delay_min, self.emitted, self.clocks)

    def value_words(self, metric: str, value: Any, unit: Optional[str], dte_unit: Optional[str]) -> Optional[str]:
        """A metric's current value as the alerts print it (``None`` when unknown)."""
        if value is None:
            return None
        if isinstance(value, bool):
            return "yes" if value else "no"
        unit = unit or alerts.METRIC_UNITS.get(metric)
        if unit in ("percent", "pct_of_threshold"):
            return self.pct(value)
        if unit == "usd":
            if metric in alerts.PER_SHARE_METRICS:
                return self.dollars(value) + " per share"
            return self.money(value)
        if unit == "days":
            return self.days(value, dte_unit or alerts.DEFAULT_DAY_UNITS.get(metric))
        if unit == "delta":
            return self.delta(value)
        if unit == "multiple":
            return self.multiple(value) + " x"
        return self.number(value)

    def threshold_words(self, condition: exit_rules.Condition) -> str:
        if condition.op in exit_rules.BOOL_OPS:
            return alerts.BOOL_OPS[condition.op]
        unit = condition.unit or alerts.METRIC_UNITS.get(condition.metric)
        threshold = self.threshold(condition.value, unit,
                                   condition.dte_unit or alerts.DEFAULT_DAY_UNITS.get(condition.metric))
        return "%s %s" % (alerts.OP_WORDS.get(condition.op, condition.op), threshold)


# --------------------------------------------------------------------------------------------
# The book indexed for the levels
# --------------------------------------------------------------------------------------------


class _Book:
    def __init__(self, document: Mapping[str, Any], calendar: Optional[calendar_events.CalendarFile],
                 levels_config: Any, today: datetime.date, history_date: Optional[datetime.date] = None) -> None:
        self.document = document
        self.legs: Dict[int, Mapping[str, Any]] = {leg["row_number"]: leg for leg in document["legs"]}
        self.groups: Dict[str, Mapping[str, Any]] = {str(group["group_id"]): group for group in document["groups"]}
        self.underlyings: Mapping[str, Mapping[str, Any]] = document["underlyings"]
        self.calendar = calendar
        self.extra = calendar_events.extra_closures(calendar)
        self.config = levels_config
        self.today = today
        self.history_date = history_date
        rates = document.get("rates") or {}
        self.rate = rates.get("risk_free_rate")

    def open_legs(self, group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        return [self.legs[row] for row in group["legs"] if row in self.legs and self.legs[row]["status"] == "open"]

    def option_legs(self, group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        return [leg for leg in self.open_legs(group) if leg.get("occ")]

    def leg_of(self, result: exit_rules.RuleResult) -> Optional[Mapping[str, Any]]:
        match = _ROW_RE.match(result.subject or "")
        return self.legs.get(int(match.group(1))) if match else None

    def spot(self, symbol: Optional[str]) -> Optional[Decimal]:
        entry = self.underlyings.get(str(symbol)) if symbol is not None else None
        return entry.get("mark_mid") if isinstance(entry, Mapping) else None

    def last_trading_day(self, leg: Mapping[str, Any]) -> Optional[datetime.date]:
        expiration = leg.get("expiry")
        if not isinstance(expiration, datetime.date):
            return None
        am = leg.get("settlement") == "am"
        return market_clock.last_trading_day(expiration, am, self.extra)

    def nearest_leg(self, legs: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
        dated = [(self.last_trading_day(leg), leg["row_number"], leg) for leg in legs]
        dated = [item for item in dated if item[0] is not None]
        return min(dated, key=lambda item: (item[0], item[1]))[2] if dated else None

    def group_class(self, group: Mapping[str, Any]) -> Tuple[Optional[str], List[str]]:
        """``(coarsest class, the standard roots)`` of a group's open option legs (``(None, [])`` without one)."""
        roots: List[str] = []
        for leg in self.option_legs(group):
            root = occ.parse(str(leg["occ"])).root
            if root not in roots:
                roots.append(root)
        if not roots:
            return None, []
        classes = [tick_class_for(root, self.config) for root in roots]
        coarsest = max(classes, key=lambda name: _COARSENESS[name])
        return coarsest, [root for root, name in zip(roots, classes) if name == CLASS_STANDARD]


def _imperative_lead(rule_id: str) -> bool:
    """True when the lint would read ``rule_id`` at a sentence start as an imperative (``ROLL30`` starts with
    ``roll``): its leading letters are one of ``alert_lint.IMPERATIVE_START_WORDS``."""
    match = re.match(r"[A-Za-z]+", rule_id)
    return match is not None and match.group(0).lower() in alert_lint.IMPERATIVE_START_WORDS


def line_lead(rule_id: str) -> str:
    """How a rule id starts a sheet line: the id itself, or ``your rule <ID>`` when its leading letters would read as
    an imperative sentence start (a line ``ROLL30 fires ...`` fails the lint; ``your rule ROLL30 fires ...`` passes)."""
    return "your rule %s" % rule_id if _imperative_lead(rule_id) else rule_id


def list_item(rule_id: str) -> str:
    """How a rule id stands in a comma list (every comma is a sentence boundary for the lint): ``rule <ID>`` when its
    leading letters would read as an imperative, else the id."""
    return "rule %s" % rule_id if _imperative_lead(rule_id) else rule_id


def _condition(result: exit_rules.RuleResult, index: int) -> Optional[exit_rules.ConditionResult]:
    return result.conditions[index] if index < len(result.conditions) else None


def _signed_percent(writer: _Writer, threshold: Decimal, sign: str) -> str:
    """``(1 - 80 %)`` / ``(1 + 100 %)``: ``sign`` applied to a non-negative threshold, a negative one flips it."""
    if threshold < 0:
        sign = "+" if sign == "-" else "-"
        threshold = -threshold
    return "(%s %s %s)" % (writer.count(1), sign, writer.threshold(threshold, "percent"))


# --------------------------------------------------------------------------------------------
# One rule's line
# --------------------------------------------------------------------------------------------


@dataclass
class _Body:
    """The pieces of a level line before its suffix: ``head`` after the rule id, the parenthesis parts, a tail."""

    kind: str
    head: str
    parts: List[str] = field(default_factory=list)
    tail: str = ""
    level: Any = None
    exact: Any = None
    rounding: Optional[str] = None
    close_side: Optional[str] = None
    current: Optional[Decimal] = None
    resting: Optional[str] = None  # ask | bid | net_credit | net_debit: the take-profit resting test
    side_missing: Optional[str] = None
    model: Optional[Dict[str, Any]] = None
    dates: Dict[str, Any] = field(default_factory=dict)
    status: Optional[str] = None  # a terminal line (cannot fire, no level) carries its status and no suffix


class _NeedsData(Exception):
    """The level cannot be computed from what the book holds (the line says why the rule cannot be read)."""


class _LineMaker:
    def __init__(self, writer: _Writer, book: _Book, rule: exit_rules.Rule, result: exit_rules.RuleResult,
                 group: Optional[Mapping[str, Any]], tick_class: Optional[str], symbol: Optional[str],
                 leg: Optional[Mapping[str, Any]] = None, representative: bool = True) -> None:
        self.w = writer
        self.book = book
        self.rule = rule
        self.result = result
        self.group = group
        self.tick_class = tick_class or CLASS_STANDARD
        self.symbol = symbol
        self.crossed = result.result in _CROSSED
        self.leg = book.leg_of(result) if leg is None else leg
        self.named = leg is not None
        """The line names its leg (``for row N <OCC>``): one of several lines of a rule that reads every long leg."""
        self.representative = representative
        """The evaluation reports this line's subject; another leg's line carries its level only (no status)."""

    # -- the line ---------------------------------------------------------------------------
    def make(self) -> LevelLine:
        rule_id = self.w.rule(self.rule.id)
        lead = line_lead(rule_id)
        result = self.result
        if result.result == exit_rules.RESULT_CLEAR and result.reason.startswith("not applicable: "):
            return LevelLine(rule_id, KIND_NONE, STATUS_NOT_APPLICABLE, "%s %s" % (lead, self.w.reason(result.reason)))
        for index, condition in enumerate(self.rule.when):
            outcome = _condition(result, index)
            if (condition.metric == "dte" and condition.op in (">", ">=") and outcome is not None
                    and outcome.truth == exit_rules.FALSE and outcome.value is not None):
                text = CANNOT_FIRE_DTE_TEXT % (self.w.days(outcome.value, condition.dte_unit),
                                               alerts.OP_WORDS[condition.op],
                                               self.w.threshold(condition.value, "days", condition.dte_unit))
                return LevelLine(rule_id, KIND_NONE, STATUS_CANNOT_FIRE, "%s %s" % (lead, text))
        try:
            body = self.body()
        except _NeedsData:
            return self.unreadable(rule_id)
        if body.status is not None:
            return LevelLine(rule_id, body.kind, body.status, "%s %s" % (lead, body.head), dates=self.dates(body))
        text = "%s %s" % (lead, body.head)
        if body.parts:
            text += " (%s)" % "; ".join(body.parts)
        text += body.tail + self.secondary(body)
        status, suffix = self.suffix(body)
        if suffix:
            text += "; " + suffix
        if result.result == exit_rules.RESULT_UNAVAILABLE and self.representative:
            text += "; " + UNREADABLE_TEXT % self.w.reason(result.reason or "an input is unavailable")
            status = STATUS_UNKNOWN
        return LevelLine(rule_id, body.kind, status, text, level=body.level, exact=body.exact, rounding=body.rounding,
                         close_side=body.close_side, current=body.current, dates=self.dates(body), model=body.model)

    def unreadable(self, rule_id: str) -> LevelLine:
        reason = self.result.reason or "an input is unavailable"
        return LevelLine(rule_id, KIND_NONE, STATUS_UNKNOWN,
                         "%s no level: %s" % (line_lead(rule_id), UNREADABLE_TEXT % self.w.reason(reason)))

    @staticmethod
    def dates(body: _Body) -> Dict[str, Any]:
        out: Dict[str, Any] = {key: None for key in DATES_KEYS}
        out.update(body.dates)
        return out

    def suffix(self, body: _Body) -> Tuple[str, str]:
        result = self.result
        if not self.representative:
            return STATUS_ALERT, ALERT_LEVEL_TEXT if body.kind in (KIND_OPTION_PRICE, KIND_SPREAD_PRICE) else ""
        if result.result in (exit_rules.RESULT_FIRED, exit_rules.RESULT_COOLDOWN):
            return STATUS_FIRED, CROSSED_FIRED_TEXT
        if result.result == exit_rules.RESULT_PENDING:
            return STATUS_PENDING, CROSSED_PENDING_TEXT % (self.w.count(result.observations),
                                                           self.w.count(result.confirmations))
        if body.kind not in (KIND_OPTION_PRICE, KIND_SPREAD_PRICE):
            return STATUS_ALERT, ""
        if self.rule.role != exit_rules.ROLE_TAKE_PROFIT:
            return STATUS_ALERT, ALERT_LEVEL_TEXT
        if body.side_missing is not None:
            return STATUS_UNKNOWN, RESTING_UNKNOWN_TEXT % body.side_missing
        current, level = body.current, body.level
        if current is None or level is None:
            return STATUS_UNKNOWN, RESTING_UNKNOWN_TEXT % "no fresh quote"
        below = body.resting in ("ask", "net_credit")
        if (below and level < current) or (not below and level > current):
            where = {"ask": "below the current ask %s", "bid": "above the current bid %s",
                     "net_credit": "below the current net cost to close %s",
                     "net_debit": "above the current net value %s"}[str(body.resting)]
            return STATUS_RESTING, RESTING_TEXT % (where % self.w.price(current))
        return STATUS_ALERT, ALERT_LEVEL_TEXT

    # -- secondary conditions: a later date, another reading -----------------------------
    def secondary(self, body: _Body) -> str:
        primary = self.rule.primary
        text = ""
        for index, condition in enumerate(self.rule.when):
            if index == 0:
                continue
            outcome = _condition(self.result, index)
            if outcome is None or outcome.truth != exit_rules.FALSE:
                continue
            if condition.metric == "dte" and condition.op in ("<=", "<", "=="):
                piece = self.dte_piece(condition)
                if piece is not None:
                    start, words, extra = piece
                    body.dates.setdefault("from", start)
                    body.dates.update(extra)
                    text += " from %s" % words
            elif condition.metric != primary.metric and condition.metric != "dte":
                label = alerts.METRIC_LABELS.get(condition.metric, condition.metric.replace("_", " "))
                now = self.w.value_words(condition.metric, outcome.value, outcome.unit, outcome.dte_unit)
                text += " once %s is %s" % (label, self.w.threshold_words(condition))
                if now is not None:
                    text += " (now %s)" % now
        return text

    def dte_piece(self, condition: exit_rules.Condition) -> Optional[Tuple[datetime.date, str, Dict[str, Any]]]:
        """``(date, '<date>[ (first observed on <date>)] (<N> <unit> days before the last trading day <LTD>)', dates)``
        of a ``dte <= N`` condition on this rule's subject, or ``None`` without an option leg."""
        leg = self.leg if self.leg is not None and self.leg.get("occ") else None
        if leg is None and self.group is not None:
            leg = self.book.nearest_leg(self.book.option_legs(self.group))
        if leg is None or condition.value is None:
            return None
        last_day = self.book.last_trading_day(leg)
        if last_day is None:
            return None
        days = int(condition.value) - (1 if condition.op == "<" else 0)
        if days < 0:
            return None
        unit = str(condition.dte_unit)
        start = first_date_within(last_day, days, unit, self.book.extra)
        words = self.w.date(start)
        extra: Dict[str, Any] = {"last_trading_day": last_day}
        if not market_clock.is_trading_day(start, self.book.extra):
            observed = market_clock.next_trading_day(start, self.book.extra)
            words += " (first observed on %s)" % self.w.date(observed)
            extra["first_observed"] = observed
        words += " (%s before the last trading day %s)" % (self.w.days(days, unit), self.w.date(last_day))
        return start, words, extra

    # -- the primary condition ----------------------------------------------------------
    def body(self) -> _Body:
        primary = self.rule.primary
        metric = primary.metric
        if metric in OPTION_METRICS:
            return self.option_body(primary)
        if metric in EXACT_METRICS:
            return self.exact_body(primary)
        if metric in BAND_METRICS:
            return self.band_body(primary)
        if metric == "abs_delta":
            return self.model_body(primary)
        if metric in DATE_METRICS:
            return self.date_body(primary)
        return self.no_single_price(primary)

    def no_single_price(self, condition: exit_rules.Condition) -> _Body:
        outcome = _condition(self.result, 0)
        label = alerts.METRIC_LABELS.get(condition.metric, condition.metric.replace("_", " "))
        now = (self.w.value_words(condition.metric, outcome.value, outcome.unit, outcome.dte_unit)
               if outcome is not None else None)
        reads = label if now is None else "%s, now %s" % (label, now)
        head = "has no single price level (it reads %s; your threshold: %s)" % (reads, self.w.threshold_words(condition))
        return _Body(KIND_NONE, head)

    # option and spread prices ---------------------------------------------------------
    def option_body(self, condition: exit_rules.Condition) -> _Body:
        metric, threshold = condition.metric, condition.value
        if threshold is None:
            return self.no_single_price(condition)
        leg_level = self.leg is not None and self.group is not None and metric == "pct_captured"
        if leg_level:
            assert self.leg is not None
            leg = self.leg
            if leg["open_quantity"] >= 0:
                raise _NeedsData()
            entry = leg.get("entry_price_per_share")
            if entry is None or entry <= 0:
                raise _NeedsData()
            exact = entry * (_ONE - threshold / _HUNDRED)
            return self.option_price(exact, True, [leg], None, lambda: "credit %s x %s" % (
                self.w.exact(entry), _signed_percent(self.w, threshold, "-")))
        group = self.group
        if group is None:
            return self.no_single_price(condition)
        structure = str(group.get("structure"))
        options = self.book.option_legs(group)
        stock = [leg for leg in self.book.open_legs(group) if not leg.get("occ")]
        vertical = structure in grouping.VERTICAL_STRUCTURES
        if stock or structure not in OPTION_ONLY_STRUCTURES or (vertical and len(options) != 2) or (
                not vertical and len(options) != 1):
            return self.no_single_price(condition)
        shorts = [leg for leg in options if leg["open_quantity"] < 0]
        longs = [leg for leg in options if leg["open_quantity"] > 0]
        net_cost = group.get("net_cost")
        if net_cost is None or net_cost == 0:
            raise _NeedsData()
        credit = net_cost < 0
        anchor = (shorts or longs)[0] if vertical else options[0]
        size = abs(anchor["open_quantity"]) * anchor["multiplier"]
        if size == 0:
            raise _NeedsData()
        per_share = abs(net_cost) / size
        formula: Callable[[], str]
        if metric == "pct_captured":
            if not credit:
                raise _NeedsData()
            exact = per_share * (_ONE - threshold / _HUNDRED)
            formula = lambda: "credit %s x %s" % (self.w.exact(per_share), _signed_percent(self.w, threshold, "-"))  # noqa: E731
        elif metric == "loss_multiple_of_credit":
            if not credit:
                raise _NeedsData()
            exact = per_share * (_ONE + threshold)
            formula = lambda: "credit %s x (%s + %s)" % (self.w.exact(per_share), self.w.count(1),  # noqa: E731
                                                          self.w.threshold(threshold, "multiple"))
        elif metric == "pct_of_debit":
            if credit:
                raise _NeedsData()
            exact = per_share * (_ONE + threshold / _HUNDRED)
            formula = lambda: "debit %s x %s" % (self.w.exact(per_share), _signed_percent(self.w, threshold, "+"))  # noqa: E731
        elif metric == "pct_of_max_profit":
            max_profit = group.get("max_profit")
            if credit or max_profit is None or max_profit <= 0:
                raise _NeedsData()
            profit_ps = max_profit / size
            exact = per_share + threshold / _HUNDRED * profit_ps
            formula = lambda: "debit %s + %s of the max profit %s" % (  # noqa: E731
                self.w.exact(per_share), self.w.threshold(threshold, "percent"), self.w.exact(profit_ps))
        elif metric == "pct_of_max_loss":
            max_loss = group.get("max_loss")
            if max_loss is None or max_loss <= 0:
                raise _NeedsData()
            loss_ps = max_loss / size
            if credit:
                exact = per_share + threshold / _HUNDRED * loss_ps
                formula = lambda: "credit %s + %s of the max loss %s" % (  # noqa: E731
                    self.w.exact(per_share), self.w.threshold(threshold, "percent"), self.w.exact(loss_ps))
            else:
                exact = per_share - threshold / _HUNDRED * loss_ps
                if max_loss == net_cost:
                    formula = lambda: "debit %s x %s" % (self.w.exact(per_share),  # noqa: E731
                                                         _signed_percent(self.w, threshold, "-"))
                else:
                    formula = lambda: "debit %s - %s of the max loss %s" % (  # noqa: E731
                        self.w.exact(per_share), self.w.threshold(threshold, "percent"), self.w.exact(loss_ps))
        else:  # unrealized_pl on a single option leg
            if vertical:
                return self.no_single_price(condition)
            leg = options[0]
            basis = leg.get("remaining_cost_basis")
            if basis is None:
                raise _NeedsData()
            quantity, multiplier = leg["open_quantity"], leg["multiplier"]
            exact = (threshold + basis) / (quantity * multiplier)
            formula = lambda: "(P/L %s + cost basis %s) / (%s x %s shares)" % (  # noqa: E731
                self.w.threshold(threshold, "usd"), self.w.money(basis), self.w.number(quantity),
                self.w.count(int(multiplier)))
        return self.option_price(exact, credit, options, (shorts, longs) if vertical else None, formula)

    def option_price(self, exact: Decimal, credit: bool, legs: Sequence[Mapping[str, Any]],
                     vertical: Optional[Tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]],
                     formula: Callable[[], str]) -> _Body:
        direction = ROUND_DOWN if credit else ROUND_UP
        if vertical is not None:
            strikes = sorted(expiry.plain_decimal(leg["strike"]) for leg in legs if leg.get("strike") is not None)
            width = strikes[-1] - strikes[0] if len(strikes) == 2 else None
            if exact < 0:
                return _Body(KIND_NONE, "cannot fire on this position: the spread price would have to fall to %s, "
                                        "below zero" % self.w.exact(exact), status=STATUS_CANNOT_FIRE)
            if width is not None and exact > width:
                return _Body(KIND_NONE, "cannot fire on this position: the spread price would have to reach %s, beyond "
                                        "the strike width %s" % (self.w.exact(exact), self.w.strike(width)),
                             status=STATUS_CANNOT_FIRE)
            level = round_spread_price(exact, direction)
            kind, words = KIND_SPREAD_PRICE, "a spread price"
        else:
            if exact < 0:
                return _Body(KIND_NONE, "cannot fire on this position: the option price would have to fall to %s, "
                                        "below zero" % self.w.exact(exact), status=STATUS_CANNOT_FIRE)
            level = round_option_price(exact, self.tick_class, direction)
            kind, words = KIND_OPTION_PRICE, "an option price"
            if credit and level <= 0:
                # no ask at or below the exact value is on the grid (its lowest tick is above it) and an ask of 0 is no
                # offer (the rule reads it as unavailable), so the rule cannot fire on this position at this class
                return _Body(KIND_NONE, BELOW_TICK_TEXT % (self.w.exact(exact), formula(),
                                                           self.w.price(OPTION_TICKS[self.tick_class][0]),
                                                           CLASS_NAMES[self.tick_class]), status=STATUS_CANNOT_FIRE)
        changed = level != exact
        head = "fires at %s of %s" % (words, self.w.price(level))
        part = ("rounded %s from %s = %s" % (direction, self.w.exact(exact, direction, level), formula()) if changed
                else "= %s" % formula())
        body = _Body(kind, head, [part], level=level, exact=exact, rounding=direction if changed else None)
        liq = self.rule.mark_basis == exit_rules.BASIS_LIQ
        if vertical is not None:
            shorts, longs = vertical
            asks = [leg.get("fresh_ask") for leg in shorts]
            bids = [leg.get("fresh_bid") for leg in longs]
            body.resting = "net_credit" if credit else "net_debit"
            body.close_side = "net" if liq else None
            if any(value is None for value in asks):
                body.side_missing = "no fresh ask"
            elif any(value is None for value in bids):
                body.side_missing = "no fresh bid"
            elif any(value == 0 for value in asks):
                body.side_missing = "no offer: ask 0"
            else:
                ask = sum((value for value in asks), Decimal(0))
                bid = sum((value for value in bids), Decimal(0))
                body.current = ask - bid if credit else bid - ask
            return body
        leg = legs[0]
        if credit:
            body.resting, body.close_side = "ask", ("ask" if liq else None)
            value = leg.get("fresh_ask")
            if value is None:
                body.side_missing = "no fresh ask"
            elif value == 0:
                body.side_missing = "no offer: ask 0"
            body.current = value
        else:
            body.resting, body.close_side = "bid", ("bid" if liq else None)
            value = leg.get("fresh_bid")
            if value is None:
                body.side_missing = "no fresh bid"
            body.current = value
        return body

    # exact underlying prices and bands -----------------------------------------------
    def distance(self, level: Decimal, spot: Optional[Decimal]) -> Optional[str]:
        if spot is None or spot <= 0 or self.crossed or not self.representative:
            return None
        if level == spot:
            return "at the current %s" % self.w.price(spot)
        pct = pnl.quantize_pct(abs(level - spot) / spot * _HUNDRED)
        return "%s %s the current %s" % (self.w.pct(pct), "above" if level > spot else "below", self.w.price(spot))

    def underlying_symbol(self) -> Optional[str]:
        if self.leg is not None:
            return str(self.leg.get("underlying"))
        if self.group is not None:
            return str(self.group.get("underlying"))
        return self.symbol

    def exact_line(self, level: Decimal, above: bool, words: str, tail: str = "") -> _Body:
        symbol = self.underlying_symbol()
        head = "fires at %s %s or %s" % (self.w.reason(str(symbol)), self.w.price(level), "above" if above else "below")
        parts = [words]
        distance = self.distance(level, self.book.spot(symbol))
        if distance is not None:
            parts.append(distance)
        return _Body(KIND_UNDERLYING_EXACT, head, parts, tail=tail, level=level, exact=level,
                     current=self.book.spot(symbol))

    def strike_words(self, strike: Decimal, above: bool, is_true: bool) -> str:
        if is_true:
            return "one cent %s the strike %s" % ("above" if above else "below", self.w.strike(strike))
        return "at or %s the strike %s" % ("above" if above else "below", self.w.strike(strike))

    def exact_body(self, condition: exit_rules.Condition) -> _Body:
        metric = condition.metric
        if metric in ("stock_above_short_call", "stock_below_long_put", "itm", "auto_exercise_itm"):
            return self.strike_body(condition)
        if condition.op not in ("<", "<=", ">", ">=") or condition.value is None:
            return self.no_single_price(condition)
        threshold = condition.value
        if metric == "spot_vs_stop_pct":
            group = self.group
            stop = group.get("underlying_stop") if group is not None else None
            if stop is None or stop <= 0:
                raise _NeedsData()

            def reading(spot_at: Decimal) -> bool:
                return exit_rules.compare((spot_at - stop) / stop * _HUNDRED, condition.op, threshold)

            start = stop * (_ONE + threshold / _HUNDRED)
            words = ("your stop in positions.csv" if threshold == 0 else
                     "%s from your stop %s in positions.csv" % (self.w.threshold(threshold, "percent"), self.w.price(stop)))
        else:  # move_pct_from_prev_eod
            entry = self.book.underlyings.get(str(self.symbol)) or {}
            previous = entry.get("prev_eod_mid")
            if previous is None or previous <= 0:
                raise _NeedsData()

            def reading(spot_at: Decimal) -> bool:
                return exit_rules.compare(pnl.quantize_pct((spot_at - previous) / previous * _HUNDRED), condition.op,
                                          threshold)

            start = previous * (_ONE + threshold / _HUNDRED)
            words = "%s from the previous end-of-day mid %s" % (self.w.threshold(threshold, "percent"),
                                                                self.w.price(previous))
        above = condition.op in (">", ">=")
        level = _grid_edge(reading, start, "min" if above else "max")
        if level is None:
            raise _NeedsData()
        return self.exact_line(level, above, words)

    def strike_body(self, condition: exit_rules.Condition) -> _Body:
        metric = condition.metric
        if condition.op not in exit_rules.BOOL_OPS:
            return self.no_single_price(condition)
        is_true = condition.op == "is_true"
        tail = ""
        if metric in ("stock_above_short_call", "stock_below_long_put"):
            group = self.group
            if group is None:
                raise _NeedsData()
            call = metric == "stock_above_short_call"
            legs = [leg for leg in self.book.option_legs(group) if leg.get("right") == (occ.CALL if call else occ.PUT)
                    and (leg["open_quantity"] < 0) == call]
            strikes = [expiry.plain_decimal(leg["strike"]) for leg in legs if leg.get("strike") is not None]
            if not strikes:
                raise _NeedsData()
            strike = min(strikes) if call else max(strikes)
        else:
            leg = self.leg
            if metric == "auto_exercise_itm":
                if leg is None or leg["open_quantity"] <= 0:
                    longs = ([item for item in self.book.option_legs(self.group) if item["open_quantity"] > 0]
                             if self.group is not None else [])
                    if not longs:
                        return _Body(KIND_NONE, CANNOT_FIRE_SHORT_TEXT, status=STATUS_CANNOT_FIRE)
                    leg = longs[0]
                last_day = self.book.last_trading_day(leg)
                if last_day is not None:
                    tail = " on the last trading day %s" % self.w.date(last_day)
                if self.named:
                    tail += " for %s" % self.w.reason("row %d %s" % (leg["row_number"], leg.get("occ") or leg.get("symbol")))
            if leg is None or leg.get("strike") is None:
                raise _NeedsData()
            call = leg.get("right") == occ.CALL
            strike = expiry.plain_decimal(leg["strike"])
        above = call if is_true else not call
        level = (strike + expiry.PENNY if call else strike - expiry.PENNY) if is_true else strike
        body = self.exact_line(level, above, self.strike_words(strike, above, is_true), tail)
        if metric == "auto_exercise_itm" and tail:
            body.dates["last_trading_day"] = self.book.last_trading_day(leg)
        return body

    def band_body(self, condition: exit_rules.Condition) -> _Body:
        symbol = self.underlying_symbol()
        if condition.metric == "between_strikes":
            group = self.group
            if group is None or condition.op not in exit_rules.BOOL_OPS:
                return self.no_single_price(condition)
            strikes = sorted({expiry.plain_decimal(leg["strike"]) for leg in self.book.option_legs(group)
                              if leg.get("strike") is not None})
            if len(strikes) != 2:
                raise _NeedsData()
            inside = condition.op == "is_true"
            # the rule reads K_low < S < K_high: inside on the cent grid is K_low + 0.01 .. K_high - 0.01
            low, high = (strikes[0] + expiry.PENNY, strikes[1] - expiry.PENNY) if inside else (strikes[0], strikes[1])
            words = "%s the strikes %s and %s" % ("between" if inside else "outside", self.w.strike(strikes[0]),
                                                   self.w.strike(strikes[1]))
        else:
            leg = self.leg
            if leg is None or leg.get("strike") is None or condition.value is None or condition.op not in (
                    "<", "<=", ">", ">="):
                return self.no_single_price(condition)
            strike = expiry.plain_decimal(leg["strike"])
            threshold = condition.value
            inside = condition.op in ("<", "<=")

            def reading(spot_at: Decimal) -> bool:
                return exit_rules.compare(expiry.distance_pct(spot_at, strike), condition.op, threshold)

            if inside:
                if not reading(strike) or threshold >= _HUNDRED:
                    raise _NeedsData()
                found_low = _grid_edge(reading, strike / (_ONE + threshold / _HUNDRED), "min")
                found_high = _grid_edge(reading, strike / (_ONE - threshold / _HUNDRED), "max")
            else:
                if threshold >= _HUNDRED:
                    raise _NeedsData()
                found_low = _grid_edge(lambda spot_at: reading(spot_at) and spot_at <= strike,
                                       strike / (_ONE + threshold / _HUNDRED), "max")
                found_high = _grid_edge(lambda spot_at: reading(spot_at) and spot_at >= strike,
                                        strike / (_ONE - threshold / _HUNDRED), "min")
            if found_low is None or found_high is None:
                raise _NeedsData()
            low, high = found_low, found_high
            words = ("within %s of the strike %s" if inside else "at least %s from the strike %s") % (
                self.w.threshold(threshold, "percent"), self.w.strike(strike))
        if inside:
            head = "fires while %s is between %s and %s" % (self.w.reason(str(symbol)), self.w.price(low),
                                                            self.w.price(high))
        else:
            head = "fires while %s is at or below %s or at or above %s" % (self.w.reason(str(symbol)), self.w.price(low),
                                                                           self.w.price(high))
        level = {"low": low, "high": high}
        return _Body(KIND_UNDERLYING_BAND, head, [words], level=level, exact=dict(level),
                     current=self.book.spot(symbol))

    # the delta model ---------------------------------------------------------------------
    def model_body(self, condition: exit_rules.Condition) -> _Body:
        leg = self.leg
        if leg is None or condition.value is None or condition.op not in ("<", "<=", ">", ">="):
            return self.no_single_price(condition)
        symbol = str(leg.get("underlying"))
        spot = self.book.spot(symbol)
        sigma, tau = leg.get("iv"), leg.get("tau_years")
        if (leg.get("iv_status") != "ok" or spot is None or spot <= 0 or not isinstance(sigma, float)
                or not isinstance(tau, float) or self.book.rate is None or leg.get("strike") is None):
            raise _NeedsData()
        dividend = calendar_events.dividend_yield(self.book.calendar, symbol)
        rate = bsm.as_float("rate", self.book.rate)
        q = bsm.as_float("dividend yield", dividend) if dividend is not None else 0.0
        threshold = condition.value
        root = delta_level(str(leg["right"]), spot, leg["strike"], tau, rate, q, sigma, threshold, condition.op)
        if root is None:
            return _Body(KIND_NONE, NO_DELTA_TEXT % self.w.threshold(threshold, "delta"), status=STATUS_NO_LEVEL)
        direction = ROUND_UP if root < spot else ROUND_DOWN
        level = round_stock_price(root, direction)
        shown = model_shown(root, level, direction)
        parts: List[str] = []
        changed = level != root
        if changed:
            parts.append("rounded %s from %s" % (direction, self.w.price(shown)))
        parts.append("model estimate at IV %s, date fixed" % self.w.pct(pnl.quantize_pct(Decimal(repr(sigma)) * _HUNDRED)))
        distance = self.distance(level, spot)
        if distance is not None:
            one_sigma = Decimal(repr(sigma)) * Decimal(repr(math.sqrt(tau))) * spot
            moves = (abs(level - spot) / one_sigma).quantize(_CENT, rounding=ROUND_HALF_EVEN)
            parts.append("%s, %s sigma" % (distance, self.w.delta(moves)))
        head = "fires near %s %s" % (self.w.reason(symbol), self.w.price(level))
        model = {"iv": sigma, "tau_years": tau, "r": rate, "q": q, "delta_boundary": delta_boundary(threshold, condition.op)}
        return _Body(KIND_UNDERLYING_MODEL, head, parts, level=level, exact=root,
                     rounding=direction if changed else None, current=spot, model=model)

    # dates -----------------------------------------------------------------------------
    def date_body(self, condition: exit_rules.Condition) -> _Body:
        metric = condition.metric
        if metric == "dte":
            if condition.op not in ("<=", "<", "=="):
                return self.no_single_price(condition)
            piece = self.dte_piece(condition)
            if piece is None:
                raise _NeedsData()
            start, words, extra = piece
            body = _Body(KIND_DATE, "fires on %s" % words)
            body.dates.update(extra)
            body.dates["fires_on"] = start
            return body
        if metric == "days_to_earnings":
            return self.earnings_body()
        return self.ex_dividend_body(condition)

    def window(self, metric: str) -> Tuple[Optional[int], int]:
        """``(hi, lo)`` days of the rule's conditions on ``metric`` (``hi`` ``None`` without an upper bound)."""
        high: Optional[int] = None
        low = 0
        for condition in self.rule.when:
            if condition.metric != metric or condition.value is None:
                continue
            value = int(condition.value)
            if condition.op in ("<=", "=="):
                high = value if high is None else min(high, value)
            if condition.op == "<":
                high = value - 1 if high is None else min(high, value - 1)
            if condition.op in (">=", "=="):
                low = max(low, value)
            if condition.op == ">":
                low = max(low, value + 1)
        return high, low

    def earnings_body(self) -> _Body:
        high, low = self.window("days_to_earnings")
        if high is None or high < low:
            return self.no_single_price(self.rule.primary)
        events = calendar_events.events_for(self.book.calendar, str(self.underlying_symbol()))
        entry = events.earnings
        if entry is None or entry.date is None or entry.status not in (calendar_events.STATUS_CONFIRMED,
                                                                         calendar_events.STATUS_ESTIMATED):
            raise _NeedsData()
        event = entry.date
        first = first_date_within(event, high, market_clock.UNIT_TRADING, self.book.extra)
        last = first_date_within(event, low, market_clock.UNIT_TRADING, self.book.extra)
        span = ("on %s" % self.w.date(first) if first == last else
                "from %s to %s" % (self.w.date(first), self.w.date(last)))
        if low == 0:
            window = "within %s" % self.w.days(high, market_clock.UNIT_TRADING)
        else:
            window = "%s to %s" % (self.w.count(low), self.w.days(high, market_clock.UNIT_TRADING))
        estimated = "estimated " if entry.status == calendar_events.STATUS_ESTIMATED else ""
        body = _Body(KIND_DATE, "fires %s" % span, ["%s of the %searnings date %s in %s" % (
            window, estimated, self.w.date(event), CALENDAR_NAME)])
        body.dates.update({"from": first, "to": last, "event_date": event})
        if first == last:
            body.dates["fires_on"] = first
        return body

    def ex_dividend_body(self, condition: exit_rules.Condition) -> _Body:
        high, _low = self.window("days_to_ex_dividend")
        leg = self.leg
        if high is None or high < 0 or leg is None:
            return self.no_single_price(condition)
        last_day = self.book.last_trading_day(leg)
        if last_day is None:
            raise _NeedsData()
        window = expiry.dividend_window(str(leg["underlying"]), last_day, self.book.calendar, self.book.today,
                                        self.book.extra)
        if not (window.known and window.applicable) or window.ex_date is None:
            raise _NeedsData()
        start = first_date_within(window.ex_date, high, market_clock.UNIT_TRADING, self.book.extra)
        body = _Body(KIND_DATE, "fires from %s" % self.w.date(start), ["within %s of the ex-dividend date %s in %s" % (
            self.w.days(high, market_clock.UNIT_TRADING), self.w.date(window.ex_date), CALENDAR_NAME)])
        body.dates.update({"from": start, "event_date": window.ex_date, "last_trading_day": last_day})
        return body


def _grid_edge(reading: Callable[[Decimal], bool], start: Decimal, edge: str) -> Optional[Decimal]:
    """The stock-grid level nearest the boundary at which ``reading`` (monotone on the grid) is true: the smallest
    such level (``min``: true at and above it) or the largest (``max``: true at and below it); ``None`` if none."""
    if edge not in ("min", "max") or not start.is_finite() or start <= 0:
        return None

    def down(level: Decimal) -> Optional[Decimal]:
        lower = level - _stock_tick(level - SUB_DOLLAR_STOCK_TICK)
        return lower if lower > 0 else None

    def up(level: Decimal) -> Decimal:
        return level + _stock_tick(level)

    cursor: Optional[Decimal] = round_stock_price(start, ROUND_DOWN if edge == "max" else ROUND_UP)
    if cursor is not None and cursor <= 0:
        cursor = SUB_DOLLAR_STOCK_TICK
    try:
        for _ in range(_MAX_GRID_STEPS):
            if cursor is None:
                return None
            if edge == "max":
                if reading(cursor):
                    if not reading(up(cursor)):
                        return cursor
                    cursor = up(cursor)
                else:
                    cursor = down(cursor)
            else:
                if reading(cursor):
                    lower = down(cursor)
                    if lower is None or not reading(lower):
                        return cursor
                    cursor = lower
                else:
                    cursor = up(cursor)
    except (InvalidOperation, ZeroDivisionError):
        return None
    return None


# --------------------------------------------------------------------------------------------
# The sheet
# --------------------------------------------------------------------------------------------


def _results(container: Any) -> Dict[str, exit_rules.RuleResult]:
    out: Dict[str, exit_rules.RuleResult] = {}
    for name in _RESULT_LISTS:
        for result in getattr(container, name, None) or []:
            out.setdefault(result.rule_id, result)
    return out


def _clock(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%H:%M UTC")


def _delay(document: Mapping[str, Any], delay_min: Optional[int]) -> int:
    if delay_min is not None:
        return delay_min
    sources = document.get("sources") or []
    delay = (sources[0] if sources else {}).get("delay_min")
    return delay if isinstance(delay, int) and not isinstance(delay, bool) else 0


def _group_levels(writer: _Writer, book: _Book, rulebook: exit_rules.Rulebook,
                  verdict: exit_rules.GroupVerdict) -> GroupLevels:
    group = book.groups.get(verdict.group_id)
    if verdict.quarantined or group is None:
        name = writer.ident(verdict.group_id)
        lines = [LevelLine(None, KIND_NONE, STATUS_UNKNOWN, "%s quarantined: %s" % (name, writer.reason(
            alerts.quarantine_words(str(reason))))) for reason in verdict.reasons]
        return GroupLevels(verdict.group_id, verdict.trade_id, verdict.underlying, verdict.structure,
                           verdict.data_quality, None, "## %s | %s" % (name, alerts.QUARANTINED_WORDS), lines, [])
    structure = str(group.get("structure"))
    trade = writer.ident(str(group.get("trade_id") or group["group_id"]))
    segments = ["## %s" % trade, "%s %s" % (writer.reason(str(group.get("underlying"))),
                                             alerts.STRUCTURE_WORDS.get(structure, structure.replace("_", " ")))]
    tick_class, standard_roots = book.group_class(group)
    if tick_class is not None:
        low, high = OPTION_TICKS[tick_class]
        words = CLASS_WORDS[tick_class]
        if tick_class == CLASS_STANDARD:
            words = words % writer.reason(", ".join(standard_roots))
        segments.append("option ticks %s below %s, %s at or above (%s)" % (writer.price(low), writer.price(TICK_BREAK),
                                                                           writer.price(high), words))
    if structure in grouping.VERTICAL_STRUCTURES:
        segments.append("spread prices on a %s grid" % writer.price(SPREAD_TICK))
    header = " | ".join(segments)
    results = _results(verdict)
    lines: List[LevelLine] = []
    if group.get("data_quality") != DATA_QUALITY_OK:
        lines.append(_data_unavailable_line(writer, book, group, trade))
    suppressed: List[str] = []
    history = book.history_date
    for rule in rulebook.rules:
        result = results.get(rule.id)
        if result is None:
            continue
        if result.result == exit_rules.RESULT_SUPPRESSED:
            suppressed.append(writer.rule(rule.id))
            continue
        if result.result == exit_rules.RESULT_NO_HISTORY:
            lines.append(LevelLine(rule.id, KIND_NONE, STATUS_NO_LEVEL, "%s: %s" % (line_lead(writer.rule(rule.id)), NO_HISTORY_TEXT % (
                writer.date(history) if isinstance(history, datetime.date) else "the previous trading day"))))
            continue
        longs = _long_legs_read(book, rule, result, group)
        if len(longs) > 1:
            chosen = book.leg_of(result)
            for leg in longs:
                mine = chosen is not None and chosen["row_number"] == leg["row_number"]
                lines.append(_LineMaker(writer, book, rule, result, group, tick_class, None, leg=leg,
                                        representative=mine).make())
            continue
        lines.append(_LineMaker(writer, book, rule, result, group, tick_class, None).make())
    return GroupLevels(verdict.group_id, group.get("trade_id"), group.get("underlying"), structure,
                       group.get("data_quality"), tick_class, header, lines, suppressed)


DATA_QUALITY_OK: str = "ok"
LONG_LEG_METRICS: Tuple[str, ...] = ("auto_exercise_itm",)
"""Leg rules whose reading is a long option leg's own: one line per open long option leg when a group has several."""


def _long_legs_read(book: _Book, rule: exit_rules.Rule, result: exit_rules.RuleResult,
                    group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """The open long option legs a :data:`LONG_LEG_METRICS` rule reads in ``group`` (``[]`` for any other rule, and for
    a result that is not applicable or cannot be read, whose one line says so)."""
    if rule.primary.metric not in LONG_LEG_METRICS or book.leg_of(result) is None:
        return []
    if result.result == exit_rules.RESULT_CLEAR and result.reason.startswith("not applicable: "):
        return []
    return [leg for leg in book.option_legs(group) if leg["open_quantity"] > 0]


def _data_unavailable_line(writer: _Writer, book: _Book, group: Mapping[str, Any], name: str) -> LevelLine:
    """``<id> data unavailable: <each open leg without a usable mark>``: the book gives the group no P/L and the rules
    reading a mark cannot be read (the reasons in the evaluation's own words, never a cell the owner typed)."""
    reasons = [NO_MARK_TEXT % (leg["row_number"], leg.get("occ") or leg.get("symbol"), leg.get("quote_grade") or "none",
                               leg.get("quote_status") or "no quote")
               for leg in book.open_legs(group) if leg.get("mark_source") != "mid"]
    text = DATA_UNAVAILABLE_TEXT % (name, writer.reason("; ".join(reasons) or "an input is unavailable"))
    return LevelLine(None, KIND_NONE, STATUS_UNKNOWN, text)


def _section_levels(writer: _Writer, book: _Book, rulebook: exit_rules.Rulebook,
                    evaluation: exit_rules.Evaluation) -> List[SectionLevels]:
    sections = list(evaluation.underlyings) + [evaluation.portfolio]
    found = [(section, _results(section)) for section in sections]
    waiting = [rule.id for rule in rulebook.rules
               if any(results.get(rule.id) is not None and results[rule.id].result == exit_rules.RESULT_NO_HISTORY
                      for _section, results in found)]
    out: List[SectionLevels] = []
    shared = False
    for rule in rulebook.rules:
        if rule.scope not in (exit_rules.SCOPE_UNDERLYING, exit_rules.SCOPE_PORTFOLIO):
            continue
        if rule.id in waiting and not shared:
            shared = True
            names = ", ".join(list_item(writer.rule(rule_id)) for rule_id in waiting)
            if _imperative_lead(waiting[0]):
                names = "your " + names
            text = "%s: %s" % (names, NO_HISTORY_TEXT % writer.date(evaluation.history_date))
            out.append(SectionLevels(SCOPE_NO_HISTORY, None, list(waiting),
                                     [LevelLine(None, KIND_NONE, STATUS_NO_LEVEL, text)]))
        for section, results in found:
            if section.scope != rule.scope:
                continue
            result = results.get(rule.id)
            if result is None or result.result in (exit_rules.RESULT_NO_HISTORY, exit_rules.RESULT_SUPPRESSED):
                continue
            symbol = section.subject if section.scope == exit_rules.SCOPE_UNDERLYING else None
            line = _LineMaker(writer, book, rule, result, None, None, symbol).make()
            out.append(SectionLevels(section.scope, section.subject, [rule.id], [line]))
    return out


def _context(evaluation: exit_rules.Evaluation, document: Mapping[str, Any],
             calendar: Optional[calendar_events.CalendarFile], levels_config: Any) -> Tuple[_Book, datetime.date]:
    """The indexed book and the sheet's date: the ET date of the book's ``generated_at`` (D1-10)."""
    generated = document["generated_at"]
    et_date = market_clock.to_et(generated).date() if isinstance(generated, datetime.datetime) else evaluation.et_date
    return _Book(document, calendar, levels_config, evaluation.et_date, evaluation.history_date), et_date


def _rules_segment(writer: _Writer, rulebook: exit_rules.Rulebook) -> str:
    return "%s, %s" % (writer.ident("rules %s" % rulebook.rulebook_version), writer.ident("sha %s" % rulebook.sha256[:8]))


def build_sheet(evaluation: exit_rules.Evaluation, document: Mapping[str, Any], rulebook: exit_rules.Rulebook, *,
                calendar: Optional[calendar_events.CalendarFile] = None, levels_config: Any = None,
                delay_min: Optional[int] = None) -> Sheet:
    """The level sheet of ``evaluation`` (made from ``document`` with ``rulebook``): one section per open group in
    evaluation order, the underlyings and portfolio section, the footer (module notes)."""
    book, et_date = _context(evaluation, document, calendar, levels_config)
    writer = _Writer()
    as_of = alerts.book_as_of(document)
    lines = ["%s %s | %s | book as of %s" % (SHEET_HEAD, writer.date(et_date), _rules_segment(writer, rulebook),
                                             writer.clock(_clock(as_of)))]
    groups = [_group_levels(writer, book, rulebook, verdict) for verdict in evaluation.groups]
    for group in groups:
        lines.extend(group.text_lines())
    sections = _section_levels(writer, book, rulebook, evaluation)
    if sections:
        lines.append("## %s" % SECTION_TITLE)
        lines.extend(line.text for section in sections for line in section.lines)
    lines.append(writer.footer(as_of, _delay(document, delay_min)))
    return Sheet(text="\n".join(lines), emitted_numbers=list(writer.emitted), rule_ids=list(writer.rule_ids),
                 identifiers=list(writer.identifiers), clocks=list(writer.clocks), as_of=as_of, et_date=et_date,
                 groups=groups, sections=sections)


def _calendar_piece(writer: _Writer, cal: Optional[calendar_events.CalendarFile], symbol: str, what: str) -> str:
    if cal is None:
        return "%s unknown (no %s)" % (what, CALENDAR_NAME)
    events = cal.events_for(symbol)
    entry = events.earnings if what == "earnings" else events.ex_dividend
    if not events.listed or entry is None:
        return "%s unknown (not in %s)" % (what, CALENDAR_NAME)
    if entry.status == calendar_events.STATUS_NONE:
        return "%s none (a confirmed none, %s)" % (what, CALENDAR_NAME)
    if entry.status == calendar_events.STATUS_UNKNOWN or entry.date is None:
        return "%s unknown (%s)" % (what, CALENDAR_NAME)
    if what == "earnings":
        return "%s %s (%s, %s)" % (what, writer.date(entry.date), entry.status, CALENDAR_NAME)
    amount = getattr(entry, "amount", None)
    if amount is None:
        return "%s %s (%s, %s)" % (what, writer.date(entry.date), entry.status, CALENDAR_NAME)
    return "%s %s (%s, %s per share)" % (what, writer.date(entry.date), entry.status, writer.dollars(amount))


def build_plan_card(evaluation: exit_rules.Evaluation, document: Mapping[str, Any], rulebook: exit_rules.Rulebook,
                    group_id: str, *, calendar: Optional[calendar_events.CalendarFile] = None, levels_config: Any = None,
                    broker_cutoff_et: Optional[datetime.time] = None, delay_min: Optional[int] = None) -> PlanCard:
    """The exit-plan card of one group (by group id or trade id): the card line, the group's section, the dates that
    matter and the footer; :class:`UnknownGroup` for an id the evaluation does not hold."""
    verdict = next((item for item in evaluation.groups if group_id in (item.group_id, item.trade_id)), None)
    if verdict is None:
        raise UnknownGroup("no open group %s in the book" % group_id)
    book, _et_date = _context(evaluation, document, calendar, levels_config)
    writer = _Writer()
    as_of = alerts.book_as_of(document)
    group = book.groups.get(verdict.group_id)
    name = str(group.get("trade_id") or group["group_id"]) if group is not None else verdict.group_id
    structure = str(group.get("structure")) if group is not None else None
    words = (alerts.QUARANTINED_WORDS if group is None or verdict.quarantined else "%s %s" % (
        writer.reason(str(group.get("underlying"))), alerts.STRUCTURE_WORDS.get(str(structure), str(structure))))
    lines = ["%s %s | %s | %s" % (CARD_HEAD, writer.ident(name), words, _rules_segment(writer, rulebook))]
    section = _group_levels(writer, book, rulebook, verdict)
    lines.extend(section.text_lines())
    if group is not None and not verdict.quarantined:
        pieces: List[str] = []
        leg = book.nearest_leg(book.option_legs(group))
        last_day = book.last_trading_day(leg) if leg is not None else None
        if last_day is not None:
            pieces.append("last trading day %s" % writer.date(last_day))
            before = first_date_within(last_day, PLAN_DTE_DAYS, market_clock.UNIT_TRADING, book.extra)
            pieces.append("%s before it %s" % (writer.days(PLAN_DTE_DAYS, market_clock.UNIT_TRADING), writer.date(before)))
        symbol = str(group.get("underlying"))
        pieces.append(_calendar_piece(writer, calendar, symbol, "earnings"))
        pieces.append(_calendar_piece(writer, calendar, symbol, "ex-dividend"))
        if broker_cutoff_et is not None and last_day is not None:
            pieces.append("your broker's exercise cutoff %s on %s" % (
                writer.clock(broker_cutoff_et.strftime("%H:%M") + " ET"), writer.date(last_day)))
        lines.append("dates that matter: " + "; ".join(pieces))
    lines.append(writer.footer(as_of, _delay(document, delay_min)))
    return PlanCard(text="\n".join(lines), emitted_numbers=list(writer.emitted), rule_ids=list(writer.rule_ids),
                    identifiers=list(writer.identifiers), clocks=list(writer.clocks), as_of=as_of,
                    group_id=verdict.group_id)


__all__ = [
    "ALERT_LEVEL_TEXT",
    "BAND_METRICS",
    "BELOW_TICK_TEXT",
    "CANNOT_FIRE_DTE_TEXT",
    "CANNOT_FIRE_SHORT_TEXT",
    "CARD_HEAD",
    "CLASS_NAMES",
    "CLASS_PENNY",
    "CLASS_PENNY_ALL",
    "CLASS_STANDARD",
    "CLASS_WORDS",
    "CROSSED_FIRED_TEXT",
    "CROSSED_PENDING_TEXT",
    "DATA_UNAVAILABLE_TEXT",
    "DATES_KEYS",
    "DATE_METRICS",
    "DIRECTIONS",
    "EXACT_METRICS",
    "EXACT_PLACES",
    "GROUP_KEYS",
    "GroupLevels",
    "KIND_DATE",
    "KIND_NONE",
    "KIND_OPTION_PRICE",
    "KIND_SPREAD_PRICE",
    "KIND_UNDERLYING_BAND",
    "KIND_UNDERLYING_EXACT",
    "KIND_UNDERLYING_MODEL",
    "LINE_KEYS",
    "LINE_KINDS",
    "LINE_STATUSES",
    "LONG_LEG_METRICS",
    "LONG_PLACES",
    "LevelLine",
    "MAX_PLACES",
    "MODEL_KEYS",
    "NO_DELTA_TEXT",
    "NO_HISTORY_TEXT",
    "NO_MARK_TEXT",
    "OPTION_METRICS",
    "OPTION_ONLY_STRUCTURES",
    "OPTION_TICKS",
    "ORDER_SHAPE_WARNING",
    "PLAN_DTE_DAYS",
    "PlanCard",
    "RESTING_TEXT",
    "RESTING_UNKNOWN_TEXT",
    "ROUND_DOWN",
    "ROUND_UP",
    "SCOPE_NO_HISTORY",
    "SECTION_KEYS",
    "SECTION_TITLE",
    "SHEET_HEAD",
    "SPREAD_TICK",
    "STATUS_ALERT",
    "STATUS_CANNOT_FIRE",
    "STATUS_FIRED",
    "STATUS_NOT_APPLICABLE",
    "STATUS_NO_LEVEL",
    "STATUS_PENDING",
    "STATUS_RESTING",
    "STATUS_UNKNOWN",
    "STOCK_TICK",
    "SUB_DOLLAR_STOCK_TICK",
    "SUPPRESSED_PREFIX",
    "SectionLevels",
    "Sheet",
    "TICK_BREAK",
    "TICK_CLASSES",
    "UNREADABLE_TEXT",
    "UnknownGroup",
    "build_plan_card",
    "build_sheet",
    "line_lead",
    "list_item",
    "model_shown",
    "delta_boundary",
    "delta_level",
    "exact_value",
    "first_date_within",
    "fmt_exact_price",
    "fmt_level_price",
    "option_tick",
    "round_option_price",
    "round_spread_price",
    "round_stock_price",
    "round_to_tick",
    "tick_class_for",
]
