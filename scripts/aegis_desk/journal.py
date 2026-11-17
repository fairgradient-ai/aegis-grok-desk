"""The signal journal: each signal's outcome from the desk's own records (roadmap 3.1, the owner's answer to open
question 6: counts only).

For each signal of ``journal/signals.jsonl`` posted in a window:

* when it was posted, its trade, structure, verdict and the rule ids that made it (its ``reasons``);
* the owner's mark on it from ``state/acks.jsonl``: the latest ``ack`` (acknowledged), ``acted`` (marked acted) or
  ``snooze`` (snoozed) record that names it, else open;
* the group's ``unrealized_pl_mid`` in each end-of-day snapshot of record (``data/book/<ET date>.json``) from the
  signal's ET date to the window's end, at most :data:`MAX_MARKS` of them: a per-signal fact. A trading day without a
  snapshot, or with one that does not read, is UNAVAILABLE for that day; a group the snapshot no longer holds is
  named as closed.

The only totals are counts, in fixed words (:func:`count_line`): signals per rule and overall, with how many the owner
acknowledged, marked acted and snoozed. No sum, average or difference of money across signals, no rate and no ranking
of rules: money appears only on a per-signal fact line, which starts with the signal's id.

Pure: the records and a snapshot reader are passed in.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import market_clock, signals

MAX_MARKS: int = 5
"""End-of-day marks shown per signal: the signal's day and the next trading days, at most five."""
MARK_ACK: str = "acknowledged"
MARK_ACTED: str = "marked acted"
MARK_SNOOZED: str = "snoozed"
MARK_OPEN: str = "open"
ACK_WORDS: Dict[str, str] = {"ack": MARK_ACK, "acted": MARK_ACTED, "snooze": MARK_SNOOZED}
REASON_NO_SNAPSHOT: str = "no snapshot"
REASON_UNREADABLE: str = "the snapshot does not read"
REASON_CLOSED: str = "closed: not in the snapshot"
REASON_NO_MARK: str = "no usable mark"
OVERALL: str = "all rules"

SnapshotReader = Callable[[datetime.date], Tuple[str, Optional[Mapping[str, Any]]]]
"""``reader(day) -> (status, book)``: ``ok`` with the book document, ``missing`` or ``unreadable`` with ``None``."""


@dataclass(frozen=True)
class Mark:
    et_date: datetime.date
    value: Optional[Decimal]
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"et_date": self.et_date.isoformat(), "unrealized_pl_mid": self.value, "reason": self.reason}


