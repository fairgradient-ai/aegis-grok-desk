"""Analytic European Greeks in DESK UNITS, with the natural-unit layer kept visible.

Ported from Aegis ``services/quant-engine/src/aegis_quant/greeks.py`` (FairGradient); standard
library only. :mod:`aegis_desk.bsm` returns the natural analytic units; this module is the single
place where they are converted to the units the desk stores in ``book.json`` and compares in the
rulebook, so every consumer reads one convention:

    delta  per $1 of underlying, SIGNED (calls in [0, 1], puts in [-1, 0]); rules compare |delta|
           through :func:`abs_delta`, the stored value keeps its sign
    gamma  per $1^2 (same for calls and puts, >= 0)
    theta  per CALENDAR DAY = natural per-year theta / ``units.DAYS_PER_YEAR``; negative while
           the option decays (the usual case for a long option; a deep in-the-money European put
           can be positive and the model's sign is reported, never forced)
    vega   per VOL POINT (1 % = 0.01 of sigma) = natural vega * ``units.VOL_POINT``
    rho    per 1 % of rate (0.01) = natural rho * ``units.RATE_POINT``, dividend yield q fixed
    iv     echoes the sigma used, which is by definition the implied vol at the priced point

Inputs follow the :mod:`aegis_desk.bsm` conventions (``right`` is ``"CALL"``/``"PUT"``; S, K,
tau in years, r, q, sigma). ``tau <= 0`` gives ``status == "expired"`` with the expiry
convention (delta 0/1 or 0/-1, every other Greek 0.0); input errors raise :class:`ValueError`.

Per-share values. Multiply by the signed quantity and the contract multiplier for position
Greeks (:func:`position_greeks`): a short contract carries the negated per-share Greeks.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import bsm
from .units import CONTRACT_MULTIPLIER, DAYS_PER_YEAR, RATE_POINT, VOL_POINT


@dataclass(frozen=True)
class NaturalGreeks:
    """Greeks in the natural analytic units of :mod:`aegis_desk.bsm` (the oracle-comparison layer).

    ``theta_per_year`` is dV/dt per year, ``vega_per_unit`` is dV/dsigma per 1.00 of sigma,
    ``rho_per_unit`` is dV/dr per 1.00 of rate with q fixed. ``status`` is ``"ok"`` or
    ``"expired"``.
    """

    delta: float
    gamma: float
    theta_per_year: float
    vega_per_unit: float
    rho_per_unit: float
    iv: float
    status: str


@dataclass(frozen=True)
class Greeks:
    """Greeks in desk units (module notes); per share unless scaled by :func:`position_greeks`."""

    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    iv: float
    status: str

    @property
    def expired(self) -> bool:
        """True when the expiry convention was applied (``status == "expired"``)."""
        return self.status == bsm.STATUS_EXPIRED


def natural_greeks(
    right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float
) -> NaturalGreeks:
    """All five analytic Greeks in natural units (one validated pass over the bsm functions)."""
    bsm.is_call(right)
    S, K, tau, r, q, sigma = bsm.validate_market(S, K, tau, r, q, sigma)
    return NaturalGreeks(
        delta=bsm.delta(right, S, K, tau, r, q, sigma),
        gamma=bsm.gamma(right, S, K, tau, r, q, sigma),
        theta_per_year=bsm.theta(right, S, K, tau, r, q, sigma),
        vega_per_unit=bsm.vega(right, S, K, tau, r, q, sigma),
        rho_per_unit=bsm.rho(right, S, K, tau, r, q, sigma),
        iv=sigma,
        status=bsm.expiry_status(tau),
    )


def to_desk_units(natural: NaturalGreeks) -> Greeks:
    """Natural -> desk units: theta / 365, vega * 0.01, rho * 0.01; delta and gamma unchanged."""
    return Greeks(
        delta=natural.delta,
        gamma=natural.gamma,
        theta=natural.theta_per_year / DAYS_PER_YEAR,
        vega=natural.vega_per_unit * VOL_POINT,
        rho=natural.rho_per_unit * RATE_POINT,
        iv=natural.iv,
        status=natural.status,
    )


def european_greeks(
    right: str, S: float, K: float, tau: float, r: float, q: float, sigma: float
) -> Greeks:
    """Full analytic Greeks for a European option in desk units (per share).

    ``tau <= 0`` returns the expiry convention with ``status == "expired"``; bad inputs raise
    :class:`ValueError` (see :mod:`aegis_desk.bsm`).
    """
    return to_desk_units(natural_greeks(right, S, K, tau, r, q, sigma))


def abs_delta(delta: float) -> float:
    """|delta| for threshold comparisons (the rulebook's DELTA watch 0.30 / signal 0.50 use it).

    Deltas are stored signed (puts negative); every comparison against an unsigned threshold goes
    through this function so the sign convention is explicit at the call site.
    """
    return abs(bsm.as_float("delta", delta))


def position_greeks(
    greeks: Greeks, quantity: float, multiplier: int = CONTRACT_MULTIPLIER
) -> Greeks:
    """Scale per-share desk Greeks to a position: each Greek * signed quantity * multiplier.

    ``quantity`` is the signed contract count (negative = short), ``multiplier`` the shares per
    contract (100 for standard equity options). ``iv`` and ``status`` are carried through
    unchanged; theta stays per calendar day, vega per vol point, rho per 1 %.
    """
    scale = bsm.as_float("quantity", quantity) * bsm.as_float("multiplier", multiplier)
    return Greeks(
        delta=greeks.delta * scale,
        gamma=greeks.gamma * scale,
        theta=greeks.theta * scale,
        vega=greeks.vega * scale,
        rho=greeks.rho * scale,
        iv=greeks.iv,
        status=greeks.status,
    )


__all__ = [
    "Greeks",
    "NaturalGreeks",
    "abs_delta",
    "european_greeks",
    "natural_greeks",
    "position_greeks",
    "to_desk_units",
]
