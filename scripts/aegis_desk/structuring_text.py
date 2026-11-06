"""The Structuring Desk's text: strings only, every number through a formatter that records it (PR 3b).

Designer C's D3-D7 and D15 under the addendum S4-S11; standard library only, no I/O except the journal record and the
report writer at the end. Every structuring text is built line by line with a :class:`Writer` (the ``opening_bell``
pattern): each number goes through one formatter that appends it to ``emitted``, each date, trade id and sha to
``identifiers`` and each clock to ``clocks``, and :func:`finish` closes the text with the structuring footer
(``alert_lint.structuring_footer_line`` at the data's as-of) and lints it strictly (``alert_lint.check(...,
footer="structuring")``, ``rule_ids=[]``, identifiers, clocks and the as-of pinned). A text that fails, or that holds
an OCC symbol, the word FAILS_RULE or a text the owner typed (``owner_texts``: account names, notes), is never printed:
:func:`finish` returns the WITHHELD line with its codes instead. :meth:`Writer.document` is the emitted document a
relay is re-linted against (``"footer": "structuring"``).

The words (designer C's D6 and D15, S4 and S11)
-----------------------------------------------
* Contracts print only in the fixed leg grammar (``leg N of M: closes your short 2 x WMT 2026-11-20 105 call | ...``,
  :func:`leg_line`, checked against ``alert_lint.STRUCTURING_LEG_RES``) or as ``the 2027-01-15 115 call``: never as an
  OCC symbol. An account prints as ``account <n> of desk.toml (<type>)`` when the row's account is the n-th
  ``[[owner.accounts]]`` entry, else ``the account in row <r> of positions.csv`` (:func:`account_words`); a cell the
  owner typed is never printed.
* Both failing tiers print ``FAILS_CRITERION <key>: <words with the value and the owner's band>``; an accepted label
  ``accepted <key>: <words> (by #assign-ok)`` or ``(by your structuring.toml)``. The word FAILS_RULE is never used.
* The best-candidate block (:func:`best_line`, :data:`CAVEAT_LINE`, :func:`tie_line`, the legs, :func:`reasons_line`,
  the accepted lines, :func:`after_line`, the approval block, :func:`american_line`) names the objective it ranked by and
  never urges; candidate lines (:func:`candidate_line`), the failure counts (:func:`failure_counts_line`), the fixed
  lines (:data:`LIMIT_LINE`, :data:`ACT_LINE`, :data:`AFTER_FILL_LINE`, :data:`AFTER_EXPIRY_LINE`, :data:`MODEL_LINE`,
  :data:`POINTER_LINE`, :func:`validity_line`) and the answers that price nothing (:func:`unavailable_line`,
  :func:`not_applicable_line`, :func:`criteria_missing_line`, :func:`config_unacknowledged_line`,
  :func:`exposure_refused_line`, :func:`refused_line` through ``aegis_desk.approval``, :func:`withheld_line`).
  :func:`roll_analysis_lines` and :func:`proposal_lines` compose the two analyses from a ``structuring.Answer``; the
  ticket tool composes its text from the same builders.
* No line starts with a structure word that is also a verb, nor with roll, close or exit; every fixed line passes the
  strict lint in structuring mode (the tests probe each one).

Records
-------
:func:`journal_record` / :func:`append_journal_record`: one line of ``journal/structuring.jsonl`` per printed text
(designer C's D7.5, :data:`JOURNAL_KEYS`) through ``signals.append_record`` under the desk lock. :func:`write_report`:
the ``.json`` and ``.md`` pair under ``tickets/``, ``reports/rolls/`` or ``reports/proposals/`` (:func:`ticket_stem`,
:func:`roll_stem`, :func:`proposal_stem`), the directory created with ``os.makedirs(exist_ok=True)`` under the desk lock
at the first write, identical bytes a no-op, different bytes :class:`ReportExists` (``ticket_exists``) with nothing
written.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import alert_lint, alerts, approval, expiry, signals, state, structuring
from .desk_paths import DeskPaths

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

FOOTER_KIND: str = alert_lint.FOOTER_STRUCTURING
FEED_DELAY_MIN: int = alert_lint.FOOTER_DELAY_MIN
SOURCE_WORDS: str = "Tradier sandbox"

LIMIT_LINE: str = "you supply the limit; the desk prints none"
ACT_LINE: str = "if you decide to act, you type it into your brokerage account yourself and you supply the limit"
AFTER_FILL_LINE: str = "after a fill, tell Aegis Position Control the fill; it proposes the positions.csv lines"
AFTER_EXPIRY_LINE: str = ("after expiry, the post-expiry check of Aegis Position Control proposes the positions.csv "
                          "lines")
MODEL_LINE: str = ("prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
                   "options is not modelled")
CAVEAT_LINE: str = "the score ranks the candidates only on the objective you chose; it is not a forecast"
POINTER_LINE: str = ("a ticket for one of these: ask the Aegis Structuring Desk for a ROLL ticket with its expiration "
                     "and strike")
NONE_QUALIFIES_LINE: str = "no candidate passed every criterion: the desk names no best candidate"
FIXED_LINES: Tuple[str, ...] = (LIMIT_LINE, ACT_LINE, AFTER_FILL_LINE, AFTER_EXPIRY_LINE, MODEL_LINE, CAVEAT_LINE,
                                POINTER_LINE, NONE_QUALIFIES_LINE)

ACTION_WORDS: Dict[str, str] = {
    structuring.ACTION_CLOSE: "the option legs closed",
    structuring.ACTION_UNWIND_STOCK: "the shares and every option leg closed",
    structuring.ACTION_LET_ASSIGN: "the short option left to expiry",
    structuring.ACTION_MONETIZE_PUT: "the long put closed for its value",
    structuring.ACTION_ROLL: "a roll of the short call",
}
"""The ticket header's words per action; a long put's ROLL reads :data:`ROLL_PUT_WORDS`."""
ROLL_PUT_WORDS: str = "a roll of the long put"
WHAT_WORDS: Tuple[str, ...] = ("roll analysis", "covered call proposal", "protective put proposal", "collar proposal",
                               "CLOSE ticket", "UNWIND_STOCK ticket", "LET_ASSIGN ticket", "MONETIZE_PUT ticket",
                               "ROLL ticket")
"""What an answer that prices nothing is about (the second segment of its line)."""
TIE_BREAK_WORDS: Dict[str, str] = {
    "net_credit": "the net credit (higher first)", "abs_delta": "the absolute delta (lower first)",
    "strike": "the strike (higher first)", "put_strike": "the put strike (higher first)",
    "net_debit": "the net debit (lower first)", "contract": "the contract (in symbol order)",
}
EXPIRATION_WORDS: Dict[int, str] = {1: "the expiration (earlier first)", -1: "the expiration (later first)"}
ARTICLE_WORDS: Dict[str, str] = {"covered_call": "a covered call", "protective_put": "a protective put",
                                 "collar": "a collar"}

CODE_OCC_SYMBOL: str = "occ_symbol"
CODE_FAILS_RULE: str = "fails_rule_word"
CODE_OWNER_TEXT: str = "owner_text"
PRECHECK_CODES: Tuple[str, ...] = (CODE_OCC_SYMBOL, CODE_FAILS_RULE, CODE_OWNER_TEXT)
"""The builders' own refusals, beside the alert lint's codes, in a WITHHELD line."""
FAILS_RULE_WORD: str = "FAILS_RULE"
OCC_RE = re.compile(r"\b[A-Z][A-Z0-9]{0,5}[0-9]{6}[CP][0-9]{8}\b")
"""An OCC option symbol (the compact form): never printed in a structuring text."""
_TRADE_RE = re.compile(r"^AG-[0-9]{8}-[0-9]{2,3}$")
_ROOT_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
_SHA_RE = re.compile(r"^[0-9a-f]{8,64}$")
_TYPE_RE = re.compile(r"^[a-z]{2,10}$")
_STEM_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_CENT = Decimal("0.01")


# --------------------------------------------------------------------------------------------
# The Writer
# --------------------------------------------------------------------------------------------


def _guard(text: str) -> str:
    if OCC_RE.search(text):
        raise ValueError("a structuring text never prints an OCC symbol")
    if FAILS_RULE_WORD in text:
        raise ValueError("a structuring text says FAILS_CRITERION, never FAILS_RULE")
    return text


def _two(value: Decimal) -> str:
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent > -2:
        value = value.quantize(_CENT)
    return format(value, "f")


class Writer:
    """A text's lines with every number, date, id, sha and clock they print (module notes)."""

    def __init__(self) -> None:
        self.emitted: List[str] = []
        self.identifiers: List[str] = []
        self.clocks: List[str] = []

    def _emit(self, text: str) -> str:
        self.emitted.extend(token.text for token in alert_lint.numeric_tokens(text))
        return text

    def _identifier(self, text: str) -> str:
        if text not in self.identifiers:
            self.identifiers.append(text)
        return text

    def number(self, text: str) -> str:
        """A number the desk wrote, as written."""
        return self._emit(_guard(text))

    def price(self, value: Decimal) -> str:
        """A price: at least two decimals, every decimal kept (``3.50``, ``3.425``)."""
        return self._emit(_two(value))

    def money(self, value: Decimal) -> str:
        """Money to the cent (``19006.00``, ``-16.00``)."""
        return self._emit(format(value.quantize(_CENT, rounding=ROUND_HALF_EVEN), "f"))

    def signed_money(self, value: Decimal) -> str:
        """Money to the cent with its sign (``+10.00``, ``-16.00``, ``0.00``)."""
        text = format(value.quantize(_CENT, rounding=ROUND_HALF_EVEN), "f")
        return self._emit("+" + text if value > 0 else text)

    def plain(self, value: Any) -> str:
        """A number exactly as written (an owner's threshold: ``0.50``, ``25``, ``0.15``)."""
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise TypeError("plain() takes an int or a Decimal, got %r" % (value,))
        return self._emit(str(value) if isinstance(value, int) else format(value, "f"))

    def strike(self, value: Decimal) -> str:
        """A strike without trailing zeros (``105``, ``112.5``)."""
        return self._emit(expiry.plain(value))

    def pct(self, value: Decimal) -> str:
        """A percentage as computed (``2.78 %``, ``25 %``)."""
        return self._emit("%s %%" % format(value, "f"))

    def score(self, value: Decimal) -> str:
        """An objective's score as quantized (``0.0467``, ``4.92``)."""
        return self._emit(format(value, "f"))

    def delta(self, value: float) -> str:
        """A share-equivalent delta to two decimals, half-even through its ``repr`` (``134.07``)."""
        return self._emit(format(Decimal(repr(value)).quantize(_CENT, rounding=ROUND_HALF_EVEN), "f"))

    def signed_delta(self, value: float) -> str:
        text = format(Decimal(repr(value)).quantize(_CENT, rounding=ROUND_HALF_EVEN), "f")
        return self._emit("+" + text if value > 0 else text)

    def count(self, value: int) -> str:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("a count must be a non-negative integer, got %r" % (value,))
        return self._emit(str(value))

    def days(self, value: int, unit: str = "calendar") -> str:
        """Whole days with their unit (``73 calendar days``, ``1 calendar day``)."""
        return "%s %s %s" % (self.count(value), unit, "day" if value == 1 else "days")

    def date(self, day: datetime.date) -> str:
        return self._identifier(day.isoformat())

    def clock(self, moment: datetime.datetime) -> str:
        """``HH:MM UTC`` of an aware instant."""
        text = moment.astimezone(UTC).strftime("%H:%M UTC")
        if text not in self.clocks:
            self.clocks.append(text)
        return text

    def et_clock(self, moment: Any) -> str:
        """``HH:MM ET`` of a naive ET time (desk.toml's broker cutoff)."""
        text = moment.strftime("%H:%M ET")
        if text not in self.clocks:
            self.clocks.append(text)
        return text

    def trade_id(self, text: str) -> str:
        if not isinstance(text, str) or not _TRADE_RE.match(text):
            raise ValueError("%r is not a trade id (AG-YYYYMMDD-NN)" % (text,))
        return self._identifier(text)

    def sha(self, digest: str) -> str:
        """``sha <8 hex>`` of a SHA-256 hex digest."""
        if not isinstance(digest, str) or not _SHA_RE.match(digest):
            raise ValueError("%r is not a sha256 hex digest" % (digest,))
        return self._identifier("sha %s" % digest[:8])

    def words(self, text: str) -> str:
        """A sentence the desk's own code wrote (an applicability or UNAVAILABLE reason): unchanged, its numbers
        emitted and its dates, trade ids, shas and clocks recorded; an OCC symbol or FAILS_RULE is refused."""
        _guard(text)
        for start, end, form in alert_lint.whitelist_spans(text, [], footer=FOOTER_KIND):
            span = text[start:end]
            if form == "clock":
                if span not in self.clocks:
                    self.clocks.append(span)
            elif form in ("trade_id", "signal_id", "group_id", "rules_version", "sha", "date"):
                self._identifier(span)
        self.emitted.extend(token.text for token in alert_lint.unwhitelisted_numbers(text, [], footer=FOOTER_KIND))
        return text

    def footer(self, as_of: datetime.datetime, delay_min: int = FEED_DELAY_MIN) -> str:
        """The structuring footer at the data's as-of (``alerts.fmt_footer``)."""
        return alerts.fmt_footer(as_of, delay_min, self.emitted, self.clocks, kind=FOOTER_KIND)

    def document(self, as_of: datetime.datetime) -> Dict[str, Any]:
        """The strict emitted document (``alert_lint.EMITTED_DOCUMENT_KEYS`` plus ``"footer": "structuring"``)."""
        return {"emitted_numbers": list(self.emitted), "rule_ids": [], "identifiers": list(self.identifiers),
                "clocks": list(self.clocks), "as_of": as_of.astimezone(UTC).isoformat(), "footer": FOOTER_KIND}


