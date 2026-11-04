#!/usr/bin/env python3
"""eod_summary.py: the Morning Desk Sheet (default) or the EOD Desk Brief (``--brief``), composed from the desk's records.

Usage::

    python3 scripts/eod_summary.py [--brief] [--json] [--schema] [--timeout S] [--desk-root DIR] [--offline]
        [--now ISO-8601-UTC]

The composer of the ``preopen`` (07:30 ET) and ``eod-brief`` (17:10 ET) routines (plan section 11; PR 2d decisions
D1-13 to D1-15, overrides O3, O5, O12, O15). It reads only what the other tools recorded and never fetches: no quote,
no network, no request ledger; it writes nothing either (the state layer moves a corrupt JSON record aside when it
reads one, which the message reports). ``aegis_desk.summaries`` composes the text; this tool gathers its inputs:

* ``desk.toml`` (the cadence profile and the feed delay; missing is exit 1 ``config_missing``, unusable
  ``config_invalid``) and ``research/calendar.toml`` when present (its full closures; ``calendar_invalid``);
* the records, each against the ET date of ``--now`` (``today``): the morning reads ``state/doctor.json`` (its
  ``run_utc`` on today), ``data/positions/<today>.recon.json``, ``reports/expiry/<today>.week.json`` and
  ``levels/<previous trading day>.json``; the brief reads ``data/book/<today>.json`` (``exit_rules.book_from_json``),
  the same reconciliation and, when it is there, the same week docket. A record that is missing, from another
  date (or missing beside an older one of its kind), unreadable or of another shape prints its UNAVAILABLE line;
* ``journal/signals.jsonl``, ``journal/alerts.jsonl`` and ``state/acks.jsonl`` (``signals.open_signals``; a missing
  journal is none, a line that does not parse is skipped and named);
* for the brief also ``exit_check.config_changes`` (CONFIG CHANGED pending), ``calendar_events.freshness`` over the
  snapshot's held symbols and short calls, and the day audit of this checkout's ``routines/manifest.toml`` against
  ``state/heartbeat/history.jsonl`` (``routines.audit_day`` with the cadence profile and the calendar's closures,
  the ``eod-brief`` routine itself excluded, through its own slot once that slot has come; before it, the slots due
  by ``--now``) and the names of the day's ``incidents/<stamp>-*.md`` files (the routine and step they name, never
  their contents; a name of another shape is counted as an other file and not printed);
* on an early-close day (``market_clock.is_early_close``), the morning's early-close line from the week docket (O5).

The message is linted in strict mode before anything prints; a failure is exit 1 ``lint_failed`` (the finding codes
named, nothing printed). Exit 0 when every record was read; 20 when any UNAVAILABLE line is printed; 1 on a desk,
configuration, calendar or I/O error or a failing lint; 2 on usage; never 10. Human output is the message exactly;
``--json`` prints the envelope, whose ``payload.message`` is that text and ``payload.emitted`` its strict emitted
document (``alert_lint.py --emitted-file``), which a routine composing the message re-lints.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="eod_summary")

import datetime  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import exit_check  # noqa: E402  (config_changes: the CONFIG CHANGED comparison book_check and exit_check share)
from aegis_desk import (  # noqa: E402
    alert_lint,
    book,
    calendar_events,
    desk_config,
    desk_paths,
    doctor,
    heartbeat,
    market_clock,
    quote_cache,
    recon,
    routines,
    signals,
    state,
    summaries,
)
from aegis_desk.version import release_version  # noqa: E402

TOOL = "eod_summary"
DESCRIPTION = ("the Morning Desk Sheet (default) or the EOD Desk Brief (--brief): one linted message composed from the "
               "desk's own records (doctor, reconciliation, docket, levels, book snapshot, journals, heartbeats); "
               "never fetches, writes nothing")
NETWORK_ACCESS = "none"
BRIEF_ROUTINE = "eod-brief"
MANIFEST_WHY = "routine_audit.py --budget shows why"
RECON_SUFFIX = ".recon.json"
DOCKET_SUFFIX = ".week.json"
JSON_SUFFIX = ".json"
JOURNALS: Tuple[str, ...] = ("journal/signals.jsonl", "journal/alerts.jsonl", "state/acks.jsonl")
HISTORY_REL = "state/heartbeat/history.jsonl"
INCIDENT_FILE_RE = re.compile(r"^(?P<stamp>[0-9]{8}T[0-9]{6}[.][0-9]{6}Z)-(?P<name>[a-z0-9_-]+)[.]md\Z")
"""An incident file ``routine.py`` writes: ``<state.quarantine_stamp of the run>-<name>.md``."""
INCIDENT_STAMP_FORMAT = "%Y%m%dT%H%M%S.%fZ"
INCIDENT_SLUG_RE = re.compile(r"^[a-z_]+(-[a-z0-9-]+)*\Z")
INCIDENT_WORDS: Dict[str, str] = {"message": "message", "crash": "crash"}
DIRTY_CHECKOUT_NAME = "dirty-checkout"
PAYLOAD_KEYS: Tuple[str, ...] = ("mode", "et_date", "message", "emitted", "lint", "unavailable", "artifacts",
                                 "open_signals", "signals_today", "routines", "config_changed", "calendar_findings",
                                 "incidents", "warnings", "exit_code")
_RECORD_SHAPE: Dict[str, Any] = {"label": " | ".join(summaries.LABELS), "path": "desk-root relative path",
                                 "status": " | ".join(summaries.ARTIFACT_STATUSES),
                                 "date": "YYYY-MM-DD (the record's own date when ok or late) | null"}
OUTPUT: Dict[str, Any] = {
    "mode": " | ".join(summaries.MODES),
    "et_date": "YYYY-MM-DD (the ET date of --now)",
    "message": "string: the message exactly as printed (the last line is the fixed footer)",
    "emitted": {"emitted_numbers": ["string"], "rule_ids": ["string"], "identifiers": ["string"],
                "clocks": ["HH:MM UTC | HH:MM ET"], "as_of": "ISO-8601 UTC (the footer's instant)"},
    "lint": {"ok": "boolean (true: a failing lint is exit 1 lint_failed)", "problems": ["string"],
             "numbers_checked": "integer"},
    "unavailable": [dict(_RECORD_SHAPE)],
    "artifacts": [dict(_RECORD_SHAPE)],
    "open_signals": "integer",
    "signals_today": {"count": "integer (brief; null in the morning)", "EXIT_SIGNAL": "integer", "WATCH": "integer",
                      "UNAVAILABLE": "integer"},
    "routines": {"profile": "lean | active (brief; null in the morning)",
                 "through_et": "HH:MM | null (the eod-brief slot once it has come)",
                 "counts": {"expected": "n", "ran": "n", "late": "n", "missed": "n", "pending": "n"},
                 "audit": "routines.DayAudit.as_dict() | null (the manifest cannot be read)"},
    "config_changed": [{"name": "desk_toml | positions_csv | rulebook (brief; null in the morning)",
                        "old": "acknowledged sha256 | null", "new": "current sha256 | null"}],
    "calendar_findings": "integer (brief; null in the morning)",
    "incidents": [{"file": "incidents/<stamp>-<name>.md (an incident file of the ET date; brief; null in the morning)",
                   "routine": "the manifest routine id it names | null", "what": "the step slug, message, crash, "
                   "'<slug> withheld' or 'dirty checkout' | null (a file of another name: counted, its name never "
                   "repeated, file null)"}],
    "warnings": ["string (a journal line that does not parse)"],
    "exit_code": "0 | 20",
}


class ToolError(Exception):
    """A failure the tool reports with an error envelope: ``code``, ``message`` and the exit code."""

    def __init__(self, code: str, message: str, exit_code: int = contract.EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def manifest_file() -> str:
    """This checkout's routine manifest (a function so the tests can point it elsewhere)."""
    return routines.manifest_path()


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--brief", action="store_true",
                        help="the EOD Desk Brief (17:10 ET) instead of the Morning Desk Sheet (07:30 ET)")
    return parser


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    return exit_check.fail(args, version, now, code, message, exit_code, tool=TOOL)


