#!/usr/bin/env python3
"""rules_adopt.py: the owner-tapped adoption of one exit rulebook version.

Usage::

    python3 scripts/rules_adopt.py --file PATH --version X.Y.Z --owner-phrase "adopt rules X.Y.Z" [--allow-rth]
        [--allow-uncovered] [--json] [--schema] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

The owner adopts a rulebook by tapping the Ask-first card that runs this command with the phrase ``adopt rules
X.Y.Z``; a routine never runs it. The file is read once (its bytes are what is hashed, copied and recorded) and
parsed strictly (``aegis_desk.exit_rules``; ``rules_missing`` and ``config_invalid`` are exit 1). Then every refusal
is collected and reported together, and any one refuses the adoption with nothing written (exit 1):

* ``owner_phrase_mismatch``: the phrase is not exactly ``adopt rules <--version>``;
* ``version_mismatch``: the file's ``rulebook_version`` is not ``--version``;
* ``regular_trading_hours``: ``--now`` is inside a live session (``market_clock.session_at`` with the calendar's full
  closures; decision 8) and ``--allow-rth`` was not given;
* ``lint_failed``: ``exit_rules.lint`` against the structures held in ``positions.csv`` found anything but
  ``uncovered_structure``, or found that and ``--allow-uncovered`` was not given (it accepts uncovered structures and
  nothing else: an example rule, an owner value still to set, a loss rule on a short call leg are never waived);
* ``immutable_version``: ``rules/exit_rules-X.Y.Z.toml`` already exists with other bytes, or an earlier adoption of
  ``X.Y.Z`` recorded another sha256 (every ``rules/CHANGELOG.md`` entry of the version and ``rules/ADOPTED``): an
  adopted version never changes, so a file edited in place after its adoption (the current version or an earlier
  one) cannot be adopted again under its number; the bytes need a new ``rulebook_version`` (the same bytes are not
  rewritten).

Re-adopting exactly the adopted rulebook (same version, same sha) is a no-op (exit 0, status quiet, nothing written);
adopting an earlier version again is a rollback like any adoption. An adoption, under the desk lock
(``state.LockBusy`` is exit 1 ``lock_busy``) with every write atomic, in this order: the immutable copy
``rules/exit_rules-X.Y.Z.toml`` (exactly the file's bytes), ``rules/ADOPTED`` (``exit_rules.adopted_text``: the
version, the sha256 of the bytes, the instant, the owner phrase's sha256 and every per-rule sha), the append-only
``rules/CHANGELOG.md`` entry (``exit_rules.changelog_entry``: every rule with its origin mark and threshold,
``--allow-rth`` and ``--allow-uncovered`` when they were used, plus the previous adoption, the digest and the rule
state counts), the rule state carried forward (``rule_state.carry_forward``: an entry whose per-rule sha is unchanged
is kept, a changed rule's is reset (left out of the state, so its next observation is a first observation, exactly as
the engine's own reset), a removed rule's is dropped; written only when a state file exists), and
``state/desk_state.json["rulebook_change"]`` (``{version, sha256, previous_version, previous_sha256, adopted_utc,
changed, snapshot}``, ``changed`` the count or null: the record the next ``book_check.py`` prints once as
``rulebook changed: N verdicts changed``; every other key of the desk state is kept).

The digest: the previous and the new rulebook evaluated on the newest snapshot of record in ``data/book/`` (at its
own instant, with a scratch rule state, by ``rules_diff.py``'s comparison), ``rulebook changed: N verdicts changed``;
``no snapshot: digest unavailable`` without one (no quote is ever fetched by an adoption). A first adoption counts
every group; an unreadable previous adoption (tampered, missing) is replaced and its digest is unavailable.

Exit codes: 0 adopted (or already adopted), 1 refused or a file error (``desk_root``, the rulebook codes,
``positions_missing``, ``positions_invalid``, ``calendar_invalid``, ``changelog_invalid``, ``lock_busy``,
``io_error``), 2 usage (a missing flag, a version that is not ``X.Y.Z``), 20 when the holiday table has expired (the
session cannot be classified). ``--json`` payload: ``{adopted, already_adopted, version, sha256, file, source_file,
previous, adopted_utc, owner_phrase_sha256, session, during_rth, allow_rth, allow_uncovered, held_structures,
findings, refusals, rule_state, digest, written}`` (a refusal carries ``status`` ``error``).
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="rules_adopt")

import datetime  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

import exit_check  # noqa: E402  (the shared book sources of the exit tools)
import rules_diff  # noqa: E402  (the comparison behind the digest)
from aegis_desk import (  # noqa: E402
    calendar_events,
    desk_paths,
    exit_rules,
    grouping,
    market_clock,
    positions_csv,
    rule_state,
    state,
)
from aegis_desk.version import release_version  # noqa: E402

TOOL = "rules_adopt"
DESCRIPTION = ("adopt one exit rulebook version, owner-tapped: lint against the held structures, refuse during "
               "regular trading hours, copy it immutably, write rules/ADOPTED, append rules/CHANGELOG.md and carry the "
               "rule state forward")
NETWORK_ACCESS = "none"
LOCK_TIMEOUT_S = state.DEFAULT_LOCK_TIMEOUT_S
PHRASE_FORMAT = "adopt rules %s"
RULEBOOK_CHANGE_KEY = "rulebook_change"
REFUSAL_PHRASE = "owner_phrase_mismatch"
REFUSAL_VERSION = "version_mismatch"
REFUSAL_RTH = "regular_trading_hours"
REFUSAL_LINT = "lint_failed"
REFUSAL_IMMUTABLE = "immutable_version"
REFUSAL_CODES: Tuple[str, ...] = (REFUSAL_PHRASE, REFUSAL_VERSION, REFUSAL_RTH, REFUSAL_LINT, REFUSAL_IMMUTABLE)
NO_SNAPSHOT_TEXT = "no snapshot: digest unavailable"
CHANGELOG_ENTRY_RE = re.compile(r"^## ([0-9]+\.[0-9]+\.[0-9]+) adopted [^\n]*\n\n- file exit_rules-([0-9]+\.[0-9]+\.[0-9]+)"
                                r"\.toml, sha256 ([0-9a-f]{64})$", re.MULTILINE)
PAYLOAD_KEYS: Tuple[str, ...] = ("adopted", "already_adopted", "version", "sha256", "file", "source_file", "previous",
                                 "adopted_utc", "owner_phrase_sha256", "session", "during_rth", "allow_rth",
                                 "allow_uncovered", "held_structures", "findings", "refusals", "rule_state", "digest",
                                 "written")
OUTPUT: Dict[str, Any] = {
    "adopted": "boolean (true only when the adoption was written)",
    "already_adopted": "boolean (the file is exactly the adopted rulebook: nothing written)",
    "version": "X.Y.Z", "sha256": "sha256 of the file's bytes", "file": "rules/exit_rules-X.Y.Z.toml",
    "source_file": "absolute path of --file",
    "previous": {"version": "X.Y.Z | null", "sha256": "sha256 | null",
                 "error": "null | not_adopted ... (why the previous adoption could not be read)"},
    "adopted_utc": "ISO-8601 UTC | null", "owner_phrase_sha256": "sha256 of the phrase",
    "session": " | ".join(market_clock.SESSIONS), "during_rth": "boolean (a live session at --now)",
    "allow_rth": "boolean (--allow-rth given)", "allow_uncovered": ["structure accepted uncovered"],
    "held_structures": ["structure held in positions.csv"],
    "findings": [{"code": " | ".join(exit_rules.LINT_CODES), "rule_id": "string | null", "structure": "string | null",
                  "message": "string", "accepted": "boolean (waived by --allow-uncovered)"}],
    "refusals": [{"code": " | ".join(REFUSAL_CODES), "message": "string"}],
    "rule_state": {"status": " | ".join(rule_state.LOAD_STATUSES), "kept": "n", "reset": "n", "dropped": "n",
                   "written": "boolean"},
    "digest": {"snapshot": "data/book/YYYY-MM-DD.json | null", "previous_version": "X.Y.Z | null",
               "verdicts_changed": "integer | null (unavailable)",
               "changes": [{"group_id": "string", "trade_id": "string | null", "underlying": "string | null",
                            "structure": "string | null", "before": "verdict | null", "after": "verdict | null"}],
               "text": "rulebook changed: N verdicts changed | no snapshot: digest unavailable | ..."},
    "written": ["root-relative path"],
}


class Refused(Exception):
    """The refusals found under the desk lock (a version file that appeared with other bytes)."""

    def __init__(self, refusals: List[Dict[str, str]]) -> None:
        self.refusals = refusals
        super().__init__("; ".join(item["message"] for item in refusals))


def owner_phrase(version: str) -> str:
    return PHRASE_FORMAT % version


def read_rulebook(path: str) -> Tuple[bytes, exit_rules.Rulebook]:
    """The file's bytes and the rulebook parsed from exactly those bytes (its sha256 is theirs)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        raise exit_check.ToolError("rules_missing", "%s: rulebook not found" % path, contract.EXIT_ERROR) from None
    except OSError as exc:
        raise exit_check.ToolError("io_error", "cannot read %s: %s" % (path, exc), contract.EXIT_ERROR) from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise exit_check.ToolError("config_invalid", "%s: not valid UTF-8: %s" % (path, exc), contract.EXIT_ERROR) from None
    try:
        return data, exit_rules.parse_rulebook_text(text, path, hashlib.sha256(data).hexdigest())
    except exit_rules.RulebookError as exc:
        raise exit_check.ToolError(exc.code, str(exc), contract.EXIT_ERROR) from None


