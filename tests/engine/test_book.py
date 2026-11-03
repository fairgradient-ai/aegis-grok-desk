"""The book of record (``aegis_desk.book``): ``build_book`` / ``book_to_json`` with exactly the plan's keys, the
money and analytics definitions (tau, spot alignment, IV, Greeks, extrinsic, ITM), the request-batch rule, the
snapshot of record and the previous-snapshot day P/L, and the one library entry point ``load_book`` driven
against a copy of the fixture desk root (the addendum's goldens, computed independently)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from aegis_desk import book, calendar_events, desk_config, desk_paths, iv, market_clock, quote_cache, quote_qc, state, units
from aegis_desk.grouping import PositionGroup, group_positions
from aegis_desk.market_types import Quote
from aegis_desk.positions_csv import PositionsFile, parse_positions_text
from aegis_desk.quote_qc import QuoteQC

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
UTC = datetime.timezone.utc
D = Decimal
TODAY = datetime.date(2026, 11, 3)
NOW = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC)
STAMP = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
PREV_NOW = datetime.datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
PREV_STAMP = datetime.datetime(2026, 11, 2, 20, 59, 55, tzinfo=UTC)
HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
          "close_price_per_unit,underlying_stop,note")
FIXTURE_LINES = [
    "equity,WMT,,200,,,,Brokerage (margin),AG-20261020-01,2026-10-20,,98.50,,open,,,,92.00,core holding #horizon:2027-03",
    "option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,covered call #assign-ok",
    "equity,NVDA,,200,,,,Brokerage (margin),AG-20261022-02,2026-10-22,28240.00,,,open,,,,,",
    "option,NVDA,,-2,2026-11-20,CALL,150,Brokerage (margin),AG-20261022-02,2026-10-22,-610.00,,,open,,,,,covered call from component columns #earnings-ok",
    "etf,SPY,,100,,,,Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,",
    "option,SPY,SPY261218P00620000,1,,,,Roth IRA,AG-20261028-03,2026-10-28,1130.00,,11.30,open,,,,,protective put",
    "option,SPY,SPY261016P00600000,-1,,,,Roth IRA,AG-20261001-04,2026-10-01,-480.00,,,closed,2026-10-14,1,0.55,,closed early",
]
WMT_CALL = "WMT261120C00105000"
NVDA_CALL = "NVDA261120C00150000"
SPY_PUT = "SPY261218P00620000"
SIX = ["NVDA", NVDA_CALL, "SPY", SPY_PUT, "WMT", WMT_CALL]
REGULAR_PRICES = {"WMT": ("104.20", "104.26"), "NVDA": ("148.90", "148.95"), "SPY": ("648.10", "648.14"),
                  WMT_CALL: ("3.35", "3.50"), NVDA_CALL: ("3.10", "3.30"), SPY_PUT: ("9.80", "10.10")}
PREV_PRICES = {"WMT": ("103.78", "103.84"), "NVDA": ("147.58", "147.63"), "SPY": ("645.88", "645.92"),
               WMT_CALL: ("3.10", "3.25"), NVDA_CALL: ("2.75", "2.95"), SPY_PUT: ("10.30", "10.60")}
RISK_FREE = 0.0412
CLOSURE_ENTRY = '\n[[closures]]\ndate = %s\nkind = "full"\nsource = "owner-entered (test)"\n'
"""An owner-injected full closure appended to a COPY's research/calendar.toml (the date is the one argument)."""

CFG = desk_config.load_desk_config(os.path.join(FIXTURE_DESK, "desk.toml"))
PATHS = desk_paths.for_root(FIXTURE_DESK)
CAL = calendar_events.load_calendar(os.path.join(FIXTURE_DESK, "research", "calendar.toml"))


def positions_of(lines: Sequence[str], today: datetime.date = TODAY) -> PositionsFile:
    return parse_positions_text("\n".join([HEADER] + list(lines)) + "\n", path="positions.csv", today=today)


def quotes_for(prices: Dict[str, Any], stamp: datetime.datetime = STAMP, **stamps: datetime.datetime) -> Dict[str, Quote]:
    out: Dict[str, Quote] = {}
    for symbol, (bid, ask) in prices.items():
        when = stamps.get(symbol, stamp)
        out[symbol] = Quote(symbol=symbol, bid=None if bid is None else D(bid), ask=None if ask is None else D(ask),
                            bid_date=when, ask_date=when)
    return out


def qcs_for(quotes: Dict[str, Quote], now: datetime.datetime) -> Dict[str, QuoteQC]:
    session = market_clock.session_at(now)
    prior = market_clock.previous_close_utc(now)
    return {symbol: quote_qc.assess(quote, now=now, session=session, bound_min=25, prior_close_utc=prior)
            for symbol, quote in quotes.items()}


def build(lines: Sequence[str] = FIXTURE_LINES, prices: Dict[str, Any] = REGULAR_PRICES, now: datetime.datetime = NOW,
          stamp: datetime.datetime = STAMP, calendar: Optional[calendar_events.CalendarFile] = CAL,
          prev_snapshot: Optional[dict] = None, stamps: Optional[Dict[str, datetime.datetime]] = None,
          quotes: Optional[Dict[str, Quote]] = None) -> Dict[str, Any]:
    positions = positions_of(lines, today=market_clock.to_et(now).date())
    groups = group_positions(positions.rows)
    quotes = quotes_for(prices, stamp, **(stamps or {})) if quotes is None else quotes
    qcs = qcs_for(quotes, now)
    session = market_clock.session_at(now)
    built = book.build_book(now=now, cfg=CFG, paths=PATHS, positions=positions, groups=groups, quotes=quotes, qcs=qcs,
                            calendar=calendar, prev_snapshot=prev_snapshot, session=session,
                            prior_close_utc=market_clock.previous_close_utc(now), bound_min=25, receipt_utc=now,
                            prev_snapshot_date=market_clock.previous_trading_day(session.et_date))
    return book.book_to_json(built)


def leg_by_symbol(document: Dict[str, Any], symbol: str, status: str = "open") -> Dict[str, Any]:
    for leg in document["legs"]:
        if (leg["occ"] or leg["symbol"]) == symbol and leg["status"] == status:
            return leg
    raise AssertionError("no %s leg %s in %r" % (status, symbol, [leg["occ"] or leg["symbol"] for leg in document["legs"]]))


def group_by_id(document: Dict[str, Any], group_id: str) -> Dict[str, Any]:
    for group in document["groups"]:
        if group["group_id"] == group_id:
            return group
    raise AssertionError("no group %s" % group_id)


def round_trip(document: Dict[str, Any]) -> Dict[str, Any]:
    """What a snapshot file holds: the document through the desk's JSON convention and back."""
    return json.loads(state.json_dumps(document))


