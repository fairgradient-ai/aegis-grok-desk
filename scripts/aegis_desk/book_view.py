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
* ``### YOUR BOOK <ET date>`` and, in italics under it, ``*<n> open positions | <source>, <n>-min delayed, as of
  HH:MM UTC*``;
* ``**The whole book**`` and its items: ``Unrealized`` (at the mids, at liquidation), ``Today`` (the day P/L, or the
  book's own reason it is unavailable), ``Realized`` (this year, today, this month), ``Net delta``, the groups the
  totals leave out (their ids only, as the EOD brief names them) and how many other warnings ``book.py`` prints; a
  gain carries its ``+`` (``Composer.signed``);
* per open group in book order, ``#### <UNDERLYING> <structure words>``, an italic line ``*<trade id or ROW-n> |
  account <n> of desk.toml (<type>)*`` (never the account name the owner typed; ``| data unavailable`` when the
  group is not ``ok``), then its items: ``P/L`` on both bases with the net cost (or credit), ``Payoff`` (max profit,
  or ``unbounded`` with the uncovered shares, max loss, breakevens) and one item per open leg (``200 WMT shares`` or
  ``2 short WMT 105 calls, 2026-11-20``: contract words, never an OCC symbol; its cost, mark, P/L and day P/L) with
  an italic sub-item of the detail (the quote grade, the liquidation mark and, for an option, the IV and the days
  left);
* a quarantined group as ``#### <id> quarantined`` and the desk's own words (``alerts.quarantine_words``);
* the session line in italics; then a blank line and the fixed footer at ``alerts.book_as_of`` of the document.

A group with no open leg (every leg closed) prints nothing: its realized P/L is in the totals.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import alert_lint, alerts, data_source, pl_figures, summaries

TITLE: str = "### YOUR BOOK"
WHOLE_BOOK_HEAD: str = "**The whole book**"
GROUP_MARK: str = "####"
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


def _signed(c: summaries.Composer, value: Any) -> str:
    return c.signed(_decimal(value))


def _money(c: summaries.Composer, value: Any) -> str:
    return c.money(_decimal(value))


def _grade(leg: Mapping[str, Any]) -> Optional[str]:
    grade = leg.get("quote_grade")
    return grade if isinstance(grade, str) and len(grade) == 1 and grade.isalpha() else None


def leg_lines(c: summaries.Composer, leg: Mapping[str, Any]) -> List[str]:
    """One open leg as an item and its detail sub-item: ``- **2 short WMT 105 calls, 2026-11-20** cost $3.42, now
    $3.425 | P/L -$1.00 | today -$50.00`` over ``  - *quote grade B | liquidation $3.50 | IV 41.32 % | 17 calendar
    days (13 trading days) left*`` (contract words, never an OCC symbol; the group's item carries both P/L bases)."""
    quantity = _decimal(leg.get("open_quantity"))
    size = alerts.fmt_number(_plain(abs(quantity) if quantity is not None else Decimal(0)), c.emitted)
    symbol = c.reason(str(leg.get("underlying") or leg.get("symbol")))
    if leg.get("occ") is None:
        label = "%s %s shares" % (size, symbol) if quantity is None or quantity >= 0 else \
            "short %s %s shares" % (size, symbol)
    else:
        side = "short" if quantity is not None and quantity < 0 else "long"
        noun = ("call" if leg.get("right") == "CALL" else "put") + ("" if quantity is not None and abs(quantity) == 1
                                                                   else "s")
        strike = _decimal(leg.get("strike"))
        label = "%s %s %s %s %s, %s" % (size, side, symbol,
                                        alerts.fmt_strike(strike, c.emitted) if strike is not None else "unknown strike",
                                        noun, c.date(leg.get("expiry")))
    head = "%s%s cost %s, now %s | P/L %s | today %s" % (
        summaries.colour(leg.get("unrealized_pl_mid")), summaries.bold_label(label), _price(c, leg.get("entry_price_per_share")), _price(c, leg.get("mark_mid")),
        _signed(c, leg.get("unrealized_pl_mid")), _signed(c, leg.get("day_pl")))
    details: List[str] = []
    grade = _grade(leg)
    if grade is not None:
        details.append("quote grade %s" % grade)
    details.append("liquidation %s" % _price(c, leg.get("mark_liq")))
    if leg.get("occ") is not None:
        iv = leg.get("iv")
        if isinstance(iv, (int, float)) and not isinstance(iv, bool):
            details.append("IV %s" % alerts.fmt_pct(float(iv) * 100, c.emitted))
        calendar, trading = leg.get("dte_calendar"), leg.get("dte_trading")
        if isinstance(calendar, int) and not isinstance(calendar, bool) and calendar >= 0:
            days = alerts.fmt_days(calendar, c.emitted, "calendar")
            if isinstance(trading, int) and not isinstance(trading, bool) and trading >= 0:
                days += " (%s)" % alerts.fmt_days(trading, c.emitted, "trading")
            details.append(days + " left")
    return [head, "%s*%s*" % (summaries.SUB_ITEM, summaries.SOURCE_SEPARATOR.join(details))]


def payoff_item(c: summaries.Composer, group: Mapping[str, Any], legs: Sequence[Mapping[str, Any]]) -> str:
    """``- **Payoff** max profit unbounded (21.6301 of the 321.6301 shares carry no call) | max loss $3,252.63 |
    breakeven $150.0252``."""
    if group.get("unbounded_up"):
        profit = "max profit unbounded"
        rest = uncovered_shares(group, legs)
        if rest is not None:
            shares = sum((_decimal(leg.get("open_quantity")) or Decimal(0) for leg in legs
                          if leg.get("occ") is None and (_decimal(leg.get("open_quantity")) or Decimal(0)) > 0),
                         Decimal(0))
            profit += " (%s of the %s shares carry no call)" % (alerts.fmt_number(_plain(rest), c.emitted),
                                                                 alerts.fmt_number(_plain(shares), c.emitted))
    else:
        profit = "max profit %s" % _money(c, group.get("max_profit"))
    loss = "max loss unbounded" if group.get("unbounded_down") else "max loss %s" % _money(c, group.get("max_loss"))
    parts = [profit, loss]
    evens = [value for value in (_decimal(item) for item in group.get("breakevens") or []) if value is not None]
    if evens:
        parts.append("%s %s" % ("breakeven" if len(evens) == 1 else "breakevens",
                                " and ".join(alerts.fmt_price(value, c.emitted) for value in evens)))
    return "- %s %s" % (summaries.bold_label("Payoff"), summaries.SOURCE_SEPARATOR.join(parts))


def group_block(c: summaries.Composer, group: Mapping[str, Any], legs: Sequence[Mapping[str, Any]],
                accounts: Sequence[Any], figures: "pl_figures.GroupFigures") -> List[str]:
    """The block of one open group: ``#### <UNDERLYING> <structure words>``, an italic line with its id and account,
    then its coloured ``P/L`` (with its percentage of the cost) and ``Today`` items, its payoff and its legs (module
    notes); an unavailable group's P/L item gives its own reason."""
    ident = c.ident(str(group.get("trade_id") or group.get("group_id")))
    structure = str(group.get("structure"))
    words = alerts.STRUCTURE_WORDS.get(structure, structure.replace("_", " "))
    first_row = min((leg.get("row_number") for leg in legs if isinstance(leg.get("row_number"), int)), default=0)
    meta = "%s | %s" % (ident, account_words(group.get("account"), accounts, first_row, c))
    lines = ["%s %s %s" % (GROUP_MARK, c.reason(str(group.get("underlying"))), words), "*%s*" % meta]
    if group.get("data_quality") != "ok":
        cost = summaries.cost_words(c, group.get("net_cost"))
        lines.append("%s%s unavailable: %s%s" % (summaries.colour(None), summaries.bold_label("P/L"),
                                                 c.reason(figures.reason) if figures.reason else "no usable quote",
                                                 " | " + cost if cost else ""))
    else:
        mid = group.get("unrealized_pl_mid")
        cost = summaries.cost_words(c, group.get("net_cost"))
        lines.append("%s%s %s since entry%s | %s at liquidation" % (
            summaries.colour(mid), summaries.bold_label("P/L"), c.with_pct(mid, figures.pl_pct),
            ", " + cost if cost else "", _signed(c, group.get("unrealized_pl_liq"))))
        if figures.day_pl is not None:
            lines.append("%s%s %s" % (summaries.colour(figures.day_pl), summaries.bold_label("Today"),
                                      c.with_pct(figures.day_pl, figures.day_pct)))
        else:
            lines.append("%s%s unavailable" % (summaries.colour(None), summaries.bold_label("Today")))
    lines.append(payoff_item(c, group, legs))
    for leg in legs:
        lines.extend(leg_lines(c, leg))
    return lines


def whole_book_lines(c: summaries.Composer, document: Mapping[str, Any]) -> List[str]:
    """``**The whole book**``: the brief's own P/L items (``summaries.book_pl_lines``), the net delta and how many
    other warnings ``book.py`` prints (module notes)."""
    portfolio = document["portfolio"]
    warnings = [text for text in document.get("warnings") or [] if isinstance(text, str)]
    lines = [WHOLE_BOOK_HEAD] + summaries.book_pl_lines(c, document)
    delta = portfolio.get("net_delta")
    if isinstance(delta, (int, float)) and not isinstance(delta, bool):
        lines.append("- %s %s share-equivalents" % (summaries.bold_label("Net delta"),
                                                    alerts.fmt_delta(float(delta), c.emitted)))
    others = [text for text in warnings if not text.startswith(_DAY_PL_PREFIX)
              and not any(prefix in text for prefix in _EXCLUDED_PREFIXES)]
    if others:
        lines.append("- %s (`book.py` without `--view` prints %s)" % (
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
    sources = document.get("sources") or []
    source = sources[0] if sources and isinstance(sources[0], Mapping) else {}
    delay = source.get("delay_min")
    delay = delay if isinstance(delay, int) and not isinstance(delay, bool) and delay >= 0 else 0
    as_of = alerts.book_as_of(document)
    c.add("%s %s" % (TITLE, c.date(et_date)))
    c.add("*%s | %s, %s, as of %s*" % (
        c.counted(len(shown), "open position"), c.reason(data_source.words_for_label(source.get("name"))),
        alerts.fmt_delay(delay, c.emitted), c.clock(as_of.astimezone(datetime.timezone.utc).strftime("%H:%M UTC"))))
    c.block(*whole_book_lines(c, document))
    figures = pl_figures.book_figures(document)
    for group in shown:
        c.block(*group_block(c, group, _open_legs(document, group), accounts,
                             figures.groups[str(group.get("group_id"))]))
    for entry in quarantined:
        reasons = entry.get("reasons") or []
        words = "; ".join(c.reason(alerts.quarantine_words(str(reason))) for reason in reasons) or alerts.GROUP_WORDS
        c.block("%s %s quarantined" % (GROUP_MARK, c.ident(str(entry.get("group_id")))), "- " + words)
    c.block("*%s*" % c.reason(session_line))
    footer = alerts.fmt_footer(as_of, delay, c.emitted, c.clocks)
    text = "\n".join(c.lines + ["", footer])
    emitted = {"emitted_numbers": list(c.emitted), "rule_ids": [], "identifiers": list(c.identifiers),
               "clocks": list(c.clocks), "as_of": as_of.astimezone(datetime.timezone.utc).isoformat()}
    lint = alert_lint.check(text, c.emitted, rule_ids=[], identifiers=c.identifiers, clocks=c.clocks, as_of=as_of)
    return BookView(text, emitted, lint)


__all__ = [
    "BookView", "DATA_UNAVAILABLE", "GROUP_MARK", "SHARES_PER_CONTRACT", "TITLE", "WHOLE_BOOK_HEAD", "account_words",
    "group_block", "leg_lines", "payoff_item", "render", "uncovered_shares", "whole_book_lines",
]
