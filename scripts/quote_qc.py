#!/usr/bin/env python3
"""quote_qc.py: grade the desk's quotes A-F with session-aware, per-side freshness.

Usage::

    python3 scripts/quote_qc.py SYMBOL [SYMBOL ...] [--force] [--fixture-set NAME] [--json] [--schema]
        [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Reads the quotes through ``aegis_desk.quote_cache.get_quotes`` (so a quote is never fetched twice
in one run across the tools) and grades each with ``aegis_desk.quote_qc.assess`` against the desk's
freshness bound (``feed_delay_min + quote_ttl_min + grace_min``) and the session at ``now``:
regular hours per side against ``now``, after the close against the close ("as of close HH:MM
ET"), before the open against the prior close, and one "NYSE closed" verdict on weekends, holidays
and the calendar's full closures. The session and the prior close are ``--now`` classified with the
full closures of ``research/calendar.toml`` (``[[closures]] kind = "full"``, read when the file is
present; without the file, the embedded NYSE table alone), the rule every desk tool that classifies the
session from a desk root follows (``market_clock.py`` reads no desk file: the embedded table alone): an
owner-injected closure on today's ET date is a closed day (nothing fetched, every symbol
grade F ``NYSE closed``, exit 20), and a closure on the previous trading day moves the prior close
(the reference and the label before the open) back to the last session that was held. An
``early_close`` entry is never injected. Prints one line per symbol::

    SYMBOL grade status bid/ask spread% mid liq(long) liq(short) label

and the footer ``Tradier sandbox, delayed 15 min, as of HH:MM UTC``. Exit 0 when every symbol graded
A-D (usable for rules), 20 otherwise (E, F, missing: UNAVAILABLE, never HOLD), 1 on a configuration
or file error (``desk_root``, ``config_invalid``, ``calendar_invalid``: a calendar file that cannot be used,
``io_error``: a file where a runtime directory belongs), 2 on usage (a symbol that fails the
client's pattern included). ``--json`` payload: ``{"symbols": [QuoteQC as dict + served_from ...],
"bound_min", "session", "missing", "warnings", "fixture_set"}``. The same production-host and
missing-token refusals as ``quote_cache.py`` (exit 20).
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="quote_qc")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import calendar_events, desk_config, desk_paths, market_clock, quote_cache, quote_qc, state  # noqa: E402
from aegis_desk.market_types import Quote  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "quote_qc"
DESCRIPTION = ("grade quotes A-F: session-aware per-side freshness against the desk's bound, spread bands, "
               "fair and liquidation marks; stale or missing is UNAVAILABLE (exit 20), never HOLD")
NETWORK_ACCESS = "GET https://sandbox.tradier.com/v1/markets/quotes only (through the quote cache); none with --offline"
LABEL_NO_QUOTE = "no quote"
OUTPUT: Dict[str, Any] = {
    "symbols": [{"symbol": "string", "grade": "A | B | C | D | E | F",
                 "status": "fresh | stale | one_side_stale | undated | closed_day | missing",
                 "label": "as of HH:MM:SS ET | as of close HH:MM ET | as of prior close YYYY-MM-DD HH:MM ET | NYSE closed | no quote",
                 "age_min_bid": "integer | null", "age_min_ask": "integer | null", "spread_pct": "decimal string (2 dp) | null",
                 "mark_mid": "decimal string | null", "mark_liq_long": "decimal string (bid) | null",
                 "mark_liq_short": "decimal string (ask) | null", "usable_for_rules": "boolean (grade A-D)",
                 "reasons": ["string"], "bid": "decimal string | null", "ask": "decimal string | null",
                 "as_of": "ISO-8601 UTC | null", "served_from": "fetch | cache | null"}],
    "bound_min": "integer minutes (feed_delay_min + quote_ttl_min + grace_min)",
    "session": ("market_clock.SessionInfo.as_dict() (--now classified with the calendar's full closures: an "
                "injected closure is session holiday)"),
    "missing": {"SYMBOL": "the cache's reason"},
    "warnings": ["string"],
    "fixture_set": "string | null (offline runs)",
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("symbols", nargs="*", metavar="SYMBOL", help="equity, ETF or OCC option symbols to grade")
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


def missing_label(session: market_clock.SessionInfo, prior_close: datetime.datetime, copy: Optional[Quote]) -> str:
    if session.session in (market_clock.SESSION_WEEKEND, market_clock.SESSION_HOLIDAY):
        return quote_qc.LABEL_CLOSED_DAY
    if copy is not None:
        return quote_qc.session_label(session, prior_close, copy.as_of)
    return LABEL_NO_QUOTE


def qc_line(qc: quote_qc.QuoteQC) -> str:
    money = quote_cache.format_money
    spread = "-" if qc.spread_pct is None else money(qc.spread_pct) + "%"
    return "%s %s %s %s/%s %s %s %s %s %s" % (qc.symbol, qc.grade, qc.status, money(qc.bid), money(qc.ask), spread,
                                            money(qc.mark_mid), money(qc.mark_liq_long), money(qc.mark_liq_short),
                                            qc.label)


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

    try:
        cal = calendar_events.load_calendar_if_present(market.paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        return fail(args, version, now, "calendar_invalid", str(exc), contract.EXIT_ERROR)
    # --now classified with the calendar's full closures, as in every desk tool that classifies the session
    # from a desk root: a closure is a closed day, and the prior close (the reference before the open) skips it.
    extra = calendar_events.extra_closures(cal)
    session = market_clock.session_at(now, extra_holidays=extra)
    prior_close = market_clock.previous_close_utc(now, extra_holidays=extra)
    try:
        result = quote_cache.get_quotes(args.symbols, now=now, cfg=market.cache, client=market.client,
                                        paths=market.paths, force=args.force, allow_fetch=session.trading_day,
                                        run=market.run)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot use the desk cache: %s" % exc, contract.EXIT_ERROR)

    verdicts: List[quote_qc.QuoteQC] = []
    for symbol in args.symbols:
        quote = result.quotes.get(symbol)
        if quote is not None:
            verdicts.append(quote_qc.assess(quote, now=now, session=session, bound_min=market.bound_min,
                                            prior_close_utc=prior_close))
        else:
            copy = result.stale.get(symbol)
            verdicts.append(quote_qc.unavailable(symbol, "cache: %s" % result.missing.get(symbol, "not served"),
                                                 label=missing_label(session, prior_close, copy), quote=copy))
    exit_code = contract.EXIT_QUIET if all(qc.usable_for_rules for qc in verdicts) else contract.EXIT_UNAVAILABLE
    shown = [qc.as_of for qc in verdicts if qc.as_of is not None]
    footer_as_of = max(shown) if shown else now

    if args.json:
        payload: Dict[str, Any] = {
            "symbols": [dict(qc.as_dict(), served_from=result.served_from.get(qc.symbol)) for qc in verdicts],
            "bound_min": market.bound_min,
            "session": session.as_dict(),
            "missing": dict(result.missing),
            "warnings": list(result.warnings),
            "fixture_set": market.fixture_set,
        }
        body = contract.envelope(TOOL, version, payload, source=quote_cache.source_label(market.cfg),
                                 delay_min=market.cfg.market_data.feed_delay_min, generated_at=now,
                                 status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(body))
        return exit_code

    lines = [qc_line(qc) for qc in verdicts]
    for warning in result.warnings:
        lines.append("warning: %s" % warning)
    if not session.trading_day:
        name = session.holiday_name or ("weekend" if session.session == market_clock.SESSION_WEEKEND else "closed")
        lines.append("NYSE closed (%s): no quote fetch; nothing is usable for rules today" % name)
    else:
        lines.append("session %s; freshness bound %d min" % (session.session, market.bound_min))
    lines.append(quote_cache.footer_line(market.cfg, footer_as_of))
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
