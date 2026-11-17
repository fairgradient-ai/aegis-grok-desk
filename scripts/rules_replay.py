#!/usr/bin/env python3
"""rules_replay.py: a candidate rulebook replayed over the desk's past end-of-day snapshots (roadmap 3.2).

Usage::

    python3 scripts/rules_replay.py --candidate PATH [--days N | --from YYYY-MM-DD [--to YYYY-MM-DD]] [--json]
        [--schema] [--desk-root DIR] [--now ISO-8601-UTC] [--offline] [--timeout S]

For each end-of-day snapshot of record in ``data/book/<ET date>.json`` (the last ``--days`` of them, default 5, or the
ones from ``--from`` to ``--to``), evaluates the adopted rulebook (``rules/ADOPTED``, verified as ``exit_check.py``
verifies it) and the candidate exactly as ``rules_diff.py`` evaluates one saved book: at the snapshot's own instant,
a closed session where one observation suffices, each with a scratch (empty) rule state. It reports, day by day, every
group whose verdict would have differed, and one count line: ``verdict differed from your adopted rule on K of N
trading days``, with the days.

The limits it states on every run: end-of-day snapshots only, so an intraday crossing is invisible; each day is one
closed session with a scratch rule state, so a confirmation spread over days, a re-arm or a cooldown is not carried
from one day to the next; no hypothetical profit or loss, and no money figure on any verdict. A torn or unreadable
snapshot is UNAVAILABLE for its day and named.

Nothing is written: no rule state, no file under ``rules/``. Exit codes: 10 a verdict differs on at least one day; 20
none differs but a day is UNAVAILABLE, a group is UNAVAILABLE under either rulebook, or there is no snapshot in the
range; 0 nothing differs; 1 a desk, rulebook or candidate error; 2 usage. ``--json`` payload: as ``--schema``
describes; ``message`` and ``as_of`` carry the printed text for a routine's post.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="rules_replay")

import datetime  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import exit_check  # noqa: E402  (the saved-book reader, the rulebook loader and the evaluation of the exit tools)
import rules_diff  # noqa: E402  (the comparison of two evaluations of one book)
from aegis_desk import market_clock, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "rules_replay"
DESCRIPTION = ("a candidate rulebook replayed over the desk's past end-of-day snapshots against the adopted one: the "
               "days and groups whose verdict would have differed, and how many days; no profit or loss, nothing "
               "written")
NETWORK_ACCESS = "none"
DEFAULT_DAYS: int = 5
MAX_DAYS: int = 260
SNAPSHOT_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
LIMITS_LINE: str = ("end-of-day snapshots only: an intraday crossing is invisible; each day is one closed session with "
                    "a scratch rule state, so confirmations across days, re-arm and cooldown are not carried; no "
                    "profit or loss is computed")
OUTPUT: Dict[str, Any] = {
    "verdict": "differs | same | unavailable",
    "exit_code": "0 | 10 | 20",
    "message": "string (the text printed)",
    "as_of": "RFC 3339 | null (the newest snapshot's instant)",
    "adopted": {"version": "string", "sha256": "hex"},
    "candidate": {"version": "string", "sha256": "hex", "path": "string"},
    "days": [{"et_date": "YYYY-MM-DD", "status": "ok | unavailable", "reason": "string | null",
              "changes": [{"group_id": "string", "trade_id": "string", "underlying": "string", "structure": "string",
                           "before": "verdict | null", "after": "verdict | null"}],
              "unavailable_groups": ["group id"]}],
    "counts": {"days": "integer", "days_differed": "integer", "days_unavailable": "integer"},
    "limits": "string",
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--candidate", metavar="PATH", required=False, default=None,
                        help="the candidate rulebook file (a TOML rulebook; never one under rules/ that is adopted)")
    parser.add_argument("--days", metavar="N", type=int, default=None,
                        help="the last N end-of-day snapshots (1-%d, default %d)" % (MAX_DAYS, DEFAULT_DAYS))
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", default=None,
                        help="the first snapshot date (with --to, the last; without, through today)")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", default=None, help="the last snapshot date")
    return parser


def _date(parser: contract.ReadOnlyParser, text: Optional[str], flag: str) -> Optional[datetime.date]:
    if text is None:
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        parser.error("%s must be a date YYYY-MM-DD" % flag)
    return None


def snapshot_dates(book_dir: str) -> List[datetime.date]:
    if not os.path.isdir(book_dir):
        return []
    found = []
    for name in os.listdir(book_dir):
        match = SNAPSHOT_NAME_RE.match(name)
        if match:
            try:
                found.append(datetime.date.fromisoformat(match.group(1)))
            except ValueError:
                continue
    return sorted(found)


def select(dates: Sequence[datetime.date], today: datetime.date, days: Optional[int], first: Optional[datetime.date],
           last: Optional[datetime.date]) -> List[datetime.date]:
    """The snapshot dates of the range (module notes), never after today."""
    usable = [day for day in dates if day <= today]
    if first is not None:
        end = last or today
        return [day for day in usable if first <= day <= end]
    return usable[-(days or DEFAULT_DAYS):]


def day_line(day: Dict[str, Any]) -> List[str]:
    stamp = datetime.date.fromisoformat(day["et_date"])
    head = "%s %s" % (stamp.strftime("%a"), day["et_date"])
    if day["status"] != "ok":
        return ["%s | UNAVAILABLE | %s" % (head, day["reason"])]
    if not day["changes"]:
        lines = ["%s | no verdict differs" % head]
    else:
        lines = ["%s | %s %s %s: %s -> %s" % (head, change["group_id"], change["underlying"], change["structure"],
                                               change["before"] or "none", change["after"] or "none")
                 for change in day["changes"]]
    if day["unavailable_groups"]:
        lines.append("%s | UNAVAILABLE under either rulebook: %s" % (head, ", ".join(day["unavailable_groups"])))
    return lines


def replay_days(paths: Any, adopted: Any, candidate: Any,
                dates: Sequence[datetime.date]) -> Tuple[List[Dict[str, Any]], List[datetime.datetime]]:
    """``(days, instants)``: each snapshot date's comparison (module notes) and the instants of the snapshots read."""
    days: List[Dict[str, Any]] = []
    stamps: List[datetime.datetime] = []
    for day in dates:
        path = os.path.join(paths.book_dir, "%s.json" % day.isoformat())
        try:
            source = exit_check.load_saved_book(paths, path)
            before = exit_check.evaluate_book(adopted, source, {}, None)
            after = exit_check.evaluate_book(candidate, source, {}, None)
        except exit_check.ToolError as exc:
            days.append({"et_date": day.isoformat(), "status": "unavailable", "reason": exc.message, "changes": [],
                         "unavailable_groups": []})
            continue
        diff = rules_diff.diff_evaluations(before, after)
        stamps.append(source.evaluated_at)
        days.append({"et_date": day.isoformat(), "status": "ok", "reason": None, "changes": diff["changes"],
                     "unavailable_groups": diff["unavailable_groups"]})
    return days, stamps


