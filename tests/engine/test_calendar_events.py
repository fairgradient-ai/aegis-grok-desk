"""Research calendar (``aegis_desk.calendar_events``): strict TOML reading, every status rule,
the dividend view, settlement lookups, closures in ``market_clock``'s ``extra_holidays`` shape and
the freshness lint. The fixture-file test skips while F0's calendar is absent."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import hashlib
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional

from aegis_desk import calendar_events as ce
from aegis_desk import market_clock, toml_compat

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_CALENDAR = os.path.join(ROOT, "tests", "fixtures", "desk", "research", "calendar.toml")
NOW = datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)
CHECKED = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)

BRIEF_TEXT = """\
schema_version = 1
[symbols.WMT]
earnings = { date = 2026-11-17, time = "pre", status = "confirmed", source = "https://example.invalid/wmt", checked_utc = 2026-11-01T12:00:00Z }
ex_dividend = { date = 2026-12-11, amount = 0.2350, status = "estimated", source = "owner-entered", checked_utc = 2026-11-01T12:00:00Z }
dividend_yield = 0.0090
[symbols.NVDA]
earnings = { date = 2026-11-19, time = "post", status = "confirmed", source = "owner-entered", checked_utc = 2026-11-01T12:00:00Z }
ex_dividend = { status = "none", source = "owner-entered: no dividend", checked_utc = 2026-11-01T12:00:00Z }
[settlement]
am_settled_roots = ["SPX", "NDX", "RUT", "VIX", "DJX"]
[[settlement.values]]
root = "SPX"
date = 2026-11-20
value = 6523.17
source = "owner-entered"
checked_utc = 2026-11-23T12:00:00Z
[[closures]]
date = 2026-12-24
kind = "early_close"
source = "NYSE hours calendar"
"""

MINIMAL = "schema_version = 1\n"


def symbol_table(symbol: str, body: str) -> str:
    return "schema_version = 1\n[symbols.%s]\n%s\n" % (symbol, body)


def entry(**fields: str) -> str:
    """An inline table from already-rendered TOML values."""
    return "{ " + ", ".join("%s = %s" % (key, value) for key, value in fields.items()) + " }"


CHECKED_TOML = "2026-11-01T12:00:00Z"
SRC = '"owner-entered"'


def earnings(**overrides: Any) -> str:
    fields = {"date": "2026-11-17", "time": '"pre"', "status": '"confirmed"', "source": SRC,
              "checked_utc": CHECKED_TOML}
    for key, value in overrides.items():
        if value is None:
            fields.pop(key, None)
        else:
            fields[key] = value
    return "earnings = " + entry(**fields)


def ex_dividend(**overrides: Any) -> str:
    fields = {"date": "2026-11-24", "amount": "0.2350", "status": '"confirmed"', "source": SRC,
              "checked_utc": CHECKED_TOML}
    for key, value in overrides.items():
        if value is None:
            fields.pop(key, None)
        else:
            fields[key] = value
    return "ex_dividend = " + entry(**fields)


def parse(text: str, path: str = "<text>") -> ce.CalendarFile:
    return ce.parse_calendar_text(text, path)


class ParseBriefExampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = parse(BRIEF_TEXT, "research/calendar.toml")

    def test_schema_path_and_sha(self) -> None:
        self.assertEqual(1, self.cal.schema_version)
        self.assertEqual("research/calendar.toml", self.cal.path)
        self.assertEqual(hashlib.sha256(BRIEF_TEXT.encode("utf-8")).hexdigest(), self.cal.sha256)
        self.assertIsInstance(self.cal.warnings, tuple)

    def test_symbol_entries_are_typed_per_the_addendum(self) -> None:
        wmt = self.cal.events_for("WMT")
        self.assertTrue(wmt.listed)
        self.assertEqual("WMT", wmt.symbol)
        self.assertEqual(date(2026, 11, 17), wmt.earnings.date)
        self.assertEqual("pre", wmt.earnings.time)
        self.assertEqual("confirmed", wmt.earnings.status)
        self.assertEqual("https://example.invalid/wmt", wmt.earnings.source)
        self.assertEqual(CHECKED, wmt.earnings.checked_utc)
        self.assertIs(UTC, wmt.earnings.checked_utc.tzinfo)
        self.assertEqual(date(2026, 12, 11), wmt.ex_dividend.date)
        self.assertEqual(Decimal("0.2350").as_tuple(), wmt.ex_dividend.amount.as_tuple(), "digits as written")
        self.assertEqual("estimated", wmt.ex_dividend.status)
        self.assertEqual(Decimal("0.0090"), wmt.dividend_yield)
        self.assertIsInstance(wmt.dividend_yield, Decimal)
        nvda = self.cal.events_for("NVDA")
        self.assertEqual("post", nvda.earnings.time)
        self.assertEqual("none", nvda.ex_dividend.status)
        self.assertIsNone(nvda.ex_dividend.amount)
        self.assertIsNone(nvda.ex_dividend.date)
        self.assertIsNone(nvda.dividend_yield, "absent is unknown, never zero")
        self.assertEqual(["NVDA", "WMT"], sorted(self.cal.symbols))

    def test_settlement_and_closures(self) -> None:
        self.assertEqual(("SPX", "NDX", "RUT", "VIX", "DJX"), self.cal.am_settled_roots)
        self.assertEqual(1, len(self.cal.settlement_values))
        value = self.cal.settlement_values[0]
        self.assertEqual(("SPX", date(2026, 11, 20), Decimal("6523.17"), "owner-entered",
                          datetime(2026, 11, 23, 12, tzinfo=UTC)),
                         (value.root, value.date, value.value, value.source, value.checked_utc))
        self.assertEqual((ce.Closure(date(2026, 12, 24), "early_close", "NYSE hours calendar"),), self.cal.closures)

    def test_dataclasses_are_frozen(self) -> None:
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.cal.schema_version = 2  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.cal.events_for("WMT").earnings.time = "post"  # type: ignore[misc]


class FixtureCalendarTest(unittest.TestCase):
    """The main fixture root's calendar as F0 writes it (addendum, F0 section)."""

    def setUp(self) -> None:
        if not os.path.isfile(FIXTURE_CALENDAR):
            self.skipTest("F0 pending: tests/fixtures/desk/research/calendar.toml is missing")
        self.cal = ce.load_calendar(FIXTURE_CALENDAR)

    def test_contents(self) -> None:
        self.assertEqual(FIXTURE_CALENDAR, self.cal.path)
        self.assertEqual(["NVDA", "SPY", "WMT"], sorted(self.cal.symbols))
        wmt = self.cal.events_for("WMT")
        self.assertEqual((date(2026, 11, 17), "pre", "confirmed"), (wmt.earnings.date, wmt.earnings.time, wmt.earnings.status))
        self.assertEqual((date(2026, 11, 24), Decimal("0.2350"), "confirmed"),
                         (wmt.ex_dividend.date, wmt.ex_dividend.amount, wmt.ex_dividend.status))
        self.assertEqual(Decimal("0.0090"), wmt.dividend_yield)
        nvda = self.cal.events_for("NVDA")
        self.assertEqual((date(2026, 11, 19), "post"), (nvda.earnings.date, nvda.earnings.time))
        self.assertEqual((date(2026, 12, 3), Decimal("0.01")), (nvda.ex_dividend.date, nvda.ex_dividend.amount))
        self.assertEqual(Decimal("0.0003"), nvda.dividend_yield)
        spy = self.cal.events_for("SPY")
        self.assertEqual("none", spy.earnings.status)
        self.assertIsNone(spy.earnings.date)
        self.assertEqual((date(2026, 12, 18), Decimal("1.85"), "estimated"),
                         (spy.ex_dividend.date, spy.ex_dividend.amount, spy.ex_dividend.status))
        self.assertEqual(Decimal("0.0115"), spy.dividend_yield)
        for events in (wmt, nvda, spy):
            for item in (events.earnings, events.ex_dividend):
                self.assertEqual(CHECKED, item.checked_utc)
                self.assertTrue(item.source)
        self.assertEqual(("SPX", "NDX", "RUT", "VIX", "DJX", "XSP"), self.cal.am_settled_roots)
        self.assertEqual((), self.cal.settlement_values)
        self.assertEqual(1, len(self.cal.closures))
        self.assertEqual((date(2026, 11, 27), "early_close"), (self.cal.closures[0].date, self.cal.closures[0].kind))
        with open(FIXTURE_CALENDAR, "rb") as handle:
            self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), self.cal.sha256)

    def test_early_close_duplicate_of_the_embedded_table_is_not_injected_as_a_closure(self) -> None:
        self.assertEqual({}, self.cal.extra_closures(), "an early close is a session, not a closure")
        self.assertEqual({date(2026, 11, 27): self.cal.closures[0].source}, self.cal.early_closes())
        self.assertEqual((), self.cal.warnings, "the embedded NYSE table knows this early close")
        extra = self.cal.extra_closures()
        self.assertTrue(market_clock.is_trading_day(date(2026, 11, 27), extra))
        self.assertTrue(market_clock.is_early_close(date(2026, 11, 27), extra))
        info = market_clock.session_at(datetime(2026, 11, 27, 15, 0, tzinfo=UTC), extra_holidays=extra)
        self.assertEqual("early_close", info.session)

    def test_fixture_is_clean_at_the_journey_instant_and_stale_at_max_age_one(self) -> None:
        held = ["WMT", "NVDA", "SPY"]
        self.assertEqual([], ce.freshness(self.cal, NOW, 7, held, ["WMT", "NVDA"]))
        findings = ce.freshness(self.cal, NOW, 1, held, ["WMT", "NVDA"])
        self.assertEqual({"STALE"}, {f.kind for f in findings})
        self.assertEqual(6, len(findings), "three symbols, two dated entries each")

    def test_cross_check_with_tomllib_when_available(self) -> None:
        if toml_compat.tomllib_module() is None:
            self.skipTest("tomllib is not available on this interpreter")
        with open(FIXTURE_CALENDAR, encoding="utf-8") as handle:
            toml_compat.cross_check(handle.read())


