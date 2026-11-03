"""Generalised Black-Scholes-Merton closed form with a continuous dividend yield (stdlib only).

Ported from Aegis ``services/quant-engine/src/aegis_quant/bsm.py`` (FairGradient), whose values
were validated to the ULP against the Thales ``#Validated`` oracle and cross-checked against
QuantLib. The only substitution is ``scipy.special.ndtr`` -> the ``math`` module's complementary
error function (see :func:`norm_cdf`). Pure and synchronous: floats in, floats out; no I/O, no
clock, no global state. The golden literal of record is the call value ``13.494231234329277``
at S=162.06, K=170.163, tau=1.553, r=0.05, q=0.052, sigma=0.226
(``tests/engine/test_golden_price.py``).

Conventions (every public function uses these names, in this order)
---------------------------------------------------------------------
right  the string ``"CALL"`` or ``"PUT"`` (exact, upper case; see :data:`CALL`, :data:`PUT`)
S      spot price of the underlying, > 0
K      strike, > 0
tau    time to expiry in YEARS on a 365-day year (``units.SECONDS_PER_YEAR`` converts seconds);
       ``tau <= 0`` means the contract has expired
r      continuously-compounded risk-free rate, annualised (may be negative)
q      continuous dividend yield, annualised; cost of carry ``b = r - q``
sigma  annualised volatility, > 0 (a quoted 22.6 % is 0.226)

Formulas (``N`` = standard-normal CDF, ``n`` = PDF, ``b = r - q``)::

    d1 = [ln(S/K) + (b + sigma^2/2) tau] / (sigma sqrt(tau));   d2 = d1 - sigma sqrt(tau)
    Call = S e^{-q tau} N(d1) - K e^{-r tau} N(d2)
    Put  = K e^{-r tau} N(-d2) - S e^{-q tau} N(-d1)

Units returned by THIS module are the natural analytic units: delta per $1 of underlying (signed,
puts negative), gamma per $1^2, vega per 1.00 of sigma, theta per YEAR (dV/dt, so negative when
the option decays), rho per 1.00 of rate with ``q`` held fixed as ``r`` moves. The desk units
(theta per calendar day, vega per vol point, rho per 1 %) are produced by :mod:`aegis_desk.greeks`
from these values; the oracle test compares both layers against QuantLib.

Input errors (programming errors, never market states) raise :class:`ValueError` with a message:
``right`` not ``"CALL"``/``"PUT"``; ``S <= 0``; ``K <= 0``; ``sigma <= 0``; any non-finite or
non-numeric argument. Inputs are converted with ``float()`` (``int`` and ``Decimal`` strikes are
accepted; ``bool`` is rejected).

Expiry convention (``tau <= 0``, a market state, reported through ``status == "expired"``)
---------------------------------------------------------------------------------------------
* price  = undiscounted intrinsic ``max(S - K, 0)`` (call) / ``max(K - S, 0)`` (put)
* delta  = 1.0 (call) if ``S > K`` else 0.0;  -1.0 (put) if ``S < K`` else 0.0.  Exactly at the
  money the contract is not auto-exercised (OCC exercises at $0.01 in the money), so delta is 0.
* gamma, vega, theta, rho = 0.0
Callers that have no sigma for an expired leg use :func:`intrinsic` directly.

Very small ``sigma * sqrt(tau)`` (a quote minutes from expiry) is NOT an error: ``N`` saturates
at 0 or 1 and every function converges to its intrinsic-limit value without NaN or overflow.
Equity options are American; this European closed form is the Stage-1 model of record and a
documented approximation for early-exercise situations (Aegis MODEL_NOTE).
"""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from decimal import Decimal

CALL: str = "CALL"
"""The ``right`` string for a call option."""

PUT: str = "PUT"
"""The ``right`` string for a put option."""

RIGHTS: tuple[str, str] = (CALL, PUT)
"""The only accepted ``right`` values (exact match)."""

STATUS_OK: str = "ok"
"""Result status when ``tau > 0`` and the closed form applies."""

STATUS_EXPIRED: str = "expired"
"""Result status when ``tau <= 0``: intrinsic value and the expiry Greek convention are returned."""