# --------------------------------------------------------------------------------------------
# Words
# --------------------------------------------------------------------------------------------


def structure_words(structure: str) -> str:
    """The grouping structure in words (``covered call``); ``ValueError`` for anything else."""
    if structure not in alerts.STRUCTURE_WORDS:
        raise ValueError("%r is not a grouping structure" % (structure,))
    return alerts.STRUCTURE_WORDS[structure]


def _root(root: str) -> str:
    if not isinstance(root, str) or not _ROOT_RE.match(root):
        raise ValueError("%r is not an option root" % (root,))
    return root


def _right(right: str) -> str:
    if right not in ("CALL", "PUT"):
        raise ValueError("right must be CALL or PUT, got %r" % (right,))
    return right.lower()


def _plural(count: int, word: str) -> str:
    return word if count == 1 else word + "s"


def account_words(w: Writer, account: str, accounts: Sequence[Any], row_number: int) -> str:
    """``account <n> of desk.toml (<type>)`` when ``account`` is the n-th ``[[owner.accounts]]`` entry's name, else
    ``the account in row <r> of positions.csv`` (designer C's D15): the name itself is never printed."""
    for index, entry in enumerate(accounts, 1):
        if entry.name == account:
            kind = entry.type if isinstance(entry.type, str) and _TYPE_RE.match(entry.type) else "account"
            return "account %s of desk.toml (%s)" % (w.count(index), kind)
    return "the account in row %s of positions.csv" % w.count(row_number)


def contract_words(w: Writer, expiration: datetime.date, strike: Decimal, right: str) -> str:
    """``the 2027-01-15 115 call``: a contract in words, never its OCC symbol."""
    return "the %s %s %s" % (w.date(expiration), w.strike(strike), _right(right))


def _source(w: Writer, as_of: datetime.datetime, delay_min: int = FEED_DELAY_MIN) -> str:
    return "%s, %s, as of %s" % (SOURCE_WORDS, w._emit("%d-min delayed" % delay_min), w.clock(as_of))


def quote_segment(w: Writer, bid: Optional[Decimal], ask: Optional[Decimal], mid: Optional[Decimal], grade: str,
                  as_of: datetime.datetime, note: Optional[str] = None, delay_min: int = FEED_DELAY_MIN) -> str:
    """``bid 3.35, ask 3.50, mid 3.425 | quote grade B, Tradier sandbox, 15-min delayed, as of 19:31 UTC`` (a ticket's
    and a proposal's leg segment); ``note`` (the grade E note) follows after `` | ``."""
    sides = []
    for name, value in (("bid", bid), ("ask", ask), ("mid", mid)):
        if value is not None:
            sides.append("%s %s" % (name, w.price(value)))
    text = "%s | quote grade %s, %s" % (", ".join(sides), grade, _source(w, as_of, delay_min))
    return text + (" | " + note if note else "")


