#!/usr/bin/env python3
"""dividend_source.py: the held symbols' declared cash dividends from Alpaca's corporate actions, proposed for
``research/calendar.toml`` (roadmap 2.11, the owner's answer to open question 1). Writes nothing.

Usage::

    python3 scripts/dividend_source.py [--days N] [--fixture-set NAME] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

One read path: ``GET https://data.alpaca.markets/v1/corporate-actions`` with ``types=cash_dividend`` for the held
underlyings, with the desk's paper key. The endpoint filters by process date, so the request spans a wider process-date
window (10 days before today to 60 days past the ex-date window) and the client keeps only the records whose ex-date
lies from today's ET date to ``--days`` ahead (default 90); a record without an ex-date is ignored.

For each held symbol the earliest declared ex-date in the window is compared with the calendar's ``ex_dividend``
entry: ``AGREES`` (same date and amount), ``NEW`` (the calendar holds no dated entry) or ``DIFFERS`` (the calendar
says otherwise). A symbol with no record in the window is ``NONE FOUND``: absence of a declaration is no proof of no
dividend, so nothing is proposed for it. ``NEW`` and ``DIFFERS`` are proposed as ``sourced_dividends`` answers for
``desk_init.py``, which writes them only on the owner's tap (``--owner-tapped``), as status ``confirmed`` with
``source = "Alpaca corporate actions (cash_dividend)"`` and the fetch time as ``checked_utc``; a yield the owner stated
is kept with its own source. Earnings dates, the yield and AM settlement values stay owner-stated.

If the endpoint is not served to the key (401, 403, a plan without it), nothing is sourced and the owner keeps stating
dividends: exit 20 UNAVAILABLE, no other source replaces it. Exit codes: 0 answered (even when nothing is new); 20
not served or unusable; 1 a desk file that cannot be used; 2 usage. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="dividend_source")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import alpaca_client, calendar_events, desk_config, desk_paths, positions_csv, quote_cache  # noqa: E402
from aegis_desk import state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "dividend_source"
DESCRIPTION = ("the held symbols' declared cash dividends from Alpaca's corporate actions, compared with "
               "research/calendar.toml and proposed as desk_init.py answers the owner taps; writes nothing")
NETWORK_ACCESS = ("GET https://data.alpaca.markets/v1/corporate-actions (types=cash_dividend) only; none with "
                  "--offline")
DEFAULT_DAYS: int = 90
MAX_DAYS: int = 400
PROCESS_LEAD_DAYS: int = 10
PROCESS_LAG_DAYS: int = 60
AGREES, NEW, DIFFERS, NONE_FOUND = "AGREES", "NEW", "DIFFERS", "NONE FOUND"
OUTPUT: Dict[str, Any] = {
    "verdict": "ok | unavailable",
    "exit_code": "0 | 20",
    "ex_start": "YYYY-MM-DD",
    "ex_end": "YYYY-MM-DD",
    "symbols": [{"symbol": "string", "state": "AGREES | NEW | DIFFERS | NONE FOUND", "ex_date": "YYYY-MM-DD | null",
                 "amount": "decimal string | null", "special": "boolean", "calendar": "string | null"}],
    "answers": {"sourced_dividends": {"SYMBOL": {"date": "YYYY-MM-DD", "amount": "decimal string",
                                                 "checked_utc": "RFC 3339"}}},
    "apply": "string | null (the desk_init.py command the owner's tap runs)",
    "ignored_without_ex_date": "integer",
    "reason": "string | null",
}
APPLY = "desk_init.py --answers <the answers above, saved as a file> --replace --owner-tapped"


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--days", metavar="N", type=int, default=DEFAULT_DAYS,
                        help="ex-dates from today to N calendar days ahead (1-%d, default %d)" % (MAX_DAYS, DEFAULT_DAYS))
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/alpaca/ (default regular; --offline only)")
    return parser


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.status_for(exit_code),
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return exit_code


def calendar_text(entry: Optional[calendar_events.DividendEntry]) -> Optional[str]:
    if entry is None:
        return None
    if entry.date is None:
        return "%s (%s)" % (entry.status, entry.source)
    return "%s %s %s (%s)" % (entry.date.isoformat(), entry.amount, entry.status, entry.source)


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    if not 1 <= args.days <= MAX_DAYS:
        parser.error("--days must be 1-%d" % MAX_DAYS)
    now = args.now or state.utc_now()
    try:
        market = quote_cache.open_market_data(args.desk_root, now=now, offline=args.offline,
                                              fixture_set=args.fixture_set, timeout_s=args.timeout,
                                              user_agent=user_agent(version))
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc), contract.EXIT_ERROR)
    except desk_config.DeskConfigError as exc:
        return fail(args, version, now, "config_invalid", str(exc), contract.EXIT_ERROR)
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
    today = market.session.et_date
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=today)
    except FileNotFoundError:
        return fail(args, version, now, "positions_missing", "positions.csv not found at %s" % paths.positions_csv,
                    contract.EXIT_ERROR)
    if positions.file_errors:
        return fail(args, version, now, "positions_invalid", "positions.csv cannot be used: %s"
                    % "; ".join(str(error) for error in positions.file_errors), contract.EXIT_ERROR)
    held = sorted({row.underlying for row in positions.open_rows})
    ex_start, ex_end = today, today + datetime.timedelta(days=args.days)
    reason: Optional[str] = None
    result = None
    if not held:
        reason = "no open position: nothing to source"
    else:
        try:
            result = market.client.cash_dividends(held, ex_start - datetime.timedelta(days=PROCESS_LEAD_DAYS),
                                                  ex_end + datetime.timedelta(days=PROCESS_LAG_DAYS), ex_start=ex_start,
                                                  ex_end=ex_end)
        except alpaca_client.AlpacaError as exc:
            reason = ("Alpaca corporate actions not served (%s): nothing is sourced; keep stating dividends in the "
                      "desk interview" % exc)
    rows: List[Dict[str, Any]] = []
    answers: Dict[str, Dict[str, str]] = {}
    stamp = now if result is None or result.receipt.receipt_utc is None else result.receipt.receipt_utc
    checked = stamp.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if result is not None:
        for symbol in held:
            found = sorted((item for item in result.dividends if item.symbol == symbol), key=lambda item: item.ex_date)
            entry = calendar_events.events_for(cal, symbol).ex_dividend
            if not found:
                rows.append({"symbol": symbol, "state": NONE_FOUND, "ex_date": None, "amount": None, "special": False,
                             "calendar": calendar_text(entry)})
                continue
            first = found[0]
            dated = entry is not None and entry.date is not None
            if dated and entry.date == first.ex_date and entry.amount == first.rate:
                status = AGREES
            else:
                status = DIFFERS if dated else NEW
                answers[symbol] = {"date": first.ex_date.isoformat(), "amount": str(first.rate), "checked_utc": checked}
            rows.append({"symbol": symbol, "state": status, "ex_date": first.ex_date.isoformat(), "amount": first.rate,
                         "special": first.special, "calendar": calendar_text(entry)})
    unavailable = result is None
    exit_code = contract.EXIT_UNAVAILABLE if unavailable else contract.EXIT_QUIET
    payload_answers = {"sourced_dividends": answers} if answers else {}
    apply = APPLY if answers else None
    if args.json:
        payload = {"verdict": "unavailable" if unavailable else "ok", "exit_code": exit_code,
                   "ex_start": ex_start.isoformat(), "ex_end": ex_end.isoformat(), "symbols": rows,
                   "answers": payload_answers, "apply": apply,
                   "ignored_without_ex_date": 0 if result is None else result.skipped, "reason": reason}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    if result is None:
        print("UNAVAILABLE | sourced dividends | %s" % reason)
        return exit_code
    lines = ["SOURCED DIVIDENDS | ex-dates %s to %s | Alpaca corporate actions (cash_dividend), as of %s"
             % (ex_start.isoformat(), ex_end.isoformat(), checked)]
    for row in rows:
        if row["state"] == NONE_FOUND:
            lines.append("%s | NONE FOUND in the window (nothing proposed; calendar: %s)" % (row["symbol"],
                                                                                         row["calendar"] or "no entry"))
            continue
        text = "%s | %s | ex-date %s, %s per share%s" % (row["symbol"], row["state"], row["ex_date"], row["amount"],
                                                         " (special)" if row["special"] else "")
        if row["state"] != NEW:
            text += " | calendar: %s" % row["calendar"]
        lines.append(text)
    if answers:
        lines.append("proposed for research/calendar.toml, written only on your tap: %s" % ", ".join(sorted(answers)))
        lines.append("apply: %s" % APPLY)
    else:
        lines.append("nothing to propose: the calendar agrees with every declaration found")
    if result.skipped:
        lines.append("ignored: %d record(s) without an ex-date" % result.skipped)
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
