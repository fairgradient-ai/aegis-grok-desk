"""One option leg on the American model of record: value, Greeks in desk units, and implied volatility (roadmap 2.3).

The owner's answer to the roadmap's open question 2 makes the American lattice (:mod:`aegis_desk.lattice`, at
:data:`aegis_desk.lattice.DEFAULT_STEPS`) the model of record, one value per leg. This module is the one place that
turns a lattice result into what the desk stores and compares, so the book, the structuring tools and the scenario
tool price a leg the same way:

    price, delta, gamma, theta   from :func:`aegis_desk.lattice.american` (theta per calendar day, as
                                 :mod:`aegis_desk.greeks` stores it)
    vega                         per vol point: the central difference of the lattice price at sigma +- 0.01
                                 (a smaller step, sigma / 2, when sigma is below 0.02, and one-sided where the low
                                 side would fall below the lowest volatility the tree accepts, :func:`tree_floor`)
    rho                          per 1 % of rate: the central difference of the lattice price at r +- 0.01, q fixed
    early_exercise_premium       American less European, from the same tree (``LatticeResult.early_exercise_premium``)
    quality                      ``"ok"``, or ``"near_boundary"`` when the lattice flags the leg as within about two
                                 node spacings of its exercise boundary

The boundary band (the owner's answer to open question 2): a ``near_boundary`` leg keeps its price (the measured price
bound holds there too), its vega and rho (both differences of the price), and its delta, which is approximate within
:data:`aegis_desk.lattice.BAND_DELTA_TOL`; its gamma and theta are ``None`` (UNAVAILABLE): the measured band gamma
error reaches 0.33 per $1^2. Nothing is repriced with more steps.

Implied volatility (:func:`implied_vol`) inverts the lattice price by Brent's method (:func:`aegis_desk.iv.brent_root`)
and returns an :class:`aegis_desk.iv.IvResult` with the statuses of :mod:`aegis_desk.iv`. The no-arbitrage bounds of an
American option are the undiscounted intrinsic value (with the European lower bound, whichever is larger) below and
``S`` (call) or ``K`` (put) above. A quote below the lower bound is ``below_intrinsic``, above the upper bound
``above_bound``, and on either bound ``no_root``: at the intrinsic value an American option that is exercised now is
worth the same for a whole range of volatilities, so no single one is implied. The European implied volatility is the
starting bracket: at one volatility the American value is never below the European one, so the American implied
volatility is never above the European. When the American value at the European implied volatility already matches the
quote within :data:`PRICE_TOL` (a call with no early-exercise value, for one), that volatility is the answer.

Pure and synchronous: floats in, floats out; no I/O, no clock. Inputs follow :mod:`aegis_desk.bsm` and input errors
raise the same :class:`ValueError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import bsm, iv, lattice
from .units import CONTRACT_MULTIPLIER, DAYS_PER_YEAR, RATE_POINT, VOL_POINT

MODEL_AMERICAN: str = "american_crr_%d" % lattice.DEFAULT_STEPS
"""The ``model`` a leg priced here carries in ``book.json``."""
MODEL_EUROPEAN: str = "european_bsm"
"""The ``model`` of a record written before the American model of record (a snapshot without the field)."""
QUALITY_OK: str = "ok"
QUALITY_NEAR_BOUNDARY: str = "near_boundary"
VEGA_BUMP: float = VOL_POINT
"""The volatility step of the vega difference (smaller when sigma is below twice this)."""
RHO_BUMP: float = RATE_POINT
"""The rate step of the rho difference."""
PRICE_TOL: float = 1e-9
"""A price within this, in $ per share, of the quote counts as a match for the implied volatility."""
XTOL: float = 1e-8
"""The implied volatility's tolerance on sigma (the lattice price is not smooth below this)."""
SIGMA_FLOOR: float = iv.BRACKET_LO_EXPANDED
"""The lowest volatility the search tries (higher where the tree needs it: :func:`tree_floor`)."""
SIGMA_CEILING: float = iv.BRACKET_HI_EXPANDED
"""The highest volatility the search tries."""


