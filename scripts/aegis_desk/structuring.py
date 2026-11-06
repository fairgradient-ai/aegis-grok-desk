"""The Structuring Desk's engine: candidates for a position the owner holds, priced, graded, scored and ranked.

PR 3b (designer Q's D3-D14 under the addendum S1-S17); standard library only, no wall clock (``now`` is a parameter,
read from the :class:`aegis_desk.quote_cache.MarketData` the tool opened), the network reached only through
``quote_cache``. Three drivers answer the three tools: :func:`run_roll_analysis` (``roll_analysis.py``: replacements for
the short call of a ``covered_call`` group under ``[roll]``), :func:`run_proposal` (``propose.py``: a covered call, a
protective put or a collar on a shares-only group under ``[covered_call]``, ``[protective_put]`` or ``[collar]``) and
:func:`run_replacement` (``exit_ticket.py --action ROLL``: the one replacement the owner names, a short call under
``[roll]`` or a long put under ``[protective_put]``, through the same code path). Each returns an :class:`Answer`;
:mod:`aegis_desk.structuring_text` turns it into the linted text.

Loading (designer Q's D3)
-------------------------
1. :func:`load_target`: ``positions.csv`` (``today`` = the ET date of ``now``) grouped, ``research/calendar.toml`` when
   present, and the session classified with the calendar's full closures. :func:`load_group_book`: ONE group-scoped
   quotes batch (``book.request_batch([group])`` -> ``quote_cache.get_quotes`` with ``allow_fetch`` the session's trading
   day -> ``book.assess_quotes`` -> ``book.build_book(groups=[group], prev_snapshot=None)``), never ``book.load_book``
   (its whole-book batch has no recorded fixture on a shares-only copy). A non-trading day answers UNAVAILABLE
   ``market_closed``; an unusable spot (the underlying's ``mark_mid`` at grade A-D) or held leg stops here.
2. :func:`load_expirations`: ``quote_cache.get_expirations`` of the underlying (cached for the ET date); the candidate
   expirations are the listed standard monthlies (:func:`is_standard_monthly`: the settlement day of the month's
   :func:`third_friday`) inside the section's ``dte`` window, in calendar days to the last trading day, and for a roll
   later than the held leg's expiration, ascending (:func:`candidate_expirations`).
3. :func:`load_chains`: ``quote_cache.get_chain`` per candidate expiration, ascending; the first expiration not served
   makes the answer UNAVAILABLE ``chain_not_served`` (no best is named on partial data) and the rest are listed
   ``not requested``. The window spans at most 90 days, so at most four monthlies: the per-run chain cap holds.
4. :func:`requests_block`: ``{made, chains, per_run_max_requests, per_run_max_chains, budget_left}``.

Pricing and quote QC (designer Q's D4)
--------------------------------------
:func:`price_contract` grades every new leg with ``quote_qc.assess`` in the calendar-aware session. It is priced only
at a usable grade (A-D) with both sides fresh (a ``mark_mid``) on a standard contract (root = the underlying, 100
shares); otherwise it is UNPRICED with exactly one code: ``market_closed``, ``quote_missing``, ``quote_undated``,
``quote_stale``, ``quote_one_side_stale``, ``quote_crossed``, ``zero_bid``, ``quote_wide``, ``nonstandard_contract``,
``spot_unavailable``, ``spot_unaligned`` (the underlying's stamp more than 60 s from the option's), ``expiring`` (under
one hour to the exercise cutoff) or ``iv_<status>`` (the solver's status). A spread above ``[general] max_spread_pct``
is priced and OUTSIDE_BANDS ``max_spread_pct``. ``tau = (exercise_cutoff_utc - as_of) / units.SECONDS_PER_YEAR``; the IV
inverts the mid with ``r`` = ``desk.toml``'s rate and ``q`` = the calendar's ``dividend_yield`` (0 when absent);
Greeks are ``greeks.european_greeks`` at that IV; ``|delta|`` is read at 2 dp half-even as the exit rules read it. A
held leg being closed uses its conservative fresh side only (:class:`HeldLeg`): a short leg its fresh ask (> 0), a long
leg its fresh bid (0 allowed), from any grade A-E; grade E adds :data:`GRADE_E_NOTE`.

Statuses (S4)
-------------
``UNPRICED > OUTSIDE_BANDS > FAILS_CRITERION > ELIGIBLE`` (:data:`STATUS_PRECEDENCE`), every reason listed
(:class:`Reason` ``{key, code, value, threshold}``, in :data:`REASON_KEY_ORDER`). OUTSIDE_BANDS keys: ``dte``,
``abs_delta``, ``min_credit``, ``max_spread_pct``, ``floor_pct``, ``cap_pct``, ``stress_drop_pct`` (no cover at the
stress) and ``max_net_debit``; FAILS_CRITERION keys: ``credit_only`` (``debit_roll``), ``strike_floor``
(``roll_down_below_cost``, ``strike_below_cost``), ``earnings`` (``crosses_earnings``, ``earnings_unknown``) and
``ex_dividend`` (``crosses_ex_dividend``, ``dividend_unknown``). An event label accepted by ``#earnings-ok`` /
``#assign-ok`` on any open row of the group, or by ``earnings`` / ``ex_dividend = "allow"``, moves to ``accepted``
(:class:`Accepted` ``{key, code, by, value, threshold}``) and fails nothing.

Events (designer Q's D9 with S4), short calls only (:func:`short_call_event_reasons`): the window runs from ``now`` (a
fresh call) or the held call's exercise cutoff (a replacement) to the new call's cutoff. Earnings: status ``none`` no
label; no calendar, an unlisted symbol, no entry, status ``unknown``, no date, or a date whose instant has passed
(09:30 ET for ``pre``/``unknown``, 16:00 ET for ``post``; a passed date says nothing about the next one, and unknown is
never none) -> ``earnings_unknown``; else ``crosses_earnings`` when the instant lies in ``(start, end]``: a release
after the close of a last trading day belongs to the call expiring that day (its holders can still exercise after the
move, until the OCC 17:30 ET cutoff), so a replacement carries a release after the close of its own last trading day
and not one after the close of the held call's (review L1-3).
Ex-dividend through ``expiry.dividend_window``: not known -> ``dividend_unknown``; applicable to the new call (its last
cum-dividend day on or before the new last trading day) and not to the held call -> ``crosses_ex_dividend``; and, the
calendar holding one ex-date per symbol, a new last trading day at least :data:`SECOND_EX_DATE_HORIZON_DAYS` (80)
calendar days after the known ex-date adds ``dividend_unknown`` (a second ex-date may fall before it).

Objectives (S3; ``structuring_config.OBJECTIVE_DEFS``), Decimal with a 28-digit context, quantized half-even:
``extrinsic_per_day`` (:func:`extrinsic_per_day`: ``E = bid - max(0, S - K)``, ``E / S / T_c x 100`` at 0.0001, higher
first); ``stress_cost_per_day`` (:func:`stress_cost_per_day`: ``ask / T_c / (K - S x (1 - x/100)) x 100`` at 0.0001,
lower first, ``None`` with no cover); ``cap_within_budget`` (:func:`cap_pct`: ``(Kc - S) / S x 100`` at 0.01, higher
first). ``T_c`` is the calendar DTE to the last trading day. Tie-breaks (:data:`TIE_BREAKS`): covered call ``|delta|``
lower, expiration earlier, strike higher, contract; roll net credit higher first, then the covered call's; protective
put strike higher, expiration later, contract; collar put strike higher, net debit lower, expiration later, contract
pair. :func:`rank_candidates`: ELIGIBLE, FAILS_CRITERION, OUTSIDE_BANDS (each by the objective and the tie-breaks), then
UNPRICED by contract; rank = 1-based position; best = rank 1 iff ELIGIBLE, else NONE_QUALIFIES. An UNPRICED candidate
was never judged against the criteria: it is counted apart (``unpriced``), never as failing them. A chain that lists
one contract more than once is read once per symbol (:func:`listed_options`): identical copies are one contract,
copies with different quotes make it UNPRICED ``quote_conflict``, so no answer depends on where a copy stands (Q D17).
When no candidate at all was priced the answer is UNAVAILABLE ``no_candidate_priced`` (no complete evaluation); a
window that holds no listed standard monthly is NOT_APPLICABLE ``no_expiration_in_window``.

After-state (designer Q's D6-D8): :class:`AfterState` at the conservative fills (new short calls at the bid, new long
puts at the ask, the held leg closed at its conservative side). With ``n`` shares, the economic net cost ``E = stock
cost + new premiums paid - premiums received - realized on the closed leg`` and the remaining option legs, the payoff
at expiry is piecewise linear with kinks at 0 and the strikes: max loss ``max(0, -min)`` over the kinks, max profit the
largest kink value unless the slope beyond the highest strike is positive (``None``, unbounded), the breakevens the
zero crossings. For matched shares they are designer Q's closed forms (roll: ``max_loss_before - net credit total``,
``n (K_new - s0) + P``, ``s0 - P / n``; protective put ``(s0 - K + ask) n``; collar ``(s0 - Kp + D) n``, ``(Kc - s0 - D)
n``, ``s0 + D``); the tests prove them equal to ``payoff`` on in-memory post-fill rows plus ``pnl.realized_for_row``
(S6: those rows are never printed or stored). The group delta after is ``n`` plus each new leg's position delta.

Applicability (S1): :func:`roll_applicability`, :func:`proposal_applicability` and :func:`ticket_applicability` return
``None`` or an :class:`Applicability` (NOT_APPLICABLE or NOTHING_TO_PROPOSE, a code and the desk's own reason words,
never a cell the owner typed). While another group of ``positions.csv`` is quarantined its rows are not loaded, so the
account's coverage cannot be counted: every answer is UNAVAILABLE ``positions_quarantined`` before any quote (reviews
L2-1 and L3-1). UNAVAILABLE causes: :data:`CAUSES`. Errors (exit 1 in the tools):
:class:`StructuringError` with ``unknown_trade``, ``positions_invalid`` or ``calendar_invalid``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import alerts, approval, book, bsm, calendar_events, expiry, grouping, iv, market_clock, payoff, positions_csv
from . import quote_cache, quote_qc
from . import structuring_config, units
from . import greeks as greeks_module
from .calendar_events import CalendarFile
from .grouping import PositionGroup
from .market_clock import SessionInfo
from .market_types import Quote
from .positions_csv import PositionRow, PositionsFile
from .quote_cache import CacheResult, MarketData
from .quote_qc import QuoteQC
from .structuring_config import Objective, StructuringConfig

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

VERDICT_BEST: str = "BEST"
VERDICT_NONE_QUALIFIES: str = "NONE_QUALIFIES"
VERDICT_NOT_APPLICABLE: str = "NOT_APPLICABLE"
VERDICT_NOTHING_TO_PROPOSE: str = "NOTHING_TO_PROPOSE"
VERDICT_UNAVAILABLE: str = "UNAVAILABLE"
VERDICT_PRICED: str = "PRICED"
"""The verdict of :func:`run_replacement`: the named contract was priced (the ticket tool prints its labels)."""
VERDICTS: Tuple[str, ...] = (VERDICT_BEST, VERDICT_NONE_QUALIFIES, VERDICT_NOT_APPLICABLE, VERDICT_NOTHING_TO_PROPOSE,
                             VERDICT_UNAVAILABLE, VERDICT_PRICED)

STATUS_ELIGIBLE: str = "ELIGIBLE"
STATUS_FAILS_CRITERION: str = "FAILS_CRITERION"
STATUS_OUTSIDE_BANDS: str = "OUTSIDE_BANDS"
STATUS_UNPRICED: str = "UNPRICED"
STATUS_PRECEDENCE: Tuple[str, ...] = (STATUS_UNPRICED, STATUS_OUTSIDE_BANDS, STATUS_FAILS_CRITERION, STATUS_ELIGIBLE)
TABLE_ORDER: Tuple[str, ...] = (STATUS_ELIGIBLE, STATUS_FAILS_CRITERION, STATUS_OUTSIDE_BANDS, STATUS_UNPRICED)

KIND_COVERED_CALL: str = "covered_call"
KIND_REPLACEMENT_CALL: str = "replacement_call"
KIND_PROTECTIVE_PUT: str = "protective_put"
KIND_REPLACEMENT_PUT: str = "replacement_put"
KIND_COLLAR: str = "collar"
KINDS: Tuple[str, ...] = (KIND_COVERED_CALL, KIND_REPLACEMENT_CALL, KIND_PROTECTIVE_PUT, KIND_REPLACEMENT_PUT, KIND_COLLAR)
KIND_SECTIONS: Dict[str, str] = {KIND_COVERED_CALL: "covered_call", KIND_REPLACEMENT_CALL: "roll",
                                 KIND_PROTECTIVE_PUT: "protective_put", KIND_REPLACEMENT_PUT: "protective_put",
                                 KIND_COLLAR: "collar"}
"""The ``structuring.toml`` section each kind of candidate is judged under."""
PROPOSAL_STRUCTURES: Tuple[str, ...] = ("covered_call", "protective_put", "collar")
PROPOSAL_KINDS: Dict[str, str] = {"covered_call": KIND_COVERED_CALL, "protective_put": KIND_PROTECTIVE_PUT,
                                  "collar": KIND_COLLAR}

TOOL_ROLL_ANALYSIS: str = "roll_analysis"
TOOL_PROPOSE: str = "propose"
TOOL_EXIT_TICKET: str = "exit_ticket"
TOOLS: Tuple[str, ...] = (TOOL_EXIT_TICKET, TOOL_ROLL_ANALYSIS, TOOL_PROPOSE)

ACTION_CLOSE: str = "CLOSE"
ACTION_UNWIND_STOCK: str = "UNWIND_STOCK"
ACTION_LET_ASSIGN: str = "LET_ASSIGN"
ACTION_MONETIZE_PUT: str = "MONETIZE_PUT"
ACTION_ROLL: str = "ROLL"
ACTIONS: Tuple[str, ...] = (ACTION_CLOSE, ACTION_UNWIND_STOCK, ACTION_LET_ASSIGN, ACTION_MONETIZE_PUT, ACTION_ROLL)

KEY_DTE: str = "dte"
KEY_ABS_DELTA: str = "abs_delta"
KEY_MIN_CREDIT: str = "min_credit"
KEY_MAX_SPREAD_PCT: str = "max_spread_pct"
KEY_FLOOR_PCT: str = "floor_pct"
KEY_CAP_PCT: str = "cap_pct"
KEY_STRESS_DROP_PCT: str = "stress_drop_pct"
KEY_MAX_NET_DEBIT: str = "max_net_debit"
KEY_CREDIT_ONLY: str = "credit_only"
KEY_STRIKE_FLOOR: str = "strike_floor"
KEY_EARNINGS: str = "earnings"
KEY_EX_DIVIDEND: str = "ex_dividend"
KEY_UNPRICED: str = "unpriced"
BAND_KEYS: Tuple[str, ...] = (KEY_DTE, KEY_ABS_DELTA, KEY_MIN_CREDIT, KEY_MAX_SPREAD_PCT, KEY_FLOOR_PCT, KEY_CAP_PCT,
                              KEY_STRESS_DROP_PCT, KEY_MAX_NET_DEBIT)
"""The OUTSIDE_BANDS keys (the owner's windows, bands, minimums and budgets)."""
CRITERION_KEYS: Tuple[str, ...] = (KEY_CREDIT_ONLY, KEY_STRIKE_FLOOR, KEY_EARNINGS, KEY_EX_DIVIDEND)
"""The FAILS_CRITERION keys (the owner's yes/no criteria)."""
REASON_KEY_ORDER: Tuple[str, ...] = BAND_KEYS + CRITERION_KEYS + (KEY_UNPRICED,)

CODE_DTE_OUTSIDE_WINDOW: str = "dte_outside_window"
CODE_DELTA_OUTSIDE_BAND: str = "delta_outside_band"
CODE_CREDIT_BELOW_MINIMUM: str = "credit_below_minimum"
CODE_SPREAD_ABOVE_MAX: str = "spread_above_max"
CODE_FLOOR_OUTSIDE_BAND: str = "floor_outside_band"
CODE_CAP_OUTSIDE_BAND: str = "cap_outside_band"
CODE_NO_COVER_AT_STRESS: str = "no_cover_at_stress"
CODE_NET_DEBIT_ABOVE_BUDGET: str = "net_debit_above_budget"
CODE_DEBIT_ROLL: str = "debit_roll"
CODE_ROLL_DOWN_BELOW_COST: str = "roll_down_below_cost"
CODE_STRIKE_BELOW_COST: str = "strike_below_cost"
CODE_CROSSES_EARNINGS: str = "crosses_earnings"
CODE_EARNINGS_UNKNOWN: str = "earnings_unknown"
CODE_CROSSES_EX_DIVIDEND: str = "crosses_ex_dividend"
CODE_DIVIDEND_UNKNOWN: str = "dividend_unknown"
CODE_MARKET_CLOSED: str = "market_closed"
CODE_QUOTE_MISSING: str = "quote_missing"
CODE_QUOTE_UNDATED: str = "quote_undated"
CODE_QUOTE_STALE: str = "quote_stale"
CODE_QUOTE_ONE_SIDE_STALE: str = "quote_one_side_stale"
CODE_QUOTE_CROSSED: str = "quote_crossed"
CODE_ZERO_BID: str = "zero_bid"
CODE_QUOTE_WIDE: str = "quote_wide"
CODE_NONSTANDARD_CONTRACT: str = "nonstandard_contract"
CODE_SPOT_UNAVAILABLE: str = "spot_unavailable"
CODE_SPOT_UNALIGNED: str = "spot_unaligned"
CODE_EXPIRING: str = "expiring"
CODE_QUOTE_CONFLICT: str = "quote_conflict"
"""The chain lists the contract more than once with different quotes: which copy stands is unknown, so none is used
(the answer never depends on where a copy stands in the vendor's list)."""
IV_CODE_PREFIX: str = "iv_"
"""An implied volatility the solver did not find is ``iv_<status>`` (``iv_below_intrinsic``, ``iv_no_root``, ...)."""
UNPRICED_CODES: Tuple[str, ...] = (CODE_MARKET_CLOSED, CODE_QUOTE_MISSING, CODE_QUOTE_UNDATED, CODE_QUOTE_STALE,
                                   CODE_QUOTE_ONE_SIDE_STALE, CODE_QUOTE_CROSSED, CODE_ZERO_BID, CODE_QUOTE_WIDE,
                                   CODE_NONSTANDARD_CONTRACT, CODE_SPOT_UNAVAILABLE, CODE_SPOT_UNALIGNED, CODE_EXPIRING,
                                   CODE_QUOTE_CONFLICT)
UNPRICED_WORDS: Dict[str, str] = {
    CODE_MARKET_CLOSED: "the market is closed", CODE_QUOTE_MISSING: "a quote side is missing",
    CODE_QUOTE_UNDATED: "a quote side is undated", CODE_QUOTE_STALE: "the quote is stale",
    CODE_QUOTE_ONE_SIDE_STALE: "one quote side is stale", CODE_QUOTE_CROSSED: "the quote is crossed",
    CODE_ZERO_BID: "no bid", CODE_QUOTE_WIDE: "the spread is wider than the quote grades allow",
    CODE_NONSTANDARD_CONTRACT: "not a standard 100-share contract",
    CODE_SPOT_UNAVAILABLE: "no usable quote of the underlying",
    CODE_SPOT_UNALIGNED: "the underlying's quote is stamped too far from the option's",
    CODE_EXPIRING: "it expires too soon for a per-day score",
    CODE_QUOTE_CONFLICT: "the chain lists the contract more than once with different quotes",
}
"""The words of every UNPRICED code (``structuring_text`` prints them; an ``iv_<status>`` code reads
:data:`IV_WORDS`)."""
IV_WORDS: str = "no implied volatility from its mid"
CODE_KEYS: Dict[str, str] = {
    CODE_DTE_OUTSIDE_WINDOW: KEY_DTE, CODE_DELTA_OUTSIDE_BAND: KEY_ABS_DELTA, CODE_CREDIT_BELOW_MINIMUM: KEY_MIN_CREDIT,
    CODE_SPREAD_ABOVE_MAX: KEY_MAX_SPREAD_PCT, CODE_FLOOR_OUTSIDE_BAND: KEY_FLOOR_PCT, CODE_CAP_OUTSIDE_BAND: KEY_CAP_PCT,
    CODE_NO_COVER_AT_STRESS: KEY_STRESS_DROP_PCT, CODE_NET_DEBIT_ABOVE_BUDGET: KEY_MAX_NET_DEBIT,
    CODE_DEBIT_ROLL: KEY_CREDIT_ONLY, CODE_ROLL_DOWN_BELOW_COST: KEY_STRIKE_FLOOR, CODE_STRIKE_BELOW_COST: KEY_STRIKE_FLOOR,
    CODE_CROSSES_EARNINGS: KEY_EARNINGS, CODE_EARNINGS_UNKNOWN: KEY_EARNINGS, CODE_CROSSES_EX_DIVIDEND: KEY_EX_DIVIDEND,
    CODE_DIVIDEND_UNKNOWN: KEY_EX_DIVIDEND,
}
"""Every band and criterion code with its ``structuring.toml`` key (designer Q's codes, S4's keys); an UNPRICED code's
key is :data:`KEY_UNPRICED`."""

BY_EARNINGS_OK: str = "#earnings-ok"
BY_ASSIGN_OK: str = "#assign-ok"
BY_CRITERIA: str = "your structuring.toml"

CAUSE_MARKET_CLOSED: str = "market_closed"
CAUSE_GROUP_QUARANTINED: str = "group_quarantined"
CAUSE_SPOT_UNUSABLE: str = "spot_unusable"
CAUSE_HELD_LEG_UNUSABLE: str = "held_leg_unusable"
CAUSE_EXPIRATIONS_NOT_SERVED: str = "expirations_not_served"
CAUSE_CHAIN_NOT_SERVED: str = "chain_not_served"
CAUSE_REPLACEMENT_NOT_LISTED: str = "replacement_not_listed"
CAUSE_POSITIONS_QUARANTINED: str = "positions_quarantined"
"""Another group of positions.csv is quarantined: its rows are not loaded, so the account's coverage cannot be
counted (reviews L2-1 and L3-1: every structuring answer fails closed while one exists)."""
CAUSE_NO_CANDIDATE_PRICED: str = "no_candidate_priced"
"""Every candidate of the evaluated chains is UNPRICED: no candidate was judged against the owner's criteria, so the
answer is not a complete evaluation (designer Q's D12: unusable data is UNAVAILABLE, never NONE_QUALIFIES)."""
CAUSES: Tuple[str, ...] = (CAUSE_MARKET_CLOSED, CAUSE_GROUP_QUARANTINED, CAUSE_POSITIONS_QUARANTINED,
                           CAUSE_SPOT_UNUSABLE, CAUSE_HELD_LEG_UNUSABLE, CAUSE_EXPIRATIONS_NOT_SERVED,
                           CAUSE_CHAIN_NOT_SERVED, CAUSE_NO_CANDIDATE_PRICED, CAUSE_REPLACEMENT_NOT_LISTED)
"""Why an answer is UNAVAILABLE (designer Q's D12 and S5); every one is exit 20 in the tools."""
NOT_REQUESTED: str = "not requested: an earlier expiration was not served"

NA_STRUCTURE: str = "structure"
NA_INDEX: str = "index_underlying"
NA_DISABLED: str = "disabled"
NA_NO_OPEN_LEGS: str = "no_open_legs"
NA_ACTION: str = "action"
NA_NO_EXPIRATION: str = "no_expiration_in_window"
"""The listed expirations hold no standard monthly inside the section's ``dte`` window: the owner's window selects
nothing to evaluate (NOT_APPLICABLE with the window in words, no report)."""
NOTHING_NOT_SHARES_ONLY: str = "not_shares_only"
NOTHING_FEWER_THAN_100: str = "fewer_than_100_shares"
NOTHING_NO_SHARES: str = "no_shares"
NOTHING_NO_FREE_SHARES: str = "no_free_shares"
"""Designer C's D6: the shares already cover short calls held in another group of the same account (fewer than 100
free shares)."""
NOTHING_NOT_ENOUGH_FREE_SHARES: str = "not_enough_free_shares"
"""Some of the group's shares are free, but short calls held in another group of the account rely on the rest: the
desk proposes no overlay on part of a group's shares (review L3-4)."""
NA_TWO_ROWS_WORDS: str = ("%s holds its %s on more than one row of positions.csv: the desk prices a replacement for a "
                          "leg held on one row")

ERROR_UNKNOWN_TRADE: str = "unknown_trade"
ERROR_POSITIONS_INVALID: str = "positions_invalid"
ERROR_CALENDAR_INVALID: str = "calendar_invalid"

SECOND_EX_DATE_HORIZON_DAYS: int = 80
"""A short call whose last trading day is at least this many calendar days after the calendar's one known ex-date also
gets ``dividend_unknown`` (S4): a second ex-date may fall before it."""
EXPIRING_SECONDS: int = book.EXPIRING_SECONDS
SPOT_ALIGNMENT_SECONDS: int = book.SPOT_ALIGNMENT_SECONDS
CONTRACT_SIZE: int = units.CONTRACT_MULTIPLIER
SCORE_QUANTUM: Decimal = Decimal("0.0001")
PCT_QUANTUM: Decimal = Decimal("0.01")
DECIMAL_DIGITS: int = 28
AMERICAN_PUT_ABS_DELTA: Decimal = Decimal("0.80")
"""A long put at or beyond this ``|delta|`` carries the American caveat (early exercise may be worth more)."""
TICKET_MINUTES: int = 30
GRADE_E_NOTE: str = "quote grade E: the conservative side stands"
MODEL_NOTE: str = ("European Black-Scholes-Merton with a continuous dividend yield on the quotes' mids; US equity "
                   "options are American and early exercise is not modelled; the American lattice is planned for Stage "
                   "2 (not in this release)")
WARNING_Q_ZERO: str = book.WARNING_Q_ZERO

TIE_BREAKS: Dict[str, Tuple[str, ...]] = {
    KIND_COVERED_CALL: ("abs_delta", "expiration", "strike", "contract"),
    KIND_REPLACEMENT_CALL: ("net_credit", "abs_delta", "expiration", "strike", "contract"),
    KIND_PROTECTIVE_PUT: ("strike", "expiration", "contract"),
    KIND_REPLACEMENT_PUT: ("strike", "expiration", "contract"),
    KIND_COLLAR: ("put_strike", "net_debit", "expiration", "contract"),
}
"""The tie-breaks after the objective, in order (designer Q's D5, D7 and D8)."""
_ASCENDING, _DESCENDING = 1, -1
TIE_BREAK_DIRECTIONS: Dict[str, Dict[str, int]] = {
    KIND_COVERED_CALL: {"abs_delta": _ASCENDING, "expiration": _ASCENDING, "strike": _DESCENDING, "contract": _ASCENDING},
    KIND_REPLACEMENT_CALL: {"net_credit": _DESCENDING, "abs_delta": _ASCENDING, "expiration": _ASCENDING,
                            "strike": _DESCENDING, "contract": _ASCENDING},
    KIND_PROTECTIVE_PUT: {"strike": _DESCENDING, "expiration": _DESCENDING, "contract": _ASCENDING},
    KIND_REPLACEMENT_PUT: {"strike": _DESCENDING, "expiration": _DESCENDING, "contract": _ASCENDING},
    KIND_COLLAR: {"put_strike": _DESCENDING, "net_debit": _ASCENDING, "expiration": _DESCENDING, "contract": _ASCENDING},
}
"""``1`` ascending (lower or earlier first, contracts in symbol order), ``-1`` descending (higher or later first)."""

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
_CENT = Decimal("0.01")
_MULTIPLIER = Decimal(CONTRACT_SIZE)
_DECIMAL_CONTEXT = Context(prec=DECIMAL_DIGITS, rounding=ROUND_HALF_EVEN)


class StructuringError(Exception):
    """A desk file the answer needs cannot be used, or the trade is not in ``positions.csv`` (exit 1 in the tools);
    ``code`` is one of :data:`ERROR_UNKNOWN_TRADE`, :data:`ERROR_POSITIONS_INVALID`, :data:`ERROR_CALENDAR_INVALID`."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# --------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reason:
    """Why a candidate is not ELIGIBLE (or an UNPRICED code): the ``structuring.toml`` key, the code, the value the
    desk read and the owner's threshold or band (S4)."""

    key: str
    code: str
    value: Any
    threshold: Any

    def as_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "code": self.code, "value": self.value, "threshold": self.threshold}


