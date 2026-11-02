"""The command-line contract every desk tool shares: flags, exit codes, envelopes, the floor.

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased
changes, commit 75a4c79 (scripts/cli_contract.py): the machine-readable argument-error
pattern of its ``ReadOnlyParser`` and the ``--schema`` self-description. The flags, exit-code
table, envelopes and floor check are Aegis Desk's own. Standard library only; no network.

Flags (every tool, added by :class:`ReadOnlyParser`)
----------------------------------------------------
``--json``       print one JSON envelope instead of prose (argument errors too, see below)
``--schema``     describe the tool's JSON output and exit 0 without touching the network or
                 any desk file
``--timeout``    finite positive seconds for any single network request (default 10)
``--desk-root``  the desk directory holding ``desk.toml``, ``positions.csv``, ``state/`` ...;
                 defaults to the current working directory. ``--offline`` runs and every
                 fixture run (tests, ``cli_journeys.py``) MUST pass it explicitly, so that a
                 tool can never read a fixture as the live desk or the live desk as a fixture.
``--offline``    never open a network connection; read only what ``--desk-root`` holds

Exit codes (one table for every tool; the Bot and ``routine.py`` branch on them)
--------------------------------------------------------------------------------
``EXIT_QUIET = 0``        ran; nothing to post (or the posted text is informational)
``EXIT_ERROR = 1``        the tool itself failed (bug, unreadable file, bad data)
``EXIT_USAGE = 2``        invalid arguments (argparse convention)
``EXIT_FIRED = 10``       a rule fired / a signal is standing: the message must be posted
``EXIT_UNAVAILABLE = 20`` an input the verdict needs is missing or stale: say UNAVAILABLE,
                          never HOLD

Envelopes
---------
:func:`envelope` wraps a tool's payload::

    {"schema_version": 1, "tool": "exit_check", "tool_version": "0.1.0", "mode": "read-only",
     "status": "fired", "generated_at": "2026-11-02T14:31:05+00:00",
     "source": "tradier sandbox", "delay_min": 15, "payload": {...}}

``mode`` is always ``"read-only"``; ``status`` is one of :data:`STATUSES` and mirrors the exit
code (:func:`exit_code_for`); ``source`` names the data source and ``delay_min`` its delay
(``None`` for tools with no market data). :func:`error_envelope` replaces ``payload`` with
``{"error": {"code": ..., "message": ...}}``. :func:`schema_envelope` is the ``--schema``
answer: the output description, the exit-code table and the error shape.

Serialisation is :func:`aegis_desk.state.json_dumps` (Decimal as strings, datetimes as
ISO-8601 UTC, sorted keys), re-exported here as :func:`json_dumps`.

Interpreter floor
-----------------
A tool's first statements are::

    from __future__ import annotations
    from aegis_desk import contract
    contract.python_floor_check()

:func:`python_floor_check` prints an error envelope and exits ``EXIT_ERROR`` when the
interpreter is below ``(3, 9)``. The check is only as old as the modules it imports can be:
this module and :mod:`aegis_desk.state` import and run on Python 3.7 and 3.8 (the
``annotations`` future import needs 3.7; nothing here needs 3.9), so 3.7 and 3.8 get the
envelope. Python 3.6 and older fail on the future import with the interpreter's own
``SyntaxError``, which is still a clear, non-silent failure.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import state
from .state import format_utc, json_dumps, utc_now

SCHEMA_VERSION: int = 1
MODE: str = "read-only"
PYTHON_FLOOR = (3, 9)
DEFAULT_TIMEOUT_S: float = 10.0

EXIT_QUIET: int = 0
EXIT_ERROR: int = 1
EXIT_USAGE: int = 2
EXIT_FIRED: int = 10
EXIT_UNAVAILABLE: int = 20

STATUS_OK: str = "ok"
STATUS_QUIET: str = "quiet"
STATUS_ERROR: str = "error"
STATUS_USAGE: str = "usage"
STATUS_FIRED: str = "fired"
STATUS_UNAVAILABLE: str = "unavailable"

EXIT_CODES: Dict[int, str] = {
    EXIT_QUIET: "quiet: ran, nothing to post (informational output at most)",
    EXIT_ERROR: "error: the tool failed (bug, unreadable file, bad data)",
    EXIT_USAGE: "usage: invalid arguments",
    EXIT_FIRED: "fired: a rule fired or a signal stands; the message must be posted",
    EXIT_UNAVAILABLE: "unavailable: an input the verdict needs is missing or stale; say UNAVAILABLE, never HOLD",
}
"""The exit-code table, as printed by ``--schema``."""

STATUS_FOR_EXIT: Dict[int, str] = {
    EXIT_QUIET: STATUS_QUIET,
    EXIT_ERROR: STATUS_ERROR,
    EXIT_USAGE: STATUS_USAGE,
    EXIT_FIRED: STATUS_FIRED,
    EXIT_UNAVAILABLE: STATUS_UNAVAILABLE,
}
EXIT_FOR_STATUS: Dict[str, int] = {status: code for code, status in STATUS_FOR_EXIT.items()}
EXIT_FOR_STATUS[STATUS_OK] = EXIT_QUIET
STATUSES = tuple([STATUS_OK] + [STATUS_FOR_EXIT[code] for code in sorted(STATUS_FOR_EXIT)])
"""Every status word an envelope may carry."""

ERROR_SHAPE: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "tool": "string",
    "tool_version": "string",
    "mode": MODE,
    "status": "error | usage | unavailable",
    "generated_at": "ISO-8601 UTC",
    "error": {"code": "string (for example invalid_arguments, source_unavailable, python_too_old)",
              "message": "string"},
}


# --------------------------------------------------------------------------------------------
# Status / exit-code mapping
# --------------------------------------------------------------------------------------------


def exit_code_for(status: str) -> int:
    """The exit code a tool returns for an envelope ``status`` (``"ok"`` exits ``EXIT_QUIET``)."""
    try:
        return EXIT_FOR_STATUS[status]
    except KeyError:
        raise ValueError("unknown status %r; expected one of %s" % (status, ", ".join(STATUSES))) from None


def status_for(exit_code: int) -> str:
    """The status word for an exit code of the table."""
    try:
        return STATUS_FOR_EXIT[exit_code]
    except KeyError:
        raise ValueError("exit code %r is not in the table %s" % (exit_code, sorted(STATUS_FOR_EXIT))) from None


# --------------------------------------------------------------------------------------------
# Envelopes
# --------------------------------------------------------------------------------------------


def _stamp(generated_at: Any) -> str:
    if generated_at is None:
        return format_utc(utc_now())
    if isinstance(generated_at, str):
        return generated_at
    return format_utc(generated_at)


def envelope(tool: str, tool_version: str, payload: Any, source: Optional[str] = None,
             delay_min: Optional[int] = None, generated_at: Any = None,
             status: str = STATUS_OK) -> Dict[str, Any]:
    """The success envelope (module notes); ``payload`` is the tool's own JSON-able object."""
    if status not in EXIT_FOR_STATUS:
        raise ValueError("unknown status %r; expected one of %s" % (status, ", ".join(STATUSES)))
    if delay_min is not None and (isinstance(delay_min, bool) or not isinstance(delay_min, int) or delay_min < 0):
        raise ValueError("delay_min must be a non-negative integer number of minutes or None")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": tool,
        "tool_version": tool_version,
        "mode": MODE,
        "status": status,
        "generated_at": _stamp(generated_at),
        "source": source,
        "delay_min": delay_min,
        "payload": payload,
    }


