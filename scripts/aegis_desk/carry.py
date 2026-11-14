"""The carry an expiration's own quotes imply, by put-call parity (standard library only; floats in, floats out).

For one expiration, at every strike K where both the call and the put have a two-sided quote, parity gives the forward
``F_K = K + e^(r tau) (C_mid - P_mid)``. Two things separate ``F_K`` from the true forward:

- **Quote resolution.** Each mid lies within its own half-spread of any price inside the quote, so ``F_K`` is known only
  to within ``e_K = e^(r tau) (h_C + h_P)``, ``h`` a leg's half-spread. The implied forward ``F`` is the median of ``F_K``
  over the ``NEAREST`` strikes closest to the spot; the median of values each within ``e_K`` of a number lies within
  ``max e_K`` of it, so ``q_quote = max e_K / (F tau)`` bounds this part (forward error is magnified by ``1 / tau``).
- **American early exercise.** Listed equity options are American. The put's early-exercise premium exceeds the call's,
  so American ``C - P`` sits below the European value and ``F_K`` is biased LOW, which biases ``q_imp`` HIGH, never low.
  :func:`early_exercise_gap` measures ``EEP_put - EEP_call`` on a Cox-Ross-Rubinstein tree at each strike's own
  volatility, at the carry ``min(0, q_imp)`` (the gap shrinks as the carry rises, so this is the larger value for any
  carry at least that low), and ``q_amer = max_K e^(r tau) gap_K / (F tau)``. A source that quotes European-style
  prices (the repository's synthetic fixture) has no such bias, so ``q_amer`` widens the interval on one side only.

So the true carry lies in ``[q_imp - q_amer - q_quote, q_imp + q_quote]`` (within the tree model for the American part).
:func:`implied_carry` returns ``q_imp`` only when the calendar yield lies OUTSIDE that interval (the quotes exclude it)
and the gap is not so large that it looks like a data error (``<= MAX_GAP``); otherwise ``None`` and the caller keeps
the calendar yield and says why. It reports the raw ``q_imp`` rather than a de-Americanized value because it cannot tell
from the quotes alone whether a source embeds the premium; the interval, not the point, is the claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

NEAREST: int = 3
MIN_STRIKES: int = 2
MAX_GAP: float = 0.05
TREE_STEPS: int = 200
"""Cox-Ross-Rubinstein steps for :func:`early_exercise_gap` (the American and European values share one tree, so most
of the discretisation error cancels in their difference)."""

Quote = Tuple[float, float]
"""``(bid, ask)`` of one leg."""


@dataclass(frozen=True)
class ParityCarry:
    """``q_imp`` from the median parity forward, ``q_quote`` its quote-resolution bound and ``q_amer`` the one-sided
    early-exercise bias bound (module notes): the truth lies in ``[q_imp - q_amer - q_quote, q_imp + q_quote]``."""

    q_imp: float
    q_quote: float
    q_amer: float

    def excludes(self, q: float) -> bool:
        """Whether ``q`` lies outside the interval the quotes allow."""
        return q > self.q_imp + self.q_quote or q < self.q_imp - self.q_amer - self.q_quote


def _tree(spot: float, strike: float, tau: float, rate: float, carry: float, sigma: float, call: bool,
          american: bool, steps: int) -> float:
    dt = tau / steps
    up = math.exp(sigma * math.sqrt(dt))
    down = 1.0 / up
    p = (math.exp((rate - carry) * dt) - down) / (up - down)
    disc = math.exp(-rate * dt)
    sign = 1.0 if call else -1.0
    values = [max(0.0, sign * (spot * up ** j * down ** (steps - j) - strike)) for j in range(steps + 1)]
    for i in range(steps - 1, -1, -1):
        for j in range(i + 1):
            held = disc * (p * values[j + 1] + (1.0 - p) * values[j])
            if american:
                held = max(held, sign * (spot * up ** j * down ** (i - j) - strike))
            values[j] = held
    return values[0]


def early_exercise_gap(spot: float, strike: float, tau: float, rate: float, carry: float, sigma: float,
                       steps: int = TREE_STEPS) -> float:
    """``EEP_put - EEP_call``: the American put's early-exercise premium less the American call's, each the American
    less the European value on one CRR tree."""
    if spot <= 0 or strike <= 0 or tau <= 0 or sigma <= 0 or steps < 1:
        raise ValueError("early_exercise_gap needs a positive spot, strike, tau, sigma and steps")
    put = (_tree(spot, strike, tau, rate, carry, sigma, False, True, steps)
           - _tree(spot, strike, tau, rate, carry, sigma, False, False, steps))
    call = (_tree(spot, strike, tau, rate, carry, sigma, True, True, steps)
            - _tree(spot, strike, tau, rate, carry, sigma, True, False, steps))
    return put - call


def parity_carry(calls: Mapping[float, Quote], puts: Mapping[float, Quote], spot: float, tau: float, rate: float,
                 vols: Mapping[float, float], with_bias: bool = True) -> Optional[ParityCarry]:
    """The parity carry and its two bounds from two-sided quotes keyed by strike and each strike's volatility (module
    notes), or ``None`` with too few usable strikes. ``with_bias=False`` skips the early-exercise tree and reports
    ``q_amer = 0.0`` (:func:`decide` asks for the tree only when its answer depends on it)."""
    if spot <= 0 or tau <= 0:
        raise ValueError("parity_carry needs a positive spot and tau")
    usable = [k for k in set(calls) & set(puts) & set(vols)
              if 0 <= calls[k][0] <= calls[k][1] and 0 <= puts[k][0] <= puts[k][1] and calls[k][1] > 0 < puts[k][1]
              and vols[k] > 0]
    strikes = sorted(usable, key=lambda k: (abs(k - spot), k))[:NEAREST]
    if len(strikes) < MIN_STRIKES:
        return None
    growth = math.exp(rate * tau)
    forwards = sorted(k + growth * (sum(calls[k]) - sum(puts[k])) / 2 for k in strikes)
    middle = len(forwards) // 2
    forward = forwards[middle] if len(forwards) % 2 else 0.5 * (forwards[middle - 1] + forwards[middle])
    if forward <= 0:
        return None
    q_imp = rate - math.log(forward / spot) / tau
    error = max(growth * ((calls[k][1] - calls[k][0]) + (puts[k][1] - puts[k][0])) / 2 for k in strikes)
    bias = 0.0
    if with_bias:
        tree_carry = min(0.0, q_imp)
        bias = max(max(0.0, early_exercise_gap(spot, k, tau, rate, tree_carry, vols[k])) for k in strikes)
    return ParityCarry(q_imp, error / (forward * tau), growth * bias / (forward * tau))


SOURCE_PARITY: str = "parity"
"""The quotes exclude the calendar's dividend rate: the expiration is priced at ``q_imp``."""
SOURCE_UNRESOLVED: str = "calendar_unresolved"
"""The calendar's rate lies inside the interval the quotes allow: they cannot resolve the carry, the calendar stands."""
SOURCE_GAP: str = "calendar_gap"
"""``q_imp`` lies more than :data:`MAX_GAP` from the calendar's rate, which looks like a data error: the calendar
stands."""
SOURCE_TOO_FEW: str = "calendar_too_few_strikes"
"""Fewer than :data:`MIN_STRIKES` strikes quote both a call and a put: the calendar stands."""


