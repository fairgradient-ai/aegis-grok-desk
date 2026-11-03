"""Golden tests: European prices equal the Thales ``#Validated`` oracle literals (per share).

Source: Aegis ``services/quant-engine/tests/test_golden_price.py`` (FairGradient), itself ported
from the ``CFPricing.py`` doctests of thales-master. Every literal and the tolerance (rel 1e-9,
abs 1e-9 with ``pytest.approx`` semantics) are copied unchanged. The stdlib engine reproduces
seven of the ten literals bit-for-bit and the other three to 4 ULPs (about 3e-16 relative), so a
second test pins the literals at 1e-13 relative: ten thousand times tighter than the contract
tolerance and a thousand times looser than any libm ``erfc`` variance between platforms.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import math
import unittest

from aegis_desk import bsm

# The canonical golden point: Vanilla European call/put with S=162.06, K=170.163, T=1.553,
# r=0.05, sigma=0.226, q=0.052 (Thales ``#Validated`` doctest; Aegis conftest.GOLDEN_KWARGS).
_BASE = {"S": 162.06, "K": 170.163, "tau": 1.553, "r": 0.05, "q": 0.052, "sigma": 0.226}

# (override, expected): base case plus one-bump perturbations, from CFPricing.py doctests
# (Aegis test_golden_price.py _CALL_CASES / _PUT_CASES).
_CALL_CASES = [
    ({}, 13.494231234329277),
    ({"S": 162.061}, 13.494676465600833),
    ({"sigma": 0.227}, 13.568482403368577),
    ({"tau": 1.554}, 13.498813626920992),
    ({"r": 0.051}, 13.585513415062863),
]
_PUT_CASES = [
    ({}, 21.456870684534124),
    ({"S": 162.061}, 21.456393497071531),
    ({"sigma": 0.227}, 21.531121853573424),
    ({"tau": 1.554}, 21.461353914219274),
    ({"r": 0.051}, 21.303823066766583),
]

# Aegis tolerance: pytest.approx(expected, rel=1e-9, abs=1e-9).
_REL = 1e-9
_ABS = 1e-9


def _approx(actual: float, expected: float, rel: float = _REL, abs_: float = _ABS) -> bool:
    """``pytest.approx`` semantics: ``|actual - expected| <= max(rel * |expected|, abs)``."""
    return abs(actual - expected) <= max(rel * abs(expected), abs_)


def _market(override: dict) -> dict:
    market = dict(_BASE)
    market.update(override)
    return market


class GoldenPriceTest(unittest.TestCase):
    """Aegis test_golden_price.py: test_golden_call_price / test_golden_put_price."""

    def test_golden_call_prices(self) -> None:
        for override, expected in _CALL_CASES:
            with self.subTest(override=override):
                actual = bsm.price(bsm.CALL, **_market(override))
                self.assertTrue(
                    _approx(actual, expected), "call %r: %r != %r" % (override, actual, expected)
                )

    def test_golden_put_prices(self) -> None:
        for override, expected in _PUT_CASES:
            with self.subTest(override=override):
                actual = bsm.price(bsm.PUT, **_market(override))
                self.assertTrue(
                    _approx(actual, expected), "put %r: %r != %r" % (override, actual, expected)
                )

    def test_golden_prices_at_ulp_level(self) -> None:
        # Seven literals match exactly, the rest to 4 ULPs; 1e-13 relative leaves a 1000x
        # margin over libm differences while catching any formula or evaluation-order drift.
        for right, cases in ((bsm.CALL, _CALL_CASES), (bsm.PUT, _PUT_CASES)):
            for override, expected in cases:
                with self.subTest(right=right, override=override):
                    actual = bsm.price(right, **_market(override))
                    self.assertTrue(
                        _approx(actual, expected, rel=1e-13, abs_=0.0),
                        "%s %r: %r vs %r (%.1f ULPs)"
                        % (
                            right,
                            override,
                            actual,
                            expected,
                            (actual - expected) / math.ulp(expected),
                        ),
                    )

    def test_validated_call_literal_is_exact(self) -> None:
        # The literal of record (plan §3 / CFPricing ``#Validated``) is reproduced bit-for-bit.
        self.assertEqual(bsm.price(bsm.CALL, **_BASE), 13.494231234329277)
        self.assertEqual(bsm.price(bsm.PUT, **_BASE), 21.456870684534124)


class PriceResultTest(unittest.TestCase):
    """The status-carrying wrapper agrees with the float API and decomposes the value."""

    def test_price_result_matches_price_and_reports_ok(self) -> None:
        for right in bsm.RIGHTS:
            with self.subTest(right=right):
                result = bsm.price_result(right, **_BASE)
                self.assertEqual(result.status, bsm.STATUS_OK)
                self.assertEqual(result.value, bsm.price(right, **_BASE))
                self.assertEqual(result.intrinsic, bsm.intrinsic(right, _BASE["S"], _BASE["K"]))
                self.assertEqual(result.time_value, result.value - result.intrinsic)
                d1, d2 = bsm.d1_d2(
                    _BASE["S"], _BASE["K"], _BASE["tau"], _BASE["r"], _BASE["q"], _BASE["sigma"]
                )
                self.assertEqual((result.d1, result.d2), (d1, d2))

    def test_golden_point_intrinsic_and_time_value(self) -> None:
        # Call is out of the money at the golden point (S < K): intrinsic 0, all time value.
        call = bsm.price_result(bsm.CALL, **_BASE)
        self.assertEqual(call.intrinsic, 0.0)
        self.assertEqual(call.time_value, 13.494231234329277)
        # Put is in the money: intrinsic K - S, positive time value.
        put = bsm.price_result(bsm.PUT, **_BASE)
        self.assertTrue(_approx(put.intrinsic, 170.163 - 162.06, rel=1e-12, abs_=1e-12))
        self.assertGreater(put.time_value, 0.0)

    def test_put_call_parity_at_golden_point(self) -> None:
        # C - P = S e^{-qT} - K e^{-rT}; the two Thales literals satisfy it to ~1e-14.
        call, put = 13.494231234329277, 21.456870684534124
        rhs = _BASE["S"] * math.exp(-_BASE["q"] * _BASE["tau"]) - _BASE["K"] * math.exp(
            -_BASE["r"] * _BASE["tau"]
        )
        self.assertTrue(_approx(call - put, rhs, rel=1e-12, abs_=1e-12))


class ExpiryAndInputTest(unittest.TestCase):
    """The documented expiry convention and the programming-error contract of ``bsm.price``."""

    def test_expired_contract_prices_at_intrinsic(self) -> None:
        for tau in (0.0, -1e-9, -0.5):
            with self.subTest(tau=tau):
                self.assertEqual(bsm.price(bsm.CALL, 105.0, 100.0, tau, 0.05, 0.02, 0.3), 5.0)
                self.assertEqual(bsm.price(bsm.CALL, 95.0, 100.0, tau, 0.05, 0.02, 0.3), 0.0)
                self.assertEqual(bsm.price(bsm.PUT, 95.0, 100.0, tau, 0.05, 0.02, 0.3), 5.0)
                self.assertEqual(bsm.price(bsm.PUT, 105.0, 100.0, tau, 0.05, 0.02, 0.3), 0.0)
                self.assertEqual(bsm.price(bsm.PUT, 100.0, 100.0, tau, 0.05, 0.02, 0.3), 0.0)
                result = bsm.price_result(bsm.CALL, 105.0, 100.0, tau, 0.05, 0.02, 0.3)
                self.assertEqual(result.status, bsm.STATUS_EXPIRED)
                self.assertEqual(
                    (result.value, result.intrinsic, result.time_value), (5.0, 5.0, 0.0)
                )
                self.assertIsNone(result.d1)
                self.assertIsNone(result.d2)

    def test_expiry_status_flag(self) -> None:
        self.assertEqual(bsm.expiry_status(1e-9), bsm.STATUS_OK)
        self.assertEqual(bsm.expiry_status(0.0), bsm.STATUS_EXPIRED)
        self.assertEqual(bsm.expiry_status(-1.0), bsm.STATUS_EXPIRED)

    def test_input_errors_raise_value_error_with_message(self) -> None:
        good = dict(_BASE)
        bad_cases = [
            ("right", {"right": "Call"}, "CALL"),
            ("right", {"right": "C"}, "CALL"),
            ("S", {"S": 0.0}, "S (spot) must be > 0"),
            ("S", {"S": -1.0}, "S (spot) must be > 0"),
            ("K", {"K": 0.0}, "K (strike) must be > 0"),
            ("sigma", {"sigma": 0.0}, "sigma"),
            ("sigma", {"sigma": -0.2}, "sigma"),
            ("S nan", {"S": float("nan")}, "finite"),
            ("tau inf", {"tau": float("inf")}, "finite"),
            ("r str", {"r": "0.05"}, "real number"),
            ("q bool", {"q": True}, "real number"),
            ("K None", {"K": None}, "real number"),
        ]
        for label, override, fragment in bad_cases:
            with self.subTest(label=label):
                kwargs = {"right": bsm.CALL}
                kwargs.update(good)
                kwargs.update(override)
                with self.assertRaises(ValueError) as ctx:
                    bsm.price(**kwargs)
                self.assertIn(fragment, str(ctx.exception))

    def test_d1_d2_rejects_expired_contract(self) -> None:
        with self.assertRaises(ValueError):
            bsm.d1_d2(100.0, 100.0, 0.0, 0.05, 0.0, 0.2)

    def test_decimal_and_int_inputs_are_accepted(self) -> None:
        from decimal import Decimal

        exact = bsm.price(bsm.CALL, 100.0, 100.0, 1.0, 0.05, 0.0, 0.2)
        self.assertEqual(
            bsm.price(bsm.CALL, Decimal("100"), 100, 1, Decimal("0.05"), 0, 0.2), exact
        )

    def test_norm_cdf_matches_erf_definition_and_keeps_the_tail(self) -> None:
        # N(x) = 0.5 (1 + erf(x / sqrt 2)) where that form is accurate ...
        for x in (-3.0, -1.0, -0.3, 0.0, 0.3, 1.0, 3.0):
            with self.subTest(x=x):
                self.assertTrue(
                    _approx(
                        bsm.norm_cdf(x), 0.5 * (1.0 + math.erf(x / math.sqrt(2.0))), 1e-15, 1e-16
                    )
                )
        # ... and stays positive and monotone deep in the lower tail where 1 + erf collapses to 0.
        self.assertEqual(0.5 * (1.0 + math.erf(-10.0 / math.sqrt(2.0))), 0.0)
        self.assertGreater(bsm.norm_cdf(-10.0), 0.0)
        self.assertGreater(bsm.norm_cdf(-10.0), bsm.norm_cdf(-20.0))
        self.assertGreater(bsm.norm_cdf(-20.0), 0.0)
        self.assertEqual(bsm.norm_cdf(0.0), 0.5)
        self.assertEqual(bsm.norm_cdf(40.0), 1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
