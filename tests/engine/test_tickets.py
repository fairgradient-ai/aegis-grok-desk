"""The Structuring Desk's tickets (``aegis_desk.tickets``; PR 3b, designer Q's D11 under the addendum S1, S5-S9, S11).

The action plans and their leg orders (S7: CLOSE shorts then longs; UNWIND_STOCK shorts, the shares, then longs; a
short call's ROLL closes the old call first, a long put's ROLL opens the new put first; MONETIZE_PUT the put alone;
LET_ASSIGN no leg), the exposure check of every prefix state through ``aegis_desk.approval.check_legs`` (both reversed
orders fail: the shares before the short calls, the new call before the old one), the conservative side and the mid
of every leg (designer Q's D4: a held leg's fresh conservative side from any grade A-E, a new leg's fresh mid), the
cash per step and the totals with the most cash the steps need at once, the realized estimate of each closing leg,
LET_ASSIGN's moneyness by the one-cent rule with its act-by and the cash if assigned, MONETIZE_PUT's moneyness, the
ROLL replacement through ``structuring.run_replacement`` (one code path with the roll analysis), the validity window
and the ticket id. Designer Q's D16 goldens are recomputed with the landed engine on copies of ``tests/fixtures/desk``
at the regular instant (2026-11-03T19:50:00Z, quotes stamped 19:31:12Z) and the expiry-day instant.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import datetime
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import List, Optional, Tuple

from aegis_desk import (alert_lint, approval, grouping, positions_csv, quote_cache, structuring, structuring_config,
                        structuring_text, tickets)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CRITERIA_FILE = os.path.join(ROOT, "tests", "fixtures", "init", "expected_structuring.toml")
CRITERIA = structuring_config.load_structuring_config(CRITERIA_FILE)
UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC)
STAMP = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
EXPIRY_DAY_NOW = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)
AFTER_CLOSE_NOW = datetime.datetime(2026, 11, 3, 21, 25, tzinfo=UTC)
HOLIDAY_NOW = datetime.datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
TODAY = datetime.date(2026, 11, 3)
D = Decimal
JAN15 = datetime.date(2027, 1, 15)
MAR19 = datetime.date(2027, 3, 19)
NOV20 = datetime.date(2026, 11, 20)
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
L = approval.Leg


class Desk:
    """A copy of tests/fixtures/desk with the fixture criteria (``replace`` rewrites one text of positions.csv)."""

    def __init__(self, owner: unittest.TestCase, replace: Optional[Tuple[str, str]] = None,
                 calendar: Optional[Tuple[str, str]] = None) -> None:
        self.tmp = tempfile.mkdtemp(prefix="aegis-tickets-")
        owner.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        shutil.copyfile(CRITERIA_FILE, os.path.join(self.root, "structuring.toml"))
        for name, change in (("positions.csv", replace), (os.path.join("research", "calendar.toml"), calendar)):
            if change is None:
                continue
            path = os.path.join(self.root, name)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            assert text.count(change[0]) == 1, change
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text.replace(*change))

    def market(self, fixture_set: str = "regular", now: datetime.datetime = NOW) -> quote_cache.MarketData:
        return quote_cache.open_market_data(self.root, now=now, offline=True, fixture_set=fixture_set, timeout_s=5.0,
                                            user_agent="aegis-desk-test")


def run(owner: unittest.TestCase, trade_id: str, action: str, fixture_set: str = "regular",
        now: datetime.datetime = NOW, expiration: Optional[datetime.date] = None, strike: Optional[str] = None,
        replace: Optional[Tuple[str, str]] = None, criteria: structuring_config.StructuringConfig = CRITERIA
        ) -> tickets.Ticket:
    market = Desk(owner, replace).market(fixture_set, now)
    return tickets.run_ticket(market, trade_id, action, criteria, new_expiration=expiration,
                              new_strike=None if strike is None else D(strike))


HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,close_price_per_unit,"
          "underlying_stop,note")
SHARES = "equity,WMT,,200,,,,Brokerage (margin),AG-20261101-01,2026-10-20,,98.50,,open,,,,,"
SHARES_LATER = "equity,WMT,,100,,,,Brokerage (margin),AG-20261101-01,2026-10-25,,101.00,,open,,,,,"
CALL = "option,WMT,WMT261218C00110000,-2,,,,Brokerage (margin),AG-20261101-01,2026-10-20,-560.00,,,open,,,,,"
LONG_CALL = "option,WMT,WMT261218C00120000,2,,,,Brokerage (margin),AG-20261101-01,2026-10-20,240.00,,,open,,,,,"
PUT = "option,WMT,WMT261218P00090000,2,,,,Brokerage (margin),AG-20261101-01,2026-10-20,300.00,,,open,,,,,"
SHORT_PUT = "option,WMT,WMT261218P00090000,-1,,,,Brokerage (margin),AG-20261101-01,2026-10-20,-150.00,,,open,,,,,"


def group_of(*rows: str, trade_id: str = "AG-20261101-01") -> grouping.PositionGroup:
    parsed = positions_csv.parse_positions_text("\n".join((HEADER,) + rows) + "\n", today=TODAY)
    assert not parsed.file_errors and not parsed.quarantined, (parsed.file_errors, parsed.quarantined)
    return [g for g in grouping.group_positions(parsed.rows) if g.trade_id == trade_id][0]


def shape(legs: List[tickets.PlanLeg]) -> List[Tuple[int, str, str, int, Optional[int]]]:
    return [(leg.step, leg.intent, leg.instrument, leg.quantity, leg.row_number) for leg in legs]


# ------------------------------------------------------------------------------------------------
# Action plans and leg orders (S7)
# ------------------------------------------------------------------------------------------------


class PlanTest(unittest.TestCase):
    def test_the_vocabulary(self) -> None:
        self.assertEqual(("TICKET", "NOT_APPLICABLE", "UNAVAILABLE", "REFUSED"), tickets.VERDICTS)
        self.assertEqual(("close_short", "close_long", "close_shares", "open_short", "open_long"), tickets.INTENTS)
        self.assertEqual(("approval_level", "intermediate_exposure"), tickets.REFUSED_CODES)
        self.assertEqual("replacement_unpriced", tickets.CAUSE_REPLACEMENT_UNPRICED)

    def test_close_closes_the_shorts_then_the_longs(self) -> None:
        collar = group_of(SHARES, PUT, CALL)
        self.assertEqual([(1, "close_short", "call", 2, 4), (2, "close_long", "put", 2, 3)],
                         shape(tickets.plan_legs(collar, "CLOSE")))
        vertical = group_of(LONG_CALL, CALL)
        self.assertEqual([(1, "close_short", "call", 2, 3), (2, "close_long", "call", 2, 2)],
                         shape(tickets.plan_legs(vertical, "CLOSE")))

    def test_unwind_closes_the_shorts_then_the_shares_then_the_longs(self) -> None:
        collar = group_of(PUT, SHARES, CALL, SHARES_LATER)
        self.assertEqual([(1, "close_short", "call", 2, 4), (2, "close_shares", "shares", 200, 3),
                          (3, "close_shares", "shares", 100, 5), (4, "close_long", "put", 2, 2)],
                         shape(tickets.plan_legs(collar, "UNWIND_STOCK")))

    def test_monetize_closes_the_put_alone_and_let_assign_has_no_leg(self) -> None:
        collar = group_of(SHARES, PUT, CALL)
        self.assertEqual([(1, "close_long", "put", 2, 3)], shape(tickets.plan_legs(collar, "MONETIZE_PUT")))
        self.assertEqual([], tickets.plan_legs(group_of(SHARES, CALL), "LET_ASSIGN"))

    def test_roll_orders(self) -> None:
        covered = group_of(SHARES, CALL)
        new_call = tickets.Replacement(root="WMT", right="CALL", strike=D("115"), expiration=JAN15, contracts=2)
        legs = tickets.plan_legs(covered, "ROLL", new_call)
        self.assertEqual([(1, "close_short", "call", 2, 3), (2, "open_short", "call", 2, None)], shape(legs))
        self.assertEqual(("WMT", "CALL", D("115"), JAN15, None), (legs[1].root, legs[1].right, legs[1].strike,
                                                                   legs[1].expiration, legs[1].symbol))
        protective = group_of(SHARES, PUT)
        new_put = tickets.Replacement(root="WMT", right="PUT", strike=D("85"), expiration=JAN15, contracts=2)
        self.assertEqual([(1, "open_long", "put", 2, None), (2, "close_long", "put", 2, 3)],
                         shape(tickets.plan_legs(protective, "ROLL", new_put)))
        with self.assertRaises(ValueError):
            tickets.plan_legs(covered, "ROLL")
        self.assertEqual(3, tickets.replaced_row(covered).row_number)
        self.assertEqual(3, tickets.replaced_row(protective).row_number)
        self.assertIsNone(tickets.replaced_row(group_of(SHARES)))

    def test_approval_legs(self) -> None:
        legs = tickets.plan_legs(group_of(SHARES, CALL), "UNWIND_STOCK")
        self.assertEqual([L("close", "short", "call", 2), L("close", "long", "shares", 200)], tickets.approval_legs(legs))


# ------------------------------------------------------------------------------------------------
# The exposure check of every prefix state (approval.check_legs)
# ------------------------------------------------------------------------------------------------


class ExposureTest(unittest.TestCase):
    def test_canonical_orders_pass_at_level_2(self) -> None:
        covered = group_of(SHARES, CALL)
        protective = group_of(SHARES, PUT)
        new_call = tickets.Replacement("WMT", "CALL", D("115"), JAN15, 2)
        new_put = tickets.Replacement("WMT", "PUT", D("85"), JAN15, 2)
        cases = [(covered, "CLOSE", None, 0), (covered, "UNWIND_STOCK", None, 0), (covered, "ROLL", new_call, 1),
                 (protective, "MONETIZE_PUT", None, 0), (protective, "UNWIND_STOCK", None, 0),
                 (protective, "ROLL", new_put, 2)]
        for group, action, replacement, level in cases:
            with self.subTest(action=action, structure=group.structure):
                holdings = approval.holdings_of(group.open_legs, group.account, group.underlying)
                result = tickets.check_plan(holdings, tickets.plan_legs(group, action, replacement), 2)
                self.assertEqual((True, level, None), (result.ok, result.required_level, result.exposure_step))

    def test_both_reversed_orders_fail(self) -> None:
        covered = group_of(SHARES, CALL)
        holdings = approval.holdings_of(covered.open_legs, covered.account, covered.underlying)
        unwind = tickets.plan_legs(covered, "UNWIND_STOCK")
        roll = tickets.plan_legs(covered, "ROLL", tickets.Replacement("WMT", "CALL", D("115"), JAN15, 2))
        for legs in (unwind, roll):
            reversed_result = tickets.check_plan(holdings, list(reversed(legs)), 2)
            self.assertEqual((False, 4, 1), (reversed_result.ok, reversed_result.required_level,
                                             reversed_result.exposure_step))
            self.assertEqual(2, reversed_result.steps[0].after.uncovered_calls)
            # even an owner at level 4 or 5 never gets the reversed order: the exposure step is an invariant breach
            self.assertEqual(1, tickets.check_plan(holdings, list(reversed(legs)), 5).exposure_step)
        self.assertEqual("opening 2 new short calls without enough free shares (0 free, 200 needed)",
                         tickets.check_plan(holdings, list(reversed(roll)), 2).refusal.words)
        self.assertEqual("closing 200 of your shares, which leaves 2 short calls uncovered",
                         tickets.check_plan(holdings, list(reversed(unwind)), 2).refusal.words)

    def test_step_records_are_every_prefix_state(self) -> None:
        covered = group_of(SHARES, CALL)
        holdings = approval.holdings_of(covered.open_legs, covered.account, covered.underlying)
        result = tickets.check_plan(holdings, tickets.plan_legs(covered, "UNWIND_STOCK"), 2)
        records = tickets.step_records(result)
        self.assertEqual([1, 2], [record["step"] for record in records])
        self.assertEqual(["close_option", "close_shares"], [record["kind"] for record in records])
        self.assertEqual([0, 0], [record["level"] for record in records])
        self.assertEqual({"shares": 200, "long_calls": 0, "short_calls": 0, "long_puts": 0, "short_puts": 0},
                         records[0]["after"])
        self.assertEqual([0, 0], [record["uncovered_calls"] for record in records])
        self.assertEqual("closing your 2 short calls", records[0]["words"])
        with self.assertRaises(ValueError):
            tickets.check_plan(holdings, [], 2)


# ------------------------------------------------------------------------------------------------
# Cash, realized, moneyness (Decimal)
# ------------------------------------------------------------------------------------------------


class MoneyTest(unittest.TestCase):
    def test_step_cash_signs(self) -> None:
        self.assertEqual((D("-700.00"), D("-685.00")), tickets.step_cash("close_short", 2, 100, D("3.50"), D("3.425")))
        self.assertEqual((D("710.00"), D("720.00")), tickets.step_cash("open_short", 2, 100, D("3.55"), D("3.60")))
        self.assertEqual((D("-2310.00"), D("-2300.00")), tickets.step_cash("open_long", 1, 100, D("23.10"), D("23.00")))
        self.assertEqual((D("980.00"), D("995.00")), tickets.step_cash("close_long", 1, 100, D("9.80"), D("9.95")))
        self.assertEqual((D("20840.00"), D("20846.00")), tickets.step_cash("close_shares", 200, 1, D("104.20"),
                                                                           D("104.23")))
        self.assertEqual((D("-5.00"), None), tickets.step_cash("close_short", 1, 100, D("0.05"), None))
        with self.assertRaises(ValueError):
            tickets.step_cash("short_to_open", 1, 100, D("1"), D("1"))

    def test_cash_summary_and_the_most_needed_at_once(self) -> None:
        roll = tickets.cash_summary([(D("-700.00"), D("-685.00")), (D("710.00"), D("720.00"))])
        self.assertEqual((D("10.00"), D("35.00"), D("700.00")), (roll.net_conservative, roll.net_mid, roll.peak_needed))
        self.assertEqual((D("-700.00"), D("10.00")), roll.prefix)
        debit = tickets.cash_summary([(D("-660.00"), D("-640.00")), (D("650.00"), D("660.00"))])
        self.assertEqual((D("-10.00"), D("20.00"), D("660.00")), (debit.net_conservative, debit.net_mid,
                                                                  debit.peak_needed))
        credit = tickets.cash_summary([(D("64810.00"), D("64812.00")), (D("980.00"), D("995.00"))])
        self.assertEqual((D("65790.00"), D("65807.00"), D("0.00")), (credit.net_conservative, credit.net_mid,
                                                                    credit.peak_needed))
        no_mid = tickets.cash_summary([(D("-700.00"), None), (D("710.00"), D("720.00"))])
        self.assertEqual((D("10.00"), None, D("700.00")), (no_mid.net_conservative, no_mid.net_mid, no_mid.peak_needed))
        self.assertEqual({"net_conservative": D("10.00"), "net_mid": None, "peak_needed": D("700.00"),
                          "prefix": [D("-700.00"), D("10.00")]}, no_mid.as_dict())

    def test_realized_estimates(self) -> None:
        covered = group_of(SHARES, CALL)
        call_row, share_row = covered.open_legs[1], covered.open_legs[0]
        self.assertEqual((3, D("-140.00"), D("-125.00")), dataclasses.astuple(
            tickets.realized_estimate(call_row, D("3.50"), D("3.425"))))
        self.assertEqual((2, D("1140.00"), D("1146.00")), dataclasses.astuple(
            tickets.realized_estimate(share_row, D("104.20"), D("104.23"))))
        self.assertIsNone(tickets.realized_estimate(share_row, D("104.20"), None).mid)

    def test_assignment_realized(self) -> None:
        covered = group_of(SHARES, CALL)
        self.assertEqual(D("2860.00"), tickets.assignment_realized(covered.open_legs[1], covered))
        # FIFO by opened date: the first 200 shares (at 98.50) are delivered, the later lot stays
        lots = group_of(SHARES_LATER, SHARES, CALL)
        self.assertEqual(D("2860.00"), tickets.assignment_realized([r for r in lots.open_legs if r.is_option][0], lots))
        short = group_of(SHORT_PUT)
        self.assertEqual(D("150.00"), tickets.assignment_realized(short.open_legs[0], short))
        uncovered = group_of(CALL)
        self.assertIsNone(tickets.assignment_realized(uncovered.open_legs[0], uncovered))

    def test_put_moneyness_by_the_one_cent_rule(self) -> None:
        protective = group_of(SHARES, PUT)
        put = protective.open_legs[1]
        out = tickets.put_moneyness(put, D("1.20"), D("104.23"))
        self.assertEqual((False, D("0"), D("1.20")), (out.itm, out.intrinsic, out.time_value))
        inside = tickets.put_moneyness(put, D("12.00"), D("80.50"))
        self.assertEqual((True, D("9.50"), D("2.50")), (inside.itm, inside.intrinsic, inside.time_value))
        edge = tickets.put_moneyness(put, D("0.30"), D("89.995"))
        self.assertFalse(edge.itm)  # 0.005 below the strike: not in the money by the one-cent rule
        self.assertTrue(tickets.put_moneyness(put, D("0.30"), D("89.99")).itm)


# ------------------------------------------------------------------------------------------------
# Fixture goldens (designer Q's D16 under S17)
# ------------------------------------------------------------------------------------------------


def legs_of(ticket: tickets.Ticket) -> List[Tuple]:
    return [(leg.plan.intent, leg.plan.quantity, leg.plan.symbol, leg.quote.bid, leg.quote.ask, leg.quote.mid,
             leg.quote.grade, leg.quote.spread_pct, leg.quote.quote_age_min, leg.quote.side, leg.quote.conservative,
             leg.cash_conservative, leg.cash_mid) for leg in ticket.legs]


def totals(ticket: tickets.Ticket) -> Tuple:
    return (ticket.cash.net_conservative, ticket.cash.net_mid, ticket.cash.peak_needed,
            sum((item.conservative for item in ticket.realized), D(0)),
            sum((item.mid for item in ticket.realized), D(0)))


class WmtGoldenTest(unittest.TestCase):
    def test_close(self) -> None:
        ticket = run(self, WMT, "CLOSE")
        self.assertEqual(("TICKET", None, "TK-AG-20261020-01-CLOSE-20261103T195000Z"),
                         (ticket.verdict, ticket.cause, ticket.ticket_id))
        self.assertEqual([("close_short", 2, "WMT261120C00105000", D("3.35"), D("3.50"), D("3.425"), "B", D("4.38"), 18,
                           "ask", D("3.50"), D("-700.00"), D("-685.00"))], legs_of(ticket))
        self.assertEqual((D("-700.00"), D("-685.00"), D("700.00"), D("-16.00"), D("-1.00")), totals(ticket))
        self.assertEqual((0, 2, True), (ticket.approval.required_level, ticket.owner_level, ticket.approval.ok))
        self.assertEqual((datetime.datetime(2026, 11, 3, 20, 20, tzinfo=UTC), True), (ticket.valid_until,
                                                                                      ticket.valid_full))
        self.assertEqual(1, ticket.requests["made"])
        self.assertEqual(STAMP, tickets.ticket_as_of(ticket))
        self.assertEqual(STAMP, ticket.legs[0].quote.as_of)

    def test_roll_with_the_january_115_call(self) -> None:
        ticket = run(self, WMT, "ROLL", expiration=JAN15, strike="115")
        self.assertEqual("TICKET", ticket.verdict)
        self.assertEqual([("close_short", 2, "WMT261120C00105000", D("3.35"), D("3.50"), D("3.425"), "B", D("4.38"), 18,
                           "ask", D("3.50"), D("-700.00"), D("-685.00")),
                          ("open_short", 2, "WMT270115C00115000", D("3.55"), D("3.65"), D("3.60"), "B", D("2.78"), 18,
                           "bid", D("3.55"), D("710.00"), D("720.00"))], legs_of(ticket))
        self.assertEqual((D("10.00"), D("35.00"), D("700.00"), D("-16.00"), D("-1.00")), totals(ticket))
        candidate = ticket.replacement.candidate
        self.assertEqual(("ELIGIBLE", D("0.0467"), D("0.05"), D("10.00")), (candidate.status, candidate.score,
                                                                          candidate.net_credit,
                                                                          candidate.net_credit_total))
        self.assertEqual((D("19006.00"), D("3994.00"), D("95.03")), (candidate.after.max_loss, candidate.after.max_profit,
                                                                    candidate.after.breakeven))
        self.assertEqual(["crosses_ex_dividend"], [label.code for label in candidate.accepted])
        self.assertEqual((1, True), (ticket.approval.required_level, ticket.approval.ok))
        self.assertEqual(2, ticket.requests["made"])

    def test_unwind_stock(self) -> None:
        ticket = run(self, WMT, "UNWIND_STOCK")
        self.assertEqual([("close_short", 2, "WMT261120C00105000", D("3.35"), D("3.50"), D("3.425"), "B", D("4.38"), 18,
                           "ask", D("3.50"), D("-700.00"), D("-685.00")),
                          ("close_shares", 200, "WMT", D("104.20"), D("104.26"), D("104.23"), "A", D("0.06"), 18,
                           "bid", D("104.20"), D("20840.00"), D("20846.00"))], legs_of(ticket))
        self.assertEqual((D("20140.00"), D("20161.00"), D("700.00"), D("1124.00"), D("1145.00")), totals(ticket))
        self.assertEqual([(4, D("-16.00"), D("-1.00")), (3, D("1140.00"), D("1146.00"))],
                         [dataclasses.astuple(item) for item in ticket.realized])
        self.assertEqual((0, None), (ticket.approval.required_level, ticket.approval.exposure_step))

    def test_let_assign(self) -> None:
        ticket = run(self, WMT, "LET_ASSIGN")
        self.assertEqual(("TICKET", [], None, []), (ticket.verdict, ticket.legs, ticket.approval, ticket.realized))
        (leg,) = ticket.let_assign
        self.assertEqual((4, "CALL", D("105"), NOV20, 2, 200, D("104.23"), False, D("21000.00"), D("1984.00")),
                         (leg.row_number, leg.right, leg.strike, leg.expiration, leg.contracts, leg.shares,
                          leg.spot_mid, leg.itm, leg.cash_if_assigned, leg.realized_if_assigned))
        self.assertEqual("act by Fri 2026-11-20 16:30 ET (21:30 UTC)", leg.act_by_text)
        self.assertEqual(datetime.datetime(2026, 11, 20, 21, 30, tzinfo=UTC), leg.act_by.utc)
        self.assertEqual((True, False, datetime.date(2026, 11, 24)), (leg.dividend.known, leg.dividend.applicable,
                                                                      leg.dividend.ex_date))
        self.assertEqual(1, ticket.requests["made"])

    def test_monetize_put_is_not_applicable(self) -> None:
        ticket = run(self, WMT, "MONETIZE_PUT")
        self.assertEqual(("NOT_APPLICABLE", structuring.NA_ACTION), (ticket.verdict, ticket.cause))
        self.assertEqual("AG-20261020-01 is a covered call: the put of a protective put or a collar is what "
                         "MONETIZE_PUT closes", ticket.reason)
        self.assertEqual(0, ticket.requests["made"])
        self.assertEqual(NOW, tickets.ticket_as_of(ticket))


class NvdaGoldenTest(unittest.TestCase):
    def test_roll_with_the_january_160_call_is_a_ticket_with_its_failing_criteria(self) -> None:
        ticket = run(self, NVDA, "ROLL", expiration=JAN15, strike="160")
        self.assertEqual("TICKET", ticket.verdict)
        self.assertEqual([("close_short", 2, "NVDA261120C00150000", D("3.10"), D("3.30"), D("3.20"), "C", D("6.25"), 18,
                           "ask", D("3.30"), D("-660.00"), D("-640.00")),
                          ("open_short", 2, "NVDA270115C00160000", D("3.25"), D("3.35"), D("3.30"), "B", D("3.03"), 18,
                           "bid", D("3.25"), D("650.00"), D("660.00"))], legs_of(ticket))
        self.assertEqual((D("-10.00"), D("20.00"), D("660.00"), D("-50.00"), D("-30.00")), totals(ticket))
        candidate = ticket.replacement.candidate
        self.assertEqual(("FAILS_CRITERION", ["debit_roll", "crosses_ex_dividend"]),
                         (candidate.status, [reason.code for reason in candidate.reasons]))


class SpyGoldenTest(unittest.TestCase):
    def test_monetize_put(self) -> None:
        ticket = run(self, SPY, "MONETIZE_PUT")
        self.assertEqual([("close_long", 1, "SPY261218P00620000", D("9.80"), D("10.10"), D("9.95"), "B", D("3.02"), 18,
                           "bid", D("9.80"), D("980.00"), D("995.00"))], legs_of(ticket))
        self.assertEqual((D("980.00"), D("995.00"), D("0.00"), D("-150.00"), D("-135.00")), totals(ticket))
        (put,) = ticket.puts
        self.assertEqual((8, False, D("648.12"), D("0"), D("9.80")), (put.row_number, put.itm, put.spot_mid,
                                                                     put.intrinsic, put.time_value))

    def test_unwind_stock(self) -> None:
        ticket = run(self, SPY, "UNWIND_STOCK")
        self.assertEqual([("close_shares", 100, "SPY", D("648.10"), D("648.14"), D("648.12"), "A", D("0.01"), 18,
                           "bid", D("648.10"), D("64810.00"), D("64812.00")),
                          ("close_long", 1, "SPY261218P00620000", D("9.80"), D("10.10"), D("9.95"), "B", D("3.02"), 18,
                           "bid", D("9.80"), D("980.00"), D("995.00"))], legs_of(ticket))
        self.assertEqual((D("65790.00"), D("65807.00"), D("0.00"), D("1420.00"), D("1437.00")), totals(ticket))

    def test_roll_with_the_march_610_put(self) -> None:
        ticket = run(self, SPY, "ROLL", expiration=MAR19, strike="610")
        self.assertEqual([("open_long", 1, "SPY270319P00610000", D("22.90"), D("23.10"), D("23.00"), "A", D("0.87"), 18,
                           "ask", D("23.10"), D("-2310.00"), D("-2300.00")),
                          ("close_long", 1, "SPY261218P00620000", D("9.80"), D("10.10"), D("9.95"), "B", D("3.02"), 18,
                           "bid", D("9.80"), D("980.00"), D("995.00"))], legs_of(ticket))
        self.assertEqual((D("-1330.00"), D("-1305.00"), D("2310.00"), D("-150.00"), D("-135.00")), totals(ticket))
        candidate = ticket.replacement.candidate
        self.assertEqual(("replacement_put", "ELIGIBLE", D("4700.00"), D("657.00"), True),
                         (candidate.kind, candidate.status, candidate.after.max_loss, candidate.after.breakeven,
                          candidate.after.unbounded_up))
        self.assertEqual((2, True), (ticket.approval.required_level, ticket.approval.ok))

    def test_let_assign_is_not_applicable(self) -> None:
        ticket = run(self, SPY, "LET_ASSIGN")
        self.assertEqual(("NOT_APPLICABLE", "AG-20261028-03 holds no short option: only a short option can be left to "
                          "assignment"), (ticket.verdict, ticket.reason))


class ExpiryDayTest(unittest.TestCase):
    def test_close_at_grade_e_takes_the_conservative_ask(self) -> None:
        ticket = run(self, WMT, "CLOSE", "expiry_day", EXPIRY_DAY_NOW)
        self.assertEqual([("close_short", 2, "WMT261120C00105000", D("0.12"), D("0.16"), D("0.14"), "E", D("28.57"), 18,
                           "ask", D("0.16"), D("-32.00"), D("-28.00"))], legs_of(ticket))
        self.assertEqual(structuring.GRADE_E_NOTE, ticket.legs[0].quote.note)
        self.assertEqual((D("-32.00"), D("-28.00"), D("32.00"), D("652.00"), D("656.00")), totals(ticket))

    def test_roll_on_expiration_day(self) -> None:
        ticket = run(self, WMT, "ROLL", "expiry_day", EXPIRY_DAY_NOW, expiration=JAN15, strike="115")
        self.assertEqual("TICKET", ticket.verdict)
        self.assertEqual([D("-32.00"), D("590.00")], [leg.cash_conservative for leg in ticket.legs])
        self.assertEqual(["earnings_unknown"], [reason.code for reason in ticket.replacement.candidate.reasons])


def assert_lints(case: unittest.TestCase, rendered: structuring_text.Rendered) -> None:
    """The text passed the strict lint in structuring mode, carries no forbidden phrase, OCC symbol or account name the
    owner typed, and ends with the structuring footer."""
    case.assertTrue(rendered.ok, (rendered.codes, rendered.lint.problems, rendered.attempted))
    case.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(rendered.text)])
    case.assertIsNone(structuring_text.OCC_RE.search(rendered.text))
    case.assertNotIn("Brokerage (margin)", rendered.text)
    case.assertNotIn("Roth IRA", rendered.text)
    case.assertTrue(rendered.text.endswith(alert_lint.structuring_footer_line(rendered.as_of)))


WMT_EX_DIVIDEND = ('ex_dividend = { date = 2026-11-24, amount = 0.2350, status = "confirmed", source = "owner-entered '
                   '(fixture)", checked_utc = 2026-11-01T12:00:00Z }\n')


class LetAssignDividendTest(unittest.TestCase):
    """The LET_ASSIGN ticket's dividend line on calendar copies (the fixture's own ex-date, 2026-11-24, falls after the
    call's last trading day: ``WmtGoldenTest`` and the tool's golden text)."""

    def dividend_line(self, new: str) -> Tuple[tickets.LetAssignLeg, str]:
        desk = Desk(self, calendar=(WMT_EX_DIVIDEND, new))
        ticket = tickets.run_ticket(desk.market(), WMT, "LET_ASSIGN", CRITERIA)
        self.assertEqual("TICKET", ticket.verdict)
        rendered = tickets.render_ticket(ticket)
        assert_lints(self, rendered)
        lines = rendered.text.split("\n")
        self.assertEqual("valid until 20:20 UTC: after that its quotes are too old, and a new ticket needs a new request",
                         lines[-3])
        return ticket.let_assign[0], lines[-4]

    def test_an_ex_date_before_the_last_trading_day(self) -> None:
        leg, line = self.dividend_line(WMT_EX_DIVIDEND.replace("2026-11-24", "2026-11-19"))
        self.assertEqual((True, True, datetime.date(2026, 11, 18)), (leg.dividend.known, leg.dividend.applicable,
                                                                     leg.dividend.cum_day))
        self.assertEqual("the ex-dividend date 2026-11-19 (cum 2026-11-18) falls inside the call's life: an early "
                         "exercise before the ex-date is possible", line)

    def test_a_passed_ex_date_counts_as_unknown(self) -> None:
        leg, line = self.dividend_line(WMT_EX_DIVIDEND.replace("2026-11-24", "2026-11-02"))
        self.assertEqual((False, False), (leg.dividend.known, leg.dividend.applicable))
        self.assertEqual("the listed ex-dividend date 2026-11-02 has passed, so the next one is unknown and counts as "
                         "inside the call's life", line)

    def test_an_unknown_dividend(self) -> None:
        leg, line = self.dividend_line('ex_dividend = { status = "unknown", source = "owner-entered (fixture)", '
                                       'checked_utc = 2026-11-01T12:00:00Z }\n')
        self.assertEqual(("unknown", False), (leg.dividend.status, leg.dividend.known))
        self.assertEqual("the dividend is unknown in research/calendar.toml, which counts as inside the call's life", line)

    def test_a_confirmed_absence_prints_no_dividend_line(self) -> None:
        leg, line = self.dividend_line('ex_dividend = { status = "none", source = "owner-entered (fixture)", '
                                       'checked_utc = 2026-11-01T12:00:00Z }\n')
        self.assertEqual(("none", True, None), (leg.dividend.status, leg.dividend.known, leg.dividend.ex_date))
        self.assertTrue(line.startswith("if assigned: 200 WMT shares are called away at the strike, cash +21000.00"),
                        line)


# ------------------------------------------------------------------------------------------------
# Answers that print no ticket
# ------------------------------------------------------------------------------------------------


class QuietAnswerTest(unittest.TestCase):
    def test_market_closed_after_the_close_and_on_a_holiday(self) -> None:
        for fixture_set, now in (("after_close", AFTER_CLOSE_NOW), ("holiday", HOLIDAY_NOW)):
            for action, extra in (("CLOSE", {}), ("ROLL", {"expiration": JAN15, "strike": "115"})):
                with self.subTest(fixture_set=fixture_set, action=action):
                    ticket = run(self, WMT, action, fixture_set, now, **extra)
                    self.assertEqual(("UNAVAILABLE", "market_closed", structuring.TICKET_CLOSED_WORDS, 0),
                                     (ticket.verdict, ticket.cause, ticket.reason, ticket.requests["made"]))

    def test_stale_quotes(self) -> None:
        ticket = run(self, WMT, "CLOSE", "stale")
        self.assertEqual(("UNAVAILABLE", "held_leg_unusable", 1), (ticket.verdict, ticket.cause,
                                                                   ticket.requests["made"]))
        let = run(self, WMT, "LET_ASSIGN", "stale")
        self.assertEqual(("UNAVAILABLE", "spot_unusable"), (let.verdict, let.cause))

    def test_replacement_not_listed_and_the_expiration_rule(self) -> None:
        ticket = run(self, WMT, "ROLL", expiration=JAN15, strike="117")
        self.assertEqual(("UNAVAILABLE", "replacement_not_listed", 2), (ticket.verdict, ticket.cause,
                                                                        ticket.requests["made"]))
        for day in (NOV20, datetime.date(2026, 11, 13)):
            with self.subTest(day=day):
                with self.assertRaises(tickets.TicketUsageError):
                    run(self, WMT, "ROLL", expiration=day, strike="110")

    def test_a_listed_replacement_that_cannot_be_priced(self) -> None:
        cases = ((datetime.date(2026, 12, 18), "the 2026-12-18 180 call cannot be priced: no bid"),
                 (JAN15, "the 2027-01-15 180 call cannot be priced: the spread is wider than the quote grades allow"))
        for day, words in cases:
            with self.subTest(day=day):
                ticket = run(self, NVDA, "ROLL", expiration=day, strike="180")
                self.assertEqual(("UNAVAILABLE", tickets.CAUSE_REPLACEMENT_UNPRICED, words, 2, []),
                                 (ticket.verdict, ticket.cause, ticket.reason, ticket.requests["made"], ticket.legs))

    def test_refused_below_the_needed_level(self) -> None:
        market = Desk(self).market()
        cfg = dataclasses.replace(market.cfg, owner=dataclasses.replace(market.cfg.owner, approval_level=1))
        market = dataclasses.replace(market, cfg=cfg)
        ticket = tickets.run_ticket(market, SPY, "ROLL", CRITERIA, new_expiration=MAR19, new_strike=D("610"))
        self.assertEqual(("REFUSED", "approval_level", 2, 1), (ticket.verdict, ticket.refused_code,
                                                               ticket.approval.required_level, ticket.owner_level))
        self.assertEqual(0, ticket.requests["made"])  # refused before any quote is asked for
        closing = tickets.run_ticket(market, SPY, "MONETIZE_PUT", CRITERIA)
        self.assertEqual("TICKET", closing.verdict)

    def test_disabled_roll_section(self) -> None:
        disabled = dataclasses.replace(CRITERIA, roll=dataclasses.replace(CRITERIA.roll, enabled=False))
        ticket = run(self, WMT, "ROLL", expiration=JAN15, strike="115", criteria=disabled)
        self.assertEqual(("NOT_APPLICABLE", structuring.NA_DISABLED), (ticket.verdict, ticket.cause))
        # "roll: disabled ..." would start a segment with an imperative the structuring lint refuses (WITHHELD, exit 1).
        self.assertEqual("the roll section is disabled in your structuring.toml", tickets.quiet_reason(ticket))
        rendered = tickets.render_ticket(ticket)
        assert_lints(self, rendered)
        self.assertEqual("NOT_APPLICABLE | AG-20261020-01 ROLL ticket | the roll section is disabled in your "
                         "structuring.toml", rendered.text.split("\n")[0])

    def test_unknown_trade(self) -> None:
        with self.assertRaises(structuring.StructuringError) as caught:
            run(self, "AG-20990101-01", "CLOSE")
        self.assertEqual("unknown_trade", caught.exception.code)


# ------------------------------------------------------------------------------------------------
# The text: built only through structuring_text and linted in structuring mode
# ------------------------------------------------------------------------------------------------


class TextTest(unittest.TestCase):
    def assert_lints(self, rendered: structuring_text.Rendered) -> None:
        assert_lints(self, rendered)

    def test_every_golden_lints(self) -> None:
        cases = [(WMT, "CLOSE", {}), (WMT, "ROLL", {"expiration": JAN15, "strike": "115"}), (WMT, "UNWIND_STOCK", {}),
                 (WMT, "LET_ASSIGN", {}), (WMT, "MONETIZE_PUT", {}), (NVDA, "ROLL", {"expiration": JAN15, "strike": "160"}),
                 (SPY, "MONETIZE_PUT", {}), (SPY, "UNWIND_STOCK", {}), (SPY, "ROLL", {"expiration": MAR19, "strike": "610"}),
                 (SPY, "LET_ASSIGN", {}), (WMT, "ROLL", {"expiration": JAN15, "strike": "117"})]
        for trade_id, action, extra in cases:
            with self.subTest(trade_id=trade_id, action=action):
                self.assert_lints(tickets.render_ticket(run(self, trade_id, action, **extra)))

    def test_the_wmt_roll_text(self) -> None:
        rendered = tickets.render_ticket(run(self, WMT, "ROLL", expiration=JAN15, strike="115"))
        self.assert_lints(rendered)
        lines = rendered.text.split("\n")
        self.assertEqual("TICKET AG-20261020-01 | WMT covered call | a roll of the short call (action ROLL) | account 1 "
                         "of desk.toml (margin) | computed 19:50 UTC, valid until 20:20 UTC (30 min)", lines[0])
        self.assertEqual("leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | "
                         "quote grade B, Tradier sandbox, 15-min delayed, as of 19:31 UTC", lines[1])
        self.assertEqual("leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60 | "
                         "quote grade B, Tradier sandbox, 15-min delayed, as of 19:31 UTC", lines[2])
        self.assertIn("cash: +10.00 at the conservative prices (mid +35.00); the most the steps need at once: 700.00; "
                      "the desk does not see your cash balance", lines)
        self.assertEqual(STAMP, rendered.as_of)

    def test_a_missing_mid_prints_the_conservative_side_alone(self) -> None:
        w = structuring_text.Writer()
        self.assertEqual("cash: -700.00 at the conservative prices (no mid: a quote side is stale); the most the steps "
                         "need at once: 700.00; the desk does not see your cash balance",
                         tickets.cash_words(w, tickets.cash_summary([(D("-700.00"), None)])))
        self.assertEqual("realized on the closing leg: -16.00 at the conservative side (no mid: a quote side is stale)",
                         tickets.realized_words(w, [tickets.RealizedLeg(4, D("-16.00"), None)]))
        self.assertEqual(["-700.00", "700.00", "-16.00"], w.emitted)

    def test_a_window_cut_short_by_the_close(self) -> None:
        ticket = run(self, WMT, "CLOSE")
        ticket.valid_until, ticket.valid_full = datetime.datetime(2026, 11, 3, 21, 0, tzinfo=UTC), False
        rendered = tickets.render_ticket(ticket)
        self.assert_lints(rendered)
        lines = rendered.text.split("\n")
        self.assertTrue(lines[0].endswith("| computed 19:50 UTC, valid until 21:00 UTC"), lines[0])
        self.assertIn("valid until 21:00 UTC: after that its quotes are too old, and a new ticket needs a new request",
                      lines)

    def test_a_rulebook_change_prints_first_and_does_not_block(self) -> None:
        change = [{"name": "rulebook", "old": "a" * 64, "new": "b" * 64}]
        rendered = tickets.render_ticket(run(self, WMT, "CLOSE"), rulebook_changes=change)
        self.assert_lints(rendered)
        self.assertTrue(rendered.text.startswith("CONFIG CHANGED: adopted rulebook: sha aaaaaaaa -> sha bbbbbbbb | the "
                                                 "structuring text below reads the files as they are now"))


if __name__ == "__main__":
    unittest.main()
