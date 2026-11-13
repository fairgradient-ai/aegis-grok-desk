"""The book view: the book of record as one linted markdown message a chat renders as one bubble (``book.py --view``).

``book.py`` prints the book as one prose line per group and leg, the record a tool or a model reads. The owner who asks
"how is my book" in a chat wants the same numbers laid out to scan: this module composes them as a markdown desk view
with the ``aegis_desk.summaries`` composer, so every number is printed by one formatter of :mod:`aegis_desk.alerts`
and recorded, and the text is linted in strict mode under the fixed footer before anything prints. Standard library
only, no I/O, no clock; NOTHING is summed or subtracted but the one count the view adds, the shares a group's short
calls leave uncovered (``Decimal``, the reason its max profit stays unbounded), so the model that relays the message
never does arithmetic.

The layout (no table: a chat client that draws a table as a card of its own would split the message)
----------------------------------------------------------------------------------------------------
* ``### YOUR BOOK <ET date> | <n> open positions``;
* ``**the whole book**`` and its items: ``unrealized P/L`` (mid, liquidation), ``day P/L`` (or the book's own reason
  it is unavailable), ``realized`` (today, month to date, year to date), ``net delta``, the groups the totals leave
  out (their ids only, as the EOD brief names them) and how many other warnings ``book.py`` prints;
* per open group in book order, ``**<trade id or ROW-n> | <UNDERLYING> <structure words>** | account <n> of
  desk.toml (<type>)`` (never the account name the owner typed) with ``| data unavailable`` when the group is not
  ``ok``, then its items: ``P/L`` on both bases with the net cost, ``payoff`` (max profit, or ``unbounded`` with the
  uncovered shares, max loss, breakevens) and one item per open leg: ``shares`` or ``short calls`` / ``long puts``
  (contract words, never an OCC symbol), the entry price, the marks with the quote grade, the P/L, the day P/L and,
  for an option, the IV and the days to expiry;
* a quarantined group in the desk's own words (``alerts.quarantine_words``);
* ``**sources**`` with the quote source, its delay and as-of, and the session line; then a blank line and the fixed
  footer at ``alerts.book_as_of`` of the document.

A group with no open leg (every leg closed) prints nothing: its realized P/L is in the totals.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import alert_lint, alerts, data_source, summaries

TITLE: str = "### YOUR BOOK"
WHOLE_BOOK_HEAD: str = "**the whole book**"
SOURCES_HEAD: str = "**sources**"
DATA_UNAVAILABLE: str = "data unavailable"
SHARES_PER_CONTRACT: Decimal = Decimal(100)
_DAY_PL_PREFIX: str = "day P/L unavailable"
_EXCLUDED_PREFIXES = (" excluded from the portfolio totals: ", " excluded from the day P/L total: ")


@dataclass(frozen=True)
class BookView:
    """The view's text (its last line the fixed footer), the strict emitted document and the lint of the text."""

    text: str
    emitted: Dict[str, Any]
    lint: alert_lint.LintResult

    @property
    def ok(self) -> bool:
        return self.lint.ok


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _plain(value: Decimal) -> str:
    """A quantity as written, without trailing zeros: ``321.6301``, ``3``."""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def account_words(account: Any, accounts: Sequence[Any], row_number: Any, c: summaries.Composer) -> str:
    """``account <n> of desk.toml (<type>)`` for the n-th ``[[owner.accounts]]`` entry, else ``the account in row <r>
    of positions.csv``: the name the owner typed is never printed (the structuring texts' rule)."""
    for index, entry in enumerate(accounts, 1):
        if getattr(entry, "name", None) == account:
            kind = getattr(entry, "type", None)
            kind = kind if isinstance(kind, str) and kind.isalpha() and kind.islower() and len(kind) <= 10 else "account"
            return "account %s of desk.toml (%s)" % (c.count(index), kind)
    row = row_number if isinstance(row_number, int) and not isinstance(row_number, bool) and row_number >= 0 else 0
    return "the account in row %s of positions.csv" % c.count(row)


