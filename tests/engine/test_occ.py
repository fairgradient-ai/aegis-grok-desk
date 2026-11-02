"""OCC/OSI symbol tests: the Aegis cases ported, plus the compact/adjusted/case edge cases.

Source: Aegis ``services/api/tests/instruments/test_occ_parser.py``, the OCC parts of
``test_review_regressions.py`` (findings 2, 3, 4 and the 20xx century) and the invariants of
``test_occ_parser_property.py`` (FairGradient), replayed here with a seeded stdlib generator in
place of Hypothesis. The exercise/settlement registry of the Aegis parser is not ported (the
desk keeps settlement on the position row), so those assertions are omitted; every other
expectation is kept, with the Aegis ``OptionRight`` enum replaced by the ``"CALL"``/``"PUT"``
strings of :mod:`aegis_desk.bsm`.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import random
import string
import unittest
from decimal import Decimal

from aegis_desk import bsm, occ
from aegis_desk.occ import OccParseError, OccSymbol, parse, render


class GoldenParseTest(unittest.TestCase):
    """Aegis TestGoldenParse."""

    def test_aapl_call(self) -> None:
        r = parse("AAPL  240119C00150000")
        self.assertEqual("AAPL", r.root)
        self.assertEqual(datetime.date(2024, 1, 19), r.expiration)
        self.assertEqual(occ.CALL, r.right)
        self.assertEqual(Decimal("150.000"), r.strike)
        self.assertFalse(r.is_adjusted)
        self.assertEqual("AAPL", r.base_root)
        self.assertTrue(r.is_call)
        self.assertFalse(r.is_put)

    def test_aapl_put(self) -> None:
        r = parse("AAPL  240119P00150000")
        self.assertEqual(occ.PUT, r.right)
        self.assertEqual(Decimal("150.000"), r.strike)
        self.assertTrue(r.is_put)

    def test_spx_call(self) -> None:
        r = parse("SPX   261218C05000000")
        self.assertEqual("SPX", r.root)
        self.assertEqual(Decimal("5000.000"), r.strike)
        self.assertEqual(occ.CALL, r.right)

    def test_spxw_root_kept_whole(self) -> None:
        self.assertEqual("SPXW", parse("SPXW  261218C05000000").root)

    def test_five_char_root_googl(self) -> None:
        r = parse("GOOGL 241115C01750000")
        self.assertEqual("GOOGL", r.root)
        self.assertEqual(datetime.date(2024, 11, 15), r.expiration)
        self.assertEqual(Decimal("1750.000"), r.strike)

    def test_fractional_strike(self) -> None:
        self.assertEqual(Decimal("150.500"), parse("AAPL  240119C00150500").strike)

    def test_sub_dollar_strike(self) -> None:
        self.assertEqual(Decimal("0.500"), parse("AAPL  240119C00000500").strike)

    def test_five_digit_strike(self) -> None:
        self.assertEqual(Decimal("10000.000"), parse("SPX   261218C10000000").strike)

    def test_canonical_preserved(self) -> None:
        symbol = "AAPL  240119C00150000"
        self.assertEqual(symbol, parse(symbol).canonical)

    def test_rights_match_bsm_constants(self) -> None:
        self.assertIs(occ.CALL, bsm.CALL)
        self.assertIs(occ.PUT, bsm.PUT)
        self.assertEqual(bsm.RIGHTS, occ.RIGHTS)


class RoundTripTest(unittest.TestCase):
    """Aegis TestRoundTrip: render(parse(s)) == s and parse(render(x)) == x."""

    _CANONICAL = (
        "AAPL  240119C00150000",
        "SPX   261218P05000000",
        "GOOGL 241115C01750000",
        "SPXW  261218C05000000",
        "AAPL  240119P00185500",
        "AAPL1 240119C00150000",
        "AAPL12240119C00150000",
    )

    def test_render_parse_symbol_equals_original(self) -> None:
        for symbol in self._CANONICAL:
            parsed = parse(symbol)
            rendered = render(parsed.root, parsed.expiration, parsed.right, parsed.strike)
            self.assertEqual(symbol, rendered)
            self.assertEqual(21, len(rendered))
            self.assertEqual(parsed, parse(rendered))

    def test_compact_round_trip(self) -> None:
        for symbol in self._CANONICAL:
            parsed = parse(symbol)
            compact = render(
                parsed.root, parsed.expiration, parsed.right, parsed.strike, compact=True
            )
            self.assertEqual(parsed.compact, compact)
            self.assertNotIn(" ", compact)
            self.assertEqual(parsed, parse(compact))

    def test_canonical_and_compact_helpers(self) -> None:
        self.assertEqual("AAPL  240119C00150000", occ.canonical("O:aapl240119c00150000"))
        self.assertEqual("AAPL240119C00150000", occ.compact("AAPL  240119C00150000"))

    def test_render_strike_zero_pads(self) -> None:
        self.assertEqual(
            "00005000", render("AAPL", datetime.date(2024, 1, 19), "CALL", Decimal("5"))[13:21]
        )

    def test_render_upper_cases_root(self) -> None:
        self.assertEqual(
            "SPX   261218P05000000",
            render("spx", datetime.date(2026, 12, 18), "PUT", Decimal("5000")),
        )

    def test_render_rejects_bad_parts(self) -> None:
        expiry = datetime.date(2024, 1, 19)
        with self.assertRaises(OccParseError):
            render("TOOLONGR", expiry, "CALL", Decimal("1"))
        with self.assertRaises(OccParseError):
            render("", expiry, "CALL", Decimal("1"))
        with self.assertRaises(OccParseError):
            render("A-B", expiry, "CALL", Decimal("1"))
        with self.assertRaises(OccParseError):
            render("AAPL", expiry, "call", Decimal("1"))
        with self.assertRaises(OccParseError):
            render("AAPL", datetime.date(1999, 12, 31), "CALL", Decimal("1"))
        with self.assertRaises(OccParseError):
            render("AAPL", expiry, "CALL", Decimal("0"))
        with self.assertRaises(OccParseError):
            render("AAPL", expiry, "CALL", Decimal("-5"))
        with self.assertRaises(OccParseError):
            render("AAPL", expiry, "CALL", Decimal("150.0005"))  # four decimals: not encodable
        with self.assertRaises(OccParseError):
            render("AAPL", expiry, "CALL", Decimal("100000"))  # nine digits of thousandths
        with self.assertRaises(TypeError):
            render("AAPL", expiry, "CALL", 150.0)  # type: ignore[arg-type]  # float never
        with self.assertRaises(TypeError):
            render("AAPL", datetime.datetime(2024, 1, 19, 12, 0), "CALL", Decimal("1"))

    def test_render_accepts_integral_and_trailing_zero_decimals(self) -> None:
        expiry = datetime.date(2024, 1, 19)
        for strike in (Decimal("150"), Decimal("150.0"), Decimal("150.000"), Decimal("1.5E2")):
            self.assertEqual("AAPL  240119C00150000", render("AAPL", expiry, "CALL", strike))
        self.assertEqual(
            "AAPL  240119C99999999", render("AAPL", expiry, "CALL", Decimal("99999.999"))
        )


class AdjustedRootTest(unittest.TestCase):
    """Aegis TestAdjustedOptions, TestNonStandardSuffixOptions and finding 3 (no multiplier)."""

    def test_adjusted_numeric_suffix_flagged(self) -> None:
        r = parse("AAPL1 240119C00150000")
        self.assertTrue(r.is_adjusted)
        self.assertEqual("AAPL1", r.root)
        self.assertEqual("AAPL", r.base_root)

    def test_adjusted_two_digit_suffix_fills_field(self) -> None:
        r = parse("AAPL12240119C00150000")
        self.assertTrue(r.is_adjusted)
        self.assertEqual("AAPL12", r.root)
        self.assertEqual("AAPL", r.base_root)

    def test_seven_suffix_is_only_flagged(self) -> None:
        # The OSI symbol cannot tell a mini (7) from the seventh adjustment: flag, never guess.
        for symbol in (
            "AAPL7 240119C00150000",
            "MSFT7 240119C00300000",
            "VIX7  261218C00020000",
            "GTX7  240119C00050000",
        ):
            r = parse(symbol)
            self.assertTrue(r.is_adjusted, symbol)
            self.assertFalse(hasattr(r, "multiplier"), "the parser never asserts a multiplier")
            self.assertFalse(hasattr(r, "is_mini"))
        self.assertEqual(Decimal("150.000"), parse("AAPL7 240119C00150000").strike)

    def test_standard_roots_not_flagged(self) -> None:
        for symbol in ("AAPL  240119C00150000", "GOOGL 241115C01750000", "SPX   261218C05000000"):
            self.assertFalse(parse(symbol).is_adjusted, symbol)

    def test_digits_before_letters_rejected(self) -> None:
        with self.assertRaisesRegex(OccParseError, "root"):
            parse("1AAPL 240119C00150000")


class CompactAndVendorFormsTest(unittest.TestCase):
    """Aegis TestPolygonPrefixStripping and finding 4 (compact parse with digit roots)."""

    def test_polygon_prefix_stripped_padded(self) -> None:
        r = parse("O:AAPL  240119C00150000")
        self.assertEqual("AAPL", r.root)
        self.assertEqual(Decimal("150.000"), r.strike)

    def test_polygon_prefix_stripped_compact(self) -> None:
        r = parse("O:AAPL240119C00150000")
        self.assertEqual("AAPL", r.root)
        self.assertEqual(datetime.date(2024, 1, 19), r.expiration)
        self.assertEqual(Decimal("150.000"), r.strike)

    def test_polygon_prefix_lower_case(self) -> None:
        self.assertEqual("AAPL  240119C00150000", parse("o:aapl240119c00150000").canonical)

    def test_compact_adjusted_roots(self) -> None:
        for symbol, root in (
            ("AAPL1240119C00150000", "AAPL1"),
            ("AAPL7240119C00150000", "AAPL7"),
            ("AAPL12240119C00150000", "AAPL12"),
        ):
            r = parse(symbol)
            self.assertEqual(root, r.root, symbol)
            self.assertEqual(datetime.date(2024, 1, 19), r.expiration)
            self.assertEqual(Decimal("150.000"), r.strike)
            self.assertTrue(r.is_adjusted)

    def test_compact_standard_roots(self) -> None:
        self.assertEqual("GOOGL", parse("GOOGL241115C01750000").root)
        self.assertEqual("AAPL", parse("AAPL240119C00150000").root)
        r = parse("SPX261218C05000000")
        self.assertEqual("SPX", r.root)
        self.assertEqual(Decimal("5000.000"), r.strike)
        self.assertEqual("F", parse("F240119C00012500").root)  # one-letter root

    def test_lower_case_variants_are_the_same_contract(self) -> None:
        upper = parse("AAPL  240119C00150000")
        self.assertEqual(upper, parse("aapl  240119C00150000"))
        self.assertEqual(upper, parse("aapl  240119c00150000"))
        self.assertEqual(upper, parse("AAPL240119C00150000"))
        self.assertEqual("AAPL", parse("aapl  240119C00150000").root)
        self.assertEqual(
            1, len({upper, parse("aapl240119c00150000"), parse("O:AAPL240119C00150000")})
        )

    def test_compact_form_keeps_surrounding_whitespace_malformed(self) -> None:
        # Whitespace is never stripped: a trailing space shifts every field.
        with self.assertRaisesRegex(OccParseError, "length"):
            parse("AAPL  240119C00150000 ")
        with self.assertRaisesRegex(OccParseError, "length"):
            parse(" AAPL240119C00150000")


class CenturyTest(unittest.TestCase):
    """Aegis finding: '%y' would map 99 to 1999; options are future-dated, so 20xx is forced."""

    def test_expiry_99_maps_to_2099(self) -> None:
        self.assertEqual(datetime.date(2099, 12, 31), parse("AAPL  991231C00150000").expiration)

    def test_expiry_00_maps_to_2000(self) -> None:
        self.assertEqual(datetime.date(2000, 1, 21), parse("AAPL  000121C00150000").expiration)

    def test_leap_day(self) -> None:
        self.assertEqual(datetime.date(2028, 2, 29), parse("AAPL  280229C00150000").expiration)
        with self.assertRaisesRegex(OccParseError, "expiration"):
            parse("AAPL  270229C00150000")


class MalformedInputTest(unittest.TestCase):
    """Aegis TestMalformedInput: every defect raises OccParseError naming the field."""

    def assert_error(self, symbol: str, field: str) -> None:
        with self.assertRaisesRegex(OccParseError, field) as ctx:
            parse(symbol)
        self.assertEqual(symbol, ctx.exception.symbol)
        self.assertIsInstance(ctx.exception, ValueError)

    def test_wrong_length(self) -> None:
        self.assert_error("AAPL240119C001500", "length")
        self.assert_error("AAPL  240119C001500000", "length")
        self.assert_error("", "length")
        self.assert_error("AAPL", "length")

    def test_invalid_right(self) -> None:
        self.assert_error("AAPL  240119X00150000", "right")

    def test_non_digit_expiry(self) -> None:
        self.assert_error("AAPL  2401XYC00150000", "expiration")

    def test_invalid_date(self) -> None:
        self.assert_error("AAPL  240132C00150000", "expiration")
        self.assert_error("AAPL  241319C00150000", "expiration")
        self.assert_error("AAPL  240019C00150000", "expiration")

    def test_non_digit_strike(self) -> None:
        self.assert_error("AAPL  240119C0015000X", "strike")
        self.assert_error("AAPL  240119C-0150000", "strike")

    def test_zero_strike(self) -> None:
        self.assert_error("AAPL  240119C00000000", "strike")

    def test_blank_or_misaligned_root(self) -> None:
        self.assert_error("      240119C00150000", "root")
        self.assert_error(" AAPL 240119C00150000", "root")
        self.assert_error("AA PL 240119C00150000", "root")
        self.assert_error("A.PL  240119C00150000", "root")
        self.assert_error("     " * 5, "length")

    def test_none_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            parse(None)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            parse(b"AAPL  240119C00150000")  # type: ignore[arg-type]

    def test_compact_form_with_bad_tail_reports_length(self) -> None:
        # The tail does not look like YYMMDD + C/P + 8 digits, so no padding is attempted.
        self.assert_error("AAPL240119X00150000", "length")

    def test_try_parse_collects_instead_of_raising(self) -> None:
        parsed, message = occ.try_parse("AAPL  240119C00150000")
        self.assertIsInstance(parsed, OccSymbol)
        self.assertIsNone(message)
        parsed, message = occ.try_parse("AAPL  240119C00000000")
        self.assertIsNone(parsed)
        self.assertIn("strike", message or "")


class StrikePrecisionTest(unittest.TestCase):
    """Aegis TestStrikePrecision: Decimal to the thousandth, never a float."""

    def test_strike_is_decimal_with_three_places(self) -> None:
        for symbol in (
            "AAPL  240119C00150000",
            "AAPL  240119C00150500",
            "AAPL  240119C00000500",
            "AAPL  240119C00000001",
        ):
            strike = parse(symbol).strike
            self.assertIsInstance(strike, Decimal)
            self.assertEqual(-3, strike.as_tuple().exponent, symbol)

    def test_exact_values(self) -> None:
        self.assertEqual(Decimal("150"), parse("AAPL  240119C00150000").strike)
        self.assertEqual(Decimal("150.5"), parse("AAPL  240119C00150500").strike)
        self.assertEqual(Decimal("0.5"), parse("AAPL  240119C00000500").strike)
        self.assertEqual(Decimal("0.001"), parse("AAPL  240119C00000001").strike)
        self.assertEqual(Decimal("99999.999"), parse("AAPL  240119C99999999").strike)

    def test_string_form_keeps_trailing_zeros(self) -> None:
        self.assertEqual("150.000", str(parse("AAPL  240119C00150000").strike))
        self.assertEqual("150.500", str(parse("AAPL  240119C00150500").strike))


class SeededPropertyTest(unittest.TestCase):
    """Aegis test_occ_parser_property.py, replayed with a seeded stdlib generator."""

    def _symbols(self, count: int = 400):
        rng = random.Random(20261102)
        for _ in range(count):
            root = "".join(rng.choice(string.ascii_uppercase) for _ in range(rng.randint(1, 6)))
            if rng.random() < 0.2 and len(root) < 6:
                root = root[: rng.randint(1, len(root))] + str(rng.randint(1, 99))
                root = root[:6]
            expiry = datetime.date(2023, 1, 1) + datetime.timedelta(days=rng.randint(0, 365 * 18))
            right = rng.choice(occ.RIGHTS)
            strike = Decimal(rng.randint(1, 99999999)) / Decimal(1000)
            yield root, expiry, right, strike

    def test_render_parse_round_trip(self) -> None:
        for root, expiry, right, strike in self._symbols():
            symbol = render(root, expiry, right, strike)
            self.assertEqual(21, len(symbol))
            parsed = parse(symbol)
            self.assertEqual(root, parsed.root)
            self.assertEqual(expiry, parsed.expiration)
            self.assertEqual(right, parsed.right)
            self.assertEqual(strike, parsed.strike)
            self.assertGreater(parsed.strike, Decimal("0"))
            self.assertIn(parsed.right, occ.RIGHTS)
            self.assertEqual(symbol, parsed.canonical)
            self.assertEqual(parsed, parse(parsed.compact))
            self.assertEqual(parsed, parse(symbol.lower()))
            self.assertEqual(parsed, parse("O:" + parsed.compact))
            self.assertEqual(root[-1].isdigit(), parsed.is_adjusted)

    def test_random_strings_raise_occ_parse_error_or_parse(self) -> None:
        rng = random.Random(7)
        alphabet = string.ascii_letters + string.digits + " :.-"
        for _ in range(1500):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
            try:
                result = parse(text)
            except OccParseError:
                continue
            self.assertIsInstance(result, OccSymbol)
            self.assertGreater(result.strike, Decimal("0"))
            self.assertIn(result.right, occ.RIGHTS)
            self.assertNotEqual("", result.root)


if __name__ == "__main__":
    unittest.main()