# --------------------------------------------------------------------------------------------
# Reading the records
# --------------------------------------------------------------------------------------------


def newest_older(directory: str, suffix: str, expected: datetime.date) -> Optional[datetime.date]:
    """The newest date before ``expected`` of a ``<YYYY-MM-DD><suffix>`` file in ``directory`` (``None``: none)."""
    pattern = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})%s\Z" % re.escape(suffix))
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return None
    dates: List[datetime.date] = []
    for name in names:
        match = pattern.match(name)
        if match is None:
            continue
        try:
            day = datetime.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day < expected:
            dates.append(day)
    return max(dates) if dates else None


def read_record(paths: desk_paths.DeskPaths, label: str, path: str, expected: datetime.date,
                suffix: Optional[str] = None) -> summaries.Artifact:
    """The :class:`summaries.Artifact` of the record at ``path`` (``state.read_json``: a corrupt file is moved aside);
    ``suffix`` names the dated files of its kind, so a missing record beside an older one is upstream late."""
    try:
        raw, status = state.read_json(path)
    except OSError as exc:
        raise ToolError("io_error", "cannot read %s: %s" % (paths.relative(path), exc.strerror or exc)) from None
    older = newest_older(os.path.dirname(path), suffix, expected) if status == state.STATUS_MISSING and suffix else None
    return summaries.make_artifact(label, paths.relative(path), raw, status, expected, older)


