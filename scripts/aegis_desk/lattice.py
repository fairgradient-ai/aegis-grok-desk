"""American early-exercise lattice: a Cox-Ross-Rubinstein binomial tree with a continuous dividend yield (stdlib only).

Step 2.1 of ``docs/ROADMAP.md``. This module is the engine only: no tool, routine, Greek in ``book.json`` or
structuring answer reads it yet, and the European closed form of :mod:`aegis_desk.bsm` stays the model of record until
the wiring step lands with its own tests. Pure and synchronous: floats in, floats out; no I/O, no clock, no global
state.

Conventions are those of :mod:`aegis_desk.bsm` (``right`` is ``"CALL"`` or ``"PUT"``; ``S``, ``K`` > 0; ``tau`` in
years on a 365-day year, ``tau <= 0`` expired; ``r`` and ``q`` continuous and annualised, either may be negative;
``sigma`` > 0), validated by :func:`aegis_desk.bsm.validate_market`, so an input error raises the same
:class:`ValueError`.

The tree
--------
``steps`` periods of ``dt = tau / steps``; ``u = e^(sigma sqrt dt)``, ``d = 1 / u``; the risk-neutral up probability
``p = (e^((r - q) dt) - d) / (u - d)``; discount ``e^(-r dt)`` per period. Node ``(i, j)`` (``j`` up moves after ``i``
periods) has the spot ``S u^(2j - i)``. At expiry each node holds the intrinsic value; rolling back, the European value
is the discounted expectation and the American value is the larger of that expectation and immediate exercise. ``p``
lies strictly inside ``(0, 1)`` only while ``|r - q| dt < sigma sqrt dt``, that is ``steps > tau ((r - q) / sigma)^2``;
fewer steps raise :class:`ValueError` naming the least count that works (a caller's choice, not a market state).

What a result reports (:class:`LatticeResult`)
----------------------------------------------
* ``tree_price``: the raw American value of the tree. With ``S = K = 50``, ``r = 0.10``, ``q = 0``, ``sigma = 0.40``
  and ``tau = 5/12`` the put is 4.488 at 5 steps and 4.263, 4.272, 4.278 and 4.283 at 30, 50, 100 and 500 steps:
  the values Hull prints for this example in *Options, Futures, and Other Derivatives*, in the chapter on basic
  numerical procedures (``tests/engine/test_lattice.py`` pins them).
* ``european_tree``: the European value of the same tree; ``european_closed``: :func:`aegis_desk.bsm.price`.
* ``early_exercise_premium = tree_price - european_tree``: both values come from one tree, so most of the
  discretisation error cancels in the difference; it is never negative, and it is exactly zero for a call when
  ``q <= 0`` and ``r > 0`` (the continuation value of such a call exceeds its exercise value at every node by at least
  ``K (1 - e^(-r dt))``, so, for any rate whose margin is above rounding, no node exercises and both trees run the same
  arithmetic). At ``r = 0`` and ``q = 0`` the two values tie in exact arithmetic
  at the deep in-the-money nodes, and rounding can leave a premium of a few units in the last place, never below zero.
* ``exercise_now``: immediate exercise is optimal now, decided with a margin the tree can stand behind. It is true
  only when exercise is strictly better than holding (the intrinsic value is positive and exceeds the discounted
  continuation value by more than :data:`EXERCISE_MARGIN` times ``max(S, K)``) at the root and at every node of steps
  1 and 2, so that the spot lies at least two node spacings (``S (u^2 - 1)``) inside the tree's exercise region. The
  root alone is not enough: a tree places its exercise boundary up to about one node spacing from the continuous-time
  boundary (measured: the tree exercises 0.5 to 0.75 of a spacing early at :data:`DEFAULT_STEPS`), so a root that
  exercises within that band may be a holder's position in fact. :data:`EXERCISE_MARGIN` keeps a rounding tie from
  reading as exercise: at ``r = 0`` and ``q = 0`` a deep in-the-money node's continuation value equals its exercise
  value in exact arithmetic (early exercise is then never strictly better, and the American value is the European
  one), and floating point can land either side of it by a few units in the last place.
* ``near_boundary``: some, but not all, of those six nodes exercise. The spot is then within about two node spacings
  of the tree's exercise boundary, where the tree cannot tell exercise from holding and its step-1 and step-2 finite
  differences straddle the boundary's kink. ``exercise_now`` is false there, the price is still the control-variate
  estimate, and delta, gamma and theta are reported but coarse (limits below). A caller that needs them there either
  marks them approximate or prices again with more steps, which narrows the band in proportion to
  ``1 / sqrt(steps)``. ``exercise_now`` and ``near_boundary`` are never both true; at ``r = 0`` and ``q = 0`` both are
  false.
* ``price``: the control-variate estimate ``european_closed + early_exercise_premium`` (Hull's control variate for an
  American option on a tree), floored at the intrinsic value; when ``exercise_now`` it is the intrinsic value itself,
  because the holder's value at an optimal exercise point is what exercise pays.
* ``delta``, ``gamma`` and ``theta_per_year``: the tree's finite differences at steps 1 and 2 (delta from the two
  step-1 nodes, gamma from the three step-2 nodes, theta from the middle step-2 node against the root over ``2 dt``),
  in the natural units of :mod:`aegis_desk.bsm`, with the same control variate (the closed-form Greek plus the American
  tree's Greek less the European tree's). When ``exercise_now`` they are those of the exercise payoff: delta ``1.0``
  (call) or ``-1.0`` (put), gamma and theta ``0.0``. The lattice gives no vega and no rho.

Limits, stated rather than hidden
---------------------------------
* The dividend is the continuous yield the rest of the engine uses (``research/calendar.toml``'s
  ``dividend_yield``). A discrete dividend, which is what makes a short call's early assignment the evening before an
  ex-date likely, is not modelled here; the roadmap proposes it as its own step.
* A binomial value converges to the continuous-time value at the rate ``1 / steps``, with an oscillation in the
  steps' parity; the tree's exercise boundary converges only at ``1 / sqrt(steps)``. Measured at
  :data:`DEFAULT_STEPS` against QuantLib's finite-difference American engine (2000 x 2000) on the oracle's 480-point
  grid, on the 729 spots the oracle places across the exercise boundary of 24 contracts, and on a wider sweep of 3528
  spots, a quarter of a node spacing apart, across the boundary of 72 contracts (puts at positive rates, calls at a
  yield above the rate, 14 days to a year, volatility 0.15 to 0.7): ``exercise_now`` was never true where the
  reference holds (1411 cases); the price was within 1.5e-3 of ``max(price, 1)`` everywhere; away from the band
  delta was within 1.4e-3 and gamma within 9.4e-4 per $1^2; inside the band delta was off by up to 0.028 and gamma
  by up to 0.33 per $1^2. ``tests/oracle/test_quantlib_oracle.py`` asserts bounds of about twice the measured worst
  away from the band, the price bound inside it, and no ``exercise_now`` where the reference holds. More steps
  narrow both the error and the band. :data:`MAX_STEPS` bounds the run time of one call.

Expiry (``tau <= 0``, ``status == "expired"``): the price is the intrinsic value, the premium is ``0.0``, delta follows
:func:`aegis_desk.bsm.delta`'s expiry convention (``1.0`` / ``-1.0`` strictly in the money, otherwise ``0.0``) and
gamma and theta are ``0.0``; ``exercise_now`` and ``near_boundary`` are ``False``, because expiry settles by exercise by exception, not by
the holder's choice on this model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

from . import bsm

DEFAULT_STEPS: int = 200
"""Steps when the caller names none (the same count as :data:`aegis_desk.carry.TREE_STEPS`)."""

MIN_STEPS: int = 2
"""The fewest steps: gamma and theta read the three nodes of step 2."""

MAX_STEPS: int = 5000
"""The most steps one call accepts (about 12.5 million node updates, a few seconds on the desk computer)."""

EXERCISE_MARGIN: float = 1e-12
"""How much more than the continuation value, as a fraction of ``max(S, K)``, immediate exercise must pay at a node
before the node counts as exercising (``exercise_now``, ``near_boundary``). A rounding tie is a few units in the last place, about 1e-14 of ``max(S, K)``; a real
exercise advantage of a dollar-scale option is many orders larger."""


@dataclass(frozen=True)
class LatticeResult:
    """One option on the tree (module notes): values per share, Greeks in :mod:`aegis_desk.bsm`'s natural units."""

    price: float
    tree_price: float
    european_tree: float
    european_closed: float
    early_exercise_premium: float
    exercise_now: bool
    near_boundary: bool
    delta: float
    gamma: float
    theta_per_year: float
    steps: int
    status: str


