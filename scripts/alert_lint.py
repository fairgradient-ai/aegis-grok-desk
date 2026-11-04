#!/usr/bin/env python3
"""alert_lint.py: prove that a message carries only numbers the desk emitted, the fixed footer and no trade language.

Usage::

    python3 scripts/alert_lint.py --message-file PATH --emitted-file PATH [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Reads the message (UTF-8 text) and its emitted-numbers file (JSON: a list of the number strings the formatters of
``aegis_desk.alerts`` printed, or the emitted document ``{"emitted_numbers": [...], "rule_ids": [...],
"identifiers": [...], "clocks": [...], "as_of": "..."}`` as ``Alert.emitted_document()`` writes it and
``book_check.py --json`` prints it in ``payload.emitted``) and runs ``aegis_desk.alert_lint.check`` (strict when the
document names its identifiers, clocks and as-of: an id, sha, version, date or clock the desk did not print is
``identifier_not_emitted``, and a footer clock that is not the as-of fails): every numeric token, attached
forms included (``2x``, ``21-DTE``, ``52.4%``, ``1,640``), must be an emitted string unless it lies in an
identifier (trade, signal and group ids, ``rules X.Y.Z``, ``sha <8 hex>``, ``HH:MM UTC|ET``, ISO dates, rule ids,
the footer's ``15 min``); the last line must be the fixed footer ``deterministic rules you adopted · posted by an AI
assistant · not personalized investment advice · data delayed 15 min, as of HH:MM UTC``; the lint's forbidden
phrases (any case, across line breaks), order-ticket fields and imperative sentence starts are rejected, and so is
any character outside printable ASCII, newline and tab (the middle dot belongs to the footer line only). Relative paths are read from the
current directory. Nothing is written and the network is never used; the shared flags ``--desk-root``,
``--offline`` and ``--now`` are accepted (``--now`` stamps the envelope) so the tool runs in a journey like any
desk tool.

Prose: ``ok: the message passes the alert lint (N numbers checked)``, or ``alert lint: N problems; the message
must not be posted`` followed by one line per problem. A problem names the forbidden-phrase entry (``FL01``...) or
the imperative word's entry (``IS01``...) instead of repeating the text, so this tool's output never carries the
language it reports.

Exit 0 when the message passes, 1 when it does not (``--json``: the envelope with status ``error`` and the payload
below) or when a file cannot be read (``message_unreadable``, ``emitted_unreadable``, ``emitted_invalid``: the
error envelope), 2 on usage. ``--json`` payload: ``{ok, problems, findings: [{code, line, column, message}],
numbers_checked, message_file, emitted_file, rule_ids}``.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="alert_lint")

import datetime  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, Optional, Sequence  # noqa: E402

from aegis_desk import alert_lint, state  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "alert_lint"
DESCRIPTION = ("prove that a message carries only numbers the desk emitted, the fixed footer and no trade language "
               "(exit 0 passes, 1 fails: the message must not be posted)")
NETWORK_ACCESS = "none"
OUTPUT: Dict[str, Any] = {
    "ok": "boolean (true: every rule holds)",
    "problems": ["string (one line per finding; never repeats the forbidden text it reports)"],
    "findings": [{"code": " | ".join(alert_lint.CODES), "line": "integer (1-based; 0 for the whole message)",
                  "column": "integer (1-based; 0 for the whole message)", "message": "string (the problem line)"}],
    "numbers_checked": "integer (numeric tokens outside the identifier whitelist)",
    "message_file": "string (as given)",
    "emitted_file": "string (as given)",
    "rule_ids": "[string] | null (the rule ids the emitted file names; null for a plain list)",
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--message-file", metavar="PATH", default=None, help="the message text (UTF-8)")
    parser.add_argument("--emitted-file", metavar="PATH", default=None,
                        help="the emitted numbers: a JSON list of strings, or the emitted document {emitted_numbers, "
                             "rule_ids, identifiers, clocks, as_of} book_check.py prints (strict)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    if args.message_file is None or args.emitted_file is None:
        parser.error("--message-file and --emitted-file are both required")


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.STATUS_ERROR,
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return contract.EXIT_ERROR


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
        with open(args.message_file, "rb") as handle:
            text = handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return fail(args, version, now, "message_unreadable", "cannot read the message %s: %s" % (args.message_file, exc))
    try:
        with open(args.emitted_file, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return fail(args, version, now, "emitted_unreadable",
                    "cannot read the emitted file %s: %s" % (args.emitted_file, exc))
    try:
        document = alert_lint.load_emitted(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as exc:
        return fail(args, version, now, "emitted_invalid", "the emitted file %s cannot be used: %s"
                    % (args.emitted_file, exc))
    rule_ids = document.rule_ids
    result = document.check(text)
    exit_code = contract.EXIT_QUIET if result.ok else contract.EXIT_ERROR
    if args.json:
        payload = result.as_dict()
        payload.update({"message_file": args.message_file, "emitted_file": args.emitted_file, "rule_ids": rule_ids})
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now,
                                                    status=contract.STATUS_OK if result.ok else contract.STATUS_ERROR)))
        return exit_code
    if result.ok:
        print("ok: the message passes the alert lint (%d numbers checked)" % result.numbers_checked)
        return exit_code
    count = len(result.problems)
    lines = ["alert lint: %d problem%s; the message must not be posted" % (count, "" if count == 1 else "s")]
    lines.extend(result.problems)
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
