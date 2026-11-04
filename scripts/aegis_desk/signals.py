"""Signal and alert journals: ``journal/signals.jsonl``, ``journal/alerts.jsonl``, the day's Markdown journal, ids.

Plan section 8 ("Signals and alerts"), the PR 2c brief and addendum decisions 1 and 5; standard library only.

Signal ids
----------
``SIG-<YYYYMMDD>-<NNN>``: the America/New_York date of the record's ``posted_at`` and a three-digit number counted
per ET day from ``journal/signals.jsonl`` (the highest number of that day plus one, so a gap is never refilled; 999
signals a day at most, then :class:`SignalJournalError`). An id is never reused: the numbering also reads the ids
still legible in a line that does not parse and in every quarantined copy (``signals.jsonl.corrupt-<stamp>``, left
by an earlier release that moved a corrupt journal aside).

Reading the journals
--------------------
The journals are append-only, so a read never moves one aside (:func:`read_journal`, used for ``signals.jsonl``,
``alerts.jsonl``, ``state/acks.jsonl`` and the heartbeat history): a line that does not parse is skipped and
reported (status ``partial``, with its line number), the records around it are kept, and a record appended right
after a torn line (a crash between a write and its newline) is still read. A dry run, ``ack.py list`` and ``ack.py
show`` therefore write nothing. :func:`append_record` ends a torn last line before it appends.

Records
-------
``signals.jsonl`` holds one record per posted verdict transition with exactly :data:`SIGNAL_KEYS` (``signal_id,
trade_id, structure, verdict, reasons, plan, data_quality, quote_age_at_post, run_lateness_min, posted_at``);
:func:`append_signal` validates the record (``verdict`` is ``EXIT_SIGNAL``, ``WATCH`` or ``UNAVAILABLE``; ``plan``
is a plan code or ``None``; ``posted_at`` an aware instant or its ISO-8601 text; minutes are non-negative integers
or ``None``), allocates the id when ``signal_id`` is ``None`` (an explicit id must name the ET date of ``posted_at``
and must not be in the journal yet) and appends it. ``alerts.jsonl`` holds one record per posted message and
signal, exactly :data:`ALERT_KEYS` (``signal_id`` (``None`` for a message that is no signal: CONFIG CHANGED, the
pre-close line, watch alive), ``text``, ``posted_at``, ``lint_ok``); :func:`append_alert` also appends the text to
``journal/<ET date>.md`` under a ``## HH:MM UTC <signal id | message> (lint ok)`` heading, the human-readable day
journal. Both appends run under the journals' own lock file (``journal/.journal.lock``), never the desk lock
(``state/.lock``), so a caller may hold the desk lock around them (``state.locked`` is not re-entrant).

Open signals and the ladder
---------------------------
:func:`open_signals` reads both journals against the acknowledgement records of ``state/acks.jsonl``
(``{utc, signal_id, trade_id, action: ack | snooze | acted | config, until, rows_sha, hashes}``): the latest signal
of each trade is open unless an ``ack`` or ``acted`` record names it (a later signal of the same trade supersedes an
earlier one, which is then no longer open); a ``snooze`` leaves it open with ``ack_state`` ``snoozed`` and the
``until`` text (the caller compares it with its clock). ``posts`` counts the linted ``alerts.jsonl`` records of the
signal. The re-post ladder for an unacknowledged EXIT_SIGNAL (plan section 11): re-post on the next run, then three
runs, then seven runs after the first post (:data:`LADDER_RUN_OFFSETS`, counted in this routine's runs);
:func:`repost_due` answers whether the next re-post is due.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from . import market_clock, state
from .desk_paths import DeskPaths

SIGNALS_FILE: str = "signals.jsonl"
ALERTS_FILE: str = "alerts.jsonl"
JOURNAL_LOCK_FILE: str = ".journal.lock"
SIGNAL_KEYS: Tuple[str, ...] = ("signal_id", "trade_id", "structure", "verdict", "reasons", "plan", "data_quality",
                                "quote_age_at_post", "run_lateness_min", "posted_at")
ALERT_KEYS: Tuple[str, ...] = ("signal_id", "text", "posted_at", "lint_ok")
SIGNAL_VERDICTS: Tuple[str, ...] = ("EXIT_SIGNAL", "WATCH", "UNAVAILABLE")
PLANS: Tuple[str, ...] = ("close", "roll_review", "hold_review")
ACK_ACTIONS: Tuple[str, ...] = ("ack", "snooze", "acted", "config")
CLOSING_ACTIONS: Tuple[str, ...] = ("ack", "acted")
ACK_STATE_OPEN: str = "open"
ACK_STATE_SNOOZED: str = "snoozed"
LADDER_RUN_OFFSETS: Tuple[int, ...] = (1, 3, 7)
"""Re-posts of an unacknowledged EXIT_SIGNAL, in this routine's runs after the first post: the next run, then three
runs, then seven runs after it; nothing after the third re-post."""
MAX_SIGNALS_PER_DAY: int = 999
SIGNAL_ID_RE = re.compile(r"^SIG-([0-9]{4})([0-9]{2})([0-9]{2})-([0-9]{3})\Z")
STATUS_OK: str = state.STATUS_OK
STATUS_MISSING: str = state.STATUS_MISSING
STATUS_PARTIAL: str = "partial"
"""A journal read with lines that do not parse (skipped, never quarantined)."""
_SIGNAL_ID_SCAN_RE = re.compile(r"SIG-([0-9]{8})-([0-9]{3})")


class SignalJournalError(RuntimeError):
    """The journal cannot give the record an id: a duplicate id, or the day's ids are exhausted."""