@dataclass(frozen=True)
class Accepted:
    """An event label the owner accepted: by ``#earnings-ok``, ``#assign-ok`` or ``your structuring.toml``."""

    key: str
    code: str
    by: str
    value: Any = None
    threshold: Any = None

    def as_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "code": self.code, "by": self.by, "value": self.value, "threshold": self.threshold}


LEG_KEYS: Tuple[str, ...] = ("occ", "root", "right", "strike", "expiration", "last_trading_day", "dte_calendar",
                             "dte_trading", "bid", "ask", "mid", "spread_pct", "grade", "quote_status", "quote_age_min",
                             "as_of", "tau_years", "iv", "delta", "abs_delta", "greeks", "unpriced")


@dataclass(frozen=True)
class PricedLeg:
    """One new leg as the desk graded and priced it (module notes). ``unpriced`` is the one UNPRICED reason, else
    ``None``; prices keep at least two decimals (``3.50``) and every digit the vendor sent (``3.425``)."""

    occ: str
    root: str
    right: str
    strike: Decimal
    expiration: datetime.date
    last_trading_day: Optional[datetime.date] = None
    dte_calendar: Optional[int] = None
    dte_trading: Optional[int] = None
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    mid: Optional[Decimal] = None
    spread_pct: Optional[Decimal] = None
    grade: Optional[str] = None
    quote_status: Optional[str] = None
    quote_age_min: Optional[int] = None
    as_of: Optional[datetime.datetime] = None
    tau_years: Optional[float] = None
    iv: Optional[float] = None
    delta: Optional[float] = None
    abs_delta: Optional[Decimal] = None
    greeks: Optional[Dict[str, float]] = None
    unpriced: Optional[Reason] = None

    def as_dict(self) -> Dict[str, Any]:
        out = {key: getattr(self, key) for key in LEG_KEYS}
        out["unpriced"] = None if self.unpriced is None else self.unpriced.code
        return out