def validate_steps(steps: object) -> int:
    """``steps`` as an ``int`` within ``[MIN_STEPS, MAX_STEPS]``; a bool, a float or any other type is a
    :class:`ValueError`."""
    if isinstance(steps, bool) or not isinstance(steps, int):
        raise ValueError("steps must be an int, got %s" % type(steps).__name__)
    if steps < MIN_STEPS or steps > MAX_STEPS:
        raise ValueError("steps must be between %d and %d, got %d" % (MIN_STEPS, MAX_STEPS, steps))
    return steps


def least_steps(tau: float, r: float, q: float, sigma: float) -> int:
    """The fewest steps whose up probability lies strictly inside ``(0, 1)``: the least integer above
    ``tau ((r - q) / sigma)^2``, and never below :data:`MIN_STEPS`. Requires ``tau > 0`` and ``sigma > 0``."""
    if not (tau > 0.0 and sigma > 0.0):
        raise ValueError("least_steps needs tau > 0 and sigma > 0")
    bound = tau * ((r - q) / sigma) ** 2
    return max(MIN_STEPS, int(math.floor(bound)) + 1)


def _roll_back(call: bool, S: float, K: float, tau: float, r: float, q: float, sigma: float,
               steps: int) -> Tuple[List[float], List[float], List[float], List[float], float, float, List[bool]]:
    """Roll the American and the European tree back together. Returns the American and European values at step 2
    (three nodes each) and at step 1 (two nodes each), the American continuation value at the root, the European
    value at the root, and, for the six nodes of steps 0 to 2 (root first, then step 1, then step 2), whether
    immediate exercise is strictly better there (:data:`EXERCISE_MARGIN`). Both trees use the same arithmetic, so where
    no node exercises the two are equal bit for bit."""
    dt = tau / steps
    move = sigma * math.sqrt(dt)
    up = math.exp(move)
    down = 1.0 / up
    growth = math.exp((r - q) * dt)
    p = (growth - down) / (up - down)
    if not 0.0 < p < 1.0:
        raise ValueError(
            "the tree's up probability %r lies outside (0, 1): %d steps are too few for tau %r, carry %r and sigma "
            "%r; at least %d are needed" % (p, steps, tau, r - q, sigma, least_steps(tau, r, q, sigma)))
    disc = math.exp(-r * dt)
    p_up = disc * p
    p_down = disc * (1.0 - p)
    sign = 1.0 if call else -1.0
    margin = EXERCISE_MARGIN * max(S, K)
    # spots[k + steps] is S u^k for k in [-steps, steps]; node (i, j) has k = 2j - i.
    spots = [S * math.exp(move * k) for k in range(-steps, steps + 1)]
    american = [max(0.0, sign * (spots[2 * j] - K)) for j in range(steps + 1)]
    european = list(american)
    saved_american: List[List[float]] = [[], [], []]
    saved_european: List[List[float]] = [[], [], []]
    exercised: List[List[bool]] = [[], [], []]
    if steps == 2:  # the step-2 nodes are the expiry nodes, where a positive payoff is always taken
        saved_american[2] = list(american)
        saved_european[2] = list(european)
        exercised[2] = [value > margin for value in american]
    continuation_root = 0.0
    for i in range(steps - 1, -1, -1):
        offset = steps - i
        for j in range(i + 1):
            held = p_up * american[j + 1] + p_down * american[j]
            exercise = sign * (spots[offset + 2 * j] - K)
            if i <= 2:
                exercised[i].append(exercise > 0.0 and exercise - held > margin)
                if i == 0:
                    continuation_root = held
            american[j] = held if held >= exercise else exercise
            european[j] = p_up * european[j + 1] + p_down * european[j]
        if i <= 2:
            saved_american[i] = american[:i + 1]
            saved_european[i] = european[:i + 1]
    flags = exercised[0] + exercised[1] + exercised[2]
    return (saved_american[2], saved_european[2], saved_american[1], saved_european[1], continuation_root,
            european[0], flags)


