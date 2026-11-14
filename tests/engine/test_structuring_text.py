"""The Structuring Desk's text (``aegis_desk.structuring_text``; PR 3b, designer C's D3-D7 and D15 under S4-S11).

Strings only: every number goes through a :class:`Writer` formatter that records it in the emitted document (the
opening_bell pattern), and every text ends with the structuring footer and passes ``alert_lint.check(...,
footer='structuring')`` strictly (rule ids ``[]``, identifiers, clocks and the as-of pinned). Every fixed line passes
the strict lint in structuring mode and fails in rules mode; leg, candidate and best-candidate lines closed by the rules
footer are refused (the reverse refusal). Probes: an OCC symbol, an owner-typed account name or the word FAILS_RULE is
refused by the builders and withheld by :func:`finish`. The golden texts are the fixture answers of
``tests.engine.test_structuring`` at the regular instant, pinned exactly. The journal record and the report writer run
on a temporary desk root.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import datetime
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import List

from aegis_desk import alert_lint, approval, desk_paths, quote_cache, state, structuring, structuring_config
from aegis_desk import structuring_text as T
from tests.engine.test_structuring import CRITERIA, CRITERIA_FILE, FIXTURE_DESK, JAN15, NOW, SPY_TAG, STAMP, WMT_TAGS

UTC = datetime.timezone.utc
D = Decimal
S = structuring
with open(CRITERIA_FILE, "rb") as _handle:
    SHA = hashlib.sha256(_handle.read()).hexdigest()
ACCOUNTS = [type("Entry", (), {"name": "Brokerage (margin)", "type": "margin"})(),
            type("Entry", (), {"name": "Roth IRA", "type": "ira"})()]
FOOTER = alert_lint.structuring_footer_line(STAMP)
TICKET_STRIP = ("*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 "
                "UTC, 15-min delayed*")


def lint(text: str, writer: "T.Writer", as_of: datetime.datetime = STAMP, footer: str = "structuring"):
    return alert_lint.check(text, writer.emitted, rule_ids=[], identifiers=writer.identifiers, clocks=writer.clocks,
                            as_of=as_of, footer=footer)


def closed(writer: "T.Writer", lines: List[str], as_of: datetime.datetime = STAMP) -> str:
    return "\n".join(list(lines) + [writer.footer(as_of)])


class DeskCopy:
    def __init__(self, owner, drop_row=None, replace=None) -> None:
        tmp = tempfile.mkdtemp(prefix="aegis-structuring-text-")
        owner.addClassCleanup(shutil.rmtree, tmp, True)
        self.root = os.path.join(tmp, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        shutil.copyfile(CRITERIA_FILE, os.path.join(self.root, "structuring.toml"))
        path = os.path.join(self.root, "positions.csv")
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        if drop_row is not None:
            del lines[drop_row - 1]
        text = "\n".join(lines)
        if replace is not None:
            text = text.replace(*replace)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def market(self, fixture_set: str = "regular", now: datetime.datetime = NOW) -> quote_cache.MarketData:
        return quote_cache.open_market_data(self.root, now=now, offline=True, fixture_set=fixture_set, timeout_s=5.0,
                                            user_agent="aegis-desk-test")


# ------------------------------------------------------------------------------------------------
# The Writer
# ------------------------------------------------------------------------------------------------


class WriterTest(unittest.TestCase):
    def test_formatters_emit_what_they_print(self) -> None:
        w = T.Writer()
        printed = [w.price(D("3.5")), w.price(D("3.425")), w.money(D("19006")), w.signed_money(D("10")),
                   w.signed_money(D("-16")), w.signed_money(D("0")), w.plain(D("0.50")), w.strike(D("112.500")),
                   w.pct(D("2.78")), w.score(D("0.0467")), w.delta(134.07082977301557), w.signed_delta(32.364320),
                   w.count(18), w.days(73)]
        self.assertEqual(["3.50", "3.425", "19006.00", "+10.00", "-16.00", "0.00", "0.50", "112.5", "2.78 %", "0.0467",
                          "134.07", "+32.36", "18", "73 calendar days"], printed)
        self.assertEqual(["3.50", "3.425", "19006.00", "+10.00", "-16.00", "0.00", "0.50", "112.5", "2.78", "0.0467",
                          "134.07", "+32.36", "18", "73"], w.emitted)
        self.assertEqual("2026-11-20", w.date(datetime.date(2026, 11, 20)))
        self.assertEqual("19:31 UTC", w.clock(STAMP))
        self.assertEqual("16:30 ET", w.et_clock(datetime.time(16, 30)))
        self.assertEqual("AG-20261020-01", w.trade_id("AG-20261020-01"))
        self.assertEqual("sha 5155427c", w.sha(SHA))
        self.assertEqual(["2026-11-20", "AG-20261020-01", "sha 5155427c"], w.identifiers)
        self.assertEqual(["19:31 UTC", "16:30 ET"], w.clocks)
        document = w.document(STAMP)
        self.assertEqual({"emitted_numbers", "rule_ids", "identifiers", "clocks", "as_of", "footer"}, set(document))
        self.assertEqual(([], "structuring", "2026-11-03T19:31:12+00:00"),
                         (document["rule_ids"], document["footer"], document["as_of"]))
        alert_lint.load_emitted(json.loads(json.dumps(document)))

    def test_builders_refuse_owner_text_occ_symbols_and_the_rule_word(self) -> None:
        w = T.Writer()
        with self.assertRaises(ValueError):
            w.trade_id("Brokerage (margin)")
        with self.assertRaises(ValueError):
            T.leg_line(w, 1, 1, "open", "short", 2, "WMT261120C00105000", JAN15, D("115"), "CALL", "bid 3.55")
        with self.assertRaises(ValueError):
            w.words("closes WMT261120C00105000 at the ask")
        with self.assertRaises(ValueError):
            w.words("FAILS_RULE credit_only")
        with self.assertRaises(ValueError):
            T.counts_caption(w, 1, 0, 1, 0, {"FAILS_RULE": 1})
        with self.assertRaises(ValueError):
            T.structure_words("Brokerage (margin)")
        accounts = [type("Entry", (), {"name": "Brokerage (margin)", "type": "margin"})(),
                    type("Entry", (), {"name": "Roth IRA", "type": "ira"})()]
        self.assertEqual("account 1 of desk.toml (margin)", T.account_words(w, "Brokerage (margin)", accounts, 4))
        self.assertEqual("account 2 of desk.toml (ira)", T.account_words(w, "Roth IRA", accounts, 8))
        self.assertEqual("the account in row 9 of positions.csv", T.account_words(w, "My Joint Acct", accounts, 9))
        self.assertNotIn("Brokerage", T.account_words(w, "Brokerage (margin)", accounts, 4))

    def test_finish_withholds_a_probe_text(self) -> None:
        for line, code in (("the WMT261120C00105000 call", T.CODE_OCC_SYMBOL), ("FAILS_RULE credit_only", T.CODE_FAILS_RULE),
                           ("held in Brokerage (margin)", T.CODE_OWNER_TEXT), ("you should hold it", "forbidden_language")):
            w = T.Writer()
            rendered = T.finish(w, [line], STAMP, "roll_analysis", owner_texts=["Brokerage (margin)", "core holding"])
            with self.subTest(code=code):
                self.assertFalse(rendered.ok)
                self.assertIn(code, rendered.codes)
                self.assertTrue(rendered.text.startswith("WITHHELD | the text of roll_analysis.py failed the alert lint ("))
                self.assertNotIn(line, rendered.text)
                self.assertTrue(lint(rendered.text, rendered.writer).ok, lint(rendered.text, rendered.writer).problems)
                self.assertEqual(["", FOOTER], rendered.text.split("\n")[-2:])
        w = T.Writer()
        good = T.finish(w, [T.LIMIT_LINE], STAMP, "exit_ticket")
        self.assertEqual((True, [], T.LIMIT_LINE + "\n\n" + FOOTER), (good.ok, good.codes, good.text))
        self.assertEqual("structuring", good.document["footer"])


# ------------------------------------------------------------------------------------------------
# Every fixed line: strict lint in structuring mode, refused in rules mode
# ------------------------------------------------------------------------------------------------


GREEN, YELLOW, RED = "\U0001F7E2", "\U0001F7E1", "\U0001F534"
OBJECTIVES = structuring_config.OBJECTIVE_DEFS


def fixed_lines() -> List[tuple]:
    """``(name, writer, lines, structuring_content)`` for every fixed line and line shape the builders print (the desk
    view's strip, captions, items and quotes included)."""
    out = []

    def add(name: str, build, content: bool = False) -> None:
        w = T.Writer()
        built = build(w)
        out.append((name, w, built if isinstance(built, list) else [built], content))

    for name, line in (("LIMIT_LINE", T.LIMIT_LINE), ("ACT_LINE", T.ACT_LINE), ("AFTER_FILL_LINE", T.AFTER_FILL_LINE),
                       ("AFTER_EXPIRY_LINE", T.AFTER_EXPIRY_LINE), ("MODEL_LINE", T.MODEL_LINE),
                       ("CAVEAT_LINE", T.CAVEAT_LINE), ("POINTER_LINE", T.POINTER_LINE),
                       ("NONE_QUALIFIES_LINE", T.NONE_QUALIFIES_LINE), ("CASH_BALANCE_LINE", T.CASH_BALANCE_LINE),
                       ("LEGS_CAPTION", T.LEGS_CAPTION), ("FAILURES_CAPTION", T.FAILURES_CAPTION),
                       ("CASH_CAPTION", T.CASH_CAPTION)):
        add(name, lambda w, line=line: line, name == "NONE_QUALIFIES_LINE")
        add(name + " quoted", lambda w, line=line: "> - " + line, name == "NONE_QUALIFIES_LINE")
    add("notes", lambda w: T.notes_block([T.MODEL_LINE, T.quotes_line(w, STAMP), T.criteria_line(w, SHA), T.LIMIT_LINE,
                                          T.ACT_LINE, T.validity_line(w, datetime.datetime(2026, 11, 3, 20, 20,
                                                                                           tzinfo=UTC))]))
    add("caveat quoted", lambda w: "> " + T.CAVEAT_LINE)
    add("context", lambda w: T.context_line(w, T.account_cell(w, "Roth IRA", ACCOUNTS, 7), "SPY", D("648.12"),
                                            D("632.40"), STAMP))
    add("ticket context", lambda w: T.strip_line([
        ("account", T.account_cell(w, "Brokerage (margin)", ACCOUNTS, 4)), ("computed", w.clock(NOW)),
        ("valid until", T.valid_until_cell(w, datetime.datetime(2026, 11, 3, 20, 20, tzinfo=UTC), True)),
        ("quotes", T.quotes_cell(w, STAMP))]))
    add("criteria_missing", lambda w: T.criteria_missing_line(w))
    add("config_unacknowledged", lambda w: T.config_unacknowledged_line(w, [
        {"name": "desk_toml", "old": "a" * 64, "new": "b" * 64}, {"name": "structuring_toml", "old": None, "new": SHA}]))
    add("config_changed", lambda w: T.config_changed_line(w, [{"name": "rulebook", "old": "c" * 64, "new": "d" * 64}]))
    add("withheld", lambda w: T.withheld_line(w, "exit_ticket", ["number_not_emitted", "forbidden_language"]))
    add("let_assign", lambda w: T.let_assign_line(w, D("105"), "CALL", datetime.time(16, 30)))
    add("cash", lambda w: T.cash_block(w, D("10.00"), D("35.00"), D("-16.00"), D("-1.00"), 1, D("700.00")))
    add("cash without mids", lambda w: T.cash_block(w, D("-700.00"), None, D("-16.00"), None, 2, D("700.00")))
    add("ticket_header", lambda w: T.ticket_header(w, "AG-20261020-01", "WMT", "covered_call", "ROLL"), True)
    add("roll_header", lambda w: T.roll_header(w, "AG-20261020-01", "WMT", "covered_call", datetime.date(2026, 11, 20),
                                               D("105")), True)
    add("proposal_header", lambda w: T.proposal_header(w, "AG-20261028-03", "SPY", "collar", D("100")), True)
    add("legs", lambda w: [T.LEGS_CAPTION,
                           "- " + T.leg_line(w, 1, 3, "close", "short", 2, "WMT", datetime.date(2026, 11, 20), D("105"),
                                             "CALL", T.quote_segment(w, D("3.35"), D("3.50"), D("3.425"), "B")),
                           "- " + T.share_leg_line(w, 2, 3, D("200"), "WMT", T.quote_segment(
                               w, D("104.20"), D("104.26"), D("104.23"), "A")),
                           "- " + T.leg_line(w, 3, 3, "close", "short", 2, "WMT", datetime.date(2026, 11, 20),
                                             D("105"), "CALL", T.quote_segment(w, D("0.12"), D("0.16"), D("0.14"), "E",
                                                                               structuring.GRADE_E_NOTE))], True)
    add("best", lambda w: [T.best_line(w, 1, 20, OBJECTIVES["extrinsic_per_day"], D("0.0467"), 1),
                           "- " + T.leg_line(w, 2, 2, "open", "short", 2, "WMT", JAN15, D("115"), "CALL",
                                             T.side_segment(w, "bid", D("3.55"), "B", 18, None))], True)
    add("best lowest", lambda w: T.best_line(w, 1, 12, OBJECTIVES["stress_cost_per_day"], D("0.1856"), 5), True)
    for objective in OBJECTIVES.values():
        add("objective " + objective.id, lambda w, objective=objective: "> - " + T.objective_line(w, objective))
    add("tie", lambda w: "> - " + T.tie_line(w, "cap_within_budget", "put_strike"))
    add("not_applicable", lambda w: T.not_applicable_line(w, "AG-20261028-03", "roll analysis",
                                                          "AG-20261028-03 is a protective put: no short call"))
    add("unavailable", lambda w: T.unavailable_line(w, "AG-20261020-01", "roll analysis", "no usable quote of the "
                                                    "underlying"))
    add("held", lambda w: ["**" + T.NONE_QUALIFIES_LINE + "**", "- " + T.held_line(w, structuring.HeldLeg(
        4, "WMT261120C00105000", "WMT", "CALL", "short", 2, D("105"), datetime.date(2026, 11, 20),
        datetime.date(2026, 11, 20), D("0.12"), D("0.16"), D("0.14"), D("28.57"), "E", "fresh", 18, STAMP, None, None,
        D("3.42"), D("-684.00"), D("0.16"), "ask", structuring.GRADE_E_NOTE, D("652.00")))], True)
    add("exposure_refused", lambda w: T.exposure_refused_line(w, "AG-20261020-01", "WMT", "covered_call", 1, 2))
    add("multi_leg_collar", lambda w: "- " + T.multi_leg_line("collar"))
    add("outside_bands", lambda w: T.outside_bands_line(w, {"puts": 7, "calls": 10}))
    add("check items", lambda w: [
        T.bold("checks for this candidate:"),
        T.check_item(["floor", "%s below" % w.pct(D("4.34")), "%s to %s %%" % (w.count(3), w.count(10)), T.STATUS_PASS]),
        T.check_item(["cap", "%s above" % w.pct(D("1.50")), "%s to %s %%" % (w.count(2), w.count(10)), T.STATUS_FAIL]),
        T.check_item(["ex-dividend", "%s, inside the call's life" % w.date(datetime.date(2026, 12, 18)),
                      "accepted by #assign-ok", T.STATUS_ACCEPTED]),
        T.check_item(["cost", "ask %s (%s in all)" % (w.price(D("23.10")), w.amount(D("2310"))), "", ""])])
    add("coverage", lambda w: "- " + T.coverage_line(w, 3, D("321.6301"), "collar"))
    add("counts", lambda w: T.counts_caption(w, 20, 0, 18, 2, {"abs_delta": 14, "ex_dividend": 18}, True), True)
    return out


class FixedLineTest(unittest.TestCase):
    def test_every_fixed_line_lints_in_structuring_mode_only(self) -> None:
        for name, w, lines, content in fixed_lines():
            text = closed(w, lines)
            with self.subTest(line=name):
                result = lint(text, w)
                self.assertTrue(result.ok, (name, lines, result.problems))
                self.assertFalse(lint(text, w, footer="rules").ok)
                self.assertFalse(lint(text, w, footer="snapshot").ok)
                if content:
                    rules = "\n".join(lines) + "\n" + alert_lint.footer_line(STAMP)
                    refused = lint(rules, w, footer="rules")
                    self.assertIn("footer_missing", [finding.code for finding in refused.findings], name)

    def test_the_fixed_words(self) -> None:
        self.assertEqual("you supply the limit; the desk prints none", T.LIMIT_LINE)
        self.assertEqual("if you decide to act, you type it into your brokerage account yourself", T.ACT_LINE)
        self.assertEqual("after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
                         T.AFTER_FILL_LINE)
        self.assertEqual("prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these "
                         "American-style options is not modelled", T.MODEL_LINE)
        self.assertEqual("the score ranks the candidates only on the objective you chose; it is not a forecast",
                         T.CAVEAT_LINE)
        self.assertEqual("a ticket for one of these: ask the Aegis Desk Lead for a ROLL ticket with its expiration "
                         "and strike", T.POINTER_LINE)
        self.assertEqual(("\U0001F7E2", "\U0001F7E1", "\U0001F534"), (T.STATUS_PASS, T.STATUS_ACCEPTED, T.STATUS_FAIL))
        self.assertEqual(alert_lint.STATUS_SYMBOLS, (T.STATUS_PASS, T.STATUS_ACCEPTED, T.STATUS_FAIL))
        w = T.Writer()
        self.assertEqual("after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
                         T.validity_line(w, datetime.datetime(2026, 11, 3, 20, 20, tzinfo=UTC)))
        self.assertEqual("20:20 UTC (30 min)", T.valid_until_cell(w, datetime.datetime(2026, 11, 3, 20, 20, tzinfo=UTC),
                                                                  True))
        self.assertEqual("21:00 UTC", T.valid_until_cell(w, datetime.datetime(2026, 11, 3, 21, 0, tzinfo=UTC), False))
        self.assertEqual("quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 "
                         "UTC", T.quotes_line(T.Writer(), STAMP))
        self.assertEqual("19:31 UTC, 15-min delayed", T.quotes_cell(T.Writer(), STAMP))
        self.assertEqual("criteria: your structuring.toml (sha 5155427c)", T.criteria_line(T.Writer(), SHA))
        self.assertEqual("bid 3.35, ask 3.50, mid 3.425 | quote grade B",
                         T.quote_segment(T.Writer(), D("3.35"), D("3.50"), D("3.425"), "B"))
        self.assertEqual("extrinsic_per_day: the bid's time value per calendar day, in percent of the share price",
                         T.objective_line(T.Writer(), OBJECTIVES["extrinsic_per_day"]))
        self.assertEqual("**BEST CANDIDATE 1 of 12** by your objective stress_cost_per_day: **0.1856**, the lowest among "
                         "the 5 candidates that passed every criterion",
                         T.best_line(T.Writer(), 1, 12, OBJECTIVES["stress_cost_per_day"], D("0.1856"), 5))
        self.assertEqual("UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your "
                         "criteria (SETUP step 9)", T.criteria_missing_line(T.Writer()))
        self.assertEqual("WITHHELD | the text of exit_ticket.py failed the alert lint (forbidden_language) and is "
                         "withheld; the JSON of exit_ticket.py --json shows it",
                         T.withheld_line(T.Writer(), "exit_ticket", ["forbidden_language"]))
        self.assertEqual("UNAVAILABLE | CONFIG CHANGED: desk.toml: sha aaaaaaaa -> sha bbbbbbbb | structuring.toml: not "
                         "acknowledged -> sha 5155427c | the desk names no candidate and prints no ticket while desk.toml, "
                         "positions.csv or structuring.toml differ from what you acknowledged | acknowledge with ack.py "
                         "config --owner-tapped",
                         T.config_unacknowledged_line(T.Writer(), [
                             {"name": "desk_toml", "old": "a" * 64, "new": "b" * 64},
                             {"name": "structuring_toml", "old": None, "new": SHA}]))
        self.assertEqual("no leg to enter: if the 105 call is at least 0.01 in the money at expiry, OCC exercises it by "
                         "exception; your broker's cutoff is 16:30 ET (desk.toml)",
                         T.let_assign_line(T.Writer(), D("105"), "CALL", datetime.time(16, 30)))
        self.assertEqual([
            "**The cash**",
            "- **net cash** +10.00 at the conservative prices, +35.00 at the mids",
            "- **realized on the closing leg** -16.00 at the conservative prices, -1.00 at the mids",
            "- **the most the steps need at once** 700.00",
        ],
                         T.cash_block(T.Writer(), D("10.00"), D("35.00"), D("-16.00"), D("-1.00"), 1, D("700.00")))
        self.assertEqual([
            "**The cash**",
            "- **net cash** -20,140.00 at the conservative prices, at the mids: no mid: a quote side is stale",
            "- **realized on the closing legs** +1,124.00 at the conservative prices, at the mids: no mid: a quote "
            "side is stale",
            "- **the most the steps need at once** 20,140.00",
        ],
                         T.cash_block(T.Writer(), D("-20140"), None, D("1124"), None, 2, D("20140")))
        self.assertEqual("### TICKET AG-20261020-01 | WMT covered call: a roll of the short call (action ROLL)",
                         T.ticket_header(T.Writer(), "AG-20261020-01", "WMT", "covered_call", "ROLL"))
        self.assertEqual("### TICKET AG-20261028-03 | SPY protective put: a roll of the long put (action ROLL)",
                         T.ticket_header(T.Writer(), "AG-20261028-03", "SPY", "protective_put", "ROLL", T.ROLL_PUT_WORDS))
        self.assertEqual("### STRUCTURING AG-20261020-01 | WMT covered call: the roll analysis of the short 2026-11-20 105 "
                         "call", T.roll_header(T.Writer(), "AG-20261020-01", "WMT", "covered_call",
                                               datetime.date(2026, 11, 20), D("105")))
        self.assertEqual("### PROPOSAL AG-20261022-02 | a collar on the 321.6301 NVDA shares you hold",
                         T.proposal_header(T.Writer(), "AG-20261022-02", "NVDA", "collar", D("321.6301")))
        self.assertEqual("1 of desk.toml (margin)", T.account_cell(T.Writer(), "Brokerage (margin)", ACCOUNTS, 4))
        self.assertEqual("row 9 of positions.csv", T.account_cell(T.Writer(), "My Joint Acct", ACCOUNTS, 9))
        self.assertEqual("entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher "
                         "level", T.multi_leg_line("roll"))
        self.assertEqual(approval.MULTI_LEG_NOTE, T.multi_leg_line("roll"))

    def test_the_desk_view_builders(self) -> None:
        w = T.Writer()
        # the desk view prints no table (a chat client draws one as a card of its own and splits the answer)
        self.assertEqual("*a x | b 1*", T.strip_line([("a", "x"), ("b", w.count(1))]))
        self.assertEqual("- " + GREEN + " **floor** 4.34 % below | your criteria: 3 to 10 %",
                         T.check_item(["floor", "4.34 % below", "3 to 10 %", T.STATUS_PASS]))
        self.assertEqual("- " + YELLOW + " **earnings** 2026-11-17, inside the call's life | accepted by #earnings-ok",
                         T.check_item(["earnings", "2026-11-17, inside the call's life", "accepted by #earnings-ok",
                                       T.STATUS_ACCEPTED]))
        self.assertEqual("- " + RED + " **ex-dividend** 2026-12-03, inside the call's life | excluded by your "
                         "structuring.toml", T.check_item(["ex-dividend", "2026-12-03, inside the call's life",
                                                           "excluded by your structuring.toml", T.STATUS_FAIL]))
        self.assertEqual("- **cost** ask 23.10", T.check_item(["cost", "ask 23.10", "", ""]))
        self.assertEqual(["- **now** in the money", "- **if assigned** the shares go"],
                         T.labelled_items([["now", "in the money"], ["if assigned", "the shares go"]]))
        self.assertEqual("- " + GREEN + " passes", T.item_line("passes", T.STATUS_PASS))
        for bad in (lambda: T.strip_line([]), lambda: T.strip_line([("a", "x\ny")]), lambda: T.item_line("x", "ok"),
                    lambda: T.item_line("x\ny"), lambda: T.check_item(["a", "b"]), lambda: T.labelled_items([["a"]]),
                    lambda: T.status_mark("ok"), lambda: T.status_mark(T.STATUS_FAIL, ["FAILS_RULE"]),
                    lambda: T.counts_caption(T.Writer(), 20, 1, 18, 0, {}),
                    lambda: T.counts_caption(T.Writer(), 1, 0, 1, 0, {"FAILS_RULE": 1})):
            with self.assertRaises(ValueError):
                bad()
        self.assertEqual((RED, "fails credit_only, ex_dividend"),
                         T.status_mark(T.STATUS_FAIL, ["credit_only", "ex_dividend"]))
        self.assertEqual((GREEN, ""), T.status_mark(T.STATUS_PASS))
        self.assertEqual("credit 0.05 (10.00 in all)", T.net_words(T.Writer(), D("0.05"), D("10.00")))
        self.assertEqual("debit 0.70 (1,400.00 in all)", T.net_words(T.Writer(), D("-0.70"), D("-1400.00")))
        self.assertEqual("credit 0.00", T.net_words(T.Writer(), D("0")))
        self.assertEqual("63,240.00", T.Writer().amount(D("63240")))
        self.assertEqual(("+20,140.00", "-16.00", "0.00"), (T.Writer().amount(D("20140"), signed=True),
                                                             T.Writer().amount(D("-16"), signed=True),
                                                             T.Writer().amount(D("0"), signed=True)))
        w = T.Writer()
        self.assertEqual("3 contracts cover 300 of your 321.6301 shares; the other 21.6301 are not part of the collar",
                         T.coverage_line(w, 3, D("321.6301"), "collar"))
        self.assertEqual(["3", "300", "321.6301", "21.6301"], w.emitted)
        self.assertEqual("1 contract covers 100 of your 150 shares; the other 50 are not part of the covered call",
                         T.coverage_line(T.Writer(), 1, D("150"), "covered_call"))
        self.assertIsNone(T.coverage_line(T.Writer(), 2, D("200"), "covered_call"))
        events = (
            (structuring.CODE_CROSSES_EARNINGS, {"date": datetime.date(2026, 11, 17), "time": "pre"}, False,
             "2026-11-17 (before the open), inside the call's life"),
            (structuring.CODE_EARNINGS_UNKNOWN, {"status": "unknown"}, True,
             "unknown in research/calendar.toml, counted inside the new call's life"),
            (structuring.CODE_EARNINGS_UNKNOWN, {"status": "passed", "date": datetime.date(2026, 11, 17)}, True,
             "2026-11-17 has passed, the next is unknown: counted inside the new call's life"),
            (structuring.CODE_CROSSES_EX_DIVIDEND, {"ex_date": datetime.date(2026, 12, 18), "cum_day":
                                                    datetime.date(2026, 12, 17), "status": "estimated"}, False,
             "2026-12-18 (estimated, cum 2026-12-17), inside the call's life"),
            (structuring.CODE_DIVIDEND_UNKNOWN, {"status": "second_ex_date"}, False,
             "a second ex-date may fall before the call's last trading day"),
            (structuring.CODE_DIVIDEND_UNKNOWN, {"status": "unknown"}, False,
             "unknown in research/calendar.toml, counted inside the call's life"))
        for code, value, replacement, words in events:
            w = T.Writer()
            key = structuring.KEY_EARNINGS if "earnings" in code else structuring.KEY_EX_DIVIDEND
            with self.subTest(code=code, value=value):
                self.assertEqual(words, T.event_words(w, structuring.Reason(key, code, value, None), replacement))
                self.assertTrue(lint(closed(w, [T.check_item(["earnings", words, "accepted by #earnings-ok",
                                                              T.STATUS_ACCEPTED])]), w).ok)
        with self.assertRaises(ValueError):
            T.event_words(T.Writer(), structuring.Reason("dte", structuring.CODE_DTE_OUTSIDE_WINDOW, 30, (40, 75)))

    def test_applicability_and_unavailable_words_lint(self) -> None:
        texts = [S._UNAVAILABLE_WORDS[cause] for cause in S.CAUSES] + [S.TICKET_CLOSED_WORDS]
        for cause in texts:
            w = T.Writer()
            self.assertTrue(lint(closed(w, [T.unavailable_line(w, "AG-20261020-01", "roll analysis", cause)]), w).ok,
                            cause)
        from tests.engine.test_structuring import (CALL, CLOSED_CALL, CLOSED_SHARES, FEW, INDEX_PUT, PUT, SHARES,
                                                   SHORT_PUT, group_of)
        groups = [group_of(*rows) for rows in ((SHARES, PUT), (SHARES, PUT, CALL), (SHORT_PUT,), (SHARES,), (FEW,),
                                               (SHARES, CALL), (INDEX_PUT,), (CLOSED_SHARES, CLOSED_CALL),
                                               (SHARES.replace(",200,", ",100,"), PUT.replace(",2,", ",1,")))]
        reasons = set()
        for group in groups:
            for answer in [S.roll_applicability(group, CRITERIA)] + [
                    S.proposal_applicability(group, structure, CRITERIA) for structure in S.PROPOSAL_STRUCTURES] + [
                    S.ticket_applicability(group, action, CRITERIA) for action in S.ACTIONS]:
                if answer is not None:
                    reasons.add(answer.reason)
        self.assertGreater(len(reasons), 8)
        reasons.update({S.NA_TWO_ROWS_WORDS % ("AG-20261101-01", "short call"),
                        S.NA_TWO_ROWS_WORDS % ("AG-20261101-01", "long put"),
                        "no free shares: 200 WMT shares are covered by 2 short calls; the desk proposes no collar"})
        for reason in sorted(reasons):
            w = T.Writer()
            line = T.not_applicable_line(w, "AG-20261101-01", "roll analysis", reason)
            result = lint(closed(w, [line]), w)
            self.assertTrue(result.ok, (reason, result.problems))


# ------------------------------------------------------------------------------------------------
# Golden texts at the regular instant
# ------------------------------------------------------------------------------------------------

WMT_ROLL = """### STRUCTURING AG-20261020-01 | WMT covered call: the roll analysis of the short 2026-11-20 105 call
*account 1 of desk.toml (margin) | WMT mid 104.23 | your cost 98.50 | quotes 19:31 UTC, 15-min delayed*

**BEST CANDIDATE 1 of 20** by your objective extrinsic_per_day: **0.0467**, the highest among the 1 candidate that passed every criterion
- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | ask 3.50 | quote grade B, 18 min old
- leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55 | quote grade B, 18 min old

> the score ranks the candidates only on the objective you chose; it is not a forecast

**Checks for this candidate**
- \U0001F7E2 **net per share** credit 0.05 (10.00 in all) | your criteria: credit only
- \U0001F7E2 **strike** 115 | your criteria: at or above your cost 98.50 or the held strike 105
- \U0001F7E2 **absolute delta** 0.33 | your criteria: 0.15 to 0.35
- \U0001F7E2 **bid** 3.55 | your criteria: at least 0.50
- \U0001F7E2 **calendar days** 73 | your criteria: 40 to 75
- \U0001F7E2 **spread** 2.78 % | your criteria: at most 25 %
- \U0001F7E1 **ex-dividend** 2026-11-24 (cum 2026-11-23), inside the new call's life | accepted by #assign-ok

**The group, now and after both legs**
- **max loss** 19,016.00 now, 19,006.00 after
- **max profit** 1,984.00 now, 3,994.00 after
- **breakeven** 95.08 now, 95.03 after
- **group delta** 101.71 now, 134.07 after

**Replacement candidates by rank** 1 of 20 passed your criteria; 19 failed (abs_delta 15, credit_only 4, strike_floor 4)
- \U0001F7E2 **1** 2027-01-15 | new call 115 | credit 0.05 | extrinsic_per_day 0.0467
- \U0001F534 **2** 2026-12-18 | new call 112.5 | debit 0.70 | extrinsic_per_day 0.0597 | fails credit_only
- \U0001F534 **3** 2026-12-18 | new call 115 | debit 1.40 | extrinsic_per_day 0.0448 | fails credit_only

a ticket for one of these: ask the Aegis Desk Lead for a ROLL ticket with its expiration and strike

> - extrinsic_per_day: the bid's time value per calendar day, in percent of the share price
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - criteria: your structuring.toml (sha 5155427c)

""" + FOOTER

FOOTER_NOW = alert_lint.structuring_footer_line(NOW)

NVDA_ROLL = """### STRUCTURING AG-20261022-02 | NVDA covered call: the roll analysis of the short 2026-11-20 150 call
*account 1 of desk.toml (margin) | NVDA mid 148.925 | your cost 141.20 | quotes 19:31 UTC, 15-min delayed*

**no candidate passed every criterion: the desk names no best candidate**
- the net credits close the held 2026-11-20 150 call | ask 3.30 | quote grade C, 18 min old

**Replacement candidates by rank** 0 of 20 passed your criteria; 18 failed (abs_delta 14, min_credit 1, credit_only 6, strike_floor 6, ex_dividend 18); 2 not priced
- \U0001F534 **1** 2027-01-15 | new call 160 | debit 0.05 | extrinsic_per_day 0.0299 | fails credit_only, ex_dividend
- \U0001F534 **2** 2026-12-18 | new call 160 | debit 1.45 | extrinsic_per_day 0.0276 | fails credit_only, ex_dividend
- \U0001F534 **3** 2027-01-15 | new call 165 | debit 1.35 | extrinsic_per_day 0.0179 | fails credit_only, ex_dividend

**Why each one fails**
- 1 fails credit_only: a net debit of 0.05
- 1, 2, 3 fail ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls inside the new call's life
- 2 fails credit_only: a net debit of 1.45
- 3 fails credit_only: a net debit of 1.35

a ticket for one of these: ask the Aegis Desk Lead for a ROLL ticket with its expiration and strike

> - extrinsic_per_day: the bid's time value per calendar day, in percent of the share price
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - criteria: your structuring.toml (sha 5155427c)

""" + FOOTER

SPY_ROLL_NA = """NOT_APPLICABLE | AG-20261028-03 roll analysis | AG-20261028-03 is a protective put: the roll analysis covers the short call of a covered call; a ROLL ticket prices the replacement of its long put that you name; the roll analysis of a long put, a short put or a collar is planned for Stage 2 (not in this release)

""" + FOOTER_NOW

PUT_NOTHING = """NOTHING_TO_PROPOSE | AG-20261028-03 protective put proposal | the 100 SPY shares already carry 1 long put

""" + FOOTER_NOW

WMT_CALL = """### PROPOSAL AG-20261020-01 | a covered call on the 200 WMT shares you hold
*account 1 of desk.toml (margin) | WMT mid 104.23 | your cost 98.50 | quotes 19:31 UTC, 15-min delayed*

**BEST CANDIDATE 1 of 20** by your objective extrinsic_per_day: **0.0597**, the highest among the 5 candidates that passed every criterion
- leg 1 of 1: opens a new short 2 x WMT 2026-12-18 112.5 call | bid 2.80, ask 2.90, mid 2.85 | quote grade B

> the score ranks the candidates only on the objective you chose; it is not a forecast

**Checks for this candidate**
- \U0001F7E2 **strike** 112.5 | your criteria: at or above your cost 98.50
- \U0001F7E2 **absolute delta** 0.32 | your criteria: 0.15 to 0.35
- \U0001F7E2 **bid** 2.80 (560.00 in all) | your criteria: at least 0.50
- \U0001F7E2 **calendar days** 45 | your criteria: 40 to 75
- \U0001F7E2 **spread** 3.51 % | your criteria: at most 25 %
- \U0001F7E1 **earnings** 2026-11-17 (before the open), inside the call's life | accepted by #earnings-ok
- \U0001F7E1 **ex-dividend** 2026-11-24 (cum 2026-11-23), inside the call's life | accepted by #assign-ok

**The group, now and after the leg**
- **max loss** 19,700.00 now, 19,140.00 after
- **max profit** unbounded now, 3,360.00 after
- **breakeven** 98.50 now, 95.70 after
- **group delta** 200.00 now, 135.05 after

**Approval-level check:** the highest step needs level 1 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides
- after leg 1: you hold 200 WMT shares, 2 short calls | level 1

**Candidates by rank** 5 of 20 passed your criteria; 15 failed (abs_delta 15, strike_floor 4)
- \U0001F7E2 **1** 2026-12-18 | strike 112.5 | delta 0.32 | bid 2.80 | extrinsic_per_day 0.0597
- \U0001F7E2 **2** 2027-01-15 | strike 115 | delta 0.33 | bid 3.55 | extrinsic_per_day 0.0467
- \U0001F7E2 **3** 2026-12-18 | strike 115 | delta 0.27 | bid 2.10 | extrinsic_per_day 0.0448

> - extrinsic_per_day: the bid's time value per calendar day, in percent of the share price
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - criteria: your structuring.toml (sha 5155427c)
> - you supply the limit; the desk prints none
> - if you decide to act, you type it into your brokerage account yourself

""" + FOOTER

SPY_PUT = """### PROPOSAL AG-20261028-03 | a protective put on the 100 SPY shares you hold
*account 2 of desk.toml (ira) | SPY mid 648.12 | your cost 632.40 | quotes 19:31 UTC, 15-min delayed*

**BEST CANDIDATE 1 of 12** by your objective stress_cost_per_day: **0.1856**, the lowest among the 5 candidates that passed every criterion
- leg 1 of 1: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | quote grade A

> the score ranks the candidates only on the objective you chose; it is not a forecast

**Checks for this candidate**
- \U0001F7E2 **floor** 5.88 % below | your criteria: 3 to 10 %
- **cost** ask 23.10 (2,310.00 in all)
- \U0001F7E2 **covered at your stress drop** 91.504 per share (9,150.40 in all) | your criteria: drop 20 % (stressed price 518.496)
- \U0001F7E2 **calendar days** 136 | your criteria: 120 to 210
- \U0001F7E2 **spread** 0.87 % | your criteria: at most 25 %

**The group, now and after the leg**
- **max loss** 63,240.00 now, 4,550.00 after
- **max profit** unbounded now, unbounded after
- **breakeven** 632.40 now, 655.50 after
- **group delta** 100.00 now, 70.17 after

**Approval-level check:** the highest step needs level 2 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides
- after leg 1: you hold 100 SPY shares, 1 long put | level 2

**Candidates by rank** 5 of 12 passed your criteria; 7 failed (floor_pct 7)
- \U0001F7E2 **1** 2027-03-19 | strike 610 | floor 5.88 % | ask 23.10 | stress_cost_per_day 0.1856
- \U0001F7E2 **2** 2027-03-19 | strike 620 | floor 4.34 % | ask 25.65 | stress_cost_per_day 0.1858
- \U0001F7E2 **3** 2027-03-19 | strike 625 | floor 3.57 % | ask 27.05 | stress_cost_per_day 0.1868

> - stress_cost_per_day: the put's ask per calendar day per 100 of loss it offsets at your stress drop
> - rank 2 lies within the best's own quote spread on stress_cost_per_day: these quotes do not tell it apart from the best
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - carry: the 2027-03-19 options are priced at the dividend carry their put-call parity implies, 0.01 % a year, because those quotes exclude the calendar's 1.15 %
> - criteria: your structuring.toml (sha 5155427c)
> - you supply the limit; the desk prints none
> - if you decide to act, you type it into your brokerage account yourself

""" + FOOTER

SPY_COLLAR = """### PROPOSAL AG-20261028-03 | a collar on the 100 SPY shares you hold
*account 2 of desk.toml (ira) | SPY mid 648.12 | your cost 632.40 | quotes 19:31 UTC, 15-min delayed*

**BEST CANDIDATE 1 of 10** by your objective cap_within_budget: **4.92**, the highest among the 4 candidates that passed every criterion
- leg 1 of 2: opens a new long 1 x SPY 2027-03-19 620 put | bid 25.45, ask 25.65, mid 25.55 | quote grade A
- leg 2 of 2: opens a new short 1 x SPY 2027-03-19 680 call | bid 26.40, ask 26.60, mid 26.50 | quote grade A

> the score ranks the candidates only on the objective you chose; it is not a forecast

**Checks for this candidate**
- \U0001F7E2 **floor** 4.34 % below | your criteria: 3 to 10 %
- \U0001F7E2 **cap** 4.92 % above | your criteria: 2 to 10 %
- \U0001F7E2 **net per share** credit 0.75 (75.00 in all) | your criteria: net debit at most 0.00
- \U0001F7E2 **call strike** 680 | your criteria: at or above your cost 632.40
- \U0001F7E2 **calendar days** 136 | your criteria: 120 to 210
- \U0001F7E2 **spreads** 0.78 % and 0.75 % | your criteria: at most 25 %
- **spread cost** 0.20 per share (20.00 in all)
- \U0001F7E1 **ex-dividend** 2026-12-18 (estimated, cum 2026-12-17), inside the call's life | accepted by #assign-ok
- \U0001F7E1 **ex-dividend** a second ex-date may fall before the call's last trading day | accepted by #assign-ok

**The group, now and after both legs**
- **max loss** 63,240.00 now, 1,165.00 after
- **max profit** unbounded now, 4,835.00 after
- **breakeven** 632.40 now, 631.65 after
- **group delta** 100.00 now, 23.75 after

**Approval-level check (leg 1 first):** the highest step needs level 2 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides
- after leg 1: you hold 100 SPY shares, 1 long put | level 2
- after leg 2: you hold 100 SPY shares, 1 short call, 1 long put | level 1
- entered as one multi-leg order, some brokers class a collar as a spread, which can need a higher level

**Candidates by rank** 4 of 10 passed your criteria; 6 failed (max_net_debit 6)
- \U0001F7E2 **1** 2027-03-19 | put 620 / call 680 | floor 4.34 % | cap 4.92 % | credit 0.75
- \U0001F7E2 **2** 2027-03-19 | put 610 / call 680 | floor 5.88 % | cap 4.92 % | credit 3.30
- \U0001F7E2 **3** 2027-03-19 | put 600 / call 680 | floor 7.42 % | cap 4.92 % | credit 5.60

not paired, outside their bands: 7 puts outside your floor band and 10 calls outside your cap band

> - cap_within_budget: the cap's distance above the share price, in percent, among pairs within your net-debit budget
> - tie on cap_within_budget broken by the put strike (higher first)
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - carry: the 2027-03-19 options are priced at the dividend carry their put-call parity implies, 0.01 % a year, because those quotes exclude the calendar's 1.15 %
> - criteria: your structuring.toml (sha 5155427c)
> - you supply the limit; the desk prints none
> - if you decide to act, you type it into your brokerage account yourself

""" + FOOTER

EXPIRY_DAY = """### STRUCTURING AG-20261020-01 | WMT covered call: the roll analysis of the short 2026-11-20 105 call
*account 1 of desk.toml (margin) | WMT mid 104.93 | your cost 98.50 | quotes 19:31 UTC, 15-min delayed*

**no candidate passed every criterion: the desk names no best candidate**
- the net credits close the held 2026-11-20 105 call | ask 0.16 | quote grade E, 18 min old | quote grade E: the conservative side stands

**Replacement candidates by rank** 0 of 10 passed your criteria; 10 failed (abs_delta 8, strike_floor 2, earnings 10)
- \U0001F534 **1** 2027-01-15 | new call 115 | credit 2.79 | extrinsic_per_day 0.0502 | fails earnings
- \U0001F534 **2** 2027-01-15 | new call 120 | credit 1.54 | extrinsic_per_day 0.0289 | fails earnings
- \U0001F534 **3** 2027-01-15 | new call 105 | credit 6.74 | extrinsic_per_day 0.1174 | fails abs_delta, earnings

**Why each one fails**
- 1, 2, 3 fail earnings: the listed earnings date 2026-11-17 has passed, so the next one is unknown and counts as inside the new call's life
- 3 fails abs_delta: absolute delta 0.54 outside your band 0.15 to 0.35

a ticket for one of these: ask the Aegis Desk Lead for a ROLL ticket with its expiration and strike

> - extrinsic_per_day: the bid's time value per calendar day, in percent of the share price
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - criteria: your structuring.toml (sha 5155427c)

""" + FOOTER


class GoldenTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        main = DeskCopy(cls)
        cls.wmt = S.run_roll_analysis(main.market(), "AG-20261020-01", CRITERIA)
        cls.nvda = S.run_roll_analysis(main.market(), "AG-20261022-02", CRITERIA)
        wmt, spy, tagged = DeskCopy(cls, 4, WMT_TAGS), DeskCopy(cls, 8), DeskCopy(cls, 8, SPY_TAG)
        cls.call = S.run_proposal(wmt.market(), "AG-20261020-01", "covered_call", CRITERIA)
        cls.put = S.run_proposal(spy.market(), "AG-20261028-03", "protective_put", CRITERIA)
        cls.collar = S.run_proposal(tagged.market(), "AG-20261028-03", "collar", CRITERIA)
        cls.replacement = S.run_replacement(main.market(), "AG-20261020-01", JAN15, D("115"), CRITERIA)
        cls.na = S.run_roll_analysis(main.market(), "AG-20261028-03", CRITERIA)
        cls.nothing = S.run_proposal(main.market(), "AG-20261028-03", "protective_put", CRITERIA)
        expiry_now = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)
        cls.expiry_day = S.run_roll_analysis(DeskCopy(cls).market("expiry_day", expiry_now), "AG-20261020-01", CRITERIA)

    def render(self, lines_of, answer, **kwargs) -> "T.Rendered":
        w = T.Writer()
        lines = lines_of(w, answer, **kwargs)
        rendered = T.finish(w, lines, T.data_as_of(answer), answer.tool)
        self.assertTrue(rendered.ok, rendered.lint.problems)
        return rendered

    def test_quiet_answers_stamp_now(self) -> None:
        holiday = datetime.datetime(2026, 11, 26, 17, 0, tzinfo=UTC)
        desk = DeskCopy(self)
        S.run_roll_analysis(desk.market(), "AG-20261020-01", CRITERIA)  # leaves cached quotes of 2026-11-03
        answer = S.run_roll_analysis(desk.market("holiday", holiday), "AG-20261020-01", CRITERIA)
        self.assertEqual((S.VERDICT_UNAVAILABLE, holiday), (answer.verdict, T.data_as_of(answer)))
        rendered = self.render(T.roll_analysis_lines, answer)
        self.assertEqual("UNAVAILABLE | AG-20261020-01 roll analysis | the market is closed today\n\n"
                         + alert_lint.structuring_footer_line(holiday), rendered.text)

    def test_none_qualifies_shows_the_held_call(self) -> None:
        rendered = self.render(T.roll_analysis_lines, self.nvda)
        lines = rendered.text.split("\n")
        at = lines.index("**no candidate passed every criterion: the desk names no best candidate**")
        self.assertEqual(("", "- the net credits close the held 2026-11-20 150 call | ask 3.30 | quote grade C, 18 min old"),
                         (lines[at - 1], lines[at + 1]))
        self.assertNotIn("BEST CANDIDATE", rendered.text)
        self.assertIn("- 1, 2, 3 fail ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls inside the new "
                      "call's life", lines)

    def test_a_disabled_section_is_named_by_words_the_lint_passes(self) -> None:
        # "roll: disabled ..." starts a segment with an imperative the structuring lint refuses; the section is named
        # as "the <section> section is disabled in your structuring.toml", the words the tools print.
        roll_off = dataclasses.replace(CRITERIA, roll=dataclasses.replace(CRITERIA.roll, enabled=False))
        answer = S.run_roll_analysis(DeskCopy(self).market(), "AG-20261020-01", roll_off)
        self.assertEqual((S.VERDICT_NOT_APPLICABLE, S.NA_DISABLED), (answer.verdict, answer.cause))
        rendered = self.render(T.roll_analysis_lines, answer)
        self.assertEqual("NOT_APPLICABLE | AG-20261020-01 roll analysis | the roll section is disabled in your "
                         "structuring.toml\n\n" + alert_lint.structuring_footer_line(NOW), rendered.text)
        collar_off = dataclasses.replace(CRITERIA, collar=dataclasses.replace(CRITERIA.collar, enabled=False))
        answer = S.run_proposal(DeskCopy(self, 8).market(), "AG-20261028-03", "collar", collar_off)
        self.assertEqual((S.VERDICT_NOT_APPLICABLE, S.NA_DISABLED), (answer.verdict, answer.cause))
        rendered = self.render(T.proposal_lines, answer, owner_level=2)
        self.assertEqual("NOT_APPLICABLE | AG-20261028-03 collar proposal | the collar section is disabled in your "
                         "structuring.toml\n\n" + alert_lint.structuring_footer_line(NOW), rendered.text)

    def test_a_refused_proposal_names_the_proposed_structure(self) -> None:
        rendered = self.render(T.proposal_lines, self.collar, owner_level=1)
        self.assertEqual("REFUSED AG-20261028-03 | SPY collar | opening 1 new long put needs level 2 by the desk's "
                         "convention; desk.toml records your self-attested level 1 | the desk shows nothing above your "
                         "level; your broker's own approval decides what you can enter\n\n" + FOOTER, rendered.text)

    def test_wmt_roll_analysis(self) -> None:
        rendered = self.render(T.roll_analysis_lines, self.wmt)
        self.assertEqual(WMT_ROLL, rendered.text)
        self.assertEqual(STAMP, T.data_as_of(self.wmt))
        relay = alert_lint.load_emitted(json.loads(state.json_dumps(rendered.document)))
        self.assertTrue(relay.check(rendered.text).ok)

    def test_golden_texts(self) -> None:
        cases = [(T.roll_analysis_lines, self.nvda, {}, NVDA_ROLL), (T.roll_analysis_lines, self.na, {}, SPY_ROLL_NA),
                 (T.proposal_lines, self.nothing, {"owner_level": 2}, PUT_NOTHING),
                 (T.proposal_lines, self.call, {"owner_level": 2}, WMT_CALL),
                 (T.proposal_lines, self.put, {"owner_level": 2}, SPY_PUT),
                 (T.proposal_lines, self.collar, {"owner_level": 2}, SPY_COLLAR),
                 (T.roll_analysis_lines, self.expiry_day, {}, EXPIRY_DAY)]
        for lines_of, answer, kwargs, expected in cases:
            with self.subTest(kind=answer.kind, verdict=answer.verdict):
                self.assertEqual(expected, self.render(lines_of, answer, **kwargs).text)

    def ticket(self, answer: "S.Answer") -> "T.Rendered":
        """A ROLL ticket composed from the builders, in the desk view ``tickets.ticket_lines`` prints (0.2.1)."""
        w = T.Writer()
        held, candidate = answer.held, answer.candidate
        leg = candidate.legs[0]
        until, full = S.valid_until(NOW, answer.target.session)
        as_of = T.data_as_of(answer)
        account = T.account_cell(w, answer.group_context.account, answer.target.market.cfg.owner.accounts,
                                 held.row_number)
        lines = [T.ticket_header(w, answer.trade_id, answer.group_context.underlying, answer.group_context.structure,
                                 "ROLL"),
                 T.strip_line([("account", account), ("computed", w.clock(NOW)),
                               ("valid until", T.valid_until_cell(w, until, full)), ("quotes", T.quotes_cell(w, as_of))]),
                 "",
                 T.LEGS_CAPTION,
                 "- " + T.leg_line(w, 1, 2, "close", held.side, held.contracts, held.root, held.expiration, held.strike,
                                   held.right, T.quote_segment(w, held.bid, held.ask, held.mid, held.grade, held.note)),
                 "- " + T.leg_line(w, 2, 2, "open", "short", candidate.contracts, leg.root, leg.expiration, leg.strike,
                                   leg.right, T.quote_segment(w, leg.bid, leg.ask, leg.mid, leg.grade)),
                 ""]
        lines.extend(T.replacement_lines(w, answer))
        lines.append("")
        lines.extend(T.approval_block(w, answer, candidate, 2))
        lines.extend(["", *T.notes_block([T.validity_line(w, until), T.quotes_line(w, as_of), T.MODEL_LINE,
                                          T.AFTER_FILL_LINE, T.LIMIT_LINE, T.ACT_LINE])])
        rendered = T.finish(w, lines, as_of, answer.tool, owner_texts=["Brokerage (margin)", "Roth IRA"])
        self.assertTrue(rendered.ok, rendered.lint.problems)
        return rendered

    def test_roll_tickets_compose_from_the_builders(self) -> None:
        wmt = self.ticket(self.replacement).text.split("\n")
        self.assertEqual("### TICKET AG-20261020-01 | WMT covered call: a roll of the short call (action ROLL)", wmt[0])
        self.assertIn(TICKET_STRIP, wmt)
        self.assertIn("- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | quote "
                      "grade B", wmt)
        at = wmt.index("**Checks for the new call**")
        self.assertEqual(["- " + GREEN + " **net per share** credit 0.05 (10.00 in all) | your criteria: credit only",
                          "- " + GREEN + " **strike** 115 | your criteria: at or above your cost 98.50 or the held "
                          "strike 105"], wmt[at + 1:at + 3])
        self.assertIn("- " + YELLOW + " **ex-dividend** 2026-11-24 (cum 2026-11-23), inside the new call's life | "
                      "accepted by #assign-ok", wmt)
        self.assertIn("- **max loss** 19,016.00 now, 19,006.00 after", wmt)
        self.assertIn("- " + approval.MULTI_LEG_NOTE, wmt)
        nvda = self.ticket(S.run_replacement(DeskCopy(self).market(), "AG-20261022-02", JAN15, D("160"), CRITERIA))
        lines = nvda.text.split("\n")
        self.assertIn("- " + RED + " **net per share** debit 0.05 (10.00 in all) | your criteria: credit only", lines)
        self.assertIn("- " + RED + " **ex-dividend** 2026-12-03 (cum 2026-12-02), inside the new call's life | excluded "
                      "by your structuring.toml", lines)
        self.assertEqual(TICKET_STRIP, lines[1])

    def test_every_golden_text_lints_and_carries_no_occ_symbol(self) -> None:
        cases = [(T.roll_analysis_lines, self.wmt, {}), (T.roll_analysis_lines, self.nvda, {}),
                 (T.proposal_lines, self.call, {"owner_level": 2}), (T.proposal_lines, self.put, {"owner_level": 2}),
                 (T.proposal_lines, self.collar, {"owner_level": 2})]
        for lines_of, answer, kwargs in cases:
            rendered = self.render(lines_of, answer, **kwargs)
            with self.subTest(kind=answer.kind):
                self.assertIsNone(T.OCC_RE.search(rendered.text))
                self.assertNotIn("FAILS_RULE", rendered.text)
                self.assertNotIn("Brokerage", rendered.text)
                self.assertNotIn("Roth", rendered.text)
                for line in rendered.text.split("\n"):
                    for cell in line.lstrip("#*>- ").split("|"):
                        first = cell.strip(" *").split(" ")[0].lower()
                        self.assertNotIn(first, ("roll", "close", "exit", "collar", "hedge", "cover", "short"), line)
                self.assertNotIn("\n\n\n", rendered.text)
                blocks = rendered.text.split("\n\n")
                self.assertTrue(blocks[0].startswith(("### STRUCTURING ", "### PROPOSAL ")), blocks[0])
                for block in blocks[1:-1]:
                    # every block renders on its own in any markdown viewer: a table (a header, its rule, its rows), a
                    # quote, or one plain line with its items; never two plain lines a viewer would run together
                    rows = block.split("\n")
                    if rows[0].startswith("|"):
                        self.assertTrue(all(row.startswith("|") and row.endswith("|") for row in rows), block)
                        self.assertRegex(rows[1], r"^\|(?:---|--:|:-:)(?:\|(?:---|--:|:-:))*\|$")
                        self.assertEqual({rows[0].count("|")}, {row.count("|") for row in rows}, block)
                    elif rows[0].startswith(">"):
                        self.assertTrue(all(row.startswith(">") for row in rows), block)
                    else:
                        self.assertTrue(all(row.startswith("- ") for row in rows[1:]), block)
                self.assertEqual(alert_lint.structuring_footer_line(rendered.as_of), blocks[-1])


