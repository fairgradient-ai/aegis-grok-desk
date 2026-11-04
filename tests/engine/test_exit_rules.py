"""``aegis_desk.exit_rules``: the rulebook grammar and its bounds, the metric catalogue read from ``book.json`` (the
addendum's decision 9), the take-profit fresh side (10), history versus data (11), the suppression tags (12),
coverage and pseudo-structures (13), EXDIV as PR 2b's rule (14, with ``expiry.dividend_window`` factored out of
``expiry.dividend_tier``), precision (15), the Kleene maximum (16), Kleene evaluation and verdict precedence, the
confirmations (17(i)), ``book_from_json`` (4), the adoption lint and ``rule_sha``; the example rulebook and the
fixture roots' committed rule files; every decision-3 / decision-17 expectation recomputed with ``book.load_book``
on a copied fixture root. Nothing under ``tests/fixtures/`` is ever written."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import copy
import dataclasses
import datetime
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aegis_desk import (alert_lint, book, calendar_events, contract, exit_rules, expiry, grouping, market_clock,
                        quote_cache, rule_state)

import assignment_watch  # the tool module scripts/assignment_watch.py (the agreement test runs it in-process)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
FIXTURE_DESK_RECON = os.path.join(ROOT, "tests", "fixtures", "desk_recon")
EXAMPLE_RULES = os.path.join(ROOT, "rules", "examples", "exit_rules-1.0.0.toml")
EXAMPLE_README = os.path.join(ROOT, "rules", "examples", "README.md")
CANDIDATE_RULES = os.path.join(ROOT, "tests", "fixtures", "rules", "candidate-1.1.0.toml")
REGULAR_NOW = "2026-11-03T19:50:00Z"
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
T1_NOW = "2026-11-19T19:50:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
AFTER_CLOSE_NEXT_RUN = "2026-11-20T21:35:00Z"
EXAMPLE_IDS = ("TP50", "TP80", "LOSSX", "DTE21", "DELTA_WATCH", "DELTA_SIGNAL", "PIN3", "EXDIV", "CAP", "LAPSE14",
               "ROLL30", "MONETIZE", "FLOOR", "COVER", "AUTOEX", "LTP100", "LLOSS50", "LDTE30", "VTP", "VLOSS",
               "VBETWEEN", "USTOP", "MOVE5_DOWN", "MOVE5_UP", "EARN2", "DAILY", "CONC")
# The screen for the rule files: the lint's own forbidden-pattern table (alert_lint.FORBIDDEN_PATTERNS) plus the two
# words of the desk's no-order list the lint leaves out, assembled from pieces so this module spells no order word.
EXTRA_ORDER_WORDS = ("fi" + "ll", "exe" + "cute")

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9
CALENDAR_TEXT = """schema_version = 1
[symbols.WMT]
ex_dividend = { date = 2026-11-24, amount = 0.2350, status = "confirmed", source = "test", checked_utc = 2026-11-01T12:00:00Z }
[symbols.SPY]
ex_dividend = { date = 2026-12-18, amount = 1.85, status = "estimated", source = "test", checked_utc = 2026-11-01T12:00:00Z }
[symbols.NVDA]
ex_dividend = { status = "none", source = "test", checked_utc = 2026-11-01T12:00:00Z }
[symbols.AMD]
ex_dividend = { status = "unknown", source = "test", checked_utc = 2026-11-01T12:00:00Z }
[symbols.KO]
earnings = { status = "none", source = "test", checked_utc = 2026-11-01T12:00:00Z }
"""


def calendar(text: str = CALENDAR_TEXT) -> calendar_events.CalendarFile:
    return calendar_events.parse_calendar_text(text)


class DividendWindowTest(unittest.TestCase):
    """``expiry.dividend_window``: the dividend facts ``dividend_tier`` grades and EXDIV reads, one function."""

    def window(self, symbol: str, last_day: datetime.date, today: datetime.date = datetime.date(2026, 11, 19),
               cal: object = "default", extra: object = ()) -> expiry.DividendWindow:
        cal_file = calendar() if cal == "default" else cal
        return expiry.dividend_window(symbol, last_day, cal_file, today, extra)  # type: ignore[arg-type]

    def test_cum_day_after_the_last_trading_day_is_not_applicable(self) -> None:
        window = self.window("WMT", datetime.date(2026, 11, 20))
        self.assertEqual((True, False, "confirmed", datetime.date(2026, 11, 24), Decimal("0.2350"),
                          datetime.date(2026, 11, 23), None),
                         (window.known, window.applicable, window.status, window.ex_date, window.amount, window.cum_day,
                          window.trading_days_to_ex))
        self.assertIn("2026-11-20", window.reason)
        self.assertIn("2026-11-23", window.reason)

    def test_cum_day_on_the_last_trading_day_is_graded(self) -> None:
        window = self.window("WMT", datetime.date(2026, 11, 23))
        self.assertEqual((True, True, 3, datetime.date(2026, 11, 23)),
                         (window.known, window.applicable, window.trading_days_to_ex, window.cum_day))

    def test_closures_move_the_cum_day_and_the_count(self) -> None:
        closure = {datetime.date(2026, 11, 23): "calendar closure"}
        window = self.window("WMT", datetime.date(2026, 11, 20), extra=closure)
        self.assertEqual((True, True, 2, datetime.date(2026, 11, 20)),
                         (window.known, window.applicable, window.trading_days_to_ex, window.cum_day))

    def test_unknowns_are_never_zero(self) -> None:
        for symbol, cal, fragment in (("WMT", None, "no research/calendar.toml"),
                                      ("XOM", "default", "XOM is not in calendar.toml"),
                                      ("KO", "default", "no ex_dividend entry for KO"),
                                      ("AMD", "default", 'AMD ex_dividend status "unknown"')):
            window = self.window(symbol, datetime.date(2026, 12, 18), cal=cal)
            self.assertEqual((False, None, None), (window.known, window.amount, window.trading_days_to_ex), symbol)
            self.assertIn(fragment, window.reason)
            self.assertIn(expiry.REASON_UNKNOWN_DIVIDEND, window.reason)

    def test_a_passed_ex_date_is_unknown_with_its_facts_kept(self) -> None:
        window = self.window("WMT", datetime.date(2026, 12, 18), today=datetime.date(2026, 11, 24))
        self.assertEqual((False, "confirmed", datetime.date(2026, 11, 24), Decimal("0.2350")),
                         (window.known, window.status, window.ex_date, window.amount))
        self.assertIn("has passed", window.reason)

    def test_a_confirmed_absence_is_known_and_not_applicable(self) -> None:
        window = self.window("NVDA", datetime.date(2026, 11, 20))
        self.assertEqual((True, False, "none"), (window.known, window.applicable, window.status))

    def test_estimated_dividends_are_graded(self) -> None:
        window = self.window("SPY", datetime.date(2026, 12, 18), today=datetime.date(2026, 12, 14))
        self.assertEqual((True, True, "estimated", 4), (window.known, window.applicable, window.status,
                                                         window.trading_days_to_ex))


# --------------------------------------------------------------------------------------------
# Grammar, bounds and rule_sha
# --------------------------------------------------------------------------------------------

MINIMAL = '''schema_version = 1
rulebook_version = "1.0.0"
title = "test rules"

[defaults]
mark_basis = "liq"
confirmations = 2
confirmation_gap_min = 10
confirmation_window_min = 90
cooldown_min = 240
rearm = "next_trading_day"
hysteresis_pct = 10

[[rule]]
id = "TP50"
title = "Take profit at half the credit"
role = "take_profit"
scope = "group"
applies_to = ["cash_secured_put", "short_call_leg"]
input_class = "M"
when = [
  { metric = "pct_captured", op = ">=", value = 50, unit = "percent" },
  { metric = "dte", op = ">", value = 21, dte_unit = "trading" },
]
verdict = "EXIT_SIGNAL"
plan = "close"
origin = "owner-entered"
source = "test"
'''


def parse(text: str) -> exit_rules.Rulebook:
    return exit_rules.parse_rulebook_text(text, "test.toml")


def problems(text: str) -> List[str]:
    try:
        parse(text)
    except exit_rules.RulebookError as exc:
        return list(exc.problems)
    raise AssertionError("the rulebook loaded")


class GrammarTest(unittest.TestCase):
    def test_minimal_rulebook(self) -> None:
        rulebook = parse(MINIMAL)
        self.assertEqual((1, "1.0.0", "test rules", ("TP50",)),
                         (rulebook.schema_version, rulebook.rulebook_version, rulebook.title, rulebook.rule_ids))
        rule = rulebook.rules[0]
        self.assertEqual(("take_profit", "group", ("cash_secured_put", "short_call_leg"), "M", "liq", 2, 10, 90, 240,
                          "next_trading_day", Decimal("10"), None, False),
                         (rule.role, rule.scope, rule.applies_to, rule.input_class, rule.mark_basis, rule.confirmations,
                          rule.confirmation_gap_min, rule.confirmation_window_min, rule.cooldown_min, rule.rearm,
                          rule.hysteresis_pct, rule.suppress_if_tag, rule.owner_must_set))
        first, second = rule.when
        self.assertEqual(("pct_captured", ">=", Decimal("50"), "percent", None), (first.metric, first.op, first.value,
                                                                                 first.unit, first.dte_unit))
        self.assertEqual(("dte", ">", Decimal("21"), None, "trading"), (second.metric, second.op, second.value,
                                                                        second.unit, second.dte_unit))
        self.assertEqual(hashlib.sha256(MINIMAL.encode("utf-8")).hexdigest(), rulebook.sha256)
        self.assertEqual({"input_class": "M", "confirmations": 2, "confirmation_gap_min": 10,
                          "confirmation_window_min": 90, "rearm": "next_trading_day", "hysteresis_pct": Decimal("10"),
                          "op": ">=", "threshold": Decimal("50"), "cooldown_min": 240}, rule.policy)

    def test_overrides_and_optional_keys(self) -> None:
        text = MINIMAL.replace('source = "test"\n', 'source = "test"\nmark_basis = "mid"\nconfirmations = 3\n'
                               'confirmation_gap_min = 5\nconfirmation_window_min = 60\ncooldown_min = 0\n'
                               'rearm = "hysteresis"\nhysteresis_pct = 25\nsuppress_if_tag = "assign-ok"\n'
                               'owner_must_set = true\n')
        rule = parse(text).rules[0]
        self.assertEqual(("mid", 3, 5, 60, 0, "hysteresis", Decimal("25"), "assign-ok", True),
                         (rule.mark_basis, rule.confirmations, rule.confirmation_gap_min, rule.confirmation_window_min,
                          rule.cooldown_min, rule.rearm, rule.hysteresis_pct, rule.suppress_if_tag, rule.owner_must_set))

    def test_unknown_keys_and_sections_are_errors(self) -> None:
        self.assertTrue(any("unknown key 'colour'" in item for item in problems(MINIMAL + 'colour = "red"\n')))
        self.assertTrue(any("short_premium" in item
                            for item in problems(MINIMAL + '\n[short_premium]\nx = 1\n')))
        text = MINIMAL.replace('unit = "percent" }', 'unit = "percent", note = "x" }')
        self.assertTrue(any("unknown key 'note'" in item for item in problems(text)))
        text = MINIMAL.replace("hysteresis_pct = 10\n", "hysteresis_pct = 10\nspeed = 1\n", 1)
        self.assertTrue(any("unknown key 'speed'" in item for item in problems(text)))

    def test_required_sections_and_keys(self) -> None:
        start = MINIMAL.index("[defaults]")
        end = MINIMAL.index("[[rule]]")
        no_defaults = MINIMAL[:start] + MINIMAL[end:]
        with self.assertRaises(exit_rules.RulebookError) as caught:
            parse(no_defaults)
        self.assertEqual("config_invalid", caught.exception.code)
        self.assertTrue(any("[defaults]" in item for item in caught.exception.problems))
        self.assertTrue(any("cooldown_min" in item for item in problems(MINIMAL.replace("cooldown_min = 240\n", "", 1))))
        self.assertTrue(any("at least one [[rule]]" in item for item in problems(MINIMAL[:end])))
        when_start = MINIMAL.index("when = [")
        when_end = MINIMAL.index("]\n", when_start) + 2
        for key in ("title", "role", "scope", "applies_to", "input_class", "when", "verdict", "plan", "origin", "source"):
            if key == "title":
                text = MINIMAL.replace('title = "Take profit at half the credit"\n', "")
            elif key == "when":
                text = MINIMAL[:when_start] + MINIMAL[when_end:]
            else:
                text = "".join(line for line in MINIMAL.splitlines(True) if not line.startswith(key + " = "))
            found = problems(text)
            self.assertTrue(any("missing" in item and key in item for item in found), (key, found))
        self.assertTrue(any("schema_version" in item for item in problems(MINIMAL.replace("schema_version = 1", "schema_version = 2"))))

    def test_toml_errors_are_rulebook_errors(self) -> None:
        with self.assertRaises(exit_rules.RulebookError) as caught:
            parse(MINIMAL + "id = \n")
        self.assertEqual("config_invalid", caught.exception.code)
        self.assertIn("test.toml", str(caught.exception))

    def test_bounds_in_the_safe_direction(self) -> None:
        cases = (("confirmations = 2", "confirmations = 0"), ("confirmations = 2", "confirmations = 4"),
                 ("confirmation_gap_min = 10", "confirmation_gap_min = 0"),
                 ("confirmation_gap_min = 10", "confirmation_gap_min = 61"),
                 ("confirmation_window_min = 90", "confirmation_window_min = 5"),
                 ("confirmation_window_min = 90", "confirmation_window_min = 241"),
                 ("cooldown_min = 240", "cooldown_min = -1"), ("cooldown_min = 240", "cooldown_min = 1441"),
                 ("hysteresis_pct = 10", "hysteresis_pct = 51"), ("hysteresis_pct = 10", "hysteresis_pct = -1"),
                 ('rearm = "next_trading_day"', 'rearm = "never"'), ('mark_basis = "liq"', 'mark_basis = "last"'),
                 ("confirmations = 2", "confirmations = 2.0"), ("confirmations = 2", "confirmations = true"))
        for old, new in cases:
            self.assertTrue(problems(MINIMAL.replace(old, new, 1)), new)
        override = MINIMAL.replace('source = "test"\n', 'source = "test"\nconfirmation_window_min = 8\n')
        self.assertTrue(any("confirmation_window_min" in item for item in problems(override)),
                        "the window may not be shorter than the gap")
        self.assertEqual(Decimal("0"), parse(MINIMAL.replace("hysteresis_pct = 10", "hysteresis_pct = 0")).defaults.hysteresis_pct)
        self.assertEqual(Decimal("50"), parse(MINIMAL.replace("hysteresis_pct = 10", "hysteresis_pct = 50.0")).defaults.hysteresis_pct)

    def test_rule_fields_are_checked(self) -> None:
        cases = (('id = "TP50"', 'id = "tp50"'), ('id = "TP50"', 'id = "T"'), ('id = "TP50"', 'id = "TP50_IS_MUCH_TOO_LONG"'),
                 ('role = "take_profit"', 'role = "greed"'), ('scope = "group"', 'scope = "account"'),
                 ('input_class = "M"', 'input_class = "X"'), ('verdict = "EXIT_SIGNAL"', 'verdict = "HOLD"'),
                 ('plan = "close"', 'plan = "roll_to_next_month"'), ('origin = "owner-entered"', 'origin = "mine"'),
                 ('source = "test"', 'source = ""'), ('"short_call_leg"]', '"short_call_leg", "short_call_leg"]'),
                 ('"short_call_leg"]', '"short_strangle"]'), ('"short_call_leg"]', '"invalid"]'),
                 ('applies_to = ["cash_secured_put", "short_call_leg"]', "applies_to = []"))
        for old, new in cases:
            self.assertTrue(problems(MINIMAL.replace(old, new, 1)), new)
        duplicate = MINIMAL + MINIMAL[MINIMAL.index("[[rule]]"):]
        self.assertTrue(any("duplicate rule id 'TP50'" in item for item in problems(duplicate)))
        tag = MINIMAL.replace('source = "test"\n', 'source = "test"\nsuppress_if_tag = "horizon"\n')
        self.assertTrue(any("suppress_if_tag" in item for item in problems(tag)))

    def test_conditions_are_checked(self) -> None:
        first = '{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" }'
        cases = ('{ metric = "pct_gained", op = ">=", value = 50, unit = "percent" }',
                 '{ metric = "pct_captured", op = "=>", value = 50, unit = "percent" }',
                 '{ metric = "pct_captured", op = ">=", value = 50 }',
                 '{ metric = "pct_captured", op = ">=", value = 50, unit = "usd" }',
                 '{ metric = "pct_captured", op = ">=", unit = "percent" }',
                 '{ metric = "pct_captured", op = ">=", value = true, unit = "percent" }',
                 '{ metric = "pct_captured", op = ">=", value = "50", unit = "percent" }',
                 '{ metric = "pct_captured", op = "is_true", unit = "percent" }',
                 '{ metric = "pct_captured", op = ">=", value = 50, unit = "percent", dte_unit = "trading" }')
        for case in cases:
            self.assertTrue(problems(MINIMAL.replace(first, case, 1)), case)
        second = '{ metric = "dte", op = ">", value = 21, dte_unit = "trading" }'
        for case in ('{ metric = "dte", op = ">", value = 21 }', '{ metric = "dte", op = ">", value = 21, dte_unit = "weeks" }',
                     '{ metric = "dte", op = ">", value = 21, dte_unit = "trading", unit = "percent" }'):
            self.assertTrue(problems(MINIMAL.replace(second, case, 1)), case)
        days = parse(MINIMAL.replace(second, '{ metric = "dte", op = ">", value = 21, dte_unit = "trading", unit = "days" }'))
        self.assertEqual("days", days.rules[0].when[1].unit, "an optional unit must match the catalogue when given")
        self.assertTrue(problems(MINIMAL.replace("when = [", "when = []\nxwhen = [")))

    def test_booleans_take_is_true_or_is_false_only(self) -> None:
        text = MINIMAL.replace('applies_to = ["cash_secured_put", "short_call_leg"]', 'applies_to = ["covered_call"]')
        text = text.replace('role = "take_profit"', 'role = "risk"')
        good = text.replace('{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" }',
                            '{ metric = "stock_above_short_call", op = "is_true" }')
        self.assertEqual("is_true", parse(good).rules[0].when[0].op)
        for bad in ('{ metric = "stock_above_short_call", op = ">=", value = 1 }',
                    '{ metric = "stock_above_short_call", op = "is_true", value = true }',
                    '{ metric = "stock_above_short_call", op = "is_false", unit = "percent" }'):
            self.assertTrue(problems(text.replace('{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" }',
                                                  bad)), bad)

    def test_scope_and_metric_agreement(self) -> None:
        under = MINIMAL.replace('scope = "group"', 'scope = "underlying"')
        self.assertTrue(any("any_underlying" in item for item in problems(under)))
        portfolio = MINIMAL.replace('scope = "group"', 'scope = "portfolio"').replace(
            'applies_to = ["cash_secured_put", "short_call_leg"]', 'applies_to = ["portfolio"]')
        self.assertTrue(any("pct_captured" in item for item in problems(portfolio)), "a group metric on the portfolio")
        leg = MINIMAL.replace('scope = "group"', 'scope = "leg"').replace(
            'applies_to = ["cash_secured_put", "short_call_leg"]', 'applies_to = ["any_group"]')
        self.assertTrue(any("any_group" in item for item in problems(leg)))
        delta = MINIMAL.replace('{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" }',
                                '{ metric = "abs_delta", op = ">=", value = 0.30 }').replace('input_class = "M"', 'input_class = "G"')
        self.assertTrue(any("abs_delta" in item for item in problems(delta)), "a leg metric on a group subject")
        watch = MINIMAL.replace('scope = "group"', 'scope = "portfolio"').replace(
            'applies_to = ["cash_secured_put", "short_call_leg"]', 'applies_to = ["portfolio"]').replace(
            '{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" },\n  { metric = "dte", op = ">", value = 21, dte_unit = "trading" },',
            '{ metric = "day_pl", op = "<=", value = -1000, unit = "usd" },')
        self.assertTrue(any("WATCH" in item for item in problems(watch)), "a section rule never sets a group verdict")
        self.assertEqual("WATCH", parse(watch.replace('verdict = "EXIT_SIGNAL"', 'verdict = "WATCH"')).rules[0].verdict)

    def test_input_class_floor(self) -> None:
        found = problems(MINIMAL.replace('input_class = "M"', 'input_class = "E"'))
        self.assertTrue(any("floor" in item and "pct_captured" in item for item in found))
        self.assertEqual("G", parse(MINIMAL.replace('input_class = "M"', 'input_class = "G"')).rules[0].input_class)

    def test_version_token(self) -> None:
        self.assertTrue(problems(MINIMAL.replace('rulebook_version = "1.0.0"', 'rulebook_version = "v1.0.0"')))
        self.assertTrue(problems(MINIMAL.replace('rulebook_version = "1.0.0"', 'rulebook_version = "1.0"')))

    def test_every_problem_is_reported_at_once(self) -> None:
        text = MINIMAL.replace('role = "take_profit"', 'role = "greed"').replace('plan = "close"', 'plan = "later"')
        found = problems(text)
        self.assertTrue(any("role" in item for item in found) and any("plan" in item for item in found))

    def test_load_rulebook_reads_bytes_and_hashes_them(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-rules-") as tmp:
            path = os.path.join(tmp, "exit_rules-1.0.0.toml")
            with open(path, "wb") as handle:
                handle.write(MINIMAL.encode("utf-8"))
            rulebook = exit_rules.load_rulebook(path)
            self.assertEqual((path, hashlib.sha256(MINIMAL.encode("utf-8")).hexdigest()), (rulebook.path, rulebook.sha256))
            with self.assertRaises(exit_rules.RulebookError) as caught:
                exit_rules.load_rulebook(os.path.join(tmp, "absent.toml"))
            self.assertEqual("rules_missing", caught.exception.code)
            with open(path, "wb") as handle:
                handle.write(b"\xff\xfe")
            with self.assertRaises(exit_rules.RulebookError):
                exit_rules.load_rulebook(path)


class RuleShaTest(unittest.TestCase):
    def test_semantics_change_the_sha_documentation_does_not(self) -> None:
        base = parse(MINIMAL).rules[0]
        sha = exit_rules.rule_sha(base)
        self.assertRegex(sha, "^[0-9a-f]{64}$")
        same = (MINIMAL.replace('title = "Take profit at half the credit"', 'title = "Half"')
                .replace('origin = "owner-entered"', 'origin = "example-accepted"').replace('source = "test"', 'source = "x"')
                .replace("value = 50,", "value = 50.0,").replace('["cash_secured_put", "short_call_leg"]',
                                                                 '["short_call_leg", "cash_secured_put"]'))
        self.assertEqual(sha, exit_rules.rule_sha(parse(same).rules[0]))
        for old, new in (("value = 50,", "value = 60,"), ('op = ">="', 'op = ">"'), ('plan = "close"', 'plan = "hold_review"'),
                         ("confirmations = 2", "confirmations = 3"), ('dte_unit = "trading"', 'dte_unit = "calendar"')):
            self.assertNotEqual(sha, exit_rules.rule_sha(parse(MINIMAL.replace(old, new, 1)).rules[0]), new)
        self.assertEqual({"TP50": sha}, parse(MINIMAL).rules_sha)

    def test_canonical_form(self) -> None:
        canonical = exit_rules.rule_canonical(parse(MINIMAL).rules[0])
        self.assertEqual(["applies_to", "confirmation_gap_min", "confirmation_window_min", "confirmations", "cooldown_min",
                          "hysteresis_pct", "id", "input_class", "mark_basis", "plan", "rearm", "role", "scope",
                          "suppress_if_tag", "verdict", "when"], sorted(canonical))
        self.assertEqual([{"dte_unit": None, "metric": "pct_captured", "op": ">=", "unit": "percent", "value": "50"},
                          {"dte_unit": "trading", "metric": "dte", "op": ">", "unit": None, "value": "21"}],
                         canonical["when"])


# --------------------------------------------------------------------------------------------
# Synthetic books through the real book pipeline (positions text -> grouping -> quotes -> build_book)
# --------------------------------------------------------------------------------------------

from aegis_desk import desk_config, desk_paths, quote_qc  # noqa: E402
from aegis_desk.grouping import group_positions  # noqa: E402
from aegis_desk.market_types import Quote  # noqa: E402
from aegis_desk.positions_csv import parse_positions_text  # noqa: E402

HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
          "close_price_per_unit,underlying_stop,note")
ACCOUNT = "Brokerage (margin)"
CFG = desk_config.load_desk_config(os.path.join(FIXTURE_DESK, "desk.toml"))
PATHS = desk_paths.for_root(FIXTURE_DESK)  # build_book reads nothing from it
with open(os.path.join(FIXTURE_DESK, "research", "calendar.toml"), encoding="utf-8") as _handle:
    CAL_TEXT = _handle.read()
CAL = calendar_events.parse_calendar_text(CAL_TEXT, "calendar.toml")
NOW = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC)
T1 = datetime.datetime(2026, 11, 19, 19, 50, tzinfo=UTC)
EXPIRY = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)
PREV = datetime.datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
FIXTURE_RULES = os.path.join(FIXTURE_DESK, "rules", "exit_rules-1.0.0.toml")


def stamp_of(now: datetime.datetime) -> datetime.datetime:
    """The fixture sets' quote stamp: 18 min 48 s before ``now`` (19:31:12Z for 19:50Z), or the close's last
    quote for a run after the close."""
    session = market_clock.session_at(now)
    if session.session == "closed" and session.close_utc is not None:
        return session.close_utc - datetime.timedelta(seconds=5)
    return now - datetime.timedelta(minutes=18, seconds=48)


def option(symbol: str, occ_symbol: str, quantity: int, trade_id: str, entry: str, opened: str = "2026-11-01",
           note: str = "", stop: str = "") -> str:
    return "option,%s,%s,%d,,,,%s,%s,%s,,,%s,open,,,,%s,%s" % (symbol, occ_symbol, quantity, ACCOUNT, trade_id, opened,
                                                              entry, stop, note)


def stock(symbol: str, quantity: int, trade_id: str, per_unit: str, opened: str = "2026-11-01", stop: str = "",
          note: str = "", kind: str = "equity") -> str:
    return "%s,%s,,%d,,,,%s,%s,%s,,%s,,open,,,,%s,%s" % (kind, symbol, quantity, ACCOUNT, trade_id, opened, per_unit, stop,
                                                       note)


class Scenario:
    """A synthetic book: the document, the rows (for tags), the session and the calendar."""

    def __init__(self, lines: Sequence[str], prices: Dict[str, Tuple[Optional[str], Optional[str]]],
                 now: datetime.datetime = NOW, calendar: Optional[calendar_events.CalendarFile] = CAL,
                 stamps: Optional[Dict[str, Any]] = None, prev: Optional[Dict[str, Any]] = None) -> None:
        extra = calendar_events.extra_closures(calendar)
        self.now = now
        self.calendar = calendar
        self.session = market_clock.session_at(now, extra_holidays=extra)
        prior = market_clock.previous_close_utc(now, extra_holidays=extra)
        positions = parse_positions_text("\n".join([HEADER] + list(lines)) + "\n", path="positions.csv",
                                         today=self.session.et_date)
        self.rows = positions.rows
        groups = group_positions(positions.rows)
        stamp = stamp_of(now)
        quotes: Dict[str, Quote] = {}
        for symbol, (bid, ask) in prices.items():
            when = (stamps or {}).get(symbol, stamp)
            bid_when, ask_when = when if isinstance(when, tuple) else (when, when)
            quotes[symbol] = Quote(symbol=symbol, bid=None if bid is None else Decimal(bid),
                                   ask=None if ask is None else Decimal(ask), bid_date=bid_when, ask_date=ask_when)
        qcs = {symbol: quote_qc.assess(quote, now=now, session=self.session, bound_min=25, prior_close_utc=prior)
               for symbol, quote in quotes.items()}
        built = book.build_book(now=now, cfg=CFG, paths=PATHS, positions=positions, groups=groups, quotes=quotes, qcs=qcs,
                                calendar=calendar, prev_snapshot=prev, session=self.session, prior_close_utc=prior,
                                bound_min=25, receipt_utc=now,
                                prev_snapshot_date=market_clock.previous_trading_day(self.session.et_date, extra))
        self.document = book.book_to_json(built)

    def evaluate(self, rulebook: Optional[exit_rules.Rulebook] = None, single: bool = True,
                 state: Optional[Dict[str, Any]] = None, with_rows: bool = True) -> exit_rules.Evaluation:
        return exit_rules.evaluate(rulebook or ADOPTED_RULES(), self.document, now=self.now, session=self.session,
                                   calendar=self.calendar, state=state if state is not None else {},
                                   next_run_utc=(self.now + datetime.timedelta(hours=6)) if single else None,
                                   rows=self.rows if with_rows else None)

    def leg(self, occ_symbol: str) -> Dict[str, Any]:
        return next(leg for leg in self.document["legs"] if (leg["occ"] or leg["symbol"]) == occ_symbol)

    def group(self, group_id: str) -> Dict[str, Any]:
        return next(group for group in self.document["groups"] if group["group_id"] == group_id)


_RULES_CACHE: Dict[str, exit_rules.Rulebook] = {}


def ADOPTED_RULES() -> exit_rules.Rulebook:  # noqa: N802 - a constant computed once
    if "adopted" not in _RULES_CACHE:
        _RULES_CACHE["adopted"] = exit_rules.load_rulebook(FIXTURE_RULES)
    return _RULES_CACHE["adopted"]


def adopted_variant(*replacements: Tuple[str, str]) -> exit_rules.Rulebook:
    with open(FIXTURE_RULES, encoding="utf-8") as handle:
        text = handle.read()
    for old, new in replacements:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return exit_rules.parse_rulebook_text(text, "variant.toml")


def all_results(evaluation: exit_rules.Evaluation) -> Dict[Tuple[str, str], exit_rules.RuleResult]:
    out: Dict[Tuple[str, str], exit_rules.RuleResult] = {}
    for verdict in evaluation.groups:
        for result in (verdict.fired + verdict.unavailable + verdict.pending + verdict.clear + verdict.suppressed
                       + verdict.no_history + verdict.cooldown):
            out[(verdict.group_id, result.rule_id)] = result
    for section in evaluation.underlyings + [evaluation.portfolio]:
        for result in (section.fired + section.unavailable + section.no_history + section.pending + section.clear
                       + section.suppressed + section.cooldown):
            out[(section.subject, result.rule_id)] = result
    return out


def result_of(evaluation: exit_rules.Evaluation, subject: str, rule_id: str) -> exit_rules.RuleResult:
    found = all_results(evaluation)
    if (subject, rule_id) not in found:
        raise AssertionError("no %s result on %s: %s" % (rule_id, subject, sorted(found)))
    return found[(subject, rule_id)]


def verdict_of(evaluation: exit_rules.Evaluation, group_id: str) -> exit_rules.GroupVerdict:
    for verdict in evaluation.groups:
        if verdict.group_id == group_id:
            return verdict
    raise AssertionError("no verdict for %s" % group_id)


def ids(results: Sequence[exit_rules.RuleResult]) -> List[str]:
    return sorted(result.rule_id for result in results)


WMT_PUT_95 = "WMT261218P00095000"
CSP = [option("WMT", WMT_PUT_95, -1, "AG-20261101-11", "2.00")]


def csp(bid: Optional[str], ask: Optional[str], now: datetime.datetime = NOW, **kwargs: Any) -> Scenario:
    return Scenario(CSP, {"WMT": ("104.20", "104.26"), WMT_PUT_95: (bid, ask)}, now=now, **kwargs)


class KleeneTest(unittest.TestCase):
    def test_conjunction_and_disjunction_truth_tables(self) -> None:
        T, F, U = exit_rules.TRUE, exit_rules.FALSE, exit_rules.UNKNOWN
        conj = {(T, T): T, (T, F): F, (T, U): U, (F, F): F, (F, U): F, (U, U): U}
        for (a, b), expected in conj.items():
            self.assertEqual(expected, exit_rules.kleene_and([a, b]), (a, b))
            self.assertEqual(expected, exit_rules.kleene_and([b, a]), (b, a))
        disj = {(T, T): T, (T, F): T, (T, U): T, (F, F): F, (F, U): U, (U, U): U}
        for (a, b), expected in disj.items():
            self.assertEqual(expected, exit_rules.kleene_or([a, b]), (a, b))
            self.assertEqual(expected, exit_rules.kleene_or([b, a]), (b, a))
        self.assertEqual(T, exit_rules.kleene_and([]))
        self.assertEqual(F, exit_rules.kleene_or([]))

    def test_kleene_maximum(self) -> None:
        maximum = exit_rules.kleene_maximum
        values = [("SPY", Decimal("26.17")), ("NVDA", Decimal("12.54")), ("WMT", None)]
        self.assertEqual(exit_rules.TRUE, maximum(values, ">=", Decimal("25"))[0], "any known value reaches 25")
        self.assertEqual(exit_rules.UNKNOWN, maximum(values, ">=", Decimal("30"))[0], "WMT unknown may reach 30")
        known = [("SPY", Decimal("26.32")), ("NVDA", Decimal("12.17")), ("WMT", Decimal("8.61"))]
        self.assertEqual(exit_rules.FALSE, maximum(known, ">=", Decimal("30"))[0])
        self.assertEqual((exit_rules.TRUE, Decimal("26.32"), "SPY"), maximum(known, ">=", Decimal("25")))
        self.assertEqual(exit_rules.FALSE, maximum(values, "<=", Decimal("25"))[0], "SPY above 25: the maximum is too")
        self.assertEqual(exit_rules.UNKNOWN, maximum(values, "<=", Decimal("30"))[0])
        self.assertEqual(exit_rules.TRUE, maximum(known, "<", Decimal("30"))[0])
        self.assertEqual(exit_rules.UNKNOWN, maximum([("WMT", None)], ">=", Decimal("25"))[0])
        self.assertEqual(exit_rules.FALSE, maximum([], ">=", Decimal("25"))[0])
        self.assertEqual(exit_rules.FALSE, maximum(values, "==", Decimal("20"))[0], "a known 26.17 exceeds 20")


class GroupHistoryTest(unittest.TestCase):
    def test_a_group_rule_without_history_never_sets_the_verdict(self) -> None:
        """Decision 11 by construction: a group-scope day P/L reading is not in the catalogue, so a stub metric reads
        history for the WMT stock group; the verdict stays HOLD and the result sits in no_history."""
        scenario = Scenario([stock("WMT", 100, "AG-20261101-81", "100.00")], {"WMT": ("104.20", "104.26")})
        original = exit_rules._METRIC_READERS["spot_vs_stop_pct"]
        try:
            exit_rules._METRIC_READERS["spot_vs_stop_pct"] = lambda context, subject, condition: exit_rules._no_history(
                "test: no previous snapshot")
            evaluation = scenario.evaluate()
        finally:
            exit_rules._METRIC_READERS["spot_vs_stop_pct"] = original
        verdict = verdict_of(evaluation, "AG-20261101-81")
        self.assertEqual(("HOLD", [], ["USTOP"]), (verdict.verdict, ids(verdict.unavailable), ids(verdict.no_history)))
        self.assertIn("USTOP", evaluation.no_history_rule_ids)
        self.assertEqual(0, evaluation.exit_code())


class PrecedenceTest(unittest.TestCase):
    def test_exit_signal_unavailable_watch_hold(self) -> None:
        verdict = exit_rules.group_verdict
        self.assertEqual("EXIT_SIGNAL", verdict(["WATCH", "EXIT_SIGNAL"], True))
        self.assertEqual("UNAVAILABLE", verdict(["WATCH"], True))
        self.assertEqual("WATCH", verdict(["WATCH"], False))
        self.assertEqual("HOLD", verdict([], False))
        self.assertEqual("UNAVAILABLE", verdict([], True), "HOLD is never printed while a rule is unavailable")
        self.assertEqual(("EXIT_SIGNAL", "UNAVAILABLE", "WATCH", "HOLD"), exit_rules.GROUP_VERDICTS)


class CreditStructureTest(unittest.TestCase):
    """TP50, TP80, LOSSX, DTE21, DELTA_*, PIN3 on cash-secured puts (decisions 9, 10, 15)."""

    def test_take_profit_at_half_the_credit_equals_the_books_liquidation_figure(self) -> None:
        scenario = csp("0.95", "1.00")
        evaluation = scenario.evaluate()
        group = scenario.group("AG-20261101-11")
        tp50 = result_of(evaluation, "AG-20261101-11", "TP50")
        self.assertEqual(("fired", Decimal("50.00"), Decimal("50"), "percent", "pct_captured"),
                         (tp50.result, tp50.value, tp50.threshold, tp50.unit, tp50.metric))
        self.assertEqual(group["pct_captured_liq"], tp50.value, "every open leg mid-marked: the book's figure")
        self.assertEqual(Decimal("32"), Decimal(tp50.conditions[1].value), "32 trading days to Fri 2026-12-18")
        self.assertEqual("clear", result_of(evaluation, "AG-20261101-11", "TP80").result)
        lossx = result_of(evaluation, "AG-20261101-11", "LOSSX")
        self.assertEqual(("clear", Decimal("-0.50")), (lossx.result, lossx.value), "unrealized +100 on a 200 credit")
        self.assertEqual("clear", result_of(evaluation, "AG-20261101-11", "DTE21").result)
        pin = result_of(evaluation, "AG-20261101-11", "PIN3")
        self.assertEqual(("clear", Decimal("8.86")), (pin.result, pin.value), "|104.23 - 95| / 104.23 x 100")
        self.assertEqual("EXIT_SIGNAL", verdict_of(evaluation, "AG-20261101-11").verdict)
        self.assertEqual(10, evaluation.exit_code())
        names = [item.name for item in tp50.inputs]
        self.assertIn("fresh_ask", names)
        self.assertEqual("C", tp50.quote_grade)

    def test_take_profit_at_eighty_percent(self) -> None:
        evaluation = csp("0.35", "0.40").evaluate()
        tp80 = result_of(evaluation, "AG-20261101-11", "TP80")
        self.assertEqual(("fired", Decimal("80.00")), (tp80.result, tp80.value))

    def test_take_profit_unavailable_on_a_stale_quote(self) -> None:
        scenario = csp("0.35", "0.40", stamps={WMT_PUT_95: NOW - datetime.timedelta(minutes=40)})
        self.assertEqual("F", scenario.leg(WMT_PUT_95)["quote_grade"])
        evaluation = scenario.evaluate()
        for rule_id in ("TP50", "TP80", "LOSSX"):
            self.assertEqual("unavailable", result_of(evaluation, "AG-20261101-11", rule_id).result, rule_id)
        self.assertIn("no fresh ask", result_of(evaluation, "AG-20261101-11", "TP80").reason)
        self.assertEqual("UNAVAILABLE", verdict_of(evaluation, "AG-20261101-11").verdict)
        self.assertEqual(20, evaluation.exit_code())

    def test_loss_at_two_times_the_credit(self) -> None:
        evaluation = csp("5.90", "6.00").evaluate()
        lossx = result_of(evaluation, "AG-20261101-11", "LOSSX")
        self.assertEqual(("fired", Decimal("2.00"), Decimal("2"), "multiple"), (lossx.result, lossx.value, lossx.threshold,
                                                                                lossx.unit))
        self.assertEqual("EXIT_SIGNAL", verdict_of(evaluation, "AG-20261101-11").verdict)

    def test_a_wide_quote_delays_the_take_profit_and_blocks_the_loss_test(self) -> None:
        scenario = csp("0.10", "0.40")
        leg = scenario.leg(WMT_PUT_95)
        self.assertEqual(("E", None, Decimal("0.40"), "unavailable"),
                         (leg["quote_grade"], leg["mark_liq"], leg["fresh_ask"], leg["mark_source"]))
        evaluation = scenario.evaluate()
        tp80 = result_of(evaluation, "AG-20261101-11", "TP80")
        self.assertEqual(("fired", Decimal("80.00")), (tp80.result, tp80.value), "(200 - 40) / 200 at the fresh ask")
        self.assertIn("quote grade E: the conservative side stands", tp80.notes)
        lossx = result_of(evaluation, "AG-20261101-11", "LOSSX")
        self.assertEqual("unavailable", lossx.result)
        self.assertIn("quote grade E: a loss test needs a two-sided fresh quote", lossx.reason)
        verdict = verdict_of(evaluation, "AG-20261101-11")
        self.assertEqual("EXIT_SIGNAL", verdict.verdict, "EXIT_SIGNAL outranks UNAVAILABLE")
        self.assertIn("LOSSX", ids(verdict.unavailable), "every unavailable rule is still listed")
        mid = adopted_variant(('id = "TP80"\ntitle = "Take profit at 80 percent of the credit"\n',
                               'id = "TP80"\ntitle = "Take profit at 80 percent of the credit"\nmark_basis = "mid"\n'))
        self.assertEqual("unavailable", result_of(scenario.evaluate(mid), "AG-20261101-11", "TP80").result,
                         "the mid basis is gated on mark_source mid")

    def test_delta_watch_and_signal_on_the_short_leg(self) -> None:
        scenario = Scenario([option("WMT", "WMT261218P00120000", -1, "AG-20261101-12", "15.00")],
                            {"WMT": ("104.20", "104.26"), "WMT261218P00120000": ("16.00", "16.20")})
        delta = scenario.leg("WMT261218P00120000")["greeks"]["delta"]
        expected = Decimal(repr(abs(delta))).quantize(Decimal("0.01"))
        evaluation = scenario.evaluate()
        for rule_id in ("DELTA_WATCH", "DELTA_SIGNAL"):
            result = result_of(evaluation, "AG-20261101-12", rule_id)
            self.assertEqual(("fired", expected), (result.result, result.value), rule_id)
        self.assertEqual(Decimal("0.86"), expected)
        far = Scenario([option("WMT", "WMT261218P00080000", -1, "AG-20261101-13", "0.50")],
                       {"WMT": ("104.20", "104.26"), "WMT261218P00080000": ("0.10", "0.12")}).evaluate()
        self.assertEqual(("clear", Decimal("0.02")), (result_of(far, "AG-20261101-13", "DELTA_WATCH").result,
                                                      result_of(far, "AG-20261101-13", "DELTA_WATCH").value))
        wide = csp("0.10", "0.40").evaluate()
        self.assertEqual("unavailable", result_of(wide, "AG-20261101-11", "DELTA_WATCH").result)
        self.assertIn("iv_status", result_of(wide, "AG-20261101-11", "DELTA_WATCH").reason)

    def test_dte21_and_pin3_near_expiry(self) -> None:
        put = "WMT261120P00106000"
        scenario = Scenario([option("WMT", put, -1, "AG-20261101-14", "2.00")],
                            {"WMT": ("106.40", "106.46"), put: ("0.90", "1.00")}, now=T1)
        evaluation = scenario.evaluate()
        self.assertEqual(("fired", 1), (result_of(evaluation, "AG-20261101-14", "DTE21").result,
                                        result_of(evaluation, "AG-20261101-14", "DTE21").value))
        pin = result_of(evaluation, "AG-20261101-14", "PIN3")
        self.assertEqual(("fired", Decimal("0.40")), (pin.result, pin.value), "|106.43 - 106| / 106.43 x 100 = 0.404")
        stale = Scenario([option("WMT", put, -1, "AG-20261101-14", "2.00")],
                         {"WMT": ("106.40", "106.46"), put: ("0.90", "1.00")}, now=T1,
                         stamps={"WMT": T1 - datetime.timedelta(minutes=40)}).evaluate()
        self.assertEqual("unavailable", result_of(stale, "AG-20261101-14", "PIN3").result)
        self.assertIn("underlying WMT", result_of(stale, "AG-20261101-14", "PIN3").reason)


class ZeroAskTest(unittest.TestCase):
    """Decision 10: a zero ask is no offer, whatever the grade; a long leg's zero bid stays usable."""

    CALL = "NVDA261218C00250000"
    LINES = [option("NVDA", CALL, -1, "AG-20261101-31", "0.50")]

    def test_zero_zero_quote_graded_e(self) -> None:
        scenario = Scenario(self.LINES, {"NVDA": ("148.90", "148.95"), self.CALL: ("0.00", "0.00")})
        leg = scenario.leg(self.CALL)
        self.assertEqual(("E", Decimal("0.00"), Decimal("0.00")), (leg["quote_grade"], leg["fresh_bid"], leg["fresh_ask"]))
        tp80 = result_of(scenario.evaluate(), "AG-20261101-31", "TP80")
        self.assertEqual("unavailable", tp80.result)
        self.assertIn("quote grade E: no offer (ask 0)", tp80.reason)

    def test_stale_zero_bid_beside_a_fresh_zero_ask_graded_d(self) -> None:
        stale = NOW - datetime.timedelta(minutes=40)
        scenario = Scenario(self.LINES, {"NVDA": ("148.90", "148.95"), self.CALL: ("0.00", "0.00")},
                            stamps={self.CALL: (stale, stamp_of(NOW))})
        leg = scenario.leg(self.CALL)
        self.assertEqual(("D", "one_side_stale", None, Decimal("0.00")),
                         (leg["quote_grade"], leg["quote_status"], leg["fresh_bid"], leg["fresh_ask"]))
        tp80 = result_of(scenario.evaluate(), "AG-20261101-31", "TP80")
        self.assertEqual("unavailable", tp80.result)
        self.assertIn("quote grade D: no offer (ask 0)", tp80.reason)

    def test_a_long_legs_zero_bid_reads_the_leg_as_worthless(self) -> None:
        long_call, short_call = "NVDA261218C00200000", "NVDA261218C00210000"
        scenario = Scenario([option("NVDA", long_call, 1, "AG-20261101-32", "1.00"),
                             option("NVDA", short_call, -1, "AG-20261101-32", "0.40")],
                            {"NVDA": ("148.90", "148.95"), long_call: ("0.00", "0.05"), short_call: ("0.00", "0.05")})
        self.assertEqual("vertical_debit_call", scenario.group("AG-20261101-32")["structure"])
        vtp = result_of(scenario.evaluate(), "AG-20261101-32", "VTP")
        self.assertEqual(("clear", Decimal("-6.91")), (vtp.result, vtp.value),
                         "(0 x 100 - 0.05 x 100) - 60 = -65 over a max profit of 940")