class KeysTest(unittest.TestCase):
    def setUp(self) -> None:
        self.document = build()

    def test_top_level_keys_are_exactly_the_plan_section_8_keys(self) -> None:
        expected = {"generated_at", "session", "as_of_label", "data_quality", "sources", "rates", "config_sha", "legs",
                    "groups", "underlyings", "portfolio", "warnings"}
        self.assertEqual(expected, set(self.document))
        self.assertEqual(expected, set(book.BOOK_KEYS))
        self.assertEqual({"grades", "usable_legs", "unavailable_legs", "bound_min"}, set(self.document["data_quality"]))
        self.assertEqual({"name", "delay_min", "receipt_utc"}, set(self.document["sources"][0]))
        self.assertEqual({"risk_free_rate", "source", "as_of"}, set(self.document["rates"]))
        self.assertEqual({"desk_toml", "positions_csv", "calendar_toml"}, set(self.document["config_sha"]))

    def test_leg_keys(self) -> None:
        expected = {"row_number", "trade_id", "group_id", "account", "instrument_type", "symbol", "occ", "underlying",
                    "right", "strike", "expiry", "settlement", "dte_calendar", "dte_trading", "quantity", "multiplier",
                    "cost_basis_total", "entry_price_per_share", "status", "quote_grade", "quote_status", "bid", "ask",
                    "bid_date", "ask_date", "as_of", "spread_pct", "mark_mid", "mark_liq", "mark_source", "tau_years", "iv",
                    "iv_status", "greeks", "position_greeks", "unrealized_pl_mid", "unrealized_pl_liq", "day_pl",
                    "realized_day_pl", "extrinsic_bid", "itm", "warnings", "open_quantity", "remaining_cost_basis"}
        self.assertEqual(7, len(self.document["legs"]), "every row, closed ones included")
        for leg in self.document["legs"]:
            self.assertEqual(expected, set(leg), leg["row_number"])
        self.assertEqual(expected, set(book.LEG_KEYS))

    def test_group_keys(self) -> None:
        expected = {"group_id", "trade_id", "account", "underlying", "structure", "basis_kind", "net_cost", "max_profit",
                    "max_loss", "breakevens", "unbounded_up", "unbounded_down", "unrealized_pl_liq", "unrealized_pl_mid",
                    "pct_captured_liq", "pct_captured_mid", "realized_pl", "net_greeks", "legs", "data_quality",
                    "warnings", "underlying_stop"}
        self.assertEqual(4, len(self.document["groups"]))
        for group in self.document["groups"]:
            self.assertEqual(expected, set(group), group["group_id"])
        self.assertEqual(expected, set(book.GROUP_KEYS))

    def test_underlying_and_portfolio_keys(self) -> None:
        self.assertEqual({"WMT", "NVDA", "SPY"}, set(self.document["underlyings"]))
        for entry in self.document["underlyings"].values():
            self.assertEqual({"mark_mid", "bid", "ask", "as_of", "quote_grade", "prev_eod_mid", "move_pct_from_prev_eod",
                              "day_change_source"}, set(entry))
            self.assertEqual("desk snapshot", entry["day_change_source"])
        portfolio = self.document["portfolio"]
        self.assertEqual({"unrealized_pl_liq", "unrealized_pl_mid", "day_pl", "realized_pl_today", "realized_pl_mtd",
                          "realized_pl_ytd", "net_delta", "beta_weighted_delta", "concentration", "open_groups",
                          "quarantined_groups"}, set(portfolio))
        self.assertEqual({"value": None, "r2_flag": "unavailable"}, portfolio["beta_weighted_delta"])
        self.assertEqual({"declared_equity", "by_underlying"}, set(portfolio["concentration"]))

    def test_document_serialises_with_the_desk_convention(self) -> None:
        text = state.json_dumps(self.document)
        reloaded = json.loads(text)
        self.assertEqual("4097.00", reloaded["portfolio"]["unrealized_pl_mid"])
        self.assertEqual("2026-11-03T19:50:00+00:00", reloaded["generated_at"])
        self.assertEqual("2026-11-20", leg_by_symbol(reloaded, WMT_CALL)["expiry"])


class GoldensTest(unittest.TestCase):
    """The addendum's main-root goldens on the mid and liquidation bases, computed from the same prices."""

    def setUp(self) -> None:
        self.document = build()

    def test_unrealized_per_leg(self) -> None:
        expected = {"WMT": ("1146.00", "1140.00"), WMT_CALL: ("-1.00", "-16.00"), "NVDA": ("1545.00", "1540.00"),
                    NVDA_CALL: ("-30.00", "-50.00"), "SPY": ("1572.00", "1570.00"), SPY_PUT: ("-135.00", "-150.00")}
        for symbol, (mid, liq) in expected.items():
            leg = leg_by_symbol(self.document, symbol)
            self.assertEqual((D(mid), D(liq)), (leg["unrealized_pl_mid"], leg["unrealized_pl_liq"]), symbol)
            self.assertEqual("mid", leg["mark_source"])

    def test_portfolio_totals_and_realized(self) -> None:
        portfolio = self.document["portfolio"]
        self.assertEqual((D("4097.00"), D("4034.00")), (portfolio["unrealized_pl_mid"], portfolio["unrealized_pl_liq"]))
        self.assertEqual((D("0.00"), D("0.00"), D("425.00")),
                         (portfolio["realized_pl_today"], portfolio["realized_pl_mtd"], portfolio["realized_pl_ytd"]))
        self.assertEqual((3, []), (portfolio["open_groups"], portfolio["quarantined_groups"]))
        self.assertEqual(D("250000.00"), portfolio["concentration"]["declared_equity"])
        self.assertEqual({"WMT": D("8.61"), "NVDA": D("12.17"), "SPY": D("26.32")},
                         portfolio["concentration"]["by_underlying"])
        self.assertEqual(0, book.exit_code_for(self.document))

    def test_marks_and_quote_fields(self) -> None:
        wmt = leg_by_symbol(self.document, "WMT")
        self.assertEqual((D("104.23"), D("104.20"), D("104.20"), D("104.26"), "A", "fresh", D("0.06")),
                         (wmt["mark_mid"], wmt["mark_liq"], wmt["bid"], wmt["ask"], wmt["quote_grade"],
                          wmt["quote_status"], wmt["spread_pct"]))
        self.assertEqual((STAMP, STAMP, STAMP), (wmt["bid_date"], wmt["ask_date"], wmt["as_of"]))
        call = leg_by_symbol(self.document, WMT_CALL)
        self.assertEqual((D("3.425"), D("3.50"), "B"), (call["mark_mid"], call["mark_liq"], call["quote_grade"]),
                         "a short leg liquidates at the ask")
        self.assertEqual((D("-2"), 100, D("-684.00"), D("3.42"), "open"),
                         (call["quantity"], call["multiplier"], call["cost_basis_total"], call["entry_price_per_share"],
                          call["status"]))
        self.assertEqual((D("-2"), D("-684.00")), (call["open_quantity"], call["remaining_cost_basis"]),
                         "nothing closed: the open part is the whole row")
        self.assertEqual(("AG-20261020-01", "AG-20261020-01", "Brokerage (margin)", "option", "WMT", "WMT"),
                         (call["trade_id"], call["group_id"], call["account"], call["instrument_type"], call["symbol"],
                          call["underlying"]))

    def test_covered_call_group(self) -> None:
        group = group_by_id(self.document, "AG-20261020-01")
        self.assertEqual(("covered_call", "stock", D("19016.00"), D("1984.00"), D("19016.00"), [D("95.08")], False, False),
                         (group["structure"], group["basis_kind"], group["net_cost"], group["max_profit"],
                          group["max_loss"], group["breakevens"], group["unbounded_up"], group["unbounded_down"]))
        self.assertEqual((D("1145.00"), D("1124.00"), D("57.71"), D("56.65"), D("0.00"), "ok", D("92.00")),
                         (group["unrealized_pl_mid"], group["unrealized_pl_liq"], group["pct_captured_mid"],
                          group["pct_captured_liq"], group["realized_pl"], group["data_quality"], group["underlying_stop"]))
        self.assertEqual([leg_by_symbol(self.document, "WMT")["row_number"], leg_by_symbol(self.document, WMT_CALL)["row_number"]],
                         group["legs"])
        self.assertEqual([], group["warnings"])

    def test_protective_put_and_closed_groups(self) -> None:
        spy = group_by_id(self.document, "AG-20261028-03")
        self.assertEqual(["643.70"], [str(value) for value in spy["breakevens"]], "printed in cents, not 643.700")
        self.assertEqual(("protective_put", None, D("2370.00"), [D("643.70")], True, False, D("1437.00"), D("1420.00")),
                         (spy["structure"], spy["max_profit"], spy["max_loss"], spy["breakevens"], spy["unbounded_up"],
                          spy["unbounded_down"], spy["unrealized_pl_mid"], spy["unrealized_pl_liq"]))
        self.assertIsNone(spy["pct_captured_mid"], "no max profit, no percentage")
        closed = group_by_id(self.document, "AG-20261001-04")
        self.assertEqual(("cash_secured_put", D("-480.00"), D("425.00"), "ok", []),
                         (closed["structure"], closed["net_cost"], closed["realized_pl"], closed["data_quality"],
                          closed["legs"][1:]))
        self.assertIsNone(closed["max_profit"])
        self.assertIn(book.REASON_NO_OPEN_LEGS, closed["warnings"])
        closed_leg = leg_by_symbol(self.document, "SPY261016P00600000", status="closed")
        self.assertEqual((None, "closed", "unavailable", None, None, None),
                         (closed_leg["quote_grade"], closed_leg["quote_status"], closed_leg["mark_source"],
                          closed_leg["mark_mid"], closed_leg["unrealized_pl_mid"], closed_leg["day_pl"]))
        self.assertIsNone(closed_leg["realized_day_pl"], "closed in October, not today")

    def test_header_blocks(self) -> None:
        document = self.document
        self.assertEqual((NOW, "open", "as of 14:31:12 ET"), (document["generated_at"], document["session"],
                                                                 document["as_of_label"]))
        self.assertEqual({"grades": {"A": 3, "B": 2, "C": 1, "D": 0, "E": 0, "F": 0}, "usable_legs": 6,
                          "unavailable_legs": 0, "bound_min": 25}, document["data_quality"])
        self.assertEqual([{"name": "tradier:sandbox", "delay_min": 15, "receipt_utc": NOW}], document["sources"])
        self.assertEqual({"risk_free_rate": D("0.0412"), "source": "3-month Treasury bill, owner-entered",
                          "as_of": datetime.date(2026, 10, 30)}, document["rates"])
        positions = positions_of(FIXTURE_LINES)
        self.assertEqual({"desk_toml": CFG.sha256, "positions_csv": positions.sha256, "calendar_toml": CAL.sha256},
                         document["config_sha"])
        self.assertEqual([book.day_pl_warning(datetime.date(2026, 11, 2))], document["warnings"])
        self.assertEqual("day P/L unavailable: no snapshot for 2026-11-02 (the desk's first day?)",
                         book.day_pl_warning(datetime.date(2026, 11, 2)))

    def test_underlyings_block_without_a_previous_snapshot(self) -> None:
        wmt = self.document["underlyings"]["WMT"]
        self.assertEqual((D("104.23"), D("104.20"), D("104.26"), STAMP, "A", None, None, "desk snapshot"),
                         (wmt["mark_mid"], wmt["bid"], wmt["ask"], wmt["as_of"], wmt["quote_grade"], wmt["prev_eod_mid"],
                          wmt["move_pct_from_prev_eod"], wmt["day_change_source"]))


class OptionAnalyticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.document = build()

    def test_tau_iv_and_greeks_match_the_engine_called_independently(self) -> None:
        cases = {WMT_CALL: ("CALL", "WMT", 105.0, 1474128), NVDA_CALL: ("CALL", "NVDA", 150.0, 1474128),
                 SPY_PUT: ("PUT", "SPY", 620.0, 3893328)}
        for symbol, (right, underlying, strike, seconds) in cases.items():
            leg = leg_by_symbol(self.document, symbol)
            spot = self.document["underlyings"][underlying]["mark_mid"]
            tau = seconds / units.SECONDS_PER_YEAR
            self.assertAlmostEqual(tau, leg["tau_years"], places=12, msg=symbol)
            q = float(CAL.dividend_yield(underlying))
            expected = iv.implied_vol(float(leg["mark_mid"]), right, float(spot), strike, tau, RISK_FREE, q)
            self.assertEqual(("ok", expected.sigma), (leg["iv_status"], leg["iv"]), symbol)
            self.assertEqual({"delta", "gamma", "theta", "vega", "rho"}, set(leg["greeks"]))
            self.assertEqual({"delta", "gamma", "theta", "vega", "rho"}, set(leg["position_greeks"]))
            scale = float(leg["quantity"]) * 100
            self.assertAlmostEqual(leg["greeks"]["delta"] * scale, leg["position_greeks"]["delta"], places=9)
            self.assertEqual([], [w for w in leg["warnings"] if w.startswith("q = 0")])
        wmt = leg_by_symbol(self.document, WMT_CALL)
        self.assertAlmostEqual(0.4132, wmt["iv"], places=3)
        self.assertGreater(wmt["greeks"]["delta"], 0)
        self.assertLess(wmt["position_greeks"]["delta"], 0, "two short calls carry a negative position delta")
        spy = leg_by_symbol(self.document, SPY_PUT)
        self.assertLess(spy["greeks"]["delta"], 0)

    def test_contract_terms_dte_settlement_extrinsic_and_itm(self) -> None:
        wmt = leg_by_symbol(self.document, WMT_CALL)
        self.assertEqual(("CALL", D("105.000"), datetime.date(2026, 11, 20), "pm", 17, 13, D("3.35"), False),
                         (wmt["right"], wmt["strike"], wmt["expiry"], wmt["settlement"], wmt["dte_calendar"],
                          wmt["dte_trading"], wmt["extrinsic_bid"], wmt["itm"]))
        spy = leg_by_symbol(self.document, SPY_PUT)
        self.assertEqual(("PUT", D("620.000"), datetime.date(2026, 12, 18), "pm", 45, 32, D("9.80"), False),
                         (spy["right"], spy["strike"], spy["expiry"], spy["settlement"], spy["dte_calendar"],
                          spy["dte_trading"], spy["extrinsic_bid"], spy["itm"]))
        stock = leg_by_symbol(self.document, "WMT")
        self.assertEqual((None, None, None, None, None, None, None, None, None, None, book.IV_STATUS_NOT_OPTION),
                         (stock["occ"], stock["right"], stock["strike"], stock["expiry"], stock["settlement"],
                          stock["dte_calendar"], stock["dte_trading"], stock["tau_years"], stock["iv"], stock["greeks"],
                          stock["iv_status"]))
        self.assertEqual("WMT", stock["underlying"])

    def test_itm_uses_the_one_cent_rule_on_the_underlying_mid(self) -> None:
        prices = dict(REGULAR_PRICES)
        prices["WMT"] = ("105.00", "105.02")  # mid 105.01 = K + 0.01: in the money
        document = build(prices=prices)
        self.assertTrue(leg_by_symbol(document, WMT_CALL)["itm"])
        self.assertEqual(D("3.34"), leg_by_symbol(document, WMT_CALL)["extrinsic_bid"])
        prices["WMT"] = ("104.99", "105.01")  # mid 105.00: not yet
        self.assertFalse(leg_by_symbol(build(prices=prices), WMT_CALL)["itm"])

    def test_net_greeks_add_stock_delta_to_position_greeks(self) -> None:
        group = group_by_id(self.document, "AG-20261020-01")
        call = leg_by_symbol(self.document, WMT_CALL)
        self.assertAlmostEqual(200.0 + call["position_greeks"]["delta"], group["net_greeks"]["delta"], places=9)
        self.assertAlmostEqual(call["position_greeks"]["vega"], group["net_greeks"]["vega"], places=9)
        portfolio_delta = sum(g["net_greeks"]["delta"] for g in self.document["groups"] if g["net_greeks"] is not None)
        self.assertAlmostEqual(portfolio_delta, self.document["portfolio"]["net_delta"], places=9)
        closed = group_by_id(self.document, "AG-20261001-04")
        self.assertIsNone(closed["net_greeks"])

    def test_q_defaults_to_zero_with_a_leg_warning_without_a_calendar(self) -> None:
        document = build(calendar=None)
        self.assertIn(book.WARNING_NO_CALENDAR, document["warnings"])
        self.assertIsNone(document["config_sha"]["calendar_toml"])
        leg = leg_by_symbol(document, WMT_CALL)
        self.assertIn("q = 0: no dividend yield in calendar.toml", leg["warnings"])
        spot = document["underlyings"]["WMT"]["mark_mid"]
        expected = iv.implied_vol(3.425, "CALL", float(spot), 105.0, 1474128 / units.SECONDS_PER_YEAR, RISK_FREE, 0.0)
        self.assertEqual(expected.sigma, leg["iv"])
        self.assertEqual("pm", leg["settlement"], "every root is PM-settled without a calendar")

    def test_under_one_hour_to_the_cutoff_is_expiring(self) -> None:
        now = datetime.datetime(2026, 11, 20, 20, 30, tzinfo=UTC)
        stamp = datetime.datetime(2026, 11, 20, 20, 25, tzinfo=UTC)  # 15:25 ET, 35 minutes before the 16:00 close
        document = build(now=now, stamp=stamp)
        leg = leg_by_symbol(document, WMT_CALL)
        self.assertEqual((book.IV_STATUS_EXPIRING, None, None, None), (leg["iv_status"], leg["iv"], leg["greeks"],
                                                                        leg["position_greeks"]))
        self.assertAlmostEqual(2100 / units.SECONDS_PER_YEAR, leg["tau_years"], places=12)
        self.assertEqual((0, 0), (leg["dte_calendar"], leg["dte_trading"]))
        self.assertEqual("mid", leg["mark_source"], "the marks and P/L stand; only the model values are withheld")
        self.assertEqual("ok", leg_by_symbol(document, SPY_PUT)["iv_status"])

    def test_spot_unaligned_when_the_underlying_stamp_differs_by_more_than_a_minute(self) -> None:
        document = build(stamps={"WMT": STAMP - datetime.timedelta(seconds=61)})
        leg = leg_by_symbol(document, WMT_CALL)
        self.assertEqual((book.IV_STATUS_SPOT_UNALIGNED, None, None), (leg["iv_status"], leg["iv"], leg["greeks"]))
        self.assertEqual("mid", leg["mark_source"])
        aligned = build(stamps={"WMT": STAMP - datetime.timedelta(seconds=60)})
        self.assertEqual("ok", leg_by_symbol(aligned, WMT_CALL)["iv_status"], "60 seconds is inside the window")

    def test_spot_unavailable_when_the_underlying_has_no_usable_mark(self) -> None:
        prices = dict(REGULAR_PRICES)
        prices["WMT"] = ("104.20", None)
        document = build(prices=prices)
        leg = leg_by_symbol(document, WMT_CALL)
        self.assertEqual((book.IV_STATUS_SPOT_UNAVAILABLE, None, None, None), (leg["iv_status"], leg["iv"],
                                                                                leg["extrinsic_bid"], leg["itm"]))
        self.assertEqual("mid", leg["mark_source"], "the option's own mark is fine")
        self.assertEqual("unavailable", group_by_id(document, "AG-20261020-01")["data_quality"],
                         "the stock leg of the group has no mark")


