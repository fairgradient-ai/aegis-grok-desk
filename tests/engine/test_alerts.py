"""Alert text (``aegis_desk.alerts``): exact goldens of every message builder against books built from copied
fixture roots (``book.load_book``), with evaluations written by hand in the shape the addendum pins
(``Evaluation``, ``GroupVerdict``, ``RuleResult``, ``Input``) as dataclasses and as plain dicts, so this module never
waits for ``exit_rules``. Every number in a message comes from one formatter per kind and is in
``emitted_numbers``, and every message passes ``alert_lint.check``."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import copy
import dataclasses
import datetime
import json
import os
import re
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from aegis_desk import alert_lint, alerts, book, contract, desk_paths, exit_rules, quote_cache, signals

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULE_PATH = os.path.join(ROOT, "scripts", "aegis_desk", "alerts.py")
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
UTC = datetime.timezone.utc
SHA = "3f9a01bc" + "5d2e7c4b" * 7
RUN_INFO = {"rulebook_version": "1.0.0", "rulebook_sha": SHA, "quote_age_min": 18, "run_lateness_min": 0,
            "posted_at": datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)}
NOT_AN_INSTRUCTION = "not an instruction to trade; you act in your brokerage account."
FOOTER_1931 = ("deterministic rules you adopted \u00b7 posted by an AI assistant \u00b7 not personalized investment advice "
               "\u00b7 data delayed 15 min, as of 19:31 UTC")
PLAN_SHAPE = re.compile(
    r"^(?:EXIT SIGNAL|WATCH) \S+ \| \S+ [a-z -]+ \| your rule [A-Z][A-Z0-9_]{1,15} \(rules \d+\.\d+\.\d+, sha [0-9a-f]{8}\) "
    r"fired: .+ \| quote grade [A-F], Tradier sandbox, \d+-min delayed, as of \d\d:\d\d UTC(?:, run \d+ min late)? \| "
    r"your plan: (?:close|review a roll|review and decide) \| not an instruction to trade; you act in your brokerage "
    r"account\.$")


@dataclasses.dataclass
class Input:
    name: str
    value: Any
    source: str
    as_of: Optional[datetime.datetime]


@dataclasses.dataclass
class Condition:
    metric: str
    op: str
    value: Any
    threshold: Any
    unit: Optional[str]
    dte_unit: Optional[str] = None


@dataclasses.dataclass
class RuleResult:
    rule_id: str
    rule_sha: str
    result: str
    metric: str
    value: Any
    threshold: Any
    unit: Optional[str]
    inputs: List[Input]
    reason: str
    verdict: Optional[str] = None
    plan: Optional[str] = None
    op: Optional[str] = None
    dte_unit: Optional[str] = None
    conditions: Optional[List[Condition]] = None
    first_observation: bool = False
    observations: Optional[int] = None
    confirmations: Optional[int] = None


@dataclasses.dataclass
class GroupVerdict:
    group_id: str
    verdict: str
    fired: List[RuleResult]
    unavailable: List[RuleResult]
    pending: List[RuleResult]
    clear: List[RuleResult]
    suppressed: List[RuleResult]


@dataclasses.dataclass
class Section:
    """A portfolio or underlying section (``subject`` is ``portfolio`` or the symbol), as exit_rules reports it."""

    subject: str
    fired: List[RuleResult]
    unavailable: List[RuleResult]
    no_history: List[RuleResult]
    pending: List[RuleResult]
    clear: List[RuleResult]
    suppressed: List[RuleResult]


@dataclasses.dataclass
class Evaluation:
    groups: List[GroupVerdict]
    portfolio: Any
    no_history: List[str]
    state_updates: List[Any]
    rulebook: Any = None
    underlyings: List[Section] = dataclasses.field(default_factory=list)
    no_history_rule_ids: List[str] = dataclasses.field(default_factory=list)


def rule(rule_id: str, result: str, metric: str, value: Any, threshold: Any, unit: Optional[str], *, op: str = ">=",
         verdict: str = "WATCH", plan: Optional[str] = "hold_review", reason: str = "", **extra: Any) -> RuleResult:
    return RuleResult(rule_id, "%s-sha" % rule_id.lower(), result, metric, value, threshold, unit, [], reason,
                      verdict=verdict, plan=plan, op=op, **extra)


def dte(rule_id: str, value: int, threshold: int, *, unit: str = "trading", verdict: str = "WATCH",
        plan: str = "roll_review", result: str = "fired") -> RuleResult:
    return rule(rule_id, result, "dte", value, threshold, "days", op="<=", verdict=verdict, plan=plan, dte_unit=unit)


def suppressed(*ids: str) -> List[RuleResult]:
    return [rule(rule_id, "suppressed", "abs_delta", None, Decimal("0.30"), "delta", reason="suppressed by #assign-ok")
            for rule_id in ids]


def load_document(fixture_set: str, now: str) -> Dict[str, Any]:
    temporary = tempfile.mkdtemp(prefix="aegis-alerts-test-")
    try:
        root = os.path.join(temporary, "desk")
        shutil.copytree(FIXTURE_DESK, root)
        market = quote_cache.open_market_data(root, now=contract.parse_utc_instant(now), offline=True,
                                              fixture_set=fixture_set, timeout_s=10.0, user_agent="aegis-desk-test")
        return book.load_book(market).document
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def expiry_day_evaluation() -> Evaluation:
    wmt = GroupVerdict("AG-20261020-01", "EXIT_SIGNAL", fired=[
        rule("TP80", "fired", "pct_captured", Decimal("95.32"), 80, "percent", verdict="EXIT_SIGNAL", plan="close",
             reason="quote grade E: the conservative side stands"),
        dte("DTE21", 0, 21),
        rule("PIN3", "fired", "pct_from_strike", Decimal("0.07"), 3, "percent", op="<=", conditions=[
            Condition("pct_from_strike", "<=", Decimal("0.07"), 3, "percent"),
            Condition("dte", "<=", 0, 3, "days", "trading")]),
    ], unavailable=[], pending=[], clear=[], suppressed=suppressed("DELTA_WATCH", "DELTA_SIGNAL", "CAP"))
    nvda = GroupVerdict("AG-20261022-02", "EXIT_SIGNAL", fired=[
        dte("DTE21", 0, 21),
        rule("DELTA_WATCH", "fired", "abs_delta", 0.9621482880991431, Decimal("0.30"), "delta"),
        rule("DELTA_SIGNAL", "fired", "abs_delta", 0.9621482880991431, Decimal("0.50"), "delta", verdict="EXIT_SIGNAL",
             plan="roll_review"),
        rule("PIN3", "fired", "pct_from_strike", Decimal("2.17"), 3, "percent", op="<=", conditions=[
            Condition("pct_from_strike", "<=", Decimal("2.17"), 3, "percent"),
            Condition("dte", "<=", 0, 3, "days", "trading")]),
        rule("CAP", "fired", "stock_above_short_call", True, None, None, op="is_true", plan="roll_review", conditions=[
            Condition("stock_above_short_call", "is_true", True, None, None),
            Condition("dte", "<=", 0, 21, "days", "trading")]),
    ], unavailable=[], pending=[], clear=[], suppressed=suppressed("EARN2"))
    spy = GroupVerdict("AG-20261028-03", "HOLD", [], [], [], [], [])
    return Evaluation([wmt, nvda, spy], [], [], [])


def regular_evaluation() -> Evaluation:
    wmt = GroupVerdict("AG-20261020-01", "WATCH", fired=[dte("DTE21", 13, 21)], unavailable=[], pending=[], clear=[],
                       suppressed=suppressed("DELTA_WATCH", "DELTA_SIGNAL", "CAP"))
    nvda = GroupVerdict("AG-20261022-02", "WATCH", fired=[dte("DTE21", 13, 21)], unavailable=[], pending=[
        rule("DELTA_WATCH", "pending", "abs_delta", 0.47695335020629354, Decimal("0.30"), "delta", observations=1,
             confirmations=2)], clear=[], suppressed=suppressed("EARN2"))
    spy = GroupVerdict("AG-20261028-03", "HOLD", [], [], [], [], [])
    conc = rule("CONC", "fired", "max_concentration_pct", Decimal("26.32"), 25, "percent")
    daily = rule("DAILY", "no_history", "day_pl", None, Decimal("-1000"), "usd", op="<=")
    portfolio = Section("portfolio", [conc], [], [daily], [], [], [])
    underlyings = [Section(symbol, [], [], [rule("MOVE5_DOWN", "no_history", "move_pct_from_prev_eod", None, -5, "percent",
                                                 op="<="),
                                            rule("MOVE5_UP", "no_history", "move_pct_from_prev_eod", None, 5, "percent")],
                           [], [], []) for symbol in ("NVDA", "SPY", "WMT")]
    return Evaluation([wmt, nvda, spy], portfolio, [], [], underlyings=underlyings,
                      no_history_rule_ids=["MOVE5_DOWN", "MOVE5_UP", "DAILY"])


def stale_evaluation() -> Evaluation:
    wmt = GroupVerdict("AG-20261020-01", "UNAVAILABLE", fired=[dte("DTE21", 13, 21)], unavailable=[
        rule("TP80", "unavailable", "pct_captured", None, 80, "percent", verdict="EXIT_SIGNAL", plan="close",
             reason="quote grade F: stale quote (60 min old)"),
        rule("USTOP", "unavailable", "spot_vs_stop_pct", None, 0, "percent", op="<=", verdict="EXIT_SIGNAL",
             reason="no usable underlying mark (WMT grade F)")], pending=[], clear=[],
        suppressed=suppressed("DELTA_WATCH", "DELTA_SIGNAL", "CAP"))
    return Evaluation([wmt], [], [], [])


def as_dicts(value: Any) -> Any:
    """The same evaluation as plain dicts and lists (the other shape a test may hand to ``render``)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: as_dicts(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, list):
        return [as_dicts(item) for item in value]
    return value