HELD_KEYS: Tuple[str, ...] = ("row_number", "occ", "root", "right", "side", "contracts", "strike", "expiration",
                              "last_trading_day", "bid", "ask", "mid", "spread_pct", "grade", "quote_status",
                              "quote_age_min", "as_of", "iv", "delta", "entry_price_per_share", "remaining_cost_basis",
                              "close_price", "close_side", "note", "realized")


@dataclass(frozen=True)
class HeldLeg:
    """The held option leg a replacement closes: its quote, the conservative side it closes at (``close_price``, the
    fresh ask of a short leg or the fresh bid of a long one; ``None`` when that side is not fresh) and the realized P/L
    at that price (``pnl.realized_for_row``'s arithmetic on the open quantity)."""

    row_number: int
    occ: str
    root: str
    right: str
    side: str
    contracts: int
    strike: Decimal
    expiration: datetime.date
    last_trading_day: datetime.date
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    mid: Optional[Decimal]
    spread_pct: Optional[Decimal]
    grade: Optional[str]
    quote_status: Optional[str]
    quote_age_min: Optional[int]
    as_of: Optional[datetime.datetime]
    iv: Optional[float]
    delta: Optional[float]
    entry_price_per_share: Decimal
    remaining_cost_basis: Decimal
    close_price: Optional[Decimal]
    close_side: str
    note: Optional[str]
    realized: Optional[Decimal]

    @property
    def usable(self) -> bool:
        return self.close_price is not None

    def as_dict(self) -> Dict[str, Any]:
        return {key: getattr(self, key) for key in HELD_KEYS}


@dataclass(frozen=True)
class BeforeState:
    """The group as it stands (the group-scoped book): net delta in share-equivalents (``None`` when an option leg has
    no Greeks), max loss, max profit (``None``: unbounded) and the breakeven."""

    group_delta: Optional[float]
    max_loss: Optional[Decimal]
    max_profit: Optional[Decimal]
    breakeven: Optional[Decimal]
    unbounded_up: bool

    def as_dict(self) -> Dict[str, Any]:
        return {"group_delta": self.group_delta, "max_loss": self.max_loss, "max_profit": self.max_profit,
                "breakeven": self.breakeven, "unbounded_up": self.unbounded_up}


@dataclass(frozen=True)
class AfterState:
    """The group after the candidate's fills (module notes): money to the cent, the breakevens in price form."""

    group_delta: Optional[float]
    delta_change: Optional[float]
    max_loss: Optional[Decimal]
    max_profit: Optional[Decimal]
    breakevens: Tuple[Decimal, ...]
    unbounded_up: bool
    realized_on_close: Optional[Decimal]

    @property
    def breakeven(self) -> Optional[Decimal]:
        return self.breakevens[0] if self.breakevens else None

    def as_dict(self) -> Dict[str, Any]:
        return {"group_delta": self.group_delta, "delta_change": self.delta_change, "max_loss": self.max_loss,
                "max_profit": self.max_profit, "breakeven": self.breakeven, "breakevens": list(self.breakevens),
                "unbounded_up": self.unbounded_up, "realized_on_close": self.realized_on_close}


CANDIDATE_KEYS: Tuple[str, ...] = ("rank", "id", "kind", "expiration", "contracts", "status", "score", "reasons",
                                   "accepted", "lost_because", "american_caveat", "extrinsic_bid", "net_credit",
                                   "net_credit_total", "credit_total", "floor_pct", "cap_pct", "covered_per_share",
                                   "covered_total", "net_debit", "net_debit_total", "cost_total", "legs", "after")


@dataclass(frozen=True)
class Candidate:
    """One candidate: a single leg, or a collar's (put, call) pair (``id`` ``<put OCC>+<call OCC>``). ``score`` is the
    objective's value (``None`` when unpriced, or for a put with no cover at the stress); the metrics a kind does not
    have are ``None``; ``rank`` and ``lost_because`` are set by :func:`rank_candidates`."""

    id: str
    kind: str
    expiration: datetime.date
    legs: Tuple[PricedLeg, ...]
    contracts: int
    score: Optional[Decimal]
    status: str
    reasons: Tuple[Reason, ...] = ()
    accepted: Tuple[Accepted, ...] = ()
    american_caveat: bool = False
    after: Optional[AfterState] = None
    extrinsic_bid: Optional[Decimal] = None
    net_credit: Optional[Decimal] = None
    net_credit_total: Optional[Decimal] = None
    credit_total: Optional[Decimal] = None
    floor_pct: Optional[Decimal] = None
    cap_pct: Optional[Decimal] = None
    covered_per_share: Optional[Decimal] = None
    covered_total: Optional[Decimal] = None
    net_debit: Optional[Decimal] = None
    net_debit_total: Optional[Decimal] = None
    cost_total: Optional[Decimal] = None
    rank: int = 0
    lost_because: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key in CANDIDATE_KEYS:
            value = getattr(self, key)
            if key in ("reasons", "accepted", "legs"):
                value = [item.as_dict() for item in value]
            elif key == "after":
                value = None if value is None else value.as_dict()
            out[key] = value
        return out


# --------------------------------------------------------------------------------------------
# Expirations
# --------------------------------------------------------------------------------------------


def third_friday(day: datetime.date) -> datetime.date:
    """The third Friday of ``day``'s month: the first Friday plus 14 days."""
    first = day.replace(day=1)
    return first + datetime.timedelta(days=(4 - first.weekday()) % 7 + 14)


def is_standard_monthly(day: datetime.date, extra_holidays: market_clock.ExtraHolidays = ()) -> bool:
    """``day`` is its month's standard expiration: the settlement day of the third Friday (a holiday Friday moves it
    to the Thursday, as ``market_clock.settlement_day`` does)."""
    return day == market_clock.settlement_day(third_friday(day), False, extra_holidays)


def candidate_expirations(dates: Iterable[datetime.date], today: datetime.date, window: Tuple[int, int], *,
                          later_than: Optional[datetime.date] = None,
                          extra_holidays: market_clock.ExtraHolidays = ()) -> List[datetime.date]:
    """The listed standard monthlies whose calendar DTE (to the last trading day) lies in ``window`` (inclusive) and,
    for a roll, that expire after ``later_than``; ascending and de-duplicated."""
    low, high = window
    out = set()
    for day in dates:
        if not is_standard_monthly(day, extra_holidays):
            continue
        if later_than is not None and day <= later_than:
            continue
        days = market_clock.dte(day, today, market_clock.UNIT_CALENDAR, False, extra_holidays)
        if low <= days <= high:
            out.add(day)
    return sorted(out)


# --------------------------------------------------------------------------------------------
# Objectives and the numbers they read (Decimal, 28 digits, half-even)
# --------------------------------------------------------------------------------------------


def price_value(value: Optional[Decimal]) -> Optional[Decimal]:
    """A price as the desk prints it: at least two decimals (``3.5`` -> ``3.50``), every decimal the vendor sent kept."""
    if value is None:
        return None
    exponent = value.as_tuple().exponent
    return value.quantize(_CENT) if isinstance(exponent, int) and exponent > -2 else value


def abs_delta_2dp(delta: float) -> Decimal:
    """``|delta|`` through its ``repr`` at 2 dp half-even: the reading the exit rules compare."""
    return abs(Decimal(repr(delta))).quantize(PCT_QUANTUM, rounding=ROUND_HALF_EVEN)


def extrinsic(bid: Decimal, spot: Decimal, strike: Decimal) -> Decimal:
    """The bid's time value of a call: ``bid - max(0, S - K)`` (negative when the bid is below intrinsic)."""
    with localcontext(_DECIMAL_CONTEXT):
        return bid - max(_ZERO, spot - strike)


def extrinsic_per_day(bid: Decimal, spot: Decimal, strike: Decimal, days: int) -> Decimal:
    """``extrinsic_per_day``: ``E / S / T_c x 100`` quantized 0.0001 half-even (higher is better)."""
    if days <= 0 or spot <= 0:
        raise ValueError("extrinsic_per_day needs a positive spot and a positive day count")
    with localcontext(_DECIMAL_CONTEXT):
        return (extrinsic(bid, spot, strike) / spot / Decimal(days) * _HUNDRED).quantize(SCORE_QUANTUM,
                                                                                        rounding=ROUND_HALF_EVEN)


def stress_spot(spot: Decimal, drop_pct: Decimal) -> Decimal:
    """The owner's stressed price ``S x (1 - x / 100)``."""
    with localcontext(_DECIMAL_CONTEXT):
        return spot * (Decimal(1) - drop_pct / _HUNDRED)


def stress_cost_per_day(ask: Decimal, strike: Decimal, stressed: Decimal, days: int) -> Optional[Decimal]:
    """``stress_cost_per_day``: ``ask / T_c / (K - S_x) x 100`` quantized 0.0001 half-even (lower is better); ``None``
    when the put covers nothing at the stress (``K <= S_x``)."""
    if days <= 0:
        raise ValueError("stress_cost_per_day needs a positive day count")
    with localcontext(_DECIMAL_CONTEXT):
        cover = strike - stressed
        if cover <= 0:
            return None
        return (ask / Decimal(days) / cover * _HUNDRED).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)


def floor_pct(spot: Decimal, strike: Decimal) -> Decimal:
    """The put's floor below the share price: ``(S - K) / S x 100`` at 2 dp half-even."""
    with localcontext(_DECIMAL_CONTEXT):
        return ((spot - strike) / spot * _HUNDRED).quantize(PCT_QUANTUM, rounding=ROUND_HALF_EVEN)


def cap_pct(spot: Decimal, strike: Decimal) -> Decimal:
    """The call's cap above the share price: ``(Kc - S) / S x 100`` at 2 dp half-even (``cap_within_budget``)."""
    with localcontext(_DECIMAL_CONTEXT):
        return ((strike - spot) / spot * _HUNDRED).quantize(PCT_QUANTUM, rounding=ROUND_HALF_EVEN)


def _money(value: Decimal) -> Decimal:
    return payoff.quantize_money(value)


# --------------------------------------------------------------------------------------------
# Contexts
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PricingContext:
    """What pricing a new leg reads: the instant and session (calendar-aware), the freshness bound, the rate, the
    dividend yield (``None``: q = 0), the underlying's graded quote (its ``mark_mid`` is the spot) and the owner's
    maximum spread."""

    now: datetime.datetime
    today: datetime.date
    session: SessionInfo
    prior_close_utc: datetime.datetime
    bound_min: int
    rate: float
    dividend_yield: Optional[Decimal]
    spot: QuoteQC
    underlying: str
    max_spread_pct: Decimal
    extra_holidays: Mapping[datetime.date, str] = field(default_factory=dict)

    @property
    def q(self) -> float:
        return 0.0 if self.dividend_yield is None else float(self.dividend_yield)

    @property
    def spot_mid(self) -> Optional[Decimal]:
        return self.spot.mark_mid if self.spot.usable_for_rules else None


