#!/usr/bin/env python3
"""signal_journal.py: each signal's outcome from the desk's own records, counts only (roadmap 3.1).

Usage::

    python3 scripts/signal_journal.py [--date YYYY-MM-DD | --week] [--close] [--json] [--schema] [--desk-root DIR]
        [--now ISO-8601-UTC] [--offline] [--timeout S]

Reads ``journal/signals.jsonl``, ``state/acks.jsonl`` and the end-of-day snapshots of record (``data/book/``) through
``aegis_desk.journal``; no market data. For each signal posted in the window (``--week``, the default: the ET week so
far, Monday to today; ``--date``, one ET day) it prints one fact line: when it was posted, its trade, structure,
verdict and rules, the owner's mark (acknowledged, marked acted, snoozed or open) and the group's P/L at the mid in
each end-of-day snapshot from that day on (UNAVAILABLE for a trading day without a usable snapshot). Then the counts,
in fixed words: one line per rule and one for all rules, ``TP50: 3 signals this week; 2 acknowledged, 1 marked
acted, 0 snoozed``. No sum, average or difference of money across signals, no rate and no ranking.

``--close`` (the ``journal-close`` routine) writes today's facts to ``journal/outcomes/<ET date>.json``, its one
writer, under the desk lock, and prints nothing to post. Without it nothing is written.

Exit codes: 0 the journal (or the day closed); 20 a signal's mark is UNAVAILABLE on a trading day of the window, or a
journal does not read cleanly; 1 a desk root that cannot be used; 2 usage. ``--json`` payload: as ``--schema``
describes; ``message`` and ``as_of`` carry the printed text.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="signal_journal")

import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from decimal import Decimal  # noqa: E402
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

from aegis_desk import alerts, calendar_events, desk_paths, exit_rules, journal, market_clock, signals, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "signal_journal"
DESCRIPTION = ("each signal's outcome from the desk's own records: when it was posted, the owner's mark and the "
               "group's end-of-day P/L at the mid; counts per rule in fixed words, never a money total; --close writes "
               "the day's facts")
NETWORK_ACCESS = "none"
OUTCOMES_SUBDIR: str = "outcomes"
OUTPUT: Dict[str, Any] = {
    "verdict": "ok | unavailable",
    "exit_code": "0 | 20",
    "message": "string | null (null with --close)",
    "as_of": "RFC 3339 (the run instant)",
    "start": "YYYY-MM-DD",
    "end": "YYYY-MM-DD",
    "signals": [{"signal_id": "string", "trade_id": "string", "structure": "string", "verdict": "string",
                 "rule_ids": ["string"], "posted_at": "RFC 3339", "owner_mark": "acknowledged | marked acted | snoozed "
                 "| open", "owner_mark_utc": "string | null",
                 "marks": [{"et_date": "YYYY-MM-DD", "unrealized_pl_mid": "decimal string | null",
                            "reason": "string | null"}]}],
    "counts": {"RULE_ID | all rules": {"signals": "integer", "acknowledged": "integer", "acted": "integer",
                                       "snoozed": "integer"}},
    "written": "journal/outcomes/<ET date>.json | null",
    "warnings": ["string"],
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None, help="one ET day instead of the week so far")
    parser.add_argument("--week", action="store_true", help="the ET week so far, Monday to today (the default)")
    parser.add_argument("--close", action="store_true",
                        help="write today's facts to journal/outcomes/<ET date>.json (the journal-close routine)")
    return parser


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.status_for(exit_code),
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return exit_code


def snapshot_reader(paths: desk_paths.DeskPaths) -> journal.SnapshotReader:
    def read(day: datetime.date) -> Tuple[str, Optional[Mapping[str, Any]]]:
        path = os.path.join(paths.book_dir, "%s.json" % day.isoformat())
        raw, status = state.read_json(path)
        if status == state.STATUS_MISSING:
            return "missing", None
        if status != state.STATUS_OK:
            return "unreadable", None
        try:
            return "ok", exit_rules.book_from_json(raw)
        except exit_rules.BookFormatError:
            return "unreadable", None
    return read


def fact_line(fact: journal.SignalFact, emitted: List[str]) -> str:
    posted = fact.posted_at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rules = " (%s)" % ", ".join(fact.rule_ids) if fact.rule_ids else ""
    mark = fact.owner_mark if fact.owner_mark_utc is None else "%s %s" % (fact.owner_mark, fact.owner_mark_utc[:16]
                                                                          .replace("T", " ") + " UTC")
    marks = []
    for item in fact.marks:
        if item.value is None:
            marks.append("%s UNAVAILABLE (%s)" % (item.et_date.isoformat(), item.reason))
        else:
            value = item.value.quantize(Decimal("0.01"))
            text = alerts.fmt_money(value, emitted)
            marks.append("%s %s%s" % (item.et_date.isoformat(), "+" if value > 0 else "", text))
    return "%s | %s %s | %s%s posted %s | %s | P/L at the mid, end of day: %s" % (
        fact.signal_id, fact.trade_id, fact.structure, fact.verdict.replace("_", " "), rules, posted, mark,
        "; ".join(marks) if marks else "none yet")


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    if args.date is not None and args.week:
        parser.error("--date and --week are two windows: give one")
    if args.close and (args.date is not None or args.week):
        parser.error("--close closes today: give it alone")
    now = args.now or state.utc_now()
    today = market_clock.to_et(now).date()
    if args.date is not None:
        try:
            start = end = datetime.date.fromisoformat(args.date)
        except ValueError:
            parser.error("--date must be a date YYYY-MM-DD")
        if end > today:
            parser.error("--date %s is after today's ET date %s" % (args.date, today.isoformat()))
        period = "on %s" % start.isoformat()
    elif args.close:
        start = end = today
        period = "on %s" % today.isoformat()
    else:
        start, end = today - datetime.timedelta(days=today.weekday()), today
        period = "this week"
    try:
        paths = desk_paths.for_root(args.desk_root)
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc), contract.EXIT_ERROR)
    try:
        cal = calendar_events.load_calendar_if_present(paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        return fail(args, version, now, "calendar_invalid", str(exc), contract.EXIT_ERROR)
    warnings: List[str] = []
    signal_records, signal_status, _bad = signals.read_journal(os.path.join(paths.journal_dir, signals.SIGNALS_FILE))
    ack_records, ack_status, _bad_acks = signals.read_journal(paths.acks_jsonl)
    for name, status in (("journal/signals.jsonl", signal_status), ("state/acks.jsonl", ack_status)):
        if status == signals.STATUS_PARTIAL:
            warnings.append("%s: unreadable lines skipped" % name)
    items = journal.facts(signal_records, ack_records, snapshot_reader(paths), start, end,
                          calendar_events.extra_closures(cal))
    table = journal.counts(items)
    gaps = any(mark.value is None and mark.reason != journal.REASON_CLOSED for fact in items for mark in fact.marks
               if mark.et_date < today)
    unavailable = gaps or bool(warnings)
    exit_code = contract.EXIT_UNAVAILABLE if unavailable else contract.EXIT_QUIET
    written: Optional[str] = None
    if args.close:
        target = os.path.join(paths.journal_dir, OUTCOMES_SUBDIR, "%s.json" % today.isoformat())
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with state.locked(paths.lock_path, timeout_s=5.0):
                state.atomic_write_json(target, {"schema": 1, "et_date": today.isoformat(), "closed_utc": now,
                                                 "signals": [fact.as_dict() for fact in items],
                                                 "counts": {name: count.as_dict() for name, count in table.items()}})
        except (OSError, state.LockBusy) as exc:
            return fail(args, version, now, "io_error", "cannot write %s: %s" % (paths.relative(target), exc),
                        contract.EXIT_ERROR)
        written = paths.relative(target)
        exit_code = contract.EXIT_QUIET
    emitted: List[str] = []
    lines = ["SIGNAL JOURNAL %s to %s | each signal's facts from the desk's own records; totals are counts only"
             % (start.isoformat(), end.isoformat())]
    lines.extend(fact_line(fact, emitted) for fact in items)
    if not items:
        lines.append("no signal was posted %s" % period)
    lines.extend(journal.count_line(name, count, period) for name, count in table.items())
    lines.extend("warning: %s" % warning for warning in warnings)
    message = None if args.close else "\n".join(lines)
    if args.json:
        payload = {"verdict": "unavailable" if unavailable and not args.close else "ok", "exit_code": exit_code,
                   "message": message, "as_of": now, "start": start.isoformat(), "end": end.isoformat(),
                   "signals": [fact.as_dict() for fact in items],
                   "counts": {name: count.as_dict() for name, count in table.items()}, "written": written,
                   "warnings": warnings}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    print(message if message is not None else "journal closed for %s: %s" % (today.isoformat(), written))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