def side_segment(w: Writer, side: str, price: Decimal, grade: str, age_min: Optional[int], note: Optional[str]) -> str:
    """``ask 3.50 | quote grade B, 18 min old``: the conservative side a roll analysis prices a leg at."""
    if side not in ("bid", "ask"):
        raise ValueError("side must be bid or ask, got %r" % (side,))
    age = "" if age_min is None else ", %s min old" % w.count(age_min)
    return "%s %s | quote grade %s%s%s" % (side, w.price(price), grade, age, " | " + note if note else "")


def leg_line(w: Writer, index: int, count: int, effect: str, side: str, quantity: int, root: str,
             expiration: datetime.date, strike: Decimal, right: str, segment: str = "") -> str:
    """``leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | <segment>`` (the fixed leg grammar of designer C's
    D5.6); ``ValueError`` for an effect, side, root or right outside it."""
    if effect == "close":
        head = "closes your %s" % side
    elif effect == "open":
        head = "opens a new %s" % side
    else:
        raise ValueError("effect must be open or close, got %r" % (effect,))
    if side not in ("long", "short"):
        raise ValueError("side must be long or short, got %r" % (side,))
    line = "leg %s of %s: %s %s x %s %s %s %s" % (w.count(index), w.count(count), head, w.count(quantity), _root(root),
                                                  w.date(expiration), w.strike(strike), _right(right))
    if segment:
        line += " | " + _guard(segment)
    if not any(pattern.match(line) for pattern in alert_lint.STRUCTURING_LEG_RES):
        raise ValueError("the leg line is outside the fixed leg grammar")
    return line


def share_leg_line(w: Writer, index: int, count: int, quantity: Decimal, symbol: str, segment: str = "") -> str:
    """``leg 2 of 3: closes 200 of your long WMT shares | <segment>``."""
    if not isinstance(symbol, str) or not _SYMBOL_RE.match(symbol):
        raise ValueError("%r is not a share symbol" % (symbol,))
    line = "leg %s of %s: closes %s of your long %s shares" % (w.count(index), w.count(count), w.number(expiry.plain(quantity)),
                                                               symbol)
    if segment:
        line += " | " + _guard(segment)
    if not any(pattern.match(line) for pattern in alert_lint.STRUCTURING_LEG_RES):
        raise ValueError("the leg line is outside the fixed leg grammar")
    return line


def ticket_header(w: Writer, trade_id: str, underlying: str, structure: str, action: str, account: str,
                  computed: datetime.datetime, valid_until: datetime.datetime, full: bool,
                  action_words: Optional[str] = None) -> str:
    """``TICKET <trade_id> | <UNDERLYING> <structure words> | <action words> (action <ACTION>) | <account words> |
    computed HH:MM UTC, valid until HH:MM UTC (30 min)``; ``(30 min)`` only when the ticket runs its full 30 minutes."""
    if action not in ACTION_WORDS:
        raise ValueError("unknown action %r" % (action,))
    words = action_words or ACTION_WORDS[action]
    if words not in tuple(ACTION_WORDS.values()) + (ROLL_PUT_WORDS,):
        raise ValueError("unknown action words %r" % (words,))
    tail = " (%s)" % w._emit("%d min" % structuring.TICKET_MINUTES) if full else ""
    return "TICKET %s | %s %s | %s (action %s) | %s | computed %s, valid until %s%s" % (
        w.trade_id(trade_id), _root(underlying), structure_words(structure), words, action, _guard(account),
        w.clock(computed), w.clock(valid_until), tail)


def roll_header(w: Writer, trade_id: str, underlying: str, structure: str, criteria_sha: str,
                as_of: datetime.datetime) -> str:
    """``STRUCTURING | <trade_id> roll analysis | <UNDERLYING> <structure words> | criteria structuring.toml (sha <8>) |
    Tradier sandbox, 15-min delayed, as of HH:MM UTC``."""
    return "STRUCTURING | %s roll analysis | %s %s | criteria structuring.toml (%s) | %s" % (
        w.trade_id(trade_id), _root(underlying), structure_words(structure), w.sha(criteria_sha), _source(w, as_of))


def proposal_header(w: Writer, symbol: str, structure: str, shares: Decimal, account: str, criteria_sha: str,
                    as_of: datetime.datetime) -> str:
    """``PROPOSAL <SYMBOL> <structure words> | on <n> <SYMBOL> shares you hold in <account words> | criteria
    structuring.toml (sha <8>) | Tradier sandbox, 15-min delayed, as of HH:MM UTC``."""
    if structure not in ARTICLE_WORDS:
        raise ValueError("unknown proposal structure %r" % (structure,))
    return "PROPOSAL %s %s | on %s %s shares you hold in %s | criteria structuring.toml (%s) | %s" % (
        _root(symbol), structure_words(structure), w.number(expiry.plain(shares)), symbol, _guard(account),
        w.sha(criteria_sha), _source(w, as_of))


def roll_summary_line(w: Writer, held: "structuring.HeldLeg", passed: int, failed: int) -> str:
    """``replacement candidates for the short 2026-11-20 105 call: 1 passed your criteria, 19 failed``."""
    return "replacement candidates for the short %s %s %s: %s passed your criteria, %s failed" % (
        w.date(held.expiration), w.strike(held.strike), _right(held.right), w.count(passed), w.count(failed))


def proposal_summary_line(w: Writer, structure: str, shares: Decimal, symbol: str, passed: int, failed: int) -> str:
    """``candidates for a collar on the 100 SPY shares: 4 passed your criteria, 6 failed``."""
    if structure not in ARTICLE_WORDS:
        raise ValueError("unknown proposal structure %r" % (structure,))
    return "candidates for %s on the %s %s shares: %s passed your criteria, %s failed" % (
        ARTICLE_WORDS[structure], w.number(expiry.plain(shares)), _root(symbol), w.count(passed), w.count(failed))


def best_line(w: Writer, rank: int, total: int, objective: Any, score: Decimal, passed: int) -> str:
    """``BEST CANDIDATE <k> of <n> by your objective <id> (<words>): <score> | the highest among the <n> candidates that
    passed every criterion`` (``lowest`` for a lower-first objective)."""
    extreme = "highest" if objective.direction == "higher" else "lowest"
    return "BEST CANDIDATE %s of %s by your objective %s (%s): %s | the %s among the %s %s that passed every criterion" % (
        w.count(rank), w.count(total), objective.id, w.words(objective.words), w.score(score), extreme, w.count(passed),
        _plural(passed, "candidate"))


def tie_line(w: Writer, objective_id: str, key: str, kind: Optional[str] = None) -> str:
    """``tie on <objective_id> broken by <rule words>``."""
    if key == "expiration":
        direction = structuring.TIE_BREAK_DIRECTIONS.get(kind or structuring.KIND_COVERED_CALL, {}).get(key, 1)
        words = EXPIRATION_WORDS[direction]
    elif key in TIE_BREAK_WORDS:
        words = TIE_BREAK_WORDS[key]
    else:
        raise ValueError("unknown tie-break %r" % (key,))
    return "tie on %s broken by %s" % (_guard(objective_id), words)


# --------------------------------------------------------------------------------------------
# Reasons, labels and candidate lines
# --------------------------------------------------------------------------------------------

_TIME_WORDS: Dict[str, str] = {"pre": "before the open", "post": "after the close", "unknown": "time unknown"}
_UNPRICED_WORDS: Dict[str, str] = {
    structuring.CODE_MARKET_CLOSED: "the market is closed", structuring.CODE_QUOTE_MISSING: "a quote side is missing",
    structuring.CODE_QUOTE_UNDATED: "a quote side is undated", structuring.CODE_QUOTE_STALE: "the quote is stale",
    structuring.CODE_QUOTE_ONE_SIDE_STALE: "one quote side is stale", structuring.CODE_QUOTE_CROSSED: "the quote is crossed",
    structuring.CODE_ZERO_BID: "no bid", structuring.CODE_QUOTE_WIDE: "the spread is wider than the quote grades allow",
    structuring.CODE_NONSTANDARD_CONTRACT: "not a standard 100-share contract",
    structuring.CODE_SPOT_UNAVAILABLE: "no usable quote of the underlying",
    structuring.CODE_SPOT_UNALIGNED: "the underlying's quote is stamped too far from the option's",
    structuring.CODE_EXPIRING: "it expires too soon for a per-day score",
}


def _life(replacement: bool) -> str:
    return "the new call's" if replacement else "the call's"


def _band_words(w: Writer, band: Sequence[Any]) -> str:
    return "%s to %s" % (w.plain(band[0]), w.plain(band[1]))


