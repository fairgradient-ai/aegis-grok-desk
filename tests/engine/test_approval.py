"""The approval-level check (``aegis_desk.approval``; PR 3b decision S7 over designer C's D4).

The desk's convention (levels 0-5), the step levels evaluated against the state before each leg with every prefix
state checked, the structure levels, the leg orders of S7, and the fixed lines the text builders print (leg order,
the other order, the approval line, the multi-leg note, REFUSED), each rendered from integers and words with every
number it prints added to the emitted list. Designer C's eleven fixture goldens are pinned at the fixture's level 2
and at level 1; the fixed lines are linted in structuring mode with the structuring footer.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import datetime
import os
import unittest
from decimal import Decimal
from typing import List, Sequence, Tuple

from aegis_desk import alert_lint, approval, grouping, positions_csv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_POSITIONS = os.path.join(ROOT, "tests", "fixtures", "desk", "positions.csv")
AS_OF = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=datetime.timezone.utc)

H = approval.Holdings
WMT = H(shares=200, short_calls=2)       # AG-20261020-01, covered call, Brokerage (margin)
NVDA = H(shares=200, short_calls=2)      # AG-20261022-02, covered call, Brokerage (margin)
SPY = H(shares=100, long_puts=1)         # AG-20261028-03, protective put, Roth IRA
SPY_SHARES = H(shares=100)               # a shares-only SPY copy (designer Q's D10)
WMT_SHARES = H(shares=200)               # a shares-only WMT copy
COLLAR = H(shares=100, long_puts=1, short_calls=1)


def close(side: str, instrument: str, quantity: int) -> "approval.Leg":
    return approval.Leg(approval.EFFECT_CLOSE, side, instrument, quantity)


def open_(side: str, instrument: str, quantity: int) -> "approval.Leg":
    return approval.Leg(approval.EFFECT_OPEN, side, instrument, quantity)


def close_shares(quantity: int) -> "approval.Leg":
    return close(approval.SIDE_LONG, approval.INSTRUMENT_SHARES, quantity)


CLOSE_SHORT_CALL_2 = close("short", "call", 2)
OPEN_SHORT_CALL_2 = open_("short", "call", 2)

# Designer C's D4 evidence: name, state, legs, level needed, OK at level 2, OK at level 1.
GOLDENS: Tuple[Tuple[str, "approval.Holdings", Sequence["approval.Leg"], int, bool, bool], ...] = (
    ("WMT CLOSE (call only)", WMT, [CLOSE_SHORT_CALL_2], 0, True, True),
    ("WMT UNWIND_STOCK call first", WMT, [CLOSE_SHORT_CALL_2, close_shares(200)], 0, True, True),
    ("WMT UNWIND_STOCK shares first (unsafe)", WMT, [close_shares(200), CLOSE_SHORT_CALL_2], 4, False, False),
    ("WMT ROLL close first", WMT, [CLOSE_SHORT_CALL_2, OPEN_SHORT_CALL_2], 1, True, True),
    ("WMT ROLL open first (unsafe)", WMT, [OPEN_SHORT_CALL_2, CLOSE_SHORT_CALL_2], 4, False, False),
    ("WMT ROLL 2 -> 3 calls (uncovered)", WMT, [CLOSE_SHORT_CALL_2, open_("short", "call", 3)], 4, False, False),
    ("SPY MONETIZE_PUT", SPY, [close("long", "put", 1)], 0, True, True),
    ("SPY covered call proposal (1 call)", SPY, [open_("short", "call", 1)], 1, True, True),
    ("WMT protective put proposal (2 puts)", WMT, [open_("long", "put", 2)], 2, True, False),
    ("NVDA collar = add 2 puts to covered call", NVDA, [open_("long", "put", 2)], 2, True, False),
    ("WMT covered call proposal (no free shares)", WMT, [open_("short", "call", 1)], 4, False, False),
)


def lint(lines: Sequence[str], emitted: List[str], identifiers: Sequence[str] = ()) -> "alert_lint.LintResult":
    text = "\n".join(list(lines) + [alert_lint.structuring_footer_line(AS_OF)])
    return alert_lint.check(text, emitted, rule_ids=[], identifiers=list(identifiers), clocks=["19:31 UTC"],
                            as_of=AS_OF, footer=alert_lint.FOOTER_STRUCTURING)


class ConventionTest(unittest.TestCase):
    def test_the_convention_word_for_word(self) -> None:
        self.assertEqual("Approval levels by the desk's convention (brokers number and define their levels differently; "
                         "state yours in this convention, and give the lowest level across your accounts):",
                         approval.CONVENTION_INTRO)
        self.assertEqual({
            0: "no options approval: the desk shows closing tickets only",
            1: "covered calls on shares held in the same account, and cash-secured puts",
            2: "level 1 plus long calls and long puts (protective puts, collars)",
            3: "level 2 plus defined-risk spreads",
            4: "level 3 plus uncovered short options",
            5: "level 4 plus uncovered index options and other advanced strategies",
        }, approval.CONVENTION)
        self.assertEqual("By the desk's convention, a closing transaction needs no approval (level 0); the broker's "
                         "own rules apply.", approval.CONVENTION_CLOSING)
        self.assertEqual((0, 5), approval.LEVEL_RANGE)
        self.assertEqual("the approval-level check", approval.CHECK_NAME)

    def test_the_step_table(self) -> None:
        self.assertEqual({"close_option": 0, "close_shares": 0, "close_uncovers": 4, "open_covered_call": 1,
                          "open_uncovered_call": 4, "open_short_put": 1, "open_long": 2}, approval.STEP_LEVELS)
        self.assertEqual((
            ("close_option", "close a short or long option you hold", 0),
            ("close_shares", "close shares that no remaining short call needs", 0),
            ("close_uncovers", "close shares or a long that leaves a remaining short option uncovered", 4),
            ("open_covered_call", "open a short call covered by free shares in the same account (shares - 100 x short "
                                  "calls already open >= 100 x qty)", 1),
            ("open_uncovered_call", "open a short call without enough free shares", 4),
            ("open_short_put", "open a short put (cash-secured; the desk does not see your cash)", 1),
            ("open_long", "open a long call or long put", 2),
        ), approval.STEP_TABLE)
        self.assertEqual(list(approval.STEP_LEVELS), [kind for kind, _words, _level in approval.STEP_TABLE])
        self.assertEqual(approval.STEP_LEVELS, {kind: level for kind, _words, level in approval.STEP_TABLE})

    def test_the_structure_levels_cover_every_grouping_structure(self) -> None:
        self.assertEqual(set(grouping.STRUCTURES), set(approval.STRUCTURE_LEVELS))
        self.assertEqual({"long_stock": 0, "covered_call": 1, "cash_secured_put": 1, "long_call": 2, "long_put": 2,
                          "protective_put": 2, "collar": 2, "vertical_credit_call": 3, "vertical_credit_put": 3,
                          "vertical_debit_call": 3, "vertical_debit_put": 3, "short_call": 4, "short_stock": None,
                          "custom": None, "invalid": None}, approval.STRUCTURE_LEVELS)
        self.assertEqual(1, approval.structure_level("covered_call"))
        self.assertIsNone(approval.structure_level("custom"))
        self.assertIsNone(approval.structure_level("no_such_structure"))

    def test_no_claim_and_no_order_shape_in_the_fixed_words(self) -> None:
        texts = [approval.CONVENTION_INTRO, approval.CONVENTION_CLOSING, approval.MULTI_LEG_NOTE, approval.CHECK_NAME]
        texts += list(approval.CONVENTION.values()) + [words for _kind, words, _level in approval.STEP_TABLE]
        for text in texts:
            self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(text)], text)
            self.assertNotIn("suitab", text.lower())
            self.assertNotIn("safe", text.lower())


class HoldingsAndLegsTest(unittest.TestCase):
    def test_holdings_count_whole_shares_and_contracts(self) -> None:
        self.assertEqual(H(0, 0, 0, 0, 0), H())
        self.assertEqual(("shares", "long_calls", "short_calls", "long_puts", "short_puts"),
                         tuple(field.name for field in dataclasses.fields(H)))
        self.assertEqual(0, WMT.uncovered_calls)
        self.assertEqual(2, H(shares=200, short_calls=4).uncovered_calls)
        self.assertEqual(1, H(shares=150, short_calls=2).uncovered_calls)
        self.assertEqual(0, H(shares=250, short_calls=2).uncovered_calls)
        self.assertEqual(1, H(short_calls=1, long_calls=1).uncovered_calls, "a long call does not cover by the convention")
        self.assertEqual(0, H(short_puts=3).uncovered_calls)
        self.assertEqual(200, WMT_SHARES.free_shares)
        self.assertEqual(0, WMT.free_shares)
        self.assertEqual(0, H(shares=100, short_calls=2).free_shares)
        for bad in ({"shares": -1}, {"short_calls": -2}, {"long_puts": True}, {"shares": 1.5}):
            with self.assertRaises(ValueError):
                H(**bad)  # type: ignore[arg-type]

    def test_legs_are_validated(self) -> None:
        self.assertEqual(("open", "close"), approval.EFFECTS)
        self.assertEqual(("long", "short"), approval.SIDES)
        self.assertEqual(("call", "put", "shares"), approval.INSTRUMENTS)
        for bad in (("hold", "short", "call", 1), ("open", "flat", "call", 1), ("open", "short", "future", 1),
                    ("open", "short", "call", 0), ("open", "short", "call", -1), ("open", "short", "call", True),
                    ("close", "short", "shares", 100), ("open", "long", "shares", 100)):
            with self.subTest(leg=bad):
                with self.assertRaises(ValueError):
                    approval.Leg(*bad)  # type: ignore[arg-type]

    def test_applying_a_leg(self) -> None:
        self.assertEqual(H(shares=200), WMT.apply(CLOSE_SHORT_CALL_2))
        self.assertEqual(H(shares=200, short_calls=4), WMT.apply(OPEN_SHORT_CALL_2))
        self.assertEqual(H(short_calls=2), WMT.apply(close_shares(200)))
        self.assertEqual(H(shares=100, long_puts=2), SPY.apply(open_("long", "put", 1)))
        with self.assertRaises(ValueError) as ctx:
            WMT.apply(close("short", "call", 3))
        self.assertIn("3 short calls", str(ctx.exception))
        with self.assertRaises(ValueError):
            WMT.apply(close_shares(201))
        with self.assertRaises(ValueError):
            SPY.apply(close("long", "call", 1))

    def test_holdings_of_the_fixture_rows_by_account_and_underlying(self) -> None:
        rows = positions_csv.load_positions(FIXTURE_POSITIONS, today=datetime.date(2026, 11, 3)).rows
        self.assertEqual(WMT, approval.holdings_of(rows, "Brokerage (margin)", "WMT"))
        self.assertEqual(NVDA, approval.holdings_of(rows, "Brokerage (margin)", "NVDA"))
        self.assertEqual(SPY, approval.holdings_of(rows, "Roth IRA", "SPY"), "the closed cash-secured put is not held")
        self.assertEqual(H(), approval.holdings_of(rows, "Roth IRA", "WMT"), "shares in another account never count")
        self.assertEqual(H(), approval.holdings_of(rows, "Brokerage (margin)", "SPY"))


class GoldensTest(unittest.TestCase):
    def test_designer_c_goldens_at_level_2_and_level_1(self) -> None:
        for name, state, legs, level, ok2, ok1 in GOLDENS:
            with self.subTest(case=name):
                result = approval.check_legs(state, legs, 2)
                self.assertEqual((level, ok2), (result.required_level, result.ok))
                self.assertEqual(2, result.owner_level)
                self.assertEqual(level, max(step.level for step in result.steps))
                low = approval.check_legs(state, legs, 1)
                self.assertEqual((level, ok1), (low.required_level, low.ok))
                self.assertEqual(level == 0, approval.check_legs(state, legs, 0).ok, "level 0: closing steps only")

    def test_the_step_kinds_and_levels(self) -> None:
        def kinds(state: "approval.Holdings", legs: Sequence["approval.Leg"]) -> List[Tuple[str, int]]:
            return [(step.kind, step.level) for step in approval.check_legs(state, legs, 5).steps]

        self.assertEqual([("close_option", 0), ("close_shares", 0)], kinds(WMT, [CLOSE_SHORT_CALL_2, close_shares(200)]))
        self.assertEqual([("close_uncovers", 4), ("close_option", 0)], kinds(WMT, [close_shares(200), CLOSE_SHORT_CALL_2]))
        self.assertEqual([("close_option", 0), ("open_covered_call", 1)], kinds(WMT, [CLOSE_SHORT_CALL_2, OPEN_SHORT_CALL_2]))
        self.assertEqual([("open_uncovered_call", 4), ("close_option", 0)],
                         kinds(WMT, [OPEN_SHORT_CALL_2, CLOSE_SHORT_CALL_2]))
        self.assertEqual([("open_long", 2)], kinds(SPY_SHARES, [open_("long", "put", 1)]))
        self.assertEqual([("open_short_put", 1)], kinds(H(), [open_("short", "put", 1)]))
        self.assertEqual([("close_shares", 0)], kinds(H(shares=250, short_calls=2), [close_shares(50)]))
        self.assertEqual([("close_uncovers", 4)], kinds(H(shares=250, short_calls=2), [close_shares(100)]))
        self.assertEqual([("close_uncovers", 4)], kinds(H(short_calls=1, long_calls=1), [close("long", "call", 1)]),
                         "closing the long leg of a call vertical first leaves the short call uncovered")
        self.assertEqual([("close_option", 0), ("close_option", 0)],
                         kinds(H(short_calls=1, long_calls=1), [close("short", "call", 1), close("long", "call", 1)]))

    def test_every_prefix_state_is_checked(self) -> None:
        result = approval.check_legs(WMT, [OPEN_SHORT_CALL_2, CLOSE_SHORT_CALL_2], 2)
        self.assertEqual([WMT, H(shares=200, short_calls=4)], [step.before for step in result.steps])
        self.assertEqual([H(shares=200, short_calls=4), WMT], [step.after for step in result.steps])
        self.assertEqual(WMT, result.steps[-1].after, "the end state is the same covered call")
        self.assertEqual(1, result.exposure_step, "an uncovered call exists after leg 1")
        self.assertEqual(result.steps[0], result.refusal)
        self.assertEqual([1, 2], [step.index for step in result.steps])

    def test_the_canonical_orders_expose_nothing(self) -> None:
        cases = [
            (WMT, [CLOSE_SHORT_CALL_2], 0),                                                       # CLOSE
            (COLLAR, [close("short", "call", 1), close("long", "put", 1)], 0),                    # CLOSE a collar
            (WMT, [CLOSE_SHORT_CALL_2, close_shares(200)], 0),                                    # UNWIND_STOCK
            (COLLAR, [close("short", "call", 1), close_shares(100), close("long", "put", 1)], 0),  # UNWIND a collar
            (SPY, [close_shares(100), close("long", "put", 1)], 0),                               # UNWIND a put
            (WMT, [CLOSE_SHORT_CALL_2, OPEN_SHORT_CALL_2], 1),                                    # ROLL a short call
            (SPY, [open_("long", "put", 1), close("long", "put", 1)], 2),                         # ROLL a long put
            (SPY, [close("long", "put", 1)], 0),                                                  # MONETIZE_PUT
            (SPY_SHARES, [open_("long", "put", 1), open_("short", "call", 1)], 2),                # collar proposal
            (WMT_SHARES, [OPEN_SHORT_CALL_2], 1),                                                 # covered call proposal
            (SPY_SHARES, [open_("long", "put", 1)], 2),                                           # protective put proposal
        ]
        for state, legs, level in cases:
            with self.subTest(state=state, legs=legs):
                result = approval.check_legs(state, legs, 2)
                self.assertEqual((True, level, None, None), (result.ok, result.required_level, result.exposure_step,
                                                             result.refusal))
        self.assertTrue(approval.check_legs(WMT, [CLOSE_SHORT_CALL_2], 0).ok, "level 0 shows closing tickets")
        self.assertFalse(approval.check_legs(WMT_SHARES, [OPEN_SHORT_CALL_2], 0).ok)

    def test_the_refusal_is_the_first_step_at_the_highest_level(self) -> None:
        result = approval.check_legs(WMT, [CLOSE_SHORT_CALL_2, open_("short", "call", 3)], 2)
        self.assertFalse(result.ok)
        self.assertEqual((2, "open_uncovered_call", 4), (result.refusal.index, result.refusal.kind, result.refusal.level))
        result = approval.check_legs(SPY_SHARES, [open_("long", "put", 1), open_("short", "call", 1)], 1)
        self.assertEqual((1, "open_long", 2), (result.refusal.index, result.refusal.kind, result.refusal.level))

    def test_invalid_input(self) -> None:
        for level in (-1, 6, True, "2", None):
            with self.assertRaises(ValueError):
                approval.check_legs(WMT, [CLOSE_SHORT_CALL_2], level)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            approval.check_legs(WMT, [], 2)
        with self.assertRaises(ValueError):
            approval.check_legs(WMT, [close("short", "call", 3)], 2)
        with self.assertRaises(ValueError):
            approval.check_legs((200, 2), [CLOSE_SHORT_CALL_2], 2)  # type: ignore[arg-type]

    def test_reverse_breach(self) -> None:
        breach = approval.reverse_breach(WMT, [CLOSE_SHORT_CALL_2, OPEN_SHORT_CALL_2], 2)
        self.assertEqual(approval.ReverseBreach(leg_index=2, uncovered_calls=2, level=4), breach)
        breach = approval.reverse_breach(WMT, [CLOSE_SHORT_CALL_2, close_shares(200)], 2)
        self.assertEqual(approval.ReverseBreach(leg_index=2, uncovered_calls=2, level=4), breach)
        breach = approval.reverse_breach(COLLAR, [close("short", "call", 1), close_shares(100), close("long", "put", 1)], 2)
        self.assertEqual(approval.ReverseBreach(leg_index=2, uncovered_calls=1, level=4), breach)
        self.assertIsNone(approval.reverse_breach(WMT, [CLOSE_SHORT_CALL_2, OPEN_SHORT_CALL_2], 4),
                          "printed only when the other order would exceed the owner's level")
        self.assertIsNone(approval.reverse_breach(SPY, [open_("long", "put", 1), close("long", "put", 1)], 2))
        self.assertIsNone(approval.reverse_breach(SPY_SHARES, [open_("long", "put", 1), open_("short", "call", 1)], 2))
        self.assertIsNone(approval.reverse_breach(WMT, [CLOSE_SHORT_CALL_2], 2), "one leg has no other order")


class WordsTest(unittest.TestCase):
    def test_holdings_words(self) -> None:
        emitted: List[str] = []
        self.assertEqual("you hold 200 WMT shares and no option", approval.holdings_words(H(shares=200), "WMT", emitted))
        self.assertEqual(["200"], emitted)
        self.assertEqual("you hold 200 WMT shares and 2 short calls", approval.holdings_words(WMT, "WMT"))
        self.assertEqual("you hold 200 WMT shares and 4 short calls (2 uncovered)",
                         approval.holdings_words(H(shares=200, short_calls=4), "WMT"))
        self.assertEqual("you hold 100 SPY shares, 1 short call and 1 long put", approval.holdings_words(COLLAR, "SPY"))
        self.assertEqual("you hold no WMT shares and 2 short calls (2 uncovered)",
                         approval.holdings_words(H(short_calls=2), "WMT"))
        self.assertEqual("you hold no SPY shares and no option", approval.holdings_words(H(), "SPY"))
        self.assertEqual("you hold 1 SPY share, 1 long call, 1 long put and 2 short puts",
                         approval.holdings_words(H(shares=1, long_calls=1, long_puts=1, short_puts=2), "SPY"))

    def test_step_words(self) -> None:
        def words(state: "approval.Holdings", legs: Sequence["approval.Leg"]) -> List[str]:
            emitted: List[str] = []
            out = [approval.step_words(step, emitted) for step in approval.check_legs(state, legs, 5).steps]
            self.assertEqual(out, [step.words for step in approval.check_legs(state, legs, 5).steps])
            return out

        self.assertEqual(["closing your 2 short calls", "closing 200 of your shares"],
                         words(WMT, [CLOSE_SHORT_CALL_2, close_shares(200)]))
        self.assertEqual(["closing 200 of your shares, which leaves 2 short calls uncovered", "closing your 2 short calls"],
                         words(WMT, [close_shares(200), CLOSE_SHORT_CALL_2]))
        self.assertEqual(["opening 2 new short calls without enough free shares (0 free, 200 needed)",
                          "closing your 2 short calls"], words(WMT, [OPEN_SHORT_CALL_2, CLOSE_SHORT_CALL_2]))
        self.assertEqual(["opening 2 new short calls, covered by 200 free shares"], words(WMT_SHARES, [OPEN_SHORT_CALL_2]))
        self.assertEqual(["opening 1 new long put", "opening 1 new short call, covered by 100 free shares"],
                         words(SPY_SHARES, [open_("long", "put", 1), open_("short", "call", 1)]))
        self.assertEqual(["opening 1 new short put (cash-secured; the desk does not see your cash)"],
                         words(H(), [open_("short", "put", 1)]))
        self.assertEqual(["closing your 1 long call, which leaves 1 short call uncovered"],
                         words(H(short_calls=1, long_calls=1), [close("long", "call", 1)]))
        self.assertEqual(["closing your 1 long put"], words(SPY, [close("long", "put", 1)]))


class LinesTest(unittest.TestCase):
    ROLL = [CLOSE_SHORT_CALL_2, OPEN_SHORT_CALL_2]

    def test_the_roll_ticket_lines(self) -> None:
        emitted: List[str] = []
        lines = approval.approval_lines(WMT, self.ROLL, 2, "WMT", emitted)
        self.assertEqual([
            "leg order: leg 1 first; after leg 1 you hold 200 WMT shares and no option (level 0 by the desk's "
            "convention); after leg 2 you hold 200 WMT shares and 2 short calls (level 1)",
            "in the other order, after leg 2 you would hold 2 uncovered calls (level 4)",
            "entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level",
            "approval: the highest step needs level 1 by the desk's convention, within your self-attested level 2 "
            "(desk.toml); your broker's own approval for this account decides",
        ], lines)
        result = lint(lines, emitted)
        self.assertTrue(result.ok, result.problems)

    def test_a_one_leg_ticket(self) -> None:
        emitted: List[str] = []
        lines = approval.approval_lines(WMT, [CLOSE_SHORT_CALL_2], 2, "WMT", emitted)
        self.assertEqual([
            "leg order: after leg 1 you hold 200 WMT shares and no option (level 0 by the desk's convention)",
            "approval: the highest step needs level 0 by the desk's convention, within your self-attested level 2 "
            "(desk.toml); your broker's own approval for this account decides",
        ], lines)
        result = lint(lines, emitted)
        self.assertTrue(result.ok, result.problems)

    def test_a_three_leg_unwind_of_a_collar(self) -> None:
        emitted: List[str] = []
        lines = approval.approval_lines(COLLAR, [close("short", "call", 1), close_shares(100), close("long", "put", 1)],
                                        2, "SPY", emitted)
        self.assertEqual([
            "leg order: leg 1 first; after leg 1 you hold 100 SPY shares and 1 long put (level 0 by the desk's "
            "convention); after leg 2 you hold no SPY shares and 1 long put (level 0); after leg 3 you hold no SPY "
            "shares and no option (level 0)",
            "in the other order, after leg 2 you would hold 1 uncovered call (level 4)",
            # an unwind is neither a roll nor a spread: shares and options together (reviews L1-5, L2-6, L4-5)
            "entered as one multi-leg order, some brokers class shares and options together as a combination, which "
            "can need a higher level",
            "approval: the highest step needs level 0 by the desk's convention, within your self-attested level 2 "
            "(desk.toml); your broker's own approval for this account decides",
        ], lines)
        result = lint(lines, emitted)
        self.assertTrue(result.ok, result.problems)

    def test_a_long_put_roll_and_a_collar_proposal_have_no_other_order_line(self) -> None:
        lines = approval.approval_lines(SPY, [open_("long", "put", 1), close("long", "put", 1)], 2, "SPY")
        self.assertEqual("leg order: leg 1 first; after leg 1 you hold 100 SPY shares and 2 long puts (level 2 by the "
                         "desk's convention); after leg 2 you hold 100 SPY shares and 1 long put (level 0)", lines[0])
        self.assertEqual(approval.MULTI_LEG_NOTE, lines[1])
        self.assertEqual(3, len(lines))
        lines = approval.approval_lines(SPY_SHARES, [open_("long", "put", 1), open_("short", "call", 1)], 2, "SPY")
        self.assertEqual("leg order: leg 1 first; after leg 1 you hold 100 SPY shares and 1 long put (level 2 by the "
                         "desk's convention); after leg 2 you hold 100 SPY shares, 1 short call and 1 long put (level 1)",
                         lines[0])
        self.assertTrue(lines[-1].startswith("approval: the highest step needs level 2 by the desk's convention"))

    def test_approval_lines_refuse_a_refused_check(self) -> None:
        with self.assertRaises(ValueError):
            approval.approval_lines(WMT, [open_("short", "call", 1)], 2, "WMT")

    def test_the_fixed_renderers(self) -> None:
        emitted: List[str] = []
        self.assertEqual("leg order: after leg 1 you hold x (level 0 by the desk's convention)",
                         approval.leg_order_line([(0, "you hold x")], emitted))
        self.assertEqual(["1", "0"], emitted)
        self.assertEqual("in the other order, after leg 3 you would hold 1 uncovered call (level 4)",
                         approval.other_order_line(3, 1))
        self.assertEqual("approval: the highest step needs level 2 by the desk's convention, within your self-attested "
                         "level 5 (desk.toml); your broker's own approval for this account decides",
                         approval.approval_line(2, 5))
        self.assertEqual("entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher "
                         "level", approval.MULTI_LEG_NOTE)
        self.assertIsNone(approval.multi_leg_note(1))
        self.assertEqual(approval.MULTI_LEG_NOTE, approval.multi_leg_note(2))
        for bad in (lambda: approval.leg_order_line([]), lambda: approval.other_order_line(2, 0),
                    lambda: approval.approval_line(3, 2), lambda: approval.approval_line(-1, 2),
                    lambda: approval.refused_line("WMT", "WMT", "covered call", "x", 2, 2)):
            with self.assertRaises(ValueError):
                bad()

    def test_the_refused_line(self) -> None:
        emitted: List[str] = []
        result = approval.check_legs(WMT, [open_("short", "call", 1)], 2)
        line = approval.refusal_line(result, "AG-20261020-01", "WMT", "covered call", emitted)
        self.assertEqual("REFUSED AG-20261020-01 | WMT covered call | opening 1 new short call without enough free shares "
                         "(0 free, 100 needed) needs level 4 by the desk's convention; desk.toml records your "
                         "self-attested level 2 | the desk shows nothing above your level; your broker's own approval "
                         "decides what you can enter", line)
        result = lint([line], emitted, identifiers=["AG-20261020-01"])
        self.assertTrue(result.ok, result.problems)
        emitted = []
        result = approval.check_legs(WMT_SHARES, [open_("long", "put", 2)], 1)
        line = approval.refusal_line(result, "AG-20261020-01", "WMT", "protective put", emitted)
        self.assertEqual("REFUSED AG-20261020-01 | WMT protective put | opening 2 new long puts needs level 2 by the "
                         "desk's convention; desk.toml records your self-attested level 1 | the desk shows nothing above "
                         "your level; your broker's own approval decides what you can enter", line)
        result = lint([line], emitted, identifiers=["AG-20261020-01"])
        self.assertTrue(result.ok, result.problems)
        self.assertEqual(line, approval.refused_line("AG-20261020-01", "WMT", "protective put",
                                                     "opening 2 new long puts", 2, 1))
        with self.assertRaises(ValueError):
            approval.refusal_line(approval.check_legs(WMT, [CLOSE_SHORT_CALL_2], 2), "AG-20261020-01", "WMT",
                                  "covered call")

    def test_the_lines_carry_no_forbidden_language_in_any_mode(self) -> None:
        lines = approval.approval_lines(WMT, self.ROLL, 2, "WMT")
        lines.append(approval.refusal_line(approval.check_legs(WMT, [open_("short", "call", 1)], 2), "AG-20261020-01",
                                           "WMT", "covered call"))
        for line in lines:
            self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(line)], line)
            for word in ("suitab", "safe", "recommend", "should"):
                self.assertNotIn(word, line.lower())
            for start in ("collar", "hedge", "cover", "roll", "close", "exit"):
                self.assertFalse(line.lower().startswith(start), line)


class FixerTest(unittest.TestCase):
    """The PR 3b review: fractional shares (BD4) and the multi-leg note worded by what the order is (L1-5)."""

    def test_fractional_shares_are_held_exactly_and_cover_whole_hundreds(self) -> None:
        with open(FIXTURE_POSITIONS, encoding="utf-8") as handle:
            text = handle.read().replace("equity,WMT,,200,", "equity,WMT,,200.5,")
        rows = positions_csv.parse_positions_text(text, today=datetime.date(2026, 11, 3)).rows
        holdings = approval.holdings_of(rows, "Brokerage (margin)", "WMT")
        self.assertEqual(H(shares=Decimal("200.5"), short_calls=2), holdings)
        self.assertEqual((0, Decimal("0.5")), (holdings.uncovered_calls, holdings.free_shares))
        self.assertIsInstance(holdings.uncovered_calls, int)
        self.assertEqual(1, H(shares=Decimal("199.9"), short_calls=2).uncovered_calls)
        # the unwind closes the exact open quantity; coverage reads whole hundreds
        result = approval.check_legs(holdings, [CLOSE_SHORT_CALL_2, close_shares(Decimal("200.5"))], 2)
        self.assertEqual((True, 0, H()), (result.ok, result.required_level, result.steps[-1].after))
        self.assertEqual("closing 200.5 of your shares", result.steps[-1].words)
        emitted: List[str] = []
        self.assertEqual("you hold 200.5 WMT shares and 2 short calls", approval.holdings_words(holdings, "WMT", emitted))
        self.assertEqual(["200.5", "2"], emitted)
        short = text.replace("equity,WMT,,200.5,", "equity,WMT,,-200,")
        with self.assertRaises(ValueError):
            approval.holdings_of(positions_csv.parse_positions_text(short, today=datetime.date(2026, 11, 3)).rows,
                                 "Brokerage (margin)", "WMT")
        for bad in (Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(shares=bad):
                with self.assertRaises(ValueError):
                    H(shares=bad)
        with self.assertRaises(ValueError):
            open_("short", "call", Decimal("1.5"))   # contracts stay whole
        with self.assertRaises(ValueError):
            close_shares(Decimal("0"))

    def test_the_multi_leg_note_names_what_the_order_is(self) -> None:
        notes = approval.MULTI_LEG_NOTES
        self.assertEqual(approval.MULTI_LEG_NOTE, notes["roll"])
        cases = (
            ("roll", WMT, [CLOSE_SHORT_CALL_2, OPEN_SHORT_CALL_2]),
            ("roll", SPY, [open_("long", "put", 1), close("long", "put", 1)]),
            ("collar", SPY_SHARES, [open_("long", "put", 1), open_("short", "call", 1)]),
            ("closing", COLLAR, [close("short", "call", 1), close("long", "put", 1)]),
            ("combination", WMT, [CLOSE_SHORT_CALL_2, close_shares(200)]),
            ("combination", SPY, [close_shares(100), close("long", "put", 1)]),
        )
        for kind, state, legs in cases:
            with self.subTest(kind=kind):
                self.assertEqual(kind, approval.multi_leg_kind(legs))
                lines = approval.approval_lines(state, legs, 2, "SPY")
                self.assertIn(notes[kind], lines)
                self.assertEqual([note for note in notes.values() if note in lines], [notes[kind]])
        self.assertEqual("entered as one multi-leg order, some brokers class closing option legs as a spread, which can "
                         "need a higher level", notes["closing"])
        self.assertEqual("entered as one multi-leg order, some brokers class shares and options together as a "
                         "combination, which can need a higher level", notes["combination"])
        self.assertEqual("entered as one multi-leg order, some brokers class a collar as a spread, which can need a "
                         "higher level", notes["collar"])
        self.assertNotIn("roll", notes["combination"] + notes["closing"])
        for note in notes.values():
            result = lint([note], [])
            self.assertTrue(result.ok, (note, result.problems))
        self.assertIsNone(approval.multi_leg_kind([CLOSE_SHORT_CALL_2]))


class VerifierTest(unittest.TestCase):
    """The PR 3b verifier: a fractional holding (BD4) keeps the whole-share form wherever the count is whole, and its
    refusal words print the exact count."""

    def test_a_whole_count_after_a_fractional_leg_is_an_int(self) -> None:
        after = H(shares=Decimal("200.5"), short_calls=2).apply(CLOSE_SHORT_CALL_2).apply(close_shares(Decimal("200.5")))
        self.assertEqual((0, int), (after.shares, type(after.shares)))
        partial = H(shares=Decimal("250.5")).apply(close_shares(Decimal("50.5")))
        self.assertEqual((200, int), (partial.shares, type(partial.shares)))
        self.assertEqual(Decimal("0.5"), H(shares=Decimal("200.5")).apply(close_shares(200)).shares)
        whole = H(shares=150).apply(close_shares(50))
        self.assertEqual((100, int), (whole.shares, type(whole.shares)))

    def test_closing_more_shares_than_held_names_the_exact_counts(self) -> None:
        with self.assertRaises(ValueError) as caught:
            H(shares=Decimal("200.5")).apply(close_shares(Decimal("250.5")))
        self.assertEqual("the leg closes 250.5 shares but 200.5 are held", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