class StrictReadingTest(unittest.TestCase):
    def fails(self, text: str, *fragments: str) -> ce.CalendarError:
        with self.assertRaises(ce.CalendarError) as caught:
            parse(text, "cal.toml")
        message = str(caught.exception)
        self.assertTrue(message.startswith("cal.toml: "), message)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return caught.exception

    def test_error_is_a_value_error_with_path_and_reason(self) -> None:
        exc = self.fails("schema_version = 2\n", "schema_version")
        self.assertIsInstance(exc, ValueError)
        self.assertEqual("cal.toml", exc.path)
        self.assertEqual("cal.toml: " + exc.reason, str(exc))

    def test_minimal_document_parses_to_an_empty_calendar(self) -> None:
        cal = parse(MINIMAL)
        self.assertEqual({}, cal.symbols)
        self.assertEqual((), cal.am_settled_roots)
        self.assertEqual((), cal.settlement_values)
        self.assertEqual((), cal.closures)
        self.assertEqual({}, cal.extra_closures())
        self.assertFalse(cal.events_for("WMT").listed)

    def test_schema_version_rules(self) -> None:
        self.fails("[symbols.WMT]\ndividend_yield = 0.01\n", "schema_version")
        self.fails("schema_version = 2\n", "schema_version", "1")
        self.fails('schema_version = "1"\n', "schema_version")
        self.fails("schema_version = true\n", "schema_version")

    def test_unknown_keys_anywhere_are_errors(self) -> None:
        self.fails(MINIMAL + "machine_role = 'x'\n", "unknown key 'machine_role'")
        self.fails(symbol_table("WMT", "dividend = 0.01"), "unknown key 'dividend'", "[symbols.WMT]")
        self.fails(symbol_table("WMT", earnings(when='"pre"')), "unknown key 'when'", "earnings")
        self.fails(symbol_table("WMT", ex_dividend(pay_date="2026-12-01")), "unknown key 'pay_date'", "ex_dividend")
        self.fails(MINIMAL + "[settlement]\nroots = []\n", "unknown key 'roots'", "[settlement]")
        self.fails(MINIMAL + "[settlement]\n[[settlement.values]]\nroot = \"SPX\"\ndate = 2026-11-20\nvalue = 1.0\n"
                   "source = \"x\"\nchecked_utc = 2026-11-23T12:00:00Z\nnote = \"x\"\n", "unknown key 'note'")
        self.fails(MINIMAL + "[[closures]]\ndate = 2026-12-24\nkind = \"full\"\nsource = \"x\"\nname = \"x\"\n",
                   "unknown key 'name'", "closures")

    def test_symbols_section_shape(self) -> None:
        self.fails(MINIMAL + "symbols = 3\n", "symbols", "table")
        self.fails(MINIMAL + "[symbols]\nWMT = 3\n", "[symbols.WMT]", "table")
        self.fails(symbol_table("wmt", "dividend_yield = 0.01"), "wmt", "upper-case")
        self.fails(symbol_table('"WM T"', "dividend_yield = 0.01"), "WM T")
        self.fails(symbol_table("BRK.B", "dividend_yield = 0"), "unknown key 'B'", "[symbols.BRK]")  # a dotted key, not a symbol
        cal = parse(symbol_table('"BRK.B"', "dividend_yield = 0"))  # a dotted symbol is quoted in the header
        self.assertEqual(Decimal("0"), cal.events_for("brk.b").dividend_yield)

    def test_status_and_time_choices(self) -> None:
        self.fails(symbol_table("WMT", earnings(status='"likely"')), "status", "confirmed, estimated, unknown, none")
        self.fails(symbol_table("WMT", earnings(status=None)), "missing key 'status'", "earnings")
        self.fails(symbol_table("WMT", earnings(time='"amc"')), "time", "pre, post, unknown")
        self.fails(symbol_table("WMT", earnings(time="1")), "time")
        cal = parse(symbol_table("WMT", earnings(time=None)))
        self.assertEqual("unknown", cal.events_for("WMT").earnings.time, "an absent time is unknown")

    def test_source_and_checked_utc_are_required_and_typed(self) -> None:
        self.fails(symbol_table("WMT", earnings(source=None)), "missing key 'source'")
        self.fails(symbol_table("WMT", earnings(source='""')), "source", "non-empty")
        self.fails(symbol_table("WMT", earnings(source="3")), "source")
        self.fails(symbol_table("WMT", earnings(checked_utc=None)), "missing key 'checked_utc'")
        self.fails(symbol_table("WMT", earnings(checked_utc="2026-11-01T12:00:00")), "checked_utc", "offset")
        self.fails(symbol_table("WMT", earnings(checked_utc="2026-11-01")), "checked_utc", "offset")
        self.fails(symbol_table("WMT", earnings(checked_utc='"2026-11-01T12:00:00Z"')), "checked_utc")
        cal = parse(symbol_table("WMT", earnings(checked_utc="2026-11-01T08:00:00-04:00")))
        self.assertEqual(CHECKED, cal.events_for("WMT").earnings.checked_utc, "normalised to UTC")
        self.assertIs(UTC, cal.events_for("WMT").earnings.checked_utc.tzinfo)

    def test_dates_are_local_dates(self) -> None:
        self.fails(symbol_table("WMT", earnings(date="2026-11-17T09:00:00Z")), "date", "local date")
        self.fails(symbol_table("WMT", earnings(date='"2026-11-17"')), "date", "local date")
        self.fails(symbol_table("WMT", ex_dividend(date="2026-11-24T00:00:00")), "date", "local date")

    def test_confirmed_and_estimated_need_a_date_and_dividends_an_amount(self) -> None:
        self.fails(symbol_table("WMT", earnings(date=None)), "earnings", "date", "confirmed")
        self.fails(symbol_table("WMT", earnings(date=None, status='"estimated"')), "earnings", "date", "estimated")
        self.fails(symbol_table("WMT", ex_dividend(date=None)), "ex_dividend", "date")
        self.fails(symbol_table("WMT", ex_dividend(amount=None)), "ex_dividend", "amount")
        self.fails(symbol_table("WMT", ex_dividend(amount="0")), "amount", "> 0", "none")
        self.fails(symbol_table("WMT", ex_dividend(amount="-0.25")), "amount", "> 0")
        self.fails(symbol_table("WMT", ex_dividend(amount='"0.25"')), "amount")
        self.fails(symbol_table("WMT", ex_dividend(amount="true")), "amount")
        cal = parse(symbol_table("WMT", ex_dividend(amount="1")))
        self.assertEqual(Decimal(1), cal.events_for("WMT").ex_dividend.amount)
        self.assertIsInstance(cal.events_for("WMT").ex_dividend.amount, Decimal)

    def test_none_takes_no_date_time_or_amount(self) -> None:
        self.fails(symbol_table("WMT", earnings(status='"none"')), "earnings", "none", "date")
        self.fails(symbol_table("WMT", earnings(status='"none"', date=None)), "earnings", "none", "time")
        self.fails(symbol_table("WMT", ex_dividend(status='"none"')), "ex_dividend", "none", "date")
        self.fails(symbol_table("WMT", ex_dividend(status='"none"', date=None)), "ex_dividend", "none", "amount")
        cal = parse(symbol_table("WMT", earnings(status='"none"', date=None, time=None) + "\n"
                                 + ex_dividend(status='"none"', date=None, amount=None)))
        events = cal.events_for("WMT")
        self.assertEqual(("none", None, "unknown"), (events.earnings.status, events.earnings.date, events.earnings.time))
        self.assertEqual(("none", None, None), (events.ex_dividend.status, events.ex_dividend.date, events.ex_dividend.amount))

    def test_unknown_may_carry_a_date_but_never_an_amount(self) -> None:
        self.fails(symbol_table("WMT", ex_dividend(status='"unknown"')), "ex_dividend", "unknown", "amount")
        cal = parse(symbol_table("WMT", earnings(status='"unknown"', date=None, time=None) + "\n"
                                 + ex_dividend(status='"unknown"', amount=None)))
        events = cal.events_for("WMT")
        self.assertEqual(("unknown", None), (events.earnings.status, events.earnings.date))
        self.assertEqual(("unknown", date(2026, 11, 24), None),
                         (events.ex_dividend.status, events.ex_dividend.date, events.ex_dividend.amount))

    def test_dividend_yield_is_a_fraction(self) -> None:
        self.fails(symbol_table("WMT", "dividend_yield = 1.5"), "dividend_yield", "fraction")
        self.fails(symbol_table("WMT", "dividend_yield = 1"), "dividend_yield", "fraction")
        self.fails(symbol_table("WMT", "dividend_yield = -0.01"), "dividend_yield")
        self.fails(symbol_table("WMT", 'dividend_yield = "0.01"'), "dividend_yield")
        self.fails(symbol_table("WMT", "dividend_yield = true"), "dividend_yield")
        cal = parse(symbol_table("WMT", "dividend_yield = 0.0090"))
        self.assertEqual(Decimal("0.0090").as_tuple(), cal.events_for("WMT").dividend_yield.as_tuple())
        self.assertIsInstance(parse(symbol_table("WMT", "dividend_yield = 0")).events_for("WMT").dividend_yield, Decimal)

    def test_entries_must_be_inline_tables(self) -> None:
        self.fails(symbol_table("WMT", 'earnings = "2026-11-17"'), "earnings", "table")
        self.fails(symbol_table("WMT", "ex_dividend = [1, 2]"), "ex_dividend", "table")

    def test_settlement_rules(self) -> None:
        self.fails(MINIMAL + "settlement = 1\n", "[settlement]", "table")
        self.fails(MINIMAL + "[settlement]\nam_settled_roots = \"SPX\"\n", "am_settled_roots", "list")
        self.fails(MINIMAL + "[settlement]\nam_settled_roots = [\"SPX\", 1]\n", "am_settled_roots")
        self.fails(MINIMAL + "[settlement]\nam_settled_roots = [\"spx\"]\n", "am_settled_roots", "upper-case")
        self.fails(MINIMAL + "[settlement]\nam_settled_roots = [\"SPX\", \"SPX\"]\n", "am_settled_roots", "twice")
        base = MINIMAL + "[settlement]\nam_settled_roots = [\"SPX\"]\n"
        good = "[[settlement.values]]\nroot = \"SPX\"\ndate = 2026-11-20\nvalue = 6523.17\nsource = \"x\"\nchecked_utc = 2026-11-23T12:00:00Z\n"
        self.fails(base + good.replace("value = 6523.17\n", ""), "missing key 'value'", "settlement.values")
        self.fails(base + good.replace("6523.17", "0"), "value", "> 0")
        self.fails(base + good.replace("6523.17", "\"6523.17\""), "value")
        self.fails(base + good.replace("\"SPX\"", "\"spx\""), "root", "upper-case")
        self.fails(base + good.replace("date = 2026-11-20", "date = 2026-11-20T09:30:00Z"), "date", "local date")
        self.fails(base + good.replace("2026-11-23T12:00:00Z", "2026-11-23T12:00:00"), "checked_utc", "offset")
        self.fails(base + good + good, "SPX", "2026-11-20", "twice")
        self.fails(MINIMAL + "[settlement]\nvalues = 3\n", "values", "array of tables")
        cal = parse(base + good)
        self.assertEqual(Decimal("6523.17"), cal.settlement_value("SPX", date(2026, 11, 20)))

    def test_closure_rules(self) -> None:
        good = "[[closures]]\ndate = 2026-12-24\nkind = \"early_close\"\nsource = \"NYSE\"\n"
        self.fails(MINIMAL + good.replace("\"early_close\"", "\"half_day\""), "kind", "full, early_close")
        self.fails(MINIMAL + good.replace("source = \"NYSE\"\n", ""), "missing key 'source'", "closures")
        self.fails(MINIMAL + good.replace("date = 2026-12-24\n", ""), "missing key 'date'", "closures")
        self.fails(MINIMAL + good.replace("2026-12-24", "2026-12-24T13:00:00Z"), "date", "local date")
        self.fails(MINIMAL + good + good, "2026-12-24", "twice")
        self.fails(MINIMAL + good.replace("2026-12-24", "2026-12-26"), "2026-12-26", "Saturday")
        self.fails(MINIMAL + good.replace("2026-12-24", "2026-12-27"), "2026-12-27", "Sunday")
        self.fails(MINIMAL + "closures = 3\n", "closures", "array of tables")
        self.fails(MINIMAL + "[closures]\ndate = 2026-12-24\n", "closures", "array of tables")

    def test_invalid_toml_names_the_file_and_position(self) -> None:
        exc = self.fails("schema_version = 1\n[symbols.WMT\n", "TOML", "line 2")
        self.assertIsInstance(exc, ValueError)

    def test_text_must_be_a_string(self) -> None:
        with self.assertRaises(ce.CalendarError):
            ce.parse_calendar_text(b"schema_version = 1\n", "x")  # type: ignore[arg-type]


class LoadingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-calendar-")
        self.dir = os.path.realpath(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_load_reads_the_file_and_hashes_its_bytes(self) -> None:
        path = self.write("calendar.toml", BRIEF_TEXT)
        cal = ce.load_calendar(path)
        self.assertEqual(path, cal.path)
        self.assertEqual(hashlib.sha256(BRIEF_TEXT.encode("utf-8")).hexdigest(), cal.sha256)
        self.assertEqual(Decimal("0.0090"), cal.dividend_yield("WMT"))

    def test_missing_file_is_a_calendar_error_naming_the_path(self) -> None:
        path = os.path.join(self.dir, "nope.toml")
        with self.assertRaises(ce.CalendarError) as caught:
            ce.load_calendar(path)
        self.assertIn(path, str(caught.exception))
        self.assertIsNone(ce.load_calendar_if_present(path))

    def test_load_if_present_still_raises_on_a_bad_file(self) -> None:
        path = self.write("calendar.toml", "schema_version = 7\n")
        with self.assertRaises(ce.CalendarError):
            ce.load_calendar_if_present(path)
        self.assertIsNotNone(ce.load_calendar_if_present(self.write("ok.toml", MINIMAL)))

    def test_not_utf8_is_a_calendar_error(self) -> None:
        path = os.path.join(self.dir, "calendar.toml")
        with open(path, "wb") as handle:
            handle.write(b"schema_version = 1\n# \xff\xfe\n")
        with self.assertRaises(ce.CalendarError) as caught:
            ce.load_calendar(path)
        self.assertIn("UTF-8", str(caught.exception))

    def test_bom_is_accepted(self) -> None:
        path = os.path.join(self.dir, "calendar.toml")
        with open(path, "wb") as handle:
            handle.write(b"\xef\xbb\xbf" + MINIMAL.encode("utf-8"))
        self.assertEqual(1, ce.load_calendar(path).schema_version)


class LookupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = parse(BRIEF_TEXT)

    def test_events_for_normalises_and_reports_unlisted(self) -> None:
        self.assertTrue(self.cal.events_for(" wmt ").listed)
        missing = self.cal.events_for("AAPL")
        self.assertEqual(ce.SymbolEvents("AAPL", False, None, None, None), missing)
        self.assertEqual(missing, ce.events_for(self.cal, "aapl"))
        self.assertEqual(ce.SymbolEvents("AAPL", False, None, None, None), ce.events_for(None, "AAPL"))

    def test_dividend_yield(self) -> None:
        self.assertEqual(Decimal("0.0090"), self.cal.dividend_yield("wmt"))
        self.assertIsNone(self.cal.dividend_yield("NVDA"))
        self.assertIsNone(self.cal.dividend_yield("AAPL"))
        self.assertEqual(Decimal("0.0090"), ce.dividend_yield(self.cal, "WMT"))
        self.assertIsNone(ce.dividend_yield(None, "WMT"))

    def test_is_am_settled_matches_the_root_exactly(self) -> None:
        self.assertTrue(self.cal.is_am_settled("SPX"))
        self.assertTrue(self.cal.is_am_settled(" spx "))
        self.assertFalse(self.cal.is_am_settled("SPXW"), "weeklys settle on the close")
        self.assertFalse(self.cal.is_am_settled("SPY"))
        self.assertTrue(ce.is_am_settled(self.cal, "RUT"))
        self.assertFalse(ce.is_am_settled(None, "SPX"), "no calendar: the brief's default is pm")

    def test_settlement_value_lookup(self) -> None:
        self.assertEqual(Decimal("6523.17"), self.cal.settlement_value("spx", date(2026, 11, 20)))
        self.assertIsNone(self.cal.settlement_value("SPX", date(2026, 11, 19)))
        self.assertIsNone(self.cal.settlement_value("RUT", date(2026, 11, 20)))
        self.assertEqual(Decimal("6523.17"), ce.settlement_value(self.cal, "SPX", date(2026, 11, 20)))
        self.assertIsNone(ce.settlement_value(None, "SPX", date(2026, 11, 20)))
        with self.assertRaises(ValueError):
            self.cal.settlement_value("SPX", datetime(2026, 11, 20, tzinfo=UTC))  # type: ignore[arg-type]


class DividendBeforeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = parse(BRIEF_TEXT)  # WMT ex-dividend 2026-12-11 estimated 0.2350; NVDA none

    def view(self, symbol: str, expiry: date, as_of: Any) -> ce.DividendView:
        return self.cal.dividend_before(symbol, expiry, as_of)

    def test_in_window_returns_the_entry(self) -> None:
        view = self.view("WMT", date(2026, 12, 18), date(2026, 11, 3))
        self.assertEqual(ce.DividendView(Decimal("0.2350"), date(2026, 12, 11), "estimated"), view)
        self.assertEqual(view, self.view("wmt", date(2026, 12, 11), date(2026, 11, 3)), "expiry day itself counts")
        self.assertEqual(view, ce.dividend_before(self.cal, "WMT", date(2026, 12, 18), date(2026, 11, 3)))

    def test_after_expiry_means_no_dividend_before_expiry_at_the_entry_confidence(self) -> None:
        self.assertEqual(ce.DividendView(None, None, "estimated"), self.view("WMT", date(2026, 11, 20), date(2026, 11, 3)))

    def test_past_ex_date_makes_the_next_dividend_unknown(self) -> None:
        self.assertEqual(ce.DividendView(None, None, "unknown"), self.view("WMT", date(2027, 1, 15), date(2026, 12, 11)),
                         "the stock goes ex today: the listed entry is spent")
        self.assertEqual(ce.DividendView(None, None, "unknown"), self.view("WMT", date(2027, 1, 15), date(2026, 12, 12)))

    def test_none_and_unknown_and_unlisted(self) -> None:
        self.assertEqual(ce.DividendView(None, None, "none"), self.view("NVDA", date(2026, 11, 20), date(2026, 11, 3)))
        self.assertEqual(ce.DividendView(None, None, "unknown"), self.view("AAPL", date(2026, 11, 20), date(2026, 11, 3)))
        cal = parse(symbol_table("WMT", ex_dividend(status='"unknown"', amount=None)))
        self.assertEqual(ce.DividendView(None, None, "unknown"), cal.dividend_before("WMT", date(2026, 12, 18), date(2026, 11, 3)))
        cal = parse(symbol_table("WMT", "dividend_yield = 0.01"))
        self.assertEqual(ce.DividendView(None, None, "unknown"), cal.dividend_before("WMT", date(2026, 12, 18), date(2026, 11, 3)),
                         "a listed symbol without an ex_dividend entry is unknown, never zero")
        self.assertEqual(ce.DividendView(None, None, "unknown"), ce.dividend_before(None, "WMT", date(2026, 12, 18), date(2026, 11, 3)))

    def test_as_of_may_be_an_aware_instant_read_on_its_et_date(self) -> None:
        late_evening_et = datetime(2026, 12, 11, 2, 30, tzinfo=UTC)  # 21:30 ET on 2026-12-10
        self.assertEqual(ce.DividendView(Decimal("0.2350"), date(2026, 12, 11), "estimated"),
                         self.view("WMT", date(2026, 12, 18), late_evening_et))
        with self.assertRaises(ValueError):
            self.view("WMT", date(2026, 12, 18), datetime(2026, 12, 10, 21, 30))
        with self.assertRaises(ValueError):
            self.view("WMT", "2026-12-18", date(2026, 11, 3))  # type: ignore[arg-type]


class ExtraClosuresTest(unittest.TestCase):
    def test_full_closures_feed_market_clock_in_its_mapping_shape(self) -> None:
        cal = parse(MINIMAL + "[[closures]]\ndate = 2026-11-02\nkind = \"full\"\nsource = \"NYSE notice\"\n"
                    "[[closures]]\ndate = 2026-11-27\nkind = \"early_close\"\nsource = \"NYSE hours\"\n")
        extra = cal.extra_closures()
        self.assertEqual({date(2026, 11, 2): ce.CLOSURE_LABEL}, extra)
        self.assertEqual("calendar closure", ce.CLOSURE_LABEL)
        self.assertEqual(extra, ce.extra_closures(cal))
        self.assertFalse(market_clock.is_trading_day(date(2026, 11, 2), extra))
        self.assertEqual("calendar closure", market_clock.holiday_name(date(2026, 11, 2), extra))
        self.assertEqual("holiday", market_clock.session_at(datetime(2026, 11, 2, 15, 0, tzinfo=UTC), extra_holidays=extra).session)
        self.assertEqual(date(2026, 10, 30), market_clock.previous_trading_day(date(2026, 11, 3), extra))
        self.assertEqual(date(2026, 10, 30), market_clock.last_trading_day(date(2026, 11, 2), False, extra))
        self.assertTrue(market_clock.is_trading_day(date(2026, 11, 27), extra), "the early close stays a session")
        self.assertEqual({date(2026, 11, 27): "NYSE hours"}, cal.early_closes())

    def test_full_closure_on_an_embedded_holiday_keeps_the_embedded_name(self) -> None:
        cal = parse(MINIMAL + "[[closures]]\ndate = 2026-11-26\nkind = \"full\"\nsource = \"NYSE\"\n")
        self.assertEqual({date(2026, 11, 26): market_clock.NAME_THANKSGIVING}, cal.extra_closures())

    def test_none_and_empty(self) -> None:
        self.assertEqual({}, ce.extra_closures(None))
        self.assertEqual({}, parse(MINIMAL).extra_closures())
        self.assertEqual({}, parse(MINIMAL).early_closes())

    def test_unknown_early_close_is_a_load_warning_not_an_injection(self) -> None:
        cal = parse(MINIMAL + "[[closures]]\ndate = 2026-12-31\nkind = \"early_close\"\nsource = \"rumour\"\n")
        self.assertEqual({}, cal.extra_closures())
        self.assertEqual(1, len(cal.warnings))
        self.assertIn("2026-12-31", cal.warnings[0])
        self.assertIn("early close", cal.warnings[0].lower())
        self.assertEqual((), parse(MINIMAL + "[[closures]]\ndate = 2026-12-24\nkind = \"early_close\"\nsource = \"NYSE\"\n").warnings)


class FreshnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = parse(BRIEF_TEXT)  # checked 2026-11-01; WMT earnings 11-17, ex-div 12-11; NVDA earnings 11-19, no dividend

    def kinds(self, findings: List[ce.Finding]) -> List[str]:
        return [f.kind for f in findings]

    def test_finding_shape(self) -> None:
        finding = ce.Finding("STALE", "WMT", "message")
        self.assertEqual(("STALE", "WMT", "message"), (finding.kind, finding.symbol, finding.message))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            finding.kind = "PAST"  # type: ignore[misc]
        self.assertEqual(("STALE", "MISSING", "UNKNOWN_DIVIDEND", "PAST"), ce.FINDING_KINDS)

    def test_clean_calendar(self) -> None:
        self.assertEqual([], ce.freshness(self.cal, NOW, 7, ["WMT", "NVDA"], ["WMT"]))
        self.assertEqual([], ce.freshness(self.cal, NOW))
        self.assertEqual(7, ce.DEFAULT_MAX_AGE_DAYS)

    def test_stale_by_checked_utc(self) -> None:
        findings = ce.freshness(self.cal, NOW, 1, ["WMT"], ["WMT"])
        self.assertEqual(["STALE"] * 4, self.kinds(findings))
        self.assertEqual(["NVDA", "NVDA", "WMT", "WMT"], [f.symbol for f in findings], "sorted by symbol, entries in order")
        self.assertIn("earnings", findings[0].message)
        self.assertIn("ex_dividend", findings[1].message)
        self.assertIn("2026-11-01T12:00:00+00:00", findings[2].message)
        self.assertIn("2 day", findings[2].message)
        self.assertIn("limit 1", findings[2].message)
        at_limit = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
        self.assertEqual([], ce.freshness(self.cal, at_limit, 7), "exactly max_age_days old is not stale")
        self.assertEqual(["STALE"] * 4, self.kinds(ce.freshness(self.cal, at_limit + timedelta(seconds=1), 7)))
        self.assertEqual(["STALE"] * 4, self.kinds(ce.freshness(self.cal, NOW, 0)))

    def test_missing_held_symbols(self) -> None:
        findings = ce.freshness(self.cal, NOW, 7, ["WMT", "aapl", "MSFT"], ["AAPL"])
        self.assertEqual([("MISSING", "AAPL"), ("MISSING", "MSFT")], [(f.kind, f.symbol) for f in findings])
        self.assertIn("never zero", findings[0].message)
        self.assertEqual(findings, ce.freshness(self.cal, NOW, 7, "WMT AAPL MSFT".split(), ["AAPL"]))

    def test_unknown_dividend_scope(self) -> None:
        cal = parse(symbol_table("WMT", earnings()) + "\n[symbols.NVDA]\n" + ex_dividend(status='"unknown"', amount=None, date=None)
                    + "\n[symbols.SPY]\n" + ex_dividend(status='"none"', date=None, amount=None))
        held = ["WMT", "NVDA", "SPY"]
        findings = ce.freshness(cal, NOW, 7, held, held)
        self.assertEqual([("UNKNOWN_DIVIDEND", "NVDA"), ("UNKNOWN_DIVIDEND", "WMT")], [(f.kind, f.symbol) for f in findings])
        self.assertIn("unknown", findings[0].message)
        self.assertIn("never zero", findings[0].message)
        self.assertEqual([("UNKNOWN_DIVIDEND", "NVDA")],
                         [(f.kind, f.symbol) for f in ce.freshness(cal, NOW, 7, held, ["NVDA"])])
        self.assertEqual([], ce.freshness(cal, NOW, 7, held, []), "no short calls: nothing to grade")
        self.assertEqual([("UNKNOWN_DIVIDEND", "NVDA"), ("UNKNOWN_DIVIDEND", "WMT")],
                         [(f.kind, f.symbol) for f in ce.freshness(cal, NOW, 7, held)],
                         "the default scope is every held symbol (conservative)")
        self.assertEqual([], ce.freshness(cal, NOW, 7, ["SPY"]), "a confirmed none is known")
        self.assertEqual([], ce.freshness(cal, NOW, 7, [], ["WMT"]), "the scope is a subset of what is held")

    def test_past_events(self) -> None:
        later = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)  # ET date 2026-11-20
        findings = ce.freshness(self.cal, later, 60)
        self.assertEqual([("PAST", "NVDA"), ("PAST", "WMT")], [(f.kind, f.symbol) for f in findings])
        self.assertIn("2026-11-19", findings[0].message)
        self.assertIn("earnings", findings[0].message)
        same_day = datetime(2026, 11, 19, 23, 30, tzinfo=UTC)  # still 2026-11-19 in ET
        self.assertEqual([("PAST", "WMT")], [(f.kind, f.symbol) for f in ce.freshness(self.cal, same_day, 60)],
                         "an event dated today is not past")
        stale_and_past = ce.freshness(self.cal, later, 7)
        self.assertEqual(["PAST", "STALE", "PAST", "STALE"], self.kinds(stale_and_past),
                         "a past entry is reported once (PAST), the others are stale")
        december = datetime(2026, 12, 15, 12, 0, tzinfo=UTC)
        self.assertEqual([("PAST", "NVDA"), ("PAST", "WMT"), ("PAST", "WMT")],
                         [(f.kind, f.symbol) for f in ce.freshness(self.cal, december, 90)])

    def test_undated_entries_are_never_past(self) -> None:
        cal = parse(symbol_table("SPY", earnings(status='"none"', date=None, time=None) + "\n"
                                 + ex_dividend(status='"unknown"', amount=None, date=None)))
        self.assertEqual([], ce.freshness(cal, datetime(2027, 6, 1, tzinfo=UTC), 400, ["SPY"], []))

    def test_without_a_calendar_every_held_symbol_is_missing(self) -> None:
        findings = ce.freshness(None, NOW, 7, ["WMT", "NVDA"])
        self.assertEqual([("MISSING", "NVDA"), ("MISSING", "WMT")], [(f.kind, f.symbol) for f in findings])
        self.assertEqual([], ce.freshness(None, NOW))

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            ce.freshness(self.cal, datetime(2026, 11, 3, 19, 50), 7)
        with self.assertRaises(ValueError):
            ce.freshness(self.cal, NOW, -1)
        with self.assertRaises(ValueError):
            ce.freshness(self.cal, NOW, 7.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ce.freshness(self.cal, NOW, True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ce.freshness(self.cal, NOW, 7, [""])
        with self.assertRaises(ValueError):
            ce.freshness(self.cal, NOW, 7, [3])  # type: ignore[list-item]


class ModuleHygieneTest(unittest.TestCase):
    def test_no_print_and_no_network_imports(self) -> None:
        path = os.path.join(ROOT, "scripts", "aegis_desk", "calendar_events.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("print(", source)
        for module in ("urllib", "http.client", "socket", "ssl", "tomllib", "subprocess"):
            self.assertNotIn("import %s" % module, source)
        self.assertTrue(source.lstrip().startswith('"""'))


if __name__ == "__main__":
    unittest.main()
