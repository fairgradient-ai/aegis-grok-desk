"""Alert messages: the exact text the desk posts for a verdict, built only from formatted numbers it emitted.

Plan section 8 ("Signals and alerts"), the PR 2c brief and addendum; standard library only, no I/O. The model that
relays a message never does arithmetic: every number in a message is produced by one formatter per kind
(:func:`fmt_money`, :func:`fmt_pct`, :func:`fmt_days`, :func:`fmt_delta`, :func:`fmt_multiple`, :func:`fmt_count`,
:func:`fmt_minutes`, :func:`fmt_delay`, :func:`fmt_strike`, :func:`fmt_threshold`, :func:`fmt_number`,
:func:`fmt_reason`, :func:`fmt_footer`), each of which appends the numeric tokens of what it printed to the
message's ``emitted_numbers``; :func:`aegis_desk.alert_lint.check` then proves that the text holds no other number.
An alert never carries order fields, a roll destination or ranked alternatives: "your rule X fired", never advice.

The exit-signal shape (one line, then the footer)
-------------------------------------------------
``EXIT SIGNAL <trade_id> | <UNDERLYING> <structure words> | your rule <ID> (rules <version>, sha <8 hex>) fired:
<metric sentence with the value and threshold> | quote grade <A-F>, <source words>, 15-min delayed, as of HH:MM
UTC[, run N min late] | your plan: <plan words> | not an instruction to trade; you act in your brokerage account.``
followed by the footer line ``deterministic rules you adopted · posted by an AI assistant · not personalized
investment advice · data delayed 15 min, as of HH:MM UTC``. ``<trade_id>`` is the group's trade id (its group id
``ROW-<n>`` when it has none); the sha is the adopted rulebook's (``run_info["rulebook_sha"]``, first eight hex
digits); the quote grade is the worst grade of the group's open legs and, for a group with an option leg, its
underlying; ``<source words>`` are :func:`aegis_desk.data_source.words_for_label` of the book's ``sources[0].name``
(``Alpaca paper (IEX stocks, indicative options: approximate)`` for ``alpaca:paper``); the quote segment's time is the
oldest as-of among those legs, the footer's the book's newest. A
metric sentence is ``<label> <value> (your threshold: <op words> <threshold>)`` per condition, joined with ``and``,
followed by ``; <reason>`` when the rule result carries one and ``; <note>`` for each of its notes (``quote grade E:
the conservative side stands``, ``earnings date estimated (research/calendar.toml)``); a yes/no metric reads
``<label>: yes (your threshold: yes)``; the loss multiple reads ``loss 2.10 x credit = cost-to-close 3.10 x
credit``. Thresholds print as the owner wrote them (``80 %``, ``0.30``, ``21 trading days``, ``-$1,000.00``: money
in dollars, never rounded). A per-share price (:data:`PER_SHARE_METRICS`: the extrinsic value at the bid, a dividend,
their difference) prints its exact decimals (:func:`fmt_price`, ``-$0.1450 per share``): it is the number the rule
compared, so the alert and ``exit_check.py`` print the same one. A percentage or multiple the engine kept to more
than two decimals at a threshold (``exit_rules.refine_at_threshold``) prints them all. Plan words: ``close`` -> ``close``, ``roll_review`` -> ``review a
roll``, ``hold_review`` -> ``review and decide``. A rule seen for the first time while already true adds the
segment ``already past this threshold at the desk's first observation`` after its sentence.

Every other rule of the group follows, one line each: ``also fired: your rule <ID>: <sentence> | your plan: <plan
words>``, ``unavailable: your rule <ID>: <reason>``, ``pending confirmation: your rule <ID>: <sentence>[;
observation N of M]``, ``cooling down: your rule <ID>: <sentence>; <reason>`` (a rule that fired and cleared, true
again before it re-arms: no new signal) and one ``suppressed by your row tags: <IDs>`` line; so the message lists
EVERY fired and EVERY unavailable rule. :func:`watch_text` is the same shape headed ``WATCH`` (its primary rule is the first fired
WATCH rule). :func:`unavailable_banner` is headed ``UNAVAILABLE <trade_id> | <UNDERLYING> <structure words> |
rules <version>, sha <8 hex>: no verdict while an input is unavailable; this is not HOLD | <quote segment> | not an
instruction ...`` and lists the unavailable rules first (a group quarantined in ``positions.csv`` reads
``quarantined in positions.csv`` with its reasons in the desk's own words, :func:`quarantine_words`: ``row 9: the
status cell cannot be used (positions_check.py shows it)``; the text the owner typed in a cell is never printed, so a
cell can neither carry trade language into a message nor break the lint for every other message). The portfolio section (portfolio- and
underlying-scope rules not tied to a group) posts ``WATCH portfolio | all open groups | ...`` only when one of its
rules fired, with the ``no history: ...`` lines of the evaluation (addendum decision 11: ``no history: your rules
MOVE5_DOWN, MOVE5_UP, DAILY need the previous end-of-day snapshot (none for YYYY-MM-DD)``).

Identifiers and the emitted document
------------------------------------
Every builder records the identifiers it printed (the trade or group id, ``rules X.Y.Z``, ``sha <8 hex>``, ISO dates)
and the clocks (``HH:MM UTC``, ``HH:MM ET``), beside the numbers: :meth:`Alert.emitted_document` is ``{emitted_numbers,
rule_ids, identifiers, clocks, as_of}`` (``as_of`` the footer's instant), the strict document ``alert_lint`` checks a
relayed message against (an invented id, sha or clock, or a footer moved in time, fails). :func:`withheld_text` is the
fixed banner a caller posts in place of a part that failed the lint.

Other messages
--------------
:func:`config_changed_text` (``CONFIG CHANGED | desk.toml: sha <old> -> sha <new> | ... | evaluated with the files
as they are now | acknowledge with ack.py config --owner-tapped``), :func:`rulebook_changed_text` (``rulebook
changed: N verdicts changed | rules <old> -> rules <new>, sha <8 hex>``), :func:`pre_close_line` (verdict counts,
open signals, data age, ``verify live quotes in your broker before acting``) and :func:`watch_alive_line`. Each
builder returns an :class:`Alert` whose ``text`` ends with the footer; :func:`compose` joins several (in the order
given, so CONFIG CHANGED can come first) under one footer.

Inputs (the addendum's shared interface pins)
---------------------------------------------
``render(evaluation, book, run_info)`` reads, attribute or mapping key alike: ``evaluation.groups`` (each
``GroupVerdict(group_id, verdict, fired, unavailable, pending, clear, suppressed)``), ``evaluation.portfolio`` and
``evaluation.underlyings`` (results or sections holding results), ``evaluation.no_history`` (sentences or results)
and ``evaluation.rulebook`` (optional: ``rules`` with ``id``, ``verdict``, ``plan``, ``when``); each ``RuleResult``
gives ``rule_id, rule_sha, result, metric, value, threshold, unit, inputs, reason`` and, when the evaluation carries
no rulebook, ``verdict``, ``plan``, ``op`` (``dte_unit`` for a ``dte`` metric) or ``conditions`` (one entry per
condition: ``metric, op, value, threshold, unit, dte_unit``); ``first_observation``, ``observations`` and
``confirmations`` are optional. ``book`` is the book document (``aegis_desk.book``); ``run_info`` is
``{rulebook_version, rulebook_sha, quote_age_min, run_lateness_min, posted_at}``.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import alert_lint, data_source, expiry, signals

KIND_EXIT_SIGNAL: str = "exit_signal"
KIND_WATCH: str = "watch"
KIND_UNAVAILABLE: str = "unavailable"
KIND_CONFIG_CHANGED: str = "config_changed"
KIND_RULEBOOK_CHANGED: str = "rulebook_changed"
KIND_PRE_CLOSE: str = "pre_close"
KIND_WATCH_ALIVE: str = "watch_alive"
KINDS: Tuple[str, ...] = (KIND_EXIT_SIGNAL, KIND_WATCH, KIND_UNAVAILABLE, KIND_CONFIG_CHANGED, KIND_RULEBOOK_CHANGED,
                          KIND_PRE_CLOSE, KIND_WATCH_ALIVE)
_KIND_SEVERITY: Tuple[str, ...] = (KIND_EXIT_SIGNAL, KIND_UNAVAILABLE, KIND_WATCH, KIND_CONFIG_CHANGED,
                                   KIND_RULEBOOK_CHANGED, KIND_PRE_CLOSE, KIND_WATCH_ALIVE)

VERDICT_EXIT_SIGNAL: str = "EXIT_SIGNAL"
VERDICT_UNAVAILABLE: str = "UNAVAILABLE"
VERDICT_WATCH: str = "WATCH"
VERDICT_HOLD: str = "HOLD"
VERDICTS: Tuple[str, ...] = (VERDICT_EXIT_SIGNAL, VERDICT_UNAVAILABLE, VERDICT_WATCH, VERDICT_HOLD)
"""Group verdicts in precedence order."""
VERDICT_WORDS: Dict[str, str] = {VERDICT_EXIT_SIGNAL: "EXIT SIGNAL", VERDICT_UNAVAILABLE: "UNAVAILABLE",
                                 VERDICT_WATCH: "WATCH", VERDICT_HOLD: "HOLD"}
RESULT_FIRED: str = "fired"
RESULT_NO_HISTORY: str = "no_history"

PLAN_WORDS: Dict[str, str] = {"close": "close", "roll_review": "review a roll", "hold_review": "review and decide"}
OP_WORDS: Dict[str, str] = {">=": "at or above", ">": "above", "<=": "at or below", "<": "below", "==": "equal to"}
BOOL_OPS: Dict[str, str] = {"is_true": "yes", "is_false": "no"}
STRUCTURE_WORDS: Dict[str, str] = {
    "long_stock": "long stock", "short_stock": "short stock", "long_call": "long call", "long_put": "long put",
    "short_call": "short call", "cash_secured_put": "cash-secured put", "covered_call": "covered call",
    "protective_put": "protective put", "collar": "collar", "vertical_credit_call": "call credit vertical",
    "vertical_credit_put": "put credit vertical", "vertical_debit_call": "call debit vertical",
    "vertical_debit_put": "put debit vertical", "custom": "custom structure", "invalid": "invalid group",
}
METRIC_LABELS: Dict[str, str] = {
    "pct_captured": "credit captured", "pct_of_max_profit": "share of the max profit captured",
    "pct_of_debit": "P/L as a share of the debit", "unrealized_pl": "unrealized P/L",
    "loss_multiple_of_credit": "loss as a multiple of the credit", "pct_of_max_loss": "loss as a share of the max loss",
    "dte": "time to expiry", "between_strikes": "stock between the strikes", "net_delta": "net delta",
    "stock_above_short_call": "stock above the short call strike", "stock_below_long_put": "stock below the long put strike",
    "abs_delta": "absolute delta", "itm": "in the money", "extrinsic_bid": "extrinsic value at the bid",
    "days_to_ex_dividend": "time to the ex-dividend date", "dividend_amount": "dividend",
    "extrinsic_minus_dividend": "extrinsic at the bid less the dividend", "pct_from_strike": "distance from the strike",
    "auto_exercise_itm": "long option in the money on its last trading day", "spot_vs_stop_pct": "stock versus your stop",
    "move_pct_from_prev_eod": "move since the previous end-of-day snapshot", "days_to_earnings": "time to earnings",
    "day_pl": "day P/L", "max_concentration_pct": "largest concentration by underlying",
}
METRIC_UNITS: Dict[str, str] = {
    "pct_captured": "percent", "pct_of_max_profit": "percent", "pct_of_debit": "percent", "unrealized_pl": "usd",
    "loss_multiple_of_credit": "multiple", "pct_of_max_loss": "percent", "dte": "days", "net_delta": "delta",
    "abs_delta": "delta", "extrinsic_bid": "usd", "days_to_ex_dividend": "days", "dividend_amount": "usd",
    "extrinsic_minus_dividend": "usd", "pct_from_strike": "percent", "spot_vs_stop_pct": "percent",
    "move_pct_from_prev_eod": "percent", "days_to_earnings": "days", "day_pl": "usd", "max_concentration_pct": "percent",
}
BOOL_METRICS: Tuple[str, ...] = ("between_strikes", "stock_above_short_call", "stock_below_long_put", "itm",
                                 "auto_exercise_itm")
PER_SHARE_METRICS: Tuple[str, ...] = ("extrinsic_bid", "dividend_amount", "extrinsic_minus_dividend")
DEFAULT_DAY_UNITS: Dict[str, str] = {"days_to_ex_dividend": "trading", "days_to_earnings": "trading"}
CONFIG_FILE_LABELS: Dict[str, str] = {"desk_toml": "desk.toml", "positions_csv": "positions.csv",
                                      "rulebook": "adopted rulebook", "rules": "adopted rulebook",
                                      "calendar_toml": "research calendar", "structuring_toml": "structuring.toml"}

NOT_AN_INSTRUCTION: str = "not an instruction to trade; you act in your brokerage account."
FIRST_OBSERVATION_SENTENCE: str = "already past this threshold at the desk's first observation"
UNAVAILABLE_SENTENCE: str = "no verdict while an input is unavailable; this is not HOLD"
VERIFY_LIVE_QUOTES: str = "verify live quotes in your broker before acting"
ACKNOWLEDGE_CONFIG: str = "acknowledge with ack.py config --owner-tapped"
EVALUATED_AS_IS: str = "evaluated with the files as they are now"
QUARANTINED_WORDS: str = "quarantined in positions.csv"
PORTFOLIO_TRADE: str = "portfolio"
PORTFOLIO_WORDS: str = "all open groups"
CELL_WORDS: str = "row %s: the %s cell cannot be used (positions_check.py shows it)"
ROWS_WORDS: str = "(positions_check.py shows the rows)"
GROUP_WORDS: str = "the group cannot be used (positions_check.py shows why)"
WITHHELD_SENTENCE: str = ("the message text failed the alert lint (%s) and is withheld (the verdict and every rule are in "
                          "exit_check.py)")
KIND_HEADS: Dict[str, str] = {KIND_EXIT_SIGNAL: "EXIT SIGNAL", KIND_WATCH: "WATCH", KIND_UNAVAILABLE: "UNAVAILABLE",
                              KIND_CONFIG_CHANGED: "CONFIG CHANGED", KIND_RULEBOOK_CHANGED: "rulebook changed",
                              KIND_PRE_CLOSE: "pre-close", KIND_WATCH_ALIVE: "watch alive"}

_CENT = Decimal("0.01")
_GRADES: Tuple[str, ...] = ("A", "B", "C", "D", "E", "F")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\Z")
_SHA_RE = re.compile(r"^[0-9a-f]{8,64}\Z")
_ROW_REASON_RE = re.compile(r"^row ([0-9]+) ([a-z_]+): ")
_DESK_WORDS_RE = re.compile(r"^[a-z][a-z ]{2,40}\Z")
_TRADE_RE = re.compile(r"^(?:AG-[0-9]{8}-[0-9]{2,3}|ROW-[0-9]+|portfolio)\Z")
_IDENTIFIER_FORMS: Tuple[str, ...] = ("trade_id", "signal_id", "group_id", "rules_version", "sha", "date")


@dataclass
class Alert:
    """One message (module notes): ``text`` (``body`` then the ``footer`` line), the number strings it printed
    (``emitted_numbers``), the ``signals.jsonl`` record it stands for (``None`` for a message that is no signal), its
    ``kind`` (:data:`KINDS`), the group and verdict it reports and the rule ids it names."""

    text: str
    emitted_numbers: List[str]
    signal_record: Optional[Dict[str, Any]]
    kind: str
    body: str = ""
    footer: str = ""
    group_id: Optional[str] = None
    verdict: Optional[str] = None
    rule_ids: List[str] = field(default_factory=list)
    as_of: Optional[datetime.datetime] = None
    identifiers: Optional[List[str]] = None
    clocks: Optional[List[str]] = None

    def lint(self) -> alert_lint.LintResult:
        """``alert_lint.check`` of the text against its own emitted numbers, rule ids and (strict) identifiers,
        clocks and as-of."""
        return alert_lint.check(self.text, self.emitted_numbers, rule_ids=self.rule_ids, identifiers=self.identifiers,
                                clocks=self.clocks, as_of=self.as_of)

    def emitted_document(self) -> Dict[str, Any]:
        """The emitted document ``scripts/alert_lint.py --emitted-file`` reads (``alert_lint.load_emitted``)."""
        return {"emitted_numbers": list(self.emitted_numbers), "rule_ids": list(self.rule_ids),
                "identifiers": list(self.identifiers or []), "clocks": list(self.clocks or []),
                "as_of": self.as_of.astimezone(datetime.timezone.utc).isoformat() if self.as_of is not None else None}


# --------------------------------------------------------------------------------------------
# Formatters: one per number kind; each appends the numeric tokens it printed to ``emitted``
# --------------------------------------------------------------------------------------------


def _emit(emitted: List[str], text: str) -> str:
    emitted.extend(token.text for token in alert_lint.numeric_tokens(text))
    return text


def _decimal(value: Any, what: str, allow_float: bool) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise TypeError("%s must be a number, not %r" % (what, value))
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        if not allow_float:
            raise TypeError("%s is money and never a binary float: pass a Decimal" % what)
        result = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError("%s %r is not a decimal number" % (what, value)) from None
    else:
        raise TypeError("%s must be a Decimal, an int or a decimal string, not %s" % (what, type(value).__name__))
    if not result.is_finite():
        raise ValueError("%s must be finite" % what)
    return result


def _cents(value: Decimal, what: str) -> Decimal:
    """``value`` to two decimals, half-even; a number too large for the context is ``ValueError``."""
    try:
        return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        raise ValueError("%s %s cannot be printed to two decimals" % (what, value)) from None


def _plain(value: Any, what: str = "threshold") -> str:
    """A number as written: an int as is, a Decimal without exponent (``0.30`` stays ``0.30``)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return format(_decimal(value, what, True), "f")