# ------------------------------------------------------------------------------------------------
# The journal record and the report writer
# ------------------------------------------------------------------------------------------------


class FixerTextTest(unittest.TestCase):
    """The PR 3b review: the reasons line below the stock cost (L4-1), the owner-text screen (L3-2), the counts of
    candidates never priced (L1-4) and the new reason words."""

    def test_a_replacement_below_the_stock_cost_says_so(self) -> None:
        desk = DeskCopy(self, replace=("AG-20261020-01,2026-10-20,,98.50,", "AG-20261020-01,2026-10-20,,120.00,"))
        answer = S.run_roll_analysis(desk.market(), "AG-20261020-01", CRITERIA)
        self.assertEqual("WMT270115C00115000", answer.best.id)
        w = T.Writer()
        rendered = T.finish(w, T.roll_analysis_lines(w, answer), T.data_as_of(answer), answer.tool)
        self.assertTrue(rendered.ok, rendered.lint.problems)
        self.assertIn("- " + GREEN + " **strike** 115 | your criteria: at or above your cost 120.00 or the held strike 105",
                      rendered.text.split("\n"))
        # a strike-floor failure is a red row with the same words
        best = answer.best
        failing = dataclasses.replace(best, reasons=(structuring.Reason(
            structuring.KEY_STRIKE_FLOOR, structuring.CODE_ROLL_DOWN_BELOW_COST, D("115"), D("120.00")),),
            status=structuring.STATUS_FAILS_CRITERION)
        rows = T.check_rows(T.Writer(), failing, answer.section, D("25"), D("120"), D("117.5"))
        self.assertEqual(["strike", "115", "at or above your cost 120.00 or the held strike 117.5", RED], rows[1])
        self.assertNotIn(RED, [row[3] for row in rows if row[0] != "strike"])

    def test_an_odd_lot_proposal_says_which_shares_the_contracts_cover(self) -> None:
        # 0.2.1: 250.5 shares carry 2 contracts; the text says the other 50.5 shares are not part of the covered call
        # (they are why the group's max profit can stay unbounded)
        desk = DeskCopy(self, 4, WMT_TAGS)
        path = os.path.join(desk.root, "positions.csv")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("equity,WMT,,200,", "equity,WMT,,250.5,"))
        answer = S.run_proposal(desk.market(), "AG-20261020-01", "covered_call", CRITERIA)
        self.assertEqual((S.VERDICT_BEST, 2), (answer.verdict, answer.best.contracts))
        w = T.Writer()
        rendered = T.finish(w, T.proposal_lines(w, answer, 2), T.data_as_of(answer), answer.tool)
        self.assertTrue(rendered.ok, rendered.lint.problems)
        lines = rendered.text.split("\n")
        at = lines.index("- 2 contracts cover 200 of your 250.5 shares; the other 50.5 are not part of the covered call")
        self.assertEqual((
            "- **group delta**",
            "",
            "**Approval-level check:**",
        ),
                         (lines[at - 1][:17], lines[at + 1], lines[at + 2][:25]))
        self.assertIn("### PROPOSAL AG-20261020-01 | a covered call on the 250.5 WMT shares you hold", lines)
        # whole lots print no such line
        self.assertNotIn("not part of the", WMT_CALL + SPY_PUT + SPY_COLLAR)

    def test_the_owner_text_screen_reads_the_desk_dynamic_text_only(self) -> None:
        accounts = ["options", "brokerage", "positions", "desk", "criteria", "sandbox", "Brokerage (margin)", "Roth IRA"]
        w = T.Writer()
        lines = [T.roll_header(w, "AG-20261020-01", "WMT", "covered_call", datetime.date(2026, 11, 20), D("105")), "",
                 T.context_line(w, T.account_cell(w, "Brokerage (margin)", ACCOUNTS, 4), "WMT", D("104.23"),
                                D("98.50"), STAMP), "", T.account_words(w, "My Joint", ACCOUNTS, 3), "",
                 *T.notes_block([T.MODEL_LINE, T.ACT_LINE, T.LIMIT_LINE, T.AFTER_FILL_LINE, T.criteria_line(w, SHA)])]
        rendered = T.finish(w, lines, STAMP, "roll_analysis", owner_texts=accounts)
        self.assertTrue(rendered.ok, (rendered.codes, rendered.lint.problems))
        for leak, texts in (("held in Brokerage (margin)", ["Brokerage (margin)"]),
                            ("the Joint acct ZZACCT holds it", ["Joint acct ZZACCT"]),
                            ("held in Optionsville", ["Optionsville"])):
            w = T.Writer()
            rendered = T.finish(w, [leak], STAMP, "roll_analysis", owner_texts=texts)
            with self.subTest(leak=leak):
                self.assertFalse(rendered.ok)
                self.assertIn(T.CODE_OWNER_TEXT, rendered.codes)
        # a token inside a longer word is not the owner's text
        w = T.Writer()
        self.assertTrue(T.finish(w, ["the Rothschild row"], STAMP, "roll_analysis", owner_texts=["Roth"]).ok)

    def test_the_lexicon_holds_every_word_the_golden_texts_print(self) -> None:
        # an account named after any word the desk itself prints must not withhold the text: every lower-case word
        # of four letters or more in the golden texts is in the desk's lexicon
        missing = set()
        for text in (WMT_ROLL, NVDA_ROLL, SPY_ROLL_NA, PUT_NOTHING, WMT_CALL, SPY_PUT, SPY_COLLAR, EXPIRY_DAY):
            for word in __import__("re").findall(r"(?<![A-Za-z_])[a-z]{4,}(?![A-Za-z_])", text):
                if word not in T.DESK_LEXICON:
                    missing.add(word)
        self.assertEqual(set(), missing)

    def test_counts_name_the_candidates_never_priced(self) -> None:
        held = structuring.HeldLeg(4, "WMT261120C00105000", "WMT", "CALL", "short", 2, D("105"), datetime.date(2026, 11, 20),
                                   datetime.date(2026, 11, 20), D("3.35"), D("3.50"), D("3.425"), D("4.38"), "B", "fresh",
                                   18, STAMP, None, None, D("3.42"), D("-684.00"), D("3.50"), "ask", None, D("-16.00"))
        self.assertEqual(D("105"), held.strike)
        w = T.Writer()
        self.assertEqual("**Replacement candidates by rank** 0 of 20 passed your criteria; 18 failed (abs_delta 14, "
                         "ex_dividend 18); 2 not priced",
                         T.counts_caption(w, 20, 0, 18, 2, {"ex_dividend": 18, "abs_delta": 14}, replacement=True))
        self.assertEqual(["14", "18", "0", "20", "18", "2"], w.emitted)
        self.assertEqual("**Candidates by rank** 4 of 11 passed your criteria; 6 failed (max_net_debit 6); 1 not priced",
                         T.counts_caption(T.Writer(), 11, 4, 6, 1, {"max_net_debit": 6}))
        self.assertEqual("**Candidates by rank** 1 of 1 passed your criteria; 0 failed",
                         T.counts_caption(T.Writer(), 1, 1, 0, 0, {}))

    def test_the_new_reason_words_lint(self) -> None:
        conflict = structuring.Reason(structuring.KEY_UNPRICED, structuring.CODE_QUOTE_CONFLICT, 2, None)
        self.assertEqual("the chain lists the contract more than once with different quotes",
                         T.reason_words(T.Writer(), conflict))
        self.assertEqual(structuring.UNPRICED_WORDS, T._UNPRICED_WORDS)
        reasons = ["no listed standard monthly expiration lies inside your roll window of 80 to 100 calendar days",
                   "200 of the 300 WMT shares are free: 1 short call held in another group of this account relies on "
                   "the rest; the desk proposes no covered call on part of a group's shares",
                   "no candidate in your window could be priced: the underlying's quote is stamped too far from the "
                   "option's", "no candidate in your window could be priced (the reasons differ; the JSON lists each)",
                   "no candidate in your window could be priced: the chains list none"]
        for reason in reasons:
            for line_of in (T.not_applicable_line, T.unavailable_line):
                w = T.Writer()
                result = lint(closed(w, [line_of(w, "AG-20261020-01", "roll analysis", reason)]), w)
                with self.subTest(reason=reason):
                    self.assertTrue(result.ok, result.problems)
        w = T.Writer()
        candidate = structuring.Candidate(
            id="WMT270115C00115000", kind=structuring.KIND_COVERED_CALL, expiration=JAN15,
            legs=(structuring.PricedLeg("WMT270115C00115000", "WMT", "CALL", D("115"), JAN15, unpriced=conflict),),
            contracts=2, score=None, status="UNPRICED", reasons=(conflict,), rank=3)
        self.assertEqual(("", "not priced"), T.candidate_status(candidate))
        rows = T.candidate_items(w, [candidate], "extrinsic_per_day")
        self.assertEqual(["- **3** 2027-01-15 | strike 115 | not priced"], rows)
        reasons = T.failure_lines(w, [candidate])
        self.assertEqual(["- 3 not priced: the chain lists the contract more than once with different quotes"], reasons)
        self.assertTrue(lint(closed(w, rows + [""] + reasons), w).ok)