@dataclass(frozen=True)
class AmericanValue:
    """One leg on the American model of record (module notes): per share, Greeks in desk units; ``gamma`` and
    ``theta`` are ``None`` in the boundary band."""

    price: float
    european: float
    early_exercise_premium: float
    exercise_now: bool
    near_boundary: bool
    delta: float
    gamma: Optional[float]
    theta: Optional[float]
    vega: float
    rho: float
    iv: float
    quality: str
    status: str
    model: str = MODEL_AMERICAN

    @property
    def expired(self) -> bool:
        return self.status == bsm.STATUS_EXPIRED

    def greeks(self) -> Dict[str, Optional[float]]:
        """The five Greeks and the volatility per share, as ``book.json`` stores them."""
        return {"delta": self.delta, "gamma": self.gamma, "theta": self.theta, "vega": self.vega, "rho": self.rho,
                "iv": self.iv}

    def position_greeks(self, quantity: float, multiplier: int = CONTRACT_MULTIPLIER) -> Dict[str, Optional[float]]:
        """Each Greek times the signed contract count and the multiplier; a ``None`` Greek stays ``None``."""
        scale = bsm.as_float("quantity", quantity) * bsm.as_float("multiplier", multiplier)
        scaled: Dict[str, Optional[float]] = {}
        for name, value in self.greeks().items():
            scaled[name] = value if name == "iv" or value is None else value * scale
        return scaled

    def as_dict(self) -> Dict[str, Any]:
        return {"model": self.model, "price": self.price, "european": self.european,
                "early_exercise_premium": self.early_exercise_premium, "exercise_now": self.exercise_now,
                "near_boundary": self.near_boundary, "quality": self.quality, "status": self.status,
                **self.greeks()}


def _price(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float, steps: int) -> float:
    return lattice.american(right, S, K, tau, r, q, sigma, steps).price


def tree_floor(tau: float, r: float, q: float, steps: int = lattice.DEFAULT_STEPS) -> float:
    """The lowest volatility the tree accepts at ``steps`` (its up probability must lie inside ``(0, 1)``, which needs
    ``sigma > |r - q| sqrt(tau / steps)``), with a margin, and never below :data:`SIGMA_FLOOR`."""
    return max(SIGMA_FLOOR, abs(r - q) * (tau / steps) ** 0.5 * (1.0 + 1e-6) + 1e-12)


def value(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float,
          steps: int = lattice.DEFAULT_STEPS) -> AmericanValue:
    """Price one American leg and its Greeks at ``sigma`` (module notes)."""
    S, K, tau, r, q, sigma = bsm.validate_market(S, K, tau, r, q, sigma)
    result = lattice.american(right, S, K, tau, r, q, sigma, steps)
    if result.status == bsm.STATUS_EXPIRED:
        return AmericanValue(price=result.price, european=result.european_closed, early_exercise_premium=0.0,
                             exercise_now=False, near_boundary=False, delta=result.delta, gamma=0.0, theta=0.0,
                             vega=0.0, rho=0.0, iv=sigma, quality=QUALITY_OK, status=result.status)
    h_vol = min(VEGA_BUMP, sigma / 2.0)
    low = max(sigma - h_vol, tree_floor(tau, r, q, steps))  # one-sided below where the tree stops
    vega = ((_price(right, S, K, tau, r, q, sigma + h_vol, steps) - _price(right, S, K, tau, r, q, low, steps))
            / (sigma + h_vol - low) * VOL_POINT)
    rho = ((_price(right, S, K, tau, r + RHO_BUMP, q, sigma, steps) - _price(right, S, K, tau, r - RHO_BUMP, q, sigma,
                                                                             steps)) / (2.0 * RHO_BUMP) * RATE_POINT)
    band = result.near_boundary
    return AmericanValue(
        price=result.price, european=result.european_closed, early_exercise_premium=result.early_exercise_premium,
        exercise_now=result.exercise_now, near_boundary=band, delta=result.delta,
        gamma=None if band else result.gamma, theta=None if band else result.theta_per_year / DAYS_PER_YEAR,
        vega=vega, rho=rho, iv=sigma, quality=QUALITY_NEAR_BOUNDARY if band else QUALITY_OK, status=result.status)


def price_bounds(right: str, S: float, K: float, tau: float, r: float, q: float) -> tuple:
    """No-arbitrage ``(lower, upper)`` bounds on an American option price per share: the undiscounted intrinsic value or
    the European lower bound, whichever is larger, and ``S`` (call) or ``K`` (put)."""
    call = bsm.is_call(right)
    S, K = bsm.validate_spot_strike(S, K)
    european_lower, _upper = iv.price_bounds(right, S, K, tau, r, q)
    lower = max(bsm.intrinsic(right, S, K), european_lower)
    if bsm.as_float("tau", tau) <= 0.0:
        return lower, lower
    return lower, S if call else K


