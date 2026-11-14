"""The objectives added after release 1 (``net_extrinsic_per_added_day``, ``band_centre``) and the quote-precision note.

Pure formulas are pinned on the fixture's own numbers (WMT 2026-11-03 19:31:12Z, SPY 2027-03-19 chain); the fixture runs
reuse the copies and helpers of ``test_structuring`` and change only the ``objective`` (and, where named,
``credit_only``) of the fixture criteria.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import random
import unittest
from decimal import Decimal

from aegis_desk import structuring, structuring_config
from aegis_desk import structuring_text as T
from tests.engine.test_structuring import CRITERIA, SPY_TAG, WMT_TAGS, Desk, by_id, leg_of, made

S = structuring
D = Decimal


def criteria(section: str, **changes: object) -> structuring_config.StructuringConfig:
    return dataclasses.replace(CRITERIA, **{section: dataclasses.replace(CRITERIA.section(section), **changes)})


class NetExtrinsicTest(unittest.TestCase):
    def test_out_of_the_money_roll(self) -> None:
        # held 2026-11-20 105 call closed at its ask 3.50 (17 days), new 2027-01-15 115 call opened at its bid 3.55 (73)
        self.assertEqual(D("0.000857"), S.net_extrinsic_per_added_day(D("3.55"), D("3.50"), D("104.23"), D("115"),
                                                                      D("105"), 73, 17))

    def test_the_quantum_separates_small_rolls_on_a_high_priced_stock(self) -> None:
        # SPY at 648.12, 28 days added: rolls adding 0.002 and 0.008 a share of time value both read 0.0000 at a 0.0001
        # quantum; at 0.000001 they read 0.000011 and 0.000044
        low = S.net_extrinsic_per_added_day(D("10.002"), D("10.00"), D("648.12"), D("700"), D("700"), 73, 45)
        high = S.net_extrinsic_per_added_day(D("10.008"), D("10.00"), D("648.12"), D("700"), D("700"), 73, 45)
        self.assertEqual((D("0.000011"), D("0.000044")), (low, high))

    def test_a_roll_that_gives_up_time_value_is_charged_the_whole_amount(self) -> None:
        # Dec 112.5 gives up 0.70 a share, Jan 120 gives up 1.25: not divided by the 28 or 56 days added, so the smaller
        # loss ranks first whatever the tenor (dividing by days would rank Jan 120 first: -0.0214 against -0.0240)
        dec = S.net_extrinsic_per_added_day(D("2.80"), D("3.50"), D("104.23"), D("112.5"), D("105"), 45, 17)
        jan = S.net_extrinsic_per_added_day(D("2.25"), D("3.50"), D("104.23"), D("120"), D("105"), 73, 17)
        self.assertEqual((D("-0.671592"), D("-1.199271")), (dec, jan))
        self.assertGreater(dec, jan)

    def test_continuous_at_zero_and_never_decreasing_in_the_new_bid(self) -> None:
        args = (D("104.23"), D("115"), D("105"), 73, 17)
        scores = [S.net_extrinsic_per_added_day(D("3.40") + D("0.01") * i, D("3.50"), *args) for i in range(21)]
        self.assertEqual(scores, sorted(scores))
        self.assertEqual(D("0.000000"), S.net_extrinsic_per_added_day(D("3.50"), D("3.50"), *args))
        self.assertEqual(D("-0.009594"), S.net_extrinsic_per_added_day(D("3.49"), D("3.50"), *args))
        self.assertEqual(D("0.000171"), S.net_extrinsic_per_added_day(D("3.51"), D("3.50"), *args))

    def test_in_the_money_held_call_is_measured_on_time_value_not_cash(self) -> None:
        # held 100 call at its ask 6.35 holds 4.23 of intrinsic value: the roll pays a net debit of 2.80 per share yet
        # sells 1.43 more time value than it buys back
        self.assertEqual(D("0.024499"), S.net_extrinsic_per_added_day(D("3.55"), D("6.35"), D("104.23"), D("115"),
                                                                      D("100"), 73, 17))

    def test_needs_a_later_expiration(self) -> None:
        with self.assertRaises(ValueError):
            S.net_extrinsic_per_added_day(D("1"), D("1"), D("100"), D("110"), D("105"), 17, 17)


class BandFitTest(unittest.TestCase):
    def test_fixture_calls(self) -> None:
        bands = ((40, 75), (D("0.15"), D("0.35")))
        def fit(days: int, delta: str) -> Decimal:
            return S.band_fit([(days, bands[0]), (D(delta), bands[1])])
        self.assertEqual(D("0.91"), fit(45, "0.27"))   # 2026-12-18 115
        self.assertEqual(D("1.41"), fit(45, "0.32"))   # 2026-12-18 112.5
        self.assertEqual(D("0.99"), fit(73, "0.24"))   # 2027-01-15 120
        self.assertEqual(D("0.00"), S.band_fit([(D("57.5"), bands[0]), (D("0.25"), bands[1])]))
        self.assertEqual(D("2.00"), fit(75, "0.35"))   # both band edges

    def test_needs_a_band(self) -> None:
        with self.assertRaises(ValueError):
            S.band_fit([(1, (2, 2))])

    def test_quote_term_counts_each_leg_in_your_spread_limit(self) -> None:
        legs = [dataclasses.replace(leg_of("115"), spread_pct=D("4.65")),
                dataclasses.replace(leg_of("120"), spread_pct=D("25"))]
        self.assertEqual(D("1.186"), S.quote_term(legs, D("25")))       # 4.65 / 25 + 25 / 25: a leg at the limit adds 1
        self.assertEqual(D("1.10"), S.band_fit([(45, (40, 75)), (D("0.27"), (D("0.15"), D("0.35")))],
                                               S.quote_term(legs[:1], D("25"))))   # Dec 115: 0.91 + 0.186
        self.assertIsNone(S.quote_term([leg_of("115")], D("25")))       # no spread read: no score


class OwnerCentresTest(unittest.TestCase):
    """Step 3b: under schema_version = 2 the owner may set a band's centre and each band's (and the quote's) weight."""

    @classmethod
    def setUpClass(cls) -> None:
        def run(**changes: object) -> "S.Answer":
            return S.run_proposal(Desk(cls, drop_row=4, replace=WMT_TAGS).market(), "AG-20261020-01", "covered_call",
                                  criteria("covered_call", objective="band_centre", **changes))
        cls.half_days = run(band_weights=(("dte", D("0.5")),))
        cls.seven_tenths = run(band_weights=(("dte", D("0.7")),))
        cls.centre_30 = run(centres=(("abs_delta", D("0.30")),))

    def test_band_fit_takes_a_centre_and_a_weight(self) -> None:
        self.assertEqual(D("0.14"), S.band_fit([(45, (40, 75), D("50"), D("0.5"))]))   # 0.5 x 5 / 17.5
        self.assertEqual(D("0.91"), S.band_fit([(45, (40, 75), None, D("1")), (D("0.27"), (D("0.15"), D("0.35")))]))
        self.assertEqual(S.band_fit([(45, (40, 75))]), S.band_fit([(45, (40, 75), D("57.5"), D("1"))]))

    def test_the_days_weight_flip_point_on_wmt(self) -> None:
        # the report's sensitivity: at a days weight of 0.5, Jan 120 (0.443 + 0.10 + 0.174) beats Dec 115
        # (0.357 + 0.20 + 0.186); at 0.7 Dec 115 is still best (the flip is near 0.65)
        self.assertEqual(["WMT270115C00120000", "WMT261218C00115000"],
                         [c.id for c in self.half_days.ranking.candidates[:2]])
        self.assertEqual([D("0.72"), D("0.74")], [c.score for c in self.half_days.ranking.candidates[:2]])
        self.assertEqual("WMT261218C00115000", self.seven_tenths.ranking.best.id)

    def test_an_owner_centre_moves_the_answer(self) -> None:
        # abs_delta centre 0.30: Dec 112.5 (0.71 + 0.20 + 0.14) is now nearest
        self.assertEqual(("WMT261218C00112500", D("1.05")), (self.centre_30.ranking.best.id,
                                                             self.centre_30.ranking.best.score))

    def test_the_text_names_the_owner_settings(self) -> None:
        w = T.Writer()
        self.assertEqual("band_centre with your own settings in structuring.toml: weight dte 0.5",
                         T.centre_settings_line(w, self.half_days.section))
        self.assertEqual("band_centre with your own settings in structuring.toml: centre abs_delta 0.30",
                         T.centre_settings_line(T.Writer(), self.centre_30.section))
        self.assertIsNone(T.centre_settings_line(T.Writer(), CRITERIA.covered_call))
        w = T.Writer()
        rendered = T.finish(w, T.proposal_lines(w, self.half_days, owner_level=2), T.data_as_of(self.half_days),
                            self.half_days.tool)
        self.assertTrue(rendered.ok, rendered.lint.problems)
        self.assertIn("> - band_centre with your own settings in structuring.toml: weight dte 0.5", rendered.text)


class NetTimeValueCriterionTest(unittest.TestCase):
    """Step 3c: roll_test = "net_time_value" fails a roll that gives up time value (N < 0, at the conservative sides),
    and passes an in-the-money roll that credit_only would fail."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.net = S.run_roll_analysis(Desk(cls).market(), "AG-20261020-01", criteria("roll", roll_test="net_time_value"))
        cls.credit = S.run_roll_analysis(Desk(cls).market(), "AG-20261020-01", CRITERIA)

    def test_the_net_time_value_per_share(self) -> None:
        # the report's cases: held 100 call at its ask 6.35 (4.23 intrinsic) to the Jan 115 bid 3.55 sells 1.43 more
        # time value than it buys back; off the held 105 call at 3.50, Jan 115 adds 0.05 and Dec 112.5 gives up 0.70
        self.assertEqual(D("1.43"), S.net_time_value(D("3.55"), D("6.35"), D("104.23"), D("115"), D("100")))
        self.assertEqual(D("0.05"), S.net_time_value(D("3.55"), D("3.50"), D("104.23"), D("115"), D("105")))
        self.assertEqual(D("-0.70"), S.net_time_value(D("2.80"), D("3.50"), D("104.23"), D("112.5"), D("105")))

    def test_roll_test_net_time_value_on_the_fixture(self) -> None:
        candidates = by_id(self.net)
        self.assertEqual(("ELIGIBLE", D("0.05")), (candidates["WMT270115C00115000"].status,
                                                   candidates["WMT270115C00115000"].net_time_value))
        dec = candidates["WMT261218C00112500"]
        self.assertIn(("roll_test", "gives_up_time_value", D("-0.70"), D("0")),
                      [(r.key, r.code, r.value, r.threshold) for r in dec.reasons])
        self.assertNotIn("credit_only", self.net.ranking.failure_counts)
        # the four debit rolls, and two deep in-the-money December calls that pay a credit (8.50 and 6.70) yet buy
        # back more time value than they sell (0.73 and 0.03): the test reads time value, not cash
        self.assertEqual(6, self.net.ranking.failure_counts["roll_test"])
        dec95 = candidates["WMT261218C00095000"]
        self.assertEqual((D("8.50"), D("-0.73")), (dec95.net_credit, dec95.net_time_value))
        self.assertIn("roll_test", [r.key for r in dec95.reasons])
        self.assertNotIn("credit_only", [r.key for r in by_id(self.credit)["WMT261218C00095000"].reasons])
        self.assertEqual("WMT270115C00115000", self.net.ranking.best.id)
        self.assertEqual(self.credit.ranking.best.id, self.net.ranking.best.id)

    def test_the_words_and_the_checks_row(self) -> None:
        dec = by_id(self.net)["WMT261218C00112500"]
        reason = next(r for r in dec.reasons if r.key == "roll_test")
        self.assertEqual("it gives up 0.70 a share of time value", T.reason_words(T.Writer(), reason, dec))
        w = T.Writer()
        best = self.net.ranking.best
        rows = T.check_rows(w, best, self.net.section, D("25"), self.net.group_context.s0, self.net.held.strike,
                            held=self.net.held)
        self.assertIn(["net time value per share", "adds 0.05 (within the held call's own quote spread)",
                       "at least 0", T.STATUS_PASS], rows)
        self.assertIn(["net per share", "credit 0.05 (10.00 in all)", "", ""], rows)


class SpreadCostTest(unittest.TestCase):
    """Step 3f (finding F11): a multi-leg answer shows what the quotes' spreads cost, which a per-leg max_spread_pct
    does not bound against a near-zero net."""

    def test_the_collar_checks_row(self) -> None:
        collar = S.run_proposal(Desk(self.__class__, drop_row=8, replace=SPY_TAG).market(), "AG-20261028-03", "collar",
                                CRITERIA)
        rows = T.check_rows(T.Writer(), collar.ranking.best, collar.section, D("25"), collar.group_context.s0)
        # put 25.45 / 25.65 and call 26.40 / 26.60: half of each spread, 0.10 + 0.10 per share, 20.00 for 1 contract
        self.assertIn(["spread cost", "0.20 per share (20.00 in all)", "", ""], rows)

    def test_the_ticket_cash_row_needs_two_legs_and_mids(self) -> None:
        lines = T.cash_table(T.Writer(), D("-1330.00"), D("-1305.00"), D("-150.00"), D("-135.00"), 1, D("2310.00"),
                             legs=2)
        self.assertIn("| spread cost against the mids | 25.00 |  |", lines)
        one = T.cash_table(T.Writer(), D("980.00"), D("995.00"), D("-150.00"), D("-135.00"), 1, D("0.00"), legs=1)
        self.assertFalse(any("spread cost" in line for line in one))
        no_mid = T.cash_table(T.Writer(), D("-1330.00"), None, D("-150.00"), None, 1, D("2310.00"), legs=2)
        self.assertFalse(any("spread cost" in line for line in no_mid))


class ObjectiveConfigTest(unittest.TestCase):
    def test_each_section_accepts_its_release_one_objective_first(self) -> None:
        self.assertEqual(("extrinsic_per_day", "band_centre"), structuring_config.OBJECTIVES["covered_call"])
        self.assertEqual(("extrinsic_per_day", "net_extrinsic_per_added_day"), structuring_config.OBJECTIVES["roll"])
        for section, ids in structuring_config.OBJECTIVES.items():
            self.assertEqual(ids[0], structuring_config.objective_for(section).id)
            for objective in ids:
                self.assertEqual(objective, criteria(section, objective=objective).objective(section).id)

    def test_band_centre_is_lower_first(self) -> None:
        self.assertEqual("lower", structuring_config.OBJECTIVE_DEFS["band_centre"].direction)
        self.assertEqual("higher", structuring_config.OBJECTIVE_DEFS["net_extrinsic_per_added_day"].direction)


class QuotePrecisionTest(unittest.TestCase):
    """``near_best``: one-sided, information only; never a change of order."""

    def put(self, strike: str, score: str, mid: str) -> "S.Candidate":
        return made(S.KIND_PROTECTIVE_PUT, [leg_of(strike, S.datetime.date(2027, 3, 19), "PUT")], score,
                    objective_scores={"stress_cost_per_day": (D(score), D(mid))})

    def test_within_the_best_half_spread_is_noted_and_the_order_kept(self) -> None:
        a = self.put("610", "0.1856", "0.1848")   # the best: half-spread 0.0008 in score units
        b = self.put("620", "0.1858", "0.1851")   # 0.0002 behind: noted
        c = self.put("625", "0.1868", "0.1861")   # 0.0012 behind: not noted
        for seed in range(8):
            cands = [a, b, c]
            random.Random(seed).shuffle(cands)
            ranking = S.rank_candidates(cands, S.KIND_PROTECTIVE_PUT)
            self.assertEqual([a.id, b.id, c.id], [x.id for x in ranking.candidates], seed)
            self.assertEqual(a.id, ranking.best.id)
            self.assertEqual((a.id, b.id), ranking.near_best)
            self.assertEqual("stress_cost_per_day", ranking.candidates[1].lost_because["by"])

    def test_a_wider_quote_never_earns_a_note(self) -> None:
        # 625's bid falls from 26.85 to 26.30: its mid score drops to 0.1842 but its ask score stays 0.1868; only the
        # conservative score admits a candidate, so it is still not noted and still ranks third
        a = self.put("610", "0.1856", "0.1848")
        b = self.put("620", "0.1858", "0.1851")
        wide = self.put("625", "0.1868", "0.1842")
        ranking = S.rank_candidates([wide, b, a], S.KIND_PROTECTIVE_PUT)
        self.assertEqual([a.id, b.id, wide.id], [x.id for x in ranking.candidates])
        self.assertEqual((a.id, b.id), ranking.near_best)

    def test_membership_is_a_prefix_of_the_ranking(self) -> None:
        # behind a best whose half-spread is 0.0004, a tight 615 at 0.1857 is noted and a wide 600 at 0.1863 is not
        a = self.put("610", "0.1856", "0.1852")
        x = self.put("615", "0.1857", "0.1856")
        y = self.put("600", "0.1863", "0.1830")
        self.assertEqual((a.id, x.id), S.rank_candidates([y, x, a], S.KIND_PROTECTIVE_PUT).near_best)

    def test_separate_scores_note_nothing(self) -> None:
        a = self.put("610", "0.1856", "0.1850")
        b = self.put("620", "0.1868", "0.1861")
        ranking = S.rank_candidates([b, a], S.KIND_PROTECTIVE_PUT)
        self.assertEqual([a.id, b.id], [x.id for x in ranking.candidates])
        self.assertEqual((), ranking.near_best)


class FixtureRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cc = criteria("covered_call", objective="band_centre")
        cls.cc = S.run_proposal(Desk(cls, drop_row=4, replace=WMT_TAGS).market(), "AG-20261020-01", "covered_call", cc)
        cls.roll_net = S.run_roll_analysis(Desk(cls).market(), "AG-20261020-01",
                                           criteria("roll", objective="net_extrinsic_per_added_day"))
        cls.roll_legacy_debits = S.run_roll_analysis(Desk(cls).market(), "AG-20261020-01",
                                                     criteria("roll", credit_only=False))
        cls.roll_net_debits = S.run_roll_analysis(Desk(cls).market(), "AG-20261020-01",
                                                  criteria("roll", credit_only=False,
                                                           objective="net_extrinsic_per_added_day"))
        cls.put_centre = S.run_proposal(Desk(cls, drop_row=8).market(), "AG-20261028-03", "protective_put",
                                        criteria("protective_put", objective="band_centre"))
        cls.put_legacy = S.run_proposal(Desk(cls, drop_row=8).market(), "AG-20261028-03", "protective_put", CRITERIA)
        cls.collar_centre = S.run_proposal(Desk(cls, drop_row=8, replace=SPY_TAG).market(), "AG-20261028-03", "collar",
                                           criteria("collar", objective="band_centre"))
        cls.collar_high_floor = S.run_proposal(Desk(cls, drop_row=8, replace=SPY_TAG).market(), "AG-20261028-03",
                                               "collar", criteria("collar", objective="band_centre",
                                                                  floor_pct=(D("6"), D("10"))))

    def test_covered_call_band_centre_names_the_december_115_call(self) -> None:
        ranking = self.cc.ranking
        self.assertEqual(("band_centre", S.VERDICT_BEST), (self.cc.objective.id, ranking.verdict))
        self.assertEqual(["WMT261218C00115000", "WMT270115C00120000", "WMT261218C00112500"],
                         [c.id for c in ranking.candidates[:3]])
        # band distances 0.91, 0.99, 1.41 plus quote terms 4.65 / 25, 4.35 / 25, 3.51 / 25
        self.assertEqual([D("1.10"), D("1.16"), D("1.55")], [c.score for c in ranking.candidates[:3]])

    def test_covered_call_switching_share_prices(self) -> None:
        # Jan 120's delta 0.2394 reads 0.25 from 104.55 up (0.89 + 0.174 < 0.91 + 0.186); Dec 112.5 takes over from
        # 102.39 down
        up, down = self.cc.ranking.switches
        self.assertEqual((D("104.55"), D("0.31"), "WMT270115C00120000"), (up.price, up.pct, up.becomes))
        self.assertEqual((D("102.39"), D("-1.77"), "WMT261218C00112500"), (down.price, down.pct, down.becomes))

    def test_put_and_collar_switching_share_prices(self) -> None:
        up, down = self.put_centre.ranking.switches
        self.assertEqual((D("657.72"), "SPY270319P00620000"), (up.price, up.becomes))
        self.assertEqual((D("646.90"), D("-0.19"), "SPY270319P00600000"), (down.price, down.pct, down.becomes))
        up, down = self.collar_centre.ranking.switches
        self.assertEqual((D("657.79"), D("647.09")), (up.price, down.price))
        self.assertEqual(((None, None)), self.put_legacy.ranking.switches)   # release-1 objectives: no scan

    def test_collar_switch_reaches_pairs_the_ranking_never_built(self) -> None:
        # floor band [6, 10]: the 610 put (floor 5.88 % today) is counted, not paired, so no 610 collar is ranked; it
        # enters the band at 648.94 and nears the 8 % centre as SPY rises, so the scan must re-test its pairs
        ids = set(by_id(self.collar_high_floor))
        self.assertNotIn("SPY270319P00610000+SPY270319C00680000", ids)
        up, _ = self.collar_high_floor.ranking.switches
        self.assertEqual("SPY270319P00610000+SPY270319C00680000", up.becomes)

    def test_delay_move(self) -> None:
        self.assertEqual(D("0.49"), T.delay_move_pct(0.3941))   # WMT Dec 115's neighbour volatilities: about 0.39
        self.assertEqual(D("0.34"), T.delay_move_pct(0.2725))

    def test_roll_net_extrinsic(self) -> None:
        best = self.roll_net.ranking.best
        self.assertEqual(("WMT270115C00115000", D("0.000857")), (best.id, best.score))

    def test_debit_rolls_allowed_legacy_names_a_roll_that_buys_back_more_time_value(self) -> None:
        legacy = self.roll_legacy_debits.ranking.best
        self.assertEqual(("WMT261218C00112500", D("0.0597"), D("-0.70")), (legacy.id, legacy.score, legacy.net_credit))
        self.assertEqual(D("-0.671592"), legacy.objective_scores["net_extrinsic_per_added_day"][0])
        net = self.roll_net_debits.ranking
        self.assertEqual(["WMT270115C00115000", "WMT261218C00112500", "WMT270115C00120000", "WMT261218C00115000",
                          "WMT261218C00120000"], [c.id for c in net.candidates[:5]])
        self.assertEqual([D("0.000857"), D("-0.671592"), D("-1.199271"), D("-1.343183"), D("-2.302600")],
                         [c.score for c in net.candidates[:5]])

    def test_roll_does_not_take_band_centre(self) -> None:
        # a score that ignores the time value bought back would name Dec 115, which gives up 1.40 a share (N = -1.40),
        # over Jan 115, the one roll that adds time value: the reader refuses it for [roll]
        self.assertNotIn("band_centre", structuring_config.OBJECTIVES["roll"])
        self.assertNotIn("band_centre", self.roll_net.ranking.best.objective_scores)

    def test_protective_put_band_centre_and_the_unchanged_release_one_best(self) -> None:
        centre = self.put_centre.ranking.best
        self.assertEqual(("SPY270319P00610000", D("0.86")), (centre.id, centre.score))   # 0.82 + 0.87 / 25
        self.assertEqual((), self.put_centre.ranking.near_best)   # band_centre has no mid score: nothing to note
        legacy = self.put_legacy.ranking
        self.assertEqual(("SPY270319P00610000", None), (legacy.best.id, legacy.tie_break))
        self.assertEqual(("SPY270319P00610000", "SPY270319P00620000"), legacy.near_best)

    def test_a_wide_quote_costs_score_under_band_centre(self) -> None:
        # widen only the 610 put to 20.80 / 25.40 (mid 23.10, spread 19.91 %, inside max_spread_pct 25): its quote term
        # rises from 0.03 to 0.80, so it falls behind the 600 put (0.95); without the term it would stay best at 0.82
        answer = self.put_centre
        base = by_id(answer)["SPY270319P00610000"].legs[0]
        wide = dataclasses.replace(base, bid=D("20.80"), ask=D("25.40"), mid=D("23.10"), spread_pct=D("19.91"))
        widened = S.protective_put_candidate(wide, answer.pricing, answer.group_context, answer.section)
        self.assertEqual(("ELIGIBLE", D("1.62")), (widened.status, widened.objective_scores["band_centre"][0]))
        others = [c for c in answer.ranking.candidates if c.id != widened.id]
        ranking = S.rank_candidates(others + [widened], S.KIND_PROTECTIVE_PUT, answer.objective)
        self.assertEqual("SPY270319P00600000", ranking.best.id)

    def test_collar_band_centre(self) -> None:
        best = self.collar_centre.ranking.best
        self.assertEqual(("SPY270319P00610000+SPY270319C00680000", D("1.16"), D("-3.30")),
                         (best.id, best.score, best.net_debit))
        self.assertIn("SPY270319P00620000+SPY270319C00680000", by_id(self.collar_centre))

    def render(self, lines_of, answer, **kwargs) -> "T.Rendered":
        w = T.Writer()
        rendered = T.finish(w, lines_of(w, answer, **kwargs), T.data_as_of(answer), answer.tool)
        self.assertTrue(rendered.ok, rendered.lint.problems)   # the alert lint passes every new text
        return rendered

    def test_every_new_objective_renders_lint_clean(self) -> None:
        for answer in (self.cc, self.put_centre, self.collar_centre):
            with self.subTest(kind=answer.kind):
                text = self.render(T.proposal_lines, answer, owner_level=2).text
                self.assertIn("by your objective band_centre", text)
                self.assertIn("summed with equal weight", text)
                self.assertIn("another candidate is best if the stock rises", text)
                self.assertIn("so these quotes do not settle the best", text)
        for answer in (self.roll_net, self.roll_net_debits):
            with self.subTest(objective=answer.objective.id):
                self.assertIn("by your objective %s" % answer.objective.id,
                              self.render(T.roll_analysis_lines, answer).text)
        self.assertIn("rank 2 lies within the best's own quote spread on stress_cost_per_day",
                      self.render(T.proposal_lines, self.put_legacy, owner_level=2).text)


class AmericanPutCaveatTest(unittest.TestCase):
    """The early-exercise caveat: absolute delta at least 0.80, or in the money with a time value at the mid below the
    interest on the strike. SPY 2027-03-19 (S = 648.12, r = 0.0412, tau = 136 days to the 16:00 ET cutoff)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.answer = S.run_proposal(Desk(cls, drop_row=8).market(), "AG-20261028-03", "protective_put", CRITERIA)

    def put(self, strike: str, bid: str, ask: str, abs_delta: str) -> "S.PricedLeg":
        base = by_id(self.answer)["SPY270319P00610000"].legs[0]
        return dataclasses.replace(base, strike=D(strike), bid=D(bid), ask=D(ask), mid=(D(bid) + D(ask)) / 2,
                                   abs_delta=D(abs_delta))

    def test_out_of_the_money_puts_never_carry_it(self) -> None:
        # exercising an out-of-the-money put now pays nothing: no caveat however small its price against K x r x tau
        for strike, bid, ask, delta in (("610", "22.90", "23.10", "0.30"), ("590", "18.55", "18.75", "0.24"),
                                        ("520", "2.40", "2.55", "0.06")):
            leg = self.put(strike, bid, ask, delta)
            self.assertIsNone(S.put_exercise_carry(leg, self.answer.pricing), strike)
            self.assertFalse(S._put_caveat(leg, self.answer.pricing), strike)

    def test_in_the_money_put_with_little_time_value(self) -> None:
        # 700 put mid 58.90: time value 7.02 against interest 10.67 (abs delta 0.66, below the 0.80 rule)
        leg = self.put("700", "58.80", "59.00", "0.66")
        self.assertEqual((D("7.02"), D("10.67")), S.put_exercise_carry(leg, self.answer.pricing))
        self.assertTrue(S._put_caveat(leg, self.answer.pricing))
        # 660 put mid 39.00: time value 27.12 against 10.06, no caveat
        self.assertFalse(S._put_caveat(self.put("660", "38.90", "39.10", "0.48"), self.answer.pricing))

    def test_the_delta_rule_still_applies(self) -> None:
        self.assertTrue(S._put_caveat(self.put("610", "22.90", "23.10", "0.80"), self.answer.pricing))


if __name__ == "__main__":
    unittest.main()