def error_envelope(tool: str, tool_version: str, code: str, message: str, status: str,
                   generated_at: Any = None) -> Dict[str, Any]:
    """The failure envelope: ``status`` is ``"error"``, ``"usage"`` or ``"unavailable"``."""
    if status not in (STATUS_ERROR, STATUS_USAGE, STATUS_UNAVAILABLE):
        raise ValueError("an error envelope's status must be error, usage or unavailable, not %r" % (status,))
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": tool,
        "tool_version": tool_version,
        "mode": MODE,
        "status": status,
        "generated_at": _stamp(generated_at),
        "error": {"code": code, "message": message},
    }


def schema_envelope(tool: str, tool_version: str, output: Any, network_access: str = "none",
                    description: Optional[str] = None) -> Dict[str, Any]:
    """The ``--schema`` answer: what the tool prints, which exit codes it uses, its error shape."""
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": tool,
        "tool_version": tool_version,
        "mode": MODE,
        "description": description,
        "network_access": network_access,
        "exit_codes": {str(code): EXIT_CODES[code] for code in sorted(EXIT_CODES)},
        "envelope": {
            "schema_version": SCHEMA_VERSION, "tool": tool, "tool_version": tool_version, "mode": MODE,
            "status": " | ".join(STATUSES), "generated_at": "ISO-8601 UTC", "source": "string | null",
            "delay_min": "integer minutes | null", "payload": output,
        },
        "error": dict(ERROR_SHAPE, tool=tool, tool_version=tool_version),
    }


# --------------------------------------------------------------------------------------------
# Interpreter floor
# --------------------------------------------------------------------------------------------


def python_floor_check(minimum: Sequence[int] = PYTHON_FLOOR, version: Optional[Sequence[int]] = None,
                       stream: Any = None, tool: str = "aegis_desk", tool_version: str = "0") -> None:
    """Exit ``EXIT_ERROR`` with an error envelope when the interpreter is below ``minimum``.

    ``version`` defaults to ``sys.version_info``; tests pass an explicit tuple and a stream.
    Written without anything newer than Python 3.7 so that the envelope, not a traceback, is
    what an old interpreter prints.
    """
    floor = tuple(minimum)
    current = tuple(sys.version_info[:3]) if version is None else tuple(version)
    if current[: len(floor)] >= floor:
        return
    out = sys.stdout if stream is None else stream
    message = "Python %s is below the desk's floor %s; install Python %s or newer" % (
        ".".join(str(part) for part in current[:3]), ".".join(str(part) for part in floor),
        ".".join(str(part) for part in floor))
    body = error_envelope(tool, tool_version, "python_too_old", message, STATUS_ERROR)
    out.write(json.dumps(body, sort_keys=True) + "\n")
    out.flush()
    sys.exit(EXIT_ERROR)


