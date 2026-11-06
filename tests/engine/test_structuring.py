"""The Structuring Desk's engine (``aegis_desk.structuring``; PR 3b, designer Q's D3-D17 under the addendum S1-S17).

Every golden of designer Q's D16 is recomputed here with the landed engine (book, quote_qc, iv, greeks, payoff, pnl)
and pinned exactly: money and scores as Decimal strings, deltas and implied volatilities to 1e-9 against
``iv.implied_vol`` and ``greeks.european_greeks`` at the solved volatility. The runs use copies of
``tests/fixtures/desk`` at the regular instant (``--now 2026-11-03T19:50:00Z``, quotes stamped 19:31:12Z) with the
fixture criteria ``tests/fixtures/init/expected_structuring.toml``; the proposal goldens use copies without row 4 (the
WMT call) or row 8 (the SPY put). Hand-built quotes pin the objectives (per-day normalisation of two protective-put
expirations, an in-the-money call's extrinsic), every UNPRICED code, the status precedence and the table order, and
each tie-break with constructed exact ties; seeded property tests permute chains, add a failing or unpriced
candidate and raise an eligible bid; the after-state of every candidate equals ``payoff.grid_profile`` on in-memory
post-fill rows (parsed from an amended ``positions.csv`` text, never written) plus ``pnl.realized_for_row``.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import csv
import dataclasses
import datetime
import io
import os
import random
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from aegis_desk import (calendar_events, greeks, grouping, iv, market_clock, payoff, pnl, positions_csv, quote_cache,
                        quote_qc, structuring, structuring_config, units)
from aegis_desk.market_types import Quote

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CRITERIA_FILE = os.path.join(ROOT, "tests", "fixtures", "init", "expected_structuring.toml")
UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC)
STAMP = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
TODAY = datetime.date(2026, 11, 3)
EXPIRY_DAY_NOW = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)
RATE = 0.0412
D = Decimal
DEC18 = datetime.date(2026, 12, 18)
JAN15 = datetime.date(2027, 1, 15)
MAR19 = datetime.date(2027, 3, 19)

S = structuring
CRITERIA = structuring_config.load_structuring_config(CRITERIA_FILE)
WMT_TAGS = ("core holding #horizon:2027-03", "core holding #horizon:2027-03 #earnings-ok #assign-ok")
SPY_TAG = ("Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,", "Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,#assign-ok")


def ms(moment: datetime.datetime) -> int:
    return int(moment.timestamp() * 1000)


def quote(symbol: str, bid: Optional[str], ask: Optional[str], *, stamp: Optional[datetime.datetime] = STAMP,
          bid_stamp: Optional[datetime.datetime] = None, ask_stamp: Optional[datetime.datetime] = None,
          underlying: Optional[str] = None, strike: Optional[str] = None, expiration: Optional[datetime.date] = None,
          right: Optional[str] = None, root: Optional[str] = None, size: Optional[int] = 100) -> Quote:
    """A vendor-shaped quote; an option when ``right`` is given (its root defaults to the underlying)."""
    bid_date = bid_stamp if bid_stamp is not None else stamp
    ask_date = ask_stamp if ask_stamp is not None else stamp
    if right is None:
        return Quote(symbol=symbol, type="stock", bid=None if bid is None else D(bid), ask=None if ask is None else D(ask),
                     bid_date=bid_date, ask_date=ask_date)
    return Quote(symbol=symbol, type="option", bid=None if bid is None else D(bid), ask=None if ask is None else D(ask),
                 bid_date=bid_date, ask_date=ask_date, underlying=underlying, strike=D(strike), contract_size=size,
                 expiration_date=expiration, expiration_type="standard", option_type=right,
                 root_symbol=root if root is not None else underlying)


def occ_symbol(root: str, expiration: datetime.date, right: str, strike: str) -> str:
    return "%s%s%s%08d" % (root, expiration.strftime("%y%m%d"), "C" if right == "call" else "P", int(D(strike) * 1000))


def option(under: str, expiration: datetime.date, right: str, strike: str, bid: str, ask: str, **kwargs) -> Quote:
    return quote(occ_symbol(kwargs.get("root") or under, expiration, right, strike), bid, ask, underlying=under,
                 strike=strike, expiration=expiration, right=right, **kwargs)


SESSION = market_clock.session_at(NOW)
PRIOR_CLOSE = market_clock.previous_close_utc(NOW)


def spot_qc(symbol: str = "WMT", bid: str = "104.20", ask: str = "104.26", stamp: datetime.datetime = STAMP,
            session=SESSION, now: datetime.datetime = NOW) -> quote_qc.QuoteQC:
    return quote_qc.assess(quote(symbol, bid, ask, stamp=stamp), now=now, session=session, bound_min=25,
                           prior_close_utc=PRIOR_CLOSE)


def pricing(symbol: str = "WMT", spot: Optional[quote_qc.QuoteQC] = None, q: Optional[str] = "0.0090",
            max_spread: str = "25", session=SESSION, now: datetime.datetime = NOW) -> "S.PricingContext":
    return S.PricingContext(now=now, today=market_clock.to_et(now).date(), session=session,
                            prior_close_utc=market_clock.previous_close_utc(now), bound_min=25, rate=RATE,
                            dividend_yield=None if q is None else D(q),
                            spot=spot if spot is not None else spot_qc(symbol, session=session, now=now),
                            underlying=symbol, max_spread_pct=D(max_spread))


def group_context(shares: str = "200", cost: str = "19700.00", *, earnings_ok: bool = False, assign_ok: bool = False,
                  structure: str = "long_stock", symbol: str = "WMT", before_delta: Optional[float] = 200.0,
                  trade_id: str = "AG-20261020-01") -> "S.GroupContext":
    shares_d = D(shares)
    before = S.BeforeState(group_delta=before_delta, max_loss=D(cost), max_profit=None, breakeven=D(cost) / shares_d,
                           unbounded_up=True)
    return S.GroupContext(trade_id=trade_id, group_id=trade_id, underlying=symbol, account="Brokerage (margin)",
                          structure=structure, shares=shares_d, stock_cost=D(cost), earnings_ok=earnings_ok,
                          assign_ok=assign_ok, before=before)


CALENDAR_TEXT = """schema_version = 1

[symbols.WMT]
earnings = { date = 2026-11-17, time = "pre", status = "confirmed", source = "test", checked_utc = 2026-11-01T12:00:00Z }
ex_dividend = { date = 2026-11-24, amount = 0.2350, status = "confirmed", source = "test", checked_utc = 2026-11-01T12:00:00Z }
dividend_yield = 0.0090

[symbols.SPY]
earnings = { status = "none", source = "test", checked_utc = 2026-11-01T12:00:00Z }
ex_dividend = { date = 2026-12-18, amount = 1.85, status = "estimated", source = "test", checked_utc = 2026-11-01T12:00:00Z }

