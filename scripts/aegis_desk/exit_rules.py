"""The exit rulebook: grammar, metric catalogue, Kleene evaluation, verdicts, the adoption lint (plan section 8).

Standard library only; no I/O except :func:`load_rulebook`, :func:`load_adopted` and
:func:`load_adopted_rulebook`, which read the owner's files; :func:`evaluate` is pure over the book dict
(:func:`aegis_desk.book.load_book`'s ``document``, or a saved book through :func:`book_from_json`). No ``eval``
anywhere: a rule is data, read by a fixed grammar and evaluated by a fixed catalogue. The rules never compute a
number the book does not already hold except the ones defined here (decision 9 of the PR 2c addendum), and every
number a rule reports is a ``Decimal`` (or an ``int`` count of days, or a ``bool``) quantised as decision 15 says,
so the alert prints exactly the value the verdict was decided on.

Grammar (strict TOML through :func:`aegis_desk.toml_compat.loads`; unknown keys and sections are errors)
----------------------------------------------------------------------------------------------------------
Top level: ``schema_version = 1``, ``rulebook_version = "X.Y.Z"`` (never a leading ``v``), ``title``, ``[defaults]``
(every key required: ``mark_basis`` ``liq | mid``, ``confirmations`` 1..3, ``confirmation_gap_min`` 1..60,
``confirmation_window_min`` gap..240, ``cooldown_min`` 0..1440, ``rearm`` ``next_trading_day | immediate |
hysteresis``, ``hysteresis_pct`` 0..50) and at least one ``[[rule]]``. A rule: ``id`` (``^[A-Z][A-Z0-9_]{1,15}$``,
unique), ``title``, ``role`` (``take_profit | loss | time | risk | event | portfolio | watch``), ``scope`` (``group |
leg | underlying | portfolio``), ``applies_to``, ``input_class`` (``E | M | G``, at least the floor of every metric it
reads), ``when`` (a non-empty conjunction of inline tables ``{metric, op, value, unit, dte_unit}``), ``verdict``
(``EXIT_SIGNAL | WATCH``; an underlying or portfolio rule is ``WATCH``: those are reported in their own section and
never set a group verdict), ``plan`` (``close | roll_review | hold_review``), ``origin`` (``example |
example-accepted | owner-entered``), ``source``; optional ``mark_basis``, ``confirmations``,
``confirmation_gap_min``, ``confirmation_window_min``, ``cooldown_min``, ``rearm``, ``hysteresis_pct`` (overrides,
bounded exactly as the defaults), ``suppress_if_tag`` (``assign-ok | earnings-ok``; group and leg rules only) and
``owner_must_set``. ``op`` is ``< <= > >= ==`` for a numeric metric (``value`` a TOML number: an integer or a float,
read as ``Decimal``) and ``is_true | is_false`` for a boolean metric (no ``value``, no ``unit``). ``unit`` is
required for the percent and usd metrics and, when given, must be the metric's own unit; ``dte_unit`` (``calendar
| trading``) is required on every ``dte`` condition and refused on any other; a threshold on a days metric is a whole
number of days. Identifiers are ASCII and end where they end (``\\Z``: a trailing newline is no part of an id or a
version). Every problem is collected and :class:`RulebookError` lists them all (``code`` ``config_invalid``;
``rules_missing`` for an absent file).

Subjects and ``applies_to`` (decision 13)
-----------------------------------------
Structures are ``grouping.STRUCTURES`` (``invalid`` excepted: a quarantined group is UNAVAILABLE without
evaluation). Pseudo-structures: ``short_call_leg`` (the open short call of a covered call or a collar),
``long_put_leg`` (the open long put of a protective put or a collar), ``any_short_call_leg`` (every open short call
of any named structure: covered call, collar, naked short call, call verticals), ``any_option_leg`` (every open
option leg, custom groups included), ``any_group`` (every open group, custom included), ``any_underlying`` and
``portfolio``. In a ``group`` rule a structure name (or ``any_group``) makes the group the subject and a leg
pseudo-structure makes that leg the subject; in a ``leg`` rule a structure name selects that structure's open
SHORT option legs (the legs that carry assignment and delta risk). A leg's result is its group's; several subjects
in one group combine by Kleene disjunction (the rule is TRUE for the group when it is TRUE on any of them).
``underlying`` rules apply to ``any_underlying`` (one subject per symbol of ``book["underlyings"]``), ``portfolio``
rules to ``portfolio``. Custom groups therefore get only ``any_group``, ``any_option_leg`` and the section rules.

The metric catalogue (:data:`METRICS`; decision 9's field map)
-------------------------------------------------------------
Group ``g`` (its open legs are the ``book["legs"]`` with ``row_number`` in ``g["legs"]`` and ``status == "open"``;
``S`` is ``book["underlyings"][g["underlying"]]["mark_mid"]``, set only for a usable grade):
``pct_captured`` (credit structures with a credit basis, ``g["basis_kind"] == "credit"``: ``(credit - ctc) / credit x
100``, ``credit = -g["net_cost"]``, ``ctc = -sum(open_quantity x multiplier x side)``; a custom group holding stock
is no credit, so it does not apply there, nor does ``loss_multiple_of_credit``); ``pct_of_max_profit`` (debit and stock-basis structures with a bounded
max profit: unrealized over ``g["max_profit"]``); ``pct_of_debit`` (``long_call``, ``long_put``,
``vertical_debit_*``: unrealized over ``g["net_cost"]``); ``unrealized_pl``; ``loss_multiple_of_credit`` (``-unrealized /
credit``; the inputs also carry the cost-to-close multiple, loss + 1); ``pct_of_max_loss`` (``-unrealized /
g["max_loss"] x 100``); ``dte`` (the minimum over the open option legs of ``dte_trading`` / ``dte_calendar``);
``between_strikes`` (verticals: ``K_low < S < K_high``); ``net_delta`` (``g["net_greeks"]["delta"]``);
``stock_above_short_call`` (``S >= K + 0.01`` of the open short call); ``stock_below_long_put`` (``S <= K - 0.01``
of an open long put); ``spot_vs_stop_pct`` (``(S - stop) / stop x 100`` for a group carrying
``g["underlying_stop"]``; not applicable without one); ``days_to_earnings``. Leg ``l``: ``pct_captured`` (a short
leg: ``(entry_price_per_share - side) / entry_price_per_share x 100``), ``abs_delta`` (only when ``iv_status`` is
``ok``), ``dte``, ``itm``, ``extrinsic_bid`` (the book's), ``pct_from_strike`` (``expiry.distance_pct(S, K)``),
``auto_exercise_itm`` (``open_quantity > 0`` and ``itm`` and ``dte_trading == 0``), ``days_to_ex_dividend``,
``dividend_amount``, ``extrinsic_minus_dividend`` (decision 14: ``expiry.dividend_window``, the assignment watch's
own facts, with the book's ``extrinsic_bid``), ``days_to_earnings`` (decision 9: ``trading_days_between(today,
date)``, 0 on the day, not applicable after it, ``estimated`` evaluated and noted). Underlying:
``move_pct_from_prev_eod``, ``days_to_earnings``. Portfolio: ``day_pl``, ``max_concentration_pct``. Each entry
lists the book fields it reads (:attr:`Metric.fields`); :func:`missing_inputs` and the lint check them against
``book.LEG_KEYS``, ``GROUP_KEYS``, ``UNDERLYING_KEYS`` and ``PORTFOLIO_KEYS``.

Marks: take-profit reads the fresh, conservative side (decision 10)
-------------------------------------------------------------------
A ``take_profit`` rule on the ``liq`` basis reads, for every open leg it needs, the fresh side of any grade A-E:
a short leg at its ``fresh_ask``, a long leg at its ``fresh_bid`` (a wide spread can only delay a take-profit: the
safe direction), with the note ``quote grade <G>: the conservative side stands`` on a leg whose ``mark_source`` is
not ``mid``. A zero fresh ask is no offer: the read is unavailable, ``quote grade <G>: no offer (ask 0)``, whatever
the grade (a cost to close of 0 would read as 100 % captured); a long leg's zero fresh bid reads the leg as
worthless (it can only delay a take-profit). Every other role, and the ``mid`` basis, is gated on ``mark_source ==
"mid"`` (``g["data_quality"] == "ok"`` for a group): a loss test on a leg without it is unavailable, ``quote grade
<G>: a loss test needs a two-sided fresh quote``. With every open leg on ``mark_source`` ``mid`` the take-profit
value equals the book's ``pct_captured_liq``.

History versus data (decision 11)
---------------------------------
An input that needs a quote the book could not use (no usable mark, a group whose ``data_quality`` is not
``ok``, no IV) is ``unavailable``. An input that needs the desk's previous end-of-day snapshot that does not exist
for it (``prev_eod_mid`` null beside a set ``mark_mid``; the portfolio day P/L null, or an ok open group with an
open leg whose ``day_pl`` is null, while no open group is unavailable) is ``no_history``: reported in its section
(:attr:`Evaluation.no_history_rule_ids`, :attr:`Evaluation.history_date`), never a group verdict, never the exit
code. With an unavailable open group the day P/L is ``unavailable``.

Evaluation, precision and verdicts
----------------------------------
Each condition is TRUE, FALSE or UNKNOWN. A metric that cannot speak about a subject for a reason of record (no
stop on the group, a confirmed absence of a dividend or of earnings, earnings already past, a contract gone
before the last cum-dividend day, a group without an open option leg) is *not applicable*: the condition is FALSE
and the rule ``clear`` (silent). A metric applied to a structure it is not defined for (a rule the lint refuses)
is UNKNOWN (``... does not apply to ...``). A rule is the Kleene conjunction of its conditions (FALSE if any is
FALSE, else UNKNOWN if any is, else TRUE); ``max_concentration_pct`` is the Kleene maximum (decision 16,
:func:`kleene_maximum`). Percentages are quantised to two decimals half-even (``pnl.quantize_pct``), multiples to
two decimals, money totals to the cent, Greeks through ``Decimal(repr(x))`` to two decimals; per-share prices
(``extrinsic_bid``, a dividend, their difference) are exact decimals already and are compared as they are. A
percentage or multiple the engine computes keeps more decimals only where two would decide the rule the other way
from the exact value, or would print a nonzero value as zero (:func:`refine_at_threshold`: a stock half a cent
above its stop is ``0.0034 %``, not ``0.00 %`` "at or below 0"). The value printed is the value compared. Results:
TRUE -> ``fired`` (with the rule's verdict), ``pending`` (an M or G rule still waiting for its confirmations,
:mod:`aegis_desk.rule_state`) or ``cooldown`` (a rule that fired, cleared and is true again before it re-arms:
reported with its values, no new signal, and like ``pending`` it counts for no verdict); UNKNOWN -> ``unavailable``
(the first unavailable input named in ``reason``) or ``no_history``; FALSE -> ``clear``. A rule whose ``suppress_if_tag`` is
on any row of the group is ``suppressed`` (its values still computed and reported, it never fires and records no
state) unless it is not applicable (then it is ``clear``). Group verdict precedence: ``EXIT_SIGNAL`` >
``UNAVAILABLE`` > ``WATCH`` > ``HOLD``; a pending rule counts as neither; ``HOLD`` is never the verdict while an
applicable rule is unavailable. A quarantined group is ``UNAVAILABLE`` with its reasons. Exit code
(:meth:`Evaluation.exit_code`): 10 when any group is ``EXIT_SIGNAL``, 20 when any is ``UNAVAILABLE``, 0 otherwise;
the underlying and portfolio sections never change it.

Confirmations (decision 17(i)) and state
----------------------------------------
E rules never wait; M and G rules need their confirmations unless a single observation suffices. Every M and G
reading carries the quote stamps it depends on (a leg's own, the open legs' for a group field such as
``unrealized_pl_liq``, the underlying's where a spot is read, every open leg's and underlying's for the portfolio):
the observation is recorded at the oldest of them, so a re-run on the same quotes is never a second confirmation,
and a TRUE M or G reading without any stamp is ``unavailable`` (``NO_STAMP_REASON``), never a confirmation on the
clock. One observation suffices
when :func:`aegis_desk.rule_state.single_observation` says so: the session is not live, or ``next_run_utc`` is later
than the session's close (``None`` means another observation is coming). ``state`` is the dict
``rule_state.load(paths)`` returns; :attr:`Evaluation.state_updates` lists one :func:`rule_state.update_record` per
observed (non-suppressed) rule, for ``rule_state.apply(state, updates, now)``. Keys: ``<group_id>|<rule>``,
``underlying:<SYM>|<rule>``, ``portfolio|<rule>``. ``evaluate`` never changes ``state``.

Rows (tags and opened dates)
----------------------------
The book does not carry the rows' ``#tags`` nor their ``opened`` dates; pass ``rows=run.positions.rows``
(objects with ``group_id``, ``row_number``, ``tags`` and ``opened``) for ``suppress_if_tag`` and the first
observation. Without ``rows`` nothing is suppressed (a real condition is never silently switched off) and the
evaluation says so in ``warnings``; the opened date then comes from the trade id's date (``AG-YYYYMMDD-NN``),
or is unknown (treated as before today).

The adoption lint (:func:`lint`)
--------------------------------
Grammar and bounds are :func:`load_rulebook`'s (a rulebook that loads has passed them). The lint adds, per rule:
``not_accepted`` (``origin = "example"``: every rule is adopted individually), ``owner_must_set`` (still set and
not ``owner-entered``), ``max_loss_over_100``, ``input_unavailable`` (a metric reads a field the book schema does
not have), ``loss_on_short_call_leg`` (a loss rule on ``short_call_leg`` or ``any_short_call_leg``),
``take_profit_metric`` (a take-profit rule whose first condition is not ``pct_captured``, ``pct_of_max_profit`` or
``pct_of_debit``, or, for a held structure, not that structure's own: :data:`STRUCTURE_TAKE_PROFIT_METRICS`, so the
short leg's credit of a debit vertical covers nothing), ``selects_nothing`` (a leg rule naming a structure without a short option leg),
``metric_not_applicable`` (a metric a named structure, or a held structure reached through ``any_group``, does not
define); and per held structure (:func:`held_structures`): ``uncovered_structure`` (no applicable take-profit rule;
``long_stock``, ``short_stock`` and ``protective_put`` need none), ``duplicate_loss`` (more than one loss rule),
``loss_metric`` (a loss rule whose metric is not a loss metric, or on a covered call or collar not
``unrealized_pl`` / ``pct_of_max_loss``). The take-profit rules applying to one structure may be several (TP50 and
TP80 on the same short premium); each must read that structure's own take-profit metric to cover it.

Identity and adoption files
---------------------------
:func:`rule_sha` is the SHA-256 of the canonical JSON of a rule's semantics (:func:`rule_canonical`: every field
that changes what it evaluates or how it confirms, re-arms and posts, defaults resolved, ``applies_to`` sorted,
numbers in their plain form) and not of its ``title``, ``origin``, ``source`` or ``owner_must_set``: accepting an
example or rewording it keeps its state. ``rules/ADOPTED`` (:func:`adopted_text`, :func:`load_adopted`) names the
adopted version, the SHA-256 of the file's bytes, the instant, the owner phrase's SHA-256 and every per-rule sha;
:func:`load_adopted_rulebook` refuses a file whose bytes, version or rule shas no longer match (``rules_tampered``).
``rules/CHANGELOG.md`` is append-only (:data:`CHANGELOG_HEADER`, :func:`changelog_entry`, :func:`changelog_append`).

``book_from_json`` (decision 4)
-------------------------------
A saved book (``state.read_json`` of a snapshot or a ``--book`` file) back to the in-memory types, driven by
explicit field tables (:data:`LEG_FIELDS`, :data:`GROUP_FIELDS`, :data:`UNDERLYING_FIELDS`,
:data:`PORTFOLIO_FIELDS`): money and percent strings -> ``Decimal``, ISO dates -> ``date``, instants -> aware
``datetime``, floats stay floats; unknown or missing keys, and a value of the wrong type, are
:class:`BookFormatError`. A snapshot written before ``fresh_bid`` / ``fresh_ask`` existed reads with them null.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import book as book_module
from . import calendar_events, expiry, grouping, market_clock, occ, payoff, pnl, rule_state, toml_compat
from .calendar_events import CalendarFile
from .desk_paths import DeskPaths
from .market_clock import SessionInfo

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

SCHEMA_VERSION: int = 1
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\Z")
"""``X.Y.Z`` in ASCII digits; ``\\Z`` (never ``$``, which also matches before a final newline) ends the match."""
RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,15}\Z")
RULE_ID_WORDS: str = "a capital letter, then 1 to 15 capitals, digits or underscores"
SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
TRADE_ID_DATE_RE = re.compile(r"^AG-([0-9]{4})([0-9]{2})([0-9]{2})-[0-9]{2,3}\Z")

BASIS_LIQ: str = "liq"
BASIS_MID: str = "mid"
MARK_BASES: Tuple[str, ...] = (BASIS_LIQ, BASIS_MID)

ROLE_TAKE_PROFIT: str = "take_profit"
ROLE_LOSS: str = "loss"
ROLES: Tuple[str, ...] = (ROLE_TAKE_PROFIT, ROLE_LOSS, "time", "risk", "event", "portfolio", "watch")

SCOPE_GROUP: str = "group"
SCOPE_LEG: str = "leg"
SCOPE_UNDERLYING: str = "underlying"
SCOPE_PORTFOLIO: str = "portfolio"
SCOPES: Tuple[str, ...] = (SCOPE_GROUP, SCOPE_LEG, SCOPE_UNDERLYING, SCOPE_PORTFOLIO)
SECTION_SCOPES: Tuple[str, ...] = (SCOPE_UNDERLYING, SCOPE_PORTFOLIO)

INPUT_CLASSES: Tuple[str, ...] = rule_state.INPUT_CLASSES
_CLASS_RANK: Dict[str, int] = {"E": 0, "M": 1, "G": 2}

COMPARISON_OPS: Tuple[str, ...] = ("<", "<=", ">", ">=", "==")
BOOL_OPS: Tuple[str, ...] = ("is_true", "is_false")
OPS: Tuple[str, ...] = COMPARISON_OPS + BOOL_OPS
UNITS: Tuple[str, ...] = ("percent", "usd", "days", "delta", "multiple", "pct_of_threshold")
REQUIRED_UNITS: Tuple[str, ...] = ("percent", "usd")
UNIT_BOOL: str = "bool"
DTE_UNITS: Tuple[str, ...] = market_clock.DTE_UNITS

VERDICT_EXIT_SIGNAL: str = "EXIT_SIGNAL"
VERDICT_WATCH: str = "WATCH"
VERDICT_UNAVAILABLE: str = "UNAVAILABLE"
VERDICT_HOLD: str = "HOLD"
RULE_VERDICTS: Tuple[str, ...] = (VERDICT_EXIT_SIGNAL, VERDICT_WATCH)
GROUP_VERDICTS: Tuple[str, ...] = (VERDICT_EXIT_SIGNAL, VERDICT_UNAVAILABLE, VERDICT_WATCH, VERDICT_HOLD)
"""Group verdicts in precedence order."""

PLANS: Tuple[str, ...] = ("close", "roll_review", "hold_review")
ORIGIN_EXAMPLE: str = "example"
ORIGIN_ACCEPTED: str = "example-accepted"
ORIGIN_OWNER: str = "owner-entered"
ORIGINS: Tuple[str, ...] = (ORIGIN_EXAMPLE, ORIGIN_ACCEPTED, ORIGIN_OWNER)
SUPPRESS_TAGS: Tuple[str, ...] = ("assign-ok", "earnings-ok")
REARM_MODES: Tuple[str, ...] = rule_state.REARM_MODES

SHORT_CALL_LEG: str = "short_call_leg"
LONG_PUT_LEG: str = "long_put_leg"
ANY_SHORT_CALL_LEG: str = "any_short_call_leg"
ANY_OPTION_LEG: str = "any_option_leg"
ANY_GROUP: str = "any_group"
ANY_UNDERLYING: str = "any_underlying"
PORTFOLIO: str = "portfolio"
LEG_SELECTORS: Tuple[str, ...] = (SHORT_CALL_LEG, LONG_PUT_LEG, ANY_SHORT_CALL_LEG, ANY_OPTION_LEG)
PSEUDO_STRUCTURES: Tuple[str, ...] = LEG_SELECTORS + (ANY_GROUP, ANY_UNDERLYING, PORTFOLIO)
RULE_STRUCTURES: Tuple[str, ...] = tuple(name for name in grouping.STRUCTURES if name != grouping.STRUCTURE_INVALID)
APPLIES_TO_NAMES: Tuple[str, ...] = RULE_STRUCTURES + PSEUDO_STRUCTURES

CREDIT_STRUCTURES: Tuple[str, ...] = ("cash_secured_put", "short_call", "vertical_credit_call", "vertical_credit_put")
DEBIT_OPTION_STRUCTURES: Tuple[str, ...] = ("long_call", "long_put", "vertical_debit_call", "vertical_debit_put")
COVERED_STRUCTURES: Tuple[str, ...] = ("covered_call", "collar")
HEDGED_STRUCTURES: Tuple[str, ...] = ("protective_put", "collar")
SHORT_CALL_STRUCTURES: Tuple[str, ...] = ("covered_call", "collar", "short_call", "vertical_credit_call",
                                          "vertical_debit_call")
SHORT_LEG_STRUCTURES: Tuple[str, ...] = ("short_call", "cash_secured_put", "covered_call", "collar",
                                         "vertical_credit_call", "vertical_credit_put", "vertical_debit_call",
                                         "vertical_debit_put", "custom")
OPTION_STRUCTURES: Tuple[str, ...] = tuple(name for name in RULE_STRUCTURES if name not in ("long_stock", "short_stock"))
TAKE_PROFIT_EXEMPT: Tuple[str, ...] = ("long_stock", "short_stock", "protective_put")
TAKE_PROFIT_METRICS: Tuple[str, ...] = ("pct_captured", "pct_of_max_profit", "pct_of_debit")
LOSS_METRICS: Tuple[str, ...] = ("loss_multiple_of_credit", "pct_of_max_loss", "unrealized_pl", "pct_of_debit")
COVERED_LOSS_METRICS: Tuple[str, ...] = ("unrealized_pl", "pct_of_max_loss")
STRUCTURE_TAKE_PROFIT_METRICS: Dict[str, Tuple[str, ...]] = dict(
    [(name, ("pct_captured",)) for name in CREDIT_STRUCTURES]
    + [(name, ("pct_of_max_profit", "pct_of_debit")) for name in DEBIT_OPTION_STRUCTURES]
    + [(name, ("pct_captured", "pct_of_max_profit")) for name in COVERED_STRUCTURES])
"""The take-profit metrics that cover a held structure (the brief's per-structure table): ``pct_captured`` for the
credit structures, ``pct_of_max_profit`` or ``pct_of_debit`` for the debit ones, the short call's credit or the max
profit for a covered call or collar; any take-profit metric for a structure not listed (custom)."""

TRUE: str = rule_state.TRUTH_TRUE
FALSE: str = rule_state.TRUTH_FALSE
UNKNOWN: str = rule_state.TRUTH_UNKNOWN

RESULT_FIRED: str = "fired"
RESULT_PENDING: str = "pending"
RESULT_CLEAR: str = "clear"
RESULT_UNAVAILABLE: str = "unavailable"
RESULT_NO_HISTORY: str = "no_history"
RESULT_SUPPRESSED: str = "suppressed"
RESULT_COOLDOWN: str = "cooldown"
RESULTS: Tuple[str, ...] = (RESULT_FIRED, RESULT_PENDING, RESULT_CLEAR, RESULT_UNAVAILABLE, RESULT_NO_HISTORY,
                            RESULT_SUPPRESSED, RESULT_COOLDOWN)

KIND_VALUE: str = "value"
KIND_NOT_APPLICABLE: str = "not_applicable"
KIND_UNAVAILABLE: str = "unavailable"
KIND_NO_HISTORY: str = "no_history"

LEVEL_GROUP: str = "group"
LEVEL_LEG: str = "leg"
LEVEL_UNDERLYING: str = "underlying"
LEVEL_PORTFOLIO: str = "portfolio"

CONSERVATIVE_NOTE: str = "quote grade %s: the conservative side stands"
NO_OFFER_REASON: str = "quote grade %s: no offer (ask 0)"
LOSS_GATE_REASON: str = "quote grade %s: a loss test needs a two-sided fresh quote"
NO_STAMP_REASON: str = "no quote stamp behind the reading: an M or G rule confirms on the quote's as-of, never the clock"
COOLDOWN_REASON: str = "fired and cleared; true again before it re-arms at %s ET, so no new signal"
ESTIMATED_EARNINGS_NOTE: str = "earnings date estimated (research/calendar.toml)"
WARNING_NO_ROWS: str = ("row tags unknown (no rows passed to evaluate): suppress_if_tag was not applied, so no rule was "
                        "switched off")

TOP_LEVEL_KEYS: Tuple[str, ...] = ("schema_version", "rulebook_version", "title", "defaults", "rule")
DEFAULTS_KEYS: Tuple[str, ...] = ("mark_basis", "confirmations", "confirmation_gap_min", "confirmation_window_min",
                                  "cooldown_min", "rearm", "hysteresis_pct")
RULE_REQUIRED_KEYS: Tuple[str, ...] = ("id", "title", "role", "scope", "applies_to", "input_class", "when", "verdict",
                                       "plan", "origin", "source")
RULE_OPTIONAL_KEYS: Tuple[str, ...] = DEFAULTS_KEYS + ("suppress_if_tag", "owner_must_set")
RULE_KEYS: Tuple[str, ...] = RULE_REQUIRED_KEYS + RULE_OPTIONAL_KEYS
CONDITION_KEYS: Tuple[str, ...] = ("metric", "op", "value", "unit", "dte_unit")
CONFIRMATIONS_RANGE: Tuple[int, int] = (1, 3)
GAP_RANGE: Tuple[int, int] = (1, 60)
WINDOW_MAX: int = 240
COOLDOWN_RANGE: Tuple[int, int] = (0, 1440)
HYSTERESIS_RANGE: Tuple[Decimal, Decimal] = (Decimal(0), Decimal(50))

LINT_NOT_ACCEPTED: str = "not_accepted"
LINT_OWNER_MUST_SET: str = "owner_must_set"
LINT_UNCOVERED: str = "uncovered_structure"
LINT_TAKE_PROFIT_METRIC: str = "take_profit_metric"
LINT_LOSS_ON_SHORT_CALL_LEG: str = "loss_on_short_call_leg"
LINT_LOSS_METRIC: str = "loss_metric"
LINT_DUPLICATE_LOSS: str = "duplicate_loss"
LINT_MAX_LOSS_OVER_100: str = "max_loss_over_100"
LINT_METRIC_NOT_APPLICABLE: str = "metric_not_applicable"
LINT_INPUT_UNAVAILABLE: str = "input_unavailable"
LINT_SELECTS_NOTHING: str = "selects_nothing"
LINT_CODES: Tuple[str, ...] = (LINT_NOT_ACCEPTED, LINT_OWNER_MUST_SET, LINT_UNCOVERED, LINT_TAKE_PROFIT_METRIC,
                               LINT_LOSS_ON_SHORT_CALL_LEG, LINT_LOSS_METRIC, LINT_DUPLICATE_LOSS, LINT_MAX_LOSS_OVER_100,
                               LINT_METRIC_NOT_APPLICABLE, LINT_INPUT_UNAVAILABLE, LINT_SELECTS_NOTHING)
ACCEPTANCE_CODES: Tuple[str, ...] = (LINT_NOT_ACCEPTED, LINT_OWNER_MUST_SET)

ADOPTED_KEYS: Tuple[str, ...] = ("version", "sha256", "adopted_utc", "owner_phrase_sha256", "rules_sha")
CHANGELOG_HEADER: str = (
    "# Rulebook changelog\n\n"
    "Append-only: rules_adopt.py appends one entry per adoption and never edits an earlier one. Each entry names\n"
    "the adopted file and the SHA-256 of its bytes, how the owner adopted it, and every rule with its origin mark\n"
    "(owner-entered or example-accepted) and its threshold.\n\n"
)

_HUNDRED = Decimal(100)
_ZERO = Decimal(0)
_CENT = Decimal("0.01")
_GRADE_ORDER: Tuple[str, ...] = ("A", "B", "C", "D", "E", "F")


# --------------------------------------------------------------------------------------------
# Errors and the catalogue
# --------------------------------------------------------------------------------------------


class RulebookError(ValueError):
    """A rulebook (or ``rules/ADOPTED``) that cannot be used: ``path``, ``code`` (``config_invalid``, ``rules_missing``,
    ``not_adopted``, ``adopted_invalid``, ``rules_tampered``) and every ``problem`` found."""

    def __init__(self, path: str, problems: Sequence[str], code: str = "config_invalid") -> None:
        self.path = path
        self.problems = list(problems)
        self.code = code
        super().__init__("%s: %s" % (path, "; ".join(self.problems)))


class BookFormatError(ValueError):
    """A saved book whose shape is not the book schema (:func:`book_from_json`)."""


@dataclass(frozen=True)
class Metric:
    """One catalogue entry (module notes): its unit (``percent``, ``usd``, ``days``, ``delta``, ``multiple`` or
    ``bool``), input-class floor, the subject levels it is defined on, the book fields it reads, the structures it
    is defined on at group level (``None``: every one) and whether at leg level it needs a short leg."""

    name: str
    unit: str
    input_class: str
    levels: Tuple[str, ...]
    fields: Tuple[str, ...]
    structures: Optional[Tuple[str, ...]] = None
    short_legs_only: bool = False
    description: str = ""

    @property
    def is_bool(self) -> bool:
        return self.unit == UNIT_BOOL


_SIDES = ("legs.open_quantity", "legs.multiplier", "legs.fresh_bid", "legs.fresh_ask", "legs.mark_source",
          "legs.quote_grade", "legs.quote_status", "legs.as_of", "legs.status", "groups.legs", "groups.data_quality")
_SPOT = ("underlyings.mark_mid", "underlyings.as_of", "underlyings.quote_grade", "groups.underlying")

METRICS: Dict[str, Metric] = {
    "pct_captured": Metric("pct_captured", "percent", "M", (LEVEL_GROUP, LEVEL_LEG),
                           _SIDES + ("groups.net_cost", "groups.basis_kind", "groups.pct_captured_liq", "groups.pct_captured_mid",
                                     "legs.entry_price_per_share", "legs.mark_liq", "legs.mark_mid"),
                           CREDIT_STRUCTURES + ("custom",), True, "share of the credit captured"),
    "pct_of_max_profit": Metric("pct_of_max_profit", "percent", "M", (LEVEL_GROUP,),
                                _SIDES + ("groups.max_profit", "groups.net_cost", "groups.basis_kind",
                                          "groups.pct_captured_liq", "groups.pct_captured_mid"),
                                ("long_put", "vertical_debit_call", "vertical_debit_put", "covered_call", "collar",
                                 "short_stock", "custom"), False, "unrealized P/L over the max profit"),
    "pct_of_debit": Metric("pct_of_debit", "percent", "M", (LEVEL_GROUP,),
                           _SIDES + ("groups.net_cost", "groups.unrealized_pl_liq", "groups.unrealized_pl_mid"),
                           DEBIT_OPTION_STRUCTURES + ("custom",), False, "unrealized P/L over the debit paid"),
    "unrealized_pl": Metric("unrealized_pl", "usd", "M", (LEVEL_GROUP,),
                            _SIDES + ("groups.net_cost", "groups.unrealized_pl_liq", "groups.unrealized_pl_mid"),
                            None, False, "unrealized P/L of the group"),
    "loss_multiple_of_credit": Metric("loss_multiple_of_credit", "multiple", "M", (LEVEL_GROUP,),
                                      ("groups.net_cost", "groups.basis_kind", "groups.unrealized_pl_liq",
                                       "groups.unrealized_pl_mid", "groups.data_quality", "legs.mark_source", "legs.as_of"),
                                      CREDIT_STRUCTURES + ("custom",), False, "the loss as a multiple of the credit"),
    "pct_of_max_loss": Metric("pct_of_max_loss", "percent", "M", (LEVEL_GROUP,),
                              ("groups.max_loss", "groups.unrealized_pl_liq", "groups.unrealized_pl_mid",
                               "groups.data_quality", "legs.mark_source"),
                              ("long_stock", "long_call", "long_put", "cash_secured_put", "covered_call", "protective_put",
                               "collar", "vertical_credit_call", "vertical_credit_put", "vertical_debit_call",
                               "vertical_debit_put", "custom"), False, "the loss over the max loss"),
    "dte": Metric("dte", "days", "E", (LEVEL_GROUP, LEVEL_LEG), ("legs.dte_trading", "legs.dte_calendar", "legs.occ"),
                  OPTION_STRUCTURES, False, "days to the last trading day"),
    "between_strikes": Metric("between_strikes", UNIT_BOOL, "M", (LEVEL_GROUP,), _SPOT + ("legs.strike", "legs.occ"),
                              grouping.VERTICAL_STRUCTURES, False, "the underlying between the vertical's strikes"),
    "net_delta": Metric("net_delta", "delta", "G", (LEVEL_GROUP,), ("groups.net_greeks", "legs.iv_status"),
                        None, False, "the group's net delta"),
    "stock_above_short_call": Metric("stock_above_short_call", UNIT_BOOL, "M", (LEVEL_GROUP,),
                                     _SPOT + ("legs.strike", "legs.right", "legs.open_quantity"),
                                     COVERED_STRUCTURES, False, "the underlying at least 0.01 above the short call"),
    "stock_below_long_put": Metric("stock_below_long_put", UNIT_BOOL, "M", (LEVEL_GROUP,),
                                   _SPOT + ("legs.strike", "legs.right", "legs.open_quantity"),
                                   HEDGED_STRUCTURES, False, "the underlying at least 0.01 below the long put"),
    "spot_vs_stop_pct": Metric("spot_vs_stop_pct", "percent", "M", (LEVEL_GROUP,), _SPOT + ("groups.underlying_stop",),
                               None, False, "the underlying against the group's stop"),
    "days_to_earnings": Metric("days_to_earnings", "days", "E", (LEVEL_GROUP, LEVEL_LEG, LEVEL_UNDERLYING),
                               ("groups.underlying", "legs.underlying"), None, False,
                               "trading days to the next earnings release"),
    "abs_delta": Metric("abs_delta", "delta", "G", (LEVEL_LEG,), ("legs.greeks", "legs.iv_status"), None, False,
                        "the leg's absolute delta"),
    "itm": Metric("itm", UNIT_BOOL, "M", (LEVEL_LEG,), ("legs.itm",), None, False, "the leg in the money"),
    "extrinsic_bid": Metric("extrinsic_bid", "usd", "M", (LEVEL_LEG,), ("legs.extrinsic_bid", "legs.fresh_bid"),
                            None, False, "the bid less intrinsic value, per share"),
    "days_to_ex_dividend": Metric("days_to_ex_dividend", "days", "E", (LEVEL_LEG,), ("legs.occ", "legs.underlying"),
                                  None, False, "trading days to the ex-dividend date"),
    "dividend_amount": Metric("dividend_amount", "usd", "E", (LEVEL_LEG,), ("legs.occ", "legs.underlying"), None, False,
                              "the coming dividend per share"),
    "extrinsic_minus_dividend": Metric("extrinsic_minus_dividend", "usd", "M", (LEVEL_LEG,),
                                       ("legs.extrinsic_bid", "legs.occ", "legs.underlying"), None, False,
                                       "the extrinsic value (bid) less the coming dividend"),
    "pct_from_strike": Metric("pct_from_strike", "percent", "M", (LEVEL_LEG,), _SPOT + ("legs.strike",), None, False,
                              "the underlying's distance from the strike"),
    "auto_exercise_itm": Metric("auto_exercise_itm", UNIT_BOOL, "M", (LEVEL_LEG,),
                                ("legs.open_quantity", "legs.itm", "legs.dte_trading"), None, False,
                                "a long option in the money on its last trading day"),
    "move_pct_from_prev_eod": Metric("move_pct_from_prev_eod", "percent", "M", (LEVEL_UNDERLYING,),
                                     ("underlyings.mark_mid", "underlyings.prev_eod_mid", "underlyings.move_pct_from_prev_eod",
                                      "underlyings.quote_grade"), None, False,
                                     "the move from the desk's previous end-of-day snapshot"),
    "day_pl": Metric("day_pl", "usd", "M", (LEVEL_PORTFOLIO,),
                     ("portfolio.day_pl", "groups.data_quality", "groups.legs", "legs.day_pl", "legs.status"), None, False,
                     "the book's day P/L"),
    "max_concentration_pct": Metric("max_concentration_pct", "percent", "M", (LEVEL_PORTFOLIO,),
                                    ("portfolio.concentration",), None, False,
                                    "the largest underlying share of the declared equity"),
}
"""The metric catalogue (module notes); the lint reads it at call time."""

_FIELD_TABLES: Dict[str, Tuple[str, ...]] = {"legs": book_module.LEG_KEYS, "groups": book_module.GROUP_KEYS,
                                             "underlyings": book_module.UNDERLYING_KEYS,
                                             "portfolio": book_module.PORTFOLIO_KEYS}


def missing_inputs() -> List[Tuple[str, str]]:
    """``(metric, field)`` for every catalogue field the book schema does not have (empty when the catalogue and the
    book agree)."""
    out: List[Tuple[str, str]] = []
    for name, metric in METRICS.items():
        for dotted in metric.fields:
            table, _, key = dotted.partition(".")
            if key not in _FIELD_TABLES.get(table, ()):
                out.append((name, dotted))
    return out


# --------------------------------------------------------------------------------------------
# The rulebook
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    """One condition of a rule's ``when``: ``value`` is ``None`` for ``is_true`` / ``is_false``."""

    metric: str
    op: str
    value: Optional[Decimal]
    unit: Optional[str]
    dte_unit: Optional[str]


@dataclass(frozen=True)
class Defaults:
    mark_basis: str
    confirmations: int
    confirmation_gap_min: int
    confirmation_window_min: int
    cooldown_min: int
    rearm: str
    hysteresis_pct: Decimal


@dataclass(frozen=True)
class Rule:
    """One rule with every parameter resolved (the rule's override, else ``[defaults]``)."""

    id: str
    title: str
    role: str
    scope: str
    applies_to: Tuple[str, ...]
    input_class: str
    when: Tuple[Condition, ...]
    verdict: str
    plan: str
    origin: str
    source: str
    mark_basis: str
    confirmations: int
    confirmation_gap_min: int
    confirmation_window_min: int
    cooldown_min: int
    rearm: str
    hysteresis_pct: Decimal
    suppress_if_tag: Optional[str]
    owner_must_set: bool

    @property
    def primary(self) -> Condition:
        """The first condition: the rule's metric, its threshold and the hysteresis reference."""
        return self.when[0]

    @property
    def policy(self) -> Dict[str, Any]:
        """The plain policy :func:`aegis_desk.rule_state.step` takes (``rule_state.POLICY_KEYS``)."""
        return {"input_class": self.input_class, "confirmations": self.confirmations,
                "confirmation_gap_min": self.confirmation_gap_min,
                "confirmation_window_min": self.confirmation_window_min, "rearm": self.rearm,
                "hysteresis_pct": self.hysteresis_pct, "op": self.primary.op, "threshold": self.primary.value,
                "cooldown_min": self.cooldown_min}


@dataclass(frozen=True)
class Rulebook:
    """A loaded rulebook: ``sha256`` is the hex digest of the file's bytes (of the text's UTF-8 for parsed text)."""

    path: str
    sha256: str
    schema_version: int
    rulebook_version: str
    title: str
    defaults: Defaults
    rules: Tuple[Rule, ...]

    @property
    def rule_ids(self) -> Tuple[str, ...]:
        return tuple(rule.id for rule in self.rules)

    @property
    def rules_sha(self) -> Dict[str, str]:
        """``{rule_id: rule_sha}`` in the file's order."""
        return {rule.id: rule_sha(rule) for rule in self.rules}

    def rule(self, rule_id: str) -> Rule:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        raise KeyError(rule_id)


def _decimal_number(value: Any) -> Optional[Decimal]:
    """A TOML number (int or the reader's Decimal) as a finite Decimal; ``None`` for anything else (a bool too)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal) and value.is_finite():
        return value
    return None


def _subject_levels(scope: str, applies_to: Sequence[str]) -> Tuple[str, ...]:
    levels: List[str] = []
    for entry in applies_to:
        if scope == SCOPE_UNDERLYING:
            level = LEVEL_UNDERLYING
        elif scope == SCOPE_PORTFOLIO:
            level = LEVEL_PORTFOLIO
        elif entry in LEG_SELECTORS or scope == SCOPE_LEG:
            level = LEVEL_LEG
        else:
            level = LEVEL_GROUP
        if level not in levels:
            levels.append(level)
    return tuple(levels)


class _Parser:
    def __init__(self, path: str) -> None:
        self.path = path
        self.problems: List[str] = []

    def fail(self, message: str) -> None:
        self.problems.append(message)

    def keys(self, table: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
        for key in table:
            if key not in allowed:
                self.fail("%s: unknown key '%s' (allowed: %s)" % (where, key, ", ".join(allowed)))

    def text(self, table: Mapping[str, Any], key: str, where: str, choices: Optional[Sequence[str]] = None) -> Optional[str]:
        value = table.get(key)
        if not isinstance(value, str) or not value.strip():
            self.fail("%s: %s must be a non-empty string" % (where, key))
            return None
        if choices is not None and value not in choices:
            self.fail("%s: %s %r is not one of %s" % (where, key, value, ", ".join(choices)))
            return None
        return value

    def integer(self, table: Mapping[str, Any], key: str, where: str, low: int, high: int) -> Optional[int]:
        value = table.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            self.fail("%s: %s must be an integer (%d..%d), got %r" % (where, key, low, high, value))
            return None
        if not low <= value <= high:
            self.fail("%s: %s %d is outside %d..%d" % (where, key, value, low, high))
            return None
        return value

    def hysteresis(self, table: Mapping[str, Any], where: str) -> Optional[Decimal]:
        value = _decimal_number(table.get("hysteresis_pct"))
        low, high = HYSTERESIS_RANGE
        if value is None:
            self.fail("%s: hysteresis_pct must be a number (%s..%s)" % (where, low, high))
            return None
        if not low <= value <= high:
            self.fail("%s: hysteresis_pct %s is outside %s..%s" % (where, value, low, high))
            return None
        return value

    def parameters(self, table: Mapping[str, Any], where: str, base: Optional[Defaults]) -> Optional[Defaults]:
        """The seven tunables: required in ``[defaults]`` (``base`` None), optional overrides in a rule."""
        values: Dict[str, Any] = {}
        for key in DEFAULTS_KEYS:
            if key not in table:
                if base is None:
                    self.fail("%s: missing required key '%s'" % (where, key))
                    values[key] = None
                else:
                    values[key] = getattr(base, key)
                continue
            if key == "mark_basis":
                values[key] = self.text(table, key, where, MARK_BASES)
            elif key == "rearm":
                values[key] = self.text(table, key, where, REARM_MODES)
            elif key == "confirmations":
                values[key] = self.integer(table, key, where, *CONFIRMATIONS_RANGE)
            elif key == "confirmation_gap_min":
                values[key] = self.integer(table, key, where, *GAP_RANGE)
            elif key == "confirmation_window_min":
                values[key] = self.integer(table, key, where, 1, WINDOW_MAX)
            elif key == "cooldown_min":
                values[key] = self.integer(table, key, where, *COOLDOWN_RANGE)
            else:
                values[key] = self.hysteresis(table, where)
        gap, window = values["confirmation_gap_min"], values["confirmation_window_min"]
        if gap is not None and window is not None and window < gap:
            self.fail("%s: confirmation_window_min %d is shorter than confirmation_gap_min %d" % (where, window, gap))
            return None
        if any(value is None for value in values.values()):
            return None
        return Defaults(**values)

    def condition(self, item: Any, where: str) -> Optional[Condition]:
        if not isinstance(item, dict):
            self.fail("%s: every condition must be an inline table {metric, op, value, unit, dte_unit}" % where)
            return None
        self.keys(item, CONDITION_KEYS, where)
        metric_name = item.get("metric")
        metric = METRICS.get(metric_name) if isinstance(metric_name, str) else None
        if metric is None:
            self.fail("%s: metric %r is not in the catalogue (%s)" % (where, metric_name, ", ".join(METRICS)))
            return None
        where = "%s %s" % (where, metric.name)
        op = item.get("op")
        if op not in OPS:
            self.fail("%s: op %r is not one of %s" % (where, op, " ".join(OPS)))
            return None
        value: Optional[Decimal] = None
        unit = item.get("unit")
        ok = True
        if metric.is_bool:
            if op not in BOOL_OPS:
                self.fail("%s: a boolean metric takes is_true or is_false, not %s" % (where, op))
                ok = False
            if "value" in item:
                self.fail("%s: %s takes no value" % (where, op))
                ok = False
            if "unit" in item:
                self.fail("%s: a boolean metric takes no unit" % where)
                ok = False
        else:
            if op not in COMPARISON_OPS:
                self.fail("%s: a numeric metric takes %s, not %s" % (where, " ".join(COMPARISON_OPS), op))
                ok = False
            value = _decimal_number(item.get("value"))
            if value is None:
                self.fail("%s: value must be a number, got %r" % (where, item.get("value")))
                ok = False
            elif metric.unit == "days" and value != value.to_integral_value():
                self.fail("%s: a days threshold is a whole number of days, got %s" % (where, value))
                ok = False
            if unit is None and metric.unit in REQUIRED_UNITS:
                self.fail("%s: unit is required (%s)" % (where, metric.unit))
                ok = False
            elif unit is not None and unit != metric.unit:
                self.fail("%s: unit %r does not match the metric's unit %r" % (where, unit, metric.unit))
                ok = False
        dte_unit = item.get("dte_unit")
        if metric.name == "dte":
            if dte_unit not in DTE_UNITS:
                self.fail("%s: dte_unit is required (%s), got %r" % (where, " | ".join(DTE_UNITS), dte_unit))
                ok = False
        elif "dte_unit" in item:
            self.fail("%s: dte_unit belongs to dte conditions only" % where)
            ok = False
        if not ok:
            return None
        return Condition(metric.name, op, value, unit, dte_unit if metric.name == "dte" else None)

    def applies_to(self, value: Any, scope: Optional[str], where: str) -> Optional[List[str]]:
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            self.fail("%s: applies_to must be a non-empty list of structure names" % where)
            return None
        names: List[str] = []
        for item in value:
            if item in names:
                self.fail("%s: applies_to names %s twice" % (where, item))
            elif item not in APPLIES_TO_NAMES:
                self.fail("%s: applies_to %r is not a structure (%s) or a pseudo-structure (%s)"
                          % (where, item, ", ".join(RULE_STRUCTURES), ", ".join(PSEUDO_STRUCTURES)))
            else:
                names.append(item)
        if scope == SCOPE_UNDERLYING and names != [ANY_UNDERLYING]:
            self.fail('%s: an underlying rule applies_to ["any_underlying"]' % where)
        elif scope == SCOPE_PORTFOLIO and names != [PORTFOLIO]:
            self.fail('%s: a portfolio rule applies_to ["portfolio"]' % where)
        elif scope in (SCOPE_GROUP, SCOPE_LEG):
            for item in names:
                if item in (ANY_UNDERLYING, PORTFOLIO):
                    self.fail("%s: %s belongs to an %s rule" % (where, item, "underlying" if item == ANY_UNDERLYING
                                                                else "portfolio"))
                if scope == SCOPE_LEG and item == ANY_GROUP:
                    self.fail("%s: a leg rule cannot apply to any_group (name the legs)" % where)
        return names

    def rule(self, table: Any, index: int, defaults: Optional[Defaults], seen: List[str]) -> Optional[Rule]:
        where = "rule[%d]" % index
        if not isinstance(table, dict):
            self.fail("%s: every [[rule]] must be a table" % where)
            return None
        rule_id = table.get("id")
        if isinstance(rule_id, str):
            where = "rule %s" % rule_id
        before = len(self.problems)
        self.keys(table, RULE_KEYS, where)
        for key in RULE_REQUIRED_KEYS:
            if key not in table:
                self.fail("%s: missing required key '%s'" % (where, key))
        if isinstance(rule_id, str) and not RULE_ID_RE.match(rule_id):
            self.fail("%s: id must be %s, got %r" % (where, RULE_ID_WORDS, rule_id))
        elif "id" in table and not isinstance(rule_id, str):
            self.fail("%s: id must be a string" % where)
        if isinstance(rule_id, str):
            if rule_id in seen:
                self.fail("%s: duplicate rule id '%s'" % (where, rule_id))
            seen.append(rule_id)
        title = self.text(table, "title", where) if "title" in table else None
        role = self.text(table, "role", where, ROLES) if "role" in table else None
        scope = self.text(table, "scope", where, SCOPES) if "scope" in table else None
        input_class = self.text(table, "input_class", where, INPUT_CLASSES) if "input_class" in table else None
        verdict = self.text(table, "verdict", where, RULE_VERDICTS) if "verdict" in table else None
        plan = self.text(table, "plan", where, PLANS) if "plan" in table else None
        origin = self.text(table, "origin", where, ORIGINS) if "origin" in table else None
        source = self.text(table, "source", where) if "source" in table else None
        applies = self.applies_to(table.get("applies_to"), scope, where) if "applies_to" in table else None
        conditions: List[Condition] = []
        when = table.get("when")
        if "when" in table:
            if not isinstance(when, list) or not when:
                self.fail("%s: when must be a non-empty list of conditions" % where)
            else:
                for number, item in enumerate(when):
                    parsed = self.condition(item, "%s when[%d]" % (where, number))
                    if parsed is not None:
                        conditions.append(parsed)
        params = self.parameters(table, where, defaults) if defaults is not None else None
        tag = table.get("suppress_if_tag")
        if "suppress_if_tag" in table:
            if tag not in SUPPRESS_TAGS:
                self.fail("%s: suppress_if_tag %r is not one of %s" % (where, tag, ", ".join(SUPPRESS_TAGS)))
            elif scope in SECTION_SCOPES:
                self.fail("%s: suppress_if_tag needs a group or leg rule (tags are on rows)" % where)
        owner_must_set = table.get("owner_must_set", False)
        if not isinstance(owner_must_set, bool):
            self.fail("%s: owner_must_set must be true or false" % where)
        if scope in SECTION_SCOPES and verdict == VERDICT_EXIT_SIGNAL:
            self.fail("%s: an underlying or portfolio rule must be WATCH: it is reported in its own section and never "
                      "sets a group verdict" % where)
        if scope is not None and applies:
            for level in _subject_levels(scope, applies):
                for condition in conditions:
                    metric = METRICS[condition.metric]
                    if level not in metric.levels:
                        self.fail("%s: metric %s is not defined on a %s subject (its levels: %s)"
                                  % (where, metric.name, level, ", ".join(metric.levels)))
        if input_class is not None and conditions:
            floor = max((METRICS[condition.metric].input_class for condition in conditions), key=_CLASS_RANK.__getitem__)
            if _CLASS_RANK[input_class] < _CLASS_RANK[floor]:
                offenders = [c.metric for c in conditions
                             if _CLASS_RANK[METRICS[c.metric].input_class] > _CLASS_RANK[input_class]]
                self.fail("%s: input_class %s is below the floor %s of metric %s" % (where, input_class, floor,
                                                                                    ", ".join(offenders)))
        complete = None not in (title, role, scope, input_class, verdict, plan, origin, source)
        if len(self.problems) > before or params is None or not complete or not applies:
            return None
        if not isinstance(when, list) or len(conditions) != len(when):
            return None
        assert isinstance(rule_id, str) and title and role and scope and input_class and verdict and plan
        assert origin and source
        return Rule(id=rule_id, title=title, role=role, scope=scope, applies_to=tuple(applies), input_class=input_class,
                    when=tuple(conditions), verdict=verdict, plan=plan, origin=origin, source=source,
                    mark_basis=params.mark_basis, confirmations=params.confirmations,
                    confirmation_gap_min=params.confirmation_gap_min,
                    confirmation_window_min=params.confirmation_window_min, cooldown_min=params.cooldown_min,
                    rearm=params.rearm, hysteresis_pct=params.hysteresis_pct,
                    suppress_if_tag=tag if isinstance(tag, str) else None, owner_must_set=bool(owner_must_set))

    def rulebook(self, document: Mapping[str, Any], sha256: str) -> Rulebook:
        self.keys(document, TOP_LEVEL_KEYS, "rulebook")
        schema = document.get("schema_version")
        if isinstance(schema, bool) or schema != SCHEMA_VERSION:
            self.fail("rulebook: schema_version must be %d, got %r" % (SCHEMA_VERSION, schema))
        version = document.get("rulebook_version")
        if not isinstance(version, str) or not VERSION_RE.match(version):
            self.fail("rulebook: rulebook_version must be X.Y.Z (no leading v), got %r" % (version,))
        title = document.get("title")
        if not isinstance(title, str) or not title.strip():
            self.fail("rulebook: title must be a non-empty string")
        defaults: Optional[Defaults] = None
        table = document.get("defaults")
        if not isinstance(table, dict):
            self.fail("rulebook: the [defaults] section is required")
        else:
            self.keys(table, DEFAULTS_KEYS, "[defaults]")
            defaults = self.parameters(table, "[defaults]", None)
        rules: List[Rule] = []
        tables = document.get("rule")
        if not isinstance(tables, list) or not tables:
            self.fail("rulebook: at least one [[rule]] is required")
        else:
            seen: List[str] = []
            for index, item in enumerate(tables):
                parsed = self.rule(item, index, defaults, seen)
                if parsed is not None:
                    rules.append(parsed)
            if defaults is None:
                self.fail("rulebook: the rules cannot be resolved without a valid [defaults]")
        if self.problems:
            raise RulebookError(self.path, self.problems)
        assert defaults is not None and isinstance(version, str) and isinstance(title, str)
        return Rulebook(path=self.path, sha256=sha256, schema_version=SCHEMA_VERSION, rulebook_version=version,
                        title=title, defaults=defaults, rules=tuple(rules))


def parse_rulebook_text(text: str, path: str = "<text>", sha256: Optional[str] = None) -> Rulebook:
    """Parse rulebook TOML text (module notes); ``sha256`` defaults to that of the text's UTF-8 bytes."""
    try:
        document = toml_compat.loads(text)
    except toml_compat.TomlError as exc:
        raise RulebookError(path, ["invalid TOML: %s" % exc]) from None
    digest = sha256 if sha256 is not None else hashlib.sha256(text.encode("utf-8")).hexdigest()
    return _Parser(path).rulebook(document, digest)


def load_rulebook(path: str) -> Rulebook:
    """Read and parse ``rules/exit_rules-X.Y.Z.toml`` (or a candidate) strictly; ``sha256`` is that of the bytes.
    :class:`RulebookError` (``rules_missing`` for an absent file, else ``config_invalid``); other ``OSError`` propagates."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        raise RulebookError(path, ["rulebook not found"], "rules_missing") from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RulebookError(path, ["not valid UTF-8: %s" % exc]) from None
    return parse_rulebook_text(text, path, hashlib.sha256(data).hexdigest())


def _canonical_number(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def rule_canonical(rule: Rule) -> Dict[str, Any]:
    """The canonical form :func:`rule_sha` hashes (module notes, "Identity")."""
    return {
        "id": rule.id,
        "role": rule.role,
        "scope": rule.scope,
        "applies_to": sorted(rule.applies_to),
        "input_class": rule.input_class,
        "mark_basis": rule.mark_basis,
        "when": [{"metric": c.metric, "op": c.op, "value": _canonical_number(c.value), "unit": c.unit,
                  "dte_unit": c.dte_unit} for c in rule.when],
        "verdict": rule.verdict,
        "plan": rule.plan,
        "confirmations": rule.confirmations,
        "confirmation_gap_min": rule.confirmation_gap_min,
        "confirmation_window_min": rule.confirmation_window_min,
        "cooldown_min": rule.cooldown_min,
        "rearm": rule.rearm,
        "hysteresis_pct": _canonical_number(rule.hysteresis_pct),
        "suppress_if_tag": rule.suppress_if_tag,
    }


def rule_sha(rule: Rule) -> str:
    """SHA-256 (hex) of the canonical JSON of the rule's semantics (module notes)."""
    text = json.dumps(rule_canonical(rule), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Input:
    """One value a rule read: ``source`` names the book field (or ``computed`` / ``calendar.toml``), ``as_of`` the quote
    stamp behind it, ``grade`` its quote grade, ``note`` a remark on how it was read."""

    name: str
    value: Any
    source: str
    as_of: Optional[datetime.datetime] = None
    grade: Optional[str] = None
    note: Optional[str] = None


@dataclass(frozen=True)
class ConditionResult:
    """One condition on the reported subject: ``truth`` TRUE / FALSE / UNKNOWN, ``kind`` how the metric was read
    (``value``, ``not_applicable``, ``unavailable``, ``no_history``), ``value`` the metric's value, ``threshold`` the
    rule's ``value``."""

    metric: str
    op: str
    threshold: Optional[Decimal]
    unit: Optional[str]
    dte_unit: Optional[str]
    truth: str
    kind: str
    value: Any
    reason: str
    inputs: Tuple[Input, ...] = ()
    notes: Tuple[str, ...] = ()
    as_of: Optional[datetime.datetime] = None


@dataclass(frozen=True)
class RuleResult:
    """One rule on one group (or section subject). The first nine fields are the brief's; ``metric``, ``value``,
    ``threshold``, ``unit``, ``op`` and ``dte_unit`` are the first condition's (``conditions`` holds every one);
    ``subject`` names the group, the leg (``row n SYMBOL``), the symbol or the portfolio the values come from;
    ``state_status`` / ``transition`` / ``new_signal`` come from the rule state (``new_signal``: this evaluation moved
    the rule into ``fired``); ``observations`` / ``confirmations`` say how far a pending rule is."""

    rule_id: str
    rule_sha: str
    result: str
    metric: str
    value: Any
    threshold: Optional[Decimal]
    unit: Optional[str]
    inputs: Tuple[Input, ...]
    reason: str
    title: str = ""
    role: str = ""
    scope: str = ""
    verdict: str = ""
    plan: str = ""
    op: str = ""
    dte_unit: Optional[str] = None
    input_class: str = ""
    basis: str = ""
    subject: str = ""
    conditions: Tuple[ConditionResult, ...] = ()
    quote_grade: Optional[str] = None
    as_of: Optional[datetime.datetime] = None
    first_observation: bool = False
    state_status: Optional[str] = None
    transition: Optional[str] = None
    new_signal: bool = False
    observations: int = 0
    confirmations: int = 1
    cooldown_min: int = 0
    notes: Tuple[str, ...] = ()
    suppress_tag: Optional[str] = None


@dataclass(frozen=True)
class GroupVerdict:
    """A group's verdict and every rule result by kind (``fired``, ``unavailable``, ``pending``, ``clear``,
    ``suppressed``; ``no_history`` never sets the verdict, decision 11; ``cooldown``, a rule true again before it
    re-arms, counts for the verdict no more than ``pending`` does). ``quarantined`` groups are UNAVAILABLE with
    ``reasons`` and no rule results."""

    group_id: str
    verdict: str
    fired: List[RuleResult]
    unavailable: List[RuleResult]
    pending: List[RuleResult]
    clear: List[RuleResult]
    suppressed: List[RuleResult]
    trade_id: Optional[str] = None
    underlying: Optional[str] = None
    structure: Optional[str] = None
    data_quality: Optional[str] = None
    quarantined: bool = False
    reasons: List[str] = field(default_factory=list)
    no_history: List[RuleResult] = field(default_factory=list)
    cooldown: List[RuleResult] = field(default_factory=list)


@dataclass(frozen=True)
class SectionVerdict:
    """The underlying (``subject`` the symbol) or portfolio (``subject`` ``portfolio``) section's results."""

    scope: str
    subject: str
    fired: List[RuleResult]
    unavailable: List[RuleResult]
    no_history: List[RuleResult]
    pending: List[RuleResult]
    clear: List[RuleResult]
    suppressed: List[RuleResult] = field(default_factory=list)
    cooldown: List[RuleResult] = field(default_factory=list)


@dataclass
class Evaluation:
    """What :func:`evaluate` returns (module notes)."""

    groups: List[GroupVerdict]
    underlyings: List[SectionVerdict]
    portfolio: SectionVerdict
    state_updates: List[Dict[str, Any]]
    evaluated_at: datetime.datetime
    et_date: datetime.date
    single_observation: bool
    next_run_utc: Optional[datetime.datetime]
    history_date: datetime.date
    rulebook_version: str
    rulebook_sha: str
    rulebook: Optional[Rulebook] = None
    warnings: List[str] = field(default_factory=list)

    def group(self, group_id: str) -> Optional[GroupVerdict]:
        return next((verdict for verdict in self.groups if verdict.group_id == group_id), None)

    def verdict_counts(self) -> Dict[str, int]:
        """``{EXIT_SIGNAL, UNAVAILABLE, WATCH, HOLD: n}`` over the groups."""
        counts = {verdict: 0 for verdict in GROUP_VERDICTS}
        for verdict in self.groups:
            counts[verdict.verdict] += 1
        return counts

    def exit_code(self) -> int:
        """10 any EXIT_SIGNAL group, 20 any UNAVAILABLE group, 0 otherwise (the sections never count)."""
        return evaluation_exit_code(self)

    @property
    def sections(self) -> List[SectionVerdict]:
        return list(self.underlyings) + [self.portfolio]

    @property
    def no_history(self) -> List[RuleResult]:
        """Every ``no_history`` result (the underlying and portfolio sections, and any group's)."""
        return ([result for section in self.sections for result in section.no_history]
                + [result for verdict in self.groups for result in verdict.no_history])

    @property
    def no_history_rule_ids(self) -> List[str]:
        """The rules waiting for the previous end-of-day snapshot, once each, in the rulebook's order."""
        found = {result.rule_id for result in self.no_history}
        order = list(self.rulebook.rule_ids) if self.rulebook is not None else sorted(found)
        return [rule_id for rule_id in order if rule_id in found]

    @property
    def pending(self) -> List[RuleResult]:
        return [result for verdict in self.groups for result in verdict.pending] + [
            result for section in self.sections for result in section.pending]


def evaluation_exit_code(evaluation: Evaluation) -> int:
    """10 when any group is EXIT_SIGNAL, 20 when any is UNAVAILABLE, 0 otherwise."""
    verdicts = [verdict.verdict for verdict in evaluation.groups]
    if VERDICT_EXIT_SIGNAL in verdicts:
        return 10
    if VERDICT_UNAVAILABLE in verdicts:
        return 20
    return 0


def group_verdict(fired_verdicts: Iterable[str], any_unavailable: bool) -> str:
    """EXIT_SIGNAL > UNAVAILABLE > WATCH > HOLD."""
    fired = list(fired_verdicts)
    if VERDICT_EXIT_SIGNAL in fired:
        return VERDICT_EXIT_SIGNAL
    if any_unavailable:
        return VERDICT_UNAVAILABLE
    if fired:
        return VERDICT_WATCH
    return VERDICT_HOLD


# --------------------------------------------------------------------------------------------
# Kleene logic
# --------------------------------------------------------------------------------------------


def kleene_and(truths: Iterable[str]) -> str:
    """FALSE if any is FALSE, else UNKNOWN if any is UNKNOWN, else TRUE (TRUE for none)."""
    values = list(truths)
    if FALSE in values:
        return FALSE
    if UNKNOWN in values:
        return UNKNOWN
    return TRUE


def kleene_or(truths: Iterable[str]) -> str:
    """TRUE if any is TRUE, else UNKNOWN if any is UNKNOWN, else FALSE (FALSE for none)."""
    values = list(truths)
    if TRUE in values:
        return TRUE
    if UNKNOWN in values:
        return UNKNOWN
    return FALSE


def compare(value: Any, op: str, threshold: Optional[Decimal]) -> bool:
    """``value op threshold`` for a numeric op, ``value is True`` / ``is False`` for a boolean op."""
    if op == "is_true":
        return value is True
    if op == "is_false":
        return value is False
    if threshold is None:
        raise ValueError("a comparison needs a threshold")
    if op == "<":
        return bool(value < threshold)
    if op == "<=":
        return bool(value <= threshold)
    if op == ">":
        return bool(value > threshold)
    if op == ">=":
        return bool(value >= threshold)
    if op == "==":
        return bool(value == threshold)
    raise ValueError("unknown op %r" % (op,))


def kleene_maximum(values: Sequence[Tuple[str, Optional[Decimal]]], op: str,
                   threshold: Decimal) -> Tuple[str, Optional[Decimal], Optional[str]]:
    """Decision 16: ``max(values) op threshold`` over values some of which are unknown (``None``):
    ``(truth, known maximum, its name)``. ``>=`` / ``>``: TRUE when a known value satisfies it, UNKNOWN when one
    is unknown, else FALSE; ``<=`` / ``<`` the dual (FALSE when a known value already breaks it); ``==`` FALSE when a
    known value exceeds the threshold, else UNKNOWN with an unknown, else the comparison. No values: FALSE."""
    if not values:
        return FALSE, None, None
    known = [(value, name) for name, value in values if value is not None]
    unknown = any(value is None for _, value in values)
    top_value: Optional[Decimal] = None
    top_name: Optional[str] = None
    if known:
        top_value, top_name = max(known, key=lambda pair: pair[0])
    if op in (">=", ">"):
        if top_value is not None and compare(top_value, op, threshold):
            return TRUE, top_value, top_name
        return (UNKNOWN if unknown else FALSE), top_value, top_name
    if op in ("<=", "<"):
        if top_value is not None and not compare(top_value, op, threshold):
            return FALSE, top_value, top_name
        return (UNKNOWN if unknown else TRUE), top_value, top_name
    if op == "==":
        if top_value is not None and top_value > threshold:
            return FALSE, top_value, top_name
        if unknown:
            return UNKNOWN, top_value, top_name
        return (TRUE if top_value == threshold else FALSE), top_value, top_name
    raise ValueError("kleene_maximum takes a comparison op, got %r" % (op,))


# --------------------------------------------------------------------------------------------
# Reading the book (the metric catalogue)
# --------------------------------------------------------------------------------------------


@dataclass
class _Reading:
    kind: str
    value: Any = None
    reason: str = ""
    inputs: List[Input] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    maximum: Optional[List[Tuple[str, Optional[Decimal]]]] = None
    stamps: List[datetime.datetime] = field(default_factory=list)


def _value(value: Any, inputs: Sequence[Input] = (), notes: Sequence[str] = ()) -> _Reading:
    return _Reading(KIND_VALUE, value, "", list(inputs), list(notes))


def _not_applicable(reason: str, inputs: Sequence[Input] = ()) -> _Reading:
    return _Reading(KIND_NOT_APPLICABLE, None, "not applicable: %s" % reason, list(inputs))


def _unavailable(reason: str, inputs: Sequence[Input] = ()) -> _Reading:
    return _Reading(KIND_UNAVAILABLE, None, reason, list(inputs))


def _no_history(reason: str, inputs: Sequence[Input] = ()) -> _Reading:
    return _Reading(KIND_NO_HISTORY, None, reason, list(inputs))


def _does_not_apply(metric: str, what: str) -> _Reading:
    return _unavailable("%s does not apply to %s (a rule the adoption lint refuses)" % (metric, what))


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def _stamped(reading: _Reading, stamps: Iterable[Any]) -> _Reading:
    """``reading`` with the quote stamps it depends on (aware datetimes; anything else is skipped)."""
    reading.stamps.extend(stamp for stamp in stamps if isinstance(stamp, datetime.datetime) and stamp.tzinfo is not None)
    return reading


def refine_at_threshold(exact: Decimal, quantised: Decimal, condition: Condition) -> Decimal:
    """Decision 15 at the threshold: ``quantised`` (the two-decimal value of ``exact``) is the value compared and
    printed, unless it decides ``condition`` the other way from ``exact`` (a half-cent above a stop reads 0.00 % and
    would be "at or below 0") or it reads zero while ``exact`` is not (zero hides the sign a stop or a break-even test
    is about); then ``exact`` is kept to 4, 6, 8 or 10 decimals, the first that decides as it does and is not a false
    zero (else ``exact`` itself). The printed number and the verdict still agree, and the side of the threshold is the
    exact value's."""
    threshold = condition.value
    if threshold is None or condition.op not in COMPARISON_OPS:
        return quantised
    decision = compare(exact, condition.op, threshold)

    def faithful(candidate: Decimal) -> bool:
        return compare(candidate, condition.op, threshold) == decision and (candidate != 0 or exact == 0)

    if faithful(quantised):
        return quantised
    for places in (4, 6, 8, 10):
        refined = exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
        if faithful(refined):
            return refined
    return exact


def _pct(exact: Decimal, condition: Condition) -> Decimal:
    return refine_at_threshold(exact, pnl.quantize_pct(exact), condition)


def _multiple(exact: Decimal, condition: Condition) -> Decimal:
    return refine_at_threshold(exact, _q2(exact), condition)


def _greek(value: Any) -> Optional[Decimal]:
    """Decision 15: a G-class float as ``Decimal(repr(x))`` to two decimals, half-even."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = Decimal(repr(value))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return _q2(number)


def _label(leg: Mapping[str, Any]) -> str:
    return "row %d %s" % (leg["row_number"], leg.get("occ") or leg.get("symbol"))


def _grade(leg: Mapping[str, Any]) -> str:
    return str(leg.get("quote_grade") or "none")


def _src(leg: Mapping[str, Any], key: str) -> str:
    return "legs[row %d].%s" % (leg["row_number"], key)


def _gsrc(group: Mapping[str, Any], key: str) -> str:
    return "groups[%s].%s" % (group["group_id"], key)


@dataclass(frozen=True)
class _Subject:
    level: str
    key: str
    group: Optional[Mapping[str, Any]] = None
    leg: Optional[Mapping[str, Any]] = None
    symbol: Optional[str] = None

    @property
    def label(self) -> str:
        if self.leg is not None:
            return _label(self.leg)
        if self.group is not None:
            return str(self.group["group_id"])
        return self.symbol or PORTFOLIO


class _Context:
    """The book indexed for the metrics, the calendar, the ET date and the rule being read."""

    def __init__(self, document: Mapping[str, Any], calendar: Optional[CalendarFile], today: datetime.date) -> None:
        self.document = document
        self.legs: Dict[int, Mapping[str, Any]] = {leg["row_number"]: leg for leg in document["legs"]}
        self.groups: List[Mapping[str, Any]] = list(document["groups"])
        self.underlyings: Mapping[str, Mapping[str, Any]] = document["underlyings"]
        self.portfolio: Mapping[str, Any] = document["portfolio"]
        self.calendar = calendar
        self.extra = calendar_events.extra_closures(calendar)
        self.today = today
        self.history_date = market_clock.previous_trading_day(today, self.extra)
        self.rule: Optional[Rule] = None

    # -- helpers ---------------------------------------------------------------------------
    def open_legs(self, group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        return [self.legs[row] for row in group["legs"] if row in self.legs and self.legs[row]["status"] == "open"]

    def open_option_legs(self, group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        return [leg for leg in self.open_legs(group) if leg.get("occ")]

    def group_stamps(self, group: Mapping[str, Any], spot: bool = False) -> List[Any]:
        """The quote stamps a group-field reading depends on: every open leg's, and the underlying's when the
        reading uses a spot (Greeks, a strike or stop test)."""
        stamps = [leg.get("as_of") for leg in self.open_legs(group)]
        if spot:
            entry = self.underlyings.get(str(group.get("underlying")))
            stamps.append(entry.get("as_of") if isinstance(entry, Mapping) else None)
        return stamps

    def book_stamps(self) -> List[Any]:
        """Every open leg's and every open group's underlying's stamp (the portfolio readings)."""
        stamps: List[Any] = []
        for group in self.groups:
            legs = self.open_legs(group)
            if legs:
                stamps.extend(self.group_stamps(group, spot=True))
        return stamps

    def take_profit_liq(self) -> bool:
        assert self.rule is not None
        return self.rule.role == ROLE_TAKE_PROFIT and self.rule.mark_basis == BASIS_LIQ

    def loss(self) -> bool:
        assert self.rule is not None
        return self.rule.role == ROLE_LOSS

    def basis(self) -> str:
        assert self.rule is not None
        return self.rule.mark_basis

    def spot(self, symbol: str) -> Tuple[Optional[Decimal], Optional[_Reading], List[Input]]:
        entry = self.underlyings.get(symbol)
        mid = entry.get("mark_mid") if isinstance(entry, Mapping) else None
        if mid is None:
            grade = entry.get("quote_grade") if isinstance(entry, Mapping) else None
            return None, _unavailable("underlying %s: no usable mark (quote grade %s)" % (symbol, grade or "none")), []
        assert isinstance(entry, Mapping)
        return mid, None, [Input("mark_mid", mid, "underlyings[%s].mark_mid" % symbol, entry.get("as_of"),
                                 entry.get("quote_grade"))]

    def leg_gate(self, leg: Mapping[str, Any]) -> Optional[_Reading]:
        """``None`` when the leg has a two-sided fresh mark (``mark_source`` ``mid``), else the unavailable reading."""
        if leg.get("mark_source") == book_module.MARK_SOURCE_MID:
            return None
        if self.loss():
            return _unavailable("%s: %s" % (_label(leg), LOSS_GATE_REASON % _grade(leg)))
        return _unavailable("%s: no usable mark (quote grade %s, %s)" % (_label(leg), _grade(leg),
                                                                          leg.get("quote_status") or "no quote"))

    def group_gate(self, group: Mapping[str, Any]) -> Optional[_Reading]:
        if group.get("data_quality") == book_module.DATA_QUALITY_OK:
            return None
        for leg in self.open_legs(group):
            gated = self.leg_gate(leg)
            if gated is not None:
                return gated
        return _unavailable("group %s: data unavailable (%s)" % (group["group_id"],
                                                                 "; ".join(group.get("warnings") or []) or "unavailable"))

    def fresh_side(self, leg: Mapping[str, Any]) -> Tuple[Optional[Decimal], Optional[_Reading], Optional[Input]]:
        """The leg's fresh conservative side for a take-profit read (decision 10)."""
        short = leg["open_quantity"] < 0
        key = "fresh_ask" if short else "fresh_bid"
        side = leg.get(key)
        grade = _grade(leg)
        if side is None:
            return None, _unavailable("%s: quote grade %s (%s): no fresh %s" % (
                _label(leg), grade, leg.get("quote_status") or "no quote", "ask" if short else "bid")), None
        if short and side == 0:
            return None, _unavailable("%s: %s" % (_label(leg), NO_OFFER_REASON % grade)), None
        note = CONSERVATIVE_NOTE % grade if leg.get("mark_source") != book_module.MARK_SOURCE_MID else None
        return side, None, Input(key, side, _src(leg, key), leg.get("as_of"), leg.get("quote_grade"), note)

    def fresh_cost_to_close(self, legs: Sequence[Mapping[str, Any]]) -> Tuple[Optional[Decimal], Optional[_Reading],
                                                                              List[Input], List[str]]:
        """``-sum(open_quantity x multiplier x side)`` at the fresh conservative sides, to the cent."""
        total = _ZERO
        inputs: List[Input] = []
        notes: List[str] = []
        for leg in legs:
            side, problem, item = self.fresh_side(leg)
            if problem is not None:
                return None, problem, inputs, notes
            assert side is not None and item is not None
            inputs.append(item)
            if item.note is not None and item.note not in notes:
                notes.append(item.note)
            total -= leg["open_quantity"] * leg["multiplier"] * side
        return payoff.quantize_money(total), None, inputs, notes

    def conservative_unrealized(self, group: Mapping[str, Any]) -> Tuple[Optional[Decimal], Optional[_Reading],
                                                                         List[Input], List[str]]:
        ctc, problem, inputs, notes = self.fresh_cost_to_close(self.open_legs(group))
        if problem is not None:
            return None, problem, inputs, notes
        assert ctc is not None
        unrealized = payoff.quantize_money(-ctc - group["net_cost"])
        return unrealized, None, [Input("unrealized_pl", unrealized, "computed")] + inputs, notes

    def gated_unrealized(self, group: Mapping[str, Any]) -> Tuple[Optional[Decimal], Optional[_Reading], List[Input]]:
        gated = self.group_gate(group)
        if gated is not None:
            return None, gated, []
        key = "unrealized_pl_%s" % self.basis()
        value = group.get(key)
        if value is None:
            return None, _unavailable("group %s: %s unavailable" % (group["group_id"], key)), []
        oldest = min((stamp for stamp in self.group_stamps(group) if isinstance(stamp, datetime.datetime)), default=None)
        return value, None, [Input(key, value, _gsrc(group, key), oldest)]

    def unrealized(self, group: Mapping[str, Any]) -> Tuple[Optional[Decimal], Optional[_Reading], List[Input], List[str]]:
        if self.take_profit_liq():
            return self.conservative_unrealized(group)
        value, problem, inputs = self.gated_unrealized(group)
        return value, problem, inputs, []

    # -- group metrics ---------------------------------------------------------------------
    def credit_problem(self, metric: str, group: Mapping[str, Any]) -> Optional[_Reading]:
        """``None`` for a credit group of a structure ``metric`` is defined on, else the does-not-apply reading (a
        custom group holding stock with a net credit is no credit: its stock proceeds are not premium)."""
        net = group.get("net_cost")
        basis = group.get("basis_kind")
        if group["structure"] not in (METRICS[metric].structures or ()) or net is None or net >= 0:
            return _does_not_apply(metric, "a %s group" % group["structure"])
        if basis != grouping.BASIS_CREDIT:
            return _does_not_apply(metric, "a %s group with a %s basis (not a credit)" % (group["structure"], basis))
        return None

    def m_pct_captured(self, subject: _Subject, condition: Condition) -> _Reading:
        if subject.leg is not None:
            return self.leg_pct_captured(subject.leg, condition)
        group = subject.group
        assert group is not None
        problem = self.credit_problem("pct_captured", group)
        if problem is not None:
            return problem
        credit = -group["net_cost"]
        credit_input = Input("credit", credit, _gsrc(group, "net_cost"))
        if self.take_profit_liq():
            ctc, problem, inputs, notes = self.fresh_cost_to_close(self.open_legs(group))
            if problem is not None:
                return problem
            assert ctc is not None
            value = _pct((credit - ctc) / credit * _HUNDRED, condition)
            return _value(value, [credit_input, Input("cost_to_close", ctc, "computed")] + inputs, notes)
        gated = self.group_gate(group)
        if gated is not None:
            return gated
        key = "pct_captured_%s" % self.basis()
        value = group.get(key)
        if value is None:
            return _unavailable("group %s: %s unavailable" % (group["group_id"], key))
        return _stamped(_value(value, [credit_input, Input(key, value, _gsrc(group, key))]), self.group_stamps(group))

    def leg_pct_captured(self, leg: Mapping[str, Any], condition: Condition) -> _Reading:
        if leg["open_quantity"] >= 0:
            return _does_not_apply("pct_captured", "a long leg (%s)" % _label(leg))
        entry = leg.get("entry_price_per_share")
        if entry is None or entry <= 0:
            return _unavailable("%s: no entry price per share" % _label(leg))
        entry_input = Input("entry_price_per_share", entry, _src(leg, "entry_price_per_share"))
        notes: List[str] = []
        if self.take_profit_liq():
            side, problem, side_input = self.fresh_side(leg)
            if problem is not None:
                return problem
            assert side is not None and side_input is not None
            if side_input.note is not None:
                notes.append(side_input.note)
        else:
            gated = self.leg_gate(leg)
            if gated is not None:
                return gated
            key = "mark_liq" if self.basis() == BASIS_LIQ else "mark_mid"
            side = leg.get(key)
            if side is None:
                return _unavailable("%s: no %s" % (_label(leg), key))
            side_input = Input(key, side, _src(leg, key), leg.get("as_of"), leg.get("quote_grade"))
        value = _pct((entry - side) / entry * _HUNDRED, condition)
        return _value(value, [entry_input, side_input], notes)

    def m_pct_of_max_profit(self, subject: _Subject, condition: Condition) -> _Reading:
        group = subject.group
        assert group is not None
        structures = METRICS["pct_of_max_profit"].structures or ()
        if group["structure"] not in structures or group.get("basis_kind") == grouping.BASIS_CREDIT:
            return _does_not_apply("pct_of_max_profit", "a %s group" % group["structure"])
        max_profit = group.get("max_profit")
        if max_profit is None or max_profit <= 0:
            return _unavailable("group %s: the max profit is unbounded or unknown" % group["group_id"])
        profit_input = Input("max_profit", max_profit, _gsrc(group, "max_profit"))
        if self.take_profit_liq():
            unrealized, problem, inputs, notes = self.conservative_unrealized(group)
            if problem is not None:
                return problem
            assert unrealized is not None
            return _value(_pct(unrealized / max_profit * _HUNDRED, condition), [profit_input] + inputs, notes)
        gated = self.group_gate(group)
        if gated is not None:
            return gated
        key = "pct_captured_%s" % self.basis()
        value = group.get(key)
        if value is None:
            return _unavailable("group %s: %s unavailable" % (group["group_id"], key))
        return _stamped(_value(value, [profit_input, Input(key, value, _gsrc(group, key))]), self.group_stamps(group))

    def m_pct_of_debit(self, subject: _Subject, condition: Condition) -> _Reading:
        group = subject.group
        assert group is not None
        structures = METRICS["pct_of_debit"].structures or ()
        net = group.get("net_cost")
        if group["structure"] not in structures or net is None or net <= 0:
            return _does_not_apply("pct_of_debit", "a %s group" % group["structure"])
        unrealized, problem, inputs, notes = self.unrealized(group)
        if problem is not None:
            return problem
        assert unrealized is not None
        return _value(_pct(unrealized / net * _HUNDRED, condition), [Input("debit", net, _gsrc(group, "net_cost"))] + inputs,
                      notes)

    def m_unrealized_pl(self, subject: _Subject, condition: Condition) -> _Reading:
        group = subject.group
        assert group is not None
        unrealized, problem, inputs, notes = self.unrealized(group)
        if problem is not None:
            return problem
        return _value(unrealized, inputs, notes)

    def m_loss_multiple_of_credit(self, subject: _Subject, condition: Condition) -> _Reading:
        group = subject.group
        assert group is not None
        problem = self.credit_problem("loss_multiple_of_credit", group)
        if problem is not None:
            return problem
        credit = -group["net_cost"]
        unrealized, problem, inputs = self.gated_unrealized(group)
        if problem is not None:
            return problem
        assert unrealized is not None
        loss = _multiple(-unrealized / credit, condition)
        return _value(loss, [Input("credit", credit, _gsrc(group, "net_cost"))] + inputs
                      + [Input("cost_to_close_multiple", loss + 1, "computed")])

    def m_pct_of_max_loss(self, subject: _Subject, condition: Condition) -> _Reading:
        group = subject.group
        assert group is not None
        if group["structure"] not in (METRICS["pct_of_max_loss"].structures or ()):
            return _does_not_apply("pct_of_max_loss", "a %s group" % group["structure"])
        max_loss = group.get("max_loss")
        if max_loss is None or max_loss == 0:
            return _unavailable("group %s: the max loss is unbounded or unknown" % group["group_id"])
        unrealized, problem, inputs = self.gated_unrealized(group)
        if problem is not None:
            return problem
        assert unrealized is not None
        return _value(_pct(-unrealized / max_loss * _HUNDRED, condition),
                      [Input("max_loss", max_loss, _gsrc(group, "max_loss"))] + inputs)

    def m_dte(self, subject: _Subject, condition: Condition) -> _Reading:
        key = "dte_%s" % condition.dte_unit
        if subject.leg is not None:
            legs = [subject.leg]
        else:
            assert subject.group is not None
            legs = self.open_option_legs(subject.group)
        if not legs:
            return _not_applicable("no open option leg")
        values: List[int] = []
        inputs: List[Input] = []
        for leg in legs:
            value = leg.get(key)
            if value is None:
                return _unavailable("%s: %s unknown" % (_label(leg), key))
            values.append(value)
            inputs.append(Input(key, value, _src(leg, key)))
        return _value(min(values), inputs)

    def m_between_strikes(self, subject: _Subject, condition: Condition) -> _Reading:
        group = subject.group
        assert group is not None
        if group["structure"] not in grouping.VERTICAL_STRUCTURES:
            return _does_not_apply("between_strikes", "a %s group" % group["structure"])
        strikes = sorted({expiry.plain_decimal(leg["strike"]) for leg in self.open_option_legs(group)
                          if leg.get("strike") is not None})
        if len(strikes) != 2:
            return _not_applicable("the vertical has no two open strikes")
        spot, problem, inputs = self.spot(str(group["underlying"]))
        if problem is not None:
            return problem
        assert spot is not None
        low, high = strikes
        return _value(bool(low < spot < high), inputs + [Input("strike_low", low, "legs.strike"),
                                                         Input("strike_high", high, "legs.strike")])

    def m_net_delta(self, subject: _Subject, condition: Condition) -> _Reading:
        group = subject.group
        assert group is not None
        gated = self.group_gate(group)
        if gated is not None:
            return gated
        greeks = group.get("net_greeks")
        delta = _greek(greeks.get("delta")) if isinstance(greeks, Mapping) else None
        if delta is None:
            for leg in self.open_option_legs(group):
                if leg.get("iv_status") != book_module.IV_STATUS_OK:
                    return _unavailable("%s: no Greeks (iv_status %s)" % (_label(leg), leg.get("iv_status")))
            return _unavailable("group %s: net Greeks unavailable" % group["group_id"])
        return _stamped(_value(delta, [Input("net_delta", delta, _gsrc(group, "net_greeks"))]),
                        self.group_stamps(group, spot=True))

    def strike_test(self, subject: _Subject, metric: str, right: str, short: bool) -> _Reading:
        group = subject.group
        assert group is not None
        if group["structure"] not in (METRICS[metric].structures or ()):
            return _does_not_apply(metric, "a %s group" % group["structure"])
        legs = [leg for leg in self.open_option_legs(group)
                if leg.get("right") == right and (leg["open_quantity"] < 0) == short]
        if not legs:
            return _not_applicable("no open %s %s" % ("short" if short else "long", right.lower()))
        spot, problem, inputs = self.spot(str(group["underlying"]))
        if problem is not None:
            return problem
        assert spot is not None
        outcome = False
        for leg in legs:
            strike = expiry.plain_decimal(leg["strike"])
            inputs.append(Input("strike", strike, _src(leg, "strike")))
            if right == occ.CALL and spot >= strike + book_module.ONE_CENT:
                outcome = True
            if right == occ.PUT and spot <= strike - book_module.ONE_CENT:
                outcome = True
        return _value(outcome, inputs)

    def m_stock_above_short_call(self, subject: _Subject, condition: Condition) -> _Reading:
        return self.strike_test(subject, "stock_above_short_call", occ.CALL, True)

    def m_stock_below_long_put(self, subject: _Subject, condition: Condition) -> _Reading:
        return self.strike_test(subject, "stock_below_long_put", occ.PUT, False)

    def m_spot_vs_stop_pct(self, subject: _Subject, condition: Condition) -> _Reading:
        group = subject.group
        assert group is not None
        stop = group.get("underlying_stop")
        if stop is None:
            return _not_applicable("no underlying_stop on group %s" % group["group_id"])
        stop_input = Input("underlying_stop", stop, _gsrc(group, "underlying_stop"))
        if stop <= 0:
            return _unavailable("group %s: the stop %s is not positive" % (group["group_id"], stop))
        spot, problem, inputs = self.spot(str(group["underlying"]))
        if problem is not None:
            return problem
        assert spot is not None
        return _value(_pct((spot - stop) / stop * _HUNDRED, condition), inputs + [stop_input])

    def m_days_to_earnings(self, subject: _Subject, condition: Condition) -> _Reading:
        if subject.leg is not None:
            symbol = str(subject.leg["underlying"])
        elif subject.group is not None:
            symbol = str(subject.group["underlying"])
        else:
            symbol = str(subject.symbol)
        unknown = "earnings unknown: %s (an unknown date is never zero)"
        if self.calendar is None:
            return _unavailable(unknown % "no research/calendar.toml")
        events = self.calendar.events_for(symbol)
        if not events.listed:
            return _unavailable(unknown % ("%s is not in calendar.toml" % symbol))
        entry = events.earnings
        if entry is None:
            return _unavailable(unknown % ("no earnings entry for %s" % symbol))
        if entry.status == calendar_events.STATUS_NONE:
            return _not_applicable("%s has no earnings (a confirmed none)" % symbol)
        if entry.status == calendar_events.STATUS_UNKNOWN or entry.date is None:
            return _unavailable(unknown % ('%s earnings status "%s"' % (symbol, entry.status)))
        date_input = Input("earnings_date", entry.date, "calendar.toml symbols.%s.earnings" % symbol)
        if entry.date < self.today:
            return _not_applicable("the %s earnings %s have passed" % (symbol, entry.date.isoformat()), [date_input])
        days = market_clock.trading_days_between(self.today, entry.date, self.extra)
        notes = [ESTIMATED_EARNINGS_NOTE] if entry.status == calendar_events.STATUS_ESTIMATED else []
        return _value(days, [date_input], notes)

    # -- leg metrics -----------------------------------------------------------------------
    def m_abs_delta(self, subject: _Subject, condition: Condition) -> _Reading:
        leg = subject.leg
        assert leg is not None
        greeks = leg.get("greeks")
        if leg.get("iv_status") != book_module.IV_STATUS_OK or not isinstance(greeks, Mapping):
            return _unavailable("%s: no Greeks (iv_status %s)" % (_label(leg), leg.get("iv_status")))
        delta = greeks.get("delta")
        value = _greek(abs(delta)) if isinstance(delta, (int, float)) and not isinstance(delta, bool) else None
        if value is None:
            return _unavailable("%s: no delta" % _label(leg))
        return _value(value, [Input("delta", delta, _src(leg, "greeks.delta"), leg.get("as_of"), leg.get("quote_grade"))])

    def m_itm(self, subject: _Subject, condition: Condition) -> _Reading:
        leg = subject.leg
        assert leg is not None
        _, problem, spot_inputs = self.spot(str(leg["underlying"]))
        value = leg.get("itm")
        if value is None:
            return problem or _unavailable("%s: in the money unknown" % _label(leg))
        return _value(bool(value), [Input("itm", value, _src(leg, "itm"))] + spot_inputs)

    def extrinsic(self, leg: Mapping[str, Any]) -> _Reading:
        value = leg.get("extrinsic_bid")
        if value is not None:
            return _value(value, [Input("extrinsic_bid", value, _src(leg, "extrinsic_bid"), leg.get("as_of"),
                                        leg.get("quote_grade"))])
        if leg.get("fresh_bid") is None:
            return _unavailable("%s: quote grade %s (%s): no fresh bid" % (_label(leg), _grade(leg),
                                                                           leg.get("quote_status") or "no quote"))
        _, problem, _ = self.spot(str(leg["underlying"]))
        if problem is not None:
            return problem
        return _unavailable("%s: spot unaligned or undated (the option and the underlying stamps are more than %d s "
                            "apart)" % (_label(leg), book_module.SPOT_ALIGNMENT_SECONDS))

    def m_extrinsic_bid(self, subject: _Subject, condition: Condition) -> _Reading:
        assert subject.leg is not None
        return self.extrinsic(subject.leg)

    def m_pct_from_strike(self, subject: _Subject, condition: Condition) -> _Reading:
        leg = subject.leg
        assert leg is not None
        spot, problem, inputs = self.spot(str(leg["underlying"]))
        if problem is not None:
            return problem
        assert spot is not None
        strike = expiry.plain_decimal(leg["strike"])
        return _value(expiry.distance_pct(spot, strike), inputs + [Input("strike", strike, _src(leg, "strike"))])

    def m_auto_exercise_itm(self, subject: _Subject, condition: Condition) -> _Reading:
        leg = subject.leg
        assert leg is not None
        quantity = Input("open_quantity", leg["open_quantity"], _src(leg, "open_quantity"))
        if leg["open_quantity"] <= 0:
            return _value(False, [quantity])
        dte = leg.get("dte_trading")
        if dte is None:
            return _unavailable("%s: dte_trading unknown" % _label(leg))
        days = Input("dte_trading", dte, _src(leg, "dte_trading"))
        if dte != 0:
            return _value(False, [quantity, days])
        itm = self.m_itm(subject, condition)
        if itm.kind != KIND_VALUE:
            return itm
        return _value(bool(itm.value), [quantity, days] + itm.inputs)

    def dividend(self, leg: Mapping[str, Any]) -> Tuple[expiry.DividendWindow, List[Input]]:
        parsed = occ.parse(str(leg["occ"]))
        am_settled = (calendar_events.is_am_settled(self.calendar, parsed.root)
                      or calendar_events.is_am_settled(self.calendar, parsed.base_root))
        last_day = market_clock.last_trading_day(parsed.expiration, am_settled, self.extra)
        window = expiry.dividend_window(str(leg["underlying"]), last_day, self.calendar, self.today, self.extra)
        inputs: List[Input] = []
        if window.ex_date is not None:
            inputs.append(Input("ex_dividend_date", window.ex_date, "calendar.toml symbols.%s.ex_dividend" % leg["underlying"]))
        return window, inputs

    def dividend_reading(self, leg: Mapping[str, Any]) -> Tuple[Optional[expiry.DividendWindow], Optional[_Reading],
                                                                List[Input]]:
        window, inputs = self.dividend(leg)
        if not window.known:
            return None, _unavailable(window.reason, inputs), inputs
        if not window.applicable:
            return None, _not_applicable(window.reason, inputs), inputs
        return window, None, inputs

    def m_days_to_ex_dividend(self, subject: _Subject, condition: Condition) -> _Reading:
        assert subject.leg is not None
        window, problem, inputs = self.dividend_reading(subject.leg)
        if problem is not None:
            return problem
        assert window is not None
        return _value(window.trading_days_to_ex, inputs)

    def m_dividend_amount(self, subject: _Subject, condition: Condition) -> _Reading:
        assert subject.leg is not None
        window, problem, inputs = self.dividend_reading(subject.leg)
        if problem is not None:
            return problem
        assert window is not None
        return _value(window.amount, inputs + [Input("dividend_amount", window.amount, "calendar.toml")])

    def m_extrinsic_minus_dividend(self, subject: _Subject, condition: Condition) -> _Reading:
        leg = subject.leg
        assert leg is not None
        window, problem, inputs = self.dividend_reading(leg)
        if problem is not None:
            return problem
        assert window is not None and window.amount is not None
        extrinsic = self.extrinsic(leg)
        if extrinsic.kind != KIND_VALUE:
            return extrinsic
        return _value(extrinsic.value - window.amount,
                      extrinsic.inputs + inputs + [Input("dividend_amount", window.amount, "calendar.toml")])

    # -- underlying and portfolio metrics --------------------------------------------------
    def m_move_pct_from_prev_eod(self, subject: _Subject, condition: Condition) -> _Reading:
        symbol = str(subject.symbol)
        entry = self.underlyings.get(symbol) or {}
        if entry.get("mark_mid") is None:
            return _unavailable("underlying %s: no usable mark (quote grade %s)" % (symbol, entry.get("quote_grade") or "none"))
        inputs = [Input("mark_mid", entry["mark_mid"], "underlyings[%s].mark_mid" % symbol, entry.get("as_of"),
                        entry.get("quote_grade"))]
        if entry.get("prev_eod_mid") is None:
            return _no_history("underlying %s: no previous end-of-day mid (snapshot %s)"
                               % (symbol, self.history_date.isoformat()), inputs)
        move = entry.get("move_pct_from_prev_eod")
        if move is None:
            return _unavailable("underlying %s: move unavailable" % symbol, inputs)
        return _value(move, inputs + [Input("prev_eod_mid", entry["prev_eod_mid"], "underlyings[%s].prev_eod_mid" % symbol)])

    def m_day_pl(self, subject: _Subject, condition: Condition) -> _Reading:
        open_groups = [group for group in self.groups if self.open_legs(group)]
        for group in open_groups:
            if group.get("data_quality") != book_module.DATA_QUALITY_OK:
                gate = self.group_gate(group)
                return _unavailable("day P/L unavailable: %s" % (gate.reason if gate is not None else group["group_id"]))
        quarantined = self.portfolio.get("quarantined_groups") or []
        if quarantined:
            return _unavailable("day P/L unavailable: quarantined group %s" % quarantined[0].get("group_id"))
        missing = "the previous end-of-day snapshot (none usable for %s)" % self.history_date.isoformat()
        value = self.portfolio.get("day_pl")
        if value is None:
            return _no_history("day P/L needs %s" % missing)
        for group in open_groups:
            for leg in self.open_legs(group):
                if leg.get("day_pl") is None:
                    return _no_history("day P/L of %s needs %s: %s has none" % (group["group_id"], missing, _label(leg)))
        return _stamped(_value(value, [Input("day_pl", value, "portfolio.day_pl")]), self.book_stamps())

    def m_max_concentration_pct(self, subject: _Subject, condition: Condition) -> _Reading:
        concentration = self.portfolio.get("concentration") or {}
        by_underlying = concentration.get("by_underlying") or {}
        values = [(str(symbol), by_underlying[symbol]) for symbol in sorted(by_underlying)]
        inputs = [Input("concentration_pct", value, "portfolio.concentration.by_underlying.%s" % symbol)
                  for symbol, value in values]
        return _stamped(_Reading(KIND_VALUE, None, "", inputs, [], values), self.book_stamps())


_METRIC_READERS: Dict[str, Callable[[_Context, _Subject, Condition], _Reading]] = {
    name: getattr(_Context, "m_%s" % name) for name in METRICS
}


# --------------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------------


def _oldest_stamp(reading: _Reading) -> Optional[datetime.datetime]:
    stamps = list(reading.stamps) + [item.as_of for item in reading.inputs if isinstance(item.as_of, datetime.datetime)]
    return min(stamps) if stamps else None


def _condition_result(reading: _Reading, condition: Condition) -> ConditionResult:
    metric = METRICS[condition.metric]
    unit = condition.unit or (None if metric.is_bool else metric.unit)
    as_of = _oldest_stamp(reading)
    if reading.maximum is not None:
        assert condition.value is not None
        truth, top, name = kleene_maximum(reading.maximum, condition.op, condition.value)
        unknown = [symbol for symbol, value in reading.maximum if value is None]
        if not reading.maximum:
            kind, reason = KIND_NOT_APPLICABLE, "not applicable: nothing open"
        elif truth == UNKNOWN:
            kind, reason = KIND_UNAVAILABLE, "%s unknown for %s (an unmarked open leg)" % (metric.name, ", ".join(unknown))
        else:
            kind, reason = KIND_VALUE, ""
        notes = tuple(reading.notes) + (("maximum: %s" % name,) if name is not None else ())
        return ConditionResult(metric.name, condition.op, condition.value, unit, condition.dte_unit, truth, kind, top,
                               reason, tuple(reading.inputs), notes, as_of)
    if reading.kind == KIND_VALUE:
        truth = TRUE if compare(reading.value, condition.op, condition.value) else FALSE
    elif reading.kind == KIND_NOT_APPLICABLE:
        truth = FALSE
    else:
        truth = UNKNOWN
    return ConditionResult(metric.name, condition.op, condition.value, unit, condition.dte_unit, truth, reading.kind,
                           reading.value, reading.reason, tuple(reading.inputs), tuple(reading.notes), as_of)


@dataclass(frozen=True)
class _Outcome:
    subject: _Subject
    truth: str
    conditions: Tuple[ConditionResult, ...]

    @property
    def not_applicable(self) -> bool:
        return self.truth == FALSE and any(item.kind == KIND_NOT_APPLICABLE for item in self.conditions)

    @property
    def cause(self) -> str:
        unknown = [item for item in self.conditions if item.truth == UNKNOWN]
        return KIND_UNAVAILABLE if any(item.kind == KIND_UNAVAILABLE for item in unknown) else KIND_NO_HISTORY

    @property
    def reason(self) -> str:
        if self.truth == UNKNOWN:
            unknown = [item for item in self.conditions if item.truth == UNKNOWN]
            first = next((item for item in unknown if item.kind == KIND_UNAVAILABLE), unknown[0])
            return first.reason
        if self.not_applicable:
            return next(item.reason for item in self.conditions if item.kind == KIND_NOT_APPLICABLE)
        return ""


def _evaluate_subject(context: _Context, rule: Rule, subject: _Subject) -> _Outcome:
    context.rule = rule
    conditions = tuple(_condition_result(_METRIC_READERS[condition.metric](context, subject, condition), condition)
                       for condition in rule.when)
    return _Outcome(subject, kleene_and(item.truth for item in conditions), conditions)


def _representative(outcomes: Sequence[_Outcome], truth: str) -> _Outcome:
    if truth == TRUE:
        return next(outcome for outcome in outcomes if outcome.truth == TRUE)
    if truth == UNKNOWN:
        unknown = [outcome for outcome in outcomes if outcome.truth == UNKNOWN]
        return next((outcome for outcome in unknown if outcome.cause == KIND_UNAVAILABLE), unknown[0])
    return next((outcome for outcome in outcomes if not outcome.not_applicable), outcomes[0])


def _worst_grade(grades: Iterable[Optional[str]]) -> Optional[str]:
    known = [grade for grade in grades if grade in _GRADE_ORDER]
    return max(known, key=_GRADE_ORDER.index) if known else None


def _subjects(context: _Context, rule: Rule, group: Mapping[str, Any]) -> List[_Subject]:
    """The subjects ``rule`` reaches in ``group`` (module notes, "Subjects"), de-duplicated."""
    structure = group["structure"]
    options = context.open_option_legs(group)
    short_options = [leg for leg in options if leg["open_quantity"] < 0]
    short_calls = [leg for leg in short_options if leg.get("right") == occ.CALL]
    long_puts = [leg for leg in options if leg["open_quantity"] > 0 and leg.get("right") == occ.PUT]
    found: List[_Subject] = []

    def add_group() -> None:
        found.append(_Subject(LEVEL_GROUP, "group", group=group))

    def add_legs(legs: Iterable[Mapping[str, Any]]) -> None:
        for leg in legs:
            found.append(_Subject(LEVEL_LEG, "row %d" % leg["row_number"], group=group, leg=leg))

    for entry in rule.applies_to:
        if entry in RULE_STRUCTURES:
            if entry != structure:
                continue
            if rule.scope == SCOPE_LEG:
                add_legs(short_options)
            else:
                add_group()
        elif entry == ANY_GROUP:
            add_group()
        elif entry == SHORT_CALL_LEG and structure in COVERED_STRUCTURES:
            add_legs(short_calls)
        elif entry == LONG_PUT_LEG and structure in HEDGED_STRUCTURES:
            add_legs(long_puts)
        elif entry == ANY_SHORT_CALL_LEG and structure not in (grouping.STRUCTURE_CUSTOM, grouping.STRUCTURE_INVALID):
            add_legs(short_calls)
        elif entry == ANY_OPTION_LEG:
            add_legs(options)
    unique: List[_Subject] = []
    for subject in found:
        if all(subject.key != other.key for other in unique):
            unique.append(subject)
    return unique


def _trade_id_date(trade_id: Any) -> Optional[datetime.date]:
    match = TRADE_ID_DATE_RE.match(trade_id) if isinstance(trade_id, str) else None
    if match is None:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


class _Rows:
    """The rows' tags and opened dates per group (module notes, "Rows")."""

    def __init__(self, rows: Optional[Iterable[Any]]) -> None:
        self.known = rows is not None
        self.tags: Dict[str, List[Tuple[int, FrozenSet[str]]]] = {}
        self.opened: Dict[str, List[datetime.date]] = {}
        for row in rows or ():
            group_id = str(row.group_id)
            self.tags.setdefault(group_id, []).append((int(row.row_number), frozenset(getattr(row, "tags", ()) or ())))
            opened = getattr(row, "opened", None)
            if isinstance(opened, datetime.date):
                self.opened.setdefault(group_id, []).append(opened)

    def tag_row(self, group_id: str, tag: str) -> Optional[int]:
        for row_number, tags in self.tags.get(group_id, []):
            if tag in tags or any(item.startswith(tag + ":") for item in tags):
                return row_number
        return None

    def opened_date(self, group: Mapping[str, Any]) -> Optional[datetime.date]:
        dates = self.opened.get(str(group["group_id"]))
        if dates:
            return min(dates)
        return _trade_id_date(group.get("trade_id"))


class _Evaluator:
    def __init__(self, rulebook: Rulebook, document: Mapping[str, Any], *, now: datetime.datetime, session: SessionInfo,
                 calendar: Optional[CalendarFile], state: Mapping[str, Any], next_run_utc: Optional[datetime.datetime],
                 rows: Optional[Iterable[Any]]) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be an aware datetime")
        self.rulebook = rulebook
        self.now = now
        self.session = session
        self.today = session.et_date
        self.context = _Context(document, calendar, self.today)
        self.state = state
        self.next_run_utc = next_run_utc
        self.single = rule_state.single_observation(session, next_run_utc)
        self.rows = _Rows(rows)
        self.updates: List[Dict[str, Any]] = []
        self.shas = rulebook.rules_sha

    def result(self, rule: Rule, outcomes: Sequence[_Outcome], state_subject: str, opened: Optional[datetime.date],
               suppressed_by: Optional[str]) -> RuleResult:
        truth = kleene_or(outcome.truth for outcome in outcomes)
        not_applicable = truth == FALSE and all(outcome.not_applicable for outcome in outcomes)
        chosen = _representative(outcomes, truth)
        primary = chosen.conditions[0]
        inputs: List[Input] = []
        notes: List[str] = []
        for condition in chosen.conditions:
            inputs.extend(condition.inputs)
            for note in condition.notes:
                if note not in notes:
                    notes.append(note)
        stamps = [item.as_of for item in chosen.conditions if isinstance(item.as_of, datetime.datetime)]
        as_of = min(stamps) if stamps else None
        unstamped = truth == TRUE and rule.input_class != rule_state.INPUT_CLASS_E and as_of is None
        if unstamped:
            truth = UNKNOWN  # never a confirmation on the clock (rule_state refuses it): unavailable, the data cause
        sha = self.shas[rule.id]
        single = rule.input_class == rule_state.INPUT_CLASS_E or self.single
        base: Dict[str, Any] = dict(
            rule_id=rule.id, rule_sha=sha, metric=primary.metric, value=primary.value, threshold=primary.threshold,
            unit=primary.unit, inputs=tuple(inputs), title=rule.title, role=rule.role, scope=rule.scope,
            verdict=rule.verdict, plan=rule.plan, op=primary.op, dte_unit=primary.dte_unit, input_class=rule.input_class,
            basis=rule.mark_basis, subject=chosen.subject.label, conditions=chosen.conditions,
            quote_grade=_worst_grade(item.grade for item in inputs), as_of=as_of, cooldown_min=rule.cooldown_min,
            notes=tuple(notes), suppress_tag=rule.suppress_if_tag, confirmations=1 if single else rule.confirmations)
        if suppressed_by is not None and not not_applicable:
            return RuleResult(result=RESULT_SUPPRESSED, reason="suppressed by %s" % suppressed_by, **base)
        entry = self.state.get(rule_state.state_key(state_subject, rule.id))
        step = rule_state.step(entry if isinstance(entry, Mapping) else None, truth=truth, as_of=as_of,
                               value=primary.value, policy=rule.policy, now=self.now, today=self.today,
                               single_observation=self.single, opened=opened, rule_sha=sha,
                               extra_holidays=self.context.extra)
        self.updates.append(rule_state.update_record(state_subject, rule.id, step, truth=truth, as_of=as_of,
                                                     value=primary.value))
        base.update(state_status=step.after, transition=step.event, new_signal=step.fired,
                    first_observation=step.first_observation,
                    observations=len(step.entry["observations"]) if step.entry is not None else 0)
        if truth == TRUE:
            if step.after == rule_state.STATUS_COOLDOWN:
                assert step.entry is not None
                rearm_at = rule_state.parse_instant(step.entry.get("rearm_at"))
                when = market_clock.to_et(rearm_at).strftime("%Y-%m-%d %H:%M") if rearm_at is not None else "its re-arm"
                return RuleResult(result=RESULT_COOLDOWN, reason=COOLDOWN_REASON % when, **base)
            fired = step.after == rule_state.STATUS_FIRED
            return RuleResult(result=RESULT_FIRED if fired else RESULT_PENDING, reason="", **base)
        if unstamped:
            return RuleResult(result=RESULT_UNAVAILABLE, reason=NO_STAMP_REASON, **base)
        if truth == UNKNOWN:
            cause = RESULT_UNAVAILABLE if chosen.cause == KIND_UNAVAILABLE else RESULT_NO_HISTORY
            return RuleResult(result=cause, reason=chosen.reason, **base)
        return RuleResult(result=RESULT_CLEAR, reason=chosen.reason if not_applicable else "", **base)

    def group(self, group: Mapping[str, Any]) -> GroupVerdict:
        group_id = str(group["group_id"])
        common: Dict[str, Any] = dict(trade_id=group.get("trade_id"), underlying=group.get("underlying"),
                                      structure=group.get("structure"), data_quality=group.get("data_quality"))
        if group.get("structure") == grouping.STRUCTURE_INVALID:
            reasons = [item for item in group.get("warnings") or [] if item != book_module.REASON_QUARANTINED]
            return GroupVerdict(group_id, VERDICT_UNAVAILABLE, [], [], [], [], [], quarantined=True,
                                reasons=reasons or [book_module.REASON_QUARANTINED], **common)
        buckets: Dict[str, List[RuleResult]] = {name: [] for name in RESULTS}
        opened = self.rows.opened_date(group)
        for rule in self.rulebook.rules:
            if rule.scope not in (SCOPE_GROUP, SCOPE_LEG):
                continue
            subjects = _subjects(self.context, rule, group)
            if not subjects:
                continue
            outcomes = [_evaluate_subject(self.context, rule, subject) for subject in subjects]
            suppressed_by = None
            if rule.suppress_if_tag is not None and self.rows.known:
                row = self.rows.tag_row(group_id, rule.suppress_if_tag)
                if row is not None:
                    suppressed_by = "#%s on row %d" % (rule.suppress_if_tag, row)
            result = self.result(rule, outcomes, group_id, opened, suppressed_by)
            buckets[result.result].append(result)
        verdict = group_verdict([item.verdict for item in buckets[RESULT_FIRED]], bool(buckets[RESULT_UNAVAILABLE]))
        return GroupVerdict(group_id, verdict, buckets[RESULT_FIRED], buckets[RESULT_UNAVAILABLE], buckets[RESULT_PENDING],
                            buckets[RESULT_CLEAR], buckets[RESULT_SUPPRESSED], no_history=buckets[RESULT_NO_HISTORY],
                            cooldown=buckets[RESULT_COOLDOWN], **common)

    def section(self, scope: str, subject: _Subject, state_subject: str) -> SectionVerdict:
        buckets: Dict[str, List[RuleResult]] = {name: [] for name in RESULTS}
        for rule in self.rulebook.rules:
            if rule.scope != scope:
                continue
            outcome = _evaluate_subject(self.context, rule, subject)
            result = self.result(rule, [outcome], state_subject, None, None)
            buckets[result.result].append(result)
        return SectionVerdict(scope, subject.label, buckets[RESULT_FIRED], buckets[RESULT_UNAVAILABLE],
                              buckets[RESULT_NO_HISTORY], buckets[RESULT_PENDING], buckets[RESULT_CLEAR],
                              buckets[RESULT_SUPPRESSED], buckets[RESULT_COOLDOWN])

    def run(self) -> Evaluation:
        context = self.context
        verdicts: List[GroupVerdict] = []
        listed = set()
        for group in context.groups:
            listed.add(str(group["group_id"]))
            if not context.open_legs(group) and group.get("structure") != grouping.STRUCTURE_INVALID:
                continue  # a closed-only group has nothing left to evaluate
            verdicts.append(self.group(group))
        for item in context.portfolio.get("quarantined_groups") or []:
            group_id = str(item.get("group_id"))
            if group_id in listed:
                continue
            listed.add(group_id)
            reasons = [str(reason) for reason in item.get("reasons") or []] or [book_module.REASON_QUARANTINED]
            verdicts.append(GroupVerdict(group_id, VERDICT_UNAVAILABLE, [], [], [], [], [], quarantined=True,
                                         reasons=reasons, data_quality=book_module.DATA_QUALITY_UNAVAILABLE))
        underlyings = [self.section(SCOPE_UNDERLYING, _Subject(LEVEL_UNDERLYING, "underlying", symbol=str(symbol)),
                                    "underlying:%s" % symbol) for symbol in sorted(context.underlyings)]
        portfolio = self.section(SCOPE_PORTFOLIO, _Subject(LEVEL_PORTFOLIO, PORTFOLIO), PORTFOLIO)
        warnings: List[str] = []
        if not self.rows.known and any(rule.suppress_if_tag for rule in self.rulebook.rules):
            warnings.append(WARNING_NO_ROWS)
        return Evaluation(groups=verdicts, underlyings=underlyings, portfolio=portfolio, state_updates=self.updates,
                          evaluated_at=self.now, et_date=self.today, single_observation=self.single,
                          next_run_utc=self.next_run_utc, history_date=context.history_date,
                          rulebook_version=self.rulebook.rulebook_version, rulebook_sha=self.rulebook.sha256,
                          rulebook=self.rulebook, warnings=warnings)


def evaluate(rulebook: Rulebook, book: Mapping[str, Any], *, now: datetime.datetime, session: SessionInfo,
             calendar: Optional[CalendarFile], state: Mapping[str, Any], next_run_utc: Optional[datetime.datetime],
             rows: Optional[Iterable[Any]] = None) -> Evaluation:
    """Evaluate ``rulebook`` against the book dict at ``now`` (module notes). ``session`` is the calendar-aware
    classification (``BookRun.session``), ``calendar`` the loaded ``research/calendar.toml`` (``None`` when absent),
    ``state`` the dict ``rule_state.load`` returns (never changed), ``next_run_utc`` the caller's next run (decision
    17(i)), ``rows`` the positions rows for tags and opened dates (``run.positions.rows``). Pure."""
    return _Evaluator(rulebook, book, now=now, session=session, calendar=calendar, state=state,
                      next_run_utc=next_run_utc, rows=rows).run()


# --------------------------------------------------------------------------------------------
# The adoption lint
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LintFinding:
    """One lint finding: ``code`` (:data:`LINT_CODES`), the rule it concerns (``None`` for a structure-level
    finding), the structure (``None`` for a rule-level finding) and one line saying what to change."""

    code: str
    rule_id: Optional[str]
    structure: Optional[str]
    message: str


def held_structures(groups: Iterable[Any]) -> List[str]:
    """The sorted, de-duplicated structures of the groups with an open leg (``grouping.PositionGroup`` objects)."""
    return sorted({group.structure for group in groups if group.open_legs})


def held_structures_from_book(document: Mapping[str, Any]) -> List[str]:
    """:func:`held_structures` from a book dict."""
    legs = {leg["row_number"]: leg for leg in document["legs"]}
    return sorted({group["structure"] for group in document["groups"]
                   if any(legs.get(row, {}).get("status") == "open" for row in group["legs"])})


def _covering_levels(rule: Rule, structure: str) -> List[Tuple[str, str]]:
    """``(level, entry)`` for each way ``rule`` reaches a group of ``structure`` (empty: it does not apply)."""
    found: List[Tuple[str, str]] = []
    for entry in rule.applies_to:
        level: Optional[str] = None
        if entry == structure and entry in RULE_STRUCTURES:
            level = LEVEL_LEG if rule.scope == SCOPE_LEG else LEVEL_GROUP
        elif entry == ANY_GROUP:
            level = LEVEL_GROUP
        elif entry == SHORT_CALL_LEG and structure in COVERED_STRUCTURES:
            level = LEVEL_LEG
        elif entry == LONG_PUT_LEG and structure in HEDGED_STRUCTURES:
            level = LEVEL_LEG
        elif entry == ANY_SHORT_CALL_LEG and structure in SHORT_CALL_STRUCTURES:
            level = LEVEL_LEG
        elif entry == ANY_OPTION_LEG and structure in OPTION_STRUCTURES:
            level = LEVEL_LEG
        if level is not None:
            found.append((level, entry))
    return found


def _metric_applies(metric: Metric, level: str, structure: Optional[str], entry: str) -> bool:
    """Whether ``metric`` is defined on the subject ``entry`` makes of ``structure`` at ``level``."""
    if level == LEVEL_GROUP:
        return metric.structures is None or structure in metric.structures
    if level == LEVEL_LEG and metric.short_legs_only:
        return entry in (SHORT_CALL_LEG, ANY_SHORT_CALL_LEG) or entry in RULE_STRUCTURES
    return True


def lint(rulebook: Rulebook, held_structures: Iterable[str]) -> List[LintFinding]:
    """The adoption lint (module notes) of ``rulebook`` for the ``held_structures`` (structure names); sorted by
    code, rule and structure. An empty list is a rulebook the desk may adopt."""
    findings: List[LintFinding] = []

    def add(code: str, rule_id: Optional[str], structure: Optional[str], message: str) -> None:
        finding = LintFinding(code, rule_id, structure, message)
        if all((item.code, item.rule_id, item.structure) != (code, rule_id, structure) for item in findings):
            findings.append(finding)

    missing = {name for name, _ in missing_inputs()}
    for rule in rulebook.rules:
        if rule.origin == ORIGIN_EXAMPLE:
            add(LINT_NOT_ACCEPTED, rule.id, None, "rule %s is an example: mark it example-accepted or owner-entered to "
                                                  "adopt it (each rule is adopted individually)" % rule.id)
        if rule.owner_must_set and rule.origin != ORIGIN_OWNER:
            add(LINT_OWNER_MUST_SET, rule.id, None, "rule %s holds a documented default the owner must set: enter the "
                                                    "value and mark the rule owner-entered" % rule.id)
        for condition in rule.when:
            if condition.metric == "pct_of_max_loss" and condition.value is not None and condition.value > _HUNDRED:
                add(LINT_MAX_LOSS_OVER_100, rule.id, None, "rule %s: pct_of_max_loss %s exceeds 100 (it can never fire)"
                    % (rule.id, condition.value))
            if condition.metric in missing:
                add(LINT_INPUT_UNAVAILABLE, rule.id, None, "rule %s: metric %s reads a field the book schema does not "
                                                           "have" % (rule.id, condition.metric))
        if rule.role == ROLE_LOSS and set(rule.applies_to) & {SHORT_CALL_LEG, ANY_SHORT_CALL_LEG}:
            add(LINT_LOSS_ON_SHORT_CALL_LEG, rule.id, None, "rule %s: a covered call or collar has no loss rule on its short "
                                                            "call; measure its loss on the group (unrealized_pl or "
                                                            "pct_of_max_loss)" % rule.id)
        if rule.role == ROLE_TAKE_PROFIT and rule.primary.metric not in TAKE_PROFIT_METRICS:
            add(LINT_TAKE_PROFIT_METRIC, rule.id, None, "rule %s: a take-profit rule's first condition reads %s"
                % (rule.id, " or ".join(TAKE_PROFIT_METRICS)))
        for entry in rule.applies_to:
            if entry in RULE_STRUCTURES:
                if rule.scope == SCOPE_LEG and entry not in SHORT_LEG_STRUCTURES:
                    add(LINT_SELECTS_NOTHING, rule.id, entry, "rule %s: a leg rule naming %s selects its short option legs, "
                                                              "and it has none" % (rule.id, entry))
                level = LEVEL_LEG if rule.scope == SCOPE_LEG else LEVEL_GROUP
                structure: Optional[str] = entry
            elif entry in LEG_SELECTORS:
                level, structure = LEVEL_LEG, None
            else:
                continue
            for condition in rule.when:
                if not _metric_applies(METRICS[condition.metric], level, structure, entry):
                    add(LINT_METRIC_NOT_APPLICABLE, rule.id, entry, "rule %s: %s is not defined on %s"
                        % (rule.id, condition.metric, entry))
    for structure in sorted(set(held_structures)):
        covered = False
        losses: List[Rule] = []
        for rule in rulebook.rules:
            ways = _covering_levels(rule, structure)
            if not ways:
                continue
            applies = True
            for level, entry in ways:
                for condition in rule.when:
                    if not _metric_applies(METRICS[condition.metric], level, structure, entry):
                        applies = False
                        add(LINT_METRIC_NOT_APPLICABLE, rule.id, structure, "rule %s: %s is not defined on a held %s"
                            % (rule.id, condition.metric, structure))
            if rule.role == ROLE_TAKE_PROFIT and applies and rule.primary.metric in TAKE_PROFIT_METRICS:
                own = STRUCTURE_TAKE_PROFIT_METRICS.get(structure, TAKE_PROFIT_METRICS)
                if rule.primary.metric in own:
                    covered = True
                else:
                    add(LINT_TAKE_PROFIT_METRIC, rule.id, structure, "rule %s: a take-profit rule on a held %s reads %s, "
                        "not %s" % (rule.id, structure, " or ".join(own), rule.primary.metric))
            if rule.role == ROLE_LOSS:
                losses.append(rule)
                metric = rule.primary.metric
                on_group = any(level == LEVEL_GROUP for level, _ in ways)
                if metric not in LOSS_METRICS or (structure in COVERED_STRUCTURES and on_group
                                                  and metric not in COVERED_LOSS_METRICS):
                    allowed = COVERED_LOSS_METRICS if structure in COVERED_STRUCTURES else LOSS_METRICS
                    add(LINT_LOSS_METRIC, rule.id, structure, "rule %s: a loss rule on %s reads %s"
                        % (rule.id, structure, " or ".join(allowed)))
        if not covered and structure not in TAKE_PROFIT_EXEMPT:
            add(LINT_UNCOVERED, None, structure, "no take-profit rule applies to the held %s (accept it explicitly with "
                                                 "--allow-uncovered)" % structure)
        if len(losses) > 1:
            add(LINT_DUPLICATE_LOSS, None, structure, "%d loss rules apply to %s (%s): keep one"
                % (len(losses), structure, ", ".join(rule.id for rule in losses)))
    return sorted(findings, key=lambda item: (item.code, item.rule_id or "", item.structure or ""))


# --------------------------------------------------------------------------------------------
# ADOPTED, CHANGELOG and summaries
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Adopted:
    """``rules/ADOPTED`` (module notes)."""

    path: str
    version: str
    sha256: str
    adopted_utc: datetime.datetime
    owner_phrase_sha256: str
    rules_sha: Dict[str, str]


def _toml_instant(moment: datetime.datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("an adoption instant must be aware")
    moment = moment.astimezone(UTC)
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def adopted_text(*, version: str, sha256: str, adopted_utc: datetime.datetime, owner_phrase_sha256: str,
                 rules_sha: Mapping[str, str]) -> str:
    """The exact text of ``rules/ADOPTED`` for an adoption (``rules_sha`` in the rulebook's order)."""
    if not VERSION_RE.match(version) or not SHA256_RE.match(sha256) or not SHA256_RE.match(owner_phrase_sha256):
        raise ValueError("adopted_text needs an X.Y.Z version and hex SHA-256 digests")
    lines = ["# The adopted exit rulebook: written by rules_adopt.py at adoption, never edited by hand.",
             'version = "%s"' % version, 'sha256 = "%s"' % sha256, "adopted_utc = %s" % _toml_instant(adopted_utc),
             'owner_phrase_sha256 = "%s"' % owner_phrase_sha256, "", "[rules_sha]"]
    for rule_id, sha in rules_sha.items():
        if not RULE_ID_RE.match(rule_id) or not SHA256_RE.match(sha):
            raise ValueError("rules_sha needs rule ids and hex SHA-256 digests")
        lines.append('%s = "%s"' % (rule_id, sha))
    return "\n".join(lines) + "\n"


def load_adopted(path: str) -> Adopted:
    """Read ``rules/ADOPTED`` strictly: :class:`RulebookError` ``not_adopted`` (no file) or ``adopted_invalid``."""
    try:
        document = toml_compat.load(path)
    except FileNotFoundError:
        raise RulebookError(path, ["no rules/ADOPTED: no rulebook has been adopted"], "not_adopted") from None
    except (toml_compat.TomlError, UnicodeDecodeError) as exc:
        raise RulebookError(path, ["invalid TOML: %s" % exc], "adopted_invalid") from None
    problems: List[str] = []
    for key in document:
        if key not in ADOPTED_KEYS:
            problems.append("unknown key '%s'" % key)
    for key in ADOPTED_KEYS:
        if key not in document:
            problems.append("missing required key '%s'" % key)
    version = document.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        problems.append("version must be X.Y.Z")
    for key in ("sha256", "owner_phrase_sha256"):
        value = document.get(key)
        if not isinstance(value, str) or not SHA256_RE.match(value):
            problems.append("%s must be a hex SHA-256 digest" % key)
    adopted_utc = document.get("adopted_utc")
    if not isinstance(adopted_utc, datetime.datetime) or adopted_utc.tzinfo is None:
        problems.append("adopted_utc must be an offset date-time")
    rules = document.get("rules_sha")
    if not isinstance(rules, dict) or not rules or not all(
            isinstance(key, str) and RULE_ID_RE.match(key) and isinstance(value, str) and SHA256_RE.match(value)
            for key, value in rules.items()):
        problems.append("rules_sha must map rule ids to hex SHA-256 digests")
    if problems:
        raise RulebookError(path, problems, "adopted_invalid")
    assert isinstance(adopted_utc, datetime.datetime) and isinstance(rules, dict)
    return Adopted(path, str(version), str(document["sha256"]), adopted_utc.astimezone(UTC),
                   str(document["owner_phrase_sha256"]), dict(rules))


def rulebook_path(paths: DeskPaths, version: str) -> str:
    """``rules/exit_rules-X.Y.Z.toml`` under the desk root."""
    if not VERSION_RE.match(version):
        raise ValueError("a rulebook version is X.Y.Z, got %r" % (version,))
    return os.path.join(paths.rules_dir, "exit_rules-%s.toml" % version)


def load_adopted_rulebook(paths: DeskPaths) -> Tuple[Adopted, Rulebook]:
    """``(ADOPTED, the rulebook it names)``; :class:`RulebookError` ``rules_tampered`` when the file's bytes, version
    or per-rule shas no longer match the adoption record (``not_adopted``, ``adopted_invalid``, ``rules_missing`` and
    ``config_invalid`` otherwise)."""
    adopted = load_adopted(paths.adopted_file)
    path = rulebook_path(paths, adopted.version)
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        raise RulebookError(path, ["the adopted rulebook %s is missing" % adopted.version], "rules_missing") from None
    digest = hashlib.sha256(data).hexdigest()
    if digest != adopted.sha256:
        raise RulebookError(path, ["the file's sha256 %s is not the adopted %s: the rulebook changed after adoption"
                                   % (digest[:8], adopted.sha256[:8])], "rules_tampered")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RulebookError(path, ["not valid UTF-8: %s" % exc]) from None
    rulebook = parse_rulebook_text(text, path, digest)
    if rulebook.rulebook_version != adopted.version:
        raise RulebookError(path, ["rulebook_version %s is not the adopted %s" % (rulebook.rulebook_version,
                                                                                   adopted.version)], "rules_tampered")
    if rulebook.rules_sha != adopted.rules_sha:
        raise RulebookError(path, ["the per-rule shas are not the adopted ones"], "rules_tampered")
    return adopted, rulebook


_UNIT_WORDS: Dict[str, str] = {"percent": "percent", "usd": "usd", "days": "days", "delta": "delta", "multiple": "x",
                               "pct_of_threshold": "percent of the threshold"}


def condition_summary(condition: Condition) -> str:
    """``pct_captured >= 50 percent``, ``dte > 21 trading days``, ``stock_above_short_call is_true``."""
    if condition.value is None:
        return "%s %s" % (condition.metric, condition.op)
    unit = condition.unit or METRICS[condition.metric].unit
    words = "%s days" % condition.dte_unit if condition.dte_unit is not None else _UNIT_WORDS.get(unit, unit)
    return "%s %s %s %s" % (condition.metric, condition.op, condition.value, words)


def rule_summary(rule: Rule) -> str:
    """The conjunction of a rule's conditions in words (the CHANGELOG's threshold summary)."""
    return " and ".join(condition_summary(condition) for condition in rule.when)


def changelog_entry(rulebook: Rulebook, *, adopted_utc: datetime.datetime, owner_phrase_sha256: str,
                    allow_rth: bool = False, allow_uncovered: Sequence[str] = ()) -> str:
    """One ``rules/CHANGELOG.md`` entry for the adoption of ``rulebook`` (module notes): the file and its sha256, the
    owner phrase's sha256, ``--allow-rth`` / ``--allow-uncovered`` when used, and every rule with its origin mark,
    its threshold summary, its verdict and its plan."""
    lines = ["## %s adopted %s" % (rulebook.rulebook_version, _toml_instant(adopted_utc)), "",
             "- file exit_rules-%s.toml, sha256 %s" % (rulebook.rulebook_version, rulebook.sha256),
             "- owner phrase sha256 %s" % owner_phrase_sha256]
    if allow_rth:
        lines.append("- adopted during regular trading hours (--allow-rth)")
    if allow_uncovered:
        lines.append("- uncovered structures accepted (--allow-uncovered): %s" % ", ".join(sorted(allow_uncovered)))
    for rule in rulebook.rules:
        lines.append("- %s %s: %s; %s, plan %s" % (rule.id, rule.origin, rule_summary(rule), rule.verdict, rule.plan))
    return "\n".join(lines) + "\n"


def changelog_append(existing: str, entry: str) -> str:
    """``existing`` (the CHANGELOG's text, or ``""`` for a new file) with ``entry`` appended after one blank line."""
    if not existing:
        return CHANGELOG_HEADER + entry
    if existing.endswith("\n\n"):
        return existing + entry
    if existing.endswith("\n"):
        return existing + "\n" + entry
    return existing + "\n\n" + entry


# --------------------------------------------------------------------------------------------
# book_from_json (decision 4)
# --------------------------------------------------------------------------------------------

LEG_FIELDS: Dict[str, str] = {
    "row_number": "int", "trade_id": "str?", "group_id": "str", "account": "str", "instrument_type": "str",
    "symbol": "str", "occ": "str?", "underlying": "str", "right": "str?", "strike": "dec?", "expiry": "date?",
    "settlement": "str?", "dte_calendar": "int?", "dte_trading": "int?", "quantity": "dec", "multiplier": "int",
    "cost_basis_total": "dec", "entry_price_per_share": "dec?", "status": "str", "quote_grade": "str?",
    "quote_status": "str?", "bid": "dec?", "ask": "dec?", "bid_date": "dt?", "ask_date": "dt?", "as_of": "dt?",
    "spread_pct": "dec?", "mark_mid": "dec?", "mark_liq": "dec?", "mark_source": "str", "tau_years": "float?",
    "iv": "float?", "iv_status": "str", "greeks": "greeks?", "position_greeks": "greeks?", "unrealized_pl_mid": "dec?",
    "unrealized_pl_liq": "dec?", "day_pl": "dec?", "realized_day_pl": "dec?", "extrinsic_bid": "dec?", "itm": "bool?",
    "warnings": "strs", "open_quantity": "dec", "remaining_cost_basis": "dec", "fresh_bid": "dec?", "fresh_ask": "dec?",
}
"""How every leg key is read back (``?``: may be null)."""
LEG_OPTIONAL_KEYS: Tuple[str, ...] = ("fresh_bid", "fresh_ask")
"""Leg keys a snapshot written before PR 2b's follow-up does not have (read as null)."""
GROUP_FIELDS: Dict[str, str] = {
    "group_id": "str", "trade_id": "str?", "account": "str", "underlying": "str", "structure": "str", "basis_kind": "str",
    "net_cost": "dec", "max_profit": "dec?", "max_loss": "dec?", "breakevens": "decs", "unbounded_up": "bool",
    "unbounded_down": "bool", "unrealized_pl_liq": "dec?", "unrealized_pl_mid": "dec?", "pct_captured_liq": "dec?",
    "pct_captured_mid": "dec?", "realized_pl": "dec", "net_greeks": "greeks?", "legs": "ints", "data_quality": "str",
    "warnings": "strs", "underlying_stop": "dec?",
}
UNDERLYING_FIELDS: Dict[str, str] = {
    "mark_mid": "dec?", "bid": "dec?", "ask": "dec?", "as_of": "dt?", "quote_grade": "str?", "prev_eod_mid": "dec?",
    "move_pct_from_prev_eod": "dec?", "day_change_source": "str",
}
PORTFOLIO_FIELDS: Dict[str, str] = {
    "unrealized_pl_liq": "dec?", "unrealized_pl_mid": "dec?", "day_pl": "dec?", "realized_pl_today": "dec",
    "realized_pl_mtd": "dec", "realized_pl_ytd": "dec", "net_delta": "float?", "beta_weighted_delta": "beta",
    "concentration": "concentration", "open_groups": "int", "quarantined_groups": "quarantined",
}
BOOK_FIELDS: Dict[str, str] = {
    "generated_at": "dt", "session": "str", "as_of_label": "str", "data_quality": "data_quality", "sources": "sources",
    "rates": "rates", "config_sha": "config_sha", "legs": "legs", "groups": "groups", "underlyings": "underlyings",
    "portfolio": "portfolio", "warnings": "strs",
}
_TABLES: Dict[str, Dict[str, str]] = {
    "data_quality": {"grades": "grades", "usable_legs": "int", "unavailable_legs": "int", "bound_min": "int?"},
    "source": {"name": "str", "delay_min": "int?", "receipt_utc": "dt?"},
    "rates": {"risk_free_rate": "dec", "source": "str", "as_of": "date"},
    "config_sha": {key: "str?" for key in book_module.CONFIG_SHA_KEYS},
    "beta": {"value": "float?", "r2_flag": "str"},
    "concentration": {"declared_equity": "dec", "by_underlying": "by_underlying"},
    "quarantined": {"group_id": "str", "reasons": "strs"},
    "greeks": {key: "float" for key in book_module.GREEK_KEYS},
}


def _bad(where: str, what: str, value: Any) -> BookFormatError:
    return BookFormatError("book%s: %s, got %r" % (where or ".", what, value))


def _read_scalar(kind: str, value: Any, where: str) -> Any:
    optional = kind.endswith("?")
    base = kind[:-1] if optional else kind
    if value is None:
        if optional:
            return None
        raise _bad(where, "a %s is required" % base, value)
    if base == "str":
        if not isinstance(value, str):
            raise _bad(where, "not a string", value)
        return value
    if base == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _bad(where, "not an integer", value)
        return value
    if base == "bool":
        if not isinstance(value, bool):
            raise _bad(where, "not a boolean", value)
        return value
    if base == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _bad(where, "not a number", value)
        return value
    if base == "dec":
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise _bad(where, "not a decimal string", value)
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            raise _bad(where, "not a decimal string", value) from None
        if not number.is_finite():
            raise _bad(where, "not a finite decimal", value)
        return number
    if base == "date":
        if not isinstance(value, str):
            raise _bad(where, "not an ISO date", value)
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            raise _bad(where, "not an ISO date", value) from None
    if base == "dt":
        moment = rule_state.parse_instant(value)
        if moment is None:
            raise _bad(where, "not an ISO-8601 instant with a zone", value)
        return moment
    raise ValueError("unknown field kind %r" % kind)


def _read_table(table: Any, fields: Mapping[str, str], where: str, optional: Sequence[str] = ()) -> Dict[str, Any]:
    if not isinstance(table, dict):
        raise _bad(where, "not an object", table)
    for key in table:
        if key not in fields:
            raise _bad("%s.%s" % (where, key), "an unknown key", table[key])
    out: Dict[str, Any] = {}
    for key, kind in fields.items():
        if key not in table:
            if key in optional:
                out[key] = None
                continue
            raise _bad(where, "the key %r is missing" % key, None)
        out[key] = _read_value(kind, table[key], "%s.%s" % (where, key))
    return out


def _read_list(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise _bad(where, "not a list", value)
    return value


def _read_mapping(value: Any, kind: str, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _bad(where, "not an object", value)
    return {str(key): _read_value(kind, item, "%s.%s" % (where, key)) for key, item in value.items()}


def _read_value(kind: str, value: Any, where: str) -> Any:
    if kind in ("strs", "ints", "decs"):
        scalar = {"strs": "str", "ints": "int", "decs": "dec"}[kind]
        return [_read_scalar(scalar, item, "%s[%d]" % (where, index)) for index, item in enumerate(_read_list(value, where))]
    if kind == "greeks?":
        return None if value is None else _read_table(value, _TABLES["greeks"], where)
    if kind == "legs":
        return [_read_table(item, LEG_FIELDS, "%s[%d]" % (where, index), LEG_OPTIONAL_KEYS)
                for index, item in enumerate(_read_list(value, where))]
    if kind == "groups":
        return [_read_table(item, GROUP_FIELDS, "%s[%d]" % (where, index)) for index, item in enumerate(_read_list(value, where))]
    if kind == "sources":
        return [_read_table(item, _TABLES["source"], "%s[%d]" % (where, index))
                for index, item in enumerate(_read_list(value, where))]
    if kind == "quarantined":
        return [_read_table(item, _TABLES["quarantined"], "%s[%d]" % (where, index))
                for index, item in enumerate(_read_list(value, where))]
    if kind == "underlyings":
        if not isinstance(value, dict):
            raise _bad(where, "not an object", value)
        return {str(symbol): _read_table(entry, UNDERLYING_FIELDS, "%s.%s" % (where, symbol)) for symbol, entry in value.items()}
    if kind == "portfolio":
        return _read_table(value, PORTFOLIO_FIELDS, where)
    if kind == "grades":
        return _read_mapping(value, "int", where)
    if kind == "by_underlying":
        return _read_mapping(value, "dec?", where)
    if kind in _TABLES:
        return _read_table(value, _TABLES[kind], where)
    return _read_scalar(kind, value, where)


def book_from_json(document: Any) -> Dict[str, Any]:
    """A saved book (as ``json.loads`` gives it) back to the in-memory book dict (module notes); raises
    :class:`BookFormatError` for anything that is not the book schema."""
    return _read_table(document, BOOK_FIELDS, "")


__all__ = [
    "ACCEPTANCE_CODES", "ADOPTED_KEYS", "ANY_GROUP", "ANY_OPTION_LEG", "ANY_SHORT_CALL_LEG", "ANY_UNDERLYING",
    "APPLIES_TO_NAMES", "BASIS_LIQ", "BASIS_MID", "BOOK_FIELDS", "BOOL_OPS", "CHANGELOG_HEADER", "COMPARISON_OPS",
    "CONDITION_KEYS", "CONSERVATIVE_NOTE", "COVERED_LOSS_METRICS", "CREDIT_STRUCTURES", "DEBIT_OPTION_STRUCTURES",
    "DEFAULTS_KEYS", "DTE_UNITS", "FALSE", "GROUP_FIELDS", "GROUP_VERDICTS", "INPUT_CLASSES", "LEG_FIELDS",
    "LEG_OPTIONAL_KEYS", "LEG_SELECTORS", "LINT_CODES", "LINT_DUPLICATE_LOSS", "LINT_INPUT_UNAVAILABLE",
    "LINT_LOSS_METRIC", "LINT_LOSS_ON_SHORT_CALL_LEG", "LINT_MAX_LOSS_OVER_100", "LINT_METRIC_NOT_APPLICABLE",
    "LINT_NOT_ACCEPTED", "LINT_OWNER_MUST_SET", "LINT_SELECTS_NOTHING", "LINT_TAKE_PROFIT_METRIC", "LINT_UNCOVERED",
    "LONG_PUT_LEG", "LOSS_GATE_REASON", "LOSS_METRICS", "MARK_BASES", "METRICS", "NO_OFFER_REASON", "NO_STAMP_REASON",
    "COOLDOWN_REASON", "ESTIMATED_EARNINGS_NOTE", "RESULT_COOLDOWN", "RULE_ID_WORDS", "SHA256_RE",
    "STRUCTURE_TAKE_PROFIT_METRICS", "refine_at_threshold", "OPS", "ORIGINS",
    "ORIGIN_ACCEPTED", "ORIGIN_EXAMPLE", "ORIGIN_OWNER", "PLANS", "PORTFOLIO", "PORTFOLIO_FIELDS", "PSEUDO_STRUCTURES",
    "REARM_MODES", "RESULTS", "RESULT_CLEAR", "RESULT_FIRED", "RESULT_NO_HISTORY", "RESULT_PENDING", "RESULT_SUPPRESSED",
    "RESULT_UNAVAILABLE", "ROLES", "ROLE_LOSS", "ROLE_TAKE_PROFIT", "RULE_ID_RE", "RULE_KEYS", "RULE_STRUCTURES",
    "RULE_VERDICTS", "SCHEMA_VERSION", "SCOPES", "SHORT_CALL_LEG", "SUPPRESS_TAGS", "TAKE_PROFIT_EXEMPT",
    "TAKE_PROFIT_METRICS", "TOP_LEVEL_KEYS", "TRUE", "UNDERLYING_FIELDS", "UNITS", "UNKNOWN", "VERDICT_EXIT_SIGNAL",
    "VERDICT_HOLD", "VERDICT_UNAVAILABLE", "VERDICT_WATCH", "VERSION_RE", "WARNING_NO_ROWS",
    "Adopted", "BookFormatError", "Condition", "ConditionResult", "Defaults", "Evaluation", "GroupVerdict", "Input",
    "LintFinding", "Metric", "Rule", "RuleResult", "Rulebook", "RulebookError", "SectionVerdict",
    "adopted_text", "book_from_json", "changelog_append", "changelog_entry", "compare", "condition_summary", "evaluate",
    "evaluation_exit_code", "group_verdict", "held_structures", "held_structures_from_book", "kleene_and",
    "kleene_maximum", "kleene_or", "lint", "load_adopted", "load_adopted_rulebook", "load_rulebook", "missing_inputs",
    "parse_rulebook_text", "rule_canonical", "rule_sha", "rule_summary", "rulebook_path",
]