def reason_words(w: Writer, reason: "structuring.Reason", candidate: Optional["structuring.Candidate"] = None,
                 replacement: bool = False) -> str:
    """The words of one reason with the value the desk read and the owner's threshold (S4)."""
    code, value, threshold = reason.code, reason.value, reason.threshold
    if code == structuring.CODE_DTE_OUTSIDE_WINDOW:
        return "%s outside your window %s" % (w.days(value), _band_words(w, threshold))
    if code == structuring.CODE_DELTA_OUTSIDE_BAND:
        return "absolute delta %s outside your band %s" % (w.plain(value), _band_words(w, threshold))
    if code == structuring.CODE_CREDIT_BELOW_MINIMUM:
        return "bid %s below your minimum %s" % (w.price(value), w.plain(threshold))
    if code == structuring.CODE_SPREAD_ABOVE_MAX:
        return "spread %s above your maximum %s" % (w.pct(value), w.pct(threshold))
    if code == structuring.CODE_FLOOR_OUTSIDE_BAND:
        return "floor %s outside your band %s" % (w.pct(value), _band_words(w, threshold))
    if code == structuring.CODE_CAP_OUTSIDE_BAND:
        return "cap %s outside your band %s" % (w.pct(value), _band_words(w, threshold))
    if code == structuring.CODE_NO_COVER_AT_STRESS:
        return "the put covers no loss at your stress drop of %s" % w.pct(threshold)
    if code == structuring.CODE_NET_DEBIT_ABOVE_BUDGET:
        return "a net debit of %s above your net-debit budget %s" % (w.price(value), w.plain(threshold))
    if code == structuring.CODE_DEBIT_ROLL:
        return "a net debit of %s" % w.price(-value)
    if code in (structuring.CODE_ROLL_DOWN_BELOW_COST, structuring.CODE_STRIKE_BELOW_COST):
        head = "call strike" if candidate is not None and candidate.kind == structuring.KIND_COLLAR else "strike"
        return "%s %s below your stock cost %s" % (head, w.strike(value), w.price(threshold))
    if code == structuring.CODE_CROSSES_EARNINGS:
        return "earnings on %s (%s) fall inside %s life" % (w.date(value["date"]), _TIME_WORDS.get(value["time"], "time "
                                                                                                    "unknown"),
                                                          _life(replacement))
    if code == structuring.CODE_EARNINGS_UNKNOWN:
        if value.get("status") == "passed" and value.get("date") is not None:
            return ("the listed earnings date %s has passed, so the next one is unknown and counts as inside %s life"
                    % (w.date(value["date"]), _life(replacement)))
        return ("the earnings date is unknown in research/calendar.toml, which counts as inside %s life"
                % _life(replacement))
    if code == structuring.CODE_CROSSES_EX_DIVIDEND:
        estimated = "estimated, " if value.get("status") == "estimated" else ""
        return "the ex-dividend date %s (%scum %s) falls inside %s life" % (w.date(value["ex_date"]), estimated,
                                                                            w.date(value["cum_day"]), _life(replacement))
    if code == structuring.CODE_DIVIDEND_UNKNOWN:
        status = value.get("status")
        if status == "second_ex_date":
            return "a second ex-date may fall before %s last trading day; the calendar holds one" % _life(replacement)
        if status == "passed" and value.get("ex_date") is not None:
            return ("the listed ex-dividend date %s has passed, so the next one is unknown and counts as inside %s life"
                    % (w.date(value["ex_date"]), _life(replacement)))
        return "the dividend is unknown in research/calendar.toml, which counts as inside %s life" % _life(replacement)
    if code in _UNPRICED_WORDS:
        return _UNPRICED_WORDS[code]
    if reason.code.startswith(structuring.IV_CODE_PREFIX):
        return "no implied volatility from its mid"
    raise ValueError("unknown reason code %r" % (code,))


def criterion_line(w: Writer, reason: "structuring.Reason", candidate: Optional["structuring.Candidate"] = None,
                   replacement: bool = False) -> str:
    """``FAILS_CRITERION <key>: <words>`` (both failing tiers, S4), or ``not priced: <words>`` for an UNPRICED reason."""
    if reason.key == structuring.KEY_UNPRICED:
        return "not priced: %s" % reason_words(w, reason, candidate, replacement)
    if reason.key not in structuring.REASON_KEY_ORDER:
        raise ValueError("unknown criteria key %r" % (reason.key,))
    return "FAILS_CRITERION %s: %s" % (reason.key, reason_words(w, reason, candidate, replacement))


def accepted_line(w: Writer, label: "structuring.Accepted", replacement: bool = False) -> str:
    """``accepted <key>: <words> (by #assign-ok)`` or ``(by your structuring.toml)``."""
    if label.by not in (structuring.BY_ASSIGN_OK, structuring.BY_EARNINGS_OK, structuring.BY_CRITERIA):
        raise ValueError("unknown acceptance %r" % (label.by,))
    reason = structuring.Reason(label.key, label.code, label.value, label.threshold)
    return "accepted %s: %s (by %s)" % (label.key, reason_words(w, reason, None, replacement), label.by)


def reasons_line(w: Writer, candidate: "structuring.Candidate", section: Any, max_spread_pct: Decimal,
                 s0: Decimal) -> str:
    """``reasons: <each criterion the candidate passed, with its value and the owner's band>; ...``."""
    leg = candidate.legs[0]
    parts: List[str] = []
    kind = candidate.kind
    parts.append("%s inside your window %s" % (w.days(leg.dte_calendar), _band_words(w, section.dte)))
    if kind in (structuring.KIND_COVERED_CALL, structuring.KIND_REPLACEMENT_CALL):
        parts.append("absolute delta %s inside your band %s" % (w.plain(leg.abs_delta), _band_words(w, section.abs_delta)))
        parts.append("bid %s at or above your minimum %s" % (w.price(leg.bid), w.plain(section.min_credit)))
        parts.append("spread %s within your maximum %s" % (w.pct(leg.spread_pct), w.pct(max_spread_pct)))
        if kind == structuring.KIND_REPLACEMENT_CALL and section.credit_only:
            net = candidate.net_credit
            parts.append("a net credit of %s (%s in all) passes credit_only" % (w.price(net), w.money(candidate.net_credit_total)))
        if section.strike_floor == "cost_basis":
            relation = "above" if leg.strike > s0 else "at"
            parts.append("strike %s %s your stock cost %s" % (w.strike(leg.strike), relation,
                                                             w.price(structuring.payoff.quantize_price(s0))))
    elif kind in (structuring.KIND_PROTECTIVE_PUT, structuring.KIND_REPLACEMENT_PUT):
        parts.append("floor %s inside your band %s" % (w.pct(candidate.floor_pct), _band_words(w, section.floor_pct)))
        parts.append("it covers %s per share (%s in all) at your stress drop of %s (stressed price %s)"
                     % (w.number(format(candidate.covered_per_share, "f")), w.money(candidate.covered_total),
                        w.pct(section.stress_drop_pct), w.number(format(leg.strike - candidate.covered_per_share, "f"))))
        parts.append("spread %s within your maximum %s" % (w.pct(leg.spread_pct), w.pct(max_spread_pct)))
    elif kind == structuring.KIND_COLLAR:
        put, call = candidate.legs
        parts.append("floor %s inside your band %s" % (w.pct(candidate.floor_pct), _band_words(w, section.floor_pct)))
        parts.append("cap %s inside your band %s" % (w.pct(candidate.cap_pct), _band_words(w, section.cap_pct)))
        debit = candidate.net_debit
        words = "a net credit of %s" % w.price(-debit) if debit < 0 else "a net debit of %s" % w.price(debit)
        parts.append("%s within your net-debit budget %s" % (words, w.plain(section.max_net_debit)))
        parts.append("spreads %s and %s within your maximum %s" % (w.pct(put.spread_pct), w.pct(call.spread_pct),
                                                                    w.pct(max_spread_pct)))
        if section.strike_floor == "cost_basis":
            relation = "above" if call.strike > s0 else "at"
            parts.append("call strike %s %s your stock cost %s" % (w.strike(call.strike), relation,
                                                                  w.price(structuring.payoff.quantize_price(s0))))
    else:
        raise ValueError("unknown candidate kind %r" % (kind,))
    return "reasons: " + "; ".join(parts)