[symbols.ZZZ]
earnings = { status = "unknown", source = "test", checked_utc = 2026-11-01T12:00:00Z }
ex_dividend = { status = "none", source = "test", checked_utc = 2026-11-01T12:00:00Z }
"""
CALENDAR = calendar_events.parse_calendar_text(CALENDAR_TEXT)


def events(symbol: str = "WMT", calendar=CALENDAR, now: datetime.datetime = NOW) -> "S.EventContext":
    return S.EventContext(symbol=symbol, calendar=calendar, now=now, today=market_clock.to_et(now).date(),
                          extra_holidays={})


# ------------------------------------------------------------------------------------------------
# Expirations: the third Friday, standard monthlies and the window
# ------------------------------------------------------------------------------------------------


class ExpirationHelpersTest(unittest.TestCase):
    def test_third_friday(self) -> None:
        self.assertEqual(datetime.date(2026, 12, 18), S.third_friday(datetime.date(2026, 12, 1)))
        self.assertEqual(datetime.date(2027, 1, 15), S.third_friday(datetime.date(2027, 1, 31)))
        self.assertEqual(datetime.date(2027, 3, 19), S.third_friday(datetime.date(2027, 3, 19)))
        self.assertEqual(datetime.date(2027, 6, 18), S.third_friday(datetime.date(2027, 6, 2)))

    def test_standard_monthly_follows_a_holiday_friday(self) -> None:
        self.assertTrue(S.is_standard_monthly(datetime.date(2026, 12, 18)))
        self.assertTrue(S.is_standard_monthly(datetime.date(2027, 6, 17)))   # Juneteenth observed on the Friday
        self.assertFalse(S.is_standard_monthly(datetime.date(2027, 6, 18)))
        self.assertFalse(S.is_standard_monthly(datetime.date(2026, 12, 11)))  # a weekly
        self.assertFalse(S.is_standard_monthly(datetime.date(2026, 11, 27)))

    def test_window_keeps_standard_monthlies_inside_it_in_order(self) -> None:
        listed = [datetime.date(2026, 11, 20), datetime.date(2026, 12, 4), datetime.date(2026, 12, 11), JAN15, DEC18,
                  datetime.date(2027, 2, 19)]
        self.assertEqual([DEC18, JAN15], S.candidate_expirations(listed, TODAY, (40, 75)))
        self.assertEqual([JAN15], S.candidate_expirations(listed, TODAY, (40, 75), later_than=DEC18))
        # the window is inclusive at both ends, in calendar days to the last trading day
        self.assertEqual([DEC18], S.candidate_expirations(listed, TODAY, (45, 45)))
        self.assertEqual([], S.candidate_expirations(listed, TODAY, (46, 72)))
        self.assertEqual([MAR19], S.candidate_expirations([MAR19, datetime.date(2027, 6, 17)], TODAY, (120, 210)))

    def test_window_from_expiration_day(self) -> None:
        listed = [datetime.date(2026, 11, 20), DEC18, JAN15, datetime.date(2027, 2, 19)]
        self.assertEqual([JAN15], S.candidate_expirations(listed, datetime.date(2026, 11, 20), (40, 75),
                                                          later_than=datetime.date(2026, 11, 20)))


# ------------------------------------------------------------------------------------------------
# Objectives in Decimal (28 digits) on hand-built numbers
# ------------------------------------------------------------------------------------------------


class ObjectiveTest(unittest.TestCase):
    def test_extrinsic_per_day_out_of_the_money(self) -> None:
        self.assertEqual(D("3.55"), S.extrinsic(D("3.55"), D("104.23"), D("115")))
        self.assertEqual(D("0.0467"), S.extrinsic_per_day(D("3.55"), D("104.23"), D("115"), 73))

    def test_extrinsic_per_day_of_an_in_the_money_call(self) -> None:
        self.assertEqual(D("2.77"), S.extrinsic(D("12.00"), D("104.23"), D("95")))
        exact = D("2.77") / D("104.23") / D(45) * 100
        self.assertEqual(exact.quantize(D("0.0001")), S.extrinsic_per_day(D("12.00"), D("104.23"), D("95"), 45))
        self.assertEqual(D("0.0591"), S.extrinsic_per_day(D("12.0"), D("104.23"), D("95.0"), 45))
        # a bid below intrinsic: negative time value, a negative score (never clipped)
        self.assertEqual(D("-0.23"), S.extrinsic(D("9.00"), D("104.23"), D("95")))
        self.assertLess(S.extrinsic_per_day(D("9.00"), D("104.23"), D("95"), 45), 0)

    def test_quantization_is_half_even_at_four_places(self) -> None:
        # E / S / T x 100 = 0.00125 exactly: half-even keeps the even digit
        self.assertEqual(D("0.0012"), S.extrinsic_per_day(D("0.125"), D("100"), D("200"), 100))
        self.assertEqual(D("0.0014"), S.extrinsic_per_day(D("0.135"), D("100"), D("200"), 100))

    def test_stress_cost_per_day(self) -> None:
        stressed = S.stress_spot(D("648.12"), D("20"))
        self.assertEqual(D("518.496"), stressed)
        self.assertEqual(D("0.1856"), S.stress_cost_per_day(D("23.10"), D("610"), stressed, 136))
        self.assertEqual(D("0.1858"), S.stress_cost_per_day(D("25.65"), D("620"), stressed, 136))
        self.assertIsNone(S.stress_cost_per_day(D("1.00"), D("518.496"), stressed, 136))  # no cover at the stress
        self.assertIsNone(S.stress_cost_per_day(D("1.00"), D("500"), stressed, 136))

    def test_floor_and_cap_percentages(self) -> None:
        self.assertEqual(D("5.88"), S.floor_pct(D("648.12"), D("610")))
        self.assertEqual(D("-0.29"), S.floor_pct(D("648.12"), D("650")))
        self.assertEqual(D("4.92"), S.cap_pct(D("648.12"), D("680")))
        self.assertEqual(D("1.83"), S.cap_pct(D("648.12"), D("660")))

    def test_abs_delta_reads_two_places_like_the_rules(self) -> None:
        self.assertEqual(D("0.15"), S.abs_delta_2dp(0.14555377880513073))
        self.assertEqual(D("0.33"), S.abs_delta_2dp(0.3296425))
        self.assertEqual(D("0.30"), S.abs_delta_2dp(-0.3031126335862973))

    def test_per_day_normalisation_of_two_put_expirations(self) -> None:
        # The same strike and stress cover: the 181-day put costs more in total but less per day of cover.
        ctx = pricing("ZZZ", spot=spot_qc("ZZZ", "99.99", "100.01"), q=None)
        group = group_context("100", "9000.00", symbol="ZZZ", trade_id="AG-20261101-01")
        near = datetime.date(2027, 3, 3)
        far = datetime.date(2027, 5, 3)
        near_days = market_clock.dte(near, TODAY)
        far_days = market_clock.dte(far, TODAY)
        self.assertEqual((120, 181), (near_days, far_days))
        section = dataclasses.replace(CRITERIA.protective_put, dte=(100, 190), floor_pct=(D("0"), D("10")))
        legs = [S.price_contract(option("ZZZ", near, "put", "95", "2.95", "3.00"), ctx),
                S.price_contract(option("ZZZ", far, "put", "95", "3.95", "4.00"), ctx)]
        candidates = [S.protective_put_candidate(leg, ctx, group, section) for leg in legs]
        self.assertEqual([D("0.1667"), D("0.1473")], [c.score for c in candidates])
        ranking = S.rank_candidates(candidates, S.KIND_PROTECTIVE_PUT)
        self.assertEqual([far, near], [c.expiration for c in ranking.candidates])
        self.assertEqual(far, ranking.best.expiration)
        self.assertEqual(S.VERDICT_BEST, ranking.verdict)


# ------------------------------------------------------------------------------------------------
# Pricing and quote QC of a new leg (designer Q's D4): every UNPRICED code
# ------------------------------------------------------------------------------------------------


class PriceContractTest(unittest.TestCase):
    def code(self, q: Quote, ctx: Optional["S.PricingContext"] = None) -> Optional[str]:
        leg = S.price_contract(q, ctx if ctx is not None else pricing())
        return None if leg.unpriced is None else leg.unpriced.code

    def test_a_usable_quote_is_priced_at_its_mid(self) -> None:
        leg = S.price_contract(option("WMT", JAN15, "call", "115", "3.55", "3.65"), pricing())
        self.assertIsNone(leg.unpriced)
        self.assertEqual((D("3.55"), D("3.65"), D("3.60"), "B", D("2.78")), (leg.bid, leg.ask, leg.mid, leg.grade,
                                                                           leg.spread_pct))
        tau = (market_clock.exercise_cutoff_utc(JAN15, False) - STAMP).total_seconds() / units.SECONDS_PER_YEAR
        solved = iv.implied_vol(3.60, "CALL", 104.23, 115.0, tau, RATE, 0.009)
        self.assertAlmostEqual(solved.sigma, leg.iv, delta=1e-12)
        self.assertAlmostEqual(greeks.european_greeks("CALL", 104.23, 115.0, tau, RATE, 0.009, solved.sigma).delta,
                               leg.delta, delta=1e-12)
        self.assertAlmostEqual(tau, leg.tau_years, delta=1e-15)
        self.assertEqual(D("0.33"), leg.abs_delta)
        self.assertEqual((73, 50), (leg.dte_calendar, leg.dte_trading))
        self.assertEqual(JAN15, leg.last_trading_day)
        self.assertEqual(18, leg.quote_age_min)
        self.assertEqual(D("115"), leg.strike)

    def test_every_unpriced_code(self) -> None:
        late = STAMP - datetime.timedelta(minutes=40)
        holiday_now = datetime.datetime(2026, 11, 26, 17, 0, tzinfo=UTC)
        holiday = market_clock.session_at(holiday_now)
        cases = [
            ("quote_missing", option("WMT", JAN15, "call", "115", None, "3.65")),
            ("quote_undated", option("WMT", JAN15, "call", "115", "3.55", "3.65", stamp=None)),
            ("quote_stale", option("WMT", JAN15, "call", "115", "3.55", "3.65", stamp=late)),
            ("quote_one_side_stale", option("WMT", JAN15, "call", "115", "3.55", "3.65", bid_stamp=late)),
            ("quote_crossed", option("WMT", JAN15, "call", "115", "3.66", "3.65")),
            ("zero_bid", option("WMT", JAN15, "call", "115", "0", "0.10")),
            ("quote_wide", option("WMT", JAN15, "call", "115", "0.20", "0.30")),
            ("nonstandard_contract", option("WMT", JAN15, "call", "115", "3.55", "3.65", size=10)),
            ("nonstandard_contract", option("WMT", JAN15, "call", "115", "3.55", "3.65", root="WMT1")),
            ("iv_below_intrinsic", option("WMT", JAN15, "call", "95", "8.00", "8.10")),
            ("spot_unaligned", option("WMT", JAN15, "call", "115", "3.55", "3.65",
                                      stamp=STAMP + datetime.timedelta(seconds=61))),
        ]
        for expected, q in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, self.code(q))
        self.assertEqual("market_closed", self.code(option("WMT", JAN15, "call", "115", "3.55", "3.65"),
                                                    pricing(session=holiday, now=holiday_now)))
        stale_spot = pricing(spot=spot_qc(stamp=late))
        self.assertEqual("spot_unavailable", self.code(option("WMT", JAN15, "call", "115", "3.55", "3.65"), stale_spot))
        # under one hour to the exercise cutoff: no implied volatility
        late_now = datetime.datetime(2026, 11, 20, 20, 30, tzinfo=UTC)
        late_session = market_clock.session_at(late_now)
        stamp = datetime.datetime(2026, 11, 20, 20, 10, tzinfo=UTC)
        ctx = pricing(spot=spot_qc(stamp=stamp, session=late_session, now=late_now), session=late_session, now=late_now)
        leg = S.price_contract(option("WMT", datetime.date(2026, 11, 20), "call", "105", "0.12", "0.13", stamp=stamp), ctx)
        self.assertEqual("expiring", leg.unpriced.code)
        self.assertEqual(S.KEY_UNPRICED, leg.unpriced.key)
        # a same-day expiration has no per-day objective (T_c = 0), however long its session still runs
        morning = datetime.datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
        morning_session = market_clock.session_at(morning)
        stamp = datetime.datetime(2026, 11, 20, 14, 50, tzinfo=UTC)
        ctx = pricing(spot=spot_qc(stamp=stamp, session=morning_session, now=morning), session=morning_session,
                      now=morning)
        leg = S.price_contract(option("WMT", datetime.date(2026, 11, 20), "call", "105", "0.42", "0.45", stamp=stamp), ctx)
        self.assertEqual((0, "expiring"), (leg.dte_calendar, leg.unpriced.code))

    def test_a_spread_above_the_owner_maximum_is_priced_and_outside_the_band(self) -> None:
        ctx = pricing(max_spread="3")
        leg = S.price_contract(option("WMT", JAN15, "call", "120", "2.25", "2.35"), ctx)  # 4.35 %
        self.assertIsNone(leg.unpriced)
        candidate = S.covered_call_candidate(leg, ctx, group_context(earnings_ok=True, assign_ok=True),
                                             CRITERIA.covered_call, events())
        self.assertEqual(S.STATUS_OUTSIDE_BANDS, candidate.status)
        self.assertIn(("max_spread_pct", "spread_above_max", D("4.35"), D("3")),
                      [(r.key, r.code, r.value, r.threshold) for r in candidate.reasons])

    def test_unpriced_candidates_carry_exactly_one_reason(self) -> None:
        leg = S.price_contract(option("WMT", JAN15, "call", "115", "0", "0.10"), pricing())
        candidate = S.covered_call_candidate(leg, pricing(), group_context(), CRITERIA.covered_call, events())
        self.assertEqual(S.STATUS_UNPRICED, candidate.status)
        self.assertEqual([("unpriced", "zero_bid")], [(r.key, r.code) for r in candidate.reasons])
        self.assertIsNone(candidate.score)
        self.assertIsNone(candidate.after)


# ------------------------------------------------------------------------------------------------
# Status precedence, table order and tie-breaks (constructed exact ties)
# ------------------------------------------------------------------------------------------------


def leg_of(strike: str, expiration: datetime.date = DEC18, right: str = "CALL", abs_delta: Optional[str] = "0.30",
           root: str = "WMT") -> "S.PricedLeg":
    return S.PricedLeg(occ=occ_symbol(root, expiration, right.lower(), strike), root=root, right=right,
                       strike=D(strike), expiration=expiration,
                       abs_delta=None if abs_delta is None else D(abs_delta))


def made(kind: str, legs: Sequence["S.PricedLeg"], score: Optional[str], status: str = "ELIGIBLE",
         **fields) -> "S.Candidate":
    reasons = fields.pop("reasons", ())
    return S.Candidate(id="+".join(leg.occ for leg in legs), kind=kind, expiration=legs[0].expiration, legs=tuple(legs),
                       contracts=1, score=None if score is None else D(score), status=status, reasons=tuple(reasons),
                       **fields)


class StatusAndOrderTest(unittest.TestCase):
    def test_status_precedence(self) -> None:
        outside = S.Reason("abs_delta", "delta_outside_band", D("0.52"), [D("0.15"), D("0.35")])
        fails = S.Reason("credit_only", "debit_roll", D("-0.70"), D("0"))
        unpriced = S.Reason("unpriced", "zero_bid", D("0"), None)
        self.assertEqual("ELIGIBLE", S.status_of([]))
        self.assertEqual("FAILS_CRITERION", S.status_of([fails]))
        self.assertEqual("OUTSIDE_BANDS", S.status_of([fails, outside]))
        self.assertEqual("UNPRICED", S.status_of([unpriced]))
        self.assertEqual(("UNPRICED", "OUTSIDE_BANDS", "FAILS_CRITERION", "ELIGIBLE"), S.STATUS_PRECEDENCE)
        self.assertEqual(("ELIGIBLE", "FAILS_CRITERION", "OUTSIDE_BANDS", "UNPRICED"), S.TABLE_ORDER)

    def test_table_order_and_ranks(self) -> None:
        cands = [
            made(S.KIND_COVERED_CALL, [leg_of("120")], None, "UNPRICED"),
            made(S.KIND_COVERED_CALL, [leg_of("105")], "0.1226", "OUTSIDE_BANDS"),
            made(S.KIND_COVERED_CALL, [leg_of("112.5")], "0.0597", "FAILS_CRITERION"),
            made(S.KIND_COVERED_CALL, [leg_of("115")], "0.0448", "ELIGIBLE"),
            made(S.KIND_COVERED_CALL, [leg_of("110")], "0.0778", "OUTSIDE_BANDS"),
            made(S.KIND_COVERED_CALL, [leg_of("117.5")], "0.0300", "ELIGIBLE"),
            made(S.KIND_COVERED_CALL, [leg_of("100")], None, "UNPRICED"),
        ]
        ranking = S.rank_candidates(cands, S.KIND_COVERED_CALL)
        self.assertEqual(["115", "117.5", "112.5", "105", "110", "100", "120"],
                         [format(c.legs[0].strike, "f") for c in ranking.candidates])
        self.assertEqual(list(range(1, 8)), [c.rank for c in ranking.candidates])
        self.assertEqual("115", format(ranking.best.legs[0].strike, "f"))
        self.assertEqual((2, 5), (ranking.passed, ranking.failed))
        self.assertIsNone(ranking.candidates[0].lost_because)
        self.assertEqual({"by": "extrinsic_per_day", "value": D("0.0300"), "best": D("0.0448")},
                         ranking.candidates[1].lost_because)
        self.assertEqual("FAILS_CRITERION", ranking.candidates[2].lost_because["status"])

    def test_no_eligible_candidate_names_no_best(self) -> None:
        cands = [made(S.KIND_COVERED_CALL, [leg_of("112.5")], "0.0597", "FAILS_CRITERION"),
                 made(S.KIND_COVERED_CALL, [leg_of("105")], "0.1226", "OUTSIDE_BANDS")]
        ranking = S.rank_candidates(cands, S.KIND_COVERED_CALL)
        self.assertIsNone(ranking.best)
        self.assertEqual(S.VERDICT_NONE_QUALIFIES, ranking.verdict)
        self.assertEqual(1, ranking.candidates[0].rank)
        empty = S.rank_candidates([], S.KIND_COVERED_CALL)
        self.assertEqual((S.VERDICT_NONE_QUALIFIES, None, 0, 0), (empty.verdict, empty.best, empty.passed, empty.failed))

    def assert_order(self, kind: str, cands: List["S.Candidate"], expected_ids: List[str], tie: Optional[str]) -> None:
        for seed in range(6):
            shuffled = list(cands)
            random.Random(seed).shuffle(shuffled)
            ranking = S.rank_candidates(shuffled, kind)
            self.assertEqual(expected_ids, [c.id for c in ranking.candidates], seed)
            self.assertEqual(tie, ranking.tie_break)

    def test_covered_call_tie_breaks(self) -> None:
        a = made(S.KIND_COVERED_CALL, [leg_of("110", abs_delta="0.25")], "0.0500")
        b = made(S.KIND_COVERED_CALL, [leg_of("112.5", abs_delta="0.30")], "0.0500")
        self.assert_order(S.KIND_COVERED_CALL, [b, a], [a.id, b.id], "abs_delta")
        c = made(S.KIND_COVERED_CALL, [leg_of("110", JAN15)], "0.0500")
        d = made(S.KIND_COVERED_CALL, [leg_of("110", DEC18)], "0.0500")
        self.assert_order(S.KIND_COVERED_CALL, [c, d], [d.id, c.id], "expiration")
        e = made(S.KIND_COVERED_CALL, [leg_of("110")], "0.0500")
        f = made(S.KIND_COVERED_CALL, [leg_of("115")], "0.0500")
        self.assert_order(S.KIND_COVERED_CALL, [e, f], [f.id, e.id], "strike")
        g = made(S.KIND_COVERED_CALL, [leg_of("110", root="WMTB")], "0.0500")
        h = made(S.KIND_COVERED_CALL, [leg_of("110", root="WMTA")], "0.0500")
        self.assert_order(S.KIND_COVERED_CALL, [g, h], [h.id, g.id], "contract")
        ranking = S.rank_candidates([e, f], S.KIND_COVERED_CALL)
        self.assertEqual({"by": "strike", "value": D("110"), "best": D("115")}, ranking.candidates[1].lost_because)

    def test_roll_breaks_a_score_tie_by_net_credit_first(self) -> None:
        a = made(S.KIND_REPLACEMENT_CALL, [leg_of("115", abs_delta="0.35")], "0.0500", net_credit=D("0.10"))
        b = made(S.KIND_REPLACEMENT_CALL, [leg_of("112.5", abs_delta="0.20")], "0.0500", net_credit=D("0.05"))
        self.assert_order(S.KIND_REPLACEMENT_CALL, [b, a], [a.id, b.id], "net_credit")
        c = made(S.KIND_REPLACEMENT_CALL, [leg_of("115", abs_delta="0.35")], "0.0500", net_credit=D("0.05"))
        self.assert_order(S.KIND_REPLACEMENT_CALL, [c, b], [b.id, c.id], "abs_delta")

    def test_protective_put_tie_breaks(self) -> None:
        a = made(S.KIND_PROTECTIVE_PUT, [leg_of("610", MAR19, "PUT")], "0.1856")
        b = made(S.KIND_PROTECTIVE_PUT, [leg_of("620", MAR19, "PUT")], "0.1856")
        self.assert_order(S.KIND_PROTECTIVE_PUT, [a, b], [b.id, a.id], "strike")
        c = made(S.KIND_PROTECTIVE_PUT, [leg_of("610", JAN15, "PUT")], "0.1856")
        self.assert_order(S.KIND_PROTECTIVE_PUT, [c, a], [a.id, c.id], "expiration")
        d = made(S.KIND_PROTECTIVE_PUT, [leg_of("610", MAR19, "PUT", root="SPYB")], "0.1856")
        e = made(S.KIND_PROTECTIVE_PUT, [leg_of("610", MAR19, "PUT", root="SPYA")], "0.1856")
        self.assert_order(S.KIND_PROTECTIVE_PUT, [d, e], [e.id, d.id], "contract")
        lower = made(S.KIND_PROTECTIVE_PUT, [leg_of("590", MAR19, "PUT")], "0.1700")
        self.assert_order(S.KIND_PROTECTIVE_PUT, [a, lower], [lower.id, a.id], None)

    def test_collar_tie_breaks(self) -> None:
        def pair(put: str, call: str, expiration: datetime.date = MAR19, debit: str = "-0.75", score: str = "4.92",
                 root: str = "SPY") -> "S.Candidate":
            return made(S.KIND_COLLAR, [leg_of(put, expiration, "PUT", root=root), leg_of(call, expiration, "CALL", root=root)],
                        score, net_debit=D(debit))
        a, b = pair("620", "680"), pair("610", "680", debit="-3.30")
        self.assert_order(S.KIND_COLLAR, [b, a], [a.id, b.id], "put_strike")
        c = pair("620", "680", debit="-1.00")
        self.assert_order(S.KIND_COLLAR, [a, c], [c.id, a.id], "net_debit")
        d = pair("620", "680", JAN15)
        self.assert_order(S.KIND_COLLAR, [d, a], [a.id, d.id], "expiration")
        e, f = pair("620", "680", root="SPYB"), pair("620", "680", root="SPYA")
        self.assert_order(S.KIND_COLLAR, [e, f], [f.id, e.id], "contract")
        higher = pair("590", "700", debit="-0.10", score="8.00")
        self.assert_order(S.KIND_COLLAR, [a, higher], [higher.id, a.id], None)

    def test_tie_break_tables(self) -> None:
        self.assertEqual(("abs_delta", "expiration", "strike", "contract"), S.TIE_BREAKS[S.KIND_COVERED_CALL])
        self.assertEqual(("net_credit", "abs_delta", "expiration", "strike", "contract"),
                         S.TIE_BREAKS[S.KIND_REPLACEMENT_CALL])
        self.assertEqual(("strike", "expiration", "contract"), S.TIE_BREAKS[S.KIND_PROTECTIVE_PUT])
        self.assertEqual(("strike", "expiration", "contract"), S.TIE_BREAKS[S.KIND_REPLACEMENT_PUT])
        self.assertEqual(("put_strike", "net_debit", "expiration", "contract"), S.TIE_BREAKS[S.KIND_COLLAR])


# ------------------------------------------------------------------------------------------------
# Events for short calls (designer Q's D9 with the addendum S4) and acceptance
# ------------------------------------------------------------------------------------------------


def codes(reasons: Sequence["S.Reason"]) -> List[str]:
    return [reason.code for reason in reasons]


class EventTest(unittest.TestCase):
    def test_a_fresh_call_crosses_earnings_and_the_ex_date(self) -> None:
        reasons = S.short_call_event_reasons(events(), new_expiration=DEC18, held_expiration=None)
        self.assertEqual(["crosses_earnings", "crosses_ex_dividend"], codes(reasons))
        earnings, dividend = reasons
        self.assertEqual("earnings", earnings.key)
        self.assertEqual({"date": datetime.date(2026, 11, 17), "time": "pre", "status": "confirmed"}, earnings.value)
        self.assertEqual({"from": NOW, "to": market_clock.exercise_cutoff_utc(DEC18, False)}, earnings.threshold)
        self.assertEqual("ex_dividend", dividend.key)
        self.assertEqual({"ex_date": datetime.date(2026, 11, 24), "cum_day": datetime.date(2026, 11, 23),
                          "amount": D("0.2350"), "status": "confirmed"}, dividend.value)
        self.assertEqual({"last_trading_day": DEC18}, dividend.threshold)

    def test_a_replacement_is_measured_from_the_held_call_cutoff(self) -> None:
        held = datetime.date(2026, 11, 20)
        self.assertEqual(["crosses_ex_dividend"],
                         codes(S.short_call_event_reasons(events(), new_expiration=JAN15, held_expiration=held)))
        # the held call already spans the ex-date: the replacement adds no crossing
        self.assertEqual([], codes(S.short_call_event_reasons(events(), new_expiration=JAN15, held_expiration=DEC18)))

    def test_unknown_unlisted_absent_and_passed_events(self) -> None:
        self.assertEqual(["earnings_unknown"],
                         codes(S.short_call_event_reasons(events("ZZZ"), new_expiration=DEC18, held_expiration=None)))
        unlisted = S.short_call_event_reasons(events("NOPE"), new_expiration=DEC18, held_expiration=None)
        self.assertEqual(["earnings_unknown", "dividend_unknown"], codes(unlisted))
        self.assertEqual("unlisted", unlisted[0].value["status"])
        no_calendar = S.short_call_event_reasons(events(calendar=None), new_expiration=DEC18, held_expiration=None)
        self.assertEqual(["earnings_unknown", "dividend_unknown"], codes(no_calendar))
        self.assertEqual("no_calendar", no_calendar[1].value["status"])
        # an earnings date that has passed says nothing about the next one: unknown, never none
        later = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)
        passed = S.short_call_event_reasons(events(now=later), new_expiration=JAN15,
                                            held_expiration=datetime.date(2026, 11, 20))
        self.assertEqual(["earnings_unknown", "crosses_ex_dividend"], codes(passed))
        self.assertEqual({"status": "passed", "date": datetime.date(2026, 11, 17)}, passed[0].value)
        # an ex-date that has passed: the dividend is unknown (expiry.dividend_window)
        after_ex = datetime.datetime(2026, 11, 25, 19, 50, tzinfo=UTC)
        self.assertEqual(["earnings_unknown", "dividend_unknown"],
                         codes(S.short_call_event_reasons(events(now=after_ex), new_expiration=JAN15,
                                                          held_expiration=None)))

    def test_status_none_gives_no_label(self) -> None:
        self.assertEqual(["crosses_ex_dividend"],
                         codes(S.short_call_event_reasons(events("SPY"), new_expiration=JAN15, held_expiration=None)))

    def test_second_ex_date_horizon(self) -> None:
        self.assertEqual(80, S.SECOND_EX_DATE_HORIZON_DAYS)
        reasons = S.short_call_event_reasons(events("SPY"), new_expiration=MAR19, held_expiration=None)
        self.assertEqual(["crosses_ex_dividend", "dividend_unknown"], codes(reasons))
        self.assertEqual({"status": "second_ex_date", "ex_date": datetime.date(2026, 12, 18)}, reasons[1].value)
        self.assertEqual(80, reasons[1].threshold)

        def horizon(ex_date: str, expiration: datetime.date) -> List[str]:
            text = CALENDAR_TEXT.replace("date = 2026-12-18, amount = 1.85", "date = %s, amount = 1.85" % ex_date)
            cal = calendar_events.parse_calendar_text(text)
            return codes(S.short_call_event_reasons(events("SPY", calendar=cal), new_expiration=expiration,
                                                    held_expiration=None))
        # 2027-03-19 is 80 days after 2026-12-29 (labelled) and 79 days after 2026-12-30 (not)
        self.assertEqual(["crosses_ex_dividend", "dividend_unknown"], horizon("2026-12-29", MAR19))
        self.assertEqual(["crosses_ex_dividend"], horizon("2026-12-30", MAR19))

    def test_acceptance_by_tag_and_by_the_criteria_file(self) -> None:
        reasons = S.short_call_event_reasons(events(), new_expiration=DEC18, held_expiration=None)
        kept, accepted = S.split_accepted(reasons, earnings_ok=False, assign_ok=True, earnings_policy="exclude",
                                          ex_dividend_policy="exclude")
        self.assertEqual(["crosses_earnings"], codes(kept))
        self.assertEqual([("ex_dividend", "crosses_ex_dividend", "#assign-ok")],
                         [(a.key, a.code, a.by) for a in accepted])
        kept, accepted = S.split_accepted(reasons, earnings_ok=False, assign_ok=False, earnings_policy="allow",
                                          ex_dividend_policy="exclude")
        self.assertEqual(["crosses_ex_dividend"], codes(kept))
        self.assertEqual([("earnings", "crosses_earnings", "your structuring.toml")],
                         [(a.key, a.code, a.by) for a in accepted])
        kept, accepted = S.split_accepted(reasons, earnings_ok=True, assign_ok=True, earnings_policy="allow",
                                          ex_dividend_policy="allow")
        self.assertEqual([], kept)
        self.assertEqual(["#earnings-ok", "#assign-ok"], [a.by for a in accepted])
        self.assertEqual({"key", "code", "by", "value", "threshold"}, set(accepted[0].as_dict()))
        self.assertEqual({"key", "code", "value", "threshold"}, set(reasons[0].as_dict()))


# ------------------------------------------------------------------------------------------------
# Applicability (the addendum S1): NOT_APPLICABLE and NOTHING_TO_PROPOSE with their reasons
# ------------------------------------------------------------------------------------------------

HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,close_price_per_unit,"
          "underlying_stop,note")


def group_of(*rows: str, trade_id: str = "AG-20261101-01") -> grouping.PositionGroup:
    parsed = positions_csv.parse_positions_text("\n".join((HEADER,) + rows) + "\n", today=TODAY)
    assert not parsed.file_errors and not parsed.quarantined, (parsed.file_errors, parsed.quarantined)
    return [g for g in grouping.group_positions(parsed.rows) if g.trade_id == trade_id][0]


SHARES = "equity,WMT,,200,,,,Brokerage (margin),AG-20261101-01,2026-10-20,,98.50,,open,,,,,"
FEW = "equity,WMT,,50,,,,Brokerage (margin),AG-20261101-01,2026-10-20,,98.50,,open,,,,,"
CALL = "option,WMT,WMT261218C00110000,-2,,,,Brokerage (margin),AG-20261101-01,2026-10-20,-560.00,,,open,,,,,"
LONG_CALL = "option,WMT,WMT261218C00120000,2,,,,Brokerage (margin),AG-20261101-01,2026-10-20,240.00,,,open,,,,,"
PUT = "option,WMT,WMT261218P00090000,2,,,,Brokerage (margin),AG-20261101-01,2026-10-20,300.00,,,open,,,,,"
SHORT_PUT = "option,WMT,WMT261218P00090000,-1,,,,Brokerage (margin),AG-20261101-01,2026-10-20,-150.00,,,open,,,,,"
INDEX_PUT = "option,SPX,SPX261218P05000000,1,,,,Brokerage (margin),AG-20261101-01,2026-10-20,3000.00,,,open,,,,,"
CLOSED_SHARES = "equity,WMT,,200,,,,Brokerage (margin),AG-20261101-01,2026-10-20,,98.50,,closed,2026-10-30,200,99.00,,"
CLOSED_CALL = ("option,WMT,WMT261218C00110000,-2,,,,Brokerage (margin),AG-20261101-01,2026-10-20,-560.00,,,closed,"
               "2026-10-30,2,1.00,,")


class ApplicabilityTest(unittest.TestCase):
    def test_roll_analysis_covers_the_short_call_of_a_covered_call_only(self) -> None:
        self.assertIsNone(S.roll_applicability(group_of(SHARES, CALL), CRITERIA))
        for rows, words in (((SHARES, PUT), "protective put"), ((SHARES, PUT, CALL), "collar"),
                            ((SHORT_PUT,), "cash-secured put"), ((SHARES,), "long stock"),
                            ((CALL.replace("-2,", "-1,").replace("-560.00", "-280.00"),
                              LONG_CALL.replace(",2,", ",1,").replace("240.00", "120.00")), "call credit vertical")):
            with self.subTest(words=words):
                answer = S.roll_applicability(group_of(*rows), CRITERIA)
                self.assertEqual((S.VERDICT_NOT_APPLICABLE, S.NA_STRUCTURE), (answer.verdict, answer.code))
                self.assertIn(words, answer.reason)
                self.assertEqual(words == "protective put", "a ROLL ticket" in answer.reason)
        index = S.roll_applicability(group_of(INDEX_PUT), CRITERIA)
        self.assertEqual(S.NA_INDEX, index.code)
        closed = S.roll_applicability(group_of(CLOSED_SHARES, CLOSED_CALL), CRITERIA)
        self.assertEqual(S.NA_NO_OPEN_LEGS, closed.code)
        disabled = dataclasses.replace(CRITERIA, roll=dataclasses.replace(CRITERIA.roll, enabled=False))
        answer = S.roll_applicability(group_of(SHARES, CALL), disabled)
        self.assertEqual((S.VERDICT_NOT_APPLICABLE, S.NA_DISABLED, structuring_config.DISABLED_REASON),
                         (answer.verdict, answer.code, answer.reason))

    def test_proposals_are_overlays_on_shares_only_groups(self) -> None:
        for structure in S.PROPOSAL_STRUCTURES:
            self.assertIsNone(S.proposal_applicability(group_of(SHARES), structure, CRITERIA))
        few = S.proposal_applicability(group_of(FEW), "covered_call", CRITERIA)
        self.assertEqual((S.VERDICT_NOTHING_TO_PROPOSE, S.NOTHING_FEWER_THAN_100), (few.verdict, few.code))
        covered = S.proposal_applicability(group_of(SHARES, CALL), "protective_put", CRITERIA)
        self.assertEqual((S.VERDICT_NOTHING_TO_PROPOSE, S.NOTHING_NOT_SHARES_ONLY), (covered.verdict, covered.code))
        self.assertIn("covered call", covered.reason)
        hedged = S.proposal_applicability(group_of(SHARES.replace(",200,", ",100,"), PUT.replace(",2,", ",1,")),
                                          "protective_put", CRITERIA)
        self.assertEqual("the 100 WMT shares already carry 1 long put", hedged.reason)
        none = S.proposal_applicability(group_of(SHORT_PUT), "covered_call", CRITERIA)
        self.assertEqual(S.NOTHING_NO_SHARES, none.code)
        disabled = dataclasses.replace(CRITERIA, collar=dataclasses.replace(CRITERIA.collar, enabled=False))
        self.assertEqual(S.NA_DISABLED, S.proposal_applicability(group_of(SHARES), "collar", disabled).code)
        with self.assertRaises(ValueError):
            S.proposal_applicability(group_of(SHARES), "cash_secured_put", CRITERIA)

    def test_ticket_actions(self) -> None:
        covered, shares, protective = group_of(SHARES, CALL), group_of(SHARES), group_of(SHARES, PUT)
        collar, short_put = group_of(SHARES, PUT, CALL), group_of(SHORT_PUT)
        expectations = [
            ("CLOSE", covered, None), ("CLOSE", shares, S.NA_ACTION), ("UNWIND_STOCK", covered, None),
            ("UNWIND_STOCK", short_put, S.NA_ACTION), ("LET_ASSIGN", covered, None), ("LET_ASSIGN", short_put, None),
            ("LET_ASSIGN", protective, S.NA_ACTION), ("MONETIZE_PUT", protective, None), ("MONETIZE_PUT", collar, None),
            ("MONETIZE_PUT", covered, S.NA_ACTION), ("ROLL", covered, None), ("ROLL", protective, None),
            ("ROLL", collar, S.NA_STRUCTURE), ("ROLL", short_put, S.NA_STRUCTURE), ("ROLL", shares, S.NA_STRUCTURE),
        ]
        for action, group, expected in expectations:
            with self.subTest(action=action, structure=group.structure):
                answer = S.ticket_applicability(group, action, CRITERIA)
                self.assertEqual(expected, None if answer is None else answer.code)
        self.assertEqual(S.NA_INDEX, S.ticket_applicability(group_of(INDEX_PUT), "CLOSE", CRITERIA).code)
        disabled = dataclasses.replace(CRITERIA, roll=dataclasses.replace(CRITERIA.roll, enabled=False))
        self.assertEqual(S.NA_DISABLED, S.ticket_applicability(covered, "ROLL", disabled).code)
        self.assertIsNone(S.ticket_applicability(covered, "CLOSE", disabled))
        self.assertEqual(("CLOSE", "UNWIND_STOCK", "LET_ASSIGN", "MONETIZE_PUT", "ROLL"), S.ACTIONS)
        with self.assertRaises(ValueError):
            S.ticket_applicability(covered, "ROLL_TO", CRITERIA)


# ------------------------------------------------------------------------------------------------
# The American caveat (designer Q's D4)
# ------------------------------------------------------------------------------------------------


class AmericanCaveatTest(unittest.TestCase):
    def test_short_call_in_the_money_with_a_dividend_above_its_extrinsic(self) -> None:
        ctx = pricing()
        group = group_context(earnings_ok=True, assign_ok=True)
        # bid-side extrinsic 9.40 - 9.23 = 0.17 < 0.2350 (the mid 9.65 lies above the forward intrinsic bound 9.60)
        low = S.price_contract(option("WMT", DEC18, "call", "95", "9.40", "9.90"), ctx)
        rich = S.price_contract(option("WMT", DEC18, "call", "95", "12.0", "12.2"), ctx)  # extrinsic 2.77
        otm = S.price_contract(option("WMT", DEC18, "call", "115", "2.1", "2.2"), ctx)
        flags = [S.covered_call_candidate(leg, ctx, group, CRITERIA.covered_call, events()).american_caveat
                 for leg in (low, rich, otm)]
        self.assertEqual([True, False, False], flags)

    def test_long_put_with_an_absolute_delta_of_at_least_080(self) -> None:
        ctx = pricing("ZZZ", spot=spot_qc("ZZZ", "99.99", "100.01"), q=None)
        group = group_context("100", "9000.00", symbol="ZZZ", trade_id="AG-20261101-01")
        section = dataclasses.replace(CRITERIA.protective_put, floor_pct=(D("0"), D("50")))
        deep = S.price_contract(option("ZZZ", MAR19, "put", "150", "50.40", "50.60"), ctx)
        near = S.price_contract(option("ZZZ", MAR19, "put", "95", "3.95", "4.05"), ctx)
        self.assertGreaterEqual(deep.abs_delta, D("0.80"))
        self.assertEqual([True, False], [S.protective_put_candidate(leg, ctx, group, section).american_caveat
                                         for leg in (deep, near)])


# ------------------------------------------------------------------------------------------------
# Fixture goldens (designer Q's D16 under S17), on copies of tests/fixtures/desk
# ------------------------------------------------------------------------------------------------


class Desk:
    """A copy of tests/fixtures/desk with the fixture criteria; ``drop_row`` removes one positions.csv line and
    ``replace`` rewrites one text of it; the copy is removed by the owning test class."""

    def __init__(self, owner, drop_row: Optional[int] = None, replace: Optional[Tuple[str, str]] = None) -> None:
        self.tmp = tempfile.mkdtemp(prefix="aegis-structuring-")
        owner.addClassCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        shutil.copyfile(CRITERIA_FILE, os.path.join(self.root, "structuring.toml"))
        path = os.path.join(self.root, "positions.csv")
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        if drop_row is not None:
            del lines[drop_row - 1]
        text = "\n".join(lines)
        if replace is not None:
            assert text.count(replace[0]) == 1, replace
            text = text.replace(*replace)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def market(self, fixture_set: str = "regular", now: datetime.datetime = NOW) -> quote_cache.MarketData:
        return quote_cache.open_market_data(self.root, now=now, offline=True, fixture_set=fixture_set, timeout_s=5.0,
                                            user_agent="aegis-desk-test")

    @property
    def positions_text(self) -> str:
        with open(os.path.join(self.root, "positions.csv"), encoding="utf-8") as handle:
            return handle.read()


def by_id(answer: "S.Answer") -> Dict[str, "S.Candidate"]:
    return {candidate.id: candidate for candidate in answer.ranking.candidates}


def solved(occ: str, right: str, spot: str, strike: str, expiration: datetime.date, mid: str, q: float,
           stamp: datetime.datetime = STAMP) -> Tuple[float, float]:
    """``(sigma, delta)`` solved independently from the mid with the landed iv and greeks."""
    tau = (market_clock.exercise_cutoff_utc(expiration, False) - stamp).total_seconds() / units.SECONDS_PER_YEAR
    result = iv.implied_vol(float(D(mid)), right, float(D(spot)), float(D(strike)), tau, RATE, q)
    assert result.ok, (occ, result)
    return result.sigma, greeks.european_greeks(right, float(D(spot)), float(D(strike)), tau, RATE, q, result.sigma).delta


def money(value: Optional[float]) -> Optional[str]:
    return None if value is None else format(D(repr(value)).quantize(D("0.01")), "f")


class RollGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.desk = Desk(cls)
        cls.wmt = S.run_roll_analysis(cls.desk.market(), "AG-20261020-01", CRITERIA)
        cls.nvda = S.run_roll_analysis(Desk(cls).market(), "AG-20261022-02", CRITERIA)

    def test_wmt_names_the_january_115_call(self) -> None:
        answer = self.wmt
        self.assertEqual((S.VERDICT_BEST, None, None), (answer.verdict, answer.cause, answer.reason))
        self.assertEqual("WMT270115C00115000", answer.ranking.best.id)
        self.assertEqual({"made": 4, "chains": 2, "per_run_max_requests": 20, "per_run_max_chains": 4,
                          "budget_left": 51}, answer.requests)
        self.assertEqual(([DEC18, JAN15], [DEC18, JAN15], []), (answer.expirations.listed_in_window,
                                                                answer.expirations.evaluated,
                                                                answer.expirations.not_evaluated))
        self.assertEqual((20, 1, 19), (len(answer.ranking.candidates), answer.ranking.passed, answer.ranking.failed))
        self.assertEqual({"abs_delta": 15, "credit_only": 4, "strike_floor": 4}, answer.ranking.failure_counts)
        self.assertIsNone(answer.ranking.tie_break)
        self.assertEqual(S.KIND_REPLACEMENT_CALL, answer.kind)
        self.assertEqual("extrinsic_per_day", answer.objective.id)

    def test_wmt_held_call_and_before_state(self) -> None:
        held, before = self.wmt.held, self.wmt.before
        self.assertEqual(("WMT261120C00105000", 4, "short", 2, D("105"), datetime.date(2026, 11, 20)),
                         (held.occ, held.row_number, held.side, held.contracts, held.strike, held.expiration))
        self.assertEqual((D("3.35"), D("3.50"), D("3.425"), D("4.38"), "B", 18), (held.bid, held.ask, held.mid,
                                                                                 held.spread_pct, held.grade,
                                                                                 held.quote_age_min))
        self.assertEqual((D("3.50"), "ask", None, D("-16.00"), D("3.42"), D("-684.00")),
                         (held.close_price, held.close_side, held.note, held.realized, held.entry_price_per_share,
                          held.remaining_cost_basis))
        self.assertEqual(0.4132098189873759, held.iv)
        self.assertEqual(0.49146745409631537, held.delta)
        self.assertEqual(101.70650918073693, before.group_delta)
        self.assertEqual((D("19016.00"), D("1984.00"), D("95.08"), False),
                         (before.max_loss, before.max_profit, before.breakeven, before.unbounded_up))
        self.assertEqual((D("200"), D("19700.00"), D("98.50")), (self.wmt.group_context.shares,
                                                                self.wmt.group_context.stock_cost,
                                                                self.wmt.group_context.s0))
        self.assertEqual((D("104.23"), "A"), (self.wmt.pricing.spot.mark_mid, self.wmt.pricing.spot.grade))

    def test_wmt_best_candidate(self) -> None:
        best = self.wmt.ranking.best
        leg = best.legs[0]
        self.assertEqual((1, "ELIGIBLE", D("0.0467"), D("0.05"), D("10.00"), D("3.55")),
                         (best.rank, best.status, best.score, best.net_credit, best.net_credit_total, best.extrinsic_bid))
        self.assertEqual((D("3.55"), D("3.65"), D("3.60"), "B", D("2.78"), 18, 73, 50, D("0.33")),
                         (leg.bid, leg.ask, leg.mid, leg.grade, leg.spread_pct, leg.quote_age_min, leg.dte_calendar,
                          leg.dte_trading, leg.abs_delta))
        sigma, delta = solved(leg.occ, "CALL", "104.23", "115", JAN15, "3.60", 0.009)
        self.assertAlmostEqual(sigma, leg.iv, delta=1e-9)
        self.assertAlmostEqual(delta, leg.delta, delta=1e-9)
        self.assertEqual("0.390069", "%.6f" % leg.iv)
        self.assertEqual("0.329643", "%.6f" % leg.delta)
        self.assertEqual([], list(best.reasons))
        self.assertEqual([("ex_dividend", "crosses_ex_dividend", "#assign-ok")],
                         [(a.key, a.code, a.by) for a in best.accepted])
        self.assertEqual({"ex_date": datetime.date(2026, 11, 24), "cum_day": datetime.date(2026, 11, 23),
                          "amount": D("0.2350"), "status": "confirmed"}, best.accepted[0].value)
        after = best.after
        self.assertAlmostEqual(200 - 200 * delta, after.group_delta, delta=1e-9)
        self.assertEqual(("134.07", "32.36"), (money(after.group_delta), money(after.delta_change)))
        self.assertEqual((D("19006.00"), D("3994.00"), D("95.03"), (D("95.03"),), False, D("-16.00")),
                         (after.max_loss, after.max_profit, after.breakeven, after.breakevens, after.unbounded_up,
                          after.realized_on_close))
        self.assertFalse(best.american_caveat)
        self.assertIsNone(best.lost_because)

    def test_wmt_table(self) -> None:
        table = [(c.rank, c.id, c.status, format(c.score, "f")) for c in self.wmt.ranking.candidates]
        self.assertEqual([
            (1, "WMT270115C00115000", "ELIGIBLE", "0.0467"), (2, "WMT261218C00112500", "FAILS_CRITERION", "0.0597"),
            (3, "WMT261218C00115000", "FAILS_CRITERION", "0.0448"), (4, "WMT270115C00120000", "FAILS_CRITERION", "0.0296"),
            (5, "WMT261218C00120000", "FAILS_CRITERION", "0.0235"), (6, "WMT261218C00105000", "OUTSIDE_BANDS", "0.1226"),
            (7, "WMT261218C00102500", "OUTSIDE_BANDS", "0.1145"), (8, "WMT270115C00105000", "OUTSIDE_BANDS", "0.0992"),
            (9, "WMT261218C00107500", "OUTSIDE_BANDS", "0.0991"), (10, "WMT270115C00102500", "OUTSIDE_BANDS", "0.0936"),
            (11, "WMT261218C00100000", "OUTSIDE_BANDS", "0.0921"), (12, "WMT270115C00107500", "OUTSIDE_BANDS", "0.0835"),
            (13, "WMT270115C00100000", "OUTSIDE_BANDS", "0.0798"), (14, "WMT261218C00110000", "OUTSIDE_BANDS", "0.0778"),
            (15, "WMT261218C00097500", "OUTSIDE_BANDS", "0.0740"), (16, "WMT270115C00110000", "OUTSIDE_BANDS", "0.0690"),
            (17, "WMT270115C00097500", "OUTSIDE_BANDS", "0.0679"), (18, "WMT261218C00095000", "OUTSIDE_BANDS", "0.0591"),
            (19, "WMT270115C00095000", "OUTSIDE_BANDS", "0.0574"), (20, "WMT270115C00112500", "OUTSIDE_BANDS", "0.0572"),
        ], table)
        cands = by_id(self.wmt)
        runner = cands["WMT261218C00112500"]
        self.assertEqual([("credit_only", "debit_roll", D("-0.70"), D("0"))],
                         [(r.key, r.code, r.value, r.threshold) for r in runner.reasons])
        self.assertEqual((D("-0.70"), D("-140.00"), D("0.32")), (runner.net_credit, runner.net_credit_total,
                                                                runner.legs[0].abs_delta))
        self.assertEqual({"by": "status", "status": "FAILS_CRITERION", "codes": ["debit_roll"]}, runner.lost_because)
        deep = cands["WMT261218C00097500"]
        self.assertEqual([("abs_delta", "delta_outside_band", D("0.70"), [D("0.15"), D("0.35")]),
                          ("strike_floor", "roll_down_below_cost", D("97.5"), D("98.50"))],
                         [(r.key, r.code, r.value, r.threshold) for r in deep.reasons])
        self.assertEqual(D("3.47"), deep.extrinsic_bid)   # 10.20 - (104.23 - 97.5): an in-the-money strike

    def test_nvda_none_qualifies(self) -> None:
        answer = self.nvda
        self.assertEqual((S.VERDICT_NONE_QUALIFIES, None), (answer.verdict, answer.ranking.best))
        self.assertEqual((0, 20), (answer.ranking.passed, answer.ranking.failed))
        self.assertEqual({"abs_delta": 14, "min_credit": 1, "credit_only": 6, "strike_floor": 6, "ex_dividend": 18,
                          "unpriced": 2}, answer.ranking.failure_counts)
        table = [(c.id, c.status, None if c.score is None else format(c.score, "f")) for c in answer.ranking.candidates]
        self.assertEqual([
            ("NVDA270115C00160000", "FAILS_CRITERION", "0.0299"), ("NVDA261218C00160000", "FAILS_CRITERION", "0.0276"),
            ("NVDA270115C00165000", "FAILS_CRITERION", "0.0179"), ("NVDA261218C00165000", "FAILS_CRITERION", "0.0134"),
            ("NVDA261218C00150000", "OUTSIDE_BANDS", "0.0821"), ("NVDA270115C00150000", "OUTSIDE_BANDS", "0.0671"),
            ("NVDA261218C00145000", "OUTSIDE_BANDS", "0.0660"), ("NVDA270115C00145000", "OUTSIDE_BANDS", "0.0573"),
            ("NVDA261218C00155000", "OUTSIDE_BANDS", "0.0507"), ("NVDA270115C00155000", "OUTSIDE_BANDS", "0.0460"),
            ("NVDA261218C00140000", "OUTSIDE_BANDS", "0.0436"), ("NVDA270115C00140000", "OUTSIDE_BANDS", "0.0421"),
            ("NVDA270115C00135000", "OUTSIDE_BANDS", "0.0310"), ("NVDA261218C00135000", "OUTSIDE_BANDS", "0.0287"),
            ("NVDA270115C00130000", "OUTSIDE_BANDS", "0.0228"), ("NVDA261218C00130000", "OUTSIDE_BANDS", "0.0190"),
            ("NVDA270115C00170000", "OUTSIDE_BANDS", "0.0097"), ("NVDA261218C00170000", "OUTSIDE_BANDS", "0.0052"),
            ("NVDA261218C00180000", "UNPRICED", None), ("NVDA270115C00180000", "UNPRICED", None),
        ], table)
        cands = by_id(answer)
        top = cands["NVDA270115C00160000"]
        self.assertEqual(["debit_roll", "crosses_ex_dividend"], codes(top.reasons))
        self.assertEqual((D("-0.05"), D("-10.00"), D("0.31")), (top.net_credit, top.net_credit_total,
                                                               top.legs[0].abs_delta))
        self.assertEqual(("137.35", "32.75", D("27640.00"), D("4360.00"), D("138.20")),
                         (money(top.after.group_delta), money(top.after.delta_change), top.after.max_loss,
                          top.after.max_profit, top.after.breakeven))
        self.assertEqual("C", answer.held.grade)
        self.assertEqual(D("3.30"), answer.held.close_price)
        self.assertEqual("D", cands["NVDA261218C00165000"].legs[0].grade)
        self.assertEqual(D("0.15"), cands["NVDA261218C00165000"].legs[0].abs_delta)   # 0.1456 reads 0.15
        self.assertEqual(["zero_bid"], codes(cands["NVDA261218C00180000"].reasons))
        self.assertEqual(["quote_wide"], codes(cands["NVDA270115C00180000"].reasons))
        self.assertEqual(D("40.00"), cands["NVDA270115C00180000"].reasons[0].value)
        self.assertEqual(["delta_outside_band", "credit_below_minimum", "debit_roll", "crosses_ex_dividend"],
                         codes(cands["NVDA261218C00170000"].reasons))
        self.assertEqual(104.6093299587413, answer.before.group_delta)

    def test_deltas_and_ivs_against_an_independent_solve(self) -> None:
        for answer, spot, q in ((self.wmt, "104.23", 0.009), (self.nvda, "148.925", 0.0003)):
            for candidate in answer.ranking.candidates:
                leg = candidate.legs[0]
                if leg.unpriced is not None:
                    continue
                sigma, delta = solved(leg.occ, leg.right, spot, format(leg.strike, "f"), leg.expiration,
                                      format(leg.mid, "f"), q)
                with self.subTest(occ=leg.occ):
                    self.assertAlmostEqual(sigma, leg.iv, delta=1e-9)
                    self.assertAlmostEqual(delta, leg.delta, delta=1e-9)
                    self.assertEqual(abs(D(repr(delta))).quantize(D("0.01")), leg.abs_delta)


class ProposalGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main = Desk(cls)
        cls.wmt_plain = S.run_proposal(Desk(cls, drop_row=4).market(), "AG-20261020-01", "covered_call", CRITERIA)
        cls.wmt_desk = Desk(cls, drop_row=4, replace=WMT_TAGS)
        cls.wmt = S.run_proposal(cls.wmt_desk.market(), "AG-20261020-01", "covered_call", CRITERIA)
        cls.spy_desk = Desk(cls, drop_row=8)
        cls.put = S.run_proposal(cls.spy_desk.market(), "AG-20261028-03", "protective_put", CRITERIA)
        cls.collar_plain = S.run_proposal(Desk(cls, drop_row=8).market(), "AG-20261028-03", "collar", CRITERIA)
        cls.collar_desk = Desk(cls, drop_row=8, replace=SPY_TAG)
        cls.collar = S.run_proposal(cls.collar_desk.market(), "AG-20261028-03", "collar", CRITERIA)

    def test_main_fixture_has_nothing_to_propose(self) -> None:
        put = S.run_proposal(self.main.market(), "AG-20261028-03", "protective_put", CRITERIA)
        self.assertEqual((S.VERDICT_NOTHING_TO_PROPOSE, "the 100 SPY shares already carry 1 long put", 0),
                         (put.verdict, put.reason, put.requests["made"]))
        call = S.run_proposal(self.main.market(), "AG-20261020-01", "covered_call", CRITERIA)
        self.assertEqual((S.VERDICT_NOTHING_TO_PROPOSE, S.NOTHING_NOT_SHARES_ONLY), (call.verdict, call.cause))
        self.assertIn("covered call", call.reason)

    def test_wmt_covered_call_without_tags_none_qualifies(self) -> None:
        answer = self.wmt_plain
        self.assertEqual((S.VERDICT_NONE_QUALIFIES, None), (answer.verdict, answer.ranking.best))
        top = answer.ranking.candidates[0]
        self.assertEqual(("WMT261218C00112500", D("0.0597"), ["crosses_earnings", "crosses_ex_dividend"]),
                         (top.id, top.score, codes(top.reasons)))

    def test_wmt_covered_call_with_tags(self) -> None:
        answer = self.wmt
        self.assertEqual((S.VERDICT_BEST, "WMT261218C00112500", 2, D("200")),
                         (answer.verdict, answer.ranking.best.id, answer.contracts, answer.group_context.shares))
        self.assertEqual(4, answer.requests["made"])
        best = answer.ranking.best
        leg = best.legs[0]
        self.assertEqual((D("2.80"), D("2.90"), D("2.85"), "B", D("3.51"), 45, 32, D("0.32")),
                         (leg.bid, leg.ask, leg.mid, leg.grade, leg.spread_pct, leg.dte_calendar, leg.dte_trading,
                          leg.abs_delta))
        self.assertEqual(("0.394108", "0.324755"), ("%.6f" % leg.iv, "%.6f" % leg.delta))
        self.assertEqual((D("0.0597"), D("560.00"), D("2.80")), (best.score, best.credit_total, best.extrinsic_bid))
        self.assertEqual(("135.05", "-64.95", D("19140.00"), D("3360.00"), D("95.70"), None),
                         (money(best.after.group_delta), money(best.after.delta_change), best.after.max_loss,
                          best.after.max_profit, best.after.breakeven, best.after.realized_on_close))
        self.assertEqual([("earnings", "crosses_earnings", "#earnings-ok"), ("ex_dividend", "crosses_ex_dividend", "#assign-ok")],
                         [(a.key, a.code, a.by) for a in best.accepted])
        self.assertEqual(["WMT261218C00112500", "WMT270115C00115000", "WMT261218C00115000", "WMT270115C00120000",
                          "WMT261218C00120000"], [c.id for c in answer.ranking.candidates[:5]])
        self.assertEqual(["ELIGIBLE"] * 5, [c.status for c in answer.ranking.candidates[:5]])
        self.assertEqual({"abs_delta": 15, "strike_floor": 4}, answer.ranking.failure_counts)
        self.assertEqual((200.0, D("19700.00"), None, D("98.50"), True),
                         (answer.before.group_delta, answer.before.max_loss, answer.before.max_profit,
                          answer.before.breakeven, answer.before.unbounded_up))
        self.assertEqual(["strike_below_cost"], codes([r for r in by_id(answer)["WMT261218C00097500"].reasons
                                                       if r.key == "strike_floor"]))

    def test_spy_protective_put(self) -> None:
        answer = self.put
        self.assertEqual((S.VERDICT_BEST, "SPY270319P00610000", 1), (answer.verdict, answer.ranking.best.id,
                                                                    answer.contracts))
        self.assertEqual({"drop_pct": D("20"), "spot": D("518.496")}, answer.stress)
        self.assertEqual({"made": 3, "chains": 1, "per_run_max_requests": 20, "per_run_max_chains": 4,
                          "budget_left": 52}, answer.requests)
        best = answer.ranking.best
        leg = best.legs[0]
        self.assertEqual((D("22.90"), D("23.10"), D("23.00"), "A", D("0.87"), 136, 93),
                         (leg.bid, leg.ask, leg.mid, leg.grade, leg.spread_pct, leg.dte_calendar, leg.dte_trading))
        self.assertEqual(("0.274363", "-0.303113"), ("%.6f" % leg.iv, "%.6f" % leg.delta))
        sigma, delta = solved(leg.occ, "PUT", "648.12", "610", MAR19, "23.00", 0.0115)
        self.assertAlmostEqual(sigma, leg.iv, delta=1e-9)
        self.assertAlmostEqual(delta, leg.delta, delta=1e-9)
        self.assertEqual((D("0.1856"), D("5.88"), D("91.504"), D("9150.40"), D("2310.00")),
                         (best.score, best.floor_pct, best.covered_per_share, best.covered_total, best.cost_total))
        self.assertEqual(("69.69", D("4550.00"), None, True, D("655.50")),
                         (money(best.after.group_delta), best.after.max_loss, best.after.max_profit,
                          best.after.unbounded_up, best.after.breakeven))
        table = [(c.id[-8:-3], c.status, format(c.score, "f")) for c in answer.ranking.candidates]
        self.assertEqual([("00610", "ELIGIBLE", "0.1856"), ("00620", "ELIGIBLE", "0.1858"), ("00625", "ELIGIBLE", "0.1868"),
                          ("00600", "ELIGIBLE", "0.1876"), ("00590", "ELIGIBLE", "0.1928"),
                          ("00630", "OUTSIDE_BANDS", "0.1879"), ("00640", "OUTSIDE_BANDS", "0.1915"),
                          ("00650", "OUTSIDE_BANDS", "0.1968"), ("00580", "OUTSIDE_BANDS", "0.2020"),
                          ("00660", "OUTSIDE_BANDS", "0.2032"), ("00680", "OUTSIDE_BANDS", "0.2190"),
                          ("00700", "OUTSIDE_BANDS", "0.2390")], table)
        self.assertEqual({"by": "stress_cost_per_day", "value": D("0.1858"), "best": D("0.1856")},
                         answer.ranking.candidates[1].lost_because)
        self.assertEqual({"floor_pct": 7}, answer.ranking.failure_counts)
        self.assertEqual([("floor_pct", "floor_outside_band", D("2.80"), [D("3"), D("10")])],
                         [(r.key, r.code, r.value, r.threshold) for r in answer.ranking.candidates[5].reasons])

    def test_spy_collar_with_assign_ok(self) -> None:
        answer = self.collar
        best = answer.ranking.best
        self.assertEqual((S.VERDICT_BEST, "SPY270319P00620000+SPY270319C00680000", "put_strike"),
                         (answer.verdict, best.id, answer.ranking.tie_break))
        put, call = best.legs
        self.assertEqual((D("25.45"), D("25.65"), D("25.55"), "A", D("0.78")), (put.bid, put.ask, put.mid, put.grade,
                                                                               put.spread_pct))
        self.assertEqual((D("26.40"), D("26.60"), D("26.50"), "A", D("0.75")), (call.bid, call.ask, call.mid, call.grade,
                                                                               call.spread_pct))
        self.assertEqual(("-0.334824", "0.423792", "0.233774"), ("%.6f" % put.delta, "%.6f" % call.delta, "%.6f" % call.iv))
        self.assertEqual((D("4.92"), D("-0.75"), D("-75.00"), D("4.34"), D("4.92")),
                         (best.score, best.net_debit, best.net_debit_total, best.floor_pct, best.cap_pct))
        self.assertEqual(("24.14", D("1165.00"), D("4835.00"), D("631.65")),
                         (money(best.after.group_delta), best.after.max_loss, best.after.max_profit, best.after.breakeven))
        self.assertEqual([("ex_dividend", "crosses_ex_dividend", "#assign-ok"), ("ex_dividend", "dividend_unknown", "#assign-ok")],
                         [(a.key, a.code, a.by) for a in best.accepted])
        self.assertEqual({"ex_date": datetime.date(2026, 12, 18), "cum_day": datetime.date(2026, 12, 17),
                          "amount": D("1.85"), "status": "estimated"}, best.accepted[0].value)
        pairs = [("P%s+C%s" % (format(c.legs[0].strike, "f"), format(c.legs[1].strike, "f")), c.status,
                  format(c.net_debit, "f")) for c in answer.ranking.candidates]
        self.assertEqual([("P620+C680", "ELIGIBLE", "-0.75"), ("P610+C680", "ELIGIBLE", "-3.30"),
                          ("P600+C680", "ELIGIBLE", "-5.60"), ("P590+C680", "ELIGIBLE", "-7.65"),
                          ("P625+C700", "OUTSIDE_BANDS", "9.50"), ("P620+C700", "OUTSIDE_BANDS", "8.10"),
                          ("P610+C700", "OUTSIDE_BANDS", "5.55"), ("P600+C700", "OUTSIDE_BANDS", "3.25"),
                          ("P590+C700", "OUTSIDE_BANDS", "1.20"), ("P625+C680", "OUTSIDE_BANDS", "0.65")], pairs)
        self.assertEqual({"puts": 7, "calls": 10}, answer.outside_bands)
        self.assertEqual({"max_net_debit": 6}, answer.ranking.failure_counts)
        self.assertEqual((4, 6), (answer.ranking.passed, answer.ranking.failed))
        self.assertEqual({"by": "put_strike", "value": D("610"), "best": D("620")},
                         answer.ranking.candidates[1].lost_because)

    def test_spy_collar_without_the_tag_none_qualifies(self) -> None:
        answer = self.collar_plain
        self.assertEqual((S.VERDICT_NONE_QUALIFIES, None), (answer.verdict, answer.ranking.best))
        top = answer.ranking.candidates[0]
        self.assertEqual(("SPY270319P00620000+SPY270319C00680000", "FAILS_CRITERION",
                          ["crosses_ex_dividend", "dividend_unknown"]), (top.id, top.status, codes(top.reasons)))
        self.assertEqual({"max_net_debit": 6, "ex_dividend": 10}, answer.ranking.failure_counts)


class ReplacementGoldenTest(unittest.TestCase):
    """The one named replacement of an exit_ticket ROLL, through the same code path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.wmt = S.run_replacement(Desk(cls).market(), "AG-20261020-01", JAN15, D("115"), CRITERIA)
        cls.nvda = S.run_replacement(Desk(cls).market(), "AG-20261022-02", JAN15, D("160"), CRITERIA)
        cls.spy = S.run_replacement(Desk(cls).market(), "AG-20261028-03", MAR19, D("610"), CRITERIA)

    def test_wmt_replacement_equals_the_roll_analysis_best(self) -> None:
        answer = self.wmt
        candidate = answer.candidate
        self.assertEqual((S.VERDICT_PRICED, S.KIND_REPLACEMENT_CALL, "WMT270115C00115000", "ELIGIBLE"),
                         (answer.verdict, answer.kind, candidate.id, candidate.status))
        self.assertEqual((D("0.0467"), D("0.05"), D("10.00")), (candidate.score, candidate.net_credit,
                                                               candidate.net_credit_total))
        self.assertEqual((D("19006.00"), D("3994.00"), D("95.03")), (candidate.after.max_loss, candidate.after.max_profit,
                                                                    candidate.after.breakeven))
        self.assertEqual({"made": 2, "chains": 1, "per_run_max_requests": 20, "per_run_max_chains": 4,
                          "budget_left": 53}, answer.requests)
        self.assertEqual(D("3.50"), answer.held.close_price)

    def test_nvda_replacement_fails_credit_only_and_ex_dividend(self) -> None:
        candidate = self.nvda.candidate
        self.assertEqual(("FAILS_CRITERION", ["debit_roll", "crosses_ex_dividend"]),
                         (candidate.status, codes(candidate.reasons)))
        self.assertEqual((D("-0.05"), D("-10.00")), (candidate.net_credit, candidate.net_credit_total))

    def test_spy_long_put_replacement(self) -> None:
        answer = self.spy
        candidate = answer.candidate
        self.assertEqual((S.KIND_REPLACEMENT_PUT, "SPY270319P00610000", "ELIGIBLE", D("0.1856")),
                         (answer.kind, candidate.id, candidate.status, candidate.score))
        held = answer.held
        self.assertEqual(("SPY261218P00620000", 8, "long", 1, D("9.80"), "bid", D("-150.00")),
                         (held.occ, held.row_number, held.side, held.contracts, held.close_price, held.close_side,
                          held.realized))
        self.assertEqual((D("4700.00"), None, True, D("657.00"), "69.69"),
                         (candidate.after.max_loss, candidate.after.max_profit, candidate.after.unbounded_up,
                          candidate.after.breakeven, money(candidate.after.group_delta)))
        self.assertEqual((D("2370.00"), D("643.70")), (answer.before.max_loss, answer.before.breakeven))
        self.assertEqual(D("2310.00"), candidate.cost_total)

    def test_a_contract_the_chain_does_not_list(self) -> None:
        answer = S.run_replacement(Desk(self).market(), "AG-20261020-01", JAN15, D("117"), CRITERIA)
        self.assertEqual((S.VERDICT_UNAVAILABLE, S.CAUSE_REPLACEMENT_NOT_LISTED, 2),
                         (answer.verdict, answer.cause, answer.requests["made"]))
        with self.assertRaises(ValueError):
            S.run_replacement(Desk(self).market(), "AG-20261020-01", datetime.date(2026, 11, 20), D("110"), CRITERIA)

    def test_a_ticket_needs_a_live_session(self) -> None:
        after = datetime.datetime(2026, 11, 3, 21, 25, tzinfo=UTC)
        answer = S.run_replacement(Desk(self).market("after_close", after), "AG-20261020-01", JAN15, D("115"), CRITERIA)
        self.assertEqual((S.VERDICT_UNAVAILABLE, S.CAUSE_MARKET_CLOSED, 0),
                         (answer.verdict, answer.cause, answer.requests["made"]))
        self.assertTrue(S.ticket_session_live(market_clock.session_at(NOW)))
        self.assertFalse(S.ticket_session_live(market_clock.session_at(after)))

    def test_valid_until(self) -> None:
        self.assertEqual((datetime.datetime(2026, 11, 3, 20, 20, tzinfo=UTC), True),
                         S.valid_until(NOW, market_clock.session_at(NOW)))
        late = datetime.datetime(2026, 11, 3, 20, 45, tzinfo=UTC)
        self.assertEqual((datetime.datetime(2026, 11, 3, 21, 0, tzinfo=UTC), False),
                         S.valid_until(late, market_clock.session_at(late)))


