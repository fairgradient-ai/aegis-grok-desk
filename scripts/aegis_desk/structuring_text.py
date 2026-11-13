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

from . import alert_lint, alerts, approval, data_source, desk_paths, expiry, signals, state, structuring
from .desk_paths import DeskPaths

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

FOOTER_KIND: str = alert_lint.FOOTER_STRUCTURING
FEED_DELAY_MIN: int = alert_lint.FOOTER_DELAY_MIN
SOURCE_WORDS: str = data_source.WORDS
"""The quote source in every structuring text (``Alpaca paper (IEX stocks, indicative options: approximate)``)."""

LIMIT_LINE: str = "you supply the limit; the desk prints none"
ACT_LINE: str = "if you decide to act, you type it into your brokerage account yourself"
AFTER_FILL_LINE: str = "after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines"
AFTER_EXPIRY_LINE: str = ("after expiry, the post-expiry check of Aegis Position Control proposes the positions.csv "
                          "lines")
MODEL_LINE: str = ("prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
                   "options is not modelled")
SPOT_BAR_LINE: str = ("spot: %s option %s quoted more than %s seconds from the stock quote %s priced against the "
                      "stock's one-minute IEX bar of the minute %s quoted in")
"""The note after :data:`MODEL_LINE` when a priced leg solved its volatility against a one-minute bar
(``structuring.attach_spot_bars``); absent otherwise."""
CAVEAT_LINE: str = "the score ranks the candidates only on the objective you chose; it is not a forecast"
POINTER_LINE: str = ("a ticket for one of these: ask the Aegis Desk Lead for a ROLL ticket with its expiration "
                     "and strike")
NONE_QUALIFIES_LINE: str = "no candidate passed every criterion: the desk names no best candidate"
NO_MID_WORDS: str = "no mid: a quote side is stale"
"""What the cash block says for the mids when a leg has no mid."""
CASH_BALANCE_LINE: str = "the desk does not see your cash balance"
LEGS_CAPTION: str = "**the legs:**"
"""A ticket's caption over its leg items (an analysis's legs sit under its BEST CANDIDATE line)."""
FAILURES_CAPTION: str = "**why each one fails:**"
"""The caption over the reasons the shown candidates fail, when none passed (a list of its own under the candidates)."""
FIXED_LINES: Tuple[str, ...] = (LIMIT_LINE, ACT_LINE, AFTER_FILL_LINE, AFTER_EXPIRY_LINE, MODEL_LINE, CAVEAT_LINE,
                                POINTER_LINE, NONE_QUALIFIES_LINE, "**%s**" % NONE_QUALIFIES_LINE, CASH_BALANCE_LINE,
                                LEGS_CAPTION, FAILURES_CAPTION)
ITEM: str = "- "
"""The list mark of an item under a block's first line (the legs, the notes, the reasons none passed)."""

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

    def amount(self, value: Decimal, signed: bool = False) -> str:
        """Money to the cent with thousands separators, as the desk view shows it (``63,240.00``, ``+20,140.00``)."""
        text = "{:,.2f}".format(value.quantize(_CENT, rounding=ROUND_HALF_EVEN))
        return self._emit("+" + text if signed and value > 0 else text)

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


def account_cell(w: Writer, account: str, accounts: Sequence[Any], row_number: int) -> str:
    """:func:`account_words` as an ``account`` column's cell: ``1 of desk.toml (margin)`` or ``row 9 of positions.csv``."""
    for index, entry in enumerate(accounts, 1):
        if entry.name == account:
            kind = entry.type if isinstance(entry.type, str) and _TYPE_RE.match(entry.type) else "account"
            return "%s of desk.toml (%s)" % (w.count(index), kind)
    return "row %s of positions.csv" % w.count(row_number)


def contract_words(w: Writer, expiration: datetime.date, strike: Decimal, right: str) -> str:
    """``the 2027-01-15 115 call``: a contract in words, never its OCC symbol."""
    return "the %s %s %s" % (w.date(expiration), w.strike(strike), _right(right))


def _source(w: Writer, as_of: datetime.datetime, delay_min: int = FEED_DELAY_MIN) -> str:
    return "%s, %s, as of %s" % (SOURCE_WORDS, w._emit("%d-min delayed" % delay_min), w.clock(as_of))


def quotes_line(w: Writer, as_of: datetime.datetime, delay_min: int = FEED_DELAY_MIN) -> str:
    """``quotes: <SOURCE_WORDS>, 15-min delayed, as of 19:31 UTC``: the one place a text names its quote source (an
    item of the header block)."""
    return "quotes: " + _source(w, as_of, delay_min)


def quote_segment(w: Writer, bid: Optional[Decimal], ask: Optional[Decimal], mid: Optional[Decimal], grade: str,
                  note: Optional[str] = None) -> str:
    """``bid 3.35, ask 3.50, mid 3.425 | quote grade B`` (a ticket's and a proposal's leg segment; the source is the
    header's :func:`quotes_line`); ``note`` (the grade E note) follows after `` | ``."""
    sides = []
    for name, value in (("bid", bid), ("ask", ask), ("mid", mid)):
        if value is not None:
            sides.append("%s %s" % (name, w.price(value)))
    if not sides:
        raise ValueError("a quote segment needs at least one side")
    text = "%s | quote grade %s" % (", ".join(sides), grade)
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


def ticket_header(w: Writer, trade_id: str, underlying: str, structure: str, action: str,
                  action_words: Optional[str] = None) -> str:
    """``TICKET <trade_id> | <UNDERLYING> <structure words>: <action words> (action <ACTION>)``: the first line of a
    ticket; the account, the quotes and the validity are its header items."""
    if action not in ACTION_WORDS:
        raise ValueError("unknown action %r" % (action,))
    words = action_words or ACTION_WORDS[action]
    if words not in tuple(ACTION_WORDS.values()) + (ROLL_PUT_WORDS,):
        raise ValueError("unknown action words %r" % (words,))
    return "### TICKET %s | %s %s: %s (action %s)" % (w.trade_id(trade_id), _root(underlying),
                                                     structure_words(structure), words, action)


def valid_until_cell(w: Writer, until: datetime.datetime, full: bool) -> str:
    """``20:20 UTC (30 min)``; ``(30 min)`` only when the ticket runs its full 30 minutes."""
    tail = " (%s)" % w._emit("%d min" % structuring.TICKET_MINUTES) if full else ""
    return w.clock(until) + tail


