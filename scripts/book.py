#!/usr/bin/env python3
"""book.py: the book of record, marked from the desk's quotes, with the EOD snapshot and the report.

Usage::

    python3 scripts/book.py [--snapshot] [--report] [--fixture-set NAME] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Loads ``desk.toml``, ``positions.csv`` (quarantined groups are listed, not fatal), ``research/calendar.toml``
when present and the previous trading day's snapshot from ``data/book/``, fetches every open leg and every
underlying in ONE quotes request through ``aegis_desk.quote_cache.get_quotes`` (never a chain), grades the
quotes with ``aegis_desk.quote_qc.assess`` and builds the book through ``aegis_desk.book.load_book`` (the
library entry point PR 2c's tools share). Prints one line per group followed by one per leg (structure, marks,
P/L on the mid and liquidation bases, grade, IV, DTE), the portfolio line, the warnings, the session label and
the footer ``Tradier sandbox, delayed 15 min, as of HH:MM UTC``.

The session the book is graded in (``session``, ``as_of_label``, the fetch decision, the session line and the
snapshot's date) is ``--now`` classified with the calendar's full closures (``[[closures]] kind = "full"`` in
``research/calendar.toml``), the rule every desk tool that classifies the session from a desk root follows
(``aegis_desk.book`` module notes, "Session"; ``market_clock.py`` reads no desk file and uses the embedded NYSE
table alone): an owner-injected closure on today's ET date is a closed day for the book: no quote fetch,
``NYSE closed (calendar closure): no quote fetch; nothing is usable for rules today``, every open group
unavailable, exit 20, while the ET date (the snapshot's name, ``positions.csv``'s ``today``) and the request
batch are unchanged.

``--snapshot`` writes ``data/book/<ET date>.json`` atomically under the desk lock (the artifact of record the
next day's day P/L is measured against); ``--report`` writes ``reports/book.md``. Both are written whatever the
exit code: an UNAVAILABLE book is still the record of what was known.

Totals (``aegis_desk.book`` module notes): ``portfolio.unrealized_pl_*`` and ``portfolio.day_pl`` sum the groups
with ``data_quality`` ``ok``; ``day_pl`` also leaves out, as a whole, any ok group with an open leg that has no
day P/L (not in the previous snapshot, or its previous mark was display-only), named in ``warnings`` as
``<group> excluded from the day P/L total: ...``, and is ``null`` when no open group has one: a group's day P/L
is never partially summed. Every leg carries ``open_quantity`` and ``remaining_cost_basis`` beside the row's
``quantity`` and ``cost_basis_total``: the marks and P/L apply to the open part. Every leg also carries
``fresh_bid`` and ``fresh_ask``, the quote's fresh sides for any grade A-E (a grade-E leg keeps them although its
``mark_liq`` is null; a fresh ask of 0 stays 0, which is no offer and not a price to close at; only ``mark_source``
``mid`` marks a leg), and its ``extrinsic_bid`` is the assignment watch's geometry (the fresh bid less the
intrinsic value at the underlying's usable mid stamped within 60 s of the option), so the two tools print one
number for one leg.

Exit 0 when every open group is ``ok``, 20 when any group is unavailable or quarantined or the market data
is refused (production host, no token, expired holiday table), 1 on a configuration or file error
(``desk_root``, ``config_invalid``, ``positions_missing``, ``positions_invalid``, ``calendar_invalid``,
``io_error``, ``lock_busy``), 2 on usage. ``--json`` payload: the book dict exactly (plan section 8
``book.json``; money as decimal strings through the envelope's encoder).
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="book")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import book, desk_config, desk_paths, market_clock, quote_cache, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "book"
DESCRIPTION = ("the book of record: every position marked from the desk's quotes with IV, Greeks, P/L, payoff "
               "metrics and day P/L against the desk's own snapshot; unavailable is a verdict (exit 20), never a zero")
NETWORK_ACCESS = "GET https://sandbox.tradier.com/v1/markets/quotes only (through the quote cache); none with --offline"
GREEKS_SHAPE = {"delta": "float", "gamma": "float", "theta": "float per day", "vega": "float per vol point",
                "rho": "float per 1 %"}
OUTPUT: Dict[str, Any] = {
    "generated_at": "ISO-8601 UTC",
    "session": " | ".join(market_clock.SESSIONS) + " (--now classified with the calendar's full closures: an injected "
               "closure is holiday)",
    "as_of_label": "as of HH:MM:SS ET | as of close HH:MM ET | as of prior close YYYY-MM-DD HH:MM ET | NYSE closed",
    "data_quality": {"grades": {"A": "n", "B": "n", "C": "n", "D": "n", "E": "n", "F": "n"}, "usable_legs": "integer",
                     "unavailable_legs": "integer", "bound_min": "integer minutes"},
    "sources": [{"name": "tradier:sandbox", "delay_min": "integer minutes", "receipt_utc": "ISO-8601 UTC | null"}],
    "rates": {"risk_free_rate": "decimal string", "source": "string", "as_of": "YYYY-MM-DD"},
    "config_sha": {"desk_toml": "sha256", "positions_csv": "sha256", "calendar_toml": "sha256 | null"},
    "legs": [{"row_number": "integer (physical line)", "trade_id": "string | null", "group_id": "string", "account": "string",
              "instrument_type": "equity | option | etf | index | crypto", "symbol": "string", "occ": "compact OCC | null",
              "underlying": "string", "right": "CALL | PUT | null", "strike": "decimal string | null",
              "expiry": "YYYY-MM-DD | null", "settlement": "pm | am | null", "dte_calendar": "integer | null",
              "dte_trading": "integer | null", "quantity": "decimal string (signed)", "multiplier": "integer",
              "cost_basis_total": "decimal string (signed cash flow)", "entry_price_per_share": "decimal string",
              "open_quantity": "decimal string (the signed open quantity, the part the marks and P/L apply to)",
              "remaining_cost_basis": "decimal string (the open part's pro-rata cost basis)",
              "status": "open | closed", "quote_grade": "A-F | null", "quote_status": "fresh | stale | one_side_stale | "
              "undated | closed_day | missing | expired | closed", "bid": "decimal string | null",
              "ask": "decimal string | null", "bid_date": "ISO-8601 UTC | null", "ask_date": "ISO-8601 UTC | null",
              "as_of": "ISO-8601 UTC | null", "spread_pct": "decimal string | null", "mark_mid": "decimal string | null",
              "mark_liq": "decimal string | null",
              "mark_source": "mid | unavailable (mid exactly for grade A-D with both sides fresh: the only P/L basis)",
              "fresh_bid": "decimal string | null (QuoteQC.mark_liq_long: the bid when it is fresh, any grade A-E, kept "
                           "for grade E although mark_liq is null; null for F, closed, expired or unquoted legs)",
              "fresh_ask": "decimal string | null (QuoteQC.mark_liq_short: the ask when it is fresh, any grade A-E, kept "
                           "for grade E although mark_liq is null; a 0 ask stays 0: no offer, not a price to close at; "
                           "null for F, closed, expired or unquoted legs)",
              "tau_years": "float | null",
              "iv": "float | null", "iv_status": "ok | expiring | spot_unaligned | spot_unavailable | unavailable | "
              "expired | closed | not_option | below_intrinsic | above_bound | no_root",
              "greeks": dict(GREEKS_SHAPE), "position_greeks": dict(GREEKS_SHAPE),
              "unrealized_pl_mid": "decimal string | null", "unrealized_pl_liq": "decimal string | null",
              "day_pl": "decimal string | null", "realized_day_pl": "decimal string | null",
              "extrinsic_bid": "decimal string | null (fresh_bid - intrinsic at the underlying's usable mid stamped "
                               "within 60 s of the option: the assignment watch's geometry)",
              "itm": "boolean | null", "warnings": ["string"]}],
    "groups": [{"group_id": "string", "trade_id": "string | null", "account": "string", "underlying": "string",
                "structure": " | ".join(("long_stock", "short_stock", "long_call", "long_put", "short_call",
                                         "cash_secured_put", "covered_call", "protective_put", "collar",
                                         "vertical_credit_call", "vertical_credit_put", "vertical_debit_call",
                                         "vertical_debit_put", "custom", "invalid")),
                "basis_kind": "credit | debit | stock", "net_cost": "decimal string (positive debit, negative credit)",
                "max_profit": "decimal string | null", "max_loss": "decimal string (positive) | null",
                "breakevens": ["decimal string"], "unbounded_up": "boolean", "unbounded_down": "boolean",
                "unrealized_pl_liq": "decimal string | null", "unrealized_pl_mid": "decimal string | null",
                "pct_captured_liq": "decimal string | null", "pct_captured_mid": "decimal string | null",
                "realized_pl": "decimal string", "net_greeks": dict(GREEKS_SHAPE), "legs": ["row_number"],
                "data_quality": "ok | unavailable", "warnings": ["string"], "underlying_stop": "decimal string | null"}],
    "underlyings": {"SYMBOL": {"mark_mid": "decimal string | null", "bid": "decimal string | null",
                               "ask": "decimal string | null", "as_of": "ISO-8601 UTC | null", "quote_grade": "A-F | null",
                               "prev_eod_mid": "decimal string | null (the previous snapshot's mid)",
                               "move_pct_from_prev_eod": "decimal string (2 dp) | null",
                               "day_change_source": "desk snapshot"}},
    "portfolio": {"unrealized_pl_liq": "decimal string | null", "unrealized_pl_mid": "decimal string | null",
                  "day_pl": "decimal string | null (sum over the ok groups whose every open leg has a day P/L; an "
                  "ok group with a gap is excluded whole and named in warnings; null when none counts)",
                  "realized_pl_today": "decimal string",
                  "realized_pl_mtd": "decimal string", "realized_pl_ytd": "decimal string", "net_delta": "float | null",
                  "beta_weighted_delta": {"value": None, "r2_flag": "unavailable"},
                  "concentration": {"declared_equity": "decimal string", "by_underlying": {"SYMBOL": "percent (2 dp) | null"}},
                  "open_groups": "integer", "quarantined_groups": [{"group_id": "string", "reasons": ["string"]}]},
    "warnings": ["string"],
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--snapshot", action="store_true",
                        help="write data/book/<ET date>.json, the snapshot of record (atomic, under the desk lock)")
    parser.add_argument("--report", action="store_true", help="write reports/book.md")
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
        run = book.load_book(market)
    except book.BookInputError as exc:
        return fail(args, version, now, exc.code, exc.message, contract.EXIT_ERROR)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot read the desk files or use the cache: %s" % exc,
                    contract.EXIT_ERROR)
    document = run.document
    session = run.session  # --now classified with the calendar's full closures (aegis_desk.book, "Session")
    written: List[str] = []
    try:
        if args.snapshot:
            written.append(book.write_snapshot(market.paths, document, session.et_date))
        if args.report:
            written.append(book.write_report(market.paths, document))
    except state.LockBusy as exc:
        return fail(args, version, now, "lock_busy", str(exc), contract.EXIT_ERROR)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot write under the desk root: %s" % exc, contract.EXIT_ERROR)
    exit_code = run.exit_code

    if args.json:
        envelope = contract.envelope(TOOL, version, document, source=quote_cache.source_label(market.cfg),
                                     delay_min=market.cfg.market_data.feed_delay_min, generated_at=now,
                                     status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(envelope))
        return exit_code

    lines = book.render_lines(document)
    for warning in document["warnings"]:
        lines.append("warning: %s" % warning)
    for path in written:
        kind = "snapshot" if path.endswith(".json") else "report"
        lines.append("%s written: %s" % (kind, market.paths.relative(path)))
    if session.trading_day:
        lines.append("session %s; %s; freshness bound %d min" % (session.session, document["as_of_label"], market.bound_min))
    else:
        name = session.holiday_name or ("weekend" if session.session == market_clock.SESSION_WEEKEND else "closed")
        lines.append("%s (%s): no quote fetch; nothing is usable for rules today" % (document["as_of_label"], name))
    stamps = [leg["as_of"] for leg in document["legs"] if leg["as_of"] is not None]
    lines.append(quote_cache.footer_line(market.cfg, max(stamps) if stamps else now))
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