# --------------------------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------------------------


class ReadOnlyParser(argparse.ArgumentParser):
    """``argparse.ArgumentParser`` with the shared flags and machine-readable usage errors.

    ``tool`` and ``tool_version`` name the command for envelopes (``prog`` defaults to
    ``tool``). ``desk_root_must_exist`` makes ``--desk-root`` an existing directory (the
    default; ``desk_init.py`` passes ``False`` because it creates the desk). After parsing,
    ``args.desk_root`` is absolute (the current directory when the flag is absent),
    ``args.desk_root_explicit`` records whether the flag was given, ``args.timeout`` is a
    finite positive float, and ``--offline`` without ``--desk-root`` is a usage error.

    When ``--json`` (or ``--schema``) is among the arguments, :meth:`error` prints an error
    envelope with code ``invalid_arguments`` and status ``usage`` on stdout and exits
    ``EXIT_USAGE``; otherwise argparse's usage text goes to stderr with the same exit code.
    Flags must be spelled out in full (``allow_abbrev`` is off): ``--js`` is an unknown
    argument, never a quiet alias of ``--json``.
    """

    def __init__(self, tool: str, tool_version: str, description: Optional[str] = None,
                 desk_root_must_exist: bool = True, **kwargs: Any) -> None:
        kwargs.setdefault("prog", tool)
        kwargs.setdefault("description", description)
        # Exact flags only: argparse would otherwise accept --js or --sch as abbreviations, and a
        # Bot that abbreviates would get a prose usage error instead of the JSON envelope it
        # asked for (machine-readable mode is decided by the exact spelling in argv).
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(**kwargs)
        self.tool = tool
        self.tool_version = tool_version
        self.desk_root_must_exist = desk_root_must_exist
        self.machine_readable = False
        self.add_argument("--json", action="store_true",
                          help="print one JSON envelope (schema_version %d) instead of prose" % SCHEMA_VERSION)
        self.add_argument("--schema", action="store_true",
                          help="describe the JSON output and the exit codes, then exit 0 without network or desk access")
        self.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, metavar="SECONDS",
                          help="finite positive timeout for any single network request (default %s)" % DEFAULT_TIMEOUT_S)
        self.add_argument("--desk-root", default=None, metavar="DIR",
                          help="desk directory (desk.toml, positions.csv, state/); default: the current directory. "
                               "--offline runs and fixture runs must pass it explicitly")
        self.add_argument("--offline", action="store_true",
                          help="never open a network connection; use only what --desk-root holds (requires --desk-root)")

    # argparse calls this for every usage problem, including unknown flags and bad types.
    def error(self, message: str) -> Any:
        if self.machine_readable:
            body = error_envelope(self.tool, self.tool_version, "invalid_arguments", message, STATUS_USAGE)
            sys.stdout.write(json_dumps(body) + "\n")
            sys.stdout.flush()
            self.exit(EXIT_USAGE)
        super().error(message)

    def parse_args(self, args: Optional[Sequence[str]] = None, namespace: Any = None) -> Any:
        argv: List[str] = list(sys.argv[1:] if args is None else args)
        self.machine_readable = "--json" in argv or "--schema" in argv
        parsed = super().parse_args(argv, namespace)
        if not math.isfinite(parsed.timeout) or parsed.timeout <= 0:
            self.error("--timeout must be a finite positive number of seconds")
        parsed.desk_root_explicit = parsed.desk_root is not None
        if parsed.offline and not parsed.desk_root_explicit:
            self.error("--offline runs must pass an explicit --desk-root")
        if parsed.desk_root is None:
            parsed.desk_root = os.getcwd()
        parsed.desk_root = os.path.abspath(parsed.desk_root)
        if parsed.desk_root_explicit and self.desk_root_must_exist and not os.path.isdir(parsed.desk_root):
            self.error("--desk-root %s is not a directory" % parsed.desk_root)
        return parsed

    def schema(self, output: Any, network_access: str = "none") -> Dict[str, Any]:
        """The ``--schema`` envelope for this tool (``output`` describes its payload)."""
        return schema_envelope(self.tool, self.tool_version, output, network_access, self.description)


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "ERROR_SHAPE",
    "EXIT_CODES",
    "EXIT_ERROR",
    "EXIT_FIRED",
    "EXIT_FOR_STATUS",
    "EXIT_QUIET",
    "EXIT_UNAVAILABLE",
    "EXIT_USAGE",
    "MODE",
    "PYTHON_FLOOR",
    "ReadOnlyParser",
    "SCHEMA_VERSION",
    "STATUSES",
    "STATUS_ERROR",
    "STATUS_FIRED",
    "STATUS_FOR_EXIT",
    "STATUS_OK",
    "STATUS_QUIET",
    "STATUS_UNAVAILABLE",
    "STATUS_USAGE",
    "envelope",
    "error_envelope",
    "exit_code_for",
    "format_utc",
    "json_dumps",
    "python_floor_check",
    "schema_envelope",
    "state",
    "status_for",
    "utc_now",
]
