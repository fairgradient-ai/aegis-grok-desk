"""Strict reader for ``structuring.toml``, the owner's structuring criteria (PR 3b decision S3 over designer C's D1).

Standard library only; modelled on :mod:`aegis_desk.desk_config`. The file lives at the desk root
(``DeskPaths.structuring_toml``), is owner-authored (``scripts/desk_init.py`` renders it from the structuring
interview; no routine and no structuring tool ever writes it) and is acknowledged with ``ack.py config
--owner-tapped`` like ``desk.toml``. It is parsed with the vendored strict TOML reader (:func:`aegis_desk.toml_compat.
loads`), so TOML floats arrive as ``Decimal``. Everything the reader does not expect is a
:class:`StructuringConfigError` (a ``ValueError``) carrying ``path`` and a one-line ``reason``; ``str(error)`` is
``"<path>: <reason>"``.

The document (:data:`SECTIONS`, each with exactly the keys of :data:`SECTION_KEYS`, in this order)::

    schema_version = 1
    [disclosure]      acknowledged = "structuring-suggestions-1"
    [general]         max_spread_pct (0, 25]; max_candidates_shown integer 1-10 (the text only)
    [covered_call]    enabled, dte, abs_delta, min_credit, strike_floor, earnings, ex_dividend, objective
    [roll]            enabled, dte, abs_delta, min_credit, credit_only, strike_floor, earnings, ex_dividend, objective
    [protective_put]  enabled, dte, floor_pct, stress_drop_pct, objective
    [collar]          enabled, dte, floor_pct, cap_pct, max_net_debit, strike_floor, earnings, ex_dividend, objective

Rules (every one an error that names the section and the key)
-------------------------------------------------------------
1. ``schema_version`` is the integer 1.
2. Every section of :data:`SECTIONS` is present: a structure the owner does not want is ``enabled = false`` (that
   structure is then NOT_APPLICABLE with :data:`DISABLED_REASON`), never an omitted section; a disabled section is
   still read in full.
3. Unknown top-level keys, unknown sections and unknown keys are errors; every key is required.
4. ``[disclosure] acknowledged`` equals :data:`DISCLOSURE_ID` exactly, the one-time disclosure
   :data:`DISCLOSURE_TEXT` the structuring interview shows last; otherwise the reason is ``the structuring
   disclosure structuring-suggestions-1 is not acknowledged in this file: run the structuring interview again``.
5. ``dte`` (every structure section): two integers, calendar days, ``7 <= lo < hi <= 400`` and ``hi - lo <= 90``
   (four consecutive standard monthlies span at least 84 days, so a window never needs more than the per-run chain
   cap of four chains).
6. ``abs_delta`` (``covered_call``, ``roll``): ``0.05 <= lo < hi <= 0.60``, compared with ``|delta|`` read at 2
   decimal places as the exit rules read it. ``min_credit`` (per share, at the bid; for ``roll`` the new call's
   bid) ``>= 0.01``. ``credit_only`` (``roll`` only) is a boolean: a net debit fails it, a zero net passes.
7. ``floor_pct`` (``protective_put``, ``collar``; ``(S - K) / S x 100`` at 2 dp) ``0 <= lo < hi <= 50``;
   ``stress_drop_pct`` (``protective_put``) lies in ``(hi(floor_pct), 90]``; ``cap_pct`` (``collar``; ``(Kc - S) /
   S x 100`` at 2 dp) ``0 < lo < hi <= 100``; ``max_net_debit`` (``collar``, per share) ``>= 0`` (0.00 is a zero
   cost or a credit). ``max_spread_pct`` ``(0, 25]``: a new leg also needs both sides fresh (a mark mid) at quote
   grade A-D.
8. Every decimal knob carries at most :data:`DECIMAL_PLACES` (2) decimal places, the precision the desk reads it at
   (``|delta|`` and percentages at 2 dp, money in cents); an integer is accepted where a decimal is allowed and kept
   as an exact ``Decimal``; a boolean is never a number and a string never a number; infinity and NaN are refused.
9. ``strike_floor`` is one of :data:`STRIKE_FLOORS`, ``earnings`` and ``ex_dividend`` one of
   :data:`EVENT_POLICIES`, ``enabled`` a boolean, and ``objective`` the one id :data:`OBJECTIVES` holds for the
   section (declared in the file so the objective is echoed with the file's sha).

Fixed rule, not a key (:data:`UNKNOWN_EVENT_RULE`): an earnings or ex-dividend date that ``research/calendar.toml``
lists as unknown, or does not list at all, counts as inside the window; it is never treated as none.

The objectives (:data:`OBJECTIVE_DEFS`: id, the words the text prints, the formula in Decimal with a 28-digit
context, the unit and the direction) describe what each score measures; their words never use return, yield, profit
or income (designer C's D6). ``sha256`` is the hex digest of the file's bytes (a UTF-8 byte-order mark included;
the text is decoded as ``utf-8-sig``), compared by the acknowledgement set against the owner-acknowledged hash.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from . import toml_compat
from .toml_compat import TomlError

FILE_NAME: str = "structuring.toml"
SCHEMA_VERSION: int = 1
"""The version ``desk_init`` writes unless an answer uses a version-2 key (:data:`SCHEMA_VERSION_2`)."""
SCHEMA_VERSION_2: int = 2
SCHEMA_VERSIONS: Tuple[int, ...] = (SCHEMA_VERSION, SCHEMA_VERSION_2)
"""The versions the reader accepts. Version 2 adds the optional keys of :data:`V2_KEYS`; everything else is as in 1."""
DISCLOSURE_ID: str = "structuring-suggestions-1"
DISCLOSURE_TEXT: str = (
    "The Aegis Structuring Desk answers only when you ask. For a position you hold in positions.csv, its tools list "
    "candidates from delayed Alpaca paper quotes (indicative option quotes, which are approximate): closing or "
    "unwinding it, a replacement for a short option you are closing or that is expiring, or a covered call, protective "
    "put or collar on shares you hold. They score the "
    "candidates by the objective and criteria in this file and name the highest-scoring one with its reasons. That "
    "is a personalized suggestion computed by deterministic tools and posted by an AI assistant; it is not an order, "
    "not a forecast and not a judgment that a trade suits you. The desk knows only your positions, these criteria and "
    "the approval level you stated; it does not see your cash, your other holdings, your objectives or your taxes. It "
    "never places anything: you decide, you supply the limit and you act in your brokerage account. Options involve "
    "substantial risk; you confirm that you received the OCC booklet Characteristics and Risks of Standardized "
    "Options.")
"""The one-time structuring disclosure (designer C's D14), shown verbatim before ``acknowledged`` is set."""
DISCLOSURE_REASON: str = ("the structuring disclosure %s is not acknowledged in this file: run the structuring "
                          "interview again" % DISCLOSURE_ID)
DISABLED_REASON: str = "disabled in your structuring.toml"
"""Why a structure whose section says ``enabled = false`` is NOT_APPLICABLE."""
UNKNOWN_EVENT_RULE: str = ("an earnings or ex-dividend date that research/calendar.toml lists as unknown, or does not "
                           "list at all, counts as inside the window; it is never treated as none")

SECTION_DISCLOSURE: str = "disclosure"
SECTION_GENERAL: str = "general"
SECTION_COVERED_CALL: str = "covered_call"
SECTION_ROLL: str = "roll"
SECTION_PROTECTIVE_PUT: str = "protective_put"
SECTION_COLLAR: str = "collar"
SECTIONS: Tuple[str, ...] = (SECTION_DISCLOSURE, SECTION_GENERAL, SECTION_COVERED_CALL, SECTION_ROLL,
                             SECTION_PROTECTIVE_PUT, SECTION_COLLAR)
STRUCTURE_SECTIONS: Tuple[str, ...] = SECTIONS[2:]
"""The sections with ``enabled``, ``dte`` and an objective (the structures the tools rank)."""
SECTION_KEYS: Dict[str, Tuple[str, ...]] = {
    SECTION_DISCLOSURE: ("acknowledged",),
    SECTION_GENERAL: ("max_spread_pct", "max_candidates_shown"),
    SECTION_COVERED_CALL: ("enabled", "dte", "abs_delta", "min_credit", "strike_floor", "earnings", "ex_dividend",
                           "objective"),
    SECTION_ROLL: ("enabled", "dte", "abs_delta", "min_credit", "credit_only", "strike_floor", "earnings",
                   "ex_dividend", "objective"),
    SECTION_PROTECTIVE_PUT: ("enabled", "dte", "floor_pct", "stress_drop_pct", "objective"),
    SECTION_COLLAR: ("enabled", "dte", "floor_pct", "cap_pct", "max_net_debit", "strike_floor", "earnings",
                     "ex_dividend", "objective"),
}
"""Every section's keys, in the order ``desk_init.render_structuring_toml`` writes them; all are required."""

BAND_WEIGHTS_KEY: str = "band_weights"
QUOTE_WEIGHT: str = "quote"
CENTRE_BANDS: Dict[str, Tuple[str, ...]] = {
    SECTION_COVERED_CALL: ("dte", "abs_delta"),
    SECTION_PROTECTIVE_PUT: ("dte", "floor_pct"),
    SECTION_COLLAR: ("dte", "floor_pct", "cap_pct"),
}
"""The bands ``band_centre`` measures in each section that can take it (``[roll]`` cannot)."""
ROLL_TEST_KEY: str = "roll_test"
ROLL_TEST_CREDIT: str = "credit"
ROLL_TEST_NET_TIME_VALUE: str = "net_time_value"
ROLL_TEST_NONE: str = "none"
ROLL_TESTS: Tuple[str, ...] = (ROLL_TEST_CREDIT, ROLL_TEST_NET_TIME_VALUE, ROLL_TEST_NONE)
"""``[roll] roll_test`` (``schema_version = 2``, in place of ``credit_only``): ``credit`` fails a net debit (as
``credit_only = true``), ``net_time_value`` fails a roll that buys back more time value than it sells, ``none`` tests
neither (as ``credit_only = false``)."""
V2_KEYS: Dict[str, Tuple[str, ...]] = {
    section: tuple("%s_centre" % band for band in bands) + (BAND_WEIGHTS_KEY,)
    for section, bands in CENTRE_BANDS.items()
}
V2_KEYS[SECTION_ROLL] = (ROLL_TEST_KEY,)
"""The optional keys of ``schema_version = 2``, in the order ``desk_init`` writes them after ``objective``: each band's
centre (a number inside the band; the midpoint when absent) and ``band_weights``, an inline table of positive numbers
per band and for ``quote`` (1 when absent), which apply only with ``objective = "band_centre"``; and ``[roll]
roll_test`` (:data:`ROLL_TESTS`), given in place of ``credit_only``."""
BAND_WEIGHT_MAX: Decimal = Decimal("10")

EVENT_EXCLUDE: str = "exclude"
EVENT_ALLOW: str = "allow"
EVENT_POLICIES: Tuple[str, ...] = (EVENT_EXCLUDE, EVENT_ALLOW)
STRIKE_FLOOR_COST_BASIS: str = "cost_basis"
STRIKE_FLOOR_NONE: str = "none"
STRIKE_FLOORS: Tuple[str, ...] = (STRIKE_FLOOR_COST_BASIS, STRIKE_FLOOR_NONE)

DTE_MIN: int = 7
DTE_MAX: int = 400
DTE_SPAN_MAX: int = 90
ABS_DELTA_RANGE: Tuple[Decimal, Decimal] = (Decimal("0.05"), Decimal("0.60"))
MIN_CREDIT_MIN: Decimal = Decimal("0.01")
MAX_SPREAD_PCT_MAX: Decimal = Decimal("25")
"""``max_spread_pct`` lies in ``(0, 25]`` (25 % is the quote-grade D band)."""
MAX_CANDIDATES_RANGE: Tuple[int, int] = (1, 10)
FLOOR_PCT_RANGE: Tuple[Decimal, Decimal] = (Decimal("0"), Decimal("50"))
"""``floor_pct``: ``0 <= lo < hi <= 50``."""
CAP_PCT_RANGE: Tuple[Decimal, Decimal] = (Decimal("0"), Decimal("100"))
"""``cap_pct``: ``0 < lo < hi <= 100`` (the low end is exclusive)."""
STRESS_DROP_PCT_MAX: Decimal = Decimal("90")
MAX_NET_DEBIT_MIN: Decimal = Decimal("0")
DECIMAL_PLACES: int = 2
_CENT = Decimal("0.01")

OBJECTIVE_EXTRINSIC_PER_DAY: str = "extrinsic_per_day"
OBJECTIVE_STRESS_COST_PER_DAY: str = "stress_cost_per_day"
OBJECTIVE_CAP_WITHIN_BUDGET: str = "cap_within_budget"
OBJECTIVE_NET_EXTRINSIC_PER_ADDED_DAY: str = "net_extrinsic_per_added_day"
OBJECTIVE_BAND_CENTRE: str = "band_centre"
OBJECTIVE_IDS: Tuple[str, ...] = (OBJECTIVE_EXTRINSIC_PER_DAY, OBJECTIVE_STRESS_COST_PER_DAY,
                                  OBJECTIVE_CAP_WITHIN_BUDGET, OBJECTIVE_NET_EXTRINSIC_PER_ADDED_DAY,
                                  OBJECTIVE_BAND_CENTRE)
OBJECTIVES: Dict[str, Tuple[str, ...]] = {
    SECTION_COVERED_CALL: (OBJECTIVE_EXTRINSIC_PER_DAY, OBJECTIVE_BAND_CENTRE),
    SECTION_ROLL: (OBJECTIVE_EXTRINSIC_PER_DAY, OBJECTIVE_NET_EXTRINSIC_PER_ADDED_DAY),
    SECTION_PROTECTIVE_PUT: (OBJECTIVE_STRESS_COST_PER_DAY, OBJECTIVE_BAND_CENTRE),
    SECTION_COLLAR: (OBJECTIVE_CAP_WITHIN_BUDGET, OBJECTIVE_BAND_CENTRE),
}
"""The objective ids each structure section accepts, the release-1 id first (a tuple, so ``in`` tests membership,
never a substring); the owner names one in the section and the file's sha names it. ``[roll]`` does not take
``band_centre``: a roll trades a position already held, and a score that ignores the time value it buys back can name a
roll that gives up more than it sells."""
BAND_CENTRE_KEYS: Dict[str, Tuple[str, ...]] = {
    SECTION_COVERED_CALL: ("dte", "abs_delta"),
    SECTION_PROTECTIVE_PUT: ("dte", "floor_pct"),
    SECTION_COLLAR: ("dte", "floor_pct", "cap_pct"),
}
"""The bands whose centres ``band_centre`` measures from, per section."""
DIRECTION_HIGHER: str = "higher"
DIRECTION_LOWER: str = "lower"


@dataclass(frozen=True)
class Objective:
    """One ranking objective: ``id``, the ``words`` the text prints, the ``formula`` (Decimal, 28-digit context),
    the score's ``unit`` and the ``direction`` (``higher`` or ``lower`` first)."""

    id: str
    words: str
    formula: str
    unit: str
    direction: str


OBJECTIVE_DEFS: Dict[str, Objective] = {
    OBJECTIVE_EXTRINSIC_PER_DAY: Objective(
        OBJECTIVE_EXTRINSIC_PER_DAY,
        "the bid's time value per calendar day, in percent of the share price",
        "E = bid - max(0, S - K); score = E / S / T_c x 100, quantized 0.0001 half-even; T_c = calendar days to the "
        "last trading day",
        "percent of the share price per calendar day", DIRECTION_HIGHER),
    OBJECTIVE_STRESS_COST_PER_DAY: Objective(
        OBJECTIVE_STRESS_COST_PER_DAY,
        "the put's ask per calendar day per 100 of loss it offsets at your stress drop",
        "S_x = S x (1 - x / 100) with x = stress_drop_pct; score = ask / T_c / (K - S_x) x 100, quantized 0.0001 "
        "half-even (no cover at the stress when K <= S_x); T_c = calendar days to the last trading day",
        "premium per calendar day per 100 of loss offset at your stress drop", DIRECTION_LOWER),
    OBJECTIVE_CAP_WITHIN_BUDGET: Objective(
        OBJECTIVE_CAP_WITHIN_BUDGET,
        "the cap's distance above the share price, in percent, among pairs within your net-debit budget",
        "U = (Kc - S) / S x 100, quantized 0.01 half-even; score = U among the put and call pairs of one expiration "
        "whose net debit (put ask - call bid) is at most max_net_debit",
        "percent above the share price", DIRECTION_HIGHER),
    OBJECTIVE_NET_EXTRINSIC_PER_ADDED_DAY: Objective(
        OBJECTIVE_NET_EXTRINSIC_PER_ADDED_DAY,
        "the new call's time value at its bid less the held call's time value at its ask, in percent of the share "
        "price per calendar day added when the roll adds time value, and the whole amount, not divided by days, when "
        "it gives some up",
        "E_new = bid - max(0, S - K_new); E_held = ask_held - max(0, S - K_held); N = E_new - E_held; score = N / S / "
        "(T_c,new - T_c,held) x 100 when N >= 0, else N / S x 100, quantized 0.000001 half-even; T_c = calendar days "
        "to the last trading day",
        "percent of the share price per calendar day added (N >= 0), percent of the share price (N < 0)",
        DIRECTION_HIGHER),
    OBJECTIVE_BAND_CENTRE: Objective(
        OBJECTIVE_BAND_CENTRE,
        "the distance from the centre of your bands, each band counted in its own half-width, plus each leg's quote "
        "spread counted in your max_spread_pct, all summed with equal weight (a desk convention: one half-width of days "
        "counts as much as one half-width of delta, floor or cap, and as one leg quoted at your spread limit)",
        "c = (lo + hi) / 2 and w = (hi - lo) / 2 for each band of the section (dte with abs_delta, dte with floor_pct, "
        "or dte with floor_pct and cap_pct); score = sum of |x - c| / w + sum over the legs of spread_pct / "
        "max_spread_pct, quantized 0.01 half-even",
        "half-widths of your bands", DIRECTION_LOWER),
}
"""The objectives by id (PR 3b decision S3; the formulas of designer Q's D5, D7 and D8)."""


def objective_for(section: str) -> Objective:
    """The one :class:`Objective` of a structure section (``KeyError`` for any other name)."""
    return OBJECTIVE_DEFS[OBJECTIVES[section][0]]


class StructuringConfigError(ValueError):
    """``structuring.toml`` cannot be used; ``path`` names the file and ``reason`` is one line."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = " ".join(str(reason).split())
        super().__init__("%s: %s" % (path, self.reason))


# --------------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DisclosureSection:
    acknowledged: str


@dataclass(frozen=True)
class GeneralSection:
    max_spread_pct: Decimal
    max_candidates_shown: int


class _CentreMixin:
    """``band_centre``'s owner-set centres and weights (``schema_version = 2``) with their defaults."""

    centres: Tuple[Tuple[str, Decimal], ...]
    band_weights: Tuple[Tuple[str, Decimal], ...]

    def band_centre_of(self, band: str) -> Decimal:
        """The centre of ``band``: the owner's ``<band>_centre``, else the band's midpoint."""
        for name, value in self.centres:
            if name == band:
                return value
        low, high = getattr(self, band)
        return (Decimal(low) + Decimal(high)) / 2

    def band_weight(self, band: str) -> Decimal:
        """The weight of ``band`` (or of ``quote``): the owner's ``band_weights`` entry, else 1."""
        for name, value in self.band_weights:
            if name == band:
                return value
        return Decimal(1)

    def owner_set(self) -> bool:
        """Whether the owner set any centre or weight."""
        return bool(self.centres or self.band_weights)

    def v2_echo(self) -> Dict[str, Any]:
        """The version-2 keys the owner set, as the payload echoes them."""
        echo: Dict[str, Any] = {"%s_centre" % name: value for name, value in self.centres}
        if self.band_weights:
            echo[BAND_WEIGHTS_KEY] = dict(self.band_weights)
        return echo


@dataclass(frozen=True)
class CoveredCallSection(_CentreMixin):
    enabled: bool
    dte: Tuple[int, int]
    abs_delta: Tuple[Decimal, Decimal]
    min_credit: Decimal
    strike_floor: str
    earnings: str
    ex_dividend: str
    objective: str
    centres: Tuple[Tuple[str, Decimal], ...] = ()
    band_weights: Tuple[Tuple[str, Decimal], ...] = ()


@dataclass(frozen=True)
class RollSection:
    enabled: bool
    dte: Tuple[int, int]
    abs_delta: Tuple[Decimal, Decimal]
    min_credit: Decimal
    credit_only: bool
    strike_floor: str
    earnings: str
    ex_dividend: str
    objective: str
    roll_test: Optional[str] = None
    """The ``schema_version = 2`` roll test, or ``None`` when the file states ``credit_only`` (:meth:`roll_rule`)."""

    def roll_rule(self) -> str:
        """The roll test the candidates are judged by: ``roll_test``, else ``credit`` or ``none`` from
        ``credit_only``."""
        if self.roll_test is not None:
            return self.roll_test
        return ROLL_TEST_CREDIT if self.credit_only else ROLL_TEST_NONE


@dataclass(frozen=True)
class ProtectivePutSection(_CentreMixin):
    enabled: bool
    dte: Tuple[int, int]
    floor_pct: Tuple[Decimal, Decimal]
    stress_drop_pct: Decimal
    objective: str
    centres: Tuple[Tuple[str, Decimal], ...] = ()
    band_weights: Tuple[Tuple[str, Decimal], ...] = ()


@dataclass(frozen=True)
class CollarSection(_CentreMixin):
    enabled: bool
    dte: Tuple[int, int]
    floor_pct: Tuple[Decimal, Decimal]
    cap_pct: Tuple[Decimal, Decimal]
    max_net_debit: Decimal
    strike_floor: str
    earnings: str
    ex_dividend: str
    objective: str
    centres: Tuple[Tuple[str, Decimal], ...] = ()
    band_weights: Tuple[Tuple[str, Decimal], ...] = ()


@dataclass(frozen=True)
class StructuringConfig:
    """The parsed ``structuring.toml``: its six frozen sections plus the file's SHA-256 and path."""

    schema_version: int
    disclosure: DisclosureSection
    general: GeneralSection
    covered_call: CoveredCallSection
    roll: RollSection
    protective_put: ProtectivePutSection
    collar: CollarSection
    sha256: str
    path: str

    def section(self, name: str) -> Any:
        """The section named ``name`` (one of :data:`SECTIONS`; ``KeyError`` otherwise)."""
        if name not in SECTIONS:
            raise KeyError(name)
        return getattr(self, name)

    def objective(self, name: str) -> Objective:
        """The :class:`Objective` a structure section ranks by."""
        return OBJECTIVE_DEFS[self.section(name).objective]

    def enabled(self, name: str) -> bool:
        """Whether a structure section is enabled (``KeyError`` for ``disclosure`` and ``general``)."""
        if name not in STRUCTURE_SECTIONS:
            raise KeyError(name)
        return bool(self.section(name).enabled)

    def as_dict(self) -> Dict[str, Any]:
        """The criteria echo a payload carries: every section's values in the file's order (bands as lists,
        decimals as ``Decimal``), then ``sha256``."""
        echo: Dict[str, Any] = {"schema_version": self.schema_version}
        for name in SECTIONS:
            section = self.section(name)
            echo[name] = {key: (list(getattr(section, key)) if isinstance(getattr(section, key), tuple)
                                else getattr(section, key)) for key in SECTION_KEYS[name]}
            if isinstance(section, _CentreMixin):
                echo[name].update(section.v2_echo())
            if isinstance(section, RollSection) and section.roll_test is not None:
                echo[name] = {(ROLL_TEST_KEY if key == "credit_only" else key):
                              (section.roll_test if key == "credit_only" else value)
                              for key, value in echo[name].items()}
        echo["sha256"] = self.sha256
        return echo


# --------------------------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return _is_int(value) or isinstance(value, Decimal)


def _text(value: Decimal) -> str:
    return format(value, "f")


class _Reader:
    """One parse of one document; every ``fail`` carries the file path."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.version = SCHEMA_VERSION

    def fail(self, reason: str) -> StructuringConfigError:
        return StructuringConfigError(self.path, reason)

    # -- structure ---------------------------------------------------------------------------
    def section(self, document: Dict[str, Any], name: str) -> Dict[str, Any]:
        if name not in document:
            raise self.fail("missing section [%s]" % name)
        table = document[name]
        if not isinstance(table, dict):
            raise self.fail("section [%s] must be a table, not a %s value" % (name, type(table).__name__))
        required = SECTION_KEYS[name]
        allowed = required + (V2_KEYS.get(name, ()) if self.version >= SCHEMA_VERSION_2 else ())
        if name == SECTION_ROLL and self.version >= SCHEMA_VERSION_2 and ROLL_TEST_KEY in table:
            if "credit_only" in table:
                raise self.fail("[%s] give %s or credit_only, not both" % (name, ROLL_TEST_KEY))
            required = tuple(key for key in required if key != "credit_only")
        for key in table:
            if key not in allowed:
                raise self.fail("unknown key '%s' in section [%s] (allowed: %s)" % (key, name, ", ".join(allowed)))
        for key in required:
            if key not in table:
                raise self.fail("missing key '%s' in section [%s]" % (key, name))
        return table

    def centres_and_weights(self, table: Dict[str, Any], where: str, bands: Dict[str, Tuple[Any, Any]], objective: str
                            ) -> Tuple[Tuple[Tuple[str, Decimal], ...], Tuple[Tuple[str, Decimal], ...]]:
        """The version-2 centres and ``band_weights`` of a section that can take ``band_centre`` (:data:`V2_KEYS`)."""
        present = [key for key in V2_KEYS[where] if key in table]
        if present and objective != OBJECTIVE_BAND_CENTRE:
            raise self.fail('[%s] %s applies only with objective = "%s" (got %r)'
                            % (where, present[0], OBJECTIVE_BAND_CENTRE, objective))
        centres: List[Tuple[str, Decimal]] = []
        for band in CENTRE_BANDS[where]:
            key = "%s_centre" % band
            if key not in table:
                continue
            value = self.number(table, key, where)
            low, high = bands[band]
            if not Decimal(low) <= value <= Decimal(high):
                raise self.fail("[%s] %s must lie inside %s [%s, %s] (got %s)"
                                % (where, key, band, _text(Decimal(low)), _text(Decimal(high)), _text(value)))
            centres.append((band, value))
        weights: List[Tuple[str, Decimal]] = []
        if BAND_WEIGHTS_KEY in table:
            value = table[BAND_WEIGHTS_KEY]
            names = CENTRE_BANDS[where] + (QUOTE_WEIGHT,)
            if not isinstance(value, dict):
                raise self.fail("[%s] %s must be a table of numbers, for example { dte = 0.5 } (got %r)"
                                % (where, BAND_WEIGHTS_KEY, value))
            if not value:
                raise self.fail("[%s] %s must name at least one band (allowed: %s)"
                                % (where, BAND_WEIGHTS_KEY, ", ".join(names)))
            for name in value:
                if name not in names:
                    raise self.fail("unknown band '%s' in [%s] %s (allowed: %s)"
                                    % (name, where, BAND_WEIGHTS_KEY, ", ".join(names)))
            for name in names:
                if name not in value:
                    continue
                label = "%s.%s" % (BAND_WEIGHTS_KEY, name)
                weight = self.decimal(value[name], label, where)
                if not Decimal(0) < weight <= BAND_WEIGHT_MAX:
                    raise self.fail("[%s] %s must be above 0 and at most %s (got %s)"
                                    % (where, label, BAND_WEIGHT_MAX, _text(weight)))
                weights.append((name, weight))
        return tuple(centres), tuple(weights)

    # -- typed values --------------------------------------------------------------------------
    def boolean(self, table: Dict[str, Any], key: str, where: str) -> bool:
        value = table[key]
        if not isinstance(value, bool):
            raise self.fail("[%s] %s must be a boolean (true or false), got %r" % (where, key, value))
        return value

    def choice(self, table: Dict[str, Any], key: str, where: str, choices: Tuple[str, ...]) -> str:
        value = table[key]
        if not isinstance(value, str) or value not in choices:
            raise self.fail("[%s] %s must be one of %s (got %r)" % (where, key, ", ".join(choices), value))
        return value

    def integer(self, table: Dict[str, Any], key: str, where: str, low: int, high: int) -> int:
        value = table[key]
        if not _is_int(value):
            raise self.fail("[%s] %s must be an integer (got %r)" % (where, key, value))
        if not low <= value <= high:
            raise self.fail("[%s] %s must be between %d and %d (got %d)" % (where, key, low, high, value))
        return value

    def decimal(self, value: Any, key: str, where: str) -> Decimal:
        """A number as an exact ``Decimal``: finite, at most :data:`DECIMAL_PLACES` places."""
        if not _is_number(value):
            raise self.fail("[%s] %s must be a number (got %r)" % (where, key, value))
        result = value if isinstance(value, Decimal) else Decimal(value)
        if not result.is_finite():
            raise self.fail("[%s] %s must be a finite number (got %s)" % (where, key, result))
        if result.quantize(_CENT) != result:
            raise self.fail("[%s] %s must have at most %d decimal places, the precision the desk reads it at (got %s)"
                            % (where, key, DECIMAL_PLACES, _text(result)))
        return result

    def number(self, table: Dict[str, Any], key: str, where: str) -> Decimal:
        return self.decimal(table[key], key, where)

    def pair(self, table: Dict[str, Any], key: str, where: str) -> List[Any]:
        value = table[key]
        if not isinstance(value, list) or len(value) != 2:
            raise self.fail("[%s] %s must be an array of two values [lo, hi] (got %r)" % (where, key, value))
        return value

    def int_band(self, table: Dict[str, Any], key: str, where: str) -> Tuple[int, int]:
        value = self.pair(table, key, where)
        if not all(_is_int(item) for item in value):
            raise self.fail("[%s] %s must be integers: two whole calendar days [lo, hi] (got %r)" % (where, key, value))
        return value[0], value[1]

    def decimal_band(self, table: Dict[str, Any], key: str, where: str) -> Tuple[Decimal, Decimal]:
        value = self.pair(table, key, where)
        if not all(_is_number(item) for item in value):
            raise self.fail("[%s] %s must be numbers [lo, hi] (got %r)" % (where, key, value))
        return self.decimal(value[0], key, where), self.decimal(value[1], key, where)

    # -- knobs ---------------------------------------------------------------------------------
    def dte(self, table: Dict[str, Any], where: str) -> Tuple[int, int]:
        lo, hi = self.int_band(table, "dte", where)
        if not (DTE_MIN <= lo < hi <= DTE_MAX and hi - lo <= DTE_SPAN_MAX):
            raise self.fail("[%s] dte must be calendar days with %d <= lo < hi <= %d and hi - lo <= %d (got [%d, %d])"
                            % (where, DTE_MIN, DTE_MAX, DTE_SPAN_MAX, lo, hi))
        return lo, hi

    def abs_delta(self, table: Dict[str, Any], where: str) -> Tuple[Decimal, Decimal]:
        lo, hi = self.decimal_band(table, "abs_delta", where)
        low, high = ABS_DELTA_RANGE
        if not low <= lo < hi <= high:
            raise self.fail("[%s] abs_delta must satisfy %s <= lo < hi <= %s (got [%s, %s])"
                            % (where, low, high, _text(lo), _text(hi)))
        return lo, hi

    def min_credit(self, table: Dict[str, Any], where: str) -> Decimal:
        value = self.number(table, "min_credit", where)
        if value < MIN_CREDIT_MIN:
            raise self.fail("[%s] min_credit must be a number >= %s per share (got %s)"
                            % (where, MIN_CREDIT_MIN, _text(value)))
        return value

    def floor_pct(self, table: Dict[str, Any], where: str) -> Tuple[Decimal, Decimal]:
        lo, hi = self.decimal_band(table, "floor_pct", where)
        low, high = FLOOR_PCT_RANGE
        if not low <= lo < hi <= high:
            raise self.fail("[%s] floor_pct must satisfy %s <= lo < hi <= %s (got [%s, %s])"
                            % (where, low, high, _text(lo), _text(hi)))
        return lo, hi

    def objective(self, table: Dict[str, Any], where: str) -> str:
        value = table["objective"]
        allowed = OBJECTIVES[where]
        if not isinstance(value, str) or value not in allowed:
            raise self.fail("[%s] objective must be one of %s, the objectives this release ranks [%s] by (got %r)"
                            % (where, ", ".join(allowed), where, value))
        return value

    # -- sections ------------------------------------------------------------------------------
    def disclosure(self, document: Dict[str, Any]) -> DisclosureSection:
        table = self.section(document, SECTION_DISCLOSURE)
        if table["acknowledged"] != DISCLOSURE_ID or not isinstance(table["acknowledged"], str):
            raise self.fail(DISCLOSURE_REASON)
        return DisclosureSection(acknowledged=DISCLOSURE_ID)

    def general(self, document: Dict[str, Any]) -> GeneralSection:
        where = SECTION_GENERAL
        table = self.section(document, where)
        spread = self.number(table, "max_spread_pct", where)
        if not Decimal(0) < spread <= MAX_SPREAD_PCT_MAX:
            raise self.fail("[%s] max_spread_pct must be in (0, %s] (got %s)" % (where, MAX_SPREAD_PCT_MAX, _text(spread)))
        low, high = MAX_CANDIDATES_RANGE
        return GeneralSection(max_spread_pct=spread,
                              max_candidates_shown=self.integer(table, "max_candidates_shown", where, low, high))

    def covered_call(self, document: Dict[str, Any]) -> CoveredCallSection:
        where = SECTION_COVERED_CALL
        table = self.section(document, where)
        dte, abs_delta = self.dte(table, where), self.abs_delta(table, where)
        objective = self.objective(table, where)
        centres, weights = self.centres_and_weights(table, where, {"dte": dte, "abs_delta": abs_delta}, objective)
        return CoveredCallSection(
            enabled=self.boolean(table, "enabled", where), dte=dte,
            abs_delta=abs_delta, min_credit=self.min_credit(table, where),
            strike_floor=self.choice(table, "strike_floor", where, STRIKE_FLOORS),
            earnings=self.choice(table, "earnings", where, EVENT_POLICIES),
            ex_dividend=self.choice(table, "ex_dividend", where, EVENT_POLICIES),
            objective=objective, centres=centres, band_weights=weights)

    def roll(self, document: Dict[str, Any]) -> RollSection:
        where = SECTION_ROLL
        table = self.section(document, where)
        roll_test = (self.choice(table, ROLL_TEST_KEY, where, ROLL_TESTS) if ROLL_TEST_KEY in table else None)
        return RollSection(
            enabled=self.boolean(table, "enabled", where), dte=self.dte(table, where),
            abs_delta=self.abs_delta(table, where), min_credit=self.min_credit(table, where),
            credit_only=(roll_test == ROLL_TEST_CREDIT if roll_test is not None
                         else self.boolean(table, "credit_only", where)),
            roll_test=roll_test,
            strike_floor=self.choice(table, "strike_floor", where, STRIKE_FLOORS),
            earnings=self.choice(table, "earnings", where, EVENT_POLICIES),
            ex_dividend=self.choice(table, "ex_dividend", where, EVENT_POLICIES),
            objective=self.objective(table, where))

    def protective_put(self, document: Dict[str, Any]) -> ProtectivePutSection:
        where = SECTION_PROTECTIVE_PUT
        table = self.section(document, where)
        enabled = self.boolean(table, "enabled", where)
        dte = self.dte(table, where)
        floor = self.floor_pct(table, where)
        stress = self.number(table, "stress_drop_pct", where)
        if not floor[1] < stress <= STRESS_DROP_PCT_MAX:
            raise self.fail("[%s] stress_drop_pct must lie above the floor band's hi and at most %s: %s < x <= %s "
                            "(got %s)" % (where, STRESS_DROP_PCT_MAX, _text(floor[1]), STRESS_DROP_PCT_MAX, _text(stress)))
        objective = self.objective(table, where)
        centres, weights = self.centres_and_weights(table, where, {"dte": dte, "floor_pct": floor}, objective)
        return ProtectivePutSection(enabled=enabled, dte=dte, floor_pct=floor, stress_drop_pct=stress,
                                    objective=objective, centres=centres, band_weights=weights)

    def collar(self, document: Dict[str, Any]) -> CollarSection:
        where = SECTION_COLLAR
        table = self.section(document, where)
        enabled = self.boolean(table, "enabled", where)
        dte = self.dte(table, where)
        floor = self.floor_pct(table, where)
        lo, hi = self.decimal_band(table, "cap_pct", where)
        low, high = CAP_PCT_RANGE
        if not low < lo < hi <= high:
            raise self.fail("[%s] cap_pct must satisfy %s < lo < hi <= %s (got [%s, %s])"
                            % (where, low, high, _text(lo), _text(hi)))
        debit = self.number(table, "max_net_debit", where)
        if debit < MAX_NET_DEBIT_MIN:
            raise self.fail("[%s] max_net_debit must be a number >= %s per share (0.00 is a zero cost or a credit; "
                            "got %s)" % (where, MAX_NET_DEBIT_MIN, _text(debit)))
        objective = self.objective(table, where)
        centres, weights = self.centres_and_weights(table, where, {"dte": dte, "floor_pct": floor, "cap_pct": (lo, hi)},
                                                    objective)
        return CollarSection(
            enabled=enabled, dte=dte, floor_pct=floor, cap_pct=(lo, hi), max_net_debit=debit,
            strike_floor=self.choice(table, "strike_floor", where, STRIKE_FLOORS),
            earnings=self.choice(table, "earnings", where, EVENT_POLICIES),
            ex_dividend=self.choice(table, "ex_dividend", where, EVENT_POLICIES),
            objective=objective, centres=centres, band_weights=weights)

    def parse(self, document: Dict[str, Any], sha256: str) -> StructuringConfig:
        sections = ", ".join("[%s]" % name for name in SECTIONS)
        for key, value in document.items():
            if key == "schema_version" or key in SECTIONS:
                continue
            if isinstance(value, dict):
                raise self.fail("unknown section [%s] (expected only %s)" % (key, sections))
            raise self.fail("unknown top-level key '%s' (expected schema_version and the sections %s)" % (key, sections))
        if "schema_version" not in document:
            raise self.fail("missing key 'schema_version' (it must be the integer %d or %d)" % SCHEMA_VERSIONS)
        version = document["schema_version"]
        if not _is_int(version) or version not in SCHEMA_VERSIONS:
            raise self.fail("schema_version must be the integer %d or %d (got %r)" % (SCHEMA_VERSIONS + (version,)))
        self.version = version
        return StructuringConfig(
            schema_version=version,
            disclosure=self.disclosure(document),
            general=self.general(document),
            covered_call=self.covered_call(document),
            roll=self.roll(document),
            protective_put=self.protective_put(document),
            collar=self.collar(document),
            sha256=sha256,
            path=self.path,
        )


def _parse_text(text: str, path: str, sha256: str) -> StructuringConfig:
    reader = _Reader(path)
    try:
        document = toml_compat.loads(text)
    except TomlError as exc:
        position = ""
        if exc.line is not None:
            position = " at line %d" % exc.line
            if exc.col is not None:
                position += ", column %d" % exc.col
        raise reader.fail("invalid TOML%s: %s" % (position, exc.message)) from None
    return reader.parse(document, sha256)


def parse_structuring_toml_text(text: str, path: str = "<text>") -> StructuringConfig:
    """Parse ``structuring.toml`` text held in memory; ``sha256`` is that of its UTF-8 bytes (``desk_init`` validates
    the rendered text with this before it writes anything)."""
    if not isinstance(text, str):
        raise StructuringConfigError(path, "structuring.toml text must be a str, got %s" % type(text).__name__)
    return _parse_text(text, path, hashlib.sha256(text.encode("utf-8")).hexdigest())


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _load_bytes(path: str, data: bytes) -> StructuringConfig:
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StructuringConfigError(path, "structuring.toml is not valid UTF-8: %s" % exc) from None
    return _parse_text(text, path, sha256)


QUARTER_SPAN_DAYS: int = 100
"""A call whose life is longer than this many calendar days spans at least one quarterly report or quarterly
ex-dividend date (companies report about every 91 days; the margin covers a late quarter)."""
SPAN_NOTE: str = ("%s dte starts at %d calendar days with %s = \"exclude\": every call that long spans a quarterly %s, "
                  "so a stock that %s never passes; %s = \"allow\" accepts it in the structuring tools only "
                  "(your exit alerts are unchanged)")
"""The note :func:`never_passing_notes` gives for one section and event."""


def never_passing_notes(config: "StructuringConfig") -> List[str]:
    """One :data:`SPAN_NOTE` per call-writing section whose ``dte`` window starts past :data:`QUARTER_SPAN_DAYS` with
    ``earnings`` or ``ex_dividend`` set to ``exclude``: criteria no candidate on a reporting (or quarterly dividend)
    stock can pass, which the interview shows before the owner accepts them."""
    notes: List[str] = []
    for name in (SECTION_COVERED_CALL, SECTION_ROLL, SECTION_COLLAR):
        section = config.section(name)
        if not section.enabled or section.dte[0] <= QUARTER_SPAN_DAYS:
            continue
        if section.earnings == EVENT_EXCLUDE:
            notes.append(SPAN_NOTE % ("[%s]" % name, section.dte[0], "earnings", "earnings date",
                                      "reports earnings", "earnings"))
        if section.ex_dividend == EVENT_EXCLUDE:
            notes.append(SPAN_NOTE % ("[%s]" % name, section.dte[0], "ex_dividend", "ex-dividend date",
                                      "pays a quarterly dividend", "ex_dividend"))
    return notes


def load_structuring_config(path: str) -> StructuringConfig:
    """Read and validate ``structuring.toml`` at ``path`` (module notes); errors are
    :class:`StructuringConfigError`, a missing file included."""
    try:
        data = _read_bytes(path)
    except OSError as exc:
        raise StructuringConfigError(path, "cannot read structuring.toml: %s" % (exc.strerror or exc)) from None
    return _load_bytes(path, data)


def load_structuring_config_if_present(path: str) -> Optional[StructuringConfig]:
    """:func:`load_structuring_config`, or ``None`` when there is no file at ``path`` (the structuring tools answer
    ``criteria_missing``; the acknowledgement set compares ``None``)."""
    try:
        data = _read_bytes(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StructuringConfigError(path, "cannot read structuring.toml: %s" % (exc.strerror or exc)) from None
    return _load_bytes(path, data)


__all__ = [
    "ABS_DELTA_RANGE",
    "BAND_CENTRE_KEYS",
    "CAP_PCT_RANGE",
    "CollarSection",
    "CoveredCallSection",
    "DECIMAL_PLACES",
    "DIRECTION_HIGHER",
    "DIRECTION_LOWER",
    "DISABLED_REASON",
    "DISCLOSURE_ID",
    "DISCLOSURE_REASON",
    "DISCLOSURE_TEXT",
    "DTE_MAX",
    "DTE_MIN",
    "DTE_SPAN_MAX",
    "DisclosureSection",
    "EVENT_ALLOW",
    "EVENT_EXCLUDE",
    "EVENT_POLICIES",
    "FILE_NAME",
    "FLOOR_PCT_RANGE",
    "GeneralSection",
    "MAX_CANDIDATES_RANGE",
    "MAX_NET_DEBIT_MIN",
    "MAX_SPREAD_PCT_MAX",
    "MIN_CREDIT_MIN",
    "OBJECTIVES",
    "OBJECTIVE_BAND_CENTRE",
    "OBJECTIVE_CAP_WITHIN_BUDGET",
    "OBJECTIVE_DEFS",
    "OBJECTIVE_EXTRINSIC_PER_DAY",
    "OBJECTIVE_IDS",
    "OBJECTIVE_NET_EXTRINSIC_PER_ADDED_DAY",
    "OBJECTIVE_STRESS_COST_PER_DAY",
    "Objective",
    "ProtectivePutSection",
    "QUARTER_SPAN_DAYS",
    "RollSection",
    "SCHEMA_VERSION",
    "SECTIONS",
    "SECTION_COLLAR",
    "SECTION_COVERED_CALL",
    "SECTION_DISCLOSURE",
    "SECTION_GENERAL",
    "SECTION_KEYS",
    "SECTION_PROTECTIVE_PUT",
    "SECTION_ROLL",
    "SPAN_NOTE",
    "STRESS_DROP_PCT_MAX",
    "STRIKE_FLOORS",
    "STRIKE_FLOOR_COST_BASIS",
    "STRIKE_FLOOR_NONE",
    "STRUCTURE_SECTIONS",
    "StructuringConfig",
    "StructuringConfigError",
    "UNKNOWN_EVENT_RULE",
    "load_structuring_config",
    "load_structuring_config_if_present",
    "never_passing_notes",
    "objective_for",
    "parse_structuring_toml_text",
]