def _metric(w: Writer, candidate: "structuring.Candidate") -> Optional[str]:
    leg = candidate.legs[0]
    if candidate.kind == structuring.KIND_REPLACEMENT_CALL:
        return None if candidate.net_credit is None else "net credit %s" % w.price(candidate.net_credit)
    if candidate.kind == structuring.KIND_COVERED_CALL:
        return None if leg.bid is None else "bid %s" % w.price(leg.bid)
    if candidate.kind in (structuring.KIND_PROTECTIVE_PUT, structuring.KIND_REPLACEMENT_PUT):
        return None if leg.ask is None else "ask %s" % w.price(leg.ask)
    if candidate.net_debit is None:
        return None
    if candidate.net_debit < 0:
        return "net credit %s" % w.price(-candidate.net_debit)
    return "net debit %s" % w.price(candidate.net_debit)


def candidate_line(w: Writer, candidate: "structuring.Candidate", held: Optional["structuring.HeldLeg"] = None,
                   objective_id: Optional[str] = None) -> str:
    """``candidate <k>: <the contracts in words> | <its metric> | <objective> <score>`` and, for a failing candidate,
    every reason as `` | FAILS_CRITERION <key>: <words>`` (`` | not priced: ...`` when unpriced)."""
    kind = candidate.kind
    objective = objective_id or structuring.kind_objective(kind).id
    if kind == structuring.KIND_REPLACEMENT_CALL:
        if held is None:
            raise ValueError("a replacement line needs the held leg")
        what = "closes %s and opens %s" % (contract_words(w, held.expiration, held.strike, held.right),
                                           contract_words(w, candidate.expiration, candidate.legs[0].strike, "CALL"))
    elif kind == structuring.KIND_COLLAR:
        put, call = candidate.legs
        what = "%s and %s" % (contract_words(w, put.expiration, put.strike, "PUT"),
                              contract_words(w, call.expiration, call.strike, "CALL"))
    else:
        leg = candidate.legs[0]
        what = contract_words(w, leg.expiration, leg.strike, leg.right)
    parts = ["candidate %s: %s" % (w.count(candidate.rank), what)]
    metric = _metric(w, candidate)
    if metric is not None:
        parts.append(metric)
    if candidate.score is not None:
        parts.append("%s %s" % (objective, w.score(candidate.score)))
    replacement = kind == structuring.KIND_REPLACEMENT_CALL
    parts.extend(criterion_line(w, reason, candidate, replacement) for reason in candidate.reasons)
    return " | ".join(parts)


def failure_counts_line(w: Writer, failed: int, counts: Mapping[str, int]) -> str:
    """``failed your criteria: 19 (abs_delta 15, credit_only 4, strike_floor 4)`` (keys in the criteria order)."""
    for key in counts:
        if key not in structuring.REASON_KEY_ORDER:
            raise ValueError("unknown criteria key %r" % (key,))
    parts = ["%s %s" % (key, w.count(counts[key])) for key in structuring.REASON_KEY_ORDER if key in counts]
    return "failed your criteria: %s%s" % (w.count(failed), " (%s)" % ", ".join(parts) if parts else "")


def outside_bands_line(w: Writer, counts: Mapping[str, int]) -> str:
    """``not paired, outside their bands: 7 puts outside your floor band and 10 calls outside your cap band``."""
    puts, calls = counts.get("puts", 0), counts.get("calls", 0)
    return "not paired, outside their bands: %s %s outside your floor band and %s %s outside your cap band" % (
        w.count(puts), _plural(puts, "put"), w.count(calls), _plural(calls, "call"))


def after_line(w: Writer, after: "structuring.AfterState", before: Optional["structuring.BeforeState"] = None,
               legs: int = 1) -> str:
    """``after both legs: group delta 134.07 (now 101.71), max loss 19006.00 (now 19016.00), max profit 3994.00 (now
    1984.00), breakeven 95.03 (now 95.08)``; ``(now ...)`` only with ``before``."""
    head = "after the leg" if legs == 1 else ("after both legs" if legs == 2 else "after the %s legs" % w.count(legs))

    def delta(value: Optional[float]) -> str:
        return "unavailable" if value is None else w.delta(value)

    def profit(value: Optional[Decimal]) -> str:
        return "unbounded" if value is None else w.money(value)

    def loss(value: Optional[Decimal]) -> str:
        return "unbounded" if value is None else w.money(value)

    def even(value: Optional[Decimal]) -> str:
        return "none" if value is None else w.price(value)

    items = [("group delta", delta(after.group_delta), None if before is None else delta(before.group_delta)),
             ("max loss", loss(after.max_loss), None if before is None else loss(before.max_loss)),
             ("max profit", profit(after.max_profit), None if before is None else profit(before.max_profit)),
             ("breakeven", even(after.breakeven), None if before is None else even(before.breakeven))]
    return "%s: %s" % (head, ", ".join("%s %s%s" % (name, now, "" if then is None else " (now %s)" % then)
                                       for name, now, then in items))


def american_line(w: Writer, candidate: "structuring.Candidate") -> str:
    """The American caveat of a candidate that carries one (designer Q's D4): early exercise may matter and the
    European model does not price it."""
    put = next((leg for leg in candidate.legs if leg.right == "PUT"), None)
    if put is not None and put.abs_delta is not None and put.abs_delta >= structuring.AMERICAN_PUT_ABS_DELTA:
        return ("american-style note: %s is deep in the money (absolute delta %s); early exercise can be worth more "
                "than the European model prices" % (contract_words(w, put.expiration, put.strike, "PUT"),
                                                    w.plain(put.abs_delta)))
    call = next(leg for leg in candidate.legs if leg.right == "CALL")
    return ("american-style note: %s is in the money and its bid's time value is below the dividend before its last "
            "trading day, so an early exercise before the ex-date is possible; the European model does not price it"
            % contract_words(w, call.expiration, call.strike, "CALL"))


# --------------------------------------------------------------------------------------------
# Ticket lines and the answers that price nothing
# --------------------------------------------------------------------------------------------


def validity_line(w: Writer, until: datetime.datetime) -> str:
    """``valid until HH:MM UTC: after that its quotes are too old, and a new ticket needs a new request``."""
    return "valid until %s: after that its quotes are too old, and a new ticket needs a new request" % w.clock(until)


def cash_line(w: Writer, net_conservative: Decimal, net_mid: Decimal, peak_needed: Decimal) -> str:
    """Designer Q's cash line (S11): the conservative side, the mid, the most cash the steps need at once."""
    return ("cash: %s at the conservative prices (mid %s); the most the steps need at once: %s; the desk does not see "
            "your cash balance" % (w.signed_money(net_conservative), w.signed_money(net_mid), w.money(peak_needed)))


def realized_line(w: Writer, conservative: Decimal, mid: Decimal, legs: int = 1) -> str:
    """``realized on the closing leg: -16.00 at the conservative side (mid -1.00)``."""
    head = "the closing leg" if legs == 1 else "the closing legs"
    return "realized on %s: %s at the conservative side (mid %s)" % (head, w.signed_money(conservative),
                                                                     w.signed_money(mid))


def let_assign_line(w: Writer, strike: Decimal, right: str, broker_cutoff_et: Any) -> str:
    """``no leg to enter: if the 105 call is at least 0.01 in the money at expiry, OCC exercises it by exception; your
    broker's cutoff is 16:30 ET (desk.toml)``."""
    return ("no leg to enter: if the %s %s is at least %s in the money at expiry, OCC exercises it by exception; your "
            "broker's cutoff is %s (desk.toml)" % (w.strike(strike), _right(right), w.number("0.01"),
                                                 w.et_clock(broker_cutoff_et)))


def _what(what: str) -> str:
    if what not in WHAT_WORDS:
        raise ValueError("unknown subject words %r" % (what,))
    return what


def unavailable_line(w: Writer, subject: str, what: str, reason: str) -> str:
    """``UNAVAILABLE | <trade_id> <what> | <reason>`` (no data segment: the answer used no quote; the footer stamps
    the instant of the run)."""
    return "UNAVAILABLE | %s %s | %s" % (w.trade_id(subject), _what(what), w.words(reason))


def held_line(w: Writer, held: "structuring.HeldLeg") -> str:
    """``the net credits close the held 2026-11-20 105 call | ask 3.50 | quote grade B, 18 min old``: the conservative
    side a roll analysis with no best candidate prices the close at (the grade E note follows when it applies)."""
    if held.close_price is None or held.grade is None:
        raise ValueError("the held leg has no conservative side")
    return "the net credits close the held %s %s %s | %s" % (
        w.date(held.expiration), w.strike(held.strike), _right(held.right),
        side_segment(w, held.close_side, held.close_price, held.grade, held.quote_age_min, held.note))