@dataclass(frozen=True)
class OpenSignal:
    """An open signal (module notes): the signal record's fields, how often it was posted and its ack state."""

    signal_id: str
    trade_id: str
    structure: str
    verdict: str
    plan: Optional[str]
    posted_at: datetime.datetime
    posts: int
    last_posted_at: Optional[datetime.datetime]
    ack_state: str
    snoozed_until: Optional[str]
    record: Dict[str, Any] = field(default_factory=dict)

    @property
    def next_repost_after_runs(self) -> Optional[int]:
        """Runs after the first post at which the next re-post is due (``None`` once the ladder is done)."""
        if self.posts <= 0:
            return LADDER_RUN_OFFSETS[0]
        index = self.posts - 1
        return LADDER_RUN_OFFSETS[index] if index < len(LADDER_RUN_OFFSETS) else None


# --------------------------------------------------------------------------------------------
# Paths, ids and instants
# --------------------------------------------------------------------------------------------


def signals_path(paths: DeskPaths) -> str:
    """``journal/signals.jsonl`` under the desk root."""
    return os.path.join(paths.journal_dir, SIGNALS_FILE)


def alerts_path(paths: DeskPaths) -> str:
    """``journal/alerts.jsonl`` under the desk root."""
    return os.path.join(paths.journal_dir, ALERTS_FILE)


def day_journal_path(paths: DeskPaths, et_date: datetime.date) -> str:
    """``journal/<ET date>.md`` under the desk root."""
    return os.path.join(paths.journal_dir, "%s.md" % et_date.isoformat())


def _lock_path(paths: DeskPaths) -> str:
    return os.path.join(paths.journal_dir, JOURNAL_LOCK_FILE)


def format_signal_id(et_date: datetime.date, number: int) -> str:
    """``SIG-<YYYYMMDD>-<NNN>``; ``number`` is 1 to :data:`MAX_SIGNALS_PER_DAY`."""
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= MAX_SIGNALS_PER_DAY:
        raise ValueError("a signal number is an integer from 1 to %d" % MAX_SIGNALS_PER_DAY)
    return "SIG-%s-%03d" % (et_date.strftime("%Y%m%d"), number)


def parse_signal_id(text: Any) -> Optional[Tuple[datetime.date, int]]:
    """``(ET date, number)`` of a well-formed signal id, else ``None``."""
    if not isinstance(text, str):
        return None
    match = SIGNAL_ID_RE.match(text)
    if match is None:
        return None
    try:
        day = datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    number = int(match.group(4))
    return (day, number) if number >= 1 else None


