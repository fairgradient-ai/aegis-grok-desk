"""Put-call parity carry (``aegis_desk.carry``) on the fixture's own chains (quotes stamped 2026-11-03 19:31:12Z)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import unittest

from aegis_desk import carry, iv, market_clock, units

STAMP = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=datetime.timezone.utc)
RATE = 0.0412


def tau(expiration: datetime.date) -> float:
    return (market_clock.exercise_cutoff_utc(expiration, False) - STAMP).total_seconds() / units.SECONDS_PER_YEAR


def mid(quote: carry.Quote) -> float:
    return (quote[0] + quote[1]) / 2


SPY_MAR = datetime.date(2027, 3, 19)
SPY_CALLS = {650.0: (43.00, 43.20), 640.0: (49.35, 49.55), 660.0: (37.05, 37.25), 620.0: (63.00, 63.20)}
SPY_PUTS = {650.0: (35.00, 35.20), 640.0: (31.45, 31.65), 660.0: (38.90, 39.10), 620.0: (25.45, 25.65)}
WMT_DEC = datetime.date(2026, 12, 18)
WMT_CALLS = {105.0: (5.75, 5.95), 102.5: (7.10, 7.30), 107.5: (4.65, 4.75)}
WMT_PUTS = {105.0: (6.00, 6.20), 102.5: (4.90, 5.00), 107.5: (7.30, 7.50)}
WMT_NOV = datetime.date(2026, 11, 20)
WMT_NOV_CALLS = {105.0: (3.35, 3.50), 102.5: (4.65, 4.75), 107.5: (2.35, 2.45)}
WMT_NOV_PUTS = {105.0: (3.95, 4.05), 102.5: (2.70, 2.80), 107.5: (5.40, 5.60)}


SPY_VOLS = {k: 0.27 for k in SPY_CALLS}
WMT_VOLS = {k: 0.39 for k in WMT_CALLS}


class ParityCarryTest(unittest.TestCase):
    def test_spy_march_resolves_almost_no_carry(self) -> None:
        found = carry.parity_carry(SPY_CALLS, SPY_PUTS, 648.12, tau(SPY_MAR), RATE, SPY_VOLS)
        self.assertAlmostEqual(0.0001, found.q_imp, delta=0.0001)
        self.assertAlmostEqual(0.0008, found.q_quote, delta=0.0001)   # 0.20 of quote width over F x tau = 241.6
        self.assertAlmostEqual(0.0043, found.q_amer, delta=0.0002)    # the 660 put's premium over the call's: 1.03
        # the interval [-0.0050, +0.0009] lies wholly below the calendar's 0.0115: the quotes exclude it, and the
        # American bias, which only lowers the truth, cannot bring it back
        self.assertTrue(found.excludes(0.0115))
        q = carry.implied_carry(SPY_CALLS, SPY_PUTS, 648.12, tau(SPY_MAR), RATE, SPY_VOLS, 0.0115)
        self.assertEqual(found.q_imp, q)
        t = tau(SPY_MAR)
        for k in (620.0, 650.0):
            vc = iv.implied_vol(mid(SPY_CALLS[k]), "CALL", 648.12, k, t, RATE, q).sigma
            vp = iv.implied_vol(mid(SPY_PUTS[k]), "PUT", 648.12, k, t, RATE, q).sigma
            self.assertLess(abs(vc - vp), 0.0005, k)
            vc0 = iv.implied_vol(mid(SPY_CALLS[k]), "CALL", 648.12, k, t, RATE, 0.0115).sigma
            vp0 = iv.implied_vol(mid(SPY_PUTS[k]), "PUT", 648.12, k, t, RATE, 0.0115).sigma
            self.assertGreater(vc0 - vp0, 0.017, k)

    def test_early_exercise_gap_is_positive_and_shrinks_with_carry(self) -> None:
        # SPY March 650 at 27 %: the put's premium exceeds the call's by about 0.88 at zero carry, 0.64 at 1.15 %
        t = tau(SPY_MAR)
        at_zero = carry.early_exercise_gap(648.12, 650.0, t, RATE, 0.0, 0.27)
        at_cal = carry.early_exercise_gap(648.12, 650.0, t, RATE, 0.0115, 0.27)
        self.assertAlmostEqual(0.88, at_zero, delta=0.02)
        self.assertAlmostEqual(0.64, at_cal, delta=0.02)
        self.assertGreater(at_zero, at_cal)
        with self.assertRaises(ValueError):
            carry.early_exercise_gap(648.12, 650.0, 0.0, RATE, 0.0, 0.27)

    def test_wmt_quotes_cannot_tell_their_carry_from_the_calendar(self) -> None:
        # the calendar's 0.009 lies inside [q_imp - q_amer - q_quote, q_imp + q_quote]: December's quote bound is
        # 0.0155, November's 0.0308 (tau 0.047), so the calendar yield is kept whatever the American bias
        found = carry.parity_carry(WMT_CALLS, WMT_PUTS, 104.23, tau(WMT_DEC), RATE, WMT_VOLS)
        self.assertGreater(found.q_quote, abs(found.q_imp - 0.009))
        self.assertFalse(found.excludes(0.009))
        self.assertIsNone(carry.implied_carry(WMT_CALLS, WMT_PUTS, 104.23, tau(WMT_DEC), RATE, WMT_VOLS, 0.009))
        nov = carry.parity_carry(WMT_NOV_CALLS, WMT_NOV_PUTS, 104.23, tau(WMT_NOV), RATE, WMT_VOLS)
        self.assertGreater(nov.q_quote, 0.03)
        self.assertIsNone(carry.implied_carry(WMT_NOV_CALLS, WMT_NOV_PUTS, 104.23, tau(WMT_NOV), RATE, WMT_VOLS,
                                              0.009))

    def test_the_american_bias_widens_the_interval_on_the_low_side_only(self) -> None:
        # a calendar yield 0.4 points BELOW q_imp is outside the quote bound (0.08) but inside the American bias
        # (0.43): it is not excluded. The same distance ABOVE q_imp is excluded.
        found = carry.parity_carry(SPY_CALLS, SPY_PUTS, 648.12, tau(SPY_MAR), RATE, SPY_VOLS)
        self.assertFalse(found.excludes(found.q_imp - 0.004))
        self.assertTrue(found.excludes(found.q_imp + 0.004))

    def test_error_bound_grows_with_quote_width(self) -> None:
        # calls widened from 0.20 to 0.40, puts kept at 0.20: (0.40 + 0.20) / (0.20 + 0.20) = 1.5
        wide = {k: (b - 0.10, a + 0.10) for k, (b, a) in SPY_CALLS.items()}
        tight = carry.parity_carry(SPY_CALLS, SPY_PUTS, 648.12, tau(SPY_MAR), RATE, SPY_VOLS)
        widened = carry.parity_carry(wide, SPY_PUTS, 648.12, tau(SPY_MAR), RATE, SPY_VOLS)
        self.assertAlmostEqual(1.5, widened.q_quote / tight.q_quote, delta=0.01)

    def test_too_few_strikes_or_too_large_a_gap(self) -> None:
        t = tau(SPY_MAR)
        self.assertIsNone(carry.implied_carry({650.0: (43.0, 43.2)}, {650.0: (35.0, 35.2)}, 648.12, t, RATE, SPY_VOLS,
                                              0.0115))
        self.assertIsNone(carry.implied_carry(SPY_CALLS, SPY_PUTS, 648.12, t, RATE, SPY_VOLS, 0.10))
        with self.assertRaises(ValueError):
            carry.parity_carry(SPY_CALLS, SPY_PUTS, 648.12, 0.0, RATE, SPY_VOLS)


if __name__ == "__main__":
    unittest.main()
