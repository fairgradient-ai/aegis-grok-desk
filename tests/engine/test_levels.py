"""Threshold levels (``aegis_desk.levels``, PR 2d decisions D1-2 to D1-10): the tick table and its rounding, the
option, spread, underlying, model and date levels of every rule on a book, the status suffixes, the exact sheet of
the regular fixture set, the plan card, and the proof that each printed level is where the landed rule reading
turns (re-evaluated with ``exit_rules.evaluate`` at the printed price). The order words a sheet must never carry
are taken from the lint's own table, so this file spells none of them."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import copy
import datetime
import os
import re
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional

from aegis_desk import (
    alert_lint,
    alerts,
    book,
    calendar_events,
    contract,
    desk_config,
    desk_paths,
    exit_rules,
    greeks,
    levels,
    market_clock,
    quote_cache,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
LEVELS_SOURCE = os.path.join(ROOT, "scripts", "aegis_desk", "levels.py")
REGULAR_NOW = "2026-11-03T19:50:00Z"
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
WMT_EQUITY_ROW = ("equity,WMT,,200,,,,Brokerage (margin),AG-20261020-01,2026-10-20,,98.50,,open,,,,92.00,core holding "
                  "#horizon:2027-03\n")
STANDARD = desk_config.LevelsSection(order_shape_enabled=False)
D = Decimal

ALERT = "alert level, not an order: a limit at this price " + "exe" + "cutes immediately"
FOOTER = alert_lint.footer_line(datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=datetime.timezone.utc))
REGULAR_SHEET = [
    "THRESHOLD LEVELS 2026-11-03 | rules 1.0.0, sha cc09859c | book as of 19:31 UTC",
    "## AG-20261020-01 | WMT covered call | option ticks 0.05 below 3.00, 0.10 at or above (standard class: desk.toml "
    "[levels] names no penny class for WMT)",
    "TP50 cannot fire on this position: time to expiry 13 trading days (your threshold: above 21 trading days), and the "
    "days only fall",
    "TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); resting-eligible: yes "
    "(below the current ask 3.50)",
    "DTE21 fires on 2026-10-22 (21 trading days before the last trading day 2026-11-20); threshold already crossed (rule "
    "fired)",
    "PIN3 fires while WMT is between 101.94 and 108.25 (within 3 % of the strike 105) from 2026-11-17 (3 trading days "
    "before the last trading day 2026-11-20)",
    "EXDIV not applicable: the last trading day 2026-11-20 precedes the last cum-dividend day 2026-11-23 of the "
    "2026-11-24 ex-date",
    "AUTOEX cannot fire on this position: it reads long option legs only",
    "USTOP fires at WMT 92.00 or below (your stop in positions.csv; 11.73 % below the current 104.23)",
    "EARN2 fires from 2026-11-13 to 2026-11-17 (within 2 trading days of the earnings date 2026-11-17 in "
    "research/calendar.toml)",
    "suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP",
    "## AG-20261022-02 | NVDA covered call | option ticks 0.05 below 3.00, 0.10 at or above (standard class: desk.toml "
    "[levels] names no penny class for NVDA)",
    "TP50 cannot fire on this position: time to expiry 13 trading days (your threshold: above 21 trading days), and the "
    "days only fall",
    "TP80 fires at an option price of 0.60 (rounded down from 0.61 = credit 3.05 x (1 - 80 %)); resting-eligible: yes "
    "(below the current ask 3.30)",
    "DTE21 fires on 2026-10-22 (21 trading days before the last trading day 2026-11-20); threshold already crossed (rule "
    "fired)",
    "DELTA_WATCH fires near NVDA 144.69 (rounded up from 144.6801; model estimate at IV 27.80 %, date fixed); threshold "
    "already crossed (pending confirmation: observation 1 of 2)",
    "DELTA_SIGNAL fires near NVDA 149.33 (rounded down from 149.3308; model estimate at IV 27.80 %, date fixed; 0.27 % "
    "above the current 148.925, 0.05 sigma)",
    "PIN3 fires while NVDA is between 145.63 and 154.64 (within 3 % of the strike 150) from 2026-11-17 (3 trading days "
    "before the last trading day 2026-11-20)",
    "EXDIV not applicable: the last trading day 2026-11-20 precedes the last cum-dividend day 2026-12-02 of the "
    "2026-12-03 ex-date",
    "CAP fires at NVDA 150.01 or above (one cent above the strike 150; 0.73 % above the current 148.925)",
    "AUTOEX cannot fire on this position: it reads long option legs only",
    "USTOP not applicable: no underlying_stop on group AG-20261022-02",
    "suppressed by your row tags: EARN2",
    "## AG-20261028-03 | SPY protective put | option ticks 0.05 below 3.00, 0.10 at or above (standard class: desk.toml "
    "[levels] names no penny class for SPY)",
    "LAPSE14 fires on 2026-12-04 (14 calendar days before the last trading day 2026-12-18)",
    # ROLL30 at a line start reads as an imperative to the lint (IS04): the line names it "your rule ROLL30"
    "your rule ROLL30 fires on 2026-11-18 (30 calendar days before the last trading day 2026-12-18)",
    "MONETIZE fires near SPY 572.79 (rounded up from 572.7826; model estimate at IV 24.68 %, date fixed; 11.62 % below "
    "the current 648.12, 1.34 sigma)",
    "FLOOR fires at SPY 619.99 or below (one cent below the strike 620; 4.34 % below the current 648.12)",
    "AUTOEX fires at SPY 619.99 or below (one cent below the strike 620; 4.34 % below the current 648.12) on the last "
    "trading day 2026-12-18",
    "USTOP not applicable: no underlying_stop on group AG-20261028-03",
    "## underlyings and portfolio",
    "MOVE5_DOWN, MOVE5_UP, DAILY: no level: no previous end-of-day snapshot (none for 2026-11-02)",
    "CONC has no single price level (it reads largest concentration by underlying, now 26.32 %; your threshold: at or "
    "above 25 %); threshold already crossed (rule fired)",
    FOOTER,
]


# --------------------------------------------------------------------------------------------
# Books built once from copies of the fixture desk root (nothing under tests/fixtures/ is written)
# --------------------------------------------------------------------------------------------


class Built:
    """One book of a copied fixture root: the document, the session, the calendar, the rows and the rulebook."""

    def __init__(self, root: str, fixture_set: str, now: str) -> None:
        market = quote_cache.open_market_data(root, now=contract.parse_utc_instant(now), offline=True,
                                              fixture_set=fixture_set, timeout_s=5.0, user_agent="aegis-desk/test levels")
        run = book.load_book(market)
        self.document = run.document
        self.session = run.session
        self.calendar = run.calendar
        self.rows = list(run.positions.rows)
        self.now = market.now
        self.rulebook = exit_rules.load_adopted_rulebook(desk_paths.for_root(root))[1]

    def evaluate(self, document: Optional[Dict[str, Any]] = None, rulebook: Optional[exit_rules.Rulebook] = None,
                 rows: Any = "default", calendar: Any = "default") -> exit_rules.Evaluation:
        return exit_rules.evaluate(rulebook or self.rulebook, self.document if document is None else document,
                                   now=self.now, session=self.session,
                                   calendar=self.calendar if calendar == "default" else calendar, state={},
                                   next_run_utc=None, rows=self.rows if rows == "default" else rows)

    def sheet(self, document: Optional[Dict[str, Any]] = None, rulebook: Optional[exit_rules.Rulebook] = None,
              config: Any = STANDARD, rows: Any = "default", calendar: Any = "default") -> levels.Sheet:
        document = self.document if document is None else document
        rulebook = rulebook or self.rulebook
        calendar = self.calendar if calendar == "default" else calendar
        return levels.build_sheet(self.evaluate(document, rulebook, rows, calendar), document, rulebook,
                                  calendar=calendar, levels_config=config)

    def copy(self) -> Dict[str, Any]:
        return copy.deepcopy(self.document)


_TMP: Optional[tempfile.TemporaryDirectory] = None
REGULAR: Optional[Built] = None
VARIANT: Optional[Built] = None


def setUpModule() -> None:
    global _TMP, REGULAR, VARIANT
    _TMP = tempfile.TemporaryDirectory(prefix="aegis-levels-")
    base = os.path.realpath(_TMP.name)
    regular = os.path.join(base, "regular")
    shutil.copytree(FIXTURE_DESK, regular)
    REGULAR = Built(regular, "regular", REGULAR_NOW)
    variant = os.path.join(base, "variant")
    shutil.copytree(FIXTURE_DESK, variant)
    path = os.path.join(variant, "positions.csv")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert text.count(WMT_EQUITY_ROW) == 1
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text.replace(WMT_EQUITY_ROW, "", 1))
    VARIANT = Built(variant, "regular", REGULAR_NOW)


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def regular() -> Built:
    assert REGULAR is not None
    return REGULAR


def variant() -> Built:
    assert VARIANT is not None
    return VARIANT


_EXPIRY: Optional[Built] = None


def expiry_day() -> Built:
    """The expiry_day book (the WMT call quote grade E: the group is unavailable), built once on its own copy."""
    global _EXPIRY
    if _EXPIRY is None:
        assert _TMP is not None
        root = os.path.join(os.path.realpath(_TMP.name), "expiry_day")
        shutil.copytree(FIXTURE_DESK, root)
        _EXPIRY = Built(root, "expiry_day", "2026-11-20T19:50:00Z")
    return _EXPIRY


def leg_of(document: Dict[str, Any], row: int) -> Dict[str, Any]:
    return next(leg for leg in document["legs"] if leg["row_number"] == row)


def group_of(document: Dict[str, Any], group_id: str) -> Dict[str, Any]:
    return next(group for group in document["groups"] if group["group_id"] == group_id)


def line_of(sheet: levels.Sheet, group_id: Optional[str], rule_id: str) -> levels.LevelLine:
    if group_id is None:
        found = [line for section in sheet.sections for line in section.lines if line.rule_id == rule_id]
    else:
        found = [line for group in sheet.groups if group.group_id == group_id for line in group.lines
                 if line.rule_id == rule_id]
    assert len(found) == 1, (group_id, rule_id, found)
    return found[0]


def result_of(evaluation: exit_rules.Evaluation, group_id: str, rule_id: str) -> exit_rules.RuleResult:
    verdict = evaluation.group(group_id)
    assert verdict is not None
    for name in ("fired", "pending", "cooldown", "unavailable", "no_history", "suppressed", "clear"):
        for result in getattr(verdict, name):
            if result.rule_id == rule_id:
                return result
    raise AssertionError("%s has no result for %s" % (group_id, rule_id))


def rulebook_with(extra: str) -> exit_rules.Rulebook:
    """The fixture rulebook with ``extra`` rule blocks appended (a parsed candidate, never written to disk)."""
    path = os.path.join(FIXTURE_DESK, "rules", "exit_rules-1.0.0.toml")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return exit_rules.parse_rulebook_text(text + "\n" + extra, path="<test rulebook>")


def rule_block(rule_id: str, role: str, applies_to: str, when: str, verdict: str = "WATCH") -> str:
    return ('[[rule]]\nid = "%s"\ntitle = "test rule"\nrole = "%s"\nscope = "group"\napplies_to = [%s]\ninput_class = "M"\n'
            'when = [ %s ]\nverdict = "%s"\nplan = "hold_review"\norigin = "owner-entered"\nsource = "test"\n'
            % (rule_id, role, applies_to, when, verdict))


def order_words(text: str) -> List[str]:
    """Every lint-table phrase and the brief's five words found in ``text`` (empty for a clean sheet)."""
    found = [entry.code for entry in alert_lint.FORBIDDEN_LANGUAGE if entry.pattern.search(text)]
    words = ("bu" + "y", "se" + "ll", "G" + "TC", "da" + "y or" + "der", "wor" + "king")
    found.extend(word for word in words if re.search(r"\b%s\b" % word, text, re.IGNORECASE))
    return found