def american(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float,
             steps: int = DEFAULT_STEPS) -> LatticeResult:
    """Price one American option on the tree (module notes)."""
    call = bsm.is_call(right)
    S, K, tau, r, q, sigma = bsm.validate_market(S, K, tau, r, q, sigma)
    steps = validate_steps(steps)
    intrinsic = bsm.intrinsic(right, S, K)
    if tau <= 0.0:
        return LatticeResult(price=intrinsic, tree_price=intrinsic, european_tree=intrinsic, european_closed=intrinsic,
                             early_exercise_premium=0.0, exercise_now=False, near_boundary=False,
                             delta=bsm.delta(right, S, K, tau, r, q, sigma), gamma=0.0, theta_per_year=0.0,
                             steps=steps, status=bsm.STATUS_EXPIRED)
    am2, eu2, am1, eu1, continuation, european_tree, flags = _roll_back(call, S, K, tau, r, q, sigma, steps)
    dt = tau / steps
    up = math.exp(sigma * math.sqrt(dt))
    down = 1.0 / up
    tree_price = max(continuation, intrinsic)
    european_closed = bsm.price(right, S, K, tau, r, q, sigma)
    premium = tree_price - european_tree
    if all(flags):
        return LatticeResult(price=intrinsic, tree_price=tree_price, european_tree=european_tree,
                             european_closed=european_closed, early_exercise_premium=premium, exercise_now=True,
                             near_boundary=False, delta=1.0 if call else -1.0, gamma=0.0, theta_per_year=0.0,
                             steps=steps, status=bsm.STATUS_OK)

    def finite_differences(level1: List[float], level2: List[float], root: float) -> Tuple[float, float, float]:
        s_up, s_down = S * up, S * down
        s_uu, s_dd = S * up * up, S * down * down
        delta = (level1[1] - level1[0]) / (s_up - s_down)
        gamma = ((level2[2] - level2[1]) / (s_uu - S) - (level2[1] - level2[0]) / (S - s_dd)) / (0.5 * (s_uu - s_dd))
        theta = (level2[1] - root) / (2.0 * dt)
        return delta, gamma, theta

    am_delta, am_gamma, am_theta = finite_differences(am1, am2, tree_price)
    eu_delta, eu_gamma, eu_theta = finite_differences(eu1, eu2, european_tree)
    return LatticeResult(
        price=max(intrinsic, european_closed + premium),
        tree_price=tree_price,
        european_tree=european_tree,
        european_closed=european_closed,
        early_exercise_premium=premium,
        exercise_now=False,
        near_boundary=any(flags),
        delta=bsm.delta(right, S, K, tau, r, q, sigma) + am_delta - eu_delta,
        gamma=bsm.gamma(right, S, K, tau, r, q, sigma) + am_gamma - eu_gamma,
        theta_per_year=bsm.theta(right, S, K, tau, r, q, sigma) + am_theta - eu_theta,
        steps=steps,
        status=bsm.STATUS_OK,
    )


__all__ = [
    "DEFAULT_STEPS",
    "EXERCISE_MARGIN",
    "MAX_STEPS",
    "MIN_STEPS",
    "LatticeResult",
    "american",
    "least_steps",
    "validate_steps",
]
