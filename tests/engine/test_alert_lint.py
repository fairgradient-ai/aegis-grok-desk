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
import unittest
from typing import List, Optional, Sequence

from aegis_desk import alert_lint

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
                             ("captured 52.10 %", "52.10"), ("Tradier sandbox, 15-min delayed", "15")):
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
        self.assertEqual(7, len(alert_lint.IMPERATIVE_START_WORDS))
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
        self.assertEqual(sorted(PASS_FIXTURES + tuple(fail_fixture_table())), found)
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

if __name__ == "__main__":
    unittest.main()
