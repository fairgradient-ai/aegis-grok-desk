#!/usr/bin/env python3
"""trigger_levels.py: the threshold-level sheet: every adopted rule of every open group as a price, band or date.

Usage::

    python3 scripts/trigger_levels.py [--plan GROUP_ID] [--from-snapshot] [--fixture-set NAME] [--json] [--schema]
        [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

The book is built now from quotes exactly as ``exit_check.py`` builds it (``exit_check.load_built_book``: one quotes
request, the calendar's full closures, the offline flags and ``--fixture-set``), or, with ``--from-snapshot``, read
back from the snapshot of record ``data/book/<ET date of --now>.json`` and evaluated at its own ``generated_at``
(``exit_check.load_saved_book``; the end-of-day routine's source). The rulebook is the adopted one (``rules/ADOPTED``
and the file it names, verified byte for byte and rule by rule), read before any quote is fetched; the rule state is
read (``state/rule_state.json``) so a fired or pending rule shows as crossed, and NEVER written: ``book_check.py
--post`` is its one writer. The evaluation is ``exit_check.evaluate_book`` with no next run, and every line's status is
that evaluation's result, never re-decided.

The sheet (``aegis_desk.levels``, PR 2d decisions D1-2 to D1-10): ``THRESHOLD LEVELS <D> | rules X.Y.Z, sha <8 hex> |
book as of HH:MM UTC``, one section per open group (its tick grid from ``desk.toml [levels] penny_classes`` and
``penny_all_classes``, then one line per rule in rulebook order: the option or spread price, the underlying price,
band or model estimate, or the date at which the rule's own reading turns, with the rounding shown and the status:
crossed, resting-eligible, or an alert level that is not an order), the underlyings and portfolio section and the data
footer. It is linted in strict mode (``aegis_desk.alert_lint``) before anything prints; a failure is exit 1
``lint_failed`` and nothing is written. Then, under the desk lock (``state.locked(state/.lock)``), the run writes
``levels/<D>.md`` (the text exactly) and ``levels/<D>.json`` (the payload), ``D`` the ET date of the book's
``generated_at``, and with ``--plan GROUP_ID`` (a group or trade id) the exit-plan card ``plans/<group id>.md``: the
card line, the group's section, the dates that matter (last trading day, 21 trading days before it, earnings,
ex-dividend, the broker's exercise cutoff) and the footer, linted the same way. A busy lock writes nothing: the
sheet is still printed, with the warning ``levels not written: ...``, and the exit code is 20.
``desk.toml [levels] order_shape_enabled = true`` changes no text in this release (a warning says so).

Printed: the message (the sheet) and, in prose, one ``warning: ...`` line per warning; with ``--plan``, a blank line
and the card follow. ``--json`` prints the envelope; ``payload.message`` is the sheet's text and ``payload.emitted``
its strict emitted document (``alert_lint.py --emitted-file``), which a routine composing the sheet re-lints.

Exit codes: 0 when written and every group is usable; 20 when a group is unavailable or quarantined (its section
says why), when the snapshot is missing (``UNAVAILABLE: book snapshot data/book/<D>.json missing``, nothing
written), when the market data is refused, or when the lock was busy; 1 on a desk, configuration, rulebook, book or
positions error, an unknown ``--plan`` id (``unknown_group``) or a failing lint (``lint_failed``), nothing written;
2 on usage (``--from-snapshot`` with ``--fixture-set``, ``--fixture-set`` without ``--offline``). Never 10: the
sheet is information, not a signal. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="trigger_levels")

import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import exit_check  # noqa: E402  (the shared book sources, rulebook and error mapping of the exit tools)
from aegis_desk import (  # noqa: E402
    alert_lint,
    alerts,
    book,
    desk_config,
    desk_paths,
    exit_rules,
    levels,
    market_clock,
    rule_state,
    state,
)
from aegis_desk.version import release_version  # noqa: E402

TOOL = "trigger_levels"
DESCRIPTION = ("the threshold-level sheet: every adopted rule of every open group as the option, spread or underlying "
               "price, band or date at which its own reading turns, linted; writes levels/<D>.md and levels/<D>.json "
               "(and plans/<group>.md with --plan); reads the rule state, never writes it")
NETWORK_ACCESS = exit_check.NETWORK_ACCESS
LOCK_TIMEOUT_S = state.DEFAULT_LOCK_TIMEOUT_S
SNAPSHOT_MISSING = "book snapshot %s missing"
LOCK_WARNING = "levels not written: %s"
STATE_WARNING = "state/rule_state.json was %s: its rules are shown as observed afresh"
PAYLOAD_KEYS: Tuple[str, ...] = ("et_date", "book", "rulebook", "groups", "sections", "text", "message", "emitted",
                                 "lint", "plan", "warnings", "written")

_LINE_SHAPE: Dict[str, Any] = {
    "rule_id": "rule id | null (the shared no-history line, a quarantine reason)",
    "kind": " | ".join(levels.LINE_KINDS),
    "status": " | ".join(levels.LINE_STATUSES),
    "level": "decimal string | {low, high} (a band) | null",
    "exact": "decimal string (the unrounded value; a model root to 4 decimals) | {low, high} | null",
    "rounding": "down | up | null (rounding left the value as it was)",
    "close_side": "ask | bid | net | null (the side a liq-basis rule reads; null for a mid-basis rule or no price)",
    "current": "decimal string | null (the fresh side, the net, or the spot the level is compared with)",
    "dates": {key: "YYYY-MM-DD | null" for key in levels.DATES_KEYS},
    "model": {"iv": "float (model is null unless the line is a delta model estimate)", "tau_years": "float",
              "r": "float (the book's risk-free rate)", "q": "float (the calendar's dividend yield, 0 when absent)",
              "delta_boundary": "decimal string (the |delta| where the rule's reading turns)"},
    "text": "string (the line exactly as printed)",
}
_EMITTED_SHAPE: Dict[str, Any] = {"emitted_numbers": ["string"], "rule_ids": ["string"], "identifiers": ["string"],
                                  "clocks": ["HH:MM UTC | HH:MM ET"], "as_of": "ISO-8601 UTC (the footer's instant)"}
_LINT_SHAPE: Dict[str, Any] = {"ok": "boolean", "problems": ["string"], "numbers_checked": "integer"}
OUTPUT: Dict[str, Any] = {
    "et_date": "YYYY-MM-DD (the ET date of the book's generated_at: the file names)",
    "book": {"origin": "quotes | file", "path": "data/book/<D>.json | null", "generated_at": "ISO-8601 UTC",
             "as_of": "ISO-8601 UTC (the newest quote stamp: the footer's instant)"},
    "rulebook": {"version": "X.Y.Z", "sha256": "hex of the adopted file's bytes"},
    "groups": [{"group_id": "string", "trade_id": "string | null", "underlying": "string | null",
                "structure": "string | null", "data_quality": "ok | unavailable | null",
                "tick_class": " | ".join(levels.TICK_CLASSES) + " | null (no open option leg)",
                "lines": [dict(_LINE_SHAPE)], "suppressed": ["rule id (suppressed by a row tag)"],
                "price_levels": "integer (lines with a level)", "resting_eligible": "integer"}],
    "sections": [{"scope": "underlying | portfolio | no_history", "subject": "SYMBOL | portfolio | null",
                  "rule_ids": ["rule id"], "lines": [dict(_LINE_SHAPE)]}],
    "text": "string (the sheet, the content of levels/<D>.md)",
    "message": "string (the sheet exactly as printed: what a routine posts)",
    "emitted": dict(_EMITTED_SHAPE),
    "lint": dict(_LINT_SHAPE),
    "plan": {"group_id": "string (plan is null without --plan)", "path": "plans/<group id>.md",
             "text": "string (the card, the content of the file)", "emitted": dict(_EMITTED_SHAPE),
             "lint": dict(_LINT_SHAPE)},
    "warnings": ["string"],
    "written": ["desk-root relative paths written (none when the lock was busy)"],
}


def user_agent(version: str) -> str:
    return exit_check.user_agent(version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--plan", metavar="GROUP_ID", default=None,
                        help="also write the exit-plan card plans/<group id>.md of this group (a group or trade id)")
    parser.add_argument("--from-snapshot", action="store_true",
                        help="read the snapshot of record data/book/<ET date of --now>.json instead of fetching quotes")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/tradier/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    if args.from_snapshot and args.fixture_set is not None:
        parser.error("--fixture-set applies to a book built from quotes, not to --from-snapshot")


def load_config(paths: desk_paths.DeskPaths) -> desk_config.DeskConfig:
    try:
        return desk_config.load_desk_config(paths.desk_toml)
    except desk_config.DeskConfigError as exc:
        raise exit_check.ToolError("config_invalid", str(exc), contract.EXIT_ERROR) from None


def load_source(args: Any, paths: desk_paths.DeskPaths, now: datetime.datetime, version: str) -> exit_check.BookSource:
    """The book built now from quotes, or the snapshot of record of ``now``'s ET date (missing: exit 20)."""
    if not args.from_snapshot:
        return exit_check.load_built_book(args.desk_root, now=now, offline=args.offline, fixture_set=args.fixture_set,
                                          timeout_s=args.timeout, agent=user_agent(version))
    path = book.snapshot_path(paths, market_clock.to_et(now).date())
    if not os.path.isfile(path):
        raise exit_check.ToolError("snapshot_missing", SNAPSHOT_MISSING % paths.relative(path), contract.EXIT_UNAVAILABLE)
    return exit_check.load_saved_book(paths, path)


