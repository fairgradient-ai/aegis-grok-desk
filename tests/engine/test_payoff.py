"""Payoff kernels (``aegis_desk.payoff``): intrinsic value, leg and group payoff at expiry, the piecewise-linear
grid profile (max profit, max loss, breakevens, unbounded tails) and the closed forms of every named structure,
asserted EQUAL to the grid result. Every number is a Decimal; the module never calls ``float(``."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import os
import re
import unittest
from decimal import Decimal
from typing import List

from aegis_desk import payoff
from aegis_desk.grouping import PositionGroup, group_positions
from aegis_desk.payoff import PayoffProfile
from aegis_desk.positions_csv import parse_positions_text

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULE_PATH = os.path.join(ROOT, "scripts", "aegis_desk", "payoff.py")
TODAY = datetime.date(2026, 11, 3)
HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
          "close_price_per_unit,underlying_stop,note")
D = Decimal
ACCOUNT = "Brokerage (margin)"
TRADE = "AG-20261020-01"
WMT_CALL_105 = "WMT261120C00105000"
WMT_CALL_110 = "WMT261120C00110000"
WMT_CALL_100 = "WMT261120C00100000"
WMT_CALL_DEC = "WMT261218C00105000"
WMT_PUT_95 = "WMT261120P00095000"
WMT_PUT_100 = "WMT261120P00100000"
SPY_PUT_620 = "SPY261218P00620000"


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


def summary(profile: PayoffProfile) -> tuple:
    return (profile.max_profit, profile.max_loss, profile.breakevens, profile.unbounded_up, profile.unbounded_down)


class IntrinsicTest(unittest.TestCase):
    def test_call_and_put_intrinsic_per_share(self) -> None:
        self.assertEqual(D("5.00"), payoff.intrinsic("CALL", D("105.000"), D("110.00")))
        self.assertEqual(D(0), payoff.intrinsic("CALL", D("105.000"), D("100.00")))
        self.assertEqual(D(0), payoff.intrinsic("CALL", D("105.000"), D("105.000")))
        self.assertEqual(D("20.00"), payoff.intrinsic("PUT", D("620.000"), D("600.00")))
        self.assertEqual(D(0), payoff.intrinsic("PUT", D("620.000"), D("650.00")))

    def test_intrinsic_is_decimal_and_rejects_bad_inputs(self) -> None:
        value = payoff.intrinsic("CALL", D("105"), D("107.5"))
        self.assertIsInstance(value, Decimal)
        self.assertEqual(D("2.5"), value)
        with self.assertRaises(ValueError):
            payoff.intrinsic("call", D("105"), D("110"))
        with self.assertRaises(ValueError):
            payoff.intrinsic("CALL", D("105"), D("-1"))
        with self.assertRaises(TypeError):
            payoff.intrinsic("CALL", 105.0, D("110"))  # type: ignore[arg-type]


class LegPayoffTest(unittest.TestCase):
    def test_stock_leg_is_quantity_times_spot(self) -> None:
        group = one(stock())
        self.assertEqual(D("22000.00"), payoff.leg_payoff(group.legs[0], D("110.00")))
        self.assertEqual(D(0), payoff.leg_payoff(group.legs[0], D(0)))

    def test_option_leg_is_signed_contracts_times_multiplier_times_intrinsic(self) -> None:
        short_calls = one(option(WMT_CALL_105, "-2", "-684.00")).legs[0]
        self.assertEqual(D("-1000.00"), payoff.leg_payoff(short_calls, D("110.00")))
        self.assertEqual(D(0), payoff.leg_payoff(short_calls, D("100.00")))
        long_put = one(option(SPY_PUT_620, "1", "1130.00", symbol="SPY")).legs[0]
        self.assertEqual(D("2000.00"), payoff.leg_payoff(long_put, D("600.00")))

    def test_partially_closed_row_uses_the_open_quantity(self) -> None:
        leg = one(option(WMT_CALL_105, "2", "640.00", closed="2026-10-30", closed_quantity="1",
                         close_price="4.00")).legs[0]
        self.assertEqual(D(1), leg.open_quantity)
        self.assertEqual(D("500.00"), payoff.leg_payoff(leg, D("110.00")))

    def test_closed_row_has_no_payoff(self) -> None:
        leg = one(option(WMT_CALL_105, "2", "640.00", status="closed", closed="2026-10-30", close_price="4.00")).legs[0]
        self.assertEqual(D(0), payoff.leg_payoff(leg, D("110.00")))


class GroupPayoffTest(unittest.TestCase):
    def test_covered_call_payoff_at_expiry(self) -> None:
        group = one(stock(), option(WMT_CALL_105, "-2", "-684.00"))
        self.assertEqual(D("1984.00"), payoff.group_payoff(group, D("105.00")))
        self.assertEqual(D("1984.00"), payoff.group_payoff(group, D("130.00")))
        self.assertEqual(D("-19016.00"), payoff.group_payoff(group, D(0)))
        self.assertEqual(D("0.00"), payoff.group_payoff(group, D("95.08")))

    def test_mixed_expiries_and_closed_only_groups_have_no_common_expiry(self) -> None:
        mixed = one(option(WMT_CALL_105, "-1", "-342.00"), option(WMT_CALL_DEC, "1", "500.00"))
        self.assertIsNone(payoff.group_payoff(mixed, D("110.00")))
        closed = one(option(WMT_PUT_95, "-1", "-165.00", status="closed", closed="2026-10-30", close_price="0.55"))
        self.assertIsNone(payoff.group_payoff(closed, D("90.00")))


class GridProfileTest(unittest.TestCase):
    def test_covered_call_grid_points_and_extremes(self) -> None:
        group = one(stock(), option(WMT_CALL_105, "-2", "-684.00"))
        profile = payoff.payoff_profile(group)
        self.assertEqual((D("1984.00"), D("19016.00"), [D("95.08")], False, False), summary(profile))
        self.assertIsNone(profile.reason)
        spots = [spot for spot, _ in profile.grid]
        self.assertEqual([D(0), D("105.000"), D("315.000")], spots)
        self.assertEqual([D("-19016.00"), D("1984.00"), D("1984.00")], [value for _, value in profile.grid])
        for value in (profile.max_profit, profile.max_loss, profile.breakevens[0]):
            self.assertIsInstance(value, Decimal)

    def test_uncovered_shares_make_the_upside_unbounded(self) -> None:
        group = one(stock(quantity="300", total="29550.00"), option(WMT_CALL_105, "-2", "-684.00"))
        self.assertEqual("covered_call", group.structure)
        profile = payoff.payoff_profile(group)
        self.assertEqual((None, D("28866.00"), [D("96.22")], True, False), summary(profile))

    def test_short_call_alone_is_undefined_risk(self) -> None:
        profile = payoff.payoff_profile(one(option(WMT_CALL_105, "-2", "-684.00")))
        self.assertEqual((D("684.00"), None, [D("108.42")], False, True), summary(profile))

    def test_breakeven_interpolation_rounds_to_four_decimals_when_it_does_not_terminate(self) -> None:
        profile = payoff.payoff_profile(one(option(WMT_CALL_100, "3", "1000.00")))
        self.assertEqual([D("103.3333")], profile.breakevens)
        self.assertEqual((None, D("1000.00"), True, False),
                         (profile.max_profit, profile.max_loss, profile.unbounded_up, profile.unbounded_down))

    def test_grid_kernel_handles_a_custom_single_expiry_structure(self) -> None:
        straddle = one(option(WMT_CALL_105, "1", "342.00"), option(WMT_PUT_100, "1", "330.00"))
        self.assertEqual("custom", straddle.structure)
        profile = payoff.grid_profile(straddle.open_legs, straddle.net_cost)
        self.assertEqual((None, D("672.00"), [D("93.28"), D("111.72")], True, False), summary(profile))
        self.assertIsNone(profile.reason)

    def test_a_structure_that_cannot_lose_has_zero_max_loss(self) -> None:
        # Long shares at 90 with a 95 put costing 1.00: the floor is above the cost.
        group = one(stock(quantity="100", total="9000.00"), option(WMT_PUT_95, "1", "100.00"))
        profile = payoff.payoff_profile(group)
        self.assertEqual((None, D("0.00"), [], True, False), summary(profile))

    def test_max_loss_is_reported_as_a_positive_number(self) -> None:
        profile = payoff.payoff_profile(one(option(WMT_PUT_95, "1", "165.00")))
        self.assertEqual(D("165.00"), profile.max_loss)
        self.assertGreater(profile.max_loss, 0)


class ClosedFormEqualsGridTest(unittest.TestCase):
    """Every named structure: the closed form and the grid agree exactly, and both match the hand-computed golden."""

    def check(self, group: PositionGroup, expected: tuple) -> None:
        grid = payoff.payoff_profile(group)
        closed = payoff.closed_form_profile(group)
        self.assertIsNotNone(closed, group.structure)
        self.assertEqual(expected, summary(grid), "grid %s" % group.structure)
        self.assertEqual(summary(grid), summary(closed), "closed form %s" % group.structure)

    def test_covered_call(self) -> None:
        group = one(stock(), option(WMT_CALL_105, "-2", "-684.00"))
        self.check(group, (D("1984.00"), D("19016.00"), [D("95.08")], False, False))

    def test_cash_secured_put(self) -> None:
        group = one(option(WMT_PUT_95, "-1", "-165.00"))
        self.check(group, (D("165.00"), D("9335.00"), [D("93.35")], False, False))

    def test_protective_put(self) -> None:
        group = one(stock(quantity="100", total="63240.00", symbol="SPY", instrument_type="etf"),
                    option(SPY_PUT_620, "1", "1130.00", symbol="SPY"))
        self.assertEqual("protective_put", group.structure)
        self.check(group, (None, D("2370.00"), [D("643.70")], True, False))

    def test_collar(self) -> None:
        group = one(stock(), option(WMT_PUT_95, "2", "330.00"), option(WMT_CALL_105, "-2", "-684.00"))
        self.assertEqual("collar", group.structure)
        self.check(group, (D("1654.00"), D("346.00"), [D("96.73")], False, False))

    def test_vertical_credit_call(self) -> None:
        group = one(option(WMT_CALL_105, "-2", "-684.00"), option(WMT_CALL_110, "2", "240.00"))
        self.assertEqual("vertical_credit_call", group.structure)
        self.check(group, (D("444.00"), D("556.00"), [D("107.22")], False, False))

    def test_vertical_credit_put(self) -> None:
        group = one(option(WMT_PUT_100, "-1", "-330.00"), option(WMT_PUT_95, "1", "165.00"))
        self.assertEqual("vertical_credit_put", group.structure)
        self.check(group, (D("165.00"), D("335.00"), [D("98.35")], False, False))

    def test_vertical_debit_call(self) -> None:
        group = one(option(WMT_CALL_105, "2", "684.00"), option(WMT_CALL_110, "-2", "-240.00"))
        self.assertEqual("vertical_debit_call", group.structure)
        self.check(group, (D("556.00"), D("444.00"), [D("107.22")], False, False))

    def test_vertical_debit_put(self) -> None:
        group = one(option(WMT_PUT_100, "1", "330.00"), option(WMT_PUT_95, "-1", "-165.00"))
        self.assertEqual("vertical_debit_put", group.structure)
        self.check(group, (D("335.00"), D("165.00"), [D("98.35")], False, False))

    def test_long_call(self) -> None:
        self.check(one(option(WMT_CALL_105, "2", "684.00")), (None, D("684.00"), [D("108.42")], True, False))

    def test_long_put(self) -> None:
        self.check(one(option(WMT_PUT_95, "1", "165.00")), (D("9335.00"), D("165.00"), [D("93.35")], False, False))

    def test_short_call(self) -> None:
        self.check(one(option(WMT_CALL_105, "-2", "-684.00")), (D("684.00"), None, [D("108.42")], False, True))

    def test_long_and_short_stock(self) -> None:
        self.check(one(stock()), (None, D("19700.00"), [D("98.50")], True, False))
        self.check(one(stock(quantity="-100", total="-9850.00")), (D("9850.00"), None, [D("98.50")], False, True))

    def test_non_terminating_debit_per_share(self) -> None:
        self.check(one(option(WMT_CALL_100, "3", "1000.00")), (None, D("1000.00"), [D("103.3333")], True, False))

    def test_two_lots_of_one_contract_aggregate(self) -> None:
        group = one(option(WMT_PUT_95, "-1", "-165.00"), option(WMT_PUT_95, "-1", "-175.00", opened="2026-10-21"))
        self.assertEqual("cash_secured_put", group.structure)
        self.check(group, (D("340.00"), D("18660.00"), [D("93.30")], False, False))

    def test_covered_call_whose_credit_exceeds_the_stock_cost_has_no_breakeven(self) -> None:
        # Money finding 3: 200 shares at 1.00 with two 95 calls received at 3.42 a share: net cost -484.00,
        # so the closed form's s0 - p is -2.42. A spot of zero (or below) is never a breakeven: the grid
        # reports none and the closed form must agree, with max loss 0.00 (the structure cannot lose).
        group = one(stock(total="200.00"), option("WMT261120C00095000", "-2", "-684.00"))
        self.assertEqual(("covered_call", D("-484.00")), (group.structure, group.net_cost))
        self.check(group, (D("19484.00"), D("0.00"), [], False, False))
        grid = payoff.payoff_profile(group).grid
        self.assertEqual([D(0), D("95.000"), D("285.000")], [spot for spot, _ in grid])
        self.assertEqual([D("484.00"), D("19484.00"), D("19484.00")], [value for _, value in grid],
                         "the payoff is above zero everywhere: nothing crosses, so there is no breakeven")

    def test_closed_form_is_none_for_custom_structures(self) -> None:
        straddle = one(option(WMT_CALL_105, "1", "342.00"), option(WMT_PUT_100, "1", "330.00"))
        self.assertIsNone(payoff.closed_form_profile(straddle))
        uncovered = one(stock(quantity="300", total="29550.00"), option(WMT_CALL_105, "-2", "-684.00"))
        self.assertIsNone(payoff.closed_form_profile(uncovered), "no closed form once shares exceed the calls")


class ProfilePolicyTest(unittest.TestCase):
    def assert_blank(self, profile: PayoffProfile, reason: str) -> None:
        self.assertEqual((None, None, [], False, False), summary(profile))
        self.assertEqual([], profile.grid)
        self.assertEqual(reason, profile.reason)

    def test_custom_structure_has_no_metrics(self) -> None:
        straddle = one(option(WMT_CALL_105, "1", "342.00"), option(WMT_PUT_100, "1", "330.00"))
        self.assert_blank(payoff.payoff_profile(straddle), payoff.REASON_CUSTOM)
        self.assertEqual("custom structure", payoff.REASON_CUSTOM)

    def test_mixed_expiries_have_no_metrics(self) -> None:
        mixed = one(stock(), option(WMT_CALL_105, "-1", "-342.00"), option(WMT_PUT_95, "1", "165.00"),
                    option("WMT261218P00095000", "1", "300.00"))
        self.assertEqual("custom", mixed.structure)
        self.assertEqual(2, len(mixed.expiries))
        self.assert_blank(payoff.payoff_profile(mixed), payoff.REASON_MIXED_EXPIRIES)
        self.assert_blank(payoff.grid_profile(mixed.open_legs, mixed.net_cost), payoff.REASON_MIXED_EXPIRIES)

    def test_closed_only_group_has_no_metrics(self) -> None:
        closed = one(option(WMT_PUT_95, "-1", "-165.00", status="closed", closed="2026-10-30", close_price="0.55"))
        self.assert_blank(payoff.payoff_profile(closed), payoff.REASON_NO_OPEN_LEGS)

    def test_quarantined_group_has_no_metrics(self) -> None:
        parsed = parse_positions_text("\n".join([HEADER, stock(), option(WMT_CALL_105, "-2", "-684.00",
                                                                           account="Roth IRA")]) + "\n", today=TODAY)
        group = group_positions(parsed.rows)[0]
        self.assertEqual("invalid", group.structure)
        self.assert_blank(payoff.payoff_profile(group), payoff.REASON_QUARANTINED)


class DecimalConventionTest(unittest.TestCase):
    def test_module_never_converts_to_float(self) -> None:
        with open(MODULE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIsNone(re.search(r"\bfloat\(", source), "payoff.py must stay Decimal-only")

    def test_quantizers(self) -> None:
        self.assertEqual(D("1984.00"), payoff.quantize_money(D("1984.000")))
        self.assertEqual(D("0.12"), payoff.quantize_money(D("0.125")), "half-even to the cent")
        self.assertEqual(D("95.08"), payoff.quantize_price(D("95.08")))
        self.assertEqual("95.08", str(payoff.quantize_price(D("95.08"))), "an exact price keeps its written digits")
        self.assertEqual(D("103.3333"), payoff.quantize_price(D(1000) / D(300) + D(100)))
        self.assertEqual(D("3.425"), payoff.quantize_price(D("3.425")))
        self.assertEqual("643.70", str(payoff.quantize_price(D("620.000") + D("23.7"))), "a 3-dp strike prints in cents")
        self.assertEqual("3.425", str(payoff.quantize_price(D("620.000") + D("3.425") - D("620"))))


if __name__ == "__main__":
    unittest.main()
