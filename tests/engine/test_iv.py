"""Implied-vol tests: golden round trip, grid and seeded round trips, status codes, Brent.

Source: Aegis ``tests/test_iv.py`` (FairGradient). Reproduced: the golden round trip (rel/abs
1e-7), the Hypothesis round-trip domain (spot 50..500, moneyness 0.8..1.25, tau 0.25..2, r
-0.01..0.08, sigma 0.15..1.0, q 0..0.05; Aegis tolerance rel/abs 1e-5, tightened here to 1e-8 on
sigma because Brent stops at 1e-10), the deep-OTM long-dated call quoted at 0.5 (S=50, K=60, tau=2,
r=0.05; 0 < iv < 0.2 and reprices to 1e-7), the quote above S e^{-qT}, the quote exactly on that
bound (no interior root), and the deep ITM call quoted below discounted intrinsic. Aegis raised
``QuantInputError``/``ConvergenceError``; this engine returns ``IvResult.status`` instead.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import math
import random
import unittest

from aegis_desk import bsm, iv

_GOLDEN = {"S": 162.06, "K": 170.163, "tau": 1.553, "r": 0.05, "q": 0.052, "sigma": 0.226}


def _approx(actual: float, expected: float, rel: float, abs_: float) -> bool:
    """``pytest.approx`` semantics: ``|actual - expected| <= max(rel * |expected|, abs)``."""
    return abs(actual - expected) <= max(rel * abs(expected), abs_)


def _solve(price: float, right: str, m: dict) -> iv.IvResult:
    return iv.implied_vol(price, right, m["S"], m["K"], m["tau"], m["r"], m["q"])


class RoundTripTest(unittest.TestCase):
    """price -> implied_vol -> price recovers sigma and the quote."""

    def test_golden_round_trip(self) -> None:
        # Aegis test_iv_round_trip_at_golden: rel 1e-7 / abs 1e-7.
        for right in bsm.RIGHTS:
            with self.subTest(right=right):
                price = bsm.price(right, **_GOLDEN)
                res = _solve(price, right, _GOLDEN)
                self.assertEqual(res.status, iv.STATUS_OK)
                self.assertTrue(res.ok)
                self.assertTrue(_approx(res.sigma, _GOLDEN["sigma"], rel=1e-7, abs_=1e-7), res)
                self.assertLessEqual(res.iterations, iv.MAX_ITER)
                self.assertLessEqual(abs(res.residual), 1e-9)

    def test_grid_round_trip(self) -> None:
        # 1,620 deterministic solves spanning both rights, moneyness 0.8..1.25, 3 months..2 years,
        # negative to high rates, dividends, 15 %..100 % vol. Brent's 1e-10 stop gives sigma to
        # ~2e-11 (measured); 1e-8 leaves a 400x margin.
        count, max_iterations = 0, 0
        for S in (50.0, 100.0, 250.0):
            for moneyness in (0.8, 0.9, 1.0, 1.1, 1.25):
                for tau in (0.25, 1.0, 2.0):
                    for r in (-0.01, 0.03, 0.08):
                        for q in (0.0, 0.03):
                            for sigma in (0.15, 0.4, 1.0):
                                for right in bsm.RIGHTS:
                                    K = S * moneyness
                                    price = bsm.price(right, S, K, tau, r, q, sigma)
                                    res = iv.implied_vol(price, right, S, K, tau, r, q)
                                    label = (right, S, K, tau, r, q, sigma)
                                    self.assertEqual(res.status, iv.STATUS_OK, label)
                                    self.assertTrue(
                                        _approx(res.sigma, sigma, rel=0.0, abs_=1e-8), (label, res)
                                    )
                                    reprice = bsm.price(right, S, K, tau, r, q, res.sigma)
                                    self.assertTrue(
                                        _approx(reprice, price, rel=1e-9, abs_=1e-9), (label, res)
                                    )
                                    self.assertEqual(res.residual, reprice - price)
                                    self.assertGreaterEqual(res.lower_bound, 0.0)
                                    self.assertLess(res.lower_bound, price)
                                    self.assertGreater(res.upper_bound, price)
                                    max_iterations = max(max_iterations, res.iterations)
                                    count += 1
        self.assertEqual(count, 1620)
        self.assertLessEqual(max_iterations, 40)  # Brent: superlinear; bisection alone needs ~37

    def test_seeded_round_trip_on_the_aegis_domain(self) -> None:
        # Aegis test_price_iv_round_trip (Hypothesis, 200 examples) on random.Random(20261102).
        rng = random.Random(20261102)
        for _ in range(300):
            spot = rng.uniform(50.0, 500.0)
            m = {
                "S": spot,
                "K": spot * rng.uniform(0.8, 1.25),
                "tau": rng.uniform(0.25, 2.0),
                "r": rng.uniform(-0.01, 0.08),
                "q": rng.uniform(0.0, 0.05),
                "sigma": rng.uniform(0.15, 1.0),
            }
            right = rng.choice(bsm.RIGHTS)
            price = bsm.price(right, **m)
            res = _solve(price, right, m)
            self.assertEqual(res.status, iv.STATUS_OK, (right, m))
            self.assertTrue(_approx(res.sigma, m["sigma"], rel=1e-8, abs_=1e-8), (right, m, res))

    def test_deep_otm_long_dated_quote(self) -> None:
        # Aegis test_iv_newton_overshoot_recovers_via_bracket: S=50, K=60, tau=2, r=0.05, quote 0.5
        # is a valid price strictly inside the bounds with a small implied vol.
        res = iv.implied_vol(0.5, bsm.CALL, 50.0, 60.0, 2.0, 0.05, 0.0)
        self.assertEqual(res.status, iv.STATUS_OK)
        self.assertTrue(0.0 < res.sigma < 0.2, res)
        reprice = bsm.price(bsm.CALL, 50.0, 60.0, 2.0, 0.05, 0.0, res.sigma)
        self.assertTrue(_approx(reprice, 0.5, rel=0.0, abs_=1e-7), reprice)

    def test_result_fields_are_consistent(self) -> None:
        res = iv.implied_vol(
            bsm.price(bsm.PUT, 100.0, 100.0, 1.0, 0.02, 0.0, 0.3),
            bsm.PUT,
            100.0,
            100.0,
            1.0,
            0.02,
            0.0,
        )
        self.assertIsInstance(res, iv.IvResult)
        self.assertEqual(res.status, iv.STATUS_OK)
        self.assertTrue(_approx(res.sigma, 0.3, rel=0.0, abs_=1e-9))
        self.assertGreater(res.iterations, 0)
        self.assertEqual(
            res.residual,
            bsm.price(bsm.PUT, 100.0, 100.0, 1.0, 0.02, 0.0, res.sigma)
            - bsm.price(bsm.PUT, 100.0, 100.0, 1.0, 0.02, 0.0, 0.3),
        )
        lower, upper = iv.price_bounds(bsm.PUT, 100.0, 100.0, 1.0, 0.02, 0.0)
        self.assertEqual((res.lower_bound, res.upper_bound), (lower, upper))


class StatusTest(unittest.TestCase):
    """Bad quotes come back as statuses, never as exceptions or numbers."""

    def _assert_rejected(self, res: iv.IvResult, status: str) -> None:
        self.assertEqual(res.status, status)
        self.assertFalse(res.ok)
        self.assertIsNone(res.sigma)
        self.assertIsNone(res.residual)
        self.assertEqual(res.iterations, 0)

    def test_expired_contract(self) -> None:
        for tau in (0.0, -1e-9, -0.25):
            with self.subTest(tau=tau):
                res = iv.implied_vol(5.0, bsm.CALL, 105.0, 100.0, tau, 0.05, 0.0)
                self._assert_rejected(res, iv.STATUS_EXPIRED)
                # Bounds collapse to the intrinsic value at expiry.
                self.assertEqual((res.lower_bound, res.upper_bound), (5.0, 5.0))
                res_otm = iv.implied_vol(0.0, bsm.PUT, 105.0, 100.0, tau, 0.05, 0.0)
                self._assert_rejected(res_otm, iv.STATUS_EXPIRED)
                self.assertEqual((res_otm.lower_bound, res_otm.upper_bound), (0.0, 0.0))

    def test_price_below_intrinsic(self) -> None:
        # Aegis test_iv_rejects_price_below_intrinsic: deep ITM call quoted at 1.0.
        res = iv.implied_vol(1.0, bsm.CALL, 200.0, 100.0, 1.0, 0.05, 0.0)
        self._assert_rejected(res, iv.STATUS_BELOW_INTRINSIC)
        self.assertTrue(_approx(res.lower_bound, 200.0 - 100.0 * math.exp(-0.05), 1e-12, 0.0))
        # A negative "price" is below every lower bound.
        self._assert_rejected(
            iv.implied_vol(-0.01, bsm.PUT, 100.0, 90.0, 0.5, 0.01, 0.0), iv.STATUS_BELOW_INTRINSIC
        )
        # A European call quoted at undiscounted intrinsic with r > 0 is below the discounted bound.
        self._assert_rejected(
            iv.implied_vol(100.0, bsm.CALL, 200.0, 100.0, 1.0, 0.05, 0.0), iv.STATUS_BELOW_INTRINSIC
        )

    def test_price_above_no_arbitrage_bound(self) -> None:
        # Aegis test_iv_rejects_price_above_upper_bound: a call cannot be worth more than S e^{-qT}.
        upper = 100.0 * math.exp(-0.0 * 1.0)
        self._assert_rejected(
            iv.implied_vol(upper + 1.0, bsm.CALL, 100.0, 100.0, 1.0, 0.01, 0.0),
            iv.STATUS_ABOVE_BOUND,
        )
        # ... and a put more than K e^{-rT}.
        k_disc = 100.0 * math.exp(-0.05 * 2.0)
        res = iv.implied_vol(k_disc + 0.01, bsm.PUT, 80.0, 100.0, 2.0, 0.05, 0.0)
        self._assert_rejected(res, iv.STATUS_ABOVE_BOUND)
        self.assertTrue(_approx(res.upper_bound, k_disc, 1e-12, 0.0))

    def test_price_on_the_upper_bound_has_no_root(self) -> None:
        # Aegis test_iv_raises_when_no_interior_root: exactly S e^{-qT} passes the bound check but
        # no finite volatility reaches it.
        self._assert_rejected(
            iv.implied_vol(100.0, bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0), iv.STATUS_NO_ROOT
        )

    def test_price_on_the_lower_bound_has_no_root(self) -> None:
        # A zero bid on an out-of-the-money option, or a deep ITM quote exactly at discounted
        # intrinsic: the implied vol is the limit 0, not a root.
        self._assert_rejected(
            iv.implied_vol(0.0, bsm.PUT, 100.0, 80.0, 0.5, 0.02, 0.0), iv.STATUS_NO_ROOT
        )
        lower, _ = iv.price_bounds(bsm.CALL, 200.0, 100.0, 1.0, 0.05, 0.0)
        self._assert_rejected(
            iv.implied_vol(lower, bsm.CALL, 200.0, 100.0, 1.0, 0.05, 0.0), iv.STATUS_NO_ROOT
        )

    def test_upward_bracket_expansion(self) -> None:
        # 700 % vol lies above the initial 500 % bracket and inside the single expansion to 1,000 %.
        price_7 = bsm.price(bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0, 7.0)
        res = iv.implied_vol(price_7, bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0)
        self.assertEqual(res.status, iv.STATUS_OK)
        self.assertTrue(_approx(res.sigma, 7.0, rel=0.0, abs_=1e-8), res)
        # 1,200 % vol is beyond the expanded bracket: no root is reported, nothing is extrapolated.
        price_12 = bsm.price(bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0, 12.0)
        self.assertLess(price_12, 100.0 - iv.BOUND_SLACK)
        self._assert_rejected(
            iv.implied_vol(price_12, bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0), iv.STATUS_NO_ROOT
        )

    def test_downward_bracket_expansion(self) -> None:
        # An at-the-money quote of 0.001 on a $100 underlying implies sigma ~ 2.5e-5 (below the
        # initial 1e-4 floor, inside the single expansion to 1e-6):
        # sigma ~ price * sqrt(2 pi / tau) / S.
        res = iv.implied_vol(0.001, bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0)
        self.assertEqual(res.status, iv.STATUS_OK)
        self.assertTrue(
            _approx(res.sigma, 0.001 * math.sqrt(2.0 * math.pi) / 100.0, 1e-6, 0.0), res
        )
        # A quote of 1e-5 implies sigma ~ 2.5e-7, below the expanded floor: no root.
        self._assert_rejected(
            iv.implied_vol(1e-5, bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0), iv.STATUS_NO_ROOT
        )

    def test_status_constants(self) -> None:
        self.assertEqual(
            iv.STATUSES, ("ok", "expired", "below_intrinsic", "above_bound", "no_root")
        )
        self.assertEqual((iv.BRACKET_LO, iv.BRACKET_HI), (1e-4, 5.0))
        self.assertEqual((iv.BRACKET_LO_EXPANDED, iv.BRACKET_HI_EXPANDED), (1e-6, 10.0))
        self.assertEqual((iv.XTOL, iv.MAX_ITER), (1e-10, 200))

    def test_programming_errors_still_raise(self) -> None:
        with self.assertRaises(ValueError):
            iv.implied_vol(5.0, "call", 100.0, 100.0, 1.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            iv.implied_vol(5.0, bsm.CALL, 0.0, 100.0, 1.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            iv.implied_vol(5.0, bsm.CALL, 100.0, -100.0, 1.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            iv.implied_vol(float("nan"), bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            iv.implied_vol("5.0", bsm.CALL, 100.0, 100.0, 1.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            iv.implied_vol(5.0, bsm.CALL, 100.0, 100.0, float("inf"), 0.0, 0.0)


class PriceBoundsTest(unittest.TestCase):
    def test_bounds_formulas(self) -> None:
        S, K, tau, r, q = 100.0, 90.0, 0.75, 0.04, 0.02
        fs, kd = S * math.exp(-q * tau), K * math.exp(-r * tau)
        self.assertEqual(iv.price_bounds(bsm.CALL, S, K, tau, r, q), (fs - kd, fs))
        self.assertEqual(iv.price_bounds(bsm.PUT, S, K, tau, r, q), (0.0, kd))
        self.assertEqual(
            iv.price_bounds(bsm.PUT, 80.0, K, tau, r, q)[0], kd - 80.0 * math.exp(-q * tau)
        )

    def test_model_price_lies_within_the_bounds(self) -> None:
        for sigma in (0.01, 0.3, 3.0):
            for right in bsm.RIGHTS:
                lower, upper = iv.price_bounds(right, 100.0, 110.0, 1.5, 0.03, 0.01)
                value = bsm.price(right, 100.0, 110.0, 1.5, 0.03, 0.01, sigma)
                self.assertGreaterEqual(value, lower)
                self.assertLessEqual(value, upper)


class BrentTest(unittest.TestCase):
    """The root finder on its own."""

    def test_sqrt_two(self) -> None:
        res = iv.brent_root(lambda x: x * x - 2.0, 0.0, 2.0)
        self.assertTrue(res.converged)
        self.assertTrue(_approx(res.root, math.sqrt(2.0), rel=0.0, abs_=1e-10), res)
        self.assertEqual(res.residual, res.root * res.root - 2.0)
        self.assertLess(res.iterations, 15)

    def test_transcendental(self) -> None:
        res = iv.brent_root(lambda x: math.cos(x) - x, 0.0, 1.0)
        self.assertTrue(res.converged)
        self.assertTrue(_approx(res.root, 0.7390851332151607, rel=0.0, abs_=1e-10), res)

    def test_exact_root_at_an_endpoint(self) -> None:
        self.assertEqual(iv.brent_root(lambda x: x - 1.0, 1.0, 3.0).root, 1.0)
        self.assertEqual(iv.brent_root(lambda x: x - 3.0, 1.0, 3.0).root, 3.0)
        self.assertEqual(iv.brent_root(lambda x: x - 1.0, 1.0, 3.0).iterations, 0)

    def test_accepts_precomputed_endpoint_values(self) -> None:
        f = lambda x: x**3 - 8.0  # noqa: E731
        res = iv.brent_root(f, 0.0, 4.0, fa=f(0.0), fb=f(4.0))
        self.assertTrue(_approx(res.root, 2.0, rel=0.0, abs_=1e-10), res)

    def test_flat_root_and_discontinuity(self) -> None:
        # A triple root (flat) and a sign step both force the bisection fallback; the stop
        # criterion is on the bracket width, so the location is still found to xtol.
        flat = iv.brent_root(lambda x: (x - 0.3) ** 3, 0.0, 1.0)
        self.assertTrue(flat.converged)
        self.assertTrue(_approx(flat.root, 0.3, rel=0.0, abs_=1e-9), flat)
        step = iv.brent_root(lambda x: 1.0 if x > 0.5 else -1.0, 0.0, 1.0)
        self.assertTrue(step.converged)
        self.assertTrue(_approx(step.root, 0.5, rel=0.0, abs_=1e-9), step)
        self.assertLessEqual(step.iterations, 60)

    def test_unbracketed_raises(self) -> None:
        with self.assertRaises(ValueError):
            iv.brent_root(lambda x: x * x + 1.0, 0.0, 1.0)

    def test_iteration_budget_is_reported_when_exhausted(self) -> None:
        res = iv.brent_root(lambda x: x - 0.123456789, 0.0, 1.0, max_iter=1)
        self.assertFalse(res.converged)
        self.assertEqual(res.iterations, 1)
        self.assertTrue(math.isfinite(res.root))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