def validity_line(w: Writer, until: datetime.datetime) -> str:
    """``after 20:20 UTC its quotes are too old, and a new ticket needs a new request`` (a ticket's note)."""
    return "after %s its quotes are too old, and a new ticket needs a new request" % w.clock(until)


def roll_header(w: Writer, trade_id: str, underlying: str, structure: str, expiration: datetime.date,
                strike: Decimal) -> str:
    """``### STRUCTURING <trade_id> | <UNDERLYING> <structure words>: the roll analysis of the short <date> <strike>
    call`` (never ``roll`` at a segment start: the lint reads it as an imperative)."""
    return "### STRUCTURING %s | %s %s: the roll analysis of the short %s %s call" % (
        w.trade_id(trade_id), _root(underlying), structure_words(structure), w.date(expiration), w.strike(strike))


def proposal_header(w: Writer, trade_id: str, symbol: str, structure: str, shares: Decimal) -> str:
    """``PROPOSAL <trade_id> | a collar on the <n> <SYMBOL> shares you hold``."""
    if structure not in ARTICLE_WORDS:
        raise ValueError("unknown proposal structure %r" % (structure,))
    return "### PROPOSAL %s | %s on the %s %s shares you hold" % (w.trade_id(trade_id), ARTICLE_WORDS[structure],
                                                                  w.number(expiry.plain(shares)), _root(symbol))




def criteria_line(w: Writer, sha: str) -> str:
    """``criteria: your structuring.toml (sha 5155427c)``: the criteria file an analysis read (a notes item)."""
    return "criteria: your structuring.toml (%s)" % w.sha(sha)


def _unpriced_tail(w: Writer, unpriced: int) -> str:
    return ", %s not priced" % w.count(unpriced) if unpriced else ""




def best_line(w: Writer, rank: int, total: int, objective: Any, score: Decimal, passed: int) -> str:
    """``BEST CANDIDATE <k> of <n> by your objective <id>: <score>, the highest among the <m> candidates that passed
    every criterion`` (``lowest`` for a lower-first objective); :func:`objective_line` gives the objective's words."""
    extreme = "highest" if objective.direction == "higher" else "lowest"
    return "**BEST CANDIDATE %s of %s** by your objective %s: **%s**, the %s among the %s %s that passed every criterion" % (
        w.count(rank), w.count(total), _guard(objective.id), w.score(score), extreme, w.count(passed),
        _plural(passed, "candidate"))


def objective_line(w: Writer, objective: Any) -> str:
    """``extrinsic_per_day: the bid's time value per calendar day, in percent of the share price``."""
    return "%s: %s" % (_guard(objective.id), w.words(objective.words))


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
_UNPRICED_WORDS: Dict[str, str] = structuring.UNPRICED_WORDS
"""The UNPRICED words: one source, :data:`aegis_desk.structuring.UNPRICED_WORDS`."""


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
    if code in _UNPRICED_WORDS or reason.code.startswith(structuring.IV_CODE_PREFIX):
        return structuring.unpriced_words(reason)
    raise ValueError("unknown reason code %r" % (code,))






# --------------------------------------------------------------------------------------------
# The desk view: one message of lines, list items and status marks (0.2.1; no tables since the single-bubble view)
# --------------------------------------------------------------------------------------------
#
# A chat client that draws a markdown table as its own card splits one answer into several bubbles, so the desk view
# prints no table: the context strip is one line of bold labels, and the checks, the group, the candidates, the cash,
# the approval steps and the key-value blocks are captioned list items. A status symbol opens an item (``- <symbol> ``,
# the alert lint's one place for it besides a table cell).

STATUS_PASS: str = alert_lint.STATUS_SYMBOLS[0]
STATUS_ACCEPTED: str = alert_lint.STATUS_SYMBOLS[1]
STATUS_FAIL: str = alert_lint.STATUS_SYMBOLS[2]
"""The status marks (``alert_lint.STATUS_SYMBOLS``): a criterion passed, an event label the owner accepted (by a row
tag or by structuring.toml), a criterion failed. Each stands only right after the ``- `` that opens an item."""
NOT_PRICED_WORDS: str = "not priced"
STRIP_SEPARATOR: str = " | "
_EVENT_LABELS: Dict[str, str] = {structuring.KEY_EARNINGS: "earnings", structuring.KEY_EX_DIVIDEND: "ex-dividend"}


def bold(text: str) -> str:
    return "**%s**" % text


def _clean(text: str, what: str) -> str:
    if "\n" in text:
        raise ValueError("%s holds no line break" % what)
    return text


def strip_line(pairs: Sequence[Tuple[str, str]]) -> str:
    """The context strip as one line: ``**account** 1 of desk.toml (margin) | **WMT mid** 104.23 | ...``."""
    if not pairs:
        raise ValueError("a strip needs a label")
    return STRIP_SEPARATOR.join("%s %s" % (bold(_clean(label, "a label")), _clean(value, "a value"))
                                for label, value in pairs)


def status_mark(status: str, keys: Sequence[str] = ()) -> Tuple[str, str]:
    """``(symbol, tail)`` of a status: the symbol that opens the item and, for a failing candidate, the tail that names
    the criteria keys it fails (``fails credit_only, ex_dividend``)."""
    if status not in alert_lint.STATUS_SYMBOLS:
        raise ValueError("%r is not a status symbol" % (status,))
    for key in keys:
        if key not in structuring.REASON_KEY_ORDER:
            raise ValueError("unknown criteria key %r" % (key,))
    return status, ("fails " + ", ".join(keys) if keys else "")


def item_line(text: str, status: str = "") -> str:
    """One list item: ``- <text>``, or ``- <symbol> <text>`` when it carries a status mark."""
    if status and status not in alert_lint.STATUS_SYMBOLS:
        raise ValueError("%r is not a status symbol" % (status,))
    return ITEM + (status + " " if status else "") + _clean(text, "an item")


def labelled_items(rows: Sequence[Sequence[str]]) -> List[str]:
    """Key-value rows as items: ``- **<label>** <value>`` (a row of two cells)."""
    lines = []
    for row in rows:
        if len(row) != 2:
            raise ValueError("a labelled item is a label and a value")
        lines.append(item_line("%s %s" % (bold(row[0]), row[1])))
    return lines


def _pct_or_none(w: Writer, value: Optional[Decimal]) -> str:
    return "none" if value is None else w.pct(value)


