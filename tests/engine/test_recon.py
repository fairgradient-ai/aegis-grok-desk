"""``aegis_desk.recon`` (PR 2b, builder B): the reconciliation engine behind ``csv_recon.py`` and the
plausibility check behind ``positions_check.py``.

Pure functions with injected inputs: the expired-leg rule, the history range (its end clamped to
the last closed session), one request per underlying, every settlement branch with the recon root's
expected outcomes, the proposed-edit CSV lines in the current header order, the quote-check scope
and the QUOTE_MISSING rule, coverage integrity, roll linkage, ACTED_UNCHANGED from the acks journal,
the positions snapshot, the verdict precedence and the proof that ``positions.csv`` is never
written. Nothing under ``tests/fixtures/`` is written; the snapshot test works on a copy.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aegis_desk import calendar_events, desk_paths, market_clock, recon, state
from aegis_desk.grouping import PositionGroup, group_positions
from aegis_desk.market_types import Bar, Quote
from aegis_desk.positions_csv import PositionRow, PositionsFile, RowError, parse_positions_text

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECON_ROOT = os.path.join(ROOT, "tests", "fixtures", "desk_recon")
MAIN_ROOT = os.path.join(ROOT, "tests", "fixtures", "desk")
RECON_SET = os.path.join(RECON_ROOT, "fixtures", "tradier", "recon")
MODULE_PATH = os.path.join(ROOT, "scripts", "aegis_desk", "recon.py")
UTC = datetime.timezone.utc
D = Decimal
HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
          "close_price_per_unit,underlying_stop,note")
COLUMNS = HEADER.split(",")
TODAY = datetime.date(2026, 11, 24)
RECON_NOW = datetime.datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)  # Tuesday 07:00 ET, pre-open
PIN_BAND = D("0.25")
NOV20 = datetime.date(2026, 11, 20)
NOV23 = datetime.date(2026, 11, 23)
WMT_PUT_95 = "WMT261120P00095000"
WMT_CALL_105 = "WMT261120C00105000"
NVDA_CALL_150 = "NVDA261120C00150000"
SPY_PUT_620 = "SPY261218P00620000"
SPX_CALL = "SPX261120C06500000"
RUT_PUT = "RUT261120P02200000"


def utc(text: str) -> datetime.datetime:
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def csv_row(instrument_type: str = "equity", symbol: str = "WMT", occ_symbol: str = "", quantity: str = "200",
            account: str = "", trade_id: str = "AG-20261020-02", opened: str = "2026-10-20", total: str = "19700.00",
            premium: str = "", status: str = "open", closed: str = "", closed_quantity: str = "", close_price: str = "",
            underlying_stop: str = "", note: str = "") -> str:
    cells = [instrument_type, symbol, occ_symbol, quantity, "", "", "", account, trade_id, opened, total, "", premium,
             status, closed, closed_quantity, close_price, underlying_stop, note]
    return ",".join(cells)


def option_row(occ_symbol: str, quantity: str, total: str, symbol: str = "", **extra: str) -> str:
    return csv_row(instrument_type="option", symbol=symbol, occ_symbol=occ_symbol, quantity=quantity, total=total,
                   **extra)


def parse(*rows: str, today: datetime.date = TODAY) -> PositionsFile:
    text = "\n".join([HEADER] + list(rows)) + "\n"
    parsed = parse_positions_text(text, today=today)
    assert parsed.file_errors == [], parsed.file_errors
    return parsed


def text_of(*rows: str) -> str:
    return "\n".join([HEADER] + list(rows)) + "\n"


def only_row(parsed: PositionsFile, row_number: int) -> PositionRow:
    for row in parsed.rows:
        if row.row_number == row_number:
            return row
    raise AssertionError("no row %d in %s" % (row_number, [row.row_number for row in parsed.rows]))


def group_of(groups: Sequence[PositionGroup], group_id: str) -> PositionGroup:
    for group in groups:
        if group.group_id == group_id:
            return group
    raise AssertionError("no group %s in %s" % (group_id, [group.group_id for group in groups]))


def bar(day: datetime.date, open_: str, close: str, high: Optional[str] = None, low: Optional[str] = None,
        volume: int = 1000000) -> Bar:
    return Bar(day, D(open_), D(high or max(open_, close, key=D)), D(low or min(open_, close, key=D)), D(close), volume)


def fixture_bars(path: str) -> List[Bar]:
    document = read_json(path)["history"]["day"]
    entries = document if isinstance(document, list) else [document]
    return [Bar.from_vendor(entry) for entry in entries]


def session_at(now: datetime.datetime, extra: Any = ()) -> market_clock.SessionInfo:
    return market_clock.session_at(now, extra_holidays=extra)


def quote(symbol: str, bid: str, ask: str, stamp: datetime.datetime, option: bool = False) -> Quote:
    ms = int(stamp.timestamp()) * 1000
    raw: Dict[str, Any] = {"symbol": symbol, "type": "option" if option else "stock", "bid": float(bid),
                           "ask": float(ask), "last": float(ask), "bid_date": ms, "ask_date": ms, "trade_date": ms}
    return Quote.from_vendor(raw)


@dataclass
class FakeHistory:
    bars: List[Bar] = field(default_factory=list)
    missing_reason: Optional[str] = None


@dataclass
class FakeQuotes:
    quotes: Dict[str, Quote] = field(default_factory=dict)
    missing: Dict[str, str] = field(default_factory=dict)
    requests_made: int = 1


# ============================================================================================
# CSV table helpers: physical row numbers, exact re-rendering, the group hash
# ============================================================================================


class CsvTableTest(unittest.TestCase):
    def test_row_numbers_are_physical_line_numbers_matching_positions_csv(self) -> None:
        text = read_text(os.path.join(RECON_ROOT, "positions.csv"))
        table = recon.csv_table(text)
        parsed = parse_positions_text(text, today=TODAY)
        self.assertEqual(COLUMNS, table.header)
        self.assertEqual(sorted(row.row_number for row in parsed.rows), sorted(table.cells))
        self.assertEqual(list(range(3, 15)), sorted(table.cells))  # line 2 is the comment
        self.assertEqual(WMT_PUT_95, table.cells[3][2])
        self.assertEqual(text.splitlines()[2], table.lines[3])

    def test_bom_crlf_and_blank_lines(self) -> None:
        text = "﻿" + HEADER + "\r\n\r\n# note\r\n" + csv_row() + "\r\n"
        table = recon.csv_table(text)
        self.assertEqual(COLUMNS, table.header)
        self.assertEqual([4], sorted(table.cells))
        self.assertEqual("WMT", table.cells[4][1])

    def test_render_csv_line_quotes_a_comma_and_keeps_blanks(self) -> None:
        cells = ["option", "WMT", WMT_PUT_95, "-1", "", "", "", "", "AG-20261015-01", "2026-10-15", "-165.00", "",
                 "1.65", "closed", "2026-11-20", "1", "0", "", "cash-secured put, expired"]
        line = recon.render_csv_line(cells)
        self.assertTrue(line.endswith(',"cash-secured put, expired"'), line)
        self.assertEqual(19, len(next(iter(__import__("csv").reader([line])))))
        self.assertNotIn("\n", line)

    def test_render_csv_line_quotes_a_leading_hash_so_the_line_is_never_a_comment(self) -> None:
        # Recon finding 1: with ``note`` as the FIRST column an added stock row would begin with ``#from:...``
        # and the parser (first non-space character ``#``) would drop it as a comment.
        line = recon.render_csv_line(["#from:AG-20261015-01 assigned", "equity", "WMT", "", "100"])
        self.assertEqual('"#from:AG-20261015-01 assigned",equity,WMT,,100', line)
        self.assertEqual('"  #x",a', recon.render_csv_line(["  #x", "a"]), "leading spaces hide nothing from the rule")
        self.assertEqual('"#a, b",c', recon.render_csv_line(["#a, b", "c"]), "a cell quoted for its comma is quoted once")
        self.assertEqual("a,#not-first", recon.render_csv_line(["a", "#not-first"]), "only the first cell can start a comment")
        self.assertEqual('"#"', recon.render_csv_line(["#"]))
        parsed = next(iter(__import__("csv").reader([line])))
        self.assertEqual(["#from:AG-20261015-01 assigned", "equity", "WMT", "", "100"], parsed)

    def test_added_rows_survive_a_note_first_header_round_trip(self) -> None:
        header = "note,trade_id,account,instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,opened," \
                 "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,close_price_per_unit," \
                 "underlying_stop"
        put = "cash-secured put,AG-20261015-01,Roth IRA,option,WMT,WMT261120P00110000,-1,,,,2026-10-15,-165.00,,1.65,open,,,,"
        text = header + "\n" + put + "\n"
        parsed = parse_positions_text(text, today=TODAY)
        self.assertEqual(([], 1), (parsed.file_errors, len(parsed.rows)))
        row = parsed.rows[0]
        groups = group_positions(parsed.rows)
        settlement = recon.settle_pm(row, [bar(NOV20, "104.60", "104.86")], pin_band=PIN_BAND)
        self.assertEqual(recon.OUTCOME_PROBABLE_ASSIGNED, settlement.outcome)
        edits, findings = recon.propose_edits(settlement, row, groups[0], recon.csv_table(text))
        self.assertEqual((), findings)
        self.assertEqual(["update", "add"], [edit.kind for edit in edits])
        added = edits[1].csv_line()
        self.assertTrue(added.startswith('"#from:AG-20261015-01 assigned",AG-20261015-01,Roth IRA,equity,WMT,,100,'), added)
        applied = header + "\n" + edits[0].csv_line() + "\n" + added + "\n"
        reparsed = parse_positions_text(applied, today=TODAY)
        self.assertEqual(([], {}, 2), (reparsed.file_errors, reparsed.quarantined, len(reparsed.rows)), "nothing dropped")
        stock = [item for item in reparsed.rows if item.is_stock_like][0]
        self.assertEqual(("WMT", D("100"), D("11000.00"), "#from:AG-20261015-01 assigned", "AG-20261015-01", "Roth IRA"),
                         (stock.symbol, stock.quantity, stock.cost_basis_total, stock.note, stock.from_trade, stock.account))
        self.assertEqual({"from:AG-20261015-01"}, set(stock.tags))
        table = recon.csv_table(applied)
        self.assertEqual([2, 3], sorted(table.cells), "csv_table keys the added row too")
        self.assertEqual("#from:AG-20261015-01 assigned", table.cell(3, "note"))
        regrouped = group_positions(reparsed.rows)
        self.assertEqual(["AG-20261015-01"], [group.group_id for group in regrouped])
        self.assertEqual(["long_stock"], [group.structure for group in regrouped], "the booked shares carry the group on")

    def test_group_rows_sha_is_sha256_of_the_groups_physical_lines(self) -> None:
        text = read_text(os.path.join(RECON_ROOT, "positions.csv"))
        table = recon.csv_table(text)
        lines = text.splitlines()
        expected = hashlib.sha256("\n".join([lines[3], lines[4]]).encode("utf-8")).hexdigest()
        self.assertEqual(expected, recon.group_rows_sha(table, "AG-20261020-02"))
        self.assertEqual(hashlib.sha256(lines[2].encode("utf-8")).hexdigest(), recon.group_rows_sha(table, "AG-20261015-01"))
        self.assertIsNone(recon.group_rows_sha(table, "AG-20991231-99"))

    def test_group_rows_sha_changes_when_a_row_changes_and_blank_trade_id_is_row_group(self) -> None:
        before = recon.csv_table(text_of(csv_row(), csv_row(trade_id="", symbol="NVDA", total="28240.00")))
        after = recon.csv_table(text_of(csv_row(total="19800.00"), csv_row(trade_id="", symbol="NVDA", total="28240.00")))
        self.assertNotEqual(recon.group_rows_sha(before, "AG-20261020-02"), recon.group_rows_sha(after, "AG-20261020-02"))
        self.assertEqual(recon.group_rows_sha(before, "ROW-3"), recon.group_rows_sha(after, "ROW-3"))


class RenderingTest(unittest.TestCase):
    def test_plain_number(self) -> None:
        self.assertEqual("150", recon.plain_number(D("150.000")))
        self.assertEqual("622.5", recon.plain_number(D("622.500")))
        self.assertEqual("-2", recon.plain_number(D("-2")))

    def test_money_text(self) -> None:
        self.assertEqual("150.00", recon.money_text(D("150.000")))
        self.assertEqual("23.17", recon.money_text(D("23.17")))
        self.assertEqual("30000.00", recon.money_text(D("30000")))
        self.assertEqual("0", recon.money_text(D("0")))
        self.assertEqual("0", recon.money_text(D("0.000")))
        self.assertEqual("0.125", recon.money_text(D("0.125")))
        self.assertEqual("-62000.00", recon.money_text(D("-62000")))


# ============================================================================================
# The expired-leg rule and the history range
# ============================================================================================


class ExpiredLegRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        parsed = parse(option_row(WMT_CALL_105, "-2", "-684.00"), option_row(SPX_CALL, "1", "2450.00", trade_id="AG-20261101-05"))
        self.pm = only_row(parsed, 2)
        self.am = only_row(parsed, 3)

    def expired(self, row: PositionRow, now: str, am_settled: bool) -> bool:
        info = session_at(utc(now))
        return recon.is_expired(row, et_date=info.et_date, session=info.session, am_settled=am_settled)

    def test_pm_leg_is_not_expired_before_or_during_its_last_session(self) -> None:
        self.assertFalse(self.expired(self.pm, "2026-11-19T19:50:00Z", False))  # Thursday, open
        self.assertFalse(self.expired(self.pm, "2026-11-20T14:00:00Z", False))  # Friday pre-open
        self.assertFalse(self.expired(self.pm, "2026-11-20T19:50:00Z", False))  # Friday, open

    def test_pm_leg_is_expired_after_the_close_of_its_last_session(self) -> None:
        self.assertTrue(self.expired(self.pm, "2026-11-20T21:25:00Z", False))  # Friday after the close
        self.assertTrue(self.expired(self.pm, "2026-11-21T14:30:00Z", False))  # Saturday
        self.assertTrue(self.expired(self.pm, "2026-11-23T13:00:00Z", False))  # Monday pre-open
        self.assertTrue(self.expired(self.pm, "2026-11-24T12:00:00Z", False))

    def test_am_leg_last_trades_the_day_before_settlement(self) -> None:
        self.assertEqual(datetime.date(2026, 11, 19), market_clock.last_trading_day(NOV20, True))
        self.assertFalse(self.expired(self.am, "2026-11-19T19:50:00Z", True))  # Thursday, open: still trading
        self.assertTrue(self.expired(self.am, "2026-11-19T21:25:00Z", True))  # Thursday after the close
        self.assertTrue(self.expired(self.am, "2026-11-20T12:00:00Z", True))  # Friday pre-open: settles this morning

    def test_am_settled_root_comes_from_the_calendar(self) -> None:
        cal = calendar_events.load_calendar(os.path.join(RECON_ROOT, "research", "calendar.toml"))
        self.assertTrue(recon.is_am_settled_root(cal, self.am))
        self.assertFalse(recon.is_am_settled_root(cal, self.pm))
        self.assertFalse(recon.is_am_settled_root(None, self.am), "no calendar: every root is PM-settled")


class HistoryRangeTest(unittest.TestCase):
    def test_end_is_clamped_to_the_last_closed_session(self) -> None:
        cases = {
            "2026-11-20T21:25:00Z": (NOV20, NOV20),  # expiry Friday after the close
            "2026-11-21T14:30:00Z": (NOV20, NOV20),  # Saturday: the post-expiry routine
            "2026-11-23T13:00:00Z": (NOV20, NOV20),  # Monday pre-open: Monday has not closed
            "2026-11-23T21:30:00Z": (NOV20, NOV23),  # Monday after the close
            "2026-11-24T12:00:00Z": (NOV20, NOV23),  # Tuesday pre-open (the recon root)
            "2026-11-26T15:00:00Z": (NOV20, NOV23),  # Thanksgiving: min(next trading day, Wednesday)
        }
        for now, expected in cases.items():
            with self.subTest(now=now):
                self.assertEqual(expected, recon.history_range(NOV20, utc(now)))

    def test_range_never_ends_before_it_starts(self) -> None:
        start, end = recon.history_range(datetime.date(2026, 11, 13), utc("2026-11-24T12:00:00Z"))
        self.assertEqual((datetime.date(2026, 11, 13), datetime.date(2026, 11, 16)), (start, end))
        self.assertLessEqual(start, end)

    def test_extra_closure_moves_the_next_trading_day(self) -> None:
        closures = {NOV23: "calendar closure"}
        self.assertEqual((NOV20, NOV20), recon.history_range(NOV20, utc("2026-11-24T12:00:00Z"), closures))


# ============================================================================================
# Settlement branches
# ============================================================================================


class IntrinsicAndRulesTest(unittest.TestCase):
    def test_intrinsic_inline(self) -> None:
        self.assertEqual(D("23.17"), recon.intrinsic("CALL", D("6500.000"), D("6523.17")))
        self.assertEqual(D("0"), recon.intrinsic("CALL", D("6500.000"), D("6400")))
        self.assertEqual(D("10.00"), recon.intrinsic("PUT", D("620.000"), D("610.00")))
        self.assertEqual(D("0"), recon.intrinsic("PUT", D("620.000"), D("650")))

    def test_one_cent_rule(self) -> None:
        self.assertTrue(recon.in_the_money("CALL", D("105.000"), D("105.01")))
        self.assertFalse(recon.in_the_money("CALL", D("105.000"), D("105.005")))
        self.assertFalse(recon.in_the_money("CALL", D("105.000"), D("105")))
        self.assertTrue(recon.in_the_money("PUT", D("95.000"), D("94.99")))
        self.assertFalse(recon.in_the_money("PUT", D("95.000"), D("94.995")))

    def test_crossing_rule(self) -> None:
        self.assertTrue(recon.crossed_strike(D("230.000"), D("231.00"), D("229.40")))
        self.assertTrue(recon.crossed_strike(D("105.000"), D("104.86"), D("105.30")))
        self.assertFalse(recon.crossed_strike(D("150.000"), D("153.40"), D("152.90")))
        self.assertTrue(recon.crossed_strike(D("150.000"), D("153.40"), D("150.00")), "an open on the strike is uncertain")
        self.assertFalse(recon.crossed_strike(D("150.000"), D("153.40"), None))


class PmSettlementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse(
            option_row(WMT_PUT_95, "-1", "-165.00", trade_id="AG-20261015-01"),
            option_row(WMT_CALL_105, "-2", "-684.00"),
            option_row(NVDA_CALL_150, "-2", "-610.00", trade_id="AG-20261022-03"),
            option_row("AMD261120C00230000", "-1", "-520.00", trade_id="AG-20261023-04"),
            option_row("AAPL261120P00230000", "2", "1240.00", trade_id="AG-20261105-07"),
            option_row("NVDA261120C00140000", "1", "1500.00", trade_id="AG-20261030-10"),
            option_row("AAPL261120P00240000", "1", "1200.00", trade_id="AG-20261030-11"),
        )
        self.wmt = [bar(NOV20, "104.60", "104.86"), bar(NOV23, "105.30", "105.62")]
        self.nvda = [bar(NOV20, "150.20", "153.40"), bar(NOV23, "152.90", "154.10")]
        self.amd = [bar(NOV20, "228.10", "231.00"), bar(NOV23, "229.40", "228.75")]

    def settle(self, row_number: int, bars: List[Bar], pin_band: Decimal = PIN_BAND, reason: Optional[str] = None):
        return recon.settle_pm(only_row(self.parsed, row_number), bars, pin_band=pin_band, history_reason=reason)

    def test_short_put_expired_out_of_the_money(self) -> None:
        result = self.settle(2, self.wmt)
        self.assertEqual(recon.OUTCOME_EXPIRED_OTM, result.outcome)
        self.assertEqual((D("104.86"), D("105.30"), NOV20, NOV20, "pm", False),
                         (result.close, result.next_open, result.last_trading_day, result.settle_date, result.settlement,
                          result.unavailable))
        self.assertEqual(D("0"), result.close_price)
        self.assertIn("104.86", result.message)
        self.assertIn("out of the money", result.message)

    def test_short_call_pinned_inside_the_band(self) -> None:
        result = self.settle(3, self.wmt)
        self.assertEqual(recon.OUTCOME_PIN_UNCERTAIN, result.outcome)
        self.assertTrue(result.unavailable)
        self.assertIsNone(result.close_price)
        self.assertIn("0.14", result.message)
        self.assertIn("confirm with your broker", result.message)

    def test_short_call_probable_assigned(self) -> None:
        result = self.settle(4, self.nvda)
        self.assertEqual(recon.OUTCOME_PROBABLE_ASSIGNED, result.outcome)
        self.assertEqual((D("153.40"), D("152.90"), False, D("0")), (result.close, result.next_open, result.unavailable,
                                                                   result.close_price))
        self.assertIn("3.40", result.message)

    def test_crossing_after_hours_is_pin_uncertain_even_outside_the_band(self) -> None:
        result = self.settle(5, self.amd)
        self.assertEqual(recon.OUTCOME_PIN_UNCERTAIN, result.outcome)
        self.assertIn("229.40", result.message)
        self.assertIn("across the strike", result.message)
        same_side = [bar(NOV20, "228.10", "231.00"), bar(NOV23, "230.40", "228.75")]
        self.assertEqual(recon.OUTCOME_PROBABLE_ASSIGNED, self.settle(5, same_side).outcome)

    def test_band_boundary_is_inclusive(self) -> None:
        exactly = [bar(NOV20, "104.00", "104.75")]  # 0.25 below the 105 strike
        self.assertEqual(recon.OUTCOME_PIN_UNCERTAIN, self.settle(3, exactly).outcome)
        outside = [bar(NOV20, "104.00", "104.74")]
        self.assertEqual(recon.OUTCOME_EXPIRED_OTM, self.settle(3, outside).outcome)
        self.assertEqual(recon.OUTCOME_EXPIRED_OTM, self.settle(3, [bar(NOV20, "104.00", "104.74")], pin_band=D("0")).outcome)

    def test_one_cent_rule_with_a_zero_band(self) -> None:
        self.assertEqual(recon.OUTCOME_PROBABLE_ASSIGNED, self.settle(3, [bar(NOV20, "105.00", "105.01")], pin_band=D("0")).outcome)
        self.assertEqual(recon.OUTCOME_EXPIRED_OTM, self.settle(3, [bar(NOV20, "105.00", "105.005")], pin_band=D("0")).outcome)
        self.assertEqual(recon.OUTCOME_PIN_UNCERTAIN, self.settle(3, [bar(NOV20, "105.00", "105.000")], pin_band=D("0")).outcome)

    def test_long_legs_in_the_money_are_probable_exercised(self) -> None:
        self.assertEqual(recon.OUTCOME_PROBABLE_EXERCISED, self.settle(7, self.nvda).outcome)  # long 140 call, close 153.40
        put = self.settle(8, [bar(NOV20, "236.00", "235.00")])  # long 240 put, close 235
        self.assertEqual(recon.OUTCOME_PROBABLE_EXERCISED, put.outcome)
        self.assertEqual(D("0"), put.close_price, "the option row closes at 0: premium stays in option P/L")
        self.assertEqual(recon.OUTCOME_EXPIRED_OTM, self.settle(6, [bar(NOV20, "236.00", "235.00")]).outcome)

    def test_no_bars_is_expired_unrecorded(self) -> None:
        result = self.settle(6, [], reason="fetch_failed:network")
        self.assertEqual(recon.OUTCOME_EXPIRED_UNRECORDED, result.outcome)
        self.assertTrue(result.unavailable)
        self.assertIsNone(result.close)
        self.assertIn("fetch_failed:network", result.message)
        self.assertIn("record the outcome of the long 230 put x 2 by hand", result.message)
        self.assertEqual("fetch_failed:network", result.history_reason)
        self.assertIn("record", result.message.lower())

    def test_bars_without_the_expiry_day_are_unrecorded(self) -> None:
        result = self.settle(6, [bar(NOV23, "229.40", "228.75")])
        self.assertEqual(recon.OUTCOME_EXPIRED_UNRECORDED, result.outcome)
        self.assertIn("2026-11-20", result.message)

    def test_result_is_json_ready(self) -> None:
        result = self.settle(4, self.nvda)
        document = json.loads(state.json_dumps(result.as_dict()))
        self.assertEqual(("PROBABLE_ASSIGNED", "153.40", "150.000", 4, "AG-20261022-03", "pm"),
                         (document["outcome"], document["close"], document["strike"], document["row_number"],
                          document["group_id"], document["settlement"]))


class AmSettlementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse(option_row(SPX_CALL, "1", "2450.00", trade_id="AG-20261101-05"),
                            option_row(RUT_PUT, "-1", "-1850.00", trade_id="AG-20261101-06"),
                            option_row("SPX261120P06600000", "-1", "-1200.00", trade_id="AG-20261101-07"))
        self.cal = calendar_events.load_calendar(os.path.join(RECON_ROOT, "research", "calendar.toml"))

    def test_cash_settled_with_the_calendar_value(self) -> None:
        row = only_row(self.parsed, 2)
        value = self.cal.settlement_value(row.occ.root, market_clock.settlement_day(row.expiration, True))
        self.assertEqual(D("6523.17"), value)
        result = recon.settle_am(row, value)
        self.assertEqual((recon.OUTCOME_CASH_SETTLED, D("6523.17"), D("23.17"), "am", False, NOV20,
                          datetime.date(2026, 11, 19)),
                         (result.outcome, result.close, result.close_price, result.settlement, result.unavailable,
                          result.settle_date, result.last_trading_day))
        self.assertNotIn("ASSIGNED", result.message)
        self.assertNotIn("EXERCISED", result.message)

    def test_cash_settled_put_and_worthless_contract(self) -> None:
        put = recon.settle_am(only_row(self.parsed, 4), D("6523.17"))
        self.assertEqual((recon.OUTCOME_CASH_SETTLED, D("76.83")), (put.outcome, put.close_price))
        worthless = recon.settle_am(only_row(self.parsed, 2), D("6400"))
        self.assertEqual((recon.OUTCOME_CASH_SETTLED, D("0")), (worthless.outcome, worthless.close_price))

    def test_missing_value_is_settlement_unknown(self) -> None:
        row = only_row(self.parsed, 3)
        self.assertIsNone(self.cal.settlement_value(row.occ.root, NOV20))
        result = recon.settle_am(row, None)
        self.assertEqual((recon.OUTCOME_SETTLEMENT_UNKNOWN, True, None), (result.outcome, result.unavailable, result.close_price))
        self.assertIn("settlement.values", result.message)
        self.assertIn("2026-11-20", result.message)
        self.assertIn("(short 2200 put x 1)", result.message, "the side word and the unsigned count, never 'x -1'")
        self.assertNotIn("x -1", result.message)

    def test_settlement_is_pending_before_the_settlement_morning(self) -> None:
        # Recon finding 4: an AM-settled leg stops trading on Thursday but its value is struck at Friday 09:30 ET.
        # Between the two the leg is expired and the value cannot exist yet: SETTLEMENT_PENDING, not an
        # UNAVAILABLE "record the official settlement value".
        row = only_row(self.parsed, 3)
        thursday_close = utc("2026-11-19T21:30:00Z")
        friday_preopen = utc("2026-11-20T12:00:00Z")
        friday_open = utc("2026-11-20T14:30:00Z")
        for now in (thursday_close, friday_preopen):
            with self.subTest(now=now):
                result = recon.settle_am(row, None, now=now)
                self.assertEqual((recon.OUTCOME_SETTLEMENT_PENDING, False, None, None, "am"),
                                 (result.outcome, result.unavailable, result.close, result.close_price, result.settlement))
                self.assertEqual((datetime.date(2026, 11, 19), NOV20), (result.last_trading_day, result.settle_date))
                self.assertIn("settles at the open of 2026-11-20", result.message)
                self.assertIn("not published yet", result.message)
                self.assertNotIn("record the official settlement value", result.message)
        struck = recon.settle_am(row, None, now=friday_open)
        self.assertEqual((recon.OUTCOME_SETTLEMENT_UNKNOWN, True), (struck.outcome, struck.unavailable),
                         "from 09:30 ET on the settlement day the value exists and must be recorded")
        self.assertEqual(recon.OUTCOME_SETTLEMENT_UNKNOWN, recon.settle_am(row, None).outcome, "no instant: the strict rule")
        recorded = recon.settle_am(only_row(self.parsed, 2), D("6523.17"), now=friday_preopen)
        self.assertEqual(recon.OUTCOME_CASH_SETTLED, recorded.outcome, "a recorded value is the owner's record and is used")
        self.assertNotIn(recon.OUTCOME_SETTLEMENT_PENDING, recon.UNAVAILABLE_OUTCOMES)
        self.assertNotIn(recon.OUTCOME_SETTLEMENT_PENDING, recon.EDIT_OUTCOMES)
        self.assertIn(recon.OUTCOME_SETTLEMENT_PENDING, recon.OUTCOMES)


# ============================================================================================
# Proposed edits: exact CSV lines in the current header order
# ============================================================================================


def edits_for(text: str, row_number: int, bars: Optional[List[Bar]] = None, value: Optional[Decimal] = None,
              am: bool = False, pin_band: Decimal = PIN_BAND) -> Tuple[List[recon.ProposedEdit], List[recon.Finding]]:
    parsed = parse_positions_text(text, today=TODAY)
    assert parsed.file_errors == [] and parsed.quarantined == {}, (parsed.file_errors, parsed.quarantined)
    row = only_row(parsed, row_number)
    groups = group_positions(parsed.rows)
    group = group_of(groups, row.group_id)
    settlement = recon.settle_am(row, value) if am else recon.settle_pm(row, bars or [], pin_band=pin_band)
    edits, findings = recon.propose_edits(settlement, row, group, recon.csv_table(text))
    return list(edits), list(findings)


class ProposedEditsTest(unittest.TestCase):
    RECON_TEXT = read_text(os.path.join(RECON_ROOT, "positions.csv"))

    def test_expired_otm_closes_the_row_at_zero_on_the_settlement_day(self) -> None:
        edits, findings = edits_for(self.RECON_TEXT, 3, [bar(NOV20, "104.60", "104.86"), bar(NOV23, "105.30", "105.62")])
        self.assertEqual([], findings)
        self.assertEqual(1, len(edits))
        edit = edits[0]
        self.assertEqual(("update", 3, recon.OUTCOME_EXPIRED_OTM, "AG-20261015-01"),
                         (edit.kind, edit.row_number, edit.outcome, edit.group_id))
        self.assertEqual({"status": "closed", "closed": "2026-11-20", "closed_quantity": "1", "close_price_per_unit": "0"},
                         edit.fields)
        self.assertEqual("option,WMT,WMT261120P00095000,-1,,,,,AG-20261015-01,2026-10-15,-165.00,,1.65,closed,2026-11-20,1,0,,"
                         "cash-secured put", edit.csv_line())
        self.assertEqual(COLUMNS, list(recon.csv_table(self.RECON_TEXT).header))
        self.assertEqual(len(COLUMNS), len(edit.cells))

    def test_probable_assigned_short_call_closes_option_and_covering_stock_at_the_strike(self) -> None:
        edits, findings = edits_for(self.RECON_TEXT, 7, [bar(NOV20, "150.20", "153.40"), bar(NOV23, "152.90", "154.10")])
        self.assertEqual([], findings)
        self.assertEqual(["update", "update"], [edit.kind for edit in edits])
        option, stock = edits
        self.assertEqual((7, {"status": "closed", "closed": "2026-11-20", "closed_quantity": "2", "close_price_per_unit": "0"}),
                         (option.row_number, option.fields))
        self.assertEqual("option,NVDA,NVDA261120C00150000,-2,,,,,AG-20261022-03,2026-10-22,-610.00,,3.05,closed,2026-11-20,2,0,,"
                         "covered call", option.csv_line())
        self.assertEqual((6, {"status": "closed", "closed": "2026-11-20", "closed_quantity": "200", "close_price_per_unit": "150.00"}),
                         (stock.row_number, stock.fields))
        self.assertEqual("equity,NVDA,,200,,,,,AG-20261022-03,2026-10-22,28240.00,,,closed,2026-11-20,200,150.00,,",
                         stock.csv_line())
        self.assertEqual(recon.OUTCOME_PROBABLE_ASSIGNED, stock.outcome)
        self.assertIn("strike", stock.reason)

    def test_short_call_assigned_with_more_shares_than_called_away_is_a_partial_close(self) -> None:
        text = text_of(csv_row(quantity="300", total="29550.00"), option_row(WMT_CALL_105, "-2", "-684.00"))
        edits, findings = edits_for(text, 3, [bar(NOV20, "106.00", "107.00")])
        self.assertEqual([], findings)
        stock = edits[1]
        self.assertEqual({"closed": "2026-11-20", "closed_quantity": "200", "close_price_per_unit": "105.00"}, stock.fields)
        self.assertEqual("equity,WMT,,300,,,,,AG-20261020-02,2026-10-20,29550.00,,,open,2026-11-20,200,105.00,,", stock.csv_line())

    def test_short_call_assigned_without_covering_stock_adds_a_short_stock_row_to_confirm(self) -> None:
        text = text_of(option_row(WMT_CALL_105, "-2", "-684.00"))
        edits, findings = edits_for(text, 2, [bar(NOV20, "106.00", "107.00")])
        self.assertEqual([], findings)
        self.assertEqual(["update", "add"], [edit.kind for edit in edits])
        added = edits[1]
        self.assertIsNone(added.row_number)
        self.assertEqual({"instrument_type": "equity", "symbol": "WMT", "quantity": "-200", "trade_id": "AG-20261020-02",
                          "opened": "2026-11-20", "cost_basis_total": "-21000.00", "status": "open",
                          "note": "#from:AG-20261020-02 assigned (confirm with your broker)"}, added.fields)
        self.assertEqual("equity,WMT,,-200,,,,,AG-20261020-02,2026-11-20,-21000.00,,,open,,,,,"
                         "#from:AG-20261020-02 assigned (confirm with your broker)", added.csv_line())
        self.assertIn("confirm with your broker", added.reason)

    def test_short_put_assigned_adds_long_stock_at_the_strike(self) -> None:
        text = text_of(option_row("WMT261120P00105000", "-1", "-165.00", trade_id="AG-20261015-01", account="Roth IRA"))
        edits, findings = edits_for(text, 2, [bar(NOV20, "101.00", "100.00")])
        self.assertEqual([], findings)
        self.assertEqual(["update", "add"], [edit.kind for edit in edits])
        added = edits[1]
        self.assertEqual({"instrument_type": "equity", "symbol": "WMT", "quantity": "100", "account": "Roth IRA",
                          "trade_id": "AG-20261015-01", "opened": "2026-11-20", "cost_basis_total": "10500.00",
                          "status": "open", "note": "#from:AG-20261015-01 assigned"}, added.fields)
        self.assertEqual("equity,WMT,,100,,,,Roth IRA,AG-20261015-01,2026-11-20,10500.00,,,open,,,,,"
                         "#from:AG-20261015-01 assigned", added.csv_line())
        option = edits[0]
        self.assertEqual("0", option.fields["close_price_per_unit"], "the premium stays in option P/L")

    def test_added_row_copies_the_stock_instrument_type_of_the_group(self) -> None:
        text = text_of(csv_row(instrument_type="etf", symbol="SPY", quantity="100", total="63240.00", trade_id="AG-20261110-08"),
                       option_row("SPY261120P00650000", "-1", "-500.00", trade_id="AG-20261110-08"))
        edits, _ = edits_for(text, 3, [bar(NOV20, "641.00", "640.00")])
        added = edits[1]
        self.assertEqual(("add", "etf", "SPY", "100", "65000.00"), (added.kind, added.fields["instrument_type"],
                                                                      added.fields["symbol"], added.fields["quantity"],
                                                                      added.fields["cost_basis_total"]))

    def test_long_call_exercised_adds_long_stock_at_the_strike(self) -> None:
        text = text_of(option_row("NVDA261120C00140000", "1", "1500.00", trade_id="AG-20261030-10"))
        edits, findings = edits_for(text, 2, [bar(NOV20, "150.20", "153.40")])
        self.assertEqual([], findings)
        option, added = edits
        self.assertEqual({"status": "closed", "closed": "2026-11-20", "closed_quantity": "1", "close_price_per_unit": "0"},
                         option.fields)
        self.assertEqual({"instrument_type": "equity", "symbol": "NVDA", "quantity": "100", "trade_id": "AG-20261030-10",
                          "opened": "2026-11-20", "cost_basis_total": "14000.00", "status": "open",
                          "note": "#from:AG-20261030-10 exercised"}, added.fields)

    def test_long_put_exercised_delivers_the_protected_shares_at_the_strike(self) -> None:
        text = text_of(csv_row(instrument_type="etf", symbol="SPY", quantity="100", total="63240.00", trade_id="AG-20261110-08"),
                       option_row("SPY261120P00650000", "1", "500.00", trade_id="AG-20261110-08"))
        edits, findings = edits_for(text, 3, [bar(NOV20, "641.00", "640.00")])
        self.assertEqual([], findings)
        option, stock = edits
        self.assertEqual(recon.OUTCOME_PROBABLE_EXERCISED, option.outcome)
        self.assertEqual((2, {"status": "closed", "closed": "2026-11-20", "closed_quantity": "100", "close_price_per_unit": "650.00"}),
                         (stock.row_number, stock.fields))
        bare = text_of(option_row("SPY261120P00650000", "1", "500.00", trade_id="AG-20261110-08"))
        edits, _ = edits_for(bare, 2, [bar(NOV20, "641.00", "640.00")])
        added = edits[1]
        self.assertEqual(("add", "-100", "-65000.00", "#from:AG-20261110-08 exercised (confirm with your broker)"),
                         (added.kind, added.fields["quantity"], added.fields["cost_basis_total"], added.fields["note"]))

    def test_cash_settled_closes_at_the_intrinsic_value(self) -> None:
        edits, findings = edits_for(self.RECON_TEXT, 9, value=D("6523.17"), am=True)
        self.assertEqual([], findings)
        self.assertEqual(1, len(edits))
        self.assertEqual({"status": "closed", "closed": "2026-11-20", "closed_quantity": "1", "close_price_per_unit": "23.17"},
                         edits[0].fields)
        self.assertEqual("option,SPX,SPX261120C06500000,1,,,,,AG-20261101-05,2026-11-01,2450.00,,24.50,closed,2026-11-20,1,23.17,,"
                         "AM-settled index call", edits[0].csv_line())

    def test_no_edit_for_pin_unknown_or_unrecorded(self) -> None:
        for row_number, bars, value, am in ((5, [bar(NOV20, "104.60", "104.86"), bar(NOV23, "105.30", "105.62")], None, False),
                                            (10, None, None, True), (11, [], None, False)):
            with self.subTest(row=row_number):
                edits, findings = edits_for(self.RECON_TEXT, row_number, bars, value, am)
                self.assertEqual(([], []), (edits, findings))

    def test_row_without_trade_id_gets_no_from_tag(self) -> None:
        text = text_of(option_row("WMT261120P00105000", "-1", "-165.00", trade_id=""))
        edits, _ = edits_for(text, 2, [bar(NOV20, "101.00", "100.00")])
        added = edits[1]
        self.assertEqual("", added.fields.get("trade_id", ""))
        self.assertNotIn("#", added.fields["note"])
        self.assertIn("row 2", added.fields["note"])
        self.assertEqual("ROW-2", added.group_id)

    def test_partially_closed_row_cannot_be_edited_in_one_line(self) -> None:
        text = text_of(option_row(WMT_PUT_95, "-2", "-330.00", trade_id="AG-20261015-01", closed="2026-11-10",
                                  closed_quantity="1", close_price="0.40"))
        edits, findings = edits_for(text, 2, [bar(NOV20, "104.60", "104.86")])
        self.assertEqual([], edits)
        self.assertEqual(1, len(findings))
        self.assertEqual((recon.FINDING_EDIT_UNREPRESENTABLE, recon.LEVEL_UNAVAILABLE, 2), (findings[0].kind, findings[0].level,
                                                                                            findings[0].row_number))
        self.assertIn("partial close", findings[0].message)

    def test_edit_is_json_ready_with_its_csv_line(self) -> None:
        edits, _ = edits_for(self.RECON_TEXT, 3, [bar(NOV20, "104.60", "104.86")])
        document = json.loads(state.json_dumps(edits[0].as_dict()))
        self.assertEqual(["cells", "csv_line", "fields", "group_id", "kind", "outcome", "reason", "row_number"], sorted(document))
        self.assertEqual(edits[0].csv_line(), document["csv_line"])


# ============================================================================================
# The plan: expired legs, one history request per underlying, the quote-check scope
# ============================================================================================


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read_text(os.path.join(RECON_ROOT, "positions.csv"))
        self.cal = calendar_events.load_calendar(os.path.join(RECON_ROOT, "research", "calendar.toml"))
        self.positions = parse_positions_text(self.text, today=TODAY)
        self.groups = group_positions(self.positions.rows)
        self.session = session_at(RECON_NOW, self.cal.extra_closures())

    def plan(self) -> recon.SettlementPlan:
        return recon.plan_settlement(self.positions, self.groups, now=RECON_NOW, session=self.session, cal=self.cal)

    def test_expired_legs_and_one_history_request_per_underlying(self) -> None:
        plan = self.plan()
        self.assertEqual([3, 5, 7, 8, 9, 10, 11], sorted(plan.expired_rows))
        self.assertEqual(TODAY, plan.et_date)
        requests = [(request.symbol, request.start, request.end) for request in plan.history_requests]
        self.assertEqual([("WMT", NOV20, NOV23), ("NVDA", NOV20, NOV23), ("AMD", NOV20, NOV23), ("AAPL", NOV20, NOV23)], requests)
        by_row = {leg.row.row_number: leg for leg in plan.legs}
        self.assertEqual(by_row[3].history_key, by_row[5].history_key, "the two WMT legs share one request")
        self.assertIsNone(by_row[9].history_key, "an AM-settled root fetches no history")
        self.assertTrue(by_row[9].am_settled and by_row[10].am_settled)
        self.assertFalse(by_row[3].am_settled)
        self.assertEqual(NOV20, by_row[9].settle_date)
        self.assertEqual(datetime.date(2026, 11, 19), by_row[9].last_trading_day)

    def test_quote_check_scope_is_unexpired_open_option_legs_and_their_underlyings(self) -> None:
        plan = self.plan()
        self.assertEqual(["SPY", SPY_PUT_620], recon.quote_check_symbols(self.groups, plan.expired_rows))

    def test_main_root_regular_has_no_expired_leg_and_the_six_symbol_batch(self) -> None:
        text = read_text(os.path.join(MAIN_ROOT, "positions.csv"))
        cal = calendar_events.load_calendar(os.path.join(MAIN_ROOT, "research", "calendar.toml"))
        now = utc("2026-11-03T19:50:00Z")
        positions = parse_positions_text(text, today=datetime.date(2026, 11, 3))
        groups = group_positions(positions.rows)
        plan = recon.plan_settlement(positions, groups, now=now, session=session_at(now), cal=cal)
        self.assertEqual(([], []), (plan.legs, plan.history_requests))
        self.assertEqual(["NVDA", NVDA_CALL_150, "SPY", SPY_PUT_620, "WMT", WMT_CALL_105],
                         recon.quote_check_symbols(groups, plan.expired_rows))

    def test_saturday_requests_the_single_day_range(self) -> None:
        text = read_text(os.path.join(MAIN_ROOT, "positions.csv"))
        now = utc("2026-11-21T14:30:00Z")
        positions = parse_positions_text(text, today=datetime.date(2026, 11, 21))
        groups = group_positions(positions.rows)
        plan = recon.plan_settlement(positions, groups, now=now, session=session_at(now), cal=None)
        self.assertEqual([("WMT", NOV20, NOV20), ("NVDA", NOV20, NOV20)],
                         [(request.symbol, request.start, request.end) for request in plan.history_requests])
        self.assertEqual(["SPY", SPY_PUT_620], recon.quote_check_symbols(groups, plan.expired_rows))

    def test_closed_rows_and_stock_legs_are_never_expired_legs(self) -> None:
        plan = self.plan()
        self.assertNotIn(14, plan.expired_rows)
        self.assertNotIn(4, plan.expired_rows)
        self.assertNotIn(13, plan.expired_rows)


# ============================================================================================
# Other checks: quotes, coverage, roll linkage, acks
# ============================================================================================


class QuoteFindingsTest(unittest.TestCase):
    STAMP = utc("2026-11-23T20:59:50Z")

    def setUp(self) -> None:
        self.parsed = parse(csv_row(instrument_type="etf", symbol="SPY", quantity="100", total="63240.00", trade_id="AG-20261110-08"),
                            option_row(SPY_PUT_620, "1", "1130.00", trade_id="AG-20261110-08"),
                            option_row("SPX261218C06500000", "1", "2450.00", trade_id="AG-20261101-05"))
        self.groups = group_positions(self.parsed.rows)
        self.symbols = recon.quote_check_symbols(self.groups, frozenset())

    def test_scope_and_a_clean_check(self) -> None:
        self.assertEqual(["SPX", "SPX261218C06500000", "SPY", SPY_PUT_620], self.symbols)
        result = FakeQuotes({symbol: quote(symbol, "1", "2", self.STAMP) for symbol in self.symbols})
        findings, check = recon.quote_findings(self.symbols, result, self.groups)
        self.assertEqual([], findings)
        self.assertEqual((tuple(self.symbols), tuple(self.symbols), None, 1), (check.symbols, check.quoted, check.not_run_reason,
                                                                             check.requests_made))
        self.assertEqual(self.STAMP, check.as_of)

    def test_unmatched_leg_is_quote_missing_and_unavailable(self) -> None:
        result = FakeQuotes({"SPY": quote("SPY", "651.90", "651.94", self.STAMP), "SPX": quote("SPX", "1", "2", self.STAMP),
                             "SPX261218C06500000": quote("SPX261218C06500000", "1", "2", self.STAMP)},
                            {SPY_PUT_620: "unmatched"})
        findings, check = recon.quote_findings(self.symbols, result, self.groups)
        self.assertEqual(1, len(findings))
        finding = findings[0]
        self.assertEqual((recon.FINDING_QUOTE_MISSING, recon.LEVEL_UNAVAILABLE, 3, SPY_PUT_620, "AG-20261110-08"),
                         (finding.kind, finding.level, finding.row_number, finding.symbol, finding.group_id))
        self.assertIn("did not match", finding.message)
        self.assertEqual({SPY_PUT_620: "unmatched"}, check.missing)

    def test_unmatched_index_underlying_is_a_warning_not_quote_missing(self) -> None:
        result = FakeQuotes({"SPY": quote("SPY", "651.90", "651.94", self.STAMP),
                             SPY_PUT_620: quote(SPY_PUT_620, "4.05", "4.25", self.STAMP, option=True),
                             "SPX261218C06500000": quote("SPX261218C06500000", "20", "21", self.STAMP, option=True)},
                            {"SPX": "unmatched"})
        findings, _ = recon.quote_findings(self.symbols, result, self.groups)
        self.assertEqual([(recon.FINDING_UNQUOTABLE_INDEX, recon.LEVEL_WARNING, "SPX")],
                         [(f.kind, f.level, f.symbol) for f in findings])
        self.assertIn("sandbox", findings[0].message)

    def test_market_closed_means_the_check_did_not_run_and_changes_nothing(self) -> None:
        result = FakeQuotes({}, {symbol: "market_closed" for symbol in self.symbols}, requests_made=0)
        findings, check = recon.quote_findings(self.symbols, result, self.groups)
        self.assertEqual([(recon.FINDING_QUOTE_CHECK_NOT_RUN, recon.LEVEL_INFO)], [(f.kind, f.level) for f in findings])
        self.assertEqual("quote check not run: market_closed", findings[0].message)
        self.assertEqual("market_closed", check.not_run_reason)
        self.assertEqual((), check.quoted)

    def test_other_reasons_per_symbol_are_informational(self) -> None:
        result = FakeQuotes({"SPY": quote("SPY", "651.90", "651.94", self.STAMP)},
                            {SPY_PUT_620: "quota_guard", "SPX": "fetch_failed:http", "SPX261218C06500000": "per_run_cap"})
        findings, check = recon.quote_findings(self.symbols, result, self.groups)
        self.assertEqual({recon.LEVEL_INFO}, {f.level for f in findings})
        self.assertEqual(3, len(findings))
        self.assertIsNone(check.not_run_reason)
        self.assertEqual(("SPY",), check.quoted)

    def test_no_symbols_and_no_result(self) -> None:
        findings, check = recon.quote_findings([], None, self.groups)
        self.assertEqual(([], (), None), (findings, check.symbols, check.not_run_reason))
        findings, check = recon.quote_findings(self.symbols, None, self.groups)
        self.assertEqual("not attempted", check.not_run_reason)
        self.assertEqual([recon.FINDING_QUOTE_CHECK_NOT_RUN], [f.kind for f in findings])


class CoverageAndRollTest(unittest.TestCase):
    def test_closed_stock_leg_under_an_open_short_call_is_uncovered(self) -> None:
        parsed = parse(csv_row(status="closed", closed="2026-11-10", close_price="110.00"), option_row(WMT_CALL_105, "-2", "-684.00"))
        groups = group_positions(parsed.rows)
        findings = recon.coverage_findings(groups)
        self.assertEqual(1, len(findings))
        finding = findings[0]
        self.assertEqual((recon.FINDING_UNCOVERED_SHORT_CALL, recon.LEVEL_WARNING, "AG-20261020-02", 3),
                         (finding.kind, finding.level, finding.group_id, finding.row_number))
        self.assertIn("uncovered short call", finding.message)
        self.assertIn("row 2", finding.message)

    def test_intact_covered_call_and_plain_short_call_are_quiet(self) -> None:
        intact = group_positions(parse(csv_row(), option_row(WMT_CALL_105, "-2", "-684.00")).rows)
        self.assertEqual([], recon.coverage_findings(intact))
        naked = group_positions(parse(option_row(WMT_CALL_105, "-2", "-684.00")).rows)
        self.assertEqual([], recon.coverage_findings(naked), "a short call without any stock row is a structure, not a defect")
        collar_closed_put = group_positions(parse(csv_row(), option_row(WMT_CALL_105, "-2", "-684.00"),
                                                  option_row(WMT_PUT_95, "2", "330.00", status="closed", closed="2026-11-10",
                                                             close_price="0.10")).rows)
        self.assertEqual([], recon.coverage_findings(collar_closed_put))

    def test_roll_and_from_links_must_name_an_existing_group(self) -> None:
        parsed = parse(option_row(WMT_CALL_105, "-2", "-684.00", note="rolled #roll-of:AG-20261001-09"),
                       option_row("WMT261218C00110000", "-1", "-300.00", trade_id="AG-20261201-01", note="#from:AG-20261020-02"))
        findings = recon.roll_findings(parsed.rows, set(parsed.group_ids))
        self.assertEqual(1, len(findings))
        self.assertEqual((recon.FINDING_ROLL_LINK, recon.LEVEL_WARNING, 2, "AG-20261020-02"),
                         (findings[0].kind, findings[0].level, findings[0].row_number, findings[0].group_id))
        self.assertIn("#roll-of:AG-20261001-09", findings[0].message)
        known = set(parsed.group_ids) | {"AG-20261001-09"}
        self.assertEqual([], recon.roll_findings(parsed.rows, known))


class AckFindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read_text(os.path.join(RECON_ROOT, "positions.csv"))
        self.table = recon.csv_table(self.text)
        self.sha = recon.group_rows_sha(self.table, "AG-20261020-02")
        self.ids = {"AG-20261020-02", "AG-20261015-01"}

    def record(self, utc_text: str = "2026-11-20T19:05:00+00:00", trade_id: str = "AG-20261020-02", action: str = "acted",
               rows_sha: Optional[str] = None) -> Dict[str, Any]:
        return {"utc": utc_text, "signal_id": "SIG-1", "trade_id": trade_id, "action": action, "until": None,
                "rows_sha": self.sha if rows_sha is None else rows_sha}

    def findings(self, records: List[Dict[str, Any]], status: str = state.STATUS_OK, et_date: datetime.date = TODAY) -> List[recon.Finding]:
        return recon.ack_findings(records, status, table=self.table, et_date=et_date, group_ids=self.ids)

    def test_acted_on_an_earlier_trading_day_with_unchanged_rows_nags(self) -> None:
        findings = self.findings([self.record()])
        self.assertEqual(1, len(findings))
        finding = findings[0]
        self.assertEqual((recon.FINDING_ACTED_UNCHANGED, recon.LEVEL_WARNING, "AG-20261020-02"), (finding.kind, finding.level, finding.group_id))
        self.assertIn("2 trading days", finding.message)
        self.assertIn("unchanged", finding.message)

    def test_same_day_changed_rows_other_actions_and_unknown_groups_are_quiet(self) -> None:
        self.assertEqual([], self.findings([self.record("2026-11-24T11:00:00+00:00")]), "acted today")
        self.assertEqual([], self.findings([self.record(rows_sha="0" * 64)]), "the rows changed")
        self.assertEqual([], self.findings([self.record(action="ack"), self.record(action="snooze")]))
        self.assertEqual([], self.findings([self.record(trade_id="AG-20991231-99")]), "the group is gone")
        self.assertEqual([], self.findings([]))
        self.assertEqual([], self.findings([], state.STATUS_MISSING))

    def test_latest_acted_record_per_group_decides(self) -> None:
        records = [self.record("2026-11-20T19:05:00+00:00"), self.record("2026-11-24T11:00:00+00:00")]
        self.assertEqual([], self.findings(records))
        self.assertEqual([], self.findings(list(reversed(records))))

    def test_one_trading_day_is_enough_and_the_weekend_does_not_count(self) -> None:
        self.assertEqual(1, len(self.findings([self.record("2026-11-23T19:05:00+00:00")])))
        self.assertIn("1 trading day", self.findings([self.record("2026-11-23T19:05:00+00:00")])[0].message)
        self.assertEqual([], self.findings([self.record("2026-11-20T19:05:00+00:00")], et_date=datetime.date(2026, 11, 21)),
                         "Saturday: no trading day has passed since Friday")

    def test_corrupt_journal_and_malformed_records_are_warnings(self) -> None:
        corrupt = self.findings([], state.STATUS_CORRUPT)
        self.assertEqual([(recon.FINDING_ACKS_UNREADABLE, recon.LEVEL_WARNING)], [(f.kind, f.level) for f in corrupt])
        malformed = self.findings([{"action": "acted"}, {"utc": "yesterday", "trade_id": "AG-20261020-02", "action": "acted"},
                                   "not a record"])
        self.assertEqual({recon.FINDING_ACKS_UNREADABLE}, {f.kind for f in malformed})
        self.assertEqual(3, len(malformed))


# ============================================================================================
# Verdict precedence, exit codes and the engine-level golden of the recon root
# ============================================================================================


class VerdictTest(unittest.TestCase):
    def test_precedence_and_exit_codes(self) -> None:
        self.assertEqual(recon.VERDICT_ERRORS, recon.verdict_for(file_errors=True, unavailable=True, edits=True))
        self.assertEqual(recon.VERDICT_UNAVAILABLE, recon.verdict_for(file_errors=False, unavailable=True, edits=True))
        self.assertEqual(recon.VERDICT_EDITS_PENDING, recon.verdict_for(file_errors=False, unavailable=False, edits=True))
        self.assertEqual(recon.VERDICT_CLEAN, recon.verdict_for(file_errors=False, unavailable=False, edits=False))
        self.assertEqual({"CLEAN": 0, "EDITS_PENDING": 10, "UNAVAILABLE": 20, "ERRORS": 1}, dict(recon.VERDICT_EXIT_CODES))
        self.assertEqual(("CLEAN", "EDITS_PENDING", "UNAVAILABLE", "ERRORS"), recon.VERDICTS)


class ReconRootGoldenTest(unittest.TestCase):
    """The B section's expected outcomes, through ``reconcile`` with the fixture bars and quotes injected."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = read_text(os.path.join(RECON_ROOT, "positions.csv"))
        cls.cal = calendar_events.load_calendar(os.path.join(RECON_ROOT, "research", "calendar.toml"))
        cls.positions = parse_positions_text(cls.text, today=TODAY)
        cls.groups = group_positions(cls.positions.rows)
        cls.session = session_at(RECON_NOW, cls.cal.extra_closures())
        cls.plan = recon.plan_settlement(cls.positions, cls.groups, now=RECON_NOW, session=cls.session, cal=cls.cal)
        histories: Dict[Any, FakeHistory] = {}
        for request in cls.plan.history_requests:
            path = os.path.join(RECON_SET, "history__%s__%s__%s.json" % (request.symbol, request.start, request.end))
            if os.path.exists(path):
                histories[request.key] = FakeHistory(fixture_bars(path))
            else:
                histories[request.key] = FakeHistory([], "fetch_failed:network")
        payload = read_json(os.path.join(RECON_SET, "quotes__SPY,SPY261218P00620000.json"))["quotes"]["quote"]
        quotes = {entry["symbol"]: Quote.from_vendor(entry) for entry in payload}
        cls.result = recon.reconcile(plan=cls.plan, positions=cls.positions, groups=cls.groups, table=recon.csv_table(cls.text),
                                     pin_band=PIN_BAND, histories=histories, quotes=FakeQuotes(quotes), acks=([], state.STATUS_MISSING))

    def outcomes(self) -> Dict[str, str]:
        return {item.group_id: item.outcome for item in self.result.settlements}

    def test_every_settlement_branch(self) -> None:
        self.assertEqual({
            "AG-20261015-01": "EXPIRED_OTM",
            "AG-20261020-02": "PIN_UNCERTAIN",
            "AG-20261022-03": "PROBABLE_ASSIGNED",
            "AG-20261023-04": "PIN_UNCERTAIN",
            "AG-20261101-05": "CASH_SETTLED",
            "AG-20261101-06": "SETTLEMENT_UNKNOWN",
            "AG-20261105-07": "EXPIRED_UNRECORDED",
        }, self.outcomes())
        by_group = {item.group_id: item for item in self.result.settlements}
        self.assertEqual(D("23.17"), by_group["AG-20261101-05"].close_price)
        self.assertEqual("fetch_failed:network", by_group["AG-20261105-07"].history_reason)
        self.assertEqual([3, 5, 7, 8, 9, 10, 11], [item.row_number for item in self.result.settlements])

    def test_verdict_unavailable_with_four_edits(self) -> None:
        self.assertEqual((recon.VERDICT_UNAVAILABLE, 20), (self.result.verdict, self.result.exit_code))
        self.assertEqual(4, len(self.result.edits))
        self.assertEqual([("update", 3), ("update", 7), ("update", 6), ("update", 9)],
                         [(edit.row_number is not None and edit.kind, edit.row_number) for edit in self.result.edits])
        self.assertEqual(["EXPIRED_OTM", "PROBABLE_ASSIGNED", "PROBABLE_ASSIGNED", "CASH_SETTLED"],
                         [edit.outcome for edit in self.result.edits])
        self.assertEqual(4, self.result.counts["unavailable"])
        self.assertEqual(4, self.result.counts["edits"])

    def test_quote_check_quoted_the_spy_pair_and_the_closed_group_is_ignored(self) -> None:
        self.assertEqual(("SPY", SPY_PUT_620), self.result.quote_check.quoted)
        self.assertNotIn("AG-20261001-09", self.outcomes())
        self.assertEqual([], [f for f in self.result.findings if f.level == recon.LEVEL_UNAVAILABLE])
        self.assertEqual(9, self.result.counts["groups"])
        self.assertEqual(8, self.result.counts["open_groups"])
        self.assertEqual(7, self.result.counts["expired_legs"])

    def test_as_dict_is_json_ready_and_lists_edits_as_csv_lines(self) -> None:
        document = json.loads(state.json_dumps(self.result.as_dict()))
        self.assertEqual("UNAVAILABLE", document["verdict"])
        self.assertEqual(20, document["exit_code"])
        self.assertEqual(4, len(document["edits"]))
        self.assertTrue(all("csv_line" in edit for edit in document["edits"]))
        self.assertEqual(7, len(document["settlements"]))
        self.assertEqual("2026-11-24", document["et_date"])
        self.assertEqual({}, document["quarantined"])
        self.assertEqual([], document["file_errors"])
        self.assertEqual(["SPY", SPY_PUT_620], document["quote_check"]["quoted"])