def held_structures(paths: desk_paths.DeskPaths, today: datetime.date) -> List[str]:
    """The structures of the groups with an open leg in ``positions.csv`` (``book.py``'s error codes)."""
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=today)
    except FileNotFoundError:
        raise exit_check.ToolError("positions_missing", "positions.csv not found at %s" % paths.positions_csv,
                                   contract.EXIT_ERROR) from None
    except OSError as exc:
        raise exit_check.ToolError("io_error", "cannot read positions.csv: %s" % exc, contract.EXIT_ERROR) from None
    if positions.file_errors:
        raise exit_check.ToolError("positions_invalid", "positions.csv cannot be used: %s"
                                   % "; ".join(str(error) for error in positions.file_errors), contract.EXIT_ERROR)
    return exit_rules.held_structures(grouping.group_positions(positions.rows))


def previous_adoption(paths: desk_paths.DeskPaths) -> Tuple[Optional[exit_rules.Adopted], Optional[exit_rules.Rulebook],
                                                              Optional[str]]:
    """``(ADOPTED, its rulebook, None)``, ``(None, None, None)`` when nothing is adopted, or ``(None, None, code)``
    when the adoption on record cannot be read (it is replaced by this one)."""
    try:
        adopted, rulebook = exit_rules.load_adopted_rulebook(paths)
    except exit_rules.RulebookError as exc:
        if exc.code == "not_adopted":
            return None, None, None
        return None, None, exc.code
    return adopted, rulebook, None