class VerticalTest(unittest.TestCase):
    def test_credit_vertical_take_profit_equals_the_book(self) -> None:
        short_put, long_put = "SPY261218P00620000", "SPY261218P00600000"
        scenario = Scenario([option("SPY", short_put, -1, "AG-20261101-41", "11.30"),
                             option("SPY", long_put, 1, "AG-20261101-41", "6.00")],
                            {"SPY": ("648.10", "648.14"), short_put: ("9.80", "10.10"), long_put: ("5.20", "5.40")})
        group = scenario.group("AG-20261101-41")
        self.assertEqual("vertical_credit_put", group["structure"])
        tp50 = result_of(scenario.evaluate(), "AG-20261101-41", "TP50")
        self.assertEqual((Decimal("7.55"), group["pct_captured_liq"]), (tp50.value, tp50.value),
                         "(530 - (1010 - 520)) / 530 x 100")

    def test_debit_vertical_take_profit_and_loss(self) -> None:
        long_call, short_call = "NVDA261218C00150000", "NVDA261218C00160000"
        lines = [option("NVDA", long_call, 1, "AG-20261101-42", "5.00"), option("NVDA", short_call, -1, "AG-20261101-42", "2.00")]
        winning = Scenario(lines, {"NVDA": ("148.90", "148.95"), long_call: ("12.40", "12.60"), short_call: ("4.80", "5.00")})
        evaluation = winning.evaluate()
        self.assertEqual(("fired", Decimal("62.86")), (result_of(evaluation, "AG-20261101-42", "VTP").result,
                                                       result_of(evaluation, "AG-20261101-42", "VTP").value))
        flat = Scenario(lines, {"NVDA": ("148.90", "148.95"), long_call: ("8.00", "8.20"), short_call: ("4.30", "4.50")})
        vtp = result_of(flat.evaluate(), "AG-20261101-42", "VTP")
        self.assertEqual(("clear", flat.group("AG-20261101-42")["pct_captured_liq"]), (vtp.result, vtp.value))
        losing = Scenario(lines, {"NVDA": ("148.90", "148.95"), long_call: ("3.00", "3.10"), short_call: ("1.60", "1.70")})
        vloss = result_of(losing.evaluate(), "AG-20261101-42", "VLOSS")
        self.assertEqual(("fired", Decimal("56.67")), (vloss.result, vloss.value), "170 of a 300 max loss")
        stale = Scenario(lines, {"NVDA": ("148.90", "148.95"), long_call: ("12.40", "12.60"), short_call: ("4.80", "5.00")},
                         stamps={long_call: NOW - datetime.timedelta(minutes=40)})
        stale_eval = stale.evaluate()
        self.assertEqual(("unavailable", "unavailable"), (result_of(stale_eval, "AG-20261101-42", "VTP").result,
                                                          result_of(stale_eval, "AG-20261101-42", "VLOSS").result))

    def test_between_the_strikes_into_the_last_two_trading_days(self) -> None:
        high, low = "SPY261120P00660000", "SPY261120P00650000"
        lines = [option("SPY", high, -1, "AG-20261101-43", "9.00"), option("SPY", low, 1, "AG-20261101-43", "4.00")]
        prices = {"SPY": ("652.10", "652.14"), high: ("8.00", "8.30"), low: ("0.60", "0.70")}
        between = result_of(Scenario(lines, prices, now=T1).evaluate(), "AG-20261101-43", "VBETWEEN")
        self.assertEqual(("fired", True), (between.result, between.value))
        outside = dict(prices, SPY=("670.00", "670.04"))
        self.assertEqual("clear", result_of(Scenario(lines, outside, now=T1).evaluate(), "AG-20261101-43", "VBETWEEN").result)
        early = result_of(Scenario(lines, dict(prices, SPY=("648.10", "648.14")), now=NOW).evaluate(), "AG-20261101-43",
                          "VBETWEEN")
        self.assertEqual("clear", early.result, "13 trading days left")
        stale = Scenario(lines, prices, now=T1, stamps={"SPY": T1 - datetime.timedelta(minutes=40)})
        self.assertEqual("unavailable", result_of(stale.evaluate(), "AG-20261101-43", "VBETWEEN").result)


