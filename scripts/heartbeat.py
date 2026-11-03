#!/usr/bin/env python3
"""heartbeat.py: record that a routine ran, or list the recorded heartbeats with their ages.

Usage::

    python3 scripts/heartbeat.py --routine ID [--exit-code N] [--status ok|error|usage|fired|unavailable]
        [--message-file PATH] [--scheduled-et HH:MM] [--started-utc ISO-8601-UTC]
        [--json] [--schema] [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]
    python3 scripts/heartbeat.py --list [--json] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Writes ``state/heartbeat/<ID>.json`` atomically and appends the same record to
``state/heartbeat/history.jsonl`` through ``aegis_desk.heartbeat`` (plan sections 9 and 11), then
prints ``heartbeat <ID> written at HH:MM:SS UTC (lateness N min)``. The run instant is ``--now`` or
the clock read once here; ``--started-utc`` defaults to it and ``finished_utc`` is it. ``--exit-code``
and ``--status`` derive from each other through the contract's table (0 is ``ok``; a code outside
the table is ``error``) and must agree when both are given. ``--message-file`` is hashed
(SHA-256) and never copied; ``--scheduled-et`` is the routine's America/New_York slot, from which
the lateness is measured (floored at 0 before the slot). ``--list`` prints every heartbeat with
its age at the run instant and never creates anything. The runtime directories are created on the
desk root before a write (``desk_paths.ensure_runtime_dirs``); ``desk.toml`` is not read.

Exit 0 on success (whatever status the heartbeat records), 1 when the desk root is unusable or the
write fails, 2 on usage. ``--json`` payload: ``{"heartbeat": record, "path", "history"}`` for a
write and ``{"heartbeats": [...], "count", "directory"}`` for ``--list``. ``routine.py`` (PR 2d)
calls the library directly; this tool is the hand-run and journey form.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="heartbeat")

import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import desk_paths, heartbeat, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "heartbeat"
DESCRIPTION = ("record one routine heartbeat (state/heartbeat/<id>.json plus history.jsonl) with its lateness "
               "against the scheduled ET slot, or list the recorded heartbeats with their ages")
NETWORK_ACCESS = "none"
WRITE_FLAGS = ("--routine", "--exit-code", "--status", "--message-file", "--scheduled-et", "--started-utc")
OUTPUT: Dict[str, Any] = {
    "heartbeat": {
        "schema": 1, "routine_id": "string (%s)" % heartbeat.ROUTINE_ID_RE.pattern,
        "run_utc": "ISO-8601 UTC", "started_utc": "ISO-8601 UTC", "finished_utc": "ISO-8601 UTC",
        "exit_code": "integer 0-255", "status": " | ".join(heartbeat.STATUSES),
        "message_sha256": "64 lowercase hex digits | null", "scheduled_et": "HH:MM (America/New_York) | null",
        "lateness_min": "integer >= 0 (whole minutes from the slot to started_utc) | null",
        "lateness_reason": " | ".join(heartbeat.LATENESS_REASONS), "tool_version": "string",
    },
    "path": "state/heartbeat/<routine_id>.json (desk-root relative)",
    "history": "state/heartbeat/history.jsonl",
    "heartbeats": ["(--list) the stored record plus read_status (ok | corrupt) and age_min (integer minutes | null)"],
    "count": "(--list) integer",
    "directory": "(--list) state/heartbeat",
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--routine", metavar="ID", default=None,
                        help="routine id matching %s, for example book-check" % heartbeat.ROUTINE_ID_RE.pattern)
    parser.add_argument("--exit-code", type=int, default=None, metavar="N",
                        help="the routine's exit code, 0-255 (default: 0, or the code the contract maps --status to)")
    parser.add_argument("--status", choices=heartbeat.TOOL_STATUSES, default=None,
                        help="the routine's envelope status (default: the status the contract maps --exit-code to)")
    parser.add_argument("--message-file", metavar="PATH", default=None,
                        help="the text the routine posted; only its SHA-256 is recorded")
    parser.add_argument("--scheduled-et", metavar="HH:MM", default=None,
                        help="the routine's scheduled America/New_York time; the lateness is measured from it")
    parser.add_argument("--started-utc", metavar="ISO-8601-UTC", default=None,
                        help="when the routine started (default: the run instant)")
    parser.add_argument("--list", action="store_true",
                        help="list every recorded heartbeat with its age instead of writing one")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any, now: datetime.datetime) -> None:
    """Resolve the write inputs (or refuse them) with exit 2 through the parser's error path."""
    if args.list:
        given = [flag for flag, value in zip(WRITE_FLAGS, (args.routine, args.exit_code, args.status, args.message_file,
                                                           args.scheduled_et, args.started_utc)) if value is not None]
        if given:
            parser.error("--list takes no write flags (given: %s)" % ", ".join(given))
        return
    if args.routine is None:
        parser.error("--routine ID is required (or --list)")
    try:
        heartbeat.validate_routine_id(args.routine)
    except heartbeat.HeartbeatError as exc:
        parser.error("--routine: %s" % exc)
    if args.exit_code is not None:
        try:
            heartbeat.validate_exit_code(args.exit_code)
        except heartbeat.HeartbeatError as exc:
            parser.error("--exit-code: %s" % exc)
    if args.exit_code is None and args.status is None:
        args.exit_code, args.status = contract.EXIT_QUIET, contract.STATUS_OK
    elif args.exit_code is None:
        args.exit_code = contract.exit_code_for(args.status)
    elif args.status is None:
        args.status = heartbeat.default_status(args.exit_code)
    elif not heartbeat.status_matches_exit(args.status, args.exit_code):
        parser.error("--status %s does not match --exit-code %d: the contract records exit %d as %s"
                     % (args.status, args.exit_code, args.exit_code, heartbeat.default_status(args.exit_code)))
    if args.scheduled_et is not None:
        try:
            heartbeat.validate_scheduled_et(args.scheduled_et)
        except heartbeat.HeartbeatError as exc:
            parser.error("--scheduled-et: %s" % exc)
    args.started = now
    if args.started_utc is not None:
        try:
            args.started = contract.parse_utc_instant(args.started_utc, "--started-utc")
        except ValueError as exc:
            parser.error(str(exc))
        if args.started > now:
            parser.error("--started-utc %s is after the run instant %s" % (state.format_utc(args.started),
                                                                           state.format_utc(now)))
    args.message_sha256 = None
    if args.message_file is not None:
        try:
            with open(os.path.abspath(args.message_file), "rb") as handle:
                args.message_sha256 = heartbeat.sha256_hex(handle.read())
        except OSError as exc:
            parser.error("--message-file %s cannot be read: %s" % (args.message_file, exc.strerror or exc))