def compute_digest(paths: desk_paths.DeskPaths, previous: Optional[exit_rules.Rulebook], previous_error: Optional[str],
                   candidate: exit_rules.Rulebook) -> Dict[str, Any]:
    """The digest against the newest snapshot of record (module notes)."""
    digest: Dict[str, Any] = {"snapshot": None, "previous_version": previous.rulebook_version if previous else None,
                              "verdicts_changed": None, "changes": [], "text": NO_SNAPSHOT_TEXT}
    path = exit_check.latest_snapshot_path(paths)
    if path is None:
        return digest
    digest["snapshot"] = paths.relative(path)
    if previous_error is not None:
        digest["text"] = "previous rulebook unreadable (%s): digest unavailable" % previous_error
        return digest
    try:
        source = exit_check.load_saved_book(paths, path)
    except exit_check.ToolError as exc:
        digest["text"] = "snapshot unreadable (%s): digest unavailable" % exc.code
        return digest
    before = exit_check.evaluate_book(previous, source, {}, None) if previous is not None else None
    after = exit_check.evaluate_book(candidate, source, {}, None)
    diff = rules_diff.diff_evaluations(before, after)
    count = diff["verdicts_changed"]
    digest["verdicts_changed"] = count
    digest["changes"] = diff["changes"]
    digest["text"] = "rulebook changed: %d %s changed%s" % (count, "verdict" if count == 1 else "verdicts",
                                                          "" if previous is not None else " (no previous adoption)")
    return digest