def read_journals(paths: desk_paths.DeskPaths) -> Tuple[List[Dict[str, Any]], List[signals.OpenSignal], List[str]]:
    """``(signal records, open signals, notes)``: the journals as ``ack.py`` reads them (never moved or changed)."""
    notes: List[str] = []
    try:
        records, status = signals.read_signals(paths)
        if status == signals.STATUS_PARTIAL:
            notes.append(summaries.PARTIAL_JOURNAL_LINE % JOURNALS[0])
        _alerts, status = signals.read_alerts(paths)
        if status == signals.STATUS_PARTIAL:
            notes.append(summaries.PARTIAL_JOURNAL_LINE % JOURNALS[1])
        acks, status, _bad = signals.read_journal(paths.acks_jsonl)
        if status == signals.STATUS_PARTIAL:
            notes.append(summaries.PARTIAL_JOURNAL_LINE % JOURNALS[2])
        open_items = signals.open_signals(paths, [item for item in acks if isinstance(item, dict)])
    except OSError as exc:
        raise ToolError("io_error", "cannot read the journals: %s" % (exc.strerror or exc)) from None
    return records, open_items, notes


def load_config(paths: desk_paths.DeskPaths) -> desk_config.DeskConfig:
    if not os.path.isfile(paths.desk_toml):
        raise ToolError("config_missing", "desk.toml missing at %s: render it with desk_init.py first" % paths.root)
    try:
        return desk_config.load_desk_config(paths.desk_toml)
    except desk_config.DeskConfigError as exc:
        raise ToolError("config_invalid", str(exc)) from None


