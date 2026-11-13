#!/usr/bin/env python3
"""positions_check.py: validate positions.csv, group the legs and run the first-observation plausibility check.

Usage::

    python3 scripts/positions_check.py [--fixture-set NAME] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Loads ``positions.csv`` through ``aegis_desk.positions_csv`` (a row error quarantines its trade
group; a file error fails the whole file), groups the legs into structures
(``aegis_desk.grouping``), and checks every open row against one quote batch (the unexpired open
option legs, their underlyings and the open stock legs, through ``quote_cache.get_quotes``; never
chains): an option row whose entry premium per share lies outside ``[mid / 20, mid x 20]`` is the
row ERROR ``unit mismatch? entry 3.42 vs mark 0.12`` and quarantines its group; a stock row outside
``[mid / 10, mid x 10]`` is a WARNING; an index underlying is warned as unquotable (the data source cannot quote index options);
an expired leg is left to ``csv_recon.py``. A cached copy is good enough for this order-of-magnitude
check, so the test also runs after the close.

Prints ``CLEAN`` (every group valid, every open leg checked), or the quarantined groups with their
row errors and the file errors, then the group table (structure, underlying, account, net cost,
rows) and one plausibility line per open leg, then the data footer. Exit 0 CLEAN, 20 when any group
is quarantined or a quote was unavailable for the check (UNAVAILABLE, never a quiet pass), 1 on file
errors, a missing ``positions.csv`` or an unusable ``desk.toml`` / calendar, 2 on usage. Never
writes ``positions.csv``. ``--json`` payload: ``{"verdict", "exit_code", "row_count",
"positions_sha256", "file_errors", "quarantined", "groups", "plausibility", "quote_check",
"counts", "session", "warnings", "fixture_set"}``. The ``ERRORS`` verdict (file-level CSV errors) is
the error envelope instead: exit 1, ``status`` ``error``, ``error.code`` ``positions_invalid``,
``error.message`` ``ERRORS: positions.csv cannot be used: <file errors joined by '; '>``, no payload,
the shape ``csv_recon.py``, ``book.py``, ``assignment_watch.py`` and ``expiry_checklist.py`` give the
same file; the prose is unchanged (the ``ERRORS`` line, one ``file error:`` line each, the footer) and
nothing is fetched on that path.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="positions_check")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import calendar_events, desk_config, desk_paths, market_clock, positions_csv, quote_cache, recon, state  # noqa: E402
from aegis_desk.grouping import PositionGroup, group_positions  # noqa: E402
from aegis_desk.market_types import Quote  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "positions_check"
DESCRIPTION = ("validate positions.csv, group the legs into structures and run the first-observation plausibility check "
               "of every open leg against its quote; CLEAN, or the quarantined groups and file errors (never writes the file)")
NETWORK_ACCESS = ("GET https://data.alpaca.markets/v2/stocks/quotes/latest and /v1beta1/options/quotes/latest only "
                  "(through the quote cache); none with --offline")
VERDICTS = (recon.VERDICT_CLEAN, recon.VERDICT_UNAVAILABLE, recon.VERDICT_ERRORS)
OUTPUT: Dict[str, Any] = {
    "verdict": " | ".join(VERDICTS) + " (ERRORS is reported through the error envelope: error.code positions_invalid, "
               "no payload)",
    "exit_code": "0 | 20 | 1",
    "row_count": "integer data rows seen (quarantined included)",
    "positions_sha256": "hex of positions.csv",
    "file_errors": ["string"],
    "quarantined": {"GROUP": ["row error strings (parser, grouping and plausibility errors)"]},
    "groups": [{"group_id": "string", "trade_id": "string | null", "account": "string", "underlying": "string",
                "structure": "string", "basis_kind": "credit | debit | stock", "net_cost": "decimal string",
                "legs": ["row numbers"], "open_legs": ["row numbers"], "closed_legs": ["row numbers"],
                "underlying_stop": "decimal string | null", "warnings": ["string"], "errors": ["string"]}],
    "plausibility": [{"row_number": "integer", "group_id": "string", "trade_id": "string | null", "symbol": "string",
                      "instrument_type": "string", "entry": "decimal string (per share)", "mark": "decimal string (mid) | null",
                      "status": " | ".join(recon.PLAUSIBILITY_STATUSES), "message": "string"}],
    "quote_check": {"symbols": ["requested"], "quoted": ["served (cache copies included)"], "missing": {"SYMBOL": "reason"},
                    "requests_made": "integer"},
    "counts": {"rows": "integer", "groups": "integer", "open_groups": "integer", "closed_groups": "integer",
               "quarantined_groups": "integer", "plausibility_ok": "integer", "plausibility_errors": "integer",
               "plausibility_warnings": "integer", "plausibility_not_checked": "integer",
               "plausibility_unquotable": "integer", "plausibility_expired": "integer"},
    "session": "market_clock.SessionInfo.as_dict()",
    "warnings": ["string"],
    "fixture_set": "string | null (offline runs)",
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/alpaca/ (default regular; --offline only)")
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


def plural(count: int, noun: str) -> str:
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def group_line(group: PositionGroup) -> str:
    rows = ", ".join(str(number) for number in group.row_numbers)
    line = "%s %s %s [%s] net_cost %s (%s); rows %s" % (group.group_id, group.structure, group.underlying, group.account,
                                                        quote_cache.format_money(group.net_cost), group.basis_kind, rows)
    if not group.open_legs:
        line += "; closed"
    if group.warnings:
        line += "; warnings: " + "; ".join(group.warnings)
    return line


def plausibility_line(check: recon.PlausibilityCheck) -> str:
    label = {recon.PLAUSIBILITY_ERROR: "ERROR ", recon.PLAUSIBILITY_WARNING: "WARNING "}.get(check.status, "")
    return "plausibility row %d %s %s%s" % (check.row_number, check.symbol, label, check.message)


def summary_line(verdict: str, counts: Dict[str, int], file_errors: List[str]) -> str:
    if verdict == recon.VERDICT_ERRORS:
        return "ERRORS: positions.csv cannot be used: %s" % "; ".join(file_errors)
    groups = "%s (%d open, %d closed)" % (plural(counts["groups"], "group"), counts["open_groups"], counts["closed_groups"])
    if verdict == recon.VERDICT_CLEAN:
        return "CLEAN: %s, %s, 0 quarantined; plausibility passed for %s" % (
            plural(counts["rows"], "row"), groups, plural(counts["plausibility_ok"], "open leg"))
    return "UNAVAILABLE: %s, %s, %s quarantined, %s not checked; plausibility passed for %s" % (
        plural(counts["rows"], "row"), groups, counts["quarantined_groups"], plural(counts["plausibility_not_checked"], "leg"),
        plural(counts["plausibility_ok"], "open leg"))


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
    session = market_clock.session_at(now, extra_holidays=calendar_events.extra_closures(cal))
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=session.et_date)
    except FileNotFoundError:
        return fail(args, version, now, "positions_missing", "positions.csv not found at %s" % paths.positions_csv,
                    contract.EXIT_ERROR)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot read positions.csv: %s" % exc, contract.EXIT_ERROR)

    file_errors = [str(error) for error in positions.file_errors]
    groups = group_positions(positions.rows)
    quarantined: Dict[str, List[str]] = {group_id: [str(error) for error in errors]
                                         for group_id, errors in positions.quarantined.items()}
    for group in groups:
        if group.is_quarantined:
            quarantined.setdefault(group.group_id, []).extend(group.errors)
    checks: List[recon.PlausibilityCheck] = []
    warnings: List[str] = []
    symbols: List[str] = []
    quoted: List[str] = []
    missing: Dict[str, str] = {}
    requests_made = 0
    footer_as_of = now
    if not file_errors:
        plan = recon.plan_settlement(positions, groups, now=now, session=session, cal=cal)
        symbols = recon.plausibility_symbols(groups, plan.expired_rows)
        quotes: Dict[str, Quote] = {}
        if symbols:
            try:
                result = quote_cache.get_quotes(symbols, now=now, cfg=market.cache, client=market.client, paths=paths,
                                                allow_fetch=session.trading_day, run=market.run)
            except OSError as exc:
                return fail(args, version, now, "io_error", "cannot use the desk cache: %s" % exc, contract.EXIT_ERROR)
            quotes = dict(result.quotes)
            for symbol, copy in result.stale.items():
                quotes.setdefault(symbol, copy)  # a cached copy serves an order-of-magnitude check
            missing = {symbol: reason for symbol, reason in result.missing.items() if symbol not in quotes}
            quoted = [symbol for symbol in symbols if symbol in quotes]
            requests_made = result.requests_made
            warnings.extend(result.warnings)
            stamps = [quote.as_of for quote in quotes.values() if quote.as_of is not None]
            if stamps:
                footer_as_of = max(stamps)
        checks = recon.plausibility(positions.rows, quotes, missing=missing, expired_rows=plan.expired_rows,
                                    index_unquotable=market.cfg.market_data.host in desk_config.HOSTS)
        for group_id, errors in recon.plausibility_quarantine(checks, positions.rows).items():
            quarantined.setdefault(group_id, []).extend(str(error) for error in errors)
    live = [group for group in groups if group.group_id not in quarantined]
    statuses = [check.status for check in checks]
    counts = {
        "rows": positions.row_count,
        "groups": len(live) + len(quarantined),
        "open_groups": sum(1 for group in live if group.open_legs),
        "closed_groups": sum(1 for group in live if not group.open_legs),
        "quarantined_groups": len(quarantined),
        "plausibility_ok": statuses.count(recon.PLAUSIBILITY_OK),
        "plausibility_errors": statuses.count(recon.PLAUSIBILITY_ERROR),
        "plausibility_warnings": statuses.count(recon.PLAUSIBILITY_WARNING),
        "plausibility_not_checked": statuses.count(recon.PLAUSIBILITY_NOT_CHECKED),
        "plausibility_unquotable": statuses.count(recon.PLAUSIBILITY_UNQUOTABLE),
        "plausibility_expired": statuses.count(recon.PLAUSIBILITY_EXPIRED),
    }
    if file_errors:
        verdict = recon.VERDICT_ERRORS
    elif quarantined or counts["plausibility_not_checked"]:
        verdict = recon.VERDICT_UNAVAILABLE
    else:
        verdict = recon.VERDICT_CLEAN
    exit_code = recon.VERDICT_EXIT_CODES[verdict]
    if verdict == recon.VERDICT_ERRORS and args.json:
        # The error envelope, as csv_recon.py and the other tools give the same file; the prose keeps its lines.
        return fail(args, version, now, "positions_invalid", summary_line(verdict, counts, file_errors), exit_code)

    if args.json:
        payload: Dict[str, Any] = {
            "verdict": verdict,
            "exit_code": exit_code,
            "row_count": positions.row_count,
            "positions_sha256": positions.sha256,
            "file_errors": file_errors,
            "quarantined": quarantined,
            "groups": [group.as_dict() for group in live],
            "plausibility": [check.as_dict() for check in checks],
            "quote_check": {"symbols": symbols, "quoted": quoted, "missing": missing, "requests_made": requests_made},
            "counts": counts,
            "session": session.as_dict(),
            "warnings": warnings,
            "fixture_set": market.fixture_set,
        }
        body = contract.envelope(TOOL, version, payload, source=quote_cache.source_label(market.cfg),
                                 delay_min=market.cfg.market_data.feed_delay_min, generated_at=now,
                                 status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(body))
        return exit_code

    lines = [summary_line(verdict, counts, file_errors)]
    for error in file_errors:
        lines.append("file error: %s" % error)
    for group_id in sorted(quarantined):
        lines.append("quarantined %s: %s" % (group_id, "; ".join(quarantined[group_id])))
    lines.extend(group_line(group) for group in live)
    lines.extend(plausibility_line(check) for check in checks)
    for warning in warnings:
        lines.append("warning: %s" % warning)
    lines.append(quote_cache.footer_line(market.cfg, footer_as_of))
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
