#!/usr/bin/env python3
"""scenario.py: the book re-priced under spot and volatility moves, and an event stress through earnings or an
ex-dividend date (roadmap 2.8). A computation on the book's model of record, not a forecast.

Usage::

    python3 scripts/scenario.py [--event earnings|ex_dividend --symbol SYM] [--symbol SYM] [--upcoming N]
        [--from-snapshot] [--fixture-set NAME] [--json] [--schema] [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Marks the book exactly as ``book.py`` does (``aegis_desk.book.load_book``: one quotes request, nothing written), then
re-prices every open group on the grid of ``aegis_desk.scenario``: spot moves of -10 to +10 % crossed with volatility
moves of -5 to +5 volatility points, 21 cells, every option leg priced by the book's own leg pricer. Without
``--event`` the cells are instantaneous. With ``--event`` and ``--symbol`` the groups on that symbol are priced at the
close of the first trading day after the symbol's earnings or ex-dividend date in ``research/calendar.toml``.
``--symbol`` alone limits the grid to that symbol's groups. ``--upcoming N`` (the ``pre-event-stress`` routine of the
Aegis Book & Risk Analyst, roadmap 2.10) runs one event stress for each held symbol whose earnings or ex-dividend date
falls from today's ET date to the N-th trading day after it, and none when no event is that close (quiet, exit 0).
``--from-snapshot`` (the Saturday ``weekend-stress`` routine) reads the latest end-of-day snapshot of record on or
before today (``data/book/<ET date>.json``, written by ``book.py --snapshot``) instead of marking the book from
quotes: on a closed day the book fetches no quote, so the snapshot of the last close is the book to stress. It sends no
request; a desk without a snapshot is exit 20.

Each cell prints the portfolio's P/L change from the book's marks (the sum of the groups', ``None`` when a group in
scope is excluded); each group prints its change at the two outer spot moves with the volatility unmoved, split into
stock and options, so a hedge shows what it offsets. At a zero move the stressed book equals the book.

The JSON payload carries ``message`` (the text printed, ``null`` when quiet) and ``as_of`` (the newest quote stamp of
the book) for the routine's post, and ``stresses``, every stress run (one, or one per upcoming event).

Exit codes: 0 every group in scope stressed (or, with ``--upcoming``, no event close enough); 10 with ``--upcoming``,
at least one event stressed in full; 20 a group excluded (named with its reason), no open group in scope, or
an event the calendar cannot date; 1 a desk file that cannot be used; 2 usage. Nothing is written but the quote
cache. The book's own warnings (a missing day P/L snapshot, a cache note) go to the JSON payload only. ``--json``
payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="scenario")

import datetime  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from decimal import Decimal  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import book, calendar_events, desk_config, desk_paths, market_clock, quote_cache  # noqa: E402
from aegis_desk import scenario as S  # noqa: E402
from aegis_desk import state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

import exit_check  # noqa: E402  (the tool module scripts/exit_check.py: the saved-book reader and its errors)

TOOL = "scenario"
DESCRIPTION = ("the book re-priced under spot and volatility moves (21 cells), or an event stress of one symbol through "
               "its earnings or ex-dividend date, on the book's model of record: a computation, not a forecast")
NETWORK_ACCESS = ("the book's one quotes request: GET https://data.alpaca.markets/v2/stocks/quotes/latest and "
                  "/v1beta1/options/quotes/latest, through the quote cache; none with --offline")
OUTPUT: Dict[str, Any] = {
    "verdict": "ok | stressed | quiet | unavailable",
    "exit_code": "0 | 10 | 20",
    "message": "string | null (the text printed; null when --upcoming finds no event)",
    "as_of": "RFC 3339 | null (the newest quote stamp of the book)",
    "stresses": [{"horizon": "object (as horizon)", "stressed": ["group id"], "excluded": ["object"],
                  "cells": ["object (as cells)"], "notes": ["string"]}],
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


UPCOMING_MAX: int = 10
SNAPSHOT_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def stress_dict(result: S.Stress) -> Dict[str, Any]:
    return {"horizon": result.horizon.as_dict(), "stressed": list(result.stressed),
            "excluded": [item.as_dict() for item in result.excluded], "cells": [item.as_dict() for item in result.cells],
            "notes": list(result.notes)}


def upcoming_horizons(run: Any, days: int) -> List[S.Horizon]:
    """Every event of a held symbol from today's ET date to the ``days``-th trading day after it, by date."""
    today = run.session.et_date
    extra = calendar_events.extra_closures(run.calendar)
    last = today
    for _ in range(days):
        last = market_clock.next_trading_day(last, extra)
    held = sorted({group["underlying"] for group in run.document["groups"]
                   if any(leg["status"] == "open" for leg in run.document["legs"] if leg["row_number"] in group["legs"])})
    found: List[S.Horizon] = []
    for symbol in held:
        for event in S.EVENTS:
            try:
                horizon = S.event_horizon(run.calendar, symbol, event, today, extra)
            except S.StressUnavailable:
                continue
            assert horizon.event_date is not None
            if horizon.event_date <= last:
                found.append(horizon)
    return sorted(found, key=lambda item: (item.event_date, item.symbol or "", item.event or ""))


