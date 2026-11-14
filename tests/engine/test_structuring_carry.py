"""Step 3a of the quant review: the structuring tools price each expiration at the carry its own put-call parity
implies when those quotes exclude the calendar's dividend rate (``carry.decide``), and say so; otherwise the calendar's
rate stands, with the reason. On the synthetic fixture the SPY chains switch (they were generated with no dividend) and
the WMT and NVDA chains keep the calendar (their quotes cannot resolve the carry)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import datetime
import math
import random
import unittest
from decimal import Decimal

from aegis_desk import carry
from aegis_desk import structuring as S
from aegis_desk import structuring_text as T
from tests.engine.test_structuring import CRITERIA, RATE, SPY_TAG, Desk, solved

MAR19 = datetime.date(2027, 3, 19)
DEC18 = datetime.date(2026, 12, 18)
JAN15 = datetime.date(2027, 1, 15)


class DecideTest(unittest.TestCase):
    """``carry.decide`` reaches the same answer as the full interval, computing the early-exercise tree only when the
    answer depends on it."""

    def test_the_lazy_decision_matches_the_full_interval(self) -> None:
        rng = random.Random(20261113)
        for _ in range(200):
            spot = rng.uniform(50, 700)
            tau = rng.uniform(0.05, 0.6)
            q_true = rng.uniform(-0.01, 0.03)
            sigma = rng.uniform(0.15, 0.5)
            forward = spot * math.exp((RATE - q_true) * tau)
            strikes = [round(spot * (1 + step * 0.02), 2) for step in (-2, -1, 0, 1, 2)]
            calls, puts, vols = {}, {}, {}
            for strike in strikes:
                gap = math.exp(-RATE * tau) * (forward - strike)
                put = max(0.5, rng.uniform(1, 30))
                call = put + gap
                if call <= 0.05:
                    continue
                half = rng.uniform(0.01, 0.3)
                calls[strike] = (max(0.0, call - half), call + half)
                puts[strike] = (max(0.0, put - half), put + half)
                vols[strike] = sigma
            calendar_q = rng.uniform(-0.01, 0.04)
            full = carry.parity_carry(calls, puts, spot, tau, RATE, vols)
            decision = carry.decide(calls, puts, spot, tau, RATE, vols, calendar_q)
            if full is None:
                self.assertEqual((None, carry.SOURCE_TOO_FEW), (decision.q, decision.source))
                continue
            expected = (full.q_imp if abs(full.q_imp - calendar_q) <= carry.MAX_GAP and full.excludes(calendar_q)
                        else None)
            self.assertEqual(expected, decision.q)
            self.assertEqual(expected, carry.implied_carry(calls, puts, spot, tau, RATE, vols, calendar_q))
            self.assertAlmostEqual(full.q_imp, decision.found.q_imp, delta=1e-15)

    def test_sources(self) -> None:
        self.assertEqual(("parity", "calendar_unresolved", "calendar_gap", "calendar_too_few_strikes"),
                         (carry.SOURCE_PARITY, carry.SOURCE_UNRESOLVED, carry.SOURCE_GAP, carry.SOURCE_TOO_FEW))


class PricingContextTest(unittest.TestCase):
    def test_q_for_falls_back_to_the_calendar(self) -> None:
        answer = S.run_proposal(Desk(self.__class__, drop_row=8).market(), "AG-20261028-03", "protective_put",
                                CRITERIA)
        ctx = answer.pricing
        self.assertIn(MAR19, ctx.chain_q)
        self.assertEqual(ctx.chain_q[MAR19], ctx.q_for(MAR19))
        self.assertEqual(ctx.q, ctx.q_for(datetime.date(2030, 1, 18)))


class FixtureCarryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.put = S.run_proposal(Desk(cls, drop_row=8).market(), "AG-20261028-03", "protective_put", CRITERIA)
        cls.collar = S.run_proposal(Desk(cls, drop_row=8, replace=SPY_TAG).market(), "AG-20261028-03", "collar",
                                    CRITERIA)
        cls.roll = S.run_roll_analysis(Desk(cls).market(), "AG-20261020-01", CRITERIA)

    def test_the_spy_march_chain_is_priced_at_the_parity_carry(self) -> None:
        record = {entry["expiration"]: entry for entry in self.put.carry}[MAR19]
        self.assertEqual(carry.SOURCE_PARITY, record["source"])
        self.assertEqual(Decimal("0.0115"), record["calendar_q"])
        self.assertLess(abs(record["q"]), Decimal("0.001"))
        self.assertAlmostEqual(float(record["q"]), self.put.pricing.q_for(MAR19), delta=1e-6)
        self.assertTrue(record["q_quote"] is not None and record["q_amer"] is None,
                        "the calendar lies above the interval, so the early-exercise tree is not needed")

    def test_the_spy_legs_solve_their_volatility_at_that_carry(self) -> None:
        q = self.put.pricing.q_for(MAR19)
        for candidate in self.put.ranking.candidates:
            leg = candidate.legs[0]
            if leg.unpriced is not None:
                continue
            sigma, delta = solved(leg.occ, "PUT", "648.12", format(leg.strike, "f"), leg.expiration,
                                  format(leg.mid, "f"), q)
            with self.subTest(occ=leg.occ):
                self.assertAlmostEqual(sigma, leg.iv, delta=1e-9)
                self.assertAlmostEqual(delta, leg.delta, delta=1e-9)

    def test_call_and_put_volatilities_agree_at_the_parity_carry(self) -> None:
        # the report's check: at the calendar's 1.15 % the call and put volatilities at the three strikes nearest the
        # money are about 1.9 points apart; at the parity carry they agree within a few hundredths of a point
        chain = {MAR19: self.collar.chains[MAR19]}

        def vols_at(ctx: "S.PricingContext") -> dict:
            out = {}
            for right in ("CALL", "PUT"):
                for quote, listed in S.listed_options(chain, right):
                    leg = S.price_listed(quote, listed, ctx)
                    if leg.unpriced is None:
                        out[(right, leg.strike)] = leg.iv
            return out

        parity = vols_at(self.collar.pricing)
        calendar = vols_at(dataclasses.replace(self.collar.pricing, chain_q={}))
        shared = sorted({k for (right, k) in parity if ("CALL", k) in parity and ("PUT", k) in parity},
                        key=lambda k: abs(k - Decimal("648.12")))[:3]
        self.assertEqual(3, len(shared))
        for strike in shared:
            with self.subTest(strike=strike):
                self.assertLess(abs(parity[("CALL", strike)] - parity[("PUT", strike)]), 0.0005)
                self.assertGreater(abs(calendar[("CALL", strike)] - calendar[("PUT", strike)]), 0.015)

    def test_the_wmt_chains_keep_the_calendar(self) -> None:
        sources = {entry["expiration"]: entry["source"] for entry in self.roll.carry}
        self.assertEqual({DEC18: carry.SOURCE_UNRESOLVED, JAN15: carry.SOURCE_UNRESOLVED}, sources)
        self.assertEqual(self.roll.pricing.q, self.roll.pricing.q_for(DEC18))
        self.assertEqual({}, dict(self.roll.pricing.chain_q))

    def test_the_text_says_which_expiration_switched(self) -> None:
        w = T.Writer()
        lines = T.carry_lines(w, self.put)
        self.assertEqual(["carry: the 2027-03-19 options are priced at the dividend carry their put-call parity implies, "
                          "0.01 % a year, because those quotes exclude the calendar's 1.15 %"], lines)
        self.assertEqual([], T.carry_lines(T.Writer(), self.roll))

    def test_the_payload_carries_every_evaluated_expiration(self) -> None:
        body = self.roll.as_dict()
        self.assertIn("carry", S.ANSWER_KEYS)
        self.assertEqual([DEC18, JAN15], [entry["expiration"] for entry in body["carry"]])
        self.assertEqual({"expiration", "source", "q", "calendar_q", "q_imp", "q_quote", "q_amer"},
                         set(body["carry"][0]))

    def test_rankings_that_read_no_delta_are_unchanged(self) -> None:
        self.assertEqual("SPY270319P00610000", self.put.ranking.best.id)
        self.assertEqual(Decimal("0.1856"), self.put.ranking.best.score)
        self.assertEqual("WMT270115C00115000", self.roll.ranking.best.id)


if __name__ == "__main__":
    unittest.main()
