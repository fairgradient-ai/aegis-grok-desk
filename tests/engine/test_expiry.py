"""``aegis_desk.expiry``: the Stage-1 (European-only) expiry library behind ``assignment_watch.py``
and ``expiry_checklist.py``: the request batch, settlement and act-by times, the dividend
(extrinsic-versus-dividend) and pin tiers, the week docket implications and the exit codes."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import ast
import datetime
import os
import unittest
from decimal import Decimal
from typing import Dict, List, Optional

from aegis_desk import alert_lint, alerts, calendar_events, contract, expiry, grouping, market_clock, positions_csv, quote_qc
from aegis_desk.quote_qc import QuoteQC

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
MODULE = os.path.join(ROOT, "scripts", "aegis_desk", "expiry.py")
UTC = datetime.timezone.utc

HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
          "close_price_per_unit,underlying_stop,note\n")

CALENDAR = """
schema_version = 1

[symbols.WMT]
ex_dividend = { date = 2026-11-24, amount = 0.2350, status = "confirmed", source = "test", checked_utc = 2026-11-01T12:00:00Z }
dividend_yield = 0.0090

[symbols.NVDA]
ex_dividend = { date = 2026-12-03, amount = 0.01, status = "confirmed", source = "test", checked_utc = 2026-11-01T12:00:00Z }

[symbols.KO]
ex_dividend = { status = "unknown", source = "test", checked_utc = 2026-11-01T12:00:00Z }

[symbols.BRK]
ex_dividend = { status = "none", source = "test", checked_utc = 2026-11-01T12:00:00Z }

[symbols.XYZ]
ex_dividend = { date = 2026-11-20, amount = 0.50, status = "estimated", source = "test", checked_utc = 2026-11-01T12:00:00Z }

[settlement]
am_settled_roots = ["SPX", "NDX", "RUT"]
"""

T1_NOW = datetime.datetime(2026, 11, 19, 19, 50, tzinfo=UTC)
T1_STAMP = datetime.datetime(2026, 11, 19, 19, 31, 12, tzinfo=UTC)
EXPIRY_NOW = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)
EXPIRY_STAMP = datetime.datetime(2026, 11, 20, 19, 31, 12, tzinfo=UTC)
REGULAR_NOW = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC)
REGULAR_STAMP = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
CUTOFF = datetime.time(16, 30)
T1 = datetime.date(2026, 11, 19)
EXPIRY = datetime.date(2026, 11, 20)


def rows_from(lines: List[str], today: datetime.date = T1) -> List[positions_csv.PositionRow]:
    parsed = positions_csv.parse_positions_text(HEADER + "\n".join(lines) + "\n", today=today)
    assert parsed.is_clean, parsed.row_errors or parsed.file_errors
    return parsed.rows


def positions_from(lines: List[str], today: datetime.date = T1) -> positions_csv.PositionsFile:
    return positions_csv.parse_positions_text(HEADER + "\n".join(lines) + "\n", today=today)


def qc(symbol: str, bid: str, ask: str, as_of: datetime.datetime = T1_STAMP, grade: str = "A") -> QuoteQC:
    bid_d, ask_d = Decimal(bid), Decimal(ask)
    mid = (bid_d + ask_d) / 2
    return QuoteQC(symbol, grade, quote_qc.STATUS_FRESH, "as of 14:31:12 ET", 18, 18, quote_qc.spread_pct(bid_d, ask_d),
                   mid, bid_d, ask_d, grade in quote_qc.USABLE_GRADES, [], bid_d, ask_d, as_of)


def unusable(symbol: str, reason: str = "cache: unmatched") -> QuoteQC:
    return quote_qc.unavailable(symbol, reason, label="no quote")


def stale(symbol: str, bid: str, ask: str) -> QuoteQC:
    bid_d, ask_d = Decimal(bid), Decimal(ask)
    return QuoteQC(symbol, quote_qc.GRADE_F, quote_qc.STATUS_STALE, "as of 13:50:00 ET", 60, 60,
                   quote_qc.spread_pct(bid_d, ask_d), None, None, None, False, ["bid stale: 60 min old > bound 25 min"],
                   bid_d, ask_d, T1_STAMP - datetime.timedelta(minutes=42))


CAL = calendar_events.parse_calendar_text(CALENDAR)
CALENDAR_EX_MONDAY = CALENDAR.replace("date = 2026-11-24, amount = 0.2350", "date = 2026-11-23, amount = 0.2350")
"""The WMT ex-dividend moved to Mon 2026-11-23: its last cum-dividend day is Fri 2026-11-20, the Nov-20 call's last
trading day, so the call is graded (the boundary case; the tool tests rewrite the copied calendar the same way)."""
CAL_EX_MONDAY = calendar_events.parse_calendar_text(CALENDAR_EX_MONDAY)
CALENDAR_CLOSED_MONDAY = CALENDAR + '\n[[closures]]\ndate = 2026-11-23\nkind = "full"\nsource = "test"\n'
"""Mon 2026-11-23 a full closure: the Tue 2026-11-24 ex-dividend's last cum-dividend day moves to Fri 2026-11-20."""
CAL_CLOSED_MONDAY = calendar_events.parse_calendar_text(CALENDAR_CLOSED_MONDAY)
BOUNDARY_NOTE_WMT = ("ex-dividend 2026-11-23 is the first trading day after the last trading day 2026-11-20: "
                     "a dividend-capture exercise would come at expiration")
FIXTURE_CAL = calendar_events.load_calendar(os.path.join(FIXTURE_DESK, "research", "calendar.toml"))

WMT_SHARES = "equity,WMT,,200,,,,Brokerage (margin),AG-20261020-01,2026-10-20,,98.50,,open,,,,92.00,core holding"
WMT_CALL = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,"
            "covered call #assign-ok")
WMT_CALL_PLAIN = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,"
                  ",,,,covered call")
NVDA_SHARES = "equity,NVDA,,200,,,,Brokerage (margin),AG-20261022-02,2026-10-22,28240.00,,,open,,,,,"
NVDA_CALL = ("option,NVDA,,-2,2026-11-20,CALL,150,Brokerage (margin),AG-20261022-02,2026-10-22,-610.00,,,open,,,,,"
             "covered call #earnings-ok")
SPY_SHARES = "etf,SPY,,100,,,,Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,"
SPY_PUT = "option,SPY,SPY261218P00620000,1,,,,Roth IRA,AG-20261028-03,2026-10-28,1130.00,,11.30,open,,,,,protective put"
SPY_CLOSED = "option,SPY,SPY261016P00600000,-1,,,,Roth IRA,AG-20261001-04,2026-10-01,-480.00,,,closed,2026-10-14,1,0.55,,closed"
FIXTURE_LINES = [WMT_SHARES, WMT_CALL, NVDA_SHARES, NVDA_CALL, SPY_SHARES, SPY_PUT, SPY_CLOSED]


def t1_qcs() -> Dict[str, QuoteQC]:
    return {
        "WMT": qc("WMT", "106.40", "106.46"), "NVDA": qc("NVDA", "149.20", "149.25"), "SPY": qc("SPY", "652.10", "652.14"),
        "WMT261120C00105000": qc("WMT261120C00105000", "1.52", "1.60"),
        "NVDA261120C00150000": qc("NVDA261120C00150000", "1.00", "1.10"),
        "SPY261218P00620000": qc("SPY261218P00620000", "3.80", "4.00"),
    }


def expiry_day_qcs() -> Dict[str, QuoteQC]:
    stamp = EXPIRY_STAMP
    return {
        "WMT": qc("WMT", "104.90", "104.96", stamp), "NVDA": qc("NVDA", "153.30", "153.36", stamp),
        "SPY": qc("SPY", "650.00", "650.04", stamp),
        "WMT261120C00105000": qc("WMT261120C00105000", "0.12", "0.16", stamp),
        "NVDA261120C00150000": qc("NVDA261120C00150000", "3.30", "3.42", stamp),
        "SPY261218P00620000": qc("SPY261218P00620000", "4.10", "4.30", stamp),
    }


def sheet_for(lines: List[str], now: datetime.datetime, qcs: Dict[str, QuoteQC],
              cal: Optional[calendar_events.CalendarFile] = CAL) -> expiry.ExpirySheet:
    session = market_clock.session_at(now, extra_holidays=calendar_events.extra_closures(cal))
    positions = positions_from(lines, today=session.et_date)
    groups = grouping.group_positions(positions.rows)
    return expiry.build_sheet(positions, groups, now=now, session=session, cal=cal, broker_cutoff_et=CUTOFF, qcs=qcs)


def leg(sheet: expiry.ExpirySheet, symbol: str) -> expiry.LegSheet:
    found = [item for item in sheet.legs if item.symbol == symbol]
    assert len(found) == 1, [item.symbol for item in sheet.legs]
    return found[0]


class RequestBatchTest(unittest.TestCase):
    """ONE get_quotes call for {compact symbol of every open option leg whose last trading day is on
    or after today's ET date} + {its underlying} + {every open stock-like leg}, sorted, de-duplicated."""

    def test_main_root_on_2026_11_03_is_the_six_symbol_file(self) -> None:
        rows = positions_csv.load_positions(os.path.join(FIXTURE_DESK, "positions.csv"), today=datetime.date(2026, 11, 3)).rows
        self.assertEqual(["NVDA", "NVDA261120C00150000", "SPY", "SPY261218P00620000", "WMT", "WMT261120C00105000"],
                         expiry.request_symbols(rows, datetime.date(2026, 11, 3), FIXTURE_CAL))

    def test_expired_legs_are_left_out_on_the_saturday_after(self) -> None:
        rows = rows_from(FIXTURE_LINES, today=datetime.date(2026, 11, 21))
        self.assertEqual(["NVDA", "SPY", "SPY261218P00620000", "WMT"],
                         expiry.request_symbols(rows, datetime.date(2026, 11, 21), CAL))

    def test_last_trading_day_itself_is_still_requested(self) -> None:
        rows = rows_from(FIXTURE_LINES, today=EXPIRY)
        self.assertIn("WMT261120C00105000", expiry.request_symbols(rows, EXPIRY, CAL))

    def test_closed_rows_and_duplicates(self) -> None:
        rows = rows_from([WMT_CALL, NVDA_CALL, SPY_CLOSED,
                          "option,WMT,WMT261120C00110000,-1,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-100.00,,,open,,,,,"])
        self.assertEqual(["NVDA", "NVDA261120C00150000", "WMT", "WMT261120C00105000", "WMT261120C00110000"],
                         expiry.request_symbols(rows, T1, CAL))

    def test_without_a_calendar(self) -> None:
        rows = rows_from([SPY_SHARES, SPY_PUT])
        self.assertEqual(["SPY", "SPY261218P00620000"], expiry.request_symbols(rows, T1, None))