def count_line(days: Sequence[Dict[str, Any]]) -> str:
    """``verdict differed from your adopted rule on 3 of 5 trading days (2026-11-02, ...)``."""
    differed = [day["et_date"] for day in days if day["changes"]]
    named = " (%s)" % ", ".join(differed) if differed else ""
    return "verdict differed from your adopted rule on %d of %d trading days%s" % (len(differed), len(days), named)


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    if args.candidate is None:
        parser.error("--candidate PATH is required")
    if args.days is not None and (args.date_from is not None or args.date_to is not None):
        parser.error("--days and --from/--to are two ways to choose the days: give one")
    if args.days is not None and not 1 <= args.days <= MAX_DAYS:
        parser.error("--days must be 1-%d" % MAX_DAYS)
    if args.date_to is not None and args.date_from is None:
        parser.error("--to needs --from")
    first, last = _date(parser, args.date_from, "--from"), _date(parser, args.date_to, "--to")
    if first is not None and last is not None and last < first:
        parser.error("--to is before --from")
    now = args.now or state.utc_now()
    today = market_clock.to_et(now).date()
    try:
        paths = exit_check.open_paths(args.desk_root)
        adopted, _ = exit_check.load_rulebook_for(paths, None)
        candidate, _ = exit_check.load_rulebook_for(paths, args.candidate)
    except exit_check.ToolError as exc:
        return exit_check.fail(args, version, now, exc.code, exc.message, exc.exit_code, tool=TOOL)
    chosen = select(snapshot_dates(paths.book_dir), today, args.days, first, last)
    days, stamps = replay_days(paths, adopted, candidate, chosen)
    differed = [day for day in days if day["changes"]]
    unavailable = [day for day in days if day["status"] != "ok" or day["unavailable_groups"]]
    if differed:
        verdict, exit_code = "differs", contract.EXIT_FIRED
    elif unavailable or not days:
        verdict, exit_code = "unavailable", contract.EXIT_UNAVAILABLE
    else:
        verdict, exit_code = "same", contract.EXIT_QUIET
    lines = ["RULES REPLAY | candidate rules %s against your adopted rules %s | %d end-of-day snapshot%s"
             % (candidate.rulebook_version, adopted.rulebook_version, len(days), "" if len(days) == 1 else "s")]
    if not days:
        lines.append("UNAVAILABLE | no end-of-day snapshot under data/book/ in the range: book.py --snapshot writes one "
                     "at the end of each trading day")
    for day in days:
        lines.extend(day_line(day))
    if days:
        lines.append(count_line(days))
    lines.append("limits: %s" % LIMITS_LINE)
    message = "\n".join(lines)
    if args.json:
        payload = {"verdict": verdict, "exit_code": exit_code, "message": message,
                   "as_of": max(stamps) if stamps else None,
                   "adopted": {"version": adopted.rulebook_version, "sha256": adopted.sha256},
                   "candidate": {"version": candidate.rulebook_version, "sha256": candidate.sha256,
                                 "path": os.path.abspath(args.candidate)},
                   "days": days, "counts": {"days": len(days), "days_differed": len(differed),
                                            "days_unavailable": len(unavailable)},
                   "limits": LIMITS_LINE}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    print(message)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