# --------------------------------------------------------------------------------------------
# The tick table (D1-2) and the price formatting (D1-3, D1-9)
# --------------------------------------------------------------------------------------------


class TickTableTest(unittest.TestCase):
    GOLDENS = {
        levels.CLASS_STANDARD: {"1.713": ("1.70", "1.75"), "3.126": ("3.10", "3.20"), "10.26": ("10.20", "10.30"),
                                "0.684": ("0.65", "0.70"), "2.993": ("2.95", "3.00"), "3.04": ("3.00", "3.10")},
        levels.CLASS_PENNY: {"1.713": ("1.71", "1.72"), "3.126": ("3.10", "3.15"), "10.26": ("10.25", "10.30"),
                             "0.684": ("0.68", "0.69"), "2.993": ("2.99", "3.00"), "3.04": ("3.00", "3.05")},
        levels.CLASS_PENNY_ALL: {"1.713": ("1.71", "1.72"), "3.126": ("3.12", "3.13"), "10.26": ("10.26", "10.26"),
                                 "0.684": ("0.68", "0.69"), "2.993": ("2.99", "3.00"), "3.04": ("3.04", "3.04")},
    }

    def test_the_table(self) -> None:
        self.assertEqual({"standard": (D("0.05"), D("0.10")), "penny": (D("0.01"), D("0.05")),
                          "penny_all": (D("0.01"), D("0.01"))}, levels.OPTION_TICKS)
        self.assertEqual((D("3.00"), D("0.01"), D("0.01"), D("0.0001")),
                         (levels.TICK_BREAK, levels.SPREAD_TICK, levels.STOCK_TICK, levels.SUB_DOLLAR_STOCK_TICK))
        self.assertEqual(("standard", "penny", "penny_all"), levels.TICK_CLASSES)

    def test_goldens_down_and_up(self) -> None:
        for tick_class, table in self.GOLDENS.items():
            for value, (down, up) in table.items():
                with self.subTest(tick_class=tick_class, value=value):
                    self.assertEqual(D(down), levels.round_option_price(D(value), tick_class, levels.ROUND_DOWN))
                    self.assertEqual(D(up), levels.round_option_price(D(value), tick_class, levels.ROUND_UP))
                    self.assertEqual(-2, levels.round_option_price(D(value), tick_class, levels.ROUND_UP).as_tuple().exponent)

    def test_the_brief_goldens_hold_for_the_penny_class(self) -> None:
        self.assertEqual(D("1.71"), levels.round_option_price(D("1.713"), levels.CLASS_PENNY, levels.ROUND_DOWN))
        self.assertEqual((D("3.10"), D("3.15")), (levels.round_option_price(D("3.126"), levels.CLASS_PENNY, "down"),
                                                  levels.round_option_price(D("3.126"), levels.CLASS_PENNY, "up")))

    def test_the_tick_follows_the_unrounded_price_and_3_00_is_on_every_grid(self) -> None:
        self.assertEqual(D("0.05"), levels.option_tick(D("2.999"), levels.CLASS_STANDARD))
        self.assertEqual(D("0.10"), levels.option_tick(D("3.00"), levels.CLASS_STANDARD))
        for tick_class in levels.TICK_CLASSES:
            for direction in (levels.ROUND_DOWN, levels.ROUND_UP):
                self.assertEqual(D("3.00"), levels.round_option_price(D("3"), tick_class, direction))
        # a standard-grid price is valid on every class's grid (D1-2): the desk never prints an off-tick price
        for cents in range(1, 1200):
            price = levels.round_option_price(D(cents) / 100, levels.CLASS_STANDARD, levels.ROUND_DOWN)
            for tick_class in levels.TICK_CLASSES:
                self.assertEqual(price, levels.round_option_price(price, tick_class, levels.ROUND_DOWN), price)

    def test_spread_and_stock_grids(self) -> None:
        self.assertEqual((D("0.48"), D("0.49")), (levels.round_spread_price(D("0.484"), "down"),
                                                  levels.round_spread_price(D("0.484"), "up")))
        self.assertEqual((D("572.78"), D("572.79")), (levels.round_stock_price(D("572.7826"), "down"),
                                                      levels.round_stock_price(D("572.7826"), "up")))
        self.assertEqual((D("0.4512"), D("0.4513")), (levels.round_stock_price(D("0.45123"), "down"),
                                                      levels.round_stock_price(D("0.45123"), "up")))
        self.assertEqual(D("1.00"), levels.round_stock_price(D("0.99999"), "up"))

    def test_refusals(self) -> None:
        with self.assertRaises(TypeError):
            levels.round_option_price(1.713, levels.CLASS_PENNY, "down")  # a binary float is never a price
        with self.assertRaises(ValueError):
            levels.round_option_price(D("1.713"), "nickel", "down")
        with self.assertRaises(ValueError):
            levels.round_option_price(D("1.713"), levels.CLASS_PENNY, "nearest")
        with self.assertRaises(ValueError):
            levels.round_to_tick(D("1.713"), D("0"), "down")

    def test_tick_class_for_a_root(self) -> None:
        config = desk_config.LevelsSection(False, penny_classes=("WMT",), penny_all_classes=("SPY",))
        self.assertEqual(("penny", "penny_all", "standard", "standard"),
                         (levels.tick_class_for("WMT", config), levels.tick_class_for("SPY", config),
                          levels.tick_class_for("NVDA", config), levels.tick_class_for("WMT", None)))