def not_applicable_line(w: Writer, subject: str, what: str, reason: str,
                        verdict: str = structuring.VERDICT_NOT_APPLICABLE) -> str:
    """``NOT_APPLICABLE | <trade_id> <what> | <reason>`` (or ``NOTHING_TO_PROPOSE | ...``)."""
    if verdict not in (structuring.VERDICT_NOT_APPLICABLE, structuring.VERDICT_NOTHING_TO_PROPOSE):
        raise ValueError("verdict must be NOT_APPLICABLE or NOTHING_TO_PROPOSE, got %r" % (verdict,))
    return "%s | %s %s | %s" % (verdict, w.trade_id(subject), _what(what), w.words(reason))


def criteria_missing_line(w: Writer) -> str:
    """Designer C's D3.7 ``criteria_missing`` line."""
    return ("UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your criteria (SETUP "
            "step %s)" % w.count(9))


def _changes(w: Writer, changes: Sequence[Mapping[str, Optional[str]]]) -> str:
    segments = []
    for change in changes:
        name = str(change["name"])
        label = alerts.CONFIG_FILE_LABELS.get(name)
        if label is None:
            raise ValueError("unknown configuration file %r" % (name,))
        old, new = change.get("old"), change.get("new")
        segments.append("%s: %s -> %s" % (label, "not acknowledged" if old is None else w.sha(old),
                                          "missing" if new is None else w.sha(new)))
    if not segments:
        raise ValueError("no configuration change to print")
    return "CONFIG CHANGED: " + " | ".join(segments)


def config_unacknowledged_line(w: Writer, changes: Sequence[Mapping[str, Optional[str]]]) -> str:
    """Designer C's D3.6 line: the CONFIG CHANGED segments in exit_check's words, then why nothing else prints."""
    return ("UNAVAILABLE | %s | the desk names no candidate and prints no ticket while desk.toml, positions.csv or "
            "structuring.toml differ from what you acknowledged | acknowledge with ack.py config --owner-tapped"
            % _changes(w, changes))


def config_changed_line(w: Writer, changes: Sequence[Mapping[str, Optional[str]]]) -> str:
    """The line printed first when only the adopted rulebook changed (it does not block a structuring text)."""
    return ("%s | the structuring text below reads the files as they are now; acknowledge with ack.py config "
            "--owner-tapped" % _changes(w, changes))


def withheld_line(w: Writer, tool: str, codes: Sequence[str]) -> str:
    """``WITHHELD | the text of <tool>.py failed the alert lint (<codes>) and is withheld; the JSON of <tool>.py --json
    shows it``."""
    if tool not in structuring.TOOLS:
        raise ValueError("unknown tool %r" % (tool,))
    known = set(alert_lint.CODES) | set(PRECHECK_CODES)
    if not codes or any(code not in known for code in codes):
        raise ValueError("unknown lint codes %r" % (list(codes),))
    return ("WITHHELD | the text of %s.py failed the alert lint (%s) and is withheld; the JSON of %s.py --json shows it"
            % (tool, ", ".join(codes), tool))


def exposure_refused_line(w: Writer, trade_id: str, underlying: str, structure: str, leg_index: int,
                          uncovered_calls: int) -> str:
    """The REFUSED line of an ``intermediate_exposure`` (S5): an invariant breach, the canonical orders never cause it."""
    return ("REFUSED %s | %s %s | after leg %s of this order you would hold %s uncovered %s (level %s): the desk prints "
            "no order with an uncovered step | your broker's own approval decides what you can enter"
            % (w.trade_id(trade_id), _root(underlying), structure_words(structure), w.count(leg_index),
               w.count(uncovered_calls), _plural(uncovered_calls, "call"), w.count(4)))


def refused_line(w: Writer, answer: "structuring.Answer", result: "approval.ApprovalResult",
                 structure: Optional[str] = None) -> str:
    """The REFUSED line of an approval-level refusal (``aegis_desk.approval.refusal_line``, S7); ``structure`` names
    what the order would make (a proposal's structure), the group's structure by default."""
    group = answer.group_context
    if group is None:
        raise ValueError("the answer loaded no group")
    return approval.refusal_line(result, w.trade_id(answer.trade_id), _root(group.underlying),
                                 structure_words(structure or group.structure), w.emitted)


def multi_leg_line(structure: str = "roll") -> str:
    """The multi-leg note (``approval.MULTI_LEG_NOTE`` for a roll; a collar's names the collar)."""
    if structure == "roll":
        return approval.MULTI_LEG_NOTE
    if structure == "collar":
        return approval.MULTI_LEG_NOTE.replace("class a roll as", "class a collar as")
    raise ValueError("unknown multi-leg structure %r" % (structure,))


def approval_block(w: Writer, answer: "structuring.Answer", candidate: "structuring.Candidate",
                   owner_level: int) -> List[str]:
    """The leg-order, other-order, multi-leg and approval lines of a candidate that passes the approval-level check
    (``aegis_desk.approval.approval_lines``); ``ValueError`` when it is refused (print :func:`refused_line`)."""
    group = answer.group_context
    if group is None:
        raise ValueError("the answer loaded no group")
    lines = approval.approval_lines(structuring.approval_state(answer), structuring.approval_legs(answer, candidate),
                                    owner_level, _root(group.underlying), w.emitted)
    if candidate.kind == structuring.KIND_COLLAR:
        lines = [multi_leg_line("collar") if line == approval.MULTI_LEG_NOTE else line for line in lines]
    return lines


# --------------------------------------------------------------------------------------------
# The two analyses, composed from an Answer
# --------------------------------------------------------------------------------------------


_PRICED_VERDICTS = (structuring.VERDICT_BEST, structuring.VERDICT_NONE_QUALIFIES, structuring.VERDICT_PRICED)


def data_as_of(answer: "structuring.Answer") -> Optional[datetime.datetime]:
    """The footer's instant: for an answer that priced candidates, the oldest stamp of the data it used (the spot, the
    held leg's conservative side and every priced leg); for any other answer the instant of the run (a quote it did
    not use is never the text's as-of); ``None`` without a target."""
    if answer.target is None:
        return None
    if answer.verdict not in _PRICED_VERDICTS:
        return answer.target.now
    stamps: List[datetime.datetime] = []
    if answer.pricing is not None and answer.pricing.spot.as_of is not None:
        stamps.append(answer.pricing.spot.as_of)
    if answer.held is not None and answer.held.usable and answer.held.as_of is not None:
        stamps.append(answer.held.as_of)
    if answer.ranking is not None:
        stamps.extend(leg.as_of for candidate in answer.ranking.candidates for leg in candidate.legs
                      if leg.as_of is not None and leg.unpriced is None)
    return min(stamps) if stamps else answer.target.now


def _quiet_reason(answer: "structuring.Answer") -> str:
    if answer.cause == structuring.NA_DISABLED:
        return "the %s section is %s" % (answer.section_name, answer.reason)
    return answer.reason or ""


def _best_legs_roll(w: Writer, answer: "structuring.Answer", best: "structuring.Candidate") -> List[str]:
    held = answer.held
    assert held is not None and held.close_price is not None and held.grade is not None
    leg = best.legs[0]
    assert leg.bid is not None and leg.grade is not None
    return [
        leg_line(w, 1, 2, "close", "short", held.contracts, held.root, held.expiration, held.strike, held.right,
                 side_segment(w, held.close_side, held.close_price, held.grade, held.quote_age_min, held.note)),
        leg_line(w, 2, 2, "open", "short", best.contracts, leg.root, leg.expiration, leg.strike, leg.right,
                 side_segment(w, "bid", leg.bid, leg.grade, leg.quote_age_min, None)),
    ]


def _best_legs_proposal(w: Writer, best: "structuring.Candidate") -> List[str]:
    lines = []
    count = len(best.legs)
    for index, leg in enumerate(best.legs, 1):
        side = "long" if leg.right == "PUT" else "short"
        assert leg.as_of is not None and leg.grade is not None
        lines.append(leg_line(w, index, count, "open", side, best.contracts, leg.root, leg.expiration, leg.strike,
                              leg.right, quote_segment(w, leg.bid, leg.ask, leg.mid, leg.grade, leg.as_of)))
    return lines


def _best_head(w: Writer, answer: "structuring.Answer", best: "structuring.Candidate") -> List[str]:
    ranking = answer.ranking
    assert ranking is not None and best.score is not None
    lines = [best_line(w, best.rank, len(ranking.candidates), ranking.objective, best.score, ranking.passed), CAVEAT_LINE]
    if ranking.tie_break is not None:
        lines.append(tie_line(w, ranking.objective.id, ranking.tie_break, best.kind))
    return lines