def net_words(w: Writer, net_credit: Decimal, total: Optional[Decimal] = None) -> str:
    """``credit 0.05 (10.00 in all)`` or ``debit 0.70 (140.00 in all)``: a net per share, signed by its words."""
    head = "credit %s" % w.price(net_credit) if net_credit >= 0 else "debit %s" % w.price(-net_credit)
    return head if total is None else "%s (%s in all)" % (head, w.amount(abs(total)))


def event_words(w: Writer, reason: "structuring.Reason", replacement: bool = False) -> str:
    """An earnings or ex-dividend label in a check cell's words: ``2026-11-24 (cum 2026-11-23), inside the new call's
    life``; ``unknown in research/calendar.toml, counted inside the call's life``."""
    code, value = reason.code, reason.value
    life = _life(replacement) + " life"
    if code == structuring.CODE_CROSSES_EARNINGS:
        return "%s (%s), inside %s" % (w.date(value["date"]), _TIME_WORDS.get(value["time"], "time unknown"), life)
    if code == structuring.CODE_CROSSES_EX_DIVIDEND:
        estimated = "estimated, " if value.get("status") == "estimated" else ""
        return "%s (%scum %s), inside %s" % (w.date(value["ex_date"]), estimated, w.date(value["cum_day"]), life)
    if code in (structuring.CODE_EARNINGS_UNKNOWN, structuring.CODE_DIVIDEND_UNKNOWN):
        status = value.get("status")
        listed = value.get("date") if code == structuring.CODE_EARNINGS_UNKNOWN else value.get("ex_date")
        if code == structuring.CODE_DIVIDEND_UNKNOWN and status == "second_ex_date":
            return "a second ex-date may fall before %s last trading day" % _life(replacement)
        if status == "passed" and listed is not None:
            return "%s has passed, the next is unknown: counted inside %s" % (w.date(listed), life)
        return "unknown in research/calendar.toml, counted inside %s" % life
    raise ValueError("%r is not an event label" % (code,))


def _event_rows(w: Writer, candidate: "structuring.Candidate", replacement: bool) -> List[List[str]]:
    rows = []
    for reason in candidate.reasons:
        if reason.key in _EVENT_LABELS:
            rows.append([_EVENT_LABELS[reason.key], event_words(w, reason, replacement), "excluded by your structuring.toml",
                         STATUS_FAIL])
    for label in candidate.accepted:
        if label.by not in (structuring.BY_ASSIGN_OK, structuring.BY_EARNINGS_OK, structuring.BY_CRITERIA):
            raise ValueError("unknown acceptance %r" % (label.by,))
        reason = structuring.Reason(label.key, label.code, label.value, label.threshold)
        rows.append([_EVENT_LABELS[label.key], event_words(w, reason, replacement), "accepted by %s" % label.by,
                     STATUS_ACCEPTED])
    return rows


def check_rows(w: Writer, candidate: "structuring.Candidate", section: Any, max_spread_pct: Decimal, s0: Decimal,
               held_strike: Optional[Decimal] = None) -> List[List[str]]:
    """The checks of one priced candidate as rows ``[check, value, the owner's band, status]``: each criterion of its
    section with the candidate's value, the owner's band and a status (:data:`STATUS_FAIL` when the candidate fails it),
    then its earnings and ex-dividend labels (failed, or accepted). A replacement's strike floor reads the held strike
    too."""
    kind = candidate.kind
    leg = candidate.legs[0]
    failing = [reason.key for reason in candidate.reasons]

    def status(key: str) -> str:
        return STATUS_FAIL if key in failing else STATUS_PASS

    cost = w.price(structuring.payoff.quantize_price(s0))
    days = ["calendar days", w.count(leg.dte_calendar), _band_words(w, section.dte), status(structuring.KEY_DTE)]
    spread_band = "at most %s" % w.pct(max_spread_pct)
    rows: List[List[str]] = []
    if kind in (structuring.KIND_COVERED_CALL, structuring.KIND_REPLACEMENT_CALL):
        if kind == structuring.KIND_REPLACEMENT_CALL:
            net = net_words(w, candidate.net_credit, candidate.net_credit_total)
            rows.append(["net per share", net, "credit only", status(structuring.KEY_CREDIT_ONLY)] if section.credit_only
                        else ["net per share", net, "", ""])
        if section.strike_floor == "cost_basis":
            floor = "at or above your cost %s" % cost
            if kind == structuring.KIND_REPLACEMENT_CALL and held_strike is not None:
                floor += " or the held strike %s" % w.strike(held_strike)
            rows.append(["strike", w.strike(leg.strike), floor, status(structuring.KEY_STRIKE_FLOOR)])
        rows.append(["absolute delta", "none" if leg.abs_delta is None else w.plain(leg.abs_delta),
                     _band_words(w, section.abs_delta), status(structuring.KEY_ABS_DELTA)])
        bid = w.price(leg.bid)
        if kind == structuring.KIND_COVERED_CALL and candidate.credit_total is not None:
            bid = "%s (%s in all)" % (bid, w.amount(candidate.credit_total))
        rows.append(["bid", bid, "at least %s" % w.plain(section.min_credit), status(structuring.KEY_MIN_CREDIT)])
        rows.append(days)
        rows.append(["spread", _pct_or_none(w, leg.spread_pct), spread_band, status(structuring.KEY_MAX_SPREAD_PCT)])
    elif kind in (structuring.KIND_PROTECTIVE_PUT, structuring.KIND_REPLACEMENT_PUT):
        rows.append(["floor", "%s below" % w.pct(candidate.floor_pct), "%s %%" % _band_words(w, section.floor_pct),
                     status(structuring.KEY_FLOOR_PCT)])
        rows.append(["cost", "ask %s (%s in all)" % (w.price(leg.ask), w.amount(candidate.cost_total)), "", ""])
        cover = candidate.covered_per_share
        covered = ("%s per share (%s in all)" % (w.number(format(cover, "f")), w.amount(candidate.covered_total))
                   if cover > 0 else "none")
        rows.append(["covered at your stress drop", covered, "drop %s (stressed price %s)" % (
            w.pct(section.stress_drop_pct), w.number(format(leg.strike - cover, "f"))),
            status(structuring.KEY_STRESS_DROP_PCT)])
        rows.append(days)
        rows.append(["spread", _pct_or_none(w, leg.spread_pct), spread_band, status(structuring.KEY_MAX_SPREAD_PCT)])
    elif kind == structuring.KIND_COLLAR:
        put, call = candidate.legs
        rows.append(["floor", "%s below" % w.pct(candidate.floor_pct), "%s %%" % _band_words(w, section.floor_pct),
                     status(structuring.KEY_FLOOR_PCT)])
        rows.append(["cap", "%s above" % w.pct(candidate.cap_pct), "%s %%" % _band_words(w, section.cap_pct),
                     status(structuring.KEY_CAP_PCT)])
        rows.append(["net per share", net_words(w, -candidate.net_debit, -candidate.net_debit_total),
                     "net debit at most %s" % w.plain(section.max_net_debit), status(structuring.KEY_MAX_NET_DEBIT)])
        if section.strike_floor == "cost_basis":
            rows.append(["call strike", w.strike(call.strike), "at or above your cost %s" % cost,
                         status(structuring.KEY_STRIKE_FLOOR)])
        rows.append(days)
        rows.append(["spreads", "%s and %s" % (_pct_or_none(w, put.spread_pct), _pct_or_none(w, call.spread_pct)),
                     spread_band, status(structuring.KEY_MAX_SPREAD_PCT)])
    else:
        raise ValueError("unknown candidate kind %r" % (kind,))
    rows.extend(_event_rows(w, candidate, kind == structuring.KIND_REPLACEMENT_CALL))
    return rows