class FormatTest(unittest.TestCase):
    def test_level_prices_keep_every_decimal_and_at_least_two(self) -> None:
        emitted: List[str] = []
        self.assertEqual(["3.50", "0.684", "10.26", "149.3308", "0.4512", "92.00"],
                         [levels.fmt_level_price(value, emitted) for value in
                          (D("3.5"), D("0.684"), D("10.26"), D("149.3308"), D("0.4512"), D("92"))])
        self.assertEqual(["3.50", "0.684", "10.26", "149.3308", "0.4512", "92.00"], emitted)
        with self.assertRaises(TypeError):
            levels.fmt_level_price(3.5, emitted)
        self.assertNotIn("$", levels.fmt_level_price(D("10.26"), []))

    def test_exact_values_drop_trailing_zeros(self) -> None:
        emitted: List[str] = []
        # four decimals or fewer print as they are; a value with more prints six (2.2833 would hide that it is not)
        self.assertEqual(["0.61", "0.684", "3.00", "1.71", "2.283333", "0.65"],
                         [levels.fmt_exact_price(value, emitted) for value in
                          (D("0.610"), D("0.684"), D("3"), D("1.7100"), D("685") / D("300"), D("0.6500"))])
        self.assertEqual(["0.61", "0.684", "3.00", "1.71", "2.283333", "0.65"], emitted)


class DateTest(unittest.TestCase):
    def test_first_date_within(self) -> None:
        ltd = datetime.date(2026, 11, 20)
        self.assertEqual(datetime.date(2026, 10, 22), levels.first_date_within(ltd, 21, "trading"))
        self.assertEqual(datetime.date(2026, 11, 17), levels.first_date_within(ltd, 3, "trading"))
        self.assertEqual(ltd, levels.first_date_within(ltd, 0, "trading"))
        self.assertEqual(datetime.date(2026, 12, 4), levels.first_date_within(datetime.date(2026, 12, 18), 14, "calendar"))
        self.assertEqual(datetime.date(2026, 11, 13), levels.first_date_within(datetime.date(2026, 11, 17), 2, "trading"))
        # the first date with dte <= N is a trading day; the day before it reads N + 1
        for days in range(0, 40):
            first = levels.first_date_within(ltd, days, "trading")
            self.assertTrue(market_clock.is_trading_day(first))
            self.assertEqual(days, market_clock.dte(ltd, first, "trading"))
            self.assertGreater(market_clock.dte(ltd, first - datetime.timedelta(days=1), "trading"), days)
        # an owner-injected closure moves the trading date (2026-11-16 closed: one trading day fewer before 11-20)
        self.assertEqual(datetime.date(2026, 11, 16), levels.first_date_within(ltd, 4, "trading"))
        self.assertEqual(datetime.date(2026, 11, 13),
                         levels.first_date_within(ltd, 4, "trading", {datetime.date(2026, 11, 16): "closed"}))
        with self.assertRaises(ValueError):
            levels.first_date_within(ltd, -1, "trading")


# --------------------------------------------------------------------------------------------
# The regular fixture set (D1-8): the whole sheet, its lint, its JSON
# --------------------------------------------------------------------------------------------


class RegularSheetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sheet = regular().sheet()

    def test_the_whole_sheet(self) -> None:
        self.maxDiff = None
        self.assertEqual(REGULAR_SHEET, self.sheet.text.split("\n"))
        self.assertEqual(datetime.date(2026, 11, 3), self.sheet.et_date)

    def test_it_passes_the_strict_lint_and_carries_no_order_text(self) -> None:
        result = self.sheet.lint()
        self.assertTrue(result.ok, result.problems)
        self.assertGreater(result.numbers_checked, 40)
        document = self.sheet.emitted_document()
        self.assertEqual(set(alert_lint.EMITTED_DOCUMENT_KEYS), set(document))
        self.assertTrue(alert_lint.load_emitted(document).check(self.sheet.text).ok)
        self.assertEqual([], order_words(self.sheet.text))
        self.assertNotIn("price:", self.sheet.text)
        for old, new in (("TP80 fires", "TP90 fires"), ("sha cc09859c", "sha 00000110"), ("0.65 (", "0.66 (")):
            self.assertFalse(alert_lint.load_emitted(document).check(self.sheet.text.replace(old, new, 1)).ok, new)

    def test_every_line_starts_with_an_id_a_heading_or_a_section_word(self) -> None:
        for line in self.sheet.text.split("\n")[:-1]:
            first = line.split(" ", 1)[0]
            self.assertTrue(first in ("THRESHOLD", "##", "suppressed", "your") or exit_rules.RULE_ID_RE.match(
                first.rstrip(",:")), line)

    def test_a_rule_id_that_reads_as_an_imperative_never_starts_a_sentence(self) -> None:
        roll = "RO" + "LL30"
        self.assertEqual(("your rule " + roll, "rule " + roll, "TP80", "TP80"),
                         (levels.line_lead(roll), levels.list_item(roll), levels.line_lead("TP80"),
                          levels.list_item("TP80")))
        for word in alert_lint.IMPERATIVE_START_WORDS:
            rule_id = word.upper()[:12] + "9"
            text = "%s fires on 2026-11-18\nsuppressed by your row tags: TP80, %s\n%s" % (
                levels.line_lead(rule_id), levels.list_item(rule_id), FOOTER)
            self.assertTrue(alert_lint.check(text, [], rule_ids=["TP80", rule_id]).ok, rule_id)
            bare = "%s fires on 2026-11-18\n%s" % (rule_id, FOOTER)
            self.assertFalse(alert_lint.check(bare, [], rule_ids=[rule_id]).ok, rule_id)

    def test_group_counts_and_classes(self) -> None:
        counts = {group.group_id: (group.price_levels, group.resting_eligible, group.tick_class) for group in self.sheet.groups}
        self.assertEqual({WMT: (3, 1, "standard"), NVDA: (5, 1, "standard"), SPY: (3, 0, "standard")}, counts)
        self.assertEqual(["DELTA_WATCH", "DELTA_SIGNAL", "CAP"], self.sheet.group(WMT).suppressed)
        self.assertEqual(["EARN2"], self.sheet.group(NVDA).suppressed)

    def test_the_line_records(self) -> None:
        tp80 = line_of(self.sheet, WMT, "TP80").as_dict()
        self.assertEqual({"rule_id": "TP80", "kind": "option_price", "status": "resting_eligible", "level": D("0.65"),
                          "exact": D("0.684"), "rounding": "down", "close_side": "ask", "current": D("3.5"),
                          "model": None}, {key: tp80[key] for key in ("rule_id", "kind", "status", "level", "exact",
                                                                     "rounding", "close_side", "current", "model")})
        self.assertEqual(set(levels.LINE_KEYS), set(tp80))
        self.assertEqual(REGULAR_SHEET[3], tp80["text"])
        dte21 = line_of(self.sheet, WMT, "DTE21").as_dict()
        self.assertEqual(("date", "crossed_fired", None), (dte21["kind"], dte21["status"], dte21["level"]))
        self.assertEqual(datetime.date(2026, 10, 22), dte21["dates"]["fires_on"])
        self.assertEqual(datetime.date(2026, 11, 20), dte21["dates"]["last_trading_day"])
        pin = line_of(self.sheet, WMT, "PIN3").as_dict()
        self.assertEqual(("underlying_band", {"low": D("101.94"), "high": D("108.25")}, datetime.date(2026, 11, 17)),
                         (pin["kind"], pin["level"], pin["dates"]["from"]))
        model = line_of(self.sheet, NVDA, "DELTA_SIGNAL").as_dict()
        self.assertEqual(("underlying_model", "alert_level", D("149.33"), D("149.3308"), "down", D("148.925")),
                         (model["kind"], model["status"], model["level"], model["exact"].quantize(D("0.0001")),
                          model["rounding"], model["current"]))
        self.assertNotEqual(D("149.3308"), model["exact"], "the JSON exact value is the unrounded root, not the print")
        self.assertEqual({"iv", "tau_years", "r", "q", "delta_boundary"}, set(model["model"]))
        self.assertEqual(D("0.495"), model["model"]["delta_boundary"])
        self.assertEqual((0.0412, 0.0003), (model["model"]["r"], model["model"]["q"]))
        watch = line_of(self.sheet, NVDA, "DELTA_WATCH").as_dict()
        self.assertEqual(("crossed_pending", "up"), (watch["status"], watch["rounding"]))
        self.assertEqual(("cannot_fire", "none"), (line_of(self.sheet, WMT, "TP50").status,
                                                   line_of(self.sheet, WMT, "TP50").kind))
        self.assertEqual("not_applicable", line_of(self.sheet, WMT, "EXDIV").status)
        conc = line_of(self.sheet, None, "CONC")
        self.assertEqual(("none", "crossed_fired", None), (conc.kind, conc.status, conc.level))
        group = self.sheet.group(WMT).as_dict()
        self.assertEqual(set(levels.GROUP_KEYS), set(group))
        self.assertEqual((WMT, "WMT", "covered_call", "ok"), (group["trade_id"], group["underlying"], group["structure"],
                                                              group["data_quality"]))
        sections = [section.as_dict() for section in self.sheet.sections]
        self.assertEqual(set(levels.SECTION_KEYS), set(sections[0]))
        self.assertEqual(["MOVE5_DOWN", "MOVE5_UP", "DAILY", "CONC"], [rule for section in sections for rule in
                                                                         section["rule_ids"]])


# --------------------------------------------------------------------------------------------
# The printed level is where the landed reading turns (re-evaluated with exit_rules.evaluate)
# --------------------------------------------------------------------------------------------


