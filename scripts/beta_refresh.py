#!/usr/bin/env python3
"""beta_refresh.py: each held underlying's beta against SPY from split-adjusted daily closes (roadmap 2.7).

Usage::

    python3 scripts/beta_refresh.py [--fixture-set NAME] [--json] [--schema] [--timeout S] [--desk-root DIR]
        [--offline] [--now ISO-8601-UTC]

Reads ``positions.csv`` for the held underlyings and fetches, through the quote cache, each one's daily bars and
SPY's with ``adjustment=split`` (the same GET path as every daily bar, ``/v2/stocks/bars``), ending at the trading day
before today's ET date and starting 400 calendar days earlier, so the last 252 closes are there. ``aegis_desk.beta``
fits each: OLS of daily simple returns on SPY's, UNAVAILABLE with fewer than 126 paired returns or a daily return
beyond 40 % in either series (an unadjusted event or bad data), ``r2_flag`` ``ok`` at r2 >= 0.30 and ``low`` below.
Closed bars are final, so a second run on the same day reads the cache and sends nothing.

It writes ``data/beta/<ET date>.json`` (``aegis_desk.beta.document``), the file the book reads to fill
``beta_weighted_delta`` and each underlying's ``beta``; it is the file's only writer. No order path, nothing else
written. Exit codes: 0 every held symbol fitted; 20 any fit UNAVAILABLE (still written: the book then reads that
symbol's beta as UNAVAILABLE); 1 a desk file that cannot be used; 2 usage. ``--json`` payload: as ``--schema``
describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="beta_refresh")

import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import beta, calendar_events, desk_config, desk_paths, market_clock, positions_csv, quote_cache, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "beta_refresh"
DESCRIPTION = ("each held underlying's beta against SPY from split-adjusted daily closes (252 closes to the previous "
               "trading day), written to data/beta/<ET date>.json for the book's beta-weighted delta")
NETWORK_ACCESS = ("GET https://data.alpaca.markets/v2/stocks/bars (adjustment=split) only, through the quote cache; "
                  "none with --offline")
LOOKBACK_DAYS: int = 400
"""Calendar days of bars asked for: enough for 252 closes before the previous trading day."""
OUTPUT: Dict[str, Any] = {
    "verdict": "ok | unavailable",
    "exit_code": "0 | 20",
    "et_date": "YYYY-MM-DD",
    "bars_start": "YYYY-MM-DD",
    "bars_end": "YYYY-MM-DD (the previous trading day)",
    "written": "data/beta/<ET date>.json",
    "betas": [{"symbol": "string", "beta": "float | null", "r2": "float | null",
               "r2_flag": "ok | low | unavailable", "returns": "integer", "reason": "string | null"}],
    "warnings": ["string"],
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
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


def line(item: beta.BetaFit) -> str:
    """``BETA | NVDA | 2.04 (r2 0.50, ok) over 251 daily returns``, ``BETA | SPY | 1.00 (the benchmark)`` or
    ``UNAVAILABLE | NVDA beta | <reason>``."""
    if item.symbol == beta.BENCHMARK:
        return "BETA | %s | 1.00 (the benchmark)" % item.symbol
    if item.beta is None:
        return "UNAVAILABLE | %s beta | %s" % (item.symbol, item.reason or "no fit")
    return "BETA | %s | %.2f (r2 %.2f, %s) over %d daily returns" % (item.symbol, item.beta, item.r2 or 0.0,
                                                                   item.r2_flag, item.returns)


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
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
    extra = calendar_events.extra_closures(cal)
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
    end = market_clock.previous_trading_day(today, extra)
    start = end - datetime.timedelta(days=LOOKBACK_DAYS)
    warnings: List[str] = []

    def closes(symbol: str) -> List[Any]:
        result = quote_cache.get_history(symbol, start, end, now=now, cfg=market.cache, client=market.client,
                                         paths=paths, run=market.run, adjustment="split")
        warnings.extend(result.warnings)
        return [(bar.date, bar.close) for bar in result.bars if bar.close is not None]

    benchmark = closes(beta.BENCHMARK) if any(symbol != beta.BENCHMARK for symbol in held) else []
    fits = [beta.fit(symbol, closes(symbol) if symbol != beta.BENCHMARK else [], benchmark) for symbol in held]
    written = beta.beta_path(paths.beta_dir, today)
    try:
        os.makedirs(paths.beta_dir, exist_ok=True)
        with state.locked(paths.lock_path, timeout_s=5.0):
            state.atomic_write_json(written, beta.document(today, end, now, fits))
    except (OSError, state.LockBusy) as exc:
        return fail(args, version, now, "io_error", "cannot write %s: %s" % (paths.relative(written), exc),
                    contract.EXIT_ERROR)
    unavailable = any(item.beta is None for item in fits)
    exit_code = contract.EXIT_UNAVAILABLE if unavailable else contract.EXIT_QUIET
    if args.json:
        payload = {"verdict": "unavailable" if unavailable else "ok", "exit_code": exit_code,
                   "et_date": today.isoformat(), "bars_start": start.isoformat(), "bars_end": end.isoformat(),
                   "written": paths.relative(written), "betas": [item.as_dict() for item in fits],
                   "warnings": warnings}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    lines = [line(item) for item in fits] or ["no held underlying: nothing to fit"]
    lines.append("source: split-adjusted daily closes from the IEX feed, %s to %s; written to %s"
                 % (start.isoformat(), end.isoformat(), paths.relative(written)))
    lines.extend("warning: %s" % warning for warning in warnings)
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