@dataclass(frozen=True)
class SignalFact:
    signal_id: str
    trade_id: str
    structure: str
    verdict: str
    rule_ids: Tuple[str, ...]
    posted_at: datetime.datetime
    owner_mark: str
    owner_mark_utc: Optional[str]
    marks: Tuple[Mark, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {"signal_id": self.signal_id, "trade_id": self.trade_id, "structure": self.structure,
                "verdict": self.verdict, "rule_ids": list(self.rule_ids), "posted_at": self.posted_at,
                "owner_mark": self.owner_mark, "owner_mark_utc": self.owner_mark_utc,
                "marks": [mark.as_dict() for mark in self.marks]}


@dataclass(frozen=True)
class Count:
    signals: int = 0
    acknowledged: int = 0
    acted: int = 0
    snoozed: int = 0

    def add(self, fact: SignalFact) -> "Count":
        return Count(self.signals + 1, self.acknowledged + (fact.owner_mark == MARK_ACK),
                     self.acted + (fact.owner_mark == MARK_ACTED), self.snoozed + (fact.owner_mark == MARK_SNOOZED))

    def as_dict(self) -> Dict[str, int]:
        return {"signals": self.signals, "acknowledged": self.acknowledged, "acted": self.acted,
                "snoozed": self.snoozed}


def _instant(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def owner_marks(ack_records: Iterable[Mapping[str, Any]]) -> Dict[str, Tuple[str, Optional[str]]]:
    """``{signal_id: (mark, utc)}`` from the latest ``ack``, ``acted`` or ``snooze`` record naming each signal."""
    found: Dict[str, Tuple[str, Optional[str]]] = {}
    for record in ack_records:
        signal_id, action = record.get("signal_id"), record.get("action")
        if isinstance(signal_id, str) and action in ACK_WORDS:
            utc = record.get("utc")
            found[signal_id] = (ACK_WORDS[action], utc if isinstance(utc, str) else None)
    return found


def _marks(trade_id: str, start: datetime.date, end: datetime.date, reader: SnapshotReader,
           extra: market_clock.ExtraHolidays) -> Tuple[Mark, ...]:
    marks: List[Mark] = []
    day = start if market_clock.is_trading_day(start, extra) else market_clock.next_trading_day(start, extra)
    while day <= end and len(marks) < MAX_MARKS:
        status, book = reader(day)
        if status != "ok" or book is None:
            marks.append(Mark(day, None, REASON_NO_SNAPSHOT if status == "missing" else REASON_UNREADABLE))
        else:
            groups = [group for group in book.get("groups") or [] if group.get("trade_id") == trade_id]
            if not groups:
                marks.append(Mark(day, None, REASON_CLOSED))
            else:
                values = [group.get("unrealized_pl_mid") for group in groups]
                if any(value is None for value in values):
                    marks.append(Mark(day, None, REASON_NO_MARK))
                else:
                    marks.append(Mark(day, sum((Decimal(str(value)) for value in values), Decimal("0.00"))))
        day = market_clock.next_trading_day(day, extra)
    return tuple(marks)


def facts(signal_records: Iterable[Mapping[str, Any]], ack_records: Iterable[Mapping[str, Any]],
          reader: SnapshotReader, start: datetime.date, end: datetime.date,
          extra: market_clock.ExtraHolidays = ()) -> List[SignalFact]:
    """Every signal posted from ``start`` to ``end`` (ET dates), in journal order, with its owner mark and its marks."""
    marks_by_signal = owner_marks(ack_records)
    out: List[SignalFact] = []
    for record in signal_records:
        signal_id = record.get("signal_id")
        posted = _instant(record.get("posted_at"))
        if not isinstance(signal_id, str) or signals.parse_signal_id(signal_id) is None or posted is None:
            continue
        day = market_clock.to_et(posted).date()
        if not start <= day <= end:
            continue
        reasons = record.get("reasons") or []
        rule_ids = tuple(sorted({item.get("rule_id") for item in reasons
                                 if isinstance(item, Mapping) and isinstance(item.get("rule_id"), str)}))
        mark, mark_utc = marks_by_signal.get(signal_id, (MARK_OPEN, None))
        trade_id = str(record.get("trade_id"))
        out.append(SignalFact(signal_id, trade_id, str(record.get("structure")), str(record.get("verdict")), rule_ids,
                              posted, mark, mark_utc, _marks(trade_id, day, end, reader, extra)))
    return out


def counts(items: Sequence[SignalFact]) -> Dict[str, Count]:
    """Per rule id (a signal counts once for each of its rules; a signal with none counts under ``-``) and overall."""
    table: Dict[str, Count] = {}
    for fact in items:
        for rule_id in fact.rule_ids or ("-",):
            table[rule_id] = table.get(rule_id, Count()).add(fact)
    ordered = {rule_id: table[rule_id] for rule_id in sorted(table)}
    overall = Count()
    for fact in items:
        overall = overall.add(fact)
    ordered[OVERALL] = overall
    return ordered


def count_line(name: str, count: Count, period: str) -> str:
    """``TP50: 3 signals this week; 2 acknowledged, 1 marked acted, 0 snoozed`` (the fixed words of open question 6)."""
    return "%s: %d signal%s %s; %d acknowledged, %d marked acted, %d snoozed" % (
        name, count.signals, "" if count.signals == 1 else "s", period, count.acknowledged, count.acted, count.snoozed)


__all__ = [
    "Count",
    "MARK_ACK",
    "MARK_ACTED",
    "MARK_OPEN",
    "MARK_SNOOZED",
    "MAX_MARKS",
    "Mark",
    "OVERALL",
    "SignalFact",
    "count_line",
    "counts",
    "facts",
    "owner_marks",
]
