#!/usr/bin/env python3
"""vol_context.py: the desk's own implied volatility per symbol against its recorded history (roadmap step 2.9).

Usage::

    python3 scripts/vol_context.py [--symbol SYM] [--json] [--schema] [--desk-root DIR] [--offline]
        [--now ISO-8601-UTC]

Reads the desk's own end-of-day book snapshots (``data/book/<ET date>.json``) through ``aegis_desk.vol_context``:
for each symbol (``--symbol``, or every symbol the latest snapshot holds an option leg on) the median implied
volatility of its open option legs on the latest recorded day, how many earlier days were lower and their range. The
vendor's Greeks are never read. Information only: no rule, alert, ticket or ranking reads it, and it changes no
verdict. No network, no market data, nothing written.

Exit codes: 0 every context available; 20 any UNAVAILABLE (no snapshot yet, no value on the latest day, fewer than
``vol_context.MIN_HISTORY_DAYS`` earlier days); 1 an unusable desk root; 2 usage. ``--json`` payload: as ``--schema``
describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="vol_context")

import datetime  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import desk_paths, state, vol_context  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "vol_context"
DESCRIPTION = ("information only: the desk's own implied volatility per symbol from its end-of-day book snapshots, "
               "against the earlier recorded days (how many were lower, the range); changes no verdict; no network, "
               "nothing written")
NETWORK_ACCESS = "none"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
INFORMATION_LINE = "information only: no rule, alert, ticket or ranking reads it, and it changes no verdict"
SOURCE_LINE = "source: the desk's own end-of-day book snapshots (implied volatility solved from the quotes' mids)"
OUTPUT: Dict[str, Any] = {
    "verdict": "ok | unavailable",
    "exit_code": "0 | 20",
    "snapshots": "integer (readable book snapshots)",
    "latest": "YYYY-MM-DD | null (the latest snapshot's ET date)",
    "min_history_days": "integer",
    "contexts": [{"symbol": "string", "status": "ok | unavailable",
                  "reason": "null | no_snapshots | no_value | short_history", "as_of": "YYYY-MM-DD | null",
                  "iv": "float | null (median of the day's legs, a fraction: 0.18 is 18 %)",
                  "history_days": "integer (earlier days holding a value)", "lower_days": "integer | null",
                  "rank_pct": "float | null (percent of the earlier days lower)", "low": "float | null",
                  "high": "float | null", "first_day": "YYYY-MM-DD | null"}],
    "skipped": ["data/book/<file> (a snapshot that could not be read)"],
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--symbol", metavar="SYM", default=None,
                        help="one symbol (default: every symbol the latest snapshot holds an option leg on)")
    return parser


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.status_for(exit_code),
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return exit_code


def pct(value: float) -> str:
    return "%.1f %%" % (100.0 * value)


def line(item: vol_context.VolContext) -> str:
    """``VOL CONTEXT | SPY | implied volatility 18.4 % on 2026-11-03 | 12 of 20 earlier days lower (60 %) | range
    15.1 % to 22.0 % since 2026-10-06``, or ``UNAVAILABLE | SPY volatility context | <reason>``."""
    if item.status != vol_context.STATUS_OK:
        words = vol_context.REASON_WORDS[item.reason or vol_context.REASON_NO_SNAPSHOTS]
        return "UNAVAILABLE | %s volatility context | %s" % (item.symbol, words)
    assert item.iv is not None and item.lower_days is not None and item.rank_pct is not None
    assert item.low is not None and item.high is not None and item.as_of is not None and item.first_day is not None
    return "VOL CONTEXT | %s | implied volatility %s on %s | %d of %d earlier days lower (%.0f %%) | range %s to %s since %s" % (
        item.symbol, pct(item.iv), item.as_of.isoformat(), item.lower_days, item.history_days, item.rank_pct,
        pct(item.low), pct(item.high), item.first_day.isoformat())


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    symbol: Optional[str] = None
    if args.symbol is not None:
        symbol = args.symbol.strip().upper()
        if not SYMBOL_RE.match(symbol):
            parser.error("--symbol must be a share symbol such as SPY, got %r" % args.symbol)
    now = args.now or state.utc_now()
    try:
        paths = desk_paths.for_root(args.desk_root)
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc), contract.EXIT_ERROR)
    history = vol_context.load_history(paths)
    items = vol_context.contexts(history, None if symbol is None else [symbol])
    if not items:  # snapshots exist but hold no option leg with a solved volatility
        items = [vol_context.context(symbol or "the book", history)]
    unavailable = any(item.status != vol_context.STATUS_OK for item in items)
    exit_code = contract.EXIT_UNAVAILABLE if unavailable else contract.EXIT_QUIET
    skipped: List[str] = [paths.relative(paths.book_dir) + "/" + name for name in history.skipped]
    if args.json:
        payload: Dict[str, Any] = {
            "verdict": "unavailable" if unavailable else "ok", "exit_code": exit_code, "snapshots": len(history.days),
            "latest": None if history.latest is None else history.latest.isoformat(),
            "min_history_days": vol_context.MIN_HISTORY_DAYS, "contexts": [item.as_dict() for item in items],
            "skipped": skipped,
        }
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    lines = [line(item) for item in items]
    lines.extend("warning: %s could not be read and is left out" % name for name in skipped)
    lines.extend([INFORMATION_LINE, SOURCE_LINE])
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