class UnavailableTest(unittest.TestCase):
    def test_stale_quotes_stop_after_the_quotes_batch(self) -> None:
        answer = S.run_roll_analysis(Desk(self).market("stale"), "AG-20261020-01", CRITERIA)
        self.assertEqual((S.VERDICT_UNAVAILABLE, S.CAUSE_SPOT_UNUSABLE, 1),
                         (answer.verdict, answer.cause, answer.requests["made"]))

    def test_a_holiday_makes_no_request(self) -> None:
        holiday = datetime.datetime(2026, 11, 26, 17, 0, tzinfo=UTC)
        answer = S.run_roll_analysis(Desk(self).market("holiday", holiday), "AG-20261020-01", CRITERIA)
        self.assertEqual((S.VERDICT_UNAVAILABLE, S.CAUSE_MARKET_CLOSED, 0),
                         (answer.verdict, answer.cause, answer.requests["made"]))

    def test_a_chain_not_served_names_no_best(self) -> None:
        after = datetime.datetime(2026, 11, 3, 21, 25, tzinfo=UTC)
        answer = S.run_roll_analysis(Desk(self).market("after_close", after), "AG-20261020-01", CRITERIA)
        self.assertEqual((S.VERDICT_UNAVAILABLE, S.CAUSE_CHAIN_NOT_SERVED, 3),
                         (answer.verdict, answer.cause, answer.requests["made"]))
        self.assertEqual([DEC18, JAN15], answer.expirations.listed_in_window)
        self.assertEqual([], answer.expirations.evaluated)
        self.assertEqual([DEC18, JAN15], [entry["expiration"] for entry in answer.expirations.not_evaluated])
        self.assertTrue(answer.expirations.not_evaluated[0]["reason"].startswith("fetch_failed:"))
        self.assertEqual(S.NOT_REQUESTED, answer.expirations.not_evaluated[1]["reason"])
        self.assertIsNone(answer.ranking)

    def test_a_held_call_without_a_fresh_ask(self) -> None:
        desk = Desk(self)
        path = os.path.join(desk.root, "fixtures", "tradier", "regular", "quotes__WMT,WMT261120C00105000.json")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        stale = '"ask_date": %d' % ms(STAMP - datetime.timedelta(minutes=40))
        marker = '"ask": 3.5,'
        self.assertEqual(1, text.count(marker))
        head, tail = text.split(marker)
        tail = tail.replace('"ask_date": %d' % ms(STAMP), stale, 1)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(head + marker + tail)
        answer = S.run_roll_analysis(desk.market(), "AG-20261020-01", CRITERIA)
        self.assertEqual((S.VERDICT_UNAVAILABLE, S.CAUSE_HELD_LEG_UNUSABLE, 1),
                         (answer.verdict, answer.cause, answer.requests["made"]))

    def test_a_quarantined_group_and_an_unknown_trade(self) -> None:
        desk = Desk(self, replace=("WMT261120C00105000", "WMT261120C0010500X"))
        answer = S.run_roll_analysis(desk.market(), "AG-20261020-01", CRITERIA)
        self.assertEqual((S.VERDICT_UNAVAILABLE, S.CAUSE_GROUP_QUARANTINED, 0),
                         (answer.verdict, answer.cause, answer.requests["made"]))
        with self.assertRaises(S.StructuringError) as caught:
            S.run_roll_analysis(Desk(self).market(), "AG-20991231-01", CRITERIA)
        self.assertEqual(S.ERROR_UNKNOWN_TRADE, caught.exception.code)

    def test_not_applicable_makes_no_request(self) -> None:
        answer = S.run_roll_analysis(Desk(self).market(), "AG-20261028-03", CRITERIA)
        self.assertEqual((S.VERDICT_NOT_APPLICABLE, S.NA_STRUCTURE, 0),
                         (answer.verdict, answer.cause, answer.requests["made"]))
        self.assertIn("protective put", answer.reason)


