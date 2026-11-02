#!/usr/bin/env python3
"""quote_cache.py: serve the desk's quotes from the cache or the GET-only Tradier client.

Usage::

    python3 scripts/quote_cache.py SYMBOL [SYMBOL ...] [--chain SYMBOL --expiration YYYY-MM-DD]
        [--force] [--fixture-set NAME] [--json] [--schema] [--timeout S] [--desk-root DIR]
        [--offline] [--now ISO-8601-UTC]

Reads ``desk.toml`` under ``--desk-root``, refuses ``host = "production"`` (exit 20: production
market data waits for the market-data proxy, plan section 10), builds the client (``--offline``
serves the recorded fixture set ``fixtures/tradier/<NAME>`` of the desk root, default ``regular``;
online, the token comes from the configured environment variable or file, and a missing token is
exit 20, never 1) and prints one line per symbol::

    SYMBOL bid/ask mid as-of HH:MM:SS ET (age N min) source

with ``source`` ``fetch`` or ``cache`` (``stale`` for an expired copy the fetch could not refresh,
display only), ``SYMBOL UNAVAILABLE: <reason>`` for anything not served, the chain when asked, and
the footer ``Tradier sandbox, delayed 15 min, as of HH:MM UTC``. On a weekend or holiday nothing is
fetched (one ``NYSE closed`` line; cached copies are shown). ``--force`` asks for fresher copies and
never discards a copy that is still inside the TTL. Exit 0 when every requested symbol (and the
chain) was served, 20 when any is missing, 1 on a configuration or file error (``desk_root``,
``config_invalid``, ``io_error``: a file where a runtime directory belongs), 2 on usage (a symbol
that fails the client's pattern included). ``--json`` prints the shared envelope with ``payload = {"quotes": [...], "stale": [...],
"missing": {...}, "requests_made", "budget": {"window_60s_used", "left"}, "session", "warnings",
"fixture_set"[, "chain"]}``; ``source`` is ``tradier:sandbox`` and ``delay_min`` the feed delay.
The library (``aegis_desk.quote_cache``) never reads the wall clock: ``now`` is ``--now`` or the
clock read once here.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="quote_cache")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import desk_config, desk_paths, market_clock, quote_cache, state  # noqa: E402
from aegis_desk.market_types import MarketTypesError, Quote, parse_iso_date  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "quote_cache"
DESCRIPTION = ("serve quotes from the desk cache or the GET-only Tradier client (TTL, 60/min budget, "
               "chains on demand); never places, modifies or cancels an order")
NETWORK_ACCESS = "GET https://sandbox.tradier.com/v1/markets/{quotes,options/chains} only; none with --offline"
OUTPUT: Dict[str, Any] = {
    "quotes": [{"symbol": "string", "type": "stock | etf | option | index | null", "bid": "decimal string | null",
                "ask": "decimal string | null", "mid": "decimal string | null", "last": "decimal string | null",
                "bid_date": "ISO-8601 UTC | null", "ask_date": "ISO-8601 UTC | null", "as_of": "ISO-8601 UTC | null",
                "age_min": "integer minutes from as_of to now | null", "source": "fetch | cache",
                "fetched_utc": "ISO-8601 UTC", "underlying": "string | null", "strike": "decimal string | null",
                "expiration_date": "YYYY-MM-DD | null", "option_type": "call | put | null"}],
    "stale": ["quote objects as above (source stale): expired cache copies the fetch could not refresh; display only"],
    "missing": {"SYMBOL": "unmatched | quota_guard | per_run_cap | market_closed | fetch_failed:<status>"},
    "requests_made": "integer requests this run",
    "budget": {"window_60s_used": "integer (ledger records in the window plus this run's own requests)",
               "left": "integer (60 - 5 headroom - used)"},
    "session": "market_clock.SessionInfo.as_dict()",
    "warnings": ["string"],
    "fixture_set": "string | null (offline runs)",
    "chain": {"symbol": "string", "expiration": "YYYY-MM-DD", "served_from": "fetch | cache | null",
              "missing_reason": "string | null", "count": "integer", "options": ["quote objects (strike, option_type, bid, ask, mid)"]},
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("symbols", nargs="*", metavar="SYMBOL", help="equity, ETF or OCC option symbols to quote")
    parser.add_argument("--chain", metavar="SYMBOL", default=None,
                        help="also fetch one option chain (on demand only; needs --expiration)")
    parser.add_argument("--expiration", metavar="YYYY-MM-DD", default=None, help="the chain's expiration date")
    parser.add_argument("--force", action="store_true", help="refetch even when the cached copy is inside the TTL")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/tradier/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    symbols: List[str] = [symbol.strip().upper() for symbol in args.symbols]
    if not symbols:
        parser.error("at least one SYMBOL is required")
    if any(not symbol for symbol in symbols):
        parser.error("a SYMBOL must not be blank")
    for symbol in symbols:
        if not quote_cache.SYMBOL_RE.fullmatch(symbol):
            parser.error("SYMBOL %r is not a valid symbol (pattern %s)" % (symbol, quote_cache.SYMBOL_RE.pattern))
    args.symbols = symbols
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    if (args.chain is None) != (args.expiration is None):
        parser.error("--chain SYMBOL and --expiration YYYY-MM-DD must be given together")
    args.expiration_date = None
    if args.expiration is not None:
        try:
            args.expiration_date = parse_iso_date(args.expiration, "--expiration")
        except MarketTypesError:
            args.expiration_date = None
        if args.expiration_date is None:
            parser.error("--expiration must be a YYYY-MM-DD date, got %r" % (args.expiration,))
        args.chain = args.chain.strip().upper()
        if not args.chain:
            parser.error("--chain SYMBOL must not be blank")
        if not quote_cache.SYMBOL_RE.fullmatch(args.chain):
            parser.error("--chain %r is not a valid symbol (pattern %s)" % (args.chain, quote_cache.SYMBOL_RE.pattern))


def quote_entry(symbol: str, quote: Quote, source: str, fetched_utc: Optional[datetime.datetime],
                now: datetime.datetime) -> Dict[str, Any]:
    return {
        "symbol": symbol, "type": quote.type, "bid": quote.bid, "ask": quote.ask, "mid": quote.mid, "last": quote.last,
        "bid_date": quote.bid_date, "ask_date": quote.ask_date, "as_of": quote.as_of,
        "age_min": quote_cache.age_minutes(now, quote.as_of), "source": source, "fetched_utc": fetched_utc,
        "underlying": quote.underlying, "strike": quote.strike, "expiration_date": quote.expiration_date,
        "option_type": quote.option_type,
    }


def quote_line(symbol: str, quote: Quote, source: str, now: datetime.datetime) -> str:
    as_of = quote.as_of
    stamp = "undated" if as_of is None else market_clock.to_et(as_of).strftime("%H:%M:%S") + " ET"
    age = quote_cache.age_minutes(now, as_of)
    return "%s %s/%s %s as-of %s (age %s min) %s" % (
        symbol, quote_cache.format_money(quote.bid), quote_cache.format_money(quote.ask),
        quote_cache.format_money(quote.mid), stamp, "n/a" if age is None else age, source)


def session_line(session: market_clock.SessionInfo) -> str:
    et = market_clock.to_et(session.as_of_utc).strftime("%Y-%m-%d %H:%M:%S")
    if not session.trading_day:
        name = session.holiday_name or ("weekend" if session.session == market_clock.SESSION_WEEKEND else "closed")
        return "NYSE closed (%s, %s ET): no quote fetch; cached copies only" % (name, et)
    return "session %s (%s ET)" % (session.session, et)


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    status = contract.status_for(exit_code)
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, status, generated_at=now)))
    elif exit_code == contract.EXIT_UNAVAILABLE:
        print("UNAVAILABLE: %s" % message)
    else:
        print("error: %s" % message)
    return exit_code


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

    session = market.session
    chain = None
    try:
        result = quote_cache.get_quotes(args.symbols, now=now, cfg=market.cache, client=market.client,
                                        paths=market.paths, force=args.force, allow_fetch=session.trading_day,
                                        run=market.run)
        if args.chain is not None:
            chain = quote_cache.get_chain(args.chain, args.expiration_date, now=now, cfg=market.cache,
                                          client=market.client, paths=market.paths, force=args.force,
                                          allow_fetch=session.trading_day, run=market.run)
        used = quote_cache.requests_counted(market.paths, now, market.run)
        left = quote_cache.budget_left(market.paths, now, market.cache, market.run)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot use the desk cache: %s" % exc, contract.EXIT_ERROR)

    requests_made = result.requests_made + (chain.requests_made if chain is not None else 0)
    warnings = list(result.warnings) + (list(chain.warnings) if chain is not None else [])
    served_ok = not result.missing and (chain is None or chain.missing_reason is None)
    exit_code = contract.EXIT_QUIET if served_ok else contract.EXIT_UNAVAILABLE
    shown = [quote.as_of for quote in list(result.quotes.values()) + list(result.stale.values())
             if quote.as_of is not None]
    footer_as_of = max(shown) if shown else now

    if args.json:
        payload: Dict[str, Any] = {
            "quotes": [quote_entry(s, result.quotes[s], result.served_from[s], result.fetched_utc.get(s), now)
                       for s in args.symbols if s in result.quotes],
            "stale": [quote_entry(s, result.stale[s], "stale", None, now) for s in args.symbols if s in result.stale],
            "missing": dict(result.missing),
            "requests_made": requests_made,
            "budget": {"window_60s_used": used, "left": left},
            "session": session.as_dict(),
            "warnings": warnings,
            "fixture_set": market.fixture_set,
        }
        if chain is not None:
            payload["chain"] = {
                "symbol": chain.symbol, "expiration": chain.expiration, "served_from": chain.served_from,
                "missing_reason": chain.missing_reason, "count": len(chain.options),
                "fetched_utc": chain.fetched_utc,
                "options": [quote_entry(o.symbol, o, chain.served_from or "", chain.fetched_utc, now)
                            for o in chain.options],
            }
        body = contract.envelope(TOOL, version, payload, source=quote_cache.source_label(market.cfg),
                                 delay_min=market.cfg.market_data.feed_delay_min, generated_at=now,
                                 status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(body))
        return exit_code

    lines: List[str] = []
    for symbol in args.symbols:
        if symbol in result.quotes:
            lines.append(quote_line(symbol, result.quotes[symbol], result.served_from[symbol], now))
        elif symbol in result.stale:
            lines.append(quote_line(symbol, result.stale[symbol], "stale (%s)" % result.missing.get(symbol, ""), now))
        else:
            lines.append("%s UNAVAILABLE: %s" % (symbol, result.missing.get(symbol, "not served")))
    if chain is not None:
        if chain.missing_reason is not None:
            lines.append("chain %s %s UNAVAILABLE: %s" % (chain.symbol, chain.expiration.isoformat(), chain.missing_reason))
        else:
            lines.append("chain %s %s: %d contracts (%s)" % (chain.symbol, chain.expiration.isoformat(),
                                                             len(chain.options), chain.served_from))
            for option in chain.options:
                lines.append("  %s %s %s %s/%s %s" % (
                    option.symbol, option.option_type or "-", quote_cache.format_money(option.strike),
                    quote_cache.format_money(option.bid), quote_cache.format_money(option.ask),
                    quote_cache.format_money(option.mid)))
    for warning in warnings:
        lines.append("warning: %s" % warning)
    lines.append("%s; requests %d; 60-s window used %d, left %d" % (session_line(session), requests_made, used, left))
    lines.append(quote_cache.footer_line(market.cfg, footer_as_of))
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