class DocumentCase(unittest.TestCase):
    documents: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not DocumentCase.documents:
            DocumentCase.documents = {
                "expiry_day": load_document("expiry_day", "2026-11-20T19:50:00Z"),
                "regular": load_document("regular", "2026-11-03T19:50:00Z"),
                "stale": load_document("stale", "2026-11-03T19:50:00Z"),
            }

    def doc(self, name: str) -> Dict[str, Any]:
        return DocumentCase.documents[name]

    def assert_lints(self, alert: alerts.Alert) -> None:
        result = alert_lint.check(alert.text, alert.emitted_numbers, rule_ids=alert.rule_ids)
        self.assertTrue(result.ok, (result.problems, alert.text))
        self.assertTrue(alert.lint().ok)
        printed = sorted(token.text for token in alert_lint.unwhitelisted_numbers(alert.text, alert.rule_ids))
        self.assertEqual(sorted(set(printed)), sorted(set(alert.emitted_numbers)), "emitted exactly what is printed")
        for pattern in alert_lint.FORBIDDEN_PATTERNS:
            self.assertIsNone(pattern.search(alert.text), pattern.pattern)


class FormatterTest(unittest.TestCase):
    def test_money(self) -> None:
        for value, text, token in ((Decimal("-1000"), "-$1,000.00", "-1,000.00"), (Decimal("425"), "$425.00", "425.00"),
                                   (Decimal("0.005"), "$0.00", "0.00"), (Decimal("0.015"), "$0.02", "0.02"),
                                   (Decimal("1234567.891"), "$1,234,567.89", "1,234,567.89"),
                                   (Decimal("-0.09"), "-$0.09", "-0.09"), (0, "$0.00", "0.00")):
            emitted: List[str] = []
            self.assertEqual(text, alerts.fmt_money(value, emitted))
            self.assertEqual([token], emitted)
        with self.assertRaises(TypeError):
            alerts.fmt_money(1.5, [])  # money is never a binary float

    def test_pct(self) -> None:
        # A Decimal prints as the engine compared it (more than two decimals only at a threshold, decision 15 as
        # refined by the lens-1 review); a float is rounded half-even to two decimals.
        for value, text in ((Decimal("95.32"), "95.32 %"), (Decimal("95.3216"), "95.3216 %"), (0.52125, "0.52 %"),
                            (52.135, "52.14 %"), (Decimal("-5.2"), "-5.20 %"), (Decimal("80"), "80.00 %"),
                            (26, "26.00 %")):
            emitted: List[str] = []
            self.assertEqual(text, alerts.fmt_pct(value, emitted))
            self.assertEqual([text.split(" ")[0]], emitted)

    def test_days(self) -> None:
        for args, text in (((13, "trading"), "13 trading days"), ((1, "calendar"), "1 calendar day"),
                           ((0, "trading"), "0 trading days"), ((3, None), "3 days"), ((Decimal("21"), "trading"),
                                                                                       "21 trading days")):
            emitted: List[str] = []
            self.assertEqual(text, alerts.fmt_days(args[0], emitted, args[1]))
            self.assertEqual([text.split(" ")[0]], emitted)

    def test_delta_and_multiple(self) -> None:
        for value, text in ((0.47695335020629354, "0.48"), (0.475, "0.48"), (0.465, "0.46"), (-0.27480438010564634,
                                                                                               "-0.27"),
                            (Decimal("0.5"), "0.50"), (0.9621482880991431, "0.96")):
            emitted: List[str] = []
            self.assertEqual(text, alerts.fmt_delta(value, emitted))
            self.assertEqual([text], emitted)
        emitted = []
        self.assertEqual("2.10", alerts.fmt_multiple(Decimal("2.1"), emitted))
        self.assertEqual(["2.10"], emitted)

    def test_counts_minutes_delay_strike(self) -> None:
        emitted: List[str] = []
        self.assertEqual("2", alerts.fmt_count(2, emitted))
        self.assertEqual("19 min", alerts.fmt_minutes(19, emitted))
        self.assertEqual("15-min delayed", alerts.fmt_delay(15, emitted))
        self.assertEqual("105", alerts.fmt_strike(Decimal("105.000"), emitted))
        self.assertEqual("6.5", alerts.fmt_strike(Decimal("6.500"), emitted))
        self.assertEqual(["2", "19", "15", "105", "6.5"], emitted)
        for bad in (True, -1, 1.0):
            with self.assertRaises((TypeError, ValueError)):
                alerts.fmt_count(bad, [])

    def test_thresholds_print_as_the_owner_wrote_them(self) -> None:
        for args, text in (((80, "percent"), "80 %"), ((Decimal("0.30"), "delta"), "0.30"),
                           ((21, "days", "trading"), "21 trading days"), ((Decimal("-1000"), "usd"), "-$1,000.00"),
                           ((Decimal("2"), "multiple"), "2"), ((Decimal("-5"), "percent"), "-5 %"),
                           ((Decimal("1E+2"), "percent"), "100 %")):
            emitted: List[str] = []
            self.assertEqual(text, alerts.fmt_threshold(args[0], args[1], emitted, *args[2:]))
            self.assertEqual([token.text for token in alert_lint.numeric_tokens(text)], emitted)

    def test_reason_emits_only_what_the_whitelist_leaves(self) -> None:
        emitted: List[str] = []
        text = "row 4 WMT261120C00105000: no usable mark on 2026-11-20 at 19:31 UTC (AG-20261020-01)"
        self.assertEqual(text, alerts.fmt_reason(text, emitted))
        self.assertEqual(["4", "261120", "00105000"], emitted)

    def test_footer_formatter(self) -> None:
        emitted: List[str] = []
        stamp = datetime.datetime(2026, 11, 20, 19, 31, 12, tzinfo=UTC)
        self.assertEqual(FOOTER_1931, alerts.fmt_footer(stamp, 15, emitted))
        self.assertEqual([], emitted, "the footer's 15 min is whitelisted (decision 6): nothing to emit")
        self.assertTrue(alerts.fmt_footer(stamp, 20, emitted).endswith("data delayed 20 min, as of 19:31 UTC"))
        self.assertEqual(["20"], emitted)

    def test_plan_words(self) -> None:
        self.assertEqual({"close": "close", "roll_review": "review a roll", "hold_review": "review and decide"},
                         alerts.PLAN_WORDS)
        self.assertEqual(("exit_signal", "watch", "unavailable", "config_changed", "rulebook_changed", "pre_close",
                          "watch_alive"), alerts.KINDS)

    def test_no_float_or_print_in_the_module(self) -> None:
        with open(MODULE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("float(", source)
        self.assertNotIn("print(", source)


class ExitSignalTest(DocumentCase):
    def test_wmt_expiry_day_golden(self) -> None:
        evaluation = expiry_day_evaluation()
        alert = alerts.exit_signal_text(evaluation.groups[0], evaluation.groups[0].fired[0], self.doc("expiry_day"),
                                        RUN_INFO)
        self.assertEqual("\n".join([
            "EXIT SIGNAL AG-20261020-01 | WMT covered call | your rule TP80 (rules 1.0.0, sha 3f9a01bc) fired: credit "
            "captured 95.32 % (your threshold: at or above 80 %); quote grade E: the conservative side stands | quote "
            "grade E, Tradier sandbox, 15-min delayed, as of 19:31 UTC | your plan: close | " + NOT_AN_INSTRUCTION,
            "also fired: your rule DTE21: time to expiry 0 trading days (your threshold: at or below 21 trading days) | "
            "your plan: review a roll",
            "also fired: your rule PIN3: distance from the strike 0.07 % (your threshold: at or below 3 %) and time to "
            "expiry 0 trading days (your threshold: at or below 3 trading days) | your plan: review and decide",
            "suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP",
            FOOTER_1931]), alert.text)
        self.assertEqual("exit_signal", alert.kind)
        self.assertEqual("AG-20261020-01", alert.group_id)
        self.assertEqual("EXIT_SIGNAL", alert.verdict)
        self.assertRegex(alert.text.split("\n")[0], PLAN_SHAPE)
        self.assertEqual(["TP80", "DTE21", "PIN3", "DELTA_WATCH", "DELTA_SIGNAL", "CAP"], alert.rule_ids)
        self.assert_lints(alert)
        self.assertEqual(FOOTER_1931, alert.footer)
        self.assertEqual(alert.text, alert.body + "\n" + alert.footer)

    def test_signal_record(self) -> None:
        evaluation = expiry_day_evaluation()
        alert = alerts.exit_signal_text(evaluation.groups[0], evaluation.groups[0].fired[0], self.doc("expiry_day"),
                                        RUN_INFO)
        record = alert.signal_record
        self.assertEqual(signals.SIGNAL_KEYS, tuple(record))
        self.assertIsNone(record["signal_id"])
        self.assertEqual(("AG-20261020-01", "covered_call", "EXIT_SIGNAL", "close", "unavailable", 18, 0,
                          RUN_INFO["posted_at"]),
                         (record["trade_id"], record["structure"], record["verdict"], record["plan"],
                          record["data_quality"], record["quote_age_at_post"], record["run_lateness_min"],
                          record["posted_at"]))
        self.assertEqual(["TP80", "DTE21", "PIN3"], [reason["rule_id"] for reason in record["reasons"]])
        first = record["reasons"][0]
        self.assertEqual({"rule_id": "TP80", "rule_sha": "tp80-sha", "result": "fired", "metric": "pct_captured",
                          "value": Decimal("95.32"), "threshold": 80, "unit": "percent", "verdict": "EXIT_SIGNAL",
                          "plan": "close", "text": "credit captured 95.32 % (your threshold: at or above 80 %); quote "
                                                   "grade E: the conservative side stands"}, first)

    def test_nvda_expiry_day_golden_with_lateness(self) -> None:
        evaluation = expiry_day_evaluation()
        group = evaluation.groups[1]
        run_info = dict(RUN_INFO, run_lateness_min=3)
        alert = alerts.exit_signal_text(group, group.fired[2], self.doc("expiry_day"), run_info)
        self.assertEqual("\n".join([
            "EXIT SIGNAL AG-20261022-02 | NVDA covered call | your rule DELTA_SIGNAL (rules 1.0.0, sha 3f9a01bc) fired: "
            "absolute delta 0.96 (your threshold: at or above 0.50) | quote grade B, Tradier sandbox, 15-min delayed, as "
            "of 19:31 UTC, run 3 min late | your plan: review a roll | " + NOT_AN_INSTRUCTION,
            "also fired: your rule DTE21: time to expiry 0 trading days (your threshold: at or below 21 trading days) | "
            "your plan: review a roll",
            "also fired: your rule DELTA_WATCH: absolute delta 0.96 (your threshold: at or above 0.30) | your plan: "
            "review and decide",
            "also fired: your rule PIN3: distance from the strike 2.17 % (your threshold: at or below 3 %) and time to "
            "expiry 0 trading days (your threshold: at or below 3 trading days) | your plan: review and decide",
            "also fired: your rule CAP: stock above the short call strike: yes (your threshold: yes) and time to expiry "
            "0 trading days (your threshold: at or below 21 trading days) | your plan: review a roll",
            "suppressed by your row tags: EARN2",
            FOOTER_1931]), alert.text)
        self.assertRegex(alert.text.split("\n")[0], PLAN_SHAPE)
        self.assert_lints(alert)
        self.assertEqual(3, alert.signal_record["run_lateness_min"])

    def test_lateness_zero_or_unknown_prints_nothing(self) -> None:
        evaluation = expiry_day_evaluation()
        for lateness in (0, None):
            alert = alerts.exit_signal_text(evaluation.groups[0], evaluation.groups[0].fired[0],
                                            self.doc("expiry_day"), dict(RUN_INFO, run_lateness_min=lateness))
            self.assertNotIn("late", alert.text)

    def test_first_observation_sentence(self) -> None:
        evaluation = expiry_day_evaluation()
        primary = evaluation.groups[0].fired[0]
        primary.first_observation = True
        alert = alerts.exit_signal_text(evaluation.groups[0], primary, self.doc("expiry_day"), RUN_INFO)
        self.assertIn(" | already past this threshold at the desk's first observation | quote grade E,",
                      alert.text.split("\n")[0])
        self.assert_lints(alert)

    def test_loss_multiple_prints_both_multiples(self) -> None:
        evaluation = expiry_day_evaluation()
        group = evaluation.groups[0]
        lossx = rule("LOSSX", "fired", "loss_multiple_of_credit", Decimal("2.1"), 2, "multiple", verdict="EXIT_SIGNAL",
                     plan="close")
        group.fired = [lossx]
        alert = alerts.exit_signal_text(group, lossx, self.doc("expiry_day"), RUN_INFO)
        self.assertIn("your rule LOSSX (rules 1.0.0, sha 3f9a01bc) fired: loss 2.10 x credit = cost-to-close 3.10 x "
                      "credit (your threshold: at or above 2 x credit) | ", alert.text)
        self.assert_lints(alert)

    def test_unavailable_and_pending_lines_on_an_exit_signal(self) -> None:
        evaluation = expiry_day_evaluation()
        group = evaluation.groups[0]
        group.unavailable = [rule("EXDIV", "unavailable", "days_to_ex_dividend", None, 5, "days", op="<=",
                                  verdict="EXIT_SIGNAL", plan="roll_review", reason="dividend unknown: unknown is never zero")]
        group.pending = [rule("TP50", "pending", "pct_captured", Decimal("95.32"), 50, "percent", verdict="EXIT_SIGNAL",
                              plan="close")]
        alert = alerts.exit_signal_text(group, group.fired[0], self.doc("expiry_day"), RUN_INFO)
        lines = alert.text.split("\n")
        self.assertIn("unavailable: your rule EXDIV: dividend unknown: unknown is never zero", lines)
        self.assertIn("pending confirmation: your rule TP50: credit captured 95.32 % (your threshold: at or above 50 %)",
                      lines)
        self.assertLess(lines.index("unavailable: your rule EXDIV: dividend unknown: unknown is never zero"),
                        lines.index("suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP"))
        self.assert_lints(alert)

    def test_a_changed_digit_fails_the_lint(self) -> None:
        evaluation = expiry_day_evaluation()
        alert = alerts.exit_signal_text(evaluation.groups[0], evaluation.groups[0].fired[0], self.doc("expiry_day"),
                                        RUN_INFO)
        for old, new in (("95.32 %", "95.33 %"), ("at or above 80 %", "at or above 70 %"), ("15-min", "16-min"),
                         ("0 trading days (your threshold: at or below 21", "1 trading days (your threshold: at or below 21")):
            tampered = alert.text.replace(old, new, 1)
            self.assertNotEqual(tampered, alert.text)
            self.assertFalse(alert_lint.check(tampered, alert.emitted_numbers, rule_ids=alert.rule_ids).ok, old)


class RenderTest(DocumentCase):
    def test_render_expiry_day(self) -> None:
        rendered = alerts.render(expiry_day_evaluation(), self.doc("expiry_day"), RUN_INFO)
        self.assertEqual([("exit_signal", "AG-20261020-01"), ("exit_signal", "AG-20261022-02")],
                         [(alert.kind, alert.group_id) for alert in rendered])
        self.assertIn("your rule DELTA_SIGNAL (rules 1.0.0, sha 3f9a01bc) fired:", rendered[1].text)
        for alert in rendered:
            self.assert_lints(alert)

    def test_dict_shaped_evaluation_renders_the_same(self) -> None:
        for name, evaluation in (("expiry_day", expiry_day_evaluation()), ("regular", regular_evaluation()),
                                 ("stale", stale_evaluation())):
            with self.subTest(name=name):
                as_objects = alerts.render(evaluation, self.doc(name), RUN_INFO)
                as_plain = alerts.render(as_dicts(evaluation), self.doc(name), RUN_INFO)
                self.assertEqual([a.text for a in as_objects], [a.text for a in as_plain])
                self.assertEqual([a.emitted_numbers for a in as_objects], [a.emitted_numbers for a in as_plain])

    def test_watch_golden_regular(self) -> None:
        rendered = alerts.render(regular_evaluation(), self.doc("regular"), RUN_INFO)
        self.assertEqual([("watch", "AG-20261020-01"), ("watch", "AG-20261022-02"), ("watch", None)],
                         [(alert.kind, alert.group_id) for alert in rendered])
        self.assertEqual("\n".join([
            "WATCH AG-20261022-02 | NVDA covered call | your rule DTE21 (rules 1.0.0, sha 3f9a01bc) fired: time to expiry "
            "13 trading days (your threshold: at or below 21 trading days) | quote grade C, Tradier sandbox, 15-min "
            "delayed, as of 19:31 UTC | your plan: review a roll | " + NOT_AN_INSTRUCTION,
            "pending confirmation: your rule DELTA_WATCH: absolute delta 0.48 (your threshold: at or above 0.30); "
            "observation 1 of 2",
            "suppressed by your row tags: EARN2",
            FOOTER_1931]), rendered[1].text)
        self.assertRegex(rendered[0].text.split("\n")[0], PLAN_SHAPE)
        self.assertEqual("WATCH", rendered[1].signal_record["verdict"])
        self.assertEqual("roll_review", rendered[1].signal_record["plan"])
        for alert in rendered:
            self.assert_lints(alert)

    def test_portfolio_watch_golden(self) -> None:
        rendered = alerts.render(regular_evaluation(), self.doc("regular"), RUN_INFO)
        portfolio = rendered[-1]
        self.assertEqual("\n".join([
            "WATCH portfolio | all open groups | your rule CONC (rules 1.0.0, sha 3f9a01bc) fired: largest concentration "
            "by underlying 26.32 % (your threshold: at or above 25 %) | quote grade C, Tradier sandbox, 15-min delayed, "
            "as of 19:31 UTC | your plan: review and decide | " + NOT_AN_INSTRUCTION,
            "no history: your rules MOVE5_DOWN, MOVE5_UP, DAILY need the previous end-of-day snapshot",
            FOOTER_1931]), portfolio.text)
        self.assertEqual(("portfolio", "portfolio", "WATCH"), (portfolio.signal_record["trade_id"],
                                                              portfolio.signal_record["structure"],
                                                              portfolio.signal_record["verdict"]))
        self.assert_lints(portfolio)

    def test_portfolio_without_a_fired_rule_posts_nothing(self) -> None:
        evaluation = regular_evaluation()
        evaluation.portfolio.fired = []
        rendered = alerts.render(evaluation, self.doc("regular"), RUN_INFO)
        self.assertEqual(["AG-20261020-01", "AG-20261022-02"], [a.group_id for a in rendered])

    def test_portfolio_as_a_plain_list_with_history_sentences(self) -> None:
        evaluation = regular_evaluation()
        evaluation.portfolio = list(evaluation.portfolio.fired)
        evaluation.underlyings = []
        evaluation.no_history_rule_ids = []
        evaluation.no_history = ["MOVE5_DOWN, MOVE5_UP and DAILY need the previous end-of-day snapshot (none for "
                                 "2026-11-02)"]
        portfolio = alerts.render(evaluation, self.doc("regular"), RUN_INFO)[-1]
        self.assertEqual("no history: MOVE5_DOWN, MOVE5_UP and DAILY need the previous end-of-day snapshot (none for "
                         "2026-11-02)", portfolio.text.split("\n")[1])
        self.assert_lints(portfolio)

    def test_an_underlying_rule_names_its_subject(self) -> None:
        evaluation = regular_evaluation()
        evaluation.portfolio.fired = []
        evaluation.underlyings[2].no_history = []
        evaluation.underlyings[2].fired = [rule("MOVE5_DOWN", "fired", "move_pct_from_prev_eod", Decimal("-5.2"), -5,
                                                "percent", op="<=")]
        evaluation.no_history_rule_ids = ["MOVE5_DOWN", "MOVE5_UP", "DAILY"]
        portfolio = alerts.render(evaluation, self.doc("regular"), RUN_INFO)[-1]
        self.assertEqual("WATCH portfolio | all open groups | your rule MOVE5_DOWN (rules 1.0.0, sha 3f9a01bc) fired: WMT "
                         "move since the previous end-of-day snapshot -5.20 % (your threshold: at or below -5 %) | quote "
                         "grade C, Tradier sandbox, 15-min delayed, as of 19:31 UTC | your plan: review and decide | "
                         + NOT_AN_INSTRUCTION, portfolio.text.split("\n")[0])
        self.assertIn("no history: your rules MOVE5_DOWN, MOVE5_UP, DAILY need the previous end-of-day snapshot",
                      portfolio.text)
        self.assert_lints(portfolio)

    def test_given_conditions_fall_back_to_the_rulebook(self) -> None:
        evaluation = expiry_day_evaluation()
        expected = alerts.render(evaluation, self.doc("expiry_day"), RUN_INFO)[0].text
        pin = evaluation.groups[0].fired[2]
        evaluation.rulebook = {"rules": [{"id": "PIN3", "verdict": "WATCH", "plan": "hold_review", "when": [
            {"metric": "pct_from_strike", "op": "<=", "value": 3, "unit": "percent"},
            {"metric": "dte", "op": "<=", "value": 3, "dte_unit": "trading"}]}]}
        pin.conditions = [Condition("pct_from_strike", None, Decimal("0.07"), None, None),
                          Condition("dte", None, 0, None, None)]
        self.assertEqual(expected, alerts.render(evaluation, self.doc("expiry_day"), RUN_INFO)[0].text)

    def test_unavailable_banner_golden(self) -> None:
        rendered = alerts.render(stale_evaluation(), self.doc("stale"), RUN_INFO)
        self.assertEqual(1, len(rendered))
        alert = rendered[0]
        self.assertEqual("\n".join([
            "UNAVAILABLE AG-20261020-01 | WMT covered call | rules 1.0.0, sha 3f9a01bc: no verdict while an input is "
            "unavailable; this is not HOLD | quote grade F, Tradier sandbox, 15-min delayed, as of 18:50 UTC | "
            + NOT_AN_INSTRUCTION,
            "unavailable: your rule TP80: quote grade F: stale quote (60 min old)",
            "unavailable: your rule USTOP: no usable underlying mark (WMT grade F)",
            "also fired: your rule DTE21: time to expiry 13 trading days (your threshold: at or below 21 trading days) | "
            "your plan: review a roll",
            "suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP",
            FOOTER_1931.replace("19:31", "18:50")]), alert.text)
        self.assertEqual(("unavailable", "UNAVAILABLE"), (alert.kind, alert.verdict))
        self.assertIsNone(alert.signal_record["plan"])
        self.assertEqual("UNAVAILABLE", alert.signal_record["verdict"])
        self.assertFalse(alert.text.startswith("HOLD"))
        self.assertIn("; this is not HOLD | ", alert.text)
        self.assert_lints(alert)

    def test_quarantined_group_banner(self) -> None:
        document = copy.deepcopy(self.doc("regular"))
        document["portfolio"]["quarantined_groups"] = [
            {"group_id": "AG-20261105-07", "reasons": ["row 12 quantity: '2x' is not a decimal number"]}]
        verdict = GroupVerdict("AG-20261105-07", "UNAVAILABLE", [], [], [], [], [])
        alert = alerts.unavailable_banner(verdict, document, RUN_INFO)
        self.assertEqual("\n".join([
            "UNAVAILABLE AG-20261105-07 | quarantined in positions.csv | rules 1.0.0, sha 3f9a01bc: no verdict while an "
            "input is unavailable; this is not HOLD | quote grade F, Tradier sandbox, 15-min delayed, as of 19:31 UTC | "
            + NOT_AN_INSTRUCTION,
            "unavailable: row 12: the quantity cell cannot be used (positions_check.py shows it)",
            FOOTER_1931]), alert.text)
        self.assertEqual(("quarantined", "unavailable"), (alert.signal_record["structure"],
                                                         alert.signal_record["data_quality"]))
        self.assert_lints(alert)

    def test_group_without_a_trade_id_uses_its_group_id(self) -> None:
        document = copy.deepcopy(self.doc("regular"))
        group = document["groups"][0]
        group["group_id"] = "ROW-3"
        group["trade_id"] = None
        evaluation = regular_evaluation()
        evaluation.groups[0].group_id = "ROW-3"
        alert = alerts.render(evaluation, document, RUN_INFO)[0]
        self.assertTrue(alert.text.startswith("WATCH ROW-3 | WMT covered call | your rule DTE21"), alert.text)
        self.assertEqual("ROW-3", alert.signal_record["trade_id"])
        self.assert_lints(alert)

    def test_rulebook_supplies_plan_verdict_and_ops(self) -> None:
        evaluation = expiry_day_evaluation()
        expected = [alert.text for alert in alerts.render(evaluation, self.doc("expiry_day"), RUN_INFO)]
        rules_by_id: Dict[str, Dict[str, Any]] = {}
        for group in evaluation.groups:
            for result in group.fired:
                when = ([{"metric": c.metric, "op": c.op, "value": c.threshold, "unit": c.unit, "dte_unit": c.dte_unit}
                         for c in result.conditions] if result.conditions else
                        [{"metric": result.metric, "op": result.op, "value": result.threshold, "unit": result.unit,
                          "dte_unit": result.dte_unit}])
                rules_by_id[result.rule_id] = {"id": result.rule_id, "verdict": result.verdict, "plan": result.plan,
                                               "when": when}
                result.plan = result.verdict = result.op = result.dte_unit = None
                if result.conditions:
                    for condition in result.conditions[1:]:
                        result.inputs.append(Input(condition.metric, condition.value, "book", None))
                    result.conditions = None
        evaluation.rulebook = {"rules": list(rules_by_id.values())}
        self.assertEqual(expected, [alert.text for alert in alerts.render(evaluation, self.doc("expiry_day"),
                                                                          RUN_INFO)])

    def test_an_exit_signal_without_a_plan_is_refused(self) -> None:
        evaluation = expiry_day_evaluation()
        evaluation.groups[0].fired[0].plan = None
        with self.assertRaises(ValueError):
            alerts.render(evaluation, self.doc("expiry_day"), RUN_INFO)

    def test_run_info_is_validated(self) -> None:
        for bad in (dict(RUN_INFO, rulebook_sha="not-hex"), dict(RUN_INFO, rulebook_sha=SHA[:7]),
                    dict(RUN_INFO, rulebook_version="v1.0.0"), dict(RUN_INFO, rulebook_version="1.0")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    alerts.render(regular_evaluation(), self.doc("regular"), bad)

    def test_a_group_without_a_verdict_posts_nothing_and_an_unknown_verdict_is_refused(self) -> None:
        evaluation = regular_evaluation()
        evaluation.groups.append(GroupVerdict("AG-20261001-04", None, [], [], [], [], []))  # type: ignore[arg-type]
        self.assertEqual(3, len(alerts.render(evaluation, self.doc("regular"), RUN_INFO)))
        self.assertEqual({"EXIT_SIGNAL": 0, "UNAVAILABLE": 0, "WATCH": 2, "HOLD": 1}, alerts.verdict_counts(evaluation))
        evaluation.groups[-1].verdict = "MAYBE"
        with self.assertRaises(ValueError):
            alerts.render(evaluation, self.doc("regular"), RUN_INFO)
        with self.assertRaises(ValueError):
            alerts.verdict_counts(evaluation)

    def test_numeric_strings_read_as_exact_decimals(self) -> None:
        evaluation = expiry_day_evaluation()
        expected = alerts.render(evaluation, self.doc("expiry_day"), RUN_INFO)[0].text
        primary = evaluation.groups[0].fired[0]
        primary.value, primary.threshold = "95.32", "80"
        self.assertEqual(expected, alerts.render(evaluation, self.doc("expiry_day"), RUN_INFO)[0].text)
        self.assertEqual("-$1,000.00", alerts.fmt_money("-1000", []))
        for bad in ("abc", "NaN", "1e999999999"):
            with self.assertRaises(ValueError):
                alerts.fmt_money(bad, [])

    def test_verdict_counts(self) -> None:
        self.assertEqual({"EXIT_SIGNAL": 0, "UNAVAILABLE": 0, "WATCH": 2, "HOLD": 1},
                         alerts.verdict_counts(regular_evaluation()))
        self.assertEqual({"EXIT_SIGNAL": 2, "UNAVAILABLE": 0, "WATCH": 0, "HOLD": 1},
                         alerts.verdict_counts(as_dicts(expiry_day_evaluation())))


class OtherMessagesTest(DocumentCase):
    def test_config_changed_golden(self) -> None:
        old = "1a2b3c4d" + "0" * 56
        new = "9f8e7d6c" + "1" * 56
        positions = "c0ffee12" + "2" * 56
        alert = alerts.config_changed_text([("desk_toml", old, new), ("positions_csv", None, positions)],
                                           self.doc("regular"), RUN_INFO)
        self.assertEqual("\n".join([
            "CONFIG CHANGED | desk.toml: sha 1a2b3c4d -> sha 9f8e7d6c | positions.csv: not acknowledged -> sha c0ffee12 | "
            "evaluated with the files as they are now | acknowledge with ack.py config --owner-tapped",
            FOOTER_1931]), alert.text)
        self.assertEqual("config_changed", alert.kind)
        self.assertIsNone(alert.signal_record)
        self.assert_lints(alert)
        dict_form = alerts.config_changed_text([{"name": "rulebook", "old": old, "new": new}], self.doc("regular"),
                                               RUN_INFO)
        self.assertTrue(dict_form.text.startswith("CONFIG CHANGED | adopted rulebook: sha 1a2b3c4d -> sha 9f8e7d6c |"))
        with self.assertRaises(ValueError):
            alerts.config_changed_text([("desk_toml", old, "not-a-sha")], self.doc("regular"), RUN_INFO)
        with self.assertRaises(ValueError):
            alerts.config_changed_text([], self.doc("regular"), RUN_INFO)

    def test_rulebook_changed_golden(self) -> None:
        run_info = dict(RUN_INFO, rulebook_version="1.1.0")
        cases = ((2, "1.0.0", "rulebook changed: 2 verdicts changed | rules 1.0.0 -> rules 1.1.0, sha 3f9a01bc"),
                 (1, "1.0.0", "rulebook changed: 1 verdict changed | rules 1.0.0 -> rules 1.1.0, sha 3f9a01bc"),
                 (0, None, "rulebook changed: 0 verdicts changed | rules 1.1.0, sha 3f9a01bc"),
                 (None, "1.0.0", "rulebook changed: digest unavailable (no snapshot) | rules 1.0.0 -> rules 1.1.0, sha "
                                 "3f9a01bc"))
        for changed, previous, first in cases:
            with self.subTest(changed=changed):
                alert = alerts.rulebook_changed_text(previous, changed, self.doc("regular"), run_info)
                self.assertEqual(first + "\n" + FOOTER_1931, alert.text)
                self.assertEqual("rulebook_changed", alert.kind)
                self.assert_lints(alert)

    def test_pre_close_golden(self) -> None:
        counts = alerts.verdict_counts(regular_evaluation())
        alert = alerts.pre_close_line(counts, 1, self.doc("regular"), RUN_INFO)
        self.assertEqual("\n".join([
            "pre-close | verdicts: 0 EXIT SIGNAL, 0 UNAVAILABLE, 2 WATCH, 1 HOLD | open signals: 1 | data age: 18 min | "
            "rules 1.0.0, sha 3f9a01bc | verify live quotes in your broker before acting", FOOTER_1931]), alert.text)
        self.assertEqual("pre_close", alert.kind)
        self.assert_lints(alert)
        unknown = alerts.pre_close_line(regular_evaluation(), [object(), object()], self.doc("regular"),
                                        dict(RUN_INFO, quote_age_min=None))
        self.assertIn("| open signals: 2 | data age: unknown |", unknown.text)
        self.assert_lints(unknown)

    def test_watch_alive_golden(self) -> None:
        alert = alerts.watch_alive_line(self.doc("regular"), RUN_INFO)
        self.assertEqual("watch alive | nothing posted since the open | rules 1.0.0, sha 3f9a01bc | data age: 18 min\n"
                         + FOOTER_1931, alert.text)
        self.assertEqual("watch_alive", alert.kind)
        self.assert_lints(alert)

    def test_compose_keeps_the_order_and_one_footer(self) -> None:
        config = alerts.config_changed_text([("desk_toml", "1a2b3c4d" + "0" * 56, "9f8e7d6c" + "1" * 56)],
                                            self.doc("expiry_day"), RUN_INFO)
        rendered = alerts.render(expiry_day_evaluation(), self.doc("expiry_day"), RUN_INFO)
        message = alerts.compose([config] + rendered)
        self.assertTrue(message.text.startswith("CONFIG CHANGED | desk.toml: sha 1a2b3c4d"))
        self.assertEqual(1, message.text.count("deterministic rules you adopted"))
        self.assertTrue(message.text.endswith(FOOTER_1931))
        self.assertEqual("exit_signal", message.kind)
        self.assertIsNone(message.signal_record)
        self.assertEqual(config.body + "\n" + rendered[0].body + "\n" + rendered[1].body + "\n" + FOOTER_1931,
                         message.text)
        self.assert_lints(message)
        with self.assertRaises(ValueError):
            alerts.compose([])

    def test_emitted_document(self) -> None:
        alert = alerts.watch_alive_line(self.doc("regular"), RUN_INFO)
        self.assertEqual({"emitted_numbers": alert.emitted_numbers, "rule_ids": alert.rule_ids,
                          "identifiers": ["rules 1.0.0", "sha 3f9a01bc"], "clocks": ["19:31 UTC"],
                          "as_of": "2026-11-03T19:31:12+00:00"}, alert.emitted_document())
        self.assertEqual((alert.emitted_numbers, alert.rule_ids),
                         alert_lint.load_emitted_document(alert.emitted_document()))



class FixtureGoldenTest(DocumentCase):
    """``tests/fixtures/alerts/pass_*`` are exactly what the builders render (the red-team fixtures derive from them,
    ``tests/engine/test_alert_lint.py``), so a change to the text shows up as a fixture diff."""

    def fixture(self, name: str) -> Tuple[str, Dict[str, Any]]:
        base = os.path.join(ROOT, "tests", "fixtures", "alerts", name)
        with open(base + ".txt", encoding="utf-8") as handle:
            text = handle.read()
        with open(base + ".emitted.json", encoding="utf-8") as handle:
            return text, json.load(handle)

    def test_pass_fixtures_are_the_rendered_goldens(self) -> None:
        expiry = alerts.render(expiry_day_evaluation(), self.doc("expiry_day"), RUN_INFO)
        regular = alerts.render(regular_evaluation(), self.doc("regular"), RUN_INFO)
        stale = alerts.render(stale_evaluation(), self.doc("stale"), RUN_INFO)
        config = alerts.config_changed_text([("desk_toml", "1a2b3c4d" + "0" * 56, "9f8e7d6c" + "1" * 56)],
                                            self.doc("expiry_day"), RUN_INFO)
        pre_close = alerts.pre_close_line(alerts.verdict_counts(regular_evaluation()), 1, self.doc("regular"), RUN_INFO)
        for name, alert in (("pass_exit_signal", expiry[0]), ("pass_watch", regular[1]), ("pass_unavailable", stale[0]),
                            ("pass_composed", alerts.compose([config] + expiry)), ("pass_pre_close", pre_close)):
            with self.subTest(name=name):
                text, emitted = self.fixture(name)
                self.assertEqual(alert.text + "\n", text)
                self.assertEqual(alert.emitted_document(), emitted)


# --------------------------------------------------------------------------------------------
# The reviews' findings on the alert text: exact per-share money, notes and cooldown lines, owner cell text never
# printed, the dated no-history clause, the strict emitted document, the withheld banner
# --------------------------------------------------------------------------------------------


def real_evaluation(fixture_set: str, now: str, next_run: Optional[str] = None, calendar_edit: Optional[Tuple[str, str]] = None,
                    state: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Any]:
    """``(book document, exit_rules evaluation)`` on a copy of the fixture desk with the adopted rules."""
    temporary = tempfile.mkdtemp(prefix="aegis-alerts-real-")
    try:
        root = os.path.join(temporary, "desk")
        shutil.copytree(FIXTURE_DESK, root)
        if calendar_edit is not None:
            path = os.path.join(root, "research", "calendar.toml")
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            assert text.count(calendar_edit[0]) == 1, calendar_edit[0]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text.replace(calendar_edit[0], calendar_edit[1]))
        market = quote_cache.open_market_data(root, now=contract.parse_utc_instant(now), offline=True,
                                              fixture_set=fixture_set, timeout_s=10.0, user_agent="aegis-desk-test")
        run = book.load_book(market)
        _adopted, rules = exit_rules.load_adopted_rulebook(desk_paths.for_root(root))
        evaluation = exit_rules.evaluate(rules, run.document, now=run.market.now, session=run.session,
                                         calendar=run.calendar, state=state or {},
                                         next_run_utc=None if next_run is None else contract.parse_utc_instant(next_run),
                                         rows=run.positions.rows)
        return run.document, evaluation
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


class ReviewFormatterTest(unittest.TestCase):
    def test_per_share_prices_print_exactly(self) -> None:
        for value, text in ((Decimal("-0.0050"), "-$0.0050"), (Decimal("0.2350"), "$0.2350"), (Decimal("0.12"), "$0.12"),
                            (Decimal("0"), "$0.00"), (Decimal("-0.1450"), "-$0.1450"), (Decimal("1234.5"), "$1,234.50")):
            emitted: List[str] = []
            self.assertEqual(text, alerts.fmt_price(value, emitted))
            self.assertEqual([text.replace("$", "")], emitted)
        with self.assertRaises(TypeError):
            alerts.fmt_price(0.25, [])

    def test_usd_thresholds_print_as_written(self) -> None:
        emitted: List[str] = []
        self.assertEqual("-$1,000.555", alerts.fmt_threshold(Decimal("-1000.555"), "usd", emitted))
        self.assertEqual("$0.00", alerts.fmt_threshold(Decimal("0"), "usd", emitted))

    def test_refined_percentages_and_multiples_keep_their_digits(self) -> None:
        emitted: List[str] = []
        self.assertEqual("0.0034 %", alerts.fmt_pct(Decimal("0.0034"), emitted))
        self.assertEqual("79.9960 %", alerts.fmt_pct(Decimal("79.9960"), emitted))
        self.assertEqual("1.9960", alerts.fmt_multiple(Decimal("1.9960"), emitted))
        self.assertEqual("95.32 %", alerts.fmt_pct(Decimal("95.32"), emitted), "two decimals stay two")


class ReviewRenderTest(DocumentCase):
    def assert_strict(self, alert: alerts.Alert) -> None:
        document = alert.emitted_document()
        self.assertEqual(set(alert_lint.EMITTED_DOCUMENT_KEYS), set(document))
        result = alert_lint.load_emitted(json.loads(json.dumps(document))).check(alert.text)
        self.assertTrue(result.ok, (result.problems, alert.text))

    def test_exdiv_prints_the_engines_exact_number(self) -> None:
        call = "WMT261120C00105000"
        edit = ("ex_dividend = { date = 2026-11-24", "ex_dividend = { date = 2026-11-23")
        document, evaluation = real_evaluation("t1", "2026-11-19T19:50:00Z", next_run="2026-11-19T21:35:00Z",
                                               calendar_edit=edit)
        wmt = evaluation.group("AG-20261020-01")
        exdiv = next(result for result in wmt.fired + wmt.suppressed if result.rule_id == "EXDIV")
        gap = exdiv.conditions[1].value
        self.assertEqual(Decimal("-0.1450"), gap)
        result = dataclasses.replace(exdiv, result="fired")
        group = dataclasses.replace(wmt, verdict="EXIT_SIGNAL", fired=[result], suppressed=[], pending=[])
        alert = alerts.exit_signal_text(group, result, document, RUN_INFO)
        self.assertIn("extrinsic at the bid less the dividend -$0.1450 per share (your threshold: below $0.00)", alert.text)
        self.assertNotIn(call, alert.text.split("\n")[0])
        self.assert_lints(alert)
        self.assert_strict(alert)

    def test_the_conservative_side_note_is_printed(self) -> None:
        document, evaluation = real_evaluation("expiry_day", "2026-11-20T19:50:00Z", next_run="2026-11-20T21:35:00Z")
        rendered = alerts.render(evaluation, document, RUN_INFO)
        wmt = next(alert for alert in rendered if alert.group_id == "AG-20261020-01")
        first = wmt.text.split("\n")[0]
        self.assertIn("credit captured 95.32 % (your threshold: at or above 80 %); quote grade E: the conservative side "
                      "stands | ", first)
        self.assertRegex(first, PLAN_SHAPE)
        for alert in rendered:
            self.assert_lints(alert)
            self.assert_strict(alert)

    def test_the_dated_no_history_clause(self) -> None:
        document, evaluation = real_evaluation("regular", "2026-11-03T19:50:00Z")
        portfolio = next(alert for alert in alerts.render(evaluation, document, RUN_INFO) if alert.group_id is None)
        self.assertIn("no history: your rules MOVE5_DOWN, MOVE5_UP, DAILY need the previous end-of-day snapshot (none "
                      "for 2026-11-02)", portfolio.text)
        self.assert_strict(portfolio)

    def test_a_cooling_rule_is_its_own_line(self) -> None:
        evaluation = expiry_day_evaluation()
        cooled = rule("TP50", "cooldown", "pct_captured", Decimal("95.32"), 50, "percent", verdict="EXIT_SIGNAL",
                      plan="close", reason="fired and cleared; true again before it re-arms at 2026-11-23 00:00 ET, "
                                           "so no new signal")
        evaluation.groups[0].cooldown = [cooled]  # type: ignore[attr-defined]
        alert = alerts.render(evaluation, self.doc("expiry_day"), RUN_INFO)[0]
        self.assertIn("\ncooling down: your rule TP50: credit captured 95.32 % (your threshold: at or above 50 %); fired "
                      "and cleared; true again before it re-arms at 2026-11-23 00:00 ET, so no new signal\n", alert.text)
        self.assert_lints(alert)
        self.assert_strict(alert)

    def test_owner_cell_text_is_never_printed(self) -> None:
        document = copy.deepcopy(self.doc("regular"))
        word = "so" + "ld"
        document["portfolio"]["quarantined_groups"] = [{"group_id": "AG-20261001-04", "reasons": [
            "row 9 status: status '%s' must be open or closed (AG-20261001-04)" % word,
            "row 9 note: 'café · x' is not usable", "account mismatch: Roth IRA, %s account" % word]}]
        verdict = GroupVerdict("AG-20261001-04", "UNAVAILABLE", [], [], [], [], [])
        alert = alerts.unavailable_banner(verdict, document, RUN_INFO)
        lines = alert.text.split("\n")
        self.assertEqual(["unavailable: row 9: the status cell cannot be used (positions_check.py shows it)",
                          "unavailable: row 9: the note cell cannot be used (positions_check.py shows it)",
                          "unavailable: account mismatch (positions_check.py shows the rows)"], lines[1:4])
        self.assertNotIn(word, alert.text)
        self.assert_lints(alert)
        self.assert_strict(alert)

    def test_the_withheld_banner(self) -> None:
        for kind, head in ((alerts.KIND_EXIT_SIGNAL, "EXIT SIGNAL AG-20261020-01"), (alerts.KIND_WATCH, "WATCH portfolio"),
                           (alerts.KIND_UNAVAILABLE, "UNAVAILABLE ROW-3")):
            trade = head.split(" ")[-1]
            alert = alerts.withheld_text(kind, trade, ["forbidden_language", "non_ascii"], self.doc("expiry_day"),
                                         RUN_INFO, group_id=trade)
            self.assertTrue(alert.text.startswith(head + " | rules 1.0.0, sha 3f9a01bc: the message text failed the "
                                                         "alert lint (forbidden_language, non_ascii) and is withheld (the "
                                                         "verdict and every rule are in exit_check.py)"),
                            alert.text)
            self.assertEqual(kind, alert.kind)
            self.assert_lints(alert)
            self.assert_strict(alert)
        odd = alerts.withheld_text(alerts.KIND_WATCH, "SO" + "LD-1", ["non_ascii"], self.doc("expiry_day"), RUN_INFO)
        self.assertTrue(odd.text.startswith("WATCH a group | "), odd.text)
        hold = alerts.withheld_text(alerts.KIND_WATCH, "AG-20261028-03", ["non_ascii"], self.doc("expiry_day"), RUN_INFO,
                                    head="HOLD")
        self.assertTrue(hold.text.startswith("HOLD AG-20261028-03 | "), hold.text)
        self.assert_strict(hold)

if __name__ == "__main__":
    unittest.main()