class EdgeAnswerTest(unittest.TestCase):
    def test_a_short_call_on_two_rows_is_not_applicable(self) -> None:
        lots = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,"
                "covered call #assign-ok",
                "option,WMT,WMT261120C00105000,-1,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-342.00,,3.42,open,,,,,"
                "covered call #assign-ok\noption,WMT,WMT261120C00105000,-1,,,,Brokerage (margin),AG-20261020-01,"
                "2026-10-21,-342.00,,3.42,open,,,,,covered call #assign-ok")
        answer = S.run_roll_analysis(Desk(self, replace=lots).market(), "AG-20261020-01", CRITERIA)
        self.assertEqual((S.VERDICT_NOT_APPLICABLE, S.NA_STRUCTURE, 0), (answer.verdict, answer.cause,
                                                                        answer.requests["made"]))
        self.assertIn("more than one row", answer.reason)

    def test_no_free_shares(self) -> None:
        # the 200 WMT shares of AG-20261020-01 stand alone, but two short WMT calls of another group in the same
        # account already rely on them: a covered call or a collar on them would be uncovered
        moved = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,",
                 "option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-09,")
        desk = Desk(self, replace=moved)
        for structure in ("covered_call", "collar"):
            answer = S.run_proposal(desk.market(), "AG-20261020-01", structure, CRITERIA)
            with self.subTest(structure=structure):
                self.assertEqual((S.VERDICT_NOTHING_TO_PROPOSE, S.NOTHING_NO_FREE_SHARES, 0),
                                 (answer.verdict, answer.cause, answer.requests["made"]))
                self.assertEqual("no free shares: 200 WMT shares are covered by 2 short calls; the desk proposes no %s"
                                 % ("covered call" if structure == "covered_call" else "collar"), answer.reason)
        put = S.run_proposal(desk.market(), "AG-20261020-01", "protective_put", CRITERIA)
        self.assertNotEqual(S.VERDICT_NOTHING_TO_PROPOSE, put.verdict)


