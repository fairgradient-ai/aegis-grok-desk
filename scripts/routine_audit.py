#!/usr/bin/env python3
"""routine_audit.py: the routine budget, the Grok Bot Auto-review texts and the day audit of the heartbeats.

Usage::

    python3 scripts/routine_audit.py --budget [--profile lean|active] [--json]
    python3 scripts/routine_audit.py --rules-text [--json]
    python3 scripts/routine_audit.py [--profile lean|active] [--date YYYY-MM-DD] [--week]
        [--json] [--schema] [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

An owner and Bot command (plan sections 10 and 11); it is never a routine step (``eod_summary`` calls
``aegis_desk.routines.audit_day`` itself) and it posts nothing: it reads this checkout's ``routines/manifest.toml``
and ``routines/autoreview.toml`` and, for the audit, the desk root's ``desk.toml`` (the cadence profile),
``research/calendar.toml`` (owner-injected closures) and ``state/heartbeat/history.jsonl``. It writes no file.

* ``--budget``: the regular runs per week of each cadence profile (``routines.budget``: slots x days per routine,
  holidays not deducted), with each routine's slots and days; ``--profile`` limits it to one profile.
* ``--rules-text``: the three Auto-review texts exactly as the owner pastes them (``routines.rules_text``: the allow
  rule with the manifest's routine ids, the write-side Ask-first rule, the owner-authority Ask-first rule), each with
  its length against ``limit_chars``. A text over the limit, or an allow text that omits a routine id, is exit 1.
* the day audit (default): the cadence profile's slots on the ET date of the run instant (``--date`` another, never
  a later one; ``--week`` every day of that ET week up to it), each ``ran`` (the earliest heartbeat of the slot, its
  start in ET, its lateness or how early it started, its exit code and status), ``missed`` (no heartbeat 45 minutes
  after the slot) or ``pending``; late runs (more than 20 minutes) are named in the day's line, and a run that
  started before its slot shows how early. A day on which every run sits about 60 or 120 minutes from its slot gets
  ``timezone offset suspected``. ``--profile`` replaces ``desk.toml``'s ``cadence_profile`` (and then ``desk.toml``
  is not read). A missing history is a warning (nothing is recorded, so every due slot is missed).

Exit 0; 20 when an audited day has a missed slot; 1 on errors (``manifest_invalid``, ``autoreview_invalid``,
``rules_invalid``, ``desk_root``, ``config_missing``, ``config_invalid``, ``calendar_invalid``, ``io_error``);
2 on usage. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="routine_audit")

import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

from aegis_desk import calendar_events, desk_config, desk_paths, heartbeat, market_clock, routines, signals, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "routine_audit"
DESCRIPTION = ("the routine budget per cadence profile, the Grok Bot Auto-review texts rendered from the manifest, "
               "and the day audit of the routine heartbeats (expected, ran, late, missed); posts nothing")
NETWORK_ACCESS = "none"
SLOT = {"routine_id": "string", "scheduled_et": "HH:MM", "scheduled_utc": "ISO-8601 UTC",
        "records": "integer (heartbeats of the slot)", "started_utc": "ISO-8601 UTC | null",
        "lateness_min": "integer | null", "offset_min": "integer | null (negative: before the slot)",
        "exit_code": "integer | null", "status": "string | null"}
OUTPUT: Dict[str, Any] = {
    "mode": "budget | rules_text | day | week",
    "manifest": {"path": "routines/manifest.toml", "sha256": "hex"},
    "budget": {"<profile>": "(budget) integer runs per week"},
    "profiles": [{"name": "lean | active", "description": "string", "additive": "boolean",
                  "runs_per_week": "integer",
                  "routines": [{"id": "string", "owner": "string", "slots": ["HH:MM"], "days": ["Mon"],
                                "runs_per_week": "integer"}]}],
    "rules": {"allow": "(rules_text) string", "ask_first_writes": "string", "ask_first_owner_authority": "string"},
    "lengths": {"allow": "integer", "ask_first_writes": "integer", "ask_first_owner_authority": "integer"},
    "limit_chars": "integer (1000)",
    "routine_ids": ["string, manifest order"],
    "autoreview": {"path": "routines/autoreview.toml", "sha256": "hex"},
    "profile": "(day, week) lean | active",
    "days": [{"et_date": "YYYY-MM-DD", "weekday": "Mon", "trading_day": "boolean",
              "counts": {"expected": "n", "ran": "n", "late": "n", "missed": "n", "pending": "n"},
              "audit": {"expected": [SLOT], "ran": [SLOT], "late": [SLOT], "missed": [SLOT], "pending": [SLOT]},
              "timezone_offset_suspected": "-120 | -60 | 60 | 120 | null"}],
    "history": {"path": "state/heartbeat/history.jsonl", "status": "ok | partial | missing", "records": "n",
                "bad_lines": "n"},
    "warnings": ["string"],
    "exit_code": "0 | 20",
}


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def manifest_file() -> str:
    """This checkout's routine manifest (a function so the tests can point it elsewhere)."""
    return routines.manifest_path()