def check_item(row: Sequence[str]) -> str:
    """One check as an item: ``- <symbol> **floor** 4.34 % below | your criteria: 3 to 10 %``; an event label's band
    reads as it is (``accepted by #assign-ok``, ``excluded by your structuring.toml``); a row with no band and no
    status (a cost, a net with no criterion) is ``- **cost** ask 23.10 (2,310.00 in all)``."""
    if len(row) != 4:
        raise ValueError("a check row is a check, a value, a band and a status")
    label, value, band, status = row
    text = "%s %s" % (bold(label), value)
    if band:
        text += STRIP_SEPARATOR + (band if band.startswith(("accepted by ", "excluded by ")) else "your criteria: " + band)
    return item_line(text, status)


def checks_block(w: Writer, candidate: "structuring.Candidate", section: Any, max_spread_pct: Decimal, s0: Decimal,
                 held_strike: Optional[Decimal] = None, subject: str = "this candidate") -> List[str]:
    """``**checks for this candidate:**`` and one :func:`check_item` per :func:`check_rows` row."""
    return [bold("checks for %s:" % subject)] + [
        check_item(row) for row in check_rows(w, candidate, section, max_spread_pct, s0, held_strike)]


def _keys(candidate: "structuring.Candidate") -> List[str]:
    keys: List[str] = []
    for reason in candidate.reasons:
        if reason.key not in keys and reason.key != structuring.KEY_UNPRICED:
            keys.append(reason.key)
    return keys


def candidate_status(candidate: "structuring.Candidate") -> Tuple[str, str]:
    """``(symbol, tail)`` of a candidate's item: the green mark; the red mark with ``fails <keys>``; or no mark with
    ``not priced``."""
    if candidate.status == structuring.STATUS_UNPRICED:
        return "", NOT_PRICED_WORDS
    if candidate.reasons:
        return status_mark(STATUS_FAIL, _keys(candidate))
    return status_mark(STATUS_PASS)


def _metric(w: Writer, label: str, value: Optional[Any], form: str) -> Optional[str]:
    """``<label> <value>``, or ``None`` for a value the candidate does not have (the item leaves it out)."""
    if value is None:
        return None
    text = {"price": w.price, "pct": w.pct, "score": w.score, "plain": w.plain, "strike": w.strike}[form](value)
    return "%s %s" % (label, text) if label else text


def candidate_items(w: Writer, candidates: Sequence["structuring.Candidate"], objective_id: str) -> List[str]:
    """The ranked candidates (the best first when there is one), one item each: its status mark, its rank in bold,
    its expiry and contract, the metrics its kind is judged on and, when it fails or was not priced, the tail that
    says so (``- <red> **2** 2026-12-18 | new call 112.5 | debit 0.70 | extrinsic_per_day 0.0597 | fails
    credit_only``). A metric the candidate does not have is left out."""
    if not candidates:
        raise ValueError("a candidates list needs a candidate")
    kind = candidates[0].kind
    objective = _guard(objective_id)
    lines: List[str] = []
    for candidate in candidates:
        leg = candidate.legs[0]
        parts: List[Optional[str]] = ["%s %s" % (bold(w.count(candidate.rank)), w.date(candidate.expiration))]
        if kind == structuring.KIND_COLLAR:
            put, call = candidate.legs
            parts += ["put %s / call %s" % (w.strike(put.strike), w.strike(call.strike)),
                      _metric(w, "floor", candidate.floor_pct, "pct"), _metric(w, "cap", candidate.cap_pct, "pct"),
                      None if candidate.net_debit is None else net_words(w, -candidate.net_debit)]
        elif kind == structuring.KIND_REPLACEMENT_CALL:
            parts += ["new call %s" % w.strike(leg.strike),
                      None if candidate.net_credit is None else net_words(w, candidate.net_credit),
                      _metric(w, objective, candidate.score, "score")]
        elif kind == structuring.KIND_COVERED_CALL:
            parts += ["strike %s" % w.strike(leg.strike), _metric(w, "delta", leg.abs_delta, "plain"),
                      _metric(w, "bid", leg.bid, "price"), _metric(w, objective, candidate.score, "score")]
        elif kind in (structuring.KIND_PROTECTIVE_PUT, structuring.KIND_REPLACEMENT_PUT):
            parts += ["strike %s" % w.strike(leg.strike), _metric(w, "floor", candidate.floor_pct, "pct"),
                      _metric(w, "ask", leg.ask, "price"), _metric(w, objective, candidate.score, "score")]
        else:
            raise ValueError("unknown candidate kind %r" % (kind,))
        symbol, tail = candidate_status(candidate)
        parts.append(tail or None)
        lines.append(item_line(STRIP_SEPARATOR.join(part for part in parts if part), symbol))
    return lines