class DataQualityTest(unittest.TestCase):
    def test_a_stale_leg_makes_its_group_unavailable_and_is_never_summed_as_zero(self) -> None:
        document = build(stamps={WMT_CALL: NOW - datetime.timedelta(minutes=60)})
        leg = leg_by_symbol(document, WMT_CALL)
        self.assertEqual(("F", "stale", "unavailable", None, None, None, None),
                         (leg["quote_grade"], leg["quote_status"], leg["mark_source"], leg["mark_mid"], leg["mark_liq"],
                          leg["unrealized_pl_mid"], leg["iv"]))
        self.assertEqual(book.IV_STATUS_UNAVAILABLE, leg["iv_status"])
        self.assertEqual(D("3.35"), leg["bid"], "the stale quote is still displayed")
        group = group_by_id(document, "AG-20261020-01")
        self.assertEqual(("unavailable", None, None, None, None, None),
                         (group["data_quality"], group["unrealized_pl_mid"], group["unrealized_pl_liq"],
                          group["pct_captured_mid"], group["pct_captured_liq"], group["net_greeks"]))
        self.assertEqual(D("1984.00"), group["max_profit"], "the payoff profile needs no quote")
        portfolio = document["portfolio"]
        self.assertEqual((D("2952.00"), D("2910.00")), (portfolio["unrealized_pl_mid"], portfolio["unrealized_pl_liq"]))
        self.assertTrue(any("AG-20261020-01" in w and "excluded" in w for w in document["warnings"]), document["warnings"])
        self.assertEqual({"A": 3, "B": 1, "C": 1, "D": 0, "E": 0, "F": 1}, document["data_quality"]["grades"])
        self.assertEqual((5, 1), (document["data_quality"]["usable_legs"], document["data_quality"]["unavailable_legs"]))
        self.assertEqual(20, book.exit_code_for(document))
        self.assertIsNone(portfolio["concentration"]["by_underlying"]["WMT"], "an unmarked leg is not silently a zero exposure")
        self.assertEqual(D("12.17"), portfolio["concentration"]["by_underlying"]["NVDA"])

    def test_grade_e_shows_the_mid_but_has_no_marks(self) -> None:
        prices = dict(REGULAR_PRICES)
        prices[WMT_CALL] = ("0", "3.50")
        document = build(prices=prices)
        leg = leg_by_symbol(document, WMT_CALL)
        self.assertEqual(("E", D("1.75"), None, "unavailable", None), (leg["quote_grade"], leg["mark_mid"], leg["mark_liq"],
                                                                        leg["mark_source"], leg["unrealized_pl_mid"]))
        self.assertEqual("unavailable", group_by_id(document, "AG-20261020-01")["data_quality"])

    def test_a_symbol_the_cache_could_not_serve_is_unavailable(self) -> None:
        quotes = quotes_for(REGULAR_PRICES)
        del quotes[NVDA_CALL]
        positions = positions_of(FIXTURE_LINES)
        groups = group_positions(positions.rows)
        qcs = qcs_for(quotes, NOW)
        qcs[NVDA_CALL] = quote_qc.unavailable(NVDA_CALL, "cache: unmatched", label="no quote")
        session = market_clock.session_at(NOW)
        built = book.build_book(now=NOW, cfg=CFG, paths=PATHS, positions=positions, groups=groups, quotes=quotes, qcs=qcs,
                                calendar=CAL, prev_snapshot=None, session=session,
                                prior_close_utc=market_clock.previous_close_utc(NOW), bound_min=25, receipt_utc=NOW,
                                cache_warnings=["NVDA261120C00150000 absent from the vendor response"])
        document = book.book_to_json(built)
        leg = leg_by_symbol(document, NVDA_CALL)
        self.assertEqual(("F", "missing", "unavailable"), (leg["quote_grade"], leg["quote_status"], leg["mark_source"]))
        self.assertIn("cache: unmatched", leg["warnings"])
        self.assertEqual("unavailable", group_by_id(document, "AG-20261022-02")["data_quality"])
        self.assertTrue(any(w.startswith("quote cache: ") for w in document["warnings"]), document["warnings"])
        self.assertEqual(20, book.exit_code_for(document))

    def test_an_open_leg_past_its_last_trading_day_is_expired_not_quoted(self) -> None:
        lines = FIXTURE_LINES + ["option,WMT,WMT261016C00105000,-1,,,,Brokerage (margin),AG-20261009-05,2026-10-09,"
                                 "-150.00,,,open,,,,,expired leg"]
        positions = positions_of(lines)
        groups = group_positions(positions.rows)
        self.assertEqual(SIX, book.request_batch(groups, TODAY, CAL), "the expired leg is not quoted")
        document = build(lines=lines)
        leg = leg_by_symbol(document, "WMT261016C00105000")
        self.assertEqual((None, book.QUOTE_STATUS_EXPIRED, "unavailable", book.IV_STATUS_EXPIRED, -18),
                         (leg["quote_grade"], leg["quote_status"], leg["mark_source"], leg["iv_status"], leg["dte_calendar"]))
        self.assertTrue(any("expired" in w for w in leg["warnings"]), leg["warnings"])
        group = group_by_id(document, "AG-20261009-05")
        self.assertEqual(("short_call", "unavailable", None), (group["structure"], group["data_quality"],
                                                                group["unrealized_pl_mid"]))
        self.assertEqual(20, book.exit_code_for(document))
        self.assertEqual((6, 1), (document["data_quality"]["usable_legs"], document["data_quality"]["unavailable_legs"]))

    def test_quarantined_groups_are_listed_not_fatal(self) -> None:
        lines = FIXTURE_LINES + ["option,WMT,WMT261120P00095000,-1,,,,Brokerage (margin),AG-20261030-06,2026-10-30,"
                                 "165.00,,,open,,,,,sign slip"]
        positions = positions_of(lines)
        self.assertEqual(["AG-20261030-06"], list(positions.quarantined))
        document = build(lines=lines)
        listed = document["portfolio"]["quarantined_groups"]
        self.assertEqual(1, len(listed))
        self.assertEqual("AG-20261030-06", listed[0]["group_id"])
        self.assertTrue(any("sign" in reason for reason in listed[0]["reasons"]), listed[0])
        self.assertEqual(3, document["portfolio"]["open_groups"])
        self.assertEqual(D("4097.00"), document["portfolio"]["unrealized_pl_mid"], "the clean groups are still valued")
        self.assertEqual(20, book.exit_code_for(document))

    def test_a_group_level_quarantine_is_listed_and_unavailable(self) -> None:
        lines = list(FIXTURE_LINES)
        lines[1] = lines[1].replace("Brokerage (margin)", "Roth IRA")
        document = build(lines=lines)
        group = group_by_id(document, "AG-20261020-01")
        self.assertEqual(("invalid", "unavailable", None), (group["structure"], group["data_quality"], group["max_profit"]))
        self.assertIn(book.REASON_QUARANTINED, group["warnings"])
        listed = document["portfolio"]["quarantined_groups"]
        self.assertEqual([("AG-20261020-01", ["account mismatch: Brokerage (margin), Roth IRA"])],
                         [(item["group_id"], item["reasons"]) for item in listed])
        self.assertEqual("mid", leg_by_symbol(document, "WMT")["mark_source"], "its legs are still marked")
        self.assertEqual(20, book.exit_code_for(document))

    def test_partial_close_is_flagged_on_the_leg(self) -> None:
        lines = list(FIXTURE_LINES)
        lines[1] = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,"
                    "open,2026-11-03,1,1.00,,covered call #assign-ok")
        document = build(lines=lines)
        leg = leg_by_symbol(document, WMT_CALL)
        self.assertEqual(D("-0.50"), leg["unrealized_pl_mid"], "one contract remains: -100 x 3.425 + 342")
        self.assertEqual(D("242.00"), leg["realized_day_pl"], "closed today at 1.00 against the 3.42 credit")
        self.assertTrue(any("partially closed" in w for w in leg["warnings"]), leg["warnings"])
        self.assertEqual((D("-2"), D("-684.00"), D("-1"), D("-342.00")),
                         (leg["quantity"], leg["cost_basis_total"], leg["open_quantity"], leg["remaining_cost_basis"]),
                         "quantity and cost_basis_total are the record of opening; the open part is marked")
        self.assertEqual(D("-0.50"), leg["open_quantity"] * leg["multiplier"] * leg["mark_mid"] - leg["remaining_cost_basis"],
                         "a consumer re-deriving the unrealized P/L from the block gets the open part")
        self.assertEqual(D("242.00"), document["portfolio"]["realized_pl_today"])
        group = group_by_id(document, "AG-20261020-01")
        self.assertEqual("covered_call", group["structure"])
        self.assertTrue(any("uncovered shares" in w for w in group["warnings"]),
                        "200 shares against one remaining call: the extra shares are uncovered, still valued")
        self.assertEqual((None, True), (group["max_profit"], group["unbounded_up"]))


