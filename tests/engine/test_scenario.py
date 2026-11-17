"""``aegis_desk.scenario`` (roadmap 2.8): the book re-priced on the grid with the book's own leg pricer; the stressed
book equal to the book at a zero move; re-pricing goldens recomputed independently on ``american.value``; the
exclusions; the event horizons (earnings and ex-dividend, a leg that expires first, a hedge through an event)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import os
import shutil
import tempfile
import unittest
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict

from aegis_desk import american, book, calendar_events, contract, market_clock, quote_cache, units
from aegis_desk import scenario as S

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
TODAY = datetime.date(2026, 11, 3)
WMT_GROUP, NVDA_GROUP, SPY_GROUP = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
D = Decimal


class BookCopy(unittest.TestCase):
    """One regular-set book per class, marked on a copy of the fixture desk."""

    book_run: book.BookRun
    _tmp: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="aegis-scenario-")
        root = os.path.join(os.path.realpath(cls._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, root)
        cls.root = root
        cls.book_run = cls.load("regular")

    @classmethod
    def load(cls, fixture_set: str, root: str = "") -> book.BookRun:
        market = quote_cache.open_market_data(root or cls.root, now=contract.parse_utc_instant(REGULAR_NOW), offline=True,
                                              fixture_set=fixture_set, timeout_s=5.0, user_agent="aegis-desk/test scenario")
        return book.load_book(market)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @property
    def document(self) -> Dict[str, Any]:
        return self.book_run.document

    def group(self, cell: S.Cell, group_id: str) -> S.GroupStress:
        return next(item for item in cell.groups if item.group_id == group_id)


class LegPriceTest(unittest.TestCase):
    def test_the_books_leg_pricer_is_the_american_model_of_record(self) -> None:
        cases = [("CALL", 104.23, 105.0, 0.047, 0.0412, 0.009, 0.413), ("PUT", 648.12, 620.0, 0.12, 0.0412, 0.0115, 0.246),
                 ("PUT", 90.0, 120.0, 0.5, 0.05, 0.0, 0.3)]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(american.value(*case).price, book.leg_price(*case))


class GridTest(unittest.TestCase):
    def test_the_proposed_grid(self) -> None:
        self.assertEqual(((-10, -5, -2, 0, 2, 5, 10), (-5, 0, 5)), (S.GRID_SPOT_PCT, S.GRID_VOL_POINTS))
        cells = S.grid()
        self.assertEqual(21, len(cells))
        self.assertEqual((S.Shock(-10, -5), S.Shock(10, 5)), (cells[0], cells[-1]))
        self.assertEqual("spot -10% vol +5", S.Shock(-10, 5).label())
        self.assertEqual(0.01, S.VOL_FLOOR)


class ZeroMoveTest(BookCopy):
    def test_the_stressed_book_equals_the_book_at_a_zero_move(self) -> None:
        result = S.stress(self.document, self.book_run.calendar)
        self.assertEqual((WMT_GROUP, NVDA_GROUP, SPY_GROUP), result.stressed, "the closed put is not in scope")
        self.assertEqual((), result.excluded)
        zero = result.cell(0, 0)
        self.assertEqual(D("0.00"), zero.change)
        self.assertEqual(self.document["portfolio"]["unrealized_pl_mid"], zero.stressed_pl_mid)
        groups = {group["group_id"]: group for group in self.document["groups"]}
        for item in zero.groups:
            self.assertEqual((D("0.00"), D("0.00"), D("0.00")), (item.change, item.stock, item.options), item.group_id)
            self.assertEqual(groups[item.group_id]["unrealized_pl_mid"], item.stressed_pl_mid)

    def test_an_event_horizon_at_a_zero_move_is_the_time_decay_only(self) -> None:
        horizon = S.event_horizon(self.book_run.calendar, "WMT", S.EVENT_EARNINGS, TODAY)
        zero = S.stress(self.document, self.book_run.calendar, horizon=horizon, symbol="WMT").cell(0, 0)
        item = self.group(zero, WMT_GROUP)
        self.assertEqual(D("0.00"), item.stock)
        self.assertGreater(item.options, D("0"), "the short call decays in fifteen days: the group gains")


class GoldensTest(BookCopy):
    """Each cell recomputed here on ``american.value`` with the leg's own inputs, independently of the module."""

    def expected(self, group_id: str, spot_pct: int, vol_points: int) -> Decimal:
        group = next(item for item in self.document["groups"] if item["group_id"] == group_id)
        spot = self.document["underlyings"][group["underlying"]]["mark_mid"]
        moved = spot * (1 + D(spot_pct) / 100)
        q = float(calendar_events.sourced_yield(self.book_run.calendar, group["underlying"]).value)
        r = float(self.document["rates"]["risk_free_rate"])
        stock, options = D(0), 0.0
        for leg in self.document["legs"]:
            if leg["row_number"] not in group["legs"] or leg["status"] != "open":
                continue
            size = leg["open_quantity"] * leg["multiplier"]
            if leg["occ"] is None:
                stock += size * (moved - spot)
                continue
            args = (leg["right"], float(leg["strike"]), leg["tau_years"], r, q)
            before = american.value(leg["right"], float(spot), *args[1:], leg["iv"]).price
            after = american.value(leg["right"], float(moved), *args[1:],
                                   max(leg["iv"] + vol_points * units.VOL_POINT, 0.01)).price
            options += float(size) * (after - before)
        return stock.quantize(D("0.01")) + D(repr(options)).quantize(D("0.01"), rounding=ROUND_HALF_EVEN)

    def test_every_cell_of_every_group(self) -> None:
        result = S.stress(self.document, self.book_run.calendar)
        for cell in result.cells:
            for item in cell.groups:
                with self.subTest(cell=cell.shock.label(), group=item.group_id):
                    self.assertEqual(self.expected(item.group_id, cell.shock.spot_pct, cell.shock.vol_points),
                                     item.change)
            self.assertEqual(sum(item.change for item in cell.groups), cell.change)

    def test_pinned_cells(self) -> None:
        result = S.stress(self.document, self.book_run.calendar)
        self.assertEqual(PINNED, {(cell.shock.spot_pct, cell.shock.vol_points): cell.change for cell in result.cells
                                  if (cell.shock.spot_pct, cell.shock.vol_points) in PINNED})

    def test_a_hedge_offsets_its_stock(self) -> None:
        down = self.group(S.stress(self.document, self.book_run.calendar).cell(-10, 0), SPY_GROUP)
        self.assertLess(down.stock, D("0"))
        self.assertGreater(down.options, D("0"), "the protective put gains when SPY falls")
        self.assertLess(abs(down.change), abs(down.stock))

    def test_the_vol_floor(self) -> None:
        result = S.stress(self.document, self.book_run.calendar, shocks=[S.Shock(0, -90)])
        self.assertIsNotNone(result.cells[0].change, "a 90-point cut is floored at 1 %, never below zero")


