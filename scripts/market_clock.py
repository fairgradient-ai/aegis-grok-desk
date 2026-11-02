#!/usr/bin/env python3
"""market_clock.py: where the NYSE stands at an instant, and an expiry's DTE and exercise cutoff.

Usage::

    python3 scripts/market_clock.py [--at ISO-8601-UTC] [--expiry YYYY-MM-DD [--am-settled]]
        [--product equity|late_close] [--json] [--schema] [--timeout S] [--desk-root DIR] [--offline]
        [--now ISO-8601-UTC]

Prints the America/New_York time and date of the instant, the session (``pre``, ``open``,
``early_close``, ``closed``, ``weekend`` or ``holiday`` with the calendar name), the minutes to the
close, the previous close, the next trading day and the holiday-table status, and with
``--expiry`` the calendar and trading DTE to the last trading day, the settlement day, the last
trading day and the exercise cutoff in UTC and ET (the exchange close for PM-settled contracts,
09:30 ET on the settlement morning with ``--am-settled``; a broker's cutoff is earlier and lives
in ``desk.toml`` for the expiry tools). ``--at`` is the instant the report is about and defaults
to ``--now`` or the clock read once here; it also stamps the envelope so a replayed run is
reproducible. ``--product late_close`` applies the 16:15 ET (13:15 ET early) close of the owner's
late-close products. Everything comes from ``aegis_desk.market_clock``; no desk file is read
(``--desk-root`` and ``--offline`` are accepted because every tool shares the flags) and nothing is
written.

Exit 0; exit 20 with the message ``holiday table expired`` when the ET date lies past the
embedded tables' ``TABLE_EXPIRES`` (the clock fails closed); 2 on usage. ``--json`` payload:
``SessionInfo.as_dict()`` plus ``et``, ``et_zone``, ``next_trading_day``, ``previous_close_utc``
and the ``expiry`` block (``null`` without ``--expiry``).
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="market_clock")

import datetime  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import market_clock, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "market_clock"
DESCRIPTION = ("NYSE session, ET time, minutes to close, previous close, next trading day and holiday-table status "
               "at an instant; with --expiry the DTE (calendar and trading), last trading day and exercise cutoff")
NETWORK_ACCESS = "none"
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
OUTPUT: Dict[str, Any] = {
    "session": " | ".join(market_clock.SESSIONS),
    "trading_day": "boolean",
    "close_utc": "ISO-8601 UTC | null (weekend, holiday)",
    "minutes_to_close": "integer while the close lies ahead | null",
    "holiday_name": "string | null (full holiday or early-close day label)",
    "as_of_utc": "ISO-8601 UTC (the --at instant)",
    "et_date": "YYYY-MM-DD (America/New_York)",
    "open_utc": "ISO-8601 UTC | null",
    "product": " | ".join(market_clock.PRODUCTS),
    "table_status": " | ".join((market_clock.STATUS_OK, market_clock.STATUS_EXPIRING_SOON, market_clock.STATUS_EXPIRED)),
    "et": "ISO-8601 with the ET offset (-05:00 EST or -04:00 EDT)",
    "et_zone": "EST | EDT",
    "next_trading_day": "YYYY-MM-DD",
    "previous_close_utc": "ISO-8601 UTC (the most recent session close at or before the instant)",
    "expiry": {
        "expiry": "YYYY-MM-DD", "am_settled": "boolean", "settlement_day": "YYYY-MM-DD",
        "last_trading_day": "YYYY-MM-DD", "dte_calendar": "integer (negative once passed)",
        "dte_trading": "integer (negative once passed)", "exercise_cutoff_utc": "ISO-8601 UTC",
        "exercise_cutoff_et": "ISO-8601 with the ET offset",
        "calendar_coverage": "table | rule (which tier of the NYSE calendar answered)",
    },
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--at", metavar="ISO-8601-UTC", default=None,
                        help="the instant to classify (default: --now, else the wall clock)")
    parser.add_argument("--expiry", metavar="YYYY-MM-DD", default=None,
                        help="an expiration date: adds DTE, last trading day and exercise cutoff")
    parser.add_argument("--am-settled", action="store_true",
                        help="the expiry is AM-settled (settles on the opening quotation; needs --expiry)")
    parser.add_argument("--product", choices=market_clock.PRODUCTS, default=market_clock.PRODUCT_EQUITY,
                        help="equity (16:00 ET close, 13:00 early) or late_close (16:15 ET, 13:15 early)")
    return parser


def parse_iso_date(text: str, what: str) -> datetime.date:
    match = ISO_DATE_RE.match(text.strip())
    if match is None:
        raise ValueError("%s must be a YYYY-MM-DD date, got %r" % (what, text))
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError as exc:
        raise ValueError("%s %r is not a valid date: %s" % (what, text, exc)) from None


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    args.at_instant = None
    if args.at is not None:
        try:
            args.at_instant = contract.parse_utc_instant(args.at, "--at")
        except ValueError as exc:
            parser.error(str(exc))
    args.expiry_date = None
    if args.expiry is not None:
        try:
            args.expiry_date = parse_iso_date(args.expiry, "--expiry")
        except ValueError as exc:
            parser.error(str(exc))
    if args.am_settled and args.expiry_date is None:
        parser.error("--am-settled needs --expiry YYYY-MM-DD")


def expiry_block(expiry: datetime.date, am_settled: bool, et_date: datetime.date) -> Dict[str, Any]:
    cutoff = market_clock.exercise_cutoff_utc(expiry, am_settled)
    return {
        "expiry": expiry.isoformat(),
        "am_settled": am_settled,
        "settlement_day": market_clock.settlement_day(expiry, am_settled).isoformat(),
        "last_trading_day": market_clock.last_trading_day(expiry, am_settled).isoformat(),
        "dte_calendar": market_clock.dte(expiry, et_date, market_clock.UNIT_CALENDAR, am_settled),
        "dte_trading": market_clock.dte(expiry, et_date, market_clock.UNIT_TRADING, am_settled),
        "exercise_cutoff_utc": cutoff.isoformat(),
        "exercise_cutoff_et": market_clock.to_et(cutoff).isoformat(),
        "calendar_coverage": market_clock.calendar_coverage(expiry),
    }


def et_clock(moment: datetime.datetime) -> str:
    return market_clock.to_et(moment).strftime("%H:%M")


def session_line(info: market_clock.SessionInfo) -> str:
    label = "session %s" % info.session
    if info.holiday_name:
        label += " (%s)" % info.holiday_name
    if not info.trading_day:
        return label + ": no session"
    close = et_clock(info.close_utc)
    if info.session == market_clock.SESSION_PRE:
        return "%s: trading day; opens %s ET, closes %s ET in %d min" % (
            label, et_clock(info.open_utc), close, info.minutes_to_close)
    if info.session == market_clock.SESSION_CLOSED:
        since = int((info.as_of_utc - info.close_utc).total_seconds() // 60)
        return "%s: trading day; closed %s ET, %d min ago" % (label, close, since)
    return "%s: trading day; closes %s ET in %d min" % (label, close, info.minutes_to_close)


def prose(payload: Dict[str, Any], info: market_clock.SessionInfo, at: datetime.datetime) -> List[str]:
    et = market_clock.to_et(at)
    lines = [
        "%s ET (%s) = %s" % (et.strftime("%Y-%m-%d %H:%M:%S"), payload["et_zone"], state.format_utc(at)),
        session_line(info),
        "previous close %s (%s ET); next trading day %s" % (
            payload["previous_close_utc"],
            market_clock.to_et(datetime.datetime.fromisoformat(payload["previous_close_utc"])).strftime("%Y-%m-%d %H:%M"),
            payload["next_trading_day"]),
        "holiday table %s (expires %s)" % (info.table_status, market_clock.TABLE_EXPIRES.isoformat()),
    ]
    block = payload["expiry"]
    if block is not None:
        cutoff_et = datetime.datetime.fromisoformat(block["exercise_cutoff_et"])
        lines.append(
            "expiry %s (%s-settled): last trading day %s, settlement day %s; DTE %d calendar, %d trading; "
            "exercise cutoff %s (%s ET, exchange; a broker cutoff is earlier); calendar coverage %s" % (
                block["expiry"], "AM" if block["am_settled"] else "PM", block["last_trading_day"],
                block["settlement_day"], block["dte_calendar"], block["dte_trading"], block["exercise_cutoff_utc"],
                cutoff_et.strftime("%Y-%m-%d %H:%M"), block["calendar_coverage"]))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    validate(parser, args)
    at = args.at_instant or args.now or state.utc_now()
    try:
        info = market_clock.session_at(at, args.product)
    except market_clock.TableExpiredError as exc:
        message = "holiday table expired: %s" % exc
        if args.json:
            print(contract.json_dumps(contract.error_envelope(TOOL, version, "holiday_table_expired", message,
                                                              contract.STATUS_UNAVAILABLE, generated_at=at)))
        else:
            print("UNAVAILABLE: %s" % message)
        return contract.EXIT_UNAVAILABLE
    payload: Dict[str, Any] = dict(info.as_dict())
    payload["et"] = market_clock.to_et(at).isoformat()
    payload["et_zone"] = "EDT" if market_clock.is_dst(at) else "EST"
    payload["next_trading_day"] = market_clock.next_trading_day(info.et_date).isoformat()
    payload["previous_close_utc"] = market_clock.previous_close_utc(at, args.product).isoformat()
    payload["expiry"] = None if args.expiry_date is None else expiry_block(args.expiry_date, args.am_settled, info.et_date)
    if args.json:
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=at)))
        return contract.EXIT_QUIET
    print("\n".join(prose(payload, info, at)))
    return contract.EXIT_QUIET


if __name__ == "__main__":
    sys.exit(main())