def uncovered_shares(group: Mapping[str, Any], legs: Sequence[Mapping[str, Any]]) -> Optional[Decimal]:
    """The long shares of the group's open legs that its open short calls do not cover (``shares - 100 x contracts``
    in ``Decimal``), or ``None`` when the group holds no short call or every share is covered."""
    shares = Decimal(0)
    covered = Decimal(0)
    for leg in legs:
        quantity = _decimal(leg.get("open_quantity"))
        if quantity is None:
            continue
        if leg.get("occ") is None and leg.get("instrument_type") in ("equity", "etf") and quantity > 0:
            shares += quantity
        elif leg.get("right") == "CALL" and quantity < 0:
            multiplier = _decimal(leg.get("multiplier")) or SHARES_PER_CONTRACT
            covered += -quantity * multiplier
    if covered <= 0 or shares <= covered:
        return None
    return shares - covered


def _price(c: summaries.Composer, value: Any) -> str:
    amount = _decimal(value)
    return "unavailable" if amount is None else alerts.fmt_price(amount, c.emitted)


def _money(c: summaries.Composer, value: Any) -> str:
    return c.money(_decimal(value))


def _contract(c: summaries.Composer, leg: Mapping[str, Any]) -> str:
    """``NVDA 2027-05-21 175 call``: a contract in words, never its OCC symbol."""
    strike = _decimal(leg.get("strike"))
    right = "call" if leg.get("right") == "CALL" else "put"
    return "%s %s %s %s" % (c.reason(str(leg.get("underlying"))), c.date(leg.get("expiry")),
                            alerts.fmt_strike(strike, c.emitted) if strike is not None else "unknown strike", right)


def _marks(c: summaries.Composer, leg: Mapping[str, Any]) -> str:
    grade = leg.get("quote_grade")
    marks = "now %s (mid), %s (liquidation)" % (_price(c, leg.get("mark_mid")), _price(c, leg.get("mark_liq")))
    return marks + (", grade %s" % grade if isinstance(grade, str) and len(grade) == 1 and grade.isalpha() else "")


def leg_item(c: summaries.Composer, leg: Mapping[str, Any]) -> str:
    """One open leg: ``- **shares** 321.6301 NVDA at $150.93 | now ... | P/L $3,618.34 (mid) | day unavailable`` (the
    group's item carries both bases) or ``- **short calls** 3 x NVDA 2027-05-21 175 call at $17.02 | ... | IV 47.70 % | 189
    calendar days (129 trading days)``."""
    quantity = _decimal(leg.get("open_quantity"))
    size = abs(quantity) if quantity is not None else Decimal(0)
    entry = _price(c, leg.get("entry_price_per_share"))
    if leg.get("occ") is None:
        label = "shares" if quantity is None or quantity >= 0 else "short shares"
        head = "%s %s at %s" % (alerts.fmt_number(_plain(size), c.emitted), c.reason(str(leg.get("symbol"))), entry)
    else:
        side = "short" if quantity is not None and quantity < 0 else "long"
        noun = "call" if leg.get("right") == "CALL" else "put"
        label = "%s %s%s" % (side, noun, "" if size == 1 else "s")
        head = "%s x %s at %s" % (alerts.fmt_number(_plain(size), c.emitted), _contract(c, leg), entry)
    parts = ["%s %s" % (summaries.bold_label(label), head), _marks(c, leg),
             "P/L %s (mid)" % _money(c, leg.get("unrealized_pl_mid")),
             "day %s" % _money(c, leg.get("day_pl"))]
    if leg.get("occ") is not None:
        iv = leg.get("iv")
        if isinstance(iv, (int, float)) and not isinstance(iv, bool):
            parts.append("IV %s" % alerts.fmt_pct(float(iv) * 100, c.emitted))
        calendar, trading = leg.get("dte_calendar"), leg.get("dte_trading")
        if isinstance(calendar, int) and not isinstance(calendar, bool) and calendar >= 0:
            days = alerts.fmt_days(calendar, c.emitted, "calendar")
            if isinstance(trading, int) and not isinstance(trading, bool) and trading >= 0:
                days += " (%s)" % alerts.fmt_days(trading, c.emitted, "trading")
            parts.append(days)
    return "- " + summaries.SOURCE_SEPARATOR.join(parts)