class LongOptionTest(unittest.TestCase):
    def test_debit_doubled_half_lost_and_the_last_thirty_days(self) -> None:
        call = "NVDA261218C00140000"
        doubled = Scenario([option("NVDA", call, 1, "AG-20261101-51", "7.00")],
                           {"NVDA": ("148.90", "148.95"), call: ("14.00", "14.30")})
        evaluation = doubled.evaluate()
        self.assertEqual(("fired", Decimal("100.00")), (result_of(evaluation, "AG-20261101-51", "LTP100").result,
                                                        result_of(evaluation, "AG-20261101-51", "LTP100").value))
        self.assertEqual("clear", result_of(evaluation, "AG-20261101-51", "LDTE30").result)
        not_yet = Scenario([option("NVDA", call, 1, "AG-20261101-51", "8.00")],
                           {"NVDA": ("148.90", "148.95"), call: ("14.00", "14.30")})
        self.assertEqual(("clear", Decimal("75.00")), (result_of(not_yet.evaluate(), "AG-20261101-51", "LTP100").result,
                                                       result_of(not_yet.evaluate(), "AG-20261101-51", "LTP100").value))
        stale = Scenario([option("NVDA", call, 1, "AG-20261101-51", "7.00")],
                         {"NVDA": ("148.90", "148.95"), call: ("14.00", "14.30")},
                         stamps={call: NOW - datetime.timedelta(minutes=40)})
        self.assertEqual("unavailable", result_of(stale.evaluate(), "AG-20261101-51", "LTP100").result)
        losing_call = "NVDA261218C00150000"
        losing = Scenario([option("NVDA", losing_call, 1, "AG-20261101-52", "5.00")],
                          {"NVDA": ("148.90", "148.95"), losing_call: ("2.40", "2.50")})
        self.assertEqual(("fired", Decimal("-52.00")), (result_of(losing.evaluate(), "AG-20261101-52", "LLOSS50").result,
                                                        result_of(losing.evaluate(), "AG-20261101-52", "LLOSS50").value))
        wide = Scenario([option("NVDA", losing_call, 1, "AG-20261101-52", "5.00")],
                        {"NVDA": ("148.90", "148.95"), losing_call: ("1.00", "2.50")})
        lloss = result_of(wide.evaluate(), "AG-20261101-52", "LLOSS50")
        self.assertEqual("unavailable", lloss.result)
        self.assertIn("a loss test needs a two-sided fresh quote", lloss.reason)
        near = "NVDA261120C00150000"
        dated = Scenario([option("NVDA", near, 1, "AG-20261101-53", "3.00")], {"NVDA": ("148.90", "148.95"), near: ("3.10", "3.30")})
        self.assertEqual(("fired", 17), (result_of(dated.evaluate(), "AG-20261101-53", "LDTE30").result,
                                         result_of(dated.evaluate(), "AG-20261101-53", "LDTE30").value))

    def test_automatic_exercise_on_the_last_trading_day(self) -> None:
        call = "NVDA261120C00140000"
        lines = [option("NVDA", call, 1, "AG-20261101-54", "8.00")]
        today = Scenario(lines, {"NVDA": ("153.30", "153.36"), call: ("13.30", "13.40")}, now=EXPIRY)
        autoex = result_of(today.evaluate(), "AG-20261101-54", "AUTOEX")
        self.assertEqual(("fired", True, "EXIT_SIGNAL", "hold_review"), (autoex.result, autoex.value, autoex.verdict,
                                                                          autoex.plan))
        before = Scenario(lines, {"NVDA": ("149.20", "149.25"), call: ("9.30", "9.40")}, now=T1)
        self.assertEqual("clear", result_of(before.evaluate(), "AG-20261101-54", "AUTOEX").result)
        stale = Scenario(lines, {"NVDA": ("153.30", "153.36"), call: ("13.30", "13.40")}, now=EXPIRY,
                         stamps={"NVDA": EXPIRY - datetime.timedelta(minutes=40)})
        self.assertEqual("unavailable", result_of(stale.evaluate(), "AG-20261101-54", "AUTOEX").result)


