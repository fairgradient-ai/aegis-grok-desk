"""P/L arithmetic (``aegis_desk.pnl``): marks per leg from a QuoteQC, unrealized and realized P/L under the
money conventions of plan section 8, day P/L against the desk's own previous snapshot, percent captured and
realized-by-period. Every golden is the brief's worked example; nothing here is ever a float."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import os
import re
import unittest
from decimal import Decimal
from typing import List, Optional

from aegis_desk import market_clock, pnl, quote_qc
from aegis_desk.grouping import PositionGroup, group_positions
from aegis_desk.market_types import Quote
from aegis_desk.positions_csv import PositionRow, parse_positions_text
from aegis_desk.quote_qc import QuoteQC

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULE_PATH = os.path.join(ROOT, "scripts", "aegis_desk", "pnl.py")
FIXTURE_CSV = os.path.join(ROOT, "tests", "fixtures", "desk", "positions.csv")
UTC = datetime.timezone.utc
TODAY = datetime.date(2026, 11, 3)
NOW = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC)
STAMP = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
SESSION = market_clock.session_at(NOW)
HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
          "close_price_per_unit,underlying_stop,note")
D = Decimal
ACCOUNT = "Brokerage (margin)"
TRADE = "AG-20261020-01"
WMT_CALL_105 = "WMT261120C00105000"
WMT_CALL_110 = "WMT261120C00110000"
WMT_PUT_95 = "WMT261120P00095000"


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


def rows_of(*lines: str) -> List[PositionRow]:
    parsed = parse_positions_text("\n".join([HEADER] + list(lines)) + "\n", today=TODAY)
    assert parsed.file_errors == [], parsed.file_errors
    assert parsed.quarantined == {}, parsed.quarantined
    return parsed.rows


def one_row(line: str) -> PositionRow:
    return rows_of(line)[0]


def one_group(*lines: str) -> PositionGroup:
    groups = group_positions(rows_of(*lines))
    assert len(groups) == 1, groups
    return groups[0]


def quote(symbol: str, bid: Optional[str], ask: Optional[str], bid_date: Optional[datetime.datetime] = STAMP,
          ask_date: Optional[datetime.datetime] = STAMP) -> Quote:
    return Quote(symbol=symbol, bid=None if bid is None else D(bid), ask=None if ask is None else D(ask),
                 bid_date=bid_date, ask_date=ask_date)


def qc(symbol: str, bid: Optional[str], ask: Optional[str], bid_date: Optional[datetime.datetime] = STAMP,
       ask_date: Optional[datetime.datetime] = STAMP) -> QuoteQC:
    return quote_qc.assess(quote(symbol, bid, ask, bid_date, ask_date), now=NOW, session=SESSION, bound_min=25,
                           prior_close_utc=None)


class LegMarksTest(unittest.TestCase):
    def test_long_leg_liquidates_at_the_bid(self) -> None:
        marks = pnl.leg_marks(one_row(stock()), qc("WMT", "104.20", "104.26"))
        self.assertEqual((D("104.23"), D("104.20"), True, "A"),
                         (marks.mark_mid, marks.mark_liq, marks.basis_available, marks.grade))
        self.assertEqual(pnl.MARK_SOURCE_MID, marks.mark_source)
        self.assertEqual("mid", marks.mark_source)

    def test_short_leg_closes_at_the_ask(self) -> None:
        marks = pnl.leg_marks(one_row(option(WMT_CALL_105, "-2", "-684.00")), qc(WMT_CALL_105, "3.35", "3.50"))
        self.assertEqual((D("3.425"), D("3.50"), True, "B"),
                         (marks.mark_mid, marks.mark_liq, marks.basis_available, marks.grade))

    def test_grade_e_displays_the_mid_but_is_no_basis(self) -> None:
        verdict = qc(WMT_CALL_105, "0", "0.05")
        self.assertEqual("E", verdict.grade)
        marks = pnl.leg_marks(one_row(option(WMT_CALL_105, "-2", "-684.00")), verdict)
        self.assertEqual((D("0.025"), None, False, "E"), (marks.mark_mid, marks.mark_liq, marks.basis_available,
                                                          marks.grade))
        self.assertEqual(pnl.MARK_SOURCE_UNAVAILABLE, marks.mark_source)
        self.assertEqual("unavailable", marks.mark_source)

    def test_grade_f_and_a_missing_verdict_are_unavailable(self) -> None:
        verdict = qc(WMT_CALL_105, "3.35", None)
        self.assertEqual("F", verdict.grade)
        marks = pnl.leg_marks(one_row(option(WMT_CALL_105, "-2", "-684.00")), verdict)
        self.assertEqual((None, None, False, "F", "unavailable"),
                         (marks.mark_mid, marks.mark_liq, marks.basis_available, marks.grade, marks.mark_source))
        absent = pnl.leg_marks(one_row(stock()), None)
        self.assertEqual((None, None, False, None, "unavailable"),
                         (absent.mark_mid, absent.mark_liq, absent.basis_available, absent.grade, absent.mark_source))

    def test_one_stale_side_keeps_only_the_fresh_side_and_is_no_basis(self) -> None:
        stale_ask = qc("WMT", "104.20", "104.26", ask_date=NOW - datetime.timedelta(minutes=40))
        self.assertEqual(("D", "one_side_stale"), (stale_ask.grade, stale_ask.status))
        long_marks = pnl.leg_marks(one_row(stock()), stale_ask)
        self.assertEqual((None, D("104.20"), False, "unavailable"),
                         (long_marks.mark_mid, long_marks.mark_liq, long_marks.basis_available, long_marks.mark_source))
        short_marks = pnl.leg_marks(one_row(stock(quantity="-100", total="-9850.00")), stale_ask)
        self.assertEqual((None, None, False), (short_marks.mark_mid, short_marks.mark_liq, short_marks.basis_available))


class UnrealizedTest(unittest.TestCase):
    def test_brief_goldens_long_and_short_at_a_gain(self) -> None:
        self.assertEqual(D("160.00"), pnl.unrealized(one_row(option(WMT_CALL_105, "2", "640.00")), D("4.00")))
        self.assertEqual(D("320.00"), pnl.unrealized(one_row(option(WMT_CALL_105, "-2", "-640.00")), D("1.60")))

    def test_long_and_short_at_a_loss(self) -> None:
        self.assertEqual(D("-140.00"), pnl.unrealized(one_row(option(WMT_CALL_105, "2", "640.00")), D("2.50")))
        self.assertEqual(D("-160.00"), pnl.unrealized(one_row(option(WMT_CALL_105, "-2", "-640.00")), D("4.00")))

    def test_stock_and_fixture_marks(self) -> None:
        self.assertEqual(D("1146.00"), pnl.unrealized(one_row(stock()), D("104.23")))
        value = pnl.unrealized(one_row(option(WMT_CALL_105, "-2", "-684.00")), D("3.425"))
        self.assertEqual(D("-1.00"), value)
        self.assertEqual("-1.00", str(value), "money is quantised to the cent")
        self.assertEqual(D("-135.00"), pnl.unrealized(one_row(option("SPY261218P00620000", "1", "1130.00", symbol="SPY")),
                                                      D("9.95")))

    def test_missing_mark_and_closed_rows_are_none(self) -> None:
        self.assertIsNone(pnl.unrealized(one_row(stock()), None))
        closed = one_row(option(WMT_CALL_105, "2", "640.00", status="closed", closed="2026-10-30", close_price="4.00"))
        self.assertIsNone(pnl.unrealized(closed, D("4.00")))

    def test_partial_close_marks_the_open_quantity_against_the_remaining_basis(self) -> None:
        leg = one_row(option(WMT_CALL_105, "2", "640.00", closed="2026-10-30", closed_quantity="1", close_price="4.00"))
        self.assertEqual((D(1), D("320.00")), (leg.open_quantity, leg.remaining_cost_basis))
        self.assertEqual(D("-20.00"), pnl.unrealized(leg, D("3.00")))


class RealizedTest(unittest.TestCase):
    def test_brief_goldens(self) -> None:
        long_closed = one_row(option(WMT_CALL_105, "2", "640.00", status="closed", closed="2026-10-30",
                                     close_price="4.00"))
        self.assertEqual(D("160.00"), pnl.realized_for_row(long_closed))
        short_closed = one_row(option(WMT_CALL_105, "-2", "-640.00", status="closed", closed="2026-10-30",
                                      close_price="1.60"))
        self.assertEqual(D("320.00"), pnl.realized_for_row(short_closed))

    def test_expired_worthless_keeps_the_whole_credit(self) -> None:
        expired = one_row(option(WMT_PUT_95, "-1", "-480.00", status="closed", closed="2026-10-30", close_price="0"))
        self.assertEqual(D("480.00"), pnl.realized_for_row(expired))

    def test_fixture_closed_put_is_425(self) -> None:
        closed = one_row(option("SPY261016P00600000", "-1", "-480.00", symbol="SPY", account="Roth IRA",
                                trade_id="AG-20261001-04", opened="2026-10-01", status="closed", closed="2026-10-14",
                                closed_quantity="1", close_price="0.55"))
        self.assertEqual(D("425.00"), pnl.realized_for_row(closed))

    def test_partial_close_realizes_the_closed_part_pro_rata(self) -> None:
        leg = one_row(option(WMT_CALL_105, "2", "640.00", closed="2026-10-30", closed_quantity="1", close_price="4.00"))
        self.assertEqual(D("80.00"), pnl.realized_for_row(leg))

    def test_open_row_without_a_close_is_none(self) -> None:
        self.assertIsNone(pnl.realized_for_row(one_row(stock())))

    def test_assignment_convention(self) -> None:
        # The option premium stays in option P/L: the assigned short call is closed at 0 and keeps its credit.
        assigned = one_row(option(WMT_CALL_105, "-2", "-684.00", status="closed", closed="2026-11-20",
                                  close_price="0"))
        self.assertEqual(D("684.00"), pnl.realized_for_row(assigned))
        # The shares are booked at the strike: the stock row leaves at 105.00 against its 98.50 cost.
        called_away = one_row(stock(status="closed", closed="2026-11-20", close_price="105.00"))
        self.assertEqual(D("1300.00"), pnl.realized_for_row(called_away))


class DayPlTest(unittest.TestCase):
    def test_from_the_previous_snapshot_mark(self) -> None:
        self.assertEqual(D("84.00"), pnl.day_pl(one_row(stock()), D("104.23"), D("103.81"), False))
        short_calls = one_row(option(WMT_CALL_105, "-2", "-684.00"))
        self.assertEqual(D("-50.00"), pnl.day_pl(short_calls, D("3.425"), D("3.175"), False))

    def test_opened_today_measures_from_the_entry_price(self) -> None:
        self.assertEqual(D("1146.00"), pnl.day_pl(one_row(stock()), D("104.23"), None, True))
        self.assertEqual(D("1146.00"), pnl.day_pl(one_row(stock()), D("104.23"), D("103.81"), True),
                         "the entry price wins over any previous mark on the opening day")
        short_calls = one_row(option(WMT_CALL_105, "-2", "-684.00"))
        self.assertEqual(D("-1.00"), pnl.day_pl(short_calls, D("3.425"), None, True))

    def test_missing_inputs_and_closed_rows_are_none(self) -> None:
        self.assertIsNone(pnl.day_pl(one_row(stock()), D("104.23"), None, False))
        self.assertIsNone(pnl.day_pl(one_row(stock()), None, D("103.81"), False))
        closed = one_row(stock(status="closed", closed="2026-11-03", close_price="104.00"))
        self.assertIsNone(pnl.day_pl(closed, D("104.23"), D("103.81"), False))

    def test_partial_close_moves_the_open_quantity_only(self) -> None:
        leg = one_row(option(WMT_CALL_105, "2", "640.00", closed="2026-10-30", closed_quantity="1", close_price="4.00"))
        self.assertEqual(D("50.00"), pnl.day_pl(leg, D("3.50"), D("3.00"), False))


class GroupArithmeticTest(unittest.TestCase):
    def test_cost_to_close_and_group_unrealized(self) -> None:
        group = one_group(stock(), option(WMT_CALL_105, "-2", "-684.00"))
        marks = {group.legs[0].row_number: D("104.23"), group.legs[1].row_number: D("3.425")}
        self.assertEqual(D("-20161.00"), pnl.cost_to_close(group.open_legs, marks))
        self.assertEqual(D("1145.00"), pnl.group_unrealized(group.open_legs, marks))
        marks[group.legs[1].row_number] = None
        self.assertIsNone(pnl.cost_to_close(group.open_legs, marks))
        self.assertIsNone(pnl.group_unrealized(group.open_legs, marks))

    def test_pct_captured_for_a_credit_group(self) -> None:
        group = one_group(option(WMT_CALL_105, "-2", "-684.00"))
        self.assertTrue(group.is_credit)
        self.assertEqual(D("700.00"), pnl.cost_to_close(group.open_legs, {group.legs[0].row_number: D("3.50")}))
        self.assertEqual(D("-2.34"), pnl.pct_captured(group, D("700.00")))
        self.assertEqual(D("50.00"), pnl.pct_captured(group, D("342.00")))
        self.assertEqual(D("100.00"), pnl.pct_captured(group, D(0)))
        self.assertIsNone(pnl.pct_captured(group, None))

    def test_pct_of_max_profit_for_stock_basis_and_debit_groups(self) -> None:
        covered = one_group(stock(), option(WMT_CALL_105, "-2", "-684.00"))
        self.assertEqual(D("57.71"), pnl.pct_captured(covered, D("-20161.00"), max_profit=D("1984.00")))
        self.assertIsNone(pnl.pct_captured(covered, D("-20161.00")), "no max profit, no percentage")
        debit = one_group(option(WMT_CALL_105, "2", "684.00"), option(WMT_CALL_110, "-2", "-240.00"))
        self.assertEqual("vertical_debit_call", debit.structure)
        # Marks 4.00 and 1.00: closing receives 600, the debit was 444, unrealized 156 of a 556 max profit.
        self.assertEqual(D("28.06"), pnl.pct_captured(debit, D("-600.00"), max_profit=D("556.00")))
        self.assertIsNone(pnl.pct_captured(debit, D("-600.00"), max_profit=D(0)))


class RealizedByPeriodTest(unittest.TestCase):
    def setUp(self) -> None:
        with open(FIXTURE_CSV, encoding="utf-8") as handle:
            self.rows = parse_positions_text(handle.read(), today=TODAY).rows
        self.assertEqual(7, len(self.rows))

    def test_fixture_periods(self) -> None:
        realized = pnl.realized_by_period(self.rows, datetime.date(2026, 11, 3))
        self.assertEqual((D("0.00"), D("0.00"), D("425.00")), (realized.today, realized.mtd, realized.ytd))
        realized = pnl.realized_by_period(self.rows, datetime.date(2026, 10, 14))
        self.assertEqual((D("425.00"), D("425.00"), D("425.00")), (realized.today, realized.mtd, realized.ytd))
        realized = pnl.realized_by_period(self.rows, datetime.date(2026, 10, 31))
        self.assertEqual((D("0.00"), D("425.00"), D("425.00")), (realized.today, realized.mtd, realized.ytd))
        realized = pnl.realized_by_period(self.rows, datetime.date(2027, 1, 5))
        self.assertEqual((D("0.00"), D("0.00"), D("0.00")), (realized.today, realized.mtd, realized.ytd))

    def test_partial_closes_count_on_their_closed_date(self) -> None:
        rows = rows_of(option(WMT_CALL_105, "2", "640.00", closed="2026-11-03", closed_quantity="1", close_price="4.00"))
        realized = pnl.realized_by_period(rows, datetime.date(2026, 11, 3))
        self.assertEqual((D("80.00"), D("80.00"), D("80.00")), (realized.today, realized.mtd, realized.ytd))


class DecimalExactnessTest(unittest.TestCase):
    def test_module_never_converts_to_float(self) -> None:
        with open(MODULE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIsNone(re.search(r"\bfloat\(", source), "pnl.py must stay Decimal-only")

    def test_results_are_decimals_quantised_to_the_cent(self) -> None:
        leg = one_row(option(WMT_CALL_105, "-1", "-342.00"))
        for value in (pnl.unrealized(leg, D("3.425")), pnl.day_pl(leg, D("3.425"), D("3.175"), False)):
            self.assertIsInstance(value, Decimal)
            self.assertEqual(-2, value.as_tuple().exponent)
        self.assertEqual(D("-0.50"), pnl.unrealized(leg, D("3.425")))
        self.assertEqual(D("-25.00"), pnl.day_pl(leg, D("3.425"), D("3.175"), False))
        self.assertEqual(D("0.12"), pnl.quantize_money(D("0.125")), "half-even")


if __name__ == "__main__":
    unittest.main()
