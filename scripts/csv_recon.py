#!/usr/bin/env python3
"""csv_recon.py: reconcile positions.csv against what expired, what quotes and what was acknowledged.

Usage::

    python3 scripts/csv_recon.py [--diff] [--fixture-set NAME] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

The reconciliation Position Control runs before the open and on the Saturday after an expiration
(plan sections 8 and 11), through ``aegis_desk.recon``. It loads ``desk.toml``, ``positions.csv``
(quarantined groups are listed, not fatal) and ``research/calendar.toml`` when present, snapshots
the file bytes to ``data/positions/<ET date>.csv`` beside ``<ET date>.sha256``, then:

* settles every expired open option leg (last trading day before today's ET date, or today after
  the close): PM-settled physical roots read the official close of the expiry day from the daily
  history (``quote_cache.get_history``, ONE request per underlying and range, the end clamped to the
  last closed session) and apply the $0.01 rule -> ``EXPIRED_OTM``, ``PROBABLE_ASSIGNED`` (short,
  in the money), ``PROBABLE_EXERCISED`` (long, in the money), or ``PIN_UNCERTAIN`` when the close is
  within ``[market_data] pin_band`` of the strike or the next session opened across it ("confirm
  with your broker", no edit); AM-settled index roots (the calendar's ``am_settled_roots``) are
  ``CASH_SETTLED`` at the calendar's settlement value, ``SETTLEMENT_PENDING`` between their last
  trading day's close and the settlement morning's 09:30 ET (the value cannot exist yet; nothing to
  record, not UNAVAILABLE) or ``SETTLEMENT_UNKNOWN`` from then on; no history bar is
  ``EXPIRED_UNRECORDED``;
* lists the proposed edits as exact CSV lines in the file's header order (always in the JSON;
  ``--diff`` prints the block): the option row closed at 0 (or at the cash-settlement intrinsic),
  the stock booked at the strike (the one documented assignment convention);
* checks quotes in ONE batch for the unexpired open option legs and their underlyings
  (``QUOTE_MISSING`` when the vendor did not match a symbol; any other reason is "quote check not
  run"), coverage integrity (``UNCOVERED_SHORT_CALL``), roll linkage (``ROLL_LINK``) and
  ``ACTED_UNCHANGED`` from ``state/acks.jsonl``.

Verdict and exit: ``CLEAN`` 0, ``EDITS_PENDING`` 10 (edits proposed), ``UNAVAILABLE`` 20 (a pin, an
unknown settlement, an unrecorded expiry, an unmatched symbol or a quarantined group), ``ERRORS`` 1
(file-level CSV errors); precedence ``ERRORS > UNAVAILABLE > EDITS_PENDING > CLEAN``. Exit 1 also
for a missing ``positions.csv``, an unusable ``desk.toml`` or calendar and I/O errors; 20 for the
production-host and missing-token refusals; 2 on usage. This tool NEVER writes ``positions.csv``,
and reads it ONCE: the snapshot, the proposed edits' raw cells and the parsed rows all describe the
same bytes. ``--json`` payload: the reconciliation dict (``verdict``, ``exit_code``, ``settlements``,
``edits`` with ``csv_line``, ``findings``, ``quote_check``, ``quarantined``, ``file_errors``,
``counts``) plus ``session``, ``history``, ``snapshot``, ``positions_sha256``, ``warnings`` and
``fixture_set``. The ``ERRORS`` verdict is the error envelope (``error.code`` ``positions_invalid``,
the message naming the file errors), the shape ``book.py``, ``assignment_watch.py`` and
``expiry_checklist.py`` give the same file; the snapshot of record is written before it.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="csv_recon")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import calendar_events, desk_config, desk_paths, market_clock, positions_csv, quote_cache, recon, state  # noqa: E402
from aegis_desk.grouping import group_positions  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "csv_recon"
DESCRIPTION = ("reconcile positions.csv: settle expired legs from official closes and settlement values, list proposed "
               "edits (never applied), check quotes, coverage, roll links and acknowledged-but-unchanged groups, "
               "snapshot the file; CLEAN / EDITS_PENDING / UNAVAILABLE / ERRORS")
NETWORK_ACCESS = ("GET https://sandbox.tradier.com/v1/markets/{quotes,history} only (through the quote cache); "
                  "none with --offline")
LEVEL_LABELS = {recon.LEVEL_UNAVAILABLE: "UNAVAILABLE", recon.LEVEL_WARNING: "warning", recon.LEVEL_INFO: "info"}
OUTPUT: Dict[str, Any] = {
    "verdict": " | ".join(recon.VERDICTS) + " (ERRORS is reported through the error envelope: error.code positions_invalid, "
               "no payload)",
    "exit_code": "0 | 10 | 20 | 1",
    "et_date": "YYYY-MM-DD (America/New_York date of --now)",
    "session": "market_clock.SessionInfo.as_dict()",
    "settlements": [{"row_number": "integer", "group_id": "string", "trade_id": "string | null", "symbol": "OCC compact",
                     "underlying": "string", "quantity": "decimal string (signed open quantity)", "right": "CALL | PUT",
                     "strike": "decimal string", "expiration": "YYYY-MM-DD", "last_trading_day": "YYYY-MM-DD",
                     "settle_date": "YYYY-MM-DD", "settlement": "pm | am", "outcome": " | ".join(recon.OUTCOMES),
                     "close": "decimal string (expiry close or settlement value) | null", "next_open": "decimal string | null",
                     "close_price": "decimal string (the proposed close_price_per_unit) | null", "message": "string",
                     "unavailable": "boolean (SETTLEMENT_PENDING is never unavailable)", "history_reason": "string | null",
                     "edits": ["as below"]}],
    "edits": [{"kind": "update | add", "row_number": "integer | null", "fields": {"column": "text"}, "reason": "string",
               "outcome": "string", "group_id": "string", "cells": ["string"], "csv_line": "exact CSV line in header order"}],
    "findings": [{"kind": " | ".join(recon.FINDING_KINDS), "level": " | ".join(recon.LEVELS), "message": "string",
                  "group_id": "string | null", "row_number": "integer | null", "symbol": "string | null"}],
    "quote_check": {"symbols": ["requested"], "quoted": ["served"], "missing": {"SYMBOL": "reason"},
                    "not_run_reason": "string | null", "requests_made": "integer", "as_of": "ISO-8601 UTC | null"},
    "quarantined": {"GROUP": ["row error strings"]},
    "file_errors": ["string"],
    "counts": {"groups": "integer", "open_groups": "integer", "closed_groups": "integer", "quarantined_groups": "integer",
               "expired_legs": "integer", "edits": "integer", "unavailable": "integer", "warnings": "integer"},
    "history": [{"symbol": "string", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "served_from": "fetch | cache | null",
                 "bars": "integer", "missing_reason": "string | null", "requests_made": "integer"}],
    "snapshot": {"path": "data/positions/<ET date>.csv (desk-root relative)", "sha256": "hex"},
    "positions_sha256": "hex of positions.csv",
    "warnings": ["string"],
    "fixture_set": "string | null (offline runs)",
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--diff", action="store_true",
                        help="print the proposed edits as CSV lines (the JSON payload always lists them)")
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


def plural(count: int, noun: str) -> str:
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def summary_line(result: recon.Reconciliation) -> str:
    if result.verdict == recon.VERDICT_ERRORS:
        return "%s (%s ET): positions.csv cannot be used: %s" % (result.verdict, result.et_date.isoformat(),
                                                                 "; ".join(result.file_errors))
    counts = result.counts
    return "%s (%s ET): %s, %s, %s, %s" % (result.verdict, result.et_date.isoformat(),
                                          plural(counts["expired_legs"], "expired leg"), plural(counts["edits"], "proposed edit"),
                                          plural(counts["unavailable"], "unavailable item"), plural(counts["warnings"], "warning"))


def settlement_line(item: recon.LegSettlement) -> str:
    return "%s row %d %s %s %s: %s" % (item.group_id, item.row_number, item.symbol, recon.plain_number(item.quantity),
                                       item.outcome, item.message)


def quote_line(check: Optional[recon.QuoteCheck]) -> str:
    if check is None or not check.symbols:
        return "quote check: no unexpired option legs to quote"
    if check.not_run_reason is not None:
        return "quote check not run: %s" % check.not_run_reason
    line = "quote check: %s quoted (%s)" % (plural(len(check.quoted), "symbol"), ", ".join(check.quoted))
    if check.missing:
        line += "; missing: " + ", ".join("%s (%s)" % (symbol, reason) for symbol, reason in sorted(check.missing.items()))
    return line


def edit_header(edit: recon.ProposedEdit) -> str:
    where = "row %d" % edit.row_number if edit.row_number is not None else "add"
    fields = ", ".join("%s=%s" % (name, value) for name, value in edit.fields.items())
    return "# %s (%s) %s: %s" % (where, edit.group_id, edit.outcome, fields)


def prose(result: recon.Reconciliation, snapshot: Optional[recon.Snapshot], paths: desk_paths.DeskPaths,
          cfg: desk_config.DeskConfig, diff: bool, footer_as_of: datetime.datetime) -> List[str]:
    lines = [summary_line(result)]
    lines.extend(settlement_line(item) for item in result.settlements)
    for finding in result.findings:
        if finding.kind == recon.FINDING_QUOTE_CHECK_NOT_RUN and result.quote_check is not None \
                and result.quote_check.not_run_reason is not None:
            continue  # the quote line below carries it
        lines.append("%s %s: %s" % (LEVEL_LABELS.get(finding.level, finding.level), finding.kind, finding.message))
    for warning in result.warnings:
        lines.append("warning: %s" % warning)
    if result.verdict != recon.VERDICT_ERRORS:
        lines.append(quote_line(result.quote_check))
    if snapshot is not None:
        lines.append("snapshot: %s sha256 %s" % (paths.relative(snapshot.csv_path), snapshot.sha256))
    if diff:
        if result.edits:
            lines.append("proposed edits (%d; not applied: positions.csv is yours to edit):" % len(result.edits))
            for edit in result.edits:
                lines.append(edit_header(edit))
                lines.append(edit.csv_line())
        else:
            lines.append("proposed edits: none")
    lines.append(quote_cache.footer_line(cfg, footer_as_of))
    return lines


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

    try:
        with open(paths.positions_csv, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return fail(args, version, now, "positions_missing", "positions.csv not found at %s" % paths.positions_csv,
                    contract.EXIT_ERROR)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot read positions.csv: %s" % exc, contract.EXIT_ERROR)
    warnings: List[str] = []
    snapshot: Optional[recon.Snapshot] = None
    try:
        snapshot = recon.snapshot_positions(paths, data, session.et_date)
    except state.LockBusy as exc:
        warnings.append("positions snapshot not written: %s" % exc)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot write the positions snapshot: %s" % exc, contract.EXIT_ERROR)
    # ONE read of positions.csv: the snapshot above, the parsed rows and the raw table all come from ``data``.
    positions = positions_csv.parse_positions_bytes(data, paths.positions_csv, today=session.et_date)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = ""  # the parser reports the encoding error; nothing is settled
    table = recon.csv_table(text)
    groups = group_positions(positions.rows)
    plan = recon.plan_settlement(positions, groups, now=now, session=session, cal=cal)

    histories: Dict[Any, Any] = {}
    history_entries: List[Dict[str, Any]] = []
    quotes: Optional[quote_cache.CacheResult] = None
    try:
        for request in plan.history_requests:
            served = quote_cache.get_history(request.symbol, request.start, request.end, now=now, cfg=market.cache,
                                             client=market.client, paths=paths, run=market.run)
            histories[request.key] = served
            warnings.extend(served.warnings)
            history_entries.append({"symbol": request.symbol, "start": request.start.isoformat(), "end": request.end.isoformat(),
                                    "served_from": served.served_from, "bars": len(served.bars),
                                    "missing_reason": served.missing_reason, "requests_made": served.requests_made})
        symbols = recon.quote_check_symbols(groups, plan.expired_rows)
        if symbols:
            quotes = quote_cache.get_quotes(symbols, now=now, cfg=market.cache, client=market.client, paths=paths,
                                            allow_fetch=session.trading_day, run=market.run)
            warnings.extend(quotes.warnings)
        acks = state.read_jsonl(paths.acks_jsonl)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot use the desk cache: %s" % exc, contract.EXIT_ERROR)

    result = recon.reconcile(plan=plan, positions=positions, groups=groups, table=table,
                             pin_band=market.cfg.market_data.pin_band, histories=histories, quotes=quotes, acks=acks)
    result.warnings.extend(warnings)
    exit_code = result.exit_code
    if result.verdict == recon.VERDICT_ERRORS and args.json:
        # The error envelope, as the other tools give the same file; the prose keeps the verdict and snapshot lines.
        return fail(args, version, now, "positions_invalid",
                    "%s: positions.csv cannot be used: %s" % (result.verdict, "; ".join(result.file_errors)), exit_code)
    footer_as_of = now
    if result.quote_check is not None and result.quote_check.as_of is not None:
        footer_as_of = result.quote_check.as_of

    if args.json:
        payload = result.as_dict()
        payload["session"] = session.as_dict()
        payload["history"] = history_entries
        payload["snapshot"] = None if snapshot is None else {"path": paths.relative(snapshot.csv_path),
                                                              "sha256": snapshot.sha256}
        payload["positions_sha256"] = positions.sha256
        payload["fixture_set"] = market.fixture_set
        body = contract.envelope(TOOL, version, payload, source=quote_cache.source_label(market.cfg),
                                 delay_min=market.cfg.market_data.feed_delay_min, generated_at=now,
                                 status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(body))
        return exit_code

    print("\n".join(prose(result, snapshot, paths, market.cfg, args.diff, footer_as_of)))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