_SQRT2: float = math.sqrt(2.0)
_SQRT_2PI: float = math.sqrt(2.0 * math.pi)


# --------------------------------------------------------------------------------------------
# Distribution helpers
# --------------------------------------------------------------------------------------------


def norm_cdf(x: float) -> float:
    """Standard-normal CDF ``N(x) = 0.5 * (1 + erf(x / sqrt 2))``.

    Evaluated as the algebraically identical ``0.5 * erfc(-x / sqrt 2)``. The two forms agree
    to rounding where the mass is (|x| of a few units, the whole golden set), but the ``1 + erf``
    subtraction cancels catastrophically in the lower tail: ``erf(x / sqrt 2)`` rounds to -1 once
    x < -8.3 and the ``erf`` form returns exactly 0.0, while ``erfc`` keeps full relative
    precision down to the underflow limit (N(-30) = 4.9e-198). Prices of far out-of-the-money
    options and the IV solver's bracket evaluations depend on that tail. QuantLib's
    ``CumulativeNormalDistribution`` uses the same ``erfc``-class accuracy via an asymptotic
    expansion below 1e-8, which is why the oracle test can hold a relative tolerance.
    """
    return 0.5 * math.erfc(-x / _SQRT2)


def norm_pdf(x: float) -> float:
    """Standard-normal density ``n(x) = exp(-x^2 / 2) / sqrt(2 pi)``; underflows cleanly to 0.0."""
    return math.exp(-0.5 * x * x) / _SQRT_2PI


# --------------------------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------------------------


def as_float(name: str, value: object) -> float:
    """Coerce a numeric argument to ``float``; reject bools, non-numbers, NaN and infinities.

    ``name`` is only used in the :class:`ValueError` message. ``int``, ``float`` and ``Decimal``
    are accepted (money arrives as ``Decimal`` elsewhere on the desk; the engine works in float).
    """
    if isinstance(value, bool) or not isinstance(value, (numbers.Real, Decimal)):
        raise ValueError(
            "%s must be a real number (int, float or Decimal), got %s"
            % (name, type(value).__name__)
        )
    out = float(value)
    if not math.isfinite(out):
        raise ValueError("%s must be finite, got %r" % (name, value))
    return out


def validate_spot_strike(S: float, K: float) -> tuple[float, float]:
    """Validate ``S > 0`` and ``K > 0`` and return them as floats (shared by every entry point)."""
    S_f = as_float("S", S)
    K_f = as_float("K", K)
    if S_f <= 0.0:
        raise ValueError("S (spot) must be > 0, got %r" % (S,))
    if K_f <= 0.0:
        raise ValueError("K (strike) must be > 0, got %r" % (K,))
    return S_f, K_f


def is_call(right: str) -> bool:
    """Return True for ``"CALL"``, False for ``"PUT"``; any other value is a :class:`ValueError`."""
    if right == CALL:
        return True
    if right == PUT:
        return False
    raise ValueError('right must be the string "CALL" or "PUT", got %r' % (right,))


def validate_market(
    S: float, K: float, tau: float, r: float, q: float, sigma: float
) -> tuple[float, float, float, float, float, float]:
    """Validate and coerce the market inputs; return them as floats in the same order.

    Raises :class:`ValueError` for ``S <= 0``, ``K <= 0``, ``sigma <= 0`` or any non-finite or
    non-numeric value. ``tau`` may be zero or negative (expired); ``r`` and ``q`` may be negative.
    """
    S_f, K_f = validate_spot_strike(S, K)
    tau_f = as_float("tau", tau)
    r_f = as_float("r", r)
    q_f = as_float("q", q)
    sigma_f = as_float("sigma", sigma)
    if sigma_f <= 0.0:
        raise ValueError("sigma (annualised volatility) must be > 0, got %r" % (sigma,))
    return S_f, K_f, tau_f, r_f, q_f, sigma_f


def expiry_status(tau: float) -> str:
    """``"ok"`` when ``tau > 0``, ``"expired"`` otherwise (the status flag every result carries)."""
    return STATUS_OK if tau > 0.0 else STATUS_EXPIRED


# --------------------------------------------------------------------------------------------
# Core terms
# --------------------------------------------------------------------------------------------