@dataclass
class BookRunView:
    """What the stress needs of a book: the document, its calendar and today's ET session (a marked book or a
    snapshot of record)."""

    document: Dict[str, Any]
    calendar: Optional[calendar_events.CalendarFile]
    session: Any
    snapshot: Optional[str] = None


def marked_book(args: Any, now: datetime.datetime, version: str) -> BookRunView:
    """The book marked now from quotes, exactly as ``book.py`` marks it."""
    try:
        market = quote_cache.open_market_data(args.desk_root, now=now, offline=args.offline,
                                              fixture_set=args.fixture_set, timeout_s=args.timeout,
                                              user_agent=user_agent(version))
    except desk_paths.DeskPathsError as exc:
        raise exit_check.ToolError("desk_root", str(exc), contract.EXIT_ERROR) from None
    except desk_config.DeskConfigError as exc:
        raise exit_check.ToolError("config_invalid", str(exc), contract.EXIT_ERROR) from None
    except quote_cache.MarketDataRefused as exc:
        raise exit_check.ToolError(exc.status, exc.message, contract.EXIT_UNAVAILABLE) from None
    except OSError as exc:
        raise exit_check.ToolError("io_error", "cannot prepare the desk runtime directories: %s" % exc,
                                   contract.EXIT_ERROR) from None
    try:
        run = book.load_book(market)
    except book.BookInputError as exc:
        raise exit_check.ToolError(exc.code, exc.message, contract.EXIT_ERROR) from None
    except OSError as exc:
        raise exit_check.ToolError("io_error", str(exc), contract.EXIT_ERROR) from None
    return BookRunView(run.document, run.calendar, run.session)


def latest_snapshot(desk_root: str, now: datetime.datetime) -> BookRunView:
    """The latest end-of-day snapshot of record dated on or before today's ET date (none: exit 20)."""
    paths = exit_check.open_paths(desk_root)
    today = market_clock.to_et(now).date()
    names = []
    if os.path.isdir(paths.book_dir):
        for name in os.listdir(paths.book_dir):
            match = SNAPSHOT_NAME_RE.match(name)
            if match and datetime.date.fromisoformat(match.group(1)) <= today:
                names.append(name)
    if not names:
        raise exit_check.ToolError("snapshot_missing", "no end-of-day snapshot under data/book/ on or before %s: "
                                   "book.py --snapshot writes one at the end of each trading day" % today.isoformat(),
                                   contract.EXIT_UNAVAILABLE)
    path = os.path.join(paths.book_dir, max(names))
    source = exit_check.load_saved_book(paths, path)
    return BookRunView(source.document, source.calendar, market_clock.session_at(now), paths.relative(path))


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--event", choices=S.EVENTS, default=None,
                        help="price the symbol's groups one trading day after this event (needs --symbol)")
    parser.add_argument("--symbol", metavar="SYM", default=None, help="limit the stress to this underlying's groups")
    parser.add_argument("--upcoming", metavar="N", type=int, default=None,
                        help="one event stress per held symbol with an earnings or ex-dividend date within N trading "
                             "days (1-%d); quiet when none" % UPCOMING_MAX)
    parser.add_argument("--from-snapshot", action="store_true",
                        help="stress the latest end-of-day snapshot of record instead of marking the book from quotes")
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