class ReconcileEdgeTest(unittest.TestCase):
    def reconcile(self, text: str, now: datetime.datetime, histories: Dict[Any, FakeHistory], quotes: Optional[FakeQuotes] = None,
                  acks: Tuple[list, str] = ([], state.STATUS_MISSING), cal: Any = None) -> recon.Reconciliation:
        positions = parse_positions_text(text, today=market_clock.to_et(now).date())
        groups = group_positions(positions.rows)
        info = session_at(now, calendar_events.extra_closures(cal))
        plan = recon.plan_settlement(positions, groups, now=now, session=info, cal=cal)
        return recon.reconcile(plan=plan, positions=positions, groups=groups, table=recon.csv_table(text), pin_band=PIN_BAND,
                               histories=histories, quotes=quotes, acks=acks)

    def test_file_errors_are_the_errors_verdict(self) -> None:
        text = "instrument_type,symbol,quantity,cost_basis_totl\nequity,WMT,1,2\n"
        result = self.reconcile(text, RECON_NOW, {})
        self.assertEqual((recon.VERDICT_ERRORS, 1), (result.verdict, result.exit_code))
        self.assertEqual(1, len(result.file_errors))
        self.assertIn("did you mean", result.file_errors[0])

    def test_quarantined_group_is_an_unavailable_item(self) -> None:
        text = text_of(csv_row(), option_row(WMT_CALL_105, "-2", "684.00"))  # sign error quarantines the group
        result = self.reconcile(text, RECON_NOW, {})
        self.assertEqual((recon.VERDICT_UNAVAILABLE, 20), (result.verdict, result.exit_code))
        self.assertEqual(["AG-20261020-02"], sorted(result.quarantined))
        kinds = [(f.kind, f.level, f.group_id) for f in result.findings]
        self.assertIn((recon.FINDING_QUARANTINED, recon.LEVEL_UNAVAILABLE, "AG-20261020-02"), kinds)

    def test_clean_book_without_expired_legs(self) -> None:
        text = text_of(csv_row(), option_row(WMT_CALL_105, "-2", "-684.00"))
        now = utc("2026-11-03T19:50:00Z")
        stamp = utc("2026-11-03T19:31:12Z")
        quotes = FakeQuotes({"WMT": quote("WMT", "104.20", "104.26", stamp), WMT_CALL_105: quote(WMT_CALL_105, "3.35", "3.50", stamp, True)})
        result = self.reconcile(text, now, {}, quotes)
        self.assertEqual((recon.VERDICT_CLEAN, 0, [], []), (result.verdict, result.exit_code, result.settlements, result.edits))
        self.assertEqual(("WMT", WMT_CALL_105), result.quote_check.quoted)

    def test_edits_pending_when_only_edits_exist(self) -> None:
        text = text_of(csv_row(), option_row(WMT_CALL_105, "-2", "-684.00"),
                       option_row(SPY_PUT_620, "1", "1130.00", trade_id="AG-20261110-08"))
        now = utc("2026-11-21T14:30:00Z")
        key = ("WMT", NOV20, NOV20)
        result = self.reconcile(text, now, {key: FakeHistory([bar(NOV20, "105.10", "104.40")])},
                                FakeQuotes({}, {"SPY": "market_closed", SPY_PUT_620: "market_closed"}, 0))
        self.assertEqual(("SPY", SPY_PUT_620), result.quote_check.symbols)
        self.assertEqual((recon.VERDICT_EDITS_PENDING, 10), (result.verdict, result.exit_code))
        self.assertEqual(["EXPIRED_OTM"], [item.outcome for item in result.settlements])
        self.assertEqual(1, len(result.edits))
        self.assertIn("quote check not run: market_closed", [f.message for f in result.findings])

    def test_missing_history_result_is_unrecorded(self) -> None:
        text = text_of(option_row(WMT_CALL_105, "-2", "-684.00"))
        result = self.reconcile(text, utc("2026-11-21T14:30:00Z"), {})
        self.assertEqual([recon.OUTCOME_EXPIRED_UNRECORDED], [item.outcome for item in result.settlements])
        self.assertEqual("not fetched", result.settlements[0].history_reason)
        self.assertEqual(recon.VERDICT_UNAVAILABLE, result.verdict)

    def test_an_am_leg_between_its_last_trade_and_the_settlement_morning_is_pending_not_unavailable(self) -> None:
        cal = calendar_events.load_calendar(os.path.join(RECON_ROOT, "research", "calendar.toml"))
        text = text_of(option_row(RUT_PUT, "-1", "-1850.00", trade_id="AG-20261101-06"))
        for now in (utc("2026-11-19T21:30:00Z"), utc("2026-11-20T12:00:00Z")):
            with self.subTest(now=now):
                result = self.reconcile(text, now, {}, cal=cal)
                self.assertEqual([recon.OUTCOME_SETTLEMENT_PENDING], [item.outcome for item in result.settlements])
                self.assertEqual((recon.VERDICT_CLEAN, 0, 1, 0, 0), (result.verdict, result.exit_code,
                                                                   result.counts["expired_legs"], result.counts["unavailable"],
                                                                   result.counts["edits"]))
                self.assertEqual([], result.edits)
                self.assertEqual((), result.quote_check.symbols, "an expired leg is not quoted")
        later = self.reconcile(text, utc("2026-11-20T14:30:00Z"), {}, cal=cal)
        self.assertEqual(([recon.OUTCOME_SETTLEMENT_UNKNOWN], recon.VERDICT_UNAVAILABLE, 1),
                         ([item.outcome for item in later.settlements], later.verdict, later.counts["unavailable"]))

    def test_acted_unchanged_appears_as_a_warning_finding(self) -> None:
        text = text_of(csv_row(), option_row(WMT_CALL_105, "-2", "-684.00"))
        sha = recon.group_rows_sha(recon.csv_table(text), "AG-20261020-02")
        acks = ([{"utc": "2026-11-02T19:00:00+00:00", "signal_id": "S", "trade_id": "AG-20261020-02", "action": "acted",
                  "until": None, "rows_sha": sha}], state.STATUS_OK)
        now = utc("2026-11-03T19:50:00Z")
        result = self.reconcile(text, now, {}, FakeQuotes({}, {"WMT": "quota_guard", WMT_CALL_105: "quota_guard"}), acks)
        kinds = [(f.kind, f.level) for f in result.findings]
        self.assertIn((recon.FINDING_ACTED_UNCHANGED, recon.LEVEL_WARNING), kinds)
        self.assertEqual(recon.VERDICT_CLEAN, result.verdict, "warnings never change the verdict")