def counts_caption(w: Writer, total: int, passed: int, failed: int, unpriced: int, counts: Mapping[str, int],
                   replacement: bool = False) -> str:
    """``**candidates by rank:** 1 of 20 passed your criteria; 19 failed (abs_delta 15, credit_only 4)``, and ``; 2 not
    priced`` when some were never judged against the criteria."""
    if passed + failed + unpriced != total:
        raise ValueError("the counts %d + %d + %d do not add up to %d candidates" % (passed, failed, unpriced, total))
    for key in counts:
        if key not in structuring.REASON_KEY_ORDER:
            raise ValueError("unknown criteria key %r" % (key,))
    parts = ["%s %s" % (key, w.count(counts[key])) for key in structuring.REASON_KEY_ORDER if key in counts]
    head = bold("%s by rank:" % ("replacement candidates" if replacement else "candidates"))
    text = "%s %s of %s passed your criteria; %s failed%s" % (head, w.count(passed), w.count(total), w.count(failed),
                                                             " (%s)" % ", ".join(parts) if parts else "")
    return text + ("; %s not priced" % w.count(unpriced) if unpriced else "")


def failure_lines(w: Writer, candidates: Sequence["structuring.Candidate"], replacement: bool = False) -> List[str]:
    """Why the shown candidates fail, one item per distinct reason with the ranks it applies to (``- 1, 2, 3 fail
    ex_dividend: ...``; ``- 4 not priced: ...``)."""
    order: List[str] = []
    ranks: Dict[str, List[str]] = {}
    for candidate in candidates:
        for reason in candidate.reasons:
            words = reason_words(w, reason, candidate, replacement)
            key = "not priced: %s" % words if reason.key == structuring.KEY_UNPRICED else "%s: %s" % (reason.key, words)
            if key not in ranks:
                order.append(key)
                ranks[key] = []
            if str(candidate.rank) not in ranks[key]:
                ranks[key].append(w.count(candidate.rank))
    lines = []
    for key in order:
        verb = "" if key.startswith("not priced") else ("fails " if len(ranks[key]) == 1 else "fail ")
        lines.append("- %s %s%s" % (", ".join(ranks[key]), verb, key))
    return lines


def coverage_line(w: Writer, contracts: int, shares: Decimal, structure: str) -> Optional[str]:
    """``3 contracts cover 300 of your 321.6301 shares; the other 21.6301 are not part of the collar``, or ``None``
    when the contracts cover every share (a proposal covers ``floor(shares / 100)`` contracts' worth)."""
    covered = Decimal(contracts * approval.SHARES_PER_CONTRACT)
    rest = shares - covered
    if rest <= 0:
        return None
    return "%s %s %s %s of your %s shares; the other %s are not part of the %s" % (
        w.count(contracts), _plural(contracts, "contract"), "covers" if contracts == 1 else "cover",
        w.number(expiry.plain(covered)),
        w.number(expiry.plain(shares)), w.number(expiry.plain(rest)), structure_words(structure))


def outside_bands_line(w: Writer, counts: Mapping[str, int]) -> str:
    """``not paired, outside their bands: 7 puts outside your floor band and 10 calls outside your cap band``."""
    puts, calls = counts.get("puts", 0), counts.get("calls", 0)
    return "not paired, outside their bands: %s %s outside your floor band and %s %s outside your cap band" % (
        w.count(puts), _plural(puts, "put"), w.count(calls), _plural(calls, "call"))


def group_block(w: Writer, after: "structuring.AfterState", before: Optional["structuring.BeforeState"] = None,
                legs: int = 1) -> List[str]:
    """``**the group, now and after both legs:**`` (``**the group after both legs:**`` without ``before``) and one
    item each for the max loss, max profit, breakeven and the group delta in share-equivalents
    (``- **max loss** 19,016.00 now, 19,006.00 after``)."""
    head = "after the leg" if legs == 1 else ("after both legs" if legs == 2 else "after the %s legs" % w.count(legs))

    def money(value: Optional[Decimal]) -> str:
        return "unbounded" if value is None else w.amount(value)

    def even(value: Optional[Decimal]) -> str:
        return "none" if value is None else w.price(value)

    def delta(value: Optional[float]) -> str:
        return "unavailable" if value is None else w.delta(value)

    items = [("max loss", money, "max_loss"), ("max profit", money, "max_profit"), ("breakeven", even, "breakeven"),
             ("group delta", delta, "group_delta")]
    rows = []
    for name, form, attribute in items:
        value = form(getattr(after, attribute))
        if before is not None:
            value = "%s now, %s after" % (form(getattr(before, attribute)), value)
        rows.append([name, value])
    caption = bold("the group %s:" % head) if before is None else bold("the group, now and %s:" % head)
    return [caption] + labelled_items(rows)


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


CASH_CAPTION: str = "**the cash:**"


def cash_block(w: Writer, net_conservative: Decimal, net_mid: Optional[Decimal], realized_conservative: Decimal,
               realized_mid: Optional[Decimal], closing_legs: int, peak_needed: Decimal) -> List[str]:
    """Designer Q's cash and realized figures (S11) as captioned items: the net cash and the realized amount at the
    conservative prices and at the mids (:data:`NO_MID_WORDS` when a leg has no mid), and the most cash the steps
    need at once (``- **net cash** +10.00 at the conservative prices, +35.00 at the mids``)."""
    head = "realized on the closing leg" if closing_legs == 1 else "realized on the closing legs"

    def both(conservative: Decimal, mid: Optional[Decimal]) -> str:
        at_mids = "at the mids: %s" % NO_MID_WORDS if mid is None else "%s at the mids" % w.amount(mid, signed=True)
        return "%s at the conservative prices, %s" % (w.amount(conservative, signed=True), at_mids)

    rows = [["net cash", both(net_conservative, net_mid)], [head, both(realized_conservative, realized_mid)],
            ["the most the steps need at once", w.amount(peak_needed)]]
    return [CASH_CAPTION] + labelled_items(rows)


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
    """The multi-leg note of ``structure`` (``approval.MULTI_LEG_NOTES``: ``roll``, ``collar``, ``closing``,
    ``combination``)."""
    if structure not in approval.MULTI_LEG_NOTES:
        raise ValueError("unknown multi-leg structure %r" % (structure,))
    return approval.MULTI_LEG_NOTES[structure]


def approval_block(w: Writer, answer: "structuring.Answer", candidate: "structuring.Candidate",
                   owner_level: int) -> List[str]:
    """The leg-order, other-order, multi-leg and approval lines of a candidate that passes the approval-level check
    (``aegis_desk.approval.approval_lines``); ``ValueError`` when it is refused (print :func:`refused_line`)."""
    group = answer.group_context
    if group is None:
        raise ValueError("the answer loaded no group")
    return approval.approval_lines(structuring.approval_state(answer), structuring.approval_legs(answer, candidate),
                                   owner_level, _root(group.underlying), w.emitted)


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