def lint_dict(result: alert_lint.LintResult) -> Dict[str, Any]:
    return {"ok": result.ok, "problems": list(result.problems), "numbers_checked": result.numbers_checked}


def coded(result: alert_lint.LintResult) -> List[str]:
    return sorted({finding.code for finding in result.findings}) or ["a finding"]


def write_outputs(paths: desk_paths.DeskPaths, et_date: datetime.date, sheet: levels.Sheet, payload: Dict[str, Any],
                  card: Optional[levels.PlanCard]) -> Tuple[List[str], List[str]]:
    """``(written, warnings)``: the files under one hold of the desk lock, or none and the warning when it is busy."""
    md = os.path.join(paths.levels_dir, "%s.md" % et_date.isoformat())
    js = os.path.join(paths.levels_dir, "%s.json" % et_date.isoformat())
    targets = [md, js] + ([os.path.join(paths.plans_dir, "%s.md" % card.group_id)] if card is not None else [])
    written = [paths.relative(path) for path in targets]
    try:
        with state.locked(paths.lock_path, timeout_s=LOCK_TIMEOUT_S):
            desk_paths.ensure_runtime_dirs(paths)
            payload["written"] = list(written)
            state.atomic_write_text(md, sheet.text)
            state.atomic_write_json(js, payload)
            if card is not None:
                state.atomic_write_text(targets[2], card.text)
    except state.LockBusy as exc:
        payload["written"] = []
        return [], [LOCK_WARNING % exc]
    return written, []


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    return exit_check.fail(args, version, now, code, message, exit_code, tool=TOOL)


