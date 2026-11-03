"""QuantLib cross-check: the stdlib engine against QuantLib's analytic European engine.

Runs only where ``QuantLib`` (the Python bindings) is importable: the CI oracle job installs it;
everywhere else this module is SKIPPED, not failed. Source of the method: Aegis
``services/quant-engine/src/aegis_quant/oracle.py`` and ``tests/test_quantlib_crosscheck.py``
(FairGradient). Two QuantLib paths are used:

* the tau-native ``BlackCalculator`` (forward, total standard deviation, discount) for price,
  delta, gamma, vega and theta-per-day at fractional maturities, including the golden point;
* the date-based ``AnalyticEuropeanEngine`` on a ``BlackScholesMertonProcess`` for every Greek
  INCLUDING rho. It measures time in whole days (Actual/365 Fixed), so the grid uses
  ``tau = days / 365`` exactly and the comparison is apples to apples.

Tolerances (plan §9): price within 1e-8 relative, Greeks within 1e-6 relative after conversion to
the same units (QuantLib theta per year / 365, vega and rho x 0.01), each with a tiny absolute
floor for far out-of-the-money values whose magnitude is below 1e-9 per share. QuantLib's normal
CDF switches to an asymptotic expansion below 1e-8 and uses the ``erf`` form above it, so a
relative comparison on tail-dominated prices is bounded by that implementation, not by ours; the
grid keeps |d| modest and the absolute floor covers the rest. Non-circular: QuantLib brings its
own CDF, its own formulas and its own solver.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import math
import unittest

from aegis_desk import bsm, greeks, iv
from aegis_desk.units import DAYS_PER_YEAR, RATE_POINT, VOL_POINT

try:  # pragma: no cover - exercised only on the CI oracle job
    import QuantLib as ql

    HAVE_QUANTLIB = True
except ImportError:  # pragma: no cover
    ql = None
    HAVE_QUANTLIB = False

_GOLDEN = {"S": 162.06, "K": 170.163, "tau": 1.553, "r": 0.05, "q": 0.052, "sigma": 0.226}

PRICE_REL, PRICE_ABS = 1e-8, 1e-10
GREEK_REL, GREEK_ABS = 1e-6, 1e-9

# Whole-day maturities keep the date-based engine exact. The grid deliberately reaches into the
# normal tail: on the sigma = 0.1 / 30-day / moneyness 0.7 corner |d1| and |d2| reach about 12.7
# (ln(1/0.7) / (0.1 * sqrt(30/365)) is 12.4 before the drift term), where N(-d) is ~1e-37 and the
# out-of-the-money price and most Greeks are pure tail values. Those points are compared under
# the absolute floors PRICE_ABS / GREEK_ABS, which is the intended contract: the relative
# tolerances govern the body of the distribution (|d| below ~4 on the rest of the grid), the
# absolute floors govern values that are numerically zero in both engines.
_SPOTS = (50.0, 100.0, 250.0)
_MONEYNESS = (0.7, 0.85, 1.0, 1.15, 1.4)
_DAYS = (30, 91, 365, 730)
_RATES = (-0.01, 0.03, 0.08)
_DIVIDENDS = (0.0, 0.025)
_SIGMAS = (0.1, 0.3, 0.8)


def _grid():
    for S in _SPOTS:
        for moneyness in _MONEYNESS:
            for days in _DAYS:
                for r in _RATES:
                    for q in _DIVIDENDS:
                        for sigma in _SIGMAS:
                            yield {
                                "S": S,
                                "K": S * moneyness,
                                "tau": days / DAYS_PER_YEAR,
                                "r": r,
                                "q": q,
                                "sigma": sigma,
                                "days": days,
                            }


def _approx(actual: float, expected: float, rel: float, abs_: float) -> bool:
    """``pytest.approx`` semantics: ``|actual - expected| <= max(rel * |expected|, abs)``."""
    return abs(actual - expected) <= max(rel * abs(expected), abs_)


def _ql_type(right: str):
    return ql.Option.Call if right == bsm.CALL else ql.Option.Put


def _black_calculator(right: str, m: dict):
    """Tau-native BlackCalculator(payoff, forward, stddev, discount)."""
    forward = m["S"] * math.exp((m["r"] - m["q"]) * m["tau"])
    discount = math.exp(-m["r"] * m["tau"])
    stddev = m["sigma"] * math.sqrt(m["tau"])
    payoff = ql.PlainVanillaPayoff(_ql_type(right), m["K"])
    return ql.BlackCalculator(payoff, forward, stddev, discount)


def _date_engine(right: str, m: dict, days: int) -> dict:
    """Date-based AnalyticEuropeanEngine results in desk units; restores the evaluation date."""
    today = ql.Date(1, 1, 2020)  # flat curves are time-homogeneous: only expiry - today matters
    settings = ql.Settings.instance()
    previous = settings.evaluationDate
    try:
        settings.evaluationDate = today
        day_count = ql.Actual365Fixed()
        expiry = today + int(days)
        spot = ql.QuoteHandle(ql.SimpleQuote(m["S"]))
        r_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, m["r"], day_count))
        q_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, m["q"], day_count))
        vol_ts = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(today, ql.NullCalendar(), m["sigma"], day_count)
        )
        process = ql.BlackScholesMertonProcess(spot, q_ts, r_ts, vol_ts)
        option = ql.VanillaOption(
            ql.PlainVanillaPayoff(_ql_type(right), m["K"]), ql.EuropeanExercise(expiry)
        )
        option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
        return {
            "price": float(option.NPV()),
            "delta": float(option.delta()),
            "gamma": float(option.gamma()),
            "theta": float(option.theta()) / DAYS_PER_YEAR,
            "vega": float(option.vega()) * VOL_POINT,
            "rho": float(option.rho()) * RATE_POINT,
            "theta_per_year": float(option.theta()),
            "vega_per_unit": float(option.vega()),
            "rho_per_unit": float(option.rho()),
            "option": option,
            "process": process,
        }
    finally:
        settings.evaluationDate = previous


@unittest.skipUnless(HAVE_QUANTLIB, "QuantLib (Python bindings) not installed; CI oracle job only")
class BlackCalculatorOracleTest(unittest.TestCase):
    """Tau-native comparison (fractional maturities allowed)."""

    def _check_point(self, right: str, m: dict) -> None:
        calc = _black_calculator(right, m)
        ours = bsm.price(right, **m)
        self.assertTrue(
            _approx(ours, float(calc.value()), PRICE_REL, PRICE_ABS), (right, m, ours, calc.value())
        )
        g = greeks.european_greeks(right, **m)
        natural = greeks.natural_greeks(right, **m)
        expected = {
            "delta": float(calc.delta(m["S"])),
            "gamma": float(calc.gamma(m["S"])),
            "vega": float(calc.vega(m["tau"])) * VOL_POINT,
            "theta": float(calc.thetaPerDay(m["S"], m["tau"])),
        }
        for name, value in expected.items():
            with self.subTest(right=right, greek=name, **{k: m[k] for k in ("S", "K", "tau")}):
                self.assertTrue(
                    _approx(getattr(g, name), value, GREEK_REL, GREEK_ABS),
                    (getattr(g, name), value),
                )
        # The natural-unit layer against QuantLib's per-year theta and per-1.00 vega.
        self.assertTrue(
            _approx(
                natural.theta_per_year, float(calc.theta(m["S"], m["tau"])), GREEK_REL, GREEK_ABS
            )
        )
        self.assertTrue(
            _approx(natural.vega_per_unit, float(calc.vega(m["tau"])), GREEK_REL, GREEK_ABS)
        )
        rho_fn = getattr(calc, "rho", None)  # exposed by newer bindings only
        if rho_fn is not None:
            self.assertTrue(
                _approx(natural.rho_per_unit, float(rho_fn(m["tau"])), GREEK_REL, GREEK_ABS)
            )

    def test_golden_point(self) -> None:
        for right in bsm.RIGHTS:
            self._check_point(right, _GOLDEN)

    def test_grid(self) -> None:
        count = 0
        for m in _grid():
            market = {k: m[k] for k in ("S", "K", "tau", "r", "q", "sigma")}
            for right in bsm.RIGHTS:
                self._check_point(right, market)
                count += 1
        self.assertEqual(
            count,
            2
            * len(_SPOTS)
            * len(_MONEYNESS)
            * len(_DAYS)
            * len(_RATES)
            * len(_DIVIDENDS)
            * len(_SIGMAS),
        )


@unittest.skipUnless(HAVE_QUANTLIB, "QuantLib (Python bindings) not installed; CI oracle job only")
class DateEngineOracleTest(unittest.TestCase):
    """Date-based AnalyticEuropeanEngine on whole-day maturities: every Greek including rho."""

    def test_grid_price_and_all_greeks(self) -> None:
        for m in _grid():
            market = {k: m[k] for k in ("S", "K", "tau", "r", "q", "sigma")}
            for right in bsm.RIGHTS:
                theirs = _date_engine(right, market, m["days"])
                ours_price = bsm.price(right, **market)
                g = greeks.european_greeks(right, **market)
                natural = greeks.natural_greeks(right, **market)
                label = (right, market)
                self.assertTrue(
                    _approx(ours_price, theirs["price"], PRICE_REL, PRICE_ABS),
                    (label, ours_price, theirs["price"]),
                )
                for name in ("delta", "gamma", "theta", "vega", "rho"):
                    with self.subTest(right=right, greek=name, S=m["S"], K=m["K"], days=m["days"]):
                        self.assertTrue(
                            _approx(getattr(g, name), theirs[name], GREEK_REL, GREEK_ABS),
                            (label, name, getattr(g, name), theirs[name]),
                        )
                # Natural units: QuantLib reports theta per year, vega and rho per 1.00.
                self.assertTrue(
                    _approx(natural.theta_per_year, theirs["theta_per_year"], GREEK_REL, GREEK_ABS)
                )
                self.assertTrue(
                    _approx(natural.vega_per_unit, theirs["vega_per_unit"], GREEK_REL, GREEK_ABS)
                )
                self.assertTrue(
                    _approx(natural.rho_per_unit, theirs["rho_per_unit"], GREEK_REL, GREEK_ABS)
                )

    def test_implied_vol_matches_quantlib_solver(self) -> None:
        # QuantLib inverts its own price with its own Brent on a volatility quote; agreement on
        # sigma to 1e-7 is a solver-independent confirmation of iv.implied_vol.
        for m in _grid():
            if m["days"] not in (91, 365) or m["sigma"] == 0.8:
                continue  # a representative subset keeps the oracle job fast
            market = {k: m[k] for k in ("S", "K", "tau", "r", "q", "sigma")}
            for right in bsm.RIGHTS:
                # Only vega-conditioned quotes identify a volatility from a double: QuantLib's
                # solver works to 1e-12 in PRICE, so sigma is resolved to 1e-12 / vega. A deep ITM
                # 10 %-vol 91-day call carries ~4e-13 of time value and no finite-precision IV
                # (our engine reports it as "no_root"); such points are excluded here.
                if greeks.natural_greeks(right, **market).vega_per_unit < 0.1:
                    continue
                theirs = _date_engine(right, market, m["days"])
                price = theirs["price"]
                ours = iv.implied_vol(price, right, m["S"], m["K"], m["tau"], m["r"], m["q"])
                self.assertEqual(ours.status, iv.STATUS_OK, (right, market, ours))
                settings = ql.Settings.instance()
                previous = settings.evaluationDate
                try:
                    settings.evaluationDate = ql.Date(1, 1, 2020)
                    ql_sigma = theirs["option"].impliedVolatility(
                        price, theirs["process"], 1e-12, 1000, 1e-6, 10.0
                    )
                finally:
                    settings.evaluationDate = previous
                self.assertTrue(
                    _approx(ours.sigma, float(ql_sigma), 0.0, 1e-7), (right, market, ours, ql_sigma)
                )
                self.assertTrue(_approx(ours.sigma, m["sigma"], 0.0, 1e-7), (right, market, ours))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
