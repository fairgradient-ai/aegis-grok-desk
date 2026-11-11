"""tests.floats.assert_close: floats within 1e-12, everything else exact."""
from __future__ import annotations

import unittest
from decimal import Decimal

import tests._path  # noqa: F401
from tests.floats import assert_close


class AssertCloseTest(unittest.TestCase):
    def test_a_last_digit_float_difference_passes(self) -> None:
        assert_close(self, {"iv": 0.25066666696134243, "mid": "28.65"}, {"mid": "28.65", "iv": 0.2506666669613431})
        assert_close(self, (0.25006528161319297, 1), (0.25006528161319225, 1))

    def test_a_real_float_difference_fails(self) -> None:
        with self.assertRaises(AssertionError):
            assert_close(self, {"iv": 0.2506}, {"iv": 0.2507})

    def test_strings_ints_decimals_and_shapes_stay_exact(self) -> None:
        for expected, actual in (({"max_loss": "19006.00"}, {"max_loss": "19006.01"}), ([1, 2], [1, 3]),
                                 (Decimal("0.15"), Decimal("0.16")), ({"a": 1}, {"a": 1, "b": 2}),
                                 ((1.0, 2.0), [1.0, 2.0]), (1.0, "1.0"), (0.0, False), ("x", "x ")):
            with self.subTest(expected=expected, actual=actual):
                with self.assertRaises(AssertionError):
                    assert_close(self, expected, actual)

    def test_nested_values_pass_and_name_the_failing_path(self) -> None:
        assert_close(self, {"after": [{"delta": 134.07142892292973, "be": "95.03"}]},
                     {"after": [{"delta": 134.07142892292978, "be": "95.03"}]})
        with self.assertRaises(AssertionError) as caught:
            assert_close(self, {"after": [{"be": "95.03"}]}, {"after": [{"be": "95.04"}]})
        self.assertIn("value['after'][0]['be']", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