class LandedReadingTest(unittest.TestCase):
    def condition(self, document: Dict[str, Any], group_id: str, rule_id: str, index: int = 0) -> str:
        return result_of(regular().evaluate(document), group_id, rule_id).conditions[index].truth

    def test_take_profit_levels_are_true_at_the_printed_price(self) -> None:
        for price, truth in ((D("0.65"), "true"), (D("0.684"), "true"), (D("0.69"), "false")):
            document = regular().copy()
            leg_of(document, 4)["fresh_ask"] = price
            self.assertEqual(truth, self.condition(document, WMT, "TP80"), price)

    def test_exact_underlying_levels_turn_at_the_printed_price(self) -> None:
        for symbol, group_id, rule_id, at, beside in (("WMT", WMT, "USTOP", "92.00", "92.01"),
                                                     ("NVDA", NVDA, "CAP", "150.01", "150.00"),
                                                     ("SPY", SPY, "FLOOR", "619.99", "620.00"),
                                                     ("WMT", WMT, "PIN3", "101.94", "101.93"),
                                                     ("WMT", WMT, "PIN3", "108.25", "108.26"),
                                                     ("NVDA", NVDA, "PIN3", "145.63", "145.62"),
                                                     ("NVDA", NVDA, "PIN3", "154.64", "154.65")):
            with self.subTest(rule=rule_id, at=at):
                for price, truth in ((at, "true"), (beside, "false")):
                    document = regular().copy()
                    document["underlyings"][symbol]["mark_mid"] = D(price)
                    self.assertEqual(truth, self.condition(document, group_id, rule_id), price)

    def test_the_model_level_is_the_reading_boundary(self) -> None:
        document = regular().document
        leg = leg_of(document, 6)
        rate, q = 0.0412, 0.0003
        root = levels.delta_level("CALL", D("148.925"), D("150"), leg["tau_years"], rate, q, leg["iv"], D("0.50"), ">=")
        assert root is not None
        delta = greeks.european_greeks("CALL", float(root), 150.0, leg["tau_years"], rate, q, leg["iv"]).delta
        self.assertLess(abs(abs(delta) - 0.495), 1e-6)
        self.assertEqual(D("149.3308"), root.quantize(D("0.0001")))
        truths = []
        for price in ("149.33", "149.34"):
            mutated = regular().copy()
            at = greeks.european_greeks("CALL", float(price), 150.0, leg["tau_years"], rate, q, leg["iv"])
            leg_of(mutated, 6)["greeks"] = dict(leg_of(mutated, 6)["greeks"], delta=at.delta)
            truths.append(self.condition(mutated, NVDA, "DELTA_SIGNAL"))
        self.assertEqual(["false", "true"], truths, "the printed level reads false, one tick further from the spot true")
        self.assertIsNone(levels.delta_level("CALL", D("148.925"), D("150"), leg["tau_years"], rate, q, leg["iv"],
                                             D("0.00"), "<"))
        self.assertEqual((D("0.295"), D("0.305"), D("0.305"), D("0.295")),
                         tuple(levels.delta_boundary(D("0.30"), op) for op in (">=", ">", "<=", "<")))

    def test_the_loss_level_is_at_most_one_tick_early(self) -> None:
        for price, truth in ((D("10.20"), "false"), (D("10.26"), "true"), (D("10.30"), "true")):
            document = variant().copy()
            group_of(document, WMT)["unrealized_pl_liq"] = -(price * 200) + D("684.00")
            self.assertEqual(truth, result_of(variant().evaluate(document), WMT, "LOSSX").conditions[0].truth, price)


# --------------------------------------------------------------------------------------------
# Status suffixes (D1-7), dates (D1-6, O14) and the other line shapes
# --------------------------------------------------------------------------------------------


class StatusTest(unittest.TestCase):
    def test_the_brief_unit_golden_on_a_penny_class_short_call(self) -> None:
        document = regular().copy()
        leg = leg_of(document, 4)
        leg["fresh_ask"], leg["dte_trading"] = D("1.84"), 30
        sheet = regular().sheet(document, config=desk_config.LevelsSection(False, penny_classes=("WMT",)))
        self.assertEqual("TP50 fires at an option price of 1.71 (= credit 3.42 x (1 - 50 %)); resting-eligible: yes "
                         "(below the current ask 1.84)", line_of(sheet, WMT, "TP50").text)
        self.assertEqual("TP80 fires at an option price of 0.68 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); "
                         "resting-eligible: yes (below the current ask 1.84)", line_of(sheet, WMT, "TP80").text)
        self.assertTrue(sheet.group(WMT).header.endswith("option ticks 0.01 below 3.00, 0.05 at or above (penny class)"))
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)

    def test_a_take_profit_at_or_through_the_ask(self) -> None:
        document = regular().copy()
        leg_of(document, 4)["fresh_ask"] = D("0.65")
        self.assertEqual("TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); "
                         "threshold already crossed (pending confirmation: observation 1 of 2)",
                         line_of(regular().sheet(document), WMT, "TP80").text)
        # a take-profit level at or through the ask whose rule is neither fired nor pending (a later date piece)
        rulebook = rulebook_with(rule_block("TPLATE", "take_profit", '"short_call_leg"',
                                            '{ metric = "pct_captured", op = ">=", value = 80, unit = "percent" }, '
                                            '{ metric = "dte", op = "<=", value = 5, dte_unit = "trading" }',
                                            verdict="EXIT_SIGNAL"))
        document = regular().copy()
        leg_of(document, 4)["fresh_ask"] = D("0.60")
        line = line_of(regular().sheet(document, rulebook=rulebook), WMT, "TPLATE")
        self.assertEqual("TPLATE fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)) "
                         "from 2026-11-13 (5 trading days before the last trading day 2026-11-20); " + ALERT, line.text)
        self.assertEqual(("alert_level", datetime.date(2026, 11, 13)), (line.status, line.dates["from"]))

    def test_missing_or_zero_fresh_sides(self) -> None:
        document = regular().copy()
        leg_of(document, 4)["fresh_ask"] = None
        line = line_of(regular().sheet(document), WMT, "TP80")
        self.assertTrue(line.text.startswith("TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 "
                                             "x (1 - 80 %)); resting-eligible: unknown (no fresh ask); the desk cannot "
                                             "read this rule now: row 4 WMT261120C00105000: quote grade B"), line.text)
        self.assertEqual(("unknown", D("0.65")), (line.status, line.level))
        document = regular().copy()
        leg_of(document, 4)["fresh_ask"] = D("0")
        sheet = regular().sheet(document)
        line = line_of(sheet, WMT, "TP80")
        self.assertIn("resting-eligible: unknown (no offer: ask 0); the desk cannot read this rule now: row 4 "
                      "WMT261120C00105000: quote grade B: no offer (ask 0)", line.text)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)

    def test_an_unreadable_spot_keeps_the_exact_level(self) -> None:
        document = regular().copy()
        document["underlyings"]["WMT"]["mark_mid"] = None
        sheet = regular().sheet(document)
        self.assertEqual("USTOP fires at WMT 92.00 or below (your stop in positions.csv); the desk cannot read this rule "
                         "now: underlying WMT: no usable mark (quote grade A)", line_of(sheet, WMT, "USTOP").text)
        self.assertEqual("unknown", line_of(sheet, WMT, "USTOP").status)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)

    def test_a_calendar_date_on_a_closed_day_says_when_it_is_first_observed(self) -> None:
        with open(os.path.join(FIXTURE_DESK, "rules", "exit_rules-1.0.0.toml"), encoding="utf-8") as handle:
            source = handle.read()
        old = '{ metric = "dte", op = "<=", value = 14, dte_unit = "calendar" }'
        self.assertEqual(1, source.count(old))
        rulebook = exit_rules.parse_rulebook_text(source.replace(old, old.replace("14", "13")), path="<test rulebook>")
        sheet = regular().sheet(rulebook=rulebook)
        self.assertEqual("LAPSE14 fires on 2026-12-05 (first observed on 2026-12-07) (13 calendar days before the last "
                         "trading day 2026-12-18)", line_of(sheet, SPY, "LAPSE14").text)
        self.assertEqual(datetime.date(2026, 12, 7), line_of(sheet, SPY, "LAPSE14").dates["first_observed"])
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)

    def test_moves_with_a_previous_snapshot(self) -> None:
        document = regular().copy()
        document["underlyings"]["WMT"]["prev_eod_mid"] = D("104.00")
        document["underlyings"]["WMT"]["move_pct_from_prev_eod"] = D("0.22")
        sheet = regular().sheet(document)
        self.assertEqual("MOVE5_DOWN fires at WMT 98.80 or below (-5 % from the previous end-of-day mid 104.00; 5.21 % "
                         "below the current 104.23)", line_of(sheet, None, "MOVE5_DOWN").text)
        self.assertEqual("MOVE5_UP fires at WMT 109.20 or above (5 % from the previous end-of-day mid 104.00; 4.77 % "
                         "above the current 104.23)", line_of(sheet, None, "MOVE5_UP").text)
        self.assertIn("MOVE5_DOWN, MOVE5_UP, DAILY: no level: no previous end-of-day snapshot (none for 2026-11-02)",
                      sheet.text, "NVDA and SPY still have no previous snapshot")
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)
        for price, truth in ((D("98.80"), "true"), (D("98.81"), "false")):
            mutated = copy.deepcopy(document)
            mutated["underlyings"]["WMT"]["move_pct_from_prev_eod"] = (
                (price - D("104.00")) / D("104.00") * 100).quantize(D("0.01"))
            section = next(item for item in regular().evaluate(mutated).underlyings if item.subject == "WMT")
            results = section.fired + section.pending + section.clear
            self.assertEqual(truth, next(item for item in results if item.rule_id == "MOVE5_DOWN").conditions[0].truth)

    def test_an_ex_dividend_window_with_its_second_reading(self) -> None:
        with open(os.path.join(FIXTURE_DESK, "research", "calendar.toml"), encoding="utf-8") as handle:
            source = handle.read()
        old = "ex_dividend = { date = 2026-11-24, amount = 0.2350"
        self.assertEqual(1, source.count(old))
        calendar = calendar_events.parse_calendar_text(source.replace(old, old.replace("11-24", "11-23")))
        sheet = regular().sheet(rows=None, calendar=calendar)  # without the rows no #assign-ok suppresses EXDIV
        line = line_of(sheet, WMT, "EXDIV")
        self.assertEqual("EXDIV fires from 2026-11-16 (within 5 trading days of the ex-dividend date 2026-11-23 in "
                         "research/calendar.toml) once extrinsic at the bid less the dividend is below $0.00 (now "
                         "$3.1150 per share)", line.text)
        self.assertEqual(("date", "alert_level", None, datetime.date(2026, 11, 16), datetime.date(2026, 11, 23)),
                         (line.kind, line.status, line.level, line.dates["from"], line.dates["event_date"]))
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)

    def test_a_quarantined_group(self) -> None:
        document = regular().copy()
        document["portfolio"]["quarantined_groups"] = [{"group_id": "ROW-9", "reasons": ["row 9 status: 'opne' is not "
                                                                                          "a status"]}]
        sheet = regular().sheet(document)
        lines = sheet.text.split("\n")
        start = lines.index("## ROW-9 | quarantined in positions.csv")
        self.assertEqual("ROW-9 quarantined: row 9: the status cell cannot be used (positions_check.py shows it)",
                         lines[start + 1])
        self.assertNotIn("opne", sheet.text)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)
        self.assertEqual((0, 0, None), (sheet.group("ROW-9").price_levels, sheet.group("ROW-9").resting_eligible,
                                        sheet.group("ROW-9").tick_class))