class ExpiryDayTest(unittest.TestCase):
    """The expiry_day set: the held call's quote is wide (0.12/0.16, grade E) and its conservative ask stands."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.plain = S.run_roll_analysis(Desk(cls).market("expiry_day", EXPIRY_DAY_NOW), "AG-20261020-01", CRITERIA)
        tagged = Desk(cls, replace=("covered call #assign-ok", "covered call #assign-ok #earnings-ok"))
        cls.tagged = S.run_roll_analysis(tagged.market("expiry_day", EXPIRY_DAY_NOW), "AG-20261020-01", CRITERIA)

    def test_the_conservative_side_of_a_grade_e_held_call(self) -> None:
        held = self.plain.held
        self.assertEqual(("E", D("0.12"), D("0.16"), D("0.16"), S.GRADE_E_NOTE, D("652.00")),
                         (held.grade, held.bid, held.ask, held.close_price, held.note, held.realized))
        self.assertIsNone(self.plain.before.group_delta)
        self.assertEqual([JAN15], self.plain.expirations.evaluated)
        self.assertEqual({"made": 3, "chains": 1, "per_run_max_requests": 20, "per_run_max_chains": 4,
                          "budget_left": 52}, self.plain.requests)

    def test_a_passed_earnings_date_is_unknown(self) -> None:
        answer = self.plain
        self.assertEqual(S.VERDICT_NONE_QUALIFIES, answer.verdict)
        top = answer.ranking.candidates[0]
        self.assertEqual(("WMT270115C00115000", ["earnings_unknown"]), (top.id, codes(top.reasons)))
        self.assertEqual(["crosses_ex_dividend"], [a.code for a in top.accepted])
        self.assertIsNone(top.after.delta_change)

    def test_with_earnings_ok_the_january_115_call_is_named(self) -> None:
        best = self.tagged.ranking.best
        self.assertEqual(("WMT270115C00115000", D("0.0502"), D("2.79"), D("558.00"), D("0.31")),
                         (best.id, best.score, best.net_credit, best.net_credit_total, best.legs[0].abs_delta))
        self.assertEqual((D("18458.00"), D("4542.00"), D("92.29")), (best.after.max_loss, best.after.max_profit,
                                                                    best.after.breakeven))


class ApprovalOrderTest(unittest.TestCase):
    """The S7 leg orders of every candidate kind, checked with F1's approval module against the holdings."""

    @classmethod
    def setUpClass(cls) -> None:
        main = Desk(cls)
        cls.roll = S.run_roll_analysis(main.market(), "AG-20261020-01", CRITERIA)
        cls.put_roll = S.run_replacement(main.market(), "AG-20261028-03", MAR19, D("610"), CRITERIA)
        wmt, tagged = Desk(cls, drop_row=4, replace=WMT_TAGS), Desk(cls, drop_row=8, replace=SPY_TAG)
        cls.call = S.run_proposal(wmt.market(), "AG-20261020-01", "covered_call", CRITERIA)
        cls.put = S.run_proposal(tagged.market(), "AG-20261028-03", "protective_put", CRITERIA)
        cls.collar = S.run_proposal(tagged.market(), "AG-20261028-03", "collar", CRITERIA)

    def test_leg_orders(self) -> None:
        from aegis_desk import approval
        L = approval.Leg
        cases = [
            (self.roll, [L("close", "short", "call", 2), L("open", "short", "call", 2)], approval.Holdings(200, short_calls=2), 1),
            (self.put_roll, [L("open", "long", "put", 1), L("close", "long", "put", 1)], approval.Holdings(100, long_puts=1), 2),
            (self.call, [L("open", "short", "call", 2)], approval.Holdings(200), 1),
            (self.put, [L("open", "long", "put", 1)], approval.Holdings(100), 2),
            (self.collar, [L("open", "long", "put", 1), L("open", "short", "call", 1)], approval.Holdings(100), 2),
        ]
        for answer, legs, holdings, level in cases:
            candidate = answer.candidate or answer.ranking.best
            with self.subTest(kind=answer.kind):
                self.assertEqual(legs, S.approval_legs(answer, candidate))
                self.assertEqual(holdings, S.approval_state(answer))
                result = S.approval_check(answer, candidate, 2)
                self.assertEqual((True, level, None), (result.ok, result.required_level, result.exposure_step))
        refused = S.approval_check(self.collar, self.collar.ranking.best, 1)
        self.assertEqual((False, 2), (refused.ok, refused.required_level))