class RequestBatchTest(unittest.TestCase):
    def test_the_six_symbol_batch_of_the_fixture_book(self) -> None:
        groups = group_positions(positions_of(FIXTURE_LINES).rows)
        self.assertEqual(SIX, book.request_batch(groups, TODAY, CAL))
        self.assertEqual(SIX, book.request_batch(groups, TODAY, None))

    def test_closed_rows_and_expired_legs_are_left_out_and_symbols_are_deduplicated(self) -> None:
        lines = FIXTURE_LINES + [
            "option,WMT,WMT261120C00110000,-1,,,,Brokerage (margin),AG-20261030-07,2026-10-30,-120.00,,,open,,,,,",
            "option,WMT,WMT261016C00105000,-1,,,,Brokerage (margin),AG-20261009-05,2026-10-09,-150.00,,,open,,,,,",
        ]
        groups = group_positions(positions_of(lines).rows)
        self.assertEqual(sorted(SIX + ["WMT261120C00110000"]), book.request_batch(groups, TODAY, CAL))

    def test_a_leg_on_its_last_trading_day_is_still_quoted(self) -> None:
        groups = group_positions(positions_of(FIXTURE_LINES, today=datetime.date(2026, 11, 20)).rows)
        self.assertEqual(SIX, book.request_batch(groups, datetime.date(2026, 11, 20), CAL))
        after = book.request_batch(groups, datetime.date(2026, 11, 21), CAL)
        self.assertEqual(sorted(["NVDA", "SPY", SPY_PUT, "WMT"]), after,
                         "the November calls have expired; their underlyings stay for the stock legs")


class DayPlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = round_trip(build(prices=PREV_PRICES, now=PREV_NOW, stamp=PREV_STAMP))
        self.assertEqual("closed", self.previous["session"])
        self.assertEqual("as of close 16:00 ET", self.previous["as_of_label"])
        self.document = build(prev_snapshot=self.previous)

    def test_leg_and_portfolio_day_pl_from_the_previous_snapshot(self) -> None:
        expected = {"WMT": "84.00", WMT_CALL: "-50.00", "NVDA": "264.00", NVDA_CALL: "-70.00", "SPY": "222.00",
                    SPY_PUT: "-50.00"}
        for symbol, value in expected.items():
            self.assertEqual(D(value), leg_by_symbol(self.document, symbol)["day_pl"], symbol)
        self.assertEqual(D("400.00"), self.document["portfolio"]["day_pl"])
        self.assertEqual([], [w for w in self.document["warnings"] if "day P/L" in w])

    def test_underlyings_carry_the_previous_desk_close(self) -> None:
        wmt = self.document["underlyings"]["WMT"]
        self.assertEqual((D("103.81"), D("0.40"), "desk snapshot"),
                         (wmt["prev_eod_mid"], wmt["move_pct_from_prev_eod"], wmt["day_change_source"]))
        self.assertEqual((D("147.605"), D("0.89")), (self.document["underlyings"]["NVDA"]["prev_eod_mid"],
                                                     self.document["underlyings"]["NVDA"]["move_pct_from_prev_eod"]))
        self.assertEqual(D("0.34"), self.document["underlyings"]["SPY"]["move_pct_from_prev_eod"])

    def test_without_a_snapshot_day_pl_is_none_with_the_warning(self) -> None:
        document = build()
        for leg in document["legs"]:
            self.assertIsNone(leg["day_pl"], leg["row_number"])
        self.assertIsNone(document["portfolio"]["day_pl"])
        self.assertIn(book.day_pl_warning(datetime.date(2026, 11, 2)), document["warnings"])

    def test_a_leg_opened_today_measures_from_its_entry_price(self) -> None:
        lines = list(FIXTURE_LINES)
        lines[0] = lines[0].replace("AG-20261020-01,2026-10-20,,98.50", "AG-20261020-01,2026-11-03,,98.50")
        document = build(lines=lines)
        self.assertEqual(D("1146.00"), leg_by_symbol(document, "WMT")["day_pl"], "entry 98.50 to the 104.23 mid")
        self.assertIsNone(document["portfolio"]["day_pl"], "the desk still has no snapshot for yesterday")
        with_snapshot = build(lines=lines, prev_snapshot=self.previous)
        self.assertEqual(D("1146.00"), leg_by_symbol(with_snapshot, "WMT")["day_pl"])
        self.assertEqual(D("1462.00"), with_snapshot["portfolio"]["day_pl"], "400 - 84 + 1146")

    def test_a_leg_missing_from_the_snapshot_is_none_with_a_warning(self) -> None:
        previous = round_trip(build(prices=PREV_PRICES, now=PREV_NOW, stamp=PREV_STAMP))
        previous["legs"] = [leg for leg in previous["legs"] if leg["occ"] != WMT_CALL]
        document = build(prev_snapshot=previous)
        leg = leg_by_symbol(document, WMT_CALL)
        self.assertIsNone(leg["day_pl"])
        self.assertTrue(any("day P/L" in w for w in leg["warnings"]), leg["warnings"])
        self.assertEqual(D("366.00"), document["portfolio"]["day_pl"],
                         "the WMT group (84.00 - 50.00) is excluded as a whole: 400.00 - 34.00; never a partial group")
        self.assertTrue(any("day P/L" in w and WMT_CALL in w for w in document["warnings"]), document["warnings"])
        self.assertTrue(any(w.startswith("AG-20261020-01 excluded from the day P/L total: ") and WMT_CALL in w
                            for w in document["warnings"]), document["warnings"])
        self.assertEqual(D("84.00"), leg_by_symbol(document, "WMT")["day_pl"], "the leg itself still has its day P/L")
        self.assertEqual(D("1145.00"), group_by_id(document, "AG-20261020-01")["unrealized_pl_mid"],
                         "the unrealized totals are untouched by a day P/L gap")
        self.assertEqual(D("4097.00"), document["portfolio"]["unrealized_pl_mid"])

    def test_a_previous_mark_that_was_unavailable_gives_no_day_pl(self) -> None:
        previous = round_trip(build(prices=PREV_PRICES, now=PREV_NOW, stamp=PREV_STAMP,
                                    stamps={WMT_CALL: PREV_NOW - datetime.timedelta(hours=2)}))
        self.assertIsNone(leg_by_symbol(previous, WMT_CALL)["mark_mid"])
        document = build(prev_snapshot=previous)
        self.assertIsNone(leg_by_symbol(document, WMT_CALL)["day_pl"])
        self.assertEqual(D("366.00"), document["portfolio"]["day_pl"], "the WMT group is excluded, the rest sums")
        self.assertTrue(any(w.startswith("AG-20261020-01 excluded from the day P/L total: ") for w in document["warnings"]),
                        document["warnings"])

    def test_a_display_only_previous_mid_never_feeds_day_pl(self) -> None:
        # Money finding 1: a grade-E previous quote keeps its mid for display (mark_source "unavailable",
        # unrealized None). That mid is not a basis and must never become mark_prev_eod.
        cases = {
            "zero bid": {WMT_CALL: ("0", "3.50")},
            "wide spread": {WMT_CALL: ("1.00", "1.40")},
        }
        for label, overrides in cases.items():
            with self.subTest(case=label):
                prices = dict(PREV_PRICES)
                prices.update(overrides)
                previous = round_trip(build(prices=prices, now=PREV_NOW, stamp=PREV_STAMP))
                prev_leg = leg_by_symbol(previous, WMT_CALL)
                self.assertEqual(("E", "unavailable", None), (prev_leg["quote_grade"], prev_leg["mark_source"],
                                                               prev_leg["unrealized_pl_mid"]))
                self.assertIsNotNone(prev_leg["mark_mid"], "the display mid is in the snapshot")
                document = build(prev_snapshot=previous)
                leg = leg_by_symbol(document, WMT_CALL)
                self.assertIsNone(leg["day_pl"], "a display-only mid is not a previous mark")
                self.assertTrue(any(w.startswith("day P/L unavailable: no mark in the previous snapshot") for w in leg["warnings"]),
                                leg["warnings"])
                self.assertEqual(D("366.00"), document["portfolio"]["day_pl"],
                                 "the WMT group is excluded; the display mid never enters the total")
                self.assertTrue(any("day P/L unavailable for %s (row 3)" % WMT_CALL in w for w in document["warnings"]),
                                document["warnings"])
                self.assertTrue(any(w.startswith("AG-20261020-01 excluded from the day P/L total: ") for w in document["warnings"]),
                                document["warnings"])

    def test_a_display_only_previous_stock_mid_never_feeds_day_pl(self) -> None:
        prices = dict(PREV_PRICES)
        prices["WMT"] = ("0", "103.84")  # grade E, mid 51.92 displayed, never a basis
        previous = round_trip(build(prices=prices, now=PREV_NOW, stamp=PREV_STAMP))
        self.assertEqual(("E", "unavailable"), (leg_by_symbol(previous, "WMT")["quote_grade"],
                                                leg_by_symbol(previous, "WMT")["mark_source"]))
        self.assertIsNone(previous["underlyings"]["WMT"]["mark_mid"], "the underlyings block never carried it")
        document = build(prev_snapshot=previous)
        self.assertIsNone(leg_by_symbol(document, "WMT")["day_pl"])
        self.assertEqual(D("-50.00"), leg_by_symbol(document, WMT_CALL)["day_pl"], "the call leg had a real previous mark")
        self.assertEqual(D("366.00"), document["portfolio"]["day_pl"], "400.00 less the whole WMT group (84.00 - 50.00)")
        self.assertIsNone(document["underlyings"]["WMT"]["prev_eod_mid"])

    def test_portfolio_day_pl_is_none_when_no_group_has_one(self) -> None:
        previous = round_trip(build(prices=PREV_PRICES, now=PREV_NOW, stamp=PREV_STAMP))
        previous["legs"] = []
        document = build(prev_snapshot=previous)
        self.assertTrue(all(leg["day_pl"] is None for leg in document["legs"]))
        self.assertIsNone(document["portfolio"]["day_pl"], "every group is excluded: no total, not zero")
        excluded = [w for w in document["warnings"] if "excluded from the day P/L total" in w]
        self.assertEqual(["AG-20261020-01", "AG-20261022-02", "AG-20261028-03"],
                         sorted(w.split(" ")[0] for w in excluded))
        self.assertEqual(D("4097.00"), document["portfolio"]["unrealized_pl_mid"])

    def test_day_pl_of_a_leg_in_an_unavailable_group_is_excluded(self) -> None:
        document = build(prev_snapshot=self.previous, stamps={NVDA_CALL: NOW - datetime.timedelta(minutes=60)})
        self.assertEqual(D("206.00"), document["portfolio"]["day_pl"], "400 less the NVDA group's 264 - 70")


class SnapshotHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-book-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        desk_paths.ensure_runtime_dirs(self.paths)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_paths_and_dates(self) -> None:
        self.assertEqual(os.path.join(self.root, "data", "book", "2026-11-03.json"),
                         book.snapshot_path(self.paths, datetime.date(2026, 11, 3)))
        self.assertEqual(datetime.date(2026, 11, 2), book.previous_snapshot_date(datetime.date(2026, 11, 3)))
        self.assertEqual(datetime.date(2026, 11, 20), book.previous_snapshot_date(datetime.date(2026, 11, 23)))
        self.assertEqual(datetime.date(2026, 11, 20), book.previous_snapshot_date(datetime.date(2026, 11, 21)),
                         "a Saturday looks back to Friday")
        self.assertEqual(datetime.date(2026, 11, 25), book.previous_snapshot_date(datetime.date(2026, 11, 27)),
                         "Thanksgiving is skipped")

    def test_write_and_read_back(self) -> None:
        document = build()
        path = book.write_snapshot(self.paths, document, datetime.date(2026, 11, 3))
        self.assertEqual(book.snapshot_path(self.paths, datetime.date(2026, 11, 3)), path)
        self.assertTrue(os.path.isfile(path))
        loaded, status = book.read_previous_snapshot(self.paths, datetime.date(2026, 11, 4))
        self.assertEqual("ok", status)
        self.assertEqual(set(book.BOOK_KEYS), set(loaded))
        self.assertEqual("4097.00", loaded["portfolio"]["unrealized_pl_mid"])
        self.assertEqual((None, "missing"), book.read_previous_snapshot(self.paths, datetime.date(2026, 11, 3)))

    def test_corrupt_and_malformed_snapshots_are_reported_not_used(self) -> None:
        path = book.snapshot_path(self.paths, datetime.date(2026, 11, 2))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual((None, "corrupt"), book.read_previous_snapshot(self.paths, datetime.date(2026, 11, 3)))
        self.assertFalse(os.path.exists(path), "quarantined by state.read_json")
        state.atomic_write_json(path, {"legs": "not a list"})
        self.assertEqual((None, "malformed"), book.read_previous_snapshot(self.paths, datetime.date(2026, 11, 3)))

    def test_report_rendering(self) -> None:
        document = build()
        text = book.render_report(document)
        for needle in ("AG-20261020-01", "covered_call", "4097.00", "425.00", "as of 14:31:12 ET"):
            self.assertIn(needle, text)
        path = book.write_report(self.paths, document)
        self.assertEqual(os.path.join(self.root, "reports", "book.md"), path)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(text, handle.read())


class AssessQuotesTest(unittest.TestCase):
    """``assess_quotes`` grades in the session it is GIVEN (the calendar-aware classification ``load_book``
    makes), never in a session of its own: one cache result reads open or closed with the session."""

    def setUp(self) -> None:
        self.quotes = quotes_for({"WMT": REGULAR_PRICES["WMT"]})
        self.result = quote_cache.CacheResult(quotes=dict(self.quotes), served_from={"WMT": "cache"},
                                              missing={WMT_CALL: "not fetched: closed day"}, requests_made=0,
                                              receipt_utc=None, warnings=[])

    def test_an_open_session_grades_the_quote_and_labels_the_missing_symbol(self) -> None:
        session = market_clock.session_at(NOW)
        quotes, verdicts = book.assess_quotes(self.result, ["WMT", WMT_CALL], now=NOW, session=session, bound_min=25,
                                              prior_close_utc=market_clock.previous_close_utc(NOW))
        self.assertEqual(["WMT"], sorted(quotes), "a symbol without a served or stale copy has no quote")
        wmt = verdicts["WMT"]
        self.assertEqual(("A", "fresh", "as of 14:31:12 ET", True, D("104.23")),
                         (wmt.grade, wmt.status, wmt.label, wmt.usable_for_rules, wmt.mark_mid))
        call = verdicts[WMT_CALL]
        self.assertEqual(("F", "missing", book.LABEL_NO_QUOTE, False, ["cache: not fetched: closed day"]),
                         (call.grade, call.status, call.label, call.usable_for_rules, call.reasons))

    def test_an_injected_closure_session_reads_closed_for_every_symbol(self) -> None:
        closure = {TODAY: calendar_events.CLOSURE_LABEL}
        session = market_clock.session_at(NOW, extra_holidays=closure)
        self.assertEqual(("holiday", False, "calendar closure"), (session.session, session.trading_day, session.holiday_name))
        quotes, verdicts = book.assess_quotes(self.result, ["WMT", WMT_CALL], now=NOW, session=session, bound_min=25,
                                              prior_close_utc=market_clock.previous_close_utc(NOW, extra_holidays=closure))
        self.assertEqual(["WMT"], sorted(quotes))
        wmt = verdicts["WMT"]
        self.assertEqual(("F", "closed_day", "NYSE closed", False, None, [quote_qc.REASON_MARKET_CLOSED]),
                         (wmt.grade, wmt.status, wmt.label, wmt.usable_for_rules, wmt.mark_mid, wmt.reasons))
        self.assertEqual((D("104.20"), D("104.26")), (wmt.bid, wmt.ask), "the quote is still displayed")
        call = verdicts[WMT_CALL]
        self.assertEqual(("F", "missing", "NYSE closed", False), (call.grade, call.status, call.label, call.usable_for_rules))