def finding_json(finding: exit_rules.LintFinding, accepted: bool) -> Dict[str, Any]:
    return {"code": finding.code, "rule_id": finding.rule_id, "structure": finding.structure,
            "message": finding.message, "accepted": accepted}


def refusal(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


def read_optional_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def recorded_shas(paths: desk_paths.DeskPaths) -> Dict[str, List[str]]:
    """``{version: [sha256 recorded by an adoption]}`` from every ``rules/CHANGELOG.md`` entry and ``rules/ADOPTED``
    (unreadable records add nothing: the version file's own bytes are still compared)."""
    found: Dict[str, List[str]] = {}

    def add(version: str, sha: str) -> None:
        if sha not in found.setdefault(version, []):
            found[version].append(sha)

    data = read_optional_bytes(os.path.join(paths.rules_dir, "CHANGELOG.md"))
    if data is not None:
        for match in CHANGELOG_ENTRY_RE.finditer(data.decode("utf-8", errors="replace")):
            if match.group(1) == match.group(2):
                add(match.group(1), match.group(3))
    try:
        adopted = exit_rules.load_adopted(paths.adopted_file)
    except exit_rules.RulebookError:
        adopted = None
    if adopted is not None:
        add(adopted.version, adopted.sha256)
    return found


def history_refusal(paths: desk_paths.DeskPaths, candidate: exit_rules.Rulebook) -> Optional[Dict[str, str]]:
    """``immutable_version`` when an adoption of the candidate's version recorded another sha256, else ``None``."""
    others = [sha for sha in recorded_shas(paths).get(candidate.rulebook_version, []) if sha != candidate.sha256]
    if not others:
        return None
    return refusal(REFUSAL_IMMUTABLE, "rules %s was adopted with sha256 %s (rules/CHANGELOG.md) and these bytes are sha "
                                      "%s: an adopted version never changes; give the bytes a new rulebook_version"
                   % (candidate.rulebook_version, ", ".join(sha[:8] for sha in others), candidate.sha256[:8]))


def immutable_refusal(paths: desk_paths.DeskPaths, version_file: str, existing: bytes) -> Dict[str, str]:
    return refusal(REFUSAL_IMMUTABLE, "%s already holds other bytes (sha %s): an adopted version never changes; give "
                                      "the candidate a new rulebook_version" % (paths.relative(version_file),
                                                                               hashlib.sha256(existing).hexdigest()[:8]))


def changelog_lines(previous: Optional[exit_rules.Adopted], previous_error: Optional[str], digest: Mapping[str, Any],
                    state_status: str, carried: Optional[rule_state.CarryForward]) -> List[str]:
    """The lines this tool adds to ``exit_rules.changelog_entry``: the previous adoption, the digest, the rule state."""
    if previous is not None:
        lines = ["- previous adopted rulebook %s, sha256 %s" % (previous.version, previous.sha256)]
    elif previous_error is not None:
        lines = ["- previous adoption unreadable (%s)" % previous_error]
    else:
        lines = ["- no previous adoption"]
    if digest["verdicts_changed"] is not None:
        lines.append("- %s (against the snapshot %s)" % (digest["text"], digest["snapshot"]))
    else:
        lines.append("- %s" % digest["text"])
    lines.append("- %s" % rule_state_text(state_status, carried))
    return lines


def rule_state_text(state_status: str, carried: Optional[rule_state.CarryForward]) -> str:
    if carried is None:
        if state_status == rule_state.LOAD_MISSING:
            return "rule state: no state file to carry forward"
        return "rule state: %s, nothing to carry forward" % state_status
    return "rule state carried forward: %d kept, %d reset, %d dropped" % (len(carried.kept), len(carried.reset),
                                                                          len(carried.dropped))


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--file", metavar="PATH", default=None, help="the rulebook file to adopt (required)")
    parser.add_argument("--version", metavar="X.Y.Z", default=None, help="the version it holds (required)")
    parser.add_argument("--owner-phrase", metavar="TEXT", default=None,
                        help='exactly "adopt rules X.Y.Z": the owner\'s tapped confirmation (required)')
    parser.add_argument("--allow-rth", action="store_true",
                        help="adopt during regular trading hours (recorded in rules/CHANGELOG.md)")
    parser.add_argument("--allow-uncovered", action="store_true",
                        help="accept held structures no take-profit rule covers (recorded in rules/CHANGELOG.md)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    for flag, value in (("--file", args.file), ("--version", args.version), ("--owner-phrase", args.owner_phrase)):
        if value is None:
            parser.error("%s is required" % flag)
    if not exit_rules.VERSION_RE.match(args.version):
        parser.error("--version must be X.Y.Z (digits, no leading v), got %r" % args.version)


def collect_refusals(args: Any, paths: desk_paths.DeskPaths, data: bytes, candidate: exit_rules.Rulebook,
                     session: market_clock.SessionInfo,
                     findings: Sequence[exit_rules.LintFinding]) -> List[Dict[str, str]]:
    refusals: List[Dict[str, str]] = []
    if args.owner_phrase.strip() != owner_phrase(args.version):
        refusals.append(refusal(REFUSAL_PHRASE, 'the owner phrase is not "%s"' % owner_phrase(args.version)))
    if candidate.rulebook_version != args.version:
        refusals.append(refusal(REFUSAL_VERSION, "%s holds rulebook_version %s, not --version %s"
                                % (os.path.abspath(args.file), candidate.rulebook_version, args.version)))
    if session.is_live and not args.allow_rth:
        refusals.append(refusal(REFUSAL_RTH, "regular trading hours (session %s): an adoption waits for the close "
                                             "unless --allow-rth is given (recorded in rules/CHANGELOG.md)" % session.session))
    blocking = [item for item in findings if not (item.code == exit_rules.LINT_UNCOVERED and args.allow_uncovered)]
    if blocking:
        refusals.append(refusal(REFUSAL_LINT, "%d lint %s the adoption (%s)" % (
            len(blocking), "finding blocks" if len(blocking) == 1 else "findings block",
            ", ".join(sorted({item.code for item in blocking})))))
    if exit_rules.VERSION_RE.match(candidate.rulebook_version):
        version_file = exit_rules.rulebook_path(paths, candidate.rulebook_version)
        existing = read_optional_bytes(version_file)
        if existing is not None and existing != data:
            refusals.append(immutable_refusal(paths, version_file, existing))
        else:
            recorded = history_refusal(paths, candidate)
            if recorded is not None:
                refusals.append(recorded)
    return refusals


def adopt(args: Any, paths: desk_paths.DeskPaths, data: bytes, candidate: exit_rules.Rulebook, *,
          now: datetime.datetime, during_rth: bool, uncovered: Sequence[str], previous: Optional[exit_rules.Adopted],
          previous_error: Optional[str], digest: Mapping[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """Write the adoption under the desk lock (module notes); returns the written paths and the rule-state summary.
    Raises :class:`Refused` when the version file appeared with other bytes before the lock was taken."""
    phrase_sha = hashlib.sha256(owner_phrase(args.version).encode("utf-8")).hexdigest()
    version_file = exit_rules.rulebook_path(paths, candidate.rulebook_version)
    changelog_path = os.path.join(paths.rules_dir, "CHANGELOG.md")
    desk_paths.ensure_runtime_dirs(paths)
    os.makedirs(paths.rules_dir, exist_ok=True)
    written: List[str] = []
    with state.locked(paths.lock_path, timeout_s=LOCK_TIMEOUT_S):
        existing = read_optional_bytes(version_file)
        if existing is not None and existing != data:
            raise Refused([immutable_refusal(paths, version_file, existing)])
        recorded = history_refusal(paths, candidate)
        if recorded is not None:
            raise Refused([recorded])
        old_changelog = read_optional_bytes(changelog_path)
        try:
            changelog_text = "" if old_changelog is None else old_changelog.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise exit_check.ToolError("changelog_invalid", "rules/CHANGELOG.md is not valid UTF-8 (%s): the append-only "
                                                            "record cannot be extended" % exc, contract.EXIT_ERROR) from None
        entries, state_status = rule_state.load_with_status(paths)
        carried = (rule_state.carry_forward(entries, candidate.rules_sha)
                   if state_status in (rule_state.LOAD_OK, rule_state.LOAD_PARTIAL) else None)
        desk_state, _ = state.read_json(paths.desk_state_json)
        desk_document: Dict[str, Any] = dict(desk_state) if isinstance(desk_state, dict) else {}
        entry = exit_rules.changelog_entry(candidate, adopted_utc=now, owner_phrase_sha256=phrase_sha,
                                           allow_rth=during_rth and args.allow_rth, allow_uncovered=uncovered)
        entry += "\n".join(changelog_lines(previous, previous_error, digest, state_status, carried)) + "\n"
        if existing is None:
            state.atomic_write_bytes(version_file, data)
            written.append(version_file)
        state.atomic_write_text(paths.adopted_file, exit_rules.adopted_text(
            version=candidate.rulebook_version, sha256=candidate.sha256, adopted_utc=now, owner_phrase_sha256=phrase_sha,
            rules_sha=candidate.rules_sha))
        written.append(paths.adopted_file)
        state.atomic_write_text(changelog_path, exit_rules.changelog_append(changelog_text, entry))
        written.append(changelog_path)
        if carried is not None:
            state.atomic_write_json(paths.rule_state_json, rule_state.document(carried.state))
            written.append(paths.rule_state_json)
        desk_document[RULEBOOK_CHANGE_KEY] = {
            "version": candidate.rulebook_version, "sha256": candidate.sha256,
            "previous_version": previous.version if previous is not None else None,
            "previous_sha256": previous.sha256 if previous is not None else None, "adopted_utc": now,
            "changed": digest["verdicts_changed"], "snapshot": digest["snapshot"]}
        state.atomic_write_json(paths.desk_state_json, desk_document)
        written.append(paths.desk_state_json)
    summary = {"status": state_status, "kept": len(carried.kept) if carried else 0,
               "reset": len(carried.reset) if carried else 0, "dropped": len(carried.dropped) if carried else 0,
               "written": carried is not None, "text": rule_state_text(state_status, carried)}
    return [paths.relative(path) for path in written], summary


def report_lines(payload: Mapping[str, Any], rulebook_sha: str, now: datetime.datetime, state_text: str) -> List[str]:
    if payload["already_adopted"]:
        return ["rules %s, sha %s, is already the adopted rulebook: nothing was written" % (payload["version"],
                                                                                          rulebook_sha[:8])]
    if not payload["adopted"]:
        lines = ["refused: rules %s was not adopted; nothing was written" % payload["version"]]
        lines.extend("refused: %s: %s" % (item["code"], item["message"]) for item in payload["refusals"])
        lines.extend("lint: %s %s: %s" % (item["code"], item["rule_id"] or item["structure"], item["message"])
                     for item in payload["findings"] if not item["accepted"])
        return lines
    previous = payload["previous"]
    if previous is None:
        previous_text = "previous: none"
    elif previous["error"] is not None:
        previous_text = "previous: unreadable (%s)" % previous["error"]
    else:
        previous_text = "previous: rules %s, sha %s" % (previous["version"], previous["sha256"][:8])
    held = payload["held_structures"]
    lines = ["adopted rules %s, sha %s, as %s at %s (session %s)" % (payload["version"], rulebook_sha[:8], payload["file"],
                                                                     exit_check.utc_minute(now), payload["session"]),
             previous_text,
             "lint: clean for the held %s" % ", ".join(held) if held else "lint: clean (no structure held)"]
    if payload["allow_uncovered"]:
        lines.append("lint: uncovered structures accepted with --allow-uncovered: %s" % ", ".join(payload["allow_uncovered"]))
    if payload["during_rth"]:
        lines.append("adopted during regular trading hours (--allow-rth)")
    lines.append(state_text)
    lines.append(payload["digest"]["text"])
    lines.append("written: %s" % ", ".join(payload["written"]))
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
    source_file = os.path.abspath(args.file)
    try:
        paths = exit_check.open_paths(args.desk_root)
        data, candidate = read_rulebook(source_file)
        try:
            calendar = calendar_events.load_calendar_if_present(paths.calendar_toml)
        except calendar_events.CalendarError as exc:
            raise exit_check.ToolError("calendar_invalid", str(exc), contract.EXIT_ERROR) from None
        try:
            session = market_clock.session_at(now, extra_holidays=calendar_events.extra_closures(calendar))
        except market_clock.TableExpiredError as exc:
            raise exit_check.ToolError("holiday_table_expired", "holiday table expired: %s" % exc,
                                       contract.EXIT_UNAVAILABLE) from None
        held = held_structures(paths, session.et_date)
        previous, previous_rulebook, previous_error = previous_adoption(paths)
    except exit_check.ToolError as exc:
        return exit_check.fail(args, version, now, exc.code, exc.message, exc.exit_code, tool=TOOL)
    except OSError as exc:
        return exit_check.fail(args, version, now, "io_error", "cannot read the desk files: %s" % exc,
                               contract.EXIT_ERROR, tool=TOOL)
    findings = exit_rules.lint(candidate, held)
    uncovered = sorted({str(item.structure) for item in findings if item.code == exit_rules.LINT_UNCOVERED})
    accepted = args.allow_uncovered
    phrase_sha = hashlib.sha256(owner_phrase(args.version).encode("utf-8")).hexdigest()
    payload: Dict[str, Any] = {
        "adopted": False, "already_adopted": False, "version": args.version, "sha256": candidate.sha256,
        "file": "rules/exit_rules-%s.toml" % args.version, "source_file": source_file,
        "previous": (None if previous is None and previous_error is None else
                     {"version": previous.version if previous else None, "sha256": previous.sha256 if previous else None,
                      "error": previous_error}),
        "adopted_utc": None, "owner_phrase_sha256": phrase_sha, "session": session.session,
        "during_rth": session.is_live, "allow_rth": args.allow_rth, "allow_uncovered": uncovered if accepted else [],
        "held_structures": held,
        "findings": [finding_json(item, accepted and item.code == exit_rules.LINT_UNCOVERED) for item in findings],
        "refusals": [], "rule_state": None, "digest": None, "written": [],
    }
    state_text = ""
    refusals = collect_refusals(args, paths, data, candidate, session, findings)
    phrase_ok = all(item["code"] not in (REFUSAL_PHRASE, REFUSAL_VERSION) for item in refusals)
    if (phrase_ok and previous is not None and previous.version == candidate.rulebook_version
            and previous.sha256 == candidate.sha256):
        payload.update(already_adopted=True, refusals=[], findings=[])
        exit_code, status = contract.EXIT_QUIET, contract.STATUS_QUIET
    elif refusals:
        payload["refusals"] = refusals
        exit_code, status = contract.EXIT_ERROR, contract.STATUS_ERROR
    else:
        try:
            digest = compute_digest(paths, previous_rulebook, previous_error, candidate)
            written, summary = adopt(args, paths, data, candidate, now=now, during_rth=session.is_live,
                                     uncovered=payload["allow_uncovered"], previous=previous,
                                     previous_error=previous_error, digest=digest)
        except Refused as exc:
            payload["refusals"] = exc.refusals
            written, summary, digest = [], None, None
        except exit_check.ToolError as exc:
            return exit_check.fail(args, version, now, exc.code, exc.message, exc.exit_code, tool=TOOL)
        except state.LockBusy as exc:
            return exit_check.fail(args, version, now, "lock_busy", str(exc), contract.EXIT_ERROR, tool=TOOL)
        except OSError as exc:
            return exit_check.fail(args, version, now, "io_error", "cannot read or write under the desk root: %s" % exc,
                                   contract.EXIT_ERROR, tool=TOOL)
        if summary is None:
            exit_code, status = contract.EXIT_ERROR, contract.STATUS_ERROR
        else:
            state_text = summary.pop("text")
            payload.update(adopted=True, adopted_utc=now, rule_state=summary, digest=digest, written=written)
            exit_code, status = contract.EXIT_QUIET, contract.STATUS_OK
    if args.json:
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now, status=status)))
        return exit_code
    print("\n".join(report_lines(payload, candidate.sha256, now, state_text)))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
