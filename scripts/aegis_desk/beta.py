"""Beta against SPY from daily closes (roadmap 2.7, the owner's answer to open question 4).

The fit (:func:`fit`)
---------------------
Ordinary least squares of a symbol's daily simple returns on SPY's, over the closes both series hold on the same dates,
the last :data:`WINDOW_CLOSES` of them (252 closes, 251 returns, ending at the previous trading day). The bars are
split-adjusted (``adjustment=split`` on the desk's own read path), so a split is no return.

UNAVAILABLE (``r2_flag = "unavailable"``, ``beta = None``) when fewer than :data:`MIN_RETURNS` returns pair up, when
SPY's returns do not vary, or when any daily return in either series lies beyond :data:`OUTLIER_LIMIT` (40 %): a move
like that is an unadjusted event or bad data, not a market move to fit. Otherwise ``r2_flag`` is ``"ok"`` at
``r2 >= R2_OK`` (0.30) and ``"low"`` below it, printed with the figure. SPY's own beta is 1 by definition.

The beta file (:func:`document`, :func:`load_betas`)
---------------------------------------------------
``data/beta/<ET date>.json`` holds one day's fits: ``{"schema": 1, "et_date", "bars_end", "computed_utc", "betas":
{SYMBOL: fit}}``. ``beta_refresh.py`` is its only writer, once a day (closed bars are final); the book reads the file
of its own ET date and fills ``beta_weighted_delta`` from it, and without it every beta is UNAVAILABLE.

:func:`fit` is pure: bars in, a record out; :func:`load_betas` reads one file.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import state

BENCHMARK: str = "SPY"
WINDOW_CLOSES: int = 252
MIN_RETURNS: int = 126
OUTLIER_LIMIT: float = 0.40
R2_OK: float = 0.30
FLAG_OK: str = "ok"
FLAG_LOW: str = "low"
FLAG_UNAVAILABLE: str = "unavailable"
REASON_SHORT: str = "fewer than %d paired daily returns" % MIN_RETURNS
REASON_OUTLIER: str = "a daily return beyond 40 % (an unadjusted event or bad data)"
REASON_FLAT: str = "the benchmark's returns do not vary"
REASON_NO_BARS: str = "no daily bars"


@dataclass(frozen=True)
class BetaFit:
    """One symbol's fit (module notes)."""

    symbol: str
    beta: Optional[float]
    r2: Optional[float]
    r2_flag: str
    returns: int
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "beta": self.beta, "r2": self.r2, "r2_flag": self.r2_flag,
                "returns": self.returns, "reason": self.reason}


def _closes(bars: Sequence[Tuple[Any, float]]) -> Dict[Any, float]:
    return {day: float(close) for day, close in bars if close is not None and float(close) > 0}


def fit(symbol: str, bars: Sequence[Tuple[Any, float]], benchmark: Sequence[Tuple[Any, float]]) -> BetaFit:
    """The beta of ``symbol`` from its ``(date, close)`` bars against the benchmark's (module notes)."""
    symbol = symbol.upper()
    if symbol == BENCHMARK:
        return BetaFit(symbol, 1.0, 1.0, FLAG_OK, 0, None)
    own, ref = _closes(bars), _closes(benchmark)
    days = sorted(set(own) & set(ref))[-WINDOW_CLOSES:]
    if len(days) < 2:
        return BetaFit(symbol, None, None, FLAG_UNAVAILABLE, 0, REASON_NO_BARS if not own else REASON_SHORT)
    xs: List[float] = []
    ys: List[float] = []
    for before, after in zip(days, days[1:]):
        xs.append(ref[after] / ref[before] - 1.0)
        ys.append(own[after] / own[before] - 1.0)
    count = len(xs)
    if any(abs(value) > OUTLIER_LIMIT for value in xs + ys):
        return BetaFit(symbol, None, None, FLAG_UNAVAILABLE, count, REASON_OUTLIER)
    if count < MIN_RETURNS:
        return BetaFit(symbol, None, None, FLAG_UNAVAILABLE, count, REASON_SHORT)
    mean_x, mean_y = sum(xs) / count, sum(ys) / count
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx <= 0.0:
        return BetaFit(symbol, None, None, FLAG_UNAVAILABLE, count, REASON_FLAT)
    slope = sxy / sxx
    r2 = 0.0 if syy <= 0.0 else (sxy * sxy) / (sxx * syy)
    return BetaFit(symbol, slope, r2, FLAG_OK if r2 >= R2_OK else FLAG_LOW, count, None)


SCHEMA: int = 1


def beta_path(beta_dir: str, day: datetime.date) -> str:
    """``data/beta/<ET date>.json``."""
    return os.path.join(beta_dir, "%s.json" % day.isoformat())


def document(day: datetime.date, bars_end: datetime.date, computed_utc: datetime.datetime,
             fits: Sequence[BetaFit]) -> Dict[str, Any]:
    """The beta file's body (module notes)."""
    return {"schema": SCHEMA, "et_date": day.isoformat(), "bars_end": bars_end.isoformat(),
            "computed_utc": state.format_utc(computed_utc), "betas": {item.symbol: item.as_dict() for item in fits}}


def _from_record(symbol: str, record: Mapping[str, Any]) -> Optional[BetaFit]:
    flag = record.get("r2_flag")
    value, r2, count = record.get("beta"), record.get("r2"), record.get("returns")
    if flag not in (FLAG_OK, FLAG_LOW, FLAG_UNAVAILABLE) or not isinstance(count, int) or isinstance(count, bool):
        return None
    if flag == FLAG_UNAVAILABLE:
        return BetaFit(symbol, None, None, flag, count, record.get("reason") if isinstance(record.get("reason"), str)
                       else None)
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (value, r2)):
        return None
    return BetaFit(symbol, float(value), float(r2), flag, count, None)


def load_betas(beta_dir: str, day: datetime.date) -> Optional[Dict[str, BetaFit]]:
    """The fits of the beta file dated ``day``, or ``None`` when there is none or it cannot be read (every beta is then
    UNAVAILABLE); a malformed entry is left out (its symbol's beta is then UNAVAILABLE)."""
    body, status = state.read_json(beta_path(beta_dir, day))
    if status != state.STATUS_OK or not isinstance(body, dict) or body.get("schema") != SCHEMA:
        return None
    if body.get("et_date") != day.isoformat() or not isinstance(body.get("betas"), dict):
        return None
    fits: Dict[str, BetaFit] = {}
    for symbol, record in body["betas"].items():
        if isinstance(symbol, str) and isinstance(record, dict):
            found = _from_record(symbol.upper(), record)
            if found is not None:
                fits[symbol.upper()] = found
    return fits


__all__ = [
    "BENCHMARK",
    "BetaFit",
    "FLAG_LOW",
    "FLAG_OK",
    "FLAG_UNAVAILABLE",
    "MIN_RETURNS",
    "OUTLIER_LIMIT",
    "R2_OK",
    "WINDOW_CLOSES",
    "SCHEMA",
    "beta_path",
    "document",
    "fit",
    "load_betas",
]
