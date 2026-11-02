"""Shared market dataclasses and vendor-value helpers (``aegis_desk.market_types``).

Every conversion is a definition, so the expectations are exact: ``Decimal`` built from the
JSON text (never from float arithmetic), epoch milliseconds and seconds to aware UTC datetimes
by the brief's formula, ISO dates, Tradier's object-or-list shape, and the two quote
properties ``mid`` and ``as_of``. The recorded AAPL quote and option shapes come from the Aegis
repository's Tradier fixtures (``services/api/tests/integrations/tradier/fixtures.py``).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import json
import unittest
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from aegis_desk import market_types as mt
from aegis_desk import state

UTC = timezone.utc

# 2026-11-03 19:31:12 UTC (14:31:12 ET, EST): the regular fixture set's quote stamp.
STAMP_MS = 1793734272000
STAMP = datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
# 2026-11-03 19:50:00 UTC: the regular set's --now instant (the clock's epoch seconds).
NOW_S = 1793735400
NOW = datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)

# Recorded Tradier shapes (Aegis fixtures.py, FairGradient): one equity quote, one option.
AAPL_QUOTE = {
    "symbol": "AAPL", "description": "Apple Inc", "exch": "Q", "type": "stock",
    "last": 185.50, "change": 1.25, "volume": 42345678, "open": 184.00, "high": 186.20,
    "low": 183.80, "close": None, "bid": 185.49, "ask": 185.51, "change_percentage": 0.68,
    "average_volume": 55000000, "last_volume": 100, "trade_date": 1718700000000,
    "prevclose": 184.25, "week_52_high": 198.23, "week_52_low": 164.08, "bidsize": 300,
    "bidexch": "Q", "bid_date": 1718700000000, "asksize": 200, "askexch": "Q",
    "ask_date": 1718700000000, "open_interest": 0, "underlying": None, "strike": None,
    "contract_size": None, "expiration_date": None, "expiration_type": None, "option_type": None,
}
AAPL_OPTION = {
    "symbol": "AAPL260117C00185000", "description": "AAPL Jan 17 2026 $185 Call", "exch": "Z",
    "type": "option", "last": 12.40, "change": 0.35, "volume": 1234, "open": 12.05,
    "high": 12.75, "low": 11.90, "close": None, "bid": 12.35, "ask": 12.45,
    "underlying": "AAPL", "strike": 185.0, "change_percentage": 2.91, "average_volume": 0,
    "last_volume": 0, "trade_date": 1718700000000, "prevclose": 12.05, "week_52_high": 0.0,
    "week_52_low": 0.0, "bidsize": 50, "bidexch": "Z", "bid_date": 1718700000000,
    "asksize": 40, "askexch": "Z", "ask_date": 1718700000000, "open_interest": 4567,
    "contract_size": 100, "expiration_date": "2026-01-17", "expiration_type": "standard",
    "option_type": "call", "root_symbol": "AAPL",
    "greeks": {"delta": 0.5210, "gamma": 0.0185, "theta": -0.0412, "vega": 0.2250,
               "rho": 0.1832, "phi": -0.1610, "bid_iv": 0.2650, "mid_iv": 0.2680,
               "ask_iv": 0.2710, "smv_vol": 0.2680, "updated_at": "2026-06-06 16:00:00"},
}


class ToDecimalTest(unittest.TestCase):
    def test_none_and_empty_are_none(self) -> None:
        self.assertIsNone(mt.to_decimal(None))
        self.assertIsNone(mt.to_decimal(""))
        self.assertIsNone(mt.to_decimal("   "))

    def test_float_goes_through_its_text_never_binary(self) -> None:
        self.assertEqual(Decimal("185.5"), mt.to_decimal(185.50))
        self.assertEqual("185.5", str(mt.to_decimal(185.50)))
        self.assertEqual("0.68", str(mt.to_decimal(0.68)))
        self.assertEqual("3.35", str(mt.to_decimal(3.35)))
        self.assertEqual("648.14", str(mt.to_decimal(648.14)))
        self.assertNotEqual(Decimal(0.68), mt.to_decimal(0.68))  # the binary float would differ

    def test_int_and_str_and_decimal(self) -> None:
        self.assertEqual(Decimal("42"), mt.to_decimal(42))
        self.assertEqual(Decimal("3.42"), mt.to_decimal("3.42"))
        self.assertEqual(Decimal("3.42"), mt.to_decimal(" 3.42 "))
        self.assertEqual(Decimal("-0.50"), mt.to_decimal("-0.50"))
        self.assertEqual(Decimal("9.80"), mt.to_decimal(Decimal("9.80")))
        self.assertIsInstance(mt.to_decimal(42), Decimal)

    def test_json_round_trip_keeps_the_text(self) -> None:
        parsed = json.loads('{"bid": 104.20, "ask": 104.26, "volume": 9800000}')
        self.assertEqual("104.2", str(mt.to_decimal(parsed["bid"])))
        self.assertEqual(Decimal("104.20"), mt.to_decimal(parsed["bid"]))
        self.assertEqual(Decimal("104.26"), mt.to_decimal(parsed["ask"]))

    def test_garbage_is_an_error_not_a_quiet_none(self) -> None:
        for bad in (True, False, "abc", "NaN", "Infinity", "-inf", float("nan"), float("inf"), [1], {"a": 1}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                mt.to_decimal(bad, "bid")
        with self.assertRaises(mt.MarketTypesError) as ctx:
            mt.to_decimal("abc", "bid")
        self.assertIn("bid", str(ctx.exception))
        self.assertEqual("bid", ctx.exception.field)
        self.assertIsInstance(ctx.exception, ValueError)


class ToIntTest(unittest.TestCase):
    def test_values(self) -> None:
        self.assertIsNone(mt.to_int(None))
        self.assertIsNone(mt.to_int(""))
        self.assertEqual(300, mt.to_int(300))
        self.assertEqual(3, mt.to_int(3.0))
        self.assertEqual(12, mt.to_int("12"))
        self.assertEqual(-1, mt.to_int("-1"))
        self.assertEqual(0, mt.to_int(0))  # a present zero stays zero
        self.assertIsInstance(mt.to_int(3.0), int)

    def test_garbage(self) -> None:
        for bad in (True, 3.5, "3.5", "abc", [1]):
            with self.assertRaises(mt.MarketTypesError, msg=repr(bad)):
                mt.to_int(bad, "volume")


class EpochTest(unittest.TestCase):
    def test_epoch_ms_matches_the_briefs_formula(self) -> None:
        result = mt.from_epoch_ms(STAMP_MS)
        self.assertEqual(STAMP, result)
        self.assertEqual(datetime.fromtimestamp(STAMP_MS / 1000, tz=UTC), result)
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(timedelta(0), result.utcoffset())

    def test_epoch_ms_accepts_numeric_strings_and_floats(self) -> None:
        self.assertEqual(STAMP, mt.from_epoch_ms(str(STAMP_MS)))
        self.assertEqual(STAMP, mt.from_epoch_ms(float(STAMP_MS)))

    def test_epoch_ms_sub_second_part_is_exact_to_the_microsecond(self) -> None:
        result = mt.from_epoch_ms(STAMP_MS + 123)
        self.assertEqual(STAMP + timedelta(milliseconds=123), result)
        self.assertEqual(123000, result.microsecond)

    def test_missing_zero_and_negative_are_undated(self) -> None:
        self.assertIsNone(mt.from_epoch_ms(None))
        self.assertIsNone(mt.from_epoch_ms(""))
        self.assertIsNone(mt.from_epoch_ms(0))
        self.assertIsNone(mt.from_epoch_ms(-5))
        self.assertIsNone(mt.from_epoch_s(None))
        self.assertIsNone(mt.from_epoch_s(0))

    def test_epoch_s(self) -> None:
        self.assertEqual(NOW, mt.from_epoch_s(NOW_S))
        self.assertEqual(NOW, mt.from_epoch_s(str(NOW_S)))
        self.assertEqual(datetime.fromtimestamp(NOW_S, tz=UTC), mt.from_epoch_s(NOW_S))

    def test_recorded_aapl_stamp(self) -> None:
        self.assertEqual(datetime(2024, 6, 18, 8, 40, 0, tzinfo=UTC), mt.from_epoch_ms(1718700000000))

    def test_garbage(self) -> None:
        for bad in (True, "abc", [1], {"a": 1}):
            with self.assertRaises(mt.MarketTypesError, msg=repr(bad)):
                mt.from_epoch_ms(bad, "bid_date")
            with self.assertRaises(mt.MarketTypesError, msg=repr(bad)):
                mt.from_epoch_s(bad, "timestamp")


class ParseIsoDateAndTimeTest(unittest.TestCase):
    def test_dates(self) -> None:
        self.assertEqual(date(2026, 11, 20), mt.parse_iso_date("2026-11-20"))
        self.assertEqual(date(2026, 11, 20), mt.parse_iso_date(date(2026, 11, 20)))
        self.assertIsNone(mt.parse_iso_date(None))
        self.assertIsNone(mt.parse_iso_date(""))
        for bad in ("2026-13-01", "20261120", "2026-11-20T00:00:00", 20261120, True):
            with self.assertRaises(mt.MarketTypesError, msg=repr(bad)):
                mt.parse_iso_date(bad, "expiration_date")

    def test_clock_times(self) -> None:
        self.assertEqual(time(9, 30), mt.parse_hhmm("09:30"))
        self.assertEqual(time(16, 0), mt.parse_hhmm("16:00"))
        self.assertEqual(time(13, 0, 0), mt.parse_hhmm("13:00:00"))
        self.assertIsNone(mt.parse_hhmm(None))
        self.assertIsNone(mt.parse_hhmm(""))
        for bad in ("9:30", "24:00", "09:60", "0930", 930):
            with self.assertRaises(mt.MarketTypesError, msg=repr(bad)):
                mt.parse_hhmm(bad, "start")


class OneOrManyTest(unittest.TestCase):
    def test_shapes(self) -> None:
        self.assertEqual([], mt.one_or_many(None))
        self.assertEqual([], mt.one_or_many("null"))
        self.assertEqual([], mt.one_or_many(""))
        self.assertEqual([{"symbol": "SPY"}], mt.one_or_many({"symbol": "SPY"}))
        self.assertEqual([{"a": 1}, {"b": 2}], mt.one_or_many([{"a": 1}, {"b": 2}]))
        self.assertEqual(["WMT261120C00105000"], mt.one_or_many("WMT261120C00105000"))
        self.assertEqual(["2026-11-06", "2026-11-13"], mt.one_or_many(["2026-11-06", "2026-11-13"]))
        self.assertEqual([105.0], mt.one_or_many(105.0))
        items = [1, 2]
        self.assertIsNot(items, mt.one_or_many(items))  # a copy, never the caller's list
        self.assertEqual([1, 2], mt.one_or_many((1, 2)))


class QuoteTest(unittest.TestCase):
    def test_equity_quote_from_recorded_shape(self) -> None:
        quote = mt.Quote.from_vendor(AAPL_QUOTE)
        self.assertEqual("AAPL", quote.symbol)
        self.assertEqual("Apple Inc", quote.description)
        self.assertEqual("Q", quote.exch)
        self.assertEqual("stock", quote.type)
        self.assertEqual(Decimal("185.5"), quote.last)
        self.assertEqual(Decimal("185.49"), quote.bid)
        self.assertEqual(Decimal("185.51"), quote.ask)
        self.assertEqual(Decimal("1.25"), quote.change)
        self.assertEqual(Decimal("0.68"), quote.change_percentage)
        self.assertEqual(Decimal("184.25"), quote.prevclose)
        self.assertEqual(Decimal("198.23"), quote.week_52_high)
        self.assertEqual(Decimal("164.08"), quote.week_52_low)
        self.assertIsNone(quote.close)
        self.assertEqual(42345678, quote.volume)
        self.assertEqual(55000000, quote.average_volume)
        self.assertEqual(100, quote.last_volume)
        self.assertEqual(300, quote.bidsize)
        self.assertEqual(200, quote.asksize)
        self.assertEqual("Q", quote.bidexch)
        self.assertEqual("Q", quote.askexch)
        stamp = datetime(2024, 6, 18, 8, 40, 0, tzinfo=UTC)
        self.assertEqual(stamp, quote.trade_date)
        self.assertEqual(stamp, quote.bid_date)
        self.assertEqual(stamp, quote.ask_date)
        self.assertEqual(0, quote.open_interest)  # present as 0 in the recorded shape: kept
        for name in ("underlying", "strike", "contract_size", "expiration_date", "expiration_type",
                     "option_type", "root_symbol", "greeks", "root_symbols"):
            self.assertIsNone(getattr_quote(quote, name), name)
        self.assertFalse(quote.is_option)
        self.assertEqual(Decimal("185.50"), quote.mid)
        self.assertEqual(stamp, quote.as_of)
        self.assertIs(AAPL_QUOTE, quote.raw)
        for value in (quote.last, quote.bid, quote.ask, quote.prevclose):
            self.assertIsInstance(value, Decimal)
        for value in (quote.volume, quote.bidsize, quote.asksize):
            self.assertIsInstance(value, int)

    def test_option_quote_from_recorded_shape(self) -> None:
        quote = mt.Quote.from_vendor(AAPL_OPTION)
        self.assertEqual("option", quote.type)
        self.assertTrue(quote.is_option)
        self.assertEqual("AAPL", quote.underlying)
        self.assertEqual(Decimal("185.0"), quote.strike)
        self.assertEqual(Decimal("185"), quote.strike)
        self.assertIsInstance(quote.strike, Decimal)
        self.assertEqual(4567, quote.open_interest)
        self.assertEqual(100, quote.contract_size)
        self.assertEqual(date(2026, 1, 17), quote.expiration_date)
        self.assertEqual("standard", quote.expiration_type)
        self.assertEqual("call", quote.option_type)
        self.assertEqual("AAPL", quote.root_symbol)
        self.assertEqual(0.5210, quote.greeks["delta"])
        self.assertEqual(Decimal("0.0"), quote.week_52_high)  # present in the vendor text: kept as sent
        self.assertEqual(Decimal("12.40"), quote.mid)

    def test_greeks_null_or_absent_are_none(self) -> None:
        with_null = dict(AAPL_OPTION, greeks=None)
        without = {k: v for k, v in AAPL_OPTION.items() if k != "greeks"}
        self.assertIsNone(mt.Quote.from_vendor(with_null).greeks)
        self.assertIsNone(mt.Quote.from_vendor(without).greeks)

    def test_minimal_quote_has_none_everywhere(self) -> None:
        quote = mt.Quote.from_vendor({"symbol": "SPY"})
        self.assertEqual("SPY", quote.symbol)
        for field in dataclasses.fields(mt.Quote):
            if field.name in ("symbol", "raw"):
                continue
            self.assertIsNone(getattr_quote(quote, field.name), field.name)
        self.assertIsNone(quote.mid)
        self.assertIsNone(quote.as_of)
        self.assertFalse(quote.is_option)

    def test_null_numbers_are_none_never_zero(self) -> None:
        quote = mt.Quote.from_vendor({"symbol": "SPY", "volume": None, "bid": None, "bidsize": None,
                                      "bid_date": None, "ask_date": 0, "trade_date": 0})
        self.assertIsNone(quote.volume)
        self.assertIsNone(quote.bid)
        self.assertIsNone(quote.bidsize)
        self.assertIsNone(quote.bid_date)
        self.assertIsNone(quote.ask_date)  # a vendor zero stamp is "undated"
        self.assertIsNone(quote.trade_date)
        self.assertIsNone(quote.as_of)

    def test_symbol_is_required(self) -> None:
        with self.assertRaises(mt.MarketTypesError):
            mt.Quote.from_vendor({"bid": 1.0})
        with self.assertRaises(mt.MarketTypesError):
            mt.Quote.from_vendor({"symbol": ""})
        with self.assertRaises(mt.MarketTypesError):
            mt.Quote.from_vendor("SPY")  # type: ignore[arg-type]

    def test_malformed_field_names_the_field(self) -> None:
        with self.assertRaises(mt.MarketTypesError) as ctx:
            mt.Quote.from_vendor({"symbol": "SPY", "bid": "abc"})
        self.assertIn("bid", str(ctx.exception))
        with self.assertRaises(mt.MarketTypesError) as ctx:
            mt.Quote.from_vendor({"symbol": "SPY", "expiration_date": "17-01-2026"})
        self.assertIn("expiration_date", str(ctx.exception))
        with self.assertRaises(mt.MarketTypesError) as ctx:
            mt.Quote.from_vendor({"symbol": "SPY", "greeks": "none"})
        self.assertIn("greeks", str(ctx.exception))

    def test_mid_rules(self) -> None:
        def mid(bid, ask):
            return mt.Quote(symbol="X", bid=bid, ask=ask).mid

        self.assertEqual(Decimal("3.425"), mid(Decimal("3.35"), Decimal("3.50")))
        self.assertEqual(Decimal("104.23"), mid(Decimal("104.20"), Decimal("104.26")))
        self.assertEqual(Decimal("1.75"), mid(Decimal("0"), Decimal("3.50")))  # one side zero: still a mid
        self.assertEqual(Decimal("1.75"), mid(Decimal("3.50"), Decimal("0.00")))
        self.assertIsNone(mid(Decimal("0"), Decimal("0")))  # both zero: no market
        self.assertIsNone(mid(Decimal("0.00"), Decimal("0")))
        self.assertIsNone(mid(None, Decimal("3.50")))
        self.assertIsNone(mid(Decimal("3.35"), None))
        self.assertIsNone(mid(None, None))
        self.assertIsInstance(mid(Decimal("3.35"), Decimal("3.50")), Decimal)
        # Exactness: the mean of two cents-quoted sides is exact in Decimal (never 3.4249999...).
        self.assertEqual("3.425", str(mid(Decimal("3.35"), Decimal("3.50"))))

    def test_as_of_rules(self) -> None:
        earlier = datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
        later = datetime(2026, 11, 3, 19, 31, 40, tzinfo=UTC)
        self.assertEqual(earlier, mt.Quote(symbol="X", bid_date=later, ask_date=earlier).as_of)
        self.assertEqual(earlier, mt.Quote(symbol="X", bid_date=earlier, ask_date=later).as_of)
        self.assertEqual(later, mt.Quote(symbol="X", bid_date=later, ask_date=None).as_of)
        self.assertEqual(later, mt.Quote(symbol="X", bid_date=None, ask_date=later).as_of)
        self.assertIsNone(mt.Quote(symbol="X").as_of)
        # trade_date never stands in for the as-of (the last trade is never a mark).
        self.assertIsNone(mt.Quote(symbol="X", trade_date=later).as_of)

    def test_quote_is_frozen_and_keyword_constructible(self) -> None:
        quote = mt.Quote(symbol="WMT", bid=Decimal("104.20"), ask=Decimal("104.26"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            quote.bid = Decimal("1")  # type: ignore[misc]
        self.assertEqual({}, quote.raw)

    def test_field_names_follow_tradiers_json(self) -> None:
        names = [field.name for field in dataclasses.fields(mt.Quote)]
        for name in ("symbol", "description", "exch", "type", "last", "change", "volume", "open", "high",
                     "low", "close", "bid", "ask", "change_percentage", "average_volume", "last_volume",
                     "trade_date", "prevclose", "week_52_high", "week_52_low", "bidsize", "bidexch",
                     "bid_date", "asksize", "askexch", "ask_date", "root_symbols", "underlying", "strike",
                     "open_interest", "contract_size", "expiration_date", "expiration_type", "option_type",
                     "root_symbol", "greeks", "raw"):
            self.assertIn(name, names)

    def test_quote_serialises_with_the_desk_json_convention(self) -> None:
        quote = mt.Quote.from_vendor(AAPL_OPTION)
        parsed = json.loads(state.json_dumps({"quote": quote}))
        self.assertEqual("12.35", parsed["quote"]["bid"])
        self.assertEqual("2024-06-18T08:40:00+00:00", parsed["quote"]["bid_date"])
        self.assertEqual("2026-01-17", parsed["quote"]["expiration_date"])
        self.assertEqual("185.0", parsed["quote"]["strike"])


def getattr_quote(quote: "mt.Quote", name: str) -> object:
    """Field access by name without reflection on modules (the AST gate forbids that)."""
    return dataclasses.asdict(quote)[name] if name != "raw" else quote.raw


class BarTest(unittest.TestCase):
    def test_from_vendor(self) -> None:
        bar = mt.Bar.from_vendor({"date": "2026-11-02", "open": 644.20, "high": 646.75, "low": 642.90,
                                  "close": 645.90, "volume": 55600000})
        self.assertEqual(date(2026, 11, 2), bar.date)
        self.assertEqual(Decimal("644.20"), bar.open)
        self.assertEqual(Decimal("646.75"), bar.high)
        self.assertEqual(Decimal("642.90"), bar.low)
        self.assertEqual(Decimal("645.90"), bar.close)
        self.assertEqual("645.9", str(bar.close))
        self.assertEqual(55600000, bar.volume)
        self.assertIsInstance(bar.volume, int)

    def test_missing_fields_are_none_and_a_missing_date_is_an_error(self) -> None:
        bar = mt.Bar.from_vendor({"date": "2026-11-02"})
        self.assertIsNone(bar.open)
        self.assertIsNone(bar.volume)
        with self.assertRaises(mt.MarketTypesError):
            mt.Bar.from_vendor({"open": 1.0})
        with self.assertRaises(mt.MarketTypesError):
            mt.Bar.from_vendor({"date": "2026-11-02", "close": "n/a"})


class SessionWindowAndCalendarDayTest(unittest.TestCase):
    def test_window(self) -> None:
        window = mt.SessionWindow.from_vendor({"start": "09:30", "end": "16:00"})
        self.assertEqual(time(9, 30), window.start)
        self.assertEqual(time(16, 0), window.end)
        self.assertIsNone(mt.SessionWindow.from_vendor(None))
        self.assertIsNone(mt.SessionWindow.from_vendor({}))
        with self.assertRaises(mt.MarketTypesError):
            mt.SessionWindow.from_vendor({"start": "09:30"})
        with self.assertRaises(mt.MarketTypesError):
            mt.SessionWindow.from_vendor({"start": "9:30", "end": "16:00"})

    def test_open_day(self) -> None:
        day = mt.CalendarDay.from_vendor({
            "date": "2026-11-27", "status": "open", "description": "Market is open from 09:30 to 13:00",
            "premarket": {"start": "07:00", "end": "09:24"}, "open": {"start": "09:30", "end": "13:00"},
            "postmarket": {"start": "13:00", "end": "17:00"}})
        self.assertEqual(date(2026, 11, 27), day.date)
        self.assertEqual("open", day.status)
        self.assertEqual("Market is open from 09:30 to 13:00", day.description)
        self.assertEqual(mt.SessionWindow(time(7, 0), time(9, 24)), day.premarket)
        self.assertEqual(mt.SessionWindow(time(9, 30), time(13, 0)), day.open)
        self.assertEqual(mt.SessionWindow(time(13, 0), time(17, 0)), day.postmarket)

    def test_closed_day_has_no_sessions(self) -> None:
        day = mt.CalendarDay.from_vendor({"date": "2026-11-26", "status": "closed", "description": "Market is closed"})
        self.assertEqual("closed", day.status)
        self.assertIsNone(day.premarket)
        self.assertIsNone(day.open)
        self.assertIsNone(day.postmarket)

    def test_bad_status_or_date(self) -> None:
        with self.assertRaises(mt.MarketTypesError):
            mt.CalendarDay.from_vendor({"date": "2026-11-26", "status": "half", "description": ""})
        with self.assertRaises(mt.MarketTypesError):
            mt.CalendarDay.from_vendor({"status": "open", "description": ""})


class ClockInfoTest(unittest.TestCase):
    PAYLOAD = {"clock": {"date": "2026-11-03", "description": "Market is open from 09:30 to 16:00",
                         "state": "open", "timestamp": NOW_S, "next_change": "16:00", "next_state": "postmarket"}}

    def test_from_full_payload_and_inner_object(self) -> None:
        for raw in (self.PAYLOAD, self.PAYLOAD["clock"]):
            clock = mt.ClockInfo.from_vendor(raw)
            self.assertEqual(date(2026, 11, 3), clock.date)
            self.assertEqual("open", clock.state)
            self.assertEqual("Market is open from 09:30 to 16:00", clock.description)
            self.assertEqual(NOW, clock.timestamp_utc)  # epoch SECONDS on this endpoint
            self.assertEqual("16:00", clock.next_change)
            self.assertEqual("postmarket", clock.next_state)

    def test_optional_fields(self) -> None:
        clock = mt.ClockInfo.from_vendor({"date": "2026-11-26", "state": "closed", "description": "Market is closed"})
        self.assertIsNone(clock.next_change)
        self.assertIsNone(clock.next_state)
        self.assertIsNone(clock.timestamp_utc)

    def test_required_fields(self) -> None:
        with self.assertRaises(mt.MarketTypesError):
            mt.ClockInfo.from_vendor({"state": "open", "description": "x"})
        with self.assertRaises(mt.MarketTypesError):
            mt.ClockInfo.from_vendor({"date": "2026-11-03", "description": "x"})
        with self.assertRaises(mt.MarketTypesError):
            mt.ClockInfo.from_vendor({"clock": None})


class RequestReceiptTest(unittest.TestCase):
    def test_fields_and_serialisation(self) -> None:
        receipt = mt.RequestReceipt(path="/v1/markets/quotes", symbols_count=3, receipt_utc=NOW, status="ok",
                                    http_status=200, ratelimit_allowed=60, ratelimit_used=7,
                                    ratelimit_available=53, ratelimit_expiry_utc=NOW + timedelta(seconds=42),
                                    elapsed_ms=180)
        names = [field.name for field in dataclasses.fields(mt.RequestReceipt)]
        self.assertEqual(["path", "symbols_count", "receipt_utc", "status", "http_status", "ratelimit_allowed",
                          "ratelimit_used", "ratelimit_available", "ratelimit_expiry_utc", "elapsed_ms"], names)
        parsed = json.loads(state.json_dumps(receipt))
        self.assertEqual("2026-11-03T19:50:00+00:00", parsed["receipt_utc"])
        self.assertEqual(53, parsed["ratelimit_available"])
        self.assertEqual("ok", parsed["status"])

    def test_failure_receipt_has_optional_fields_none(self) -> None:
        receipt = mt.RequestReceipt(path="/v1/markets/clock", symbols_count=0, receipt_utc=NOW, status="timeout")
        self.assertIsNone(receipt.http_status)
        self.assertIsNone(receipt.ratelimit_available)
        self.assertIsNone(receipt.ratelimit_expiry_utc)
        self.assertIsNone(receipt.elapsed_ms)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            receipt.status = "ok"  # type: ignore[misc]


class ModuleHygieneTest(unittest.TestCase):
    def test_quote_types_vocabulary(self) -> None:
        self.assertEqual(("stock", "etf", "option", "index", "mutual_fund"), mt.QUOTE_TYPES)

    def test_all_exports_resolve(self) -> None:
        for name in mt.__all__:
            self.assertTrue(hasattr(mt, name), name)
        for name in ("Quote", "Bar", "CalendarDay", "SessionWindow", "ClockInfo", "RequestReceipt",
                     "MarketTypesError", "to_decimal", "to_int", "from_epoch_ms", "from_epoch_s",
                     "parse_iso_date", "parse_hhmm", "one_or_many"):
            self.assertIn(name, mt.__all__)


if __name__ == "__main__":
    unittest.main()