def fmt_money(value: Any, emitted: List[str]) -> str:
    """Dollars and cents, half-even, thousands separated, the sign before the dollar sign: ``-$1,000.00``."""
    amount = _cents(_decimal(value, "money", False), "money")
    sign = "-" if amount < 0 else ""
    return _emit(emitted, "%s$%s" % (sign, format(abs(amount), ",.2f")))


def _exact_dollars(amount: Decimal) -> str:
    """``amount`` in dollars with every decimal it has (at least two, padding only), thousands separated."""
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent > -2:
        amount = amount.quantize(_CENT)
    sign = "-" if amount < 0 else ""
    return "%s$%s" % (sign, format(abs(amount), ",f"))


def fmt_price(value: Any, emitted: List[str]) -> str:
    """A per-share price exactly as the engine compared it, never rounded (at least two decimals): ``-$0.1450``,
    ``$0.2350``, ``$0.12`` (refuses floats)."""
    return _emit(emitted, _exact_dollars(_decimal(value, "price", False)))


def _keeps_digits(value: Any) -> bool:
    """A Decimal with more than two decimals: a value the engine kept at a threshold (printed in full)."""
    if not isinstance(value, Decimal) or not value.is_finite():
        return False
    exponent = value.as_tuple().exponent
    return isinstance(exponent, int) and exponent < -2


