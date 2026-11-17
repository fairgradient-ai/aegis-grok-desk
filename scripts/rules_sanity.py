#!/usr/bin/env python3
"""rules_sanity.py: the adopted rulebook checked against the positions held today, or its weekly memo (roadmap 3.3).

Usage::

    python3 scripts/rules_sanity.py [--memo] [--json] [--schema] [--desk-root DIR] [--now ISO-8601-UTC] [--offline]
        [--timeout S]

Reads ``rules/ADOPTED`` and the rulebook it names, verified byte for byte and rule by rule exactly as the book check
verifies them (a tampered file is exit 1 ``rules_tampered``), and ``positions.csv``; no market data, nothing written.

Without ``--memo`` (the ``rules-sanity`` routine of the Aegis Rules Steward) it runs the adoption lint
(``aegis_desk.exit_rules.lint``) of the adopted rulebook against the structures held today: a position opened after
the adoption can be a structure no take-profit rule reaches, or one with two loss rules. Each finding prints with its
code; none is ``SANE``. With ``--memo`` (the ``rulebook-memo`` routine) it prints the memo of the adopted rulebook: its
version and sha, when it was adopted, its rules by role, the rules your positions reach today, and the lint's verdict.

Exit codes: 0 sane (or the memo of a sane rulebook); 20 a lint finding against today's positions; 1 a desk file or
rulebook that cannot be used (``rules_tampered`` included); 2 usage. ``--json`` payload: as ``--schema`` describes;
``message`` and ``as_of`` carry the printed text.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="rules_sanity")

import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

from aegis_desk import desk_paths, exit_rules, grouping, market_clock, positions_csv, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "rules_sanity"
DESCRIPTION = ("the adopted rulebook, verified, linted against the structures held today (a new structure no rule "
               "reaches); --memo prints its weekly memo; no market data, nothing written")
NETWORK_ACCESS = "none"
OUTPUT: Dict[str, Any] = {
    "verdict": "sane | findings",
    "exit_code": "0 | 20",
    "message": "string (the text printed, for a routine's post)",
    "as_of": "RFC 3339 (the run instant)",
    "rulebook": {"version": "X.Y.Z", "sha256": "hex", "adopted_utc": "RFC 3339", "rules": "integer"},
    "held_structures": ["structure"],
    "findings": [{"code": "string", "rule_id": "string | null", "structure": "string | null", "message": "string"}],
    "roles": {"ROLE": "integer"},
    "memo": "boolean",
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--memo", action="store_true", help="print the weekly memo of the adopted rulebook")
    return parser


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.status_for(exit_code),
                                                          generated_at=now)))
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
    now = args.now or state.utc_now()
    today = market_clock.to_et(now).date()
    try:
        paths = desk_paths.for_root(args.desk_root)
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc), contract.EXIT_ERROR)
    try:
        adopted, rulebook = exit_rules.load_adopted_rulebook(paths)
    except exit_rules.RulebookError as exc:
        return fail(args, version, now, exc.code, str(exc), contract.EXIT_ERROR)
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=today)
    except FileNotFoundError:
        return fail(args, version, now, "positions_missing", "positions.csv not found at %s" % paths.positions_csv,
                    contract.EXIT_ERROR)
    if positions.file_errors:
        return fail(args, version, now, "positions_invalid", "positions.csv cannot be used: %s"
                    % "; ".join(str(error) for error in positions.file_errors), contract.EXIT_ERROR)
    held = exit_rules.held_structures(grouping.group_positions(positions.rows))
    findings = exit_rules.lint(rulebook, held)
    roles: Dict[str, int] = {}
    for rule in rulebook.rules:
        roles[rule.role] = roles.get(rule.role, 0) + 1
    exit_code = contract.EXIT_UNAVAILABLE if findings else contract.EXIT_QUIET
    head = "rules %s, sha %s" % (rulebook.rulebook_version, rulebook.sha256[:8])
    lines: List[str] = []
    if args.memo:
        adopted_day = market_clock.to_et(adopted.adopted_utc).date()
        lines.append("RULEBOOK MEMO | %s | adopted %s (%d calendar days ago)"
                     % (head, adopted_day.isoformat(), (today - adopted_day).days))
        lines.append("%d rules: %s" % (len(rulebook.rules), ", ".join("%s %d" % (role, roles[role])
                                                                       for role in sorted(roles))))
        lines.append("structures held today: %s" % (", ".join(held) if held else "none"))
    if findings:
        lines.append("RULES SANITY | %s | %d finding%s against the positions held today"
                     % (head, len(findings), "" if len(findings) == 1 else "s"))
        for item in findings:
            target = item.rule_id or item.structure or "-"
            lines.append("%s | %s | %s" % (item.code, target, item.message))
        lines.append("the Aegis Rules Steward offers a candidate rulebook for your review; nothing changes until you "
                     "adopt one with your phrase and your tap")
    else:
        lines.append("RULES SANITY | %s | SANE: every structure held today has its rules" % head)
    message = "\n".join(lines)
    if args.json:
        payload = {"verdict": "findings" if findings else "sane", "exit_code": exit_code, "message": message,
                   "as_of": now, "rulebook": {"version": rulebook.rulebook_version, "sha256": rulebook.sha256,
                                               "adopted_utc": adopted.adopted_utc, "rules": len(rulebook.rules)},
                   "held_structures": held,
                   "findings": [{"code": item.code, "rule_id": item.rule_id, "structure": item.structure,
                                 "message": item.message} for item in findings],
                   "roles": roles, "memo": bool(args.memo)}
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.status_for(exit_code) if exit_code
                                                    else contract.STATUS_OK)))
        return exit_code
    print(message)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