def quotes_cell(w: Writer, as_of: datetime.datetime, delay_min: int = FEED_DELAY_MIN) -> str:
    """``19:31 UTC, 15-min delayed``: the context strip's quotes value (the notes name the source)."""
    return "%s, %s" % (w.clock(as_of), w._emit("%d-min delayed" % delay_min))


def context_line(w: Writer, account: str, symbol: str, mid: Decimal, cost: Decimal, as_of: datetime.datetime,
                 delay_min: int = FEED_DELAY_MIN) -> str:
    """The context strip under an analysis's title, one line: ``**account** 2 of desk.toml (ira) | **SPY mid** 648.12 |
    **your cost** 632.40 | **quotes** 19:31 UTC, 15-min delayed``."""
    if not isinstance(symbol, str) or not _SYMBOL_RE.match(symbol):
        raise ValueError("%r is not a share symbol" % (symbol,))
    return strip_line([("account", account), ("%s mid" % symbol, w.price(mid)), ("your cost", w.price(cost)),
                       ("quotes", quotes_cell(w, as_of, delay_min))])


def spot_bar_lines(w: Writer, answer: "structuring.Answer") -> List[str]:
    """:data:`SPOT_BAR_LINE` when some priced leg used a one-minute bar for its spot, else nothing."""
    count = structuring.spot_bar_count(answer)
    if not count:
        return []
    one = count == 1
    return [SPOT_BAR_LINE % (w.count(count), "leg" if one else "legs", w.count(structuring.SPOT_ALIGNMENT_SECONDS),
                             "was" if one else "were", "it was" if one else "each was")]


def notes_block(items: Sequence[str]) -> List[str]:
    """The fine print at the end of a text: one quoted ``- `` item each (``> - ...``)."""
    return ["> - " + _guard(item) for item in items]


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
        assert leg.grade is not None
        lines.append(leg_line(w, index, count, "open", side, best.contracts, leg.root, leg.expiration, leg.strike,
                              leg.right, quote_segment(w, leg.bid, leg.ask, leg.mid, leg.grade)))
    return lines


def _best_block(w: Writer, answer: "structuring.Answer", best: "structuring.Candidate", legs: Sequence[str]) -> List[str]:
    """The BEST CANDIDATE line, its legs as items and the caveat quoted right under them."""
    ranking = answer.ranking
    assert ranking is not None and best.score is not None
    lines = [best_line(w, best.rank, len(ranking.candidates), ranking.objective, best.score, ranking.passed)]
    lines.extend(ITEM + leg for leg in legs)
    return lines + ["", "> " + CAVEAT_LINE]


def _analysis_notes(w: Writer, answer: "structuring.Answer", as_of: datetime.datetime,
                    best: Optional["structuring.Candidate"]) -> List[str]:
    ranking = answer.ranking
    assert ranking is not None
    items = [objective_line(w, ranking.objective)]
    if best is not None and ranking.tie_break is not None:
        items.append(tie_line(w, ranking.objective.id, ranking.tie_break, best.kind))
    items.append(quotes_line(w, as_of))
    items.append(MODEL_LINE)
    items.extend(spot_bar_lines(w, answer))
    if best is not None and best.american_caveat:
        items.append(american_line(w, best))
    items.append(criteria_line(w, answer.criteria.sha256))
    return items


def _candidates_section(w: Writer, answer: "structuring.Answer", best: Optional["structuring.Candidate"],
                        replacement: bool) -> List[str]:
    ranking = answer.ranking
    assert ranking is not None
    shown = list(ranking.candidates[:max(1, answer.criteria.general.max_candidates_shown)])
    lines = [counts_caption(w, len(ranking.candidates), ranking.passed, ranking.failed, ranking.unpriced,
                            ranking.failure_counts, replacement)]
    lines.extend(candidate_items(w, shown, ranking.objective.id))
    if best is None:
        lines.extend(["", FAILURES_CAPTION] + failure_lines(w, shown, replacement))
    outside = answer.outside_bands
    if outside is not None and (outside.get("puts", 0) or outside.get("calls", 0)):
        lines.extend(["", outside_bands_line(w, outside)])
    if replacement:
        lines.extend(["", POINTER_LINE])
    return lines


def roll_analysis_lines(w: Writer, answer: "structuring.Answer") -> List[str]:
    """The roll analysis in the desk view (0.2.1, without the footer: :func:`finish` adds it): the title, the context
    strip, the best candidate's block (its legs, the caveat, the checks and the group) or
    :data:`NONE_QUALIFIES_LINE` with the held call's close, the ranked candidates with the ticket pointer, and the
    notes; one line for an answer that prices nothing."""
    subject = answer.trade_id
    if answer.verdict in (structuring.VERDICT_NOT_APPLICABLE, structuring.VERDICT_NOTHING_TO_PROPOSE):
        return [not_applicable_line(w, subject, "roll analysis", _quiet_reason(answer), answer.verdict)]
    as_of = data_as_of(answer)
    assert as_of is not None
    if answer.verdict == structuring.VERDICT_UNAVAILABLE:
        return [unavailable_line(w, subject, "roll analysis", answer.reason or "")]
    target, ranking, held, group = answer.target, answer.ranking, answer.held, answer.group_context
    assert target is not None and target.group is not None and ranking is not None and held is not None
    assert group is not None and answer.pricing is not None and answer.pricing.spot_mid is not None
    account = account_cell(w, group.account, target.market.cfg.owner.accounts, held.row_number)
    lines = [roll_header(w, subject, group.underlying, group.structure, held.expiration, held.strike), ""]
    lines.extend([context_line(w, account, group.underlying, answer.pricing.spot_mid,
                               structuring.payoff.quantize_price(group.s0), as_of), ""])
    best = ranking.best
    if best is not None:
        lines.extend(_best_block(w, answer, best, _best_legs_roll(w, answer, best)))
        lines.append("")
        lines.extend(checks_block(w, best, answer.section, answer.pricing.max_spread_pct, group.s0, held.strike))
        lines.append("")
        assert best.after is not None
        lines.extend(group_block(w, best.after, answer.before, 2))
    else:
        lines.extend([bold(NONE_QUALIFIES_LINE), ITEM + held_line(w, held)])
    lines.append("")
    lines.extend(_candidates_section(w, answer, best, True))
    return lines + [""] + notes_block(_analysis_notes(w, answer, as_of, best))


