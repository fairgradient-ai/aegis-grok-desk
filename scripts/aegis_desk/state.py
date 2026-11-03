"""Durable on-disk state for the desk: locking, atomic writes, journal appends, safe reads.

Standard library only; no network. Every state file the tools write (``state/desk_state.json``,
``journal/*.jsonl``, ``data/book/*.json``, heartbeat and acknowledgement ledgers) goes through
this module so that a routine killed mid-write, two routines overlapping after a late queue, or
a corrupt file never produce a silently wrong number:

* :func:`locked` -- an advisory exclusive lock on a lock file (``fcntl.flock``), acquired with
  ``LOCK_NB`` and retried every ``poll_s`` until ``timeout_s`` elapses, then :class:`LockBusy`.
  ``flock`` locks are per open file description, so the lock excludes other processes AND a
  second ``locked()`` on the same path inside one process (the context manager is therefore
  not re-entrant: nesting it on one path times out). Requires a POSIX platform; on a platform
  without ``fcntl`` it raises :class:`LockUnsupported`.
* :func:`atomic_write_text` / :func:`atomic_write_json` -- write to a temporary file in the
  same directory, ``fsync`` it, ``os.replace`` it over the target and ``fsync`` the directory,
  so a reader sees either the old or the new complete file. An existing target keeps its
  permission bits; a new file gets ``0o644`` masked by the process umask.
* :func:`append_jsonl` -- one JSON object per line appended with ``O_APPEND`` and ``fsync``;
  the serialised object never contains a newline.
* :func:`read_json` -- returns ``(obj, status)``; ``status`` is ``"ok"``, ``"missing"`` (no
  file) or ``"corrupt"``. A file that is not valid UTF-8 JSON is moved aside to
  ``<path>.corrupt-<UTC timestamp>`` in the same directory (quarantine), the function returns
  ``(None, "corrupt")`` and the caller reports UNAVAILABLE; the next read of the path says
  ``"missing"``. Environmental read errors (permissions, I/O) propagate as :class:`OSError`.
* :class:`DeskJSONEncoder` / :func:`json_dumps` -- the one JSON convention: ``Decimal`` as a
  string (money is never a binary float on the wire), ``datetime`` as ISO-8601 in UTC with a
  ``+00:00`` offset (``datetime.fromisoformat`` on Python 3.9 reads it back; naive datetimes
  are refused because their zone is unknown), ``date``/``time`` as ISO strings, dataclasses as
  objects, sets as sorted lists. ``NaN``/``inf`` floats are refused (``allow_nan=False``), keys
  are sorted and the indentation is two spaces; :func:`json_dumps_line` is the single-line form
  used for JSONL.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import errno
import json
import os
import tempfile
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None  # type: ignore[assignment]

STATUS_OK: str = "ok"
"""``read_json`` status: the file was read and parsed."""

STATUS_MISSING: str = "missing"
"""``read_json`` status: no file at the path (the desk's first day, or after a quarantine)."""

STATUS_CORRUPT: str = "corrupt"
"""``read_json`` status: the file was not valid JSON and has been moved to the quarantine name."""

DEFAULT_LOCK_TIMEOUT_S: float = 5.0
DEFAULT_LOCK_POLL_S: float = 0.05
NEW_FILE_MODE: int = 0o644
QUARANTINE_SUFFIX: str = ".corrupt-"


class LockBusy(RuntimeError):
    """The lock file is held by another process (or another handle) past the timeout."""

    def __init__(self, lock_path: str, timeout_s: float) -> None:
        super().__init__("lock %s is busy after %.3f s" % (lock_path, timeout_s))
        self.lock_path = lock_path
        self.timeout_s = timeout_s


class LockUnsupported(RuntimeError):
    """``fcntl`` is unavailable on this platform, so advisory locking cannot be provided."""


# --------------------------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------------------------


def utc_now() -> datetime.datetime:
    """The current time as an aware UTC ``datetime`` (the only clock the state layer uses)."""
    return datetime.datetime.now(datetime.timezone.utc)  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9


def format_utc(moment: datetime.datetime) -> str:
    """ISO-8601 in UTC with a ``+00:00`` offset; refuses naive datetimes (unknown zone)."""
    if not isinstance(moment, datetime.datetime):
        raise TypeError("format_utc() takes datetime.datetime, got %s" % type(moment).__name__)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise TypeError(
            "naive datetime %r has no zone; attach tzinfo before serialising" % (moment,)
        )
    return moment.astimezone(datetime.timezone.utc).isoformat()  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9


def quarantine_stamp(moment: datetime.datetime | None = None) -> str:
    """Filesystem-safe UTC stamp for quarantine names: ``YYYYMMDDTHHMMSS.ffffffZ``."""
    moment = utc_now() if moment is None else moment.astimezone(datetime.timezone.utc)  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9
    return moment.strftime("%Y%m%dT%H%M%S.%fZ")


# --------------------------------------------------------------------------------------------
# JSON convention
# --------------------------------------------------------------------------------------------


class DeskJSONEncoder(json.JSONEncoder):
    """The desk's JSON encoder (module notes): Decimal -> str, aware datetime -> ISO-8601 UTC."""

    def default(self, o: Any) -> Any:  # noqa: D401 - json.JSONEncoder hook
        if isinstance(o, Decimal):
            if not o.is_finite():
                raise ValueError("non-finite Decimal %s cannot be serialised" % o)
            return str(o)
        if isinstance(o, datetime.datetime):
            return format_utc(o)
        if isinstance(o, (datetime.date, datetime.time)):
            return o.isoformat()
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, (set, frozenset)):
            return sorted(o)
        return super().default(o)


def json_dumps(obj: Any) -> str:
    """Pretty JSON (two-space indent, sorted keys, UTF-8 text, no NaN) in the desk convention."""
    return json.dumps(
        obj, cls=DeskJSONEncoder, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    )