def d1_d2(S: float, K: float, tau: float, r: float, q: float, sigma: float) -> tuple[float, float]:
    """Return ``(d1, d2)`` with cost of carry ``b = r - q``; requires ``tau > 0``.

    ``d1 = [ln(S/K) + (b + sigma^2/2) tau] / (sigma sqrt tau)``, ``d2 = d1 - sigma sqrt tau``.
    Raises :class:`ValueError` when ``tau <= 0`` (the terms are undefined at expiry).
    """
    S, K, tau, r, q, sigma = validate_market(S, K, tau, r, q, sigma)
    if tau <= 0.0:
        raise ValueError("d1/d2 are undefined at or after expiry (tau=%r <= 0)" % (tau,))
    b = r - q
    vol_t = sigma * math.sqrt(tau)
    d1 = (math.log(S / K) + (b + 0.5 * sigma * sigma) * tau) / vol_t
    return d1, d1 - vol_t


def intrinsic(right: str, S: float, K: float) -> float:
    """Undiscounted intrinsic value per share: ``max(S-K, 0)`` (call) or ``max(K-S, 0)`` (put)."""
    call = is_call(right)
    S_f, K_f = validate_spot_strike(S, K)
    return max(S_f - K_f, 0.0) if call else max(K_f - S_f, 0.0)


def _expired_delta(call: bool, S: float, K: float) -> float:
    """Expiry delta convention: 1/-1 strictly in the money, else 0 (also exactly at the money)."""
    if call:
        return 1.0 if S > K else 0.0
    return -1.0 if S < K else 0.0


# --------------------------------------------------------------------------------------------
# Price
# --------------------------------------------------------------------------------------------


def price(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float) -> float:
    """European option value today, per share (intrinsic when ``tau <= 0``)."""
    call = is_call(right)
    S, K, tau, r, q, sigma = validate_market(S, K, tau, r, q, sigma)
    if tau <= 0.0:
        return max(S - K, 0.0) if call else max(K - S, 0.0)
    d1, d2 = d1_d2(S, K, tau, r, q, sigma)
    carry = math.exp(-q * tau)  # e^{(b-r)T} = e^{-qT}
    disc = math.exp(-r * tau)
    if call:
        return S * carry * norm_cdf(d1) - K * disc * norm_cdf(d2)
    return K * disc * norm_cdf(-d2) - S * carry * norm_cdf(-d1)


@dataclass(frozen=True)
class PriceResult:
    """A priced European option with its decomposition and status flag.

    ``value`` is per share; ``intrinsic`` is undiscounted ``max(S-K,0)``/``max(K-S,0)``;
    ``time_value = value - intrinsic`` (0.0 when expired; may be NEGATIVE for a deep in-the-money
    European option because the strike is received or paid only at expiry). ``d1``/``d2`` are
    ``None`` when expired. ``status`` is ``"ok"`` or ``"expired"``.
    """

    value: float
    intrinsic: float
    time_value: float
    status: str
    d1: float | None
    d2: float | None


def price_result(
    right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float
) -> PriceResult:
    """Price with the explicit status flag and decomposition (see :class:`PriceResult`)."""
    call = is_call(right)
    S, K, tau, r, q, sigma = validate_market(S, K, tau, r, q, sigma)
    intr = max(S - K, 0.0) if call else max(K - S, 0.0)
    if tau <= 0.0:
        return PriceResult(
            value=intr, intrinsic=intr, time_value=0.0, status=STATUS_EXPIRED, d1=None, d2=None
        )
    d1, d2 = d1_d2(S, K, tau, r, q, sigma)
    value = price(right, S, K, tau, r, q, sigma)
    return PriceResult(
        value=value, intrinsic=intr, time_value=value - intr, status=STATUS_OK, d1=d1, d2=d2
    )


# --------------------------------------------------------------------------------------------
# Greeks in natural analytic units
# --------------------------------------------------------------------------------------------