class LoadBookTest(unittest.TestCase):
    """The library entry point against a copied fixture desk root: one quotes request, the addendum's goldens."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-book-load-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def market(self, fixture_set: str, now: datetime.datetime) -> quote_cache.MarketData:
        return quote_cache.open_market_data(self.root, now=now, offline=True, fixture_set=fixture_set, timeout_s=5.0,
                                            user_agent="aegis-desk/test book")

    def ledger(self) -> List[Dict[str, Any]]:
        path = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def test_regular_goldens_through_the_entry_point(self) -> None:
        run = book.load_book(self.market("regular", NOW))
        self.assertEqual((0, SIX, "missing", datetime.date(2026, 11, 2)),
                         (run.exit_code, run.symbols, run.prev_snapshot_status, run.prev_snapshot_date))
        self.assertEqual(1, len(self.ledger()), "one quotes request for the whole book")
        self.assertEqual(6, self.ledger()[0]["symbols"])
        document = run.document
        self.assertEqual(D("4097.00"), document["portfolio"]["unrealized_pl_mid"])
        self.assertEqual(D("4034.00"), document["portfolio"]["unrealized_pl_liq"])
        self.assertEqual(D("425.00"), document["portfolio"]["realized_pl_ytd"])
        self.assertEqual([D("1146.00"), D("-1.00"), D("1545.00"), D("-30.00"), D("1572.00"), D("-135.00"), None],
                         [leg["unrealized_pl_mid"] for leg in document["legs"]])
        self.assertEqual([3, 4, 5, 6, 7, 8, 9], [leg["row_number"] for leg in document["legs"]], "physical line numbers")
        payload = round_trip(document)
        self.assertEqual(("4097.00", "425.00", None), (payload["portfolio"]["unrealized_pl_mid"],
                                                       payload["portfolio"]["realized_pl_ytd"], payload["portfolio"]["day_pl"]))
        self.assertEqual("regular", run.market.fixture_set)
        self.assertEqual(4, len(run.groups))
        self.assertIsNotNone(run.calendar)

    def test_stale_set_is_unavailable(self) -> None:
        run = book.load_book(self.market("stale", NOW))
        self.assertEqual(20, run.exit_code)
        for leg in run.document["legs"]:
            if leg["status"] == "open":
                self.assertEqual(("F", "unavailable"), (leg["quote_grade"], leg["mark_source"]), leg["row_number"])
        self.assertEqual((0, 6), (run.document["data_quality"]["usable_legs"], run.document["data_quality"]["unavailable_legs"]))
        self.assertIsNone(run.document["portfolio"]["net_delta"])

    def test_snapshot_chain_gives_the_day_pl_goldens(self) -> None:
        first = book.load_book(self.market("pre_open", PREV_NOW))
        self.assertEqual(0, first.exit_code)
        self.assertEqual(("closed", "as of close 16:00 ET"), (first.document["session"], first.document["as_of_label"]))
        path = book.write_snapshot(first.market.paths, first.document, first.session.et_date)
        self.assertEqual(os.path.join(self.root, "data", "book", "2026-11-02.json"), path)
        second = book.load_book(self.market("regular", NOW))
        self.assertEqual((0, "ok", datetime.date(2026, 11, 2)), (second.exit_code, second.prev_snapshot_status,
                                                                  second.prev_snapshot_date))
        document = second.document
        self.assertEqual(D("400.00"), document["portfolio"]["day_pl"])
        self.assertEqual([D("84.00"), D("-50.00"), D("264.00"), D("-70.00"), D("222.00"), D("-50.00"), None],
                         [leg["day_pl"] for leg in document["legs"]])
        self.assertEqual(D("103.81"), document["underlyings"]["WMT"]["prev_eod_mid"])
        self.assertEqual("400.00", round_trip(document)["portfolio"]["day_pl"])
        self.assertEqual(2, len(self.ledger()), "the second day refetched: the 11-02 copies are past the TTL")

    def test_holiday_fetches_nothing_and_is_unavailable(self) -> None:
        run = book.load_book(self.market("holiday", datetime.datetime(2026, 11, 26, 15, 0, tzinfo=UTC)))
        self.assertEqual((20, []), (run.exit_code, self.ledger()))
        self.assertEqual(("holiday", "NYSE closed"), (run.document["session"], run.document["as_of_label"]))
        self.assertIsNone(run.document["portfolio"]["unrealized_pl_mid"], "no open group is ok: never a zero")
        self.assertIsNone(run.document["portfolio"]["net_delta"])
        for leg in run.document["legs"]:
            if leg["status"] == "open":
                self.assertEqual("unavailable", leg["mark_source"], leg["row_number"])

    def test_positions_file_errors_are_input_errors(self) -> None:
        path = os.path.join(self.root, "positions.csv")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("cost_basis_total", "cost_basis_totl", 1))
        with self.assertRaises(book.BookInputError) as ctx:
            book.load_book(self.market("regular", NOW))
        self.assertEqual("positions_invalid", ctx.exception.code)
        self.assertIn("did you mean", ctx.exception.message)
        self.assertEqual([], self.ledger(), "nothing is fetched for an unreadable book")

    def test_missing_positions_file_and_bad_calendar_are_input_errors(self) -> None:
        os.remove(os.path.join(self.root, "positions.csv"))
        with self.assertRaises(book.BookInputError) as ctx:
            book.load_book(self.market("regular", NOW))
        self.assertEqual("positions_missing", ctx.exception.code)
        with open(os.path.join(self.root, "positions.csv"), "w", encoding="utf-8") as handle:
            handle.write(HEADER + "\n")
        with open(os.path.join(self.root, "research", "calendar.toml"), "a", encoding="utf-8") as handle:
            handle.write("\nunknown_key = 1\n")
        with self.assertRaises(book.BookInputError) as ctx:
            book.load_book(self.market("regular", NOW))
        self.assertEqual("calendar_invalid", ctx.exception.code)

    def test_header_only_positions_is_an_empty_clean_book(self) -> None:
        with open(os.path.join(self.root, "positions.csv"), "w", encoding="utf-8") as handle:
            handle.write(HEADER + "\n")
        run = book.load_book(self.market("regular", NOW))
        self.assertEqual((0, [], []), (run.exit_code, run.symbols, run.document["legs"]))
        self.assertEqual((0, D("0.00"), D("0.00")), (run.document["portfolio"]["open_groups"],
                                                      run.document["portfolio"]["unrealized_pl_mid"],
                                                      run.document["portfolio"]["realized_pl_ytd"]))
        self.assertEqual([], self.ledger())

    def test_no_calendar_file_is_a_warning_not_an_error(self) -> None:
        os.remove(os.path.join(self.root, "research", "calendar.toml"))
        run = book.load_book(self.market("regular", NOW))
        self.assertEqual(0, run.exit_code)
        self.assertIsNone(run.calendar)
        self.assertIn(book.WARNING_NO_CALENDAR, run.document["warnings"])
        self.assertIsNone(run.document["config_sha"]["calendar_toml"])

    def inject_closure(self, day: datetime.date) -> None:
        with open(os.path.join(self.root, "research", "calendar.toml"), "a", encoding="utf-8") as handle:
            handle.write(CLOSURE_ENTRY % day.isoformat())

    def test_an_injected_full_closure_on_today_is_a_closed_day_for_the_book(self) -> None:
        # One rule for all six tools: the book classifies the session with the calendar's full closures, not
        # with open_market_data's calendar-blind session. An owner-injected closure on today's ET date is a
        # non-trading day for the book (no fetch, the closed-day label, nothing usable for rules, exit 20)
        # while the ET date (positions.csv, the DTE, the previous snapshot) and the request batch are unchanged.
        self.inject_closure(TODAY)
        run = book.load_book(self.market("regular", NOW))
        self.assertEqual("open", run.market.session.session, "open_market_data does not read the calendar")
        self.assertEqual(("holiday", False, calendar_events.CLOSURE_LABEL, TODAY, None),
                         (run.session.session, run.session.trading_day, run.session.holiday_name, run.session.et_date,
                          run.session.close_utc))
        self.assertEqual(datetime.datetime(2026, 11, 2, 21, 0, tzinfo=UTC), run.prior_close_utc, "Monday's 16:00 EST close")
        self.assertEqual([], self.ledger(), "no quote fetch on a closed day")
        self.assertEqual(SIX, run.symbols, "the request batch is unchanged")
        self.assertEqual(20, run.exit_code)
        self.assertEqual(("holiday", "NYSE closed"), (run.document["session"], run.document["as_of_label"]))
        self.assertEqual(["unavailable", "unavailable", "unavailable", "ok"],
                         [group["data_quality"] for group in run.document["groups"]], "the closed group has nothing to mark")
        self.assertEqual((0, 6), (run.document["data_quality"]["usable_legs"], run.document["data_quality"]["unavailable_legs"]))
        self.assertIsNone(run.document["portfolio"]["unrealized_pl_mid"], "no open group is ok: never a zero")
        self.assertIsNone(run.document["portfolio"]["net_delta"])
        for leg in run.document["legs"]:
            if leg["status"] == "open":
                self.assertEqual(("F", "missing", "unavailable", None),
                                 (leg["quote_grade"], leg["quote_status"], leg["mark_source"], leg["mark_mid"]), leg["row_number"])
        self.assertEqual((datetime.date(2026, 11, 2), "missing"), (run.prev_snapshot_date, run.prev_snapshot_status))
        call = leg_by_symbol(run.document, WMT_CALL)
        self.assertEqual((17, 13, datetime.date(2026, 11, 20)), (call["dte_calendar"], call["dte_trading"], call["expiry"]),
                         "today is still 2026-11-03: a closure on it takes nothing off the trading days ahead")

    def test_an_injected_full_closure_on_another_date_moves_only_the_trading_day_counts(self) -> None:
        # The session classification and the DTE arithmetic read the same closures: a closure on Tuesday
        # 2026-11-10 leaves today's open session and the goldens intact and takes one trading day off every
        # open leg that spans it (13 -> 12 for the November calls, 32 -> 31 for the December put).
        self.inject_closure(datetime.date(2026, 11, 10))
        run = book.load_book(self.market("regular", NOW))
        self.assertEqual((0, "open", True, "as of 14:31:12 ET"),
                         (run.exit_code, run.session.session, run.session.trading_day, run.document["as_of_label"]))
        self.assertEqual(1, len(self.ledger()), "one quotes request, as on any open day")
        self.assertEqual((D("4097.00"), D("4034.00"), 6),
                         (run.document["portfolio"]["unrealized_pl_mid"], run.document["portfolio"]["unrealized_pl_liq"],
                          run.document["data_quality"]["usable_legs"]))
        legs = {symbol: leg_by_symbol(run.document, symbol) for symbol in (WMT_CALL, NVDA_CALL, SPY_PUT)}
        self.assertEqual({WMT_CALL: (17, 12), NVDA_CALL: (17, 12), SPY_PUT: (45, 31)},
                         {symbol: (leg["dte_calendar"], leg["dte_trading"]) for symbol, leg in legs.items()})
        self.assertEqual(datetime.date(2026, 11, 2), run.prev_snapshot_date)


if __name__ == "__main__":
    unittest.main()