class SettlementAndActByTest(unittest.TestCase):
    def test_pm_settled_november_contract(self) -> None:
        row = rows_from([WMT_CALL])[0]
        self.assertEqual(expiry.SETTLEMENT_PM, expiry.settlement_of(row, CAL))
        self.assertEqual(EXPIRY, expiry.leg_last_trading_day(row, CAL))
        act = expiry.act_by(EXPIRY, CUTOFF, T1_NOW)
        self.assertEqual(datetime.datetime(2026, 11, 20, 21, 30, tzinfo=UTC), act.utc)
        self.assertEqual(("2026-11-20 16:30 ET", "21:30 UTC", False), (act.et_text, act.utc_text, act.passed))
        self.assertEqual(EXPIRY, act.last_trading_day)
        self.assertEqual(CUTOFF, act.cutoff_et)

    def test_act_by_passed_after_the_cutoff(self) -> None:
        self.assertTrue(expiry.act_by(EXPIRY, CUTOFF, datetime.datetime(2026, 11, 20, 21, 30, tzinfo=UTC)).passed)
        self.assertFalse(expiry.act_by(EXPIRY, CUTOFF, datetime.datetime(2026, 11, 20, 21, 29, 59, tzinfo=UTC)).passed)

    def test_am_settled_index_root_stops_trading_the_day_before(self) -> None:
        row = rows_from(["option,SPX,SPX261120C06500000,1,,,,,AG-20261101-05,2026-11-01,2450.00,,24.50,open,,,,,index call"])[0]
        self.assertEqual(expiry.SETTLEMENT_AM, expiry.settlement_of(row, CAL))
        self.assertEqual(datetime.date(2026, 11, 19), expiry.leg_last_trading_day(row, CAL))
        self.assertEqual(datetime.datetime(2026, 11, 19, 21, 30, tzinfo=UTC), expiry.act_by(
            expiry.leg_last_trading_day(row, CAL), CUTOFF, T1_NOW).utc)

    def test_without_a_calendar_every_root_is_pm_settled(self) -> None:
        row = rows_from(["option,SPX,SPX261120C06500000,1,,,,,AG-20261101-05,2026-11-01,2450.00,,24.50,open,,,,,index call"])[0]
        self.assertEqual(expiry.SETTLEMENT_PM, expiry.settlement_of(row, None))
        self.assertEqual(EXPIRY, expiry.leg_last_trading_day(row, None))

    def test_holiday_and_legacy_saturday_expirations_move_back(self) -> None:
        thanksgiving = rows_from(["option,WMT,WMT261126C00105000,-1,,,,,AG-20261020-01,2026-10-20,-100.00,,,open,,,,,"])[0]
        self.assertEqual(datetime.date(2026, 11, 25), expiry.leg_last_trading_day(thanksgiving, CAL))
        saturday = rows_from(["option,WMT,WMT261121C00105000,-1,,,,,AG-20261020-01,2026-10-20,-100.00,,,open,,,,,"])[0]
        self.assertEqual(EXPIRY, expiry.leg_last_trading_day(saturday, CAL))

    def test_act_by_on_an_early_close_day_is_the_earlier_of_the_cutoff_and_the_close(self) -> None:
        # 2026-11-27 closes at 13:00 ET: a 16:30 ET broker cutoff would name a time three and a half hours
        # after the session has ended, so the act-by is the close, marked as an early close to confirm.
        act = expiry.act_by(datetime.date(2026, 11, 27), CUTOFF, T1_NOW)
        self.assertEqual(datetime.datetime(2026, 11, 27, 18, 0, tzinfo=UTC), act.utc)
        self.assertEqual(("2026-11-27 13:00 ET", "18:00 UTC", datetime.time(13, 0), CUTOFF, True),
                         (act.et_text, act.utc_text, act.cutoff_et, act.broker_cutoff_et, act.early_close))
        self.assertEqual("act by Fri 2026-11-27 13:00 ET (18:00 UTC) [next trading day]; early close 13:00 ET: confirm your "
                         "broker's cutoff", expiry.act_by_text(act, datetime.date(2026, 11, 25), datetime.date(2026, 11, 27)),
                         "Thanksgiving is skipped: Friday is the next trading day after Wednesday")
        self.assertEqual("act by Fri 2026-11-27 13:00 ET (18:00 UTC); early close 13:00 ET: confirm your broker's cutoff",
                         expiry.act_by_text(act, datetime.date(2026, 11, 16), datetime.date(2026, 11, 17)))
        earlier = expiry.act_by(datetime.date(2026, 11, 27), datetime.time(12, 30), T1_NOW)
        self.assertEqual((datetime.datetime(2026, 11, 27, 17, 30, tzinfo=UTC), datetime.time(12, 30), True),
                         (earlier.utc, earlier.cutoff_et, earlier.early_close), "a cutoff before the close stands")
        normal = expiry.act_by(EXPIRY, CUTOFF, T1_NOW)
        self.assertEqual((CUTOFF, CUTOFF, False), (normal.cutoff_et, normal.broker_cutoff_et, normal.early_close))
        self.assertNotIn("early close", expiry.act_by_text(normal, T1, EXPIRY))
        self.assertTrue(expiry.act_by(datetime.date(2026, 11, 27), CUTOFF,
                                      datetime.datetime(2026, 11, 27, 18, 0, tzinfo=UTC)).passed, "passed at the close")

    def test_act_by_on_an_injected_full_closure_moves_with_the_last_trading_day(self) -> None:
        # The calendar's closures are full closures: the leg's last trading day moves back and the act-by with it.
        extra = {datetime.date(2026, 11, 27): "calendar closure"}
        row = rows_from(["option,WMT,WMT261127C00105000,-1,,,,,AG-20261020-01,2026-10-20,-100.00,,,open,,,,,"])[0]
        self.assertEqual(datetime.date(2026, 11, 25), market_clock.last_trading_day(row.expiration, False, extra))
        act = expiry.act_by(datetime.date(2026, 11, 25), CUTOFF, T1_NOW, extra)
        self.assertEqual((datetime.datetime(2026, 11, 25, 21, 30, tzinfo=UTC), False), (act.utc, act.early_close))

    def test_act_by_text_marks_today_and_the_next_trading_day(self) -> None:
        act = expiry.act_by(EXPIRY, CUTOFF, T1_NOW)
        self.assertEqual("act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]",
                         expiry.act_by_text(act, T1, EXPIRY))
        self.assertEqual("act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]",
                         expiry.act_by_text(act, EXPIRY, datetime.date(2026, 11, 23)))
        self.assertEqual("act by Fri 2026-11-20 16:30 ET (21:30 UTC)",
                         expiry.act_by_text(act, datetime.date(2026, 11, 16), datetime.date(2026, 11, 17)))
        passed = expiry.act_by(EXPIRY, CUTOFF, datetime.datetime(2026, 11, 20, 22, 0, tzinfo=UTC))
        self.assertEqual("act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today, passed]",
                         expiry.act_by_text(passed, EXPIRY, datetime.date(2026, 11, 23)))