def proposal_lines(w: Writer, answer: "structuring.Answer", owner_level: int) -> List[str]:
    """The proposal in the desk view (0.2.1, without the footer): the title, the context strip, the best candidate's
    block (its legs, the caveat, the checks, the group and the approval block) or
    :data:`NONE_QUALIFIES_LINE`, the ranked candidates (the collar's legs outside their bands under it), and the
    notes with :data:`LIMIT_LINE` and :data:`ACT_LINE`. A best candidate the approval-level check refuses gives the
    REFUSED line alone (exit 20 in the tool); an answer that prices nothing gives its one line."""
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
    assert answer.pricing is not None and answer.pricing.spot_mid is not None
    best = ranking.best
    if best is not None:
        result = structuring.approval_check(answer, best, owner_level)
        if not result.ok:
            return [refused_line(w, answer, result, structure)]
        if result.exposure_step is not None:
            uncovered = result.steps[result.exposure_step - 1].after.uncovered_calls
            return [exposure_refused_line(w, subject, group.underlying, group.structure, result.exposure_step, uncovered)]
    stock = [row for row in target.group.open_legs if row.is_stock_like]
    account = account_cell(w, group.account, target.market.cfg.owner.accounts,
                           stock[0].row_number if stock else target.group.legs[0].row_number)
    lines = [proposal_header(w, subject, group.underlying, structure, group.shares), ""]
    lines.extend([context_line(w, account, group.underlying, answer.pricing.spot_mid,
                               structuring.payoff.quantize_price(group.s0), as_of), ""])
    if best is not None:
        lines.extend(_best_block(w, answer, best, _best_legs_proposal(w, best)))
        lines.append("")
        lines.extend(checks_block(w, best, answer.section, answer.pricing.max_spread_pct, group.s0))
        lines.append("")
        assert best.after is not None
        lines.extend(group_block(w, best.after, answer.before, len(best.legs)))
        coverage = coverage_line(w, best.contracts, group.shares, structure)
        if coverage is not None:
            lines.append(ITEM + coverage)  # one more item of the group: why its max profit can stay unbounded
        lines.append("")
        lines.extend(approval_block(w, answer, best, owner_level))
    else:
        lines.append(bold(NONE_QUALIFIES_LINE))
    lines.append("")
    lines.extend(_candidates_section(w, answer, best, False))
    notes = _analysis_notes(w, answer, as_of, best) + [LIMIT_LINE, ACT_LINE]
    return lines + [""] + notes_block(notes)


def replacement_lines(w: Writer, answer: "structuring.Answer") -> List[str]:
    """A ROLL ticket's named replacement in the desk view (S5, S11): its checks (``the new call`` or ``the new
    put``; a failing criterion still prints: the owner named the contract) and, a blank line apart, the group
    (now and after both legs). The American caveat, when it applies, is a note (:func:`american_line`)."""
    candidate = answer.candidate
    group = answer.group_context
    if candidate is None or group is None or answer.pricing is None:
        raise ValueError("the answer priced no named replacement")
    subject = "the new put" if candidate.kind == structuring.KIND_REPLACEMENT_PUT else "the new call"
    held_strike = None if answer.held is None else answer.held.strike
    lines = checks_block(w, candidate, answer.section, answer.pricing.max_spread_pct, group.s0, held_strike, subject)
    if candidate.after is not None:
        lines.extend([""] + group_block(w, candidate.after, answer.before, 2))
    return lines


# --------------------------------------------------------------------------------------------
# The self-lint: the text, or the WITHHELD line with its codes
# --------------------------------------------------------------------------------------------

_DESK_WORDS = ("cash", "margin", "ira", "account", "call", "put", "shares")
_TEMPLATE_WORDS: str = (
    "above absent absolute acceptance accepted account accounts acknowledge acknowledged across action advanced "
    "after alert allow already american among amount analysis another answer applicable apply approval assign "
    "assigned assignment attested away balance band bands before below best both break breakeven breakevens "
    "broken broker brokerage brokers budget cache calendar call called calls candidate candidates cannot carry "
    "cash cause chain chains change check checks chose class clock clocks close closed closes closing code codes collar "
    "collars combination computed config configuration conservative contents continuous contract contracts "
    "convention cost could count counted counts cover coverage covered covers credit credits criteria criterion crossed "
    "cutoff "
    "date days debit decide decides deep define defined delayed delivers delta desk differ different differently "
    "digest direction disabled dividend does downside drop each earlier early earnings effect empty enough enter "
    "entered equity estimated evaluated every exception exercise exercises exists expiration expirations expires "
    "excluded expiring expiry fail failed fails fall falls fewer file files fill finish finite first fixed floor footer "
    "forecast "
    "formula found free fresh from gamma give grade grades grammar greeks group grouping groups held higher "
    "highest hold holds identifiers implied index inside instrument integer intent into intrinsic json kind known "
    "last later lattice least leaves left legs less level levels lies life limit line lines lint list listed "
    "lists live loaded long loss lower lowest made margin market maximum mids minimum missing model modelled "
    "money monthly more most multi multiplier must name named names need needed needs negative never next none "
    "note nothing number objective once only open opening opens option options order other otherwise outside "
    "overlay overlays overwrites owner paired part passed passes plain plan planned plus positions positive "
    "possible post prefix price priced prices print prints problems profit proposal proposals propose proposes "
    "protective puts quantity quarantined quote quotes rank ranks reads realized reason reasons receive records "
    "refusal refused release remaining replaced replacement replacements replaces report reports request "
    "requested requests required research rest right risk roll rolls root rules same says score second "
    "section secured self sequence served session share shares short shows side some soon source split spot spread "
    "spreads stale stamp stamped standard stands state states status stays stem step steps stock strategies "
    "stress stressed strike structure structuring style subject supply symbol takes tapped tell text than that "
    "their these theta they this threshold tick ticket tickets time today together toml tool trade trading "
    "transaction type unavailable unbounded uncovered undated underlying underlyings unhedged unit unknown "
    "unlisted unpriced until usable used valid value vega verdict volatility warnings were what which while whole "
    "wider window with withheld within without words worth would yield your yours yourself "
)
"""The words of the desk's own templates (every string literal of this module, ``tickets``, ``approval`` and
``structuring`` but the docstrings, four letters or more): with the module constants and the footer they make
:data:`DESK_LEXICON`."""


