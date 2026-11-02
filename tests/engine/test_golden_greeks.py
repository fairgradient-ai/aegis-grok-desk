"""Golden tests: analytic Greeks at the golden point in desk units, plus a 60-digit Decimal oracle.

Sources: Aegis ``services/quant-engine/tests/test_golden_greeks.py`` (FairGradient). The call-side
literals are independently sourced there (py_vollib analytical BSM Greeks) and the put delta is
pinned; tolerance rel 1e-9 / abs 1e-9 with ``pytest.approx`` semantics, copied unchanged. The
remaining put Greeks were not pinned in Aegis (QuantLib covered them); here they are minted from
the ``DecimalOracleTest`` below, an independent evaluation of the closed form in 60-digit
``decimal`` arithmetic with a Taylor-series error function, and cross-checked again by QuantLib in
``tests/oracle/test_quantlib_oracle.py`` when it is installed.

Desk units: delta per $1, gamma per $1^2, theta per calendar day, vega per vol point (1 %), rho per
1 % of rate (q held fixed).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import math
import unittest
from decimal import Decimal, getcontext

from aegis_desk import bsm, greeks
from aegis_desk.units import CONTRACT_MULTIPLIER, DAYS_PER_YEAR, RATE_POINT, VOL_POINT

_GOLDEN = {"S": 162.06, "K": 170.163, "tau": 1.553, "r": 0.05, "q": 0.052, "sigma": 0.226}

# Aegis test_golden_greeks.py: _GOLDEN_CALL_GREEKS (py_vollib) and _GOLDEN_PUT_DELTA.
_GOLDEN_CALL_GREEKS = {
    "delta": 0.44522724413493947,
    "gamma": 0.008054866254209737,
    "theta": -0.012557555150677538,
    "vega": 0.7424887840855364,
    "rho": 0.9109788661062801,
}
_GOLDEN_PUT_DELTA = -0.4771914900244757

# Put-side literals minted 2026-11-02 from the 60-digit Decimal oracle in this module (relative
# agreement with the float engine 1.2e-16 .. 2.2e-16). Gamma and vega are the call's by identity.
_GOLDEN_PUT_GREEKS = {
    "delta": _GOLDEN_PUT_DELTA,
    "gamma": 0.008054866254209737,
    "theta": -0.01228589045918578,
    "vega": 0.7424887840855364,
    "rho": -1.5342168308541975,
}

_REL = 1e-9
_ABS = 1e-9


def _approx(actual: float, expected: float, rel: float = _REL, abs_: float = _ABS) -> bool:
    """``pytest.approx`` semantics: ``|actual - expected| <= max(rel * |expected|, abs)``."""
    return abs(actual - expected) <= max(rel * abs(expected), abs_)


class GoldenGreeksTest(unittest.TestCase):
    """Aegis test_golden_greeks.py, plus the minted put side."""

    def test_golden_call_greeks(self) -> None:
        g = greeks.european_greeks(bsm.CALL, **_GOLDEN)
        for name, expected in _GOLDEN_CALL_GREEKS.items():
            with self.subTest(greek=name):
                actual = getattr(g, name)
                self.assertTrue(
                    _approx(actual, expected), "%s: %r != %r" % (name, actual, expected)
                )

    def test_golden_put_delta(self) -> None:
        g = greeks.european_greeks(bsm.PUT, **_GOLDEN)
        self.assertTrue(_approx(g.delta, _GOLDEN_PUT_DELTA), repr(g.delta))

    def test_golden_put_greeks(self) -> None:
        g = greeks.european_greeks(bsm.PUT, **_GOLDEN)
        for name, expected in _GOLDEN_PUT_GREEKS.items():
            with self.subTest(greek=name):
                actual = getattr(g, name)
                self.assertTrue(
                    _approx(actual, expected), "%s: %r != %r" % (name, actual, expected)
                )

    def test_golden_greeks_at_ulp_level(self) -> None:
        # Every literal reproduces bit-for-bit today; 1e-13 relative leaves room for libm variance.
        for right, table in ((bsm.CALL, _GOLDEN_CALL_GREEKS), (bsm.PUT, _GOLDEN_PUT_GREEKS)):
            g = greeks.european_greeks(right, **_GOLDEN)
            for name, expected in table.items():
                with self.subTest(right=right, greek=name):
                    self.assertTrue(_approx(getattr(g, name), expected, rel=1e-13, abs_=0.0))

    def test_iv_field_echoes_input_vol(self) -> None:
        g = greeks.european_greeks(bsm.CALL, **_GOLDEN)
        self.assertEqual(g.iv, _GOLDEN["sigma"])

    def test_status_ok_at_golden_point(self) -> None:
        for right in bsm.RIGHTS:
            g = greeks.european_greeks(right, **_GOLDEN)
            self.assertEqual(g.status, bsm.STATUS_OK)
            self.assertFalse(g.expired)

    def test_call_put_gamma_and_vega_identical(self) -> None:
        gc = greeks.european_greeks(bsm.CALL, **_GOLDEN)
        gp = greeks.european_greeks(bsm.PUT, **_GOLDEN)
        self.assertEqual(gc.gamma, gp.gamma)
        self.assertEqual(gc.vega, gp.vega)


class UnitConversionTest(unittest.TestCase):
    """Desk units are the natural units of ``bsm`` converted exactly once (units.py constants)."""

    def test_desk_units_are_converted_natural_units(self) -> None:
        for right in bsm.RIGHTS:
            with self.subTest(right=right):
                natural = greeks.natural_greeks(right, **_GOLDEN)
                desk = greeks.european_greeks(right, **_GOLDEN)
                self.assertEqual(greeks.to_desk_units(natural), desk)
                self.assertEqual(desk.delta, natural.delta)
                self.assertEqual(desk.gamma, natural.gamma)
                self.assertEqual(desk.theta, natural.theta_per_year / DAYS_PER_YEAR)
                self.assertEqual(desk.vega, natural.vega_per_unit * VOL_POINT)
                self.assertEqual(desk.rho, natural.rho_per_unit * RATE_POINT)
                self.assertEqual(desk.iv, natural.iv)
                self.assertEqual(desk.status, natural.status)

    def test_natural_units_match_the_bsm_functions(self) -> None:
        for right in bsm.RIGHTS:
            with self.subTest(right=right):
                natural = greeks.natural_greeks(right, **_GOLDEN)
                self.assertEqual(natural.delta, bsm.delta(right, **_GOLDEN))
                self.assertEqual(natural.gamma, bsm.gamma(right, **_GOLDEN))
                self.assertEqual(natural.theta_per_year, bsm.theta(right, **_GOLDEN))
                self.assertEqual(natural.vega_per_unit, bsm.vega(right, **_GOLDEN))
                self.assertEqual(natural.rho_per_unit, bsm.rho(right, **_GOLDEN))

    def test_unit_constants_are_the_desk_standard(self) -> None:
        self.assertEqual(DAYS_PER_YEAR, 365.0)
        self.assertEqual(VOL_POINT, 0.01)
        self.assertEqual(RATE_POINT, 0.01)
        self.assertEqual(CONTRACT_MULTIPLIER, 100)

    def test_abs_delta_is_unsigned_and_delta_stays_signed(self) -> None:
        gp = greeks.european_greeks(bsm.PUT, **_GOLDEN)
        self.assertLess(gp.delta, 0.0)
        self.assertEqual(greeks.abs_delta(gp.delta), -gp.delta)
        self.assertEqual(greeks.abs_delta(0.3), 0.3)
        self.assertEqual(greeks.abs_delta(Decimal("-0.5")), 0.5)
        with self.assertRaises(ValueError):
            greeks.abs_delta(float("nan"))

    def test_position_greeks_scale_by_signed_quantity_and_multiplier(self) -> None:
        per_share = greeks.european_greeks(bsm.PUT, **_GOLDEN)
        short_three = greeks.position_greeks(per_share, -3)
        for name in ("delta", "gamma", "theta", "vega", "rho"):
            with self.subTest(greek=name):
                self.assertEqual(getattr(short_three, name), getattr(per_share, name) * -300.0)
        self.assertGreater(short_three.delta, 0.0)  # short puts are long delta
        self.assertGreater(short_three.theta, 0.0)  # short premium collects decay
        self.assertEqual(short_three.iv, per_share.iv)
        self.assertEqual(short_three.status, per_share.status)
        mini = greeks.position_greeks(per_share, Decimal("2"), multiplier=10)
        self.assertEqual(mini.delta, per_share.delta * 20.0)
        with self.assertRaises(ValueError):
            greeks.position_greeks(per_share, float("inf"))


class ExpiryConventionTest(unittest.TestCase):
    """``tau <= 0``: delta 0/1 (call) or 0/-1 (put), every other Greek 0.0, status ``expired``."""

    def test_expired_call_and_put_conventions(self) -> None:
        cases = [
            (bsm.CALL, 105.0, 1.0),
            (bsm.CALL, 100.0, 0.0),  # exactly at the money: not auto-exercised
            (bsm.CALL, 95.0, 0.0),
            (bsm.PUT, 95.0, -1.0),
            (bsm.PUT, 100.0, 0.0),
            (bsm.PUT, 105.0, 0.0),
        ]
        for tau in (0.0, -1e-12, -3.0):
            for right, spot, expected_delta in cases:
                with self.subTest(tau=tau, right=right, S=spot):
                    g = greeks.european_greeks(right, spot, 100.0, tau, 0.05, 0.01, 0.25)
                    self.assertEqual(g.status, bsm.STATUS_EXPIRED)
                    self.assertTrue(g.expired)
                    self.assertEqual(g.delta, expected_delta)
                    self.assertEqual((g.gamma, g.theta, g.vega, g.rho), (0.0, 0.0, 0.0, 0.0))
                    self.assertEqual(g.iv, 0.25)
                    natural = greeks.natural_greeks(right, spot, 100.0, tau, 0.05, 0.01, 0.25)
                    self.assertEqual(natural.status, bsm.STATUS_EXPIRED)
                    self.assertEqual(natural.delta, expected_delta)

    def test_expired_inputs_are_still_validated(self) -> None:
        with self.assertRaises(ValueError):
            greeks.european_greeks(bsm.CALL, 100.0, 100.0, 0.0, 0.05, 0.0, 0.0)  # sigma 0
        with self.assertRaises(ValueError):
            greeks.european_greeks("call", 100.0, 100.0, 0.0, 0.05, 0.0, 0.2)
        with self.assertRaises(ValueError):
            greeks.european_greeks(bsm.PUT, -1.0, 100.0, 0.0, 0.05, 0.0, 0.2)


# ---------------------------------------------------------------------------------------------
# Independent oracle: the closed form in 60-digit Decimal arithmetic.
# ---------------------------------------------------------------------------------------------

_PI = Decimal("3.14159265358979323846264338327950288419716939937510582097494459")


def _d_erf(x: Decimal) -> Decimal:
    """erf by its Maclaurin series, 2/sqrt(pi) * sum (-1)^n x^(2n+1) / (n! (2n+1)).

    At 60 digits the alternating series is accurate to ~1e-50 for |x| <= 4 (the largest term is
    about e^{x^2} / sqrt(x^2), 1e7 at x = 4, so cancellation costs at most eight digits).
    """
    total = Decimal(0)
    term = x
    n = 0
    while True:
        contribution = term / (2 * n + 1)
        total += contribution
        if abs(contribution) < Decimal(10) ** -58:
            break
        n += 1
        term = -term * x * x / n
    return 2 / _PI.sqrt() * total


def _d_cdf(x: Decimal) -> Decimal:
    return (1 + _d_erf(x / Decimal(2).sqrt())) / 2


def _d_pdf(x: Decimal) -> Decimal:
    return (-(x * x) / 2).exp() / (2 * _PI).sqrt()


def _decimal_bsm(right: str, market: dict) -> dict:
    """Price and desk-unit Greeks from the formulas, in Decimal (float inputs via repr)."""
    S, K, tau, r, q, sigma = (
        Decimal(repr(market[k])) for k in ("S", "K", "tau", "r", "q", "sigma")
    )
    sqrt_tau = tau.sqrt()
    vol_t = sigma * sqrt_tau
    d1 = ((S / K).ln() + (r - q + sigma * sigma / 2) * tau) / vol_t
    d2 = d1 - vol_t
    carry = (-q * tau).exp()
    disc = (-r * tau).exp()
    bleed = -S * carry * _d_pdf(d1) * sigma / (2 * sqrt_tau)
    if right == bsm.CALL:
        value = S * carry * _d_cdf(d1) - K * disc * _d_cdf(d2)
        delta = carry * _d_cdf(d1)
        theta = bleed + q * S * carry * _d_cdf(d1) - r * K * disc * _d_cdf(d2)
        rho = K * tau * disc * _d_cdf(d2)
    else:
        value = K * disc * _d_cdf(-d2) - S * carry * _d_cdf(-d1)
        delta = -carry * _d_cdf(-d1)
        theta = bleed - q * S * carry * _d_cdf(-d1) + r * K * disc * _d_cdf(-d2)
        rho = -K * tau * disc * _d_cdf(-d2)
    gamma = carry * _d_pdf(d1) / (S * vol_t)
    vega = S * carry * _d_pdf(d1) * sqrt_tau
    return {
        "price": value,
        "delta": delta,
        "gamma": gamma,
        "theta": theta / 365,
        "vega": vega / 100,
        "rho": rho / 100,
    }


class DecimalOracleTest(unittest.TestCase):
    """Float engine vs the 60-digit Decimal evaluation: an independent method, no QuantLib needed.

    1e-13 relative is 1000x the double-precision noise floor and 10,000x tighter than the golden
    contract; the points keep |d1|, |d2| < 4 so the series oracle itself is exact to ~50 digits.
    """

    POINTS = [
        _GOLDEN,
        {"S": 100.0, "K": 120.0, "tau": 0.25, "r": -0.01, "q": 0.03, "sigma": 0.45},  # OTM call
        {"S": 250.0, "K": 180.0, "tau": 3.0, "r": 0.08, "q": 0.0, "sigma": 0.12},  # deep ITM call
        {"S": 42.5, "K": 40.0, "tau": 0.08, "r": 0.045, "q": 0.015, "sigma": 0.9},  # short-dated
    ]

    def setUp(self) -> None:
        self._previous_prec = getcontext().prec
        getcontext().prec = 60

    def tearDown(self) -> None:
        getcontext().prec = self._previous_prec

    def test_price_and_greeks_match_decimal_oracle(self) -> None:
        for market in self.POINTS:
            for right in bsm.RIGHTS:
                reference = _decimal_bsm(right, market)
                g = greeks.european_greeks(right, **market)
                ours = {
                    "price": bsm.price(right, **market),
                    "delta": g.delta,
                    "gamma": g.gamma,
                    "theta": g.theta,
                    "vega": g.vega,
                    "rho": g.rho,
                }
                for name, value in ours.items():
                    expected = float(reference[name])
                    with self.subTest(right=right, S=market["S"], K=market["K"], value=name):
                        self.assertTrue(
                            _approx(value, expected, rel=1e-13, abs_=1e-15),
                            "%s %s: %r vs %s" % (right, name, value, str(reference[name])[:22]),
                        )

    def test_decimal_oracle_reproduces_the_thales_literals(self) -> None:
        # The oracle is itself validated against the independent Thales/py_vollib literals.
        call = _decimal_bsm(bsm.CALL, _GOLDEN)
        put = _decimal_bsm(bsm.PUT, _GOLDEN)
        self.assertTrue(_approx(float(call["price"]), 13.494231234329277, rel=1e-13, abs_=0.0))
        self.assertTrue(_approx(float(put["price"]), 21.456870684534124, rel=1e-13, abs_=0.0))
        for name, expected in _GOLDEN_CALL_GREEKS.items():
            with self.subTest(greek=name):
                self.assertTrue(_approx(float(call[name]), expected, rel=1e-13, abs_=0.0))
        self.assertTrue(_approx(float(put["delta"]), _GOLDEN_PUT_DELTA, rel=1e-13, abs_=0.0))

    def test_series_erf_matches_math_erf(self) -> None:
        for x in (-3.5, -1.0, -0.25, 0.0, 0.5, 2.0, 3.9):
            with self.subTest(x=x):
                series = float(_d_erf(Decimal(repr(x))))
                self.assertTrue(_approx(series, math.erf(x), rel=1e-14, abs_=1e-16))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
