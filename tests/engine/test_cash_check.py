"""``aegis_desk.cash_check``: a ticket's or a proposal's legs against the cash the owner states per account (roadmap 2.6,
the owner's answer to open question 5), and how the structuring texts print it."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import unittest
from decimal import Decimal as D

from aegis_desk import alert_lint, cash_check as C, desk_config
from aegis_desk import structuring_text as T

TODAY = datetime.date(2026, 11, 17)
STAMP = datetime.datetime(2026, 11, 17, 19, 31, tzinfo=datetime.timezone.utc)
WMT_ROLL = [C.Step(D("-700.00")), C.Step(D("710.00"))]  # leg 1 buys the held call back at 3.50 x 200, leg 2 sells


def account(cash: str = "18500.00", as_of: datetime.date = datetime.date(2026, 11, 13)) -> desk_config.AccountEntry:
    return desk_config.AccountEntry("Brokerage (margin)", "margin", None, D(cash), as_of)


class PeakTest(unittest.TestCase):
    def test_a_credit_roll_needs_its_first_legs_cash(self) -> None:
        self.assertEqual((D("700.00"), 1), C.peak_need(WMT_ROLL))

    def test_a_short_put_opened_secures_its_strike_and_one_closed_releases_it(self) -> None:
        # roll a short 620 put to a 625 put: close the held one at its ask (pays 10.30 x 100, releases 62,000 that the
        # stated free cash did not count), then open the new one at its bid (receives 12.10 x 100, secures 62,500):
        # the roll needs the 500 of new collateral less the net credit of 180
        steps = [C.Step(D("-1030.00"), D("-62000")), C.Step(D("1210.00"), D("62500"))]
        self.assertEqual((D("320.00"), 2), C.peak_need(steps))
        opened_first = [C.Step(D("1210.00"), D("62500")), C.Step(D("-1030.00"), D("-62000"))]
        self.assertEqual((D("61290.00"), 1), C.peak_need(opened_first), "the order decides the peak")

    def test_a_ticket_that_takes_no_cash_out_needs_nothing(self) -> None:
        self.assertEqual((D("0.00"), 1), C.peak_need([C.Step(D("980.00"))]))

    def test_an_unknown_premium_makes_the_need_unknown(self) -> None:
        self.assertIsNone(C.peak_need([C.Step(D("-700.00")), C.Step(None)]))
        with self.assertRaises(ValueError):
            C.peak_need([])


class CheckTest(unittest.TestCase):
    def test_the_comparison_and_its_age(self) -> None:
        check = C.check(WMT_ROLL, account(), TODAY)
        self.assertEqual((C.STATUS_OK, D("700.00"), 1, 2, D("18500.00"), 2, None),
                         (check.status, check.need, check.after_step, check.steps, check.cash, check.age_trading_days,
                          check.shortfall))
        short = C.check(WMT_ROLL, account("500.00"), TODAY)
        self.assertEqual((C.STATUS_SHORT, D("200.00")), (short.status, short.shortfall))

    def test_every_unavailable_case(self) -> None:
        cases = {
            C.REASON_NO_ACCOUNT: C.check(WMT_ROLL, None, TODAY),
            C.REASON_NOT_STATED: C.check(WMT_ROLL, desk_config.AccountEntry("Roth IRA", "ira"), TODAY),
            C.REASON_NO_QUOTE: C.check([C.Step(None)], account(), TODAY),
            C.REASON_FUTURE: C.check(WMT_ROLL, account(as_of=datetime.date(2026, 11, 18)), TODAY),
            C.REASON_STALE: C.check(WMT_ROLL, account(as_of=datetime.date(2026, 11, 9)), TODAY),
        }
        for reason, check in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual((C.STATUS_UNAVAILABLE, reason), (check.status, check.reason))
                self.assertIsNone(check.shortfall)

    def test_five_trading_days_is_the_oldest_and_holidays_do_not_count(self) -> None:
        self.assertEqual(5, C.CASH_MAX_AGE_TRADING_DAYS)
        self.assertEqual((C.STATUS_OK, 5), (C.check(WMT_ROLL, account(as_of=datetime.date(2026, 11, 10)), TODAY).status,
                                            C.check(WMT_ROLL, account(as_of=datetime.date(2026, 11, 10)), TODAY)
                                            .age_trading_days))
        # Thanksgiving (Thu 2026-11-26) is no trading day: Fri 11-20 to Mon 11-30 is five trading days
        self.assertEqual(C.STATUS_OK, C.check(WMT_ROLL, account(as_of=datetime.date(2026, 11, 20)),
                                              datetime.date(2026, 11, 30)).status)

    def test_the_record(self) -> None:
        record = C.check(WMT_ROLL, account(), TODAY).as_dict()
        self.assertEqual({"status": "ok", "reason": None, "need": D("700.00"), "after_step": 1, "steps": 2,
                          "cash": D("18500.00"), "as_of": "2026-11-13", "age_trading_days": 2, "shortfall": None},
                         record)


class TextTest(unittest.TestCase):
    def lint(self, w: "T.Writer", item: str) -> "alert_lint.LintResult":
        text = item + "\n" + w.footer(STAMP)
        return alert_lint.check(text, w.emitted, rule_ids=[], identifiers=w.identifiers, clocks=w.clocks, as_of=STAMP,
                                footer="structuring")

    def test_each_reading_prints_one_linted_item(self) -> None:
        w = T.Writer()
        ok = T.cash_check_item(w, C.check(WMT_ROLL, account(), TODAY))
        self.assertEqual("- **cash check** cash needed at the peak, after step 1 of 2: 700.00 | stated cash 18,500.00 "
                         "as of 2026-11-13 (2 trading days old)", ok)
        self.assertTrue(self.lint(w, ok).ok, self.lint(w, ok).problems)
        w = T.Writer()
        short = T.cash_check_item(w, C.check(WMT_ROLL, account("500.00"), TODAY))
        self.assertTrue(short.startswith("- %s **cash check**" % T.STATUS_FAIL))
        self.assertTrue(short.endswith("| the stated cash is 200.00 less than the peak"))
        self.assertTrue(self.lint(w, short).ok, self.lint(w, short).problems)
        w = T.Writer()
        missing = T.cash_check_item(w, C.check(WMT_ROLL, None, TODAY))
        self.assertEqual("- **cash check** UNAVAILABLE: %s" % C.REASON_NO_ACCOUNT, missing)
        self.assertTrue(self.lint(w, missing).ok, self.lint(w, missing).problems)


if __name__ == "__main__":
    unittest.main()