class PayloadTest(unittest.TestCase):
    """``Answer.as_dict`` is the payload body: exactly ``ANSWER_KEYS``, serialisable with the desk's JSON encoder."""

    @classmethod
    def setUpClass(cls) -> None:
        main, tagged = Desk(cls), Desk(cls, drop_row=8, replace=SPY_TAG)
        cls.answers = [S.run_roll_analysis(main.market(), "AG-20261020-01", CRITERIA),
                       S.run_roll_analysis(main.market(), "AG-20261028-03", CRITERIA),
                       S.run_proposal(tagged.market(), "AG-20261028-03", "collar", CRITERIA),
                       S.run_replacement(main.market(), "AG-20261020-01", JAN15, D("115"), CRITERIA),
                       S.run_roll_analysis(Desk(cls).market("stale"), "AG-20261020-01", CRITERIA)]

    def test_payload_keys_and_json(self) -> None:
        from aegis_desk import state
        for answer in self.answers:
            payload = answer.as_dict()
            with self.subTest(tool=answer.tool, verdict=answer.verdict):
                self.assertEqual(S.ANSWER_KEYS, tuple(payload))
                text = state.json_dumps(payload)
                from aegis_desk import alert_lint
                self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(text)])
                decoded = __import__("json").loads(text)
                self.assertEqual(answer.verdict, decoded["verdict"])
                self.assertEqual(CRITERIA.sha256, decoded["criteria"]["sha256"])
        roll = self.answers[0].as_dict()
        self.assertEqual(("WMT270115C00115000", "Brokerage (margin)", "margin", "roll", "covered_call"),
                         (roll["best"], roll["account"], roll["account_type"], roll["section"], roll["structure"]))
        self.assertEqual(S.CANDIDATE_KEYS, tuple(roll["candidates"][0]))
        self.assertEqual(S.LEG_KEYS, tuple(roll["candidates"][0]["legs"][0]))
        self.assertEqual(S.HELD_KEYS, tuple(roll["held"]))
        self.assertEqual(D("19006.00"), roll["after"]["max_loss"])
        self.assertEqual({"symbol": "WMT", "bid": D("104.20"), "ask": D("104.26"), "mid": D("104.23"), "grade": "A",
                          "as_of": STAMP}, roll["spot"])
        self.assertEqual(S.MODEL_NOTE, roll["model_note"])
        replacement = self.answers[3].as_dict()
        self.assertEqual(("PRICED", None, D("3994.00")), (replacement["verdict"], replacement["best"],
                                                          replacement["after"]["max_profit"]))
        self.assertEqual("WMT270115C00115000", replacement["candidates"][0]["id"])
        stale = self.answers[4].as_dict()
        self.assertEqual(("UNAVAILABLE", "spot_unusable", [], None), (stale["verdict"], stale["cause"],
                                                                     stale["candidates"], stale["after"]))