class HedgeAndCollarTest(unittest.TestCase):
    SPY_PUT = "SPY261218P00620000"
    SPY_CALL = "SPY261218C00640000"

    def collar(self, spy: Tuple[str, str], now: datetime.datetime = NOW) -> Scenario:
        return Scenario([stock("SPY", 100, "AG-20261101-61", "640.00", kind="etf"),
                         option("SPY", self.SPY_PUT, 1, "AG-20261101-61", "11.30"),
                         option("SPY", self.SPY_CALL, -1, "AG-20261101-61", "20.00")],
                        {"SPY": spy, self.SPY_PUT: ("9.80", "10.10"), self.SPY_CALL: ("19.00", "19.40")}, now=now)

    def test_collar_reaches_its_legs_through_the_pseudo_structures(self) -> None:
        scenario = self.collar(("648.10", "648.14"))
        self.assertEqual("collar", scenario.group("AG-20261101-61")["structure"])
        evaluation = scenario.evaluate()
        verdict = verdict_of(evaluation, "AG-20261101-61")
        reported = ids(verdict.fired + verdict.unavailable + verdict.pending + verdict.clear + verdict.suppressed)
        self.assertEqual(sorted(["TP50", "TP80", "DTE21", "DELTA_WATCH", "DELTA_SIGNAL", "PIN3", "EXDIV", "LAPSE14",
                                 "ROLL30", "MONETIZE", "FLOOR", "COVER", "AUTOEX", "USTOP", "EARN2"]), reported)
        self.assertEqual(("fired", True), (result_of(evaluation, "AG-20261101-61", "COVER").result,
                                           result_of(evaluation, "AG-20261101-61", "COVER").value))
        tp50 = result_of(evaluation, "AG-20261101-61", "TP50")
        self.assertEqual(("clear", Decimal("3.00")), (tp50.result, tp50.value), "(20.00 - 19.40) / 20.00 on the short call")
        self.assertEqual("clear", result_of(evaluation, "AG-20261101-61", "FLOOR").result)
        self.assertEqual("clear", result_of(evaluation, "AG-20261101-61", "EARN2").result, "SPY: no earnings (a confirmed none)")
        self.assertEqual("EXIT_SIGNAL", verdict.verdict, "DELTA_SIGNAL on the short call (0.63)")
        floor = self.collar(("610.00", "610.04"))
        self.assertEqual(("fired", True), (result_of(floor.evaluate(), "AG-20261101-61", "FLOOR").result,
                                           result_of(floor.evaluate(), "AG-20261101-61", "FLOOR").value))

    def test_protective_put_time_and_monetize(self) -> None:
        lines = [stock("SPY", 100, "AG-20261101-62", "640.00", kind="etf"), option("SPY", self.SPY_PUT, 1, "AG-20261101-62", "11.30")]
        late = datetime.datetime(2026, 12, 7, 19, 50, tzinfo=UTC)
        expiring = Scenario(lines, {"SPY": ("648.10", "648.14"), self.SPY_PUT: ("2.00", "2.10")}, now=late)
        evaluation = expiring.evaluate()
        self.assertEqual(("fired", 11), (result_of(evaluation, "AG-20261101-62", "LAPSE14").result,
                                         result_of(evaluation, "AG-20261101-62", "LAPSE14").value))
        self.assertEqual("fired", result_of(evaluation, "AG-20261101-62", "ROLL30").result)
        near = Scenario(lines, {"SPY": ("600.00", "600.04"), self.SPY_PUT: ("21.00", "21.40")})
        monetize = result_of(near.evaluate(), "AG-20261101-62", "MONETIZE")
        delta = near.leg(self.SPY_PUT)["greeks"]["delta"]
        self.assertEqual(("clear", Decimal(repr(abs(delta))).quantize(Decimal("0.01")), Decimal("0.75")),
                         (monetize.result, monetize.value, monetize.value), "|delta| 0.753 is below 0.80")
        self.assertEqual("fired", result_of(near.evaluate(), "AG-20261101-62", "FLOOR").result, "600.02 <= 619.99")
        deep = Scenario(lines, {"SPY": ("590.00", "590.04"), self.SPY_PUT: ("30.60", "31.00")})
        monetize = result_of(deep.evaluate(), "AG-20261101-62", "MONETIZE")
        delta = deep.leg(self.SPY_PUT)["greeks"]["delta"]
        self.assertEqual(("fired", Decimal(repr(abs(delta))).quantize(Decimal("0.01")), Decimal("0.81")),
                         (monetize.result, monetize.value, monetize.value), "|delta| 0.807 rounds to 0.81 >= 0.80")
        stale = Scenario(lines, {"SPY": ("590.00", "590.04"), self.SPY_PUT: ("30.60", "31.00")},
                         stamps={self.SPY_PUT: NOW - datetime.timedelta(minutes=40)})
        self.assertEqual("unavailable", result_of(stale.evaluate(), "AG-20261101-62", "MONETIZE").result)
        self.assertEqual("clear", result_of(stale.evaluate(), "AG-20261101-62", "LAPSE14").result, "E rules still read")


class CoverageTest(unittest.TestCase):
    """Decision 13: custom groups get only any_group, any_option_leg and underlying rules; a naked short call joins
    the short-premium family; EXDIV reaches every open short call."""

    def test_custom_group(self) -> None:
        lines = [option("NVDA", "NVDA261218C00150000", 1, "AG-20261101-71", "5.00"),
                 option("NVDA", "NVDA261120C00150000", -1, "AG-20261101-71", "3.00")]
        scenario = Scenario(lines, {"NVDA": ("148.90", "148.95"), "NVDA261218C00150000": ("8.00", "8.20"),
                                    "NVDA261120C00150000": ("3.10", "3.30")})
        self.assertEqual("custom", scenario.group("AG-20261101-71")["structure"])
        verdict = verdict_of(scenario.evaluate(), "AG-20261101-71")
        self.assertEqual(["AUTOEX", "USTOP"], ids(verdict.fired + verdict.unavailable + verdict.pending + verdict.clear
                                                  + verdict.suppressed))

    def test_naked_short_call_and_the_dividend_watch(self) -> None:
        call = "WMT261218C00100000"
        lines = [option("WMT", call, -1, "AG-20261101-72", "7.00")]
        exposed = Scenario(lines, {"WMT": ("106.40", "106.46"), call: ("6.50", "6.70")}, now=T1)
        self.assertEqual("short_call", exposed.group("AG-20261101-72")["structure"])
        evaluation = exposed.evaluate()
        verdict = verdict_of(evaluation, "AG-20261101-72")
        reported = ids(verdict.fired + verdict.unavailable + verdict.pending + verdict.clear + verdict.suppressed)
        self.assertEqual(sorted(["TP50", "TP80", "LOSSX", "DTE21", "DELTA_WATCH", "DELTA_SIGNAL", "PIN3", "EXDIV",
                                 "AUTOEX", "USTOP", "EARN2"]), reported)
        exdiv = result_of(evaluation, "AG-20261101-72", "EXDIV")
        self.assertEqual(("fired", 3), (exdiv.result, exdiv.value), "three trading days to Tue 2026-11-24")
        self.assertEqual(Decimal("-0.1650"), exdiv.conditions[1].value, "bid 6.50 - (106.43 - 100) - 0.2350")
        self.assertEqual("EXIT_SIGNAL", verdict.verdict)
        rich = Scenario(lines, {"WMT": ("106.40", "106.46"), call: ("7.00", "7.20")}, now=T1)
        self.assertEqual("clear", result_of(rich.evaluate(), "AG-20261101-72", "EXDIV").result)
        unknown_cal = calendar_events.parse_calendar_text(CAL_TEXT.replace(
            'ex_dividend = { date = 2026-11-24, amount = 0.2350, status = "confirmed"',
            'ex_dividend = { date = 2026-11-24, status = "unknown"'), "calendar.toml")
        unknown = Scenario(lines, {"WMT": ("106.40", "106.46"), call: ("6.50", "6.70")}, now=T1, calendar=unknown_cal)
        exdiv_unknown = result_of(unknown.evaluate(), "AG-20261101-72", "EXDIV")
        self.assertEqual("unavailable", exdiv_unknown.result)
        self.assertIn("unknown dividend is never zero", exdiv_unknown.reason)

    def test_stop_and_earnings(self) -> None:
        stopped = Scenario([stock("WMT", 100, "AG-20261101-73", "100.00", stop="105.00")], {"WMT": ("104.20", "104.26")})
        ustop = result_of(stopped.evaluate(), "AG-20261101-73", "USTOP")
        self.assertEqual(("fired", Decimal("-0.73")), (ustop.result, ustop.value), "(104.23 - 105) / 105 x 100")
        stale = Scenario([stock("WMT", 100, "AG-20261101-73", "100.00", stop="105.00")], {"WMT": ("104.20", "104.26")},
                         stamps={"WMT": NOW - datetime.timedelta(minutes=40)})
        self.assertEqual("unavailable", result_of(stale.evaluate(), "AG-20261101-73", "USTOP").result)
        put = "NVDA261218P00140000"
        lines = [option("NVDA", put, -1, "AG-20261101-74", "3.00")]
        earnings_day = Scenario(lines, {"NVDA": ("149.20", "149.25"), put: ("1.60", "1.70")}, now=T1)
        earn = result_of(earnings_day.evaluate(), "AG-20261101-74", "EARN2")
        self.assertEqual(("fired", 0), (earn.result, earn.value), "earnings on the day: 0 trading days")
        early = Scenario(lines, {"NVDA": ("148.90", "148.95"), put: ("3.10", "3.30")})
        self.assertEqual(("clear", 12), (result_of(early.evaluate(), "AG-20261101-74", "EARN2").result,
                                         result_of(early.evaluate(), "AG-20261101-74", "EARN2").value))
        no_nvda_earnings = calendar_events.parse_calendar_text(CAL_TEXT.replace(
            'earnings = { date = 2026-11-19, time = "post", status = "confirmed"',
            'earnings = { date = 2026-11-19, time = "post", status = "unknown"'), "calendar.toml")
        unknown = Scenario(lines, {"NVDA": ("149.20", "149.25"), put: ("1.60", "1.70")}, now=T1, calendar=no_nvda_earnings)
        self.assertEqual("unavailable", result_of(unknown.evaluate(), "AG-20261101-74", "EARN2").result)
        estimated_cal = calendar_events.parse_calendar_text(CAL_TEXT.replace(
            'earnings = { date = 2026-11-19, time = "post", status = "confirmed"',
            'earnings = { date = 2026-11-19, time = "post", status = "estimated"'), "calendar.toml")
        estimated = Scenario(lines, {"NVDA": ("149.20", "149.25"), put: ("1.60", "1.70")}, now=T1, calendar=estimated_cal)
        estimated_result = result_of(estimated.evaluate(), "AG-20261101-74", "EARN2")
        self.assertEqual("fired", estimated_result.result)
        self.assertTrue(any("estimated" in note for note in estimated_result.notes))
        after = Scenario(lines, {"NVDA": ("153.30", "153.36"), put: ("0.20", "0.25")}, now=EXPIRY)
        self.assertEqual("clear", result_of(after.evaluate(), "AG-20261101-74", "EARN2").result, "after the date: FALSE")


