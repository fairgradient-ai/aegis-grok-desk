"""Volatility context: the desk's own implied volatility per symbol against its recorded history (roadmap step 2.9).

The record is the desk's own end-of-day book snapshots (``data/book/<ET date>.json``, ``book.write_snapshot``): for a
symbol, a day's value is the median implied volatility of that day's open option legs on it whose ``iv_status`` is
``ok`` and whose ``model`` is the model of record (:data:`MODEL`, the American lattice of roadmap 2.3; each solved
from the quotes' mids; the vendor's Greeks are never read). A leg without ``model`` was priced before the American
model of record (``european_bsm``) and is left out, so a European history never ranks an American volatility: the
context is UNAVAILABLE until enough American days are recorded. The context of a
symbol places the latest snapshot's value among the earlier recorded days: how many were lower, the range, the number
of days. Fewer than :data:`MIN_HISTORY_DAYS` earlier days, or no value on the latest day, is UNAVAILABLE.

Information only: no verdict reads this module. Nothing here writes a file or asks for market data.
"""

from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import american, state
from .desk_paths import DeskPaths

MODEL: str = american.MODEL_AMERICAN
"""The ``model`` a leg must carry to count (a leg without one counts as :data:`aegis_desk.american.MODEL_EUROPEAN`)."""
MIN_HISTORY_DAYS: int = 5
"""Earlier recorded days a context needs; fewer is UNAVAILABLE ``short_history``."""
STATUS_OK: str = "ok"
STATUS_UNAVAILABLE: str = "unavailable"
REASON_NO_SNAPSHOTS: str = "no_snapshots"
REASON_NO_VALUE: str = "no_value"
REASON_SHORT_HISTORY: str = "short_history"
REASON_WORDS: Dict[str, str] = {
    REASON_NO_SNAPSHOTS: "the desk has written no end-of-day book snapshot yet",
    REASON_NO_VALUE: "the latest snapshot holds no option leg on it with a solved implied volatility",
    REASON_SHORT_HISTORY: "fewer than %d earlier recorded days hold a value on the American model" % MIN_HISTORY_DAYS,
}
SNAPSHOT_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def symbol_ivs(document: Mapping[str, Any]) -> Dict[str, float]:
    """``{underlying: median implied volatility}`` of one book snapshot's open option legs whose ``iv_status`` is
    ``ok`` and whose ``model`` is :data:`MODEL` (a leg without a finite positive ``iv`` is skipped)."""
    values: Dict[str, List[float]] = {}
    for leg in document.get("legs") or []:
        if not isinstance(leg, Mapping) or leg.get("instrument_type") != "option" or leg.get("iv_status") != "ok":
            continue
        if (leg.get("model") or american.MODEL_EUROPEAN) != MODEL:
            continue
        iv, underlying = leg.get("iv"), leg.get("underlying")
        if isinstance(iv, bool) or not isinstance(iv, (int, float)) or not iv > 0 or not isinstance(underlying, str):
            continue
        values.setdefault(underlying, []).append(float(iv))
    return {symbol: _median(ivs) for symbol, ivs in sorted(values.items())}


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return float(ordered[middle]) if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


@dataclass(frozen=True)
class History:
    """The recorded days in date order (``(ET date, {symbol: iv})``) and the snapshot files that could not be read."""

    days: Tuple[Tuple[datetime.date, Dict[str, float]], ...]
    skipped: Tuple[str, ...] = ()

    @property
    def latest(self) -> Optional[datetime.date]:
        return self.days[-1][0] if self.days else None


def load_history(paths: DeskPaths, through: Optional[datetime.date] = None) -> History:
    """Every readable book snapshot under ``data/book/`` dated on or before ``through`` (all when ``None``), in date
    order; a corrupt or malformed file is skipped and named."""
    try:
        names = sorted(os.listdir(paths.book_dir))
    except FileNotFoundError:
        return History(())
    days: List[Tuple[datetime.date, Dict[str, float]]] = []
    skipped: List[str] = []
    for name in names:
        match = SNAPSHOT_NAME_RE.match(name)
        if match is None:
            continue
        try:
            day = datetime.date.fromisoformat(match.group(1))
        except ValueError:
            skipped.append(name)
            continue
        if through is not None and day > through:
            continue
        document, status = state.read_json(os.path.join(paths.book_dir, name))
        if status != state.STATUS_OK or not isinstance(document, dict) or not isinstance(document.get("legs"), list):
            skipped.append(name)
            continue
        days.append((day, symbol_ivs(document)))
    return History(tuple(days), tuple(skipped))


@dataclass(frozen=True)
class VolContext:
    """The context of one symbol: its value on the latest recorded day, how many earlier days were lower, and their
    range; ``status`` UNAVAILABLE carries a ``reason`` (:data:`REASON_WORDS`)."""

    symbol: str
    status: str
    reason: Optional[str]
    as_of: Optional[datetime.date]
    iv: Optional[float]
    history_days: int
    lower_days: Optional[int]
    rank_pct: Optional[float]
    low: Optional[float]
    high: Optional[float]
    first_day: Optional[datetime.date]

    def as_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "status": self.status, "reason": self.reason,
                "as_of": None if self.as_of is None else self.as_of.isoformat(), "iv": self.iv,
                "history_days": self.history_days, "lower_days": self.lower_days, "rank_pct": self.rank_pct,
                "low": self.low, "high": self.high,
                "first_day": None if self.first_day is None else self.first_day.isoformat()}


def context(symbol: str, history: History) -> VolContext:
    """The context of ``symbol`` on the latest recorded day against every earlier day that holds a value."""
    if not history.days:
        return VolContext(symbol, STATUS_UNAVAILABLE, REASON_NO_SNAPSHOTS, None, None, 0, None, None, None, None, None)
    latest, values = history.days[-1]
    earlier = [(day, ivs[symbol]) for day, ivs in history.days[:-1] if symbol in ivs]
    today = values.get(symbol)
    if today is None:
        return VolContext(symbol, STATUS_UNAVAILABLE, REASON_NO_VALUE, latest, None, len(earlier), None, None, None,
                          None, earlier[0][0] if earlier else None)
    if len(earlier) < MIN_HISTORY_DAYS:
        return VolContext(symbol, STATUS_UNAVAILABLE, REASON_SHORT_HISTORY, latest, today, len(earlier), None, None,
                          None, None, earlier[0][0] if earlier else None)
    ivs = [iv for _day, iv in earlier]
    lower = sum(1 for iv in ivs if iv < today)
    return VolContext(symbol, STATUS_OK, None, latest, today, len(ivs), lower, 100.0 * lower / len(ivs), min(ivs),
                      max(ivs), earlier[0][0])


def contexts(history: History, symbols: Optional[Sequence[str]] = None) -> List[VolContext]:
    """The context of each of ``symbols``, or of every symbol the latest day holds a value for."""
    if symbols is None:
        symbols = sorted(history.days[-1][1]) if history.days else []
    return [context(symbol, history) for symbol in symbols]


__all__ = [
    "History",
    "MIN_HISTORY_DAYS",
    "MODEL",
    "REASON_NO_SNAPSHOTS",
    "REASON_NO_VALUE",
    "REASON_SHORT_HISTORY",
    "REASON_WORDS",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "VolContext",
    "context",
    "contexts",
    "load_history",
    "symbol_ivs",
]