def fmt_pct(value: Any, emitted: List[str]) -> str:
    """A percentage as the engine compared it: a Decimal of two decimals or fewer to two decimals (``95.32 %``,
    ``80.00 %``), a Decimal with more printed in full (the engine keeps more only at a threshold, decision 15:
    ``0.0034 %``), a float to two decimals half-even through its ``repr``."""
    if _keeps_digits(value):
        return _emit(emitted, "%s %%" % format(value, "f"))
    quantised = _cents(_decimal(value, "percent", True), "percent")
    return _emit(emitted, "%s %%" % format(quantised, "f"))


def fmt_days(value: Any, emitted: List[str], unit: Optional[str] = None) -> str:
    """A whole number of days with its unit: ``13 trading days``, ``1 calendar day``, ``3 days``."""
    days = _decimal(value, "days", False)
    if days != days.to_integral_value():
        raise ValueError("days must be a whole number, got %s" % days)
    count = int(days)
    noun = "day" if count == 1 else "days"
    words = "%s %s" % (unit, noun) if unit in ("trading", "calendar") else noun
    return _emit(emitted, "%d %s" % (count, words))


def fmt_delta(value: Any, emitted: List[str]) -> str:
    """A Greek to two decimals, half-even; a float is read through its ``repr`` (addendum decision 15): ``0.48``."""
    quantised = _cents(_decimal(value, "delta", True), "delta")
    return _emit(emitted, format(quantised, "f"))


def fmt_multiple(value: Any, emitted: List[str]) -> str:
    """A multiple to two decimals, half-even: ``2.10`` (the caller adds ``x credit``); a Decimal with more decimals
    (the engine's value at a threshold) prints them all."""
    if _keeps_digits(value):
        return _emit(emitted, format(value, "f"))
    quantised = _cents(_decimal(value, "multiple", True), "multiple")
    return _emit(emitted, format(quantised, "f"))