class HistoryTest(unittest.TestCase):
    """Decision 11: no previous snapshot is no_history (never a group verdict); a missing quote is unavailable."""

    LINES = [stock("WMT", 200, "AG-20261101-81", "100.00")]

    def prev(self, wmt: Tuple[str, str]) -> Dict[str, Any]:
        return json.loads(contract.json_dumps(Scenario(self.LINES, {"WMT": wmt}, now=PREV).document))

    def test_no_previous_snapshot_is_no_history(self) -> None:
        evaluation = Scenario(self.LINES, {"WMT": ("104.20", "104.26")}).evaluate()
        self.assertEqual("no_history", result_of(evaluation, "WMT", "MOVE5_UP").result)
        self.assertEqual("no_history", result_of(evaluation, "portfolio", "DAILY").result)
        self.assertEqual(["MOVE5_DOWN", "MOVE5_UP", "DAILY"], evaluation.no_history_rule_ids)
        self.assertEqual(datetime.date(2026, 11, 2), evaluation.history_date)
        self.assertEqual("HOLD", verdict_of(evaluation, "AG-20261101-81").verdict)
        self.assertEqual(0, evaluation.exit_code())

    def test_moves_and_the_day_loss_with_a_previous_snapshot(self) -> None:
        up = Scenario(self.LINES, {"WMT": ("104.20", "104.26")}, prev=self.prev(("98.98", "99.02"))).evaluate()
        self.assertEqual(("fired", Decimal("5.28")), (result_of(up, "WMT", "MOVE5_UP").result,
                                                      result_of(up, "WMT", "MOVE5_UP").value))
        self.assertEqual("clear", result_of(up, "WMT", "MOVE5_DOWN").result)
        self.assertEqual(("clear", Decimal("1046.00")), (result_of(up, "portfolio", "DAILY").result,
                                                         result_of(up, "portfolio", "DAILY").value))
        down = Scenario(self.LINES, {"WMT": ("104.20", "104.26")}, prev=self.prev(("109.98", "110.02"))).evaluate()
        self.assertEqual(("fired", Decimal("-5.25")), (result_of(down, "WMT", "MOVE5_DOWN").result,
                                                       result_of(down, "WMT", "MOVE5_DOWN").value))
        daily = result_of(down, "portfolio", "DAILY")
        self.assertEqual(("fired", Decimal("-1154.00"), Decimal("-1000"), "usd"), (daily.result, daily.value,
                                                                                   daily.threshold, daily.unit))
        self.assertEqual("HOLD", verdict_of(down, "AG-20261101-81").verdict, "section rules never set a group verdict")
        self.assertEqual(0, down.exit_code())

    def test_a_missing_quote_is_unavailable_not_no_history(self) -> None:
        stale = Scenario(self.LINES, {"WMT": ("104.20", "104.26")}, prev=self.prev(("98.98", "99.02")),
                         stamps={"WMT": NOW - datetime.timedelta(minutes=40)}).evaluate()
        self.assertEqual("unavailable", result_of(stale, "WMT", "MOVE5_UP").result)
        self.assertEqual("unavailable", result_of(stale, "portfolio", "DAILY").result)
        self.assertEqual([], stale.no_history_rule_ids)
        self.assertEqual(("HOLD", 0), (verdict_of(stale, "AG-20261101-81").verdict, stale.exit_code()),
                         "verdicts come from the rules: no stop on the group, so no applicable group rule is unknown; "
                         "the stale mark is reported by the underlying and portfolio sections")


class ConfirmationAndStateTest(unittest.TestCase):
    def test_pending_then_fired_on_a_second_stamp(self) -> None:
        first = csp("0.95", "1.00")
        evaluation = first.evaluate(single=False)
        tp50 = result_of(evaluation, "AG-20261101-11", "TP50")
        self.assertEqual(("pending", "pending"), (tp50.result, tp50.state_status))
        verdict = verdict_of(evaluation, "AG-20261101-11")
        self.assertEqual(("HOLD", ["TP50"]), (verdict.verdict, ids(verdict.pending)),
                         "a pending EXIT rule leaves the group at HOLD")
        state = rule_state.apply({}, evaluation.state_updates, first.now)
        self.assertEqual("pending", state["AG-20261101-11|TP50"]["status"])
        same = csp("0.95", "1.00", now=NOW + datetime.timedelta(minutes=1))
        same_quote = exit_rules.evaluate(ADOPTED_RULES(), first.document, now=same.now, session=same.session,
                                         calendar=CAL, state=state, next_run_utc=None, rows=first.rows)
        self.assertEqual("pending", result_of(same_quote, "AG-20261101-11", "TP50").result, "the same quote again")
        later = csp("0.95", "1.00", now=NOW + datetime.timedelta(minutes=12))
        second = later.evaluate(single=False, state=state)
        fired = result_of(second, "AG-20261101-11", "TP50")
        self.assertEqual(("fired", True, True, "fired"), (fired.result, fired.new_signal, fired.first_observation,
                                                          fired.state_status))
        state = rule_state.apply(state, second.state_updates, later.now)
        third = csp("0.95", "1.00", now=NOW + datetime.timedelta(minutes=24)).evaluate(single=False, state=state)
        again = result_of(third, "AG-20261101-11", "TP50")
        self.assertEqual(("fired", False), (again.result, again.new_signal), "no second transition")

    def test_e_rules_never_wait(self) -> None:
        near = "NVDA261120C00150000"
        scenario = Scenario([option("NVDA", near, 1, "AG-20261101-53", "3.00")],
                            {"NVDA": ("148.90", "148.95"), near: ("3.10", "3.30")})
        self.assertEqual("fired", result_of(scenario.evaluate(single=False), "AG-20261101-53", "LDTE30").result)

    def test_first_observation_needs_a_position_opened_before_today(self) -> None:
        opened_today = Scenario([option("WMT", WMT_PUT_95, -1, "AG-20261103-11", "2.00", opened="2026-11-03")],
                                {"WMT": ("104.20", "104.26"), WMT_PUT_95: ("0.95", "1.00")})
        self.assertFalse(result_of(opened_today.evaluate(), "AG-20261103-11", "TP50").first_observation)
        self.assertTrue(result_of(csp("0.95", "1.00").evaluate(), "AG-20261101-11", "TP50").first_observation)

    def test_dry_run_state_is_never_changed(self) -> None:
        state: Dict[str, Any] = {}
        csp("0.95", "1.00").evaluate(state=state)
        self.assertEqual({}, state)

    def test_updates_name_every_observed_rule(self) -> None:
        evaluation = csp("0.95", "1.00").evaluate()
        keys = {update["key"] for update in evaluation.state_updates}
        self.assertIn("AG-20261101-11|TP50", keys)
        self.assertIn("portfolio|CONC", keys)
        unseen = next(update for update in evaluation.state_updates if update["key"] == "underlying:WMT|MOVE5_UP")
        self.assertEqual(("unknown", None), (unseen["truth"], unseen["entry"]), "no_history records nothing yet")
        for update in evaluation.state_updates:
            self.assertEqual(rule_state.UPDATE_KEYS, tuple(update))
            json.dumps(update)


class SuppressionTest(unittest.TestCase):
    def test_tags_switch_rules_off_and_values_are_still_computed(self) -> None:
        tagged = Scenario([option("WMT", "WMT261218P00120000", -1, "AG-20261101-12", "15.00", note="#assign-ok")],
                          {"WMT": ("104.20", "104.26"), "WMT261218P00120000": ("16.00", "16.20")})
        evaluation = tagged.evaluate()
        delta = result_of(evaluation, "AG-20261101-12", "DELTA_SIGNAL")
        self.assertEqual(("suppressed", Decimal("0.86")), (delta.result, delta.value))
        self.assertIn("#assign-ok", delta.reason)
        self.assertEqual("HOLD", verdict_of(evaluation, "AG-20261101-12").verdict)
        self.assertNotIn("AG-20261101-12|DELTA_SIGNAL", {update["key"] for update in evaluation.state_updates})

    def test_without_rows_nothing_is_suppressed_and_the_evaluation_says_so(self) -> None:
        tagged = Scenario([option("WMT", "WMT261218P00120000", -1, "AG-20261101-12", "15.00", note="#assign-ok")],
                          {"WMT": ("104.20", "104.26"), "WMT261218P00120000": ("16.00", "16.20")})
        evaluation = tagged.evaluate(with_rows=False)
        self.assertEqual("fired", result_of(evaluation, "AG-20261101-12", "DELTA_SIGNAL").result)
        self.assertTrue(any("rows" in warning for warning in evaluation.warnings))


# --------------------------------------------------------------------------------------------
# The fixture roots: decision 3 / decision 17 recomputed through book.load_book on a copy
# --------------------------------------------------------------------------------------------

WMT_GROUP, NVDA_GROUP, SPY_GROUP = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"


class FixtureCopy(unittest.TestCase):
    ROOT = FIXTURE_DESK

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-exit-rules-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(self.ROOT, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = os.path.join(self.root, name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(old), old)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))

    def load(self, fixture_set: str, now: str) -> book.BookRun:
        market = quote_cache.open_market_data(self.root, now=contract.parse_utc_instant(now), offline=True,
                                              fixture_set=fixture_set, timeout_s=5.0, user_agent="aegis-desk/test exit_rules")
        return book.load_book(market)

    def evaluate(self, fixture_set: str, now: str, next_run: Optional[str] = None,
                 rulebook: Optional[exit_rules.Rulebook] = None) -> Tuple[book.BookRun, exit_rules.Evaluation]:
        run = self.load(fixture_set, now)
        adopted, rules = exit_rules.load_adopted_rulebook(self.paths)
        self.assertEqual("1.0.0", adopted.version)
        evaluation = exit_rules.evaluate(rulebook or rules, run.document, now=run.market.now, session=run.session,
                                         calendar=run.calendar, state=rule_state.load(self.paths),
                                         next_run_utc=None if next_run is None else contract.parse_utc_instant(next_run),
                                         rows=run.positions.rows)
        return run, evaluation


class RegularInstantTest(FixtureCopy):
    """Decision 3 ``regular`` with decision 17(ii): WMT WATCH, NVDA WATCH, SPY HOLD, exit 0."""

    def test_regular_without_next_run(self) -> None:
        run, evaluation = self.evaluate("regular", REGULAR_NOW)
        wmt = verdict_of(evaluation, WMT_GROUP)
        self.assertEqual(("WATCH", ["DTE21"], [], []), (wmt.verdict, ids(wmt.fired), ids(wmt.pending), ids(wmt.unavailable)))
        self.assertEqual(["CAP", "DELTA_SIGNAL", "DELTA_WATCH"], ids(wmt.suppressed), "#assign-ok on the WMT call")
        self.assertEqual(["AUTOEX", "EARN2", "EXDIV", "PIN3", "TP50", "TP80", "USTOP"], ids(wmt.clear))
        self.assertEqual(13, result_of(evaluation, WMT_GROUP, "DTE21").value)
        self.assertEqual(Decimal("-2.34"), result_of(evaluation, WMT_GROUP, "TP50").value, "(3.42 - 3.50) / 3.42 x 100")
        self.assertEqual(Decimal("13.29"), result_of(evaluation, WMT_GROUP, "USTOP").value, "WMT 104.23 vs the 92.00 stop")
        self.assertEqual(10, result_of(evaluation, WMT_GROUP, "EARN2").value, "10 trading days to 2026-11-17")
        exdiv = result_of(evaluation, WMT_GROUP, "EXDIV")
        self.assertEqual("clear", exdiv.result, "not applicable: the Nov-20 call is gone before the Nov-23 cum day")
        self.assertIn("2026-11-23", exdiv.reason)
        self.assertEqual(Decimal("0.49"), result_of(evaluation, WMT_GROUP, "DELTA_WATCH").value)
        nvda = verdict_of(evaluation, NVDA_GROUP)
        self.assertEqual(("WATCH", ["DTE21"], ["DELTA_WATCH"], ["EARN2"]),
                         (nvda.verdict, ids(nvda.fired), ids(nvda.pending), ids(nvda.suppressed)))
        self.assertEqual(Decimal("0.48"), result_of(evaluation, NVDA_GROUP, "DELTA_WATCH").value)
        self.assertEqual(Decimal("-8.20"), result_of(evaluation, NVDA_GROUP, "TP50").value)
        spy = verdict_of(evaluation, SPY_GROUP)
        self.assertEqual(("HOLD", []), (spy.verdict, ids(spy.fired + spy.pending + spy.unavailable)))
        self.assertEqual(["AUTOEX", "FLOOR", "LAPSE14", "MONETIZE", "ROLL30", "USTOP"], ids(spy.clear))
        self.assertEqual(45, result_of(evaluation, SPY_GROUP, "ROLL30").value)
        self.assertEqual(Decimal("0.27"), result_of(evaluation, SPY_GROUP, "MONETIZE").value)
        conc = result_of(evaluation, "portfolio", "CONC")
        self.assertEqual(("fired", Decimal("26.32"), Decimal("25")), (conc.result, conc.value, conc.threshold))
        self.assertEqual("no_history", result_of(evaluation, "portfolio", "DAILY").result)
        for symbol in ("NVDA", "SPY", "WMT"):
            for rule_id in ("MOVE5_DOWN", "MOVE5_UP"):
                self.assertEqual("no_history", result_of(evaluation, symbol, rule_id).result)
        self.assertEqual(["MOVE5_DOWN", "MOVE5_UP", "DAILY"], evaluation.no_history_rule_ids)
        self.assertEqual({"EXIT_SIGNAL": 0, "UNAVAILABLE": 0, "WATCH": 2, "HOLD": 1}, evaluation.verdict_counts())
        self.assertEqual(0, evaluation.exit_code())
        self.assertEqual([WMT_GROUP, NVDA_GROUP, SPY_GROUP], [verdict.group_id for verdict in evaluation.groups],
                         "the closed cash-secured put has nothing to evaluate")
        self.assertEqual((run.document["generated_at"], datetime.date(2026, 11, 3), False),
                         (evaluation.evaluated_at, evaluation.et_date, evaluation.single_observation))

    def test_regular_with_a_single_observation(self) -> None:
        _, evaluation = self.evaluate("regular", REGULAR_NOW, next_run="2026-11-03T21:35:00Z")
        nvda = verdict_of(evaluation, NVDA_GROUP)
        self.assertEqual(("WATCH", ["DELTA_WATCH", "DTE21"], []), (nvda.verdict, ids(nvda.fired), ids(nvda.pending)))
        self.assertEqual(0, evaluation.exit_code())

    def test_candidate_changes_no_verdict_on_the_regular_day(self) -> None:
        _, adopted = self.evaluate("regular", REGULAR_NOW)
        _, candidate = self.evaluate("regular", REGULAR_NOW, rulebook=exit_rules.load_rulebook(CANDIDATE_RULES))
        self.assertEqual([(v.group_id, v.verdict) for v in adopted.groups], [(v.group_id, v.verdict) for v in candidate.groups])

    def test_book_round_trip_through_json(self) -> None:
        run = self.load("regular", REGULAR_NOW)
        self.assertEqual(run.document, exit_rules.book_from_json(json.loads(contract.json_dumps(run.document))))


