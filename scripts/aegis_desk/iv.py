"""Implied volatility (price -> sigma) by bracketed Brent root-finding under generalised BSM.

Ported from Aegis ``services/quant-engine/src/aegis_quant/iv.py`` (FairGradient), standard library
only: ``scipy.optimize.brentq`` is replaced by :func:`brent_root` (the van Wijngaarden-Dekker-Brent
algorithm of Brent 1973, in the ``zbrent`` form of Numerical Recipes) and the Newton pre-step is
dropped. The model price is strictly increasing in sigma (vega > 0 for tau > 0), so one bracket
holds exactly one root and Brent converges superlinearly in 10-30 evaluations; a single
deterministic path is easier to defend than two.

Contract
--------
``implied_vol(price, right, S, K, tau, r, q) -> IvResult`` never raises for a bad QUOTE: an
economically impossible or unsolvable price comes back as a status, never as a number.

    status              meaning                                                   sigma
    ``"ok"``            converged; ``residual`` = model(sigma) - price (in $)      float
    ``"expired"``       tau <= 0, no volatility is implied                        None
    ``"below_intrinsic"`` price < discounted intrinsic lower bound (arbitrage)    None
    ``"above_bound"``   price > S e^{-q tau} (call) / K e^{-r tau} (put)          None
    ``"no_root"``       price sits exactly on a bound (IV limit 0 or infinity), or
                        no sigma in [1e-6, 10.0] reproduces it, or Brent did not
                        converge in ``MAX_ITER`` steps (not reachable in practice)  None

Order of checks: expiry, then the no-arbitrage bounds (lower = max(0, S e^{-q tau} - K e^{-r tau})
for a call and max(0, K e^{-r tau} - S e^{-q tau}) for a put; upper = S e^{-q tau} for a call and
K e^{-r tau} for a put, each with ``BOUND_SLACK`` = 1e-12 of absolute slack), then the bracket
[``BRACKET_LO`` = 1e-4, ``BRACKET_HI`` = 5.0] (0.01 % to 500 % vol). If the quote lies below the
model price at 1e-4 the low end is expanded ONCE to 1e-6; if it lies above the model price at 5.0
the high end is expanded ONCE to 10.0. Tolerance ``XTOL`` = 1e-10 on sigma, ``MAX_ITER`` = 200.

Input errors (programming errors, never quotes) raise :class:`ValueError` exactly as
:mod:`aegis_desk.bsm` does: ``right`` not ``"CALL"``/``"PUT"``, ``S <= 0``, ``K <= 0``, or any
non-finite or non-numeric ``price``, ``S``, ``K``, ``tau``, ``r``, ``q``.

The European closed form is the Stage-1 model of record; a deep in-the-money American quote at
undiscounted intrinsic can legitimately come back ``"below_intrinsic"`` under it (documented
limitation, Aegis MODEL_NOTE). Pure function API only; no vectorisation.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from dataclasses import dataclass

from . import bsm

STATUS_OK: str = "ok"
STATUS_EXPIRED: str = "expired"
STATUS_BELOW_INTRINSIC: str = "below_intrinsic"
STATUS_ABOVE_BOUND: str = "above_bound"
STATUS_NO_ROOT: str = "no_root"
STATUSES: tuple[str, ...] = (
    STATUS_OK,
    STATUS_EXPIRED,
    STATUS_BELOW_INTRINSIC,
    STATUS_ABOVE_BOUND,
    STATUS_NO_ROOT,
)
"""Every status :func:`implied_vol` can return."""

BRACKET_LO: float = 1e-4
"""Initial lower bracket on sigma (0.01 % vol)."""

BRACKET_HI: float = 5.0
"""Initial upper bracket on sigma (500 % vol)."""

BRACKET_LO_EXPANDED: float = 1e-6
"""Lower bracket after the single downward expansion."""

BRACKET_HI_EXPANDED: float = 10.0
"""Upper bracket after the single upward expansion (1,000 % vol)."""

XTOL: float = 1e-10
"""Absolute tolerance on sigma at which Brent stops."""

MAX_ITER: int = 200
"""Iteration cap for Brent (bisection alone would need ~37 steps on the widest bracket)."""

BOUND_SLACK: float = 1e-12
"""Absolute slack applied to the no-arbitrage bounds before a quote is rejected."""

_EPS: float = sys.float_info.epsilon


@dataclass(frozen=True)
class RootResult:
    """Outcome of :func:`brent_root`: the estimate, iterations used, convergence flag, f(root)."""

    root: float
    iterations: int
    converged: bool
    residual: float


@dataclass(frozen=True)
class IvResult:
    """Result of :func:`implied_vol`; ``sigma`` is ``None`` unless ``status == "ok"``.

    ``iterations`` counts Brent iterations (0 when the solver did not run); ``residual`` is the
    signed price error model(sigma) - price in $ per share at the returned sigma (``None`` when
    there is no sigma); ``lower_bound``/``upper_bound`` are the no-arbitrage bounds the quote was
    checked against (both equal to the intrinsic value when expired).
    """

    sigma: float | None
    status: str
    iterations: int
    residual: float | None
    lower_bound: float
    upper_bound: float

    @property
    def ok(self) -> bool:
        """True when a volatility was found (``status == "ok"``)."""
        return self.status == STATUS_OK


def brent_root(
    f: Callable[[float], float],
    a: float,
    b: float,
    fa: float | None = None,
    fb: float | None = None,
    xtol: float = XTOL,
    max_iter: int = MAX_ITER,
) -> RootResult:
    """Find a root of ``f`` on the bracket ``[a, b]`` by Brent's method.

    ``f(a)`` and ``f(b)`` must have opposite signs (or one of them be exactly zero); pass them as
    ``fa``/``fb`` when already evaluated. Each iteration tries inverse quadratic interpolation
    (secant when only two distinct points are available) and falls back to bisection whenever the
    interpolated step is not at least halving the bracket, so the bracket always shrinks and
    convergence is guaranteed. Stops when the half-bracket is within
    ``2 * eps * |b| + xtol / 2`` (so the root is within ``xtol`` of the returned estimate) or when
    ``f`` is exactly zero; ``converged`` is False only if ``max_iter`` is exhausted, in which case
    the best estimate so far is still returned. Raises :class:`ValueError` when the root is not
    bracketed (a programming error).
    """
    if fa is None:
        fa = f(a)
    if fb is None:
        fb = f(b)
    if fa == 0.0:
        return RootResult(root=a, iterations=0, converged=True, residual=0.0)
    if fb == 0.0:
        return RootResult(root=b, iterations=0, converged=True, residual=0.0)
    if (fa > 0.0) == (fb > 0.0):
        raise ValueError(
            "root is not bracketed: f(%r)=%r and f(%r)=%r have the same sign" % (a, fa, b, fb)
        )
    c, fc = b, fb
    d = e = 0.0
    for iteration in range(1, max_iter + 1):
        if (fb > 0.0) == (fc > 0.0):
            # b and c are on the same side: rename a as c so that b and c bracket the root.
            c, fc = a, fa
            d = e = b - a
        if abs(fc) < abs(fb):
            # Keep b as the best estimate (smallest |f|).
            a, b, c = b, c, b
            fa, fb, fc = fb, fc, fb
        tol1 = 2.0 * _EPS * abs(b) + 0.5 * xtol
        xm = 0.5 * (c - b)
        if abs(xm) <= tol1 or fb == 0.0:
            return RootResult(root=b, iterations=iteration, converged=True, residual=fb)
        if abs(e) >= tol1 and abs(fa) > abs(fb):
            s = fb / fa
            if a == c:
                # Secant step.
                p = 2.0 * xm * s
                qq = 1.0 - s
            else:
                # Inverse quadratic interpolation.
                qq = fa / fc
                rr = fb / fc
                p = s * (2.0 * xm * qq * (qq - rr) - (b - a) * (rr - 1.0))
                qq = (qq - 1.0) * (rr - 1.0) * (s - 1.0)
            if p > 0.0:
                qq = -qq
            p = abs(p)
            min1 = 3.0 * xm * qq - abs(tol1 * qq)
            min2 = abs(e * qq)
            if 2.0 * p < min(min1, min2):
                # Accept the interpolation.
                e = d
                d = p / qq
            else:
                # Interpolation would not shrink the bracket enough: bisect.
                d = xm
                e = d
        else:
            d = xm
            e = d
        a, fa = b, fb
        if abs(d) > tol1:
            b += d
        else:
            b += math.copysign(tol1, xm)
        fb = f(b)
    return RootResult(root=b, iterations=max_iter, converged=False, residual=fb)


def price_bounds(
    right: str, S: float, K: float, tau: float, r: float, q: float
) -> tuple[float, float]:
    """No-arbitrage ``(lower, upper)`` bounds on a European option price per share.

    Call: ``[max(0, S e^{-q tau} - K e^{-r tau}), S e^{-q tau}]``;
    put:  ``[max(0, K e^{-r tau} - S e^{-q tau}), K e^{-r tau}]``.
    When ``tau <= 0`` both bounds equal the undiscounted intrinsic value.
    """
    call = bsm.is_call(right)
    S, K = bsm.validate_spot_strike(S, K)
    tau = bsm.as_float("tau", tau)
    r = bsm.as_float("r", r)
    q = bsm.as_float("q", q)
    if tau <= 0.0:
        intr = max(S - K, 0.0) if call else max(K - S, 0.0)
        return intr, intr
    fwd_spot = S * math.exp(-q * tau)  # S e^{-qT}
    k_disc = K * math.exp(-r * tau)  # K e^{-rT}
    if call:
        return max(0.0, fwd_spot - k_disc), fwd_spot
    return max(0.0, k_disc - fwd_spot), k_disc


def _rejected(status: str, lower: float, upper: float) -> IvResult:
    return IvResult(
        sigma=None,
        status=status,
        iterations=0,
        residual=None,
        lower_bound=lower,
        upper_bound=upper,
    )


def implied_vol(
    price: float, right: str, S: float, K: float, tau: float, r: float, q: float
) -> IvResult:
    """Black-Scholes-Merton implied volatility of an observed ``price`` (per share).

    See the module notes for the status table, the bound checks, the bracket and its single
    expansion, and the tolerance. ``price`` is the quote to invert (a mid, bid or ask in $ per
    share); ``right``, ``S``, ``K``, ``tau``, ``r``, ``q`` follow :mod:`aegis_desk.bsm`.
    """
    bsm.is_call(right)
    target = bsm.as_float("price", price)
    S, K = bsm.validate_spot_strike(S, K)
    tau = bsm.as_float("tau", tau)
    r = bsm.as_float("r", r)
    q = bsm.as_float("q", q)
    lower, upper = price_bounds(right, S, K, tau, r, q)
    if tau <= 0.0:
        return _rejected(STATUS_EXPIRED, lower, upper)
    if target < lower - BOUND_SLACK:
        return _rejected(STATUS_BELOW_INTRINSIC, lower, upper)
    if target > upper + BOUND_SLACK:
        return _rejected(STATUS_ABOVE_BOUND, lower, upper)
    if target <= lower + BOUND_SLACK or target >= upper - BOUND_SLACK:
        # On a bound the implied vol is a limit (0 at the floor, infinity at the cap), not a root.
        return _rejected(STATUS_NO_ROOT, lower, upper)

    def f(sigma: float) -> float:
        return bsm.price(right, S, K, tau, r, q, sigma) - target

    lo, hi = BRACKET_LO, BRACKET_HI
    f_lo, f_hi = f(lo), f(hi)
    if f_lo > 0.0:
        # The quote is below the model price at 0.01 % vol: expand downward once.
        lo = BRACKET_LO_EXPANDED
        f_lo = f(lo)
    if f_hi < 0.0:
        # The quote is above the model price at 500 % vol: expand upward once.
        hi = BRACKET_HI_EXPANDED
        f_hi = f(hi)
    if f_lo > 0.0 or f_hi < 0.0:
        return _rejected(STATUS_NO_ROOT, lower, upper)
    result = brent_root(f, lo, hi, f_lo, f_hi, xtol=XTOL, max_iter=MAX_ITER)
    if not result.converged:
        return IvResult(
            sigma=None,
            status=STATUS_NO_ROOT,
            iterations=result.iterations,
            residual=result.residual,
            lower_bound=lower,
            upper_bound=upper,
        )
    return IvResult(
        sigma=result.root,
        status=STATUS_OK,
        iterations=result.iterations,
        residual=result.residual,
        lower_bound=lower,
        upper_bound=upper,
    )


__all__ = [
    "BOUND_SLACK",
    "BRACKET_HI",
    "BRACKET_HI_EXPANDED",
    "BRACKET_LO",
    "BRACKET_LO_EXPANDED",
    "MAX_ITER",
    "STATUSES",
    "STATUS_ABOVE_BOUND",
    "STATUS_BELOW_INTRINSIC",
    "STATUS_EXPIRED",
    "STATUS_NO_ROOT",
    "STATUS_OK",
    "XTOL",
    "IvResult",
    "RootResult",
    "brent_root",
    "implied_vol",
    "price_bounds",
]
