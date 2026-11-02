"""Property tests on the pricing invariants with seeded standard-library sampling.

Port of Aegis ``tests/test_property_pricing.py`` and ``tests/test_property_greeks.py`` (Hypothesis)
to ``random.Random(20261102)``: every test re-seeds, so each one sees the same reproducible sample.
Domain identical to Aegis ``markets()``: spot 1..1000, strike = spot x moneyness in 0.3..3.0 (deep
OTM to deep ITM, no rejection sampling), tau 0.05..5 years, r -0.02..0.10, sigma 0.05..2.0,
q 0..0.08. Invariants: put-call parity, no-arbitrage bounds, monotonicity in S / sigma / tau,
Greek signs and bounds, the Greek parity relations, and central finite differences against every
analytic Greek with tolerances derived from the error analysis in the comments and a measured
survey of 3,000 draws (2026-11-02; worst observed errors quoted per test, margins >= 10x).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import math
import random
import unittest

from aegis_desk import bsm, greeks
from aegis_desk.units import DAYS_PER_YEAR, RATE_POINT, SECONDS_PER_YEAR, VOL_POINT

SEED = 20261102
N_DRAWS = 500


def _approx(actual: float, expected: float, rel: float, abs_: float) -> bool:
    """``pytest.approx`` semantics: ``|actual - expected| <= max(rel * |expected|, abs)``."""
    return abs(actual - expected) <= max(rel * abs(expected), abs_)


def _markets(n: int = N_DRAWS, q: float | None = None) -> list:
    """The Aegis ``markets()`` domain, drawn from a freshly seeded generator."""
    rng = random.Random(SEED)
    out = []
    for _ in range(n):
        spot = rng.uniform(1.0, 1000.0)
        strike = spot * rng.uniform(0.3, 3.0)
        tau = rng.uniform(0.05, 5.0)
        rate = rng.uniform(-0.02, 0.10)
        sigma = rng.uniform(0.05, 2.0)
        div = rng.uniform(0.0, 0.08) if q is None else q
        out.append({"S": spot, "K": strike, "tau": tau, "r": rate, "q": div, "sigma": sigma})
    return out


def _carry_spot(m: dict) -> float:
    return m["S"] * math.exp(-m["q"] * m["tau"])


def _disc_strike(m: dict) -> float:
    return m["K"] * math.exp(-m["r"] * m["tau"])


def _bumped(m: dict, **override: float) -> dict:
    out = dict(m)
    out.update(override)
    return out


class PricePropertyTest(unittest.TestCase):
    """Aegis test_property_pricing.py (parity, bounds, monotonicity)."""

    def test_put_call_parity(self) -> None:
        # C - P = S e^{-qT} - K e^{-rT}; worst observed residual 3e-14 relative.
        for m in _markets():
            c = bsm.price(bsm.CALL, **m)
            p = bsm.price(bsm.PUT, **m)
            self.assertTrue(
                _approx(c - p, _carry_spot(m) - _disc_strike(m), rel=1e-9, abs_=1e-9), m
            )

    def test_no_arbitrage_bounds(self) -> None:
        for m in _markets():
            c = bsm.price(bsm.CALL, **m)
            p = bsm.price(bsm.PUT, **m)
            fs, ks = _carry_spot(m), _disc_strike(m)
            self.assertGreaterEqual(c, max(0.0, fs - ks) - 1e-9, m)
            self.assertLessEqual(c, fs + 1e-9, m)
            self.assertGreaterEqual(p, max(0.0, ks - fs) - 1e-9, m)
            self.assertLessEqual(p, ks + 1e-9, m)

    def test_call_increasing_and_put_decreasing_in_spot(self) -> None:
        for m in _markets():
            up = _bumped(m, S=m["S"] * (1.0 + 1e-3))
            self.assertGreaterEqual(bsm.price(bsm.CALL, **up), bsm.price(bsm.CALL, **m) - 1e-9, m)
            self.assertLessEqual(bsm.price(bsm.PUT, **up), bsm.price(bsm.PUT, **m) + 1e-9, m)

    def test_call_and_put_increasing_in_sigma(self) -> None:
        for m in _markets():
            up = _bumped(m, sigma=m["sigma"] + 1e-3)
            for right in bsm.RIGHTS:
                self.assertGreaterEqual(
                    bsm.price(right, **up), bsm.price(right, **m) - 1e-9, (right, m)
                )

    def test_call_increasing_in_tau_without_dividend_and_nonnegative_rate(self) -> None:
        # A European call is monotone in maturity only when q = 0 and r >= 0 (carry b = r >= 0);
        # a negative rate can invert it, so the invariant is scoped to that regime (Aegis note).
        checked = 0
        for m in _markets(q=0.0):
            if m["r"] < 0.0 or m["tau"] >= 4.99:
                continue
            checked += 1
            up = _bumped(m, tau=m["tau"] + 1e-3)
            self.assertGreaterEqual(bsm.price(bsm.CALL, **up), bsm.price(bsm.CALL, **m) - 1e-9, m)
        self.assertGreater(checked, N_DRAWS // 2)

    def test_price_result_is_consistent_with_price(self) -> None:
        for m in _markets(100):
            for right in bsm.RIGHTS:
                res = bsm.price_result(right, **m)
                self.assertEqual(res.value, bsm.price(right, **m))
                self.assertEqual(res.status, bsm.STATUS_OK)
                self.assertEqual(res.intrinsic, bsm.intrinsic(right, m["S"], m["K"]))
                self.assertTrue(
                    _approx(res.time_value, res.value - res.intrinsic, rel=0.0, abs_=1e-12)
                )


class GreekPropertyTest(unittest.TestCase):
    """Aegis test_property_greeks.py: signs, bounds and parity relations in desk units."""

    def test_greek_signs_and_bounds(self) -> None:
        eps = 1e-12
        for m in _markets():
            gc = greeks.european_greeks(bsm.CALL, **m)
            gp = greeks.european_greeks(bsm.PUT, **m)
            # delta: call in [0, 1], put in [-1, 0] (q >= 0 keeps e^{-qT} <= 1)
            self.assertGreaterEqual(gc.delta, -eps, m)
            self.assertLessEqual(gc.delta, 1.0 + eps, m)
            self.assertGreaterEqual(gp.delta, -1.0 - eps, m)
            self.assertLessEqual(gp.delta, eps, m)
            # gamma, vega >= 0 and identical for call and put
            self.assertGreaterEqual(gc.gamma, -eps, m)
            self.assertGreaterEqual(gc.vega, -eps, m)
            self.assertTrue(_approx(gc.gamma, gp.gamma, rel=1e-12, abs_=1e-15), m)
            self.assertTrue(_approx(gc.vega, gp.vega, rel=1e-12, abs_=1e-15), m)
            # theta finite and of sane magnitude (per-day decay below the option value + 1)
            price = bsm.price(bsm.CALL, **m)
            self.assertTrue(math.isfinite(gc.theta), m)
            self.assertLess(abs(gc.theta), price + 1.0, m)
            for g in (gc, gp):
                self.assertEqual(g.status, bsm.STATUS_OK)
                for value in (g.delta, g.gamma, g.theta, g.vega, g.rho):
                    self.assertTrue(math.isfinite(value), m)

    def test_delta_parity(self) -> None:
        # delta_call - delta_put = e^{-qT}
        for m in _markets():
            gc = greeks.european_greeks(bsm.CALL, **m)
            gp = greeks.european_greeks(bsm.PUT, **m)
            self.assertTrue(
                _approx(gc.delta - gp.delta, math.exp(-m["q"] * m["tau"]), rel=1e-9, abs_=1e-9), m
            )

    def test_theta_and_rho_parity(self) -> None:
        # Differentiating C - P = S e^{-q tau} - K e^{-r tau} with respect to calendar time t
        # (d tau / dt = -1): theta_c - theta_p = q S e^{-q tau} - r K e^{-r tau} per year;
        # with respect to r (q fixed): rho_c - rho_p = K tau e^{-r tau} per 1.00 of rate.
        for m in _markets():
            gc = greeks.european_greeks(bsm.CALL, **m)
            gp = greeks.european_greeks(bsm.PUT, **m)
            theta_gap = (m["q"] * _carry_spot(m) - m["r"] * _disc_strike(m)) / DAYS_PER_YEAR
            rho_gap = m["K"] * m["tau"] * math.exp(-m["r"] * m["tau"]) * RATE_POINT
            self.assertTrue(_approx(gc.theta - gp.theta, theta_gap, rel=1e-9, abs_=1e-9), m)
            self.assertTrue(_approx(gc.rho - gp.rho, rho_gap, rel=1e-9, abs_=1e-9), m)

    def test_abs_delta_bounded_by_carry(self) -> None:
        for m in _markets(200):
            for right in bsm.RIGHTS:
                g = greeks.european_greeks(right, **m)
                self.assertLessEqual(
                    greeks.abs_delta(g.delta), math.exp(-m["q"] * m["tau"]) + 1e-12
                )


class FiniteDifferenceTest(unittest.TestCase):
    """Analytic Greeks against central finite differences of the price (both rights).

    A central difference of step h has truncation error h^2/6 * f''' (first derivative) or
    h^2/12 * f'''' (second derivative) and rounding error ~eps * |f| / h (first) or eps * |f| / h^2
    (second), eps = 2.2e-16. The steps below balance the two; the tolerances are the measured worst
    case over 3,000 domain draws (seed 20261102, both rights) with at least a 10x margin, as a
    relative bound where the Greek is large and an absolute floor where it is tiny (deep OTM).
    """

    def test_delta_matches_finite_difference(self) -> None:
        # h = 1e-5 S: truncation ~1e-10 S^2 V''' (V''' ~ gamma/S * |d1|/(sigma sqrt tau), at most
        # ~1e2 gamma/S in the sharp low-total-vol corner) and rounding ~1e-11 V/S. Measured worst:
        # abs 1.2e-9, rel 2.9e-8 (|delta| > 1e-3). Tolerance rel 1e-6 / abs 1e-7.
        for m in _markets():
            h = m["S"] * 1e-5
            for right in bsm.RIGHTS:
                fd = (
                    bsm.price(right, **_bumped(m, S=m["S"] + h))
                    - bsm.price(right, **_bumped(m, S=m["S"] - h))
                ) / (2.0 * h)
                analytic = greeks.european_greeks(right, **m).delta
                self.assertTrue(
                    _approx(analytic, fd, rel=1e-6, abs_=1e-7), (right, m, analytic, fd)
                )

    def test_gamma_matches_second_finite_difference(self) -> None:
        # Second central difference, h = 5e-4 S: truncation ~2e-8 S^2 V'''' and rounding
        # ~1e-9 V / S^2. Measured worst: abs 6.2e-8, rel 1.8e-5 (|gamma| > 1e-3).
        # Tolerance rel 2e-4 / abs 1e-6.
        for m in _markets():
            h = m["S"] * 5e-4
            for right in bsm.RIGHTS:
                fd = (
                    bsm.price(right, **_bumped(m, S=m["S"] + h))
                    - 2.0 * bsm.price(right, **m)
                    + bsm.price(right, **_bumped(m, S=m["S"] - h))
                ) / (h * h)
                analytic = greeks.european_greeks(right, **m).gamma
                self.assertTrue(
                    _approx(analytic, fd, rel=2e-4, abs_=1e-6), (right, m, analytic, fd)
                )

    def test_vega_matches_finite_difference(self) -> None:
        # h = 1e-5 in sigma; the analytic vega is per vol point, the difference per 1.00 of sigma,
        # so the difference is scaled by VOL_POINT. Measured worst: abs 2.9e-8, rel 7.4e-7.
        # Tolerance rel 1e-5 / abs 1e-6.
        h = 1e-5
        for m in _markets():
            for right in bsm.RIGHTS:
                fd_per_unit = (
                    bsm.price(right, **_bumped(m, sigma=m["sigma"] + h))
                    - bsm.price(right, **_bumped(m, sigma=m["sigma"] - h))
                ) / (2.0 * h)
                analytic = greeks.european_greeks(right, **m).vega
                self.assertTrue(
                    _approx(analytic, fd_per_unit * VOL_POINT, rel=1e-5, abs_=1e-6),
                    (right, m, analytic, fd_per_unit * VOL_POINT),
                )

    def test_theta_matches_finite_difference(self) -> None:
        # dV/dt = -dV/dtau; h = 1e-5 years in tau (tau >= 0.05 keeps tau - h > 0); analytic theta
        # is per calendar day, the difference per year, so divide by DAYS_PER_YEAR. Measured
        # worst: abs 8.5e-9, rel 5.5e-8. Tolerance rel 1e-6 / abs 1e-7.
        h = 1e-5
        for m in _markets():
            for right in bsm.RIGHTS:
                fd_per_year = -(
                    bsm.price(right, **_bumped(m, tau=m["tau"] + h))
                    - bsm.price(right, **_bumped(m, tau=m["tau"] - h))
                ) / (2.0 * h)
                analytic = greeks.european_greeks(right, **m).theta
                self.assertTrue(
                    _approx(analytic, fd_per_year / DAYS_PER_YEAR, rel=1e-6, abs_=1e-7),
                    (right, m, analytic, fd_per_year / DAYS_PER_YEAR),
                )

    def test_rho_matches_finite_difference_with_q_fixed(self) -> None:
        # h = 1e-5 in r with q passed separately, so the difference holds the dividend yield
        # fixed: this pins the q-fixed rho convention, not only the arithmetic. Analytic rho is
        # per 1 % (RATE_POINT). Measured worst: abs 1.5e-7, rel 3.0e-7.
        # Tolerance rel 1e-5 / abs 2e-6.
        h = 1e-5
        for m in _markets():
            for right in bsm.RIGHTS:
                fd_per_unit = (
                    bsm.price(right, **_bumped(m, r=m["r"] + h))
                    - bsm.price(right, **_bumped(m, r=m["r"] - h))
                ) / (2.0 * h)
                analytic = greeks.european_greeks(right, **m).rho
                self.assertTrue(
                    _approx(analytic, fd_per_unit * RATE_POINT, rel=1e-5, abs_=2e-6),
                    (right, m, analytic, fd_per_unit * RATE_POINT),
                )


class NearExpiryRobustnessTest(unittest.TestCase):
    """Minutes or seconds from the exercise cutoff the closed form stays finite and in bounds."""

    def test_tiny_tau_is_finite_and_within_bounds(self) -> None:
        for seconds in (3600.0, 60.0, 1.0):
            tau = seconds / SECONDS_PER_YEAR
            for spot in (80.0, 99.999, 100.0, 100.001, 125.0):
                for right in bsm.RIGHTS:
                    with self.subTest(seconds=seconds, S=spot, right=right):
                        m = {"S": spot, "K": 100.0, "tau": tau, "r": 0.05, "q": 0.01, "sigma": 0.3}
                        value = bsm.price(right, **m)
                        g = greeks.european_greeks(right, **m)
                        fs, ks = _carry_spot(m), _disc_strike(m)
                        lower = max(0.0, fs - ks) if right == bsm.CALL else max(0.0, ks - fs)
                        upper = fs if right == bsm.CALL else ks
                        self.assertGreaterEqual(value, lower - 1e-12)
                        self.assertLessEqual(value, upper + 1e-12)
                        for field in (g.delta, g.gamma, g.theta, g.vega, g.rho):
                            self.assertTrue(math.isfinite(field))
                        self.assertLessEqual(greeks.abs_delta(g.delta), 1.0 + 1e-12)
                        self.assertEqual(g.status, bsm.STATUS_OK)

    def test_price_converges_to_intrinsic_away_from_the_strike(self) -> None:
        tau = 1.0 / SECONDS_PER_YEAR
        self.assertTrue(
            _approx(bsm.price(bsm.CALL, 125.0, 100.0, tau, 0.05, 0.01, 0.3), 25.0, 1e-9, 1e-6)
        )
        self.assertTrue(
            _approx(bsm.price(bsm.PUT, 80.0, 100.0, tau, 0.05, 0.01, 0.3), 20.0, 1e-9, 1e-6)
        )
        self.assertLess(bsm.price(bsm.CALL, 80.0, 100.0, tau, 0.05, 0.01, 0.3), 1e-12)
        self.assertLess(bsm.price(bsm.PUT, 125.0, 100.0, tau, 0.05, 0.01, 0.3), 1e-12)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