# ============================================================================================
# Plausibility (positions_check)
# ============================================================================================


class PlausibilityTest(unittest.TestCase):
    STAMP = utc("2026-11-03T19:31:12Z")

    def setUp(self) -> None:
        self.parsed = parse(csv_row(total="19700.00"), option_row(WMT_CALL_105, "-2", "-684.00"),
                            option_row(NVDA_CALL_150, "-2", "-61000.00", trade_id="AG-20261022-03"),
                            csv_row(symbol="NVDA", total="2824000.00", trade_id="AG-20261022-04"),
                            option_row("SPX261218C06500000", "1", "2450.00", trade_id="AG-20261101-05"),
                            option_row(SPY_PUT_620, "1", "1130.00", trade_id="AG-20261110-08"),
                            option_row(WMT_PUT_95, "-1", "-165.00", trade_id="AG-20261015-01", status="closed", closed="2026-10-14",
                                       close_price="0.55"),
                            option_row("AMD261120C00230000", "-1", "-520.00", trade_id="AG-20261023-04"),
                            today=datetime.date(2026, 11, 3))
        self.quotes = {"WMT": quote("WMT", "104.20", "104.26", self.STAMP),
                       WMT_CALL_105: quote(WMT_CALL_105, "3.35", "3.50", self.STAMP, True),
                       NVDA_CALL_150: quote(NVDA_CALL_150, "3.1", "3.3", self.STAMP, True),
                       "NVDA": quote("NVDA", "148.9", "148.95", self.STAMP)}
        self.missing = {"SPX": "unmatched", "SPX261218C06500000": "unmatched", SPY_PUT_620: "quota_guard", "SPY": "quota_guard"}

    def checks(self, expired: Sequence[int] = (), sandbox: bool = True) -> Dict[int, recon.PlausibilityCheck]:
        checks = recon.plausibility(self.parsed.rows, self.quotes, missing=self.missing, expired_rows=frozenset(expired), sandbox=sandbox)
        return {check.row_number: check for check in checks}

    def test_option_within_twenty_times_is_ok_and_a_hundred_times_off_is_unit_mismatch(self) -> None:
        checks = self.checks(expired=[9])
        ok = checks[3]
        self.assertEqual((recon.PLAUSIBILITY_OK, D("3.42"), D("3.425")), (ok.status, ok.entry, ok.mark))
        bad = checks[4]
        self.assertEqual((recon.PLAUSIBILITY_ERROR, D("305"), D("3.2")), (bad.status, bad.entry, bad.mark))
        self.assertEqual("unit mismatch? entry 305.00 vs mark 3.20", bad.message)

    def test_stock_outside_ten_times_is_a_warning(self) -> None:
        checks = self.checks(expired=[9])
        self.assertEqual(recon.PLAUSIBILITY_OK, checks[2].status)
        self.assertEqual(recon.PLAUSIBILITY_WARNING, checks[5].status)
        self.assertIn("14120.00", checks[5].message)
        self.assertIn("148.925", checks[5].message)

    def test_index_underlying_is_unquotable_on_the_sandbox_and_quota_is_not_checked(self) -> None:
        checks = self.checks(expired=[9])
        self.assertEqual(recon.PLAUSIBILITY_UNQUOTABLE, checks[6].status)
        self.assertIn("sandbox", checks[6].message)
        self.assertEqual(recon.PLAUSIBILITY_NOT_CHECKED, checks[7].status)
        self.assertIn("quota_guard", checks[7].message)
        off_sandbox = self.checks(expired=[9], sandbox=False)
        self.assertEqual(recon.PLAUSIBILITY_NOT_CHECKED, off_sandbox[6].status)

    def test_closed_rows_are_skipped_and_expired_rows_are_labelled(self) -> None:
        checks = self.checks(expired=[9])
        self.assertNotIn(8, checks)
        self.assertEqual(recon.PLAUSIBILITY_EXPIRED, checks[9].status)
        self.assertIn("csv_recon", checks[9].message)

    def test_quarantine_from_errors_names_the_row_and_group(self) -> None:
        checks = list(self.checks(expired=[9]).values())
        quarantine = recon.plausibility_quarantine(checks, self.parsed.rows)
        self.assertEqual(["AG-20261022-03"], sorted(quarantine))
        error = quarantine["AG-20261022-03"][0]
        self.assertIsInstance(error, RowError)
        self.assertEqual((4, "cost_basis_total", "AG-20261022-03"), (error.row_number, error.field, error.trade_id))
        self.assertIn("unit mismatch?", error.message)

    def test_boundaries_are_inclusive(self) -> None:
        parsed = parse(option_row(WMT_CALL_105, "-1", "-6850.00"), option_row(WMT_CALL_105, "-1", "-17.00", trade_id="AG-20261020-03"),
                       option_row(WMT_CALL_105, "-1", "-6900.00", trade_id="AG-20261020-04"),
                       today=datetime.date(2026, 11, 3))
        quotes = {WMT_CALL_105: quote(WMT_CALL_105, "3.35", "3.50", self.STAMP, True)}  # mid 3.425: band [0.17125, 68.5]
        checks = {c.row_number: c.status for c in recon.plausibility(parsed.rows, quotes, missing={}, expired_rows=frozenset(), sandbox=True)}
        self.assertEqual({2: recon.PLAUSIBILITY_OK, 3: recon.PLAUSIBILITY_ERROR, 4: recon.PLAUSIBILITY_ERROR}, checks)

    def test_check_is_json_ready(self) -> None:
        document = json.loads(state.json_dumps(self.checks(expired=[9])[3].as_dict()))
        self.assertEqual(("ok", "3.42", "3.425", WMT_CALL_105, 3), (document["status"], document["entry"], document["mark"],
                                                                   document["symbol"], document["row_number"]))


