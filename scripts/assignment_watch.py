#!/usr/bin/env python3
"""assignment_watch.py: the dividend (extrinsic-versus-dividend) and pin tiers with act-by times.

Usage::

    python3 scripts/assignment_watch.py [--fixture-set NAME] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Stage 1, European-only (``aegis_desk.expiry``). Loads ``positions.csv`` and ``research/calendar.toml``,
groups the legs, reads ONE quote batch through ``quote_cache.get_quotes`` (every open option leg still
ahead of its last trading day, its underlying and every open stock leg; never chains), grades the quotes
with ``quote_qc.assess`` and prints, for every open short call whose underlying goes ex-dividend within
five trading days and whose last trading day is on or after the last cum-dividend day (the trading day
before the ex-date, when a dividend-capture exercise happens; a call gone by then is nothing to list and is
left to the pin tier, a call whose last trading day IS that day is graded with a note), the tier ``HIGH`` /
``WATCH`` / ``LOW`` from ``extrinsic_bid = bid - max(0, S_mid - K)``
against the dividend (``NOTED`` when the row carries ``#assign-ok``; ``UNAVAILABLE`` when the dividend is
unknown: unknown is never zero), and for every option leg on its last or next-to-last trading day the pin
tier ``PIN HIGH`` (within 1 % of the strike) / ``PIN WATCH`` (3 %) / ``ITM`` / ``PIN LOW``, each with the
act-by time (the owner's broker cutoff on the leg's last trading day) in ET and UTC, then the standard
data footer. Exit 10 when any tier fired (``HIGH``, ``PIN HIGH``, ``ITM`` on its last trading day), 20
when any leg or group is UNAVAILABLE and nothing fired, 0 otherwise; 1 on configuration or file errors
(``desk_root``, ``config_invalid``, ``positions_missing``, ``positions_invalid``, ``calendar_invalid``,
``io_error``), 2 on usage. Never writes ``positions.csv``. ``--json`` payload: ``{"verdict", "exit_code",
"today", "next_trading_day", "week_start", "week_end", "legs": [...], "expired", "quarantined",
"quote_check", "counts", "session", "warnings", "fixture_set"}``. On an early-close day the act-by is the
earlier of the broker cutoff and the 13:00 ET close, marked ``early close ...: confirm your broker's cutoff``
(``act_by_early_close`` in each leg).
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="assignment_watch")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import calendar_events, desk_config, desk_paths, expiry, grouping, market_clock, positions_csv, quote_cache, quote_qc, state  # noqa: E402
from aegis_desk.market_types import Quote  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "assignment_watch"
DESCRIPTION = ("dividend (extrinsic-versus-dividend, bid side) and pin tiers for the open option legs with act-by times "
               "from the owner's broker cutoff; Stage 1, European-only; UNAVAILABLE when a dividend or quote is unknown")
NETWORK_ACCESS = "GET https://sandbox.tradier.com/v1/markets/quotes only (through the quote cache); none with --offline"
LABEL_NO_QUOTE = "no quote"
NOTHING_LINE = ("nothing within the windows: no ex-dividend inside 5 trading days with a short call alive on the last "
                "cum-dividend day, no contract on its last or next-to-last trading day")
LEG_OUTPUT: Dict[str, Any] = expiry.LEG_OUTPUT
"""The leg object (``aegis_desk.expiry.LEG_OUTPUT``): one dictionary shared with expiry_checklist.py."""
OUTPUT: Dict[str, Any] = {
    "verdict": "fired | unavailable | quiet",
    "exit_code": "10 | 20 | 0",
    "today": "YYYY-MM-DD (ET date of --now)",
    "next_trading_day": "YYYY-MM-DD",
    "week_start": "YYYY-MM-DD (Monday)",
    "week_end": "YYYY-MM-DD (Sunday)",
    "legs": [LEG_OUTPUT],
    "expired": [{"row_number": "integer", "symbol": "string", "expiration": "YYYY-MM-DD"}],
    "quarantined": {"GROUP": ["row or group error strings"]},
    "quote_check": {"symbols": ["requested"], "quoted": ["served"], "missing": {"SYMBOL": "reason"}, "requests_made": "integer"},
    "counts": {"open_option_legs": "integer", "fired": "integer", "unavailable": "integer", "noted": "integer",
               "expired": "integer", "quarantined_groups": "integer"},
    "session": "market_clock.SessionInfo.as_dict()",
    "warnings": ["string"],
    "fixture_set": "string | null (offline runs)",
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/tradier/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")


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


def verdict_word(exit_code: int) -> str:
    return contract.status_for(exit_code)


def summary_line(sheet: expiry.ExpirySheet, session: market_clock.SessionInfo, exit_code: int, counts: Dict[str, int]) -> str:
    return "assignment watch %s (session %s): %s (%d fired, %d noted, %d unavailable, %d open option legs)" % (
        expiry.weekday_date(sheet.today), session.session, verdict_word(exit_code).upper(), counts["fired"],
        counts["noted"], counts["unavailable"], counts["open_option_legs"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    validate(parser, args)
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

    sheet = expiry.build_sheet(positions, groups, now=now, session=session, cal=cal,
                               broker_cutoff_et=market.cfg.owner.broker_exercise_cutoff_et, qcs=qcs)
    exit_code = expiry.assignment_exit_code(sheet)
    counts = {
        "open_option_legs": len(sheet.legs),
        "fired": sum(1 for leg in sheet.legs if leg.fired),
        "unavailable": sum(1 for leg in sheet.legs if leg.unavailable_reasons),
        "noted": sum(1 for leg in sheet.legs if leg.dividend is not None and leg.dividend.tier == expiry.TIER_NOTED),
        "expired": len(sheet.expired),
        "quarantined_groups": len(sheet.quarantined),
    }
    all_warnings = list(sheet.warnings) + warnings

    if args.json:
        payload: Dict[str, Any] = dict(sheet.as_dict())
        payload.update({
            "verdict": verdict_word(exit_code),
            "exit_code": exit_code,
            "quote_check": {"symbols": symbols, "quoted": quoted, "missing": missing, "requests_made": requests_made},
            "counts": counts,
            "session": session.as_dict(),
            "warnings": all_warnings,
            "fixture_set": market.fixture_set,
        })
        body = contract.envelope(TOOL, version, payload, source=quote_cache.source_label(market.cfg),
                                 delay_min=market.cfg.market_data.feed_delay_min, generated_at=now,
                                 status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(body))
        return exit_code

    lines = [summary_line(sheet, session, exit_code, counts)]
    listed = False
    for leg in sheet.legs:
        if leg.dividend is not None:
            lines.append(expiry.dividend_line(leg))
            listed = True
        if leg.pin is not None:
            lines.append(expiry.pin_line(leg))
            listed = True
        if leg.dividend is not None or leg.pin is not None:
            lines.extend(expiry.extra_lines(leg))
    if not listed:
        lines.append(NOTHING_LINE)
    for group_id in sorted(sheet.quarantined):
        lines.append("quarantined %s: %s" % (group_id, "; ".join(sheet.quarantined[group_id])))
    for warning in all_warnings:
        lines.append("warning: %s" % warning)
    if not session.trading_day:
        name = session.holiday_name or ("weekend" if session.session == market_clock.SESSION_WEEKEND else "closed")
        lines.append("NYSE closed (%s): no quote fetch" % name)
    lines.append(quote_cache.footer_line(market.cfg, footer_as_of))
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