# ------------------------------------------------------------------------------------------------
# The after-state identity: closed forms == payoff on in-memory post-fill rows + realized (S6: never written)
# ------------------------------------------------------------------------------------------------


def post_fill_text(positions_text: str, answer: "S.Answer", candidate: "S.Candidate") -> str:
    """The positions.csv text after the candidate's fills at the conservative prices (an in-memory oracle only)."""
    lines = positions_text.split("\n")
    today = answer.pricing.today.isoformat()
    if answer.held is not None:
        index = answer.held.row_number - 1
        fields = next(csv.reader([lines[index]]))
        fields[13:17] = ["closed", today, str(answer.held.contracts), format(answer.held.close_price, "f")]
        out = io.StringIO()
        csv.writer(out, lineterminator="").writerow(fields)
        lines[index] = out.getvalue()
    group = answer.group_context
    for leg in candidate.legs:
        short = leg.right == "CALL"
        price = leg.bid if short else leg.ask
        quantity = -candidate.contracts if short else candidate.contracts
        basis = price * 100 * quantity
        row = ["option", leg.root, leg.occ, str(quantity), "", "", "", group.account, group.trade_id, today,
               format(basis.quantize(D("0.01")), "f"), "", "", "open", "", "", "", "", ""]
        out = io.StringIO()
        csv.writer(out, lineterminator="").writerow(row)
        lines.insert(len(lines) - 1 if lines[-1] == "" else len(lines), out.getvalue())
    return "\n".join(lines)