def payoff_item(c: summaries.Composer, group: Mapping[str, Any], legs: Sequence[Mapping[str, Any]]) -> str:
    """``- **payoff** max profit unbounded: 21.6301 of the 321.6301 shares carry no call | max loss $3,252.63 |
    breakeven $150.0252``."""
    if group.get("unbounded_up"):
        profit = "max profit unbounded"
        rest = uncovered_shares(group, legs)
        if rest is not None:
            shares = sum((_decimal(leg.get("open_quantity")) or Decimal(0) for leg in legs
                          if leg.get("occ") is None and (_decimal(leg.get("open_quantity")) or Decimal(0)) > 0),
                         Decimal(0))
            profit += ": %s of the %s shares carry no call" % (alerts.fmt_number(_plain(rest), c.emitted),
                                                                alerts.fmt_number(_plain(shares), c.emitted))
    else:
        profit = "max profit %s" % _money(c, group.get("max_profit"))
    loss = "max loss unbounded" if group.get("unbounded_down") else "max loss %s" % _money(c, group.get("max_loss"))
    parts = [profit, loss]
    evens = [value for value in (_decimal(item) for item in group.get("breakevens") or []) if value is not None]
    if evens:
        parts.append("%s %s" % ("breakeven" if len(evens) == 1 else "breakevens",
                                " and ".join(alerts.fmt_price(value, c.emitted) for value in evens)))
    return "- %s %s" % (summaries.bold_label("payoff"), summaries.SOURCE_SEPARATOR.join(parts))


def group_block(c: summaries.Composer, group: Mapping[str, Any], legs: Sequence[Mapping[str, Any]],
                accounts: Sequence[Any]) -> List[str]:
    """The block of one open group: its bold head line and its items (module notes)."""
    ident = c.ident(str(group.get("trade_id") or group.get("group_id")))
    structure = str(group.get("structure"))
    words = alerts.STRUCTURE_WORDS.get(structure, structure.replace("_", " "))
    first_row = min((leg.get("row_number") for leg in legs if isinstance(leg.get("row_number"), int)), default=0)
    head = "**%s | %s %s**%s%s" % (ident, c.reason(str(group.get("underlying"))), words, summaries.SOURCE_SEPARATOR,
                                   account_words(group.get("account"), accounts, first_row, c))
    if group.get("data_quality") != "ok":
        head += summaries.SOURCE_SEPARATOR + DATA_UNAVAILABLE
    lines = [head,
             "- %s %s (mid), %s (liquidation) on a net cost of %s" % (
                 summaries.bold_label("P/L"), _money(c, group.get("unrealized_pl_mid")),
                 _money(c, group.get("unrealized_pl_liq")), _money(c, group.get("net_cost"))),
             payoff_item(c, group, legs)]
    lines.extend(leg_item(c, leg) for leg in legs)
    return lines


def whole_book_lines(c: summaries.Composer, document: Mapping[str, Any]) -> List[str]:
    """``**the whole book**`` and its items (module notes)."""
    portfolio = document["portfolio"]
    warnings = [text for text in document.get("warnings") or [] if isinstance(text, str)]
    day = portfolio.get("day_pl")
    if day is None:
        reason = next((text for text in warnings if text.startswith(_DAY_PL_PREFIX)), None)
        day_text = c.reason(reason[len("day P/L "):]) if reason else "unavailable"
    else:
        day_text = _money(c, day)
    lines = [WHOLE_BOOK_HEAD,
             "- %s %s (mid), %s (liquidation)" % (summaries.bold_label("unrealized P/L"),
                                                  _money(c, portfolio.get("unrealized_pl_mid")),
                                                  _money(c, portfolio.get("unrealized_pl_liq"))),
             "- %s %s" % (summaries.bold_label("day P/L"), day_text),
             "- %s today %s, month to date %s, year to date %s" % (
                 summaries.bold_label("realized"), _money(c, portfolio.get("realized_pl_today")),
                 _money(c, portfolio.get("realized_pl_mtd")), _money(c, portfolio.get("realized_pl_ytd")))]
    delta = portfolio.get("net_delta")
    if isinstance(delta, (int, float)) and not isinstance(delta, bool):
        lines.append("- %s %s (share-equivalents)" % (summaries.bold_label("net delta"),
                                                      alerts.fmt_delta(float(delta), c.emitted)))
    totals, day_only = summaries.excluded_groups(document)
    pieces: List[str] = []
    if totals:
        pieces.append(summaries.TOTALS_WITHOUT % ", ".join(summaries.EXCLUDED_UNAVAILABLE % c.ident(group_id)
                                                           for group_id in totals))
    if day_only and day is not None:
        pieces.append(summaries.DAY_TOTAL_WITHOUT % ", ".join(summaries.EXCLUDED_DAY % c.ident(group_id)
                                                              for group_id in day_only))
    if pieces:
        lines.append("- " + "; ".join(pieces))
    others = [text for text in warnings if not text.startswith(_DAY_PL_PREFIX)
              and not any(prefix in text for prefix in _EXCLUDED_PREFIXES)]
    if others:
        lines.append("- %s (book.py without --view prints %s)" % (
            c.counted(len(others), "more warning"), "it" if len(others) == 1 else "them"))
    return lines


