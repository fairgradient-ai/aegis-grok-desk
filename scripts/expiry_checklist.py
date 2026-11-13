#!/usr/bin/env python3
"""expiry_checklist.py: the expiry-week docket, the T-1 preview and the Final Call sheet.

Usage::

    python3 scripts/expiry_checklist.py (--week | --tomorrow | --final-call) [--fixture-set NAME] [--json]
        [--schema] [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Stage 1, European-only (``aegis_desk.expiry``, the same functions as ``assignment_watch.py``). Loads
``positions.csv`` and ``research/calendar.toml``, groups the legs, reads ONE quote batch through
``quote_cache.get_quotes`` (never chains) and lists, per open option leg: ``--week`` every contract whose
last trading day falls in the current ET week (Monday to Sunday), ``--tomorrow`` the contracts whose last
trading day is the next trading day (the T-1 preview), ``--final-call`` the contracts on their last trading
day (the expiry-day sheet). Every line carries the structure, the settlement type (``pm`` or ``am`` from the
calendar's ``am_settled_roots``), the last trading day, the implication (shares called away for a short
call, cash needed for a short put as ``K x 100 x contracts``, the delivery of a long put, the cash of a long
call) and the act-by time in ET and UTC (``aegis_desk.expiry.leg_act_by``): for a short leg the earlier of the
session close of its last trading day (16:00 ET; 13:00 ET on an early close) and the owner's broker cutoff, its text
ending ``; the session close: a short option is closed only while the market trades`` when the close governs; for a
long leg the broker cutoff on the last trading day, on an early-close day the earlier of that cutoff and the 13:00 ET
close, marked ``early close ...: confirm your broker's cutoff``. The Final Call header names the earliest act-by of
the listed legs when they differ (``; the earliest act-by of the listed contracts (each line names its own)``);
legs on their last or next-to-last trading day add the dividend and pin tiers and, for verticals, the ``never
into expiration with the stock between the strikes`` line. Standard data footer. Exit 0 nothing due, 10 when items are
listed, 20 when a quarantined group exists or a listed item is UNAVAILABLE (quotes or dividend unknown);
1 on configuration or file errors (``io_error`` also when the docket record cannot be written), 2 on usage. Never
writes ``positions.csv``. ``--json`` payload: ``{"mode", "verdict", "exit_code", "today", "next_trading_day",
"week_start", "week_end", "legs": [the listed legs], "expired", "quarantined", "quote_check", "counts", "session",
"warnings", "fixture_set", "message", "as_of"}``: ``message`` is the human text exactly (without the final newline)
and ``as_of`` the instant of its footer line (the newest graded quote, or the run instant when nothing was quoted),
so a routine can post the text without running the tool twice.

The docket record: every run that reaches the sheet (exit 0, 10 or 20; never an error exit) writes the success
envelope ``--json`` prints to ``reports/expiry/<ET date>.<mode>.json`` (``week``, ``tomorrow`` or ``final-call``)
atomically, in human mode too; the latest run of a mode on an ET date replaces its file. The Morning Desk Sheet and
the EOD brief read ``reports/expiry/<today>.week.json`` (PR 2d decision D1-12).
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="expiry_checklist")

import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import calendar_events, desk_config, desk_paths, expiry, grouping, market_clock, positions_csv, quote_cache, quote_qc, state  # noqa: E402
from aegis_desk.market_types import Quote  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "expiry_checklist"
DESCRIPTION = ("expiry-week docket (--week), T-1 preview (--tomorrow) or Final Call sheet (--final-call) for the open "
               "option legs: settlement, implications, act-by times (a short leg's session close or the owner's broker "
               "cutoff), dividend and pin tiers")
NETWORK_ACCESS = ("GET https://data.alpaca.markets/v2/stocks/quotes/latest and /v1beta1/options/quotes/latest only "
                  "(through the quote cache); none with --offline")
LABEL_NO_QUOTE = "no quote"
EARLIEST_NOTE = "the earliest act-by of the listed contracts (each line names its own)"
"""The Final Call header's note when the listed legs' act-bys differ (a short leg's session close, a long leg's broker
cutoff): the header names the earliest."""
MODE_FLAGS = (("--week", expiry.MODE_WEEK), ("--tomorrow", expiry.MODE_TOMORROW), ("--final-call", expiry.MODE_FINAL_CALL))
OUTPUT: Dict[str, Any] = {
    "mode": " | ".join(expiry.MODES),
    "verdict": "fired | unavailable | quiet",
    "exit_code": "10 | 20 | 0",
    "today": "YYYY-MM-DD (ET date of --now)",
    "next_trading_day": "YYYY-MM-DD",
    "week_start": "YYYY-MM-DD (Monday)",
    "week_end": "YYYY-MM-DD (Sunday)",
    "legs": [expiry.LEG_OUTPUT],
    "expired": [{"row_number": "integer", "symbol": "string", "expiration": "YYYY-MM-DD"}],
    "quarantined": {"GROUP": ["row or group error strings"]},
    "quote_check": {"symbols": ["requested"], "quoted": ["served"], "missing": {"SYMBOL": "reason"}, "requests_made": "integer"},
    "counts": {"open_option_legs": "integer", "listed": "integer", "fired": "integer", "unavailable": "integer",
               "expired": "integer", "quarantined_groups": "integer"},
    "session": "market_clock.SessionInfo.as_dict()",
    "warnings": ["string"],
    "fixture_set": "string | null (offline runs)",
    "message": "string: the human text exactly, without the final newline",
    "as_of": "ISO-8601 UTC: the instant of the text's footer line (Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of HH:MM UTC)",
}
RECORD_SUBDIR = "expiry"
"""``reports/expiry/``: the docket records (module notes)."""


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--week", action="store_true", help="every contract whose last trading day is in the current ET week")
    parser.add_argument("--tomorrow", action="store_true", help="the T-1 preview: contracts on their last trading day tomorrow")
    parser.add_argument("--final-call", action="store_true", help="the expiry-day sheet: contracts on their last trading day today")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/alpaca/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> str:
    chosen = [mode for flag, mode in MODE_FLAGS if getattr(args, flag.lstrip("-").replace("-", "_"))]
    if len(chosen) != 1:
        parser.error("exactly one of --week, --tomorrow or --final-call is required")
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    return chosen[0]


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    status = contract.status_for(exit_code)
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, status, generated_at=now)))
    elif exit_code == contract.EXIT_UNAVAILABLE:
        print("UNAVAILABLE: %s" % message)
    else:
        print("error: %s" % message)
    return exit_code


def missing_label(session: market_clock.SessionInfo, prior_close: datetime.datetime, copy: Optional[Quote]) -> str:
    if session.session in (market_clock.SESSION_WEEKEND, market_clock.SESSION_HOLIDAY):
        return quote_qc.LABEL_CLOSED_DAY
    if copy is not None:
        return quote_qc.session_label(session, prior_close, copy.as_of)
    return LABEL_NO_QUOTE


def contracts_text(count: int, tail: str) -> str:
    if count == 1:
        return "1 contract %s" % tail.replace("their", "its")
    return "%d contracts %s" % (count, tail)


def record_path(paths: desk_paths.DeskPaths, et_date: datetime.date, mode: str) -> str:
    """``reports/expiry/<ET date>.<mode>.json``: the docket record of one mode on one ET date."""
    return os.path.join(paths.reports_dir, RECORD_SUBDIR, "%s.%s.json" % (et_date.isoformat(), mode))


def header_line(mode: str, sheet: expiry.ExpirySheet, listed: List[expiry.LegSheet], cutoff_et: datetime.time,
                now: datetime.datetime) -> str:
    today = expiry.weekday_date(sheet.today)
    if mode == expiry.MODE_WEEK:
        span = "%s..%s" % (sheet.week_start.isoformat(), sheet.week_end.isoformat())
        body = contracts_text(len(listed), "due") if listed else "nothing due"
        return "EXPIRY WEEK %s: %s (today %s)" % (span, body, today)
    if mode == expiry.MODE_TOMORROW:
        body = (contracts_text(len(listed), "on their last trading day") if listed
                else "nothing on its last trading day")
        return "T-1 PREVIEW for %s: %s (today %s)" % (expiry.weekday_date(sheet.next_trading_day), body, today)
    if not listed:
        return "FINAL CALL %s: nothing due today" % today
    del cutoff_et, now  # each leg's act-by already applied its rule (side, cutoff, early close) at ``now``
    # Every listed leg is on its last trading day today, but a short leg acts by the session close and a long one by
    # the broker cutoff (FAI-689): the header names the EARLIEST act-by (conservative), the marked one on a tie.
    act = min((leg.act_by for leg in listed), key=lambda item: (item.utc, not item.early_close))
    text = "FINAL CALL %s: %s; act by %s ET (%s)%s" % (today, contracts_text(len(listed), "on their last trading day"),
                                                       act.cutoff_et.strftime("%H:%M"), act.utc_text,
                                                       " [passed]" if act.passed else "")
    if len({leg.act_by.utc for leg in listed}) > 1:
        text += "; " + EARLIEST_NOTE
    early = act.early_close_text
    if early is not None:
        text += "; " + early
    return text


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    mode = validate(parser, args)
    now = args.now or state.utc_now()
    try:
        market = quote_cache.open_market_data(args.desk_root, now=now, offline=args.offline,
                                              fixture_set=args.fixture_set, timeout_s=args.timeout,
                                              user_agent=user_agent(version))
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc), contract.EXIT_ERROR)
    except desk_config.DeskConfigError as exc:
        return fail(args, version, now, "config_invalid", str(exc), contract.EXIT_ERROR)
    except market_clock.TableExpiredError as exc:
        return fail(args, version, now, "holiday_table_expired", "holiday table expired: %s" % exc,
                    contract.EXIT_UNAVAILABLE)
    except quote_cache.MarketDataRefused as exc:
        return fail(args, version, now, exc.status, exc.message, contract.EXIT_UNAVAILABLE)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot prepare the desk runtime directories: %s" % exc,
                    contract.EXIT_ERROR)
    paths = market.paths
    try:
        cal = calendar_events.load_calendar_if_present(paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        return fail(args, version, now, "calendar_invalid", str(exc), contract.EXIT_ERROR)
    extra = calendar_events.extra_closures(cal)
    session = market_clock.session_at(now, extra_holidays=extra)
    prior_close = market_clock.previous_close_utc(now, extra_holidays=extra)
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=session.et_date)
    except FileNotFoundError:
        return fail(args, version, now, "positions_missing", "positions.csv not found at %s" % paths.positions_csv,
                    contract.EXIT_ERROR)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot read positions.csv: %s" % exc, contract.EXIT_ERROR)
    if positions.file_errors:
        return fail(args, version, now, "positions_invalid",
                    "positions.csv cannot be used: %s" % "; ".join(str(error) for error in positions.file_errors),
                    contract.EXIT_ERROR)
    groups = grouping.group_positions(positions.rows)

    symbols = expiry.request_symbols(positions.rows, session.et_date, cal)
    qcs: Dict[str, quote_qc.QuoteQC] = {}
    quoted: List[str] = []
    missing: Dict[str, str] = {}
    requests_made = 0
    warnings: List[str] = []
    footer_as_of = now
    if symbols:
        try:
            result = quote_cache.get_quotes(symbols, now=now, cfg=market.cache, client=market.client, paths=paths,
                                            allow_fetch=session.trading_day, run=market.run)
        except OSError as exc:
            return fail(args, version, now, "io_error", "cannot use the desk cache: %s" % exc, contract.EXIT_ERROR)
        for symbol in symbols:
            quote = result.quotes.get(symbol)
            if quote is not None:
                qcs[symbol] = quote_qc.assess(quote, now=now, session=session, bound_min=market.bound_min,
                                              prior_close_utc=prior_close)
                quoted.append(symbol)
            else:
                copy = result.stale.get(symbol)
                qcs[symbol] = quote_qc.unavailable(symbol, "cache: %s" % result.missing.get(symbol, "not served"),
                                                   label=missing_label(session, prior_close, copy), quote=copy)
        missing = dict(result.missing)
        requests_made = result.requests_made
        warnings.extend(result.warnings)
        stamps = [qc.as_of for qc in qcs.values() if qc.as_of is not None]
        if stamps:
            footer_as_of = max(stamps)

    cutoff_et = market.cfg.owner.broker_exercise_cutoff_et
    sheet = expiry.build_sheet(positions, groups, now=now, session=session, cal=cal, broker_cutoff_et=cutoff_et, qcs=qcs)
    listed = expiry.checklist_legs(sheet, mode)
    exit_code = expiry.checklist_exit_code(sheet, mode)
    counts = {
        "open_option_legs": len(sheet.legs),
        "listed": len(listed),
        "fired": sum(1 for leg in listed if leg.fired),
        "unavailable": sum(1 for leg in listed if leg.unavailable_reasons),
        "expired": len(sheet.expired),
        "quarantined_groups": len(sheet.quarantined),
    }
    all_warnings = list(sheet.warnings) + warnings

    lines = [header_line(mode, sheet, listed, cutoff_et, now)]
    for leg in listed:
        lines.append(expiry.docket_line(leg))
        if leg.dividend is not None:
            lines.append("  " + expiry.dividend_line(leg))
        if leg.pin is not None:
            lines.append("  " + expiry.pin_line(leg))
        lines.extend("  " + line for line in expiry.extra_lines(leg))
    for group_id in sorted(sheet.quarantined):
        lines.append("quarantined %s: %s" % (group_id, "; ".join(sheet.quarantined[group_id])))
    if exit_code == contract.EXIT_UNAVAILABLE:
        lines.append("UNAVAILABLE: %d listed item(s) could not be graded and %d group(s) are quarantined; the sheet is incomplete"
                     % (counts["unavailable"], counts["quarantined_groups"]))
    for warning in all_warnings:
        lines.append("warning: %s" % warning)
    if not session.trading_day:
        name = session.holiday_name or ("weekend" if session.session == market_clock.SESSION_WEEKEND else "closed")
        lines.append("NYSE closed (%s): no quote fetch" % name)
    lines.append(quote_cache.footer_line(market.cfg, footer_as_of))
    message = "\n".join(lines)

    payload: Dict[str, Any] = dict(sheet.as_dict())
    payload.update({
        "mode": mode,
        "verdict": contract.status_for(exit_code),
        "exit_code": exit_code,
        "legs": [leg.as_dict() for leg in listed],
        "quote_check": {"symbols": symbols, "quoted": quoted, "missing": missing, "requests_made": requests_made},
        "counts": counts,
        "session": session.as_dict(),
        "warnings": all_warnings,
        "fixture_set": market.fixture_set,
        "message": message,
        "as_of": state.format_utc(footer_as_of),
    })
    body = contract.envelope(TOOL, version, payload, source=quote_cache.source_label(market.cfg),
                             delay_min=market.cfg.market_data.feed_delay_min, generated_at=now,
                             status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
    record = record_path(paths, session.et_date, mode)
    try:
        state.atomic_write_json(record, body)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot write the docket record %s: %s"
                    % (paths.relative(record), exc.strerror or exc), contract.EXIT_ERROR)
    if args.json:
        print(contract.json_dumps(body))
        return exit_code
    print(message)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