@dataclass(frozen=True)
class GroupContext:
    """The target group as the criteria read it: the open long shares, their remaining cost, the tags of any open row
    (``#earnings-ok``, ``#assign-ok``) and the before-state."""

    trade_id: str
    group_id: str
    underlying: str
    account: str
    structure: str
    shares: Decimal
    stock_cost: Decimal
    earnings_ok: bool
    assign_ok: bool
    before: BeforeState

    @property
    def s0(self) -> Decimal:
        """The stock cost per share (exact): the strike floor ``cost_basis`` compares strikes with it."""
        with localcontext(_DECIMAL_CONTEXT):
            return self.stock_cost / self.shares if self.shares > 0 else _ZERO

    @property
    def contracts(self) -> int:
        """Whole contracts the shares cover: ``floor(n / 100)``."""
        return int(self.shares // _MULTIPLIER)


@dataclass(frozen=True)
class EventContext:
    """What the short-call event tests read: the symbol, the calendar (``None``: none on the desk), the instant and the
    calendar's full closures."""

    symbol: str
    calendar: Optional[CalendarFile]
    now: datetime.datetime
    today: datetime.date
    extra_holidays: Mapping[datetime.date, str] = field(default_factory=dict)


# --------------------------------------------------------------------------------------------
# Pricing a new leg (designer Q's D4)
# --------------------------------------------------------------------------------------------


def _age(qc: QuoteQC) -> Optional[int]:
    ages = [age for age in (qc.age_min_bid, qc.age_min_ask) if age is not None]
    return max(ages) if ages else None


def _quote_reason(qc: QuoteQC) -> Optional[Reason]:
    """The UNPRICED reason of the quote itself (its grade and freshness), or ``None`` when it is usable with a mid."""
    def reason(code: str, value: Any = None, threshold: Any = None) -> Reason:
        return Reason(KEY_UNPRICED, code, value, threshold)

    if qc.status == quote_qc.STATUS_CLOSED_DAY:
        return reason(CODE_MARKET_CLOSED, qc.label)
    if qc.status == quote_qc.STATUS_MISSING:
        return reason(CODE_QUOTE_MISSING)
    if qc.status == quote_qc.STATUS_UNDATED:
        return reason(CODE_QUOTE_UNDATED)
    if qc.status == quote_qc.STATUS_STALE:
        return reason(CODE_QUOTE_STALE, _age(qc))
    if qc.grade == quote_qc.GRADE_F:
        return reason(CODE_QUOTE_CROSSED, price_value(qc.bid), price_value(qc.ask))
    if qc.grade == quote_qc.GRADE_E:
        if qc.bid is not None and qc.bid == 0:
            return reason(CODE_ZERO_BID, price_value(qc.bid))
        return reason(CODE_QUOTE_WIDE, qc.spread_pct, quote_qc.WIDE_SPREAD_PCT)
    if qc.status == quote_qc.STATUS_ONE_SIDE_STALE or qc.mark_mid is None:
        return reason(CODE_QUOTE_ONE_SIDE_STALE, _age(qc))
    return None


def price_contract(quote: Quote, ctx: PricingContext) -> PricedLeg:
    """Grade and price one new leg (module notes, "Pricing and quote QC"); never raises on a quote's state."""
    right = (quote.option_type or "").upper()
    if right not in (bsm.CALL, bsm.PUT) or quote.strike is None or quote.expiration_date is None:
        raise ValueError("price_contract() takes an option quote, got %r" % (quote.symbol,))
    strike = expiry.plain_decimal(quote.strike)
    expiration = quote.expiration_date
    extra = ctx.extra_holidays
    qc = quote_qc.assess(quote, now=ctx.now, session=ctx.session, bound_min=ctx.bound_min,
                         prior_close_utc=ctx.prior_close_utc)
    leg = PricedLeg(
        occ=quote.symbol.strip().upper(), root=(quote.root_symbol or ctx.underlying).strip().upper(), right=right,
        strike=strike, expiration=expiration,
        last_trading_day=market_clock.last_trading_day(expiration, False, extra),
        dte_calendar=market_clock.dte(expiration, ctx.today, market_clock.UNIT_CALENDAR, False, extra),
        dte_trading=market_clock.dte(expiration, ctx.today, market_clock.UNIT_TRADING, False, extra),
        bid=price_value(qc.bid), ask=price_value(qc.ask), mid=price_value(qc.mark_mid), spread_pct=qc.spread_pct,
        grade=qc.grade, quote_status=qc.status, quote_age_min=_age(qc), as_of=qc.as_of)
    unpriced = _quote_reason(qc)
    if unpriced is None and (quote.contract_size != CONTRACT_SIZE or leg.root != ctx.underlying
                             or (quote.underlying or ctx.underlying).strip().upper() != ctx.underlying):
        unpriced = Reason(KEY_UNPRICED, CODE_NONSTANDARD_CONTRACT, quote.contract_size, CONTRACT_SIZE)
    spot = ctx.spot_mid
    if unpriced is None and spot is None:
        unpriced = Reason(KEY_UNPRICED, CODE_SPOT_UNAVAILABLE, ctx.spot.grade, None)
    if unpriced is None and (qc.as_of is None or ctx.spot.as_of is None
                             or abs((ctx.spot.as_of - qc.as_of).total_seconds()) > SPOT_ALIGNMENT_SECONDS):
        apart = None if qc.as_of is None or ctx.spot.as_of is None else abs((ctx.spot.as_of - qc.as_of).total_seconds())
        unpriced = Reason(KEY_UNPRICED, CODE_SPOT_UNALIGNED, apart, SPOT_ALIGNMENT_SECONDS)
    seconds = None
    if qc.as_of is not None:
        seconds = (market_clock.exercise_cutoff_utc(expiration, False, None, extra) - qc.as_of).total_seconds()
        leg = replace(leg, tau_years=seconds / units.SECONDS_PER_YEAR)
    if unpriced is None and (seconds is None or seconds < EXPIRING_SECONDS or (leg.dte_calendar or 0) <= 0):
        unpriced = Reason(KEY_UNPRICED, CODE_EXPIRING, seconds, EXPIRING_SECONDS)  # no per-day objective at T_c = 0
    if unpriced is not None:
        return replace(leg, unpriced=unpriced)
    assert spot is not None and qc.mark_mid is not None and leg.tau_years is not None
    result = iv.implied_vol(float(qc.mark_mid), right, float(spot), float(strike), leg.tau_years, ctx.rate, ctx.q)
    if not result.ok or result.sigma is None:
        return replace(leg, unpriced=Reason(KEY_UNPRICED, IV_CODE_PREFIX + result.status, leg.mid, None))
    values = greeks_module.european_greeks(right, float(spot), float(strike), leg.tau_years, ctx.rate, ctx.q,
                                           result.sigma)
    return replace(leg, iv=result.sigma, delta=values.delta, abs_delta=abs_delta_2dp(values.delta),
                   greeks={"delta": values.delta, "gamma": values.gamma, "theta": values.theta, "vega": values.vega,
                           "rho": values.rho})


# --------------------------------------------------------------------------------------------
# The held leg a replacement closes (its conservative fresh side)
# --------------------------------------------------------------------------------------------


def held_leg(row: PositionRow, qc: Optional[QuoteQC], record: Optional[Mapping[str, Any]],
             extra_holidays: Optional[Mapping[datetime.date, str]] = None) -> HeldLeg:
    """The :class:`HeldLeg` of an open option row: its quote (the book's leg record supplies the IV and delta) and the
    conservative side it closes at (module notes). ``ValueError`` for a stock row or a fractional quantity."""
    if not row.is_option or row.occ is None:
        raise ValueError("row %d is not an option row" % row.row_number)
    quantity = abs(row.open_quantity)
    if quantity != quantity.to_integral_value() or quantity <= 0:
        raise ValueError("row %d holds %s contracts: the desk counts whole contracts" % (row.row_number, row.open_quantity))
    short = row.is_short
    close: Optional[Decimal] = None
    note: Optional[str] = None
    if qc is not None and qc.grade != quote_qc.GRADE_F:
        side = qc.mark_liq_short if short else qc.mark_liq_long
        if side is not None and (side > 0 or not short):
            close = price_value(side)
            if qc.grade == quote_qc.GRADE_E:
                note = GRADE_E_NOTE
    realized = None
    if close is not None:
        realized = _money(row.sign * quantity * row.multiplier * close - row.remaining_cost_basis)
    record = record or {}
    greeks_record = record.get("greeks") or {}
    return HeldLeg(
        row_number=row.row_number, occ=row.occ.compact, root=row.occ.root, right=row.occ.right,
        side=positions_csv.SIDE_SHORT if short else positions_csv.SIDE_LONG, contracts=int(quantity),
        strike=expiry.plain_decimal(row.occ.strike), expiration=row.occ.expiration,
        last_trading_day=market_clock.last_trading_day(row.occ.expiration, False, extra_holidays or {}),
        bid=None if qc is None else price_value(qc.bid), ask=None if qc is None else price_value(qc.ask),
        mid=None if qc is None else price_value(qc.mark_mid), spread_pct=None if qc is None else qc.spread_pct,
        grade=None if qc is None else qc.grade, quote_status=None if qc is None else qc.status,
        quote_age_min=None if qc is None else _age(qc), as_of=None if qc is None else qc.as_of,
        iv=record.get("iv"), delta=greeks_record.get("delta"), entry_price_per_share=row.entry_price_per_share,
        remaining_cost_basis=row.remaining_cost_basis, close_price=close, close_side="ask" if short else "bid",
        note=note, realized=realized)


# --------------------------------------------------------------------------------------------
# Events for short calls (designer Q's D9 with S4) and acceptance
# --------------------------------------------------------------------------------------------


def _earnings_reason(ctx: EventContext, start: datetime.datetime, end: datetime.datetime) -> Optional[Reason]:
    def unknown(status: str, day: Optional[datetime.date] = None) -> Reason:
        return Reason(KEY_EARNINGS, CODE_EARNINGS_UNKNOWN, {"status": status, "date": day}, None)

    if ctx.calendar is None:
        return unknown("no_calendar")
    events = ctx.calendar.events_for(ctx.symbol)
    if not events.listed:
        return unknown("unlisted")
    entry = events.earnings
    if entry is None:
        return unknown("absent")
    if entry.status == calendar_events.STATUS_NONE:
        return None
    if entry.status not in calendar_events.DATED_STATUSES or entry.date is None:
        return unknown(entry.status, entry.date)
    clock = market_clock.REGULAR_CLOSE_ET if entry.time == calendar_events.TIME_POST else market_clock.REGULAR_OPEN_ET
    instant = market_clock.from_et(datetime.datetime.combine(entry.date, clock))
    if instant <= ctx.now:
        return unknown("passed", entry.date)
    # (start, end]: a release after the close of a last trading day belongs to the call expiring that day (its holders
    # can still exercise after the move, until the OCC 17:30 ET cutoff), so it is inside the new call's life when it
    # is on the new last trading day, and the held call's when it is on the held one
    if start < instant <= end:
        return Reason(KEY_EARNINGS, CODE_CROSSES_EARNINGS, {"date": entry.date, "time": entry.time, "status": entry.status},
                      {"from": start, "to": end})
    return None


def _dividend_status(ctx: EventContext) -> Tuple[str, Optional[datetime.date]]:
    """Why the dividend is not known, in the reason's words (``expiry.dividend_window`` decides that it is not)."""
    if ctx.calendar is None:
        return "no_calendar", None
    events = ctx.calendar.events_for(ctx.symbol)
    if not events.listed:
        return "unlisted", None
    entry = events.ex_dividend
    if entry is None:
        return "absent", None
    if entry.status == calendar_events.STATUS_UNKNOWN or entry.date is None or entry.amount is None:
        return entry.status, entry.date
    return "passed", entry.date


def short_call_event_reasons(ctx: EventContext, *, new_expiration: datetime.date,
                             held_expiration: Optional[datetime.date]) -> List[Reason]:
    """The earnings and ex-dividend labels of a short call expiring ``new_expiration`` (module notes, "Events"): a fresh
    call when ``held_expiration`` is ``None``, else the replacement of a call expiring then."""
    extra = ctx.extra_holidays
    end = market_clock.exercise_cutoff_utc(new_expiration, False, None, extra)
    start = ctx.now if held_expiration is None else market_clock.exercise_cutoff_utc(held_expiration, False, None, extra)
    reasons: List[Reason] = []
    earnings = _earnings_reason(ctx, start, end)
    if earnings is not None:
        reasons.append(earnings)
    ltd_new = market_clock.last_trading_day(new_expiration, False, extra)
    window = expiry.dividend_window(ctx.symbol, ltd_new, ctx.calendar, ctx.today, extra)
    if not window.known:
        status, day = _dividend_status(ctx)
        reasons.append(Reason(KEY_EX_DIVIDEND, CODE_DIVIDEND_UNKNOWN, {"status": status, "ex_date": day}, None))
        return reasons
    if window.applicable:
        held_applies = False
        if held_expiration is not None:
            ltd_old = market_clock.last_trading_day(held_expiration, False, extra)
            old = expiry.dividend_window(ctx.symbol, ltd_old, ctx.calendar, ctx.today, extra)
            held_applies = old.known and old.applicable
        if not held_applies:
            reasons.append(Reason(KEY_EX_DIVIDEND, CODE_CROSSES_EX_DIVIDEND,
                                  {"ex_date": window.ex_date, "cum_day": window.cum_day, "amount": window.amount,
                                   "status": window.status}, {"last_trading_day": ltd_new}))
    if window.ex_date is not None and (ltd_new - window.ex_date).days >= SECOND_EX_DATE_HORIZON_DAYS:
        reasons.append(Reason(KEY_EX_DIVIDEND, CODE_DIVIDEND_UNKNOWN, {"status": "second_ex_date",
                                                                      "ex_date": window.ex_date},
                              SECOND_EX_DATE_HORIZON_DAYS))
    return reasons


def split_accepted(reasons: Sequence[Reason], *, earnings_ok: bool, assign_ok: bool, earnings_policy: str,
                   ex_dividend_policy: str) -> Tuple[List[Reason], List[Accepted]]:
    """``(kept, accepted)``: an earnings label is accepted by ``#earnings-ok`` or ``earnings = "allow"``, a dividend
    label by ``#assign-ok`` or ``ex_dividend = "allow"`` (the row tag named first); every other reason is kept."""
    kept: List[Reason] = []
    accepted: List[Accepted] = []
    for reason in reasons:
        by: Optional[str] = None
        if reason.key == KEY_EARNINGS:
            by = BY_EARNINGS_OK if earnings_ok else (BY_CRITERIA if earnings_policy == "allow" else None)
        elif reason.key == KEY_EX_DIVIDEND:
            by = BY_ASSIGN_OK if assign_ok else (BY_CRITERIA if ex_dividend_policy == "allow" else None)
        if by is None:
            kept.append(reason)
        else:
            accepted.append(Accepted(reason.key, reason.code, by, reason.value, reason.threshold))
    return kept, accepted


def status_of(reasons: Iterable[Reason]) -> str:
    """The status a candidate's reasons give (:data:`STATUS_PRECEDENCE`)."""
    keys = {reason.key for reason in reasons}
    if KEY_UNPRICED in keys:
        return STATUS_UNPRICED
    if keys & set(BAND_KEYS):
        return STATUS_OUTSIDE_BANDS
    if keys:
        return STATUS_FAILS_CRITERION
    return STATUS_ELIGIBLE


def _ordered(reasons: Iterable[Reason]) -> Tuple[Reason, ...]:
    """Reasons in :data:`REASON_KEY_ORDER` (a stable sort: two reasons of one key keep their order)."""
    return tuple(sorted(reasons, key=lambda reason: REASON_KEY_ORDER.index(reason.key)))


# --------------------------------------------------------------------------------------------
# Bands (OUTSIDE_BANDS) and the after-state
# --------------------------------------------------------------------------------------------


def _band(reasons: List[Reason], key: str, code: str, value: Any, low: Any, high: Any) -> None:
    if value is None or not low <= value <= high:
        reasons.append(Reason(key, code, value, [low, high]))


def _common_bands(reasons: List[Reason], leg: PricedLeg, section: Any, ctx: PricingContext) -> None:
    _band(reasons, KEY_DTE, CODE_DTE_OUTSIDE_WINDOW, leg.dte_calendar, section.dte[0], section.dte[1])
    if leg.spread_pct is None or leg.spread_pct > ctx.max_spread_pct:
        reasons.append(Reason(KEY_MAX_SPREAD_PCT, CODE_SPREAD_ABOVE_MAX, leg.spread_pct, ctx.max_spread_pct))


def _call_bands(reasons: List[Reason], leg: PricedLeg, section: Any, ctx: PricingContext) -> None:
    _common_bands(reasons, leg, section, ctx)
    _band(reasons, KEY_ABS_DELTA, CODE_DELTA_OUTSIDE_BAND, leg.abs_delta, section.abs_delta[0], section.abs_delta[1])
    if leg.bid is None or leg.bid < section.min_credit:
        reasons.append(Reason(KEY_MIN_CREDIT, CODE_CREDIT_BELOW_MINIMUM, leg.bid, section.min_credit))


def _profile(shares: Decimal, options: Sequence[Tuple[str, Decimal, int]],
             economic_cost: Decimal) -> Tuple[Optional[Decimal], Optional[Decimal], Tuple[Decimal, ...], bool]:
    """``(max_loss, max_profit, breakevens, unbounded_up)`` of ``shares`` plus option legs ``(right, strike, signed
    contracts)`` at one expiry against the economic net cost: the payoff is linear between the kinks 0 and the strikes,
    so its extremes and zero crossings are exact (module notes, "After-state")."""
    with localcontext(_DECIMAL_CONTEXT):
        strikes = sorted({strike for _right, strike, _count in options})
        kinks = [_ZERO] + [strike for strike in strikes if strike > 0]

        def value(spot: Decimal) -> Decimal:
            total = shares * spot - economic_cost
            for right, strike, count in options:
                total += Decimal(count) * _MULTIPLIER * payoff.intrinsic(right, strike, spot)
            return total

        values = [value(spot) for spot in kinks]
        slope = shares + sum((Decimal(count) * _MULTIPLIER for right, _strike, count in options if right == "CALL"), _ZERO)
        unbounded_up, unbounded_down = slope > 0, slope < 0
        max_profit = None if unbounded_up else _money(max(values))
        max_loss = None if unbounded_down else _money(max(_ZERO, -min(values)))
        crossings: List[Decimal] = []
        for (x0, y0), (x1, y1) in zip(zip(kinks, values), zip(kinks[1:], values[1:])):
            if y0 == 0:
                if x0 > 0:
                    crossings.append(x0)
            elif (y0 < 0 < y1) or (y0 > 0 > y1):
                crossings.append(x0 + (x1 - x0) * (-y0) / (y1 - y0))
        last_x, last_y = kinks[-1], values[-1]
        if last_y == 0:
            if last_x > 0:
                crossings.append(last_x)
        elif slope != 0 and ((last_y < 0 < slope) or (last_y > 0 > slope)):
            crossings.append(last_x - last_y / slope)
        return max_loss, max_profit, tuple(sorted({payoff.quantize_price(x) for x in crossings})), unbounded_up


def after_state(group: GroupContext, contracts: int, new_legs: Sequence[Tuple[PricedLeg, int, Decimal]],
                held: Optional[HeldLeg]) -> AfterState:
    """The group after opening ``new_legs`` (``(leg, signed contracts, fill price)``: a short call ``-c`` at its bid,
    a long put ``+c`` at its ask) and closing ``held`` at its conservative side (module notes, "After-state")."""
    with localcontext(_DECIMAL_CONTEXT):
        premiums = sum((Decimal(count) * _MULTIPLIER * price for _leg, count, price in new_legs), _ZERO)
        realized = held.realized if held is not None and held.realized is not None else _ZERO
        economic = group.stock_cost + premiums - realized
        options = [(leg.right, leg.strike, count) for leg, count, _price in new_legs]
        max_loss, max_profit, breakevens, unbounded_up = _profile(group.shares, options, economic)
    delta: Optional[float] = float(group.shares)
    for leg, count, _price in new_legs:
        if leg.delta is None or delta is None:
            delta = None
            break
        delta += count * CONTRACT_SIZE * leg.delta
    change = None
    if delta is not None and group.before.group_delta is not None:
        change = delta - group.before.group_delta
    return AfterState(group_delta=delta, delta_change=change, max_loss=max_loss, max_profit=max_profit,
                      breakevens=breakevens, unbounded_up=unbounded_up,
                      realized_on_close=None if held is None else held.realized)


def _short_call_caveat(leg: PricedLeg, spot: Decimal, events: EventContext) -> bool:
    """A short call in the money by at least 0.01 whose life holds a known cum-dividend day and whose bid-side
    extrinsic is below that dividend (the assignment watch's dividend test): early exercise may come first."""
    if leg.bid is None or spot < leg.strike + _CENT:
        return False
    window = expiry.dividend_window(events.symbol, leg.last_trading_day or leg.expiration, events.calendar,
                                    events.today, events.extra_holidays)
    if not (window.known and window.applicable and window.amount is not None):
        return False
    return extrinsic(leg.bid, spot, leg.strike) < window.amount


def _put_caveat(leg: PricedLeg) -> bool:
    return leg.abs_delta is not None and leg.abs_delta >= AMERICAN_PUT_ABS_DELTA


# --------------------------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------------------------


def _unpriced(kind: str, legs: Sequence[PricedLeg], contracts: int, reason: Reason, **metrics: Any) -> Candidate:
    return Candidate(id="+".join(leg.occ for leg in legs), kind=kind, expiration=legs[0].expiration, legs=tuple(legs),
                     contracts=contracts, score=None, status=STATUS_UNPRICED, reasons=(reason,), **metrics)


def _finish(kind: str, legs: Sequence[PricedLeg], contracts: int, score: Optional[Decimal], reasons: List[Reason],
            accepted: Sequence[Accepted], caveat: bool, after: AfterState, **metrics: Any) -> Candidate:
    ordered = _ordered(reasons)
    return Candidate(id="+".join(leg.occ for leg in legs), kind=kind, expiration=legs[0].expiration, legs=tuple(legs),
                     contracts=contracts, score=score, status=status_of(ordered), reasons=ordered,
                     accepted=tuple(accepted), american_caveat=caveat, after=after, **metrics)


def _call_events(leg: PricedLeg, group: GroupContext, section: Any, events: EventContext,
                 held: Optional[HeldLeg]) -> Tuple[List[Reason], List[Accepted]]:
    reasons = short_call_event_reasons(events, new_expiration=leg.expiration,
                                       held_expiration=None if held is None else held.expiration)
    return split_accepted(reasons, earnings_ok=group.earnings_ok, assign_ok=group.assign_ok,
                          earnings_policy=section.earnings, ex_dividend_policy=section.ex_dividend)


def covered_call_candidate(leg: PricedLeg, ctx: PricingContext, group: GroupContext, section: Any,
                           events: EventContext) -> Candidate:
    """A fresh covered call on the group's shares, ``floor(n / 100)`` contracts, under ``[covered_call]``."""
    contracts = group.contracts
    if leg.unpriced is not None:
        return _unpriced(KIND_COVERED_CALL, [leg], contracts, leg.unpriced)
    spot = ctx.spot_mid
    assert spot is not None and leg.bid is not None and leg.dte_calendar is not None
    reasons: List[Reason] = []
    _call_bands(reasons, leg, section, ctx)
    if section.strike_floor == "cost_basis" and leg.strike < group.s0:
        reasons.append(Reason(KEY_STRIKE_FLOOR, CODE_STRIKE_BELOW_COST, leg.strike, payoff.quantize_price(group.s0)))
    kept, accepted = _call_events(leg, group, section, events, None)
    after = after_state(group, contracts, [(leg, -contracts, leg.bid)], None)
    return _finish(KIND_COVERED_CALL, [leg], contracts, extrinsic_per_day(leg.bid, spot, leg.strike, leg.dte_calendar),
                   reasons + kept, accepted, _short_call_caveat(leg, spot, events), after,
                   extrinsic_bid=extrinsic(leg.bid, spot, leg.strike),
                   credit_total=_money(leg.bid * _MULTIPLIER * contracts))


def replacement_call_candidate(leg: PricedLeg, ctx: PricingContext, group: GroupContext, held: HeldLeg, section: Any,
                               events: EventContext) -> Candidate:
    """A replacement for the held short call under ``[roll]``: the held call closed at its fresh ask, this one opened at
    its fresh bid, ``|held contracts|`` each (designer Q's D6)."""
    contracts = held.contracts
    if leg.unpriced is not None:
        return _unpriced(KIND_REPLACEMENT_CALL, [leg], contracts, leg.unpriced)
    spot = ctx.spot_mid
    assert spot is not None and leg.bid is not None and leg.dte_calendar is not None and held.close_price is not None
    reasons: List[Reason] = []
    _call_bands(reasons, leg, section, ctx)
    net = leg.bid - held.close_price
    if section.credit_only and net < 0:
        reasons.append(Reason(KEY_CREDIT_ONLY, CODE_DEBIT_ROLL, net, _ZERO))
    if section.strike_floor == "cost_basis" and leg.strike < held.strike and leg.strike < group.s0:
        reasons.append(Reason(KEY_STRIKE_FLOOR, CODE_ROLL_DOWN_BELOW_COST, leg.strike, payoff.quantize_price(group.s0)))
    kept, accepted = _call_events(leg, group, section, events, held)
    after = after_state(group, contracts, [(leg, -contracts, leg.bid)], held)
    return _finish(KIND_REPLACEMENT_CALL, [leg], contracts, extrinsic_per_day(leg.bid, spot, leg.strike, leg.dte_calendar),
                   reasons + kept, accepted, _short_call_caveat(leg, spot, events), after,
                   extrinsic_bid=extrinsic(leg.bid, spot, leg.strike), net_credit=net,
                   net_credit_total=_money(net * _MULTIPLIER * contracts))


def _put_parts(leg: PricedLeg, ctx: PricingContext, section: Any, contracts: int,
               reasons: List[Reason]) -> Tuple[Optional[Decimal], Dict[str, Any]]:
    spot = ctx.spot_mid
    assert spot is not None and leg.ask is not None and leg.dte_calendar is not None
    _common_bands(reasons, leg, section, ctx)
    floor = floor_pct(spot, leg.strike)
    _band(reasons, KEY_FLOOR_PCT, CODE_FLOOR_OUTSIDE_BAND, floor, section.floor_pct[0], section.floor_pct[1])
    stressed = stress_spot(spot, section.stress_drop_pct)
    with localcontext(_DECIMAL_CONTEXT):
        cover = leg.strike - stressed
    score = stress_cost_per_day(leg.ask, leg.strike, stressed, leg.dte_calendar) if leg.dte_calendar > 0 else None
    if cover <= 0:
        reasons.append(Reason(KEY_STRESS_DROP_PCT, CODE_NO_COVER_AT_STRESS, cover, section.stress_drop_pct))
    return score, {"floor_pct": floor, "covered_per_share": cover, "covered_total": _money(cover * _MULTIPLIER * contracts),
                   "cost_total": _money(leg.ask * _MULTIPLIER * contracts)}


def protective_put_candidate(leg: PricedLeg, ctx: PricingContext, group: GroupContext, section: Any) -> Candidate:
    """A fresh protective put on the group's shares, ``floor(n / 100)`` contracts, under ``[protective_put]``."""
    contracts = group.contracts
    if leg.unpriced is not None:
        return _unpriced(KIND_PROTECTIVE_PUT, [leg], contracts, leg.unpriced)
    reasons: List[Reason] = []
    score, metrics = _put_parts(leg, ctx, section, contracts, reasons)
    assert leg.ask is not None
    after = after_state(group, contracts, [(leg, contracts, leg.ask)], None)
    return _finish(KIND_PROTECTIVE_PUT, [leg], contracts, score, reasons, (), _put_caveat(leg), after, **metrics)


def replacement_put_candidate(leg: PricedLeg, ctx: PricingContext, group: GroupContext, held: HeldLeg,
                              section: Any) -> Candidate:
    """A replacement for the held long put under ``[protective_put]``: this put opened at its fresh ask, the held one
    closed at its fresh bid (S7: the new put first), ``|held contracts|`` each."""
    contracts = held.contracts
    if leg.unpriced is not None:
        return _unpriced(KIND_REPLACEMENT_PUT, [leg], contracts, leg.unpriced)
    reasons: List[Reason] = []
    score, metrics = _put_parts(leg, ctx, section, contracts, reasons)
    assert leg.ask is not None
    after = after_state(group, contracts, [(leg, contracts, leg.ask)], held)
    return _finish(KIND_REPLACEMENT_PUT, [leg], contracts, score, reasons, (), _put_caveat(leg), after, **metrics)


def collar_candidate(put: PricedLeg, call: PricedLeg, ctx: PricingContext, group: GroupContext, section: Any,
                     events: EventContext) -> Candidate:
    """A collar on the group's shares (a long put and a short call of one expiration, ``floor(n / 100)`` each) under
    ``[collar]``: net debit ``D = put ask - call bid``, score the cap ``U`` (designer Q's D8)."""
    contracts = group.contracts
    spot = ctx.spot_mid
    assert spot is not None
    floor, cap = floor_pct(spot, put.strike), cap_pct(spot, call.strike)
    for leg in (put, call):
        if leg.unpriced is not None:
            return _unpriced(KIND_COLLAR, [put, call], contracts, leg.unpriced, floor_pct=floor, cap_pct=cap)
    assert put.ask is not None and call.bid is not None and call.dte_calendar is not None
    reasons: List[Reason] = []
    _common_bands(reasons, put, section, ctx)
    if call.spread_pct is None or call.spread_pct > ctx.max_spread_pct:
        reasons.append(Reason(KEY_MAX_SPREAD_PCT, CODE_SPREAD_ABOVE_MAX, call.spread_pct, ctx.max_spread_pct))
    _band(reasons, KEY_FLOOR_PCT, CODE_FLOOR_OUTSIDE_BAND, floor, section.floor_pct[0], section.floor_pct[1])
    _band(reasons, KEY_CAP_PCT, CODE_CAP_OUTSIDE_BAND, cap, section.cap_pct[0], section.cap_pct[1])
    debit = put.ask - call.bid
    if debit > section.max_net_debit:
        reasons.append(Reason(KEY_MAX_NET_DEBIT, CODE_NET_DEBIT_ABOVE_BUDGET, debit, section.max_net_debit))
    if section.strike_floor == "cost_basis" and call.strike < group.s0:
        reasons.append(Reason(KEY_STRIKE_FLOOR, CODE_STRIKE_BELOW_COST, call.strike, payoff.quantize_price(group.s0)))
    kept, accepted = _call_events(call, group, section, events, None)
    after = after_state(group, contracts, [(put, contracts, put.ask), (call, -contracts, call.bid)], None)
    caveat = _put_caveat(put) or _short_call_caveat(call, spot, events)
    return _finish(KIND_COLLAR, [put, call], contracts, cap, reasons + kept, accepted, caveat, after, floor_pct=floor,
                   cap_pct=cap, net_debit=debit, net_debit_total=_money(debit * _MULTIPLIER * contracts))


@dataclass(frozen=True)
class CandidateSet:
    """What :func:`candidates_for` built: the candidates (unranked) and, for a collar, the legs left out of the pairs
    because they lie outside their band (``{"puts": n, "calls": m}``)."""

    candidates: List[Candidate]
    outside_bands: Optional[Dict[str, int]] = None


QUOTE_IDENTITY_FIELDS: Tuple[str, ...] = ("bid", "ask", "bid_date", "ask_date", "strike", "expiration_date",
                                          "contract_size", "root_symbol", "option_type", "underlying")
"""The fields that price a contract: two copies of one symbol equal in all of them are one contract."""


def _identity(quote: Quote) -> Tuple[Any, ...]:
    return tuple(getattr(quote, name) for name in QUOTE_IDENTITY_FIELDS)


def listed_options(chains: Mapping[datetime.date, Sequence[Quote]], right: str) -> List[Tuple[Quote, int]]:
    """Every contract of ``right`` the ``chains`` list, once per symbol, with the number of different quotes the
    vendor listed for it: copies equal in every :data:`QUOTE_IDENTITY_FIELDS` field are one contract (``1``); copies
    that differ give that number (``> 1``, priced UNPRICED ``quote_conflict`` by :func:`price_listed`). The result
    never depends on the order of the entries (Q D17), and is in symbol order."""
    copies: Dict[str, List[Quote]] = {}
    for expiration in sorted(chains):
        for quote in chains[expiration]:
            if (quote.option_type or "").upper() != right or quote.strike is None or quote.expiration_date is None:
                continue
            copies.setdefault(quote.symbol.strip().upper(), []).append(quote)
    out: List[Tuple[Quote, int]] = []
    for symbol in sorted(copies):
        group = copies[symbol]
        distinct = sorted({repr(_identity(quote)) for quote in group})
        canonical = min(group, key=lambda quote: repr(_identity(quote)))
        out.append((canonical, len(distinct)))
    return out


def price_listed(quote: Quote, quotes_listed: int, ctx: PricingContext) -> PricedLeg:
    """:func:`price_contract` of a listed contract; one listed with conflicting quotes is UNPRICED ``quote_conflict``
    and carries no price, no grade and no Greeks."""
    leg = price_contract(quote, ctx)
    if quotes_listed <= 1:
        return leg
    return replace(leg, bid=None, ask=None, mid=None, spread_pct=None, grade=None, quote_status=None,
                   quote_age_min=None, as_of=None, tau_years=None, iv=None, delta=None, abs_delta=None, greeks=None,
                   unpriced=Reason(KEY_UNPRICED, CODE_QUOTE_CONFLICT, quotes_listed, None))


def _priced_options(chains: Mapping[datetime.date, Sequence[Quote]], right: str,
                    ctx: PricingContext) -> List[PricedLeg]:
    return [price_listed(quote, count, ctx) for quote, count in listed_options(chains, right)]


def candidates_for(kind: str, chains: Mapping[datetime.date, Sequence[Quote]], ctx: PricingContext, group: GroupContext,
                   section: Any, events: EventContext, *, held: Optional[HeldLeg] = None) -> CandidateSet:
    """Every candidate of ``kind`` in the evaluated ``chains`` (pure; order-independent: ranking sorts them)."""
    if kind == KIND_COVERED_CALL:
        return CandidateSet([covered_call_candidate(leg, ctx, group, section, events)
                             for leg in _priced_options(chains, "CALL", ctx)])
    if kind == KIND_REPLACEMENT_CALL:
        if held is None:
            raise ValueError("a replacement call needs the held call")
        return CandidateSet([replacement_call_candidate(leg, ctx, group, held, section, events)
                             for leg in _priced_options(chains, "CALL", ctx)])
    if kind == KIND_PROTECTIVE_PUT:
        return CandidateSet([protective_put_candidate(leg, ctx, group, section)
                             for leg in _priced_options(chains, "PUT", ctx)])
    if kind == KIND_REPLACEMENT_PUT:
        if held is None:
            raise ValueError("a replacement put needs the held put")
        return CandidateSet([replacement_put_candidate(leg, ctx, group, held, section)
                             for leg in _priced_options(chains, "PUT", ctx)])
    if kind != KIND_COLLAR:
        raise ValueError("unknown candidate kind %r" % (kind,))
    spot = ctx.spot_mid
    assert spot is not None
    puts = _priced_options(chains, "PUT", ctx)
    calls = _priced_options(chains, "CALL", ctx)
    in_puts = [leg for leg in puts if section.floor_pct[0] <= floor_pct(spot, leg.strike) <= section.floor_pct[1]]
    in_calls = [leg for leg in calls if section.cap_pct[0] <= cap_pct(spot, leg.strike) <= section.cap_pct[1]]
    pairs = [collar_candidate(put, call, ctx, group, section, events) for put in in_puts for call in in_calls
             if put.expiration == call.expiration]
    return CandidateSet(pairs, {"puts": len(puts) - len(in_puts), "calls": len(calls) - len(in_calls)})


# --------------------------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ranking:
    """The ranked table (module notes): every candidate with its ``rank`` and ``lost_because``; ``best`` is rank 1 when
    it is ELIGIBLE (``verdict`` BEST), else ``None`` (NONE_QUALIFIES); ``tie_break`` names the rule that separated the
    best from an ELIGIBLE runner-up with the same score. ``passed`` counts the ELIGIBLE candidates, ``failed`` those
    that failed a criterion (FAILS_CRITERION, OUTSIDE_BANDS) and ``unpriced`` those never judged against the criteria
    (UNPRICED); ``failure_counts`` counts the failing candidates per ``structuring.toml`` key (UNPRICED apart)."""

    candidates: Tuple[Candidate, ...]
    best: Optional[Candidate]
    verdict: str
    tie_break: Optional[str]
    passed: int
    failed: int
    failure_counts: Dict[str, int]
    objective: Objective
    unpriced: int = 0

    @property
    def priced(self) -> int:
        """The candidates judged against the criteria (every one but the UNPRICED)."""
        return self.passed + self.failed

    def as_dict(self) -> Dict[str, Any]:
        return {"candidates": [candidate.as_dict() for candidate in self.candidates],
                "best": None if self.best is None else self.best.id, "verdict": self.verdict,
                "tie_break": self.tie_break, "passed": self.passed, "failed": self.failed, "unpriced": self.unpriced,
                "failure_counts": dict(self.failure_counts), "objective": objective_dict(self.objective)}


def objective_dict(objective: Objective) -> Dict[str, Any]:
    return {"id": objective.id, "words": objective.words, "formula": objective.formula, "unit": objective.unit,
            "direction": objective.direction}


def kind_objective(kind: str) -> Objective:
    """The objective a kind ranks by (S3)."""
    return structuring_config.objective_for(KIND_SECTIONS[kind])


def _tie_value(candidate: Candidate, key: str) -> Any:
    leg = candidate.legs[0]
    if key == "abs_delta":
        return leg.abs_delta
    if key == "expiration":
        return candidate.expiration
    if key in ("strike", "put_strike"):
        return leg.strike
    if key == "contract":
        return candidate.id
    if key == "net_credit":
        return candidate.net_credit
    if key == "net_debit":
        return candidate.net_debit
    raise ValueError("unknown tie-break %r" % (key,))


def _component(value: Any, direction: int) -> Tuple[int, Any]:
    if value is None:
        return (1, 0)
    if isinstance(value, str):
        return (0, value)
    if isinstance(value, datetime.date):
        value = value.toordinal()
    return (0, -value if direction == _DESCENDING else value)


def _sort_key(candidate: Candidate, kind: str, objective: Objective) -> Tuple[Any, ...]:
    tier = TABLE_ORDER.index(candidate.status)
    if candidate.status == STATUS_UNPRICED:
        return (tier, (0, candidate.id))
    direction = _DESCENDING if objective.direction == "higher" else _ASCENDING
    parts = [tier, _component(candidate.score, direction)]
    directions = TIE_BREAK_DIRECTIONS[kind]
    parts.extend(_component(_tie_value(candidate, key), directions[key]) for key in TIE_BREAKS[kind])
    return tuple(parts)


def _separating(candidate: Candidate, best: Candidate, kind: str, objective: Objective) -> Optional[Dict[str, Any]]:
    if candidate.score != best.score:
        return {"by": objective.id, "value": candidate.score, "best": best.score}
    for key in TIE_BREAKS[kind]:
        mine, theirs = _tie_value(candidate, key), _tie_value(best, key)
        if mine != theirs:
            return {"by": key, "value": mine, "best": theirs}
    return None


def rank_candidates(candidates: Iterable[Candidate], kind: str) -> Ranking:
    """Order, rank and name the best (module notes, "Objectives"); deterministic whatever the input order."""
    if kind not in KINDS:
        raise ValueError("unknown candidate kind %r" % (kind,))
    objective = kind_objective(kind)
    ordered = sorted(candidates, key=lambda candidate: _sort_key(candidate, kind, objective))
    top = ordered[0] if ordered else None
    best = top if top is not None and top.status == STATUS_ELIGIBLE else None
    ranked: List[Candidate] = []
    counts: Dict[str, int] = {}
    for index, candidate in enumerate(ordered, 1):
        if candidate.status == STATUS_ELIGIBLE:
            lost = None if best is None or index == 1 else _separating(candidate, best, kind, objective)
        else:
            lost = {"by": "status", "status": candidate.status, "codes": [reason.code for reason in candidate.reasons]}
            if candidate.status != STATUS_UNPRICED:   # never judged: counted apart, not as failing the criteria
                for key in dict.fromkeys(reason.key for reason in candidate.reasons):
                    counts[key] = counts.get(key, 0) + 1
        ranked.append(replace(candidate, rank=index, lost_because=lost))
    tie = None
    if best is not None and len(ranked) > 1 and ranked[1].status == STATUS_ELIGIBLE and ranked[1].score == best.score:
        separating = _separating(ranked[1], best, kind, objective)
        tie = None if separating is None else separating["by"]
    passed = sum(1 for candidate in ranked if candidate.status == STATUS_ELIGIBLE)
    unpriced = sum(1 for candidate in ranked if candidate.status == STATUS_UNPRICED)
    failure_counts = {key: counts[key] for key in REASON_KEY_ORDER if key in counts}
    return Ranking(tuple(ranked), None if best is None else ranked[0], VERDICT_BEST if best is not None
                   else VERDICT_NONE_QUALIFIES, tie, passed, len(ranked) - passed - unpriced, failure_counts, objective,
                   unpriced)


# --------------------------------------------------------------------------------------------
# Applicability (S1)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Applicability:
    """A complete answer that prices nothing: NOT_APPLICABLE or NOTHING_TO_PROPOSE, a code and the desk's words."""

    verdict: str
    code: str
    reason: str


def _plain(value: Decimal) -> str:
    return expiry.plain(value)


def _words(group: PositionGroup) -> str:
    return alerts.STRUCTURE_WORDS.get(group.structure, group.structure)


def _subject(group: PositionGroup) -> str:
    return group.trade_id or group.group_id


def _index(group: PositionGroup) -> Optional[Applicability]:
    legs = group.open_legs or group.legs
    if any(row.is_index_underlying for row in legs):
        return Applicability(VERDICT_NOT_APPLICABLE, NA_INDEX, "%s is on the index %s: the sandbox cannot quote index "
                             "underlyings, so the desk prices no candidate for it" % (_subject(group), group.underlying))
    return None


def _no_open_legs(group: PositionGroup) -> Optional[Applicability]:
    if not group.open_legs:
        return Applicability(VERDICT_NOT_APPLICABLE, NA_NO_OPEN_LEGS, "%s has no open leg in positions.csv"
                             % _subject(group))
    return None


def _disabled(criteria: StructuringConfig, section: str) -> Optional[Applicability]:
    if not criteria.enabled(section):
        return Applicability(VERDICT_NOT_APPLICABLE, NA_DISABLED, structuring_config.DISABLED_REASON)
    return None


def _open_shares(group: PositionGroup) -> Decimal:
    return sum((row.open_quantity for row in group.open_legs if row.is_stock_like), _ZERO)


def roll_applicability(group: PositionGroup, criteria: StructuringConfig) -> Optional[Applicability]:
    """``None`` when a roll analysis applies: the open short call of a ``covered_call`` group (S1); otherwise
    NOT_APPLICABLE with its reason (an index underlying, no open leg, another structure, ``[roll] enabled = false``)."""
    for answer in (_index(group), _no_open_legs(group)):
        if answer is not None:
            return answer
    if group.structure != grouping.STRUCTURE_COVERED_CALL:
        ticket = ("; a ROLL ticket prices the replacement of its long put that you name"
                  if group.structure == grouping.STRUCTURE_PROTECTIVE_PUT else "")
        return Applicability(VERDICT_NOT_APPLICABLE, NA_STRUCTURE, "%s is a %s: the roll analysis covers the short "
                             "call of a covered call%s; the roll analysis of a long put, a short put or a collar is "
                             "planned for Stage 2 (not in this release)" % (_subject(group), _words(group), ticket))
    return _disabled(criteria, "roll")


def proposal_applicability(group: PositionGroup, structure: str, criteria: StructuringConfig) -> Optional[Applicability]:
    """``None`` when ``structure`` (covered_call, protective_put, collar) may be proposed: a shares-only group holding
    at least 100 shares (S1, designer Q's D10); otherwise NOTHING_TO_PROPOSE or NOT_APPLICABLE with its reason."""
    if structure not in PROPOSAL_STRUCTURES:
        raise ValueError("structure must be one of %s, got %r" % (", ".join(PROPOSAL_STRUCTURES), structure))
    for answer in (_index(group), _no_open_legs(group)):
        if answer is not None:
            return answer
    shares = _open_shares(group)
    subject = _subject(group)
    if shares <= 0:
        return Applicability(VERDICT_NOTHING_TO_PROPOSE, NOTHING_NO_SHARES, "%s holds no shares: the desk proposes "
                             "overlays on shares you hold" % subject)
    if group.structure != grouping.STRUCTURE_LONG_STOCK:
        options = [row for row in group.open_legs if row.is_option]
        if group.structure == grouping.STRUCTURE_PROTECTIVE_PUT and options:
            puts = sum((abs(row.open_quantity) for row in options), _ZERO)
            reason = "the %s %s shares already carry %s long put%s" % (_plain(shares), group.underlying, _plain(puts),
                                                                     "" if puts == 1 else "s")
        elif group.structure == grouping.STRUCTURE_COVERED_CALL:
            reason = "%s is a covered call: ask for a roll analysis" % subject
        else:
            reason = ("%s is a %s: the desk proposes overlays on groups that hold shares only (no share-row split in "
                      "this release)" % (subject, _words(group)))
        return Applicability(VERDICT_NOTHING_TO_PROPOSE, NOTHING_NOT_SHARES_ONLY, reason)
    if shares < _MULTIPLIER:
        return Applicability(VERDICT_NOTHING_TO_PROPOSE, NOTHING_FEWER_THAN_100, "%s holds %s %s shares: an overlay "
                             "needs 100 shares per contract" % (subject, _plain(shares), group.underlying))
    return _disabled(criteria, structure)


def ticket_applicability(group: PositionGroup, action: str,
                         criteria: Optional[StructuringConfig] = None) -> Optional[Applicability]:
    """``None`` when ``action`` applies to the group (S1); otherwise NOT_APPLICABLE with its reason. ROLL reads
    ``[roll]`` (a covered call's short call) or ``[protective_put]`` (a protective put's long put) when ``criteria``
    is given."""
    if action not in ACTIONS:
        raise ValueError("action must be one of %s, got %r" % (", ".join(ACTIONS), action))
    for answer in (_index(group), _no_open_legs(group)):
        if answer is not None:
            return answer
    subject = _subject(group)
    options = [row for row in group.open_legs if row.is_option]
    shares = _open_shares(group)

    def refuse(code: str, reason: str) -> Applicability:
        return Applicability(VERDICT_NOT_APPLICABLE, code, reason)

    if action == ACTION_CLOSE and not options:
        return refuse(NA_ACTION, "%s holds no open option leg to close; an UNWIND_STOCK ticket covers the shares" % subject)
    if action == ACTION_UNWIND_STOCK and shares <= 0:
        return refuse(NA_ACTION, "%s holds no shares to close" % subject)
    if action == ACTION_LET_ASSIGN and not any(row.is_short for row in options):
        return refuse(NA_ACTION, "%s holds no short option: only a short option can be left to assignment" % subject)
    if action == ACTION_MONETIZE_PUT and group.structure not in (grouping.STRUCTURE_PROTECTIVE_PUT,
                                                                 grouping.STRUCTURE_COLLAR):
        return refuse(NA_ACTION, "%s is a %s: the put of a protective put or a collar is what MONETIZE_PUT closes"
                      % (subject, _words(group)))
    if action == ACTION_ROLL:
        if group.structure == grouping.STRUCTURE_COVERED_CALL:
            return None if criteria is None else _disabled(criteria, "roll")
        if group.structure == grouping.STRUCTURE_PROTECTIVE_PUT:
            return None if criteria is None else _disabled(criteria, "protective_put")
        if group.structure == grouping.STRUCTURE_COLLAR:
            return refuse(NA_STRUCTURE, "%s is a collar: a collar's legs are replaced as a unit, which is planned for "
                          "Stage 2 (not in this release)" % subject)
        return refuse(NA_STRUCTURE, "%s is a %s: a ROLL ticket replaces the short call of a covered call or the long put "
                      "of a protective put; other replacements are planned for Stage 2 (not in this release)"
                      % (subject, _words(group)))
    return None


# --------------------------------------------------------------------------------------------
# Loading (designer Q's D3)
# --------------------------------------------------------------------------------------------


@dataclass
class Target:
    """The target group and everything loaded around it (module notes, "Loading" 1)."""

    market: MarketData
    trade_id: str
    positions: PositionsFile
    groups: List[PositionGroup]
    group: Optional[PositionGroup]
    calendar: Optional[CalendarFile]
    extra_holidays: Dict[datetime.date, str]
    session: SessionInfo
    prior_close_utc: datetime.datetime
    today: datetime.date
    now: datetime.datetime
    quarantined: bool

    @property
    def unusable(self) -> bool:
        """Quarantined by the parser (its rows are not loaded) or by the grouping (an account or root mismatch)."""
        return self.quarantined or (self.group is not None and self.group.is_quarantined)

    @property
    def others_quarantined(self) -> bool:
        """Another group of positions.csv is quarantined by the parser: its rows are not loaded, so neither the
        account's share coverage nor its other short options can be counted (every structuring answer fails closed)."""
        return any(key != self.trade_id for key in self.positions.quarantined)


def owner_texts(target: Optional[Target]) -> List[str]:
    """The cells the owner typed that a structuring text never prints (designer C's D15), the same set in the three
    tools: every account name of desk.toml and every account cell of positions.csv (review L3-2)."""
    texts = set()
    if target is not None:
        texts.update(entry.name for entry in target.market.cfg.owner.accounts if entry.name)
        texts.update(row.account for row in target.positions.rows if row.account)
    return sorted(texts)


def load_target(market: MarketData, trade_id: str) -> Target:
    """Load ``positions.csv`` and the calendar and find the trade's group (module notes); :class:`StructuringError`
    for a file that cannot be used or a trade id ``positions.csv`` does not hold."""
    now = market.now
    today = market.session.et_date
    if not isinstance(trade_id, str) or not positions_csv.TRADE_ID_RE.match(trade_id):
        raise StructuringError(ERROR_UNKNOWN_TRADE, "%r is not a trade id (AG-YYYYMMDD-NN)" % (trade_id,))
    try:
        positions = positions_csv.load_positions(market.paths.positions_csv, today=today)
    except FileNotFoundError:
        raise StructuringError(ERROR_POSITIONS_INVALID, "positions.csv not found at %s"
                               % market.paths.positions_csv) from None
    if positions.file_errors:
        raise StructuringError(ERROR_POSITIONS_INVALID, "positions.csv cannot be used: %s"
                               % "; ".join(str(error) for error in positions.file_errors))
    try:
        calendar = calendar_events.load_calendar_if_present(market.paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        raise StructuringError(ERROR_CALENDAR_INVALID, str(exc)) from None
    extra = calendar_events.extra_closures(calendar)
    groups = grouping.group_positions(positions.rows)
    group = next((g for g in groups if g.trade_id == trade_id), None)
    quarantined = trade_id in positions.quarantined
    if group is None and not quarantined:
        raise StructuringError(ERROR_UNKNOWN_TRADE, "%s is not a trade id in positions.csv" % trade_id)
    return Target(market=market, trade_id=trade_id, positions=positions, groups=groups, group=group, calendar=calendar,
                  extra_holidays=dict(extra), session=market_clock.session_at(now, extra_holidays=extra),
                  prior_close_utc=market_clock.previous_close_utc(now, extra_holidays=extra), today=today, now=now,
                  quarantined=quarantined)


@dataclass
class GroupBook:
    """The group-scoped book (module notes, "Loading" 1): the requested symbols, the quotes and their verdicts, the
    book document, the target group's record and leg records (by row number) and the underlying's verdict."""

    symbols: List[str]
    quotes: Dict[str, Quote]
    verdicts: Dict[str, QuoteQC]
    document: Dict[str, Any]
    group_record: Dict[str, Any]
    leg_records: Dict[int, Dict[str, Any]]
    spot: Optional[QuoteQC]
    cache: Optional[CacheResult]


def load_group_book(target: Target) -> GroupBook:
    """ONE quotes batch for the target group's symbols and the book built for that group only (never ``load_book``)."""
    market, group = target.market, target.group
    assert group is not None
    symbols = book.request_batch([group], target.today, target.calendar)
    result: Optional[CacheResult] = None
    quotes: Dict[str, Quote] = {}
    verdicts: Dict[str, QuoteQC] = {}
    if symbols:
        result = quote_cache.get_quotes(symbols, now=target.now, cfg=market.cache, client=market.client,
                                        paths=market.paths, allow_fetch=target.session.trading_day, run=market.run)
        quotes, verdicts = book.assess_quotes(result, symbols, now=target.now, session=target.session,
                                              bound_min=market.bound_min, prior_close_utc=target.prior_close_utc)
    built = book.build_book(now=target.now, cfg=market.cfg, paths=market.paths, positions=target.positions,
                            groups=[group], quotes=quotes, qcs=verdicts, calendar=target.calendar, prev_snapshot=None,
                            session=target.session, prior_close_utc=target.prior_close_utc, bound_min=market.bound_min,
                            receipt_utc=None if result is None else result.receipt_utc,
                            cache_warnings=() if result is None else result.warnings)
    document = book.book_to_json(built)
    record = next(rec for rec in document["groups"] if rec["group_id"] == group.group_id)
    legs = {rec["row_number"]: rec for rec in document["legs"] if rec["group_id"] == group.group_id}
    return GroupBook(symbols, quotes, verdicts, document, record, legs, verdicts.get(group.underlying), result)


def before_state(record: Mapping[str, Any]) -> BeforeState:
    """The before-state from the group-scoped book's group record."""
    greeks_record = record.get("net_greeks")
    breakevens = record.get("breakevens") or []
    return BeforeState(group_delta=None if not greeks_record else greeks_record.get("delta"),
                       max_loss=record.get("max_loss"), max_profit=record.get("max_profit"),
                       breakeven=breakevens[0] if breakevens else None, unbounded_up=bool(record.get("unbounded_up")))


def group_context(target: Target, group_book: GroupBook) -> GroupContext:
    group = target.group
    assert group is not None
    stock = [row for row in group.open_legs if row.is_stock_like and row.is_long]
    return GroupContext(trade_id=target.trade_id, group_id=group.group_id, underlying=group.underlying,
                        account=group.account, structure=group.structure,
                        shares=sum((row.open_quantity for row in stock), _ZERO),
                        stock_cost=sum((row.remaining_cost_basis for row in stock), _ZERO),
                        earnings_ok=any(row.earnings_ok for row in group.open_legs),
                        assign_ok=any(row.assign_ok for row in group.open_legs),
                        before=before_state(group_book.group_record))


@dataclass(frozen=True)
class ExpirationsBlock:
    """``listed_in_window`` (the candidate expirations), ``evaluated`` (their chains served) and ``not_evaluated``
    (``[{expiration, reason}]``)."""

    listed_in_window: List[datetime.date]
    evaluated: List[datetime.date]
    not_evaluated: List[Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        return {"listed_in_window": list(self.listed_in_window), "evaluated": list(self.evaluated),
                "not_evaluated": [dict(entry) for entry in self.not_evaluated]}


def load_expirations(target: Target, window: Tuple[int, int],
                     later_than: Optional[datetime.date] = None) -> Tuple[Optional[List[datetime.date]], Optional[str], List[str]]:
    """``(candidate expirations | None, missing reason | None, warnings)`` from ``quote_cache.get_expirations``."""
    market = target.market
    assert target.group is not None
    result = quote_cache.get_expirations(target.group.underlying, now=target.now, cfg=market.cache, client=market.client,
                                         paths=market.paths, allow_fetch=target.session.trading_day, run=market.run)
    if result.missing_reason is not None:
        return None, result.missing_reason, list(result.warnings)
    return (candidate_expirations(result.dates, target.today, window, later_than=later_than,
                                  extra_holidays=target.extra_holidays), None, list(result.warnings))


def load_chains(target: Target, expirations: Sequence[datetime.date]) -> Tuple[Dict[datetime.date, List[Quote]],
                                                                                ExpirationsBlock, List[str]]:
    """The chains of ``expirations`` in ascending order, stopping at the first one not served (module notes)."""
    market = target.market
    assert target.group is not None
    chains: Dict[datetime.date, List[Quote]] = {}
    missing: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for expiration in sorted(expirations):
        if missing:
            missing.append({"expiration": expiration, "reason": NOT_REQUESTED})
            continue
        result = quote_cache.get_chain(target.group.underlying, expiration, now=target.now, cfg=market.cache,
                                       client=market.client, paths=market.paths,
                                       allow_fetch=target.session.trading_day, run=market.run)
        warnings.extend(result.warnings)
        if result.missing_reason is not None:
            missing.append({"expiration": expiration, "reason": result.missing_reason})
        else:
            chains[expiration] = list(result.options)
    return chains, ExpirationsBlock(sorted(expirations), sorted(chains), missing), warnings


def requests_block(market: MarketData) -> Dict[str, Any]:
    """``{made, chains, per_run_max_requests, per_run_max_chains, budget_left}`` at the end of the run."""
    return {"made": market.run.requests, "chains": market.run.chains,
            "per_run_max_requests": market.cache.per_run_max_requests,
            "per_run_max_chains": market.cache.per_run_max_chains,
            "budget_left": quote_cache.budget_left(market.paths, market.now, market.cache, market.run)}


def ticket_session_live(session: SessionInfo) -> bool:
    """A ticket is computed only while the session trades (regular or early close: S5)."""
    return session.is_live


def valid_until(now: datetime.datetime, session: SessionInfo,
                minutes: int = TICKET_MINUTES) -> Tuple[datetime.datetime, bool]:
    """``(min(now + 30 min, the session's close), whether that is the full 30 minutes)`` (S5)."""
    until = now + datetime.timedelta(minutes=minutes)
    if session.close_utc is not None and session.close_utc < until:
        return session.close_utc, False
    return until, True


# --------------------------------------------------------------------------------------------
# The answer and the three drivers
# --------------------------------------------------------------------------------------------

ANSWER_KEYS: Tuple[str, ...] = (
    "tool", "kind", "trade_id", "group_id", "structure", "structure_requested", "section", "underlying", "account",
    "account_type", "verdict", "cause", "reason", "objective", "criteria", "spot", "held", "before", "shares",
    "contracts", "stress", "expirations", "candidates", "best", "tie_break", "passed", "failed", "unpriced",
    "failure_counts", "outside_bands", "after", "model_note", "requests", "warnings",
)
"""The keys of :meth:`Answer.as_dict`, the payload body the three tools extend with their own keys."""


@dataclass
class Answer:
    """What a driver answered (module notes): the verdict (``BEST``, ``NONE_QUALIFIES``, ``NOT_APPLICABLE``,
    ``NOTHING_TO_PROPOSE``, ``UNAVAILABLE`` or, for :func:`run_replacement`, ``PRICED``), its cause and reason words
    when it prices nothing, everything it loaded (so a caller or a test re-ranks the same inputs with
    :func:`candidates_for`) and the requests block."""

    tool: str
    kind: str
    trade_id: str
    section_name: str
    criteria: StructuringConfig
    structure_requested: Optional[str] = None
    verdict: str = VERDICT_UNAVAILABLE
    cause: Optional[str] = None
    reason: Optional[str] = None
    target: Optional[Target] = None
    book: Optional[GroupBook] = None
    group_context: Optional[GroupContext] = None
    before: Optional[BeforeState] = None
    pricing: Optional[PricingContext] = None
    events: Optional[EventContext] = None
    held: Optional[HeldLeg] = None
    section: Any = None
    objective: Optional[Objective] = None
    expirations: Optional[ExpirationsBlock] = None
    chains: Dict[datetime.date, List[Quote]] = field(default_factory=dict)
    ranking: Optional[Ranking] = None
    contracts: Optional[int] = None
    stress: Optional[Dict[str, Any]] = None
    outside_bands: Optional[Dict[str, int]] = None
    requests: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    model_note: str = MODEL_NOTE

    @property
    def candidate(self) -> Optional[Candidate]:
        """The named replacement of :func:`run_replacement` (the one candidate), else ``None``."""
        if self.verdict != VERDICT_PRICED or self.ranking is None or not self.ranking.candidates:
            return None
        return self.ranking.candidates[0]

    @property
    def best(self) -> Optional[Candidate]:
        """The best candidate of an analysis; ``None`` for a named replacement (the owner chose it, the desk did not)."""
        return None if self.ranking is None or self.verdict == VERDICT_PRICED else self.ranking.best

    @property
    def account_type(self) -> Optional[str]:
        if self.target is None or self.group_context is None:
            return None
        for entry in self.target.market.cfg.owner.accounts:
            if entry.name == self.group_context.account:
                return entry.type
        return None

    def as_dict(self) -> Dict[str, Any]:
        """The payload body (:data:`ANSWER_KEYS`; money as Decimals, dates and instants as Python objects for
        ``contract.json_dumps``)."""
        group = None if self.target is None else self.target.group
        spot = None
        if self.pricing is not None:
            qc = self.pricing.spot
            spot = {"symbol": self.pricing.underlying, "bid": price_value(qc.bid), "ask": price_value(qc.ask),
                    "mid": price_value(qc.mark_mid), "grade": qc.grade, "as_of": qc.as_of}
        ranking = self.ranking
        best = self.best
        after = None
        if self.verdict == VERDICT_PRICED and self.candidate is not None:
            after = None if self.candidate.after is None else self.candidate.after.as_dict()
        elif best is not None and best.after is not None:
            after = best.after.as_dict()
        return {
            "tool": self.tool, "kind": self.kind, "trade_id": self.trade_id,
            "group_id": None if group is None else group.group_id,
            "structure": None if group is None else group.structure, "structure_requested": self.structure_requested,
            "section": self.section_name, "underlying": None if group is None else group.underlying,
            "account": None if group is None else group.account, "account_type": self.account_type,
            "verdict": self.verdict, "cause": self.cause, "reason": self.reason,
            "objective": None if self.objective is None else objective_dict(self.objective),
            "criteria": self.criteria.as_dict(), "spot": spot, "held": None if self.held is None else self.held.as_dict(),
            "before": None if self.before is None else self.before.as_dict(),
            "shares": None if self.group_context is None else self.group_context.shares, "contracts": self.contracts,
            "stress": self.stress, "expirations": None if self.expirations is None else self.expirations.as_dict(),
            "candidates": [] if ranking is None else [candidate.as_dict() for candidate in ranking.candidates],
            "best": None if best is None else best.id, "tie_break": None if best is None else ranking.tie_break,
            "passed": None if ranking is None else ranking.passed, "failed": None if ranking is None else ranking.failed,
            "unpriced": None if ranking is None else ranking.unpriced,
            "failure_counts": None if ranking is None else dict(ranking.failure_counts),
            "outside_bands": self.outside_bands, "after": after, "model_note": self.model_note,
            "requests": dict(self.requests), "warnings": list(self.warnings),
        }


def _close(answer: Answer, verdict: str, cause: Optional[str] = None, reason: Optional[str] = None) -> Answer:
    answer.verdict, answer.cause, answer.reason = verdict, cause, reason
    if answer.target is not None:
        answer.requests = requests_block(answer.target.market)
    return answer


_UNAVAILABLE_WORDS: Dict[str, str] = {
    CAUSE_MARKET_CLOSED: "the market is closed today",
    CAUSE_GROUP_QUARANTINED: "the group is quarantined in positions.csv (positions_check.py shows why)",
    CAUSE_POSITIONS_QUARANTINED: ("positions.csv has a quarantined group (positions_check.py shows why): the desk cannot "
                                  "count the account's coverage"),
    CAUSE_NO_CANDIDATE_PRICED: "no candidate in your window could be priced",
    CAUSE_SPOT_UNUSABLE: "no usable quote of the underlying",
    CAUSE_HELD_LEG_UNUSABLE: "no fresh conservative side for the held leg",
    CAUSE_EXPIRATIONS_NOT_SERVED: "the listed expirations were not served",
    CAUSE_CHAIN_NOT_SERVED: "an option chain in your window was not served",
    CAUSE_REPLACEMENT_NOT_LISTED: "the chain does not list that contract",
}
"""The desk's reason words for each UNAVAILABLE cause (the text module words its line from the cause)."""
TICKET_CLOSED_WORDS: str = "a ticket needs quotes live enough to act on: the session is not open"


def _unavailable(answer: Answer, cause: str, reason: Optional[str] = None) -> Answer:
    return _close(answer, VERDICT_UNAVAILABLE, cause, reason or _UNAVAILABLE_WORDS[cause])


def _start(answer: Answer, market: MarketData) -> Optional[Answer]:
    """Load the target; ``answer`` closed UNAVAILABLE when the group is quarantined, else ``None``."""
    target = load_target(market, answer.trade_id)
    answer.target = target
    if target.unusable:
        return _unavailable(answer, CAUSE_GROUP_QUARANTINED)
    if target.others_quarantined:
        return _unavailable(answer, CAUSE_POSITIONS_QUARANTINED)
    return None


def _priced_context(answer: Answer) -> Optional[Answer]:
    """The group book, the before-state, the contexts and the stops of designer Q's D3 item 1."""
    target = answer.target
    assert target is not None and target.group is not None
    group_book = load_group_book(target)
    answer.book = group_book
    answer.group_context = group_context(target, group_book)
    answer.before = answer.group_context.before
    if group_book.cache is not None:
        answer.warnings.extend("quote cache: %s" % warning for warning in group_book.cache.warnings)
    if target.calendar is not None:
        answer.warnings.extend(target.calendar.warnings)
    if not target.session.trading_day:
        return _unavailable(answer, CAUSE_MARKET_CLOSED)
    spot = group_book.spot
    if spot is None or not spot.usable_for_rules or spot.mark_mid is None:
        return _unavailable(answer, CAUSE_SPOT_UNUSABLE)
    dividend_yield = calendar_events.dividend_yield(target.calendar, target.group.underlying)
    if dividend_yield is None:
        answer.warnings.append(WARNING_Q_ZERO)
    market = target.market
    answer.pricing = PricingContext(now=target.now, today=target.today, session=target.session,
                                    prior_close_utc=target.prior_close_utc, bound_min=market.bound_min,
                                    rate=market.cfg.rates.risk_free_rate_f, dividend_yield=dividend_yield, spot=spot,
                                    underlying=target.group.underlying,
                                    max_spread_pct=answer.criteria.general.max_spread_pct,
                                    extra_holidays=target.extra_holidays)
    answer.events = EventContext(symbol=target.group.underlying, calendar=target.calendar, now=target.now,
                                 today=target.today, extra_holidays=target.extra_holidays)
    return None


def _held(answer: Answer, row: PositionRow) -> Optional[Answer]:
    target, group_book = answer.target, answer.book
    assert target is not None and group_book is not None
    answer.held = held_leg(row, group_book.verdicts.get(row.compact_symbol), group_book.leg_records.get(row.row_number),
                           target.extra_holidays)
    if not answer.held.usable:
        return _unavailable(answer, CAUSE_HELD_LEG_UNUSABLE)
    return None


def _evaluate(answer: Answer, later_than: Optional[datetime.date]) -> Answer:
    """Expirations, chains, candidates and the ranking (module notes, "Loading" 2-3)."""
    target = answer.target
    assert target is not None and answer.pricing is not None and answer.group_context is not None
    expirations, missing, warnings = load_expirations(target, answer.section.dte, later_than)
    answer.warnings.extend(warnings)
    if expirations is None:
        return _unavailable(answer, CAUSE_EXPIRATIONS_NOT_SERVED)
    if not expirations:
        low, high = answer.section.dte
        answer.expirations = ExpirationsBlock([], [], [])
        return _close(answer, VERDICT_NOT_APPLICABLE, NA_NO_EXPIRATION,
                      "no listed standard monthly expiration lies inside your %s window of %d to %d calendar days"
                      % (answer.section_name, low, high))
    chains, block, chain_warnings = load_chains(target, expirations)
    answer.warnings.extend(chain_warnings)
    answer.expirations = block
    if block.not_evaluated:
        return _unavailable(answer, CAUSE_CHAIN_NOT_SERVED)
    answer.chains = chains
    assert answer.events is not None
    built = candidates_for(answer.kind, chains, answer.pricing, answer.group_context, answer.section, answer.events,
                           held=answer.held)
    answer.outside_bands = built.outside_bands
    answer.ranking = rank_candidates(built.candidates, answer.kind)
    if answer.ranking.priced == 0 and (built.candidates or answer.kind != KIND_COLLAR):
        return _unavailable(answer, CAUSE_NO_CANDIDATE_PRICED, no_candidate_words(built.candidates))
    return _close(answer, answer.ranking.verdict)


def unpriced_words(reason: Reason) -> str:
    """The words of an UNPRICED reason (:data:`UNPRICED_WORDS`, :data:`IV_WORDS`)."""
    if reason.code in UNPRICED_WORDS:
        return UNPRICED_WORDS[reason.code]
    if reason.code.startswith(IV_CODE_PREFIX):
        return IV_WORDS
    raise ValueError("unknown UNPRICED code %r" % (reason.code,))


def no_candidate_words(candidates: Sequence[Candidate]) -> str:
    """Why no candidate was priced: the one UNPRICED reason they share, else that the reasons differ."""
    head = _UNAVAILABLE_WORDS[CAUSE_NO_CANDIDATE_PRICED]
    if not candidates:
        return head + ": the chains list none"
    words = {unpriced_words(reason) for candidate in candidates for reason in candidate.reasons}
    if len(words) == 1:
        return "%s: %s" % (head, words.pop())
    return head + " (the reasons differ; the JSON lists each)"


def _option_row(group: PositionGroup, right: str, short: bool) -> Optional[PositionRow]:
    rows = [row for row in group.open_legs if row.is_option and row.right == right and row.is_short == short]
    return rows[0] if len(rows) == 1 else None


def run_roll_analysis(market: MarketData, trade_id: str, criteria: StructuringConfig) -> Answer:
    """``roll_analysis.py``: every replacement short call of a later standard monthly inside ``[roll] dte`` for the
    short call of a ``covered_call`` group, ranked by ``extrinsic_per_day`` (module notes)."""
    answer = Answer(tool=TOOL_ROLL_ANALYSIS, kind=KIND_REPLACEMENT_CALL, trade_id=trade_id, section_name="roll",
                    criteria=criteria, section=criteria.roll, objective=kind_objective(KIND_REPLACEMENT_CALL))
    stopped = _start(answer, market)
    if stopped is not None:
        return stopped
    group = answer.target.group if answer.target is not None else None
    assert group is not None
    applicability = roll_applicability(group, criteria)
    if applicability is not None:
        return _close(answer, applicability.verdict, applicability.code, applicability.reason)
    row = _option_row(group, "CALL", True)
    if row is None:  # a covered_call group holds one short call leg, possibly on several rows (lots)
        return _close(answer, VERDICT_NOT_APPLICABLE, NA_STRUCTURE, NA_TWO_ROWS_WORDS % (trade_id, "short call"))
    stopped = _priced_context(answer) or _held(answer, row)
    if stopped is not None:
        return stopped
    assert answer.held is not None
    answer.contracts = answer.held.contracts
    return _evaluate(answer, answer.held.expiration)


def _free_shares(target: Optional[Target], group: PositionGroup, structure: str) -> Optional[Applicability]:
    """NOTHING_TO_PROPOSE when the account's short calls on the underlying (held in other groups) already rely on the
    shares a covered call or a collar would need (designer C's D6: coverage is per account, whatever the group)."""
    assert target is not None
    try:
        holdings = approval.holdings_of(target.positions.rows, group.account, group.underlying)
    except ValueError as exc:
        raise StructuringError(ERROR_POSITIONS_INVALID, str(exc)) from None
    shares = _open_shares(group)
    contracts = int(shares // _MULTIPLIER)
    free = holdings.free_shares
    if free >= CONTRACT_SIZE * contracts:
        return None
    calls = holdings.short_calls
    if free < CONTRACT_SIZE:
        return Applicability(VERDICT_NOTHING_TO_PROPOSE, NOTHING_NO_FREE_SHARES,
                             "no free shares: %s %s shares are covered by %s short call%s; the desk proposes no %s"
                             % (_plain(Decimal(holdings.shares)), group.underlying, calls, "" if calls == 1 else "s",
                                _words_of(structure)))
    return Applicability(VERDICT_NOTHING_TO_PROPOSE, NOTHING_NOT_ENOUGH_FREE_SHARES,
                         "%s of the %s %s shares are free: %s short call%s held in another group of this account "
                         "rel%s on the rest; the desk proposes no %s on part of a group's shares"
                         % (_plain(Decimal(free)), _plain(shares), group.underlying, calls, "" if calls == 1 else "s",
                            "ies" if calls == 1 else "y", _words_of(structure)))


def _words_of(structure: str) -> str:
    return alerts.STRUCTURE_WORDS.get(structure, structure)


def run_proposal(market: MarketData, trade_id: str, structure: str, criteria: StructuringConfig) -> Answer:
    """``propose.py``: a covered call, protective put or collar on a shares-only group, ``floor(n / 100)`` contracts,
    ranked by the structure's objective (module notes)."""
    if structure not in PROPOSAL_STRUCTURES:
        raise ValueError("structure must be one of %s, got %r" % (", ".join(PROPOSAL_STRUCTURES), structure))
    kind = PROPOSAL_KINDS[structure]
    answer = Answer(tool=TOOL_PROPOSE, kind=kind, trade_id=trade_id, section_name=structure, criteria=criteria,
                    structure_requested=structure, section=criteria.section(structure), objective=kind_objective(kind))
    stopped = _start(answer, market)
    if stopped is not None:
        return stopped
    group = answer.target.group if answer.target is not None else None
    assert group is not None
    applicability = proposal_applicability(group, structure, criteria)
    if applicability is None and structure in ("covered_call", "collar"):
        applicability = _free_shares(answer.target, group, structure)
    if applicability is not None:
        return _close(answer, applicability.verdict, applicability.code, applicability.reason)
    stopped = _priced_context(answer)
    if stopped is not None:
        return stopped
    assert answer.group_context is not None and answer.pricing is not None and answer.pricing.spot_mid is not None
    answer.contracts = answer.group_context.contracts
    if kind == KIND_PROTECTIVE_PUT:
        answer.stress = {"drop_pct": answer.section.stress_drop_pct,
                         "spot": stress_spot(answer.pricing.spot_mid, answer.section.stress_drop_pct)}
    return _evaluate(answer, None)


def run_replacement(market: MarketData, trade_id: str, expiration: datetime.date, strike: Decimal,
                    criteria: StructuringConfig, *, require_live: bool = True) -> Answer:
    """``exit_ticket.py --action ROLL``: the one replacement the owner names (the held leg's root and right, the given
    expiration and strike), priced and labelled through the same code path: a short call under ``[roll]`` or a long
    put under ``[protective_put]`` (S1, S2). ``ValueError`` when ``expiration`` is not after the held leg's (a usage
    error in the tool). A ticket needs a live session (``require_live``: S5)."""
    answer = Answer(tool=TOOL_EXIT_TICKET, kind=KIND_REPLACEMENT_CALL, trade_id=trade_id, section_name="roll",
                    criteria=criteria, section=criteria.roll, objective=kind_objective(KIND_REPLACEMENT_CALL))
    stopped = _start(answer, market)
    if stopped is not None:
        return stopped
    target = answer.target
    assert target is not None and target.group is not None
    group = target.group
    applicability = ticket_applicability(group, ACTION_ROLL, criteria)
    if applicability is not None:
        return _close(answer, applicability.verdict, applicability.code, applicability.reason)
    if group.structure == grouping.STRUCTURE_PROTECTIVE_PUT:
        answer.kind, answer.section_name, answer.section = KIND_REPLACEMENT_PUT, "protective_put", criteria.protective_put
        answer.objective = kind_objective(KIND_REPLACEMENT_PUT)
        row = _option_row(group, "PUT", False)
    else:
        row = _option_row(group, "CALL", True)
    if row is None or row.occ is None:
        held_words = "long put" if answer.kind == KIND_REPLACEMENT_PUT else "short call"
        return _close(answer, VERDICT_NOT_APPLICABLE, NA_STRUCTURE, NA_TWO_ROWS_WORDS % (trade_id, held_words))
    if expiration <= row.occ.expiration:
        raise ValueError("the replacement's expiration %s must be later than the held leg's %s"
                         % (expiration.isoformat(), row.occ.expiration.isoformat()))
    if require_live and not ticket_session_live(target.session):
        return _unavailable(answer, CAUSE_MARKET_CLOSED, TICKET_CLOSED_WORDS)
    stopped = _priced_context(answer) or _held(answer, row)
    if stopped is not None:
        return stopped
    assert answer.held is not None and answer.pricing is not None and answer.group_context is not None
    answer.contracts = answer.held.contracts
    chains, block, warnings = load_chains(target, [expiration])
    answer.warnings.extend(warnings)
    answer.expirations = block
    if block.not_evaluated:
        return _unavailable(answer, CAUSE_CHAIN_NOT_SERVED)
    wanted = expiry.plain_decimal(Decimal(strike))
    listed = [q for q in chains[expiration] if (q.option_type or "").upper() == row.occ.right
              and q.strike is not None and expiry.plain_decimal(q.strike) == wanted and q.expiration_date == expiration
              and (q.root_symbol or row.occ.root).strip().upper() == row.occ.root]
    answer.chains = {expiration: listed}
    entries = listed_options({expiration: listed}, row.occ.right)
    if not entries:
        return _unavailable(answer, CAUSE_REPLACEMENT_NOT_LISTED)
    assert answer.events is not None
    leg = price_listed(entries[0][0], entries[0][1], answer.pricing)
    if answer.kind == KIND_REPLACEMENT_PUT:
        candidate = replacement_put_candidate(leg, answer.pricing, answer.group_context, answer.held, answer.section)
    else:
        candidate = replacement_call_candidate(leg, answer.pricing, answer.group_context, answer.held, answer.section,
                                               answer.events)
    answer.ranking = rank_candidates([candidate], answer.kind)
    return _close(answer, VERDICT_PRICED)


# --------------------------------------------------------------------------------------------
# The approval-level check of a candidate (S7 leg orders through aegis_desk.approval)
# --------------------------------------------------------------------------------------------


def approval_legs(answer: Answer, candidate: Candidate) -> List[approval.Leg]:
    """The candidate's legs in the S7 order: a short-call roll closes the held call, then opens the new one; a long-put
    roll opens the new put, then closes the held one; a collar opens the put, then the call; a covered call or a
    protective put is its one opening leg."""
    count = candidate.contracts
    legs: List[approval.Leg] = []
    opening = [approval.Leg(approval.EFFECT_OPEN, approval.SIDE_LONG if leg.right == "PUT" else approval.SIDE_SHORT,
                            approval.INSTRUMENT_PUT if leg.right == "PUT" else approval.INSTRUMENT_CALL, count)
               for leg in candidate.legs]
    held = answer.held
    if candidate.kind == KIND_REPLACEMENT_CALL and held is not None:
        legs.append(approval.Leg(approval.EFFECT_CLOSE, approval.SIDE_SHORT, approval.INSTRUMENT_CALL, held.contracts))
        legs.extend(opening)
    elif candidate.kind == KIND_REPLACEMENT_PUT and held is not None:
        legs.extend(opening)
        legs.append(approval.Leg(approval.EFFECT_CLOSE, approval.SIDE_LONG, approval.INSTRUMENT_PUT, held.contracts))
    else:
        legs.extend(opening)
    return legs


def approval_state(answer: Answer) -> approval.Holdings:
    """What the group's account holds of the underlying, every open row of ``positions.csv`` counted
    (``approval.holdings_of``: coverage is per account and underlying, whatever the group)."""
    target, group = answer.target, answer.group_context
    if target is None or group is None:
        raise ValueError("the answer loaded no group")
    return approval.holdings_of(target.positions.rows, group.account, group.underlying)


def approval_check(answer: Answer, candidate: Candidate, owner_level: int) -> approval.ApprovalResult:
    """The approval-level check of the candidate's legs (S7) against the owner's self-attested level."""
    return approval.check_legs(approval_state(answer), approval_legs(answer, candidate), owner_level)