class StructureTest(unittest.TestCase):
    """Synthetic structures made from the regular book (dicts edited, then evaluated with the landed engine)."""

    def long_put(self) -> Dict[str, Any]:
        document = regular().copy()
        group = group_of(document, SPY)
        group.update(structure="long_put", legs=[8], basis_kind="debit", net_cost=D("1130.00"), max_profit=D("60870.00"),
                     max_loss=D("1130.00"))
        return document

    def test_a_long_put_debit_take_profit_and_loss(self) -> None:
        document = self.long_put()
        sheet = regular().sheet(document)
        self.assertEqual("LTP100 fires at an option price of 22.60 (= debit 11.30 x (1 + 100 %)); resting-eligible: yes "
                         "(above the current bid 9.80)", line_of(sheet, SPY, "LTP100").text)
        self.assertEqual("LLOSS50 fires at an option price of 5.70 (rounded up from 5.65 = debit 11.30 x (1 - 50 %)); "
                         + ALERT, line_of(sheet, SPY, "LLOSS50").text)
        self.assertEqual("LDTE30 fires on 2026-11-18 (30 calendar days before the last trading day 2026-12-18)",
                         line_of(sheet, SPY, "LDTE30").text)
        self.assertEqual(("bid", None), (line_of(sheet, SPY, "LTP100").close_side, line_of(sheet, SPY, "LTP100").rounding))
        self.assertEqual(("bid", "up", D("5.70"), D("5.65")), (line_of(sheet, SPY, "LLOSS50").close_side,
                                                              line_of(sheet, SPY, "LLOSS50").rounding,
                                                              line_of(sheet, SPY, "LLOSS50").level,
                                                              line_of(sheet, SPY, "LLOSS50").exact))
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)
        mutated = copy.deepcopy(document)
        leg_of(mutated, 8)["fresh_bid"] = D("22.60")
        self.assertEqual("true", result_of(regular().evaluate(mutated), SPY, "LTP100").conditions[0].truth)

    def vertical(self, debit: bool) -> Dict[str, Any]:
        document = regular().copy()
        short = leg_of(document, 4)
        other = copy.deepcopy(short)
        other.update(row_number=10, occ="WMT261120C00110000", strike=D("110.000"), open_quantity=D("2"),
                     entry_price_per_share=D("1.00"), remaining_cost_basis=D("200.00"), fresh_bid=D("1.20"),
                     fresh_ask=D("1.30"))
        if debit:
            short.update(open_quantity=D("2"), remaining_cost_basis=D("684.00"))
            other.update(open_quantity=D("-2"), remaining_cost_basis=D("-200.00"))
        document["legs"].append(other)
        group = group_of(document, WMT)
        if debit:
            group.update(structure="vertical_debit_call", legs=[4, 10], basis_kind="debit", net_cost=D("484.00"),
                         max_profit=D("516.00"), max_loss=D("484.00"), underlying_stop=None)
        else:
            group.update(structure="vertical_credit_call", legs=[4, 10], basis_kind="credit", net_cost=D("-484.00"),
                         max_profit=D("484.00"), max_loss=D("516.00"), underlying_stop=None)
        return document

    def test_a_credit_vertical_prints_spread_prices(self) -> None:
        document = self.vertical(debit=False)
        sheet = regular().sheet(document, rows=None)
        header = sheet.group(WMT).header
        self.assertTrue(header.startswith("## AG-20261020-01 | WMT call credit vertical | option ticks"), header)
        self.assertTrue(header.endswith(" | spread prices on a 0.01 grid"), header)
        self.assertEqual("TP80 fires at a spread price of 0.48 (rounded down from 0.484 = credit 2.42 x (1 - 80 %)); "
                         "resting-eligible: yes (below the current net cost to close 2.30)", line_of(sheet, WMT, "TP80").text)
        self.assertEqual(("spread_price", "net", D("2.30")), (line_of(sheet, WMT, "TP80").kind,
                                                              line_of(sheet, WMT, "TP80").close_side,
                                                              line_of(sheet, WMT, "TP80").current))
        self.assertEqual("LOSSX cannot fire on this position: the spread price would have to reach 7.26, beyond the "
                         "strike width 5", line_of(sheet, WMT, "LOSSX").text)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)
        outside = rulebook_with(rule_block("VOUT", "risk", '"vertical_credit_call"',
                                           '{ metric = "between_strikes", op = "is_false" }'))
        line = line_of(regular().sheet(document, rulebook=outside, rows=None), WMT, "VOUT")
        self.assertEqual("VOUT fires while WMT is at or below 105.00 or at or above 110.00 (outside the strikes 105 and "
                         "110); threshold already crossed (pending confirmation: observation 1 of 2)", line.text,
                         "WMT at 104.23 is already outside the strikes")
        self.assertEqual({"low": D("105"), "high": D("110")}, line.level)
        for spot, truth in (("105.00", "true"), ("105.01", "false"), ("109.99", "false"), ("110.00", "true")):
            mutated = copy.deepcopy(document)
            mutated["underlyings"]["WMT"]["mark_mid"] = D(spot)
            result = result_of(regular().evaluate(mutated, outside, rows=None), WMT, "VOUT")
            self.assertEqual(truth, result.conditions[0].truth, spot)

    def test_a_debit_vertical(self) -> None:
        sheet = regular().sheet(self.vertical(debit=True), rows=None)
        self.assertEqual("VTP fires at a spread price of 3.97 (rounded up from 3.968 = debit 2.42 + 60 % of the max profit "
                         "2.58); resting-eligible: yes (above the current net value 2.05)", line_of(sheet, WMT, "VTP").text)
        self.assertEqual("VLOSS fires at a spread price of 1.21 (= debit 2.42 x (1 - 50 %)); " + ALERT,
                         line_of(sheet, WMT, "VLOSS").text)
        self.assertEqual("VBETWEEN fires while WMT is between 105.01 and 109.99 (between the strikes 105 and 110) from "
                         "2026-11-18 (2 trading days before the last trading day 2026-11-20)",
                         line_of(sheet, WMT, "VBETWEEN").text)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)

    def test_an_unrealized_threshold_on_a_single_option_leg_and_on_a_stock_basis(self) -> None:
        extra = (rule_block("ULOSS", "risk", '"short_call"', '{ metric = "unrealized_pl", op = "<=", value = -1000, '
                            'unit = "usd" }')
                 + "\n" + rule_block("CCLOSS", "risk", '"covered_call"', '{ metric = "unrealized_pl", op = "<=", value = '
                                     '-1000, unit = "usd" }'))
        rulebook = rulebook_with(extra)
        sheet = variant().sheet(rulebook=rulebook)
        self.assertEqual("ULOSS fires at an option price of 8.40 (rounded down from 8.42 = (P/L -$1,000.00 + cost basis "
                         "-$684.00) / (-2 x 100 shares)); " + ALERT, line_of(sheet, WMT, "ULOSS").text)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)
        regular_sheet = regular().sheet(rulebook=rulebook)
        value = group_of(regular().document, NVDA)["unrealized_pl_liq"]
        self.assertEqual("CCLOSS has no single price level (it reads unrealized P/L, now %s; your threshold: at or below "
                         "-$1,000.00)" % alerts.fmt_money(value, []), line_of(regular_sheet, NVDA, "CCLOSS").text)
        self.assertTrue(regular_sheet.lint().ok, regular_sheet.lint().problems)

    def test_the_variant_short_call_reads_at_group_level(self) -> None:
        sheet = variant().sheet()
        self.assertEqual("TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); "
                         "resting-eligible: yes (below the current ask 3.50)", line_of(sheet, WMT, "TP80").text)
        self.assertEqual("LOSSX fires at an option price of 10.20 (rounded down from 10.26 = credit 3.42 x (1 + 2)); "
                         + ALERT, line_of(sheet, WMT, "LOSSX").text)
        self.assertTrue(sheet.group(WMT).header.startswith("## AG-20261020-01 | WMT short call | "))
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)


