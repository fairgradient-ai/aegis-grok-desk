#!/usr/bin/env python3
"""decision_calendar.py: the owner's dated decision points over the coming weeks (roadmap 2.11).

Usage::

    python3 scripts/decision_calendar.py [--days N] [--json] [--schema] [--desk-root DIR] [--now ISO-8601-UTC]
        [--offline] [--timeout S]

Reads ``desk.toml`` (the broker's exercise cutoff), ``positions.csv`` and ``research/calendar.toml``; no market data
and nothing written. Lists, in date order from today's ET date to ``--days`` ahead (default 45), each open option
leg's act-by instant and its contract's last trading day, and each held symbol's ex-dividend and earnings dates with
their status and source (``aegis_desk.decision_calendar``). A held symbol whose date the calendar holds as unknown, or
does not hold, is UNAVAILABLE: a date nobody sourced is never guessed. ``--offline`` and ``--timeout`` are accepted for
the shared contract and change nothing.

Exit codes: 0 every held symbol's dates known; 20 a gap (UNAVAILABLE), or no calendar; 1 a desk file that cannot be
used; 2 usage. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="decision_calendar")

import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, Optional, Sequence  # noqa: E402

from aegis_desk import calendar_events, decision_calendar, desk_config, desk_paths, market_clock, positions_csv  # noqa: E402
from aegis_desk import state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "decision_calendar"
DESCRIPTION = ("the owner's dated decision points in date order: act-by instants and last trading days of the open "
               "option legs, and the held symbols' ex-dividend and earnings dates with their sources; an unknown date "
               "is UNAVAILABLE")
NETWORK_ACCESS = "none"
OUTPUT: Dict[str, Any] = {
    "verdict": "ok | unavailable",
    "exit_code": "0 | 20",
    "start": "YYYY-MM-DD (today's ET date)",
    "end": "YYYY-MM-DD",
    "points": [{"date": "YYYY-MM-DD", "time_et": "HH:MM | null", "kind": "act_by | last_trading_day | ex_dividend | "
                "earnings", "symbol": "string", "detail": "string", "status": "confirmed | estimated | null",
                "source": "string | null"}],
    "gaps": [{"symbol": "string", "kind": "ex_dividend | earnings", "reason": "string"}],
    "message": "string (the text printed, for a routine's post)",
    "as_of": "RFC 3339 (the run instant)",
}
KIND_WORDS = {decision_calendar.KIND_ACT_BY: "act by", decision_calendar.KIND_LAST_TRADING_DAY: "last trading day",
              decision_calendar.KIND_EX_DIVIDEND: "ex-dividend", decision_calendar.KIND_EARNINGS: "earnings"}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--days", metavar="N", type=int, default=decision_calendar.DEFAULT_HORIZON_DAYS,
                        help="calendar days ahead of today to cover (1-%d, default %d)"
                        % (decision_calendar.MAX_HORIZON_DAYS, decision_calendar.DEFAULT_HORIZON_DAYS))
    return parser


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.status_for(exit_code),
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return exit_code


def point_line(point: decision_calendar.Point) -> str:
    when = "%s %s" % (point.date.strftime("%a"), point.date.isoformat())
    if point.time_et is not None:
        when += " %s ET" % point.time_et
    text = "%s | %s | %s" % (when, KIND_WORDS[point.kind], point.detail)
    if point.source is not None:
        text += " (%s; %s)" % (point.status, point.source)
    return text


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    if not 1 <= args.days <= decision_calendar.MAX_HORIZON_DAYS:
        parser.error("--days must be 1-%d" % decision_calendar.MAX_HORIZON_DAYS)
    now = args.now or state.utc_now()
    try:
        paths = desk_paths.for_root(args.desk_root)
        if not os.path.isfile(paths.desk_toml):
            raise FileNotFoundError(paths.desk_toml)
        cfg = desk_config.load_desk_config(paths.desk_toml)
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc), contract.EXIT_ERROR)
    except FileNotFoundError:
        return fail(args, version, now, "config_missing", "desk.toml not found under the desk root", contract.EXIT_ERROR)
    except desk_config.DeskConfigError as exc:
        return fail(args, version, now, "config_invalid", str(exc), contract.EXIT_ERROR)
    try:
        cal = calendar_events.load_calendar_if_present(paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        return fail(args, version, now, "calendar_invalid", str(exc), contract.EXIT_ERROR)
    today = market_clock.to_et(now).date()
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=today)
    except FileNotFoundError:
        return fail(args, version, now, "positions_missing", "positions.csv not found at %s" % paths.positions_csv,
                    contract.EXIT_ERROR)
    if positions.file_errors:
        return fail(args, version, now, "positions_invalid", "positions.csv cannot be used: %s"
                    % "; ".join(str(error) for error in positions.file_errors), contract.EXIT_ERROR)
    result = decision_calendar.build(positions.rows, cal, now, cfg.owner.broker_exercise_cutoff_et, args.days)
    unavailable = bool(result.gaps)
    exit_code = contract.EXIT_UNAVAILABLE if unavailable else contract.EXIT_QUIET
    lines = ["DECISION CALENDAR | %s to %s | your dated decision points" % (result.start.isoformat(),
                                                                             result.end.isoformat())]
    lines.extend(point_line(point) for point in result.points)
    if not result.points:
        lines.append("no dated decision point in the window")
    lines.extend("UNAVAILABLE | %s %s date | %s" % (gap.symbol, KIND_WORDS[gap.kind], gap.reason) for gap in result.gaps)
    if args.json:
        payload = {"verdict": "unavailable" if unavailable else "ok", "exit_code": exit_code,
                   "start": result.start.isoformat(), "end": result.end.isoformat(),
                   "points": [point.as_dict() for point in result.points],
                   "gaps": [gap.as_dict() for gap in result.gaps], "message": "\n".join(lines), "as_of": now}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