def _best_tail(w: Writer, answer: "structuring.Answer", best: "structuring.Candidate", replacement: bool) -> List[str]:
    group = answer.group_context
    assert group is not None and answer.pricing is not None and best.after is not None
    lines = [reasons_line(w, best, answer.section, answer.pricing.max_spread_pct, group.s0)]
    lines.extend(accepted_line(w, label, replacement) for label in best.accepted)
    legs = len(best.legs) + (1 if answer.held is not None else 0)
    lines.append(after_line(w, best.after, answer.before, legs))
    return lines


def roll_analysis_lines(w: Writer, answer: "structuring.Answer") -> List[str]:
    """The roll analysis text of designer C's D6 with S11 (without the footer: :func:`finish` adds it): the header, the
    counts, the best-candidate block (or :data:`NONE_QUALIFIES_LINE`), up to ``max_candidates_shown`` candidates in
    all, the failure counts, :data:`POINTER_LINE` and :data:`MODEL_LINE`; one line for an answer that prices nothing."""
    subject = answer.trade_id
    if answer.verdict in (structuring.VERDICT_NOT_APPLICABLE, structuring.VERDICT_NOTHING_TO_PROPOSE):
        return [not_applicable_line(w, subject, "roll analysis", _quiet_reason(answer), answer.verdict)]
    as_of = data_as_of(answer)
    assert as_of is not None
    if answer.verdict == structuring.VERDICT_UNAVAILABLE:
        return [unavailable_line(w, subject, "roll analysis", answer.reason or "")]
    target, ranking, held, group = answer.target, answer.ranking, answer.held, answer.group_context
    assert target is not None and target.group is not None and ranking is not None and held is not None
    assert group is not None
    lines = [roll_header(w, subject, group.underlying, group.structure, answer.criteria.sha256, as_of),
             roll_summary_line(w, held, ranking.passed, ranking.failed)]
    shown = answer.criteria.general.max_candidates_shown
    rest = list(ranking.candidates)
    best = ranking.best
    if best is not None:
        lines.extend(_best_head(w, answer, best))
        lines.extend(_best_legs_roll(w, answer, best))
        lines.extend(_best_tail(w, answer, best, True))
        if best.american_caveat:
            lines.append(american_line(w, best))
        rest, shown = rest[1:], shown - 1
    else:
        lines.extend([NONE_QUALIFIES_LINE, held_line(w, held)])
    lines.extend(candidate_line(w, candidate, held, ranking.objective.id) for candidate in rest[:max(0, shown)])
    lines.extend([failure_counts_line(w, ranking.failed, ranking.failure_counts), POINTER_LINE, MODEL_LINE])
    return lines


def proposal_lines(w: Writer, answer: "structuring.Answer", owner_level: int) -> List[str]:
    """The proposal text of designer C's D6 with S11 (without the footer): the header, the counts, the best-candidate
    block with its leg lines and the approval block, other candidates, the failure counts, the collar's legs outside
    their bands, :data:`MODEL_LINE`, :data:`LIMIT_LINE` and :data:`ACT_LINE`. A best candidate the approval-level check
    refuses gives the REFUSED line alone (exit 20 in the tool); an answer that prices nothing gives its one line."""
    subject = answer.trade_id
    structure = answer.structure_requested or answer.section_name
    what = "%s proposal" % alerts.STRUCTURE_WORDS.get(structure, structure)
    if answer.verdict in (structuring.VERDICT_NOT_APPLICABLE, structuring.VERDICT_NOTHING_TO_PROPOSE):
        return [not_applicable_line(w, subject, what, _quiet_reason(answer), answer.verdict)]
    as_of = data_as_of(answer)
    assert as_of is not None
    if answer.verdict == structuring.VERDICT_UNAVAILABLE:
        return [unavailable_line(w, subject, what, answer.reason or "")]
    target, ranking, group = answer.target, answer.ranking, answer.group_context
    assert target is not None and target.group is not None and ranking is not None and group is not None
    best = ranking.best
    if best is not None:
        result = structuring.approval_check(answer, best, owner_level)
        if not result.ok:
            return [refused_line(w, answer, result, structure)]
        if result.exposure_step is not None:
            uncovered = result.steps[result.exposure_step - 1].after.uncovered_calls
            return [exposure_refused_line(w, subject, group.underlying, group.structure, result.exposure_step, uncovered)]
    stock = [row for row in target.group.open_legs if row.is_stock_like]
    account = account_words(w, group.account, target.market.cfg.owner.accounts,
                            stock[0].row_number if stock else target.group.legs[0].row_number)
    lines = [proposal_header(w, group.underlying, structure, group.shares, account, answer.criteria.sha256, as_of),
             proposal_summary_line(w, structure, group.shares, group.underlying, ranking.passed, ranking.failed)]
    shown = answer.criteria.general.max_candidates_shown
    rest = list(ranking.candidates)
    if best is not None:
        lines.extend(_best_head(w, answer, best))
        lines.extend(_best_legs_proposal(w, best))
        lines.extend(_best_tail(w, answer, best, False))
        lines.extend(approval_block(w, answer, best, owner_level))
        if best.american_caveat:
            lines.append(american_line(w, best))
        rest, shown = rest[1:], shown - 1
    else:
        lines.append(NONE_QUALIFIES_LINE)
    lines.extend(candidate_line(w, candidate, None, ranking.objective.id) for candidate in rest[:max(0, shown)])
    lines.append(failure_counts_line(w, ranking.failed, ranking.failure_counts))
    if answer.outside_bands is not None:
        lines.append(outside_bands_line(w, answer.outside_bands))
    lines.extend([MODEL_LINE, LIMIT_LINE, ACT_LINE])
    return lines


def replacement_lines(w: Writer, answer: "structuring.Answer") -> List[str]:
    """The named replacement's lines of a ROLL ticket (S5, S11): each failing reason as ``FAILS_CRITERION <key>:
    <words>`` (the ticket still prints: the owner named it), the accepted labels, the after-state and the American
    caveat when it applies."""
    candidate = answer.candidate
    if candidate is None:
        raise ValueError("the answer priced no named replacement")
    replacement = candidate.kind == structuring.KIND_REPLACEMENT_CALL
    lines = [criterion_line(w, reason, candidate, replacement) for reason in candidate.reasons]
    lines.extend(accepted_line(w, label, replacement) for label in candidate.accepted)
    if candidate.after is not None:
        lines.append(after_line(w, candidate.after, None, 2))
    if candidate.american_caveat:
        lines.append(american_line(w, candidate))
    return lines


# --------------------------------------------------------------------------------------------
# The self-lint: the text, or the WITHHELD line with its codes
# --------------------------------------------------------------------------------------------

_DESK_WORDS = ("cash", "margin", "ira", "account", "call", "put", "shares")


@dataclass(frozen=True)
class Rendered:
    """What :func:`finish` produced: ``text`` is what the tool prints (the linted text when ``ok``, else the WITHHELD
    line and the footer), ``attempted`` the text that was linted, ``writer`` and ``document`` the printed text's own
    emitted record, ``lint`` the result on ``attempted`` and ``codes`` the WITHHELD codes (``[]`` when ``ok``)."""

    ok: bool
    text: str
    attempted: str
    writer: Writer
    document: Dict[str, Any]
    lint: alert_lint.LintResult
    codes: List[str]
    as_of: datetime.datetime

    def lint_dict(self) -> Dict[str, Any]:
        """``payload.lint``: ``{ok, problems, numbers_checked}`` of the attempted text."""
        return {"ok": self.lint.ok, "problems": list(self.lint.problems), "numbers_checked": self.lint.numbers_checked}


def _owner_text_found(text: str, owner_texts: Sequence[str]) -> bool:
    for raw in owner_texts:
        value = (raw or "").strip()
        if len(value) < 4 or value.lower() in _DESK_WORDS:
            continue  # too short to tell apart from the desk's own words
        if value in text:
            return True
    return False