def header(result: S.Stress, document: Dict[str, Any], snapshot: Optional[str] = None) -> str:
    horizon = result.horizon
    as_of = document["as_of_label"]
    if snapshot is not None:
        as_of = "the end-of-day snapshot of %s, %s" % (os.path.basename(snapshot)[:10], as_of)
    if horizon.event is None:
        return "SCENARIO | the book re-priced, %s | %s" % (as_of, S.LABEL)
    words = "earnings" if horizon.event == S.EVENT_EARNINGS else "ex-dividend date"
    when = "" if not horizon.event_time else " (%s)" % horizon.event_time
    assert horizon.event_date is not None and horizon.horizon_date is not None and horizon.utc is not None
    close = market_clock.to_et(horizon.utc).strftime("%H:%M")
    return ("EVENT STRESS | %s %s %s%s | priced at the %s ET close of %s, one trading day after | %s"
            % (horizon.symbol, words, horizon.event_date.isoformat(), when, close, horizon.horizon_date.isoformat(),
               S.LABEL))


def render(result: S.Stress, document: Dict[str, Any], snapshot: Optional[str] = None) -> List[str]:
    lines = [header(result, document, snapshot)]
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
    if args.from_snapshot and args.fixture_set is not None:
        parser.error("--fixture-set applies to a book marked from quotes, not to --from-snapshot")
    if args.event is not None and args.symbol is None:
        parser.error("--event needs --symbol")
    if args.upcoming is not None and (args.event is not None or args.symbol is not None):
        parser.error("--upcoming finds its own events: give it without --event or --symbol")
    if args.upcoming is not None and not 1 <= args.upcoming <= UPCOMING_MAX:
        parser.error("--upcoming must be 1-%d trading days" % UPCOMING_MAX)
    if args.symbol is not None and not quote_cache.SYMBOL_RE.match(args.symbol.upper()):
        parser.error("--symbol %r is not a ticker" % args.symbol)
    symbol = None if args.symbol is None else args.symbol.upper()
    now = args.now or state.utc_now()
    try:
        run = latest_snapshot(args.desk_root, now) if args.from_snapshot else marked_book(args, now, version)
    except exit_check.ToolError as exc:
        return fail(args, version, now, exc.code, exc.message, exc.exit_code)
    document = run.document
    stamps = [leg["as_of"] for leg in document["legs"] if leg["as_of"] is not None]
    as_of = max(stamps) if stamps else now
    warnings = list(document.get("warnings") or [])
    reason: Optional[str] = None
    results: List[S.Stress] = []
    horizon = S.Horizon()
    if args.upcoming is not None:
        for found in upcoming_horizons(run, args.upcoming):
            results.append(S.stress(document, run.calendar, horizon=found, symbol=found.symbol))
    else:
        if args.event is not None:
            assert symbol is not None
            try:
                horizon = S.event_horizon(run.calendar, symbol, args.event, run.session.et_date,
                                          calendar_events.extra_closures(run.calendar))
            except S.StressUnavailable as exc:
                reason = exc.reason
        if reason is None:
            single = S.stress(document, run.calendar, horizon=horizon, symbol=symbol)
            if not single.stressed and not single.excluded:
                reason = "no open group%s in the book" % ("" if symbol is None else " on %s" % symbol)
            else:
                results.append(single)
    excluded = any(item.excluded for item in results)
    if reason is not None or excluded:
        verdict, exit_code = "unavailable", contract.EXIT_UNAVAILABLE
    elif args.upcoming is not None and results:
        verdict, exit_code = "stressed", contract.EXIT_FIRED
    elif args.upcoming is not None:
        verdict, exit_code = "quiet", contract.EXIT_QUIET
    else:
        verdict, exit_code = "ok", contract.EXIT_QUIET
    if reason is not None:
        lines = ["UNAVAILABLE | %s %s | %s" % (TOOL if args.event is None else "event stress", symbol or "book", reason)]
    else:
        lines = []
        for index, item in enumerate(results):
            if index:
                lines.append("")
            lines.extend(render(item, document, run.snapshot))
    message = "\n".join(lines) if lines else None
    if args.json:
        first = results[0] if results else None
        payload = {"verdict": verdict, "exit_code": exit_code, "label": S.LABEL, "message": message,
                   "as_of": as_of, "stresses": [stress_dict(item) for item in results],
                   "horizon": (first.horizon if first is not None else horizon).as_dict(),
                   "grid": {"spot_pct": list(S.GRID_SPOT_PCT), "vol_points": list(S.GRID_VOL_POINTS)},
                   "stressed": [] if first is None else list(first.stressed),
                   "excluded": [] if first is None else [item.as_dict() for item in first.excluded],
                   "cells": [] if first is None else [item.as_dict() for item in first.cells],
                   "notes": [] if first is None else list(first.notes), "reason": reason, "warnings": warnings}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    print(message if message is not None else "quiet: no held symbol has an earnings or ex-dividend date within %d "
          "trading days" % args.upcoming)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
