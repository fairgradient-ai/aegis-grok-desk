"""Leg grouping (``aegis_desk.grouping``): rows share a group by ``group_id``; the structure is decided
by the pattern of OPEN legs (closed rows stay for realized P/L); account or underlying mismatches
quarantine the group. Every structure of the brief's table has a case; the fixture book of record is
asserted once F0's rows exist (skip-guard otherwise).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import csv
import datetime
import io
import os
import unittest
from decimal import Decimal
from typing import List, Sequence

from aegis_desk import grouping, positions_csv
from aegis_desk.grouping import PositionGroup, group_positions
from aegis_desk.positions_csv import parse_positions_text

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_CSV = os.path.join(ROOT, "tests", "fixtures", "desk", "positions.csv")
MODULE_PATH = os.path.join(ROOT, "scripts", "aegis_desk", "grouping.py")
TODAY = datetime.date(2026, 11, 2)
HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
          "close_price_per_unit,underlying_stop,note")
D = Decimal
ACCOUNT = "Brokerage (margin)"
TRADE = "AG-20261020-01"


def row(instrument_type: str = "equity", symbol: str = "WMT", occ_symbol: str = "", quantity: str = "200",
        account: str = ACCOUNT, trade_id: str = TRADE, opened: str = "2026-10-20", total: str = "19700.00",
        status: str = "open", closed: str = "", closed_quantity: str = "", close_price: str = "",
        underlying_stop: str = "", note: str = "") -> str:
    cells = [instrument_type, symbol, occ_symbol, quantity, "", "", "", account, trade_id, opened, total, "", "",
             status, closed, closed_quantity, close_price, underlying_stop, note]
    return ",".join(cells)


def stock(quantity: str = "200", total: str = "19700.00", **extra: str) -> str:
    return row(quantity=quantity, total=total, **extra)


def option(occ_symbol: str, quantity: str, total: str, symbol: str = "", **extra: str) -> str:
    return row(instrument_type="option", symbol=symbol, occ_symbol=occ_symbol, quantity=quantity, total=total, **extra)


def groups_of(*rows: str) -> List[PositionGroup]:
    parsed = parse_positions_text("\n".join([HEADER] + list(rows)) + "\n", today=TODAY)
    assert parsed.file_errors == [], parsed.file_errors
    assert parsed.quarantined == {}, parsed.quarantined
    return group_positions(parsed.rows)


def one(*rows: str) -> PositionGroup:
    groups = groups_of(*rows)
    assert len(groups) == 1, groups
    return groups[0]


WMT_CALL = "WMT261120C00105000"
WMT_CALL_110 = "WMT261120C00110000"
WMT_CALL_DEC = "WMT261218C00105000"
WMT_PUT_95 = "WMT261120P00095000"
WMT_PUT_100 = "WMT261120P00100000"
WMT_PUT_DEC = "WMT261218P00100000"


class SingleLegStructuresTest(unittest.TestCase):
    def test_long_and_short_stock(self) -> None:
        long = one(stock())
        self.assertEqual(("long_stock", "stock", D("19700.00"), False), (long.structure, long.basis_kind,
                                                                           long.net_cost, long.is_credit))
        self.assertEqual((TRADE, TRADE, ACCOUNT, "WMT"), (long.group_id, long.trade_id, long.account, long.underlying))
        self.assertEqual(1, len(long.open_legs))
        self.assertEqual([], long.closed_legs)
        self.assertEqual(1, len(long.stock_legs))
        self.assertEqual([], long.open_option_legs)
        short = one(stock(quantity="-100", total="-9850.00"))
        self.assertEqual(("short_stock", "stock"), (short.structure, short.basis_kind))

    def test_long_call_and_long_put(self) -> None:
        call = one(option(WMT_CALL, "2", "640.00"))
        self.assertEqual(("long_call", "debit", D("640.00"), False), (call.structure, call.basis_kind, call.net_cost,
                                                                     call.is_credit))
        put = one(option(WMT_PUT_95, "1", "165.00"))
        self.assertEqual(("long_put", "debit"), (put.structure, put.basis_kind))

    def test_short_call_is_undefined_risk_and_cash_secured_put_warns(self) -> None:
        call = one(option(WMT_CALL, "-2", "-684.00"))
        self.assertEqual(("short_call", "credit", D("-684.00"), True), (call.structure, call.basis_kind, call.net_cost,
                                                                       call.is_credit))
        put = one(option(WMT_PUT_95, "-1", "-165.00"))
        self.assertEqual(("cash_secured_put", "credit", True), (put.structure, put.basis_kind, put.is_credit))
        self.assertIn("cash reserve not verified", put.warnings)
        self.assertEqual([], call.warnings)

    def test_underlying_is_the_occ_base_root(self) -> None:
        group = one(option("WMT1261120C00105000", "-1", "-342.00"))
        self.assertEqual("WMT", group.underlying)


class StockPlusOptionStructuresTest(unittest.TestCase):
    def test_covered_call(self) -> None:
        group = one(stock(underlying_stop="92.00"), option(WMT_CALL, "-2", "-684.00", note="#assign-ok"))
        self.assertEqual(("covered_call", "stock", D("19016.00")), (group.structure, group.basis_kind, group.net_cost))
        self.assertEqual([], group.warnings)
        self.assertEqual(D("92.00"), group.underlying_stop)
        self.assertEqual(2, len(group.open_legs))
        self.assertEqual(1, len(group.open_option_legs))
        self.assertEqual(1, len(group.stock_legs))
        self.assertEqual([datetime.date(2026, 11, 20)], group.expiries)

    def test_covered_call_any_expiry_and_extra_shares_warn(self) -> None:
        group = one(stock(quantity="300", total="29550.00"), option(WMT_CALL_DEC, "-2", "-684.00"))
        self.assertEqual("covered_call", group.structure)
        self.assertEqual(["uncovered shares: 100 of 300 shares are not covered by the 2 short calls"], group.warnings)

    def test_covered_call_from_two_rows_of_the_same_contract(self) -> None:
        group = one(stock(), option(WMT_CALL, "-1", "-342.00"), option(WMT_CALL, "-1", "-345.00", opened="2026-10-21"))
        self.assertEqual("covered_call", group.structure)
        self.assertEqual(D("19013.00"), group.net_cost)
        self.assertEqual([], group.warnings)

    def test_short_calls_beyond_the_shares_are_custom(self) -> None:
        group = one(stock(quantity="100", total="9850.00"), option(WMT_CALL, "-2", "-684.00"))
        self.assertEqual("custom", group.structure)
        self.assertIn("short calls not fully covered: 100 shares cover 1 of 2 calls", group.warnings)
        self.assertIn("custom structure", group.warnings)

    def test_protective_put(self) -> None:
        group = one(stock(quantity="100", total="9850.00"), option(WMT_PUT_95, "1", "165.00"))
        self.assertEqual(("protective_put", "stock", D("10015.00")), (group.structure, group.basis_kind, group.net_cost))
        self.assertEqual([], group.warnings)
        several = one(stock(), option(WMT_PUT_95, "1", "165.00"), option(WMT_PUT_DEC, "1", "300.00"))
        self.assertEqual("protective_put", several.structure, "long put(s) of any expiry")
        self.assertEqual([datetime.date(2026, 11, 20), datetime.date(2026, 12, 18)], several.expiries)
        partial = one(stock(), option(WMT_PUT_95, "1", "165.00"))
        self.assertEqual(["protective puts cover 100 of 200 shares"], partial.warnings)

    def test_collar(self) -> None:
        group = one(stock(), option(WMT_PUT_95, "2", "330.00"), option(WMT_CALL, "-2", "-684.00"))
        self.assertEqual(("collar", "stock", D("19346.00")), (group.structure, group.basis_kind, group.net_cost))
        self.assertEqual([], group.warnings)
        mixed = one(stock(), option(WMT_PUT_DEC, "2", "600.00"), option(WMT_CALL, "-2", "-684.00"))
        self.assertEqual("collar", mixed.structure)
        self.assertEqual(2, len(mixed.expiries))
        naked = one(stock(quantity="100", total="9850.00"), option(WMT_PUT_95, "1", "165.00"),
                    option(WMT_CALL, "-2", "-684.00"))
        self.assertEqual("custom", naked.structure)


class VerticalStructuresTest(unittest.TestCase):
    def test_credit_and_debit_verticals(self) -> None:
        credit_call = one(option(WMT_CALL, "-2", "-684.00"), option(WMT_CALL_110, "2", "300.00"))
        self.assertEqual(("vertical_credit_call", "credit", D("-384.00"), True),
                         (credit_call.structure, credit_call.basis_kind, credit_call.net_cost, credit_call.is_credit))
        debit_call = one(option(WMT_CALL, "2", "684.00"), option(WMT_CALL_110, "-2", "-300.00"))
        self.assertEqual(("vertical_debit_call", "debit", D("384.00")),
                         (debit_call.structure, debit_call.basis_kind, debit_call.net_cost))
        credit_put = one(option(WMT_PUT_100, "-1", "-250.00"), option(WMT_PUT_95, "1", "165.00"))
        self.assertEqual(("vertical_credit_put", D("-85.00")), (credit_put.structure, credit_put.net_cost))
        debit_put = one(option(WMT_PUT_100, "1", "250.00"), option(WMT_PUT_95, "-1", "-165.00"))
        self.assertEqual(("vertical_debit_put", D("85.00")), (debit_put.structure, debit_put.net_cost))
        self.assertEqual([], credit_call.warnings)

    def test_not_a_vertical(self) -> None:
        mixed_expiry = one(option(WMT_CALL, "-2", "-684.00"), option("WMT261218C00110000", "2", "500.00"))
        self.assertEqual("custom", mixed_expiry.structure)
        unequal = one(option(WMT_CALL, "-2", "-684.00"), option(WMT_CALL_110, "1", "150.00"))
        self.assertEqual("custom", unequal.structure)
        same_sign = one(option(WMT_CALL, "2", "684.00"), option(WMT_CALL_110, "2", "300.00"))
        self.assertEqual("custom", same_sign.structure)
        straddle = one(option(WMT_CALL, "-1", "-342.00"), option(WMT_PUT_100, "1", "250.00"))
        self.assertEqual("custom", straddle.structure)
        three_legs = one(option(WMT_CALL, "-2", "-684.00"), option(WMT_CALL_110, "1", "150.00"),
                         option("WMT261120C00115000", "1", "80.00"))
        self.assertEqual("custom", three_legs.structure)
        self.assertEqual(["custom structure"], three_legs.warnings)

    def test_short_stock_with_options_is_custom(self) -> None:
        group = one(stock(quantity="-100", total="-9850.00"), option(WMT_CALL, "1", "342.00"))
        self.assertEqual("custom", group.structure)


class ClosedLegsTest(unittest.TestCase):
    def test_closed_rows_stay_in_the_group_but_not_in_the_pattern(self) -> None:
        group = one(stock(), option(WMT_CALL, "-2", "-684.00", status="closed", closed="2026-10-30",
                                    closed_quantity="2", close_price="1.20"))
        self.assertEqual("long_stock", group.structure)
        self.assertEqual(2, len(group.legs))
        self.assertEqual(1, len(group.open_legs))
        self.assertEqual(1, len(group.closed_legs))
        self.assertEqual(D("19700.00"), group.net_cost, "net cost is over the open legs")
        self.assertFalse(group.is_closed)

    def test_closed_only_group_keeps_the_structure_its_legs_had(self) -> None:
        group = one(option("SPY261016P00600000", "-1", "-480.00", symbol="SPY", account="Roth IRA",
                           trade_id="AG-20261001-04", opened="2026-10-01", status="closed", closed="2026-10-14",
                           closed_quantity="1", close_price="0.55"))
        self.assertEqual("cash_secured_put", group.structure)
        self.assertEqual([], group.open_legs)
        self.assertTrue(group.is_closed)
        self.assertEqual(1, len(group.closed_legs))
        self.assertEqual(("credit", D("-480.00"), True), (group.basis_kind, group.net_cost, group.is_credit))
        self.assertEqual([], group.warnings, "nothing to verify on a closed group")
        self.assertEqual([], group.errors)

    def test_partially_closed_leg_counts_its_remaining_quantity(self) -> None:
        group = one(stock(), option(WMT_CALL, "-2", "-684.00", closed="2026-10-30", closed_quantity="1",
                                    close_price="1.20"))
        self.assertEqual("covered_call", group.structure)
        self.assertEqual(["uncovered shares: 100 of 200 shares are not covered by the 1 short call"], group.warnings)
        self.assertEqual(D("19358.00"), group.net_cost, "19700 + remaining -342")


class GroupIntegrityTest(unittest.TestCase):
    def test_account_mismatch_quarantines(self) -> None:
        group = one(stock(), option(WMT_CALL, "-2", "-684.00", account="Roth IRA"))
        self.assertEqual("invalid", group.structure)
        self.assertTrue(group.is_quarantined)
        self.assertEqual(1, len(group.errors))
        self.assertIn("account mismatch", group.errors[0])
        self.assertIn("Brokerage (margin)", group.errors[0])
        self.assertIn("Roth IRA", group.errors[0])
        self.assertEqual(tuple(group.errors), group.quarantine_reasons)
        self.assertEqual(ACCOUNT, group.account, "the first row's account names the group")

    def test_underlying_mismatch_quarantines(self) -> None:
        group = one(stock(), option("NVDA261120C00150000", "-2", "-610.00"))
        self.assertEqual("invalid", group.structure)
        self.assertEqual(["underlying mismatch: WMT, NVDA"], group.errors)
        self.assertEqual("WMT", group.underlying)

    def test_valid_groups_have_no_errors(self) -> None:
        group = one(stock(), option(WMT_CALL, "-2", "-684.00"))
        self.assertEqual(([], (), False), (group.errors, group.quarantine_reasons, group.is_quarantined))

    def test_groups_keep_file_order_and_row_groups(self) -> None:
        groups = groups_of(stock(trade_id="AG-20261022-02", symbol="NVDA", total="28240.00"),
                           option(WMT_CALL, "-2", "-684.00"),
                           stock(trade_id=""),
                           option("NVDA261120C00150000", "-2", "-610.00", trade_id="AG-20261022-02"))
        self.assertEqual(["AG-20261022-02", TRADE, "ROW-4"], [group.group_id for group in groups])
        self.assertEqual(["covered_call", "short_call", "long_stock"], [group.structure for group in groups])
        self.assertIsNone(groups[2].trade_id)
        self.assertEqual([2, 5], [leg.row_number for leg in groups[0].legs])
        self.assertEqual([2, 5], groups[0].row_numbers)

    def test_underlying_stop_is_the_minimum_or_none(self) -> None:
        group = one(stock(underlying_stop="92.00"), option(WMT_CALL, "-2", "-684.00", underlying_stop="90.50"))
        self.assertEqual(D("90.50"), group.underlying_stop)
        self.assertIsNone(one(stock()).underlying_stop)

    def test_empty_input(self) -> None:
        self.assertEqual([], group_positions([]))

    def test_structures_constant_and_no_float(self) -> None:
        for structure in ("long_stock", "short_stock", "long_call", "long_put", "short_call", "cash_secured_put",
                          "covered_call", "protective_put", "collar", "vertical_credit_call", "vertical_credit_put",
                          "vertical_debit_call", "vertical_debit_put", "custom", "invalid"):
            self.assertIn(structure, grouping.STRUCTURES)
        self.assertEqual(("credit", "debit", "stock"), grouping.BASIS_KINDS)
        with open(MODULE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("float(", source)
        group = one(stock())
        self.assertIsInstance(group.net_cost, Decimal)


def fixture_data_rows() -> List[Sequence[str]]:
    with open(FIXTURE_CSV, encoding="utf-8", newline="") as handle:
        text = handle.read()
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return list(csv.reader(io.StringIO("\n".join(lines[1:]))))


class FixtureGroupsTest(unittest.TestCase):
    def setUp(self) -> None:
        if not fixture_data_rows():
            self.skipTest("F0 pending: tests/fixtures/desk/positions.csv has no data rows yet")
        parsed = positions_csv.load_positions(FIXTURE_CSV, today=TODAY)
        self.assertEqual(([], {}), (parsed.file_errors, parsed.quarantined))
        self.groups = group_positions(parsed.rows)

    def test_four_groups_as_the_addendum_states(self) -> None:
        self.assertEqual([("AG-20261020-01", "covered_call"), ("AG-20261022-02", "covered_call"),
                          ("AG-20261028-03", "protective_put"), ("AG-20261001-04", "cash_secured_put")],
                         [(group.group_id, group.structure) for group in self.groups])
        wmt, nvda, spy, closed = self.groups
        self.assertEqual((D("19016.00"), "stock", D("92.00"), []), (wmt.net_cost, wmt.basis_kind, wmt.underlying_stop,
                                                                   wmt.warnings))
        self.assertEqual((D("27630.00"), "stock", None), (nvda.net_cost, nvda.basis_kind, nvda.underlying_stop))
        self.assertEqual((D("64370.00"), "stock", "Roth IRA", []), (spy.net_cost, spy.basis_kind, spy.account,
                                                                    spy.warnings))
        self.assertEqual(([], 1, D("-480.00"), "credit"), (closed.open_legs, len(closed.closed_legs), closed.net_cost,
                                                           closed.basis_kind))
        for group in self.groups:
            self.assertEqual(([], False), (group.errors, group.is_quarantined))
        self.assertEqual(3, sum(1 for group in self.groups if not group.is_closed))


if __name__ == "__main__":
    unittest.main()
