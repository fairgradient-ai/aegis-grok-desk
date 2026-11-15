"""``aegis_desk.american``: one leg on the American model of record (roadmap 2.3, the owner's answer to open question 2).

Value and Greeks from the lattice in desk units, vega and rho as central differences of the lattice price, the boundary
band (approximate delta, gamma and theta UNAVAILABLE), and the implied volatility that inverts the lattice price."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import itertools
import math
import unittest

from aegis_desk import american, bsm, greeks, iv, lattice

R, Q = 0.0412, 0.0115
GRID = list(itertools.product(bsm.RIGHTS, (0.85, 0.95, 1.0, 1.05, 1.2), (21 / 365, 0.25, 0.75), (0.15, 0.3, 0.6)))


def band_put() -> tuple:
    """A put whose spot lies in the lattice's boundary band (found by a scan, so the test does not pin a spot)."""
    for n in range(600, 1000):
        spot = 0.1 * n
        if lattice.american(bsm.PUT, spot, 100.0, 0.3, R, Q, 0.25).near_boundary:
            return (bsm.PUT, spot, 100.0, 0.3, R, Q, 0.25)
    raise AssertionError("no band spot found")


class ValueTest(unittest.TestCase):
    def test_price_delta_gamma_theta_are_the_lattice_s_in_desk_units(self) -> None:
        args = (bsm.PUT, 100.0, 105.0, 0.3, R, Q, 0.25)
        tree = lattice.american(*args)
        value = american.value(*args)
        self.assertFalse(value.near_boundary or value.exercise_now)
        self.assertEqual((tree.price, tree.delta, tree.gamma), (value.price, value.delta, value.gamma))
        self.assertAlmostEqual(tree.theta_per_year / 365.0, value.theta, places=15)
        self.assertEqual(tree.early_exercise_premium, value.early_exercise_premium)
        self.assertEqual(tree.european_closed, value.european)
        self.assertGreater(value.early_exercise_premium, 0.0)
        self.assertEqual((american.QUALITY_OK, "american_crr_200", 0.25), (value.quality, value.model, value.iv))

    def test_without_early_exercise_vega_and_rho_are_the_closed_form_s(self) -> None:
        # A call with no yield is never exercised early, so its lattice price is the closed form and the differences
        # are those of the closed form (error of order bump^2).
        args = (bsm.CALL, 100.0, 95.0, 0.4, R, 0.0, 0.3)
        value = american.value(*args)
        closed = greeks.european_greeks(*args)
        self.assertEqual(0.0, value.early_exercise_premium)
        self.assertAlmostEqual(closed.vega, value.vega, delta=1e-4 * abs(closed.vega))
        self.assertAlmostEqual(closed.rho, value.rho, delta=1e-3 * abs(closed.rho))
        self.assertAlmostEqual(closed.delta, value.delta, places=12)

    def test_an_early_exercise_value_lowers_a_put_s_vega_and_rho(self) -> None:
        args = (bsm.PUT, 100.0, 110.0, 0.5, R, 0.0, 0.25)
        value = american.value(*args)
        closed = greeks.european_greeks(*args)
        self.assertTrue(0.0 < value.vega < closed.vega)
        self.assertTrue(closed.rho < value.rho < 0.0)

    def test_a_small_volatility_takes_a_smaller_vega_step(self) -> None:
        # sigma 0.015: the step is sigma / 2, so the difference is the closed form's over [0.0075, 0.0225]
        value = american.value(bsm.CALL, 100.0, 100.0, 0.25, R, 0.0, 0.015)
        expected = (bsm.price(bsm.CALL, 100.0, 100.0, 0.25, R, 0.0, 0.0225)
                    - bsm.price(bsm.CALL, 100.0, 100.0, 0.25, R, 0.0, 0.0075)) / 0.015 * 0.01
        self.assertAlmostEqual(expected, value.vega, places=12)

    def test_below_the_tree_s_lowest_volatility_the_vega_step_is_one_sided(self) -> None:
        floor = american.tree_floor(0.25, R, 0.0)
        self.assertAlmostEqual(R * math.sqrt(0.25 / 200), floor, delta=1e-8)
        sigma = floor * 1.5  # sigma / 2 below would leave the tree
        value = american.value(bsm.CALL, 100.0, 100.0, 0.25, R, 0.0, sigma)
        self.assertTrue(math.isfinite(value.vega) and value.vega >= 0.0)

    def test_the_boundary_band_keeps_delta_and_blanks_gamma_and_theta(self) -> None:
        args = band_put()
        value = american.value(*args)
        self.assertTrue(value.near_boundary)
        self.assertEqual((american.QUALITY_NEAR_BOUNDARY, None, None), (value.quality, value.gamma, value.theta))
        self.assertEqual(lattice.american(*args).delta, value.delta)
        self.assertTrue(math.isfinite(value.vega) and math.isfinite(value.rho))
        scaled = value.position_greeks(-2)
        self.assertEqual((None, None), (scaled["gamma"], scaled["theta"]))
        self.assertAlmostEqual(-200.0 * value.delta, scaled["delta"])
        self.assertEqual(value.iv, scaled["iv"])
        self.assertEqual(american.QUALITY_NEAR_BOUNDARY, value.as_dict()["quality"])

    def test_a_put_exercised_now_carries_the_payoff_s_greeks(self) -> None:
        value = american.value(bsm.PUT, 50.0, 100.0, 0.25, R, 0.0, 0.25)
        self.assertTrue(value.exercise_now)
        self.assertEqual((50.0, -1.0, 0.0, 0.0, 0.0, 0.0),
                         (value.price, value.delta, value.gamma, value.theta, value.vega, value.rho))
        self.assertEqual(american.QUALITY_OK, value.quality)

    def test_an_expired_leg(self) -> None:
        value = american.value(bsm.CALL, 110.0, 100.0, 0.0, R, Q, 0.3)
        self.assertTrue(value.expired)
        self.assertEqual((10.0, 1.0, 0.0, 0.0, 0.0, 0.0),
                         (value.price, value.delta, value.gamma, value.theta, value.vega, value.rho))

    def test_position_greeks_scale_by_quantity_and_multiplier(self) -> None:
        value = american.value(bsm.CALL, 100.0, 105.0, 0.25, R, Q, 0.3)
        scaled = value.position_greeks(3)
        for name in ("delta", "gamma", "theta", "vega", "rho"):
            self.assertAlmostEqual(300.0 * value.greeks()[name], scaled[name])

    def test_input_errors_raise_as_in_bsm(self) -> None:
        for bad in ((bsm.CALL, 0.0, 100.0, 0.25, R, Q, 0.3), ("call", 100.0, 100.0, 0.25, R, Q, 0.3),
                    (bsm.CALL, 100.0, 100.0, 0.25, R, Q, 0.0)):
            with self.assertRaises(ValueError):
                american.value(*bad)