def run(args: Any, version: str, now: datetime.datetime) -> int:
    paths = exit_check.open_paths(args.desk_root)
    rulebook, _adopted = exit_check.load_rulebook_for(paths, None)
    cfg = load_config(paths)
    source = load_source(args, paths, now, version)
    entries, state_status = rule_state.load_with_status(paths)
    evaluation = exit_check.evaluate_book(rulebook, source, entries, None)
    warnings = list(source.warnings) + list(evaluation.warnings)
    if state_status not in (rule_state.LOAD_OK, rule_state.LOAD_MISSING):
        warnings.append(STATE_WARNING % state_status)
    if cfg.levels.order_shape_enabled:
        warnings.append(levels.ORDER_SHAPE_WARNING)
    document = source.document
    sheet = levels.build_sheet(evaluation, document, rulebook, calendar=source.calendar, levels_config=cfg.levels,
                               delay_min=source.delay_min)
    lint = sheet.lint()
    if not lint.ok:
        raise exit_check.ToolError("lint_failed", "the level sheet failed alert_lint and was not printed or written: %s"
                                   % ", ".join(coded(lint)), contract.EXIT_ERROR)
    card: Optional[levels.PlanCard] = None
    plan: Optional[Dict[str, Any]] = None
    if args.plan is not None:
        try:
            card = levels.build_plan_card(evaluation, document, rulebook, args.plan, calendar=source.calendar,
                                          levels_config=cfg.levels, broker_cutoff_et=cfg.owner.broker_exercise_cutoff_et,
                                          delay_min=source.delay_min)
        except levels.UnknownGroup:
            raise exit_check.ToolError("unknown_group", "--plan %s names no open group of the book (a group or trade id)"
                                       % args.plan, contract.EXIT_ERROR) from None
        card_lint = card.lint()
        if not card_lint.ok:
            raise exit_check.ToolError("lint_failed", "the plan card failed alert_lint and was not printed or written: "
                                       "%s" % ", ".join(coded(card_lint)), contract.EXIT_ERROR)
        plan = {"group_id": card.group_id, "path": paths.relative(os.path.join(paths.plans_dir, "%s.md" % card.group_id)),
                "text": card.text, "emitted": card.emitted_document(), "lint": lint_dict(card_lint)}
    payload: Dict[str, Any] = {
        "et_date": sheet.et_date,
        "book": {"origin": source.origin, "path": paths.relative(source.path) if source.path else None,
                 "generated_at": document["generated_at"], "as_of": alerts.book_as_of(document)},
        "rulebook": {"version": rulebook.rulebook_version, "sha256": rulebook.sha256},
        "groups": [group.as_dict() for group in sheet.groups],
        "sections": [section.as_dict() for section in sheet.sections],
        "text": sheet.text, "message": sheet.text, "emitted": sheet.emitted_document(), "lint": lint_dict(lint),
        "plan": plan, "warnings": warnings, "written": [],
    }
    _written, lock_warnings = write_outputs(paths, sheet.et_date, sheet, payload, card)
    payload["warnings"] = warnings + lock_warnings
    exit_code = contract.EXIT_UNAVAILABLE if lock_warnings else book.exit_code_for(document)
    if args.json:
        envelope = contract.envelope(TOOL, version, payload, source=source.source_label, delay_min=source.delay_min,
                                     generated_at=now,
                                     status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(envelope))
        return exit_code
    lines = [sheet.text] + ["warning: %s" % warning for warning in payload["warnings"]]
    if card is not None:
        lines += ["", card.text]
    print("\n".join(lines))
    return exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    now = args.now or state.utc_now()
    validate(parser, args)
    try:
        return run(args, version, now)
    except exit_check.ToolError as exc:
        return fail(args, version, now, exc.code, exc.message, exc.exit_code)
    except exit_rules.RulebookError as exc:
        return fail(args, version, now, exc.code, str(exc), contract.EXIT_ERROR)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot read or write under the desk root: %s" % exc,
                    contract.EXIT_ERROR)


if __name__ == "__main__":
    sys.exit(main())
