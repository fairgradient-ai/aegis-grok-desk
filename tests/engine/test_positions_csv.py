"""``positions.csv`` parser (``aegis_desk.positions_csv``): the Aegis import cases ported, the desk's
money conventions as goldens, the extension columns, quarantine and the file-level errors.

The Aegis cases come from ``services/api/tests/integrations/import_csv/test_csv_parser.py`` and the
review fixes beside it (FairGradient); every one is reproduced by name. Where the desk's money
convention is stricter than Aegis (the sign of ``cost_basis_total`` must equal the sign of
``quantity``, a cost basis is required), the ported row carries a convention-correct cost basis and
the test says so. Row numbers are physical line numbers of the file (what an editor shows).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import csv
import datetime
import hashlib
import io
import os
import tempfile
import unittest
from decimal import Decimal
from typing import List, Optional, Sequence

from aegis_desk import occ, positions_csv
from aegis_desk.positions_csv import PositionRow, PositionsFile, RowError, parse_positions_text

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_CSV = os.path.join(ROOT, "tests", "fixtures", "desk", "positions.csv")
MODULE_PATH = os.path.join(ROOT, "scripts", "aegis_desk", "positions_csv.py")
TODAY = datetime.date(2026, 11, 2)

FULL_HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
               "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
               "close_price_per_unit,underlying_stop,note")
# The Aegis test headers, verbatim.
EQUITY_HEADER = "account,symbol,instrument_type,quantity,cost_basis_total,cost_basis_per_unit"
OPTION_HEADER = ("account,symbol,occ_symbol,instrument_type,quantity,cost_basis_total,cost_basis_per_unit,"
                 "expiration,option_right,strike")

D = Decimal


def parse(*rows: str, header: str = FULL_HEADER, today: datetime.date = TODAY) -> PositionsFile:
    return parse_positions_text("\n".join([header] + list(rows)) + "\n", today=today)


def full_row(instrument_type: str = "equity", symbol: str = "WMT", occ_symbol: str = "", quantity: str = "200",
             expiration: str = "", option_right: str = "", strike: str = "", account: str = "Brokerage (margin)",
             trade_id: str = "AG-20261020-01", opened: str = "2026-10-20", cost_basis_total: str = "19700.00",
             cost_basis_per_unit: str = "", open_price_per_share: str = "", status: str = "open", closed: str = "",
             closed_quantity: str = "", close_price_per_unit: str = "", underlying_stop: str = "",
             note: str = "") -> str:
    cells = [instrument_type, symbol, occ_symbol, quantity, expiration, option_right, strike, account, trade_id,
             opened, cost_basis_total, cost_basis_per_unit, open_price_per_share, status, closed, closed_quantity,
             close_price_per_unit, underlying_stop, note]
    return ",".join(cells)


def short_call(quantity: str = "-2", total: str = "-684.00", premium: str = "", symbol: str = "WMT",
               **extra: str) -> str:
    return full_row(instrument_type="option", symbol=symbol, occ_symbol="WMT261120C00105000", quantity=quantity,
                    cost_basis_total=total, open_price_per_share=premium, **extra)


def long_put(quantity: str = "1", total: str = "1130.00", premium: str = "", **extra: str) -> str:
    return full_row(instrument_type="option", symbol="SPY", occ_symbol="SPY261218P00620000", quantity=quantity,
                    account="Roth IRA", trade_id="AG-20261028-03", opened="2026-10-28", cost_basis_total=total,
                    open_price_per_share=premium, **extra)


def only_row(result: PositionsFile) -> PositionRow:
    assert result.file_errors == [], result.file_errors
    assert result.quarantined == {}, result.quarantined
    assert len(result.rows) == 1, result.rows
    return result.rows[0]


def only_error(result: PositionsFile, field: Optional[str] = None) -> RowError:
    assert result.file_errors == [], result.file_errors
    errors = [error for group in result.quarantined.values() for error in group]
    assert len(errors) >= 1, "expected a row error, got none"
    if field is not None:
        matching = [error for error in errors if error.field == field]
        assert matching, "no error on field %r among %r" % (field, errors)
        return matching[0]
    return errors[0]


# =============================================================================================
# The Aegis parser cases, by name
# =============================================================================================


class AegisEquityCasesTest(unittest.TestCase):
    def test_equity_row_cost_basis_total(self) -> None:
        row = only_row(parse("acct-001,AAPL,equity,10,1500.00,", header=EQUITY_HEADER))
        self.assertEqual("AAPL", row.symbol)
        self.assertEqual("equity", row.instrument_type)
        self.assertEqual(D("10"), row.quantity)
        self.assertEqual(D("1500.00"), row.cost_basis_total)
        self.assertIsNone(row.cost_basis_per_unit)
        self.assertEqual("acct-001", row.account)
        self.assertTrue(row.is_stock_like)
        self.assertFalse(row.is_option)
        self.assertEqual(1, row.multiplier)

    def test_equity_row_cost_basis_per_unit(self) -> None:
        row = only_row(parse("acct-001,AAPL,equity,10,,150.00", header=EQUITY_HEADER))
        self.assertEqual(D("1500.00"), row.cost_basis_total)
        self.assertEqual(D("150.00"), row.cost_basis_per_unit)
        self.assertEqual(D("150.00"), row.entry_price_per_share)

    def test_equity_row_both_cost_basis_total_wins(self) -> None:
        row = only_row(parse("acct-001,AAPL,equity,10,999.99,50.00", header=EQUITY_HEADER))
        self.assertEqual(D("999.99"), row.cost_basis_total)
        self.assertEqual(D("50.00"), row.cost_basis_per_unit)
        self.assertTrue(any("total used" in warning for warning in row.warnings), row.warnings)

    def test_etf_row(self) -> None:
        row = only_row(parse(",SPY,etf,5,750.00,", header=EQUITY_HEADER))
        self.assertEqual("etf", row.instrument_type)
        self.assertEqual("default", row.account)

    def test_negative_quantity_short_position(self) -> None:
        # The Aegis row carries ``0.00``; the desk convention needs a received (negative) basis.
        row = only_row(parse("acct-001,SPY,equity,-3,-1500.00,", header=EQUITY_HEADER))
        self.assertEqual(D("-3"), row.quantity)
        self.assertEqual("short", row.side)
        self.assertEqual(D("-1500.00"), row.cost_basis_total)
        self.assertEqual(D("500"), row.entry_price_per_share)

    def test_multi_row_same_instrument_same_account(self) -> None:
        result = parse("acct-001,AAPL,equity,5,750.00,", "acct-001,AAPL,equity,5,760.00,", header=EQUITY_HEADER)
        self.assertEqual(2, len(result.rows))
        self.assertEqual(["ROW-2", "ROW-3"], [row.group_id for row in result.rows])

    def test_multi_row_different_instruments(self) -> None:
        result = parse("acct-001,AAPL,equity,10,1500.00,", "acct-001,MSFT,equity,5,1000.00,", header=EQUITY_HEADER)
        self.assertEqual({"AAPL", "MSFT"}, {row.symbol for row in result.rows})


class AegisOptionCasesTest(unittest.TestCase):
    def test_option_row_canonical_occ(self) -> None:
        row = only_row(parse("acct-001,,AAPL  240119C00150000,option,1,800.00,,,,", header=OPTION_HEADER))
        self.assertEqual("option", row.instrument_type)
        self.assertEqual(D("1"), row.quantity)
        self.assertEqual("AAPL  240119C00150000", row.occ.canonical)
        self.assertEqual("AAPL240119C00150000", row.compact_symbol)
        self.assertEqual("AAPL", row.symbol, "a blank symbol is filled from the OCC root")
        self.assertEqual(100, row.multiplier)
        self.assertEqual(D("8.00"), row.entry_price_per_share)

    def test_option_row_compact_vendor_form(self) -> None:
        row = only_row(parse("acct-001,,AAPL240119C00150000,option,2,1600.00,,,,", header=OPTION_HEADER))
        self.assertEqual("AAPL  240119C00150000", row.occ.canonical)
        self.assertEqual(datetime.date(2024, 1, 19), row.expiration)
        self.assertEqual(occ.CALL, row.right)
        self.assertEqual(D("150.000"), row.strike)

    def test_option_row_polygon_prefix(self) -> None:
        row = only_row(parse("acct-001,,O:AAPL240119C00150000,option,1,800.00,,,,", header=OPTION_HEADER))
        self.assertEqual("AAPL  240119C00150000", row.occ.canonical)

    def test_option_row_component_columns(self) -> None:
        row = only_row(parse("acct-001,AAPL,,option,1,800.00,,2024-01-19,CALL,150.00", header=OPTION_HEADER))
        self.assertEqual("AAPL", row.symbol)
        self.assertEqual(datetime.date(2024, 1, 19), row.expiration)
        self.assertEqual("CALL", row.right)
        self.assertEqual(D("150.000"), row.strike)
        self.assertEqual("AAPL  240119C00150000", row.occ.canonical, "synthesised exactly as Aegis does")

    def test_component_columns_vs_occ_symbol_same_canonical(self) -> None:
        by_components = only_row(parse("acct-001,AAPL,,option,1,800.00,,2024-01-19,CALL,150.00",
                                       header=OPTION_HEADER))
        by_symbol = only_row(parse("acct-001,,AAPL240119C00150000,option,1,800.00,,,,", header=OPTION_HEADER))
        self.assertEqual(by_symbol.occ, by_components.occ)
        put = only_row(parse("acct-001,AAPL,,option,1,800.00,,2024-01-19,put,150", header=OPTION_HEADER))
        self.assertEqual("AAPL  240119P00150000", put.occ.canonical)

    def test_adjusted_option_flagged(self) -> None:
        row = only_row(parse("acct-001,,AAPL1 240119C00150000,option,1,800.00,,,,", header=OPTION_HEADER))
        self.assertTrue(row.occ.is_adjusted)
        self.assertEqual("AAPL", row.symbol)
        self.assertEqual("AAPL", row.underlying)
        self.assertIn("adjusted contract: deliverable may differ from 100 shares", row.warnings)
        self.assertEqual(100, row.multiplier, "Stage 1 never guesses a deliverable")

    def test_strike_at_boundary_passes(self) -> None:
        symbol = positions_csv.synthesize_occ_symbol("AAPL", datetime.date(2024, 1, 19), "CALL", D("99999.999"))
        self.assertEqual(21, len(symbol))
        self.assertEqual("AAPL  240119C99999999", symbol)

    def test_strike_above_max_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            positions_csv.synthesize_occ_symbol("AAPL", datetime.date(2024, 1, 19), "CALL", D("100000.000"))
        self.assertIn("strike", str(ctx.exception).lower())

    def test_csv_option_row_strike_overflow_raises(self) -> None:
        error = only_error(parse("acct-001,AAPL,,option,1,800.00,,2024-01-19,CALL,100000.00", header=OPTION_HEADER),
                           field="strike")
        self.assertEqual(2, error.row_number)


class AegisMalformedRowsTest(unittest.TestCase):
    def test_blank_symbol_raises(self) -> None:
        error = only_error(parse(",  ,equity,10,1500.00,", header=EQUITY_HEADER))
        self.assertEqual(2, error.row_number)
        self.assertIn("symbol", error.field)

    def test_non_numeric_quantity_raises(self) -> None:
        error = only_error(parse("acct-001,AAPL,equity,abc,1500.00,", header=EQUITY_HEADER), field="quantity")
        self.assertEqual(2, error.row_number)

    def test_bad_occ_symbol_raises(self) -> None:
        error = only_error(parse("acct-001,,NOT_AN_OCC_SYMBOL,option,1,800.00,,,,", header=OPTION_HEADER),
                           field="occ_symbol")
        self.assertEqual(2, error.row_number)
        self.assertIn("NOT_AN_OCC_SYMBOL", error.message)

    def test_option_component_zero_strike_raises(self) -> None:
        error = only_error(parse("acct-001,AAPL,,option,1,800.00,,2024-01-19,CALL,0.00", header=OPTION_HEADER),
                           field="strike")
        self.assertEqual(2, error.row_number)

    def test_option_missing_components_raises(self) -> None:
        error = only_error(parse("acct-001,AAPL,,option,1,800.00,,,,", header=OPTION_HEADER))
        self.assertEqual(2, error.row_number)
        self.assertIn("expiration", error.message)
        self.assertIn("option_right", error.message)
        self.assertIn("strike", error.message)

    def test_zero_quantity_raises(self) -> None:
        error = only_error(parse("acct-001,AAPL,equity,0,0.00,", header=EQUITY_HEADER), field="quantity")
        self.assertEqual(2, error.row_number)

    def test_blank_lines_skipped(self) -> None:
        text = "%s\n\nacct-001,AAPL,equity,10,1500.00,\n\n" % EQUITY_HEADER
        result = parse_positions_text(text, today=TODAY)
        self.assertEqual(1, len(result.rows))
        self.assertEqual(3, result.rows[0].row_number, "physical line numbers")

    def test_comment_lines_skipped(self) -> None:
        text = "# leading comment\n%s\n# this is a comment\n  # indented comment\nacct-001,AAPL,equity,10,1500.00,\n" % EQUITY_HEADER
        result = parse_positions_text(text, today=TODAY)
        self.assertEqual([], result.file_errors)
        self.assertEqual(1, len(result.rows))
        self.assertEqual(5, result.rows[0].row_number)
        self.assertEqual("ROW-5", result.rows[0].group_id)

    def test_formula_injection_rejected(self) -> None:
        error = only_error(parse("=CMD|'/C calc'!A0,AAPL,equity,10,1500.00,", header=EQUITY_HEADER), field="account")
        self.assertTrue("formula" in error.message.lower() or "injection" in error.message.lower(), error.message)

    def test_formula_injection_in_symbol_rejected(self) -> None:
        error = only_error(parse("acct-001,+AAPL,equity,10,1500.00,", header=EQUITY_HEADER), field="symbol")
        self.assertEqual(2, error.row_number)

    def test_null_byte_before_injection_prefix_rejected(self) -> None:
        error = only_error(parse("\x00=CMD|'/C calc'!A0,AAPL,equity,10,1500.00,", header=EQUITY_HEADER))
        self.assertEqual(2, error.row_number)
        self.assertTrue("null" in error.message.lower() or "control" in error.message.lower(), error.message)

    def test_zero_width_space_before_injection_prefix_rejected(self) -> None:
        error = only_error(parse("​=HYPERLINK(),AAPL,equity,10,1500.00,", header=EQUITY_HEADER), field="account")
        self.assertEqual(2, error.row_number)
        self.assertTrue("control" in error.message.lower() or "invisible" in error.message.lower(), error.message)

    def test_left_to_right_mark_and_soft_hyphen_rejected(self) -> None:
        for raw in ("‎+HYPERLINK()", "­@system()"):
            with self.subTest(raw=raw):
                only_error(parse("%s,AAPL,equity,10,1500.00," % raw, header=EQUITY_HEADER), field="account")

    def test_clean_value_passes_guard(self) -> None:
        row = only_row(parse("  acct-001  ,AAPL,equity,10,1500.00,", header=EQUITY_HEADER))
        self.assertEqual("acct-001", row.account)

    def test_max_rows_exceeded_raises(self) -> None:
        lines = ["acct-001,S%d,equity,1,100.00," % i for i in range(positions_csv.MAX_ROWS + 1)]
        result = parse(*lines, header=EQUITY_HEADER)
        self.assertEqual([], result.rows)
        self.assertEqual(["row_limit"], [error.field for error in result.file_errors])
        self.assertEqual(positions_csv.MAX_ROWS + 2, result.file_errors[0].row_number)
        exactly = parse(*lines[:-1], header=EQUITY_HEADER)
        self.assertEqual(positions_csv.MAX_ROWS, len(exactly.rows))

    def test_extra_columns_ignored(self) -> None:
        row = only_row(parse("acct-001,AAPL,equity,10,1500.00,,some_future_value",
                             header=EQUITY_HEADER + ",unknown_future_column"))
        self.assertEqual("AAPL", row.symbol)

    def test_csv_account_too_long_raises(self) -> None:
        only_error(parse("%s,AAPL,equity,10,1500.00," % ("a" * 201), header=EQUITY_HEADER), field="account")
        only_row(parse("%s,AAPL,equity,10,1500.00," % ("a" * 200), header=EQUITY_HEADER))

    def test_csv_symbol_too_long_raises(self) -> None:
        only_error(parse("acct-001,%s,equity,10,1500.00," % ("A" * 33), header=EQUITY_HEADER), field="symbol")

    def test_csv_occ_symbol_too_long_raises(self) -> None:
        only_error(parse("acct-001,,%s,option,1,800.00,,,," % ("A" * 65), header=OPTION_HEADER), field="occ_symbol")


# =============================================================================================
# Money conventions (brief: the worked examples are the goldens)
# =============================================================================================


class MoneyConventionTest(unittest.TestCase):
    def test_long_two_calls_paid_640(self) -> None:
        row = only_row(parse(full_row(instrument_type="option", occ_symbol="WMT261120C00105000", quantity="2",
                                      cost_basis_total="640.00")))
        self.assertEqual(D("640.00"), row.cost_basis_total)
        self.assertEqual(D("3.20"), row.entry_price_per_share)
        self.assertEqual("long", row.side)
        self.assertEqual(D("2"), row.open_quantity)
        self.assertEqual(D("640.00"), row.remaining_cost_basis)

    def test_long_two_calls_from_premium(self) -> None:
        row = only_row(parse(full_row(instrument_type="option", occ_symbol="WMT261120C00105000", quantity="2",
                                      cost_basis_total="", open_price_per_share="3.20")))
        self.assertEqual(D("640.00"), row.cost_basis_total)
        self.assertEqual(D("3.20"), row.open_price_per_share)

    def test_short_two_calls_received_640(self) -> None:
        row = only_row(parse(short_call(quantity="-2", total="-640.00")))
        self.assertEqual(D("-640.00"), row.cost_basis_total)
        self.assertEqual(D("3.20"), row.entry_price_per_share)
        self.assertEqual("short", row.side)

    def test_short_two_calls_from_premium_carries_the_sign_of_quantity(self) -> None:
        row = only_row(parse(short_call(quantity="-2", total="", premium="3.20")))
        self.assertEqual(D("-640.00"), row.cost_basis_total)

    def test_stock_per_unit_total(self) -> None:
        row = only_row(parse(full_row(cost_basis_total="", cost_basis_per_unit="98.50")))
        self.assertEqual(D("19700.00"), row.cost_basis_total)
        self.assertEqual(D("98.50"), row.entry_price_per_share)

    def test_total_and_premium_must_agree_within_a_cent_per_share(self) -> None:
        agree = only_row(parse(short_call(total="-684.00", premium="3.42")))
        self.assertEqual((D("-684.00"), D("3.42")), (agree.cost_basis_total, agree.open_price_per_share))
        boundary = only_row(parse(short_call(total="-684.00", premium="3.43")))  # 2.00 == 0.01 x 2 x 100
        self.assertEqual(D("-684.00"), boundary.cost_basis_total, "the written total is kept")
        error = only_error(parse(short_call(total="-684.00", premium="3.44")))
        self.assertIn("open_price_per_share", error.field)
        self.assertIn("-684.00", error.message)
        self.assertIn("3.44", error.message)

    def test_cost_basis_per_unit_on_an_option_row_is_an_error(self) -> None:
        error = only_error(parse(short_call(total="", cost_basis_per_unit="3.42")), field="cost_basis_per_unit")
        self.assertIn("ambiguous for options: use cost_basis_total or open_price_per_share", error.message)

    def test_sign_of_total_must_equal_sign_of_quantity(self) -> None:
        for quantity, total in (("-2", "684.00"), ("2", "-684.00"), ("2", "0.00")):
            with self.subTest(quantity=quantity, total=total):
                error = only_error(parse(short_call(quantity=quantity, total=total)), field="cost_basis_total")
                self.assertIn("cost basis sign must equal quantity sign: paid is positive, received is negative",
                              error.message)
        error = only_error(parse(full_row(quantity="-200", cost_basis_total="19700.00")), field="cost_basis_total")
        self.assertIn("paid is positive", error.message)

    def test_option_row_needs_total_or_premium(self) -> None:
        error = only_error(parse(short_call(total="")))
        self.assertIn("cost_basis_total or open_price_per_share", error.message)

    def test_stock_row_needs_a_cost_basis(self) -> None:
        error = only_error(parse(full_row(cost_basis_total="")))
        self.assertIn("cost_basis_total or cost_basis_per_unit", error.message)

    def test_open_price_per_share_on_a_stock_row_is_an_error(self) -> None:
        error = only_error(parse(full_row(open_price_per_share="98.50")), field="open_price_per_share")
        self.assertIn("cost_basis_per_unit", error.message)

    def test_premium_must_be_positive_and_at_most_the_strike_for_puts(self) -> None:
        error = only_error(parse(long_put(total="0.00")))
        self.assertIn("cost_basis_total", error.field)
        error = only_error(parse(long_put(total="", premium="0")))
        self.assertIn("premium", error.message)
        error = only_error(parse(long_put(quantity="1", total="62100.00")))
        self.assertIn("premium exceeds strike", error.message)
        at_strike = only_row(parse(long_put(quantity="1", total="62000.00")))
        self.assertEqual(D("620"), at_strike.entry_price_per_share)

    def test_option_quantity_must_be_whole_contracts(self) -> None:
        error = only_error(parse(short_call(quantity="-1.5", total="-513.00")), field="quantity")
        self.assertIn("whole", error.message)
        only_row(parse(full_row(instrument_type="crypto", symbol="BTC", quantity="0.25",
                                cost_basis_total="20000.00")))

    def test_exponent_nan_and_infinity_forms_are_rejected(self) -> None:
        for quantity in ("1e2", "NaN", "Infinity", "-Infinity", "0x10", "1_000", " 2 0"):
            with self.subTest(quantity=quantity):
                only_error(parse(full_row(quantity=quantity, cost_basis_total="100.00")), field="quantity")
        row = only_row(parse(full_row(quantity="+200")))
        self.assertEqual(D("200"), row.quantity)

    def test_entry_price_quantised_to_four_places_when_inexact(self) -> None:
        row = only_row(parse(full_row(instrument_type="option", occ_symbol="WMT261120C00105000", quantity="3",
                                      cost_basis_total="1000.00")))
        self.assertEqual(D("3.3333"), row.entry_price_per_share)

    def test_money_is_decimal_never_float(self) -> None:
        row = only_row(parse(short_call(total="-684.00", premium="3.42", underlying_stop="92.00")))
        for value in (row.quantity, row.cost_basis_total, row.open_price_per_share, row.underlying_stop,
                      row.entry_price_per_share, row.remaining_cost_basis, row.open_quantity, row.strike):
            self.assertIsInstance(value, Decimal)
        with open(MODULE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("float(", source)


class CloseFieldsTest(unittest.TestCase):
    def test_closed_row(self) -> None:
        row = only_row(parse(full_row(instrument_type="option", symbol="SPY", occ_symbol="SPY261016P00600000",
                                      quantity="-1", account="Roth IRA", trade_id="AG-20261001-04",
                                      opened="2026-10-01", cost_basis_total="-480.00", status="closed",
                                      closed="2026-10-14", closed_quantity="1", close_price_per_unit="0.55",
                                      note="closed early")))
        self.assertTrue(row.is_closed)
        self.assertFalse(row.is_open)
        self.assertEqual(D("0"), row.open_quantity)
        self.assertEqual(D("0"), row.remaining_cost_basis)
        self.assertEqual(D("-480.00"), row.closed_cost_basis)
        self.assertEqual((datetime.date(2026, 10, 14), D("1"), D("0.55")),
                         (row.closed, row.closed_quantity, row.close_price_per_unit))
        # The brief's realized formula on the parsed fields: -1 x 1 x 100 x 0.55 - (-480.00) = +425.00.
        sign = D(1) if row.quantity > 0 else D(-1)
        realized = sign * row.closed_quantity * row.multiplier * row.close_price_per_unit - row.closed_cost_basis
        self.assertEqual(D("425.00"), realized)

    def test_closed_quantity_defaults_to_all_when_status_closed(self) -> None:
        row = only_row(parse(short_call(status="closed", closed="2026-10-30", close_price_per_unit="1.20")))
        self.assertEqual(D("2"), row.closed_quantity)
        self.assertEqual(D("0"), row.open_quantity)

    def test_expired_worthless_closes_at_zero(self) -> None:
        row = only_row(parse(short_call(status="closed", closed="2026-11-20", closed_quantity="2",
                                        close_price_per_unit="0")))
        self.assertEqual(D("0"), row.close_price_per_unit)
        self.assertEqual(D("-684.00"), row.closed_cost_basis)

    def test_partial_close(self) -> None:
        row = only_row(parse(short_call(quantity="-2", total="-684.00", closed="2026-10-30", closed_quantity="1",
                                        close_price_per_unit="1.20")))
        self.assertTrue(row.is_open)
        self.assertTrue(row.is_partially_closed)
        self.assertEqual(D("-1"), row.open_quantity)
        self.assertEqual(D("-342.00"), row.remaining_cost_basis)
        self.assertEqual(D("-342.00"), row.closed_cost_basis)

    def test_partial_close_split_is_exact_to_the_cent(self) -> None:
        row = only_row(parse(full_row(instrument_type="option", occ_symbol="WMT261120C00105000", quantity="3",
                                      cost_basis_total="1130.00", closed="2026-10-30", closed_quantity="1",
                                      close_price_per_unit="4.00")))
        self.assertEqual(D("376.67"), row.closed_cost_basis)
        self.assertEqual(D("753.33"), row.remaining_cost_basis)
        self.assertEqual(row.cost_basis_total, row.closed_cost_basis + row.remaining_cost_basis)

    def test_status_closed_requires_close_price_and_date(self) -> None:
        only_error(parse(short_call(status="closed", closed="2026-10-30")), field="close_price_per_unit")
        only_error(parse(short_call(status="closed", close_price_per_unit="1.20")), field="closed")

    def test_closed_quantity_needs_price_and_date(self) -> None:
        only_error(parse(short_call(closed_quantity="1")), field="closed_quantity")
        only_error(parse(short_call(closed_quantity="1", closed="2026-10-30")), field="close_price_per_unit")
        only_error(parse(short_call(closed_quantity="1", close_price_per_unit="1.20")), field="closed")

    def test_closed_quantity_bounds(self) -> None:
        only_error(parse(short_call(closed="2026-10-30", closed_quantity="3", close_price_per_unit="1.20")),
                   field="closed_quantity")
        only_error(parse(short_call(closed="2026-10-30", closed_quantity="0", close_price_per_unit="1.20")),
                   field="closed_quantity")
        error = only_error(parse(short_call(closed="2026-10-30", closed_quantity="2", close_price_per_unit="1.20")),
                           field="closed_quantity")
        self.assertIn("status = closed", error.message)
        only_error(parse(short_call(status="closed", closed="2026-10-30", closed_quantity="1",
                                    close_price_per_unit="1.20")), field="closed_quantity")

    def test_close_fields_consistency(self) -> None:
        only_error(parse(short_call(closed="2026-10-30")), field="closed")
        only_error(parse(short_call(close_price_per_unit="1.20")), field="close_price_per_unit")
        only_error(parse(short_call(status="closed", closed="2026-10-19", close_price_per_unit="1.20")),
                   field="closed")  # before opened
        only_error(parse(short_call(status="closed", closed="2026-10-30", close_price_per_unit="-1")),
                   field="close_price_per_unit")
        only_error(parse(short_call(status="done")), field="status")
        row = only_row(parse(short_call(status="OPEN")))
        self.assertEqual("open", row.status)


# =============================================================================================
# Extensions: trade_id, opened, tags, stop
# =============================================================================================


class ExtensionColumnsTest(unittest.TestCase):
    def test_trade_id_pattern(self) -> None:
        for good in ("AG-20261020-01", "AG-20261020-123"):
            with self.subTest(good=good):
                row = only_row(parse(full_row(trade_id=good)))
                self.assertEqual((good, good), (row.trade_id, row.group_id))
        for bad in ("AG-2026102-01", "ag-20261020-01", "AG-20261020-1", "AG-20261020-1234", "T-1"):
            with self.subTest(bad=bad):
                result = parse(full_row(trade_id=bad))
                error = only_error(result, field="trade_id")
                self.assertEqual(bad, error.trade_id)
                self.assertEqual([bad], list(result.quarantined))

    def test_blank_trade_id_forms_its_own_group(self) -> None:
        result = parse(full_row(trade_id=""), full_row(trade_id="", symbol="NVDA", cost_basis_total="28240.00"))
        self.assertEqual([None, None], [row.trade_id for row in result.rows])
        self.assertEqual(["ROW-2", "ROW-3"], [row.group_id for row in result.rows])

    def test_opened_blank_is_warned(self) -> None:
        row = only_row(parse(full_row(opened="")))
        self.assertIsNone(row.opened)
        self.assertTrue(any(warning.startswith("opened blank") for warning in row.warnings), row.warnings)
        dated = only_row(parse(full_row(opened="2026-10-20")))
        self.assertEqual(datetime.date(2026, 10, 20), dated.opened)
        self.assertEqual((), dated.warnings)
        only_error(parse(full_row(opened="20261020")), field="opened")
        only_error(parse(full_row(opened="2026-13-01")), field="opened")

    def test_underlying_stop(self) -> None:
        row = only_row(parse(full_row(underlying_stop="92.00")))
        self.assertEqual(D("92.00"), row.underlying_stop)
        self.assertIsNone(only_row(parse(full_row())).underlying_stop)
        only_error(parse(full_row(underlying_stop="0")), field="underlying_stop")
        only_error(parse(full_row(underlying_stop="-5")), field="underlying_stop")

    def test_tags_are_extracted_and_unknown_tags_stay_text(self) -> None:
        row = only_row(parse(full_row(note="core holding #horizon:2027-03 #Assign-OK #earnings-ok #from:AG-20261001-04 "
                                           "#roll-of:AG-20261001-05 #custom-tag; #assign-ok.")))
        self.assertEqual(frozenset({"horizon:2027-03", "assign-ok", "earnings-ok", "from:AG-20261001-04",
                                    "roll-of:AG-20261001-05"}), row.tags)
        self.assertTrue(row.assign_ok)
        self.assertTrue(row.earnings_ok)
        self.assertEqual("2027-03", row.horizon)
        self.assertEqual("AG-20261001-04", row.from_trade)
        self.assertEqual("AG-20261001-05", row.roll_of)
        self.assertIn("#custom-tag", row.note, "the note keeps its full text")
        plain = only_row(parse(full_row(note="covered call")))
        self.assertEqual(frozenset(), plain.tags)
        self.assertFalse(plain.assign_ok)
        self.assertIsNone(plain.horizon)

    def test_malformed_known_tags_are_errors(self) -> None:
        for note in ("#horizon:2027", "#horizon:2027-13", "#from:XYZ", "#roll-of:AG-1"):
            with self.subTest(note=note):
                only_error(parse(full_row(note=note)), field="note")

    def test_note_and_trade_id_guard_a_leading_minus(self) -> None:
        only_error(parse(full_row(note="-1+1")), field="note")
        only_error(parse(full_row(trade_id="-AG-20261020-01")), field="trade_id")
        row = only_row(parse(full_row(note="risk-off; see #horizon:2027-03")))
        self.assertEqual("2027-03", row.horizon)

    def test_account_length_and_default(self) -> None:
        row = only_row(parse(full_row(account="")))
        self.assertEqual("default", row.account)


# =============================================================================================
# Option specifics and properties
# =============================================================================================


class OptionRowTest(unittest.TestCase):
    def test_symbol_must_match_the_occ_root(self) -> None:
        error = only_error(parse(short_call(symbol="NVDA")), field="symbol")
        self.assertIn("symbol/occ root mismatch", error.message)
        row = only_row(parse(short_call(symbol="wmt")))
        self.assertEqual("WMT", row.symbol)
        adjusted = only_row(parse(full_row(instrument_type="option", symbol="WMT1", occ_symbol="WMT1261120C00105000",
                                           quantity="-2", cost_basis_total="-684.00")))
        self.assertEqual("WMT", adjusted.symbol, "the root itself is accepted and normalised to the base root")

    def test_components_present_beside_occ_must_agree(self) -> None:
        row = only_row(parse(short_call(expiration="2026-11-20", option_right="call", strike="105")))
        self.assertEqual(D("105.000"), row.strike)
        only_error(parse(short_call(strike="110")), field="strike")
        only_error(parse(short_call(expiration="2026-12-18")), field="expiration")
        only_error(parse(short_call(option_right="PUT")), field="option_right")

    def test_component_validation(self) -> None:
        base = dict(instrument_type="option", symbol="NVDA", quantity="-2", expiration="2026-11-20",
                    option_right="CALL", strike="150", trade_id="AG-20261022-02", opened="2026-10-22",
                    cost_basis_total="-610.00")
        row = only_row(parse(full_row(**base)))
        self.assertEqual("NVDA261120C00150000", row.compact_symbol)
        only_error(parse(full_row(**dict(base, option_right="CAL"))), field="option_right")
        only_error(parse(full_row(**dict(base, expiration="11/20/2026"))), field="expiration")
        only_error(parse(full_row(**dict(base, strike="-150"))), field="strike")
        only_error(parse(full_row(**dict(base, strike="150.0005"))), field="strike")
        only_error(parse(full_row(**dict(base, symbol=""))), field="symbol")
        only_error(parse(full_row(**dict(base, symbol="TOOLONGROOT"))), field="symbol")

    def test_expiration_sanity_window(self) -> None:
        far = only_error(parse(full_row(instrument_type="option", occ_symbol="WMT321120C00105000", quantity="-2",
                                        cost_basis_total="-684.00")), field="expiration")
        self.assertIn("2031-11-02", far.message)
        only_row(parse(full_row(instrument_type="option", occ_symbol="WMT311031C00105000", quantity="-2",
                                cost_basis_total="-684.00")))
        past = only_row(parse(full_row(instrument_type="option", occ_symbol="WMT201120C00105000", quantity="-2",
                                       cost_basis_total="-684.00")))
        self.assertEqual(datetime.date(2020, 11, 20), past.expiration, "expired legs still parse")

    def test_stock_like_rows_reject_option_columns(self) -> None:
        only_error(parse(full_row(occ_symbol="WMT261120C00105000")), field="occ_symbol")
        only_error(parse(full_row(strike="105")), field="strike")
        only_error(parse(full_row(expiration="2026-11-20")), field="expiration")
        only_error(parse(full_row(option_right="CALL")), field="option_right")

    def test_instrument_type_values(self) -> None:
        for value in ("equity", "ETF", "Index", "crypto"):
            with self.subTest(value=value):
                row = only_row(parse(full_row(instrument_type=value, symbol="X")))
                self.assertEqual(value.lower(), row.instrument_type)
                self.assertTrue(row.is_stock_like)
        only_error(parse(full_row(instrument_type="stock")), field="instrument_type")
        only_error(parse(full_row(instrument_type="")), field="instrument_type")

    def test_properties(self) -> None:
        call = only_row(parse(short_call(underlying_stop="98.00")))
        self.assertEqual(("WMT261120C00105000", "WMT", 100, True, False, "short"),
                         (call.compact_symbol, call.underlying, call.multiplier, call.is_option, call.is_stock_like,
                          call.side))
        self.assertTrue(call.is_call)
        self.assertFalse(call.is_put)
        self.assertFalse(call.is_index_underlying)
        self.assertEqual(datetime.date(2026, 11, 20), call.expiration)
        stock = only_row(parse(full_row()))
        self.assertEqual(("WMT", "WMT", 1, False, True, "long"),
                         (stock.compact_symbol, stock.underlying, stock.multiplier, stock.is_option,
                          stock.is_stock_like, stock.side))
        self.assertIsNone(stock.occ)
        self.assertIsNone(stock.expiration)
        self.assertIsNone(stock.right)
        self.assertIsNone(stock.strike)

    def test_index_underlying(self) -> None:
        spx = only_row(parse(full_row(instrument_type="option", symbol="SPX", occ_symbol="SPX261120C06500000",
                                      quantity="1", cost_basis_total="2450.00")))
        self.assertTrue(spx.is_index_underlying)
        spxw = only_row(parse(full_row(instrument_type="option", symbol="", occ_symbol="SPXW261120C06500000",
                                       quantity="1", cost_basis_total="2450.00")))
        self.assertTrue(spxw.is_index_underlying)
        self.assertEqual("SPXW", spxw.underlying)
        index_row = only_row(parse(full_row(instrument_type="index", symbol="NDX", cost_basis_total="100.00",
                                            quantity="1")))
        self.assertTrue(index_row.is_index_underlying)
        self.assertFalse(only_row(parse(full_row())).is_index_underlying)
        self.assertEqual(("SPX", "SPXW", "NDX", "RUT", "VIX", "DJX", "XSP"), positions_csv.INDEX_ROOTS)

    def test_row_is_frozen(self) -> None:
        row = only_row(parse(full_row()))
        with self.assertRaises(Exception):
            row.quantity = D("1")  # type: ignore[misc]


# =============================================================================================
# Quarantine and file-level errors
# =============================================================================================


class QuarantineTest(unittest.TestCase):
    def test_a_row_error_quarantines_the_whole_trade_group(self) -> None:
        result = parse(full_row(), short_call(quantity="-2", total="684.00"),
                       full_row(symbol="NVDA", trade_id="AG-20261022-02", opened="2026-10-22",
                                cost_basis_total="28240.00"))
        self.assertEqual([], result.file_errors)
        self.assertEqual(["AG-20261022-02"], [row.group_id for row in result.rows])
        self.assertEqual(["AG-20261020-01"], list(result.quarantined))
        errors = result.quarantined["AG-20261020-01"]
        self.assertEqual([(3, "cost_basis_total", "AG-20261020-01")],
                         [(error.row_number, error.field, error.trade_id) for error in errors])
        self.assertEqual(3, result.row_count)
        self.assertEqual(errors, result.row_errors)
        self.assertFalse(result.is_clean)

    def test_several_errors_on_one_row_are_all_reported(self) -> None:
        result = parse(full_row(quantity="abc", account="=bad", opened="yesterday"))
        fields = sorted(error.field for error in result.row_errors)
        self.assertEqual(["account", "opened", "quantity"], fields)

    def test_too_many_cells_is_a_row_error(self) -> None:
        error = only_error(parse(full_row(note="a") + ",extra"), field="row")
        self.assertIn("20 cells", error.message)
        self.assertIn("19", error.message)

    def test_fewer_cells_are_padded_like_dictreader(self) -> None:
        row = only_row(parse("equity,WMT,,200,,,,Brokerage (margin),AG-20261020-01,2026-10-20,19700.00"))
        self.assertEqual("open", row.status)
        self.assertEqual("", row.note)

    def test_all_blank_cells_row_is_skipped(self) -> None:
        result = parse(",,,,,,,,,,,,,,,,,,", full_row(), " , , ,,,,,,,,,,,,,,,,")
        self.assertEqual(1, len(result.rows))
        self.assertEqual(1, result.row_count)
        self.assertEqual(3, result.rows[0].row_number)

    def test_quoted_cells(self) -> None:
        row = only_row(parse(full_row(account='"Brokerage, margin"', note='"hello, world #assign-ok"')))
        self.assertEqual("Brokerage, margin", row.account)
        self.assertEqual("hello, world #assign-ok", row.note)
        self.assertTrue(row.assign_ok)

    def test_clean_file_properties(self) -> None:
        result = parse(full_row(), short_call())
        self.assertTrue(result.is_clean)
        self.assertEqual([], result.row_errors)
        self.assertEqual(2, len(result.open_rows))
        self.assertEqual(["AG-20261020-01"], result.group_ids)


class FileLevelTest(unittest.TestCase):
    def test_header_is_lower_cased_and_stripped_and_bom_dropped(self) -> None:
        header = " Instrument_Type , SYMBOL,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened," \
                 "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity," \
                 "close_price_per_unit,underlying_stop,note"
        result = parse_positions_text("﻿" + header + "\n" + full_row() + "\n", today=TODAY)
        self.assertEqual([], result.file_errors)
        self.assertEqual(FULL_HEADER.split(","), result.header)
        self.assertEqual(1, len(result.rows))

    def test_crlf_line_endings(self) -> None:
        result = parse_positions_text(FULL_HEADER + "\r\n" + full_row() + "\r\n", today=TODAY)
        self.assertEqual(1, len(result.rows))

    def test_no_header(self) -> None:
        for text in ("", "\n\n", "# only a comment\n"):
            with self.subTest(text=text):
                result = parse_positions_text(text, today=TODAY)
                self.assertEqual(["header"], [error.field for error in result.file_errors])
                self.assertEqual([], result.rows)
                self.assertEqual([], result.header)

    def test_missing_required_columns(self) -> None:
        result = parse("WMT,200,19700.00", header="symbol,quantity,cost_basis_total")
        self.assertEqual(["header"], [error.field for error in result.file_errors])
        self.assertIn("instrument_type", result.file_errors[0].message)
        result = parse("equity,200,19700.00", header="instrument_type,quantity,cost_basis_total")
        self.assertIn("symbol", result.file_errors[0].message)
        self.assertEqual([], result.rows)

    def test_duplicate_column(self) -> None:
        result = parse("equity,WMT,200,19700.00,1", header="instrument_type,symbol,quantity,cost_basis_total,Quantity")
        self.assertEqual(["duplicate_column"], [error.field for error in result.file_errors])
        self.assertIn("quantity", result.file_errors[0].message)
        self.assertEqual(1, result.file_errors[0].row_number)
        self.assertEqual([], result.rows)

    def test_did_you_mean(self) -> None:
        result = parse("equity,WMT,200,19700.00", header="instrument_type,symbol,quantity,cost_basis_totl")
        self.assertEqual(["did_you_mean"], [error.field for error in result.file_errors])
        self.assertEqual("unknown column 'cost_basis_totl': did you mean 'cost_basis_total'?",
                         result.file_errors[0].message)
        self.assertEqual([], result.rows)
        for typo in ("notes", "trade-id", "staus", "close_price_per_unti", "underlying_stp", "opend"):
            with self.subTest(typo=typo):
                result = parse(full_row() + ",x", header=FULL_HEADER + "," + typo)
                self.assertEqual(["did_you_mean"], [error.field for error in result.file_errors], typo)
        far = parse(full_row() + ",x", header=FULL_HEADER + ",broker_reference")
        self.assertEqual([], far.file_errors)

    def test_damerau_levenshtein(self) -> None:
        distance = positions_csv.damerau_levenshtein
        self.assertEqual(0, distance("note", "note"))
        self.assertEqual(1, distance("note", "notes"))
        self.assertEqual(1, distance("ab", "ba"), "a transposition is one edit")
        self.assertEqual(2, distance("cost_basis_tol", "cost_basis_total"))
        self.assertEqual(4, distance("", "note"))
        self.assertEqual(3, distance("kitten", "sitting"))

    def test_encoding_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-csv-") as tmp:
            path = os.path.join(tmp, "positions.csv")
            with open(path, "wb") as handle:
                handle.write((FULL_HEADER + "\n" + full_row(note="caf")).encode("utf-8") + b"\xe9\n")
            result = positions_csv.load_positions(path, today=TODAY)
        self.assertEqual(["encoding"], [error.field for error in result.file_errors])
        self.assertEqual([], result.rows)

    def test_null_byte_line_is_a_row_error_not_a_crash(self) -> None:
        result = parse(full_row(), "\x00" + short_call())
        self.assertEqual([], result.file_errors)
        self.assertEqual(1, len(result.rows))
        errors = result.row_errors
        self.assertEqual(1, len(errors))
        self.assertEqual((3, "row"), (errors[0].row_number, errors[0].field))

    def test_header_only_file_is_empty_not_an_error(self) -> None:
        result = parse_positions_text(FULL_HEADER + "\n# comment\n", today=TODAY)
        self.assertEqual(([], [], {}, 0), (result.rows, result.file_errors, result.quarantined, result.row_count))
        self.assertTrue(result.is_clean)

    def test_load_positions_matches_text_parse_and_hashes_the_bytes(self) -> None:
        text = FULL_HEADER + "\n" + full_row() + "\n" + short_call(premium="3.42") + "\n"
        with tempfile.TemporaryDirectory(prefix="aegis-csv-") as tmp:
            path = os.path.join(tmp, "positions.csv")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            loaded = positions_csv.load_positions(path, today=TODAY)
            with self.assertRaises(OSError):
                positions_csv.load_positions(os.path.join(tmp, "missing.csv"), today=TODAY)
        parsed = parse_positions_text(text, today=TODAY)
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), loaded.sha256)
        self.assertEqual(parsed.sha256, loaded.sha256)
        self.assertEqual(parsed.rows, loaded.rows)
        self.assertEqual(path, loaded.path)
        self.assertEqual("<text>", parsed.path)

    def test_today_defaults_to_the_current_utc_date(self) -> None:
        result = parse_positions_text(FULL_HEADER + "\n" + short_call() + "\n")
        self.assertEqual(1, len(result.rows), result.quarantined)

    def test_row_error_str(self) -> None:
        error = RowError(3, "quantity", "must be a finite non-zero number", "AG-20261020-01")
        self.assertEqual("row 3 quantity: must be a finite non-zero number (AG-20261020-01)", str(error))
        self.assertEqual("row 1 header: no header row", str(RowError(1, "header", "no header row", None)))


# =============================================================================================
# The fixture book of record (F0 writes the rows; skip while the file is header-only)
# =============================================================================================


class ParseBytesTest(unittest.TestCase):
    """``parse_positions_bytes``: the parse of the bytes a tool has already read (recon finding 5: one read of
    positions.csv per run, so the snapshot, the table and the rows describe one byte string)."""

    def test_bytes_parse_like_the_file_and_hash_the_bytes(self) -> None:
        text = FULL_HEADER + "\n" + full_row() + "\n"
        data = ("\ufeff" + text).encode("utf-8")
        result = positions_csv.parse_positions_bytes(data, "positions.csv", today=TODAY)
        self.assertEqual(([], 1, hashlib.sha256(data).hexdigest(), "positions.csv"),
                         (result.file_errors, len(result.rows), result.sha256, result.path))
        self.assertEqual("WMT", result.rows[0].symbol, "the BOM is dropped")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "positions.csv")
            with open(path, "wb") as handle:
                handle.write(data)
            loaded = positions_csv.load_positions(path, today=TODAY)
        self.assertEqual((loaded.sha256, loaded.row_count, [row.as_dict() for row in loaded.rows]),
                         (result.sha256, result.row_count, [row.as_dict() for row in result.rows]))

    def test_invalid_utf8_bytes_are_the_encoding_file_error(self) -> None:
        data = (FULL_HEADER + "\n").encode("utf-8") + b"equity,WMT,,200,,,,\xff,AG-20261020-01,,19700.00,,,open,,,,,\n"
        result = positions_csv.parse_positions_bytes(data, today=TODAY)
        self.assertEqual(([], ["encoding"], hashlib.sha256(data).hexdigest()),
                         (result.rows, [error.field for error in result.file_errors], result.sha256))
        with self.assertRaises(TypeError):
            positions_csv.parse_positions_bytes(FULL_HEADER, today=TODAY)  # type: ignore[arg-type]


def fixture_data_rows() -> List[Sequence[str]]:
    with open(FIXTURE_CSV, encoding="utf-8", newline="") as handle:
        text = handle.read()
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return list(csv.reader(io.StringIO("\n".join(lines[1:]))))


class FixtureFileTest(unittest.TestCase):
    def setUp(self) -> None:
        if not fixture_data_rows():
            self.skipTest("F0 pending: tests/fixtures/desk/positions.csv has no data rows yet")
        self.result = positions_csv.load_positions(FIXTURE_CSV, today=TODAY)

    def test_parse_matches_the_addendum(self) -> None:
        result = self.result
        self.assertEqual([], result.file_errors)
        self.assertEqual({}, result.quarantined)
        self.assertEqual(7, result.row_count)
        self.assertEqual(7, len(result.rows))
        self.assertEqual(FULL_HEADER.split(","), result.header)
        self.assertEqual(["AG-20261020-01", "AG-20261022-02", "AG-20261028-03", "AG-20261001-04"], result.group_ids)
        self.assertEqual(list(range(3, 10)), [row.row_number for row in result.rows])

    def test_rows_in_detail(self) -> None:
        wmt, wmt_call, nvda, nvda_call, spy, spy_put, closed = self.result.rows
        self.assertEqual(("equity", "WMT", D("200"), D("19700.00"), D("98.50"), D("92.00")),
                         (wmt.instrument_type, wmt.symbol, wmt.quantity, wmt.cost_basis_total,
                          wmt.cost_basis_per_unit, wmt.underlying_stop))
        self.assertEqual(frozenset({"horizon:2027-03"}), wmt.tags)
        self.assertEqual("Brokerage (margin)", wmt.account)
        self.assertEqual(("WMT261120C00105000", D("-2"), D("-684.00"), D("3.42"), D("3.42")),
                         (wmt_call.compact_symbol, wmt_call.quantity, wmt_call.cost_basis_total,
                          wmt_call.open_price_per_share, wmt_call.entry_price_per_share))
        self.assertTrue(wmt_call.assign_ok)
        self.assertEqual(D("28240.00"), nvda.cost_basis_total)
        self.assertEqual(("NVDA261120C00150000", D("150.000"), "CALL", datetime.date(2026, 11, 20), D("-610.00")),
                         (nvda_call.compact_symbol, nvda_call.strike, nvda_call.right, nvda_call.expiration,
                          nvda_call.cost_basis_total))
        self.assertTrue(nvda_call.earnings_ok)
        self.assertEqual(("Roth IRA", "etf", D("63240.00")), (spy.account, spy.instrument_type, spy.cost_basis_total))
        self.assertEqual(("SPY261218P00620000", D("1"), D("1130.00"), D("11.30")),
                         (spy_put.compact_symbol, spy_put.quantity, spy_put.cost_basis_total,
                          spy_put.open_price_per_share))
        self.assertTrue(closed.is_closed)
        self.assertEqual(("SPY261016P00600000", D("-1"), D("-480.00"), datetime.date(2026, 10, 14), D("1"), D("0.55")),
                         (closed.compact_symbol, closed.quantity, closed.cost_basis_total, closed.closed,
                          closed.closed_quantity, closed.close_price_per_unit))
        realized = D(-1) * closed.closed_quantity * closed.multiplier * closed.close_price_per_unit - closed.closed_cost_basis
        self.assertEqual(D("425.00"), realized)
        self.assertEqual(6, len(self.result.open_rows))
        for row in self.result.rows:
            self.assertEqual((), tuple(w for w in row.warnings if not w.startswith("opened blank")), row)


if __name__ == "__main__":
    unittest.main()
