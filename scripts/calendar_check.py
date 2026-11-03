#!/usr/bin/env python3
"""calendar_check.py: the freshness lint of research/calendar.toml against the held symbols.

Usage::

    python3 scripts/calendar_check.py [--max-age-days N] [--json] [--schema] [--desk-root DIR] [--offline]
        [--now ISO-8601-UTC]

Reads ``research/calendar.toml`` strictly (``aegis_desk.calendar_events``), ``positions.csv`` for the
held symbols (the underlyings of the open rows) and the short-call underlyings (``aegis_desk.grouping``),
and prints every finding of ``calendar_events.freshness``: ``STALE`` (an entry checked more than
``--max-age-days`` ago, default 7), ``MISSING`` (a held symbol absent from the file), ``UNKNOWN_DIVIDEND``
(a short-call underlying whose dividend is absent or ``unknown``: never zero) and ``PAST`` (a dated event
that has passed). No network, no market data, nothing written. Exit 0 clean, 20 when anything is stale,
missing, unknown or past, when ``research/calendar.toml`` does not exist (every event is then unknown) or
when ``positions.csv`` has file errors (held symbols unknown); 1 on a parse error of the calendar, a
missing ``positions.csv`` or an unusable desk root; 2 on usage. ``--json`` payload: ``{"verdict",
"exit_code", "today", "max_age_days", "calendar_path", "calendar_sha256", "listed_symbols", "held_symbols",
"short_call_symbols", "findings": [{"kind", "symbol", "message"}], "counts", "warnings"}``.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="calendar_check")

import datetime  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import calendar_events, desk_paths, grouping, market_clock, positions_csv, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "calendar_check"
DESCRIPTION = ("freshness lint of research/calendar.toml: STALE, MISSING, UNKNOWN_DIVIDEND and PAST findings against the held "
               "symbols and the short-call underlyings; no network, nothing written")
NETWORK_ACCESS = "none"
VERDICT_CLEAN = "clean"
VERDICT_UNAVAILABLE = "unavailable"
DIGITS_RE = re.compile(r"^\d+$")
OUTPUT: Dict[str, Any] = {
    "verdict": "clean | unavailable",
    "exit_code": "0 | 20",
    "today": "YYYY-MM-DD (ET date of --now)",
    "max_age_days": "integer",
    "calendar_path": "research/calendar.toml (root-relative)",
    "calendar_sha256": "hex",
    "listed_symbols": ["symbols in the file"],
    "held_symbols": ["underlyings of the open rows"],
    "short_call_symbols": ["underlyings carrying an open short call"],
    "findings": [{"kind": " | ".join(calendar_events.FINDING_KINDS), "symbol": "string | null", "message": "string"}],
    "counts": {kind: "integer" for kind in calendar_events.FINDING_KINDS},
    "warnings": ["string"],
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--max-age-days", metavar="N", default=str(calendar_events.DEFAULT_MAX_AGE_DAYS),
                        help="an entry checked more than N days ago is STALE (default %d)" % calendar_events.DEFAULT_MAX_AGE_DAYS)
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> int:
    text = str(args.max_age_days).strip()
    if not DIGITS_RE.fullmatch(text):
        parser.error("--max-age-days must be a non-negative integer number of days (got %r)" % args.max_age_days)
    return int(text)


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    status = contract.status_for(exit_code)
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, status, generated_at=now)))
    elif exit_code == contract.EXIT_UNAVAILABLE:
        print("UNAVAILABLE: %s" % message)
    else:
        print("error: %s" % message)
    return exit_code


def symbols_text(symbols: Sequence[str]) -> str:
    return ", ".join(symbols) if symbols else "none"


def days_text(days: int) -> str:
    return "%d day%s" % (days, "" if days == 1 else "s")


def counts_text(counts: Dict[str, int]) -> str:
    parts = ["%d %s" % (counts[kind], kind) for kind in calendar_events.FINDING_KINDS if counts[kind]]
    return ", ".join(parts) if parts else "none"


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    max_age_days = validate(parser, args)
    now = args.now or state.utc_now()
    try:
        paths = desk_paths.for_root(args.desk_root)
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc), contract.EXIT_ERROR)
    try:
        cal = calendar_events.load_calendar_if_present(paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        return fail(args, version, now, "calendar_invalid", str(exc), contract.EXIT_ERROR)
    if cal is None:
        return fail(args, version, now, "calendar_missing",
                    "no research/calendar.toml at %s: every earnings date and dividend is unknown (never zero); "
                    "write the file to run the lint" % paths.calendar_toml, contract.EXIT_UNAVAILABLE)
    try:
        session = market_clock.session_at(now, extra_holidays=cal.extra_closures())
    except market_clock.TableExpiredError as exc:
        return fail(args, version, now, "holiday_table_expired", "holiday table expired: %s" % exc,
                    contract.EXIT_UNAVAILABLE)
    today = session.et_date
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=today)
    except FileNotFoundError:
        return fail(args, version, now, "positions_missing", "positions.csv not found at %s" % paths.positions_csv,
                    contract.EXIT_ERROR)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot read positions.csv: %s" % exc, contract.EXIT_ERROR)

    warnings: List[str] = list(cal.warnings)
    held: List[str] = []
    short_calls: List[str] = []
    incomplete = False
    if positions.file_errors:
        incomplete = True
        warnings.append("positions.csv has file errors (%s): held symbols unknown; MISSING and UNKNOWN_DIVIDEND not evaluated"
                        % "; ".join(str(error) for error in positions.file_errors))
    else:
        held = sorted({row.underlying for row in positions.open_rows})
        groups = grouping.group_positions(positions.rows)
        short_calls = sorted({leg.underlying for group in groups for leg in group.open_option_legs
                              if leg.is_call and leg.is_short})
        if positions.quarantined:
            warnings.append("%d group(s) quarantined (%s): their symbols are not in the held set"
                            % (len(positions.quarantined), ", ".join(sorted(positions.quarantined))))
    findings = calendar_events.freshness(cal, now, max_age_days, held, short_calls)
    counts = {kind: sum(1 for finding in findings if finding.kind == kind) for kind in calendar_events.FINDING_KINDS}
    exit_code = contract.EXIT_UNAVAILABLE if findings or incomplete else contract.EXIT_QUIET
    verdict = VERDICT_UNAVAILABLE if exit_code else VERDICT_CLEAN
    listed = sorted(cal.symbols)

    if args.json:
        payload: Dict[str, Any] = {
            "verdict": verdict,
            "exit_code": exit_code,
            "today": today.isoformat(),
            "max_age_days": max_age_days,
            "calendar_path": paths.relative(paths.calendar_toml),
            "calendar_sha256": cal.sha256,
            "listed_symbols": listed,
            "held_symbols": held,
            "short_call_symbols": short_calls,
            "findings": [{"kind": finding.kind, "symbol": finding.symbol, "message": finding.message} for finding in findings],
            "counts": counts,
            "warnings": warnings,
        }
        body = contract.envelope(TOOL, version, payload, generated_at=now,
                                 status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(body))
        return exit_code

    lines = ["calendar check %s: %s (sha256 %s), max age %s" % (
        "%s %s" % (("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[today.weekday()], today.isoformat()),
        paths.relative(paths.calendar_toml), cal.sha256[:12], days_text(max_age_days))]
    for finding in findings:
        lines.append("%s %s: %s" % (finding.kind, finding.symbol or "-", finding.message))
    for warning in warnings:
        lines.append("warning: %s" % warning)
    held_text = "held %s (short calls %s)" % (symbols_text(held), symbols_text(short_calls))
    if incomplete:
        held_text = "held symbols unknown (positions.csv file errors)"
    if exit_code:
        lines.append("UNAVAILABLE: %d finding%s (%s); %s; within %s as of %s" % (
            len(findings), "" if len(findings) == 1 else "s", counts_text(counts), held_text, days_text(max_age_days),
            today.isoformat()))
    else:
        lines.append("CLEAN: research/calendar.toml lists %s; %s; 0 findings within %s as of %s" % (
            symbols_text(listed), held_text, days_text(max_age_days), today.isoformat()))
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