def autoreview_file() -> str:
    """This checkout's Auto-review texts (a function so the tests can point it elsewhere)."""
    return routines.autoreview_path()


def shown(path: str, default: str, rel: str) -> str:
    return rel if os.path.abspath(path) == os.path.abspath(default) else path


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--budget", action="store_true", help="print the regular runs per week of each cadence profile")
    parser.add_argument("--rules-text", action="store_true",
                        help="print the three Grok Bot Auto-review texts rendered from the manifest")
    parser.add_argument("--profile", choices=desk_config.CADENCE_PROFILES, default=None,
                        help="the cadence profile (default: desk.toml's cadence_profile)")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="audit this ET date instead of the run instant's (never a later one)")
    parser.add_argument("--week", action="store_true",
                        help="audit every day of the ET week up to the date (Monday first)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any, today: datetime.date) -> None:
    if args.budget and args.rules_text:
        parser.error("--budget and --rules-text are separate modes; give one")
    if args.rules_text and (args.profile or args.date or args.week):
        parser.error("--rules-text takes no --profile, --date or --week")
    if args.budget and (args.date or args.week):
        parser.error("--budget takes no --date or --week")
    args.day = today
    if args.date is not None:
        try:
            args.day = datetime.date.fromisoformat(args.date) if len(args.date) == 10 else None
        except ValueError:
            args.day = None
        if args.day is None:
            parser.error("--date must be YYYY-MM-DD, got %r" % args.date)
        if args.day > today:
            parser.error("--date %s is after the ET date of the run instant (%s)" % (args.day.isoformat(),
                                                                                     today.isoformat()))


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.STATUS_ERROR,
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return contract.EXIT_ERROR


def load_manifest() -> routines.Manifest:
    try:
        return routines.load_manifest(manifest_file())
    except routines.ManifestError as exc:
        raise ToolError("manifest_invalid", str(exc)) from None


def manifest_ref(manifest: routines.Manifest) -> Dict[str, str]:
    return {"path": shown(manifest.path, routines.manifest_path(), routines.MANIFEST_REL), "sha256": manifest.sha256}


def days_text(days: Sequence[str]) -> str:
    return "Mon-Fri" if tuple(days) == routines.WEEKDAYS else ", ".join(days)


# --------------------------------------------------------------------------------------------
# --budget
# --------------------------------------------------------------------------------------------


def budget_payload(manifest: routines.Manifest, profiles: Sequence[str]) -> Dict[str, Any]:
    described: List[Dict[str, Any]] = []
    for name in profiles:
        profile = manifest.profiles[name]
        items = [{"id": routine.id, "owner": routine.owner, "slots": list(routine.slots(name)),
                  "days": list(routine.days), "runs_per_week": len(routine.slots(name)) * len(routine.days)}
                 for routine in manifest.routines_for(name)]
        described.append({"name": name, "description": profile.description, "additive": profile.additive,
                          "runs_per_week": routines.budget(manifest, name), "routines": items})
    return {"mode": "budget", "manifest": manifest_ref(manifest),
            "budget": {item["name"]: item["runs_per_week"] for item in described}, "profiles": described}


def budget_lines(payload: Dict[str, Any]) -> List[str]:
    lines = ["routine budget (%s, sha %s): regular runs per week by cadence profile (holidays not deducted)"
             % (payload["manifest"]["path"], payload["manifest"]["sha256"][:8])]
    for profile in payload["profiles"]:
        lines.append("%s: %d runs per week (%s)" % (profile["name"], profile["runs_per_week"], profile["description"]))
        for item in profile["routines"]:
            lines.append("  %s (%s): %s ET, %s: %d" % (item["id"], item["owner"], ", ".join(item["slots"]),
                                                       days_text(item["days"]), item["runs_per_week"]))
    return lines


# --------------------------------------------------------------------------------------------
# --rules-text
# --------------------------------------------------------------------------------------------


def rules_payload(manifest: routines.Manifest) -> Dict[str, Any]:
    path = autoreview_file()
    try:
        autoreview = routines.load_autoreview(path)
    except routines.ManifestError as exc:
        raise ToolError("autoreview_invalid", str(exc)) from None
    problems = routines.rules_text_problems(manifest, autoreview)
    if problems:
        raise ToolError("rules_invalid", "%s: %s" % (shown(path, routines.autoreview_path(), routines.AUTOREVIEW_REL),
                                                     "; ".join(problems)))
    texts = routines.rules_text(manifest, autoreview)
    return {"mode": "rules_text", "manifest": manifest_ref(manifest),
            "autoreview": {"path": shown(path, routines.autoreview_path(), routines.AUTOREVIEW_REL),
                           "sha256": autoreview.sha256},
            "rules": texts, "lengths": {key: len(value) for key, value in texts.items()},
            "limit_chars": autoreview.limit_chars, "routine_ids": list(manifest.routine_ids)}


def rules_lines(payload: Dict[str, Any]) -> List[str]:
    lines = ["Auto-review rules (%s, rendered for %d routines); paste each rule exactly as printed, one rule per line:"
             % (payload["autoreview"]["path"], len(payload["routine_ids"]))]
    for number, key in enumerate(routines.RULE_KEYS, 1):
        lines.extend(["", "%d. %s (%d of %d characters)" % (number, key, payload["lengths"][key],
                                                             payload["limit_chars"]), payload["rules"][key]])
    return lines


# --------------------------------------------------------------------------------------------
# The day audit
# --------------------------------------------------------------------------------------------


def audit_profile(args: Any, paths: desk_paths.DeskPaths) -> str:
    if args.profile is not None:
        return args.profile
    if not os.path.isfile(paths.desk_toml):
        raise ToolError("config_missing", "desk.toml missing at %s: give --profile or render desk.toml first"
                        % paths.root)
    try:
        return desk_config.load_desk_config(paths.desk_toml).desk.cadence_profile
    except desk_config.DeskConfigError as exc:
        raise ToolError("config_invalid", str(exc)) from None


def extra_closures(paths: desk_paths.DeskPaths) -> Dict[datetime.date, str]:
    try:
        return calendar_events.extra_closures(calendar_events.load_calendar_if_present(paths.calendar_toml))
    except calendar_events.CalendarError as exc:
        raise ToolError("calendar_invalid", str(exc)) from None


def audit_days(args: Any) -> List[datetime.date]:
    if not args.week:
        return [args.day]
    monday = args.day - datetime.timedelta(days=args.day.weekday())
    return [monday + datetime.timedelta(days=offset) for offset in range(args.day.weekday() + 1)]


def et_clock(moment: datetime.datetime) -> str:
    return market_clock.to_et(moment).strftime("%H:%M")


def slot_name(slot: routines.SlotAudit) -> str:
    return "%s %s ET" % (slot.routine_id, slot.scheduled_et)


def day_lines(day: datetime.date, profile: str, now: datetime.datetime, audit: routines.DayAudit,
              scheduled: Sequence[routines.Slot], extra: Dict[datetime.date, str]) -> List[str]:
    head = "routine audit %s %s (profile %s, as of %s UTC): " % (routines.DAYS[day.weekday()], day.isoformat(), profile,
                                                                 now.strftime("%H:%M"))
    if not audit.expected:
        if scheduled:
            return [head + "no slot of profile %s is due yet (first slot %s ET)" % (profile, scheduled[0].scheduled_et)]
        reason = ""
        if not market_clock.is_trading_day(day, extra):
            reason = " (no session: %s)" % (market_clock.holiday_name(day, extra) or "weekend")
        return [head + "no slot of profile %s is due%s" % (profile, reason)]
    parts = ["%d of %d due slots ran" % (len(audit.ran), len(audit.expected))]
    if audit.late:
        parts.append("late: " + ", ".join("%s (%d min late)" % (slot_name(s), s.lateness_min) for s in audit.late))
    if audit.missed:
        parts.append("missed: " + ", ".join(slot_name(s) for s in audit.missed))
    if audit.pending:
        parts.append("pending: " + ", ".join(slot_name(s) for s in audit.pending))
    lines = [head + "; ".join(parts)]
    for slot in audit.expected:
        if slot.ran:
            timing = ("%d min before its slot" % -slot.offset_min if slot.offset_min is not None and slot.offset_min < 0
                      else "%d min late" % slot.lateness_min)
            text = "  %s: ran at %s ET (%s)" % (slot_name(slot), et_clock(slot.started_utc), timing)
            if slot.exit_code is not None:
                text += ", exit %d %s" % (slot.exit_code, slot.status or heartbeat.default_status(slot.exit_code))
            if slot.records > 1:
                text += ", %d heartbeats" % slot.records
            lines.append(text)
        elif slot in audit.missed:
            lines.append("  %s: missed (no heartbeat %d min after the slot)" % (slot_name(slot), routines.MISSED_AFTER_MIN))
        else:
            lines.append("  %s: pending (no heartbeat yet)" % slot_name(slot))
    return lines


def offset_warning(day: datetime.date, offset: int) -> str:
    return ("timezone offset suspected: every run of %s %s started about %d min %s its slot; check the time zone of "
            "the routine schedule" % (routines.DAYS[day.weekday()], day.isoformat(), abs(offset),
                                      "after" if offset > 0 else "before"))


def audit_payload(args: Any, now: datetime.datetime, manifest: routines.Manifest) -> Tuple[Dict[str, Any], List[str]]:
    try:
        paths = desk_paths.for_root(args.desk_root)
    except desk_paths.DeskPathsError as exc:
        raise ToolError("desk_root", str(exc)) from None
    profile = audit_profile(args, paths)
    extra = extra_closures(paths)
    history = heartbeat.history_path(paths)
    try:
        records, status, bad = signals.read_journal(history)
    except OSError as exc:
        raise ToolError("io_error", "cannot read %s: %s" % (paths.relative(history), exc.strerror or exc)) from None
    warnings: List[str] = []
    if status == signals.STATUS_MISSING:
        warnings.append("%s missing: no routine run is recorded" % paths.relative(history))
    elif bad:
        warnings.append("%s: %d unreadable line(s) skipped" % (paths.relative(history), len(bad)))
    days: List[Dict[str, Any]] = []
    lines: List[str] = []
    exit_code = contract.EXIT_QUIET
    for day in audit_days(args):
        audit = routines.audit_day(manifest, profile, records, day, now, extra)
        offset = routines.timezone_offset_suspected(audit)
        if audit.missed:
            exit_code = contract.EXIT_UNAVAILABLE
        days.append({"et_date": day.isoformat(), "weekday": routines.DAYS[day.weekday()],
                     "trading_day": market_clock.is_trading_day(day, extra),
                     "counts": {"expected": len(audit.expected), "ran": len(audit.ran), "late": len(audit.late),
                                "missed": len(audit.missed), "pending": len(audit.pending)},
                     "audit": audit.as_dict(), "timezone_offset_suspected": offset})
        lines.extend(day_lines(day, profile, now, audit, routines.day_slots(manifest, profile, day, extra), extra))
        if offset is not None:
            warnings.append(offset_warning(day, offset))
    payload = {"mode": "week" if args.week else "day", "profile": profile, "manifest": manifest_ref(manifest),
               "days": days, "history": {"path": paths.relative(history), "status": status, "records": len(records),
                                         "bad_lines": len(bad)},
               "warnings": warnings, "exit_code": exit_code}
    return payload, lines + warnings


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    now = args.now or state.utc_now()
    validate(parser, args, market_clock.to_et(now).date())
    try:
        manifest = load_manifest()
        if args.budget:
            names = [args.profile] if args.profile else list(manifest.profiles)
            missing = [name for name in names if name not in manifest.profiles]
            if missing:
                raise ToolError("manifest_invalid", "%s declares no profile %s" % (routines.MANIFEST_REL, missing[0]))
            payload = budget_payload(manifest, names)
            lines = budget_lines(payload)
            exit_code = contract.EXIT_QUIET
        elif args.rules_text:
            payload = rules_payload(manifest)
            lines = rules_lines(payload)
            exit_code = contract.EXIT_QUIET
        else:
            payload, lines = audit_payload(args, now, manifest)
            exit_code = payload["exit_code"]
    except ToolError as exc:
        return fail(args, version, now, exc.code, exc.message)
    if args.json:
        status = contract.status_for(exit_code) if exit_code else contract.STATUS_OK
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now, status=status)))
        return exit_code
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
