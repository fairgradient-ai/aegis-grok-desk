"""Quote quality control (``aegis_desk.quote_qc``): session-aware per-side freshness and grades.

Every expectation here is a definition from plan section 8 and the PR 2a brief, so the values are
exact: the 25-minute bound is inclusive on the exact instant (19:25:00Z is fresh, 19:24:59Z is
stale at ``now`` 19:50:00Z), the reported age floors whole minutes, the after-close and pre-open
rules judge against the close instant with a 60-second tolerance, a weekend or holiday is one
"NYSE closed" verdict, and the grade bands are closed on the quantised two-decimal spread.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from aegis_desk import market_clock as mc
from aegis_desk import quote_qc, state
from aegis_desk.market_types import Quote

UTC = timezone.utc
BOUND = 25

# The regular fixture set: quotes stamped 14:31:12 ET, the run at 14:50:00 ET (EST, UTC-5).
NOW = datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)
STAMP = datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
# The after_close set: quotes 15:59:40 ET, the run at 16:25 ET; the close is 16:00 ET = 21:00Z.
AFTER_CLOSE_NOW = datetime(2026, 11, 3, 21, 25, 0, tzinfo=UTC)
AFTER_CLOSE_STAMP = datetime(2026, 11, 3, 20, 59, 40, tzinfo=UTC)
CLOSE_NOV3 = datetime(2026, 11, 3, 21, 0, 0, tzinfo=UTC)
# The pre_open set: the 2026-11-02 closing quotes read at 08:00 ET on 2026-11-03.
PRE_NOW = datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC)
PRE_STAMP = datetime(2026, 11, 2, 20, 59, 55, tzinfo=UTC)
PRIOR_CLOSE = datetime(2026, 11, 2, 21, 0, 0, tzinfo=UTC)
HOLIDAY_NOW = datetime(2026, 11, 26, 15, 0, 0, tzinfo=UTC)
WEEKEND_NOW = datetime(2026, 11, 7, 15, 0, 0, tzinfo=UTC)


def quote(symbol: str = "SPY", bid: str = "648.10", ask: str = "648.14", bid_date=STAMP, ask_date=STAMP,
          **extra) -> Quote:
    return Quote(symbol, bid=None if bid is None else Decimal(bid), ask=None if ask is None else Decimal(ask),
                 bid_date=bid_date, ask_date=ask_date, **extra)


def assess(q: Quote, now: datetime = NOW, bound: int = BOUND, prior_close=None) -> quote_qc.QuoteQC:
    session = mc.session_at(now)
    if prior_close is None and session.session == mc.SESSION_PRE:
        prior_close = mc.previous_close_utc(now)
    return quote_qc.assess(q, now=now, session=session, bound_min=bound, prior_close_utc=prior_close)


class RegularHoursFreshnessTest(unittest.TestCase):
    """The live-session rule: ``now - side_date <= bound`` per side, boundary inclusive."""

    def test_regular_set_quote_is_fresh_with_age_18(self) -> None:
        qc = assess(quote())
        self.assertEqual("fresh", qc.status)
        self.assertEqual(18, qc.age_min_bid)
        self.assertEqual(18, qc.age_min_ask)
        self.assertEqual("A", qc.grade)
        self.assertTrue(qc.usable_for_rules)
        self.assertEqual([], qc.reasons)
        self.assertEqual("as of 14:31:12 ET", qc.label)

    def test_exactly_the_bound_is_fresh(self) -> None:
        stamp = datetime(2026, 11, 3, 19, 25, 0, tzinfo=UTC)
        qc = assess(quote(bid_date=stamp, ask_date=stamp))
        self.assertEqual("fresh", qc.status)
        self.assertEqual(25, qc.age_min_bid)
        self.assertTrue(qc.usable_for_rules)

    def test_one_second_past_the_bound_is_stale(self) -> None:
        stamp = datetime(2026, 11, 3, 19, 24, 59, tzinfo=UTC)
        qc = assess(quote(bid_date=stamp, ask_date=stamp))
        self.assertEqual("stale", qc.status)
        self.assertEqual("F", qc.grade)
        self.assertFalse(qc.usable_for_rules)
        self.assertEqual(25, qc.age_min_bid, "25 min 1 s floors to 25 minutes while failing the exact bound")
        self.assertTrue(any("bid" in r and "stale" in r for r in qc.reasons), qc.reasons)
        self.assertTrue(any("ask" in r and "stale" in r for r in qc.reasons), qc.reasons)

    def test_plan_golden_forty_minute_old_quote_is_unavailable(self) -> None:
        stamp = NOW - timedelta(minutes=40)
        qc = assess(quote(bid_date=stamp, ask_date=stamp))
        self.assertEqual("stale", qc.status)
        self.assertEqual("F", qc.grade)
        self.assertFalse(qc.usable_for_rules)
        self.assertEqual(40, qc.age_min_bid)
        self.assertIsNone(qc.mark_mid)
        self.assertIsNone(qc.mark_liq_long)
        self.assertIsNone(qc.mark_liq_short)

    def test_stale_fixture_set_is_sixty_minutes_old(self) -> None:
        stamp = datetime(2026, 11, 3, 18, 50, 0, tzinfo=UTC)
        qc = assess(quote(bid_date=stamp, ask_date=stamp))
        self.assertEqual(("stale", "F", 60, 60), (qc.status, qc.grade, qc.age_min_bid, qc.age_min_ask))

    def test_age_floors_seconds_per_side(self) -> None:
        q = quote(bid_date=NOW - timedelta(minutes=3, seconds=59), ask_date=NOW - timedelta(minutes=4))
        qc = assess(q)
        self.assertEqual(3, qc.age_min_bid)
        self.assertEqual(4, qc.age_min_ask)

    def test_a_stamp_slightly_ahead_of_now_is_tolerated(self) -> None:
        ahead = NOW + timedelta(seconds=30)
        qc = assess(quote(bid_date=ahead, ask_date=ahead))
        self.assertEqual("fresh", qc.status)
        self.assertEqual(0, qc.age_min_bid, "age is never negative")

    def test_a_stamp_well_ahead_of_now_is_not_trusted(self) -> None:
        ahead = NOW + timedelta(seconds=quote_qc.SKEW_TOLERANCE_S + 1)
        qc = assess(quote(bid_date=ahead, ask_date=STAMP))
        self.assertEqual("one_side_stale", qc.status)
        self.assertTrue(any("ahead" in r for r in qc.reasons), qc.reasons)
        self.assertEqual(0, qc.age_min_bid)
        self.assertEqual(60, quote_qc.SKEW_TOLERANCE_S)

    def test_bound_is_the_desk_config_sum(self) -> None:
        stamp = NOW - timedelta(minutes=26)
        self.assertEqual("stale", assess(quote(bid_date=stamp, ask_date=stamp), bound=25).status)
        self.assertEqual("fresh", assess(quote(bid_date=stamp, ask_date=stamp), bound=30).status)

    def test_edt_day_uses_the_same_arithmetic(self) -> None:
        now = datetime(2026, 10, 30, 18, 0, 0, tzinfo=UTC)  # Friday 14:00 EDT
        stamp = datetime(2026, 10, 30, 17, 41, 12, tzinfo=UTC)
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=now)
        self.assertEqual("fresh", qc.status)
        self.assertEqual(18, qc.age_min_bid)
        self.assertEqual("as of 13:41:12 ET", qc.label)
        late = datetime(2026, 10, 30, 17, 34, 59, tzinfo=UTC)
        self.assertEqual("stale", assess(quote(bid_date=late, ask_date=late), now=now).status)

    def test_early_close_session_is_live(self) -> None:
        now = datetime(2026, 11, 27, 16, 0, 0, tzinfo=UTC)  # 11:00 ET on the half day
        self.assertEqual(mc.SESSION_EARLY_CLOSE, mc.session_at(now).session)
        stamp = now - timedelta(minutes=18, seconds=48)
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=now)
        self.assertEqual(("fresh", 18), (qc.status, qc.age_min_bid))
        old = now - timedelta(minutes=25, seconds=1)
        self.assertEqual("stale", assess(quote(bid_date=old, ask_date=old), now=now).status)


class OneSideStaleTest(unittest.TestCase):
    def test_one_stale_side_caps_the_grade_at_d_without_a_mid(self) -> None:
        old = NOW - timedelta(minutes=40)
        qc = assess(quote(bid_date=STAMP, ask_date=old))
        self.assertEqual("one_side_stale", qc.status)
        self.assertEqual("D", qc.grade)
        self.assertTrue(qc.usable_for_rules)
        self.assertIsNone(qc.mark_mid, "a mid is never invented from one fresh side")
        self.assertIsNone(qc.spread_pct)
        self.assertEqual(Decimal("648.10"), qc.mark_liq_long, "the fresh bid is a real liquidation mark")
        self.assertIsNone(qc.mark_liq_short, "the stale ask is not a mark")
        self.assertEqual((18, 40), (qc.age_min_bid, qc.age_min_ask))
        self.assertTrue(any("one side stale" in r for r in qc.reasons), qc.reasons)

    def test_fresh_ask_with_stale_bid_marks_the_short_side_only(self) -> None:
        old = NOW - timedelta(minutes=40)
        qc = assess(quote(bid_date=old, ask_date=STAMP))
        self.assertEqual(("one_side_stale", "D"), (qc.status, qc.grade))
        self.assertIsNone(qc.mark_liq_long)
        self.assertEqual(Decimal("648.14"), qc.mark_liq_short)

    def test_a_fresh_zero_bid_beside_a_stale_ask_is_e(self) -> None:
        old = NOW - timedelta(minutes=40)
        qc = assess(quote(bid="0", ask="0.05", bid_date=STAMP, ask_date=old))
        self.assertEqual("one_side_stale", qc.status)
        self.assertEqual("E", qc.grade)
        self.assertFalse(qc.usable_for_rules)


class MissingAndUndatedTest(unittest.TestCase):
    def test_missing_ask_is_f(self) -> None:
        qc = assess(quote(ask=None))
        self.assertEqual(("missing", "F", False), (qc.status, qc.grade, qc.usable_for_rules))
        self.assertIn("ask missing", qc.reasons)
        self.assertIsNone(qc.spread_pct)
        self.assertIsNone(qc.mark_mid)
        self.assertIsNone(qc.age_min_ask)
        self.assertEqual(18, qc.age_min_bid)

    def test_zero_ask_under_a_positive_bid_is_a_missing_ask_not_a_crossed_market(self) -> None:
        # Alpaca's ap 0 means no active offer; with a positive bid it is an empty ask, never "crossed".
        qc = assess(quote(bid="1.0", ask="0"))
        self.assertEqual(("missing", "F", False), (qc.status, qc.grade, qc.usable_for_rules))
        self.assertEqual(["ask missing (no offer)"], qc.reasons)
        self.assertFalse(any(reason.startswith("crossed") for reason in qc.reasons))
        self.assertEqual((None, None, None, None), (qc.mark_mid, qc.mark_liq_long, qc.mark_liq_short, qc.spread_pct))
        # the same with a stale ask: still missing, its stale reason kept
        qc = assess(quote(bid="1.0", ask="0", ask_date=NOW - timedelta(minutes=40)))
        self.assertEqual(("missing", "F"), (qc.status, qc.grade))
        self.assertEqual("ask missing (no offer)", qc.reasons[-1])
        self.assertFalse(any(reason.startswith("crossed") for reason in qc.reasons))
        # a 0/0 market keeps its E grade (no bid), and a zero bid under a positive ask is unchanged
        self.assertEqual("E", assess(quote(bid="0", ask="0")).grade)
        self.assertEqual("E", assess(quote(bid="0", ask="0.05")).grade)

    def test_missing_bid_is_f(self) -> None:
        qc = assess(quote(bid=None))
        self.assertEqual(("missing", "F"), (qc.status, qc.grade))
        self.assertIn("bid missing", qc.reasons)

    def test_undated_side_is_f(self) -> None:
        qc = assess(quote(bid_date=None))
        self.assertEqual(("undated", "F", False), (qc.status, qc.grade, qc.usable_for_rules))
        self.assertIn("bid undated", qc.reasons)
        self.assertIsNone(qc.age_min_bid)
        self.assertEqual(18, qc.age_min_ask)
        self.assertEqual("as of 14:31:12 ET", qc.label, "the dated side still gives the as-of")
        self.assertIsNone(qc.mark_mid)

    def test_both_sides_undated_label(self) -> None:
        qc = assess(quote(bid_date=None, ask_date=None))
        self.assertEqual("undated", qc.status)
        self.assertEqual("undated", qc.label)

    def test_missing_outranks_undated_and_stale(self) -> None:
        qc = assess(quote(ask=None, bid_date=None))
        self.assertEqual("missing", qc.status)
        self.assertEqual(["bid undated", "ask missing"], qc.reasons)

    def test_last_trade_is_never_a_mark(self) -> None:
        q = Quote("SPY", last=Decimal("648.12"), trade_date=STAMP)
        qc = assess(q)
        self.assertEqual(("missing", "F"), (qc.status, qc.grade))
        self.assertIsNone(qc.mark_mid)
        self.assertIsNone(qc.mark_liq_long)
        self.assertIsNone(qc.mark_liq_short)
        self.assertIsNone(qc.bid)
        self.assertIsNone(qc.ask)


class GradeBandsTest(unittest.TestCase):
    """Both sides fresh and ``bid > 0``: A <= 2 %, B <= 5 %, C <= 10 %, D <= 25 %, E above."""

    def grade(self, bid: str, ask: str) -> quote_qc.QuoteQC:
        return assess(quote(bid=bid, ask=ask))

    def test_fixture_anchors(self) -> None:
        cases = [
            ("SPY", "648.10", "648.14", "0.01", "A", "648.12"),
            ("WMT", "104.20", "104.26", "0.06", "A", "104.23"),
            ("NVDA", "148.90", "148.95", "0.03", "A", "148.925"),
            ("WMT261120C00105000", "3.35", "3.50", "4.38", "B", "3.425"),
            ("NVDA261120C00150000", "3.10", "3.30", "6.25", "C", "3.20"),
            ("SPY261218P00620000", "9.80", "10.10", "3.02", "B", "9.95"),
        ]
        for symbol, bid, ask, spread, grade, mid in cases:
            with self.subTest(symbol=symbol):
                qc = assess(quote(symbol, bid, ask))
                self.assertEqual(Decimal(spread), qc.spread_pct)
                self.assertEqual(grade, qc.grade)
                self.assertEqual(Decimal(mid), qc.mark_mid)
                self.assertEqual(Decimal(bid), qc.mark_liq_long)
                self.assertEqual(Decimal(ask), qc.mark_liq_short)
                self.assertTrue(qc.usable_for_rules)
                self.assertEqual(symbol, qc.symbol)

    def test_band_boundaries_are_inclusive_on_the_quantised_spread(self) -> None:
        self.assertEqual(("A", Decimal("2.00")), (self.grade("99", "101").grade, self.grade("99", "101").spread_pct))
        self.assertEqual("B", self.grade("99", "101.01").grade)
        self.assertEqual(("B", Decimal("5.00")), (self.grade("97.5", "102.5").grade, self.grade("97.5", "102.5").spread_pct))
        self.assertEqual("C", self.grade("97.5", "102.51").grade)
        self.assertEqual(("C", Decimal("10.00")), (self.grade("95", "105").grade, self.grade("95", "105").spread_pct))
        self.assertEqual("D", self.grade("95", "105.01").grade)
        self.assertEqual(("D", Decimal("25.00")), (self.grade("87.5", "112.5").grade, self.grade("87.5", "112.5").spread_pct))
        wide = self.grade("87.5", "112.51")
        self.assertEqual(("E", Decimal("25.01")), (wide.grade, wide.spread_pct))

    def test_bands_are_closed_on_the_printed_two_decimal_value(self) -> None:
        # 2.47/2.52: the exact ratio is 2.004...% (above 2) while the printed spread is 2.00. The
        # band follows the two-decimal ``spread_pct`` the brief defines (A), so the number a reader
        # sees and the grade always agree; a band on the exact ratio would print 2.00 beside a B.
        exact = (Decimal("2.52") - Decimal("2.47")) / Decimal("2.495") * 100
        self.assertGreater(exact, Decimal("2"))
        qc = self.grade("2.47", "2.52")
        self.assertEqual((Decimal("2.00"), "A", True), (qc.spread_pct, qc.grade, qc.usable_for_rules))
        # One cent wider and the printed value and the grade move together.
        qc = self.grade("2.47", "2.53")
        self.assertEqual((Decimal("2.40"), "B"), (qc.spread_pct, qc.grade))
        # The same rule at the D/E edge, where usable_for_rules turns: 21.94/28.21 is 25.00498...%
        # exactly, prints 25.00 and is D (usable); one cent wider prints 25.04 and is E.
        exact = (Decimal("28.21") - Decimal("21.94")) / Decimal("25.075") * 100
        self.assertGreater(exact, Decimal("25"))
        qc = self.grade("21.94", "28.21")
        self.assertEqual((Decimal("25.00"), "D", True), (qc.spread_pct, qc.grade, qc.usable_for_rules))
        qc = self.grade("21.94", "28.22")
        self.assertEqual((Decimal("25.04"), "E", False), (qc.spread_pct, qc.grade, qc.usable_for_rules))

    def test_spread_is_quantised_half_even_to_two_decimals(self) -> None:
        self.assertEqual(Decimal("0.00"), self.grade("199.995", "200.005").spread_pct)  # 0.005 -> 0.00
        self.assertEqual(Decimal("0.02"), self.grade("199.985", "200.015").spread_pct)  # 0.015 -> 0.02
        self.assertEqual(2, -self.grade("3.35", "3.50").spread_pct.as_tuple().exponent)

    def test_wide_market_is_e_with_a_displayed_mid(self) -> None:
        qc = self.grade("1.00", "1.40")
        self.assertEqual("E", qc.grade)
        self.assertEqual("fresh", qc.status)
        self.assertFalse(qc.usable_for_rules)
        self.assertEqual(Decimal("1.20"), qc.mark_mid, "displayed, never used by class-M rules")
        self.assertEqual(Decimal("33.33"), qc.spread_pct)
        self.assertTrue(any("wide" in r for r in qc.reasons), qc.reasons)

    def test_zero_bid_is_e(self) -> None:
        qc = self.grade("0", "0.05")
        self.assertEqual(("E", "fresh", False), (qc.grade, qc.status, qc.usable_for_rules))
        self.assertEqual(Decimal("0.025"), qc.mark_mid)
        self.assertEqual(Decimal("0"), qc.mark_liq_long)
        self.assertIn("no bid", qc.reasons)

    def test_zero_both_sides_has_no_mid(self) -> None:
        qc = self.grade("0", "0")
        self.assertEqual("E", qc.grade)
        self.assertIsNone(qc.mark_mid)
        self.assertIsNone(qc.spread_pct)

    def test_crossed_market_is_f_even_when_fresh(self) -> None:
        qc = self.grade("10.10", "9.80")
        self.assertEqual(("F", "fresh", False), (qc.grade, qc.status, qc.usable_for_rules))
        self.assertTrue(any(r.startswith("crossed") for r in qc.reasons), qc.reasons)
        self.assertIsNone(qc.mark_mid)
        self.assertIsNone(qc.mark_liq_long)
        self.assertIsNone(qc.mark_liq_short)
        self.assertEqual(Decimal("-3.02"), qc.spread_pct, "the crossed spread is reported as it is")

    def test_usable_for_rules_is_exactly_a_to_d(self) -> None:
        self.assertEqual(("A", "B", "C", "D"), quote_qc.USABLE_GRADES)
        self.assertEqual(("A", "B", "C", "D", "E", "F"), quote_qc.GRADES)
        for bid, ask, expected in (("99", "101", True), ("97.5", "102.5", True), ("95", "105", True),
                                   ("87.5", "112.5", True), ("87.5", "112.51", False), ("10.10", "9.80", False)):
            self.assertEqual(expected, self.grade(bid, ask).usable_for_rules, (bid, ask))


class AfterCloseTest(unittest.TestCase):
    """``closed``: a side is fresh when ``close_utc - side_date <= bound`` and the stamp is at most
    60 s after the close; the label is "as of close HH:MM ET"."""

    def test_plan_golden_16_25_run_on_closing_quotes(self) -> None:
        qc = assess(quote(bid_date=AFTER_CLOSE_STAMP, ask_date=AFTER_CLOSE_STAMP), now=AFTER_CLOSE_NOW)
        self.assertEqual("fresh", qc.status)
        self.assertEqual("as of close 16:00 ET", qc.label)
        self.assertEqual("A", qc.grade)
        self.assertTrue(qc.usable_for_rules)
        self.assertEqual(0, qc.age_min_bid, "20 s before the close floors to 0 minutes from the reference")
        self.assertEqual(Decimal("648.12"), qc.mark_mid)

    def test_quote_at_the_bound_before_the_close(self) -> None:
        at_bound = CLOSE_NOV3 - timedelta(minutes=25)
        qc = assess(quote(bid_date=at_bound, ask_date=at_bound), now=AFTER_CLOSE_NOW)
        self.assertEqual(("fresh", 25), (qc.status, qc.age_min_bid))
        past = CLOSE_NOV3 - timedelta(minutes=25, seconds=1)
        qc = assess(quote(bid_date=past, ask_date=past), now=AFTER_CLOSE_NOW)
        self.assertEqual(("stale", "F"), (qc.status, qc.grade))

    def test_a_stamp_within_sixty_seconds_after_the_close_is_the_closing_quote(self) -> None:
        stamp = CLOSE_NOV3 + timedelta(seconds=60)
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=AFTER_CLOSE_NOW)
        self.assertEqual(("fresh", 0), (qc.status, qc.age_min_bid))

    def test_a_later_postmarket_print_is_not_a_closing_quote(self) -> None:
        stamp = CLOSE_NOV3 + timedelta(seconds=61)
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=AFTER_CLOSE_NOW)
        self.assertEqual(("stale", "F"), (qc.status, qc.grade))
        self.assertTrue(any("after the close" in r for r in qc.reasons), qc.reasons)

    def test_now_well_after_the_close_still_judges_against_the_close(self) -> None:
        late_evening = datetime(2026, 11, 4, 1, 0, 0, tzinfo=UTC)  # 20:00 ET
        qc = assess(quote(bid_date=AFTER_CLOSE_STAMP, ask_date=AFTER_CLOSE_STAMP), now=late_evening)
        self.assertEqual(("fresh", "as of close 16:00 ET"), (qc.status, qc.label))

    def test_edt_close_label(self) -> None:
        now = datetime(2026, 10, 30, 20, 25, 0, tzinfo=UTC)  # 16:25 EDT
        stamp = datetime(2026, 10, 30, 19, 59, 40, tzinfo=UTC)
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=now)
        self.assertEqual(("fresh", "as of close 16:00 ET"), (qc.status, qc.label))

    def test_early_close_label_is_13_00(self) -> None:
        now = datetime(2026, 11, 27, 18, 25, 0, tzinfo=UTC)  # 13:25 ET after the 13:00 close
        stamp = datetime(2026, 11, 27, 17, 59, 50, tzinfo=UTC)
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=now)
        self.assertEqual(("fresh", "as of close 13:00 ET"), (qc.status, qc.label))
        regular_hours_stamp = datetime(2026, 11, 27, 17, 34, 59, tzinfo=UTC)
        qc = assess(quote(bid_date=regular_hours_stamp, ask_date=regular_hours_stamp), now=now)
        self.assertEqual("stale", qc.status)


class PreOpenTest(unittest.TestCase):
    def test_pre_open_set_is_fresh_against_the_prior_close(self) -> None:
        qc = assess(quote(bid="645.88", ask="645.92", bid_date=PRE_STAMP, ask_date=PRE_STAMP), now=PRE_NOW)
        self.assertEqual("fresh", qc.status)
        self.assertEqual("as of prior close 2026-11-02 16:00 ET", qc.label)
        self.assertEqual(0, qc.age_min_bid)
        self.assertEqual("A", qc.grade)

    def test_prior_close_is_an_explicit_parameter(self) -> None:
        session = mc.session_at(PRE_NOW)
        self.assertEqual(mc.SESSION_PRE, session.session)
        qc = quote_qc.assess(quote(bid_date=PRE_STAMP, ask_date=PRE_STAMP), now=PRE_NOW, session=session,
                             bound_min=BOUND, prior_close_utc=PRIOR_CLOSE)
        self.assertEqual("fresh", qc.status)
        with self.assertRaises(ValueError):
            quote_qc.assess(quote(), now=PRE_NOW, session=session, bound_min=BOUND, prior_close_utc=None)

    def test_a_quote_long_before_the_prior_close_is_stale(self) -> None:
        stamp = PRIOR_CLOSE - timedelta(minutes=30)
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=PRE_NOW)
        self.assertEqual(("stale", "F", 30), (qc.status, qc.grade, qc.age_min_bid))

    def test_a_premarket_print_is_not_a_closing_quote(self) -> None:
        stamp = PRE_NOW - timedelta(minutes=10)  # 07:50 ET today: after the prior close + 60 s
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=PRE_NOW)
        self.assertEqual(("stale", "F"), (qc.status, qc.grade))
        self.assertTrue(any("after the prior close" in r for r in qc.reasons), qc.reasons)

    def test_monday_pre_open_judges_against_friday(self) -> None:
        monday = datetime(2026, 11, 9, 13, 0, 0, tzinfo=UTC)
        prior = mc.previous_close_utc(monday)
        self.assertEqual(datetime(2026, 11, 6, 21, 0, 0, tzinfo=UTC), prior)
        stamp = prior - timedelta(seconds=10)
        qc = assess(quote(bid_date=stamp, ask_date=stamp), now=monday)
        self.assertEqual(("fresh", "as of prior close 2026-11-06 16:00 ET"), (qc.status, qc.label))


class ClosedDayTest(unittest.TestCase):
    def test_holiday_is_one_nyse_closed_verdict(self) -> None:
        stamp = datetime(2026, 11, 25, 20, 59, 50, tzinfo=UTC)
        qc = assess(quote(bid="651.38", ask="651.44", bid_date=stamp, ask_date=stamp), now=HOLIDAY_NOW)
        self.assertEqual("closed_day", qc.status)
        self.assertEqual("NYSE closed", qc.label)
        self.assertEqual("F", qc.grade)
        self.assertFalse(qc.usable_for_rules)
        self.assertEqual(["market closed"], qc.reasons)
        self.assertIsNone(qc.age_min_bid)
        self.assertIsNone(qc.age_min_ask)
        self.assertIsNone(qc.mark_mid)
        self.assertIsNone(qc.mark_liq_long)
        self.assertIsNone(qc.mark_liq_short)
        self.assertEqual(Decimal("651.38"), qc.bid, "the quote is still displayed")
        self.assertEqual(Decimal("0.01"), qc.spread_pct)

    def test_weekend(self) -> None:
        qc = assess(quote(), now=WEEKEND_NOW)
        self.assertEqual(("closed_day", "NYSE closed", "F"), (qc.status, qc.label, qc.grade))

    def test_closed_day_does_not_need_a_prior_close(self) -> None:
        session = mc.session_at(HOLIDAY_NOW)
        qc = quote_qc.assess(quote(), now=HOLIDAY_NOW, session=session, bound_min=BOUND, prior_close_utc=None)
        self.assertEqual("closed_day", qc.status)


class ResultShapeTest(unittest.TestCase):
    def test_fields_and_vocabulary(self) -> None:
        names = [f.name for f in dataclasses.fields(quote_qc.QuoteQC)]
        for name in ("symbol", "grade", "status", "label", "age_min_bid", "age_min_ask", "spread_pct", "mark_mid",
                     "mark_liq_long", "mark_liq_short", "usable_for_rules", "reasons", "bid", "ask", "as_of"):
            self.assertIn(name, names)
        self.assertEqual(("fresh", "stale", "one_side_stale", "undated", "closed_day", "missing"), quote_qc.STATUSES)

    def test_as_dict_serialises_in_the_desk_convention(self) -> None:
        qc = assess(quote("WMT261120C00105000", "3.35", "3.50"))
        rendered = json.loads(state.json_dumps(qc.as_dict()))
        self.assertEqual("WMT261120C00105000", rendered["symbol"])
        self.assertEqual("B", rendered["grade"])
        self.assertEqual("3.425", rendered["mark_mid"])
        self.assertEqual("3.35", rendered["mark_liq_long"])
        self.assertEqual("3.50", rendered["mark_liq_short"])
        self.assertEqual("4.38", rendered["spread_pct"])
        self.assertEqual("2026-11-03T19:31:12+00:00", rendered["as_of"])
        self.assertTrue(rendered["usable_for_rules"])
        self.assertEqual([], rendered["reasons"])
        self.assertEqual(18, rendered["age_min_bid"])

    def test_results_are_frozen(self) -> None:
        qc = assess(quote())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            qc.grade = "A"  # type: ignore[misc]

    def test_unavailable_helper(self) -> None:
        qc = quote_qc.unavailable("SPX", "cache: unmatched", label="as of 14:31:12 ET")
        self.assertEqual(("SPX", "F", "missing", False), (qc.symbol, qc.grade, qc.status, qc.usable_for_rules))
        self.assertEqual(["cache: unmatched"], qc.reasons)
        self.assertEqual("as of 14:31:12 ET", qc.label)
        self.assertIsNone(qc.mark_mid)
        shown = quote_qc.unavailable("SPY", "cache: fetch_failed:network", label="stale", quote=quote())
        self.assertEqual(Decimal("648.10"), shown.bid)
        self.assertEqual(Decimal("648.14"), shown.ask)
        self.assertEqual(STAMP, shown.as_of)
        self.assertIsNone(shown.mark_liq_long, "a displayed stale copy never carries marks")

    def test_input_validation(self) -> None:
        session = mc.session_at(NOW)
        with self.assertRaises(ValueError):
            quote_qc.assess(quote(), now=datetime(2026, 11, 3, 19, 50), session=session, bound_min=BOUND,
                            prior_close_utc=None)
        with self.assertRaises(ValueError):
            quote_qc.assess(quote(), now=NOW, session=session, bound_min=-1, prior_close_utc=None)
        with self.assertRaises(ValueError):
            quote_qc.assess(quote(), now=NOW, session=session, bound_min=True, prior_close_utc=None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            quote_qc.assess("SPY", now=NOW, session=session, bound_min=BOUND, prior_close_utc=None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            quote_qc.assess(quote(), now=NOW, session="open", bound_min=BOUND, prior_close_utc=None)  # type: ignore[arg-type]

    def test_session_label_helper(self) -> None:
        self.assertEqual("as of close 16:00 ET", quote_qc.session_label(mc.session_at(AFTER_CLOSE_NOW), None, None))
        self.assertEqual("as of prior close 2026-11-02 16:00 ET",
                         quote_qc.session_label(mc.session_at(PRE_NOW), PRIOR_CLOSE, None))
        self.assertEqual("NYSE closed", quote_qc.session_label(mc.session_at(HOLIDAY_NOW), None, STAMP))
        self.assertEqual("as of 14:31:12 ET", quote_qc.session_label(mc.session_at(NOW), None, STAMP))
        self.assertEqual("undated", quote_qc.session_label(mc.session_at(NOW), None, None))
        self.assertEqual(date(2026, 11, 3), mc.session_at(NOW).et_date)


if __name__ == "__main__":
    unittest.main()