class ImpliedVolTest(unittest.TestCase):
    def test_the_lattice_price_inverts_to_its_volatility(self) -> None:
        for right, moneyness, tau, sigma in GRID:
            args = (right, 100.0, 100.0 * moneyness, tau, R, Q)
            value = american.value(*args, sigma)
            if value.near_boundary or value.exercise_now or value.vega < 1e-3:
                continue  # in the band the price barely moves with volatility; covered below
            with self.subTest(right=right, moneyness=moneyness, tau=tau, sigma=sigma):
                result = american.implied_vol(value.price, *args)
                self.assertEqual(iv.STATUS_OK, result.status)
                assert result.sigma is not None
                self.assertAlmostEqual(sigma, result.sigma, delta=1e-6)
                self.assertLessEqual(result.iterations, 30)

    def test_never_above_the_european_implied_volatility(self) -> None:
        for right, moneyness, tau, sigma in GRID:
            price = american.value(right, 100.0, 100.0 * moneyness, tau, R, Q, sigma).price
            ours = american.implied_vol(price, right, 100.0, 100.0 * moneyness, tau, R, Q)
            theirs = iv.implied_vol(price, right, 100.0, 100.0 * moneyness, tau, R, Q)
            if ours.status == iv.STATUS_OK and theirs.status == iv.STATUS_OK:
                assert ours.sigma is not None and theirs.sigma is not None
                self.assertLessEqual(ours.sigma, theirs.sigma + 1e-9)

    def test_a_call_without_early_exercise_takes_the_european_volatility_in_one_price(self) -> None:
        price = bsm.price(bsm.CALL, 100.0, 105.0, 0.25, R, 0.0, 0.3)
        result = american.implied_vol(price, bsm.CALL, 100.0, 105.0, 0.25, R, 0.0)
        self.assertEqual((iv.STATUS_OK, 1), (result.status, result.iterations))
        assert result.sigma is not None
        self.assertAlmostEqual(0.3, result.sigma, places=9)

    def test_a_band_quote_above_intrinsic_solves(self) -> None:
        args = band_put()
        price = american.value(*args).price
        if price > bsm.intrinsic(bsm.PUT, args[1], args[2]) + 1e-6:
            result = american.implied_vol(price, *args[:6])
            self.assertEqual(iv.STATUS_OK, result.status)
            assert result.sigma is not None
            self.assertAlmostEqual(price, american.value(*args[:6], result.sigma).price, delta=1e-6)

    def test_the_bounds(self) -> None:
        # a put at 70 with strike 100: intrinsic 30, the upper bound the strike
        self.assertEqual((30.0, 100.0), american.price_bounds(bsm.PUT, 70.0, 100.0, 0.25, R, Q))
        self.assertEqual(iv.STATUS_BELOW_INTRINSIC, american.implied_vol(29.9, bsm.PUT, 70.0, 100.0, 0.25, R, Q).status)
        self.assertEqual(iv.STATUS_NO_ROOT, american.implied_vol(30.0, bsm.PUT, 70.0, 100.0, 0.25, R, Q).status)
        self.assertEqual(iv.STATUS_ABOVE_BOUND, american.implied_vol(100.5, bsm.PUT, 70.0, 100.0, 0.25, R, Q).status)
        self.assertEqual(iv.STATUS_NO_ROOT, american.implied_vol(100.0, bsm.CALL, 100.0, 90.0, 0.25, R, Q).status)
        self.assertEqual(iv.STATUS_EXPIRED, american.implied_vol(5.0, bsm.CALL, 100.0, 95.0, 0.0, R, Q).status)
        # A European put's upper bound is K e^(-r tau); an American put may be quoted above it.
        quote = 100.0 * math.exp(-R * 0.25) + 0.05
        self.assertEqual(iv.STATUS_ABOVE_BOUND, iv.implied_vol(quote, bsm.PUT, 2.0, 100.0, 0.25, R, Q).status)
        self.assertNotEqual(iv.STATUS_ABOVE_BOUND, american.implied_vol(quote, bsm.PUT, 2.0, 100.0, 0.25, R, Q).status)

    def test_a_quote_below_intrinsic_has_no_volatility(self) -> None:
        # A deep in-the-money put's mid a cent under intrinsic: the European solver may still solve it, the American
        # model never does (the owner's decision: that leg is UNAVAILABLE).
        result = american.implied_vol(19.99, bsm.PUT, 80.0, 100.0, 0.3, R, Q)
        self.assertEqual((iv.STATUS_BELOW_INTRINSIC, None), (result.status, result.sigma))


class BandToleranceTest(unittest.TestCase):
    def test_the_tolerance_and_its_headroom_over_the_measured_worst(self) -> None:
        self.assertEqual(0.05, lattice.BAND_DELTA_TOL)
        self.assertGreater(lattice.BAND_DELTA_TOL, 1.5 * 0.028)


if __name__ == "__main__":
    unittest.main()