def lateness_text(record: Dict[str, Any]) -> str:
    if record.get("lateness_min") is None:
        return "lateness n/a: no schedule"
    if record.get("lateness_reason") == heartbeat.LATENESS_BEFORE_SCHEDULE:
        return "lateness 0 min, before schedule"
    return "lateness %d min" % record["lateness_min"]


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.STATUS_ERROR,
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return contract.EXIT_ERROR


def list_heartbeats(args: Any, version: str, now: datetime.datetime, paths: desk_paths.DeskPaths) -> int:
    directory = paths.relative(paths.heartbeat_dir)
    try:
        items = heartbeat.list_heartbeats(paths)
    except OSError as exc:
        return fail(args, version, now, "read_failed", "cannot read %s: %s" % (directory, exc))
    entries: List[Dict[str, Any]] = []
    for item in items:
        age = heartbeat.heartbeat_age_min(item, now) if item["read_status"] == state.STATUS_OK else None
        entries.append(dict(item, age_min=age))
    if args.json:
        payload = {"heartbeats": entries, "count": len(entries), "directory": directory}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now)))
        return contract.EXIT_QUIET
    if not entries:
        print("no heartbeats recorded under %s" % directory)
        return contract.EXIT_QUIET
    for entry in entries:
        if entry["read_status"] != state.STATUS_OK:
            print("heartbeat %s: corrupt record (quarantined)" % entry["routine_id"])
            continue
        print("heartbeat %s: %s (exit %d), run %s, age %d min, %s" % (
            entry["routine_id"], entry["status"], entry["exit_code"], entry["run_utc"], entry["age_min"],
            lateness_text(entry)))
    return contract.EXIT_QUIET


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    now = args.now or state.utc_now()
    validate(parser, args, now)
    try:
        paths = desk_paths.for_root(args.desk_root)
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc))
    if args.list:
        return list_heartbeats(args, version, now, paths)
    try:
        desk_paths.ensure_runtime_dirs(paths)
        record = heartbeat.write_heartbeat(paths, args.routine, now=now, exit_code=args.exit_code, status=args.status,
                                           started_utc=args.started, finished_utc=now,
                                           message_sha256=args.message_sha256, scheduled_et=args.scheduled_et,
                                           tool_version=version)
    except OSError as exc:
        return fail(args, version, now, "write_failed",
                    "cannot write the heartbeat under %s: %s" % (paths.relative(paths.heartbeat_dir), exc))
    except heartbeat.HeartbeatError as exc:  # inputs were validated above; this would be a tool bug
        return fail(args, version, now, "heartbeat_invalid", str(exc))
    document = record.as_dict()
    if args.json:
        payload = {
            "heartbeat": document,
            "path": paths.relative(heartbeat.heartbeat_path(paths, args.routine)),
            "history": paths.relative(heartbeat.history_path(paths)),
        }
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now)))
        return contract.EXIT_QUIET
    print("heartbeat %s written at %s UTC (%s)" % (args.routine, record.run_utc.strftime("%H:%M:%S"),
                                                   lateness_text(document)))
    return contract.EXIT_QUIET


if __name__ == "__main__":
    sys.exit(main())