class LegActByTest(unittest.TestCase):
    """FAI-689: a SHORT option is closed only while the market trades, so its act-by is the session close of the last
    trading day (16:00 ET; 13:00 ET on an early close) or the broker cutoff when that is earlier, and no early-close
    marker is printed for it; a LONG leg keeps the broker cutoff (exercise and do-not-exercise instructions) with the
    early-close rule and marker. ``ActBy.basis`` names the instant that governed."""

    SHORT_PHRASE = "the session close: a short option is closed only while the market trades"
    EARLY = datetime.date(2026, 11, 27)

    def short_call(self) -> positions_csv.PositionRow:
        return rows_from([WMT_SHARES, WMT_CALL])[1]

    def long_put(self) -> positions_csv.PositionRow:
        return rows_from([SPY_SHARES, SPY_PUT])[1]

    def test_the_basis_vocabulary(self) -> None:
        self.assertEqual(("session_close", "broker_cutoff"), (expiry.BASIS_SESSION_CLOSE, expiry.BASIS_BROKER_CUTOFF))
        self.assertEqual((expiry.BASIS_SESSION_CLOSE, expiry.BASIS_BROKER_CUTOFF), expiry.ACT_BY_BASES)
        self.assertEqual(self.SHORT_PHRASE, expiry.SHORT_BASIS_PHRASE)

    def test_act_by_names_its_basis(self) -> None:
        self.assertEqual(expiry.BASIS_BROKER_CUTOFF, expiry.act_by(EXPIRY, CUTOFF, T1_NOW).basis)
        self.assertEqual(expiry.BASIS_SESSION_CLOSE, expiry.act_by(self.EARLY, CUTOFF, T1_NOW).basis,
                         "the 13:00 ET close governs a 16:30 ET cutoff on an early-close day")
        self.assertEqual(expiry.BASIS_BROKER_CUTOFF, expiry.act_by(self.EARLY, datetime.time(12, 30), T1_NOW).basis)

    def test_a_short_leg_acts_by_the_session_close(self) -> None:
        act = expiry.leg_act_by(self.short_call(), EXPIRY, CUTOFF, T1_NOW)
        self.assertEqual((datetime.datetime(2026, 11, 20, 21, 0, tzinfo=UTC), "2026-11-20 16:00 ET", "21:00 UTC",
                          datetime.time(16, 0), CUTOFF, False, False, expiry.BASIS_SESSION_CLOSE),
                         (act.utc, act.et_text, act.utc_text, act.cutoff_et, act.broker_cutoff_et, act.early_close,
                          act.passed, act.basis))
        self.assertTrue(expiry.leg_act_by(self.short_call(), EXPIRY, CUTOFF,
                                          datetime.datetime(2026, 11, 20, 21, 0, tzinfo=UTC)).passed)
        self.assertFalse(expiry.leg_act_by(self.short_call(), EXPIRY, CUTOFF,
                                           datetime.datetime(2026, 11, 20, 20, 59, 59, tzinfo=UTC)).passed)

    def test_a_short_leg_on_an_early_close_day_has_no_broker_marker(self) -> None:
        act = expiry.leg_act_by(self.short_call(), self.EARLY, CUTOFF, T1_NOW)
        self.assertEqual((datetime.datetime(2026, 11, 27, 18, 0, tzinfo=UTC), datetime.time(13, 0), False,
                          expiry.BASIS_SESSION_CLOSE, None),
                         (act.utc, act.cutoff_et, act.early_close, act.basis, act.early_close_text))
        self.assertEqual("act by Fri 2026-11-27 13:00 ET (18:00 UTC) [today]; " + self.SHORT_PHRASE,
                         expiry.leg_act_by_text(self.short_call(), act, self.EARLY, datetime.date(2026, 11, 30)))

    def test_a_short_leg_with_an_earlier_broker_cutoff_keeps_it(self) -> None:
        act = expiry.leg_act_by(self.short_call(), EXPIRY, datetime.time(15, 30), T1_NOW)
        self.assertEqual((datetime.time(15, 30), expiry.BASIS_BROKER_CUTOFF, False), (act.cutoff_et, act.basis, act.early_close))
        self.assertEqual("act by Fri 2026-11-20 15:30 ET (20:30 UTC) [next trading day]",
                         expiry.leg_act_by_text(self.short_call(), act, T1, EXPIRY), "no basis phrase: the cutoff governs")
        tie = expiry.leg_act_by(self.short_call(), EXPIRY, datetime.time(16, 0), T1_NOW)
        self.assertEqual((datetime.time(16, 0), expiry.BASIS_SESSION_CLOSE), (tie.cutoff_et, tie.basis),
                         "a 16:00 ET cutoff equals the close: the session close is the reason")
        early = expiry.leg_act_by(self.short_call(), self.EARLY, datetime.time(12, 30), T1_NOW)
        self.assertEqual((datetime.time(12, 30), expiry.BASIS_BROKER_CUTOFF, False, None),
                         (early.cutoff_et, early.basis, early.early_close, early.early_close_text))

    def test_a_short_leg_on_an_injected_closure_moves_with_the_last_trading_day(self) -> None:
        extra = {datetime.date(2026, 11, 26): "Thanksgiving Day", datetime.date(2026, 11, 27): "test closure"}
        act = expiry.leg_act_by(self.short_call(), datetime.date(2026, 11, 25), CUTOFF, T1_NOW, extra)
        self.assertEqual((datetime.datetime(2026, 11, 25, 21, 0, tzinfo=UTC), expiry.BASIS_SESSION_CLOSE),
                         (act.utc, act.basis))

    def test_a_long_leg_keeps_the_broker_cutoff(self) -> None:
        put = self.long_put()
        for day, now in ((EXPIRY, T1_NOW), (self.EARLY, T1_NOW), (datetime.date(2026, 12, 18), REGULAR_NOW)):
            with self.subTest(day=day):
                self.assertEqual(expiry.act_by(day, CUTOFF, now), expiry.leg_act_by(put, day, CUTOFF, now))
        act = expiry.leg_act_by(put, EXPIRY, CUTOFF, T1_NOW)
        self.assertEqual((datetime.time(16, 30), expiry.BASIS_BROKER_CUTOFF), (act.cutoff_et, act.basis))
        self.assertEqual("act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]",
                         expiry.leg_act_by_text(put, act, T1, EXPIRY))
        early = expiry.leg_act_by(put, self.EARLY, CUTOFF, T1_NOW)
        self.assertEqual((datetime.time(13, 0), True, expiry.BASIS_SESSION_CLOSE), (early.cutoff_et, early.early_close, early.basis))
        self.assertEqual("act by Fri 2026-11-27 13:00 ET (18:00 UTC) [today]; early close 13:00 ET: confirm your broker's "
                         "cutoff", expiry.leg_act_by_text(put, early, self.EARLY, datetime.date(2026, 11, 30)))

    def test_a_stock_row_has_no_act_by(self) -> None:
        with self.assertRaises(ValueError):
            expiry.leg_act_by(rows_from([WMT_SHARES])[0], EXPIRY, CUTOFF, T1_NOW)

    def test_the_sheet_uses_the_leg_rule(self) -> None:
        lines = FIXTURE_LINES + ["option,SPY,SPY261120P00600000,1,,,,Roth IRA,AG-20261101-05,2026-11-01,100.00,,1.00,"
                                 "open,,,,,long put into expiry"]
        sheet = sheet_for(lines, EXPIRY_NOW, expiry_day_qcs())
        wmt, put = leg(sheet, "WMT261120C00105000"), leg(sheet, "SPY261120P00600000")
        self.assertEqual("act by Fri 2026-11-20 16:00 ET (21:00 UTC) [today]; " + self.SHORT_PHRASE, wmt.act_by_text)
        self.assertEqual("act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]", put.act_by_text)
        short_doc, long_doc = wmt.as_dict(), put.as_dict()
        self.assertEqual(("session_close", "2026-11-20 16:00 ET", "21:00 UTC", False),
                         (short_doc["act_by_basis"], short_doc["act_by_et"], short_doc["act_by_utc_text"],
                          short_doc["act_by_early_close"]))
        self.assertEqual(("broker_cutoff", "2026-11-20 16:30 ET"), (long_doc["act_by_basis"], long_doc["act_by_et"]))
        self.assertIn("act_by_basis", expiry.LEG_OUTPUT)
        self.assertEqual(list(short_doc).index("act_by_early_close") + 1, list(short_doc).index("act_by_basis"),
                         "act_by_basis sits next to the other act_by keys")

    def test_the_short_text_passes_the_strict_rules_lint(self) -> None:
        act = expiry.leg_act_by(self.short_call(), EXPIRY, CUTOFF, EXPIRY_NOW)
        text = expiry.leg_act_by_text(self.short_call(), act, EXPIRY, datetime.date(2026, 11, 23))
        for body in (text, "AG-20261020-01 WMT261120C00105000 short 2 | " + text):
            emitted: List[str] = []
            rule_ids: List[str] = []
            identifiers: List[str] = []
            clocks: List[str] = []
            message = alerts.fmt_reason(body, emitted, rule_ids=rule_ids, identifiers=identifiers, clocks=clocks)
            message += "\n" + alerts.fmt_footer(EXPIRY_STAMP, 15, emitted, clocks)
            result = alert_lint.check(message, emitted, rule_ids=rule_ids or None, identifiers=identifiers, clocks=clocks,
                                      as_of=EXPIRY_STAMP)
            self.assertTrue(result.ok, result.problems)