def _rejected(status: str, lower: float, upper: float, iterations: int = 0,
              residual: Optional[float] = None) -> iv.IvResult:
    return iv.IvResult(sigma=None, status=status, iterations=iterations, residual=residual, lower_bound=lower,
                       upper_bound=upper)


def implied_vol(price: float, right: str, S: float, K: float, tau: float, r: float, q: float,
                steps: int = lattice.DEFAULT_STEPS) -> iv.IvResult:
    """The volatility at which the American lattice price equals ``price`` (module notes). ``iterations`` counts the
    lattice prices the search took."""
    bsm.is_call(right)
    target = bsm.as_float("price", price)
    S, K = bsm.validate_spot_strike(S, K)
    tau, r, q = bsm.as_float("tau", tau), bsm.as_float("r", r), bsm.as_float("q", q)
    lower, upper = price_bounds(right, S, K, tau, r, q)
    if tau <= 0.0:
        return _rejected(iv.STATUS_EXPIRED, lower, upper)
    if target < lower - iv.BOUND_SLACK:
        return _rejected(iv.STATUS_BELOW_INTRINSIC, lower, upper)
    if target > upper + iv.BOUND_SLACK:
        return _rejected(iv.STATUS_ABOVE_BOUND, lower, upper)
    if target <= lower + iv.BOUND_SLACK or target >= upper - iv.BOUND_SLACK:
        return _rejected(iv.STATUS_NO_ROOT, lower, upper)
    evaluations = [0]

    def f(sigma: float) -> float:
        evaluations[0] += 1
        return _price(right, S, K, tau, r, q, sigma, steps) - target

    european = iv.implied_vol(target, right, S, K, tau, r, q)
    floor = tree_floor(tau, r, q, steps)
    lo, hi = floor, SIGMA_CEILING
    f_lo: Optional[float] = None
    f_hi: Optional[float] = None
    if european.status == iv.STATUS_OK and european.sigma is not None and european.sigma > floor:
        start = european.sigma
        f_start = f(start)
        if abs(f_start) <= PRICE_TOL:
            return iv.IvResult(sigma=start, status=iv.STATUS_OK, iterations=evaluations[0], residual=f_start,
                               lower_bound=lower, upper_bound=upper)
        if f_start > 0.0:  # the usual case: an early-exercise value lowers the volatility that matches the quote
            hi, f_hi = start, f_start
            probe = start
            while probe > floor:
                probe = max(floor, probe * 0.8)
                f_probe = f(probe)
                if f_probe <= 0.0:
                    lo, f_lo = probe, f_probe
                    break
                hi, f_hi = probe, f_probe
        else:  # the tree's own error put the American value a hair below the quote
            lo, f_lo = start, f_start
            probe = start
            while probe < SIGMA_CEILING:
                probe = min(SIGMA_CEILING, probe * 1.05)
                f_probe = f(probe)
                if f_probe >= 0.0:
                    hi, f_hi = probe, f_probe
                    break
                lo, f_lo = probe, f_probe
    if f_lo is None:
        f_lo = f(lo)
    if f_hi is None:
        f_hi = f(hi)
    if f_lo > 0.0 or f_hi < 0.0:
        return _rejected(iv.STATUS_NO_ROOT, lower, upper, evaluations[0])
    if f_lo == 0.0 or f_hi == 0.0:
        root = lo if f_lo == 0.0 else hi
        return iv.IvResult(sigma=root, status=iv.STATUS_OK, iterations=evaluations[0], residual=0.0,
                           lower_bound=lower, upper_bound=upper)
    result = iv.brent_root(f, lo, hi, f_lo, f_hi, xtol=XTOL, max_iter=iv.MAX_ITER)
    if not result.converged:
        return _rejected(iv.STATUS_NO_ROOT, lower, upper, evaluations[0], result.residual)
    return iv.IvResult(sigma=result.root, status=iv.STATUS_OK, iterations=evaluations[0], residual=result.residual,
                       lower_bound=lower, upper_bound=upper)


__all__ = [
    "MODEL_AMERICAN",
    "MODEL_EUROPEAN",
    "PRICE_TOL",
    "QUALITY_NEAR_BOUNDARY",
    "QUALITY_OK",
    "RHO_BUMP",
    "SIGMA_CEILING",
    "SIGMA_FLOOR",
    "VEGA_BUMP",
    "XTOL",
    "AmericanValue",
    "implied_vol",
    "price_bounds",
    "tree_floor",
    "value",
]