def _words_of(*texts: str) -> List[str]:
    return [word for text in texts for word in re.findall(r"[a-z]+", text.lower()) if len(word) >= 4]


_LEXICON_SOURCES: Tuple[str, ...] = (
    _TEMPLATE_WORDS, SOURCE_WORDS, alert_lint.STRUCTURING_FOOTER_PREFIX, *FIXED_LINES, *alerts.STRUCTURE_WORDS.values(),
    *ACTION_WORDS.values(), ROLL_PUT_WORDS, *WHAT_WORDS, *TIE_BREAK_WORDS.values(), *EXPIRATION_WORDS.values(),
    *structuring.UNPRICED_WORDS.values(), *approval.MULTI_LEG_NOTES.values(), *approval.CONVENTION.values(),
    *(objective.words for objective in structuring.structuring_config.OBJECTIVE_DEFS.values()),
    structuring.structuring_config.DISABLED_REASON)
DESK_LEXICON: frozenset = frozenset(_words_of(*_LEXICON_SOURCES))
"""The desk's own vocabulary: an owner text that is one of these words (an account named ``options`` or
``brokerage``) is the desk's word wherever it stands, so the owner-text screen does not compare it (review L3-2)."""
_DESK_SHORT_WORDS: frozenset = frozenset(word for text in _LEXICON_SOURCES for word in re.findall(r"[a-z]+", text.lower())
                                         if len(word) < 4)
"""The words under four letters of the same sources (``the``, ``of``, ``a``): with :data:`DESK_LEXICON` they make the
phrases the desk itself prints (an account named ``covered call``), which the screen does not compare either."""
_OWNER_WORD_RE = re.compile(r"[A-Za-z]+")
_OWNER_PHRASE_RE = re.compile(r"[A-Za-z]+(?: [A-Za-z]+)+")


def _desk_phrase(value: str) -> bool:
    """``value`` is two or more words, each a word of the desk's own templates (:data:`DESK_LEXICON`,
    ``_DESK_WORDS`` or :data:`_DESK_SHORT_WORDS`)."""
    if not _OWNER_PHRASE_RE.fullmatch(value):
        return False
    return all(word in DESK_LEXICON or word in _DESK_WORDS or word in _DESK_SHORT_WORDS
               for word in value.lower().split(" "))


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


_MARKUP_RE = re.compile(r"^(?:> )?(?:- )?")


def _dynamic_text(text: str) -> str:
    """The lines of ``text`` the desk composed from data: every fixed line (:data:`FIXED_LINES`, quoted or listed as
    the desk view prints it) and the footer out."""
    fixed = set(FIXED_LINES)
    return "\n".join(line for line in text.split("\n")
                     if _MARKUP_RE.sub("", line) not in fixed and not alert_lint.STRUCTURING_FOOTER_RE.match(line))


def _owner_text_found(text: str, owner_texts: Sequence[str]) -> bool:
    """An owner text printed in the composed lines, matched case-sensitively as a whole token (``Roth`` is not in
    ``Rothschild``); a text under four characters, one word of the desk's own vocabulary (:data:`DESK_LEXICON`) or a
    phrase made only of the desk's own words is the desk's wording wherever it stands and is not compared."""
    dynamic = _dynamic_text(text)
    for raw in owner_texts:
        value = (raw or "").strip()
        if len(value) < 4 or value.lower() in _DESK_WORDS:
            continue  # too short to tell apart from the desk's own words
        if _OWNER_WORD_RE.fullmatch(value) and value.lower() in DESK_LEXICON:
            continue  # one word the desk's own templates use
        if _desk_phrase(value):
            continue  # a phrase of the desk's own words (an account named "covered call")
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", dynamic):
            return True
    return False


def finish(w: Writer, lines: Sequence[str], as_of: datetime.datetime, tool: str, owner_texts: Sequence[str] = (),
           delay_min: int = FEED_DELAY_MIN) -> Rendered:
    """Close ``lines`` with the structuring footer at ``as_of`` and lint them strictly (module notes). ``owner_texts``
    are the cells the owner typed that must never print (the account names; texts shorter than four characters or
    equal to the desk's own words are not compared). A failure returns the WITHHELD line with its codes."""
    attempted = "\n".join(list(lines) + ["", w.footer(as_of, delay_min)])
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
    text = withheld_line(withheld, tool, codes) + "\n\n" + withheld.footer(as_of, delay_min)
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


def append_unavailable_record(paths: DeskPaths, *, now: datetime.datetime, tool: str, trade_id: str, code: str,
                              text: str, hashes: Mapping[str, Optional[str]], action: Optional[str] = None,
                              structure: Optional[str] = None) -> None:
    """The record of an UNAVAILABLE answer a tool printed through its error envelope (market data refused, an expired
    holiday table): S5 journals every printed text (exit 0 or 20), these too (review L3-6). ``text`` is the line the
    text mode prints; ``hashes`` are the files' current sha256 (``exit_check.config_hashes``)."""
    desk_paths.ensure_runtime_dirs(paths)  # the lock and journal/ live in the runtime directories
    record = journal_record(now=now, tool=tool, trade_id=trade_id, verdict=structuring.VERDICT_UNAVAILABLE, text=text,
                            criteria_sha256=hashes.get("structuring_toml"), desk_toml_sha256=hashes.get("desk_toml"),
                            positions_csv_sha256=hashes.get("positions_csv"), action=action, structure=structure,
                            refused_code=code)
    append_journal_record(paths, record)


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
    "append_journal_record", "append_unavailable_record", "approval_block", "best_line", "candidate_line", "cash_line",
    "config_changed_line",
    "config_unacknowledged_line", "contract_words", "criteria_missing_line", "criterion_line", "data_as_of",
    "exposure_refused_line", "failure_counts_line", "finish", "held_line", "journal_record", "leg_line", "let_assign_line",
    "multi_leg_line", "not_applicable_line", "outside_bands_line", "proposal_header", "proposal_lines",
    "proposal_stem", "proposal_summary_line", "quote_segment", "realized_line", "reason_words", "reasons_line",
    "refused_line", "replacement_lines", "report_markdown", "roll_analysis_lines", "roll_header", "roll_stem", "roll_summary_line",
    "share_leg_line", "side_segment", "stamp", "structure_words", "ticket_header", "ticket_stem", "tie_line",
    "unavailable_line", "validity_line", "withheld_line", "write_report",
]