def json_dumps_line(obj: Any) -> str:
    """Single-line JSON for JSONL journals; the result contains no newline character."""
    text = json.dumps(
        obj,
        cls=DeskJSONEncoder,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    if "\n" in text or "\r" in text:  # json escapes newlines inside strings; this cannot happen
        raise ValueError("serialised JSONL record contains a newline")
    return text


# --------------------------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------------------------


@contextlib.contextmanager
def locked(
    lock_path: str, timeout_s: float = DEFAULT_LOCK_TIMEOUT_S, poll_s: float = DEFAULT_LOCK_POLL_S
) -> Iterator[None]:
    """Hold an exclusive advisory lock on ``lock_path`` for the duration of the block.

    The lock file is created when missing (its directory too). Acquisition is non-blocking
    with a retry every ``poll_s`` seconds; :class:`LockBusy` is raised once ``timeout_s`` has
    elapsed without the lock (``timeout_s=0`` means one attempt). The lock is released and
    the handle closed on exit, including on exceptions.
    """
    if fcntl is None:
        raise LockUnsupported("file locking needs fcntl, which this platform does not provide")
    if timeout_s < 0 or poll_s <= 0:
        raise ValueError("timeout_s must be >= 0 and poll_s > 0")
    directory = os.path.dirname(os.path.abspath(lock_path))
    os.makedirs(directory, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise LockBusy(lock_path, timeout_s) from None
                time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())) or poll_s)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------------------------
# Atomic writes and appends
# --------------------------------------------------------------------------------------------


def _fsync_directory(directory: str) -> None:
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - directories that cannot be opened (some filesystems)
        return
    try:
        os.fsync(dir_fd)
    except OSError:  # pragma: no cover - fsync unsupported on this directory
        pass
    finally:
        os.close(dir_fd)


def _target_mode(path: str) -> int:
    try:
        return os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mask = os.umask(0o022)
        os.umask(mask)
        return NEW_FILE_MODE & ~mask


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically (temp file, fsync, ``os.replace``, dir fsync)."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("atomic_write_bytes() takes bytes, got %s" % type(data).__name__)
    path = os.path.abspath(path)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    mode = _target_mode(path)
    fd, temporary = tempfile.mkstemp(
        prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    _fsync_directory(directory)


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text`` atomically; ``text`` is written as-is (no newline added)."""
    if not isinstance(text, str):
        raise TypeError("atomic_write_text() takes str, got %s" % type(text).__name__)
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: str, obj: Any) -> None:
    """Replace ``path`` with ``obj`` serialised by :func:`json_dumps` plus a final newline."""
    atomic_write_text(path, json_dumps(obj) + "\n")


def append_jsonl(path: str, obj: Any) -> None:
    """Append one JSON object as a line to ``path`` (created when missing) and ``fsync`` it."""
    line = (json_dumps_line(obj) + "\n").encode("utf-8")
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, NEW_FILE_MODE)
    try:
        view = memoryview(line)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------------------------


def quarantine_path(path: str, moment: datetime.datetime | None = None) -> str:
    """The name a corrupt ``path`` is moved to: ``<path>.corrupt-<UTC stamp>``."""
    return path + QUARANTINE_SUFFIX + quarantine_stamp(moment)


def read_json(path: str) -> tuple[Any, str]:
    """Read JSON from ``path``: ``(obj, "ok")``, ``(None, "missing")`` or ``(None, "corrupt")``.

    Corruption (undecodable UTF-8, invalid JSON, an empty file) moves the file to
    :func:`quarantine_path` so the next run does not trip over it again, and the caller maps
    the status to UNAVAILABLE. Other :class:`OSError` conditions propagate.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None, STATUS_MISSING
    try:
        return json.loads(raw.decode("utf-8")), STATUS_OK
    except (UnicodeDecodeError, ValueError):
        destination = quarantine_path(path)
        with contextlib.suppress(FileNotFoundError):
            os.replace(path, destination)
        _fsync_directory(os.path.dirname(os.path.abspath(path)))
        return None, STATUS_CORRUPT


def read_jsonl(path: str) -> tuple[list, str]:
    """Read a JSONL journal: ``(records, status)`` with the statuses of :func:`read_json`.

    A truncated last line (a crash between the write and the fsync) is dropped and reported
    through ``status == "ok"`` only when every complete line parsed; any malformed complete
    line quarantines the whole file, exactly like :func:`read_json`.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return [], STATUS_MISSING
    try:
        text = raw.decode("utf-8")
        lines = text.split("\n")
        if lines and lines[-1] != "":
            lines = lines[:-1]  # incomplete trailing record: not yet durable, ignored
        else:
            lines = lines[:-1] if lines else lines
        return [json.loads(line) for line in lines if line.strip()], STATUS_OK
    except (UnicodeDecodeError, ValueError):
        destination = quarantine_path(path)
        with contextlib.suppress(FileNotFoundError):
            os.replace(path, destination)
        _fsync_directory(os.path.dirname(os.path.abspath(path)))
        return [], STATUS_CORRUPT


__all__ = [
    "DEFAULT_LOCK_POLL_S",
    "DEFAULT_LOCK_TIMEOUT_S",
    "DeskJSONEncoder",
    "LockBusy",
    "LockUnsupported",
    "NEW_FILE_MODE",
    "QUARANTINE_SUFFIX",
    "STATUS_CORRUPT",
    "STATUS_MISSING",
    "STATUS_OK",
    "append_jsonl",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "format_utc",
    "json_dumps",
    "json_dumps_line",
    "locked",
    "quarantine_path",
    "quarantine_stamp",
    "read_json",
    "read_jsonl",
    "utc_now",
]