class PlanCardTest(unittest.TestCase):
    def test_the_wmt_card(self) -> None:
        card = levels.build_plan_card(regular().evaluate(), regular().document, regular().rulebook, WMT,
                                      calendar=regular().calendar, levels_config=STANDARD,
                                      broker_cutoff_et=datetime.time(16, 30))
        lines = card.text.split("\n")
        self.assertEqual("PLAN CARD AG-20261020-01 | WMT covered call | rules 1.0.0, sha cc09859c", lines[0])
        self.assertEqual(REGULAR_SHEET[1:11], lines[1:11])
        self.assertEqual("dates that matter: last trading day 2026-11-20; 21 trading days before it 2026-10-22; earnings "
                         "2026-11-17 (confirmed, research/calendar.toml); ex-dividend 2026-11-24 (confirmed, $0.2350 per "
                         "share); your broker's exercise cutoff 16:30 ET on 2026-11-20", lines[11])
        self.assertEqual(FOOTER, lines[12])
        self.assertEqual(13, len(lines))
        self.assertTrue(card.lint().ok, card.lint().problems)
        self.assertEqual([], order_words(card.text))
        self.assertEqual(WMT, card.group_id)

    def test_spy_and_an_unknown_group(self) -> None:
        card = levels.build_plan_card(regular().evaluate(), regular().document, regular().rulebook, SPY,
                                      calendar=regular().calendar, levels_config=STANDARD,
                                      broker_cutoff_et=datetime.time(16, 30))
        self.assertIn("dates that matter: last trading day 2026-12-18; 21 trading days before it 2026-11-18; earnings none "
                      "(a confirmed none, research/calendar.toml); ex-dividend 2026-12-18 (estimated, $1.85 per share); "
                      "your broker's exercise cutoff 16:30 ET on 2026-12-18", card.text)
        self.assertTrue(card.lint().ok, card.lint().problems)
        with self.assertRaises(levels.UnknownGroup):
            levels.build_plan_card(regular().evaluate(), regular().document, regular().rulebook, "AG-20261231-99",
                                   calendar=regular().calendar, levels_config=STANDARD)


# --------------------------------------------------------------------------------------------
# Review fixes: a level below the lowest tick, an unavailable group, exact values near the grid, every long leg
# --------------------------------------------------------------------------------------------


def tp80_truth(built: Built, document: Dict[str, Any], group_id: str, row: int, ask: str) -> str:
    mutated = copy.deepcopy(document)
    leg_of(mutated, row)["fresh_ask"] = D(ask)
    return result_of(built.evaluate(mutated), group_id, "TP80").conditions[0].truth