def load_calendar(paths: desk_paths.DeskPaths) -> Optional[calendar_events.CalendarFile]:
    try:
        return calendar_events.load_calendar_if_present(paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        raise ToolError("calendar_invalid", str(exc)) from None


def brief_slot(manifest: routines.Manifest, profile: str, today: datetime.date,
               now: datetime.datetime) -> Optional[str]:
    """The latest ``eod-brief`` slot of ``profile`` at or before ``now`` (O15: the audit counts through it), or
    ``None`` before it (a hand run: the slots due by ``now``)."""
    routine = manifest.by_id(BRIEF_ROUTINE)
    if routine is None:
        return None
    passed = [slot for slot in routine.slots(profile) if routines.slot_instant(today, slot) <= now]
    return passed[-1] if passed else None


def day_audit(paths: desk_paths.DeskPaths, profile: str, today: datetime.date, now: datetime.datetime,
              extra: Dict[datetime.date, str]) -> Tuple[Optional[routines.DayAudit], Optional[summaries.Artifact],
                                                         Dict[str, Any], List[str]]:
    """``(audit, problem, routines payload, notes)``: the day audit of the other routines (module notes)."""
    payload: Dict[str, Any] = {"profile": profile, "through_et": None, "counts": None, "audit": None, "ids": []}
    try:
        manifest = routines.load_manifest(manifest_file())
        manifest.routines_for(profile)
    except (routines.ManifestError, ValueError):
        problem = summaries.Artifact(summaries.LABEL_MANIFEST, routines.MANIFEST_REL, summaries.ARTIFACT_INVALID,
                                     present=True, why=MANIFEST_WHY)
        return None, problem, payload, []
    payload["ids"] = list(manifest.routine_ids)
    try:
        records, status, _bad = signals.read_journal(heartbeat.history_path(paths))
    except OSError as exc:
        raise ToolError("io_error", "cannot read %s: %s" % (HISTORY_REL, exc.strerror or exc)) from None
    notes = [summaries.PARTIAL_JOURNAL_LINE % HISTORY_REL] if status == signals.STATUS_PARTIAL else []
    through = brief_slot(manifest, profile, today, now)
    audit = routines.audit_day(manifest, profile, records, today, now, extra, exclude=(BRIEF_ROUTINE,),
                               through_et=through)
    payload.update(through_et=through, audit=audit.as_dict(),
                   counts={"expected": len(audit.expected), "ran": len(audit.ran), "late": len(audit.late),
                           "missed": len(audit.missed), "pending": len(audit.pending)})
    return audit, None, payload, notes


def incident_entry(name: str, routine_ids: Sequence[str]) -> Tuple[Optional[str], Optional[str]]:
    """``(routine id, what)`` of an incident file's name (without its stamp and ``.md``), or ``(None, None)`` for a
    name that is not one ``routine.py`` writes: a routine id of the manifest then ``message``, ``crash`` or a step slug
    (optionally ``-withheld``), or ``dirty-checkout``."""
    if name == DIRTY_CHECKOUT_NAME:
        return None, "dirty checkout"
    for routine_id in sorted(routine_ids, key=len, reverse=True):
        if not name.startswith(routine_id + "-"):
            continue
        rest = name[len(routine_id) + 1:]
        if rest in INCIDENT_WORDS:
            return routine_id, INCIDENT_WORDS[rest]
        withheld = rest.endswith("-withheld")
        slug = rest[:-len("-withheld")] if withheld else rest
        if INCIDENT_SLUG_RE.match(slug) and slug.split("-", 1)[0] in routines.STEP_TOOLS:
            return routine_id, slug + (" withheld" if withheld else "")
    return None, None


def todays_incidents(paths: desk_paths.DeskPaths, today: datetime.date,
                     routine_ids: Sequence[str]) -> List[Dict[str, Any]]:
    """The incident files whose stamp falls on the ET date ``today``, in name (stamp) order (module notes)."""
    try:
        names = sorted(os.listdir(paths.incidents_dir))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ToolError("io_error", "cannot list incidents/: %s" % (exc.strerror or exc)) from None
    found: List[Dict[str, Any]] = []
    for name in names:
        match = INCIDENT_FILE_RE.match(name)
        if match is None:
            continue
        try:
            stamp = datetime.datetime.strptime(match.group("stamp"), INCIDENT_STAMP_FORMAT)
        except ValueError:
            continue
        if market_clock.to_et(stamp.replace(tzinfo=datetime.timezone.utc)).date() != today:
            continue
        routine_id, what = incident_entry(match.group("name"), routine_ids)
        # a file of another name is counted, its name never repeated (it is not a name the desk wrote)
        rel = paths.relative(os.path.join(paths.incidents_dir, name)) if what is not None else None
        found.append({"file": rel, "routine": routine_id, "what": what})
    return found


def held_symbols(item: summaries.Artifact) -> Tuple[List[str], List[str]]:
    """``(held, short calls)``: the underlyings of the snapshot's open legs, and of its open short calls."""
    if not item.ok:
        return [], []
    legs = [leg for leg in item.document.get("legs") or [] if leg.get("status") == "open" and leg.get("underlying")]
    held = sorted({str(leg["underlying"]) for leg in legs})
    short_calls = sorted({str(leg["underlying"]) for leg in legs if leg.get("occ") and leg.get("right") == "CALL"
                          and leg.get("open_quantity") is not None and leg["open_quantity"] < 0})
    return held, short_calls


def record_entry(item: summaries.Artifact, expected: datetime.date) -> Dict[str, Any]:
    day = expected if item.ok else item.found_date
    return {"label": item.label, "path": item.path, "status": item.status,
            "date": day.isoformat() if day is not None else None}


# --------------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------------


def compose(args: Any, paths: desk_paths.DeskPaths, now: datetime.datetime) -> Tuple[summaries.Summary, Dict[str, Any],
                                                                                     desk_config.DeskConfig]:
    """The summary and the payload fields beyond the message (module notes)."""
    cfg = load_config(paths)
    cal = load_calendar(paths)
    extra = calendar_events.extra_closures(cal)
    today = market_clock.to_et(now).date()
    delay = cfg.market_data.feed_delay_min
    recon_record = read_record(paths, summaries.LABEL_RECON, recon.recon_path(paths, today), today, RECON_SUFFIX)
    docket_path = os.path.join(paths.reports_dir, "expiry", "%s%s" % (today.isoformat(), DOCKET_SUFFIX))
    docket = read_record(paths, summaries.LABEL_DOCKET, docket_path, today, DOCKET_SUFFIX)
    records, open_items, notes = read_journals(paths)
    extras: Dict[str, Any] = {"open_signals": len(open_items), "signals_today": None, "routines": None,
                              "config_changed": None, "calendar_findings": None, "incidents": None}
    if not args.brief:
        previous = market_clock.previous_trading_day(today, extra)
        doctor_record = read_record(paths, summaries.LABEL_DOCTOR, doctor.doctor_path(paths), today)
        levels_record = read_record(paths, summaries.LABEL_LEVELS,
                                    os.path.join(paths.levels_dir, "%s%s" % (previous.isoformat(), JSON_SUFFIX)),
                                    previous, JSON_SUFFIX)
        early = None
        if market_clock.is_early_close(today, extra):
            early = market_clock.session_close_et(today, extra_holidays=extra)
        inputs = summaries.MorningInputs(et_date=today, now=now, doctor=doctor_record, recon=recon_record, docket=docket,
                                         levels=levels_record, open_signals=open_items, delay_min=delay,
                                         early_close_et=early, notes=notes)
        summary = summaries.morning_sheet(inputs)
        read = [(doctor_record, today), (recon_record, today), (docket, today), (levels_record, previous)]
    else:
        snapshot = read_record(paths, summaries.LABEL_BOOK, book.snapshot_path(paths, today), today, JSON_SUFFIX)
        audit, problem, routines_payload, history_notes = day_audit(paths, cfg.desk.cadence_profile, today, now, extra)
        incidents = todays_incidents(paths, today, routines_payload.pop("ids"))
        held, short_calls = held_symbols(snapshot)
        findings = calendar_events.freshness(cal, now, calendar_events.DEFAULT_MAX_AGE_DAYS, held, short_calls)
        try:
            changes = exit_check.config_changes(paths)
        except OSError as exc:
            raise ToolError("io_error", "cannot read the desk files: %s" % (exc.strerror or exc)) from None
        inputs_brief = summaries.BriefInputs(
            et_date=today, now=now, book=snapshot, recon=recon_record, docket=docket, signal_records=records,
            open_signals=open_items, delay_min=delay, audit=audit, audit_problem=problem, config_changes=changes,
            calendar_findings=len(findings), notes=notes + history_notes,
            incidents=[(item["routine"], item["what"]) for item in incidents])
        summary = summaries.eod_brief(inputs_brief)
        notes = notes + history_notes
        read = [(snapshot, today), (recon_record, today), (docket, today)]
        extras.update(signals_today=summaries.signals_today(records, today), routines=routines_payload,
                      config_changed=[dict(change) for change in changes], calendar_findings=len(findings),
                      incidents=incidents)
    expected = {id(item): day for item, day in read}
    extras["artifacts"] = [record_entry(item, day) for item, day in read]
    extras["unavailable"] = [record_entry(item, expected.get(id(item), today)) for item in summary.unavailable]
    extras["warnings"] = list(notes)
    return summary, extras, cfg


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    now = args.now or state.utc_now()
    try:
        try:
            paths = desk_paths.for_root(args.desk_root)
        except desk_paths.DeskPathsError as exc:
            raise ToolError("desk_root", str(exc)) from None
        summary, extras, cfg = compose(args, paths, now)
    except ToolError as exc:
        return fail(args, version, now, exc.code, exc.message, exc.exit_code)
    except market_clock.TableExpiredError as exc:
        return fail(args, version, now, "holiday_table_expired", "holiday table expired: %s" % exc,
                    contract.EXIT_UNAVAILABLE)
    lint = summary.lint()
    if not lint.ok:
        codes = sorted({finding.code for finding in lint.findings}) or ["a finding"]
        return fail(args, version, now, "lint_failed", "the %s failed alert_lint and was not printed: %s"
                    % ("brief" if args.brief else "morning sheet", ", ".join(codes)), contract.EXIT_ERROR)
    exit_code = summary.exit_code
    if args.json:
        payload: Dict[str, Any] = {
            "mode": summary.mode, "et_date": summary.et_date, "message": summary.text,
            "emitted": summary.emitted_document(),
            "lint": {"ok": lint.ok, "problems": list(lint.problems), "numbers_checked": lint.numbers_checked},
            "unavailable": extras["unavailable"], "artifacts": extras["artifacts"],
            "open_signals": extras["open_signals"], "signals_today": extras["signals_today"],
            "routines": extras["routines"], "config_changed": extras["config_changed"],
            "calendar_findings": extras["calendar_findings"], "incidents": extras["incidents"],
            "warnings": extras["warnings"], "exit_code": exit_code,
        }
        envelope = contract.envelope(TOOL, version, payload, source=quote_cache.source_label(cfg),
                                     delay_min=summary.delay_min, generated_at=now,
                                     status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(envelope))
        return exit_code
    print(summary.text)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