def _open_legs(document: Mapping[str, Any], group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    by_row = {leg.get("row_number"): leg for leg in document.get("legs") or []}
    return [by_row[row] for row in group.get("legs") or [] if row in by_row and by_row[row].get("status") == "open"]


def render(document: Mapping[str, Any], *, et_date: datetime.date, accounts: Sequence[Any],
           session_line: str) -> BookView:
    """The book view of a ``book.load_book`` document (module notes), linted strictly under the fixed footer."""
    c = summaries.Composer()
    groups = [group for group in document.get("groups") or [] if _open_legs(document, group)]
    quarantined = [entry for entry in (document["portfolio"].get("quarantined_groups") or [])
                   if isinstance(entry, Mapping)]
    q_ids = {entry.get("group_id") for entry in quarantined}
    shown = [group for group in groups if group.get("group_id") not in q_ids]
    c.add("%s %s%s%s" % (TITLE, c.date(et_date), summaries.SOURCE_SEPARATOR,
                         c.counted(len(shown), "open position")))
    c.block(*whole_book_lines(c, document))
    for group in shown:
        c.block(*group_block(c, group, _open_legs(document, group), accounts))
    for entry in quarantined:
        reasons = entry.get("reasons") or []
        words = "; ".join(c.reason(alerts.quarantine_words(str(reason))) for reason in reasons) or alerts.GROUP_WORDS
        c.block("**%s** | quarantined: %s" % (c.ident(str(entry.get("group_id"))), words))
    sources = document.get("sources") or []
    source = sources[0] if sources and isinstance(sources[0], Mapping) else {}
    delay = source.get("delay_min")
    delay = delay if isinstance(delay, int) and not isinstance(delay, bool) and delay >= 0 else 0
    as_of = alerts.book_as_of(document)
    c.block("%s %s, %s, as of %s; %s" % (
        SOURCES_HEAD, c.reason(data_source.words_for_label(source.get("name"))), alerts.fmt_delay(delay, c.emitted),
        c.clock(as_of.astimezone(datetime.timezone.utc).strftime("%H:%M UTC")), c.reason(session_line)))
    footer = alerts.fmt_footer(as_of, delay, c.emitted, c.clocks)
    text = "\n".join(c.lines + ["", footer])
    emitted = {"emitted_numbers": list(c.emitted), "rule_ids": [], "identifiers": list(c.identifiers),
               "clocks": list(c.clocks), "as_of": as_of.astimezone(datetime.timezone.utc).isoformat()}
    lint = alert_lint.check(text, c.emitted, rule_ids=[], identifiers=c.identifiers, clocks=c.clocks, as_of=as_of)
    return BookView(text, emitted, lint)


__all__ = [
    "BookView", "DATA_UNAVAILABLE", "SHARES_PER_CONTRACT", "SOURCES_HEAD", "TITLE", "WHOLE_BOOK_HEAD", "account_words",
    "group_block", "leg_item", "payoff_item", "render", "uncovered_shares", "whole_book_lines",
]