def _instant(value: Any, what: str) -> datetime.datetime:
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("%s %r is not an ISO-8601 instant" % (what, value)) from None
    if not isinstance(value, datetime.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("%s must be an aware instant" % what)
    return value.astimezone(datetime.timezone.utc)


def et_date_of(moment: datetime.datetime) -> datetime.date:
    """The America/New_York date of an aware instant."""
    return market_clock.to_et(_instant(moment, "instant")).date()


# --------------------------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------------------------


def _salvage(line: str) -> Optional[Any]:
    """The JSON record a line ends with when a torn fragment precedes it (``None`` when there is none)."""
    start = line.find("{", 1)
    while start != -1:
        try:
            return json.loads(line[start:])
        except ValueError:
            start = line.find("{", start + 1)
    return None


def read_journal(path: str) -> Tuple[List[Any], str, List[Tuple[int, str]]]:
    """``(records, status, bad_lines)`` of an append-only JSONL journal (module notes, "Reading the journals"):
    ``missing`` without the file, ``partial`` when some line did not parse (``bad_lines`` holds its 1-based number
    and its text), else ``ok``. An unterminated last line is not yet durable and is ignored, as ``state.read_jsonl``
    ignores it. The file is never moved or changed."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return [], STATUS_MISSING, []
    lines = raw.split(b"\n")
    lines = lines[:-1]  # the text after the last newline: empty, or a torn record not yet durable
    records: List[Any] = []
    bad: List[Tuple[int, str]] = []
    for number, data in enumerate(lines, 1):
        if not data.strip():
            continue
        text = data.decode("utf-8", errors="replace")
        try:
            records.append(json.loads(data.decode("utf-8")))
            continue
        except (UnicodeDecodeError, ValueError):
            bad.append((number, text))
        salvaged = _salvage(text)
        if salvaged is not None:
            records.append(salvaged)
    return records, (STATUS_PARTIAL if bad else STATUS_OK), bad


def read_signals(paths: DeskPaths) -> Tuple[List[Dict[str, Any]], str]:
    """``(records, status)`` of ``signals.jsonl`` (:func:`read_journal`: a bad line is skipped, never quarantined)."""
    records, status, _bad = read_journal(signals_path(paths))
    return [record for record in records if isinstance(record, dict)], status


def read_alerts(paths: DeskPaths) -> Tuple[List[Dict[str, Any]], str]:
    """``(records, status)`` of ``alerts.jsonl`` (:func:`read_journal`: a bad line is skipped, never quarantined)."""
    records, status, _bad = read_journal(alerts_path(paths))
    return [record for record in records if isinstance(record, dict)], status


def append_record(path: str, record: Any) -> None:
    """``state.append_jsonl`` after ending a torn last line with a newline, so the new record is a line of its own."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            torn = False
            if size:
                handle.seek(size - 1)
                torn = handle.read(1) != b"\n"
    except FileNotFoundError:
        torn = False
    if torn:
        _append_text(path, "\n")
    state.append_jsonl(path, record)


def _quarantined_numbers(paths: DeskPaths, day_key: str) -> Set[int]:
    numbers: Set[int] = set()
    directory = paths.journal_dir
    prefix = SIGNALS_FILE + state.QUARANTINE_SUFFIX
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return numbers
    for name in names:
        if not name.startswith(prefix):
            continue
        with open(os.path.join(directory, name), "rb") as handle:
            text = handle.read().decode("utf-8", errors="replace")
        numbers.update(int(number) for day, number in _SIGNAL_ID_SCAN_RE.findall(text) if day == day_key)
    return numbers


def _used_numbers(paths: DeskPaths, et_date: datetime.date) -> Tuple[Set[int], Set[str]]:
    raw, _status, bad = read_journal(signals_path(paths))
    records = [record for record in raw if isinstance(record, dict)]
    ids = {record.get("signal_id") for record in records if isinstance(record.get("signal_id"), str)}
    numbers = set()
    for signal_id in ids:
        parsed = parse_signal_id(signal_id)
        if parsed is not None and parsed[0] == et_date:
            numbers.add(parsed[1])
    day_key = et_date.strftime("%Y%m%d")
    for _number, text in bad:
        numbers.update(int(number) for day, number in _SIGNAL_ID_SCAN_RE.findall(text) if day == day_key)
    numbers.update(_quarantined_numbers(paths, day_key))
    return numbers, ids


def _next_id(paths: DeskPaths, et_date: datetime.date) -> Tuple[str, Set[str]]:
    numbers, ids = _used_numbers(paths, et_date)
    number = max(numbers) + 1 if numbers else 1
    if number > MAX_SIGNALS_PER_DAY:
        raise SignalJournalError("no signal id left for %s: %d signals already recorded"
                                 % (et_date.isoformat(), MAX_SIGNALS_PER_DAY))
    return format_signal_id(et_date, number), ids


def next_signal_id(paths: DeskPaths, et_date: datetime.date) -> str:
    """The id the next signal of ``et_date`` gets (module notes, "Signal ids"); a preview: :func:`append_signal`
    allocates under the journal lock."""
    return _next_id(paths, et_date)[0]


# --------------------------------------------------------------------------------------------
# Appends
# --------------------------------------------------------------------------------------------


def _minutes(value: Any, what: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("%s must be a non-negative integer number of minutes or None" % what)


def _validate_signal(record: Mapping[str, Any]) -> datetime.datetime:
    keys = set(record)
    if keys != set(SIGNAL_KEYS):
        missing = sorted(set(SIGNAL_KEYS) - keys)
        extra = sorted(keys - set(SIGNAL_KEYS))
        raise ValueError("a signal record has exactly the keys %s (missing %s, unknown %s)"
                         % (", ".join(SIGNAL_KEYS), missing or "none", extra or "none"))
    if not isinstance(record["trade_id"], str) or not record["trade_id"]:
        raise ValueError("trade_id must be a non-empty string")
    if not isinstance(record["structure"], str) or not record["structure"]:
        raise ValueError("structure must be a non-empty string")
    if record["verdict"] not in SIGNAL_VERDICTS:
        raise ValueError("verdict must be one of %s" % ", ".join(SIGNAL_VERDICTS))
    if record["plan"] is not None and record["plan"] not in PLANS:
        raise ValueError("plan must be one of %s or None" % ", ".join(PLANS))
    if not isinstance(record["reasons"], list):
        raise ValueError("reasons must be a list")
    if not isinstance(record["data_quality"], str):
        raise ValueError("data_quality must be a string")
    _minutes(record["quote_age_at_post"], "quote_age_at_post")
    _minutes(record["run_lateness_min"], "run_lateness_min")
    return _instant(record["posted_at"], "posted_at")


def append_signal(paths: DeskPaths, record: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate ``record`` (module notes, "Records"), give it its id when ``signal_id`` is ``None`` and append it to
    ``journal/signals.jsonl`` under the journal lock; returns the stored record (keys in :data:`SIGNAL_KEYS` order)."""
    posted_at = _validate_signal(record)
    et_date = et_date_of(posted_at)
    given = record["signal_id"]
    if given is not None:
        parsed = parse_signal_id(given)
        if parsed is None:
            raise ValueError("signal_id %r is not SIG-YYYYMMDD-NNN" % (given,))
        if parsed[0] != et_date:
            raise ValueError("signal_id %s does not name the ET date %s of posted_at" % (given, et_date.isoformat()))
    os.makedirs(paths.journal_dir, exist_ok=True)
    with state.locked(_lock_path(paths)):
        allocated, ids = _next_id(paths, et_date) if given is None else (given, _used_numbers(paths, et_date)[1])
        if given is not None and given in ids:
            raise SignalJournalError("signal id %s is already in %s" % (given, SIGNALS_FILE))
        stored = {key: record[key] for key in SIGNAL_KEYS}
        stored["signal_id"] = allocated
        stored["posted_at"] = posted_at
        append_record(signals_path(paths), stored)
    return stored


def _append_text(path: str, text: str) -> None:
    data = text.encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, state.NEW_FILE_MODE)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def append_alert(paths: DeskPaths, signal_id: Optional[str], text: str, lint_ok: bool, *,
                 posted_at: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Append one posted message to ``journal/alerts.jsonl`` and to the day's Markdown journal (module notes);
    ``posted_at`` defaults to ``state.utc_now()``. Returns the stored record (keys in :data:`ALERT_KEYS` order)."""
    if signal_id is not None and parse_signal_id(signal_id) is None:
        raise ValueError("signal_id %r is not SIG-YYYYMMDD-NNN" % (signal_id,))
    if not isinstance(text, str) or not text.strip():
        raise ValueError("an alert's text must be a non-empty string")
    if not isinstance(lint_ok, bool):
        raise ValueError("lint_ok must be a bool")
    moment = _instant(state.utc_now() if posted_at is None else posted_at, "posted_at")
    stored = {"signal_id": signal_id, "text": text, "posted_at": moment, "lint_ok": lint_ok}
    et_date = et_date_of(moment)
    os.makedirs(paths.journal_dir, exist_ok=True)
    with state.locked(_lock_path(paths)):
        append_record(alerts_path(paths), stored)
        day_path = day_journal_path(paths, et_date)
        entry = "## %s UTC %s (%s)\n\n%s\n\n" % (moment.strftime("%H:%M"), signal_id or "message",
                                                 "lint ok" if lint_ok else "lint failed: not posted", text.rstrip("\n"))
        if not os.path.exists(day_path):
            entry = "# Desk journal %s (America/New_York)\n\n%s" % (et_date.isoformat(), entry)
        _append_text(day_path, entry)
    return stored


# --------------------------------------------------------------------------------------------
# Open signals and the ladder
# --------------------------------------------------------------------------------------------


def posted_since(paths: DeskPaths, since: datetime.datetime) -> bool:
    """True when a linted message was journaled at or after ``since`` (the watch-alive check)."""
    moment = _instant(since, "since")
    records, _status = read_alerts(paths)
    for record in records:
        if record.get("lint_ok") is not True:
            continue
        try:
            if _instant(record.get("posted_at"), "posted_at") >= moment:
                return True
        except ValueError:
            continue
    return False


def open_signals(paths: DeskPaths, acks: Iterable[Mapping[str, Any]]) -> List[OpenSignal]:
    """The open signals in journal order (module notes, "Open signals and the ladder")."""
    records, _status = read_signals(paths)
    alert_records, _alert_status = read_alerts(paths)
    latest: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if parse_signal_id(record.get("signal_id")) is None or not isinstance(record.get("trade_id"), str):
            continue
        latest[record["trade_id"]] = record  # a later signal of the same trade supersedes an earlier one
    closed: Set[str] = set()
    snoozed: Dict[str, Optional[str]] = {}
    for item in acks:
        signal_id = item.get("signal_id")
        if not isinstance(signal_id, str):
            continue
        if item.get("action") in CLOSING_ACTIONS:
            closed.add(signal_id)
        elif item.get("action") == "snooze":
            until = item.get("until")
            snoozed[signal_id] = until if isinstance(until, str) else None
    posts: Dict[str, List[datetime.datetime]] = {}
    for alert in alert_records:
        signal_id = alert.get("signal_id")
        if not isinstance(signal_id, str) or alert.get("lint_ok") is not True:
            continue
        try:
            posts.setdefault(signal_id, []).append(_instant(alert.get("posted_at"), "posted_at"))
        except ValueError:
            continue
    out: List[OpenSignal] = []
    for record in sorted(latest.values(), key=lambda item: records.index(item)):
        signal_id = record["signal_id"]
        if signal_id in closed:
            continue
        try:
            posted_at = _instant(record.get("posted_at"), "posted_at")
        except ValueError:
            continue
        stamps = posts.get(signal_id, [])
        out.append(OpenSignal(
            signal_id=signal_id, trade_id=record["trade_id"], structure=str(record.get("structure")),
            verdict=str(record.get("verdict")), plan=record.get("plan"), posted_at=posted_at, posts=len(stamps),
            last_posted_at=max(stamps) if stamps else None,
            ack_state=ACK_STATE_SNOOZED if signal_id in snoozed else ACK_STATE_OPEN,
            snoozed_until=snoozed.get(signal_id), record=dict(record)))
    return out


def repost_due(posts: int, runs_since_first_post: int) -> bool:
    """Whether an unacknowledged EXIT_SIGNAL posted ``posts`` times is due again ``runs_since_first_post`` runs of
    the routine after its first post (:data:`LADDER_RUN_OFFSETS`); a signal never posted is due at once."""
    for value, what in ((posts, "posts"), (runs_since_first_post, "runs_since_first_post")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("%s must be a non-negative integer" % what)
    if posts == 0:
        return True
    index = posts - 1
    if index >= len(LADDER_RUN_OFFSETS):
        return False
    return runs_since_first_post >= LADDER_RUN_OFFSETS[index]


__all__ = [
    "ACK_ACTIONS",
    "ACK_STATE_OPEN",
    "ACK_STATE_SNOOZED",
    "ALERTS_FILE",
    "ALERT_KEYS",
    "CLOSING_ACTIONS",
    "JOURNAL_LOCK_FILE",
    "LADDER_RUN_OFFSETS",
    "MAX_SIGNALS_PER_DAY",
    "OpenSignal",
    "PLANS",
    "SIGNALS_FILE",
    "SIGNAL_ID_RE",
    "SIGNAL_KEYS",
    "SIGNAL_VERDICTS",
    "STATUS_MISSING",
    "STATUS_OK",
    "STATUS_PARTIAL",
    "SignalJournalError",
    "alerts_path",
    "append_alert",
    "append_record",
    "append_signal",
    "day_journal_path",
    "et_date_of",
    "format_signal_id",
    "next_signal_id",
    "open_signals",
    "parse_signal_id",
    "posted_since",
    "read_alerts",
    "read_journal",
    "read_signals",
    "repost_due",
    "signals_path",
]