class ExpiryDayTest(FixtureCopy):
    """Decision 3 ``expiry_day`` with decision 17(iv), journeys 2 and 3."""

    def test_single_observation_two_exit_signals(self) -> None:
        run, evaluation = self.evaluate("expiry_day", EXPIRY_DAY_NOW, next_run=AFTER_CLOSE_NEXT_RUN)
        self.assertTrue(evaluation.single_observation)
        wmt = verdict_of(evaluation, WMT_GROUP)
        self.assertEqual(("EXIT_SIGNAL", ["DTE21", "PIN3", "TP80"], [], ["CAP", "DELTA_SIGNAL", "DELTA_WATCH"]),
                         (wmt.verdict, ids(wmt.fired), ids(wmt.unavailable), ids(wmt.suppressed)))
        tp80 = result_of(evaluation, WMT_GROUP, "TP80")
        self.assertEqual((Decimal("95.32"), Decimal("80"), "percent", "E", "close"),
                         (tp80.value, tp80.threshold, tp80.unit, tp80.quote_grade, tp80.plan))
        self.assertIn("quote grade E: the conservative side stands", tp80.notes)
        fresh_ask = next(item for item in tp80.inputs if item.name == "fresh_ask")
        self.assertEqual((Decimal("0.16"), datetime.datetime(2026, 11, 20, 19, 31, 12, tzinfo=UTC)),
                         (fresh_ask.value, fresh_ask.as_of))
        self.assertEqual(Decimal("0.07"), result_of(evaluation, WMT_GROUP, "PIN3").value, "|104.93 - 105| / 104.93")
        self.assertEqual("clear", result_of(evaluation, WMT_GROUP, "TP50").result, "95.32 % but 0 trading days left")
        nvda = verdict_of(evaluation, NVDA_GROUP)
        self.assertEqual(("EXIT_SIGNAL", ["CAP", "DELTA_SIGNAL", "DELTA_WATCH", "DTE21", "PIN3"], []),
                         (nvda.verdict, ids(nvda.fired), ids(nvda.suppressed)))
        earn = result_of(evaluation, NVDA_GROUP, "EARN2")
        self.assertEqual("clear", earn.result, "the 2026-11-19 earnings have passed: not applicable, so not suppressed")
        self.assertIn("have passed", earn.reason)
        self.assertEqual(Decimal("0.96"), result_of(evaluation, NVDA_GROUP, "DELTA_SIGNAL").value)
        self.assertEqual(Decimal("2.17"), result_of(evaluation, NVDA_GROUP, "PIN3").value)
        self.assertEqual(Decimal("-12.13"), result_of(evaluation, NVDA_GROUP, "TP80").value, "cost to close 3.42 > 3.05")
        self.assertTrue(result_of(evaluation, NVDA_GROUP, "CAP").value, "153.33 >= 150.01")
        spy = verdict_of(evaluation, SPY_GROUP)
        self.assertEqual(("WATCH", ["ROLL30"]), (spy.verdict, ids(spy.fired)), "28 calendar days to 2026-12-18")
        self.assertEqual("fired", result_of(evaluation, "portfolio", "CONC").result, "SPY 26.17 % whatever WMT is")
        daily = result_of(evaluation, "portfolio", "DAILY")
        self.assertEqual("unavailable", daily.result, "an open group is unavailable: data, not history")
        self.assertEqual({"EXIT_SIGNAL": 2, "UNAVAILABLE": 0, "WATCH": 1, "HOLD": 0}, evaluation.verdict_counts())
        self.assertEqual(10, evaluation.exit_code())
        self.assertEqual("unavailable", run.document["groups"][0]["data_quality"], "the WMT call quote is grade E")

    def test_without_next_run_the_m_rules_wait(self) -> None:
        _, evaluation = self.evaluate("expiry_day", EXPIRY_DAY_NOW)
        wmt = verdict_of(evaluation, WMT_GROUP)
        self.assertEqual(("WATCH", ["DTE21"], ["PIN3", "TP80"]), (wmt.verdict, ids(wmt.fired), ids(wmt.pending)))
        nvda = verdict_of(evaluation, NVDA_GROUP)
        self.assertEqual(("WATCH", ["DTE21"], ["CAP", "DELTA_SIGNAL", "DELTA_WATCH", "PIN3"]),
                         (nvda.verdict, ids(nvda.fired), ids(nvda.pending)))
        self.assertEqual(0, evaluation.exit_code())

    def test_state_carries_the_fire_and_a_rerun_is_no_transition(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        _, first = self.evaluate("expiry_day", EXPIRY_DAY_NOW, next_run=AFTER_CLOSE_NEXT_RUN)
        rule_state.save(self.paths, rule_state.apply(rule_state.load(self.paths), first.state_updates,
                                                     contract.parse_utc_instant(EXPIRY_DAY_NOW)))
        fired_now = sorted(update["key"] for update in first.state_updates if update["fired"])
        self.assertIn(WMT_GROUP + "|TP80", fired_now)
        _, second = self.evaluate("expiry_day", "2026-11-20T19:51:00Z", next_run=AFTER_CLOSE_NEXT_RUN)
        self.assertEqual([], [update["key"] for update in second.state_updates if update["event"] is not None])
        self.assertEqual("fired", result_of(second, WMT_GROUP, "TP80").result)
        self.assertFalse(result_of(second, WMT_GROUP, "TP80").new_signal)

    def test_candidate_changes_no_verdict_on_expiry_day(self) -> None:
        _, adopted = self.evaluate("expiry_day", EXPIRY_DAY_NOW, next_run=AFTER_CLOSE_NEXT_RUN)
        _, candidate = self.evaluate("expiry_day", EXPIRY_DAY_NOW, next_run=AFTER_CLOSE_NEXT_RUN,
                                     rulebook=exit_rules.load_rulebook(CANDIDATE_RULES))
        self.assertEqual([(v.group_id, v.verdict) for v in adopted.groups], [(v.group_id, v.verdict) for v in candidate.groups],
                         "TP50 needs more than 21 trading days: 50 or 60 percent, it stays clear")
        self.assertEqual(Decimal("60"), result_of(candidate, WMT_GROUP, "TP50").threshold)

    def test_book_round_trip_through_json(self) -> None:
        run = self.load("expiry_day", EXPIRY_DAY_NOW)
        self.assertEqual(run.document, exit_rules.book_from_json(json.loads(contract.json_dumps(run.document))))


class T1AndStaleTest(FixtureCopy):
    def test_t1(self) -> None:
        _, evaluation = self.evaluate("t1", T1_NOW)
        wmt = verdict_of(evaluation, WMT_GROUP)
        self.assertEqual(("WATCH", ["DTE21"], ["PIN3"], ["CAP", "DELTA_SIGNAL", "DELTA_WATCH"]),
                         (wmt.verdict, ids(wmt.fired), ids(wmt.pending), ids(wmt.suppressed)))
        self.assertEqual("clear", result_of(evaluation, WMT_GROUP, "EXDIV").result,
                         "decision 17(iii): not applicable, not suppressed")
        self.assertEqual(Decimal("1.34"), result_of(evaluation, WMT_GROUP, "PIN3").value)
        self.assertEqual(Decimal("53.22"), result_of(evaluation, WMT_GROUP, "TP50").value)
        nvda = verdict_of(evaluation, NVDA_GROUP)
        self.assertEqual(("WATCH", ["DTE21"], ["DELTA_WATCH", "PIN3"], ["EARN2"]),
                         (nvda.verdict, ids(nvda.fired), ids(nvda.pending), ids(nvda.suppressed)))
        self.assertEqual(Decimal("0.52"), result_of(evaluation, NVDA_GROUP, "PIN3").value)
        tp50 = result_of(evaluation, NVDA_GROUP, "TP50")
        self.assertEqual(("clear", Decimal("63.93")), (tp50.result, tp50.value), "63.9 % captured but 1 trading day left")
        earn = result_of(evaluation, NVDA_GROUP, "EARN2")
        self.assertEqual(("suppressed", 0), (earn.result, earn.value), "earnings today, #earnings-ok on the NVDA call")
        self.assertEqual(("WATCH", ["ROLL30"]), (verdict_of(evaluation, SPY_GROUP).verdict,
                                                 ids(verdict_of(evaluation, SPY_GROUP).fired)))
        self.assertEqual(0, evaluation.exit_code())
        _, single = self.evaluate("t1", T1_NOW, next_run="2026-11-19T21:35:00Z")
        self.assertEqual(["DTE21", "PIN3"], ids(verdict_of(single, WMT_GROUP).fired))

    def test_stale(self) -> None:
        _, evaluation = self.evaluate("stale", REGULAR_NOW)
        wmt = verdict_of(evaluation, WMT_GROUP)
        self.assertEqual(("UNAVAILABLE", ["DTE21"], ["TP80", "USTOP"]), (wmt.verdict, ids(wmt.fired), ids(wmt.unavailable)))
        self.assertEqual("clear", result_of(evaluation, WMT_GROUP, "TP50").result,
                         "Kleene: an unknown capture AND a FALSE 'more than 21 trading days' is FALSE")
        nvda = verdict_of(evaluation, NVDA_GROUP)
        self.assertEqual(("UNAVAILABLE", ["CAP", "DELTA_SIGNAL", "DELTA_WATCH", "TP80"]), (nvda.verdict, ids(nvda.unavailable)))
        spy = verdict_of(evaluation, SPY_GROUP)
        self.assertEqual(("UNAVAILABLE", ["FLOOR", "MONETIZE"]), (spy.verdict, ids(spy.unavailable)))
        for result in wmt.unavailable + nvda.unavailable + spy.unavailable:
            self.assertTrue(result.reason, result.rule_id)
        self.assertEqual({"EXIT_SIGNAL": 0, "UNAVAILABLE": 3, "WATCH": 0, "HOLD": 0}, evaluation.verdict_counts())
        self.assertEqual(20, evaluation.exit_code())


class QuarantineTest(FixtureCopy):
    def test_a_quarantined_group_is_unavailable_with_its_reasons(self) -> None:
        # the closed row's group: quarantining it leaves the quote batch (and so the offline fixture set) unchanged
        self.rewrite("positions.csv", "closed,2026-10-14,1,0.55,", "closed,2026-10-14,1,zero,")
        _, evaluation = self.evaluate("regular", REGULAR_NOW)
        closed = verdict_of(evaluation, "AG-20261001-04")
        self.assertEqual(("UNAVAILABLE", True, [], []), (closed.verdict, closed.quarantined, closed.fired, closed.unavailable))
        self.assertTrue(closed.reasons and all(isinstance(reason, str) and reason for reason in closed.reasons))
        self.assertTrue(any("close_price_per_unit" in reason for reason in closed.reasons), closed.reasons)
        self.assertEqual(20, evaluation.exit_code(), "a quarantined group makes the run UNAVAILABLE")
        self.assertEqual(("WATCH", "WATCH", "HOLD"), (verdict_of(evaluation, WMT_GROUP).verdict,
                                                      verdict_of(evaluation, NVDA_GROUP).verdict,
                                                      verdict_of(evaluation, SPY_GROUP).verdict))
        self.assertEqual("unavailable", result_of(evaluation, "portfolio", "DAILY").result,
                         "with a quarantined group the day P/L is data-unavailable, not history")


class HistoryFixtureTest(FixtureCopy):
    def test_with_the_previous_snapshot_the_moves_and_the_day_pl_evaluate(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        previous = self.load("pre_open", PREV_EOD_NOW)
        book.write_snapshot(self.paths, previous.document, previous.session.et_date)
        run, evaluation = self.evaluate("regular", REGULAR_NOW)
        day_pl = run.document["portfolio"]["day_pl"]
        self.assertIsNotNone(day_pl)
        daily = result_of(evaluation, "portfolio", "DAILY")
        self.assertEqual(("clear", day_pl), (daily.result, daily.value))
        for symbol in ("NVDA", "SPY", "WMT"):
            move = run.document["underlyings"][symbol]["move_pct_from_prev_eod"]
            self.assertEqual(move, result_of(evaluation, symbol, "MOVE5_UP").value)
            self.assertEqual("clear", result_of(evaluation, symbol, "MOVE5_UP").result)
        self.assertEqual([], evaluation.no_history_rule_ids)


class ConcentrationTest(FixtureCopy):
    def test_kleene_maximum_on_the_book(self) -> None:
        thirty = adopted_variant(("value = 25, unit = \"percent\"", "value = 30, unit = \"percent\""))
        _, regular = self.evaluate("regular", REGULAR_NOW, rulebook=thirty)
        self.assertEqual(("clear", Decimal("26.32")), (result_of(regular, "portfolio", "CONC").result,
                                                       result_of(regular, "portfolio", "CONC").value))
        _, expiry_day = self.evaluate("expiry_day", EXPIRY_DAY_NOW, rulebook=thirty)
        unknown = result_of(expiry_day, "portfolio", "CONC")
        self.assertEqual("unavailable", unknown.result, "WMT unmarked: it could still reach 30")
        self.assertIn("WMT", unknown.reason)


class ExdivAgreementTest(FixtureCopy):
    """Decision 14: the rule and assignment_watch agree on t1, expiry_day and a copy whose WMT ex-date is Mon
    2026-11-23 (the cum day is then the call's last trading day: EXDIV evaluates)."""

    WMT_CALL = "WMT261120C00105000"

    def watch(self, fixture_set: str, now: str) -> Dict[str, Any]:
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            assignment_watch.main(["--json", "--offline", "--fixture-set", fixture_set, "--now", now, "--desk-root", self.root])
        body = json.loads(buffer.getvalue())
        return next(leg for leg in body["payload"]["legs"] if leg["symbol"] == self.WMT_CALL)

    def check(self, fixture_set: str, now: str, applicable: bool) -> None:
        _, evaluation = self.evaluate(fixture_set, now)
        exdiv = result_of(evaluation, WMT_GROUP, "EXDIV")
        watched = self.watch(fixture_set, now)["dividend"]
        if not applicable:
            self.assertEqual("clear", exdiv.result)
            self.assertIsNone(watched)
            return
        self.assertEqual("suppressed", exdiv.result, "#assign-ok switches the rule off; the watch says NOTED")
        days, gap = exdiv.conditions
        extrinsic = next(item for item in gap.inputs if item.name == "extrinsic_bid")
        amount = next(item for item in gap.inputs if item.name == "dividend_amount")
        self.assertEqual(watched["trading_days_to_ex"], days.value)
        self.assertEqual(Decimal(watched["extrinsic_bid"]), extrinsic.value)
        self.assertEqual(Decimal(watched["amount"]), amount.value)
        self.assertEqual(exit_rules.TRUE, gap.truth)
        self.assertEqual(("NOTED", "HIGH"), (watched["tier"], watched["downgraded_from"]))

    def test_the_two_tools_agree(self) -> None:
        self.check("t1", T1_NOW, applicable=False)
        self.check("expiry_day", EXPIRY_DAY_NOW, applicable=False)
        self.rewrite(os.path.join("research", "calendar.toml"), "date = 2026-11-24, amount = 0.2350",
                     "date = 2026-11-23, amount = 0.2350")
        self.check("t1", T1_NOW, applicable=True)
        self.check("expiry_day", EXPIRY_DAY_NOW, applicable=True)


class BookFromJsonTest(unittest.TestCase):
    def test_round_trip_with_history_and_every_type(self) -> None:
        prev = json.loads(contract.json_dumps(Scenario([stock("WMT", 200, "AG-20261101-81", "100.00")],
                                                       {"WMT": ("98.98", "99.02")}, now=PREV).document))
        scenario = Scenario([stock("WMT", 200, "AG-20261101-81", "100.00", stop="92.00")] + CSP,
                            {"WMT": ("104.20", "104.26"), WMT_PUT_95: ("0.95", "1.00")}, prev=prev)
        document = scenario.document
        back = exit_rules.book_from_json(json.loads(contract.json_dumps(document)))
        self.assertEqual(document, back)
        leg = next(item for item in back["legs"] if item["occ"] == WMT_PUT_95)
        self.assertIsInstance(leg["strike"], Decimal)
        self.assertIsInstance(leg["expiry"], datetime.date)
        self.assertIsInstance(leg["as_of"], datetime.datetime)
        self.assertIsInstance(leg["greeks"]["delta"], float)
        self.assertIsInstance(back["generated_at"], datetime.datetime)
        self.assertIsInstance(back["rates"]["as_of"], datetime.date)
        self.assertIsInstance(back["portfolio"]["concentration"]["declared_equity"], Decimal)

    def test_an_old_snapshot_without_the_fresh_sides_reads(self) -> None:
        raw = json.loads(contract.json_dumps(csp("0.95", "1.00").document))
        for leg in raw["legs"]:
            del leg["fresh_bid"]
            del leg["fresh_ask"]
        back = exit_rules.book_from_json(raw)
        self.assertTrue(all(leg["fresh_bid"] is None and leg["fresh_ask"] is None for leg in back["legs"]))

    def test_malformed_documents_are_refused(self) -> None:
        good = json.loads(contract.json_dumps(csp("0.95", "1.00").document))
        cases = []
        missing = copy.deepcopy(good)
        del missing["portfolio"]
        cases.append(missing)
        extra = copy.deepcopy(good)
        extra["legs"][0]["colour"] = "red"
        cases.append(extra)
        money = copy.deepcopy(good)
        money["groups"][0]["net_cost"] = -200.0
        cases.append(money)
        date = copy.deepcopy(good)
        date["legs"][0]["expiry"] = "next friday"
        cases.append(date)
        cases.append(["not", "a", "book"])
        for case in cases:
            with self.assertRaises(exit_rules.BookFormatError):
                exit_rules.book_from_json(case)


# --------------------------------------------------------------------------------------------
# The adoption lint
# --------------------------------------------------------------------------------------------


def codes(findings: Sequence[exit_rules.LintFinding]) -> List[Tuple[str, Optional[str], Optional[str]]]:
    return sorted((item.code, item.rule_id, item.structure) for item in findings)


class LintTest(unittest.TestCase):
    MAIN_HELD = ["covered_call", "protective_put"]
    RECON_HELD = ["cash_secured_put", "covered_call", "long_call", "long_put", "protective_put", "short_call"]

    def test_the_adopted_fixture_rulebook_passes_for_both_roots(self) -> None:
        self.assertEqual([], exit_rules.lint(ADOPTED_RULES(), self.MAIN_HELD))
        self.assertEqual([], exit_rules.lint(ADOPTED_RULES(), self.RECON_HELD))
        self.assertEqual([], exit_rules.lint(exit_rules.load_rulebook(CANDIDATE_RULES), self.MAIN_HELD))
        every = [name for name in grouping.STRUCTURES if name not in ("custom", "invalid")]
        self.assertEqual([], exit_rules.lint(ADOPTED_RULES(), every), "every named structure is covered")

    def test_the_example_file_needs_only_the_owners_acceptance(self) -> None:
        findings = exit_rules.lint(exit_rules.load_rulebook(EXAMPLE_RULES), self.MAIN_HELD)
        self.assertEqual(sorted([("not_accepted", rule_id, None) for rule_id in EXAMPLE_IDS]
                                + [("owner_must_set", "CONC", None), ("owner_must_set", "DAILY", None)]), codes(findings))

    def test_held_structures(self) -> None:
        main = exit_rules.held_structures(book_groups(FIXTURE_DESK))
        self.assertEqual(self.MAIN_HELD, main, "the closed cash-secured put is not held")
        self.assertEqual(self.RECON_HELD, exit_rules.held_structures(book_groups(FIXTURE_DESK_RECON)))

    def test_uncovered_structures(self) -> None:
        without_tp = adopted_variant(('role = "take_profit"\nscope = "group"\napplies_to = ["long_call", "long_put"]',
                                      'role = "take_profit"\nscope = "group"\napplies_to = ["long_put"]'))
        self.assertEqual([("uncovered_structure", None, "long_call")], codes(exit_rules.lint(without_tp, ["long_call"])))
        self.assertEqual([("uncovered_structure", None, "custom")], codes(exit_rules.lint(ADOPTED_RULES(), ["custom"])))
        self.assertEqual([], exit_rules.lint(ADOPTED_RULES(), ["long_stock", "short_stock"]), "stock alone needs none")

    def rule(self, body: str) -> exit_rules.Rulebook:
        return parse(MINIMAL + "\n[[rule]]\n" + body)

    def test_loss_rules(self) -> None:
        on_leg = self.rule('id = "LEGLOSS"\ntitle = "x"\nrole = "loss"\nscope = "group"\napplies_to = ["short_call_leg"]\n'
                           'input_class = "M"\nwhen = [{ metric = "pct_captured", op = "<=", value = -100, unit = "percent" }]\n'
                           'verdict = "EXIT_SIGNAL"\nplan = "close"\norigin = "owner-entered"\nsource = "x"\n')
        self.assertIn(("loss_on_short_call_leg", "LEGLOSS", None), codes(exit_rules.lint(on_leg, ["covered_call"])))
        group = self.rule('id = "CCLOSS"\ntitle = "x"\nrole = "loss"\nscope = "group"\napplies_to = ["covered_call"]\n'
                          'input_class = "M"\nwhen = [{ metric = "loss_multiple_of_credit", op = ">=", value = 2, unit = "multiple" }]\n'
                          'verdict = "EXIT_SIGNAL"\nplan = "close"\norigin = "owner-entered"\nsource = "x"\n')
        found = codes(exit_rules.lint(group, ["covered_call"]))
        self.assertIn(("loss_metric", "CCLOSS", "covered_call"), found)
        self.assertIn(("metric_not_applicable", "CCLOSS", "covered_call"), found)
        fine = self.rule('id = "CCLOSS"\ntitle = "x"\nrole = "loss"\nscope = "group"\napplies_to = ["covered_call"]\n'
                         'input_class = "M"\nwhen = [{ metric = "pct_of_max_loss", op = ">=", value = 25, unit = "percent" }]\n'
                         'verdict = "EXIT_SIGNAL"\nplan = "close"\norigin = "owner-entered"\nsource = "x"\n')
        self.assertEqual([], exit_rules.lint(fine, ["covered_call"]))
        over = fine.rules[1]
        too_far = self.rule('id = "CCLOSS"\ntitle = "x"\nrole = "loss"\nscope = "group"\napplies_to = ["covered_call"]\n'
                            'input_class = "M"\nwhen = [{ metric = "pct_of_max_loss", op = ">=", value = 101, unit = "percent" }]\n'
                            'verdict = "EXIT_SIGNAL"\nplan = "close"\norigin = "owner-entered"\nsource = "x"\n')
        self.assertIn(("max_loss_over_100", "CCLOSS", None), codes(exit_rules.lint(too_far, ["covered_call"])))
        self.assertEqual("CCLOSS", over.id)
        twice = self.rule('id = "CSPLOSS"\ntitle = "x"\nrole = "loss"\nscope = "group"\napplies_to = ["cash_secured_put"]\n'
                          'input_class = "M"\nwhen = [{ metric = "pct_of_max_loss", op = ">=", value = 50, unit = "percent" }]\n'
                          'verdict = "EXIT_SIGNAL"\nplan = "close"\norigin = "owner-entered"\nsource = "x"\n')
        self.assertEqual([], exit_rules.lint(twice, ["cash_secured_put"]), "one loss rule")
        both = parse(MINIMAL.replace('role = "take_profit"', 'role = "loss"').replace(
            '{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" },\n  { metric = "dte", op = ">", value = 21, dte_unit = "trading" },',
            '{ metric = "unrealized_pl", op = "<=", value = -300, unit = "usd" },').replace(
            '["cash_secured_put", "short_call_leg"]', '["cash_secured_put"]') + "\n[[rule]]\n"
            'id = "CSPLOSS"\ntitle = "x"\nrole = "loss"\nscope = "group"\napplies_to = ["cash_secured_put"]\n'
            'input_class = "M"\nwhen = [{ metric = "pct_of_max_loss", op = ">=", value = 50, unit = "percent" }]\n'
            'verdict = "EXIT_SIGNAL"\nplan = "close"\norigin = "owner-entered"\nsource = "x"\n')
        found = codes(exit_rules.lint(both, ["cash_secured_put"]))
        self.assertIn(("duplicate_loss", None, "cash_secured_put"), found)

    def test_take_profit_metrics(self) -> None:
        wrong = parse(MINIMAL.replace('{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" },',
                                      '{ metric = "pct_of_debit", op = ">=", value = 50, unit = "percent" },').replace(
            '["cash_secured_put", "short_call_leg"]', '["cash_secured_put"]'))
        found = codes(exit_rules.lint(wrong, ["cash_secured_put"]))
        self.assertIn(("metric_not_applicable", "TP50", "cash_secured_put"), found)
        self.assertIn(("uncovered_structure", None, "cash_secured_put"), found, "a take-profit that cannot apply covers nothing")
        timed = parse(MINIMAL.replace('{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" },\n', ""))
        self.assertIn(("take_profit_metric", "TP50", None), codes(exit_rules.lint(timed, ["cash_secured_put"])))

    def test_leg_rules_must_select_a_leg(self) -> None:
        bad = self.rule('id = "LONGDELTA"\ntitle = "x"\nrole = "risk"\nscope = "leg"\napplies_to = ["long_call"]\n'
                        'input_class = "G"\nwhen = [{ metric = "abs_delta", op = ">=", value = 0.9, unit = "delta" }]\n'
                        'verdict = "WATCH"\nplan = "hold_review"\norigin = "owner-entered"\nsource = "x"\n')
        self.assertIn(("selects_nothing", "LONGDELTA", "long_call"), codes(exit_rules.lint(bad, ["long_call"])))

    def test_required_inputs_are_book_fields(self) -> None:
        self.assertEqual([], exit_rules.missing_inputs())
        original = exit_rules.METRICS["pct_from_strike"]
        try:
            exit_rules.METRICS["pct_from_strike"] = dataclasses.replace(original, fields=original.fields + ("legs.spot_last",))
            findings = codes(exit_rules.lint(ADOPTED_RULES(), self.MAIN_HELD))
            self.assertIn(("input_unavailable", "PIN3", None), findings)
        finally:
            exit_rules.METRICS["pct_from_strike"] = original
        self.assertEqual([], exit_rules.lint(ADOPTED_RULES(), self.MAIN_HELD))


def book_groups(root: str) -> List[grouping.PositionGroup]:
    from aegis_desk import positions_csv
    positions = positions_csv.load_positions(os.path.join(root, "positions.csv"), today=datetime.date(2026, 11, 3))
    return grouping.group_positions(positions.rows)


# --------------------------------------------------------------------------------------------
# The committed rule files
# --------------------------------------------------------------------------------------------


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


class ExampleRulebookTest(unittest.TestCase):
    def test_every_example_rule_is_inert_and_sourced(self) -> None:
        rulebook = exit_rules.load_rulebook(EXAMPLE_RULES)
        self.assertEqual(("1.0.0", EXAMPLE_IDS), (rulebook.rulebook_version, rulebook.rule_ids))
        for rule in rulebook.rules:
            self.assertEqual("example", rule.origin, rule.id)
            self.assertTrue(rule.source and rule.title, rule.id)
        by_id = {rule.id: rule for rule in rulebook.rules}
        self.assertEqual({"EXDIV": "assign-ok", "DELTA_WATCH": "assign-ok", "DELTA_SIGNAL": "assign-ok", "CAP": "assign-ok",
                          "EARN2": "earnings-ok"},
                         {rule.id: rule.suppress_if_tag for rule in rulebook.rules if rule.suppress_if_tag})
        self.assertIsNone(by_id["PIN3"].suppress_if_tag, "pin risk is never suppressed")
        self.assertEqual(("any_short_call_leg",), by_id["EXDIV"].applies_to)
        self.assertNotIn("short_call_leg", by_id["LOSSX"].applies_to)
        self.assertIn("short_call", by_id["LOSSX"].applies_to)
        self.assertEqual({"CONC", "DAILY"}, {rule.id for rule in rulebook.rules if rule.owner_must_set})
        self.assertEqual((Decimal("-1000"), "usd"), (by_id["DAILY"].when[0].value, by_id["DAILY"].when[0].unit))
        self.assertEqual((Decimal("25"), "percent", 1), (by_id["CONC"].when[0].value, by_id["CONC"].when[0].unit,
                                                         by_id["CONC"].confirmations))
        self.assertIn("loss 2.0 x credit = cost-to-close 3.0 x credit", by_id["LOSSX"].source)
        self.assertIn("risk control, not edge", by_id["LOSSX"].source)
        for rule in rulebook.rules:
            for condition in rule.when:
                if condition.metric == "dte":
                    self.assertIn(condition.dte_unit, ("calendar", "trading"), rule.id)

    def test_no_order_shaped_text(self) -> None:
        import re
        screen = list(alert_lint.FORBIDDEN_PATTERNS) + [re.compile(r"\b%s\b" % word, re.IGNORECASE)
                                                       for word in EXTRA_ORDER_WORDS]
        for path in (EXAMPLE_RULES, EXAMPLE_README, FIXTURE_RULES, CANDIDATE_RULES,
                     os.path.join(FIXTURE_DESK, "rules", "CHANGELOG.md")):
            text = read_bytes(path).decode("utf-8").lower()
            for entry, pattern in zip(list(alert_lint.FORBIDDEN_LANGUAGE) + [None] * len(EXTRA_ORDER_WORDS), screen):
                if path == EXAMPLE_README and entry is not None and entry.code == "FL04":
                    continue  # the README cites FINRA NTM 01-23, whose subject is the very noun FL04 forbids
                self.assertIsNone(pattern.search(text), (path, pattern.pattern))
            self.assertIsNone(re.search(r"\bv\d+\.\d+\.\d+\b", text), path)
            self.assertNotIn("trading/", text, path)

    def test_readme(self) -> None:
        text = read_bytes(EXAMPLE_README).decode("utf-8")
        for phrase in ("Nothing in this directory is active", "adopted", "owner", "FINRA", "01-23", "not legal advice",
                       "exit_rules-1.0.0.toml"):
            self.assertIn(phrase, text)


class FixtureRuleFilesTest(unittest.TestCase):
    def test_the_adopted_file_is_the_example_with_its_acceptance_marks(self) -> None:
        example = read_bytes(EXAMPLE_RULES).decode("utf-8")
        expected = example.replace('origin = "example"', 'origin = "example-accepted"').replace(
            "owner_must_set = true", "owner_must_set = false")
        self.assertEqual(expected.encode("utf-8"), read_bytes(FIXTURE_RULES))
        rulebook = exit_rules.load_rulebook(FIXTURE_RULES)
        self.assertTrue(all(rule.origin == "example-accepted" and not rule.owner_must_set for rule in rulebook.rules))
        self.assertEqual(exit_rules.load_rulebook(EXAMPLE_RULES).rules_sha, rulebook.rules_sha,
                         "accepting a rule does not change its sha")

    def test_adopted_names_the_bytes(self) -> None:
        rules_bytes = read_bytes(FIXTURE_RULES)
        adopted = exit_rules.load_adopted(os.path.join(FIXTURE_DESK, "rules", "ADOPTED"))
        rulebook = exit_rules.load_rulebook(FIXTURE_RULES)
        self.assertEqual(("1.0.0", hashlib.sha256(rules_bytes).hexdigest(), rulebook.rules_sha,
                          hashlib.sha256(b"adopt rules 1.0.0").hexdigest(),
                          datetime.datetime(2026, 11, 2, 22, 0, tzinfo=UTC)),
                         (adopted.version, adopted.sha256, adopted.rules_sha, adopted.owner_phrase_sha256,
                          adopted.adopted_utc))
        self.assertEqual(exit_rules.adopted_text(version="1.0.0", sha256=adopted.sha256, adopted_utc=adopted.adopted_utc,
                                                 owner_phrase_sha256=adopted.owner_phrase_sha256,
                                                 rules_sha=rulebook.rules_sha).encode("utf-8"),
                         read_bytes(os.path.join(FIXTURE_DESK, "rules", "ADOPTED")))

    def test_changelog_has_the_one_adoption_entry(self) -> None:
        rulebook = exit_rules.load_rulebook(FIXTURE_RULES)
        adopted = exit_rules.load_adopted(os.path.join(FIXTURE_DESK, "rules", "ADOPTED"))
        expected = exit_rules.CHANGELOG_HEADER + exit_rules.changelog_entry(
            rulebook, adopted_utc=adopted.adopted_utc, owner_phrase_sha256=adopted.owner_phrase_sha256)
        text = read_bytes(os.path.join(FIXTURE_DESK, "rules", "CHANGELOG.md")).decode("utf-8")
        self.assertEqual(expected, text)
        self.assertEqual(1, text.count("\n## "))
        for rule in rulebook.rules:
            self.assertIn("- %s example-accepted: %s" % (rule.id, exit_rules.rule_summary(rule)), text)

    def test_the_recon_root_holds_the_same_three_files(self) -> None:
        for name in ("exit_rules-1.0.0.toml", "ADOPTED", "CHANGELOG.md"):
            self.assertEqual(read_bytes(os.path.join(FIXTURE_DESK, "rules", name)),
                             read_bytes(os.path.join(FIXTURE_DESK_RECON, "rules", name)), name)
        self.assertEqual(["ADOPTED", "CHANGELOG.md", "exit_rules-1.0.0.toml"],
                         sorted(os.listdir(os.path.join(FIXTURE_DESK_RECON, "rules"))))
        for root in (FIXTURE_DESK, FIXTURE_DESK_RECON):
            for runtime in ("state", "data", "journal"):
                self.assertFalse(os.path.exists(os.path.join(root, runtime)), (root, runtime))

    def test_the_candidate_moves_tp50_to_sixty(self) -> None:
        adopted_text = read_bytes(FIXTURE_RULES).decode("utf-8")
        tp50 = adopted_text.index('id = "TP50"')
        old = '{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" }'
        position = adopted_text.index(old, tp50)
        expected = (adopted_text[:position] + old.replace("value = 50", "value = 60") + adopted_text[position + len(old):])
        expected = expected.replace('rulebook_version = "1.0.0"', 'rulebook_version = "1.1.0"', 1)
        self.assertEqual(expected.encode("utf-8"), read_bytes(CANDIDATE_RULES))
        candidate = exit_rules.load_rulebook(CANDIDATE_RULES)
        changed = sorted(rule_id for rule_id, sha in candidate.rules_sha.items() if ADOPTED_RULES().rules_sha[rule_id] != sha)
        self.assertEqual(("1.1.0", ["TP50"]), (candidate.rulebook_version, changed))

    def test_load_adopted_rulebook_refuses_changed_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-adopted-") as tmp:
            root = os.path.join(os.path.realpath(tmp), "desk")
            shutil.copytree(FIXTURE_DESK, root)
            paths = desk_paths.for_root(root)
            adopted, rulebook = exit_rules.load_adopted_rulebook(paths)
            self.assertEqual((adopted.version, adopted.sha256), (rulebook.rulebook_version, rulebook.sha256))
            self.assertEqual(os.path.join(root, "rules", "exit_rules-1.0.0.toml"), exit_rules.rulebook_path(paths, "1.0.0"))
            path = exit_rules.rulebook_path(paths, "1.0.0")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("# edited after adoption\n")
            with self.assertRaises(exit_rules.RulebookError) as caught:
                exit_rules.load_adopted_rulebook(paths)
            self.assertEqual("rules_tampered", caught.exception.code)
            os.remove(paths.adopted_file)
            with self.assertRaises(exit_rules.RulebookError) as missing:
                exit_rules.load_adopted_rulebook(paths)
            self.assertEqual("not_adopted", missing.exception.code)


# --------------------------------------------------------------------------------------------
# The lens-1 review: quote stamps, the re-arm bound, strict grammar, per-structure take-profit metrics, the credit
# basis, carry-forward, notes and boundary precision
# --------------------------------------------------------------------------------------------

NO_TP50 = ('{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" }',
           '{ metric = "pct_captured", op = ">=", value = 99, unit = "percent" }')
OLD_VTP = 'applies_to = ["vertical_debit_call", "vertical_debit_put"]\ninput_class = "M"\nwhen = [\n  { metric = "pct_of_max_profit"'
VTP_ON_LONG_CALL = 'applies_to = ["long_put"]\ninput_class = "M"\nwhen = [\n  { metric = "pct_of_max_profit"'


def stamp_results(evaluation: exit_rules.Evaluation) -> List[Tuple[str, str, exit_rules.RuleResult]]:
    return [(subject, rule_id, result) for (subject, rule_id), result in all_results(evaluation).items()]


class QuoteStampTest(FixtureCopy):
    def test_a_group_field_reading_confirms_on_the_quote_not_the_clock(self) -> None:
        scenario = csp("5.90", "6.10")
        state: Dict[str, Any] = {}
        seen = []
        for minutes in (0, 11):
            now = NOW + datetime.timedelta(minutes=minutes)
            evaluation = exit_rules.evaluate(ADOPTED_RULES(), scenario.document, now=now, session=scenario.session,
                                             calendar=CAL, state=state, next_run_utc=None, rows=scenario.rows)
            lossx = result_of(evaluation, "AG-20261101-11", "LOSSX")
            seen.append((lossx.result, lossx.value, lossx.observations, lossx.as_of))
            state = rule_state.apply(state, evaluation.state_updates, now)
        stamp = stamp_of(NOW)
        self.assertEqual([("pending", Decimal("2.05"), 1, stamp)] * 2, seen, "the same quote twice is one observation")
        self.assertEqual([stamp], [rule_state.parse_instant(item["as_of"])
                                   for item in state["AG-20261101-11|LOSSX"]["observations"]])
        later = csp("5.90", "6.10", now=NOW + datetime.timedelta(minutes=12))
        second = result_of(later.evaluate(single=False, state=state), "AG-20261101-11", "LOSSX")
        self.assertEqual(("fired", 2), (second.result, second.observations))

    def test_every_true_m_or_g_observation_carries_its_quote_stamp(self) -> None:
        checked = 0
        for fixture_set, now in (("regular", REGULAR_NOW), ("expiry_day", EXPIRY_DAY_NOW), ("t1", T1_NOW)):
            _, evaluation = self.evaluate(fixture_set, now)
            assert evaluation.rulebook is not None
            for subject, rule_id, result in stamp_results(evaluation):
                if result.input_class == "E" or result.result not in ("fired", "pending", "cooldown"):
                    continue
                checked += 1
                with self.subTest(fixture_set=fixture_set, subject=subject, rule=rule_id):
                    self.assertIsNotNone(result.as_of)
            for update in evaluation.state_updates:
                if update["truth"] == "true" and evaluation.rulebook.rule(update["rule_id"]).input_class != "E":
                    self.assertIsNotNone(update["as_of"], update["key"])
        self.assertGreater(checked, 5)

    def test_group_and_portfolio_field_readings_are_stamped(self) -> None:
        evaluation = csp("5.90", "6.10").evaluate(single=False)
        stamp = stamp_of(NOW)
        self.assertEqual(stamp, result_of(evaluation, "AG-20261101-11", "LOSSX").as_of)
        conc = result_of(evaluation, "portfolio", "CONC")
        self.assertEqual(stamp, conc.conditions[0].as_of, "the concentration reads every open leg and underlying")


class CooldownTest(unittest.TestCase):
    """A rule that fired and cleared cannot fire again before it re-arms: TRUE in cooldown is ``cooldown``, a
    non-counting result like ``pending``, so the verdict does not flip back to EXIT_SIGNAL inside the re-arm bound."""

    def test_an_oscillating_take_profit_fires_once_until_it_rearms(self) -> None:
        rules = adopted_variant(NO_TP50)
        state: Dict[str, Any] = {}
        seen = []
        for step_index, (bid, ask) in enumerate((("0.25", "0.30"), ("0.45", "0.50"), ("0.25", "0.30"), ("0.45", "0.50"),
                                                 ("0.25", "0.30"))):
            scenario = csp(bid, ask, now=NOW + datetime.timedelta(minutes=10 * step_index))
            evaluation = scenario.evaluate(rulebook=rules, state=state)
            tp80 = result_of(evaluation, "AG-20261101-11", "TP80")
            verdict = verdict_of(evaluation, "AG-20261101-11")
            seen.append((tp80.result, tp80.new_signal, verdict.verdict, ids(verdict.cooldown)))
            state = rule_state.apply(state, evaluation.state_updates, scenario.now)
        self.assertEqual([("fired", True, "EXIT_SIGNAL", []), ("clear", False, "HOLD", []),
                          ("cooldown", False, "HOLD", ["TP80"]), ("clear", False, "HOLD", []),
                          ("cooldown", False, "HOLD", ["TP80"])], seen)
        tomorrow = datetime.datetime(2026, 11, 4, 19, 50, tzinfo=UTC)  # re-armed at 00:00 ET
        rearmed = csp("0.25", "0.30", now=tomorrow).evaluate(rulebook=rules, state=state)
        tp80 = result_of(rearmed, "AG-20261101-11", "TP80")
        self.assertEqual(("fired", True, "EXIT_SIGNAL"), (tp80.result, tp80.new_signal,
                                                         verdict_of(rearmed, "AG-20261101-11").verdict))

    def test_cooldown_reason_names_the_rearm(self) -> None:
        rules = adopted_variant(NO_TP50)
        state: Dict[str, Any] = {}
        for step_index, (bid, ask) in enumerate((("0.25", "0.30"), ("0.45", "0.50"))):
            scenario = csp(bid, ask, now=NOW + datetime.timedelta(minutes=10 * step_index))
            state = rule_state.apply(state, scenario.evaluate(rulebook=rules, state=state).state_updates, scenario.now)
        again = csp("0.25", "0.30", now=NOW + datetime.timedelta(minutes=20)).evaluate(rulebook=rules, state=state)
        tp80 = result_of(again, "AG-20261101-11", "TP80")
        self.assertEqual(("cooldown", Decimal("85.00"), "cooldown"), (tp80.result, tp80.value, tp80.state_status))
        self.assertIn("2026-11-04 00:00 ET", tp80.reason)


class StrictGrammarTest(unittest.TestCase):
    def test_a_days_threshold_must_be_whole(self) -> None:
        found = problems(MINIMAL.replace('{ metric = "dte", op = ">", value = 21, dte_unit = "trading" }',
                                         '{ metric = "dte", op = ">", value = 21.5, dte_unit = "trading" }'))
        self.assertTrue(any("whole number" in item for item in found), found)
        self.assertEqual(Decimal("21.0"), parse(MINIMAL.replace(
            '{ metric = "dte", op = ">", value = 21, dte_unit = "trading" }',
            '{ metric = "dte", op = ">", value = 21.0, dte_unit = "trading" }')).rules[0].when[1].value)

    def test_identifiers_refuse_a_trailing_newline_and_other_digits(self) -> None:
        self.assertTrue(any("id must" in item for item in problems(MINIMAL.replace('id = "TP50"', 'id = "TP50\\n"'))))
        self.assertTrue(any("rulebook_version" in item for item in problems(
            MINIMAL.replace('rulebook_version = "1.0.0"', 'rulebook_version = "1.0.0\\n"'))))
        self.assertTrue(any("rulebook_version" in item for item in problems(
            MINIMAL.replace('rulebook_version = "1.0.0"', 'rulebook_version = "\\u0661.0.0"'))))
        for pattern, text in ((exit_rules.VERSION_RE, "1.0.0\n"), (exit_rules.RULE_ID_RE, "TP80\n"),
                              (exit_rules.SHA256_RE, "a" * 64 + "\n"), (exit_rules.VERSION_RE, "١.0.0")):
            self.assertIsNone(pattern.match(text), (pattern.pattern, text))
        with self.assertRaises(ValueError):
            exit_rules.adopted_text(version="1.0.0\n", sha256="a" * 64, adopted_utc=NOW, owner_phrase_sha256="b" * 64,
                                    rules_sha={"TP50": "c" * 64})


class TakeProfitCoverageTest(unittest.TestCase):
    def test_a_debit_vertical_is_not_covered_by_its_short_legs_credit(self) -> None:
        held = ["covered_call", "protective_put", "vertical_debit_call", "vertical_debit_put"]
        without_vtp = adopted_variant((OLD_VTP, VTP_ON_LONG_CALL))
        self.assertEqual([("uncovered_structure", None, "vertical_debit_call"),
                          ("uncovered_structure", None, "vertical_debit_put")],
                         [item for item in codes(exit_rules.lint(without_vtp, held)) if item[0] == "uncovered_structure"])
        short_tp = ('\n[[rule]]\nid = "SHORTTP"\ntitle = "x"\nrole = "take_profit"\nscope = "leg"\n'
                    'applies_to = ["vertical_debit_call", "vertical_debit_put"]\ninput_class = "M"\n'
                    'when = [{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" }]\n'
                    'verdict = "EXIT_SIGNAL"\nplan = "close"\norigin = "owner-entered"\nsource = "x"\n')
        with open(FIXTURE_RULES, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(OLD_VTP))
        variant = exit_rules.parse_rulebook_text(text.replace(OLD_VTP, VTP_ON_LONG_CALL) + short_tp, "variant.toml")
        found = codes(exit_rules.lint(variant, held))
        for structure in ("vertical_debit_call", "vertical_debit_put"):
            self.assertIn(("uncovered_structure", None, structure), found)
            self.assertIn(("take_profit_metric", "SHORTTP", structure), found)
        self.assertEqual([], exit_rules.lint(ADOPTED_RULES(), held), "VTP's pct_of_max_profit covers them")


STOCK_BASIS_CUSTOM = """schema_version = 1
rulebook_version = "1.0.0"
title = "custom test rules"

[defaults]
mark_basis = "mid"
confirmations = 1
confirmation_gap_min = 10
confirmation_window_min = 90
cooldown_min = 240
rearm = "next_trading_day"
hysteresis_pct = 10

[[rule]]
id = "CTP"
title = "x"
role = "take_profit"
scope = "group"
applies_to = ["custom"]
input_class = "M"
when = [{ metric = "pct_captured", op = ">=", value = 50, unit = "percent" }]
verdict = "EXIT_SIGNAL"
plan = "close"
origin = "owner-entered"
source = "x"

[[rule]]
id = "CLOSS"
title = "x"
role = "loss"
scope = "group"
applies_to = ["custom"]
input_class = "M"
when = [{ metric = "loss_multiple_of_credit", op = ">=", value = 2, unit = "multiple" }]
verdict = "EXIT_SIGNAL"
plan = "close"
origin = "owner-entered"
source = "x"
"""


class CreditBasisTest(unittest.TestCase):
    def test_credit_metrics_need_a_credit_basis(self) -> None:
        put = WMT_PUT_95
        scenario = Scenario([stock("WMT", -100, "AG-20261101-81", "104.00"), option("WMT", put, -1, "AG-20261101-81", "2.00")],
                            {"WMT": ("104.20", "104.26"), put: ("0.95", "1.00")})
        group = scenario.group("AG-20261101-81")
        self.assertEqual(("custom", "stock"), (group["structure"], group["basis_kind"]))
        self.assertTrue(group["net_cost"] < 0)
        evaluation = scenario.evaluate(rulebook=parse(STOCK_BASIS_CUSTOM))
        for rule_id, metric in (("CTP", "pct_captured"), ("CLOSS", "loss_multiple_of_credit")):
            result = result_of(evaluation, "AG-20261101-81", rule_id)
            self.assertEqual("unavailable", result.result, rule_id)
            self.assertIn("%s does not apply" % metric, result.reason)
            self.assertIn("stock", result.reason)


class CarryForwardEquivalenceTest(unittest.TestCase):
    def test_both_resets_give_the_same_first_observation(self) -> None:
        scenario = csp("0.95", "1.00")
        sha = ADOPTED_RULES().rules_sha["TP50"]
        stale_state = {"AG-20261101-11|TP50": rule_state.new_entry("0" * 64, datetime.date(2026, 11, 1))}
        direct = result_of(scenario.evaluate(state=stale_state), "AG-20261101-11", "TP50")
        carried = rule_state.carry_forward(stale_state, ADOPTED_RULES().rules_sha)
        self.assertEqual(["AG-20261101-11|TP50"], carried.reset)
        after = result_of(scenario.evaluate(state=carried.state), "AG-20261101-11", "TP50")
        self.assertEqual(("fired", True), (direct.result, direct.first_observation))
        self.assertEqual((direct.result, direct.first_observation, sha), (after.result, after.first_observation,
                                                                          after.rule_sha))


class NotesTest(unittest.TestCase):
    def test_an_estimated_earnings_note_names_no_owner_text(self) -> None:
        estimated_cal = calendar_events.parse_calendar_text(CAL_TEXT.replace(
            'earnings = { date = 2026-11-19, time = "post", status = "confirmed", source = "owner-entered (fixture)"',
            'earnings = { date = 2026-11-19, time = "post", status = "estimated", source = "owner typed note, café"'),
            "calendar.toml")
        put = "NVDA261218P00140000"
        scenario = Scenario([option("NVDA", put, -1, "AG-20261101-74", "3.00")],
                            {"NVDA": ("149.20", "149.25"), put: ("1.60", "1.70")}, now=T1, calendar=estimated_cal)
        earn = result_of(scenario.evaluate(), "AG-20261101-74", "EARN2")
        self.assertEqual(("fired", (exit_rules.ESTIMATED_EARNINGS_NOTE,)), (earn.result, earn.notes))
        self.assertNotIn("owner typed", " ".join(earn.notes))


class BoundaryPrecisionTest(unittest.TestCase):
    """Decision 15, refined at the threshold: two decimals are kept unless they would decide the other way from the
    exact value; then more decimals are kept, compared and printed (a half-cent above the stop is not at the stop)."""

    def test_a_half_cent_above_the_stop_is_not_at_the_stop(self) -> None:
        lines = [stock("NVDA", 100, "AG-20261101-91", "140.00", stop="148.92")]
        above = Scenario(lines, {"NVDA": ("148.92", "148.93")})
        self.assertEqual(Decimal("148.925"), above.document["underlyings"]["NVDA"]["mark_mid"])
        ustop = result_of(above.evaluate(), "AG-20261101-91", "USTOP")
        self.assertEqual(("clear", Decimal("0.0034")), (ustop.result, ustop.value))
        at_stop = result_of(Scenario(lines, {"NVDA": ("148.91", "148.93")}).evaluate(), "AG-20261101-91", "USTOP")
        self.assertEqual(("fired", Decimal("0.00")), (at_stop.result, at_stop.value))
        below = result_of(Scenario(lines, {"NVDA": ("148.91", "148.92")}).evaluate(), "AG-20261101-91", "USTOP")
        self.assertEqual(("fired", Decimal("-0.0034")), (below.result, below.value))
        far = Scenario([stock("WMT", 100, "AG-20261101-73", "100.00", stop="105.00")], {"WMT": ("104.20", "104.26")})
        self.assertEqual(Decimal("-0.73"), result_of(far.evaluate(), "AG-20261101-73", "USTOP").value, "two decimals")

    def test_the_refinement_helper(self) -> None:
        at_zero = exit_rules.Condition("spot_vs_stop_pct", "<=", Decimal("0"), "percent", None)
        self.assertEqual(Decimal("0.0034"), exit_rules.refine_at_threshold(Decimal("0.003357"), Decimal("0.00"), at_zero))
        self.assertEqual(Decimal("-0.73"), exit_rules.refine_at_threshold(Decimal("-0.7333"), Decimal("-0.73"), at_zero))
        floor = exit_rules.Condition("pct_captured", ">=", Decimal("80"), "percent", None)
        self.assertEqual(Decimal("79.9960"), exit_rules.refine_at_threshold(Decimal("79.996"), Decimal("80.00"), floor))
        self.assertEqual(Decimal("80.00"), exit_rules.refine_at_threshold(Decimal("80.004"), Decimal("80.00"), floor))
        self.assertEqual(Decimal("0.00"), exit_rules.refine_at_threshold(Decimal("0"), Decimal("0.00"), at_zero))

if __name__ == "__main__":
    unittest.main()