def delta(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float) -> float:
    """dV/dS per $1 of underlying, signed: call in [0, e^{-q tau}], put in [-e^{-q tau}, 0].

    At expiry returns the convention 1.0 / 0.0 (call) or -1.0 / 0.0 (put), see the module notes.
    """
    call = is_call(right)
    S, K, tau, r, q, sigma = validate_market(S, K, tau, r, q, sigma)
    if tau <= 0.0:
        return _expired_delta(call, S, K)
    d1, _ = d1_d2(S, K, tau, r, q, sigma)
    carry = math.exp(-q * tau)
    return carry * norm_cdf(d1) if call else carry * (norm_cdf(d1) - 1.0)


def gamma(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float) -> float:
    """d2V/dS2 per $1^2, identical for calls and puts, >= 0; 0.0 at expiry."""
    is_call(right)
    S, K, tau, r, q, sigma = validate_market(S, K, tau, r, q, sigma)
    if tau <= 0.0:
        return 0.0
    d1, _ = d1_d2(S, K, tau, r, q, sigma)
    carry = math.exp(-q * tau)
    return carry * norm_pdf(d1) / (S * sigma * math.sqrt(tau))


def vega(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float) -> float:
    """dV/dsigma per 1.00 of sigma (identical for calls and puts, >= 0); 0.0 at expiry.

    Multiply by ``units.VOL_POINT`` (0.01) for the desk's per-vol-point vega.
    """
    is_call(right)
    S, K, tau, r, q, sigma = validate_market(S, K, tau, r, q, sigma)
    if tau <= 0.0:
        return 0.0
    d1, _ = d1_d2(S, K, tau, r, q, sigma)
    carry = math.exp(-q * tau)
    return S * carry * norm_pdf(d1) * math.sqrt(tau)


def theta(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float) -> float:
    """dV/dt per YEAR (negative = time decay); 0.0 at expiry.

    Divide by ``units.DAYS_PER_YEAR`` (365) for the desk's per-calendar-day theta. Usually
    negative for a long option; a deep in-the-money European put (or a call with a large dividend
    yield) can carry a positive theta, and the sign is reported as the model gives it.
    """
    call = is_call(right)
    S, K, tau, r, q, sigma = validate_market(S, K, tau, r, q, sigma)
    if tau <= 0.0:
        return 0.0
    d1, d2 = d1_d2(S, K, tau, r, q, sigma)
    b = r - q
    carry = math.exp(-q * tau)
    disc = math.exp(-r * tau)
    bleed = -S * carry * norm_pdf(d1) * sigma / (2.0 * math.sqrt(tau))
    if call:
        return bleed - (b - r) * S * carry * norm_cdf(d1) - r * K * disc * norm_cdf(d2)
    return bleed + (b - r) * S * carry * norm_cdf(-d1) + r * K * disc * norm_cdf(-d2)


def rho(right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float) -> float:
    """dV/dr per 1.00 of rate, holding the dividend yield ``q`` fixed as ``r`` moves; 0.0 at expiry.

    Because ``S e^{(b-r)tau} = S e^{-q tau}`` does not depend on ``r`` under the q-fixed
    convention, the analytic rho reduces to the no-dividend form ``K tau e^{-r tau} N(d2)`` for a
    call and ``-K tau e^{-r tau} N(-d2)`` for a put. Multiply by ``units.RATE_POINT`` (0.01) for
    the desk's per-1 % rho. (A finite difference in ``r`` with ``q`` passed separately reproduces
    this convention; QuantLib's ``rho`` of a ``BlackScholesMertonProcess`` uses the same one.)
    """
    call = is_call(right)
    S, K, tau, r, q, sigma = validate_market(S, K, tau, r, q, sigma)
    if tau <= 0.0:
        return 0.0
    _, d2 = d1_d2(S, K, tau, r, q, sigma)
    disc = math.exp(-r * tau)
    if call:
        return K * tau * disc * norm_cdf(d2)
    return -K * tau * disc * norm_cdf(-d2)


__all__ = [
    "CALL",
    "PUT",
    "RIGHTS",
    "STATUS_EXPIRED",
    "STATUS_OK",
    "PriceResult",
    "as_float",
    "d1_d2",
    "delta",
    "expiry_status",
    "gamma",
    "intrinsic",
    "is_call",
    "norm_cdf",
    "norm_pdf",
    "price",
    "price_result",
    "rho",
    "theta",
    "validate_market",
    "validate_spot_strike",
    "vega",
]