class ExclusionTest(BookCopy):
    def test_a_stale_book_excludes_every_group_and_sums_nothing(self) -> None:
        root = os.path.join(os.path.realpath(self._tmp.name), "stale-desk")  # its own cache: no regular quote served
        shutil.copytree(FIXTURE_DESK, root)
        stale = self.load("stale", root)
        result = S.stress(stale.document, stale.calendar)
        self.assertEqual((), result.stressed)
        self.assertEqual([(WMT_GROUP, S.REASON_UNAVAILABLE), (NVDA_GROUP, S.REASON_UNAVAILABLE),
                          (SPY_GROUP, S.REASON_UNAVAILABLE)], [(item.group_id, item.reason) for item in result.excluded])
        self.assertTrue(all(cell.change is None and cell.stressed_pl_mid is None for cell in result.cells))

    def test_an_unknown_yield_excludes_its_group_and_the_total(self) -> None:
        result = S.stress(self.document, None)
        self.assertEqual({S.REASON_DIVIDEND % symbol for symbol in ("WMT", "NVDA", "SPY")},
                         {item.reason for item in result.excluded})
        self.assertIsNone(result.cell(0, 0).change)

    def test_a_symbol_limits_the_scope(self) -> None:
        result = S.stress(self.document, self.book_run.calendar, symbol="spy")
        self.assertEqual((SPY_GROUP,), result.stressed)
        self.assertEqual((SPY_GROUP,), tuple(item.group_id for item in result.cell(5, 0).groups))
        self.assertEqual((), S.stress(self.document, self.book_run.calendar, symbol="AAPL").stressed)


