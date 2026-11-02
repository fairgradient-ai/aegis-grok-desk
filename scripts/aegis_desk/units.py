"""Unit conventions for the pricing engine and the Greeks the desk reports.

Ported from Aegis ``services/quant-engine/src/aegis_quant/units.py`` (FairGradient); standard
library only. Every consumer of a Greek reads the same convention, so the constants live here and
nowhere else.

Natural (analytic) units, as returned by :mod:`aegis_desk.bsm`::

    delta  per $1 of underlying      gamma  per $1^2
    theta  per YEAR                  vega   per 1.00 of sigma (100 vol points)
    rho    per 1.00 of rate (10,000 bp), dividend yield q held fixed

Desk units, as returned by :func:`aegis_desk.greeks.european_greeks` and stored in ``book.json``::

    delta  per $1 of underlying, SIGNED (puts negative)
    gamma  per $1^2
    theta  per CALENDAR DAY  (= per-year theta / DAYS_PER_YEAR)
    vega   per VOL POINT     (1 % = 0.01 of sigma; = per-unit vega * VOL_POINT)
    rho    per 1 % of rate   (0.01; = per-unit rho * RATE_POINT)

Time: ``tau`` is always in years on a 365-day calendar year. The book converts a quote's
seconds-to-cutoff with ``seconds / SECONDS_PER_YEAR`` (plan §8: tau = seconds from the quote's
as-of to the exercise cutoff divided by 365 x 86 400, no floor).
"""

from __future__ import annotations

DAYS_PER_YEAR: float = 365.0
"""Calendar-day basis for theta (per-year theta / 365) and for tau (calendar days / 365)."""

SECONDS_PER_YEAR: int = 365 * 86_400
"""Seconds in the 365-day year used for tau: 31,536,000. tau_years = seconds / SECONDS_PER_YEAR."""

VOL_POINT: float = 0.01
"""One volatility point = 1 % = 0.01 of sigma. Desk vega = natural vega * VOL_POINT."""

RATE_POINT: float = 0.01
"""One rate point = 1 % = 0.01 of the continuous rate. Desk rho = natural rho * RATE_POINT."""

CONTRACT_MULTIPLIER: int = 100
"""Shares per standard listed equity option contract (per-share Greeks * this = contract Greeks)."""

__all__ = [
    "CONTRACT_MULTIPLIER",
    "DAYS_PER_YEAR",
    "RATE_POINT",
    "SECONDS_PER_YEAR",
    "VOL_POINT",
]