def finish(w: Writer, lines: Sequence[str], as_of: datetime.datetime, tool: str, owner_texts: Sequence[str] = (),
           delay_min: int = FEED_DELAY_MIN) -> Rendered:
    """Close ``lines`` with the structuring footer at ``as_of`` and lint them strictly (module notes). ``owner_texts``
    are the cells the owner typed that must never print (the account names; texts shorter than four characters or
    equal to the desk's own words are not compared). A failure returns the WITHHELD line with its codes."""
    attempted = "\n".join(list(lines) + [w.footer(as_of, delay_min)])
    pre: List[str] = []
    if OCC_RE.search(attempted):
        pre.append(CODE_OCC_SYMBOL)
    if FAILS_RULE_WORD in attempted:
        pre.append(CODE_FAILS_RULE)
    if _owner_text_found(attempted, owner_texts):
        pre.append(CODE_OWNER_TEXT)
    result = alert_lint.check(attempted, w.emitted, rule_ids=[], identifiers=w.identifiers, clocks=w.clocks,
                              as_of=as_of, footer=FOOTER_KIND)
    codes = sorted({finding.code for finding in result.findings} | set(pre))
    if not codes:
        return Rendered(True, attempted, attempted, w, w.document(as_of), result, [], as_of)
    withheld = Writer()
    text = withheld_line(withheld, tool, codes) + "\n" + withheld.footer(as_of, delay_min)
    return Rendered(False, text, attempted, withheld, withheld.document(as_of), result, codes, as_of)


# --------------------------------------------------------------------------------------------
# The journal record (designer C's D7.5) and the report files (S9)
# --------------------------------------------------------------------------------------------

JOURNAL_KEYS: Tuple[str, ...] = ("utc", "tool", "action", "structure", "trade_id", "verdict", "criteria_sha256",
                                 "desk_toml_sha256", "positions_csv_sha256", "best_candidate", "candidates_passed",
                                 "candidates_failed", "refused_code", "text_sha256")
"""One ``journal/structuring.jsonl`` line: designer C's D7.5 keys plus ``verdict``."""


def journal_record(*, now: datetime.datetime, tool: str, trade_id: str, verdict: str, text: str,
                   criteria_sha256: Optional[str], desk_toml_sha256: Optional[str], positions_csv_sha256: Optional[str],
                   action: Optional[str] = None, structure: Optional[str] = None, best_candidate: Optional[str] = None,
                   candidates_passed: Optional[int] = None, candidates_failed: Optional[int] = None,
                   refused_code: Optional[str] = None) -> Dict[str, Any]:
    """The audit record of one printed text (exit 0 or 20; never on 1 or 2: S5): what was suggested, on which files,
    and the sha256 of the text exactly as printed."""
    if tool not in structuring.TOOLS:
        raise ValueError("unknown tool %r" % (tool,))
    values = {"utc": now.astimezone(UTC), "tool": tool, "action": action, "structure": structure, "trade_id": trade_id,
              "verdict": verdict, "criteria_sha256": criteria_sha256, "desk_toml_sha256": desk_toml_sha256,
              "positions_csv_sha256": positions_csv_sha256, "best_candidate": best_candidate,
              "candidates_passed": candidates_passed, "candidates_failed": candidates_failed,
              "refused_code": refused_code, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    return {key: values[key] for key in JOURNAL_KEYS}


def append_journal_record(paths: DeskPaths, record: Mapping[str, Any], *, lock: bool = True) -> None:
    """Append ``record`` to ``journal/structuring.jsonl`` (``signals.append_record``: fsync, a torn line ended first),
    under the desk lock unless the caller holds it (``lock=False``)."""
    if lock:
        with state.locked(paths.lock_path):
            signals.append_record(paths.structuring_journal, dict(record))
    else:
        signals.append_record(paths.structuring_journal, dict(record))


def stamp(now: datetime.datetime) -> str:
    """``YYYYMMDDTHHMMSSZ`` of an aware instant."""
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _stem_trade(trade_id: str) -> str:
    if not isinstance(trade_id, str) or not _TRADE_RE.match(trade_id):
        raise ValueError("%r is not a trade id (AG-YYYYMMDD-NN)" % (trade_id,))
    return trade_id


def ticket_stem(trade_id: str, action: str, now: datetime.datetime) -> str:
    """``TK-<trade_id>-<ACTION>-<YYYYMMDDTHHMMSSZ>`` (S9)."""
    if action not in structuring.ACTIONS:
        raise ValueError("unknown action %r" % (action,))
    return "TK-%s-%s-%s" % (_stem_trade(trade_id), action, stamp(now))


def roll_stem(trade_id: str, now: datetime.datetime) -> str:
    """``<trade_id>-<YYYYMMDDTHHMMSSZ>`` (``reports/rolls/``)."""
    return "%s-%s" % (_stem_trade(trade_id), stamp(now))


def proposal_stem(trade_id: str, structure: str, now: datetime.datetime) -> str:
    """``<trade_id>-<structure>-<YYYYMMDDTHHMMSSZ>`` (``reports/proposals/``)."""
    if structure not in structuring.PROPOSAL_STRUCTURES:
        raise ValueError("unknown proposal structure %r" % (structure,))
    return "%s-%s-%s" % (_stem_trade(trade_id), structure, stamp(now))


class ReportExists(Exception):
    """A report or ticket file already exists with different bytes (exit 1 ``ticket_exists``); nothing was written."""

    code = "ticket_exists"

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__("%s exists with different contents: the desk never overwrites a ticket or a report" % path)


@dataclass(frozen=True)
class ReportFiles:
    """Where :func:`write_report` put the pair, and whether it wrote anything (identical bytes are a no-op)."""

    json_path: str
    md_path: str
    written: bool


def write_report(paths: DeskPaths, directory: str, stem: str, json_text: str, md_text: str, *,
                 lock: bool = True) -> ReportFiles:
    """Write ``<directory>/<stem>.json`` and ``.md`` atomically under the desk lock (module notes): ``directory`` is
    ``tickets/``, ``reports/rolls/`` or ``reports/proposals/`` of the desk root, created at the first write."""
    allowed = (paths.tickets_dir, paths.rolls_dir, paths.proposals_dir)
    if directory not in allowed:
        raise ValueError("%s is not tickets/, reports/rolls/ or reports/proposals/ of the desk root" % directory)
    if not isinstance(stem, str) or not _STEM_RE.match(stem):
        raise ValueError("%r is not a report file stem" % (stem,))
    pairs = [(os.path.join(directory, stem + ".json"), json_text.encode("utf-8")),
             (os.path.join(directory, stem + ".md"), md_text.encode("utf-8"))]

    def write() -> bool:
        os.makedirs(directory, exist_ok=True)
        missing = []
        for path, data in pairs:
            try:
                with open(path, "rb") as handle:
                    current = handle.read()
            except FileNotFoundError:
                missing.append((path, data))
                continue
            if current != data:
                raise ReportExists(path)
        for path, data in missing:
            state.atomic_write_bytes(path, data)
        return bool(missing)

    if lock:
        with state.locked(paths.lock_path):
            written = write()
    else:
        written = write()
    return ReportFiles(pairs[0][0], pairs[1][0], written)


def report_markdown(title: str, text: str) -> str:
    """The ``.md`` report: a heading and the text exactly as printed, in a fenced block."""
    return "# %s\n\n```text\n%s\n```\n" % (title, text)


__all__ = [
    "ACTION_WORDS", "ACT_LINE", "AFTER_EXPIRY_LINE", "AFTER_FILL_LINE", "ARTICLE_WORDS", "CAVEAT_LINE",
    "CODE_FAILS_RULE", "CODE_OCC_SYMBOL", "CODE_OWNER_TEXT", "EXPIRATION_WORDS", "FAILS_RULE_WORD", "FEED_DELAY_MIN",
    "FIXED_LINES", "FOOTER_KIND", "JOURNAL_KEYS", "LIMIT_LINE", "MODEL_LINE", "NONE_QUALIFIES_LINE", "OCC_RE",
    "POINTER_LINE", "PRECHECK_CODES", "ROLL_PUT_WORDS", "Rendered", "ReportExists", "ReportFiles", "SOURCE_WORDS",
    "TIE_BREAK_WORDS", "WHAT_WORDS", "Writer", "accepted_line", "account_words", "after_line", "american_line",
    "append_journal_record", "approval_block", "best_line", "candidate_line", "cash_line", "config_changed_line",
    "config_unacknowledged_line", "contract_words", "criteria_missing_line", "criterion_line", "data_as_of",
    "exposure_refused_line", "failure_counts_line", "finish", "held_line", "journal_record", "leg_line", "let_assign_line",
    "multi_leg_line", "not_applicable_line", "outside_bands_line", "proposal_header", "proposal_lines",
    "proposal_stem", "proposal_summary_line", "quote_segment", "realized_line", "reason_words", "reasons_line",
    "refused_line", "replacement_lines", "report_markdown", "roll_analysis_lines", "roll_header", "roll_stem", "roll_summary_line",
    "share_leg_line", "side_segment", "stamp", "structure_words", "ticket_header", "ticket_stem", "tie_line",
    "unavailable_line", "validity_line", "withheld_line", "write_report",
]