class EventHorizonTest(BookCopy):
    def test_earnings_prices_the_close_one_trading_day_after(self) -> None:
        horizon = S.event_horizon(self.book_run.calendar, "wmt", S.EVENT_EARNINGS, TODAY)
        self.assertEqual(("WMT", datetime.date(2026, 11, 17), "pre", datetime.date(2026, 11, 18)),
                         (horizon.symbol, horizon.event_date, horizon.event_time, horizon.horizon_date))
        self.assertEqual(datetime.datetime(2026, 11, 18, 21, 0, tzinfo=datetime.timezone.utc), horizon.utc)
        result = S.stress(self.document, self.book_run.calendar, horizon=horizon, symbol="WMT")
        self.assertIn(S.NOTE_EARNINGS, result.notes)

    def test_the_day_after_a_friday_event_is_monday_and_an_early_close_is_honoured(self) -> None:
        cal = calendar_events.parse_calendar_text(
            'schema_version = 2\n[symbols.XYZ]\nearnings = { date = 2026-11-25, time = "post", status = "estimated", '
            'source = "test", checked_utc = 2026-11-01T12:00:00Z }\n')
        horizon = S.event_horizon(cal, "XYZ", S.EVENT_EARNINGS, TODAY)
        self.assertEqual(datetime.date(2026, 11, 27), horizon.horizon_date, "Thanksgiving is skipped")
        self.assertEqual(datetime.datetime(2026, 11, 27, 18, 0, tzinfo=datetime.timezone.utc), horizon.utc,
                         "the 13:00 ET early close")

    def test_an_ex_dividend_stress_never_cuts_spot_and_names_the_dividend_tier(self) -> None:
        horizon = S.event_horizon(self.book_run.calendar, "WMT", S.EVENT_EX_DIVIDEND, TODAY)
        self.assertEqual((datetime.date(2026, 11, 24), datetime.date(2026, 11, 25)),
                         (horizon.event_date, horizon.horizon_date))
        result = S.stress(self.document, self.book_run.calendar, horizon=horizon, symbol="WMT")
        self.assertEqual((S.NOTE_EXPIRES % "row 4 WMT261120C00105000", S.NOTE_NO_DIVIDEND_CUT, S.NOTE_DIVIDEND_TIER),
                         result.notes, "the WMT covered call holds a short call: the dividend tier is named")
        spy = S.stress(self.document, self.book_run.calendar, horizon=S.event_horizon(
            self.book_run.calendar, "SPY", S.EVENT_EX_DIVIDEND, TODAY), symbol="SPY")
        self.assertNotIn(S.NOTE_DIVIDEND_TIER, spy.notes, "a protective put holds no short call")
        zero = self.group(result.cell(0, 0), WMT_GROUP)
        self.assertEqual(D("0.00"), zero.stock, "a zero move leaves the stock where it is: no dividend cut")
        # the call expired out of the money at 104.23 < 105: worth nothing, so the group gains the call's whole mark
        call = next(leg for leg in self.document["legs"] if leg["row_number"] == 4)
        base = book.leg_price("CALL", float(self.document["underlyings"]["WMT"]["mark_mid"]), 105.0, call["tau_years"],
                              float(self.document["rates"]["risk_free_rate"]), 0.009, call["iv"])
        self.assertEqual(D(repr(-2 * 100 * (0.0 - base))).quantize(D("0.01")), zero.options)

    def test_a_leg_expiring_at_the_horizon_is_valued_at_intrinsic(self) -> None:
        horizon = S.event_horizon(self.book_run.calendar, "NVDA", S.EVENT_EARNINGS, TODAY)
        self.assertEqual(datetime.date(2026, 11, 20), horizon.horizon_date, "the NVDA call's own last trading day")
        result = S.stress(self.document, self.book_run.calendar, horizon=horizon, symbol="NVDA")
        self.assertIn(S.NOTE_EXPIRES % "row 6 NVDA261120C00150000", result.notes)
        up = self.group(result.cell(10, 0), NVDA_GROUP)
        spot = self.document["underlyings"]["NVDA"]["mark_mid"]
        moved = spot * D("1.10")
        self.assertEqual((200 * (moved - spot)).quantize(D("0.01")), up.stock)

    def test_an_event_the_calendar_cannot_date_is_unavailable(self) -> None:
        for cal, symbol, event, phrase in ((self.book_run.calendar, "SPY", S.EVENT_EARNINGS, "says SPY has no earnings"),
                                           (None, "WMT", S.EVENT_EARNINGS, "holds no earnings date"),
                                           (self.book_run.calendar, "AAPL", S.EVENT_EX_DIVIDEND, "holds no ex-dividend date")):
            with self.subTest(symbol=symbol, event=event):
                with self.assertRaises(S.StressUnavailable) as caught:
                    S.event_horizon(cal, symbol, event, TODAY)
                self.assertIn(phrase, caught.exception.reason)
        with self.assertRaises(S.StressUnavailable) as caught:
            S.event_horizon(self.book_run.calendar, "WMT", S.EVENT_EARNINGS, datetime.date(2026, 11, 18))
        self.assertIn("is past", caught.exception.reason)
        with self.assertRaises(ValueError):
            S.event_horizon(self.book_run.calendar, "WMT", "split", TODAY)


PINNED: Dict[Any, Decimal] = {(-10, 0): D("-7055.59"), (-5, 5): D("-3378.53"), (2, -5): D("1361.52"),
                               (10, 0): D("6990.26")}
"""The regular book's portfolio change in four cells (the goldens; each also recomputed above)."""


if __name__ == "__main__":
    unittest.main()