class DividendTierTest(unittest.TestCase):
    """Short calls only; the ex-date is inside the window (today, today + 5 trading days] by
    ``trading_days_between`` AND the last cum-dividend day (the trading day before the ex-date) is on
    or before the leg's last trading day, else nothing is listed (a dividend-capture exercise happens at
    the close of the cum-dividend day; a contract gone by then carries no such risk);
    ``extrinsic_bid = bid - max(0, S_mid - K)``; HIGH below the amount, WATCH below twice it, LOW
    otherwise; ``#assign-ok`` turns HIGH into NOTED; unknown is UNAVAILABLE, never zero."""

    def tier(self, line: str, option: QuoteQC, underlying: QuoteQC, cal=CAL, today: datetime.date = T1):
        row = rows_from([line], today=today)[0]
        return expiry.dividend_tier(row, option, underlying, cal, today, calendar_events.extra_closures(cal))

    def test_fixture_geometry_the_nov_20_call_is_gone_before_the_nov_24_cum_dividend_day(self) -> None:
        # The last cum-dividend day of the Tue 2026-11-24 ex-date is Mon 2026-11-23, after the call's last trading
        # day Fri 2026-11-20: a dividend-capture exercise cannot reach this contract, so there is nothing to list
        # (its assignment risk at expiry is the pin tier's). The ex-date is two trading days after the last day.
        self.assertEqual(datetime.date(2026, 11, 23), market_clock.previous_trading_day(datetime.date(2026, 11, 24)))
        self.assertEqual(EXPIRY, expiry.leg_last_trading_day(rows_from([WMT_CALL])[0], CAL))
        for line in (WMT_CALL, WMT_CALL_PLAIN):
            self.assertIsNone(self.tier(line, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46")))
            self.assertIsNone(self.tier(line, qc("WMT261120C00105000", "0.12", "0.16", EXPIRY_STAMP),
                                        qc("WMT", "104.90", "104.96", EXPIRY_STAMP), today=EXPIRY))
        # The contract's life is checked before the quotes: an unusable quote changes nothing here.
        self.assertIsNone(self.tier(WMT_CALL_PLAIN, unusable("WMT261120C00105000"), stale("WMT", "106.40", "106.46")))
        self.assertIsNone(self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"),
                                    today=datetime.date(2026, 11, 17)), "five trading days out, and still gone before the cum day")

    def test_ex_dividend_on_the_first_trading_day_after_the_last_trading_day_is_graded_with_the_note(self) -> None:
        # Ex-dividend Mon 2026-11-23: the last cum-dividend day is Fri 2026-11-20, the call's own last trading day,
        # so a dividend-capture exercise would come at expiration and the leg is graded, with the boundary note.
        tier = self.tier(WMT_CALL, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"), cal=CAL_EX_MONDAY)
        self.assertIsNotNone(tier)
        self.assertEqual((expiry.TIER_NOTED, expiry.TIER_HIGH), (tier.tier, tier.downgraded_from))
        self.assertEqual((datetime.date(2026, 11, 23), 2, "confirmed", Decimal("0.2350")),
                         (tier.ex_date, tier.trading_days_to_ex, tier.dividend_status, tier.amount))
        self.assertEqual((Decimal("1.52"), Decimal("106.43"), Decimal("1.43"), Decimal("0.09"), True),
                         (tier.bid, tier.spot_mid, tier.intrinsic, tier.extrinsic_bid, tier.itm))
        self.assertEqual((BOUNDARY_NOTE_WMT,), tier.notes)
        self.assertEqual(BOUNDARY_NOTE_WMT, expiry.BOUNDARY_NOTE % ("2026-11-23", "2026-11-20"))
        self.assertFalse(tier.fired)
        on_expiry = self.tier(WMT_CALL, qc("WMT261120C00105000", "0.12", "0.16", EXPIRY_STAMP),
                              qc("WMT", "104.90", "104.96", EXPIRY_STAMP), cal=CAL_EX_MONDAY, today=EXPIRY)
        self.assertEqual((expiry.TIER_NOTED, 1, Decimal("0"), Decimal("0.12"), False, (BOUNDARY_NOTE_WMT,)),
                         (on_expiry.tier, on_expiry.trading_days_to_ex, on_expiry.intrinsic, on_expiry.extrinsic_bid,
                          on_expiry.itm, on_expiry.notes))

    def test_ex_dividend_on_or_before_the_last_trading_day_is_graded_without_the_note(self) -> None:
        # Ex-dividend Tue 2026-11-24 against a call that last trades Fri 2026-11-27: the cum day Mon 2026-11-23 is
        # inside the contract's life, 3 trading days from T1, no note.
        alive = WMT_CALL_PLAIN.replace("WMT261120C00105000", "WMT261127C00105000")
        tier = self.tier(alive, qc("WMT261127C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"))
        self.assertEqual((expiry.TIER_HIGH, datetime.date(2026, 11, 24), 3, (), True),
                         (tier.tier, tier.ex_date, tier.trading_days_to_ex, tier.notes, tier.fired))
        # Ex-dividend ON the last trading day (XYZ 2026-11-20): the cum day is T1 itself, one trading day out.
        xyz = "option,XYZ,XYZ261120C00100000,-1,,,,,AG-20261020-02,2026-10-20,-100.00,,,open,,,,,"
        tier = self.tier(xyz, qc("XYZ261120C00100000", "0.30", "0.40"), qc("XYZ", "100.00", "100.10"))
        self.assertEqual((expiry.TIER_HIGH, "estimated", datetime.date(2026, 11, 20), 1, (), Decimal("0.25")),
                         (tier.tier, tier.dividend_status, tier.ex_date, tier.trading_days_to_ex, tier.notes, tier.extrinsic_bid))

    def test_a_full_closure_between_the_ex_date_and_the_last_trading_day_moves_the_cum_day(self) -> None:
        # Mon 2026-11-23 a full closure: the Tue 2026-11-24 ex-date's last cum-dividend day becomes Fri 2026-11-20, the
        # call's last trading day, so the leg is graded (with the note) and the ex-date is 2 trading days out.
        extra = calendar_events.extra_closures(CAL_CLOSED_MONDAY)
        self.assertEqual({datetime.date(2026, 11, 23): calendar_events.CLOSURE_LABEL}, extra)
        self.assertEqual(datetime.date(2026, 11, 20), market_clock.previous_trading_day(datetime.date(2026, 11, 24), extra))
        tier = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"),
                         cal=CAL_CLOSED_MONDAY)
        self.assertEqual((expiry.TIER_HIGH, datetime.date(2026, 11, 24), 2, Decimal("0.09"), True),
                         (tier.tier, tier.ex_date, tier.trading_days_to_ex, tier.extrinsic_bid, tier.fired))
        self.assertEqual((expiry.BOUNDARY_NOTE % ("2026-11-24", "2026-11-20"),), tier.notes)

    def test_without_assign_ok_the_same_leg_is_high_and_fires(self) -> None:
        tier = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"), cal=CAL_EX_MONDAY)
        self.assertEqual((expiry.TIER_HIGH, None, True, (BOUNDARY_NOTE_WMT,)), (tier.tier, tier.downgraded_from, tier.fired, tier.notes))

    def test_watch_and_low_thresholds_are_exact(self) -> None:
        # amount 0.2350: extrinsic 0.2350 is not HIGH (not below), 0.4699 is WATCH, 0.4700 is LOW.
        cal = CAL_EX_MONDAY
        high = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.6649", "1.70"), qc("WMT", "106.40", "106.46"), cal=cal)
        self.assertEqual(expiry.TIER_HIGH, high.tier)
        boundary = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.6650", "1.70"), qc("WMT", "106.40", "106.46"), cal=cal)
        self.assertEqual((expiry.TIER_WATCH, Decimal("0.2350")), (boundary.tier, boundary.extrinsic_bid))
        watch = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.8999", "1.95"), qc("WMT", "106.40", "106.46"), cal=cal)
        self.assertEqual((expiry.TIER_WATCH, Decimal("0.4699")), (watch.tier, watch.extrinsic_bid))
        low = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.90", "1.95"), qc("WMT", "106.40", "106.46"), cal=cal)
        self.assertEqual((expiry.TIER_LOW, Decimal("0.47")), (low.tier, low.extrinsic_bid))
        self.assertFalse(watch.fired or low.fired)

    def test_out_of_the_money_call_uses_the_bid_as_extrinsic(self) -> None:
        tier = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "0.12", "0.16"), qc("WMT", "104.90", "104.96"), cal=CAL_EX_MONDAY)
        self.assertEqual((Decimal("0"), Decimal("0.12"), False, expiry.TIER_HIGH),
                         (tier.intrinsic, tier.extrinsic_bid, tier.itm, tier.tier))

    def test_window_is_exclusive_of_today_inclusive_of_five_trading_days(self) -> None:
        # Ex-dividend 2026-11-24 against a December call (alive on the cum day): 6 trading days from Mon 2026-11-16
        # (17, 18, 19, 20, 23, 24), 5 from Tue 2026-11-17.
        december = WMT_CALL_PLAIN.replace("WMT261120C00105000", "WMT261218C00105000")
        self.assertEqual(6, market_clock.trading_days_between(datetime.date(2026, 11, 16), datetime.date(2026, 11, 24)))
        self.assertEqual(5, market_clock.trading_days_between(datetime.date(2026, 11, 17), datetime.date(2026, 11, 24)))
        beyond = self.tier(december, qc("WMT261218C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"),
                           today=datetime.date(2026, 11, 16))
        self.assertIsNone(beyond)
        edge = self.tier(december, qc("WMT261218C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"),
                         today=datetime.date(2026, 11, 17))
        self.assertEqual((5, ()), (edge.trading_days_to_ex, edge.notes))
        regular = self.tier(december, qc("WMT261218C00105000", "3.35", "3.50"), qc("WMT", "104.20", "104.26"),
                            today=datetime.date(2026, 11, 3))
        self.assertIsNone(regular, "15 trading days out: nothing to watch on 2026-11-03")

    def test_a_holiday_inside_the_window_is_not_a_trading_day(self) -> None:
        # NVDA ex-dividend 2026-12-03 seen from Wed 2026-11-25: 27 (early close), 30, 1, 2, 3 = 5 (Thanksgiving skipped).
        tier = self.tier(NVDA_CALL.replace("2026-11-20", "2026-12-18"), qc("NVDA261218C00150000", "1.00", "1.10"),
                         qc("NVDA", "149.20", "149.25"), today=datetime.date(2026, 11, 25))
        self.assertEqual((5, datetime.date(2026, 12, 3)), (tier.trading_days_to_ex, tier.ex_date))

    def test_unknown_status_missing_symbol_and_no_calendar_are_unavailable(self) -> None:
        ko = "option,KO,KO261120C00070000,-1,,,,,AG-20261020-02,2026-10-20,-100.00,,,open,,,,,"
        tier = self.tier(ko, qc("KO261120C00070000", "1.00", "1.10"), qc("KO", "70.00", "70.10"))
        self.assertEqual((expiry.TIER_UNAVAILABLE, "unknown"), (tier.tier, tier.dividend_status))
        self.assertIn(expiry.REASON_UNKNOWN_DIVIDEND, tier.reason)
        missing = self.tier("option,ABC,ABC261120C00070000,-1,,,,,AG-20261020-02,2026-10-20,-100.00,,,open,,,,,",
                            qc("ABC261120C00070000", "1.00", "1.10"), qc("ABC", "70.00", "70.10"))
        self.assertEqual(expiry.TIER_UNAVAILABLE, missing.tier)
        self.assertIn("ABC is not in calendar.toml", missing.reason)
        no_cal = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"), cal=None)
        self.assertEqual(expiry.TIER_UNAVAILABLE, no_cal.tier)
        self.assertIn("no research/calendar.toml", no_cal.reason)
        for item in (tier, missing, no_cal):
            self.assertFalse(item.fired)
            self.assertTrue(item.unavailable)

    def test_a_past_ex_date_is_unavailable_until_the_calendar_is_updated(self) -> None:
        tier = self.tier(WMT_CALL.replace("WMT261120C00105000", "WMT261218C00105000"), qc("WMT261218C00105000", "2.00", "2.10"),
                         qc("WMT", "106.40", "106.46"), today=datetime.date(2026, 11, 24))
        self.assertEqual(expiry.TIER_UNAVAILABLE, tier.tier)
        self.assertIn("2026-11-24 has passed", tier.reason)

    def test_confirmed_absence_and_a_date_beyond_the_window_are_nothing_to_list(self) -> None:
        brk = "option,BRK,BRK261120C00500000,-1,,,,,AG-20261020-02,2026-10-20,-100.00,,,open,,,,,"
        self.assertIsNone(self.tier(brk, qc("BRK261120C00500000", "1.00", "1.10"), qc("BRK", "500.00", "500.10")))
        # NVDA ex-dividend 2026-12-03 seen from T1: nine trading days out for a December call alive on the cum day.
        self.assertIsNone(self.tier(NVDA_CALL.replace("2026-11-20", "2026-12-18"), qc("NVDA261218C00150000", "1.00", "1.10"),
                                    qc("NVDA", "149.20", "149.25")))
        self.assertIsNone(self.tier(NVDA_CALL, qc("NVDA261120C00150000", "1.00", "1.10"), qc("NVDA", "149.20", "149.25")),
                          "and the Nov-20 call is gone before the cum day either way")

    def test_only_open_short_calls_qualify(self) -> None:
        cal = CAL_EX_MONDAY  # the geometry that grades the open short call, so the row kind alone decides
        long_call = WMT_CALL_PLAIN.replace(",-2,", ",2,").replace("-684.00", "684.00")
        self.assertIsNone(self.tier(long_call, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"), cal=cal))
        short_put = "option,WMT,WMT261120P00105000,-2,,,,,AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,"
        self.assertIsNone(self.tier(short_put, qc("WMT261120P00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"), cal=cal))
        closed = WMT_CALL_PLAIN.replace(",open,,,,,covered call", ",closed,2026-11-10,2,1.00,,covered call")
        self.assertIsNone(self.tier(closed, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"), cal=cal))
        self.assertIsNotNone(self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.52", "1.60"), qc("WMT", "106.40", "106.46"),
                                       cal=cal))

    def test_quotes_unavailable_make_the_leg_unavailable(self) -> None:
        cal = CAL_EX_MONDAY
        option_missing = self.tier(WMT_CALL_PLAIN, unusable("WMT261120C00105000"), qc("WMT", "106.40", "106.46"), cal=cal)
        self.assertEqual(expiry.TIER_UNAVAILABLE, option_missing.tier)
        self.assertIn("WMT261120C00105000 quote unavailable (F missing: cache: unmatched)", option_missing.reason)
        underlying_stale = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.52", "1.60"), stale("WMT", "106.40", "106.46"),
                                     cal=cal)
        self.assertEqual(expiry.TIER_UNAVAILABLE, underlying_stale.tier)
        self.assertIn("WMT quote unavailable (F stale", underlying_stale.reason)
        self.assertEqual((datetime.date(2026, 11, 23), Decimal("0.2350"), (BOUNDARY_NOTE_WMT,)),
                         (underlying_stale.ex_date, underlying_stale.amount, underlying_stale.notes),
                         "the dividend facts are still reported")
        self.assertEqual(("F", "A"), (option_missing.option_grade, underlying_stale.option_grade))

    def test_an_e_grade_option_quote_still_has_a_usable_bid(self) -> None:
        # The EXDIV rule is bid-side so that a wide or zero-bid quote (grade E, display-only mid) is still graded:
        # QuoteQC.mark_liq_long is the bid a rule may use (present and fresh), None only for an F or a stale bid side.
        cal = CAL_EX_MONDAY
        wide = QuoteQC("WMT261120C00105000", "E", quote_qc.STATUS_FRESH, "as of 14:31:12 ET", 18, 18, Decimal("28.57"),
                       Decimal("0.14"), Decimal("0.12"), Decimal("0.16"), False, ["wide: spread 28.57% > 25%"],
                       Decimal("0.12"), Decimal("0.16"), T1_STAMP)
        tier = self.tier(WMT_CALL_PLAIN, wide, qc("WMT", "104.90", "104.96"), cal=cal)
        self.assertEqual((expiry.TIER_HIGH, Decimal("0.12"), Decimal("0.12"), "E"),
                         (tier.tier, tier.bid, tier.extrinsic_bid, tier.option_grade))
        self.assertEqual((BOUNDARY_NOTE_WMT,
                          "option quote grade E (wide: spread 28.57% > 25%): the bid-side arithmetic stands"), tier.notes)
        no_bid = QuoteQC("WMT261120C00105000", "E", quote_qc.STATUS_FRESH, "as of 14:31:12 ET", 18, 18, Decimal("200.00"),
                         Decimal("0.025"), Decimal("0"), Decimal("0.05"), False, ["no bid"], Decimal("0"), Decimal("0.05"), T1_STAMP)
        tier = self.tier(WMT_CALL_PLAIN, no_bid, qc("WMT", "106.40", "106.46"), cal=cal)
        self.assertEqual((expiry.TIER_HIGH, Decimal("0"), Decimal("-1.43"), True), (tier.tier, tier.bid, tier.extrinsic_bid, tier.itm))
        stale_bid = QuoteQC("WMT261120C00105000", "D", quote_qc.STATUS_ONE_SIDE_STALE, "as of 14:31:12 ET", 60, 18, None, None,
                            None, Decimal("1.60"), True, ["bid stale: 60 min old > bound 25 min"], Decimal("1.52"), Decimal("1.60"),
                            T1_STAMP)
        tier = self.tier(WMT_CALL_PLAIN, stale_bid, qc("WMT", "106.40", "106.46"), cal=cal)
        self.assertEqual(expiry.TIER_UNAVAILABLE, tier.tier, "a usable grade with a stale bid side has no bid to use")
        self.assertIn("WMT261120C00105000 quote unavailable (D one_side_stale", tier.reason)

    def test_spot_must_be_stamped_within_sixty_seconds_of_the_option(self) -> None:
        cal = CAL_EX_MONDAY
        late = qc("WMT", "106.40", "106.46", as_of=T1_STAMP + datetime.timedelta(seconds=61))
        tier = self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.52", "1.60"), late, cal=cal)
        self.assertEqual(expiry.TIER_UNAVAILABLE, tier.tier)
        self.assertIn("spot unaligned", tier.reason)
        aligned = qc("WMT", "106.40", "106.46", as_of=T1_STAMP + datetime.timedelta(seconds=60))
        self.assertEqual(expiry.TIER_HIGH, self.tier(WMT_CALL_PLAIN, qc("WMT261120C00105000", "1.52", "1.60"), aligned, cal=cal).tier)


class PinTierTest(unittest.TestCase):
    """Every open option leg whose last trading day is today or the next trading day:
    ``|S_mid - K| / S_mid`` on the printed two-decimal percentage, 1 % PIN HIGH, 3 % PIN WATCH,
    beyond that ITM (in the money) or PIN LOW; the underlying's quote is all it needs."""

    def tier(self, line: str, underlying: Optional[QuoteQC], today: datetime.date, cal=CAL):
        row = rows_from([line], today=today)[0]
        extra = calendar_events.extra_closures(cal)
        ltd = expiry.leg_last_trading_day(row, cal)
        return expiry.pin_tier(row, underlying, today, market_clock.next_trading_day(today, extra), ltd)

    def test_t1_nvda_call_is_pin_high(self) -> None:
        tier = self.tier(NVDA_CALL, qc("NVDA", "149.20", "149.25"), T1)
        self.assertEqual((expiry.PIN_HIGH, Decimal("0.52"), Decimal("149.225"), Decimal("150.000"), False, False),
                         (tier.tier, tier.distance_pct, tier.spot_mid, tier.strike, tier.itm, tier.on_last_trading_day))
        self.assertTrue(tier.fired)
        self.assertIsNone(tier.line)

    def test_expiry_day_wmt_is_pin_high_and_nvda_pin_watch_in_the_money(self) -> None:
        wmt = self.tier(WMT_CALL, qc("WMT", "104.90", "104.96"), EXPIRY)
        self.assertEqual((expiry.PIN_HIGH, Decimal("0.07"), False, True), (wmt.tier, wmt.distance_pct, wmt.itm, wmt.on_last_trading_day))
        self.assertIsNone(wmt.line)
        nvda = self.tier(NVDA_CALL, qc("NVDA", "153.30", "153.36"), EXPIRY)
        self.assertEqual((expiry.PIN_WATCH, Decimal("2.17"), True), (nvda.tier, nvda.distance_pct, nvda.itm))
        self.assertEqual(expiry.ITM_LINE_TODAY, nvda.line)
        self.assertFalse(nvda.fired)

    def test_bands_are_closed_on_the_printed_percentage(self) -> None:
        # 1.005 % rounds half-even to 1.00 (PIN HIGH); 1.0051 rounds to 1.01 (PIN WATCH).
        row_line = WMT_CALL_PLAIN
        high = self.tier(row_line, qc("WMT", "106.0606", "106.0606"), T1)  # (106.0606 - 105) / 106.0606 = 0.99999 %
        self.assertEqual(expiry.PIN_HIGH, high.tier)
        watch = self.tier(row_line, qc("WMT", "106.08", "106.08"), T1)  # 1.018 %
        self.assertEqual((expiry.PIN_WATCH, Decimal("1.02")), (watch.tier, watch.distance_pct))
        edge = self.tier(row_line, qc("WMT", "108.2474", "108.2474"), T1)  # 3.000 %
        self.assertEqual((expiry.PIN_WATCH, Decimal("3.00")), (edge.tier, edge.distance_pct))

    def test_beyond_three_percent_is_itm_or_pin_low(self) -> None:
        itm = self.tier(NVDA_CALL, qc("NVDA", "160.00", "160.10"), EXPIRY)
        self.assertEqual((expiry.PIN_ITM, True, True), (itm.tier, itm.itm, itm.fired))
        self.assertEqual(expiry.ITM_LINE_TODAY, itm.line)
        itm_tomorrow = self.tier(NVDA_CALL, qc("NVDA", "160.00", "160.10"), T1)
        self.assertEqual((expiry.PIN_ITM, False), (itm_tomorrow.tier, itm_tomorrow.fired))
        self.assertEqual("in the money: automatic exercise or assignment expected at the close of Fri 2026-11-20",
                         itm_tomorrow.line)
        low = self.tier(NVDA_CALL, qc("NVDA", "140.00", "140.10"), EXPIRY)
        self.assertEqual((expiry.PIN_LOW, False, False, None), (low.tier, low.itm, low.fired, low.line))

    def test_puts_use_the_mirror_rule(self) -> None:
        put = "option,SPY,SPY261120P00650000,1,,,,Roth IRA,AG-20261028-03,2026-10-28,1130.00,,11.30,open,,,,,"
        itm = self.tier(put, qc("SPY", "640.00", "640.04"), EXPIRY)
        self.assertEqual((expiry.PIN_WATCH, Decimal("1.56"), True), (itm.tier, itm.distance_pct, itm.itm))
        near = self.tier(put, qc("SPY", "652.10", "652.14"), EXPIRY)
        self.assertEqual((expiry.PIN_HIGH, Decimal("0.33"), False), (near.tier, near.distance_pct, near.itm))
        otm = self.tier(put, qc("SPY", "680.00", "680.04"), EXPIRY)
        self.assertEqual((expiry.PIN_LOW, Decimal("4.41"), False), (otm.tier, otm.distance_pct, otm.itm))
        penny = self.tier(put, qc("SPY", "649.99", "649.99"), EXPIRY)
        self.assertTrue(penny.itm, "the $0.01 rule: S <= K - 0.01 is in the money for a put")
        at_strike = self.tier(put, qc("SPY", "650.00", "650.00"), EXPIRY)
        self.assertEqual((False, Decimal("0.00"), expiry.PIN_HIGH), (at_strike.itm, at_strike.distance_pct, at_strike.tier))

    def test_outside_the_window_there_is_no_pin_tier(self) -> None:
        self.assertIsNone(self.tier(WMT_CALL, qc("WMT", "104.20", "104.26"), datetime.date(2026, 11, 3)))
        self.assertIsNone(self.tier(WMT_CALL, qc("WMT", "104.20", "104.26"), datetime.date(2026, 11, 18)),
                          "two trading days out is not the pin window")
        self.assertIsNone(self.tier(SPY_PUT, qc("SPY", "652.10", "652.14"), T1))

    def test_underlying_quote_unavailable_is_unavailable(self) -> None:
        tier = self.tier(NVDA_CALL, unusable("NVDA", "cache: fetch_failed:network"), T1)
        self.assertEqual((expiry.PIN_UNAVAILABLE, True, False), (tier.tier, tier.unavailable, tier.fired))
        self.assertIn("NVDA quote unavailable (F missing: cache: fetch_failed:network)", tier.reason)
        self.assertEqual(expiry.PIN_UNAVAILABLE, self.tier(NVDA_CALL, None, T1).tier)
        self.assertEqual(expiry.PIN_UNAVAILABLE, self.tier(NVDA_CALL, stale("NVDA", "149.20", "149.25"), T1).tier)


class ImplicationTest(unittest.TestCase):
    def group_and_row(self, lines: List[str], symbol: str):
        rows = rows_from(lines)
        groups = grouping.group_positions(rows)
        row = [row for row in rows if row.compact_symbol == symbol][0]
        group = [group for group in groups if group.group_id == row.group_id][0]
        return row, group

    def test_covered_short_call_shares_called_away(self) -> None:
        row, group = self.group_and_row([WMT_SHARES, WMT_CALL], "WMT261120C00105000")
        text = expiry.implication(row, group)
        self.assertEqual("shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held)", text)

    def test_uncovered_short_call(self) -> None:
        row, group = self.group_and_row([WMT_CALL_PLAIN], "WMT261120C00105000")
        self.assertEqual("shares called away if assigned: 200 WMT at 105 (no shares in the group: short stock would result)",
                         expiry.implication(row, group))

    def test_partially_covered_short_call(self) -> None:
        shares = WMT_SHARES.replace(",200,", ",100,")
        row, group = self.group_and_row([shares, WMT_CALL], "WMT261120C00105000")
        self.assertEqual("shares called away if assigned: 200 WMT at 105 (100 of the 200 shares are held)",
                         expiry.implication(row, group))

    def test_short_put_cash_needed(self) -> None:
        put = "option,SPY,SPY261120P00600000,-1,,,,Roth IRA,AG-20261001-04,2026-10-01,-480.00,,,open,,,,,"
        row, group = self.group_and_row([put], "SPY261120P00600000")
        self.assertEqual("cash needed if assigned: 60000.00 for 100 SPY shares at 600 (600 x 100 x 1)", expiry.implication(row, group))
        two = put.replace(",-1,", ",-2,").replace("-480.00", "-960.00")
        row, group = self.group_and_row([two], "SPY261120P00600000")
        self.assertEqual("cash needed if assigned: 120000.00 for 200 SPY shares at 600 (600 x 100 x 2)", expiry.implication(row, group))

    def test_long_put_and_long_call(self) -> None:
        row, group = self.group_and_row([SPY_SHARES, SPY_PUT], "SPY261218P00620000")
        self.assertEqual("exercise would deliver 100 SPY shares at 620 (the 100 shares held)", expiry.implication(row, group))
        lone = "option,SPY,SPY261218P00620000,1,,,,Roth IRA,AG-20261028-03,2026-10-28,1130.00,,11.30,open,,,,,"
        row, group = self.group_and_row([lone], "SPY261218P00620000")
        self.assertEqual("exercise would deliver 100 SPY shares at 620 (no shares in the group: short stock would result)",
                         expiry.implication(row, group))
        call = "option,NVDA,NVDA261120C00150000,1,,,,,AG-20261022-02,2026-10-22,610.00,,,open,,,,,"
        row, group = self.group_and_row([call], "NVDA261120C00150000")
        self.assertEqual("exercise would take 100 NVDA shares at 150 for 15000.00 cash (150 x 100 x 1)", expiry.implication(row, group))

    def test_fractional_strike_renders_plainly(self) -> None:
        call = "option,XYZ,XYZ261120C00006500,-1,,,,,AG-20261022-02,2026-10-22,-50.00,,,open,,,,,"
        row, group = self.group_and_row([call], "XYZ261120C00006500")
        self.assertIn("100 XYZ at 6.5 ", expiry.implication(row, group))


class VerticalCheckTest(unittest.TestCase):
    LONG = "option,WMT,WMT261120C00100000,1,,,,,AG-20261105-07,2026-11-05,300.00,,3.00,open,,,,,"
    SHORT = "option,WMT,WMT261120C00105000,-1,,,,,AG-20261105-07,2026-11-05,-120.00,,1.20,open,,,,,"

    def check(self, underlying: Optional[QuoteQC]):
        rows = rows_from([self.LONG, self.SHORT])
        group = grouping.group_positions(rows)[0]
        self.assertEqual(grouping.STRUCTURE_VERTICAL_DEBIT_CALL, group.structure)
        return expiry.vertical_check(group, underlying)

    def test_between_the_strikes(self) -> None:
        check = self.check(qc("WMT", "102.40", "102.46"))
        self.assertEqual((Decimal("100.000"), Decimal("105.000"), Decimal("102.43"), True),
                         (check.low_strike, check.high_strike, check.spot_mid, check.between))
        self.assertEqual("the underlying 102.43 is between the strikes 100 and 105: never into expiration with the stock "
                         "between the strikes", check.message)
        self.assertTrue(check.fired)

    def test_outside_the_strikes(self) -> None:
        above = self.check(qc("WMT", "106.40", "106.46"))
        self.assertEqual((False, False), (above.between, above.fired))
        self.assertEqual("the underlying 106.43 is above both strikes 100 and 105 (both legs in the money)", above.message)
        below = self.check(qc("WMT", "98.00", "98.04"))
        self.assertEqual("the underlying 98.02 is below both strikes 100 and 105 (both legs out of the money)", below.message)
        at_strike = self.check(qc("WMT", "105.00", "105.00"))
        self.assertEqual(False, at_strike.between)

    def test_unavailable_underlying(self) -> None:
        check = self.check(unusable("WMT"))
        self.assertEqual((None, None, True), (check.between, check.spot_mid, check.unavailable))
        self.assertIn("between-the-strikes check cannot run", check.message)

    def test_non_vertical_groups_have_no_check(self) -> None:
        rows = rows_from([WMT_SHARES, WMT_CALL])
        self.assertIsNone(expiry.vertical_check(grouping.group_positions(rows)[0], qc("WMT", "102.40", "102.46")))


class WeekBoundsTest(unittest.TestCase):
    def test_monday_to_sunday_of_the_et_date(self) -> None:
        self.assertEqual((datetime.date(2026, 11, 16), datetime.date(2026, 11, 22)), expiry.week_bounds(T1))
        self.assertEqual((datetime.date(2026, 11, 16), datetime.date(2026, 11, 22)), expiry.week_bounds(datetime.date(2026, 11, 16)))
        self.assertEqual((datetime.date(2026, 11, 16), datetime.date(2026, 11, 22)), expiry.week_bounds(datetime.date(2026, 11, 22)))
        self.assertEqual((datetime.date(2026, 11, 2), datetime.date(2026, 11, 8)), expiry.week_bounds(datetime.date(2026, 11, 3)))


class SheetTest(unittest.TestCase):
    """``build_sheet`` on the fixture book with hand-built quotes at the fixture instants."""

    def test_t1_sheet_the_wmt_call_expires_before_its_ex_dividend(self) -> None:
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, t1_qcs(), FIXTURE_CAL)
        self.assertEqual((T1, EXPIRY, datetime.date(2026, 11, 16), datetime.date(2026, 11, 22)),
                         (sheet.today, sheet.next_trading_day, sheet.week_start, sheet.week_end))
        self.assertEqual(["WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000"], [item.symbol for item in sheet.legs])
        wmt = leg(sheet, "WMT261120C00105000")
        self.assertIsNone(wmt.dividend, "the WMT ex-dividend 2026-11-24 (cum day Mon 2026-11-23) falls after the call's last "
                                        "trading day Fri 2026-11-20: no dividend tier, no HIGH for a risk that cannot occur")
        self.assertFalse(wmt.fired)
        self.assertEqual([], wmt.unavailable_reasons)
        self.assertEqual((expiry.PIN_WATCH, Decimal("1.34"), True), (wmt.pin.tier, wmt.pin.distance_pct, wmt.pin.itm))
        self.assertEqual(["  " + expiry.ITM_LINE_LATER % "Fri 2026-11-20"], expiry.extra_lines(wmt))
        self.assertEqual(("AG-20261020-01", "covered_call", "pm", EXPIRY, 1, Decimal(2), "short"),
                         (wmt.trade_id, wmt.structure, wmt.settlement, wmt.last_trading_day, wmt.dte_trading, wmt.contracts, wmt.side))
        self.assertEqual("shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held)", wmt.implication)
        self.assertEqual(datetime.datetime(2026, 11, 20, 21, 0, tzinfo=UTC), wmt.act_by.utc)
        self.assertTrue(wmt.assign_ok)
        nvda = leg(sheet, "NVDA261120C00150000")
        self.assertIsNone(nvda.dividend, "the NVDA ex-dividend 2026-12-03 is nine trading days out")
        self.assertEqual((expiry.PIN_HIGH, Decimal("0.52"), True), (nvda.pin.tier, nvda.pin.distance_pct, nvda.fired))
        self.assertEqual("shares called away if assigned: 200 NVDA at 150 (covered by the 200 shares held)", nvda.implication)
        spy = leg(sheet, "SPY261218P00620000")
        self.assertIsNone(spy.pin)
        self.assertIsNone(spy.dividend)
        self.assertFalse(spy.due_today or spy.due_tomorrow or spy.in_week)
        self.assertEqual([True, True, False], [item.due_tomorrow for item in sheet.legs])
        self.assertEqual([False, False, False], [item.due_today for item in sheet.legs])
        self.assertEqual([True, True, False], [item.in_week for item in sheet.legs])
        self.assertEqual([], sheet.expired)
        self.assertEqual({}, sheet.quarantined)
        self.assertEqual(contract.EXIT_FIRED, expiry.assignment_exit_code(sheet))
        self.assertEqual(contract.EXIT_FIRED, expiry.checklist_exit_code(sheet, expiry.MODE_WEEK))
        self.assertEqual(contract.EXIT_FIRED, expiry.checklist_exit_code(sheet, expiry.MODE_TOMORROW))
        self.assertEqual(contract.EXIT_QUIET, expiry.checklist_exit_code(sheet, expiry.MODE_FINAL_CALL))
        self.assertEqual(["WMT261120C00105000", "NVDA261120C00150000"],
                         [item.symbol for item in expiry.checklist_legs(sheet, expiry.MODE_WEEK)])
        self.assertEqual([], expiry.checklist_legs(sheet, expiry.MODE_FINAL_CALL))

    def test_expiry_day_sheet(self) -> None:
        sheet = sheet_for(FIXTURE_LINES, EXPIRY_NOW, expiry_day_qcs(), FIXTURE_CAL)
        wmt = leg(sheet, "WMT261120C00105000")
        self.assertEqual((expiry.PIN_HIGH, Decimal("0.07"), True, True), (wmt.pin.tier, wmt.pin.distance_pct, wmt.due_today, wmt.fired))
        self.assertIsNone(wmt.dividend, "the cum day Mon 2026-11-23 is after the last trading day Fri 2026-11-20 (today)")
        self.assertEqual([], expiry.extra_lines(wmt))
        nvda = leg(sheet, "NVDA261120C00150000")
        self.assertEqual((expiry.PIN_WATCH, Decimal("2.17"), expiry.ITM_LINE_TODAY), (nvda.pin.tier, nvda.pin.distance_pct, nvda.pin.line))
        self.assertEqual(0, nvda.dte_trading)
        self.assertEqual("act by Fri 2026-11-20 16:00 ET (21:00 UTC) [today]; the session close: a short option is closed only while the market trades", nvda.act_by_text)
        self.assertEqual(contract.EXIT_FIRED, expiry.assignment_exit_code(sheet))
        self.assertEqual(contract.EXIT_FIRED, expiry.checklist_exit_code(sheet, expiry.MODE_FINAL_CALL))
        self.assertEqual(contract.EXIT_QUIET, expiry.checklist_exit_code(sheet, expiry.MODE_TOMORROW))
        self.assertEqual(2, len(expiry.checklist_legs(sheet, expiry.MODE_FINAL_CALL)))

    def test_early_close_final_call_acts_by_the_close(self) -> None:
        now = datetime.datetime(2026, 11, 27, 17, 20, tzinfo=UTC)  # 12:20 ET on the early-close Friday
        stamp = datetime.datetime(2026, 11, 27, 17, 1, 12, tzinfo=UTC)
        # A long put: no dividend tier, so the sheet's exit code rests on the pin tier alone.
        lines = [SPY_SHARES, "option,SPY,SPY261127P00650000,1,,,,Roth IRA,AG-20261028-03,2026-10-28,500.00,,5.00,open,,,,,"
                             "protective put into the early close"]
        qcs = {"SPY": qc("SPY", "650.00", "650.04", stamp), "SPY261127P00650000": qc("SPY261127P00650000", "4.10", "4.30", stamp)}
        sheet = sheet_for(lines, now, qcs, FIXTURE_CAL)
        self.assertEqual("early_close", market_clock.session_at(now).session)
        wmt = leg(sheet, "SPY261127P00650000")
        self.assertEqual((expiry.PIN_HIGH, None), (wmt.pin.tier, wmt.dividend))
        self.assertEqual((datetime.date(2026, 11, 27), True, 0), (wmt.last_trading_day, wmt.due_today, wmt.dte_trading))
        self.assertEqual((datetime.datetime(2026, 11, 27, 18, 0, tzinfo=UTC), "2026-11-27 13:00 ET", True, False),
                         (wmt.act_by.utc, wmt.act_by.et_text, wmt.act_by.early_close, wmt.act_by.passed))
        self.assertEqual("act by Fri 2026-11-27 13:00 ET (18:00 UTC) [today]; early close 13:00 ET: confirm your broker's "
                         "cutoff", wmt.act_by_text)
        self.assertTrue(expiry.pin_line(wmt).endswith(wmt.act_by_text))
        self.assertTrue(expiry.docket_line(wmt).endswith(wmt.act_by_text))
        document = sheet.as_dict()["legs"][0]
        self.assertEqual(("2026-11-27 13:00 ET", "18:00 UTC", True), (document["act_by_et"], document["act_by_utc_text"],
                                                                     document["act_by_early_close"]))
        self.assertEqual(contract.EXIT_FIRED, expiry.checklist_exit_code(sheet, expiry.MODE_FINAL_CALL))

    def test_regular_day_has_nothing_in_any_window(self) -> None:
        qcs = {"WMT": qc("WMT", "104.20", "104.26", REGULAR_STAMP), "NVDA": qc("NVDA", "148.90", "148.95", REGULAR_STAMP),
               "SPY": qc("SPY", "648.10", "648.14", REGULAR_STAMP),
               "WMT261120C00105000": qc("WMT261120C00105000", "3.35", "3.50", REGULAR_STAMP),
               "NVDA261120C00150000": qc("NVDA261120C00150000", "3.10", "3.30", REGULAR_STAMP),
               "SPY261218P00620000": qc("SPY261218P00620000", "9.80", "10.10", REGULAR_STAMP)}
        sheet = sheet_for(FIXTURE_LINES, REGULAR_NOW, qcs, FIXTURE_CAL)
        self.assertEqual(3, len(sheet.legs))
        self.assertTrue(all(item.pin is None and item.dividend is None for item in sheet.legs))
        self.assertEqual(contract.EXIT_QUIET, expiry.assignment_exit_code(sheet))
        for mode in expiry.MODES:
            self.assertEqual(contract.EXIT_QUIET, expiry.checklist_exit_code(sheet, mode))
            self.assertEqual([], expiry.checklist_legs(sheet, mode))

    def test_monday_week_docket_lists_the_friday_contracts_without_tiers(self) -> None:
        now = datetime.datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
        sheet = sheet_for(FIXTURE_LINES, now, {}, FIXTURE_CAL)
        listed = expiry.checklist_legs(sheet, expiry.MODE_WEEK)
        self.assertEqual(["WMT261120C00105000", "NVDA261120C00150000"], [item.symbol for item in listed])
        self.assertEqual([4, 4], [item.dte_trading for item in listed])
        self.assertTrue(all(item.pin is None for item in listed), "Monday is not the pin window of a Friday expiry")
        self.assertIsNone(listed[0].dividend, "the WMT ex-dividend is six trading days out on 2026-11-16, and its cum day "
                                              "Mon 2026-11-23 is after the call's last trading day anyway")
        self.assertEqual(contract.EXIT_FIRED, expiry.checklist_exit_code(sheet, expiry.MODE_WEEK))
        self.assertEqual(contract.EXIT_QUIET, expiry.assignment_exit_code(sheet))
        self.assertEqual("act by Fri 2026-11-20 16:00 ET (21:00 UTC); the session close: a short option is closed only while the market trades", listed[0].act_by_text)

    def test_quotes_unavailable_for_a_due_item(self) -> None:
        qcs = {symbol: stale(symbol, "1", "2") for symbol in t1_qcs()}
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, qcs, FIXTURE_CAL)
        wmt = leg(sheet, "WMT261120C00105000")
        self.assertEqual((None, expiry.PIN_UNAVAILABLE), (wmt.dividend, wmt.pin.tier))
        self.assertTrue(wmt.unavailable_reasons)
        self.assertEqual(contract.EXIT_UNAVAILABLE, expiry.assignment_exit_code(sheet))
        self.assertEqual(contract.EXIT_UNAVAILABLE, expiry.checklist_exit_code(sheet, expiry.MODE_TOMORROW))
        self.assertEqual(contract.EXIT_UNAVAILABLE, expiry.checklist_exit_code(sheet, expiry.MODE_WEEK))
        self.assertEqual(contract.EXIT_QUIET, expiry.checklist_exit_code(sheet, expiry.MODE_FINAL_CALL), "nothing due today")

    def test_a_fired_item_outranks_unavailable_in_the_assignment_watch(self) -> None:
        # The ex-dividend on Mon 2026-11-23 grades the WMT call (the fixture's Nov-24 date would list nothing).
        qcs = t1_qcs()
        qcs["WMT261120C00105000"] = unusable("WMT261120C00105000")
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, qcs, CAL_EX_MONDAY)
        self.assertEqual(expiry.TIER_UNAVAILABLE, leg(sheet, "WMT261120C00105000").dividend.tier)
        self.assertEqual(contract.EXIT_FIRED, expiry.assignment_exit_code(sheet))
        self.assertEqual(contract.EXIT_UNAVAILABLE, expiry.checklist_exit_code(sheet, expiry.MODE_TOMORROW),
                         "the checklist sheet is incomplete: UNAVAILABLE outranks listed")

    def test_no_calendar_makes_every_short_call_unavailable(self) -> None:
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, t1_qcs(), None)
        self.assertEqual(expiry.TIER_UNAVAILABLE, leg(sheet, "WMT261120C00105000").dividend.tier)
        self.assertEqual(expiry.TIER_UNAVAILABLE, leg(sheet, "NVDA261120C00150000").dividend.tier)
        self.assertEqual(contract.EXIT_FIRED, expiry.assignment_exit_code(sheet), "the NVDA PIN HIGH still fires")
        without_nvda = sheet_for([WMT_SHARES, WMT_CALL, SPY_SHARES, SPY_PUT], T1_NOW, t1_qcs(), None)
        self.assertEqual(contract.EXIT_UNAVAILABLE, expiry.assignment_exit_code(without_nvda))

    def test_expired_open_legs_are_left_to_the_reconciliation(self) -> None:
        now = datetime.datetime(2026, 11, 21, 14, 30, tzinfo=UTC)
        sheet = sheet_for(FIXTURE_LINES, now, {}, FIXTURE_CAL)
        self.assertEqual(["SPY261218P00620000"], [item.symbol for item in sheet.legs])
        self.assertEqual([3, 5], [row.row_number for row in sheet.expired], "lines of the synthetic file (no comment line)")
        self.assertTrue(any("csv_recon.py" in warning for warning in sheet.warnings))
        self.assertEqual(contract.EXIT_QUIET, expiry.assignment_exit_code(sheet))

    def test_quarantined_groups_are_unavailable_items(self) -> None:
        bad = WMT_CALL.replace("-684.00", "684.00")  # sign mismatch quarantines AG-20261020-01
        session = market_clock.session_at(T1_NOW)
        positions = positions_from([WMT_SHARES, bad, SPY_SHARES, SPY_PUT], today=T1)
        self.assertIn("AG-20261020-01", positions.quarantined)
        groups = grouping.group_positions(positions.rows)
        sheet = expiry.build_sheet(positions, groups, now=T1_NOW, session=session, cal=FIXTURE_CAL, broker_cutoff_et=CUTOFF,
                                   qcs=t1_qcs())
        self.assertEqual(["AG-20261020-01"], sorted(sheet.quarantined))
        self.assertEqual(contract.EXIT_UNAVAILABLE, expiry.assignment_exit_code(sheet))
        self.assertEqual(contract.EXIT_UNAVAILABLE, expiry.checklist_exit_code(sheet, expiry.MODE_FINAL_CALL))
        mismatch = positions_from([WMT_SHARES, WMT_CALL.replace("Brokerage (margin)", "Roth IRA")], today=T1)
        groups = grouping.group_positions(mismatch.rows)
        self.assertTrue(groups[0].is_quarantined)
        sheet = expiry.build_sheet(mismatch, groups, now=T1_NOW, session=session, cal=FIXTURE_CAL, broker_cutoff_et=CUTOFF,
                                   qcs=t1_qcs())
        self.assertIn("account mismatch: Brokerage (margin), Roth IRA", sheet.quarantined["AG-20261020-01"])

    def test_vertical_between_the_strikes_is_a_fired_due_item(self) -> None:
        lines = [VerticalCheckTest.LONG, VerticalCheckTest.SHORT]
        qcs = {"WMT": qc("WMT", "102.40", "102.46"), "WMT261120C00100000": qc("WMT261120C00100000", "2.50", "2.60"),
               "WMT261120C00105000": qc("WMT261120C00105000", "0.20", "0.25")}
        sheet = sheet_for(lines, EXPIRY_NOW, qcs, FIXTURE_CAL)
        self.assertEqual(2, len(sheet.legs))
        for item in sheet.legs:
            self.assertTrue(item.vertical.between)
            self.assertTrue(item.fired)
        self.assertEqual(contract.EXIT_FIRED, expiry.assignment_exit_code(sheet))
        self.assertEqual(contract.EXIT_FIRED, expiry.checklist_exit_code(sheet, expiry.MODE_FINAL_CALL))

    def test_leg_dict_is_json_ready(self) -> None:
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, t1_qcs(), FIXTURE_CAL)
        document = sheet.as_dict()
        text = contract.json_dumps(document)
        self.assertIn('"distance_pct": "0.52"', text)
        self.assertIn('"act_by_utc": "2026-11-20T21:00:00+00:00"', text)
        self.assertEqual(["expired", "legs", "next_trading_day", "quarantined", "today", "warnings", "week_end", "week_start"],
                         sorted(document))
        wmt = document["legs"][0]
        self.assertEqual("WMT261120C00105000", wmt["symbol"])
        self.assertIsNone(wmt["dividend"], "no dividend tier: the call expires before its ex-dividend's cum day")
        self.assertIsNone(document["legs"][2]["pin"])
        graded = sheet_for(FIXTURE_LINES, T1_NOW, t1_qcs(), CAL_EX_MONDAY).as_dict()
        self.assertIn('"extrinsic_bid": "0.09"', contract.json_dumps(graded))
        wmt = graded["legs"][0]
        self.assertEqual((expiry.TIER_NOTED, expiry.TIER_HIGH, [BOUNDARY_NOTE_WMT]),
                         (wmt["dividend"]["tier"], wmt["dividend"]["downgraded_from"], wmt["dividend"]["notes"]))