def oracle_profile(positions_text: str, answer: "S.Answer", candidate: "S.Candidate"):
    text = post_fill_text(positions_text, answer, candidate)
    parsed = positions_csv.parse_positions_text(text, today=answer.pricing.today)
    assert not parsed.file_errors and not parsed.quarantined, (parsed.file_errors, parsed.quarantined)
    group = [g for g in grouping.group_positions(parsed.rows) if g.trade_id == answer.group_context.trade_id][0]
    realized = D(0)
    if answer.held is not None:
        closed = [row for row in group.legs if row.row_number == answer.held.row_number]
        realized = pnl.realized_for_row(closed[0])
        assert realized == answer.held.realized, (realized, answer.held.realized)
    return group, payoff.payoff_profile(group), payoff.grid_profile(group.open_legs, group.net_cost - realized), realized


class AfterStateIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        main = Desk(cls)
        wmt, spy, tagged = Desk(cls, drop_row=4, replace=WMT_TAGS), Desk(cls, drop_row=8), Desk(cls, drop_row=8, replace=SPY_TAG)
        cls.cases = [
            (main, S.run_roll_analysis(main.market(), "AG-20261020-01", CRITERIA), "covered_call"),
            (main, S.run_roll_analysis(main.market(), "AG-20261022-02", CRITERIA), "covered_call"),
            (wmt, S.run_proposal(wmt.market(), "AG-20261020-01", "covered_call", CRITERIA), "covered_call"),
            (spy, S.run_proposal(spy.market(), "AG-20261028-03", "protective_put", CRITERIA), "protective_put"),
            (tagged, S.run_proposal(tagged.market(), "AG-20261028-03", "collar", CRITERIA), "collar"),
            (main, S.run_replacement(main.market(), "AG-20261028-03", MAR19, D("590"), CRITERIA), "protective_put"),
            (main, S.run_roll_analysis(main.market("expiry_day", EXPIRY_DAY_NOW), "AG-20261020-01", CRITERIA),
             "covered_call"),
        ]
        odd = Desk(cls, drop_row=4, replace=("equity,WMT,,200,", "equity,WMT,,250,"))
        cls.cases.append((odd, S.run_proposal(odd.market(), "AG-20261020-01", "covered_call", CRITERIA), "covered_call"))
        odd_put = Desk(cls, drop_row=8, replace=("etf,SPY,,100,,,,Roth IRA,AG-20261028-03,2026-10-28,63240.00",
                                                 "etf,SPY,,150,,,,Roth IRA,AG-20261028-03,2026-10-28,94860.00"))
        cls.cases.append((odd_put, S.run_proposal(odd_put.market(), "AG-20261028-03", "protective_put", CRITERIA),
                          "protective_put"))

    def test_every_priced_candidate(self) -> None:
        checked = 0
        for desk, answer, structure in self.cases:
            text = desk.positions_text
            for candidate in answer.ranking.candidates:
                if candidate.after is None:
                    self.assertEqual("UNPRICED", candidate.status)
                    continue
                group, open_profile, economic, realized = oracle_profile(text, answer, candidate)
                with self.subTest(candidate=candidate.id):
                    self.assertEqual(structure, group.structure)
                    self.assertEqual(economic.max_loss, candidate.after.max_loss)
                    self.assertEqual(economic.max_profit, candidate.after.max_profit)
                    self.assertEqual(tuple(economic.breakevens), candidate.after.breakevens)
                    self.assertEqual(economic.unbounded_up, candidate.after.unbounded_up)
                    self.assertEqual(realized if answer.held is not None else None, candidate.after.realized_on_close)
                    # designer Q's reading: the economic max loss and profit are the open legs' less / plus realized
                    self.assertEqual(open_profile.max_loss - realized, candidate.after.max_loss)
                    if open_profile.max_profit is not None:
                        self.assertEqual(open_profile.max_profit + realized, candidate.after.max_profit)
                checked += 1
        self.assertGreater(checked, 100)

    def test_uncovered_remainder_shares(self) -> None:
        odd = self.cases[-2][1]
        self.assertEqual(2, odd.contracts)
        self.assertIsNone(odd.ranking.candidates[0].after.max_profit)
        self.assertTrue(odd.ranking.candidates[0].after.unbounded_up)

    def test_roll_closed_forms_of_designer_q(self) -> None:
        answer = self.cases[0][1]
        best = answer.ranking.best
        n, s0 = answer.group_context.shares, answer.group_context.s0
        kept = -answer.held.remaining_cost_basis - answer.held.close_price * 200 + best.legs[0].bid * 200
        self.assertEqual(D("694.00"), kept)
        self.assertEqual(answer.before.max_loss - best.net_credit_total, best.after.max_loss)
        self.assertEqual(n * (best.legs[0].strike - s0) + kept, best.after.max_profit)
        self.assertEqual(payoff.quantize_price(s0 - kept / n), best.after.breakeven)


# ------------------------------------------------------------------------------------------------
# Seeded property tests
# ------------------------------------------------------------------------------------------------


class PropertyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        main, tagged = Desk(cls), Desk(cls, drop_row=8, replace=SPY_TAG)
        wmt = Desk(cls, drop_row=4, replace=WMT_TAGS)
        cls.answers = [S.run_roll_analysis(main.market(), "AG-20261020-01", CRITERIA),
                       S.run_roll_analysis(main.market(), "AG-20261022-02", CRITERIA),
                       S.run_proposal(wmt.market(), "AG-20261020-01", "covered_call", CRITERIA),
                       S.run_proposal(tagged.market(), "AG-20261028-03", "protective_put", CRITERIA),
                       S.run_proposal(tagged.market(), "AG-20261028-03", "collar", CRITERIA)]

    def rerun(self, answer: "S.Answer", chains: Dict[datetime.date, List[Quote]]) -> "S.Ranking":
        built = S.candidates_for(answer.kind, chains, answer.pricing, answer.group_context, answer.section,
                                 answer.events, held=answer.held)
        return S.rank_candidates(built.candidates, answer.kind)

    def test_the_pure_rerun_reproduces_the_answer(self) -> None:
        for answer in self.answers:
            ranking = self.rerun(answer, answer.chains)
            self.assertEqual([c.id for c in answer.ranking.candidates], [c.id for c in ranking.candidates])

    def test_permuting_chain_entries_changes_nothing(self) -> None:
        for answer in self.answers:
            expected = [(c.id, c.rank, c.status, c.score) for c in answer.ranking.candidates]
            best = None if answer.ranking.best is None else answer.ranking.best.id
            for seed in range(5):
                rng = random.Random(seed)
                chains = {}
                for expiration in sorted(answer.chains, key=lambda _: rng.random()):
                    options = list(answer.chains[expiration])
                    rng.shuffle(options)
                    chains[expiration] = options
                ranking = self.rerun(answer, chains)
                with self.subTest(kind=answer.kind, seed=seed):
                    self.assertEqual(expected, [(c.id, c.rank, c.status, c.score) for c in ranking.candidates])
                    self.assertEqual(best, None if ranking.best is None else ranking.best.id)

    def test_adding_a_failing_or_unpriced_candidate_never_changes_the_best(self) -> None:
        rng = random.Random(11)
        for answer in self.answers:
            if answer.ranking.best is None:
                continue
            expiration = sorted(answer.chains)[0]
            root = answer.group_context.underlying
            spot = answer.pricing.spot.mark_mid
            extras = [
                option(root, expiration, "call", format((spot * D("1.6")).quantize(D("1")), "f"), "0", "0.05"),
                option(root, expiration, "put", format((spot * D("0.4")).quantize(D("1")), "f"), "0.20", "0.35"),
                option(root, expiration, "call", format((spot * D("0.6")).quantize(D("1")), "f"), "1.00", "1.10"),
                option(root, expiration, "put", format((spot * D("1.4")).quantize(D("1")), "f"), "1.00", "9.00"),
            ]
            for extra in extras:
                chains = {key: list(value) for key, value in answer.chains.items()}
                chains[expiration].insert(rng.randrange(len(chains[expiration]) + 1), extra)
                ranking = self.rerun(answer, chains)
                with self.subTest(kind=answer.kind, extra=extra.symbol):
                    self.assertEqual(answer.ranking.best.id, ranking.best.id)

    def test_raising_a_bid_never_lowers_the_score(self) -> None:
        rng = random.Random(7)
        checked = 0
        for answer in self.answers:
            if answer.kind not in (S.KIND_COVERED_CALL, S.KIND_REPLACEMENT_CALL):
                continue
            for candidate in answer.ranking.candidates:
                if candidate.score is None:
                    continue
                leg = candidate.legs[0]
                for _ in range(3):
                    bump = D(rng.randrange(1, 40)) / 100
                    q = option(leg.root, leg.expiration, "call", format(leg.strike, "f"), format(leg.bid + bump, "f"),
                               format(max(leg.ask, leg.bid + bump + D("0.05")), "f"))
                    priced = S.price_contract(q, answer.pricing)
                    if answer.kind == S.KIND_COVERED_CALL:
                        again = S.covered_call_candidate(priced, answer.pricing, answer.group_context, answer.section,
                                                         answer.events)
                    else:
                        again = S.replacement_call_candidate(priced, answer.pricing, answer.group_context, answer.held,
                                                             answer.section, answer.events)
                    if again.score is not None:
                        self.assertGreaterEqual(again.score, candidate.score, (leg.occ, bump))
                        checked += 1
        self.assertGreater(checked, 50)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