def read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="aegis-structuring-records-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.paths = desk_paths.for_root(self.tmp)

    def test_journal_record(self) -> None:
        record = T.journal_record(now=NOW, tool="roll_analysis", trade_id="AG-20261020-01", verdict="BEST",
                                  text="a text", criteria_sha256=SHA, desk_toml_sha256="b" * 64,
                                  positions_csv_sha256="c" * 64, best_candidate="WMT270115C00115000",
                                  candidates_passed=1, candidates_failed=19)
        self.assertEqual(T.JOURNAL_KEYS, tuple(record))
        self.assertEqual(hashlib.sha256(b"a text").hexdigest(), record["text_sha256"])
        self.assertEqual((None, None, None), (record["action"], record["structure"], record["refused_code"]))
        T.append_journal_record(self.paths, record)
        T.append_journal_record(self.paths, dict(record, verdict="NONE_QUALIFIES"))
        with open(self.paths.structuring_journal, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(["BEST", "NONE_QUALIFIES"], [json.loads(line)["verdict"] for line in lines])
        self.assertEqual("2026-11-03T19:50:00+00:00", json.loads(lines[0])["utc"])

    def test_stems(self) -> None:
        self.assertEqual("20261103T195000Z", T.stamp(NOW))
        self.assertEqual("TK-AG-20261020-01-ROLL-20261103T195000Z", T.ticket_stem("AG-20261020-01", "ROLL", NOW))
        self.assertEqual("AG-20261020-01-20261103T195000Z", T.roll_stem("AG-20261020-01", NOW))
        self.assertEqual("AG-20261028-03-collar-20261103T195000Z", T.proposal_stem("AG-20261028-03", "collar", NOW))
        with self.assertRaises(ValueError):
            T.ticket_stem("AG-20261020-01", "ROLL_TO", NOW)

    def test_report_writer(self) -> None:
        stem = T.roll_stem("AG-20261020-01", NOW)
        files = T.write_report(self.paths, self.paths.rolls_dir, stem, '{"a": 1}\n', "# text\n")
        self.assertTrue(files.written)
        self.assertEqual(os.path.join(self.paths.rolls_dir, stem + ".json"), files.json_path)
        self.assertEqual((b'{"a": 1}\n', b"# text\n"), (read(files.json_path), read(files.md_path)))
        again = T.write_report(self.paths, self.paths.rolls_dir, stem, '{"a": 1}\n', "# text\n")
        self.assertFalse(again.written)
        with self.assertRaises(T.ReportExists) as caught:
            T.write_report(self.paths, self.paths.rolls_dir, stem, '{"a": 2}\n', "# other\n")
        self.assertEqual("ticket_exists", caught.exception.code)
        self.assertEqual((b'{"a": 1}\n', b"# text\n"), (read(files.json_path), read(files.md_path)))
        for directory in (self.paths.tickets_dir, self.paths.proposals_dir):
            self.assertTrue(T.write_report(self.paths, directory, "x", "{}\n", "x\n").written)
        with self.assertRaises(ValueError):
            T.write_report(self.paths, self.paths.journal_dir, "x", "{}\n", "x\n")

    def test_report_markdown(self) -> None:
        self.assertEqual("# AG-20261020-01 roll analysis\n\n```text\nline one\nline two\n```\n",
                         T.report_markdown("AG-20261020-01 roll analysis", "line one\nline two"))


class VerifierTest(unittest.TestCase):
    """The PR 3b verifier: review L3-2 for an account named with a phrase of the desk's own words."""

    def test_an_account_named_with_a_desk_phrase_does_not_withhold_the_text(self) -> None:
        for name in ("covered call", "Alpaca", "Alpaca paper", "roll analysis", "the shares"):
            w = T.Writer()
            lines = [T.roll_header(w, "AG-20261020-01", "WMT", "covered_call", datetime.date(2026, 11, 20), D("105")),
                     "", "> - " + T.criteria_line(w, SHA), "", "the shares of the group are free"]
            rendered = T.finish(w, lines, STAMP, "roll_analysis", owner_texts=[name, "Brokerage (margin)"])
            with self.subTest(name=name):
                self.assertTrue(rendered.ok, (rendered.codes, rendered.lint.problems))
        # a phrase with one word of its own is still the owner's text
        for leak, texts in (("held in covered call ZZACCT", ["covered call ZZACCT"]), ("held in Roth IRA", ["Roth IRA"]),
                            ("held in my covered calls", ["my covered calls"])):
            w = T.Writer()
            rendered = T.finish(w, [leak], STAMP, "roll_analysis", owner_texts=texts)
            with self.subTest(leak=leak):
                self.assertFalse(rendered.ok)
                self.assertIn(T.CODE_OWNER_TEXT, rendered.codes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