@dataclass(frozen=True)
class CarryDecision:
    """What :func:`decide` chose: ``q`` the parity carry (``None``: the calendar's rate stands), ``source`` one of the
    ``SOURCE_*`` words, ``found`` the parity estimate (``None`` with too few strikes) and ``bias_computed`` whether its
    ``q_amer`` came from the early-exercise tree."""

    q: Optional[float]
    source: str
    found: Optional[ParityCarry]
    bias_computed: bool


def decide(calls: Mapping[float, Quote], puts: Mapping[float, Quote], spot: float, tau: float, rate: float,
           vols: Mapping[float, float], calendar_q: float) -> CarryDecision:
    """The carry of one expiration (module notes). The interval is ``[q_imp - q_amer - q_quote, q_imp + q_quote]``,
    and only its lower end needs the tree: a calendar rate above ``q_imp + q_quote`` is excluded and one in
    ``[q_imp - q_quote, q_imp + q_quote]`` is kept, whatever ``q_amer`` is. So the tree runs only for a calendar rate
    below ``q_imp - q_quote``, and the answer is the one :func:`parity_carry` with the tree would give."""
    found = parity_carry(calls, puts, spot, tau, rate, vols, with_bias=False)
    if found is None:
        return CarryDecision(None, SOURCE_TOO_FEW, None, False)
    if abs(found.q_imp - calendar_q) > MAX_GAP:
        return CarryDecision(None, SOURCE_GAP, found, False)
    if calendar_q > found.q_imp + found.q_quote:
        return CarryDecision(found.q_imp, SOURCE_PARITY, found, False)
    if calendar_q >= found.q_imp - found.q_quote:
        return CarryDecision(None, SOURCE_UNRESOLVED, found, False)
    full = parity_carry(calls, puts, spot, tau, rate, vols)
    assert full is not None
    if full.excludes(calendar_q):
        return CarryDecision(full.q_imp, SOURCE_PARITY, full, True)
    return CarryDecision(None, SOURCE_UNRESOLVED, full, True)


def implied_carry(calls: Mapping[float, Quote], puts: Mapping[float, Quote], spot: float, tau: float, rate: float,
                  vols: Mapping[float, float], calendar_q: float) -> Optional[float]:
    """``q_imp`` when the quotes exclude ``calendar_q`` (module notes), else ``None``."""
    return decide(calls, puts, spot, tau, rate, vols, calendar_q).q


__all__ = ["CarryDecision", "MAX_GAP", "MIN_STRIKES", "NEAREST", "ParityCarry", "Quote", "SOURCE_GAP", "SOURCE_PARITY",
           "SOURCE_TOO_FEW", "SOURCE_UNRESOLVED", "TREE_STEPS", "decide", "early_exercise_gap", "implied_carry",
           "parity_carry"]
