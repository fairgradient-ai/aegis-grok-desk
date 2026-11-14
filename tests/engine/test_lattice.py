"""``aegis_desk.lattice``: the American early-exercise lattice (step 2.1 of ``docs/ROADMAP.md``).

Goldens: Hull's American put (S = K = 50, r = 10 %, sigma = 40 %, five months, no dividend), whose tree values Hull
prints in *Options, Futures, and Other Derivatives* (the chapter on basic numerical procedures) as 4.49 at 5 steps and
4.263, 4.272, 4.278 and 4.283 at 30, 50, 100 and 500 steps, with the control-variate estimate 4.49 + 4.08 - 4.32 =
4.25 at 5 steps. The module's own literals at 5 steps are pinned at 1e-12 relative beside the printed roundings.

Properties that hold exactly on any Cox-Ross-Rubinstein tree (no tolerance beyond rounding): a call without a dividend
at a positive rate is never exercised early, so its American and European tree values are equal bit for bit and its
Greeks are the closed form's; the put-call symmetry ``C(S, K, r, q) = P(K, S, q, r)``; the early-exercise premium is
never negative; at a zero rate and yield, where early exercise is never strictly better, ``exercise_now`` and
``near_boundary`` stay false. Bounds and monotonicity hold over a grid. Near the exercise boundary the default tree's
root decision is not trusted: spots where its root exercises but a finer tree holds are ``near_boundary``, never
``exercise_now``, and every ``exercise_now`` near the band is confirmed by a ten-times finer tree. The early-exercise gap of ``aegis_desk.carry``, an independent tree in the
same repository, is reproduced. The continuous-time cross-check against QuantLib's finite-difference American engine is
in ``tests/oracle/test_quantlib_oracle.py``.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import ast
import io
import itertools
import math
import os
import re
import tokenize
import unittest
from decimal import Decimal
from typing import Iterator, List, Tuple

from aegis_desk import bsm, carry, lattice

HULL = {"S": 50.0, "K": 50.0, "tau": 5.0 / 12.0, "r": 0.10, "q": 0.0, "sigma": 0.40}
GOLDEN = {"S": 162.06, "K": 170.163, "tau": 1.553, "r": 0.05, "q": 0.052, "sigma": 0.226}

SPOTS = (80.0, 100.0, 125.0)
STRIKES = (90.0, 100.0, 110.0)
TAUS = (30.0 / 365.0, 0.5, 1.5)
RATES = (-0.005, 0.0, 0.05)
YIELDS = (0.0, 0.03, 0.08)
SIGMAS = (0.15, 0.45)


def grid():
    for S, K, tau, r, q, sigma in itertools.product(SPOTS, STRIKES, TAUS, RATES, YIELDS, SIGMAS):
        yield {"S": S, "K": K, "tau": tau, "r": r, "q": q, "sigma": sigma}


def price(right: str, m: dict, steps: int = lattice.DEFAULT_STEPS) -> lattice.LatticeResult:
    return lattice.american(right, m["S"], m["K"], m["tau"], m["r"], m["q"], m["sigma"], steps)


def rel_close(actual: float, expected: float, rel: float) -> bool:
    return abs(actual - expected) <= rel * abs(expected)


class HullGoldenTest(unittest.TestCase):
    def test_five_step_tree_values(self) -> None:
        result = price(bsm.PUT, HULL, 5)
        self.assertTrue(rel_close(result.tree_price, 4.488458534725913, 1e-12), repr(result.tree_price))
        self.assertTrue(rel_close(result.european_tree, 4.319018716515818, 1e-12), repr(result.european_tree))
        self.assertTrue(rel_close(result.price, 4.245420802997878, 1e-12), repr(result.price))
        self.assertEqual(4.49, round(result.tree_price, 2))
        self.assertEqual(4.32, round(result.european_tree, 2))
        self.assertEqual(4.08, round(result.european_closed, 2))
        self.assertEqual(4.25, round(result.price, 2))

    def test_convergence_table(self) -> None:
        for steps, printed in ((30, 4.263), (50, 4.272), (100, 4.278), (500, 4.283)):
            with self.subTest(steps=steps):
                self.assertEqual(printed, round(price(bsm.PUT, HULL, steps).tree_price, 3))

    def test_the_control_variate_moves_toward_the_converged_value(self) -> None:
        converged = price(bsm.PUT, HULL, 2000).price
        for steps in (5, 30, 100):
            with self.subTest(steps=steps):
                result = price(bsm.PUT, HULL, steps)
                self.assertLess(abs(result.price - converged), abs(result.tree_price - converged))

    def test_the_european_tree_converges_to_the_closed_form(self) -> None:
        for right in bsm.RIGHTS:
            with self.subTest(right=right):
                coarse = price(right, GOLDEN, 50)
                fine = price(right, GOLDEN, 2000)
                self.assertEqual(bsm.price(right, **GOLDEN), fine.european_closed)
                self.assertLess(abs(fine.european_tree - fine.european_closed), 1e-3)
                self.assertLess(abs(fine.european_tree - fine.european_closed),
                                abs(coarse.european_tree - coarse.european_closed))


class ExactPropertyTest(unittest.TestCase):
    def test_a_call_without_dividend_is_never_exercised_early(self) -> None:
        for m in grid():
            if m["q"] != 0.0 or m["r"] <= 0.0:
                continue
            with self.subTest(**m):
                result = price(bsm.CALL, m)
                self.assertEqual(result.european_tree, result.tree_price)
                self.assertEqual(0.0, result.early_exercise_premium)
                self.assertFalse(result.exercise_now)
                self.assertEqual(result.european_closed, result.price)

    def test_at_a_zero_rate_the_tie_leaves_at_most_rounding(self) -> None:
        for m in grid():
            if m["q"] != 0.0 or m["r"] != 0.0:
                continue
            for right in bsm.RIGHTS:
                with self.subTest(right=right, **m):
                    result = price(right, m)
                    self.assertGreaterEqual(result.early_exercise_premium, 0.0)
                    self.assertLessEqual(result.early_exercise_premium, 1e-12 * max(1.0, result.tree_price))

    def test_at_a_zero_rate_and_yield_exercise_is_never_signalled(self) -> None:
        # With r = q = 0 early exercise is never strictly better, so a rounding tie at a deep in-the-money root must not
        # read as exercise_now (which would print the payoff's Greeks: delta -1, gamma 0, theta 0).
        cases = [{"S": 100.0, "K": 140.0, "tau": 7.0 / 365.0, "r": 0.0, "q": 0.0, "sigma": 0.2},
                 {"S": 100.0, "K": 5.0, "tau": 7.0 / 365.0, "r": 0.0, "q": 0.0, "sigma": 0.2}]
        cases += [{"S": 100.0, "K": k, "tau": t, "r": 0.0, "q": 0.0, "sigma": v}
                  for k in (5.0, 20.0, 50.0, 65.0, 95.0, 105.0, 150.0, 200.0, 400.0)
                  for t in (1.0 / 365.0, 7.0 / 365.0, 0.25, 2.0)
                  for v in (0.05, 0.2, 0.9)]
        for m in cases:
            for right in bsm.RIGHTS:
                with self.subTest(right=right, **m):
                    result = price(right, m)
                    self.assertFalse(result.exercise_now)
                    self.assertFalse(result.near_boundary)
                    self.assertTrue(abs(result.price - result.european_closed) <= 1e-9 * max(1.0, result.price),
                                    (result.price, result.european_closed))

    def test_such_a_call_carries_the_closed_form_greeks(self) -> None:
        m = {"S": 100.0, "K": 95.0, "tau": 0.5, "r": 0.05, "q": 0.0, "sigma": 0.3}
        result = price(bsm.CALL, m)
        self.assertAlmostEqual(bsm.delta(bsm.CALL, **m), result.delta, places=12)
        self.assertAlmostEqual(bsm.gamma(bsm.CALL, **m), result.gamma, places=12)
        self.assertAlmostEqual(bsm.theta(bsm.CALL, **m), result.theta_per_year, places=9)

    def test_put_call_symmetry(self) -> None:
        for m in grid():
            with self.subTest(**m):
                call = price(bsm.CALL, m)
                mirrored = dict(m, S=m["K"], K=m["S"], r=m["q"], q=m["r"])
                put = price(bsm.PUT, mirrored)
                self.assertTrue(abs(call.tree_price - put.tree_price) <= 1e-10 * max(1.0, call.tree_price),
                                (call.tree_price, put.tree_price))

    def test_the_premium_is_never_negative(self) -> None:
        for m in grid():
            for right in bsm.RIGHTS:
                with self.subTest(right=right, **m):
                    self.assertGreaterEqual(price(right, m).early_exercise_premium, 0.0)

    def test_price_is_the_closed_form_plus_the_premium_unless_exercise_is_due(self) -> None:
        for m in grid():
            for right in bsm.RIGHTS:
                with self.subTest(right=right, **m):
                    result = price(right, m)
                    intrinsic = bsm.intrinsic(right, m["S"], m["K"])
                    if result.exercise_now:
                        self.assertEqual(intrinsic, result.price)
                    else:
                        self.assertEqual(max(intrinsic, result.european_closed + result.early_exercise_premium),
                                         result.price)


class BoundsTest(unittest.TestCase):
    def test_no_arbitrage_bounds(self) -> None:
        for m in grid():
            for right in bsm.RIGHTS:
                with self.subTest(right=right, **m):
                    result = price(right, m)
                    intrinsic = bsm.intrinsic(right, m["S"], m["K"])
                    self.assertGreaterEqual(result.price, intrinsic)
                    self.assertGreaterEqual(result.price, result.european_closed)
                    self.assertGreaterEqual(result.tree_price, result.european_tree)
                    self.assertLessEqual(result.price, m["S"] if right == bsm.CALL else m["K"])
                    self.assertEqual("ok", result.status)
                    self.assertFalse(result.exercise_now and result.near_boundary)

    def test_greek_signs(self) -> None:
        for m in grid():
            for right in bsm.RIGHTS:
                with self.subTest(right=right, **m):
                    result = price(right, m)
                    if right == bsm.CALL:
                        self.assertGreaterEqual(result.delta, -1e-9)
                        self.assertLessEqual(result.delta, 1.0 + 1e-9)
                    else:
                        self.assertGreaterEqual(result.delta, -1.0 - 1e-9)
                        self.assertLessEqual(result.delta, 1e-9)
                    self.assertGreaterEqual(result.gamma, -1e-9)

    def test_the_put_rises_with_strike_and_volatility_and_falls_with_spot(self) -> None:
        base = {"S": 100.0, "K": 100.0, "tau": 0.75, "r": 0.04, "q": 0.01, "sigma": 0.3}
        by_strike = [price(bsm.PUT, dict(base, K=k)).price for k in (90.0, 95.0, 100.0, 105.0, 110.0)]
        by_spot = [price(bsm.PUT, dict(base, S=s)).price for s in (90.0, 95.0, 100.0, 105.0, 110.0)]
        by_sigma = [price(bsm.PUT, dict(base, sigma=v)).price for v in (0.1, 0.2, 0.3, 0.5, 0.8)]
        self.assertEqual(sorted(by_strike), by_strike)
        self.assertEqual(sorted(by_spot, reverse=True), by_spot)
        self.assertEqual(sorted(by_sigma), by_sigma)

    def test_the_american_put_is_worth_more_with_longer_life(self) -> None:
        base = {"S": 100.0, "K": 100.0, "r": 0.05, "q": 0.0, "sigma": 0.25}
        values = [price(bsm.PUT, dict(base, tau=t), 1000).price for t in (0.1, 0.5, 1.0, 2.0)]
        self.assertEqual(sorted(values), values)


class ExerciseNowTest(unittest.TestCase):
    def test_a_deep_in_the_money_put_is_worth_its_intrinsic_value(self) -> None:
        result = lattice.american(bsm.PUT, 50.0, 100.0, 1.0, 0.10, 0.0, 0.20)
        self.assertTrue(result.exercise_now)
        self.assertEqual(50.0, result.price)
        self.assertEqual(50.0, result.tree_price)
        self.assertGreater(result.early_exercise_premium, 9.0)
        self.assertFalse(result.near_boundary)
        self.assertEqual((-1.0, 0.0, 0.0), (result.delta, result.gamma, result.theta_per_year))

    def test_a_deep_in_the_money_call_before_a_high_yield(self) -> None:
        result = lattice.american(bsm.CALL, 150.0, 100.0, 1.0, 0.01, 0.10, 0.20)
        self.assertTrue(result.exercise_now)
        self.assertEqual(50.0, result.price)
        self.assertFalse(result.near_boundary)
        self.assertEqual((1.0, 0.0, 0.0), (result.delta, result.gamma, result.theta_per_year))

    def test_an_at_the_money_put_is_held(self) -> None:
        result = price(bsm.PUT, HULL)
        self.assertFalse(result.exercise_now)
        self.assertFalse(result.near_boundary)
        self.assertGreater(result.early_exercise_premium, 0.0)

    def test_an_out_of_the_money_option_is_never_exercised_now(self) -> None:
        for right, strike in ((bsm.CALL, 120.0), (bsm.PUT, 80.0)):
            with self.subTest(right=right):
                result = lattice.american(right, 100.0, strike, 0.5, 0.2, -0.2, 0.2)
                self.assertFalse(result.exercise_now)
                self.assertFalse(result.near_boundary)


# Spots just inside the default tree's exercise region, where its root exercises but the continuous-time holder does
# not (reference values from a 5000-step tree and QuantLib 1.43's finite-difference engine at 4000 x 4000, which agree):
# (right, S, K, tau, r, q, sigma, reference delta, reference gamma).
BOUNDARY_CASES = (
    (bsm.PUT, 86.70, 100.0, 1.0, 0.10, 0.0, 0.20, -0.9719, 0.0654),
    (bsm.PUT, 69.00, 100.0, 182.0 / 365.0, 0.045, 0.0, 0.35, -0.9939, 0.0158),
    (bsm.PUT, 87.00, 100.0, 1.0, 0.10, 0.0, 0.20, -0.9525, 0.0641),
    (bsm.PUT, 108.375, 125.0, 1.0, 0.045, 0.0, 0.15, -0.9773, 0.0423),
)

# Contracts whose exercise boundary the sweep tests cross: two puts at a positive rate and one call at a yield above
# it, each with the range of spot, as fractions of the strike from in to out of the money, that holds its boundary.
SWEEP_CONTRACTS = (
    ((bsm.PUT, 100.0, 1.0, 0.10, 0.0, 0.20), 0.78, 0.96),
    ((bsm.PUT, 100.0, 45.0 / 365.0, 0.045, 0.0, 0.35), 0.74, 0.86),
    ((bsm.CALL, 100.0, 1.0, 0.01, 0.08, 0.15), 1.20, 1.04),
)
_SWEEPS: dict = {}


def sweep(contract: tuple, start: float, stop: float) -> List[Tuple[float, lattice.LatticeResult]]:
    """Spots from ``start`` to ``stop`` times the strike in steps of 0.05% of the strike, with their results at the
    default steps (computed once per contract)."""
    if contract not in _SWEEPS:
        right, K, tau, r, q, sigma = contract
        count = int(round(abs(stop - start) / 0.0005)) + 1
        direction = 1.0 if stop > start else -1.0
        spots = [K * (start + direction * 0.0005 * n) for n in range(count)]
        _SWEEPS[contract] = [(S, lattice.american(right, S, K, tau, r, q, sigma)) for S in spots]
    return _SWEEPS[contract]


def state(result: lattice.LatticeResult) -> str:
    return "exercise" if result.exercise_now else "band" if result.near_boundary else "hold"


class BoundaryBandTest(unittest.TestCase):
    def test_the_root_alone_is_not_an_exercise_signal(self) -> None:
        for right, S, K, tau, r, q, sigma, ref_delta, ref_gamma in BOUNDARY_CASES:
            with self.subTest(S=S, K=K, tau=tau):
                result = lattice.american(right, S, K, tau, r, q, sigma)
                self.assertFalse(result.exercise_now)
                self.assertTrue(result.near_boundary)
                self.assertLess(abs(result.delta - ref_delta), 0.03)
                self.assertLess(abs(result.price - max(K - S, 0.0)), 0.02)

    def test_a_finer_tree_holds_where_the_default_root_exercises(self) -> None:
        coarse = lattice.american(bsm.PUT, 86.70, 100.0, 1.0, 0.10, 0.0, 0.20)
        fine = lattice.american(bsm.PUT, 86.70, 100.0, 1.0, 0.10, 0.0, 0.20, 3000)
        self.assertEqual(13.3, round(coarse.tree_price, 10))
        self.assertGreater(fine.tree_price, 13.3 + 1e-3)
        self.assertFalse(fine.exercise_now)

    def test_the_states_are_ordered_along_spot(self) -> None:
        for contract, start, stop in SWEEP_CONTRACTS:
            with self.subTest(contract=contract):
                states: List[str] = []
                for _, result in sweep(contract, start, stop):
                    if not states or states[-1] != state(result):
                        states.append(state(result))
                self.assertEqual(["exercise", "band", "hold"], states)

    def test_the_band_is_about_four_node_spacings_wide(self) -> None:
        for contract, start, stop in SWEEP_CONTRACTS:
            right, K, tau, r, q, sigma = contract
            with self.subTest(right=right, tau=tau):
                band = [S for S, result in sweep(contract, start, stop) if result.near_boundary]
                spacing = band[0] * sigma * math.sqrt(tau / lattice.DEFAULT_STEPS)
                width = (max(band) - min(band)) / spacing
                self.assertGreater(width, 3.0)
                self.assertLess(width, 5.0)

    def test_exercise_now_next_to_the_band_is_confirmed_by_a_finer_tree(self) -> None:
        for contract, start, stop in SWEEP_CONTRACTS:
            right, K, tau, r, q, sigma = contract
            with self.subTest(right=right, tau=tau):
                edge = [S for S, result in sweep(contract, start, stop) if result.exercise_now][-1]
                fine = lattice.american(right, edge, K, tau, r, q, sigma, 10 * lattice.DEFAULT_STEPS)
                self.assertTrue(fine.exercise_now)


class CarryAgreementTest(unittest.TestCase):
    def test_the_early_exercise_gap_of_the_carry_module(self) -> None:
        for S, K, tau, r, q, sigma in ((100.0, 105.0, 0.5, 0.03, 0.02, 0.25), (100.0, 90.0, 1.2, 0.05, -0.01, 0.4),
                                       (648.0, 640.0, 0.1, 0.045, 0.0, 0.18)):
            with self.subTest(S=S, K=K, tau=tau, r=r, q=q, sigma=sigma):
                put = lattice.american(bsm.PUT, S, K, tau, r, q, sigma, carry.TREE_STEPS)
                call = lattice.american(bsm.CALL, S, K, tau, r, q, sigma, carry.TREE_STEPS)
                gap = carry.early_exercise_gap(S, K, tau, r, q, sigma)
                self.assertAlmostEqual(gap, put.early_exercise_premium - call.early_exercise_premium, places=9)

    def test_the_default_step_count_is_the_carry_module_s(self) -> None:
        self.assertEqual(carry.TREE_STEPS, lattice.DEFAULT_STEPS)


class ExpiryTest(unittest.TestCase):
    def test_expired_options_are_worth_their_intrinsic_value(self) -> None:
        for tau in (0.0, -0.01):
            for right, strike, value, delta in ((bsm.CALL, 95.0, 5.0, 1.0), (bsm.PUT, 95.0, 0.0, 0.0),
                                                (bsm.PUT, 105.0, 5.0, -1.0), (bsm.CALL, 100.0, 0.0, 0.0),
                                                (bsm.PUT, 100.0, 0.0, 0.0)):
                with self.subTest(tau=tau, right=right, strike=strike):
                    result = lattice.american(right, 100.0, strike, tau, 0.05, 0.0, 0.3)
                    self.assertEqual("expired", result.status)
                    self.assertEqual(value, result.price)
                    self.assertEqual(0.0, result.early_exercise_premium)
                    self.assertFalse(result.exercise_now)
                    self.assertEqual((delta, 0.0, 0.0), (result.delta, result.gamma, result.theta_per_year))


class ValidationTest(unittest.TestCase):
    def test_market_inputs_are_validated_as_in_bsm(self) -> None:
        good = (100.0, 100.0, 0.5, 0.05, 0.0, 0.3)
        for index, bad in ((1, 0.0), (0, -1.0), (5, 0.0), (2, float("nan")), (3, float("inf")), (4, True)):
            args = list(good)
            args[index] = bad
            with self.subTest(index=index, bad=bad):
                with self.assertRaises(ValueError):
                    lattice.american(bsm.PUT, *args)
        with self.assertRaises(ValueError):
            lattice.american("put", *good)

    def test_decimal_and_int_inputs_are_accepted(self) -> None:
        as_float = lattice.american(bsm.PUT, 100.0, 105.0, 0.5, 0.05, 0.0, 0.3)
        mixed = lattice.american(bsm.PUT, 100, Decimal("105"), 0.5, 0.05, 0, 0.3)
        self.assertEqual(as_float, mixed)

    def test_steps_are_validated(self) -> None:
        for steps in (1, 0, -5, lattice.MAX_STEPS + 1, 200.0, True, "200", None):
            with self.subTest(steps=steps):
                with self.assertRaises(ValueError):
                    lattice.american(bsm.PUT, 100.0, 100.0, 0.5, 0.05, 0.0, 0.3, steps)  # type: ignore[arg-type]
        self.assertEqual(lattice.MIN_STEPS, lattice.american(bsm.PUT, 100.0, 100.0, 0.5, 0.05, 0.0, 0.3,
                                                             lattice.MIN_STEPS).steps)

    def test_too_few_steps_for_the_carry_name_the_least_count(self) -> None:
        self.assertEqual(26, lattice.least_steps(1.0, 0.05, 0.0, 0.01))
        with self.assertRaises(ValueError) as raised:
            lattice.american(bsm.PUT, 100.0, 100.0, 1.0, 0.05, 0.0, 0.01, 25)
        self.assertIn("at least 26 are needed", str(raised.exception))
        self.assertEqual("ok", lattice.american(bsm.PUT, 100.0, 100.0, 1.0, 0.05, 0.0, 0.01, 26).status)

    def test_least_steps_needs_a_live_option(self) -> None:
        for tau, sigma in ((0.0, 0.2), (1.0, 0.0)):
            with self.subTest(tau=tau, sigma=sigma):
                with self.assertRaises(ValueError):
                    lattice.least_steps(tau, 0.05, 0.0, sigma)

    def test_two_steps_is_a_complete_tree(self) -> None:
        result = price(bsm.PUT, HULL, 2)
        self.assertTrue(all(math.isfinite(value) for value in (result.price, result.delta, result.gamma,
                                                                result.theta_per_year)))


class TreeWordingTest(unittest.TestCase):
    """Once the lattice is in the tree, no sentence in the repository may say that it, or an early-exercise model,
    engine or tree, is planned or not in this release (the roadmap's rule: a landed step rewrites every line it makes
    untrue in the same pull request). What is still planned is American pricing: the desk's prices, Greeks and
    early-exercise analysis on that engine, which step 2.3 wires.

    Every Markdown, Python, TOML, JSON, YAML, text and shell file is read, outside hidden directories other than
    ``.github``; the roadmap and the changelog, which record the plan, and this file, which must spell the pattern, are
    not. A Python file contributes its string constants (the parser has already joined adjacent literals, so a sentence
    split across two literals is one string) and its comments; any other file contributes its whole text. Whitespace
    is collapsed, so a sentence wrapped across lines is one sentence. A sentence that names the lattice or an
    early-exercise model, engine or tree, and also carries a plan marker ("is planned", "are planned", "planned for",
    "not in this release", a later stage number (2 to 9) written with a space, a hyphen or neither, or "later stages"),
    in either order, fails, unless it is listed in :attr:`ALLOWED`. "Stage 1" is history, not a plan, and "as planned"
    makes no claim that something is missing, so neither is a marker. A true sentence of the failing shape, such as
    one about a planned feature of the lattice, is added to :attr:`ALLOWED`, collapsed, by the pull request that writes
    it: a reviewed exception, not a looser pattern."""

    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    NOT_SCANNED = ("CHANGELOG.md", os.path.join("docs", "ROADMAP.md"), os.path.join("tests", "engine", "test_lattice.py"))
    EXTENSIONS = (".md", ".py", ".toml", ".json", ".yml", ".yaml", ".txt", ".sh")
    SENTENCE_END = re.compile(r"(?<=[.;!?])\s+")
    SUBJECT = re.compile(r"lattice|early[- ]exercise (?:model|engine|tree)", re.IGNORECASE)
    PLAN = re.compile(r"\b(?:is|are) planned\b|\bplanned for\b|not in this release|\bstage[- ]?[2-9]\b|"
                      r"\blater stages?\b", re.IGNORECASE)
    ALLOWED: Tuple[str, ...] = ()

    def files(self) -> Iterator[str]:
        for folder, dirs, names in os.walk(self.ROOT):
            dirs[:] = sorted(d for d in dirs if d == ".github" or not (d.startswith(".") or d == "__pycache__"))
            for name in sorted(names):
                path = os.path.relpath(os.path.join(folder, name), self.ROOT)
                if name.endswith(self.EXTENSIONS) and path not in self.NOT_SCANNED:
                    yield path

    @staticmethod
    def prose(path: str, source: str) -> List[str]:
        if not path.endswith(".py"):
            return [source]
        tree = ast.parse(source, filename=path)
        strings = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        comments = [token.string.lstrip("#") for token in tokenize.generate_tokens(io.StringIO(source).readline)
                    if token.type == tokenize.COMMENT]
        return strings + ["\n".join(comments)]

    def claims(self, text: str) -> List[str]:
        sentences = self.SENTENCE_END.split(" ".join(text.split()))
        return [s for s in sentences if self.SUBJECT.search(s) and self.PLAN.search(s) and s not in self.ALLOWED]

    def test_no_sentence_says_the_lattice_is_planned(self) -> None:
        scanned = 0
        for path in self.files():
            with open(os.path.join(self.ROOT, path), encoding="utf-8", errors="replace") as handle:
                source = handle.read()
            scanned += 1
            with self.subTest(path=path):
                self.assertEqual([], [claim for text in self.prose(path, source) for claim in self.claims(text)])
        self.assertGreater(scanned, 300)

    def test_the_pattern_catches_each_way_of_saying_it(self) -> None:
        # Fixture sentences use stage 4, which no plan uses, so they never read as a promise of the roadmap's stages.
        for sentence in ("An American lattice is planned for Stage 4 (not in this release).",
                         "Planned for Stage 4 (not in this release): an American lattice.",
                         "The roll analysis, the American lattice and weekly expirations are\n  planned for later stages.",
                         "A Stage-4 lattice, not in this release.",
                         "The lattice lands in stage4.",
                         "An early-exercise model is planned for Stage 4 (not in this release).",
                         "The roll analysis and an American\n  early-exercise model are planned for Stage 4."):
            with self.subTest(sentence=sentence):
                self.assertEqual(1, len(self.claims(sentence)))

    def test_true_sentences_about_the_landed_lattice_pass(self) -> None:
        for sentence in ("The lattice replaced the Stage-1 European model.",
                         "In Stage 1 the tiers used the closed form, and now they use the lattice.",
                         "Position Control reads the lattice, as planned in the roadmap.",
                         "American pricing is planned for Stage 4 (not in this release).",
                         "Early-exercise analysis is planned for Stage 4 (not in this release)."):
            with self.subTest(sentence=sentence):
                self.assertEqual([], self.claims(sentence))

    def test_a_python_file_is_read_through_its_strings_and_comments(self) -> None:
        source = ('NOTE = ("the American lattice is planned for "\n'
                  '        "Stage 4 (not in this release)")\n'
                  '# a lattice for later stages\n'
                  'PLANNED = "lattice"  # names in code are not prose\n')
        found = [claim for text in self.prose("x.py", source) for claim in self.claims(text)]
        self.assertEqual(["the American lattice is planned for Stage 4 (not in this release)",
                          "a lattice for later stages names in code are not prose"], found)

    def test_a_true_sentence_passes_only_when_it_is_listed(self) -> None:
        sentence = "Discrete cash dividends in the lattice are planned for Stage 4 (not in this release)."
        self.assertEqual([sentence], self.claims(sentence))
        self.ALLOWED = (sentence,)
        self.assertEqual([], self.claims(sentence))

    def test_the_structuring_model_note_names_american_pricing_as_planned(self) -> None:
        from aegis_desk import structuring
        self.assertNotIn("lattice", structuring.MODEL_NOTE)
        self.assertIn("American pricing is planned for Stage 2 (not in this release)", structuring.MODEL_NOTE)


if __name__ == "__main__":
    unittest.main()
