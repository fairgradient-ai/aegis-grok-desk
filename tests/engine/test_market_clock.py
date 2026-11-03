"""Market-clock tests: NYSE tables vs the rule generator, the embedded DST rule (cross-checked
against ``zoneinfo`` when tzdata is present), session classification, trading-day arithmetic,
DTE in both units, last trading day and exercise cutoff for PM- and AM-settled contracts, the
table expiry status, expected quote age and the vendor reconciliation.

Every expected instant below was derived by hand from the NYSE hours (09:30-16:00 ET, 13:00 ET
early close, 16:15 / 13:15 ET for late-close products) and the US DST rule (EST = UTC-5, EDT =
UTC-4; daylight time from the second Sunday of March 07:00Z to the first Sunday of November
06:00Z) and then confirmed against ``zoneinfo`` on 2026-11-02. The 2026 and 2027 holiday dates
are pinned as literals so the table test is independent of the generator it also checks.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import unittest
from datetime import date, datetime, time, timedelta, timezone

from aegis_desk import market_clock as mc

try:  # pragma: no cover - depends on the interpreter's tzdata
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        _NEW_YORK = ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        _NEW_YORK = None
except ImportError:  # pragma: no cover
    _NEW_YORK = None

UTC = timezone.utc
H4 = timedelta(hours=-4)
H5 = timedelta(hours=-5)


def z(iso: str) -> datetime:
    """An aware UTC instant from ``YYYY-MM-DDTHH:MM``."""
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


# Published NYSE dates, written out so the table test does not lean on the generator.
_FULL_2026 = [
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
]
_EARLY_2026 = [date(2026, 11, 27), date(2026, 12, 24)]
_FULL_2027 = [
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
]
_EARLY_2027 = [date(2027, 11, 26)]


class HolidayTableTest(unittest.TestCase):
    """The explicit NYSE tables are authoritative; the generator must reproduce them exactly."""

    def test_generator_reproduces_2026_table_exactly(self) -> None:
        self.assertEqual(mc.generate_nyse_calendar(2026), mc.NYSE_HOLIDAYS_2026)

    def test_generator_reproduces_2027_table_exactly(self) -> None:
        self.assertEqual(mc.generate_nyse_calendar(2027), mc.NYSE_HOLIDAYS_2027)

    def test_published_dates_2026(self) -> None:
        full = [e.date for e in mc.NYSE_HOLIDAYS_2026 if e.type == mc.ENTRY_FULL]
        early = [e.date for e in mc.NYSE_HOLIDAYS_2026 if e.type == mc.ENTRY_EARLY_CLOSE]
        self.assertEqual(full, _FULL_2026)
        self.assertEqual(early, _EARLY_2026)

    def test_published_dates_2027(self) -> None:
        full = [e.date for e in mc.NYSE_HOLIDAYS_2027 if e.type == mc.ENTRY_FULL]
        early = [e.date for e in mc.NYSE_HOLIDAYS_2027 if e.type == mc.ENTRY_EARLY_CLOSE]
        self.assertEqual(full, _FULL_2027)
        self.assertEqual(early, _EARLY_2027)

    def test_observed_labels(self) -> None:
        names = {e.date: e.name for e in mc.NYSE_HOLIDAYS_2026 + mc.NYSE_HOLIDAYS_2027}
        self.assertEqual(names[date(2026, 7, 3)], "Independence Day (observed)")
        self.assertEqual(names[date(2027, 6, 18)], "Juneteenth National Independence Day (observed)")
        self.assertEqual(names[date(2027, 7, 5)], "Independence Day (observed)")
        self.assertEqual(names[date(2027, 12, 24)], "Christmas Day (observed)")
        self.assertEqual(names[date(2026, 2, 16)], "Washington's Birthday")
        self.assertEqual(names[date(2026, 11, 27)], "Day after Thanksgiving")
        self.assertEqual(names[date(2026, 12, 24)], "Christmas Eve")

    def test_table_shape(self) -> None:
        for year, table in mc.EXPLICIT_TABLES.items():
            with self.subTest(year=year):
                dates = [e.date for e in table]
                self.assertEqual(dates, sorted(dates), "chronological")
                self.assertEqual(len(dates), len(set(dates)), "unique dates")
                self.assertTrue(all(d.year == year for d in dates))
                self.assertTrue(all(d.weekday() < 5 for d in dates), "observed dates are weekdays")
                self.assertEqual(sum(1 for e in table if e.type == mc.ENTRY_FULL), 10)
                for entry in table:
                    if entry.type == mc.ENTRY_EARLY_CLOSE:
                        self.assertEqual(entry.close_time_et, time(13, 0))
                    else:
                        self.assertIsNone(entry.close_time_et)

    def test_table_metadata(self) -> None:
        self.assertEqual(mc.SOURCE, "https://www.nyse.com/markets/hours-calendars")
        self.assertEqual(mc.TABLE_EXPIRES, date(2027, 12, 31))
        self.assertEqual(tuple(sorted(mc.EXPLICIT_TABLES)), mc.TABLE_YEARS)
        self.assertEqual(mc.TABLE_EXPIRES, date(max(mc.TABLE_YEARS), 12, 31))
        self.assertEqual(mc.TABLE_WARN_DAYS, 60)

    def test_2027_has_no_eve_early_closes(self) -> None:
        # Independence Day observed Monday July 5 and Christmas observed Friday Dec 24 leave the
        # Friday before and the Thursday before as full regular sessions (NYSE 2021 precedent).
        for d in (date(2027, 7, 2), date(2027, 12, 23)):
            with self.subTest(d=d):
                self.assertTrue(mc.is_trading_day(d))
                self.assertFalse(mc.is_early_close(d))
                self.assertIsNone(mc.calendar_entry(d))

    def test_entry_validation(self) -> None:
        with self.assertRaises(ValueError):
            mc.CalendarEntry(date(2026, 1, 1), "x", "partial", None)
        with self.assertRaises(ValueError):
            mc.CalendarEntry(date(2026, 1, 1), "x", mc.ENTRY_FULL, time(13, 0))
        with self.assertRaises(ValueError):
            mc.CalendarEntry(date(2026, 1, 1), "x", mc.ENTRY_EARLY_CLOSE, None)


class GeneratorConventionTest(unittest.TestCase):
    """The NYSE conventions encoded by the generator, checked on years whose calendars are known."""

    def test_computus(self) -> None:
        for year, easter in ((2000, date(2000, 4, 23)), (2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)),
                             (2026, date(2026, 4, 5)), (2027, date(2027, 3, 28)), (2028, date(2028, 4, 16)),
                             (2038, date(2038, 4, 25))):
            with self.subTest(year=year):
                self.assertEqual(mc.easter_sunday(year), easter)
                self.assertEqual(mc.good_friday(year), easter - timedelta(days=2))

    def test_observed_date_rule(self) -> None:
        self.assertEqual(mc.observed_date(date(2026, 7, 4)), date(2026, 7, 3))  # Saturday -> Friday
        self.assertEqual(mc.observed_date(date(2027, 7, 4)), date(2027, 7, 5))  # Sunday -> Monday
        self.assertEqual(mc.observed_date(date(2026, 12, 25)), date(2026, 12, 25))  # weekday stays

    def test_new_years_day_rule_7_2(self) -> None:
        dates_2022 = {e.date for e in mc.generate_nyse_calendar(2022)}
        self.assertNotIn(date(2022, 1, 1), dates_2022)
        self.assertTrue(mc.is_trading_day(date(2021, 12, 31)), "Saturday Jan 1 is not observed on Friday Dec 31")
        self.assertIn(date(2023, 1, 2), {e.date for e in mc.generate_nyse_calendar(2023)}, "Sunday Jan 1 -> Monday")
        self.assertEqual(mc.holiday_name(date(2023, 1, 2)), "New Year's Day (observed)")

    def test_weekend_shifted_holidays_2021_and_2022(self) -> None:
        by_date = {e.date: e for e in mc.generate_nyse_calendar(2021)}
        self.assertEqual(by_date[date(2021, 7, 5)].name, "Independence Day (observed)")
        self.assertEqual(by_date[date(2021, 12, 24)].name, "Christmas Day (observed)")
        self.assertNotIn(date(2021, 6, 18), by_date, "Juneteenth not observed by the NYSE before 2022")
        self.assertEqual([e.date for e in mc.generate_nyse_calendar(2021) if e.type == mc.ENTRY_EARLY_CLOSE],
                         [date(2021, 11, 26)])
        by_date_2022 = {e.date: e for e in mc.generate_nyse_calendar(2022)}
        self.assertEqual(by_date_2022[date(2022, 6, 20)].name, "Juneteenth National Independence Day (observed)")

    def test_eve_early_close_rule(self) -> None:
        early_2023 = [e.date for e in mc.generate_nyse_calendar(2023) if e.type == mc.ENTRY_EARLY_CLOSE]
        self.assertEqual(early_2023, [date(2023, 7, 3), date(2023, 11, 24)], "Dec 24 2023 is a Sunday")
        early_2024 = [e.date for e in mc.generate_nyse_calendar(2024) if e.type == mc.ENTRY_EARLY_CLOSE]
        self.assertEqual(early_2024, [date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24)])
        early_2025 = [e.date for e in mc.generate_nyse_calendar(2025) if e.type == mc.ENTRY_EARLY_CLOSE]
        self.assertEqual(early_2025, [date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)])

    def test_floating_holidays_2025(self) -> None:
        dates = {e.name: e.date for e in mc.generate_nyse_calendar(2025)}
        self.assertEqual(dates["Martin Luther King, Jr. Day"], date(2025, 1, 20))
        self.assertEqual(dates["Washington's Birthday"], date(2025, 2, 17))
        self.assertEqual(dates["Memorial Day"], date(2025, 5, 26))
        self.assertEqual(dates["Labor Day"], date(2025, 9, 1))
        self.assertEqual(dates["Thanksgiving Day"], date(2025, 11, 27))

    def test_unscheduled_closures_are_data(self) -> None:
        self.assertFalse(mc.is_trading_day(date(2025, 1, 9)))
        self.assertFalse(mc.is_trading_day(date(2018, 12, 5)))
        self.assertIn(date(2025, 1, 9), {e.date for e in mc.holidays_for_year(2025)})
        self.assertNotIn(date(2025, 1, 9), {e.date for e in mc.generate_nyse_calendar(2025)},
                         "the generator never invents unscheduled closures")

    def test_coverage_tiers(self) -> None:
        self.assertEqual(mc.calendar_coverage(date(2026, 6, 1)), mc.COVERAGE_TABLE)
        self.assertEqual(mc.calendar_coverage(date(2027, 12, 31)), mc.COVERAGE_TABLE)
        self.assertEqual(mc.calendar_coverage(date(2028, 1, 1)), mc.COVERAGE_RULE)
        self.assertEqual(mc.calendar_coverage(date(2025, 1, 1)), mc.COVERAGE_RULE)
        self.assertIs(mc.holidays_for_year(2026), mc.NYSE_HOLIDAYS_2026)
        self.assertTrue(mc.is_trading_day(date(2028, 1, 21)), "a 2028 LEAPS Friday is answered by the rules")
        self.assertFalse(mc.is_trading_day(date(2028, 1, 17)), "MLK Day 2028 from the rules")


class DstRuleTest(unittest.TestCase):
    """The embedded America/New_York rule at the 2026 and 2027 transitions."""

    def test_spring_forward_2026(self) -> None:
        self.assertEqual(mc.et_offset(z("2026-03-08T06:59")), H5)
        self.assertEqual(mc.et_offset(z("2026-03-08T07:00")), H4)
        self.assertEqual(mc.to_et(z("2026-03-08T06:59")).tzname(), "EST")
        self.assertEqual(mc.to_et(z("2026-03-08T07:00")).tzname(), "EDT")
        self.assertEqual(mc.to_et(z("2026-03-08T07:00")).replace(tzinfo=None), datetime(2026, 3, 8, 3, 0))

    def test_fall_back_2026(self) -> None:
        # The clocks go back at 02:00 EDT = 06:00Z; by 06:59Z and 07:00Z standard time already applies.
        self.assertEqual(mc.et_offset(z("2026-11-01T05:59")), H4)
        self.assertEqual(mc.et_offset(z("2026-11-01T06:00")), H5)
        self.assertEqual(mc.et_offset(z("2026-11-01T06:59")), H5)
        self.assertEqual(mc.et_offset(z("2026-11-01T07:00")), H5)
        self.assertEqual(mc.to_et(z("2026-11-01T06:00")).replace(tzinfo=None), datetime(2026, 11, 1, 1, 0))

    def test_transitions_2027(self) -> None:
        self.assertEqual(mc.et_offset(z("2027-03-14T06:59")), H5)
        self.assertEqual(mc.et_offset(z("2027-03-14T07:00")), H4)
        self.assertEqual(mc.et_offset(z("2027-11-07T05:59")), H4)
        self.assertEqual(mc.et_offset(z("2027-11-07T06:00")), H5)
        self.assertEqual(mc.et_offset(z("2027-11-07T06:59")), H5)
        self.assertEqual(mc.et_offset(z("2027-11-07T07:00")), H5)

    def test_is_dst_and_mid_season_offsets(self) -> None:
        self.assertTrue(mc.is_dst(z("2026-07-01T12:00")))
        self.assertFalse(mc.is_dst(z("2026-01-15T12:00")))
        self.assertEqual(mc.et_offset(z("2026-07-01T12:00")), H4)
        self.assertEqual(mc.et_offset(z("2026-12-31T23:59")), H5)

    def test_non_utc_aware_input_is_an_instant(self) -> None:
        ist = timezone(timedelta(hours=5, minutes=30))
        same_instant = datetime(2026, 11, 2, 20, 0, tzinfo=ist)  # 14:30Z
        self.assertEqual(mc.to_et(same_instant), mc.to_et(z("2026-11-02T14:30")))
        self.assertEqual(mc.to_et(same_instant).replace(tzinfo=None), datetime(2026, 11, 2, 9, 30))

    def test_naive_and_pre_2007_inputs_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            mc.et_offset(datetime(2026, 11, 2, 14, 30))
        with self.assertRaises(ValueError):
            mc.to_et(datetime(2026, 11, 2, 14, 30))
        with self.assertRaises(ValueError):
            mc.et_offset(z("2006-07-01T12:00"))
        with self.assertRaises(ValueError):
            mc.et_offset("2026-11-02T14:30")  # type: ignore[arg-type]


class FromEtTest(unittest.TestCase):
    """Wall-clock ET -> UTC with PEP 495 fold=0 semantics."""

    def test_round_trip_every_seven_hours_across_2026(self) -> None:
        t = z("2026-01-01T00:00")
        while t < z("2027-01-01T00:00"):
            self.assertEqual(mc.from_et(mc.to_et(t).replace(tzinfo=None)), t)
            t += timedelta(hours=7)

    def test_regular_wall_times(self) -> None:
        self.assertEqual(mc.from_et(datetime(2026, 11, 2, 9, 30)), z("2026-11-02T14:30"))  # EST
        self.assertEqual(mc.from_et(datetime(2026, 6, 15, 9, 30)), z("2026-06-15T13:30"))  # EDT
        self.assertEqual(mc.from_et(datetime(2026, 11, 2, 16, 0)), z("2026-11-02T21:00"))

    def test_gap_resolves_with_pre_transition_offset(self) -> None:
        # 02:30 does not exist on 2026-03-08; fold=0 keeps the EST offset, so it maps to 07:30Z.
        self.assertEqual(mc.from_et(datetime(2026, 3, 8, 2, 30)), z("2026-03-08T07:30"))
        self.assertEqual(mc.from_et(datetime(2026, 3, 8, 1, 59)), z("2026-03-08T06:59"))
        self.assertEqual(mc.from_et(datetime(2026, 3, 8, 3, 0)), z("2026-03-08T07:00"))

    def test_ambiguous_hour_is_first_occurrence(self) -> None:
        # 01:30 happens twice on 2026-11-01; fold=0 is the EDT occurrence, 05:30Z.
        self.assertEqual(mc.from_et(datetime(2026, 11, 1, 1, 30)), z("2026-11-01T05:30"))
        self.assertEqual(mc.from_et(datetime(2026, 11, 1, 0, 59)), z("2026-11-01T04:59"))
        self.assertEqual(mc.from_et(datetime(2026, 11, 1, 2, 0)), z("2026-11-01T07:00"))

    def test_aware_or_pre_2007_input_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            mc.from_et(datetime(2026, 11, 2, 9, 30, tzinfo=UTC))
        with self.assertRaises(ValueError):
            mc.from_et(datetime(2006, 11, 2, 9, 30))
        with self.assertRaises(ValueError):
            mc.from_et(date(2026, 11, 2))  # type: ignore[arg-type]


@unittest.skipUnless(_NEW_YORK is not None, "zoneinfo America/New_York (tzdata) not available")
class ZoneinfoCrossCheckTest(unittest.TestCase):
    """Independent oracle: the embedded rule against the system tz database."""

    def test_hourly_offsets_2026_2027(self) -> None:
        t = z("2026-01-01T00:00")
        end = z("2028-01-01T00:00")
        while t < end:
            self.assertEqual(mc.et_offset(t), t.astimezone(_NEW_YORK).utcoffset(), t.isoformat())
            t += timedelta(hours=1)

    def test_transition_instants_2007_2037(self) -> None:
        for year in range(2007, 2038):
            start, end = mc.dst_window_utc(year)
            for instant in (start - timedelta(seconds=1), start, end - timedelta(seconds=1), end):
                with self.subTest(instant=instant.isoformat()):
                    ref = instant.astimezone(_NEW_YORK)
                    self.assertEqual(mc.et_offset(instant), ref.utcoffset())
                    self.assertEqual(mc.to_et(instant).tzname(), ref.tzname())
                    self.assertEqual(mc.to_et(instant).replace(tzinfo=None), ref.replace(tzinfo=None))

    def test_from_et_matches_fold_zero_on_transition_days(self) -> None:
        for day in (date(2026, 3, 8), date(2026, 11, 1), date(2027, 3, 14), date(2027, 11, 7), date(2026, 6, 15)):
            for minute in range(0, 24 * 60, 5):
                naive = datetime.combine(day, time(0, 0)) + timedelta(minutes=minute)
                expected = naive.replace(tzinfo=_NEW_YORK).astimezone(UTC)
                self.assertEqual(mc.from_et(naive), expected, naive.isoformat())


class SessionClassificationTest(unittest.TestCase):
    """session_at on a regular EST Monday (2026-11-02), an EDT day, an early close, a weekend
    and a holiday. 09:30 ET = 14:30Z in EST; 16:00 ET = 21:00Z; 13:00 ET = 18:00Z."""

    def test_regular_day_boundaries(self) -> None:
        cases = [
            ("2026-11-02T14:29", mc.PRODUCT_EQUITY, mc.SESSION_PRE, 391, "2026-11-02T21:00"),
            ("2026-11-02T14:30", mc.PRODUCT_EQUITY, mc.SESSION_OPEN, 390, "2026-11-02T21:00"),
            ("2026-11-02T20:59", mc.PRODUCT_EQUITY, mc.SESSION_OPEN, 1, "2026-11-02T21:00"),
            ("2026-11-02T21:00", mc.PRODUCT_EQUITY, mc.SESSION_CLOSED, None, "2026-11-02T21:00"),
            ("2026-11-02T21:14", mc.PRODUCT_LATE_CLOSE, mc.SESSION_OPEN, 1, "2026-11-02T21:15"),
            ("2026-11-02T21:15", mc.PRODUCT_LATE_CLOSE, mc.SESSION_CLOSED, None, "2026-11-02T21:15"),
        ]
        for instant, product, session, minutes, close in cases:
            with self.subTest(instant=instant, product=product):
                info = mc.session_at(z(instant), product)
                self.assertEqual(info.session, session)
                self.assertTrue(info.trading_day)
                self.assertEqual(info.minutes_to_close, minutes)
                self.assertEqual(info.close_utc, z(close))
                self.assertEqual(info.open_utc, z("2026-11-02T14:30"))
                self.assertEqual(info.et_date, date(2026, 11, 2))
                self.assertIsNone(info.holiday_name)
                self.assertEqual(info.product, product)
                self.assertEqual(info.table_status, mc.STATUS_OK)
                self.assertEqual(info.as_of_utc, z(instant))

    def test_minutes_to_close_floors_partial_minutes(self) -> None:
        info = mc.session_at(z("2026-11-02T20:59").replace(second=30))
        self.assertEqual(info.minutes_to_close, 0)
        self.assertEqual(info.session, mc.SESSION_OPEN)

    def test_edt_day_uses_minus_four(self) -> None:
        info = mc.session_at(z("2026-06-15T13:29"))
        self.assertEqual(info.session, mc.SESSION_PRE)
        self.assertEqual(info.minutes_to_close, 391)
        self.assertEqual(info.open_utc, z("2026-06-15T13:30"))
        self.assertEqual(info.close_utc, z("2026-06-15T20:00"))
        self.assertEqual(mc.session_at(z("2026-06-15T13:30")).session, mc.SESSION_OPEN)
        self.assertEqual(mc.session_at(z("2026-06-15T20:00")).session, mc.SESSION_CLOSED)

    def test_early_close_day(self) -> None:
        cases = [
            ("2026-11-27T14:29", mc.PRODUCT_EQUITY, mc.SESSION_PRE, 211, "2026-11-27T18:00"),
            ("2026-11-27T14:30", mc.PRODUCT_EQUITY, mc.SESSION_EARLY_CLOSE, 210, "2026-11-27T18:00"),
            ("2026-11-27T17:59", mc.PRODUCT_EQUITY, mc.SESSION_EARLY_CLOSE, 1, "2026-11-27T18:00"),
            ("2026-11-27T18:00", mc.PRODUCT_EQUITY, mc.SESSION_CLOSED, None, "2026-11-27T18:00"),
            ("2026-11-27T18:14", mc.PRODUCT_LATE_CLOSE, mc.SESSION_EARLY_CLOSE, 1, "2026-11-27T18:15"),
            ("2026-11-27T18:15", mc.PRODUCT_LATE_CLOSE, mc.SESSION_CLOSED, None, "2026-11-27T18:15"),
        ]
        for instant, product, session, minutes, close in cases:
            with self.subTest(instant=instant, product=product):
                info = mc.session_at(z(instant), product)
                self.assertEqual(info.session, session)
                self.assertTrue(info.trading_day)
                self.assertEqual(info.minutes_to_close, minutes)
                self.assertEqual(info.close_utc, z(close))
                self.assertEqual(info.holiday_name, "Day after Thanksgiving")
                self.assertEqual(info.is_live, session == mc.SESSION_EARLY_CLOSE)

    def test_saturday(self) -> None:
        info = mc.session_at(z("2026-11-07T15:00"))
        self.assertEqual(info.session, mc.SESSION_WEEKEND)
        self.assertFalse(info.trading_day)
        self.assertIsNone(info.close_utc)
        self.assertIsNone(info.open_utc)
        self.assertIsNone(info.minutes_to_close)
        self.assertIsNone(info.holiday_name)
        self.assertFalse(info.is_live)

    def test_holiday(self) -> None:
        info = mc.session_at(z("2026-11-26T15:00"))
        self.assertEqual(info.session, mc.SESSION_HOLIDAY)
        self.assertFalse(info.trading_day)
        self.assertEqual(info.holiday_name, "Thanksgiving Day")
        self.assertIsNone(info.close_utc)
        self.assertIsNone(info.minutes_to_close)
        self.assertEqual(mc.session_at(z("2026-07-03T15:00")).holiday_name, "Independence Day (observed)")

    def test_et_date_not_utc_date(self) -> None:
        info = mc.session_at(z("2026-11-03T00:30"))  # 19:30 ET on Monday Nov 2
        self.assertEqual(info.et_date, date(2026, 11, 2))
        self.assertEqual(info.session, mc.SESSION_CLOSED)
        self.assertEqual(info.close_utc, z("2026-11-02T21:00"))
        sunday_night = mc.session_at(z("2026-11-09T03:00"))  # 22:00 ET Sunday Nov 8
        self.assertEqual(sunday_night.session, mc.SESSION_WEEKEND)

    def test_extra_holidays_injection(self) -> None:
        plain = mc.session_at(z("2026-11-02T15:00"), extra_holidays=[date(2026, 11, 2)])
        self.assertEqual(plain.session, mc.SESSION_HOLIDAY)
        self.assertEqual(plain.holiday_name, mc.UNSCHEDULED_LABEL)
        named = mc.session_at(z("2026-11-02T15:00"), extra_holidays={date(2026, 11, 2): "Hurricane closure"})
        self.assertEqual(named.session, mc.SESSION_HOLIDAY)
        self.assertEqual(named.holiday_name, "Hurricane closure")
        other_day = mc.session_at(z("2026-11-03T15:00"), extra_holidays=[date(2026, 11, 2)])
        self.assertEqual(other_day.session, mc.SESSION_OPEN)
        with self.assertRaises(ValueError):
            mc.session_at(z("2026-11-02T15:00"), extra_holidays=[z("2026-11-02T00:00")])

    def test_input_errors(self) -> None:
        with self.assertRaises(ValueError):
            mc.session_at(datetime(2026, 11, 2, 15, 0))
        with self.assertRaises(ValueError):
            mc.session_at(z("2026-11-02T15:00"), product="index")

    def test_fails_closed_past_table_coverage(self) -> None:
        with self.assertRaises(mc.TableExpiredError):
            mc.session_at(z("2028-01-03T15:00"))
        with self.assertRaises(mc.TableExpiredError):
            mc.previous_close_utc(z("2028-01-03T15:00"))
        self.assertTrue(issubclass(mc.TableExpiredError, mc.MarketClockError))
        self.assertFalse(issubclass(mc.TableExpiredError, ValueError), "not an input error")
        # The last covered day still classifies, and reports expiring_soon.
        info = mc.session_at(z("2027-12-31T15:00"))
        self.assertEqual(info.session, mc.SESSION_OPEN)
        self.assertEqual(info.table_status, mc.STATUS_EXPIRING_SOON)

    def test_as_dict_is_json_ready(self) -> None:
        info = mc.session_at(z("2026-11-02T15:00"))
        data = info.as_dict()
        self.assertEqual(data["session"], "open")
        self.assertEqual(data["close_utc"], "2026-11-02T21:00:00+00:00")
        self.assertEqual(data["et_date"], "2026-11-02")
        self.assertEqual(data["minutes_to_close"], 360)
        self.assertEqual(set(data), {"session", "trading_day", "close_utc", "minutes_to_close", "holiday_name",
                                     "as_of_utc", "et_date", "open_utc", "product", "table_status"})
        self.assertIsNone(mc.session_at(z("2026-11-07T15:00")).as_dict()["close_utc"])

    def test_every_session_value_is_in_the_vocabulary(self) -> None:
        instants = ("2026-11-02T12:00", "2026-11-02T15:00", "2026-11-02T22:00", "2026-11-26T15:00",
                    "2026-11-27T15:00", "2026-11-07T15:00")
        seen = {mc.session_at(z(i)).session for i in instants}
        self.assertEqual(seen, set(mc.SESSIONS))


class TradingDayTest(unittest.TestCase):
    """is_trading_day, is_early_close, holiday_name, session_close_et and the day steppers."""

    def test_is_trading_day(self) -> None:
        self.assertTrue(mc.is_trading_day(date(2026, 11, 2)))
        self.assertTrue(mc.is_trading_day(date(2026, 11, 27)), "an early-close day trades")
        self.assertFalse(mc.is_trading_day(date(2026, 11, 26)))
        self.assertFalse(mc.is_trading_day(date(2026, 11, 7)))
        self.assertFalse(mc.is_trading_day(date(2026, 11, 8)))
        self.assertFalse(mc.is_trading_day(date(2026, 11, 2), extra_holidays=[date(2026, 11, 2)]))
        self.assertTrue(mc.is_trading_day(z("2026-11-03T00:30")), "an aware datetime uses its ET date")

    def test_is_early_close(self) -> None:
        self.assertTrue(mc.is_early_close(date(2026, 11, 27)))
        self.assertTrue(mc.is_early_close(date(2026, 12, 24)))
        self.assertFalse(mc.is_early_close(date(2026, 11, 26)))
        self.assertFalse(mc.is_early_close(date(2026, 11, 2)))
        self.assertFalse(mc.is_early_close(date(2026, 11, 27), extra_holidays=[date(2026, 11, 27)]),
                         "an injected closure is not an early close")

    def test_holiday_name_is_a_label(self) -> None:
        self.assertEqual(mc.holiday_name(date(2026, 11, 26)), "Thanksgiving Day")
        self.assertEqual(mc.holiday_name(date(2026, 11, 27)), "Day after Thanksgiving")
        self.assertIsNone(mc.holiday_name(date(2026, 11, 2)))
        self.assertIsNone(mc.holiday_name(date(2026, 11, 7)), "an ordinary weekend has no label")
        self.assertEqual(mc.holiday_name(date(2026, 11, 2), extra_holidays=[date(2026, 11, 2)]), mc.UNSCHEDULED_LABEL)
        self.assertEqual(mc.holiday_name(date(2026, 11, 27), extra_holidays={date(2026, 11, 27): "Storm"}), "Storm")

    def test_session_close_et(self) -> None:
        self.assertEqual(mc.session_close_et(date(2026, 11, 2)), time(16, 0))
        self.assertEqual(mc.session_close_et(date(2026, 11, 2), mc.PRODUCT_LATE_CLOSE), time(16, 15))
        self.assertEqual(mc.session_close_et(date(2026, 11, 27)), time(13, 0))
        self.assertEqual(mc.session_close_et(date(2026, 11, 27), mc.PRODUCT_LATE_CLOSE), time(13, 15))
        with self.assertRaises(ValueError):
            mc.session_close_et(date(2026, 11, 7))
        with self.assertRaises(ValueError):
            mc.session_close_et(date(2026, 11, 26))
        with self.assertRaises(ValueError):
            mc.session_close_et(date(2026, 11, 2), product="futures")

    def test_day_steppers_across_thanksgiving(self) -> None:
        self.assertEqual(mc.previous_trading_day(date(2026, 11, 30)), date(2026, 11, 27))
        self.assertEqual(mc.previous_trading_day(date(2026, 11, 27)), date(2026, 11, 25))
        self.assertEqual(mc.next_trading_day(date(2026, 11, 25)), date(2026, 11, 27))
        self.assertEqual(mc.next_trading_day(date(2026, 11, 27)), date(2026, 11, 30))
        self.assertEqual(mc.previous_trading_day(date(2026, 11, 26), extra_holidays=[date(2026, 11, 25)]),
                         date(2026, 11, 24))

    def test_trading_days_between_half_open(self) -> None:
        # (Fri 2026-05-22, Fri 2026-05-29]: Memorial Day Monday is skipped -> Tue, Wed, Thu, Fri.
        self.assertEqual(mc.trading_days_between(date(2026, 5, 22), date(2026, 5, 29)), 4)
        self.assertEqual(mc.trading_days_between(date(2026, 5, 22), date(2026, 5, 22)), 0)
        self.assertEqual(mc.trading_days_between(date(2026, 5, 22), date(2026, 5, 25)), 0, "holiday Monday adds nothing")
        self.assertEqual(mc.trading_days_between(date(2026, 5, 22), date(2026, 5, 26)), 1)
        self.assertEqual(mc.trading_days_between(date(2026, 5, 29), date(2026, 5, 22)), -4, "antisymmetric")
        self.assertEqual(mc.trading_days_between(date(2026, 11, 25), date(2026, 12, 4)), 6)
        self.assertEqual(mc.trading_days_between(date(2026, 11, 25), date(2026, 12, 4), extra_holidays=[date(2026, 12, 1)]), 5)

    def test_date_inputs_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            mc.is_trading_day("2026-11-02")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            mc.is_trading_day(datetime(2026, 11, 2, 15, 0))


class DteTest(unittest.TestCase):
    """DTE in both units, measured to the last trading day."""

    def test_across_memorial_day_weekend(self) -> None:
        self.assertEqual(mc.dte(date(2026, 5, 29), date(2026, 5, 22)), 7)
        self.assertEqual(mc.dte(date(2026, 5, 29), date(2026, 5, 22), mc.UNIT_CALENDAR), 7)
        self.assertEqual(mc.dte(date(2026, 5, 29), date(2026, 5, 22), mc.UNIT_TRADING), 4)

    def test_across_thanksgiving(self) -> None:
        self.assertEqual(mc.dte(date(2026, 12, 4), date(2026, 11, 25)), 9)
        self.assertEqual(mc.dte(date(2026, 12, 4), date(2026, 11, 25), "trading"), 6)

    def test_zero_on_last_trading_day_and_negative_after(self) -> None:
        self.assertEqual(mc.dte(date(2026, 12, 18), date(2026, 12, 18)), 0)
        self.assertEqual(mc.dte(date(2026, 12, 18), date(2026, 12, 18), "trading"), 0)
        self.assertEqual(mc.dte(date(2026, 12, 18), date(2026, 12, 21)), -3)
        self.assertEqual(mc.dte(date(2026, 12, 18), date(2026, 12, 21), "trading"), -1)

    def test_holiday_and_saturday_expiries_measure_to_the_adjusted_day(self) -> None:
        # Legacy Saturday symbol 2026-06-20; Friday 06-19 is Juneteenth -> last trade Thursday 06-18.
        self.assertEqual(mc.dte(date(2026, 6, 20), date(2026, 6, 15)), 3)
        self.assertEqual(mc.dte(date(2026, 6, 20), date(2026, 6, 15), "trading"), 3)

    def test_am_settled_ends_one_trading_day_earlier(self) -> None:
        self.assertEqual(mc.dte(date(2026, 12, 18), date(2026, 12, 14)), 4)
        self.assertEqual(mc.dte(date(2026, 12, 18), date(2026, 12, 14), am_settled=True), 3)
        self.assertEqual(mc.dte(date(2026, 12, 18), date(2026, 12, 14), "trading", am_settled=True), 3)

    def test_as_of_datetime_uses_et_date(self) -> None:
        # 00:30Z on Nov 3 is still Monday Nov 2 in New York.
        self.assertEqual(mc.dte(date(2026, 11, 6), z("2026-11-03T00:30")), 4)
        self.assertEqual(mc.dte(date(2026, 11, 6), z("2026-11-03T05:30")), 3)

    def test_extra_holidays_reduce_trading_count_only(self) -> None:
        self.assertEqual(mc.dte(date(2026, 12, 4), date(2026, 11, 25), "trading", extra_holidays=[date(2026, 12, 2)]), 5)
        self.assertEqual(mc.dte(date(2026, 12, 4), date(2026, 11, 25), extra_holidays=[date(2026, 12, 2)]), 9)

    def test_invalid_unit(self) -> None:
        with self.assertRaises(ValueError):
            mc.dte(date(2026, 12, 18), date(2026, 12, 14), "business")


class LastTradingDayTest(unittest.TestCase):
    """PM: the adjusted expiry; AM: one trading day before the settlement morning."""

    def test_pm_regular_friday(self) -> None:
        self.assertEqual(mc.last_trading_day(date(2026, 12, 18), False), date(2026, 12, 18))
        self.assertEqual(mc.settlement_day(date(2026, 12, 18), False), date(2026, 12, 18))

    def test_pm_legacy_saturday_expiry(self) -> None:
        # Saturday 2026-06-20 -> Friday 06-19 is Juneteenth -> Thursday 06-18.
        self.assertEqual(mc.last_trading_day(date(2026, 6, 20), False), date(2026, 6, 18))
        self.assertEqual(mc.last_trading_day(date(2026, 12, 19), False), date(2026, 12, 18))

    def test_pm_holiday_friday(self) -> None:
        self.assertEqual(mc.last_trading_day(date(2026, 4, 3), False), date(2026, 4, 2), "Good Friday -> Thursday")

    def test_am_settled(self) -> None:
        self.assertEqual(mc.last_trading_day(date(2026, 12, 18), True), date(2026, 12, 17))
        self.assertEqual(mc.settlement_day(date(2026, 12, 18), True), date(2026, 12, 18))
        # Good Friday expiry: settles Thursday morning, last trade Wednesday (Cboe rule).
        self.assertEqual(mc.settlement_day(date(2026, 4, 3), True), date(2026, 4, 2))
        self.assertEqual(mc.last_trading_day(date(2026, 4, 3), True), date(2026, 4, 1))
        # Monday expiry: the preceding trading day is the Friday before.
        self.assertEqual(mc.last_trading_day(date(2026, 11, 30), True), date(2026, 11, 27))

    def test_extra_holidays_and_validation(self) -> None:
        self.assertEqual(mc.last_trading_day(date(2026, 12, 18), False, extra_holidays=[date(2026, 12, 18)]),
                         date(2026, 12, 17))
        with self.assertRaises(ValueError):
            mc.last_trading_day(date(2026, 12, 18), "pm")  # type: ignore[arg-type]


class ExerciseCutoffTest(unittest.TestCase):
    """The instant tau is measured to. 16:00 EST = 21:00Z; 13:00 EST = 18:00Z; 09:30 EST = 14:30Z."""

    def test_pm_without_broker_cutoff(self) -> None:
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 12, 18), False), z("2026-12-18T21:00"))
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 12, 18), False, None), z("2026-12-18T21:00"))

    def test_pm_with_earlier_broker_cutoff(self) -> None:
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 12, 18), False, time(15, 30)), z("2026-12-18T20:30"))

    def test_pm_later_broker_cutoff_does_not_extend_the_market(self) -> None:
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 12, 18), False, time(17, 30)), z("2026-12-18T21:00"))

    def test_pm_on_early_close_day(self) -> None:
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 11, 27), False), z("2026-11-27T18:00"))
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 11, 27), False, time(15, 30)), z("2026-11-27T18:00"))
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 11, 27), False, time(12, 0)), z("2026-11-27T17:00"))

    def test_pm_saturday_legacy_in_daylight_time(self) -> None:
        # Thursday 2026-06-18 is the last trading day (EDT): 16:00 ET = 20:00Z.
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 6, 20), False), z("2026-06-18T20:00"))

    def test_am_settled(self) -> None:
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 12, 18), True), z("2026-12-18T14:30"))
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 12, 18), True, time(15, 30)), z("2026-12-18T14:30"),
                         "the broker cutoff does not move an AM settlement")
        self.assertEqual(mc.exercise_cutoff_utc(date(2026, 4, 3), True), z("2026-04-02T13:30"), "Good Friday -> Thursday open, EDT")

    def test_broker_cutoff_validation(self) -> None:
        with self.assertRaises(ValueError):
            mc.exercise_cutoff_utc(date(2026, 12, 18), False, time(15, 30, tzinfo=UTC))
        with self.assertRaises(ValueError):
            mc.exercise_cutoff_utc(date(2026, 12, 18), False, datetime(2026, 12, 18, 15, 30))  # type: ignore[arg-type]


class TableStatusTest(unittest.TestCase):
    """ok / expiring_soon (<= 60 days before TABLE_EXPIRES) / expired."""

    def test_status_boundaries(self) -> None:
        self.assertEqual(mc.table_status(date(2026, 11, 2)), mc.STATUS_OK)
        # 2027-10-01 is 91 days before 2027-12-31, outside the 60-day window: still ok.
        self.assertEqual(mc.table_status(date(2027, 10, 1)), mc.STATUS_OK)
        self.assertEqual(mc.table_status(date(2027, 10, 31)), mc.STATUS_OK)
        self.assertEqual(mc.table_status(date(2027, 11, 1)), mc.STATUS_EXPIRING_SOON, "exactly 60 days out")
        self.assertEqual(mc.table_status(date(2027, 12, 31)), mc.STATUS_EXPIRING_SOON, "the last covered day")
        self.assertEqual(mc.table_status(date(2028, 1, 1)), mc.STATUS_EXPIRED)
        self.assertEqual(mc.table_status(date(2028, 1, 2)), mc.STATUS_EXPIRED)

    def test_warn_days_parameter_and_datetime_input(self) -> None:
        self.assertEqual(mc.table_status(date(2027, 10, 1), warn_days=91), mc.STATUS_EXPIRING_SOON)
        self.assertEqual(mc.table_status(date(2027, 10, 1), warn_days=90), mc.STATUS_OK)
        self.assertEqual(mc.table_status(z("2028-01-01T03:00")), mc.STATUS_EXPIRING_SOON, "22:00 ET Dec 31")
        self.assertEqual(mc.table_status(z("2028-01-01T05:00")), mc.STATUS_EXPIRED, "00:00 ET Jan 1")
        with self.assertRaises(ValueError):
            mc.table_status(date(2027, 10, 1), warn_days=-1)
        with self.assertRaises(ValueError):
            mc.table_status(date(2027, 10, 1), warn_days=True)


class ExpectedQuoteAgeTest(unittest.TestCase):
    """Expected age of the newest quote on a 15-minute delayed feed, by session."""

    def test_live_sessions_report_the_feed_delay(self) -> None:
        self.assertEqual(mc.expected_quote_age_min(mc.session_at(z("2026-11-02T15:00")), 15), 15)
        self.assertEqual(mc.expected_quote_age_min(mc.session_at(z("2026-11-27T15:00")), 15), 15)
        self.assertEqual(mc.expected_quote_age_min(mc.session_at(z("2026-11-02T15:00")), 0), 0)

    def test_after_close_the_closing_quote_ages(self) -> None:
        self.assertEqual(mc.expected_quote_age_min(mc.session_at(z("2026-11-02T21:20")), 15), 20)
        self.assertEqual(mc.expected_quote_age_min(mc.session_at(z("2026-11-02T21:05")), 15), 15)
        self.assertEqual(mc.expected_quote_age_min(mc.session_at(z("2026-11-02T21:00")), 15), 15)
        self.assertEqual(mc.expected_quote_age_min(mc.session_at(z("2026-11-02T23:00")), 15), 120)

    def test_no_live_quote_expected(self) -> None:
        self.assertIsNone(mc.expected_quote_age_min(mc.session_at(z("2026-11-02T12:00")), 15))
        self.assertIsNone(mc.expected_quote_age_min(mc.session_at(z("2026-11-07T15:00")), 15))
        self.assertIsNone(mc.expected_quote_age_min(mc.session_at(z("2026-11-26T15:00")), 15))

    def test_validation(self) -> None:
        info = mc.session_at(z("2026-11-02T15:00"))
        with self.assertRaises(ValueError):
            mc.expected_quote_age_min(info, -1)
        with self.assertRaises(ValueError):
            mc.expected_quote_age_min(info, 15.0)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            mc.expected_quote_age_min(info, True)
        with self.assertRaises(ValueError):
            mc.expected_quote_age_min({"session": "open"}, 15)  # type: ignore[arg-type]


class PreviousCloseTest(unittest.TestCase):
    """The 'as of close HH:MM ET' reference instant."""

    def test_before_the_open_is_the_prior_trading_day(self) -> None:
        # Monday 2026-11-02 08:00 ET -> Friday 2026-10-30 16:00 EDT = 20:00Z.
        self.assertEqual(mc.previous_close_utc(z("2026-11-02T13:00")), z("2026-10-30T20:00"))

    def test_weekend_and_holiday(self) -> None:
        self.assertEqual(mc.previous_close_utc(z("2026-11-07T15:00")), z("2026-11-06T21:00"))
        self.assertEqual(mc.previous_close_utc(z("2026-11-26T15:00")), z("2026-11-25T21:00"))
        # Monday after Thanksgiving, pre-open: Friday's EARLY close 13:00 EST = 18:00Z.
        self.assertEqual(mc.previous_close_utc(z("2026-11-30T13:00")), z("2026-11-27T18:00"))

    def test_after_the_close_is_today(self) -> None:
        self.assertEqual(mc.previous_close_utc(z("2026-11-02T21:00")), z("2026-11-02T21:00"))
        self.assertEqual(mc.previous_close_utc(z("2026-11-02T21:30")), z("2026-11-02T21:00"))
        self.assertEqual(mc.previous_close_utc(z("2026-11-02T20:59")), z("2026-10-30T20:00"), "still open: prior close")
        self.assertEqual(mc.previous_close_utc(z("2026-11-02T21:30"), mc.PRODUCT_LATE_CLOSE), z("2026-11-02T21:15"))


class ReconcileVendorStateTest(unittest.TestCase):
    """The more-closed of local and vendor wins; every disagreement is returned."""

    def setUp(self) -> None:
        self.open_info = mc.session_at(z("2026-11-02T15:00"))
        self.pre_info = mc.session_at(z("2026-11-02T12:00"))
        self.holiday_info = mc.session_at(z("2026-11-26T15:00"))

    def test_vendor_closed_beats_local_open(self) -> None:
        result = mc.reconcile_vendor_state(self.open_info, "closed")
        self.assertEqual(result.effective_session, mc.SESSION_CLOSED)
        self.assertEqual(result.local_session, mc.SESSION_OPEN)
        self.assertEqual(result.vendor_session, mc.SESSION_CLOSED)
        self.assertFalse(result.agree)
        self.assertIn("vendor 'closed'", result.disagreement)

    def test_tradier_clock_payload_shape(self) -> None:
        payload = {"clock": {"date": "2026-11-02", "description": "Market is closed", "state": "closed",
                             "timestamp": 1762097400, "next_change": "09:30", "next_state": "open"}}
        result = mc.reconcile_vendor_state(self.open_info, payload)
        self.assertEqual(result.effective_session, mc.SESSION_CLOSED)
        self.assertEqual(result.vendor_state, "closed")
        inner_only = mc.reconcile_vendor_state(self.open_info, payload["clock"])
        self.assertEqual(inner_only.effective_session, mc.SESSION_CLOSED)

    def test_agreement(self) -> None:
        result = mc.reconcile_vendor_state(self.open_info, "open")
        self.assertTrue(result.agree)
        self.assertIsNone(result.disagreement)
        self.assertEqual(result.effective_session, mc.SESSION_OPEN)
        self.assertTrue(mc.reconcile_vendor_state(self.pre_info, "premarket").agree)
        self.assertTrue(mc.reconcile_vendor_state(self.pre_info, "closed").agree, "both non-live")
        self.assertTrue(mc.reconcile_vendor_state(mc.session_at(z("2026-11-02T21:30")), "postmarket").agree)
        self.assertTrue(mc.reconcile_vendor_state(mc.session_at(z("2026-11-27T15:00")), "OPEN").agree,
                        "early_close is live; vendor strings are case-insensitive")

    def test_vendor_open_never_opens_a_closed_day(self) -> None:
        result = mc.reconcile_vendor_state(self.holiday_info, "open")
        self.assertEqual(result.effective_session, mc.SESSION_HOLIDAY)
        self.assertFalse(result.agree)
        self.assertIn("local 'holiday'", result.disagreement)

    def test_stale_or_unrecognised_vendor_is_ignored_but_reported(self) -> None:
        stale = mc.reconcile_vendor_state(self.open_info, {"clock": {"date": "2026-11-01", "state": "closed"}})
        self.assertEqual(stale.effective_session, mc.SESSION_OPEN)
        self.assertIsNone(stale.vendor_session)
        self.assertFalse(stale.agree)
        self.assertIn("2026-11-01", stale.disagreement)
        unknown = mc.reconcile_vendor_state(self.open_info, "halted")
        self.assertEqual(unknown.effective_session, mc.SESSION_OPEN)
        self.assertFalse(unknown.agree)
        self.assertIn("unrecognised", unknown.disagreement)
        missing = mc.reconcile_vendor_state(self.open_info, None)
        self.assertEqual(missing.effective_session, mc.SESSION_OPEN)
        self.assertFalse(missing.agree)

    def test_local_must_be_session_info(self) -> None:
        with self.assertRaises(ValueError):
            mc.reconcile_vendor_state("open", "closed")  # type: ignore[arg-type]


class ModuleContractTest(unittest.TestCase):
    """Export list and library hygiene."""

    def test_all_names_exist(self) -> None:
        for name in mc.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(mc, name))
        for name in ("et_offset", "to_et", "from_et", "is_trading_day", "holiday_name", "is_early_close",
                     "session_close_et", "session_at", "trading_days_between", "dte", "last_trading_day",
                     "exercise_cutoff_utc", "expected_quote_age_min", "table_status", "reconcile_vendor_state",
                     "SOURCE", "TABLE_EXPIRES", "SessionInfo"):
            self.assertIn(name, mc.__all__)

    def test_no_print_in_library_module(self) -> None:
        with open(mc.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("print(", source)

    def test_fixed_offset_zones(self) -> None:
        self.assertEqual(mc.EST.utcoffset(None), H5)
        self.assertEqual(mc.EDT.utcoffset(None), H4)
        self.assertEqual(mc.REGULAR_OPEN_ET, time(9, 30))
        self.assertEqual(mc.REGULAR_CLOSE_ET, time(16, 0))
        self.assertEqual(mc.EARLY_CLOSE_ET, time(13, 0))
        self.assertEqual(mc.LATE_CLOSE_ET, time(16, 15))
        self.assertEqual(mc.LATE_EARLY_CLOSE_ET, time(13, 15))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
