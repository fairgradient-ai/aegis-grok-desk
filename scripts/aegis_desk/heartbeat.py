"""Heartbeats: the on-disk proof that a routine ran (plan sections 9 and 11). Standard library only.

Every routine run ends by recording one heartbeat. ``state/heartbeat/<routine_id>.json`` holds the
latest run, replaced atomically through :func:`aegis_desk.state.atomic_write_json`, and
``state/heartbeat/history.jsonl`` keeps every run, appended with
:func:`aegis_desk.state.append_jsonl`. The Desk Lead's routine audit and the doctor read these
back to say "the desk ran today" from evidence, never from memory.

Record (schema 1; keys sorted on disk by the desk JSON convention)::

    {"schema": 1, "routine_id": "book-check",
     "run_utc": "...", "started_utc": "...", "finished_utc": "...",   ISO-8601 UTC (+00:00)
     "exit_code": 0, "status": "ok",                                  the contract's table
     "message_sha256": "<64 lowercase hex>" | null,                   hash of the posted text, never the text
     "scheduled_et": "HH:MM" | null, "lateness_min": 7 | null,
     "lateness_reason": "measured" | "before_schedule" | "no_schedule",
     "tool_version": "0.1.0"}

Lateness is the whole number of minutes (floor division of the seconds, as every age in the desk)
from the scheduled America/New_York time on the run's ET date to ``started_utc``; the slot is
mapped to UTC with :func:`aegis_desk.market_clock.from_et`, so daylight time is right on both
sides of the November and March transitions. It is never negative: a run that starts before its
slot records ``0`` with ``lateness_reason = "before_schedule"``; without a schedule
``lateness_min`` is ``None`` with ``lateness_reason = "no_schedule"``; otherwise the reason is
``"measured"``. Desk routines are daytime slots (07:05 to 18:00 ET), so a slot within minutes of
midnight ET is outside this definition (the run's ET date could differ from the slot's).

``status`` mirrors ``exit_code`` by the contract's own table (:data:`aegis_desk.contract.
STATUS_FOR_EXIT`): 0 is ``ok`` or ``quiet``, 1 ``error``, 2 ``usage``, 10 ``fired``, 20
``unavailable``; any other exit code (a crash, a signal, 127) is ``error``, because the contract
defines everything outside its table as the tool failing. :func:`write_heartbeat` refuses a record
whose status and exit code disagree (:class:`HeartbeatError`, a ``ValueError``), so the audit never
reads "exit 20, status ok". :func:`default_status` is the status a caller records when it holds
only the exit code (``ok`` for 0).

Writes take no desk lock: each routine owns its own file (replaced atomically) and the history is
append-only (``O_APPEND``, one fsync'd line per record), so overlapping routines cannot corrupt
either. Reads report ``ok`` / ``missing`` / ``corrupt`` through :func:`aegis_desk.state.read_json`;
a file that parses but is not this routine's schema-1 record (another routine's id, another
schema, an unreadable ``run_utc``, a non-integer exit code, an unknown status) is corrupt too and
is quarantined the same way (moved to ``<file>.corrupt-<UTC stamp>``), so the next read says
``missing`` and the caller reports UNAVAILABLE instead of reading a wrong record twice.

The library never reads the wall clock: ``now`` is always a parameter.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import contract, market_clock, state
from .desk_paths import DeskPaths

SCHEMA_VERSION: int = 1
"""The record schema written and accepted by this module."""

HISTORY_FILE: str = "history.jsonl"
"""The append-only ledger of every run, beside the per-routine files."""

RECORD_SUFFIX: str = ".json"

ROUTINE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
"""A routine id: lowercase letters, digits and hyphens, 2 to 41 characters, starting with a letter."""

SCHEDULED_ET_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
"""The schedule spelling: ``HH:MM`` on the 24-hour clock, America/New_York."""

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
"""``hashlib.sha256(...).hexdigest()``: 64 lowercase hex digits."""

MAX_EXIT_CODE: int = 255
"""Exit codes are process exit statuses: 0 to 255."""

STATUSES: Tuple[str, ...] = contract.STATUSES
"""Every status a heartbeat may carry (the contract's vocabulary)."""

TOOL_STATUSES: Tuple[str, ...] = (
    contract.STATUS_OK, contract.STATUS_ERROR, contract.STATUS_USAGE, contract.STATUS_FIRED,
    contract.STATUS_UNAVAILABLE,
)
"""The ``--status`` choices of ``scripts/heartbeat.py`` (the brief's list; ``quiet`` is spelled ``ok``)."""

LATENESS_MEASURED: str = "measured"
LATENESS_BEFORE_SCHEDULE: str = "before_schedule"
LATENESS_NO_SCHEDULE: str = "no_schedule"
LATENESS_REASONS: Tuple[str, ...] = (LATENESS_MEASURED, LATENESS_BEFORE_SCHEDULE, LATENESS_NO_SCHEDULE)

UTC = datetime.timezone.utc


class HeartbeatError(ValueError):
    """An invalid heartbeat input: routine id, status, exit code, hash, schedule or instant."""


@dataclass(frozen=True)
class Heartbeat:
    """One recorded run (module notes). ``as_dict`` is exactly the on-disk record."""

    routine_id: str
    run_utc: datetime.datetime
    started_utc: datetime.datetime
    finished_utc: datetime.datetime
    exit_code: int
    status: str
    message_sha256: Optional[str]
    scheduled_et: Optional[str]
    lateness_min: Optional[int]
    lateness_reason: str
    tool_version: str
    schema: int = SCHEMA_VERSION

    def as_dict(self) -> Dict[str, Any]:
        """The JSON record: instants as ISO-8601 UTC strings, everything else as stored."""
        return {
            "schema": self.schema,
            "routine_id": self.routine_id,
            "run_utc": state.format_utc(self.run_utc),
            "started_utc": state.format_utc(self.started_utc),
            "finished_utc": state.format_utc(self.finished_utc),
            "exit_code": self.exit_code,
            "status": self.status,
            "message_sha256": self.message_sha256,
            "scheduled_et": self.scheduled_et,
            "lateness_min": self.lateness_min,
            "lateness_reason": self.lateness_reason,
            "tool_version": self.tool_version,
        }


# --------------------------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------------------------


def validate_routine_id(routine_id: Any) -> str:
    """``routine_id`` when it matches :data:`ROUTINE_ID_RE`; otherwise :class:`HeartbeatError`."""
    if not isinstance(routine_id, str) or not ROUTINE_ID_RE.match(routine_id):
        raise HeartbeatError("routine id must match %s, got %r" % (ROUTINE_ID_RE.pattern, routine_id))
    return routine_id


def validate_scheduled_et(value: Any) -> str:
    """``value`` when it is ``HH:MM`` on the 24-hour clock; otherwise :class:`HeartbeatError`."""
    if not isinstance(value, str) or not SCHEDULED_ET_RE.match(value):
        raise HeartbeatError("scheduled_et must be HH:MM on the 24-hour clock (America/New_York), got %r" % (value,))
    return value


def validate_exit_code(value: Any) -> int:
    """``value`` when it is an ``int`` (not a bool) between 0 and 255; otherwise :class:`HeartbeatError`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > MAX_EXIT_CODE:
        raise HeartbeatError("exit_code must be an integer from 0 to %d, got %r" % (MAX_EXIT_CODE, value))
    return value


def validate_message_sha256(value: Any) -> Optional[str]:
    """``None`` or 64 lowercase hex digits; anything else is :class:`HeartbeatError`."""
    if value is None:
        return None
    if not isinstance(value, str) or not SHA256_RE.match(value):
        raise HeartbeatError("message_sha256 must be 64 lowercase hex digits or None, got %r" % (value,))
    return value


def _instant(value: Any, what: str) -> datetime.datetime:
    if not isinstance(value, datetime.datetime):
        raise HeartbeatError("%s must be an aware UTC datetime, got %s" % (what, type(value).__name__))
    if value.tzinfo is None or value.utcoffset() is None:
        raise HeartbeatError("%s must be timezone-aware (UTC); a naive datetime is ambiguous" % what)
    return value.astimezone(UTC)


def _stored_instant(value: Any, what: str) -> datetime.datetime:
    """An instant read back from a record (``...+00:00`` or ``...Z``)."""
    try:
        return contract.parse_utc_instant(value, what)
    except ValueError as exc:
        raise HeartbeatError(str(exc)) from None


def default_status(exit_code: int) -> str:
    """The status the contract's table gives ``exit_code``: ``ok`` for 0, ``error`` outside the table."""
    code = validate_exit_code(exit_code)
    if code == contract.EXIT_QUIET:
        return contract.STATUS_OK
    return contract.STATUS_FOR_EXIT.get(code, contract.STATUS_ERROR)


def status_matches_exit(status: str, exit_code: int) -> bool:
    """True when ``status`` is the contract's status for ``exit_code`` (``ok`` and ``quiet`` both
    fit 0; only ``error`` fits a code outside the table). Unknown statuses never match."""
    code = validate_exit_code(exit_code)
    if status not in STATUSES:
        return False
    if code in contract.STATUS_FOR_EXIT:
        return contract.EXIT_FOR_STATUS[status] == code
    return status == contract.STATUS_ERROR


def sha256_hex(data: bytes) -> str:
    """``hashlib.sha256(data).hexdigest()``: what ``message_sha256`` records of the posted text."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("sha256_hex() takes bytes, got %s" % type(data).__name__)
    return hashlib.sha256(bytes(data)).hexdigest()


# --------------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------------


def heartbeat_path(paths: DeskPaths, routine_id: str) -> str:
    """``state/heartbeat/<routine_id>.json`` under the desk root."""
    return os.path.join(paths.heartbeat_dir, validate_routine_id(routine_id) + RECORD_SUFFIX)


def history_path(paths: DeskPaths) -> str:
    """``state/heartbeat/history.jsonl`` under the desk root."""
    return os.path.join(paths.heartbeat_dir, HISTORY_FILE)


# --------------------------------------------------------------------------------------------
# Lateness
# --------------------------------------------------------------------------------------------


def lateness(scheduled_et: Optional[str], started_utc: datetime.datetime) -> Tuple[Optional[int], str]:
    """``(lateness_min, lateness_reason)`` for a run that started at ``started_utc`` (module notes).

    The slot is ``scheduled_et`` on the America/New_York date of ``started_utc``, mapped to UTC
    with :func:`aegis_desk.market_clock.from_et`. Whole minutes, floor; ``0`` with
    ``before_schedule`` when the run started early; ``None`` with ``no_schedule`` when there is no
    schedule.
    """
    started = _instant(started_utc, "started_utc")
    if scheduled_et is None:
        return None, LATENESS_NO_SCHEDULE
    match = SCHEDULED_ET_RE.match(validate_scheduled_et(scheduled_et))
    hour, minute = int(match.group(1)), int(match.group(2))
    et_date = market_clock.to_et(started).date()
    slot_utc = market_clock.from_et(datetime.datetime.combine(et_date, datetime.time(hour, minute)))
    seconds = (started - slot_utc).total_seconds()
    if seconds < 0:
        return 0, LATENESS_BEFORE_SCHEDULE
    return int(seconds // 60), LATENESS_MEASURED


# --------------------------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------------------------


def write_heartbeat(paths: DeskPaths, routine_id: str, *, now: datetime.datetime, exit_code: int, status: str,
                    started_utc: datetime.datetime, finished_utc: datetime.datetime,
                    message_sha256: Optional[str], scheduled_et: Optional[str], tool_version: str) -> Heartbeat:
    """Record one run: replace ``state/heartbeat/<routine_id>.json`` atomically and append the same
    record to ``history.jsonl``. Returns the :class:`Heartbeat` written.

    Every input is validated first (:class:`HeartbeatError`) so a refused record leaves no file
    behind: the routine id, the instants (aware; ``finished_utc`` not before ``started_utc``), the
    exit code (0-255), the status (the contract's vocabulary, matching the exit code), the hash
    (64 lowercase hex or None), the schedule (``HH:MM`` or None) and a non-blank ``tool_version``.
    The heartbeat directory is created when missing (through the state layer); ``OSError`` from
    the filesystem propagates for the tool to report as a write error.
    """
    if not isinstance(paths, DeskPaths):
        raise HeartbeatError("write_heartbeat() takes a DeskPaths, got %s" % type(paths).__name__)
    routine_id = validate_routine_id(routine_id)
    run_utc = _instant(now, "now")
    started = _instant(started_utc, "started_utc")
    finished = _instant(finished_utc, "finished_utc")
    if finished < started:
        raise HeartbeatError("finished_utc %s is before started_utc %s" % (state.format_utc(finished),
                                                                          state.format_utc(started)))
    code = validate_exit_code(exit_code)
    if status not in STATUSES:
        raise HeartbeatError("status must be one of %s, got %r" % (", ".join(STATUSES), status))
    if not status_matches_exit(status, code):
        raise HeartbeatError("status %r does not match exit code %d: the contract records exit %d as %r"
                             % (status, code, code, default_status(code)))
    digest = validate_message_sha256(message_sha256)
    schedule = None if scheduled_et is None else validate_scheduled_et(scheduled_et)
    if not isinstance(tool_version, str) or not tool_version.strip():
        raise HeartbeatError("tool_version must be a non-blank string, got %r" % (tool_version,))
    minutes, reason = lateness(schedule, started)
    record = Heartbeat(routine_id, run_utc, started, finished, code, status, digest, schedule, minutes, reason,
                       tool_version)
    document = record.as_dict()
    state.atomic_write_json(heartbeat_path(paths, routine_id), document)
    state.append_jsonl(history_path(paths), document)
    return record


# --------------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------------


def record_problem(document: Any, routine_id: str) -> Optional[str]:
    """Why ``document`` is not a usable schema-1 record of ``routine_id``; ``None`` when it is."""
    if not isinstance(document, dict):
        return "not a JSON object"
    if document.get("routine_id") != routine_id:
        return "routine_id %r does not match the file %r" % (document.get("routine_id"), routine_id)
    if document.get("schema") != SCHEMA_VERSION:
        return "schema %r is not %d" % (document.get("schema"), SCHEMA_VERSION)
    try:
        _stored_instant(document.get("run_utc"), "run_utc")
    except HeartbeatError as exc:
        return str(exc)
    code = document.get("exit_code")
    if isinstance(code, bool) or not isinstance(code, int):
        return "exit_code %r is not an integer" % (code,)
    if document.get("status") not in STATUSES:
        return "status %r is not one of %s" % (document.get("status"), ", ".join(STATUSES))
    return None


def _quarantine(path: str) -> None:
    destination = state.quarantine_path(path)
    try:
        os.replace(path, destination)
    except FileNotFoundError:
        pass


def read_heartbeat(paths: DeskPaths, routine_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(record, "ok")``, ``(None, "missing")`` or ``(None, "corrupt")`` for the routine's latest run.

    Corruption (undecodable content through :func:`aegis_desk.state.read_json`, or a document that
    :func:`record_problem` rejects) quarantines the file so the next read says ``missing``.
    """
    routine_id = validate_routine_id(routine_id)
    path = heartbeat_path(paths, routine_id)
    document, status = state.read_json(path)
    if status != state.STATUS_OK:
        return None, status
    if record_problem(document, routine_id) is not None:
        _quarantine(path)
        return None, state.STATUS_CORRUPT
    return document, state.STATUS_OK


def heartbeat_age_min(record: Any, now: datetime.datetime) -> int:
    """Whole minutes (floor) from the record's ``run_utc`` to ``now``; negative when the record is
    stamped after ``now`` (clock skew is shown, never hidden). :class:`HeartbeatError` when the
    record carries no readable ``run_utc`` or ``now`` is not an aware datetime."""
    if not isinstance(record, dict):
        raise HeartbeatError("a heartbeat record is a dict, got %s" % type(record).__name__)
    run_utc = _stored_instant(record.get("run_utc"), "run_utc")
    moment = _instant(now, "now")
    return int((moment - run_utc).total_seconds() // 60)


def list_heartbeats(paths: DeskPaths) -> List[Dict[str, Any]]:
    """Every heartbeat under ``state/heartbeat/``, sorted by routine id.

    Each entry is the stored record plus ``read_status = "ok"``, or
    ``{"routine_id": ..., "read_status": "corrupt"}`` for a file :func:`read_heartbeat` rejected
    (and quarantined). Only ``<routine_id>.json`` files whose stem is a routine id are heartbeats;
    ``history.jsonl``, quarantine files and anything else in the directory are not listed. A
    missing directory lists nothing.
    """
    if not isinstance(paths, DeskPaths):
        raise HeartbeatError("list_heartbeats() takes a DeskPaths, got %s" % type(paths).__name__)
    try:
        names = os.listdir(paths.heartbeat_dir)
    except FileNotFoundError:
        return []
    found: List[Dict[str, Any]] = []
    for name in sorted(names):
        if not name.endswith(RECORD_SUFFIX):
            continue
        stem = name[: -len(RECORD_SUFFIX)]
        if not ROUTINE_ID_RE.match(stem):
            continue
        record, status = read_heartbeat(paths, stem)
        if status == state.STATUS_OK and record is not None:
            found.append(dict(record, read_status=status))
        else:
            found.append({"routine_id": stem, "read_status": status})
    return sorted(found, key=lambda item: item["routine_id"])


__all__ = [
    "HISTORY_FILE",
    "Heartbeat",
    "HeartbeatError",
    "LATENESS_BEFORE_SCHEDULE",
    "LATENESS_MEASURED",
    "LATENESS_NO_SCHEDULE",
    "LATENESS_REASONS",
    "MAX_EXIT_CODE",
    "RECORD_SUFFIX",
    "ROUTINE_ID_RE",
    "SCHEDULED_ET_RE",
    "SCHEMA_VERSION",
    "SHA256_RE",
    "STATUSES",
    "TOOL_STATUSES",
    "default_status",
    "heartbeat_age_min",
    "heartbeat_path",
    "history_path",
    "lateness",
    "list_heartbeats",
    "read_heartbeat",
    "record_problem",
    "sha256_hex",
    "status_matches_exit",
    "validate_exit_code",
    "validate_message_sha256",
    "validate_routine_id",
    "validate_scheduled_et",
    "write_heartbeat",
]
