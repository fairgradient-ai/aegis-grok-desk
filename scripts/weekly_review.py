#!/usr/bin/env python3
"""weekly_review.py: the weekly review of your signals and your rules, from the desk's own records (roadmap 3.4).

Usage::

    python3 scripts/weekly_review.py [--week-of YYYY-MM-DD] [--candidate PATH] [--json] [--schema] [--desk-root DIR]
        [--now ISO-8601-UTC] [--offline] [--timeout S]

The review of one ET week, Monday to Friday (the week of ``--week-of``, default the week of today's ET date; on a
Saturday or Sunday, the week just ended), in two parts:

* **Signals**: every signal of the week as a per-signal fact line (``aegis_desk.journal``: when it was posted, the
  owner's mark, the group's end-of-day P/L at the mid), then the counts in fixed words, one line per rule and one for
  all rules.
* **Replay**: the candidate rulebook (``--candidate``, else the newest ``rules/candidate-X.Y.Z.toml`` of the desk
  root, else none) replayed over the week's end-of-day snapshots exactly as ``rules_replay.py`` replays it, with its
  count line ``verdict differed from your adopted rule on K of N trading days``. Without a candidate the part says so.

Counts only (the owner's answer to open question 6): no sum, average or difference of money across signals or days,
no rate and no ranking of rules. The text passes the alert lint and its review mode (``alert_lint.review_findings``):
a currency amount only on a per-signal fact line, and none of the review's refused phrases. A text that fails is not
printed (exit 1 ``lint_failed``).

Nothing is written. Exit codes: 0 the review; 20 a signal's mark or a replay day is UNAVAILABLE; 1 a desk, rulebook or
lint error; 2 usage. ``--json`` payload: as ``--schema`` describes; ``message`` and ``as_of`` carry the printed text.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="weekly_review")

import datetime  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import exit_check  # noqa: E402  (the rulebook loader of the exit tools)
import rules_replay  # noqa: E402  (the replay of one candidate over the snapshots)
import signal_journal  # noqa: E402  (the per-signal fact line and the snapshot reader)
from aegis_desk import alert_lint, calendar_events, journal, market_clock, signals, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "weekly_review"
DESCRIPTION = ("the weekly review of your signals and your rules from the desk's own records: each signal's facts and "
               "the counts in fixed words, and a candidate rulebook replayed over the week; counts only, nothing written")
NETWORK_ACCESS = "none"
CANDIDATE_RE = re.compile(r"^candidate-(\d+)\.(\d+)\.(\d+)\.toml$")
OUTPUT: Dict[str, Any] = {
    "verdict": "ok | unavailable",
    "exit_code": "0 | 20",
    "message": "string (the text printed, for a routine's post)",
    "as_of": "RFC 3339 (the run instant)",
    "week": {"start": "YYYY-MM-DD (Monday)", "end": "YYYY-MM-DD (Friday)"},
    "signals": ["object (as signal_journal.py)"],
    "counts": {"RULE_ID | all rules": {"signals": "integer", "acknowledged": "integer", "acted": "integer",
                                       "snoozed": "integer"}},
    "replay": {"candidate": "path | null", "days": ["object (as rules_replay.py)"],
               "days_differed": "integer | null"},
    "warnings": ["string"],
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--week-of", metavar="YYYY-MM-DD", default=None,
                        help="a date in the week to review (default: today's ET week; on a weekend, the week just ended)")
    parser.add_argument("--candidate", metavar="PATH", default=None,
                        help="the candidate rulebook to replay (default: the newest rules/candidate-X.Y.Z.toml)")
    return parser


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.status_for(exit_code),
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return exit_code


def newest_candidate(rules_dir: str) -> Optional[str]:
    found: List[Tuple[Tuple[int, int, int], str]] = []
    if os.path.isdir(rules_dir):
        for name in os.listdir(rules_dir):
            match = CANDIDATE_RE.match(name)
            if match:
                found.append((tuple(int(part) for part in match.groups()), name))  # type: ignore[misc]
    return os.path.join(rules_dir, max(found)[1]) if found else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    now = args.now or state.utc_now()
    today = market_clock.to_et(now).date()
    anchor = today
    if args.week_of is not None:
        try:
            anchor = datetime.date.fromisoformat(args.week_of)
        except ValueError:
            parser.error("--week-of must be a date YYYY-MM-DD")
        if anchor > today:
            parser.error("--week-of %s is after today's ET date %s" % (args.week_of, today.isoformat()))
    start = anchor - datetime.timedelta(days=anchor.weekday())
    end = min(start + datetime.timedelta(days=4), today)
    try:
        paths = exit_check.open_paths(args.desk_root)
        adopted, _ = exit_check.load_rulebook_for(paths, None)
        cal = calendar_events.load_calendar_if_present(paths.calendar_toml)
        candidate_path = args.candidate or newest_candidate(paths.rules_dir)
        candidate = exit_check.load_rulebook_for(paths, candidate_path)[0] if candidate_path else None
    except exit_check.ToolError as exc:
        return fail(args, version, now, exc.code, exc.message, exc.exit_code)
    except calendar_events.CalendarError as exc:
        return fail(args, version, now, "calendar_invalid", str(exc), contract.EXIT_ERROR)
    warnings: List[str] = []
    signal_records, signal_status, _bad = signals.read_journal(os.path.join(paths.journal_dir, signals.SIGNALS_FILE))
    ack_records, ack_status, _bad_acks = signals.read_journal(paths.acks_jsonl)
    for name, status in (("journal/signals.jsonl", signal_status), ("state/acks.jsonl", ack_status)):
        if status == signals.STATUS_PARTIAL:
            warnings.append("%s: unreadable lines skipped" % name)
    extra = calendar_events.extra_closures(cal)
    items = journal.facts(signal_records, ack_records, signal_journal.snapshot_reader(paths), start, end, extra)
    table = journal.counts(items)
    emitted: List[str] = []
    lines = ["WEEKLY REVIEW %s to %s | your signals and your rules, from the desk's own records; counts only"
             % (start.isoformat(), end.isoformat()), "", "**Signals**"]
    lines.extend(signal_journal.fact_line(fact, emitted) for fact in items)
    if not items:
        lines.append("no signal was posted this week")
    lines.extend(journal.count_line(name, count, "this week") for name, count in table.items())
    lines.extend(["", "**Replay**"])
    days: List[Dict[str, Any]] = []
    if candidate is None:
        lines.append("no candidate rulebook under rules/ (candidate-X.Y.Z.toml): nothing to replay this week")
    else:
        dates = [day for day in rules_replay.snapshot_dates(paths.book_dir) if start <= day <= end]
        days, _stamps = rules_replay.replay_days(paths, adopted, candidate, dates)
        lines.append("candidate rules %s against your adopted rules %s, over the week's end-of-day snapshots"
                     % (candidate.rulebook_version, adopted.rulebook_version))
        for day in days:
            lines.extend(rules_replay.day_line(day))
        lines.append(rules_replay.count_line(days) if days else "UNAVAILABLE | no end-of-day snapshot this week")
        lines.append("limits: %s" % rules_replay.LIMITS_LINE)
    lines.extend("warning: %s" % warning for warning in warnings)
    message = "\n".join(lines)
    findings = alert_lint.review_findings(message)
    if findings:
        return fail(args, version, now, "lint_failed", "the review failed the alert lint's review mode and was not "
                    "printed: %s" % ", ".join(sorted({finding.code for finding in findings})), contract.EXIT_ERROR)
    gaps = any(mark.value is None and mark.reason != journal.REASON_CLOSED for fact in items for mark in fact.marks
               if mark.et_date < today)
    unavailable = gaps or bool(warnings) or any(day["status"] != "ok" for day in days) or (
        candidate is not None and not days)
    exit_code = contract.EXIT_UNAVAILABLE if unavailable else contract.EXIT_QUIET
    if args.json:
        payload = {"verdict": "unavailable" if unavailable else "ok", "exit_code": exit_code, "message": message,
                   "as_of": now, "week": {"start": start.isoformat(), "end": end.isoformat()},
                   "signals": [fact.as_dict() for fact in items],
                   "counts": {name: count.as_dict() for name, count in table.items()},
                   "replay": {"candidate": None if candidate_path is None else paths.relative(os.path.abspath(candidate_path)),
                              "days": days, "days_differed": None if candidate is None
                              else sum(1 for day in days if day["changes"])},
                   "warnings": warnings}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    print(message)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