# ============================================================================================
# The positions snapshot and the proof that positions.csv is never written
# ============================================================================================


class SnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-recon-snapshot-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(RECON_ROOT, self.root)
        self.paths = desk_paths.for_root(self.root)
        desk_paths.ensure_runtime_dirs(self.paths)
        with open(self.paths.positions_csv, "rb") as handle:
            self.data = handle.read()
        self.before = os.stat(self.paths.positions_csv)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_snapshot_copies_the_bytes_and_writes_the_digest(self) -> None:
        snapshot = recon.snapshot_positions(self.paths, self.data, TODAY)
        self.assertEqual(os.path.join(self.paths.positions_snapshots_dir, "2026-11-24.csv"), snapshot.csv_path)
        self.assertEqual(os.path.join(self.paths.positions_snapshots_dir, "2026-11-24.sha256"), snapshot.sha_path)
        with open(snapshot.csv_path, "rb") as handle:
            self.assertEqual(self.data, handle.read())
        digest = hashlib.sha256(self.data).hexdigest()
        self.assertEqual(digest, snapshot.sha256)
        with open(snapshot.sha_path, encoding="utf-8") as handle:
            self.assertEqual("%s  2026-11-24.csv\n" % digest, handle.read())
        self.assertEqual("data/positions/2026-11-24.csv", self.paths.relative(snapshot.csv_path))

    def test_positions_csv_is_untouched_and_a_rerun_is_idempotent(self) -> None:
        recon.snapshot_positions(self.paths, self.data, TODAY)
        recon.snapshot_positions(self.paths, self.data, TODAY)
        with open(self.paths.positions_csv, "rb") as handle:
            self.assertEqual(self.data, handle.read())
        after = os.stat(self.paths.positions_csv)
        self.assertEqual((self.before.st_mtime_ns, self.before.st_ino), (after.st_mtime_ns, after.st_ino))
        self.assertEqual(["2026-11-24.csv", "2026-11-24.sha256"], sorted(os.listdir(self.paths.positions_snapshots_dir)))

    def test_fixture_roots_are_never_written(self) -> None:
        for root in (RECON_ROOT, MAIN_ROOT):
            self.assertFalse(os.path.exists(os.path.join(root, "data")), root)
            self.assertFalse(os.path.exists(os.path.join(root, "state")), root)


