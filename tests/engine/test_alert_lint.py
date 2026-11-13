"""The alert lint (``aegis_desk.alert_lint``): every numeric token of a message, attached forms included, must be a
string a formatter emitted; the identifier whitelist (addendum decision 6) is exact; the fixed footer must be the
last line; forbidden language and imperative sentence starts are rejected in any case and across line breaks;
characters outside the message alphabet are rejected. The forbidden phrases are never written in this file: the
tests take them from the lint's own table, so the test source stays free of order-shaped text."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import glob
import json
import os
import re
import unittest
from typing import Dict, List, Optional, Sequence, Tuple
from unittest import mock

from aegis_desk import alert_lint, alerts

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ALERT_FIXTURES = os.path.join(ROOT, "tests", "fixtures", "alerts")
UTC = datetime.timezone.utc
AS_OF = datetime.datetime(2026, 11, 20, 19, 31, 12, tzinfo=UTC)
FOOTER = alert_lint.footer_line(AS_OF)
HEAD = "EXIT SIGNAL AG-20261020-01 | WMT covered call"


def message(*lines: str, footer: Optional[str] = FOOTER) -> str:
    body = list(lines)
    if footer is not None:
        body.append(footer)
    return "\n".join(body)


def codes(result: alert_lint.LintResult) -> List[str]:
    return [finding.code for finding in result.findings]


class FooterTest(unittest.TestCase):
    def test_footer_line_is_the_fixed_text(self) -> None:
        self.assertEqual("deterministic rules you adopted · posted by an AI assistant · not personalized "
                         "investment advice · data delayed 15 min, as of 19:31 UTC", FOOTER)

    def test_footer_line_converts_to_utc_and_refuses_naive(self) -> None:
        eastern = datetime.timezone(datetime.timedelta(hours=-5))
        self.assertTrue(alert_lint.footer_line(AS_OF.astimezone(eastern)).endswith("as of 19:31 UTC"))
        with self.assertRaises(ValueError):
            alert_lint.footer_line(AS_OF.replace(tzinfo=None))
        self.assertTrue(alert_lint.footer_line(AS_OF, delay_min=20).endswith("data delayed 20 min, as of 19:31 UTC"))

    def test_a_clean_message_passes(self) -> None:
        result = alert_lint.check(message(HEAD + " | credit captured 95.32 %"), ["95.32"])
        self.assertTrue(result.ok, result.problems)
        self.assertEqual([], result.problems)
        self.assertEqual([], result.findings)

    def test_footer_missing_or_not_last(self) -> None:
        self.assertEqual(["footer_missing"], codes(alert_lint.check(message(HEAD, footer=None), [])))
        self.assertEqual(["footer_missing"], codes(alert_lint.check("\n".join([FOOTER, HEAD]), [])))
        variants = [FOOTER.replace("15 min", "15 minutes"), FOOTER.replace(" UTC", ""),
                    FOOTER.replace("19:31", "24:10"), FOOTER.replace("·", "-"), FOOTER.upper(), " " + FOOTER,
                    FOOTER.replace("not personalized", "personalized")]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertIn("footer_missing", codes(alert_lint.check(message(HEAD, footer=variant), [])))

    def test_trailing_newlines_after_the_footer_are_allowed(self) -> None:
        self.assertTrue(alert_lint.check(message(HEAD) + "\n\n", []).ok)

    def test_footer_delay_other_than_15_must_be_emitted(self) -> None:
        footer = alert_lint.footer_line(AS_OF, delay_min=20)
        self.assertEqual(["number_not_emitted"], codes(alert_lint.check(message(HEAD, footer=footer), [])))
        self.assertTrue(alert_lint.check(message(HEAD, footer=footer), ["20"]).ok)

    def test_empty_message(self) -> None:
        self.assertEqual(["empty_message"], codes(alert_lint.check("", [])))
        self.assertEqual(["empty_message"], codes(alert_lint.check(" \n\n ", [])))


class SnapshotFooterTest(unittest.TestCase):
    """The market-snapshot footer (``footer="snapshot"``): an accurate disclosure for a public quote shown before
    any rule exists. It closes only rule-free text, so the rules footer stays mandatory for anything a rule says."""

    SNAPSHOT = alert_lint.snapshot_footer_line(AS_OF)
    BODY = "SPY 648.12 mid (bid 648.10, ask 648.14), as of 19:31 UTC"
    EMITTED = ["648.12", "648.10", "648.14"]

    def lint(self, text: str, **kwargs: object) -> alert_lint.LintResult:
        return alert_lint.check(text, self.EMITTED, footer=alert_lint.FOOTER_SNAPSHOT, **kwargs)

    def test_the_exact_line(self) -> None:
        self.assertEqual("public market data · posted by an AI assistant · not a signal and not personalized "
                         "investment advice · data delayed 15 min, as of 19:31 UTC", self.SNAPSHOT)
        self.assertEqual((alert_lint.FOOTER_RULES, alert_lint.FOOTER_SNAPSHOT, alert_lint.FOOTER_STRUCTURING),
                         alert_lint.FOOTER_KINDS)

    def test_a_snapshot_passes_only_in_snapshot_mode(self) -> None:
        text = message(self.BODY, footer=self.SNAPSHOT)
        self.assertTrue(self.lint(text, as_of=AS_OF).ok, self.lint(text).problems)
        default = alert_lint.check(text, self.EMITTED)
        self.assertFalse(default.ok)
        self.assertIn("footer_missing", codes(default), "the default stays the rules footer: a snapshot footer never "
                      "closes an alert (its middle dots and delay are not whitelisted there either)")
        rules_closed = self.lint(message(self.BODY))
        self.assertFalse(rules_closed.ok)
        self.assertIn("footer_missing", codes(rules_closed), "snapshot mode does not accept the rules footer either")

    def test_rule_content_cannot_close_with_the_snapshot_footer(self) -> None:
        for body in ("your rule TP80 (rules 1.0.0, sha cc09859c) fired", "EXIT SIGNAL AG-20261020-01 | WMT covered call",
                     "WATCH AG-20261020-01", "HOLD portfolio", "UNAVAILABLE: book stale", "rules 1.0.0"):
            with self.subTest(body=body):
                result = self.lint(message(body, footer=self.SNAPSHOT))
                self.assertIn("footer_missing", codes(result))
                self.assertTrue(any("rule content" in problem for problem in result.problems), result.problems)
        named = self.lint(message(self.BODY, footer=self.SNAPSHOT), rule_ids=["TP80"])
        self.assertIn("footer_missing", codes(named), "a caller naming rule ids is not a snapshot")
        self.assertTrue(self.lint(message(self.BODY, footer=self.SNAPSHOT), rule_ids=[]).ok)

    def test_the_footer_rules_still_hold(self) -> None:
        pinned = self.lint(message(self.BODY, footer=self.SNAPSHOT), as_of=AS_OF + datetime.timedelta(minutes=5))
        self.assertEqual(["footer_missing"], codes(pinned), "as_of pins the snapshot footer's clock too")
        late = alert_lint.snapshot_footer_line(AS_OF, 20)
        self.assertEqual(["number_not_emitted"], codes(self.lint(message(self.BODY, footer=late))),
                         "a delay other than 15 minutes must be emitted")
        dotted = message(self.BODY + " · extra", footer=self.SNAPSHOT)
        self.assertEqual(["non_ascii"], codes(self.lint(dotted)), "the middle dot belongs to the footer line only")
        with self.assertRaises(ValueError):
            alert_lint.check(message(self.BODY, footer=self.SNAPSHOT), self.EMITTED, footer="market")

    def test_the_emitted_document_names_its_footer(self) -> None:
        text = message(self.BODY, footer=self.SNAPSHOT)
        document = alert_lint.load_emitted({"emitted_numbers": self.EMITTED, "footer": "snapshot"})
        self.assertEqual(alert_lint.FOOTER_SNAPSHOT, document.footer)
        self.assertTrue(document.check(text).ok, document.check(text).problems)
        self.assertEqual(alert_lint.FOOTER_RULES, alert_lint.load_emitted({"emitted_numbers": self.EMITTED}).footer)
        self.assertFalse(alert_lint.load_emitted({"emitted_numbers": self.EMITTED}).check(text).ok)
        self.assertNotIn("footer", alert_lint.EMITTED_DOCUMENT_KEYS, "an optional key: every rules document is unchanged")
        with self.assertRaises(ValueError):
            alert_lint.load_emitted({"emitted_numbers": self.EMITTED, "footer": "market"})


# --------------------------------------------------------------------------------------------
# The structuring footer (PR 3b, designer C's decision D5): the Structuring Desk's tickets, roll analyses and
# proposals. Its table's advice and claim phrases are spelled in the cases below (none is order-shaped); the
# order-shaped words among the cases are assembled from pieces, as everywhere in this file.
# --------------------------------------------------------------------------------------------

STRUCTURING_LINE = ("computed by the desk's tools from your criteria · posted by an AI assistant · a personalized "
                    "suggestion for positions you hold, not an order · you decide and act in your brokerage account "
                    "· data delayed 15 min, as of 19:31 UTC")
LEG_CLOSE = "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425"
LEG_OPEN = "leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60"
STRUCTURING_IMPERATIVES = ("open", "write", "take", "go", "consider", "choose", "pick", "use", "let", "keep", "hold", "set",
                           "type", "act", "do", "try", "hedge", "protect", "cover", "collar", "unwind", "monetize",
                           "replace", "lock", "add", "pur" + "chase", "liquidate", "exercise", "short", "trade", "acquire",
                           "dump", "unload")
# (code, texts the entry refuses, texts it leaves alone): the regex of each entry re-derived against both lists.
STRUCTURING_PHRASE_CASES: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("SL01", ("you might want to", "one may wish to", "you would like to", "you could want to"),
     ("you might see", "one would expect", "you may")),
    ("SL02", ("I'd", "we would", "I would", "we'd"), ("it would", "we will", "Id")),
    ("SL03", ("I suggest", "we advise", "I prefer", "we favour", "we favor", "I propose", "we like", "we think",
              "I believe"),
     ("it proposes", "the suggestion", "I see")),
    ("SL04", ("go with", "goes with", "going with"), ("go without", "gone with")),
    ("SL05", ("the one to pick", "the one to choose", "the one to take", "the one to use", "the one to open"),
     ("the one to the left", "one to pick")),
    ("SL06", ("best move", "better choice", "smart bet", "smarter trade", "wise play", "right idea", "good deal"),
     ("best score", "go" + "od till", "a better level")),
    ("SL07", ("you must", "you need to", "you have to", "you ought to", "you will need to", "you really must"),
     ("you need a", "the steps need at once", "you have a ticket")),
    ("SL08", ("probability", "probabilities", "chance", "chances", "likely", "likelihood", "odds", "pop", "unlikely",
              "probably", "probable", "improbable", "likelier", "probability_of_profit", "likely_outcome"),
     ("popular", "chancellor", "oddsmaker", "unlikeable")),
    ("SL09", ("guarantee", "guarantees", "guaranteed", "risk-free", "risk free", "cannot lose", "can't lose",
              "sure thing", "no risk", "zero risk", "without risk", "riskless", "guaranteed_income", "risk_free"),
     ("risky", "unsure", "the risk of assignment")),
    ("SL10", ("act now", "move fast", "act quickly", "move today", "don't miss", "dont miss", "urgent", "urgently",
              "hurry", "hurrying", "immediately"),
     ("act in your brokerage account", "the move of the stock", "missing")),
    ("SL11", ("suit", "suits", "suited", "suitable", "suitability", "right for you", "best fit for you",
              "ideal for you", "appropriate for you", "appropriate", "fits you", "fit you"),
     ("suite", "pursuit", "right for the call", "it fits the window")),
    ("SL12", tuple("pl" + "ac" + ending for ending in ("e", "es", "ed", "ing", "e_it")),
     ("re" + "pl" + "ace", "re" + "pl" + "acement", "pl" + "acement")),
    ("SL13", ("safe", "safer", "safest", "safely", "safety", "safe_bet"), ("unsafe", "safeguard")),
    ("SL14", ("at 1.90", "for 3.50", "at $1.90", "at 1,640.00", "for +0.05", "at -0.70", "at .50", "at~3.50", "at ~ 3.50"),
     ("at 19:31 UTC", "for 2 contracts", "at or above 0.50", "for 2,000 shares", "at 15 min", "for the 105 call")),
    ("SL15", ("return", "returns", "yield", "yields", "yielding", "income", "gain", "gains", "daily_yield"),
     ("again", "bargain", "the outcome")),
    ("SL16", ("not investment advice", "not personalized investment advice", "not advice", "non-advice", "nonadvice"),
     ("not an order", "a personalized suggestion", "advice")),
    ("SL17", ("my pick", "our choice", "the top pick", "a clear winner", "my favourite", "it is best to",
              "it would be wise to", "you had better", "you'd better", "the desk suggests", "the desk prefers",
              "the desk favors", "the tool advises", "a no-brainer", "no brainer", "the desk's suggestion"),
     ("the desk proposes no covered call", "the desk proposes overlays on shares you hold", "a picked strike",
      "it is best known")),
    ("SL18", ("sure", "surely", "a sure gain"), ("unsure", "insured", "measure")),
    ("SL19", ("L" + "MT", "m" + "kt", "g" + "td", "i" + "oc", "f" + "ok", "a" + "on", "s" + "to", "b" + "tc", "D" + "AY",
              "good for the day", "rou" + "te", "rou" + "ting"),
     ("the day", "a day", "today", "routine", "stock", "lock", "atone")),
    ("SL20", ("lim" + "it of about 3.50", "lim" + "it at or below 3.50", "lim" + "it no higher than 3.50",
              "pay at most 3.50", "the lim" + "it (3.50)", "price 3.50"),
     ("stressed price 518.496", "the share price): 0.0467", "pay the strike, cash -62000.00",
      "the conservative prices (mid +35.00)", "you supply the lim" + "it; the desk prints none")),
    ("SL21", ("pur" + "chase", "pur" + "chases", "pur" + "chased", "pur" + "chasing"), ("purse", "chase")),
    ("SL22", ("attractive", "cheap", "cheaper", "a bargain", "undervalued", "overvalued", "with confidence", "confident"),
     ("attraction", "the cheapside", "a confidential note")),
)


def structuring_line(as_of: datetime.datetime = AS_OF, delay_min: int = 15) -> str:
    return alert_lint.structuring_footer_line(as_of, delay_min)


def emitted_of(text: str) -> List[str]:
    """Every number a formatter would have emitted for ``text`` (rule ids read as none, as structuring mode does)."""
    return [token.text for token in alert_lint.unwhitelisted_numbers(text, [])]


def structuring_check(*lines: str, footer: Optional[str] = None, emitted: Optional[Sequence[str]] = None,
                      **kwargs: object) -> alert_lint.LintResult:
    text = "\n".join(list(lines) + [structuring_line() if footer is None else footer])
    numbers = emitted_of("\n".join(lines)) if emitted is None else list(emitted)
    return alert_lint.check(text, numbers, footer=alert_lint.FOOTER_STRUCTURING, **kwargs)


class StructuringFooterTest(unittest.TestCase):
    """``footer="structuring"``: the exact line, the cross-refusals (it never closes rule content; the rules and snapshot
    footers never close a leg or a candidate), ``rule_ids=None`` read as ``[]``, the as-of pin, the SL table, the
    imperative starts IS10 to IS34 and the fixed leg grammar (designer C's decision D5, items 1 to 7)."""

    def test_the_exact_line(self) -> None:
        line = structuring_line()
        self.assertEqual(STRUCTURING_LINE, line)
        self.assertEqual(226, len(line))
        self.assertEqual("structuring", alert_lint.FOOTER_STRUCTURING)
        self.assertEqual(("rules", "snapshot", "structuring"), alert_lint.FOOTER_KINDS)
        self.assertEqual(line[:-len("15 min, as of 19:31 UTC")], alert_lint.STRUCTURING_FOOTER_PREFIX)
        match = alert_lint.STRUCTURING_FOOTER_RE.match(line)
        self.assertEqual(("15", "19:31"), (match.group("delay"), match.group("clock")))
        self.assertIsNone(alert_lint.STRUCTURING_FOOTER_RE.match(FOOTER))
        self.assertIsNone(alert_lint.FOOTER_RE.match(line))
        self.assertTrue(structuring_line(delay_min=20).endswith("data delayed 20 min, as of 19:31 UTC"))
        eastern = datetime.timezone(datetime.timedelta(hours=-5))
        self.assertEqual(line, structuring_line(AS_OF.astimezone(eastern)))
        with self.assertRaises(ValueError):
            structuring_line(AS_OF.replace(tzinfo=None))

    def test_the_footer_dicts(self) -> None:
        kinds = set(alert_lint.FOOTER_KINDS)
        self.assertEqual((kinds, kinds, kinds), (set(alert_lint._FOOTER_RES), set(alert_lint._FOOTER_PREFIXES),
                                                 set(alert_lint._FOOTER_NAMES)))
        self.assertEqual({"rules": "fixed footer", "snapshot": "market-snapshot footer",
                          "structuring": "structuring footer"}, alert_lint._FOOTER_NAMES)
        self.assertEqual({"rules": alert_lint.FOOTER_PREFIX, "snapshot": alert_lint.SNAPSHOT_FOOTER_PREFIX,
                          "structuring": alert_lint.STRUCTURING_FOOTER_PREFIX}, alert_lint._FOOTER_PREFIXES)
        self.assertIs(alert_lint.STRUCTURING_FOOTER_RE, alert_lint._FOOTER_RES["structuring"])
        missing = structuring_check(LEG_CLOSE, footer="no footer on this line")
        self.assertEqual(["footer_missing"], codes(missing), missing.problems)
        self.assertEqual("the last line is not the structuring footer (%sN min, as of HH:MM UTC)"
                         % alert_lint.STRUCTURING_FOOTER_PREFIX, missing.problems[0])

    def test_a_structuring_text_passes_only_in_its_own_mode(self) -> None:
        text = "\n".join([LEG_CLOSE, LEG_OPEN, structuring_line()])
        numbers = emitted_of("\n".join([LEG_CLOSE, LEG_OPEN]))
        result = alert_lint.check(text, numbers, footer=alert_lint.FOOTER_STRUCTURING, as_of=AS_OF)
        self.assertTrue(result.ok, result.problems)
        for kind in (alert_lint.FOOTER_RULES, alert_lint.FOOTER_SNAPSHOT):
            with self.subTest(kind=kind):
                self.assertIn("footer_missing", codes(alert_lint.check(text, numbers, footer=kind)))
        self.assertIn("footer_missing", codes(alert_lint.check(text, numbers)), "the default stays the rules footer")

    def test_rule_content_cannot_close_with_the_structuring_footer(self) -> None:
        for body in ("your rule DTE21 fired", "Your Rules changed", "EXIT SIGNAL AG-20261020-01", "an EXIT_SIGNAL",
                     "WATCH AG-20261020-01", "HOLD portfolio", "rules 1.0.0", "the RULES 1.1.0 file"):
            with self.subTest(body=body):
                result = structuring_check("TICKET AG-20261020-01 | WMT covered call", body)
                self.assertIn("footer_missing", codes(result), result.problems)
                self.assertIn("the structuring footer cannot close rule content (a rule, a verdict or a rulebook "
                              "version, line 2): an alert keeps the rules footer", result.problems)
        named = structuring_check(LEG_CLOSE, rule_ids=["TP80"])
        self.assertEqual(["footer_missing"], codes(named))
        self.assertEqual(["the structuring footer cannot close rule content (a rule, a verdict or a rulebook version: "
                          "rule ids were given): an alert keeps the rules footer"], named.problems)
        for body in ("UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your criteria",
                     "after leg 1 you hold 200 WMT shares", "the held call", "a watch list", "rules of thumb"):
            with self.subTest(body=body):
                self.assertTrue(structuring_check(body).ok, structuring_check(body).problems)
        self.assertTrue(structuring_check(LEG_CLOSE, rule_ids=[]).ok)

    def test_rule_ids_none_is_read_as_empty(self) -> None:
        body = "an adjusted root WMT1 after a corporate action"
        result = structuring_check(body, emitted=[])
        self.assertEqual(["number_not_emitted"], codes(result), "no capitals-and-digits word is a rule id here")
        self.assertEqual(codes(result), codes(structuring_check(body, emitted=[], rule_ids=[])))
        self.assertTrue(structuring_check(body, emitted=["1"]).ok)
        self.assertTrue(alert_lint.check(message(body), []).ok, "the rules mode keeps its permissive rule-id whitelist")

    def test_the_rules_and_snapshot_footers_cannot_close_a_structuring_text(self) -> None:
        bodies = ((LEG_CLOSE, 1), ("  leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call", 1),
                  ("LEG 1 OF 2: closes your short 2 x WMT 2026-11-20 105 call", 1),
                  ("an opening line\nleg 2 of 2 : opens a new short 2 x WMT 2027-01-15 115 call", 2),
                  ("BEST CANDIDATE 1 of 20", 1), ("the best candidate", 1), ("the best-candidate line", 1),
                  ("best_candidates", 1), ("x\ncandidate 2: closes the 2026-11-20 105 call", 2),
                  ("candidates 3 of 7", 1), ("candidate 3 of 7 shown", 1), ("Candidate 2 :", 1),
                  ("the call is leg 1 of 2: in a roll", 1), ("candidate-2: x", 1))
        numbers = ["1", "2", "105", "115", "3.35", "3.50", "3.425", "20", "3", "7"]
        for kind, name, line in ((alert_lint.FOOTER_RULES, "fixed footer", FOOTER),
                                 (alert_lint.FOOTER_SNAPSHOT, "market-snapshot footer",
                                  alert_lint.snapshot_footer_line(AS_OF))):
            for body, number in bodies:
                with self.subTest(kind=kind, body=body):
                    result = alert_lint.check(body + "\n" + line, numbers, footer=kind)
                    self.assertEqual(["footer_missing"], codes(result), result.problems)
                    self.assertEqual(["the %s cannot close a structuring text (a leg or a candidate, line %d): use the "
                                      "structuring footer" % (name, number)], result.problems)
            for body in ("rules/candidate-1.1.0.toml", "the candidate rulebook candidate-1.1.0.toml", "candidate 1.1.0",
                         "a candidate", "the candidates", "legs 1 of 2: shown", "the leg 1 quote",
                         "best of the candidates"):
                with self.subTest(kind=kind, narrowed=body):
                    result = alert_lint.check(body + "\n" + line, ["1.1.0", "1", "2"], footer=kind)
                    self.assertTrue(result.ok, result.problems)

    def test_the_footer_rules_still_hold(self) -> None:
        moved = structuring_check(LEG_CLOSE, as_of=AS_OF + datetime.timedelta(minutes=5))
        self.assertEqual(["footer_missing"], codes(moved), "as_of pins the structuring footer's clock too")
        self.assertIn("19:36 UTC", moved.problems[0])
        late = structuring_check(LEG_CLOSE, footer=structuring_line(delay_min=20))
        self.assertEqual(["number_not_emitted"], codes(late), "a delay other than 15 minutes must be emitted")
        self.assertTrue(structuring_check(LEG_CLOSE, footer=structuring_line(delay_min=20),
                                          emitted=emitted_of(LEG_CLOSE) + ["20"]).ok)
        dotted = structuring_check("the held call · extra")
        self.assertEqual(["non_ascii"], codes(dotted), "the middle dot belongs to the footer line only")
        strict = structuring_check(LEG_CLOSE, "on 2026-11-23", identifiers=["2026-11-20"], clocks=["19:31 UTC"],
                                   as_of=AS_OF)
        self.assertEqual(["identifier_not_emitted"], codes(strict), strict.problems)
        self.assertTrue(structuring_check(LEG_CLOSE, identifiers=["2026-11-20"], clocks=["19:31 UTC"], as_of=AS_OF).ok)
        with self.assertRaises(ValueError):
            alert_lint.check(LEG_CLOSE + "\n" + structuring_line(), [], footer="structured")

    def test_the_structuring_phrase_table(self) -> None:
        table = alert_lint.STRUCTURING_FORBIDDEN_LANGUAGE
        self.assertEqual(["SL%02d" % index for index in range(1, 23)], [entry.code for entry in table])
        self.assertEqual(["advice", "advice", "advice", "advice", "advice", "advice", "advice", "claim", "claim",
                          "urgency", "advice", "order-instruction", "claim", "order-field", "claim", "claim", "advice",
                          "claim", "order-kind", "order-field", "trade-verb", "claim"],
                         [entry.category for entry in table])
        for entry in table:
            with self.subTest(entry=entry.code):
                self.assertTrue(entry.pattern.search(entry.example), entry.code)
                self.assertTrue(entry.pattern.flags & re.IGNORECASE, entry.code)
                self.assertNotIn(entry.pattern, alert_lint.FORBIDDEN_PATTERNS, "the SL table stays out of the "
                                 "global screen cli_journeys reads")
        self.assertEqual(25, len(alert_lint.FORBIDDEN_LANGUAGE))
        self.assertEqual(len(alert_lint.FORBIDDEN_LANGUAGE), len(alert_lint.FORBIDDEN_PATTERNS))
        self.assertEqual([case[0] for case in STRUCTURING_PHRASE_CASES], [entry.code for entry in table])

    def test_each_entry_against_its_cases(self) -> None:
        table = {entry.code: entry for entry in alert_lint.STRUCTURING_FORBIDDEN_LANGUAGE}
        for code, refused, allowed in STRUCTURING_PHRASE_CASES:
            for text in refused:
                with self.subTest(code=code, refused=text):
                    self.assertIsNotNone(table[code].pattern.search("the desk says " + text + " here"), text)
            for text in allowed:
                with self.subTest(code=code, allowed=text):
                    self.assertIsNone(table[code].pattern.search("the desk says " + text + " here"), text)

    def test_every_phrase_is_refused_in_structuring_mode_only(self) -> None:
        for entry in alert_lint.STRUCTURING_FORBIDDEN_LANGUAGE:
            code = "order_field" if entry.code in ("SL14", "SL20") else "forbidden_language"
            self.assertEqual(code, "order_field" if entry.category == "order-field" else "forbidden_language")
            for variant in (entry.example, entry.example.upper(), entry.example.title(), entry.example.lower()):
                body = "the desk says " + variant + " here"
                with self.subTest(entry=entry.code, variant=variant):
                    result = structuring_check(body)
                    self.assertIn(code, codes(result), result.problems)
                    self.assertTrue(any(entry.code in problem for problem in result.problems), result.problems)
                    rules = alert_lint.check(message(body), emitted_of(body), rule_ids=[])
                    self.assertFalse(any(entry.code in problem for problem in rules.problems), rules.problems)
            if " " in entry.example and entry.pattern.fullmatch(entry.example):  # a phrase of several words
                for joined in (entry.example.replace(" ", "\n"), entry.example.replace(" ", "   "),
                               entry.example.replace(" ", "\t"), entry.example.replace(" ", "-"),
                               entry.example.replace(" ", "_")):
                    with self.subTest(entry=entry.code, joined=joined):
                        result = structuring_check("the desk says " + joined + " here")
                        self.assertTrue(any(entry.code in problem for problem in result.problems), result.problems)

    def test_problems_never_echo_the_phrase(self) -> None:
        for entry in alert_lint.STRUCTURING_FORBIDDEN_LANGUAGE:
            result = structuring_check("the desk says " + entry.example + " here")
            for problem in result.problems:
                for other in alert_lint.STRUCTURING_FORBIDDEN_LANGUAGE:
                    self.assertIsNone(other.pattern.search(problem), (entry.code, problem))
                for pattern in alert_lint.FORBIDDEN_PATTERNS:
                    self.assertIsNone(pattern.search(problem), (entry.code, problem))

    def test_phrases_inside_the_footer_line_are_skipped(self) -> None:
        probe = alert_lint._phrase("SL99", "claim", r"\bpersonalized\s+suggestion\b", "personalized suggestion")
        with mock.patch.object(alert_lint, "STRUCTURING_FORBIDDEN_LANGUAGE", (probe,)):
            clean = structuring_check(LEG_CLOSE)
            self.assertTrue(clean.ok, clean.problems)
            echoed = structuring_check("a personalized suggestion")
            self.assertEqual(["forbidden_language"], codes(echoed))
            self.assertEqual("line 1, column 3: forbidden language (claim, SL99)", echoed.problems[0])

    def test_the_structuring_imperative_starts(self) -> None:
        self.assertEqual(STRUCTURING_IMPERATIVES, alert_lint.STRUCTURING_IMPERATIVE_START_WORDS)
        self.assertEqual(9, len(alert_lint.IMPERATIVE_START_WORDS), "the base list is unchanged")
        for index, word in enumerate(STRUCTURING_IMPERATIVES, 10):
            for spelled in (word, word.title()):
                for body in (spelled + " the 2027-01-15 115 call", "a line. " + spelled + " it",
                             "TICKET AG-20261020-01 | " + spelled + " it", "reasons: " + spelled + " it",
                             "a line, so " + spelled + " it"):
                    with self.subTest(word=spelled, body=body):
                        result = structuring_check(body)
                        problems = [p for f, p in zip(result.findings, result.problems) if f.code == "imperative_start"]
                        self.assertEqual(1, len(problems), result.problems)
                        self.assertIn("IS%02d" % index, problems[0])
                        self.assertNotIn(word, problems[0].lower().replace("imperative", ""))
                        rules = alert_lint.check(message(body), emitted_of(body))
                        self.assertNotIn("imperative_start", codes(rules), "structuring mode only")
        for index, word in enumerate(alert_lint.IMPERATIVE_START_WORDS, 1):
            with self.subTest(base=word):
                result = structuring_check("a line. " + word.title() + " it")
                self.assertTrue(any("IS%02d" % index in problem for problem in result.problems), result.problems)

    def test_the_fixed_lines_pass_and_the_wording_traps_fail(self) -> None:
        for line in ("TICKET AG-20261020-01 | WMT covered call | a roll of the short call (action ROLL) | account 1 of "
                     "desk.toml (margin) | computed 19:50 UTC, valid until 20:20 UTC (30 min)",
                     "you supply the limit; the desk prints none",
                     "valid until 20:20 UTC: after that its quotes are too old, and a new ticket needs a new request",
                     "after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
                     "if you decide to act, you type it into your brokerage account yourself and you supply the limit",
                     "the score ranks the candidates only on the objective you chose; it is not a forecast",
                     "a ticket for one of these: ask the Aegis Desk Lead for a ROLL ticket with its expiration "
                     "and strike",
                     "prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
                     "options is not modelled",
                     "entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher "
                     "level",
                     "approval: the highest step needs level 1 by the desk's convention, within your self-attested "
                     "level 2 (desk.toml); your broker's own approval for this account decides",
                     "STRUCTURING | AG-20261020-01 roll analysis | WMT covered call",
                     "the open interest and the hold period", "after leg 1 you hold 200 WMT shares and no option"):
            with self.subTest(line=line):
                result = structuring_check(line)
                self.assertTrue(result.ok, result.problems)
        traps = (("type this into your broker if you decide to act", "IS22"),
                 ("roll analysis AG-20261020-01", "IS04"),
                 ("the desk prints no " + "lim" + "it pri" + "ce: you supply the limit", "FL22"))
        for line, code in traps:
            with self.subTest(trap=line):
                result = structuring_check(line)
                self.assertTrue(any(code in problem for problem in result.problems), result.problems)

    def test_the_leg_grammar(self) -> None:
        passing = ("leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call",
                   "leg 1 of 2: closes your short 2 x WMT 2026-12-18 112.5 call | ask 3.50 | quote grade B, 18 min old",
                   "leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55",
                   "leg 1 of 1: opens a new long 1 x SPY 2027-03-19 610 put",
                   "leg 1 of 1: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80",
                   "leg 2 of 3: closes 200 of your long WMT shares | bid 104.20, ask 104.26",
                   "leg 3 of 3: closes 100 of your long BRK.B shares",
                   "leg 1 of 2: opens a new short 1 x WMT1 2026-12-18 110 call")
        for line in passing:
            with self.subTest(line=line):
                result = structuring_check(line)
                self.assertTrue(result.ok, result.problems)
        failing = ("leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | ",
                   "leg 1 of 2: closes your short 2 WMT 2026-11-20 105 call",
                   "Leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call",
                   "  leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call",
                   "leg 1 of 2 : closes your short 2 x WMT 2026-11-20 105 call",
                   "leg 1 of 2:closes your short 2 x WMT 2026-11-20 105 call",
                   "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 calls",
                   "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call|bid 3.35",
                   "leg 1 of 2: closes 200 of your short WMT shares",
                   "leg 1 of 2: closes your short 2 x wmt 2026-11-20 105 call",
                   "leg 1 of 2: closes your short 2 x WMT 20261120 105 call",
                   "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call and the rest",
                   "leg 1 of 2: opens a new short 2 x WMT 2026-11-20 105 call\t",
                   "leg 1 of 2: opens a new long 2 x WMT 2026-11-20 105 strangle",
                   "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105. call",
                   "leg 1 of 2: closes your short 2 x WMTXYZ1 2026-11-20 105 call")
        for line in failing:
            with self.subTest(line=line):
                result = structuring_check(line)
                self.assertEqual(["order_field"], codes(result), result.problems)
                self.assertEqual(["line 1, column 1: a leg line outside the fixed leg grammar"], result.problems)
        side = structuring_check("leg 1 of 2: short 2 x WMT 2026-12-18 110 call to close")
        self.assertEqual(["order_field", "imperative_start"], codes(side), "a side word at a segment start is IS38")
        both = structuring_check(LEG_CLOSE, "leg 2 of 2: opens 2 new WMT calls")
        self.assertEqual(["line 2, column 1: a leg line outside the fixed leg grammar"], both.problems)
        written = structuring_check("leg 1 of 2: " + STRUCTURING_IMPERATIVES[1] + " 2 WMT calls")
        self.assertEqual(["order_field", "imperative_start"], codes(written), written.problems)
        rules = alert_lint.check(failing[0] + "\n" + FOOTER, emitted_of(failing[0]))
        self.assertEqual(["footer_missing"], codes(rules), "the grammar is a structuring rule; the rules footer refuses "
                         "the leg line outright")

    def test_the_status_symbols_stand_only_at_a_table_cell_start(self) -> None:
        # 0.2.1: the structuring texts colour a check with exactly three symbols, each at the start of a table cell
        green, yellow, red = alert_lint.STATUS_SYMBOLS
        self.assertEqual(("\U0001F7E2", "\U0001F7E1", "\U0001F534"), alert_lint.STATUS_SYMBOLS)
        table = ("| check | this candidate | your criteria | |", "|---|---|---|:-:|",
                 "| floor | 4.34 % below | 3 to 10 % | " + green + " |",
                 "| ex-dividend | 2026-12-18, inside the call's life | accepted by #assign-ok | " + yellow + " |",
                 "| 2 | 2026-12-18 112.5 | debit 0.70 | " + red + " credit_only |")
        result = structuring_check(*table)
        self.assertTrue(result.ok, result.problems)
        for kind, footer in ((alert_lint.FOOTER_RULES, FOOTER),
                             (alert_lint.FOOTER_SNAPSHOT, alert_lint.snapshot_footer_line(AS_OF))):
            with self.subTest(footer=kind):
                refused = alert_lint.check(table[2] + "\n" + footer, emitted_of(table[2]), footer=kind)
                self.assertIn("non_ascii", codes(refused), "only a structuring text carries a status symbol")
        misplaced = ("floor " + green + " passes", green + " floor passes", "| floor |" + green + " |",
                     "| floor | " + green + "|", "| floor | " + green + green + " |", "| 4.34 " + green + " |",
                     "- " + green + " floor", "| bu" + green + "y |", "| 1" + green + "2 |",
                     "| floor | " + green + "️ |", "| floor | ✅ |", "| floor | \U0001F7E0 |")
        for line in misplaced:
            with self.subTest(line=line):
                self.assertIn("non_ascii", codes(structuring_check(line, emitted=["4.34", "1", "2"])))
        # a symbol is a list mark to the sentence-start rule: the word after it is a sentence start
        for word in ("close", "sell", "roll", "hold", "collar"):
            with self.subTest(word=word):
                result = structuring_check("| floor | " + red + " " + word + " the 105 call |")
                self.assertIn("imperative_start", codes(result), result.problems)

    def test_the_leg_grammar_takes_a_list_mark_and_a_quote_grade_alone(self) -> None:
        # 0.2.1: the structuring texts print their legs as list items, and a proposal or ticket names the quote source
        # once in its header, so a leg's quote segment may end at its grade; nothing else joins the grammar
        passing = ("- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | ask 3.50 | quote grade B, 18 min old",
                   "- leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60 | quote "
                   "grade B",
                   "leg 1 of 1: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | quote "
                   "grade A",
                   "- leg 2 of 3: closes 200 of your long WMT shares | bid 104.20, ask 104.26, mid 104.23 | quote grade A",
                   "- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 0.12, ask 0.16, mid 0.14 | quote "
                   "grade E | quote grade E: the conservative side stands",
                   "- leg 1 of 1: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95 | quote "
                   "grade B, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC")
        for line in passing:
            with self.subTest(line=line):
                result = structuring_check(line)
                self.assertTrue(result.ok, result.problems)
        leg = "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call"
        failing = ("-leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call",
                   "-  " + leg, "* " + leg, "+ " + leg, "  - " + leg, "- - " + leg, "-- " + leg, "1. " + leg,
                   "- " + leg + " | quote grade B",
                   "- " + leg + " | bid 3.35, ask 3.50, mid 3.425 | quote grade B | 3.45",
                   "- " + leg + " | bid 3.35, ask 3.50, mid 3.425 | quote grade B, firm",
                   "- " + leg + " | bid 3.35, ask 3.50, mid 3.425 | quote grade B, 18 min old, firm",
                   "- " + leg + " | bid 3.35, ask 3.50, mid 3.425 | quote grade G",
                   "- " + leg + " | bid 3.35, ask 3.50, mid 3.425 | quote grade b",
                   "- " + leg + " | bid 3.35, ask 3.50, mid 3.425 |quote grade B")
        for line in failing:
            with self.subTest(line=line):
                result = structuring_check(line)
                self.assertEqual(["order_field"], codes(result), result.problems)
                self.assertEqual(["line 1, column 1: a leg line outside the fixed leg grammar"], result.problems)
        for kind, footer in ((alert_lint.FOOTER_RULES, FOOTER),
                             (alert_lint.FOOTER_SNAPSHOT, alert_lint.snapshot_footer_line(AS_OF))):
            with self.subTest(footer=kind):
                result = alert_lint.check(passing[0] + "\n" + footer, emitted_of(passing[0]), footer=kind)
                self.assertIn("footer_missing", codes(result), "a listed leg is structuring content under any other "
                                                               "footer")


# The PR 3b review of structuring mode (L2-2, L2-3, L2-4, L2-7, BD7): every line below passed the lint before the
# fixer pass. Order-shaped words among them are assembled from pieces, as everywhere in this file.
ROLL_LEG_1 = "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call"
ROLL_LEG_2 = "leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call"
REVIEW_ORDER_SHAPED = (
    ROLL_LEG_1 + " | " + "LM" + "T 3.50 " + "DA" + "Y",
    ROLL_LEG_2 + " | " + "LM" + "T 3.55 " + "GT" + "D " + "IO" + "C",
    ROLL_LEG_1 + " | 3.50 debit, " + "DA" + "Y, " + "rou" + "te SMART",
    ROLL_LEG_2 + " | " + "ST" + "O, " + "BT" + "C",
    ROLL_LEG_1 + " | good for the day",
    "- leg 1 of 2: short 2 x WMT 2027-01-15 115 call to open",
    "leg one of two: short 2 x WMT 2027-01-15 115 call to open",
    "leg 1 of 2 - short 2 x WMT 2027-01-15 115 call to open",
    "leg_1_of_2: short 2 x WMT 2027-01-15 115 call to open",
    "1. leg 2 of 2: short 2 x WMT 2027-01-15 115 call to open",
    "> leg 2/2: short 2 x WMT 2027-01-15 115 call to open",
    "the plan: leg 2 of 2: short 2 x WMT 2027-01-15 115 call to open",
    "step 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call",
    "a " + "lim" + "it at or below 3.50 on leg 1",
    "a " + "lim" + "it no higher than 3.50",
    "a " + "lim" + "it of about 3.50",
    "pay at most 3.50",
    "the " + "lim" + "it (3.50) on leg 1",
    "pur" + "chase 1 x WMT 2027-01-15 115 call",
    "liquidate the 200 WMT shares",
    "exercise the 2026-11-20 105 call",
    "short 2 x WMT 2027-01-15 115 calls",
    "trade the 2027-01-15 115 call",
    "acquire 100 SPY shares",
    "dump the 200 WMT shares",
    "unload the 200 WMT shares",
)
REVIEW_CLAIMS = (
    "this is not investment advice", "this is not personalized investment advice",
    "not advice: the 2027-01-15 115 call ranks first", "a non-advice note",
    "the 2027-01-15 115 call is unlikely to be assigned", "the 2027-01-15 115 call will probably expire worthless",
    "assignment is improbable", "the roll carries no risk", "zero risk to the 200 shares", "a riskless credit of 10.00",
    "my pick is the 2027-01-15 115 call", "the top pick: the 2027-01-15 115 call", "it is best to open the 115 call",
    "it would be wise to roll now", "you had better roll before 20:20 UTC", "hurry: the ticket is valid until 20:20 UTC",
    "the desk suggests the 2027-01-15 115 call", "the desk prefers the 2027-01-15 115 call",
    "the desk favors the 2027-01-15 115 call", "the desk advises the 2027-01-15 115 call",
    "the 115 call is appropriate for your account", "the 115 call fits you", "this is a no-brainer",
    "a 0.0467 % return per calendar day", "the 115 call yields 0.0467 % a day", "a 0.0467 % daily yield on your shares",
    "income of 10.00 from the roll", "a sure gain of 10.00", "gains of 10.00",
    # BD7: underscore-joined forms of the claim and instruction words
    "probability_of_profit 0.62", "the likely_outcome", "pl" + "ace_it", "a safe_bet", "guaranteed_income",
)
REVIEW_RULE_CONTENT = ("your adopted rule fired: your plan: close", "your plan: close", "your_rule fired",
                       "Watch: the 2026-11-20 105 call", "the rulebook verdict is Hold", "an exit signal stands",
                       "Exit-Signal stands", "your own adopted rules say so")
REVIEW_STRUCTURING_UNDER_RULES = (
    "- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call", "> leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call",
    "1. leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call", "step 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call",
    "leg 2/2: opens a new short 2 x WMT 2027-01-15 115 call",
    "candidate #1: the 2027-01-15 115 call, extrinsic_per_day 0.0467", "1st candidate: the 2027-01-15 115 call",
    "rank 1: the 2027-01-15 115 call, extrinsic_per_day 0.0467; rank 2: the 2026-12-18 112.5 call",
    "candidate_2: the 2026-12-18 112.5 call", "candidate-2: the 2026-12-18 112.5 call",
    "BEST: the 2027-01-15 115 call (extrinsic_per_day 0.0467)",
    "TICKET AG-20261020-01 | WMT covered call | a roll of the short call (action ROLL)",
    "replacement candidates for the short 2026-11-20 105 call: 1 passed your criteria, 19 failed",
    "no leg to enter: if the 105 call is at least 0.01 in the money at expiry, OCC exercises it by exception",
    "FAILS_CRITERION credit_only: a net debit of 0.05", "the account holds 200 of your long WMT shares",
    "STRUCTURING | AG-20261020-01 roll analysis | WMT covered call | criteria structuring.toml (sha 5155427c)",
    "UNAVAILABLE | AG-20261020-01 roll analysis | no usable quote of the underlying",
    "NOT_APPLICABLE | AG-20261028-03 collar proposal | the 100 SPY shares already carry 1 long put",
    "NOTHING_TO_PROPOSE | AG-20261028-03 protective put proposal | the 100 SPY shares already carry 1 long put",
    "PROPOSAL SPY collar | on 100 SPY shares you hold",
    # 0.2.1: the desk view's markdown titles
    "### PROPOSAL AG-20261028-03 | SPY collar on 100 shares", "## TICKET AG-20261020-01 | WMT covered call",
    "# STRUCTURING AG-20261020-01 | WMT covered call", "**NOTHING_TO_PROPOSE** | AG-20261028-03",
    "REFUSED AG-20261020-01 | WMT covered call | closing your 2 short calls needs level 1 by the desk's convention; "
    "desk.toml records your self-attested level 0",
    "WITHHELD | the text of roll_analysis.py failed the alert lint (owner_text) and is withheld",
    "UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your criteria (SETUP step 9)",
    "UNAVAILABLE | CONFIG CHANGED: positions.csv: sha aaaaaaaa -> sha bbbbbbbb | the desk names no candidate and prints "
    "no ticket while desk.toml, positions.csv or structuring.toml differ from what you acknowledged",
)


class StructuringReviewTest(unittest.TestCase):
    """The PR 3b review findings L2-2, L2-3, L2-4, L2-7 and BD7, each string the reviewers showed passing."""

    def test_order_shapes_and_leg_lookalikes_are_refused(self) -> None:
        for body in REVIEW_ORDER_SHAPED:
            with self.subTest(body=body):
                result = structuring_check(body)
                self.assertFalse(result.ok, result.problems)

    def test_the_tools_own_lines_still_pass(self) -> None:
        for body in ("reasons: 73 calendar days inside your window 40 to 75; bid 3.55 at or above your minimum 0.50",
                     ROLL_LEG_1 + " | bid 3.35, ask 3.50, mid 3.425 | quote grade B, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, "
                     "as of 19:31 UTC",
                     ROLL_LEG_1 + " | ask 3.50 | quote grade B, 18 min old",
                     ROLL_LEG_1 + " | ask 0.16 | quote grade E, 18 min old | quote grade E: the conservative side stands",
                     ROLL_LEG_1 + " | bid 0.12, ask 0.16, mid 0.14 | quote grade E, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as "
                     "of 19:31 UTC | quote grade E: the conservative side stands",
                     ROLL_LEG_2 + " | bid 3.55 | quote grade B, 18 min old",
                     "leg 2 of 2: closes 200.5 of your long WMT shares | bid 104.20, ask 104.26, mid 104.23 | quote grade "
                     "A, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
                     "leg order: leg 1 first; after leg 1 you hold 200 WMT shares and no option",
                     "after leg 2 of this order you would hold 2 uncovered calls (level 4)",
                     "cash: +10.00 at the conservative prices (mid +35.00); the most the steps need at once: 700.00",
                     "it covers 91.504 per share (9150.40 in all) at your stress drop of 20 % (stressed price 518.496)",
                     "if assigned: you receive 100 SPY shares and pay the strike, cash -62000.00",
                     "if you decide otherwise, you act by Fri 2026-11-20 16:00 ET (21:00 UTC): a short option left to "
                     "expiry can be closed only while the session trades",
                     "you hold 200 WMT shares and 2 short calls", "the held call; the open interest and the hold period"):
            with self.subTest(body=body):
                result = structuring_check(body)
                self.assertTrue(result.ok, result.problems)

    def test_claims_preferences_and_non_advice_are_refused(self) -> None:
        for body in REVIEW_CLAIMS:
            with self.subTest(body=body):
                result = structuring_check(body)
                self.assertIn("forbidden_language", codes(result), result.problems)

    def test_rule_content_cannot_close_with_the_structuring_footer(self) -> None:
        for body in REVIEW_RULE_CONTENT:
            with self.subTest(body=body):
                result = structuring_check(body)
                self.assertIn("footer_missing", codes(result), result.problems)
        self.assertTrue(structuring_check("you hold 200 shares; the hold period; a watch list").ok)

    def test_the_rules_and_snapshot_footers_never_close_structuring_content(self) -> None:
        for kind, line in ((alert_lint.FOOTER_RULES, FOOTER), (alert_lint.FOOTER_SNAPSHOT,
                                                               alert_lint.snapshot_footer_line(AS_OF))):
            for body in REVIEW_STRUCTURING_UNDER_RULES:
                with self.subTest(kind=kind, body=body):
                    result = alert_lint.check(body + "\n" + line, emitted_of(body), footer=kind)
                    self.assertIn("footer_missing", codes(result), result.problems)
            narrowed = ["rules/candidate-1.1.0.toml", "the candidate rulebook candidate-1.1.0.toml", "candidate 1.1.0",
                        "a candidate", "the candidates", "legs 1 of 2: shown", "the leg 1 quote", "best of the candidates",
                        "rank and file", "the structuring.toml sha"]
            if kind == alert_lint.FOOTER_RULES:
                narrowed.append("your rule TP50 fired")
                # the routine's own WITHHELD line closes with the fixed footer (routine.py WITHHELD_LINE)
                narrowed.append("WITHHELD | book_check: the text failed the alert lint (imperative_start) and is "
                                "withheld; book_check.py shows it")
            for body in narrowed:
                with self.subTest(kind=kind, narrowed=body):
                    result = alert_lint.check(body + "\n" + line, ["1.1.0", "1", "2", "50"], footer=kind)
                    self.assertTrue(result.ok, result.problems)


class NumericTokenTest(unittest.TestCase):
    def tokens(self, text: str) -> List[str]:
        return [token.text for token in alert_lint.numeric_tokens(text)]

    def test_attached_forms(self) -> None:
        self.assertEqual(["2"], self.tokens("2x credit"))
        self.assertEqual(["2.0"], self.tokens("2.0x"))
        self.assertEqual(["21"], self.tokens("21-DTE"))
        self.assertEqual(["52.4"], self.tokens("52.4%"))
        self.assertEqual(["1,640"], self.tokens("1,640"))
        self.assertEqual(["1,640"], self.tokens("1,640, then"))
        self.assertEqual(["3.42"], self.tokens("at 3.42."))
        self.assertEqual(["2"], self.tokens("x2"))
        self.assertEqual(["50"], self.tokens("TP50"))
        self.assertEqual([".5"], self.tokens("a .5 move"))
        self.assertEqual(["1.0.0"], self.tokens("version 1.0.0."))
        self.assertEqual(["1,640.00"], self.tokens("$1,640.00"))
        self.assertEqual(["12", "30"], self.tokens("12:30"))

    def test_sign_belongs_to_the_number_only_when_detached_from_a_word(self) -> None:
        self.assertEqual(["-5"], self.tokens("move -5 %"))
        self.assertEqual(["+5"], self.tokens("(+5)"))
        self.assertEqual(["-1,000.00"], self.tokens("day P/L -$1,000.00"))
        self.assertEqual(["-5"], self.tokens("-5 at the start"))
        self.assertEqual(["5"], self.tokens("x-5"))
        self.assertEqual(["10", "20"], self.tokens("10-20"))
        self.assertEqual(["20261020", "01"], self.tokens("AG-20261020-01"))

    def test_positions_are_one_based(self) -> None:
        token = alert_lint.numeric_tokens("ab\ncd 42")[0]
        self.assertEqual(("42", 2, 4), (token.text, token.line, token.column))


class EmittedTest(unittest.TestCase):
    def test_brief_red_team_examples(self) -> None:
        for text, emitted, number in (("loss 2x credit", ["2.0"], "2"), ("21-DTE", ["34"], "21"),
                                      ("captured 52.4 %", ["52.1"], "52.4")):
            with self.subTest(text=text):
                result = alert_lint.check(message(HEAD + " | " + text), emitted)
                self.assertFalse(result.ok)
                self.assertEqual(["number_not_emitted"], codes(result))
                self.assertIn("number %s " % number, result.problems[0])

    def test_exact_string_match(self) -> None:
        for text, emitted in (("2 x", ["2.0"]), ("2.00 x", ["2.0"]), ("move -5 %", ["5"]), ("move 5 %", ["-5"]),
                              ("1640", ["1,640"]), ("1,640", ["1640"]), ("0.48", ["0.480"])):
            with self.subTest(text=text, emitted=emitted):
                self.assertFalse(alert_lint.check(message(HEAD + " | " + text), emitted).ok)

    def test_trailing_percent_x_and_comma_are_stripped_from_emitted_strings(self) -> None:
        for text, emitted in (("52.4 %", ["52.4%"]), ("2.0 x credit", ["2.0x"]), ("1,640 then", ["1,640,"]),
                              ("-1,000.00 day", ["-$1,000.00"]), ("95.32 %", ["95.32 %"])):
            with self.subTest(text=text, emitted=emitted):
                result = alert_lint.check(message(HEAD + " | " + text), emitted)
                self.assertTrue(result.ok, result.problems)

    def test_days_minutes_and_percentages_must_be_emitted(self) -> None:
        for text, number in (("dte 13 trading days", "13"), ("run 3 min late", "3"), ("45 calendar days", "45"),
                             ("captured 52.10 %", "52.10"), ("Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed", "15")):
            with self.subTest(text=text):
                self.assertEqual(["number_not_emitted"], codes(alert_lint.check(message(HEAD + " | " + text), [])))
                self.assertTrue(alert_lint.check(message(HEAD + " | " + text), [number]).ok)

    def test_numbers_checked_counts_every_token_outside_the_whitelist(self) -> None:
        result = alert_lint.check(message(HEAD + " | 1 2 3 at 19:31 UTC"), ["1", "2", "3"])
        self.assertTrue(result.ok)
        self.assertEqual(3, result.numbers_checked)


class WhitelistTest(unittest.TestCase):
    def passes(self, text: str, rule_ids: Optional[Sequence[str]] = None) -> bool:
        return alert_lint.check(message(HEAD + " | " + text), [], rule_ids=rule_ids).ok

    def test_identifiers_need_no_emission(self) -> None:
        for text in ("AG-20261020-01", "AG-20261020-123", "SIG-20261120-001", "group ROW-12", "rules 1.0.0",
                     "rules 12.30.4", "sha 3f9a01bc", "as of 19:31 UTC", "act by 16:30 ET", "on 2026-11-20",
                     "your rule TP80", "your rule MOVE5_DOWN", "your rule PIN3", "your rule LTP100"):
            with self.subTest(text=text):
                self.assertTrue(self.passes(text))

    def test_whitelist_forms_are_exact(self) -> None:
        for text in ("sha 3f9a01b", "sha 3F9A01BC", "rules 1.0", "rules1.0.0", "25:61 UTC", "19:31 PST",
                     "2026-13-45", "2026-02-30", "AG-2026102-01", "SIG-20261120-01", "ROW-x12", "X2", "group row-12"):
            with self.subTest(text=text):
                self.assertFalse(self.passes(text))

    def test_known_rule_ids_narrow_the_rule_id_whitelist(self) -> None:
        self.assertTrue(self.passes("your rule DTE34"))
        self.assertFalse(self.passes("your rule DTE34", rule_ids=["DTE21", "TP80"]))
        self.assertTrue(self.passes("your rule DTE21", rule_ids=["DTE21", "TP80"]))
        self.assertTrue(self.passes("your rule T1", rule_ids=["T1"]))


class ForbiddenLanguageTest(unittest.TestCase):
    def test_table_shape(self) -> None:
        self.assertGreaterEqual(len(alert_lint.FORBIDDEN_LANGUAGE), 20)
        seen = set()
        for entry in alert_lint.FORBIDDEN_LANGUAGE:
            self.assertNotIn(entry.code, seen)
            seen.add(entry.code)
            self.assertIn(entry.category, alert_lint.FORBIDDEN_CATEGORIES)
            self.assertTrue(entry.pattern.search(entry.example), entry.code)
        self.assertEqual(len(alert_lint.FORBIDDEN_LANGUAGE), len(alert_lint.FORBIDDEN_PATTERNS))

    def test_the_brief_list_is_covered(self) -> None:
        brief = ("you " + "should", "we " + "rec" + "ommend", "I " + "rec" + "ommend", "bu" + "y", "se" + "ll", "place an " + "order",
                 "limit " + "order", "market " + "order", "G" + "TC", "day " + "order", "roll " + "to",
                 "roll " + "into", "tar" + "get")
        for phrase in brief:
            with self.subTest(phrase=phrase):
                self.assertTrue(any(p.search(phrase) for p in alert_lint.FORBIDDEN_PATTERNS))

    def expect(self, text: str, code: str) -> None:
        result = alert_lint.check(message(HEAD + " | " + text), [])
        self.assertFalse(result.ok, text)
        self.assertIn(code, codes(result), (text, result.problems))

    def test_every_entry_any_case(self) -> None:
        for entry in alert_lint.FORBIDDEN_LANGUAGE:
            code = "order_field" if entry.category == "order-field" else "forbidden_language"
            for variant in (entry.example, entry.example.upper(), entry.example.title(), entry.example.lower()):
                with self.subTest(entry=entry.code, variant=variant):
                    self.expect("the desk says " + variant + " now", code)

    def test_multiword_entries_split_across_lines_and_spaces(self) -> None:
        for entry in alert_lint.FORBIDDEN_LANGUAGE:
            if " " not in entry.example:
                continue
            code = "order_field" if entry.category == "order-field" else "forbidden_language"
            joins = [entry.example.replace(" ", "\n"), entry.example.replace(" ", "   "),
                     entry.example.replace(" ", "\t")]
            if entry.category != "order-field":
                joins += [entry.example.replace(" ", "-"), entry.example.replace(" ", "_"),
                          entry.example.replace(" ", " -\n")]
            for joined in joins:
                with self.subTest(entry=entry.code, joined=joined):
                    self.expect("the desk says " + joined + " now", code)

    def test_word_bounded(self) -> None:
        for entry in alert_lint.FORBIDDEN_LANGUAGE:
            if entry.category == "order-field" or not entry.example.isalpha():
                continue
            text = "the desk says q" + entry.example + "q now"
            with self.subTest(entry=entry.code):
                self.assertTrue(alert_lint.check(message(HEAD + " | " + text), []).ok, text)

    def test_problems_never_echo_the_forbidden_text(self) -> None:
        for entry in alert_lint.FORBIDDEN_LANGUAGE:
            result = alert_lint.check(message(HEAD + " | the desk says " + entry.example + " now"), [])
            for problem in result.problems:
                for pattern in alert_lint.FORBIDDEN_PATTERNS:
                    self.assertIsNone(pattern.search(problem), (entry.code, problem))
            self.assertIn(entry.code, " ".join(result.problems))


class ImperativeStartTest(unittest.TestCase):
    def imperative(self, text: str) -> bool:
        result = alert_lint.check(text, [])
        return "imperative_start" in codes(result)

    def test_every_word_at_every_sentence_start(self) -> None:
        self.assertEqual(9, len(alert_lint.IMPERATIVE_START_WORDS))
        for word in alert_lint.IMPERATIVE_START_WORDS:
            for spelled in (word, word.title(), word.upper()):
                for text in (message(spelled + " the group now"), message(HEAD + " | " + spelled + " it"),
                             message("WATCH. " + spelled + " it"), message("WATCH; " + spelled + " it"),
                             message("WATCH: " + spelled + " it"), message("WATCH! " + spelled + " it"),
                             message("WATCH? " + spelled + " it"), message("- " + spelled + " it"),
                             message("WATCH | (" + spelled + " it)"), message('WATCH. "' + spelled + '" it'),
                             message("## " + spelled + " it"), message("WATCH |" + spelled + " it"),
                             message("WATCH;" + spelled + " it"), message("WATCH:" + spelled + " it"),
                             message("WATCH!" + spelled + " it"), message("WATCH. \t" + spelled + " it")):
                    if spelled == "EXIT" and text.startswith("EXIT SIGNAL"):
                        continue
                    with self.subTest(text=text):
                        self.assertTrue(self.imperative(text))

    def test_the_pr2d_hardening(self) -> None:
        # PR 2d addendum O2 (decisions D1-9): two imperative starts (IS08, IS09) and one order kind (FL25), while the
        # plan's own warning on a level sheet still passes. The words are assembled so this file spells none of them.
        is08, is09 = "exe" + "cute", "fi" + "ll"
        self.assertEqual((is08, is09), alert_lint.IMPERATIVE_START_WORDS[7:])
        working = next(entry for entry in alert_lint.FORBIDDEN_LANGUAGE if entry.code == "FL25")
        self.assertEqual(("order-kind", "wor" + "king or" + "der"), (working.category, working.example))
        warning = message("TP50 fires at an option price of 1.71 (= credit 3.42 x (1 - 50 %)); alert level, not an "
                          "order: a limit at this price " + is08 + "s immediately")
        self.assertTrue(alert_lint.check(warning, ["1.71", "3.42", "1", "50"]).ok, alert_lint.check(warning, []).problems)
        for text, code, problem in ((is08.title() + " at 1.71 when TP50 fires", "imperative_start", "IS08"),
                                    (is09.title() + " at 1.71 when TP50 fires", "imperative_start", "IS09"),
                                    ("a " + working.example + " at 1.71 rests", "forbidden_language", "FL25"),
                                    ("a " + working.example.replace("der", "ders") + " rests", "forbidden_language",
                                     "FL25")):
            with self.subTest(text=text):
                result = alert_lint.check(message(text), ["1.71"])
                self.assertEqual([code], codes(result), result.problems)
                self.assertIn(problem, result.problems[0])
        for text in ("the " + is09 + " of it", "TP50 " + is08 + "s at 1.71", "the " + is09 + " rate"):
            with self.subTest(text=text):
                self.assertNotIn("imperative_start", codes(alert_lint.check(message(text), ["1.71", "50"])))

    def test_mid_sentence_words_are_not_starts(self) -> None:
        for word in alert_lint.IMPERATIVE_START_WORDS:
            with self.subTest(word=word):
                self.assertFalse(self.imperative(message("WATCH the " + word + " of it")))
                self.assertFalse(self.imperative(message("WATCH at 3.42" + word)))
                self.assertFalse(self.imperative(message("WATCH ack." + word + " now")))

    def test_exceptions_are_exact(self) -> None:
        close = alert_lint.IMPERATIVE_START_WORDS[0]
        self.assertEqual("close", close)
        self.assertFalse(self.imperative(message(HEAD + " | your plan: " + close + " | not an instruction")))
        self.assertFalse(self.imperative(message(HEAD + " | your plan: " + close)))
        self.assertFalse(self.imperative(message(HEAD + " | your plan: " + close + " (TP80)")))
        self.assertTrue(self.imperative(message(HEAD + " | your plan: " + close + " the group now")))
        self.assertTrue(self.imperative(message(HEAD + " | Your plan: " + close.title())))
        self.assertTrue(self.imperative(message(HEAD + " | plan: " + close)))
        self.assertFalse(self.imperative(message("EXIT SIGNAL AG-20261020-01")))
        self.assertTrue(self.imperative(message("Exit signal AG-20261020-01")))
        self.assertTrue(self.imperative(message("EXIT now")))

    def test_problem_text_names_the_list_entry_not_the_word(self) -> None:
        for index, word in enumerate(alert_lint.IMPERATIVE_START_WORDS, 1):
            result = alert_lint.check(message("WATCH. " + word.title() + " it"), [])
            problems = [p for f, p in zip(result.findings, result.problems) if f.code == "imperative_start"]
            self.assertEqual(1, len(problems), result.problems)
            self.assertIn("IS%02d" % index, problems[0])
            self.assertNotIn(word, problems[0].lower().replace("imperative", ""))


class AlphabetTest(unittest.TestCase):
    def test_outside_characters_are_rejected(self) -> None:
        for char in ("о", "​", " ", "２", "\r", "−", "—", "\U0001f4c8", "\x00"):
            with self.subTest(char=hex(ord(char))):
                result = alert_lint.check(message(HEAD + " | y" + char + "ou"), [])
                self.assertIn("non_ascii", codes(result))
                self.assertIn("U+%04X" % ord(char), " ".join(result.problems))

    def test_middle_dot_tab_and_newline_are_allowed(self) -> None:
        # The middle dot belongs to the footer (the lens-2 review: anywhere else it hid a phrase or a sentence start).
        self.assertTrue(alert_lint.check(message(HEAD + " x\tz"), []).ok)
        self.assertEqual(["non_ascii"], codes(alert_lint.check(message(HEAD + " · x\tz"), [])))


class ResultShapeTest(unittest.TestCase):
    def test_findings_and_problems_line_up(self) -> None:
        result = alert_lint.check(message(HEAD + " | 21-DTE", footer=None), ["34"])
        self.assertFalse(result.ok)
        self.assertEqual(len(result.findings), len(result.problems))
        for finding, problem in zip(result.findings, result.problems):
            self.assertIsInstance(problem, str)
            self.assertEqual(problem, finding.message)
            self.assertGreaterEqual(finding.line, 0)
            self.assertGreaterEqual(finding.column, 0)
        self.assertEqual(["number_not_emitted", "footer_missing"], codes(result))
        self.assertEqual({"code": "number_not_emitted", "line": 1, "column": 49,
                          "message": result.problems[0]}, result.findings[0].as_dict())

    def test_load_emitted_document(self) -> None:
        self.assertEqual((["1", "2"], None), alert_lint.load_emitted_document(["1", "2"]))
        self.assertEqual((["1"], ["TP80"]), alert_lint.load_emitted_document({"emitted_numbers": ["1"], "rule_ids": ["TP80"]}))
        self.assertEqual((["1"], None), alert_lint.load_emitted_document({"emitted_numbers": ["1"]}))
        for bad in ({"numbers": []}, [1, 2], "1", None, {"emitted_numbers": ["1"], "rule_ids": "TP80"},
                    {"emitted_numbers": ["1"], "extra": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    alert_lint.load_emitted_document(bad)

    def test_unwhitelisted_numbers_feed_the_formatters(self) -> None:
        tokens = alert_lint.unwhitelisted_numbers("row 4 on 2026-11-20 at 19:31 UTC: 0.16 ask (AG-20261020-01)")
        self.assertEqual(["4", "0.16"], [token.text for token in tokens])


# --------------------------------------------------------------------------------------------
# The committed fixtures under tests/fixtures/alerts/: passing messages (rendered by aegis_desk.alerts, pinned by
# tests/engine/test_alerts.py) and red-team messages, each one documented mutation of a passing message.
# --------------------------------------------------------------------------------------------

PASS_FIXTURES = ("pass_exit_signal", "pass_watch", "pass_unavailable", "pass_composed", "pass_pre_close")


def example(code: str) -> str:
    return next(entry.example for entry in alert_lint.FORBIDDEN_LANGUAGE if entry.code == code)


def fail_fixture_table() -> dict:
    """``{fixture: (base pass fixture, [(old, new) text replacements], (emitted added, emitted removed), [codes])}``."""
    footer = "\n" + FOOTER
    return {
        "fail_2x_credit": ("pass_exit_signal", [("credit captured 95.32 % (your threshold: at or above 80 %)",
                                                 "loss 2x credit")], (["2.0"], []), ["number_not_emitted"]),
        "fail_21_dte": ("pass_watch", [("time to expiry 13 trading days (your threshold: at or below 21 trading days)",
                                        "time to expiry 34 trading days (a 21-DTE rule)")], (["34"], ["21", "13"]),
                        ["number_not_emitted"]),
        "fail_pct_52_4": ("pass_exit_signal", [("credit captured 95.32 %", "credit captured 52.4 %")],
                          (["52.1"], ["95.32"]), ["number_not_emitted"]),
        "fail_you_should": ("pass_exit_signal", [("your plan: close |", "your plan: close; %s act today |"
                                                  % example("FL01"))], ([], []), ["forbidden_language"]),
        "fail_missing_footer": ("pass_exit_signal", [(footer, "")], ([], []), ["footer_missing"]),
        "fail_order_field": ("pass_exit_signal", [("\nsuppressed by your row tags", "\nticket with %s, %s, %s\n"
                                                   "suppressed by your row tags" % (example("FL21"), example("FL22"),
                                                                                   example("FL23")))],
                             (["2", "0.16"], []), ["order_field"]),
        "fail_imperative_start": ("pass_exit_signal", [("your plan: close |", "your plan: close | %s the group today |"
                                                        % alert_lint.IMPERATIVE_START_WORDS[0].title())], ([], []),
                                  ["imperative_start"]),
        "fail_split_phrase": ("pass_exit_signal", [("not an instruction to trade; you act in your brokerage account.",
                                                    "not an instruction to trade; %s acting in your brokerage account."
                                                    % example("FL02").replace(" ", "\n"))], ([], []),
                              ["forbidden_language"]),
        "fail_homoglyph": ("pass_exit_signal", [("your plan: close", "your plan: cl\u043ese")], ([], []), ["non_ascii"]),
        "fail_imperative_execute": ("pass_exit_signal", [("your plan: close |", "your plan: close | %s the group today |"
                                                          % alert_lint.IMPERATIVE_START_WORDS[7].title())], ([], []),
                                    ["imperative_start"]),
        "fail_imperative_fill": ("pass_exit_signal", [("your plan: close |", "your plan: close | %s the group today |"
                                                       % alert_lint.IMPERATIVE_START_WORDS[8].title())], ([], []),
                                 ["imperative_start"]),
        "fail_order_kind": ("pass_exit_signal", [("your plan: close |", "your plan: close; a %s rests |"
                                                     % example("FL25"))], ([], []), ["forbidden_language"]),
    }


STRUCTURING_PASS_FIXTURES = ("pass_structuring_ticket", "pass_structuring_roll", "pass_structuring_proposal")


def structuring_fail_fixture_table() -> Dict[str, tuple]:
    """``{fixture: (base pass fixture, [(old, new) text replacements], {document key: new value}, [codes])}``: each
    structuring red-team fixture is one documented change of a passing fixture and its emitted document."""
    rules = FOOTER
    structuring = alert_lint.structuring_footer_line(AS_OF)
    caveat = "the score ranks the candidates only on the objective you chose; it is not a forecast"
    return {
        # the desk view's status symbols stand only in a structuring text: under the rules footer they are non_ascii too
        "fail_structuring_under_rules_footer": ("pass_structuring_ticket", [(structuring, rules)], {"footer": "rules"},
                                                ["non_ascii", "footer_missing"]),
        # "your plan: close" is no exception under the structuring footer (review L2-7): an imperative start too
        "fail_rules_text_under_structuring_footer": ("pass_exit_signal", [(rules, structuring)],
                                                     {"footer": "structuring"}, ["imperative_start", "footer_missing"]),
        "fail_structuring_leg_grammar": ("pass_structuring_ticket", [(
            "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call",
            "leg 1 of 2: short 2 x WMT 2026-11-20 105 call to close")], {}, ["order_field", "imperative_start"]),
        "fail_structuring_go_with": ("pass_structuring_roll", [(caveat, caveat + ", and owners often " + "go wi" + "th it")],
                                     {}, ["forbidden_language"]),
        "fail_structuring_at_price": ("pass_structuring_ticket", [("you supply the limit; the desk prints none",
                                                                   "you supply the limit, for example " + "a" + "t 3.50")],
                                      {}, ["order_field"]),
    }


def read_fixture(name: str) -> str:
    with open(os.path.join(ALERT_FIXTURES, name + ".txt"), encoding="utf-8") as handle:
        return handle.read()


def read_emitted(name: str) -> dict:
    with open(os.path.join(ALERT_FIXTURES, name + ".emitted.json"), encoding="utf-8") as handle:
        return json.load(handle)


class FixtureTest(unittest.TestCase):
    def lint(self, name: str) -> alert_lint.LintResult:
        return alert_lint.load_emitted(read_emitted(name)).check(read_fixture(name))

    def test_every_fixture_is_listed(self) -> None:
        found = sorted(os.path.basename(path)[:-len(".txt")] for path in glob.glob(os.path.join(ALERT_FIXTURES, "*.txt")))
        self.assertEqual(sorted(PASS_FIXTURES + tuple(fail_fixture_table()) + STRUCTURING_PASS_FIXTURES
                                + tuple(structuring_fail_fixture_table())), found)
        for name in found:
            self.assertTrue(os.path.isfile(os.path.join(ALERT_FIXTURES, name + ".emitted.json")), name)
        self.assertTrue(os.path.isfile(os.path.join(ALERT_FIXTURES, "README.md")))

    def test_the_brief_red_team_set_is_present(self) -> None:
        for name in ("fail_2x_credit", "fail_21_dte", "fail_pct_52_4", "fail_you_should", "fail_missing_footer",
                     "fail_order_field"):
            self.assertIn(name, fail_fixture_table())

    def test_passing_fixtures_pass(self) -> None:
        for name in PASS_FIXTURES:
            with self.subTest(name=name):
                result = self.lint(name)
                self.assertTrue(result.ok, result.problems)

    def test_red_team_fixtures_fail_for_their_reason_only(self) -> None:
        for name, (_base, _replacements, _emitted, codes) in fail_fixture_table().items():
            with self.subTest(name=name):
                result = self.lint(name)
                self.assertFalse(result.ok)
                self.assertEqual(sorted(set(codes)), sorted({finding.code for finding in result.findings}),
                                 result.problems)

    def test_red_team_fixtures_derive_from_a_passing_fixture(self) -> None:
        for name, (base, replacements, (added, removed), _codes) in fail_fixture_table().items():
            with self.subTest(name=name):
                text = read_fixture(base)
                for old, new in replacements:
                    self.assertEqual(1, text.count(old), old)
                    text = text.replace(old, new)
                self.assertEqual(text, read_fixture(name))
                base_emitted = read_emitted(base)
                expected = [item for item in base_emitted["emitted_numbers"] if item not in removed] + list(added)
                self.assertEqual(dict(base_emitted, emitted_numbers=expected), read_emitted(name))
                self.assertEqual(set(alert_lint.EMITTED_DOCUMENT_KEYS), set(read_emitted(name)), "the strict document")

    def test_structuring_fixtures_pass_in_structuring_mode(self) -> None:
        for name in STRUCTURING_PASS_FIXTURES:
            with self.subTest(name=name):
                document = read_emitted(name)
                self.assertEqual(list(alert_lint.EMITTED_DOCUMENT_KEYS) + ["footer"], list(document))
                self.assertEqual(("structuring", []), (document["footer"], document["rule_ids"]))
                result = self.lint(name)
                self.assertTrue(result.ok, result.problems)
                self.assertTrue(read_fixture(name).endswith(alert_lint.structuring_footer_line(AS_OF) + "\n"))
                for kind in (alert_lint.FOOTER_RULES, alert_lint.FOOTER_SNAPSHOT):
                    other = alert_lint.load_emitted(document).check(read_fixture(name), kind)
                    self.assertIn("footer_missing", [finding.code for finding in other.findings])

    def test_structuring_red_team_fixtures_fail_for_their_reason_only(self) -> None:
        for name, (_base, _replacements, _changes, expected) in structuring_fail_fixture_table().items():
            with self.subTest(name=name):
                result = self.lint(name)
                self.assertFalse(result.ok)
                self.assertEqual(sorted(set(expected)), sorted({finding.code for finding in result.findings}),
                                 result.problems)

    def test_structuring_red_team_fixtures_derive_from_a_passing_fixture(self) -> None:
        for name, (base, replacements, changes, _codes) in structuring_fail_fixture_table().items():
            with self.subTest(name=name):
                text = read_fixture(base)
                for old, new in replacements:
                    self.assertEqual(1, text.count(old), old)
                    text = text.replace(old, new)
                self.assertEqual(text, read_fixture(name))
                self.assertEqual(dict(read_emitted(base), **changes), read_emitted(name))

    def test_the_brief_examples_are_verbatim(self) -> None:
        self.assertIn("loss 2x credit", read_fixture("fail_2x_credit"))
        self.assertIn("2.0", read_emitted("fail_2x_credit")["emitted_numbers"])
        self.assertIn("21-DTE", read_fixture("fail_21_dte"))
        self.assertIn("34", read_emitted("fail_21_dte")["emitted_numbers"])
        self.assertNotIn("21", read_emitted("fail_21_dte")["emitted_numbers"])
        self.assertIn("52.4 %", read_fixture("fail_pct_52_4"))
        self.assertIn("52.1", read_emitted("fail_pct_52_4")["emitted_numbers"])


# --------------------------------------------------------------------------------------------
# The lens-2 review's evasion forms. Each phrase is assembled from pieces, so this file still spells no
# order-shaped text; each must be refused.
# --------------------------------------------------------------------------------------------

EVASION_NUMBERS = ["95.32", "80", "0.16", "2", "110", "105", "10", "50"]


def evasion_forms() -> List[tuple]:
    """``(name, extra line, expected finding code)``: the forms the lens-2 review found passing."""
    lim, pri, qua, rec, sho, rol, clo = "lim" + "it", "pri" + "ce", "quan" + "tity", "rec" + "ommend", "sh" + "ould", \
        "ro" + "ll", "Cl" + "ose"
    return [
        ("field with spaces", "%s %s: 0.16" % (lim, pri), "order_field"),
        ("field with a hyphen", "%s-%s: 0.16" % (lim.title(), pri.title()), "order_field"),
        ("order type field", "or" + "der type: " + lim, "order_field"),
        ("quantity field", "%s: 2" % qua, "order_field"),
        ("price field", "%s: 0.16" % pri, "order_field"),
        ("limit and a price", "your plan: close (the call, %s 0.16)" % lim, "order_field"),
        ("contracted advice", "I'd %s closing it" % rec, "forbidden_language"),
        ("would advice", "I would %s closing it" % rec, "forbidden_language"),
        ("adverb advice", "we strongly %s closing it" % rec, "forbidden_language"),
        ("bare advice", "%s closing the call now" % rec, "forbidden_language"),
        ("an adverb before the modal", "you really %s close it" % sho, "forbidden_language"),
        ("one word before the destination", "the plan is to %s out t" % rol + "o the December 110 call",
         "forbidden_language"),
        ("three words before the destination", "a %s up and out t" % rol + "o the 110 strike", "forbidden_language"),
        ("middle dot imperative", "fired · %s the call now" % clo, "non_ascii"),
        ("middle dot phrase", "you·%s close" % sho, "non_ascii"),
        ("dash imperative", "fired - %s the call now" % clo.lower(), "imperative_start"),
        ("double dash imperative", "fired -- %s the call now" % clo.lower(), "imperative_start"),
        ("comma imperative", "fired, so %s the call now" % clo.lower(), "imperative_start"),
        ("paren list imperative", "1) %s the call now" % clo, "imperative_start"),
        ("letter list imperative", "a) %s the call now" % clo, "imperative_start"),
    ]


class EvasionTest(unittest.TestCase):
    def test_every_form_is_refused_in_both_modes(self) -> None:
        for name, line, code in evasion_forms():
            text = message(HEAD + " | your rule TP80 (rules 1.0.0, sha 3f9a01bc) fired: credit captured 95.32 %", line)
            for rule_ids in (None, ["TP80"]):
                with self.subTest(form=name, rule_ids=rule_ids):
                    result = alert_lint.check(text, EVASION_NUMBERS, rule_ids=rule_ids)
                    self.assertFalse(result.ok, (name, result.problems))
                    self.assertIn(code, codes(result), (name, result.problems))

    def test_the_forbidden_patterns_screen_the_phrases_too(self) -> None:
        for name, line, code in evasion_forms():
            if code not in ("order_field", "forbidden_language"):
                continue
            with self.subTest(form=name):
                self.assertTrue(any(pattern.search(line) for pattern in alert_lint.FORBIDDEN_PATTERNS), name)

    def test_the_desk_sentences_still_pass(self) -> None:
        clean = message(HEAD + " | your rule TP80 (rules 1.0.0, sha 3f9a01bc) fired: credit captured 95.32 % | your "
                        "plan: review a roll | not an instruction to trade; you act in your brokerage account.",
                        "also fired: your rule DTE21: time to expiry 2 trading days | your plan: review and decide",
                        "pre-close | verdicts: 0 EXIT SIGNAL, 2 WATCH | rules 1.0.0, sha 3f9a01bc, run 2 min late")
        self.assertTrue(alert_lint.check(clean, ["95.32", "2", "0"]).ok, alert_lint.check(clean, ["95.32", "2", "0"]).problems)

    def test_the_middle_dot_belongs_to_the_footer_only(self) -> None:
        self.assertTrue(alert_lint.check(message(HEAD), []).ok, "the footer's own dots")
        result = alert_lint.check(message(HEAD + " · x"), [])
        self.assertEqual(["non_ascii"], codes(result))
        self.assertIn("footer", result.problems[0])


class StrictIdentifierTest(unittest.TestCase):
    """With the message's own identifiers, clocks and as-of (the emitted document book_check prints), only those
    exact spans are identifiers: an invented row id, sha or clock fails, and so does a footer moved in time."""

    BODY = HEAD + " | your rule TP80 (rules 1.0.0, sha 3f9a01bc) fired: credit captured 95.32 % | quote grade E, as of 19:31 UTC"
    IDENTIFIERS = ["AG-20261020-01", "rules 1.0.0", "sha 3f9a01bc"]
    CLOCKS = ["19:31 UTC"]

    def strict(self, text: str) -> alert_lint.LintResult:
        return alert_lint.check(text, ["95.32"], rule_ids=["TP80"], identifiers=self.IDENTIFIERS, clocks=self.CLOCKS,
                                as_of=AS_OF)

    def test_the_messages_own_spans_pass(self) -> None:
        self.assertTrue(self.strict(message(self.BODY)).ok, self.strict(message(self.BODY)).problems)

    def test_invented_spans_fail(self) -> None:
        for extra in ("ROW-105 strike", "sha 00000110 strike", "loss 10:50 ET", "on 2026-11-23", "AG-20261020-02",
                      "rules 1.1.0", "SIG-20261120-009"):
            with self.subTest(extra=extra):
                result = self.strict(message(self.BODY, extra))
                self.assertEqual(["identifier_not_emitted"], codes(result), result.problems)
                self.assertTrue(alert_lint.check(message(self.BODY, extra), ["95.32", "105", "110", "10", "50"]).ok
                                or extra.startswith("on "), "plain mode keeps the permissive whitelist")

    def test_a_footer_moved_in_time_fails(self) -> None:
        moved = message(self.BODY.replace("19:31 UTC", "19:49 UTC"),
                        footer=alert_lint.footer_line(AS_OF + datetime.timedelta(minutes=18)))
        result = alert_lint.check(moved, ["95.32"], rule_ids=["TP80"], identifiers=self.IDENTIFIERS,
                                  clocks=self.CLOCKS + ["19:49 UTC"], as_of=AS_OF)
        self.assertEqual(["footer_missing"], codes(result), result.problems)
        self.assertIn("19:31 UTC", result.problems[0])
        clocked = alert_lint.check(moved, ["95.32"], rule_ids=["TP80"], identifiers=self.IDENTIFIERS, clocks=self.CLOCKS)
        self.assertIn("identifier_not_emitted", codes(clocked))

    def test_the_emitted_document_carries_them(self) -> None:
        document = {"emitted_numbers": ["95.32"], "rule_ids": ["TP80"], "identifiers": self.IDENTIFIERS,
                    "clocks": self.CLOCKS, "as_of": "2026-11-20T19:31:12+00:00"}
        loaded = alert_lint.load_emitted(document)
        self.assertEqual((["95.32"], ["TP80"], self.IDENTIFIERS, self.CLOCKS, AS_OF),
                         (loaded.emitted_numbers, loaded.rule_ids, loaded.identifiers, loaded.clocks, loaded.as_of))
        self.assertTrue(loaded.check(message(self.BODY)).ok)
        self.assertEqual((["95.32"], ["TP80"]), alert_lint.load_emitted_document(document))
        plain = alert_lint.load_emitted(["95.32"])
        self.assertEqual((None, None, None, None), (plain.rule_ids, plain.identifiers, plain.clocks, plain.as_of))
        with self.assertRaises(ValueError):
            alert_lint.load_emitted(dict(document, as_of="19:31"))


class FmtFooterKindTest(unittest.TestCase):
    """``alerts.fmt_footer(kind=...)`` prints the footer of each of the lint's three kinds (designer C's D5.8: a
    structuring text must never be closed by the snapshot footer)."""

    def test_each_kind_prints_its_own_footer(self) -> None:
        for kind, build in ((alert_lint.FOOTER_RULES, alert_lint.footer_line),
                            (alert_lint.FOOTER_SNAPSHOT, alert_lint.snapshot_footer_line),
                            (alert_lint.FOOTER_STRUCTURING, alert_lint.structuring_footer_line)):
            with self.subTest(kind=kind):
                emitted: List[str] = []
                clocks: List[str] = []
                self.assertEqual(build(AS_OF), alerts.fmt_footer(AS_OF, 15, emitted, clocks, kind=kind))
                self.assertEqual(([], ["19:31 UTC"]), (emitted, clocks))
                self.assertEqual(build(AS_OF, 20), alerts.fmt_footer(AS_OF, 20, emitted, kind=kind))
                self.assertEqual(["20"], emitted)
        self.assertEqual(alert_lint.footer_line(AS_OF), alerts.fmt_footer(AS_OF, 15, []), "the default is the rules footer")
        with self.assertRaises(ValueError):
            alerts.fmt_footer(AS_OF, 15, [], kind="structured")

    def test_a_structuring_text_closed_by_fmt_footer_passes(self) -> None:
        emitted = emitted_of(LEG_CLOSE)
        clocks: List[str] = []
        text = LEG_CLOSE + "\n" + alerts.fmt_footer(AS_OF, 15, emitted, clocks, kind=alert_lint.FOOTER_STRUCTURING)
        result = alert_lint.check(text, emitted, rule_ids=[], identifiers=["2026-11-20"], clocks=clocks, as_of=AS_OF,
                                  footer=alert_lint.FOOTER_STRUCTURING)
        self.assertTrue(result.ok, result.problems)


if __name__ == "__main__":
    unittest.main()