def _whole(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("%s must be a non-negative integer, not %r" % (what, value))
    return value


def fmt_count(value: Any, emitted: List[str]) -> str:
    """A count: ``2``."""
    return _emit(emitted, str(_whole(value, "a count")))


def fmt_minutes(value: Any, emitted: List[str]) -> str:
    """Whole minutes: ``19 min``."""
    return _emit(emitted, "%d min" % _whole(value, "minutes"))


def fmt_delay(value: Any, emitted: List[str]) -> str:
    """The feed delay of the quote segment: ``15-min delayed``."""
    return _emit(emitted, "%d-min delayed" % _whole(value, "the feed delay"))


def fmt_strike(value: Any, emitted: List[str]) -> str:
    """A strike without trailing zeros (``expiry.plain``): ``105``, ``6.5``."""
    return _emit(emitted, expiry.plain(_decimal(value, "strike", False)))


def fmt_number(value: Any, emitted: List[str]) -> str:
    """A plain number as written (the fallback for a unit without its own formatter)."""
    return _emit(emitted, _plain(value, "number"))


def fmt_threshold(value: Any, unit: Optional[str], emitted: List[str], dte_unit: Optional[str] = None) -> str:
    """An owner threshold as written in the rulebook, with its unit: ``80 %``, ``0.30``, ``21 trading days``,
    ``-$1,000.00`` (money in dollars, every decimal the owner wrote kept)."""
    if unit == "usd":
        return _emit(emitted, _exact_dollars(_decimal(value, "threshold", False)))
    if unit == "days":
        return fmt_days(value, emitted, dte_unit)
    if unit in ("percent", "pct_of_threshold"):
        return _emit(emitted, "%s %%" % _plain(value))
    return _emit(emitted, _plain(value))


def fmt_reason(text: str, emitted: List[str], rule_ids: Optional[List[str]] = None,
               identifiers: Optional[List[str]] = None, clocks: Optional[List[str]] = None) -> str:
    """A sentence the desk's own code wrote (a rule result's reason, a note): returned unchanged; the numbers the
    whitelist leaves are emitted, and the rule ids, identifiers and clocks it names are added to ``rule_ids``,
    ``identifiers`` and ``clocks`` when given."""
    if not isinstance(text, str):
        raise TypeError("a reason must be a string")
    emitted.extend(token.text for token in alert_lint.unwhitelisted_numbers(text))
    for start, end, form in alert_lint.whitelist_spans(text):
        span = text[start:end]
        bucket = (rule_ids if form == "rule_id" else identifiers if form in _IDENTIFIER_FORMS
                  else clocks if form == "clock" else None)
        if bucket is not None and span not in bucket:
            bucket.append(span)
    return text


def quarantine_words(reason: str) -> str:
    """A quarantine reason in the desk's own words: ``row 9: the status cell cannot be used (positions_check.py shows
    it)`` for a parser reason (``row N <column>: ...``), ``account mismatch (positions_check.py shows the rows)`` for a
    grouping reason; never the text the owner typed in a cell."""
    match = _ROW_REASON_RE.match(reason)
    if match is not None:
        return CELL_WORDS % (match.group(1), match.group(2))
    head = reason.split(":", 1)[0].strip()
    if _DESK_WORDS_RE.match(head):
        return "%s %s" % (head, ROWS_WORDS)
    return GROUP_WORDS


def fmt_footer(as_of: datetime.datetime, delay_min: int, emitted: List[str], clocks: Optional[List[str]] = None,
               kind: str = alert_lint.FOOTER_RULES) -> str:
    """The fixed footer (``alert_lint.footer_line``; with ``kind`` ``snapshot`` the market-snapshot footer of
    ``alert_lint.snapshot_footer_line``, for rule-free text only; with ``kind`` ``structuring`` the structuring footer
    of ``alert_lint.structuring_footer_line``, for the Structuring Desk's texts only) with the data's as-of time (its
    ``HH:MM UTC`` added to ``clocks`` when given). Its ``15 min`` is in the lint's whitelist (addendum decision 6) and
    is not emitted; any other delay is."""
    builders = {alert_lint.FOOTER_RULES: alert_lint.footer_line,
                alert_lint.FOOTER_SNAPSHOT: alert_lint.snapshot_footer_line,
                alert_lint.FOOTER_STRUCTURING: alert_lint.structuring_footer_line}
    if kind not in alert_lint.FOOTER_KINDS or kind not in builders:
        raise ValueError("kind must be one of %s, got %r" % (", ".join(alert_lint.FOOTER_KINDS), kind))
    if _whole(delay_min, "the feed delay") != alert_lint.FOOTER_DELAY_MIN:
        _emit(emitted, str(delay_min))
    line = builders[kind](as_of, delay_min)
    clock = as_of.astimezone(datetime.timezone.utc).strftime("%H:%M UTC")
    if clocks is not None and clock not in clocks:
        clocks.append(clock)
    return line


# --------------------------------------------------------------------------------------------
# Reading the evaluation (attributes or mapping keys) and the book
# --------------------------------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _items(obj: Any, name: str) -> List[Any]:
    value = _get(obj, name)
    if value is None:
        return []
    if isinstance(value, Mapping):
        return list(value.values())
    return list(value)


def _is_result(obj: Any) -> bool:
    return _get(obj, "rule_id") is not None


def _flatten_results(value: Any) -> List[Any]:
    if value is None:
        return []
    if _is_result(value):
        return [value]
    if isinstance(value, (list, tuple)):
        out: List[Any] = []
        for item in value:
            out.extend(_flatten_results(item))
        return out
    lists = [name for name in _SECTION_LISTS if _get(value, name) is not None]
    if lists:
        out = []
        for name in lists:
            out.extend(_flatten_results(_get(value, name)))
        return out
    if isinstance(value, Mapping):
        out = []
        for item in value.values():
            out.extend(_flatten_results(item))
        return out
    return []


_SECTION_LISTS: Tuple[str, ...] = ("results", "fired", "unavailable", "pending", "cooldown", "no_history", "suppressed")


def _section_results(evaluation: Any) -> List[Tuple[Optional[str], Any]]:
    """``(subject, result)`` of every portfolio- and underlying-scope result: ``evaluation.portfolio`` (one section,
    or a list of results) and ``evaluation.underlyings`` (sections whose ``subject`` is the symbol, or a mapping of
    symbol to results); a ``clear`` list is not read."""
    out: List[Tuple[Optional[str], Any]] = []
    portfolio = _get(evaluation, "portfolio")
    sections: List[Tuple[Optional[str], Any]] = [(_get(portfolio, "subject") if not _is_result(portfolio) else None,
                                                  portfolio)]
    underlyings = _get(evaluation, "underlyings")
    if isinstance(underlyings, Mapping):
        sections.extend((str(symbol), value) for symbol, value in underlyings.items())
    elif underlyings is not None:
        sections.extend((_get(section, "subject"), section) for section in underlyings)
    for subject, section in sections:
        name = subject if isinstance(subject, str) and subject != PORTFOLIO_TRADE else None
        out.extend((name, result) for result in _flatten_results(section))
    return out


class _Rulebook:
    def __init__(self, evaluation: Any) -> None:
        self.rules: Dict[str, Any] = {}
        for item in _items(_get(evaluation, "rulebook"), "rules"):
            rule_id = _get(item, "id") or _get(item, "rule_id")
            if isinstance(rule_id, str):
                self.rules[rule_id] = item

    def rule(self, rule_id: Any) -> Any:
        return self.rules.get(rule_id)


@dataclass
class _Condition:
    metric: str
    op: Optional[str]
    value: Any
    threshold: Any
    unit: Optional[str]
    dte_unit: Optional[str]


@dataclass
class _Rule:
    rule_id: str
    rule_sha: Any
    result: str
    verdict: Optional[str]
    plan: Optional[str]
    conditions: List[_Condition]
    reason: str
    first_observation: bool
    observations: Optional[int]
    confirmations: Optional[int]
    raw: Any
    subject: Optional[str] = None
    notes: Tuple[str, ...] = ()


def _input_value(result: Any, metric: str) -> Any:
    for item in _items(result, "inputs"):
        if _get(item, "name") == metric:
            return _get(item, "value")
    return None


def _view(result: Any, rulebook: _Rulebook) -> _Rule:
    rule_id = _get(result, "rule_id")
    if not isinstance(rule_id, str):
        raise ValueError("a rule result needs its rule_id")
    rule = rulebook.rule(rule_id)
    when = _items(rule, "when") if rule is not None else []
    given = _items(result, "conditions")
    if given:
        conditions = []
        for index, item in enumerate(given):
            metric = _get(item, "metric")
            declared = when[index] if index < len(when) and _get(when[index], "metric") == metric else next(
                (entry for entry in when if _get(entry, "metric") == metric), None)
            threshold = _get(item, "threshold")
            conditions.append(_Condition(metric, _get(item, "op") or _get(declared, "op"), _get(item, "value"),
                                         threshold if threshold is not None else _get(declared, "value"),
                                         _get(item, "unit") or _get(declared, "unit"),
                                         _get(item, "dte_unit") or _get(declared, "dte_unit")))
    else:
        primary = _Condition(_get(result, "metric"), _get(result, "op"), _get(result, "value"),
                             _get(result, "threshold"), _get(result, "unit"), _get(result, "dte_unit"))
        conditions = [primary]
        if when:
            conditions = []
            matched = False
            for item in when:
                metric = _get(item, "metric")
                if not matched and metric == primary.metric:
                    matched = True
                    conditions.append(_Condition(metric, primary.op or _get(item, "op"), primary.value,
                                                 primary.threshold if primary.threshold is not None else _get(item, "value"),
                                                 primary.unit or _get(item, "unit"),
                                                 primary.dte_unit or _get(item, "dte_unit")))
                else:
                    conditions.append(_Condition(metric, _get(item, "op"), _input_value(result, metric),
                                                 _get(item, "value"), _get(item, "unit"), _get(item, "dte_unit")))
            if not matched:
                conditions.insert(0, primary)
    observations = _get(result, "observations")
    if isinstance(observations, (list, tuple)):
        observations = len(observations)
    confirmations = _get(result, "confirmations")
    notes = tuple(str(note) for note in (_get(result, "notes") or ()) if isinstance(note, str))
    return _Rule(rule_id=rule_id, rule_sha=_get(result, "rule_sha"), result=str(_get(result, "result") or ""), notes=notes,
                 verdict=_get(result, "verdict") or _get(rule, "verdict"), plan=_get(result, "plan") or _get(rule, "plan"),
                 conditions=conditions, reason=str(_get(result, "reason") or ""),
                 first_observation=bool(_get(result, "first_observation")),
                 observations=observations if isinstance(observations, int) else None,
                 confirmations=confirmations if isinstance(confirmations, int) else None, raw=result)


@dataclass
class _Run:
    version: str
    sha8: str
    quote_age_min: Optional[int]
    run_lateness_min: Optional[int]
    posted_at: Any


def _run(run_info: Mapping[str, Any]) -> _Run:
    version = run_info.get("rulebook_version")
    sha = run_info.get("rulebook_sha")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise ValueError("run_info rulebook_version must be X.Y.Z (no leading v), got %r" % (version,))
    if not isinstance(sha, str) or not _SHA_RE.match(sha.lower()):
        raise ValueError("run_info rulebook_sha must be at least eight hex digits, got %r" % (sha,))
    for key in ("quote_age_min", "run_lateness_min"):
        value = run_info.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("run_info %s must be a non-negative integer or None" % key)
    return _Run(version, sha.lower()[:8], run_info.get("quote_age_min"), run_info.get("run_lateness_min"),
                run_info.get("posted_at"))


def _instant(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def book_as_of(document: Mapping[str, Any]) -> datetime.datetime:
    """The footer's instant of a book: the newest quote as-of of its legs, else its ``generated_at`` (``ValueError``
    when it has neither). Every builder's footer reads it; the level sheet and the summaries reuse it."""
    stamps = [stamp for stamp in (_instant(leg.get("as_of")) for leg in document.get("legs") or []) if stamp]
    if stamps:
        return max(stamps)
    generated = _instant(document.get("generated_at"))
    if generated is None:
        raise ValueError("the book has neither a quote as-of nor generated_at")
    return generated


def _source(document: Mapping[str, Any]) -> Tuple[str, int]:
    sources = document.get("sources") or []
    source = sources[0] if sources else {}
    words = data_source.words_for_label(source.get("name"))
    delay = source.get("delay_min")
    return words, delay if isinstance(delay, int) and not isinstance(delay, bool) else 0


def _group_record(document: Mapping[str, Any], group_id: Any) -> Optional[Mapping[str, Any]]:
    for group in document.get("groups") or []:
        if group.get("group_id") == group_id:
            return group
    return None


def _quarantine_record(document: Mapping[str, Any], group_id: Any) -> Optional[Mapping[str, Any]]:
    for entry in (document.get("portfolio") or {}).get("quarantined_groups") or []:
        if entry.get("group_id") == group_id:
            return entry
    return None


def _open_legs(document: Mapping[str, Any], group: Optional[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    rows = set(group.get("legs") or []) if group is not None else None
    return [leg for leg in document.get("legs") or []
            if leg.get("status") == "open" and (rows is None or leg.get("row_number") in rows)]


def _worst_grade(grades: Iterable[Any]) -> str:
    worst = 0
    seen = False
    for grade in grades:
        seen = True
        index = _GRADES.index(grade) if grade in _GRADES else len(_GRADES) - 1
        worst = max(worst, index)
    return _GRADES[worst] if seen else _GRADES[-1]


def _grade_and_as_of(document: Mapping[str, Any], group: Optional[Mapping[str, Any]],
                     everything: bool = False) -> Tuple[str, datetime.datetime]:
    legs = _open_legs(document, None if everything else group) if (group is not None or everything) else []
    grades: List[Any] = [leg.get("quote_grade") for leg in legs]
    underlyings = document.get("underlyings") or {}
    symbols = {leg.get("underlying") for leg in legs if leg.get("occ")}
    grades.extend((underlyings.get(symbol) or {}).get("quote_grade") for symbol in sorted(s for s in symbols if s))
    stamps = [stamp for stamp in (_instant(leg.get("as_of")) for leg in legs) if stamp]
    return _worst_grade(grades), (min(stamps) if stamps else book_as_of(document))


# --------------------------------------------------------------------------------------------
# Sentences
# --------------------------------------------------------------------------------------------


class _Message:
    """The lines of one message, its emitted numbers and the rule ids it names."""

    def __init__(self, document: Mapping[str, Any], run: _Run) -> None:
        self.document = document
        self.run = run
        self.emitted: List[str] = []
        self.rule_ids: List[str] = []
        self.identifiers: List[str] = []
        self.clocks: List[str] = []
        self.lines: List[str] = []

    def name(self, rule_id: str) -> str:
        if rule_id not in self.rule_ids:
            self.rule_ids.append(rule_id)
        return rule_id

    def ident(self, text: str) -> str:
        """An identifier the message prints (a trade or group id, ``rules X.Y.Z``, ``sha <8 hex>``)."""
        if text not in self.identifiers:
            self.identifiers.append(text)
        return text

    def clock(self, text: str) -> str:
        if text not in self.clocks:
            self.clocks.append(text)
        return text

    def reason(self, text: str) -> str:
        return fmt_reason(text, self.emitted, self.rule_ids, self.identifiers, self.clocks)

    def value(self, condition: _Condition) -> Optional[str]:
        value = condition.value
        if value is None:
            return None
        unit = condition.unit or METRIC_UNITS.get(condition.metric)
        if unit in ("percent", "pct_of_threshold"):
            return fmt_pct(value, self.emitted)
        if unit == "usd":
            if condition.metric in PER_SHARE_METRICS:
                return fmt_price(value, self.emitted) + " per share"
            return fmt_money(value, self.emitted)
        if unit == "days":
            return fmt_days(value, self.emitted, condition.dte_unit or DEFAULT_DAY_UNITS.get(condition.metric))
        if unit == "delta":
            return fmt_delta(value, self.emitted)
        if unit == "multiple":
            return fmt_multiple(value, self.emitted) + " x"
        return fmt_number(value, self.emitted)

    def condition(self, condition: _Condition) -> str:
        metric = condition.metric or "metric"
        label = METRIC_LABELS.get(metric, metric.replace("_", " "))
        unit = condition.unit or METRIC_UNITS.get(metric)
        if metric in BOOL_METRICS or condition.op in BOOL_OPS or isinstance(condition.value, bool):
            text = label
            if isinstance(condition.value, bool):
                text += ": yes" if condition.value else ": no"
            if condition.op in BOOL_OPS:
                text += " (your threshold: %s)" % BOOL_OPS[condition.op]
            return text
        if metric == "loss_multiple_of_credit" and condition.value is not None:
            loss = _decimal(condition.value, "loss multiple", True)
            text = "loss %s x credit = cost-to-close %s x credit" % (fmt_multiple(loss, self.emitted),
                                                                     fmt_multiple(loss + 1, self.emitted))
            if condition.threshold is not None:
                words = OP_WORDS.get(condition.op or "")
                threshold = _emit(self.emitted, _plain(condition.threshold))
                text += " (your threshold: %s x credit)" % ("%s %s" % (words, threshold) if words else threshold)
            return text
        value = self.value(condition)
        text = label if value is None else "%s %s" % (label, value)
        if condition.threshold is not None:
            threshold = fmt_threshold(condition.threshold, unit, self.emitted, condition.dte_unit
                                      or DEFAULT_DAY_UNITS.get(metric))
            words = OP_WORDS.get(condition.op or "")
            text += " (your threshold: %s)" % ("%s %s" % (words, threshold) if words else threshold)
        return text

    def rule_text(self, rule: _Rule) -> str:
        text = " and ".join(self.condition(condition) for condition in rule.conditions)
        if rule.subject:
            text = "%s %s" % (self.reason(rule.subject), text)
        if rule.reason:
            text += "; " + self.reason(rule.reason)
        for note in rule.notes:
            if note != rule.reason:
                text += "; " + self.reason(note)
        return text

    def plan(self, rule: _Rule) -> Optional[str]:
        if rule.plan is None:
            return None
        if rule.plan not in PLAN_WORDS:
            raise ValueError("rule %s has the unknown plan %r" % (rule.rule_id, rule.plan))
        return "your plan: %s" % PLAN_WORDS[rule.plan]

    def quote_segment(self, grade: str, as_of: datetime.datetime) -> str:
        words, delay = _source(self.document)
        clock = self.clock(as_of.astimezone(datetime.timezone.utc).strftime("%H:%M UTC"))
        text = "quote grade %s, %s, %s, as of %s" % (grade, words, fmt_delay(delay, self.emitted), clock)
        if self.run.run_lateness_min:
            text += ", run %s late" % fmt_minutes(self.run.run_lateness_min, self.emitted)
        return text

    def rules_segment(self) -> str:
        return "%s, %s" % (self.ident("rules %s" % self.run.version), self.ident("sha %s" % self.run.sha8))

    def also_fired(self, rule: _Rule) -> Tuple[str, str]:
        """``(line, sentence)`` of a fired rule that is not the message's primary rule."""
        sentence = self.rule_text(rule)
        segments = ["also fired: your rule %s: %s" % (self.name(rule.rule_id), sentence)]
        if rule.first_observation:
            segments.append(FIRST_OBSERVATION_SENTENCE)
        plan = self.plan(rule)
        if plan is not None:
            segments.append(plan)
        return " | ".join(segments), sentence

    def unavailable(self, rule: _Rule) -> Tuple[str, str]:
        """``(line, reason)`` of an unavailable rule."""
        reason = self.reason(rule.reason) if rule.reason else "an input is unavailable"
        return "unavailable: your rule %s: %s" % (self.name(rule.rule_id), reason), reason

    def cooldown(self, rule: _Rule) -> Tuple[str, str]:
        """``(line, sentence)`` of a rule true again before it re-arms (no new signal)."""
        sentence = self.rule_text(rule)
        return "cooling down: your rule %s: %s" % (self.name(rule.rule_id), sentence), sentence

    def pending(self, rule: _Rule) -> Tuple[str, str]:
        """``(line, sentence)`` of a rule waiting for its confirmations."""
        sentence = self.rule_text(rule)
        if rule.observations is not None and rule.confirmations is not None:
            sentence += "; observation %s of %s" % (fmt_count(rule.observations, self.emitted),
                                                    fmt_count(rule.confirmations, self.emitted))
        return "pending confirmation: your rule %s: %s" % (self.name(rule.rule_id), sentence), sentence

    def follow_lines(self, parts: Mapping[str, Sequence[_Rule]], order: Sequence[str], skip: Any,
                     reasons: List[Dict[str, Any]]) -> None:
        """Append the lines of every rule of ``parts`` in ``order`` (``fired``, ``unavailable``, ``pending``), the
        primary result ``skip`` left out, and their entries to ``reasons``; then the suppressed line."""
        builders = {"fired": self.also_fired, "unavailable": self.unavailable, "pending": self.pending,
                    "cooldown": self.cooldown}
        for name in order:
            for rule in parts[name]:
                if skip is not None and rule.raw is skip:
                    continue
                line, sentence = builders[name](rule)
                self.lines.append(line)
                reasons.append(_reason_entry(rule, sentence))
        suppressed_line = self.suppressed(parts["suppressed"])
        if suppressed_line is not None:
            self.lines.append(suppressed_line)

    def suppressed(self, rules: Sequence[_Rule]) -> Optional[str]:
        if not rules:
            return None
        return "suppressed by your row tags: %s" % ", ".join(self.name(rule.rule_id) for rule in rules)

    def finish(self, kind: str, signal_record: Optional[Dict[str, Any]], group_id: Optional[str] = None,
               verdict: Optional[str] = None) -> Alert:
        as_of = book_as_of(self.document)
        _words, delay = _source(self.document)
        footer = fmt_footer(as_of, delay, self.emitted, self.clocks)
        body = "\n".join(self.lines)
        return Alert(text=body + "\n" + footer, emitted_numbers=list(self.emitted), signal_record=signal_record,
                     kind=kind, body=body, footer=footer, group_id=group_id, verdict=verdict,
                     rule_ids=list(self.rule_ids), as_of=as_of, identifiers=list(self.identifiers),
                     clocks=list(self.clocks))


def _reason_entry(rule: _Rule, text: str) -> Dict[str, Any]:
    raw = rule.raw
    return {"rule_id": rule.rule_id, "rule_sha": rule.rule_sha, "result": rule.result, "metric": _get(raw, "metric"),
            "value": _get(raw, "value"), "threshold": _get(raw, "threshold"), "unit": _get(raw, "unit"),
            "verdict": rule.verdict, "plan": rule.plan, "text": text}


def _signal_record(run: _Run, *, trade_id: str, structure: str, verdict: str, plan: Optional[str], data_quality: str,
                   reasons: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = {"signal_id": None, "trade_id": trade_id, "structure": structure, "verdict": verdict, "reasons": reasons,
              "plan": plan, "data_quality": data_quality, "quote_age_at_post": run.quote_age_min,
              "run_lateness_min": run.run_lateness_min, "posted_at": run.posted_at}
    return {key: values[key] for key in signals.SIGNAL_KEYS}


def _views(results: Iterable[Any], rulebook: _Rulebook) -> List[_Rule]:
    return [_view(result, rulebook) for result in results]


def _group_parts(group_verdict: Any, rulebook: _Rulebook) -> Dict[str, List[_Rule]]:
    return {name: _views(_items(group_verdict, name), rulebook)
            for name in ("fired", "unavailable", "pending", "cooldown", "suppressed")}


def _subject(document: Mapping[str, Any], group_id: Any) -> Tuple[str, str, str, str, Optional[Mapping[str, Any]]]:
    """``(trade id, subject words, structure, data quality, group record)`` of a group or a quarantined group."""
    group = _group_record(document, group_id)
    if group is not None:
        structure = str(group.get("structure") or "custom")
        words = "%s %s" % (group.get("underlying"), STRUCTURE_WORDS.get(structure, structure.replace("_", " ")))
        trade = group.get("trade_id") or group.get("group_id")
        return str(trade), words, structure, str(group.get("data_quality") or "unavailable"), group
    return str(group_id), QUARANTINED_WORDS, "quarantined", "unavailable", None


def _signal_text(kind: str, group_verdict: Any, primary: Any, document: Mapping[str, Any],
                 run_info: Mapping[str, Any], rulebook: Optional[_Rulebook] = None) -> Alert:
    run = _run(run_info)
    rulebook = rulebook or _Rulebook(None)
    group_id = _get(group_verdict, "group_id")
    parts = _group_parts(group_verdict, rulebook)
    head = _view(primary, rulebook)
    verdict = VERDICT_EXIT_SIGNAL if kind == KIND_EXIT_SIGNAL else VERDICT_WATCH
    trade, subject, structure, quality, group = _subject(document, group_id)
    message = _Message(document, run)
    grade, as_of = _grade_and_as_of(document, group)
    sentence = message.rule_text(head)
    segments = ["%s %s" % (VERDICT_WORDS[verdict], message.ident(trade)), subject,
                "your rule %s (%s) fired: %s" % (message.name(head.rule_id), message.rules_segment(), sentence)]
    reasons = [_reason_entry(head, sentence)]
    if head.first_observation:
        segments.append(FIRST_OBSERVATION_SENTENCE)
    segments.append(message.quote_segment(grade, as_of))
    plan = message.plan(head)
    if plan is None and verdict == VERDICT_EXIT_SIGNAL:
        raise ValueError("the EXIT_SIGNAL rule %s carries no plan (neither on its result nor in the rulebook)"
                         % head.rule_id)
    if plan is not None:
        segments.append(plan)
    segments.append(NOT_AN_INSTRUCTION)
    message.lines.append(" | ".join(segments))
    message.follow_lines(parts, ("fired", "unavailable", "pending", "cooldown"), primary, reasons)
    record = _signal_record(run, trade_id=trade, structure=structure, verdict=verdict, plan=head.plan,
                            data_quality=quality, reasons=reasons)
    return message.finish(kind, record, group_id, verdict)


def _primary(group_verdict: Any, verdict: str, rulebook: _Rulebook) -> Any:
    fired = _items(group_verdict, "fired")
    for result in fired:
        if _view(result, rulebook).verdict == verdict:
            return result
    if not fired:
        raise ValueError("group %s is %s without a fired rule" % (_get(group_verdict, "group_id"), verdict))
    return fired[0]


# --------------------------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------------------------


def exit_signal_text(group: Any, result: Any, book: Mapping[str, Any], run: Mapping[str, Any]) -> Alert:
    """The EXIT SIGNAL message of ``group`` (a GroupVerdict) headed by the fired rule ``result`` (module notes)."""
    return _signal_text(KIND_EXIT_SIGNAL, group, result, book, run)


def watch_text(group: Any, result: Any, book: Mapping[str, Any], run: Mapping[str, Any]) -> Alert:
    """The WATCH message of ``group`` headed by the fired rule ``result`` (module notes)."""
    return _signal_text(KIND_WATCH, group, result, book, run)


def unavailable_banner(group: Any, book: Mapping[str, Any], run: Mapping[str, Any],
                       rulebook: Optional[Any] = None) -> Alert:
    """The UNAVAILABLE banner of ``group`` (a GroupVerdict): every unavailable rule with its reason, then every
    fired, pending and suppressed rule; a group quarantined in ``positions.csv`` lists the parser's reasons."""
    run_parsed = _run(run)
    book_rules = rulebook if isinstance(rulebook, _Rulebook) else _Rulebook(rulebook)
    group_id = _get(group, "group_id")
    parts = _group_parts(group, book_rules)
    trade, subject, structure, quality, record = _subject(book, group_id)
    message = _Message(book, run_parsed)
    grade, as_of = _grade_and_as_of(book, record)
    message.lines.append(" | ".join(["%s %s" % (VERDICT_WORDS[VERDICT_UNAVAILABLE], message.ident(trade)), subject,
                                     "%s: %s" % (message.rules_segment(), UNAVAILABLE_SENTENCE),
                                     message.quote_segment(grade, as_of), NOT_AN_INSTRUCTION]))
    reasons: List[Dict[str, Any]] = []
    quarantine = _quarantine_record(book, group_id)
    if quarantine is not None:
        for reason in quarantine.get("reasons") or []:
            words = quarantine_words(str(reason))
            message.lines.append("unavailable: %s" % message.reason(words))
            reasons.append({"rule_id": None, "rule_sha": None, "result": "unavailable", "metric": None, "value": None,
                            "threshold": None, "unit": None, "verdict": None, "plan": None, "text": words})
    message.follow_lines(parts, ("unavailable", "fired", "pending", "cooldown"), None, reasons)
    signal_record = _signal_record(run_parsed, trade_id=trade, structure=structure, verdict=VERDICT_UNAVAILABLE,
                                   plan=None, data_quality=quality, reasons=reasons)
    return message.finish(KIND_UNAVAILABLE, signal_record, group_id, VERDICT_UNAVAILABLE)


def _no_history_lines(message: _Message, evaluation: Any, results: Sequence[Any]) -> List[str]:
    """Addendum decision 11, once per message: the sentences of ``evaluation.no_history`` as given, and one line
    naming every rule that needs the previous end-of-day snapshot (``evaluation.no_history_rule_ids``, the
    ``no_history`` results of ``evaluation.no_history`` and of the sections), each id once."""
    lines = []
    ids: List[str] = [rule_id for rule_id in _items(evaluation, "no_history_rule_ids") if isinstance(rule_id, str)]
    found = list(results)
    for item in _items(evaluation, "no_history"):
        if isinstance(item, str):
            lines.append("no history: %s" % message.reason(item))
        elif _is_result(item):
            found.append(item)
    for item in found:
        rule_id = _get(item, "rule_id")
        if _get(item, "result") == RESULT_NO_HISTORY and isinstance(rule_id, str) and rule_id not in ids:
            ids.append(rule_id)
    if ids:
        names = ", ".join(message.name(rule_id) for rule_id in ids)
        line = "no history: your rules %s need the previous end-of-day snapshot" % names
        history = _get(evaluation, "history_date")
        if isinstance(history, datetime.date) and not isinstance(history, datetime.datetime):
            line += " (none for %s)" % message.ident(history.isoformat())
        lines.append(line)
    return lines


def _portfolio_alert(evaluation: Any, book: Mapping[str, Any], run: _Run, rulebook: _Rulebook) -> Optional[Alert]:
    pairs = _section_results(evaluation)
    results = [result for _subject, result in pairs]
    views = []
    for subject, result in pairs:
        view = _view(result, rulebook)
        view.subject = subject
        views.append(view)
    fired = [rule for rule in views if rule.result == RESULT_FIRED]
    if not fired:
        return None
    head = next((rule for rule in fired if rule.verdict == VERDICT_EXIT_SIGNAL), fired[0])
    verdict = VERDICT_EXIT_SIGNAL if head.verdict == VERDICT_EXIT_SIGNAL else VERDICT_WATCH
    kind = KIND_EXIT_SIGNAL if verdict == VERDICT_EXIT_SIGNAL else KIND_WATCH
    message = _Message(book, run)
    grade, as_of = _grade_and_as_of(book, None, everything=True)
    sentence = message.rule_text(head)
    segments = ["%s %s" % (VERDICT_WORDS[verdict], message.ident(PORTFOLIO_TRADE)), PORTFOLIO_WORDS,
                "your rule %s (%s) fired: %s" % (message.name(head.rule_id), message.rules_segment(), sentence)]
    reasons = [_reason_entry(head, sentence)]
    if head.first_observation:
        segments.append(FIRST_OBSERVATION_SENTENCE)
    segments.append(message.quote_segment(grade, as_of))
    plan = message.plan(head)
    if plan is None and verdict == VERDICT_EXIT_SIGNAL:
        raise ValueError("the EXIT_SIGNAL rule %s carries no plan" % head.rule_id)
    if plan is not None:
        segments.append(plan)
    segments.append(NOT_AN_INSTRUCTION)
    message.lines.append(" | ".join(segments))
    parts: Dict[str, List[_Rule]] = {"fired": [rule for rule in fired if rule is not head],
                                     "unavailable": [rule for rule in views if rule.result == "unavailable"],
                                     "pending": [rule for rule in views if rule.result == "pending"],
                                     "cooldown": [rule for rule in views if rule.result == "cooldown"],
                                     "suppressed": [rule for rule in views if rule.result == "suppressed"]}
    message.follow_lines(parts, ("fired", "unavailable", "pending", "cooldown"), None, reasons)
    message.lines.extend(_no_history_lines(message, evaluation, results))
    groups = [group for group in book.get("groups") or [] if _open_legs(book, group)]
    quality = "ok" if all(group.get("data_quality") == "ok" for group in groups) else "unavailable"
    record = _signal_record(run, trade_id=PORTFOLIO_TRADE, structure=PORTFOLIO_TRADE, verdict=verdict, plan=head.plan,
                            data_quality=quality, reasons=reasons)
    return message.finish(kind, record, None, verdict)


def render(evaluation: Any, book: Mapping[str, Any], run_info: Mapping[str, Any]) -> List[Alert]:
    """Every message the evaluation calls for, in the evaluation's group order: an EXIT SIGNAL, WATCH or
    UNAVAILABLE message per group with that verdict (HOLD and a group without a verdict post nothing; any other
    verdict is ``ValueError``), then the portfolio section's message when one of its rules fired. Which of them are posted is the caller's posting rules (transitions, the ladder)."""
    run = _run(run_info)
    rulebook = _Rulebook(evaluation)
    out: List[Alert] = []
    for group_verdict in _items(evaluation, "groups"):
        verdict = _get(group_verdict, "verdict")
        if verdict == VERDICT_EXIT_SIGNAL:
            primary = _primary(group_verdict, VERDICT_EXIT_SIGNAL, rulebook)
            out.append(_signal_text(KIND_EXIT_SIGNAL, group_verdict, primary, book, run_info, rulebook))
        elif verdict == VERDICT_WATCH:
            primary = _primary(group_verdict, VERDICT_WATCH, rulebook)
            out.append(_signal_text(KIND_WATCH, group_verdict, primary, book, run_info, rulebook))
        elif verdict == VERDICT_UNAVAILABLE:
            out.append(unavailable_banner(group_verdict, book, run_info, rulebook))
        elif verdict is not None and verdict != VERDICT_HOLD:
            raise ValueError("group %s has the unknown verdict %r" % (_get(group_verdict, "group_id"), verdict))
    portfolio = _portfolio_alert(evaluation, book, run, rulebook)
    if portfolio is not None:
        out.append(portfolio)
    return out


def verdict_counts(evaluation: Any) -> Dict[str, int]:
    """The number of groups per verdict, in precedence order (:data:`VERDICTS`); a group without a verdict (nothing
    open to judge) is not counted."""
    counts = {verdict: 0 for verdict in VERDICTS}
    for group_verdict in _items(evaluation, "groups"):
        verdict = _get(group_verdict, "verdict")
        if verdict is None:
            continue
        if verdict not in counts:
            raise ValueError("unknown verdict %r" % (verdict,))
        counts[verdict] += 1
    return counts


def _sha8(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.match(value.lower()):
        raise ValueError("%s must be a hex sha256, got %r" % (what, value))
    return value.lower()[:8]


def config_changed_text(changes: Sequence[Any], book: Mapping[str, Any], run_info: Mapping[str, Any]) -> Alert:
    """The CONFIG CHANGED block: each changed file with its acknowledged and current sha prefixes. ``changes`` holds
    ``(name, acknowledged_sha | None, current_sha | None)`` tuples or mappings with ``name``, ``old`` and ``new``;
    names ``desk_toml``, ``positions_csv``, ``rulebook`` and ``calendar_toml`` print as the files' words."""
    if not changes:
        raise ValueError("CONFIG CHANGED needs at least one changed file")
    message = _Message(book, _run(run_info))
    segments = ["CONFIG CHANGED"]
    for change in changes:
        if isinstance(change, Mapping):
            name, old, new = change.get("name"), change.get("old"), change.get("new")
        else:
            name, old, new = change
        label = CONFIG_FILE_LABELS.get(str(name), str(name))
        old_text = ("not acknowledged" if old is None
                    else message.ident("sha %s" % _sha8(old, "the acknowledged sha of %s" % label)))
        new_text = "missing" if new is None else message.ident("sha %s" % _sha8(new, "the current sha of %s" % label))
        segments.append("%s: %s -> %s" % (message.reason(label), old_text, new_text))
    segments += [EVALUATED_AS_IS, ACKNOWLEDGE_CONFIG]
    message.lines.append(" | ".join(segments))
    return message.finish(KIND_CONFIG_CHANGED, None)


def rulebook_changed_text(previous_version: Optional[str], changed: Optional[int], book: Mapping[str, Any],
                          run_info: Mapping[str, Any]) -> Alert:
    """``rulebook changed: N verdicts changed | rules <previous> -> rules <adopted>, sha <8 hex>``; ``changed`` is
    ``None`` when no snapshot let the adoption compute the digest."""
    run = _run(run_info)
    if previous_version is not None and (not isinstance(previous_version, str) or not _VERSION_RE.match(previous_version)):
        raise ValueError("the previous rulebook version must be X.Y.Z or None")
    message = _Message(book, run)
    if changed is None:
        digest = "digest unavailable (no snapshot)"
    else:
        digest = "%s %s changed" % (fmt_count(changed, message.emitted), "verdict" if changed == 1 else "verdicts")
    versions = ("%s -> %s" % (message.ident("rules %s" % previous_version), message.rules_segment()) if previous_version
                else message.rules_segment())
    message.lines.append("rulebook changed: %s | %s" % (digest, versions))
    return message.finish(KIND_RULEBOOK_CHANGED, None)


def _data_age(message: _Message) -> str:
    age = message.run.quote_age_min
    return "data age: unknown" if age is None else "data age: %s" % fmt_minutes(age, message.emitted)


def pre_close_line(counts: Any, open_signals: Any, book: Mapping[str, Any], run_info: Mapping[str, Any]) -> Alert:
    """``pre-close | verdicts: ... | open signals: N | data age: N min | rules ... | verify live quotes in your
    broker before acting``. ``counts`` is :func:`verdict_counts`' mapping or an evaluation; ``open_signals`` a count
    or a sequence of open signals."""
    if isinstance(counts, Mapping) and any(verdict in counts for verdict in VERDICTS):
        tally = {verdict: counts.get(verdict, 0) for verdict in VERDICTS}
    else:
        tally = verdict_counts(counts)
    number = open_signals if isinstance(open_signals, int) and not isinstance(open_signals, bool) else len(open_signals)
    message = _Message(book, _run(run_info))
    verdicts = ", ".join("%s %s" % (fmt_count(tally[verdict], message.emitted), VERDICT_WORDS[verdict])
                         for verdict in VERDICTS)
    message.lines.append(" | ".join(["pre-close", "verdicts: %s" % verdicts,
                                     "open signals: %s" % fmt_count(number, message.emitted), _data_age(message),
                                     message.rules_segment(), VERIFY_LIVE_QUOTES]))
    return message.finish(KIND_PRE_CLOSE, None)


def watch_alive_line(book: Mapping[str, Any], run_info: Mapping[str, Any]) -> Alert:
    """``watch alive | nothing posted since the open | rules ... | data age: N min``."""
    message = _Message(book, _run(run_info))
    message.lines.append(" | ".join(["watch alive", "nothing posted since the open", message.rules_segment(),
                                     _data_age(message)]))
    return message.finish(KIND_WATCH_ALIVE, None)


def compose(parts: Sequence[Alert]) -> Alert:
    """One message from several, in the order given (CONFIG CHANGED first when the caller puts it first): their
    bodies, then one footer (the newest as-of among them); the emitted numbers and rule ids of all; the most severe
    kind; no signal record (each part keeps its own)."""
    if not parts:
        raise ValueError("compose needs at least one alert")
    newest = max(parts, key=lambda part: part.as_of or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc))
    emitted: List[str] = []
    rule_ids: List[str] = []
    identifiers: List[str] = []
    clocks: List[str] = []
    for part in parts:
        emitted.extend(item for item in part.emitted_numbers if item not in emitted)
        rule_ids.extend(item for item in part.rule_ids if item not in rule_ids)
        identifiers.extend(item for item in part.identifiers or [] if item not in identifiers)
        clocks.extend(item for item in part.clocks or [] if item not in clocks)
    body = "\n".join(part.body for part in parts)
    kind = min((part.kind for part in parts), key=_KIND_SEVERITY.index)
    return Alert(text=body + "\n" + newest.footer, emitted_numbers=emitted, signal_record=None, kind=kind, body=body,
                 footer=newest.footer, rule_ids=rule_ids, as_of=newest.as_of, identifiers=identifiers, clocks=clocks)


def hold_text(trade_id: str, subject_words: str, sentence: str, book: Mapping[str, Any], run_info: Mapping[str, Any],
              group_id: Optional[str] = None) -> Alert:
    """``HOLD <trade> | <subject> | rules X.Y.Z, sha <8 hex>: <sentence> | not an instruction ...`` plus the footer:
    the notice a caller posts when a group (or the portfolio section) is back at HOLD after a posted verdict."""
    message = _Message(book, _run(run_info))
    trade = message.ident(trade_id) if _TRADE_RE.match(trade_id) else "a group"
    message.lines.append(" | ".join(["HOLD %s" % trade, subject_words, "%s: %s" % (message.rules_segment(),
                                                                                  message.reason(sentence)),
                                     NOT_AN_INSTRUCTION]))
    return message.finish(KIND_WATCH, None, group_id, VERDICT_HOLD)


def withheld_text(kind: str, trade_id: Optional[str], finding_codes: Sequence[str], book: Mapping[str, Any],
                  run_info: Mapping[str, Any], group_id: Optional[str] = None, head: Optional[str] = None) -> Alert:
    """The fixed banner posted in place of a part whose text failed the lint (``<HEAD> <trade> | rules X.Y.Z, sha
    <8 hex>: the message text failed the alert lint (<codes>) and is withheld; exit_check.py shows ...``): it names
    the kind, a trade id of the desk's own shape (else ``a group``) and the finding codes, and keeps ``kind`` so the
    caller's exit code and signal stand; ``head`` replaces the kind's heading (``HOLD`` for a HOLD notice, which is a
    ``watch``-kind message)."""
    if kind not in KIND_HEADS:
        raise ValueError("unknown alert kind %r" % (kind,))
    if head is not None and head not in ("HOLD",) + tuple(KIND_HEADS.values()):
        raise ValueError("unknown heading %r" % (head,))
    message = _Message(book, _run(run_info))
    trade = message.ident(trade_id) if isinstance(trade_id, str) and _TRADE_RE.match(trade_id) else "a group"
    codes = ", ".join(code for code in finding_codes if code in alert_lint.CODES) or "a finding"
    message.lines.append(" | ".join(["%s %s" % (head or KIND_HEADS[kind], trade), "%s: %s" % (message.rules_segment(),
                                                                                       WITHHELD_SENTENCE % codes),
                                     NOT_AN_INSTRUCTION]))
    return message.finish(kind, None, group_id, None)


__all__ = [
    "ACKNOWLEDGE_CONFIG",
    "Alert",
    "BOOL_METRICS",
    "BOOL_OPS",
    "CELL_WORDS",
    "CONFIG_FILE_LABELS",
    "DEFAULT_DAY_UNITS",
    "EVALUATED_AS_IS",
    "FIRST_OBSERVATION_SENTENCE",
    "GROUP_WORDS",
    "KIND_HEADS",
    "KINDS",
    "KIND_CONFIG_CHANGED",
    "KIND_EXIT_SIGNAL",
    "KIND_PRE_CLOSE",
    "KIND_RULEBOOK_CHANGED",
    "KIND_UNAVAILABLE",
    "KIND_WATCH",
    "KIND_WATCH_ALIVE",
    "METRIC_LABELS",
    "METRIC_UNITS",
    "NOT_AN_INSTRUCTION",
    "OP_WORDS",
    "PER_SHARE_METRICS",
    "PLAN_WORDS",
    "PORTFOLIO_TRADE",
    "PORTFOLIO_WORDS",
    "QUARANTINED_WORDS",
    "RESULT_FIRED",
    "RESULT_NO_HISTORY",
    "ROWS_WORDS",
    "STRUCTURE_WORDS",
    "UNAVAILABLE_SENTENCE",
    "VERDICTS",
    "VERDICT_EXIT_SIGNAL",
    "VERDICT_HOLD",
    "VERDICT_UNAVAILABLE",
    "VERDICT_WATCH",
    "VERDICT_WORDS",
    "VERIFY_LIVE_QUOTES",
    "WITHHELD_SENTENCE",
    "book_as_of",
    "compose",
    "config_changed_text",
    "exit_signal_text",
    "fmt_count",
    "fmt_days",
    "fmt_delay",
    "fmt_delta",
    "fmt_footer",
    "fmt_minutes",
    "fmt_money",
    "fmt_multiple",
    "fmt_number",
    "fmt_pct",
    "fmt_price",
    "fmt_reason",
    "fmt_strike",
    "fmt_threshold",
    "hold_text",
    "pre_close_line",
    "quarantine_words",
    "render",
    "rulebook_changed_text",
    "unavailable_banner",
    "verdict_counts",
    "watch_alive_line",
    "watch_text",
    "withheld_text",
]