# ============================================================================================
# Module hygiene
# ============================================================================================


class HygieneTest(unittest.TestCase):
    def test_no_float_print_payoff_or_order_words(self) -> None:
        source = read_text(MODULE_PATH)
        self.assertNotIn("float(", source)
        self.assertNotIn("print(", source)
        self.assertNotIn("payoff", source, "intrinsic is computed inline; A's module is not imported")
        order_words = re.compile(r"(?i)\b(?:buy|sell|sells|selling|sold|bought|submit|limit order|market order|good till|"
                                 r"time in force|day order)\b")
        self.assertIsNone(order_words.search(source))
        self.assertTrue(source.lstrip().startswith('"""'))
        self.assertIn("from __future__ import annotations", source.splitlines()[source.count("\n", 0, source.index("from __future__"))])

    def test_public_constants(self) -> None:
        self.assertEqual(("EXPIRED_OTM", "PROBABLE_ASSIGNED", "PROBABLE_EXERCISED", "PIN_UNCERTAIN", "CASH_SETTLED",
                          "SETTLEMENT_UNKNOWN", "EXPIRED_UNRECORDED", "SETTLEMENT_PENDING"), recon.OUTCOMES)
        self.assertEqual(("PIN_UNCERTAIN", "SETTLEMENT_UNKNOWN", "EXPIRED_UNRECORDED"), recon.UNAVAILABLE_OUTCOMES)
        self.assertEqual("confirm with your broker", recon.CONFIRM_WITH_BROKER)
        for name in ("csv_table", "render_csv_line", "group_rows_sha", "is_expired", "history_range", "settle_pm", "settle_am",
                     "propose_edits", "plan_settlement", "quote_check_symbols", "reconcile", "plausibility",
                     "plausibility_quarantine", "snapshot_positions", "verdict_for"):
            self.assertIn(name, recon.__all__, name)


if __name__ == "__main__":
    unittest.main()