class LowestTickTest(unittest.TestCase):
    """A take-profit level that rounds down to 0.00 is no level: no ask at or below it is a price (ask 0 is no offer)."""

    def test_a_take_profit_below_the_lowest_tick_cannot_fire(self) -> None:
        document = regular().copy()
        leg = leg_of(document, 4)
        leg["entry_price_per_share"], leg["fresh_ask"] = D("0.20"), D("0.15")
        sheet = regular().sheet(document)
        line = line_of(sheet, WMT, "TP80")
        self.assertEqual("TP80 cannot fire on this position: the option price would have to fall to 0.04 (= credit 0.20 x "
                         "(1 - 80 %)), below the lowest tick 0.05 of the standard class", line.text)
        self.assertEqual(("none", "cannot_fire", None), (line.kind, line.status, line.level))
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)
        # the rule never reads TRUE on the standard grid: its lowest tick reads false, an ask of 0 is no offer
        self.assertEqual(["false", "unknown"], [tp80_truth(regular(), document, WMT, 4, ask) for ask in ("0.05", "0")])
        # on the penny grid 0.04 is a price, and the rule reads TRUE there
        penny = regular().sheet(document, config=desk_config.LevelsSection(False, penny_classes=("WMT",)))
        self.assertEqual("TP80 fires at an option price of 0.04 (= credit 0.20 x (1 - 80 %)); resting-eligible: yes (below "
                         "the current ask 0.15)", line_of(penny, WMT, "TP80").text)
        self.assertEqual("true", tp80_truth(regular(), document, WMT, 4, "0.04"))
        leg["entry_price_per_share"] = D("0.02")
        penny = regular().sheet(document, config=desk_config.LevelsSection(False, penny_classes=("WMT",)))
        self.assertEqual("TP80 cannot fire on this position: the option price would have to fall to 0.004 (= credit 0.02 x "
                         "(1 - 80 %)), below the lowest tick 0.01 of the penny class", line_of(penny, WMT, "TP80").text)
        self.assertTrue(penny.lint().ok, penny.lint().problems)

    def test_the_group_level_reading_on_a_short_call(self) -> None:
        document = variant().copy()
        group_of(document, WMT).update(net_cost=D("-40.00"), max_profit=D("40.00"))
        sheet = variant().sheet(document)
        self.assertEqual("TP80 cannot fire on this position: the option price would have to fall to 0.04 (= credit 0.20 x "
                         "(1 - 80 %)), below the lowest tick 0.05 of the standard class", line_of(sheet, WMT, "TP80").text)
        self.assertEqual(0, sheet.group(WMT).resting_eligible)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)


class UnavailableGroupTest(unittest.TestCase):
    def test_an_unavailable_group_says_so_under_its_header(self) -> None:
        sheet = expiry_day().sheet()
        lines = sheet.text.split("\n")
        start = next(index for index, line in enumerate(lines) if line.startswith("## %s |" % WMT))
        self.assertEqual("AG-20261020-01 data unavailable: row 4 WMT261120C00105000: no usable mark (quote grade E, fresh)",
                         lines[start + 1])
        first = sheet.group(WMT).lines[0]
        self.assertEqual((None, "none", "unknown", None), (first.rule_id, first.kind, first.status, first.level))
        self.assertEqual("unavailable", sheet.group(WMT).data_quality)
        self.assertNotIn("data unavailable", "\n".join(line for group in sheet.groups if group.group_id != WMT
                                                       for line in group.text_lines()))
        self.assertNotIn("data unavailable", regular().sheet().text)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)


class ExactDisplayTest(unittest.TestCase):
    def test_an_exact_value_with_hidden_digits_shows_its_side_of_the_grid(self) -> None:
        document = variant().copy()
        group_of(document, WMT).update(net_cost=D("-974.99"), max_profit=D("974.99"))
        call = next(leg for leg in document["legs"] if leg.get("occ") == "WMT261120C00105000")
        call.update(open_quantity=D("-3"), quantity=D("-3"), remaining_cost_basis=D("-974.99"),
                    cost_basis_total=D("-974.99"))
        sheet = variant().sheet(document)
        tp80 = line_of(sheet, WMT, "TP80")
        self.assertEqual("TP80 fires at an option price of 0.60 (rounded down from 0.649993 = credit 3.249967 x (1 - 80 %)); "
                         "resting-eligible: yes (below the current ask 3.50)", tp80.text)
        self.assertEqual((D("974.99") / D("300")) * (1 - D("80") / D("100")), tp80.exact, "the JSON value is unrounded")
        self.assertEqual("down", tp80.rounding)
        self.assertEqual("LOSSX fires at an option price of 9.70 (rounded down from 9.7499 = credit 3.249967 x (1 + 2)); "
                         + ALERT, line_of(sheet, WMT, "LOSSX").text)
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)

    def test_the_shown_value_never_lands_on_the_level_or_beyond_it(self) -> None:
        self.assertEqual(D("0.649999"), levels.exact_value(D("0.64999999"), "down", D("0.60")))
        self.assertEqual(D("0.6000003"), levels.exact_value(D("0.60000031"), "down", D("0.60")))
        self.assertEqual(D("5.650001"), levels.exact_value(D("5.6500001"), "up", D("5.70")))
        self.assertEqual(D("0.684"), levels.exact_value(D("0.684"), "down", D("0.65")))
        self.assertEqual(D("2.283333"), levels.exact_value(D("685") / D("300")))
        for value, direction, level in ((D("0.64999999"), "down", D("0.60")), (D("5.6500001"), "up", D("5.70"))):
            shown = levels.exact_value(value, direction, level)
            self.assertEqual(level, levels.round_option_price(shown, "standard", direction), (value, shown))


class EveryLongLegTest(unittest.TestCase):
    def custom(self) -> Dict[str, Any]:
        """The WMT group as a custom long strangle: the long call row 4 (K=105) and a long put row 10 (K=100)."""
        document = regular().copy()
        call = leg_of(document, 4)
        put = copy.deepcopy(call)
        call.update(open_quantity=D("2"), quantity=D("2"), remaining_cost_basis=D("684.00"), cost_basis_total=D("684.00"))
        put.update(row_number=10, occ="WMT261120P00100000", right="PUT", strike=D("100.000"), open_quantity=D("2"),
                   quantity=D("2"), entry_price_per_share=D("1.00"), remaining_cost_basis=D("200.00"),
                   cost_basis_total=D("200.00"), fresh_bid=D("0.90"), fresh_ask=D("1.00"))
        document["legs"].append(put)
        group_of(document, WMT).update(structure="custom", legs=[4, 10], basis_kind="debit", net_cost=D("884.00"),
                                       max_profit=None, max_loss=D("884.00"), underlying_stop=None)
        return document

    def test_a_leg_rule_prints_one_line_per_long_leg(self) -> None:
        sheet = regular().sheet(self.custom(), rows=None)
        lines = [line for line in sheet.group(WMT).lines if line.rule_id == "AUTOEX"]
        self.assertEqual(["AUTOEX fires at WMT 105.01 or above (one cent above the strike 105; 0.75 % above the current "
                          "104.23) on the last trading day 2026-11-20 for row 4 WMT261120C00105000",
                          "AUTOEX fires at WMT 99.99 or below (one cent below the strike 100) on the last trading day "
                          "2026-11-20 for row 10 WMT261120P00100000"], [line.text for line in lines])
        self.assertEqual([D("105.01"), D("99.99")], [line.level for line in lines])
        self.assertEqual(["alert_level", "alert_level"], [line.status for line in lines])
        self.assertTrue(sheet.lint().ok, sheet.lint().problems)
        # one long leg keeps the single line without a row (the SPY golden of the regular sheet)
        self.assertTrue(REGULAR_SHEET[-6].startswith("AUTOEX fires at SPY "))
        self.assertIn(REGULAR_SHEET[-6], regular().sheet().text.split("\n"))


class SourceTest(unittest.TestCase):
    def test_no_binary_float_conversion_and_no_print(self) -> None:
        with open(LEVELS_SOURCE, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_.])float\(", source), "floats come through bsm.as_float only")
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_.])print\(", source))
        self.assertEqual([], order_words(source.replace(ALERT.split(": ", 1)[1], "")))


if __name__ == "__main__":
    unittest.main()