class LineFormatTest(unittest.TestCase):
    def test_dividend_pin_and_docket_lines(self) -> None:
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, t1_qcs(), FIXTURE_CAL)
        wmt = leg(sheet, "WMT261120C00105000")
        self.assertIsNone(wmt.dividend, "the fixture's Nov-24 ex-dividend is beyond the Nov-20 call's life: no dividend line")
        with self.assertRaises(ValueError):
            expiry.dividend_line(wmt)
        nvda = leg(sheet, "NVDA261120C00150000")
        self.assertEqual("PIN HIGH AG-20261022-02 NVDA261120C00150000 short 2: 0.52 % from the strike (mid 149.225 vs 150), out of "
                         "the money; last trading day Fri 2026-11-20; act by Fri 2026-11-20 16:00 ET (21:00 UTC) [next trading day]; the session close: a short option is closed only while the market trades",
                         expiry.pin_line(nvda))
        self.assertEqual("AG-20261020-01 WMT261120C00105000 short 2 | covered_call | pm-settled | last trading day Fri 2026-11-20 "
                         "(1 trading day) | shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held) | "
                         "act by Fri 2026-11-20 16:00 ET (21:00 UTC) [next trading day]; the session close: a short option is closed only while the market trades", expiry.docket_line(wmt))
        self.assertEqual(["  in the money: automatic exercise or assignment expected at the close of Fri 2026-11-20"],
                         expiry.extra_lines(wmt), "no dividend note: the pin tier alone carries the assignment-at-expiry line")
        # The ex-dividend on the first trading day after the last trading day (the tool tests' rewritten calendar).
        graded = leg(sheet_for(FIXTURE_LINES, T1_NOW, t1_qcs(), CAL_EX_MONDAY), "WMT261120C00105000")
        self.assertEqual("NOTED AG-20261020-01 WMT261120C00105000 short 2: ex-dividend 2026-11-23 in 2 trading days (confirmed), "
                         "amount 0.2350; extrinsic (bid) 0.09 = bid 1.52 - max(0, 106.43 - 105); in the money; HIGH downgraded to "
                         "NOTED: #assign-ok (owner accepted assignment); act by Fri 2026-11-20 16:00 ET (21:00 UTC) [next trading day]; the session close: a short option is closed only while the market trades",
                         expiry.dividend_line(graded))
        self.assertEqual(["  in the money: automatic exercise or assignment expected at the close of Fri 2026-11-20",
                          "  note: " + BOUNDARY_NOTE_WMT], expiry.extra_lines(graded))

    def test_expiry_day_lines(self) -> None:
        sheet = sheet_for(FIXTURE_LINES, EXPIRY_NOW, expiry_day_qcs(), FIXTURE_CAL)
        nvda = leg(sheet, "NVDA261120C00150000")
        self.assertEqual("PIN WATCH AG-20261022-02 NVDA261120C00150000 short 2: 2.17 % from the strike (mid 153.33 vs 150), in the "
                         "money; last trading day Fri 2026-11-20; act by Fri 2026-11-20 16:00 ET (21:00 UTC) [today]; the session close: a short option is closed only while the market trades",
                         expiry.pin_line(nvda))
        self.assertEqual(["  " + expiry.ITM_LINE_TODAY], expiry.extra_lines(nvda))
        wmt = leg(sheet, "WMT261120C00105000")
        self.assertIn("PIN HIGH AG-20261020-01 WMT261120C00105000 short 2: 0.07 % from the strike (mid 104.93 vs 105), out of the money",
                      expiry.pin_line(wmt))
        self.assertEqual("AG-20261022-02 NVDA261120C00150000 short 2 | covered_call | pm-settled | last trading day Fri 2026-11-20 "
                         "(today) | shares called away if assigned: 200 NVDA at 150 (covered by the 200 shares held) | "
                         "act by Fri 2026-11-20 16:00 ET (21:00 UTC) [today]; the session close: a short option is closed only while the market trades", expiry.docket_line(nvda))
        self.assertIsNone(wmt.dividend)
        graded = leg(sheet_for(FIXTURE_LINES, EXPIRY_NOW, expiry_day_qcs(), CAL_EX_MONDAY), "WMT261120C00105000")
        self.assertEqual("NOTED AG-20261020-01 WMT261120C00105000 short 2: ex-dividend 2026-11-23 in 1 trading day (confirmed), "
                         "amount 0.2350; extrinsic (bid) 0.12 = bid 0.12 - max(0, 104.93 - 105); out of the money; HIGH downgraded "
                         "to NOTED: #assign-ok (owner accepted assignment); act by Fri 2026-11-20 16:00 ET (21:00 UTC) [today]; the session close: a short option is closed only while the market trades",
                         expiry.dividend_line(graded))
        self.assertEqual(["  note: " + BOUNDARY_NOTE_WMT], expiry.extra_lines(graded), "out of the money: no in-the-money line")

    def test_unavailable_lines(self) -> None:
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, t1_qcs(), None)
        wmt = leg(sheet, "WMT261120C00105000")
        self.assertEqual("UNAVAILABLE AG-20261020-01 WMT261120C00105000 short 2: dividend unknown: no research/calendar.toml "
                         "(unknown dividend is never zero); act by Fri 2026-11-20 16:00 ET (21:00 UTC) [next trading day]; the session close: a short option is closed only while the market trades",
                         expiry.dividend_line(wmt))
        qcs = t1_qcs()
        qcs["NVDA"] = unusable("NVDA")
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, qcs, FIXTURE_CAL)
        self.assertEqual("UNAVAILABLE AG-20261022-02 NVDA261120C00150000 short 2: pin tier not graded: NVDA quote unavailable "
                         "(F missing: cache: unmatched); last trading day Fri 2026-11-20; act by Fri 2026-11-20 16:00 ET (21:00 UTC) "
                         "[next trading day]; the session close: a short option is closed only while the market trades",
                         expiry.pin_line(leg(sheet, "NVDA261120C00150000")))


