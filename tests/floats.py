"""Compare tool payloads whose floats come from libm (IV, delta, Greeks, group delta).

Different platforms' ``math.exp``, ``math.log`` and ``math.erf`` can differ in the last unit of
precision, so a computed float pinned to all 17 digits fails on one CI image and passes on another.
:func:`assert_close` compares floats with ``math.isclose`` (relative and absolute 1e-12) and every
other value exactly: Decimal money strings, text, ints, booleans and None keep their exact pins,
and dicts, lists and tuples are compared element by element with the same rule.
"""
from __future__ import annotations

import math
import unittest
from typing import Any

REL_TOL: float = 1e-12
ABS_TOL: float = 1e-12


def _diff(expected: Any, actual: Any, path: str) -> str:
    if isinstance(expected, float) and not isinstance(expected, bool):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return "%s: expected the float %r, got %r" % (path, expected, actual)
        if math.isclose(expected, float(actual), rel_tol=REL_TOL, abs_tol=ABS_TOL):
            return ""
        return "%s: %r is not within 1e-12 of %r" % (path, actual, expected)
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(expected) != set(actual):
            return "%s: expected keys %r, got %r" % (path, sorted(expected), actual)
        for key in sorted(expected, key=str):
            found = _diff(expected[key], actual[key], "%s[%r]" % (path, key))
            if found:
                return found
        return ""
    if isinstance(expected, (list, tuple)):
        if type(actual) is not type(expected) or len(actual) != len(expected):
            return "%s: expected %r, got %r" % (path, expected, actual)
        for index, (want, got) in enumerate(zip(expected, actual)):
            found = _diff(want, got, "%s[%d]" % (path, index))
            if found:
                return found
        return ""
    if expected != actual or type(expected) is not type(actual):
        return "%s: expected %r, got %r" % (path, expected, actual)
    return ""


def assert_close(case: unittest.TestCase, expected: Any, actual: Any, msg: str = "") -> None:
    """Fail ``case`` unless ``actual`` matches ``expected`` (floats within 1e-12, the rest exactly)."""
    found = _diff(expected, actual, "value")
    if found:
        case.fail(found + ((" (" + msg + ")") if msg else ""))
