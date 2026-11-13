#!/usr/bin/env python3
"""rules_diff.py: what a candidate rulebook would change, group by group, against the adopted one.

Usage::

    python3 scripts/rules_diff.py --candidate PATH [--book PATH | --latest-snapshot] [--next-run-utc ISO-8601-UTC]
        [--fixture-set NAME] [--json] [--schema] [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Evaluates the adopted rulebook (``rules/ADOPTED``, verified exactly as ``exit_check.py`` verifies it) and the candidate
at ``--candidate PATH`` against ONE book, each with a scratch (empty) rule state, so the routine's record neither
colours the comparison nor is read or touched: the book built now from quotes (``aegis_desk.book.load_book``, the
offline flags and ``--fixture-set`` honoured, exactly as ``exit_check.py``), a saved book (``--book PATH``) or the
newest snapshot of record in ``data/book/`` (``--latest-snapshot``; none is exit 20 ``snapshot_missing``). A saved
book is evaluated at its own ``generated_at``; an end-of-day snapshot is a closed session, where one observation
suffices for every rule. Nothing is linted here (``rules_adopt.py`` lints) and nothing is written.

Printed: the rule set changes (rules added, removed and changed, compared by their per-rule sha, which ignores
titles, sources and origin marks), every group whose verdict differs (``<group> <underlying> <structure>: <before> ->
<after>``), every rule whose result differs on a group (``rule result: <group> <rule> <before> -> <after>``; a rule
that does not apply is ``none``), every portfolio or underlying result that differs (``section result: ...``), the
groups UNAVAILABLE under either rulebook, the count of verdicts changed and the data footer.

Exit codes: 10 when any group verdict changes (the rule-level and section differences are reported and never set
the exit code: the sections never set a verdict); 20 when no verdict changes but a group is UNAVAILABLE under either
rulebook (neither can judge it on this book), when there is no snapshot for ``--latest-snapshot`` or when the market
data is refused; 0 when nothing changes; 1 on a desk, rulebook or book error (exactly ``exit_check.py``'s codes);
2 on usage. ``--json`` payload: ``{adopted, candidate, book, evaluation_utc, et_date, session, single_observation,
next_run_utc, state: "scratch", rules {added, removed, changed, unchanged}, changes [...], verdicts_changed,
rule_changes [...], section_changes [...], adopted_verdicts, candidate_verdicts, counts {adopted, candidate},
unavailable_groups, warnings}``.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="rules_diff")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

import exit_check  # noqa: E402  (the shared book sources and verdict serialisation of the exit tools)
from aegis_desk import exit_rules, market_clock, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "rules_diff"
DESCRIPTION = ("the adopted and a candidate rulebook evaluated on one book with a scratch rule state: the group "
               "verdicts that would change (exit 10), the rule and section results that differ, the rules added, "
               "removed and changed; nothing is written")
NETWORK_ACCESS = exit_check.NETWORK_ACCESS
STATE_SCRATCH = "scratch"
NONE_WORD = "none"
RESULT_KINDS: Tuple[str, ...] = (exit_rules.RESULT_FIRED, exit_rules.RESULT_PENDING, exit_rules.RESULT_UNAVAILABLE,
                                 exit_rules.RESULT_NO_HISTORY, exit_rules.RESULT_SUPPRESSED, exit_rules.RESULT_CLEAR)
PAYLOAD_KEYS: Tuple[str, ...] = ("adopted", "candidate", "book", "evaluation_utc", "et_date", "session",
                                 "single_observation", "next_run_utc", "state", "rules", "changes", "verdicts_changed",
                                 "rule_changes", "section_changes", "adopted_verdicts", "candidate_verdicts", "counts",
                                 "unavailable_groups", "warnings")
_RESULT_WORDS = "fired | pending | unavailable | no_history | suppressed | clear | null (the rule does not apply)"
OUTPUT: Dict[str, Any] = {
    "adopted": dict(exit_check.OUTPUT["rulebook"]),
    "candidate": dict(exit_check.OUTPUT["rulebook"]),
    "book": dict(exit_check.OUTPUT["book"]),
    "evaluation_utc": "ISO-8601 UTC (the run's instant; a saved book's own generated_at)",
    "et_date": "YYYY-MM-DD", "session": " | ".join(market_clock.SESSIONS),
    "single_observation": "boolean", "next_run_utc": "ISO-8601 UTC | null",
    "state": "scratch (both rulebooks start from an empty rule state; the routine's file is not read)",
    "rules": {"added": ["rule id"], "removed": ["rule id"], "changed": ["rule id (its per-rule sha differs)"],
              "unchanged": "integer"},
    "changes": [{"group_id": "string", "trade_id": "string | null", "underlying": "string | null",
                 "structure": "string | null", "before": "the adopted verdict | null",
                 "after": "the candidate verdict | null"}],
    "verdicts_changed": "integer",
    "rule_changes": [{"group_id": "string", "rule_id": "string", "before": _RESULT_WORDS, "after": _RESULT_WORDS}],
    "section_changes": [{"subject": "portfolio | SYMBOL", "rule_id": "string", "before": _RESULT_WORDS,
                         "after": _RESULT_WORDS}],
    "adopted_verdicts": {"GROUP_ID": "EXIT_SIGNAL | UNAVAILABLE | WATCH | HOLD"},
    "candidate_verdicts": {"GROUP_ID": "EXIT_SIGNAL | UNAVAILABLE | WATCH | HOLD"},
    "counts": {"adopted": dict(exit_check.OUTPUT["counts"]), "candidate": dict(exit_check.OUTPUT["counts"])},
    "unavailable_groups": ["group id (UNAVAILABLE under either rulebook)"],
    "warnings": ["string"],
}


def rule_set_changes(before: Optional[exit_rules.Rulebook], after: exit_rules.Rulebook) -> Dict[str, Any]:
    """Rules added, removed and changed (per-rule sha) from ``before`` (``None``: nothing adopted) to ``after``."""
    old = before.rules_sha if before is not None else {}
    new = after.rules_sha
    return {"added": [rule_id for rule_id in new if rule_id not in old],
            "removed": [rule_id for rule_id in old if rule_id not in new],
            "changed": [rule_id for rule_id in new if rule_id in old and old[rule_id] != new[rule_id]],
            "unchanged": sum(1 for rule_id in new if old.get(rule_id) == new[rule_id])}


def _results(holder: Any) -> Dict[str, str]:
    """``{rule_id: result}`` of a group or section verdict."""
    found: Dict[str, str] = {}
    for kind in RESULT_KINDS:
        for result in getattr(holder, kind):
            found[result.rule_id] = kind
    return found


def _rule_order(before: Optional[exit_rules.Evaluation], after: exit_rules.Evaluation) -> List[str]:
    order: List[str] = []
    for evaluation in (after, before):
        if evaluation is not None and evaluation.rulebook is not None:
            order.extend(rule_id for rule_id in evaluation.rulebook.rule_ids if rule_id not in order)
    return order


def _result_changes(before: Mapping[str, str], after: Mapping[str, str], order: Sequence[str]) -> List[Tuple[str, Any, Any]]:
    ids = [rule_id for rule_id in order if rule_id in before or rule_id in after]
    ids += sorted(rule_id for rule_id in set(before) | set(after) if rule_id not in ids)
    return [(rule_id, before.get(rule_id), after.get(rule_id)) for rule_id in ids
            if before.get(rule_id) != after.get(rule_id)]


def diff_evaluations(before: Optional[exit_rules.Evaluation], after: exit_rules.Evaluation) -> Dict[str, Any]:
    """The differences between two evaluations of one book: ``changes`` (group verdicts; a group absent from one
    side has the verdict ``None`` there; ``before`` ``None`` is no previous rulebook, so every group counts),
    ``verdicts_changed``, ``rule_changes``, ``section_changes`` and ``unavailable_groups`` (UNAVAILABLE on either
    side)."""
    old_groups = {verdict.group_id: verdict for verdict in (before.groups if before is not None else [])}
    new_groups = {verdict.group_id: verdict for verdict in after.groups}
    group_ids = [verdict.group_id for verdict in after.groups]
    group_ids += [group_id for group_id in old_groups if group_id not in new_groups]
    order = _rule_order(before, after)
    changes: List[Dict[str, Any]] = []
    rule_changes: List[Dict[str, Any]] = []
    unavailable: List[str] = []
    for group_id in group_ids:
        old, new = old_groups.get(group_id), new_groups.get(group_id)
        known = new if new is not None else old
        assert known is not None
        if exit_rules.VERDICT_UNAVAILABLE in (getattr(old, "verdict", None), getattr(new, "verdict", None)):
            unavailable.append(group_id)
        old_verdict = old.verdict if old is not None else None
        new_verdict = new.verdict if new is not None else None
        if old_verdict != new_verdict:
            changes.append({"group_id": group_id, "trade_id": known.trade_id, "underlying": known.underlying,
                            "structure": known.structure, "before": old_verdict, "after": new_verdict})
        for rule_id, was, now in _result_changes(_results(old) if old is not None else {},
                                                 _results(new) if new is not None else {}, order):
            rule_changes.append({"group_id": group_id, "rule_id": rule_id, "before": was, "after": now})
    old_sections = {section.subject: section for section in (before.sections if before is not None else [])}
    section_changes: List[Dict[str, Any]] = []
    subjects = [section.subject for section in after.sections]
    subjects += [subject for subject in old_sections if subject not in subjects]
    new_sections = {section.subject: section for section in after.sections}
    for subject in subjects:
        old_section, new_section = old_sections.get(subject), new_sections.get(subject)
        for rule_id, was, now in _result_changes(_results(old_section) if old_section is not None else {},
                                                 _results(new_section) if new_section is not None else {}, order):
            section_changes.append({"subject": subject, "rule_id": rule_id, "before": was, "after": now})
    return {"changes": changes, "verdicts_changed": len(changes), "rule_changes": rule_changes,
            "section_changes": section_changes, "unavailable_groups": unavailable}


def diff_exit_code(diff: Mapping[str, Any]) -> int:
    """10 when a group verdict changes, 20 when none does but a group is UNAVAILABLE on either side, 0 otherwise."""
    if diff["verdicts_changed"]:
        return contract.EXIT_FIRED
    if diff["unavailable_groups"]:
        return contract.EXIT_UNAVAILABLE
    return contract.EXIT_QUIET


def _word(value: Optional[str]) -> str:
    return NONE_WORD if value is None else value


def _names(items: Sequence[str]) -> str:
    return ", ".join(items) if items else NONE_WORD


def diff_lines(diff: Mapping[str, Any], rules: Mapping[str, Any], group_count: int) -> List[str]:
    """The prose of a diff: the rule set, the verdict changes, the rule and section results, the count."""
    lines = ["rules: changed %s; added %s; removed %s; %d unchanged" % (
        _names(rules["changed"]), _names(rules["added"]), _names(rules["removed"]), rules["unchanged"])]
    for change in diff["changes"]:
        parts = [change["group_id"]] + [str(change[key]) for key in ("underlying", "structure") if change[key]]
        lines.append("%s: %s -> %s" % (" ".join(parts), _word(change["before"]), _word(change["after"])))
    if not diff["changes"]:
        lines.append("no group verdict changed (%d %s compared)" % (group_count, "group" if group_count == 1 else "groups"))
    lines.extend("rule result: %s %s %s -> %s" % (item["group_id"], item["rule_id"], _word(item["before"]),
                                                 _word(item["after"])) for item in diff["rule_changes"])
    lines.extend("section result: %s %s %s -> %s" % (item["subject"], item["rule_id"], _word(item["before"]),
                                                    _word(item["after"])) for item in diff["section_changes"])
    if diff["unavailable_groups"]:
        lines.append("unavailable under either rulebook (neither can judge it on this book): %s"
                     % ", ".join(diff["unavailable_groups"]))
    lines.append("verdicts changed: %d" % diff["verdicts_changed"])
    return lines


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--candidate", metavar="PATH", default=None, help="the candidate rulebook file (required)")
    parser.add_argument("--book", metavar="PATH", default=None, help="compare on a saved book instead of building one")
    parser.add_argument("--latest-snapshot", action="store_true",
                        help="compare on the newest snapshot of record in data/book/")
    parser.add_argument("--next-run-utc", metavar="ISO-8601-UTC", default=None,
                        help="the caller's next run: after the session's close a single observation suffices")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/alpaca/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any, now: datetime.datetime) -> Optional[datetime.datetime]:
    if args.candidate is None:
        parser.error("--candidate PATH is required: the rulebook to compare with the adopted one")
    if args.book is not None and args.latest_snapshot:
        parser.error("--book and --latest-snapshot name two different books: give one")
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    if args.fixture_set is not None and (args.book is not None or args.latest_snapshot):
        parser.error("--fixture-set applies to a book built from quotes, not to a saved book")
    return exit_check.parse_next_run(parser, args.next_run_utc, now)


def load_source(args: Any, paths: Any, now: datetime.datetime, agent: str) -> exit_check.BookSource:
    if args.latest_snapshot:
        path = exit_check.latest_snapshot_path(paths)
        if path is None:
            raise exit_check.ToolError("snapshot_missing", "no snapshot of record in data/book/ (book.py --snapshot "
                                                           "writes one)", contract.EXIT_UNAVAILABLE)
        return exit_check.load_saved_book(paths, path)
    return exit_check.load_source(args, paths, now, agent)


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    now = args.now or state.utc_now()
    next_run_utc = validate(parser, args, now)
    try:
        paths = exit_check.open_paths(args.desk_root)
        adopted, _ = exit_check.load_rulebook_for(paths, None)
        candidate, _ = exit_check.load_rulebook_for(paths, args.candidate)
        source = load_source(args, paths, now, exit_check.user_agent(version, TOOL))
        before = exit_check.evaluate_book(adopted, source, {}, next_run_utc)
        after = exit_check.evaluate_book(candidate, source, {}, next_run_utc)
    except exit_check.ToolError as exc:
        return exit_check.fail(args, version, now, exc.code, exc.message, exc.exit_code, tool=TOOL)
    except OSError as exc:
        return exit_check.fail(args, version, now, "io_error", "cannot read the desk files: %s" % exc,
                               contract.EXIT_ERROR, tool=TOOL)
    diff = diff_evaluations(before, after)
    rules = rule_set_changes(adopted, candidate)
    exit_code = diff_exit_code(diff)
    warnings = list(source.warnings)
    for warning in list(before.warnings) + list(after.warnings):
        if warning not in warnings:
            warnings.append(warning)
    if args.json:
        payload = {
            "adopted": exit_check.rulebook_json(adopted, True, paths),
            "candidate": exit_check.rulebook_json(candidate, False, paths),
            "book": exit_check.book_json(source), "evaluation_utc": after.evaluated_at, "et_date": after.et_date,
            "session": source.session.session, "single_observation": after.single_observation,
            "next_run_utc": after.next_run_utc, "state": STATE_SCRATCH, "rules": rules,
            "changes": diff["changes"], "verdicts_changed": diff["verdicts_changed"], "rule_changes": diff["rule_changes"],
            "section_changes": diff["section_changes"],
            "adopted_verdicts": {verdict.group_id: verdict.verdict for verdict in before.groups},
            "candidate_verdicts": {verdict.group_id: verdict.verdict for verdict in after.groups},
            "counts": {"adopted": before.verdict_counts(), "candidate": after.verdict_counts()},
            "unavailable_groups": diff["unavailable_groups"], "warnings": warnings,
        }
        envelope = contract.envelope(TOOL, version, payload, source=source.source_label, delay_min=source.delay_min,
                                     generated_at=now,
                                     status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(envelope))
        return exit_code
    lines = ["rules diff: adopted rules %s, sha %s against candidate rules %s, sha %s (%s), on %s, scratch rule state"
             % (adopted.rulebook_version, adopted.sha256[:8], candidate.rulebook_version, candidate.sha256[:8],
                exit_check.rulebook_json(candidate, False, paths)["path"], exit_check.book_phrase(source))]
    lines.extend(diff_lines(diff, rules, len({verdict.group_id for verdict in before.groups + after.groups})))
    lines.extend("warning: %s" % warning for warning in warnings)
    lines.append(exit_check.book.footer_from_document(source.document))
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
