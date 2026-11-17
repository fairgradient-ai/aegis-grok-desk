"""``aegis_desk.beta`` (roadmap 2.7, the owner's answer to open question 4): the OLS beta against SPY on split-adjusted
daily closes, its UNAVAILABLE cases (a 10:1 split in raw bars trips the 40 % guard, the split-adjusted bars fit), and the
day's beta file the book reads."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
from tests.floats import assert_close
# isort: on

import datetime
import json
import os
import random
import tempfile
import unittest
from typing import List, Tuple

from aegis_desk import beta as B
from aegis_desk import market_clock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGULAR = os.path.join(ROOT, "tests", "fixtures", "desk", "fixtures", "alpaca", "regular")
START, END = datetime.date(2025, 9, 28), datetime.date(2026, 11, 2)
TODAY = datetime.date(2026, 11, 3)
COMPUTED = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=datetime.timezone.utc)


def fixture_bars(symbol: str) -> List[Tuple[datetime.date, float]]:
    """The regular set's split-adjusted bars, as the tool reads them (ET date of the midnight stamp, close)."""
    name = os.path.join(REGULAR, "bars__%s__%s__%s__split.json" % (symbol, START.isoformat(), END.isoformat()))
    with open(name, encoding="utf-8") as handle:
        records = json.load(handle)["bars"][symbol]
    out = []
    for record in records:
        instant = datetime.datetime.strptime(record["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        out.append((market_clock.to_et(instant).date(), record["c"]))
    return out


def trading_days(count: int, end: datetime.date = END) -> List[datetime.date]:
    days: List[datetime.date] = []
    day = end
    while len(days) < count:
        if market_clock.is_trading_day(day):
            days.append(day)
        day -= datetime.timedelta(days=1)
    return days[::-1]


def walk(days: List[datetime.date], returns: List[float], first: float) -> List[Tuple[datetime.date, float]]:
    closes = [first]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return list(zip(days, closes))


class FixtureGoldensTest(unittest.TestCase):
    def test_the_regular_sets_fits(self) -> None:
        spy = fixture_bars("SPY")
        self.assertEqual((START + datetime.timedelta(days=1), END), (spy[0][0], spy[-1][0]))
        nvda = B.fit("NVDA", fixture_bars("NVDA"), spy)
        assert_close(self, 2.040109114663841, nvda.beta)
        assert_close(self, 0.4993795751074928, nvda.r2)
        self.assertEqual((B.FLAG_OK, 251, None), (nvda.r2_flag, nvda.returns, nvda.reason))
        wmt = B.fit("wmt", fixture_bars("WMT"), spy)
        assert_close(self, 0.5336704290370435, wmt.beta)
        assert_close(self, 0.23406719069678866, wmt.r2)
        self.assertEqual(("WMT", B.FLAG_LOW, 251), (wmt.symbol, wmt.r2_flag, wmt.returns), "r2 below 0.30 is low")
        self.assertEqual(B.BetaFit("SPY", 1.0, 1.0, B.FLAG_OK, 0, None), B.fit("SPY", [], []), "SPY's beta is 1")

    def test_the_window_is_the_last_252_closes(self) -> None:
        spy, nvda = fixture_bars("SPY"), fixture_bars("NVDA")
        self.assertEqual(276, len(spy))
        self.assertEqual(B.fit("NVDA", nvda, spy), B.fit("NVDA", nvda[-252:], spy[-252:]))
        self.assertNotEqual(B.fit("NVDA", nvda, spy), B.fit("NVDA", nvda[:-1], spy[:-1]))

    def test_an_exact_multiple_of_the_benchmark(self) -> None:
        days = trading_days(200)
        rng = random.Random(7)
        spy_returns = [rng.gauss(0.0, 0.01) for _ in days[1:]]
        spy = walk(days, spy_returns, 500.0)
        double = walk(days, [2.0 * value for value in spy_returns], 50.0)
        found = B.fit("XYZ", double, spy)
        assert_close(self, 2.0, found.beta)
        assert_close(self, 1.0, found.r2)
        self.assertEqual(199, found.returns)


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.days = trading_days(252)
        rng = random.Random(11)
        self.spy_returns = [rng.gauss(0.0004, 0.01) for _ in self.days[1:]]
        self.spy = walk(self.days, self.spy_returns, 600.0)
        own = [1.2 * value + rng.gauss(0.0, 0.01) for value in self.spy_returns]
        self.adjusted = walk(self.days, own, 90.0)  # split-adjusted: the 10:1 split is no return
        split_at = 180
        self.raw = [(day, close * 10.0 if index < split_at else close)
                    for index, (day, close) in enumerate(self.adjusted)]  # raw: ten times the price before the split

    def test_a_ten_for_one_split_trips_the_guard_in_raw_bars_and_fits_adjusted(self) -> None:
        raw = B.fit("SPLT", self.raw, self.spy)
        self.assertEqual((None, None, B.FLAG_UNAVAILABLE, 251, B.REASON_OUTLIER),
                         (raw.beta, raw.r2, raw.r2_flag, raw.returns, raw.reason))
        adjusted = B.fit("SPLT", self.adjusted, self.spy)
        self.assertIsNotNone(adjusted.beta)
        self.assertGreater(adjusted.beta, 1.0)
        self.assertLess(adjusted.beta, 1.4)
        self.assertEqual(B.FLAG_OK, adjusted.r2_flag)

    def test_the_guard_reads_both_series_and_its_limit_is_forty_percent(self) -> None:
        self.assertEqual(0.40, B.OUTLIER_LIMIT)
        jumpy_spy = list(self.spy)
        day, close = jumpy_spy[100]
        jumpy_spy[100] = (day, close * 1.41)
        self.assertEqual(B.REASON_OUTLIER, B.fit("SPLT", self.adjusted, jumpy_spy).reason)
        own = list(self.adjusted)
        own[100] = (own[100][0], own[99][1] * 1.39)
        self.assertNotEqual(B.REASON_OUTLIER, B.fit("SPLT", own, self.spy).reason, "a 39 % day is a market move")

    def test_too_few_returns_missing_bars_and_a_flat_benchmark(self) -> None:
        self.assertEqual(126, B.MIN_RETURNS)
        short = B.fit("SPLT", self.adjusted[-126:], self.spy)
        self.assertEqual((B.FLAG_UNAVAILABLE, 125, B.REASON_SHORT), (short.r2_flag, short.returns, short.reason))
        self.assertIsNotNone(B.fit("SPLT", self.adjusted[-127:], self.spy).beta, "126 returns fit")
        gaps = [bar for index, bar in enumerate(self.adjusted) if index % 2 == 0]
        self.assertEqual(B.REASON_SHORT, B.fit("SPLT", gaps, self.spy).reason, "only paired dates count")
        self.assertEqual(B.REASON_NO_BARS, B.fit("SPLT", [], self.spy).reason)
        self.assertEqual(B.REASON_SHORT, B.fit("SPLT", self.adjusted, []).reason, "no benchmark bars")
        flat = [(day, 600.0) for day, _close in self.spy]
        self.assertEqual(B.REASON_FLAT, B.fit("SPLT", self.adjusted, flat).reason)
        zero = [(day, None) for day, _close in self.adjusted]
        self.assertEqual(B.REASON_NO_BARS, B.fit("SPLT", zero, self.spy).reason, "a close without a price is no bar")


class BetaFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-beta-")
        self.dir = os.path.join(self._tmp.name, "beta")
        os.makedirs(self.dir)
        self.fits = [B.BetaFit("NVDA", 2.04, 0.5, B.FLAG_OK, 251), B.BetaFit("WMT", 0.53, 0.23, B.FLAG_LOW, 251),
                     B.BetaFit("XYZ", None, None, B.FLAG_UNAVAILABLE, 40, B.REASON_SHORT)]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, body: object, day: datetime.date = TODAY) -> None:
        with open(B.beta_path(self.dir, day), "w", encoding="utf-8") as handle:
            json.dump(body, handle)

    def test_the_document_round_trips(self) -> None:
        self.assertEqual(os.path.join(self.dir, "2026-11-03.json"), B.beta_path(self.dir, TODAY))
        body = B.document(TODAY, END, COMPUTED, self.fits)
        self.assertEqual({"schema": 1, "et_date": "2026-11-03", "bars_end": "2026-11-02",
                          "computed_utc": "2026-11-03T19:50:00+00:00"}, {k: body[k] for k in body if k != "betas"})
        self.write(body)
        self.assertEqual({fit.symbol: fit for fit in self.fits}, B.load_betas(self.dir, TODAY))

    def test_no_usable_file_is_none(self) -> None:
        self.assertIsNone(B.load_betas(self.dir, TODAY), "no file")
        self.write(B.document(TODAY, END, COMPUTED, self.fits), day=END)
        self.assertIsNone(B.load_betas(self.dir, TODAY), "yesterday's file is not today's")
        for body in ({**B.document(TODAY, END, COMPUTED, self.fits), "schema": 2},
                     {**B.document(TODAY, END, COMPUTED, self.fits), "et_date": "2026-11-02"},
                     {**B.document(TODAY, END, COMPUTED, self.fits), "betas": []}, ["not", "an", "object"]):
            with self.subTest(body=str(body)[:60]):
                self.write(body)
                self.assertIsNone(B.load_betas(self.dir, TODAY))
        with open(B.beta_path(self.dir, TODAY), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertIsNone(B.load_betas(self.dir, TODAY))

    def test_a_malformed_entry_is_left_out(self) -> None:
        body = B.document(TODAY, END, COMPUTED, self.fits)
        body["betas"]["NVDA"]["r2_flag"] = "great"
        body["betas"]["WMT"]["beta"] = "0.53"
        body["betas"]["XYZ"]["returns"] = True
        body["betas"]["spy"] = {"beta": 1.0, "r2": 1.0, "r2_flag": "ok", "returns": 0}
        self.write(body)
        self.assertEqual({"SPY": B.BetaFit("SPY", 1.0, 1.0, B.FLAG_OK, 0)}, B.load_betas(self.dir, TODAY))


if __name__ == "__main__":
    unittest.main()
