#!/usr/bin/env python3
"""scenario.py: the book re-priced under spot and volatility moves, and an event stress through earnings or an
ex-dividend date (roadmap 2.8). A computation on the book's model of record, not a forecast.

Usage::

    python3 scripts/scenario.py [--event earnings|ex_dividend --symbol SYM] [--symbol SYM] [--fixture-set NAME]
        [--json] [--schema] [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Marks the book exactly as ``book.py`` does (``aegis_desk.book.load_book``: one quotes request, nothing written), then
re-prices every open group on the grid of ``aegis_desk.scenario``: spot moves of -10 to +10 % crossed with volatility
moves of -5 to +5 volatility points, 21 cells, every option leg priced by the book's own leg pricer. Without
``--event`` the cells are instantaneous. With ``--event`` and ``--symbol`` the groups on that symbol are priced at the
close of the first trading day after the symbol's earnings or ex-dividend date in ``research/calendar.toml``.
``--symbol`` alone limits the grid to that symbol's groups.

Each cell prints the portfolio's P/L change from the book's marks (the sum of the groups', ``None`` when a group in
scope is excluded); each group prints its change at the two outer spot moves with the volatility unmoved, split into
stock and options, so a hedge shows what it offsets. At a zero move the stressed book equals the book.

Exit codes: 0 every group in scope stressed; 20 a group excluded (named with its reason), no open group in scope, or
an event the calendar cannot date; 1 a desk file that cannot be used; 2 usage. Nothing is written but the quote
cache. The book's own warnings (a missing day P/L snapshot, a cache note) go to the JSON payload only. ``--json``
payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="scenario")

import datetime  # noqa: E402
import sys  # noqa: E402
from decimal import Decimal  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import book, calendar_events, desk_config, desk_paths, market_clock, quote_cache  # noqa: E402
from aegis_desk import scenario as S  # noqa: E402
from aegis_desk import state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "scenario"
DESCRIPTION = ("the book re-priced under spot and volatility moves (21 cells), or an event stress of one symbol through "
               "its earnings or ex-dividend date, on the book's model of record: a computation, not a forecast")
NETWORK_ACCESS = ("the book's one quotes request: GET https://data.alpaca.markets/v2/stocks/quotes/latest and "
                  "/v1beta1/options/quotes/latest, through the quote cache; none with --offline")
OUTPUT: Dict[str, Any] = {
    "verdict": "ok | unavailable",
    "exit_code": "0 | 20",
    "label": "string (a computation, not a forecast)",
    "horizon": {"utc": "RFC 3339 | null (instantaneous)", "event": "earnings | ex_dividend | null",
                "symbol": "string | null", "event_date": "YYYY-MM-DD | null", "event_time": "pre | post | unknown | null",
                "horizon_date": "YYYY-MM-DD | null"},
    "grid": {"spot_pct": ["integer"], "vol_points": ["integer"]},
    "stressed": ["group id"],
    "excluded": [{"group_id": "string", "reason": "string"}],
    "cells": [{"spot_pct": "integer", "vol_points": "integer", "change": "decimal string | null",
               "stressed_pl_mid": "decimal string | null",
               "groups": [{"group_id": "string", "change": "decimal string", "stock": "decimal string",
                           "options": "decimal string", "stressed_pl_mid": "decimal string | null"}]}],
    "notes": ["string"],
    "reason": "string | null (why the stress is unavailable)",
    "warnings": ["string"],
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--event", choices=S.EVENTS, default=None,
                        help="price the symbol's groups one trading day after this event (needs --symbol)")
    parser.add_argument("--symbol", metavar="SYM", default=None, help="limit the stress to this underlying's groups")
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


def signed(value: Optional[Decimal]) -> str:
    return "-" if value is None else "%+.2f" % value


def header(result: S.Stress, document: Dict[str, Any]) -> str:
    horizon = result.horizon
    as_of = document["as_of_label"]
    if horizon.event is None:
        return "SCENARIO | the book re-priced, %s | %s" % (as_of, S.LABEL)
    words = "earnings" if horizon.event == S.EVENT_EARNINGS else "ex-dividend date"
    when = "" if not horizon.event_time else " (%s)" % horizon.event_time
    assert horizon.event_date is not None and horizon.horizon_date is not None and horizon.utc is not None
    close = market_clock.to_et(horizon.utc).strftime("%H:%M")
    return ("EVENT STRESS | %s %s %s%s | priced at the %s ET close of %s, one trading day after | %s"
            % (horizon.symbol, words, horizon.event_date.isoformat(), when, close, horizon.horizon_date.isoformat(),
               S.LABEL))


def render(result: S.Stress, document: Dict[str, Any]) -> List[str]:
    lines = [header(result, document)]
    for spot in S.GRID_SPOT_PCT:
        parts = ["vol %+d %s" % (vol, signed(result.cell(spot, vol).change)) for vol in S.GRID_VOL_POINTS]
        lines.append("P/L change | spot %+d%% | %s" % (spot, " | ".join(parts)))
    groups = {group["group_id"]: group for group in document["groups"]}
    low, high = S.GRID_SPOT_PCT[0], S.GRID_SPOT_PCT[-1]
    for group_id in result.stressed:
        group = groups[group_id]
        sides = []
        for spot in (low, high):
            item = next(entry for entry in result.cell(spot, 0).groups if entry.group_id == group_id)
            sides.append("spot %+d%%: stock %s, options %s, net %s" % (spot, signed(item.stock), signed(item.options),
                                                                       signed(item.change)))
        lines.append("%s %s %s | %s" % (group_id, group["structure"], group["underlying"], " | ".join(sides)))
    lines.extend("excluded | %s | %s" % (item.group_id, item.reason) for item in result.excluded)
    lines.extend("note: %s" % note for note in result.notes)
    rate = Decimal(str(document["rates"]["risk_free_rate"])) * 100
    lines.append("grid: spot %+d to %+d %%, volatility %+d to %+d points (the desk's proposed grid); risk-free rate "
                 "%s %%; each symbol's sourced dividend yield" % (low, high, S.GRID_VOL_POINTS[0], S.GRID_VOL_POINTS[-1],
                                                                 rate.normalize()))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    if args.event is not None and args.symbol is None:
        parser.error("--event needs --symbol")
    if args.symbol is not None and not quote_cache.SYMBOL_RE.match(args.symbol.upper()):
        parser.error("--symbol %r is not a ticker" % args.symbol)
    symbol = None if args.symbol is None else args.symbol.upper()
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
    try:
        run = book.load_book(market)
    except book.BookInputError as exc:
        return fail(args, version, now, exc.code, exc.message, contract.EXIT_ERROR)
    except OSError as exc:
        return fail(args, version, now, "io_error", str(exc), contract.EXIT_ERROR)
    document = run.document
    horizon = S.Horizon()
    reason: Optional[str] = None
    if args.event is not None:
        assert symbol is not None
        try:
            horizon = S.event_horizon(run.calendar, symbol, args.event, run.session.et_date,
                                      calendar_events.extra_closures(run.calendar))
        except S.StressUnavailable as exc:
            reason = exc.reason
    result = S.stress(document, run.calendar, horizon=horizon, symbol=symbol) if reason is None else None
    if result is not None and not result.stressed and not result.excluded:
        reason = "no open group%s in the book" % ("" if symbol is None else " on %s" % symbol)
        result = None
    unavailable = result is None or bool(result.excluded)
    exit_code = contract.EXIT_UNAVAILABLE if unavailable else contract.EXIT_QUIET
    warnings = list(document.get("warnings") or [])
    if args.json:
        payload = {"verdict": "unavailable" if unavailable else "ok", "exit_code": exit_code, "label": S.LABEL,
                   "horizon": (result.horizon if result is not None else horizon).as_dict(),
                   "grid": {"spot_pct": list(S.GRID_SPOT_PCT), "vol_points": list(S.GRID_VOL_POINTS)},
                   "stressed": [] if result is None else list(result.stressed),
                   "excluded": [] if result is None else [item.as_dict() for item in result.excluded],
                   "cells": [] if result is None else [item.as_dict() for item in result.cells],
                   "notes": [] if result is None else list(result.notes), "reason": reason, "warnings": warnings}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    if result is None:
        lines = ["UNAVAILABLE | %s %s | %s" % (TOOL if args.event is None else "event stress", symbol or "book", reason)]
    else:
        lines = render(result, document)
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