class ModuleHygieneTest(unittest.TestCase):
    def source(self) -> str:
        with open(MODULE, encoding="utf-8") as handle:
            return handle.read()

    def test_decimal_only_no_print_and_python_39_syntax(self) -> None:
        source = self.source()
        self.assertNotIn("float(", source)
        self.assertNotIn("print(", source)
        tree = ast.parse(source, filename=MODULE, feature_version=(3, 9))
        first = tree.body[1]
        self.assertIsInstance(first, ast.ImportFrom)
        self.assertEqual("__future__", first.module)

    def test_no_order_shaped_words(self) -> None:
        import re
        pattern = re.compile(r"\b(?:buy|sell|sold|bought|place|submit|limit order|market order|good till|time in force|GTC)\b",
                             re.IGNORECASE)
        self.assertEqual([], [match.group(0) for match in pattern.finditer(self.source())])

    def test_leg_output_schema_matches_the_leg_dict(self) -> None:
        # Recon finding 6: both tools describe the leg object through one dictionary, so --schema never
        # drifts from --json (the dividend block carries option_grade, the act-by its early-close flag).
        # The Monday ex-dividend grades the WMT call, so the leg carries a dividend block to compare.
        sheet = sheet_for(FIXTURE_LINES, T1_NOW, t1_qcs(), CAL_EX_MONDAY)
        wmt = leg(sheet, "WMT261120C00105000").as_dict()
        self.assertEqual(set(wmt), set(expiry.LEG_OUTPUT))
        self.assertEqual(set(wmt["dividend"]), set(expiry.LEG_OUTPUT["dividend"]))
        self.assertEqual(set(wmt["pin"]), set(expiry.LEG_OUTPUT["pin"]))
        vertical = sheet_for([VerticalCheckTest.LONG, VerticalCheckTest.SHORT], EXPIRY_NOW,
                             {"WMT": qc("WMT", "102.40", "102.46")}, FIXTURE_CAL).legs[0].as_dict()
        self.assertEqual(set(vertical["vertical"]), set(expiry.LEG_OUTPUT["vertical"]))
        self.assertIn("option_grade", expiry.LEG_OUTPUT["dividend"])
        self.assertIn("act_by_early_close", expiry.LEG_OUTPUT)

    def test_vocabulary_constants(self) -> None:
        self.assertEqual(("HIGH", "WATCH", "LOW", "NOTED", "UNAVAILABLE"), expiry.DIVIDEND_TIERS)
        self.assertEqual(("PIN HIGH", "PIN WATCH", "PIN LOW", "ITM", "UNAVAILABLE"), expiry.PIN_TIERS)
        self.assertEqual(("week", "tomorrow", "final-call"), expiry.MODES)
        self.assertEqual(5, expiry.DIVIDEND_WINDOW_TRADING_DAYS)
        self.assertEqual("ex-dividend %s is the first trading day after the last trading day %s: a dividend-capture exercise "
                         "would come at expiration", expiry.BOUNDARY_NOTE)
        self.assertEqual((Decimal("1"), Decimal("3")), (expiry.PIN_HIGH_PCT, expiry.PIN_WATCH_PCT))


if __name__ == "__main__":
    unittest.main()
