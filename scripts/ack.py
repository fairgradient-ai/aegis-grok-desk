#!/usr/bin/env python3
"""ack.py: the owner's acknowledgements of signals and of configuration changes (``state/acks.jsonl``).

Usage::

    python3 scripts/ack.py list [--json]
    python3 scripts/ack.py show SIGNAL_ID [--json]
    python3 scripts/ack.py ack SIGNAL_ID [--json]
    python3 scripts/ack.py snooze SIGNAL_ID --until ISO-8601-UTC|"HH:MM ET" --owner-tapped [--json]
    python3 scripts/ack.py acted SIGNAL_ID --owner-tapped [--json]
    python3 scripts/ack.py config --owner-tapped [--json]
    python3 scripts/ack.py connectors --owner-tapped [--json]
        (every form also takes --desk-root DIR, --offline, --now ISO-8601-UTC, --schema and --timeout)

The signals are the records ``book_check.py --post`` appends to ``journal/signals.jsonl`` (``SIG-YYYYMMDD-NNN``);
their posts are the ``journal/alerts.jsonl`` records (``aegis_desk.signals``). Actions:

* ``list`` prints the open signals (the latest signal of each trade that no ``ack`` or ``acted`` record closes)
  with their ladder position (an unacknowledged EXIT_SIGNAL is re-posted on the next run of its routine, then three
  and seven runs after its first post, ``signals.LADDER_RUN_OFFSETS``: four posts at most, and never inside its
  primary rule's ``cooldown_min`` after the last post, so the line names the earliest instant, ``not before
  YYYY-MM-DD HH:MM UTC``; WATCH signals have no ladder; a standing UNAVAILABLE is re-posted on every run until it is
  acknowledged) and their ack state (``open`` or ``snoozed``). ``show`` prints one signal, its posts and its
  acknowledgement records, and its state: ``open``, ``snoozed``, ``acknowledged``, ``acted`` or ``superseded`` (a
  later signal of the same trade replaced it). Both only read and write nothing: a journal line that does not parse
  is skipped and the output says so (``signals.read_journal``; the file is never moved aside).
* ``ack`` closes a signal (the owner has seen it; ``book_check`` re-posts it no more: neither the EXIT_SIGNAL ladder
  nor a standing UNAVAILABLE banner).
* ``snooze`` keeps it open but quiet until ``--until``: an ISO-8601 UTC instant or ``HH:MM ET`` on today's
  America/New_York date, stored as the ISO-8601 UTC instant; it must lie after ``--now``. Only an open (or snoozed)
  signal can be snoozed (``signal_not_open``, exit 1).
* ``acted`` records that the owner acted in the brokerage account, with ``rows_sha`` =
  ``aegis_desk.recon.group_rows_sha(aegis_desk.recon.csv_table(<positions.csv text>), <trade id>)``: the SHA-256 of
  the group's physical CSV lines. ``csv_recon.py`` reports ``ACTED_UNCHANGED`` while those rows stay unchanged a
  trading day later (record the close or the new position in ``positions.csv``). A signal with no rows (the
  portfolio) records ``rows_sha`` null. It closes the signal like ``ack``.
* ``config`` acknowledges the current configuration: the SHA-256 of the bytes of ``desk.toml``, ``positions.csv``,
  the rulebook file ``rules/ADOPTED`` names (``rules/exit_rules-X.Y.Z.toml``) and ``structuring.toml`` (keys
  ``desk_toml``, ``positions_csv``, ``rulebook``, ``structuring_toml``, :data:`HASH_NAMES`; null when nothing is
  adopted, or for a missing file: a desk without structuring criteria acknowledges ``structuring_toml`` null) is
  written to ``state/desk_state.json["acknowledged_hashes"]`` (atomically, under the desk lock, every other key kept;
  a corrupt file is replaced and said so). ``book_check.py`` prints CONFIG CHANGED first on every run until these
  hashes match the files it evaluates, and the structuring tools show nothing while desk.toml, positions.csv or
  structuring.toml differ from them. The printed line names ``structuring.toml sha <8 hex>`` last, and only when the
  file exists. An unreadable ``rules/ADOPTED`` is ``adopted_invalid`` (exit 1, nothing written).
* ``connectors`` records that the owner read back the account's Connected apps and MCP servers and confirmed that none
  can place or change an order: the run instant is written to ``state/desk_state.json["connector_readback_utc"]``
  (atomically, under the desk lock, every other key kept; a corrupt file is replaced and said so), the key that
  ``desk_doctor`` check 10 reads (it asks again once the record is more than 30 days old). It prints
  ``connector readback recorded at HH:MM UTC; desk_doctor re-asks after 30 days``.

``snooze``, ``acted``, ``config`` and ``connectors`` require ``--owner-tapped``: the flag is the signature of the
owner's tap on the Ask-first card that runs them; routines never run them. ``ack`` does not need it. Every write
appends exactly one record to ``state/acks.jsonl``: ``{utc, signal_id|null, trade_id|null, action
(ack|snooze|acted|config|connectors), until|null, rows_sha|null, hashes|null}`` (``config`` carries the acknowledged
``hashes``). The tool never reads quotes and never touches ``positions.csv``, ``desk.toml``, the rulebook or the
journals.

Exit 0 on success, 1 on an error (``desk_root``, ``unknown_signal``, ``signal_not_open``, ``adopted_invalid``,
``io_error``, ``lock_busy``), 2 on usage. ``--json`` payload: ``{action, ...}`` as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="ack")

import datetime  # noqa: E402
import hashlib  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

from aegis_desk import (  # noqa: E402
    alerts,
    desk_paths,
    exit_rules,
    market_clock,
    recon,
    signals,
    state,
)
from aegis_desk.version import release_version  # noqa: E402

TOOL = "ack"
DESCRIPTION = ("the owner's acknowledgements: list and show the open signals with their re-post ladder, ack, snooze "
               "or record acted on a signal, acknowledge the configuration hashes book_check compares, and record the "
               "owner's readback of the connected apps and MCP servers")
NETWORK_ACCESS = "none"
ACTIONS: Tuple[str, ...] = ("list", "show", "ack", "snooze", "acted", "config", "connectors")
SIGNAL_ACTIONS: Tuple[str, ...] = ("show", "ack", "snooze", "acted")
OWNER_TAPPED_ACTIONS: Tuple[str, ...] = ("snooze", "acted", "config", "connectors")
WRITE_ACTIONS: Tuple[str, ...] = ("ack", "snooze", "acted", "config", "connectors")
CONNECTOR_KEY: str = "connector_readback_utc"
"""The ``state/desk_state.json`` key ``connectors`` writes and ``desk_doctor`` check 10 reads (``doctor.CONNECTOR_KEY``)."""
CONNECTOR_REASK_DAYS: int = 30
"""The age after which ``desk_doctor`` asks for the readback again (``doctor.CONNECTOR_MAX_AGE_DAYS``)."""
ACK_RECORD_KEYS: Tuple[str, ...] = ("utc", "signal_id", "trade_id", "action", "until", "rows_sha", "hashes")
HASH_NAMES: Tuple[str, ...] = ("desk_toml", "positions_csv", "rulebook", "structuring_toml")
"""The acknowledged files, in the order CONFIG CHANGED lists them: ``desk.toml``, ``positions.csv``, the adopted
rulebook file and ``structuring.toml`` (the names ``book_check.HASH_NAMES`` and ``exit_check.CONFIG_NAMES`` compare;
a test keeps the three equal)."""
LADDER_POSTS: int = 1 + len(signals.LADDER_RUN_OFFSETS)
STATE_OPEN = signals.ACK_STATE_OPEN
STATE_SNOOZED = signals.ACK_STATE_SNOOZED
STATE_ACKNOWLEDGED = "acknowledged"
STATE_ACTED = "acted"
STATE_SUPERSEDED = "superseded"
SIGNAL_STATES: Tuple[str, ...] = (STATE_OPEN, STATE_SNOOZED, STATE_ACKNOWLEDGED, STATE_ACTED, STATE_SUPERSEDED)
OWNER_TAP_MESSAGE = ("%s requires --owner-tapped: the signature of the owner's tap on the Ask-first card that runs "
                     "it (routines never run it)")
ET_CLOCK_RE = re.compile(r"^\s*([01][0-9]|2[0-3]):([0-5][0-9])\s+ET\s*$")
SIGNAL_SHAPE: Dict[str, Any] = {
    "signal_id": "SIG-YYYYMMDD-NNN", "trade_id": "string (AG-..., ROW-n or portfolio)", "structure": "string",
    "verdict": "EXIT_SIGNAL | WATCH | UNAVAILABLE", "plan": "close | roll_review | hold_review | null",
    "posted_at": "ISO-8601 UTC (first post)", "posts": "integer (linted alerts.jsonl records of the signal)",
    "last_posted_at": "ISO-8601 UTC | null", "ack_state": "open | snoozed", "snoozed_until": "ISO-8601 UTC | null",
    "ladder": {"applies": "boolean (EXIT_SIGNAL only)", "posts": "integer", "of": "4 | null",
               "next_repost_after_runs": "integer runs after the first post | null", "done": "boolean",
               "not_before": "ISO-8601 UTC | null (the last post plus the primary rule's cooldown_min: book_check counts "
                             "the ladder's runs only outside it)",
               "rule_id": "the primary EXIT rule | null", "cooldown_min": "integer | null"},
}
OUTPUT: Dict[str, Any] = {
    "action": " | ".join(ACTIONS),
    "count": "(list) integer: the open signals",
    "signals": ["(list) " + "the open signal: see signal"],
    "signal": dict(SIGNAL_SHAPE, reasons=["the signal record's reasons"], data_quality="string",
                   quote_age_at_post="integer | null", run_lateness_min="integer | null"),
    "state": "(show, ack, snooze, acted) " + " | ".join(SIGNAL_STATES),
    "superseded_by": "(show) SIG-YYYYMMDD-NNN | null",
    "posts": "(show) integer",
    "alerts": ["(show) {signal_id, text, posted_at, lint_ok}: the signal's alerts.jsonl records"],
    "acks": ["(show) the signal's acks.jsonl records"],
    "record": {"utc": "ISO-8601 UTC", "signal_id": "SIG-YYYYMMDD-NNN | null", "trade_id": "string | null",
               "action": "ack | snooze | acted | config | connectors", "until": "ISO-8601 UTC | null (snooze)",
               "rows_sha": "sha256 of the group's positions.csv lines | null (acted)",
               "hashes": "{desk_toml, positions_csv, rulebook, structuring_toml: sha256 | null} | null (config)"},
    "state_before": "(ack, snooze, acted) the signal's state before the record",
    "state_after": "(ack, snooze, acted) the signal's state after it",
    "acknowledged_hashes": "(config) {desk_toml, positions_csv, rulebook, structuring_toml: sha256 | null (a missing "
                           "file)}",
    "rulebook_version": "(config) X.Y.Z | null",
    "connector_readback_utc": "(connectors) ISO-8601 UTC: the run instant, written to state/desk_state.json",
    "path": "(writes) state/acks.jsonl",
    "warnings": ["string"],
}


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("action", nargs="?", default=None, choices=ACTIONS,
                        help="list | show | ack | snooze | acted | config | connectors (required except with --schema)")
    parser.add_argument("signal_id", nargs="?", default=None, metavar="SIGNAL_ID",
                        help="SIG-YYYYMMDD-NNN (show, ack, snooze, acted)")
    parser.add_argument("--until", default=None, metavar="ISO-8601-UTC|HH:MM ET",
                        help="snooze until this instant: ISO-8601 UTC, or HH:MM ET on today's America/New_York date")
    parser.add_argument("--owner-tapped", action="store_true",
                        help="the owner tapped the Ask-first card (required by snooze, acted, config and connectors)")
    return parser


def parse_until(text: str, now: datetime.datetime) -> datetime.datetime:
    """``--until``: an ISO-8601 UTC instant, or ``HH:MM ET`` on the America/New_York date of ``now``."""
    match = ET_CLOCK_RE.match(text)
    if match is not None:
        et_date = market_clock.to_et(now).date()
        naive = datetime.datetime.combine(et_date, datetime.time(int(match.group(1)), int(match.group(2))))
        return market_clock.from_et(naive)
    try:
        return contract.parse_utc_instant(text, "--until")
    except ValueError:
        raise ValueError("--until must be an ISO-8601 UTC instant such as 2026-11-20T20:30:00Z or a clock time such "
                         "as \"15:30 ET\", got %r" % (text,)) from None


def validate(parser: contract.ReadOnlyParser, args: Any, now: datetime.datetime) -> None:
    action = args.action
    if action is None:
        parser.error("an action is required: %s" % " | ".join(ACTIONS))
    if action in SIGNAL_ACTIONS:
        if args.signal_id is None:
            parser.error("%s needs a signal id (SIG-YYYYMMDD-NNN)" % action)
        if signals.parse_signal_id(args.signal_id) is None:
            parser.error("%r is not a signal id (SIG-YYYYMMDD-NNN)" % (args.signal_id,))
    elif args.signal_id is not None:
        parser.error("%s takes no signal id" % action)
    if action == "snooze":
        if args.until is None:
            parser.error("snooze needs --until ISO-8601-UTC or --until \"HH:MM ET\"")
    elif args.until is not None:
        parser.error("--until applies only to snooze")
    if action in OWNER_TAPPED_ACTIONS and not args.owner_tapped:
        parser.error(OWNER_TAP_MESSAGE % action)
    args.until_utc = None
    if action == "snooze":
        try:
            until = parse_until(args.until, now)
        except ValueError as exc:
            parser.error(str(exc))
        if until <= now:
            parser.error("--until %s has already passed at %s" % (until.strftime("%Y-%m-%d %H:%M UTC"),
                                                                    now.strftime("%Y-%m-%d %H:%M UTC")))
        args.until_utc = until


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.STATUS_ERROR,
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return contract.EXIT_ERROR


class ToolError(Exception):
    """An error the tool reports with exit 1: ``code`` and ``message``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------------------------
# Reading the journals
# --------------------------------------------------------------------------------------------


class Journals:
    """The signals, their posts and the acknowledgements, read once (a corrupt journal is quarantined by the
    state layer and reported in ``warnings``)."""

    def __init__(self, paths: desk_paths.DeskPaths) -> None:
        self.warnings: List[str] = []
        self.signals, status = signals.read_signals(paths)
        self._note(status, "journal/signals.jsonl")
        self.alerts, status = signals.read_alerts(paths)
        self._note(status, "journal/alerts.jsonl")
        acks, status, _bad = signals.read_journal(paths.acks_jsonl)
        self._note(status, "state/acks.jsonl")
        self.acks: List[Dict[str, Any]] = [record for record in acks if isinstance(record, dict)]
        self.open = signals.open_signals(paths, self.acks)
        self.cooldowns = rule_cooldowns(paths)

    def _note(self, status: str, name: str) -> None:
        if status == signals.STATUS_PARTIAL:
            self.warnings.append("%s has lines that do not parse: they were skipped (the file is kept as it is)" % name)

    def find(self, signal_id: str) -> Dict[str, Any]:
        for record in self.signals:
            if record.get("signal_id") == signal_id:
                return record
        raise ToolError("unknown_signal", "%s is not in journal/signals.jsonl" % signal_id)

    def open_signal(self, signal_id: str) -> Optional[signals.OpenSignal]:
        return next((item for item in self.open if item.signal_id == signal_id), None)

    def acks_of(self, signal_id: str) -> List[Dict[str, Any]]:
        return [record for record in self.acks if record.get("signal_id") == signal_id]

    def alerts_of(self, signal_id: str) -> List[Dict[str, Any]]:
        return [record for record in self.alerts if record.get("signal_id") == signal_id]

    def latest_of_trade(self, trade_id: Any) -> Optional[Dict[str, Any]]:
        latest = None
        for record in self.signals:
            if record.get("trade_id") == trade_id and signals.parse_signal_id(record.get("signal_id")) is not None:
                latest = record
        return latest

    def state_of(self, signal_id: str) -> Tuple[str, Optional[str]]:
        """``(state, superseded_by)`` of a known signal (module notes)."""
        current = self.open_signal(signal_id)
        if current is not None:
            return current.ack_state, None
        closing = [record for record in self.acks_of(signal_id) if record.get("action") in signals.CLOSING_ACTIONS]
        if closing:
            return (STATE_ACTED if closing[-1].get("action") == "acted" else STATE_ACKNOWLEDGED), None
        record = self.find(signal_id)
        latest = self.latest_of_trade(record.get("trade_id"))
        return STATE_SUPERSEDED, (latest or {}).get("signal_id")


def _instant(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def _clock(value: Any) -> str:
    moment = _instant(value)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if moment else "unknown"


def rule_cooldowns(paths: desk_paths.DeskPaths) -> Dict[str, int]:
    """``{rule_id: cooldown_min}`` of the adopted rulebook (empty when it cannot be read: the ladder text then names
    no instant)."""
    try:
        _adopted, rulebook = exit_rules.load_adopted_rulebook(paths)
    except (exit_rules.RulebookError, OSError):
        return {}
    return {rule.id: rule.cooldown_min for rule in rulebook.rules}


def _primary_rule(item: signals.OpenSignal) -> Optional[str]:
    reasons = item.record.get("reasons") if isinstance(item.record, dict) else None
    first = reasons[0] if isinstance(reasons, list) and reasons and isinstance(reasons[0], dict) else {}
    rule_id = first.get("rule_id")
    return rule_id if isinstance(rule_id, str) else None


def ladder(item: signals.OpenSignal, cooldowns: Optional[Mapping[str, int]] = None) -> Dict[str, Any]:
    """The ladder position of an open signal (module notes): the runs after the first post and, as book_check counts
    them only outside the primary rule's ``cooldown_min``, the earliest instant of the next re-post."""
    if item.verdict != "EXIT_SIGNAL":
        return {"applies": False, "posts": item.posts, "of": None, "next_repost_after_runs": None, "done": False,
                "not_before": None, "rule_id": None, "cooldown_min": None}
    nxt = item.next_repost_after_runs
    rule_id = _primary_rule(item)
    cooldown = (cooldowns or {}).get(rule_id) if rule_id is not None else None
    last = item.last_posted_at or item.posted_at
    not_before = last + datetime.timedelta(minutes=cooldown) if cooldown is not None and nxt is not None else None
    return {"applies": True, "posts": item.posts, "of": LADDER_POSTS, "next_repost_after_runs": nxt, "done": nxt is None,
            "not_before": not_before, "rule_id": rule_id, "cooldown_min": cooldown}


def signal_entry(item: signals.OpenSignal, cooldowns: Optional[Mapping[str, int]] = None) -> Dict[str, Any]:
    return {"signal_id": item.signal_id, "trade_id": item.trade_id, "structure": item.structure,
            "verdict": item.verdict, "plan": item.plan, "posted_at": item.posted_at, "posts": item.posts,
            "last_posted_at": item.last_posted_at, "ack_state": item.ack_state,
            "snoozed_until": item.snoozed_until, "ladder": ladder(item, cooldowns)}


def _subject(trade_id: Any, structure: Any) -> str:
    """``AG-... covered_call``, or the one word when the trade and the structure coincide (``portfolio``)."""
    return str(trade_id) if trade_id == structure else "%s %s" % (trade_id, structure)


def _plan_words(plan: Any) -> str:
    return ", your plan: %s" % alerts.PLAN_WORDS.get(plan, plan) if isinstance(plan, str) else ""


def signal_line(item: signals.OpenSignal, cooldowns: Optional[Mapping[str, int]] = None) -> str:
    steps = ladder(item, cooldowns)
    times = "posted %d time%s" % (item.posts, "" if item.posts == 1 else "s")
    if item.verdict == "UNAVAILABLE":
        posts = "%s; re-posted on every run until acknowledged" % times
    elif not steps["applies"]:
        posts = "%s; no re-post ladder for %s" % (times, item.verdict)
    elif steps["done"]:
        posts = "posted %d of %d times; the re-post ladder is done" % (item.posts, LADDER_POSTS)
    else:
        runs = steps["next_repost_after_runs"]
        posts = "posted %d of %d times; next re-post %d run%s after the first post" % (
            item.posts, LADDER_POSTS, runs, "" if runs == 1 else "s")
        if steps["not_before"] is not None:
            posts += ", not before %s (rule %s cooldown %d min)" % (_clock(steps["not_before"]), steps["rule_id"],
                                                                     steps["cooldown_min"])
    status = item.ack_state
    if item.ack_state == STATE_SNOOZED:
        status = "snoozed until %s" % _clock(item.snoozed_until)
    return " | ".join(["%s" % item.signal_id, _subject(item.trade_id, item.structure),
                       "%s%s" % (item.verdict, _plan_words(item.plan)), "first posted %s" % _clock(item.posted_at),
                       posts, status])


# --------------------------------------------------------------------------------------------
# Hashes and the desk state
# --------------------------------------------------------------------------------------------


def _file_sha(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except FileNotFoundError:
        return None


def current_hashes(paths: desk_paths.DeskPaths) -> Tuple[Dict[str, Optional[str]], Optional[str]]:
    """``({desk_toml, positions_csv, rulebook, structuring_toml: sha256 | None}, adopted version | None)``: the SHA-256
    of each file's bytes, the rulebook being the file ``rules/ADOPTED`` names (:class:`ToolError` ``adopted_invalid``
    when that file cannot be read)."""
    version: Optional[str] = None
    rulebook: Optional[str] = None
    try:
        adopted = exit_rules.load_adopted(paths.adopted_file)
    except exit_rules.RulebookError as exc:
        if exc.code != "not_adopted":
            raise ToolError("adopted_invalid", str(exc)) from None
    else:
        version = adopted.version
        rulebook = _file_sha(exit_rules.rulebook_path(paths, adopted.version))
    hashes = {"rulebook": rulebook, "desk_toml": _file_sha(paths.desk_toml),
              "positions_csv": _file_sha(paths.positions_csv), "structuring_toml": _file_sha(paths.structuring_toml)}
    return {name: hashes[name] for name in HASH_NAMES}, version


def write_acknowledged_hashes(paths: desk_paths.DeskPaths, hashes: Mapping[str, Optional[str]],
                              now: datetime.datetime) -> List[str]:
    """Write ``acknowledged_hashes`` (and ``acknowledged_utc``) into ``state/desk_state.json`` under the desk lock,
    keeping every other key; returns warnings."""
    return update_desk_state(paths, {"acknowledged_hashes": dict(hashes), "acknowledged_utc": now})


def update_desk_state(paths: desk_paths.DeskPaths, values: Mapping[str, Any]) -> List[str]:
    """Set ``values`` in ``state/desk_state.json`` atomically under the desk lock, keeping every other key (a corrupt
    or non-object file is written afresh); returns warnings."""
    warnings: List[str] = []
    with state.locked(paths.lock_path):
        document, status = state.read_json(paths.desk_state_json)
        if status == state.STATUS_CORRUPT:
            warnings.append("state/desk_state.json was corrupt and has been quarantined; it is written afresh")
            document = {}
        elif status == state.STATUS_OK and not isinstance(document, dict):
            warnings.append("state/desk_state.json was not a JSON object; it is written afresh")
            document = {}
        elif document is None:
            document = {}
        document = dict(document)
        document.update(values)
        state.atomic_write_json(paths.desk_state_json, document)
    return warnings


def rows_sha(paths: desk_paths.DeskPaths, trade_id: str) -> Tuple[Optional[str], List[str]]:
    """``recon.group_rows_sha(recon.csv_table(<positions.csv text>), trade_id)`` exactly as ``csv_recon`` computes it."""
    try:
        with open(paths.positions_csv, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return None, ["positions.csv not found: rows_sha is null, so csv_recon cannot check the acted group"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    return recon.group_rows_sha(recon.csv_table(text), trade_id), []


def ack_record(now: datetime.datetime, action: str, signal_id: Optional[str] = None, trade_id: Optional[str] = None,
               until: Optional[datetime.datetime] = None, rows: Optional[str] = None,
               hashes: Optional[Mapping[str, Optional[str]]] = None) -> Dict[str, Any]:
    values = {"utc": now, "signal_id": signal_id, "trade_id": trade_id, "action": action,
              "until": state.format_utc(until) if until is not None else None, "rows_sha": rows,
              "hashes": dict(hashes) if hashes is not None else None}
    return {key: values[key] for key in ACK_RECORD_KEYS}


# --------------------------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------------------------


def do_list(paths: desk_paths.DeskPaths) -> Tuple[Dict[str, Any], List[str]]:
    journals = Journals(paths)
    entries = [signal_entry(item, journals.cooldowns) for item in journals.open]
    lines = [signal_line(item, journals.cooldowns) for item in journals.open]
    lines.append("%d open signal%s" % (len(entries), "" if len(entries) == 1 else "s"))
    lines.extend("warning: %s" % warning for warning in journals.warnings)
    return {"action": "list", "count": len(entries), "signals": entries, "warnings": journals.warnings}, lines


def do_show(paths: desk_paths.DeskPaths, signal_id: str) -> Tuple[Dict[str, Any], List[str]]:
    journals = Journals(paths)
    record = journals.find(signal_id)
    status, superseded_by = journals.state_of(signal_id)
    alerts_of = journals.alerts_of(signal_id)
    posts = len([alert for alert in alerts_of if alert.get("lint_ok") is True])
    acks_of = journals.acks_of(signal_id)
    lines = [" | ".join([signal_id, _subject(record.get("trade_id"), record.get("structure")),
                         "%s%s" % (record.get("verdict"), _plan_words(record.get("plan"))),
                         "first posted %s" % _clock(record.get("posted_at")),
                         "posted %d time%s" % (posts, "" if posts == 1 else "s"),
                         status if superseded_by is None else "superseded by %s" % superseded_by])]
    for item in acks_of:
        lines.append("%s at %s" % (item.get("action"), _clock(item.get("utc"))))
    if alerts_of:
        last = alerts_of[-1]
        lines.append("last post (%s):" % _clock(last.get("posted_at")))
        lines.extend(str(last.get("text") or "").splitlines())
    lines.extend("warning: %s" % warning for warning in journals.warnings)
    payload = {"action": "show", "signal": record, "state": status, "superseded_by": superseded_by, "posts": posts,
               "alerts": alerts_of, "acks": acks_of, "warnings": journals.warnings}
    return payload, lines


def do_signal_write(paths: desk_paths.DeskPaths, action: str, signal_id: str, now: datetime.datetime,
                    until: Optional[datetime.datetime]) -> Tuple[Dict[str, Any], List[str]]:
    journals = Journals(paths)
    record = journals.find(signal_id)
    before, _superseded_by = journals.state_of(signal_id)
    trade_id = record.get("trade_id") if isinstance(record.get("trade_id"), str) else None
    warnings = list(journals.warnings)
    rows: Optional[str] = None
    if action == "snooze" and before not in (STATE_OPEN, STATE_SNOOZED):
        raise ToolError("signal_not_open", "%s is %s: only an open signal can be snoozed" % (signal_id, before))
    if action == "acted" and trade_id is not None:
        rows, notes = rows_sha(paths, trade_id)
        warnings.extend(notes)
    entry = ack_record(now, action, signal_id, trade_id, until=until, rows=rows)
    desk_paths.ensure_runtime_dirs(paths)
    signals.append_record(paths.acks_jsonl, entry)
    after, _ = Journals(paths).state_of(signal_id)
    if action == "ack":
        line = "%s acknowledged at %s (%s %s): it is no longer re-posted" % (
            signal_id, _clock(now), trade_id, record.get("structure"))
    elif action == "snooze":
        line = "%s snoozed until %s (%s %s)" % (signal_id, _clock(until), trade_id, record.get("structure"))
    else:
        line = "%s recorded as acted on at %s (%s %s); rows sha %s" % (
            signal_id, _clock(now), trade_id, record.get("structure"), rows[:8] if rows else "none")
        if rows:
            line += ": csv_recon reports ACTED_UNCHANGED while the group's rows in positions.csv stay unchanged"
    lines = [line] + ["warning: %s" % warning for warning in warnings]
    payload = {"action": action, "record": entry, "signal_id": signal_id, "trade_id": trade_id,
               "state_before": before, "state_after": after, "path": paths.relative(paths.acks_jsonl),
               "warnings": warnings}
    return payload, lines


def do_config(paths: desk_paths.DeskPaths, now: datetime.datetime) -> Tuple[Dict[str, Any], List[str]]:
    hashes, version = current_hashes(paths)
    desk_paths.ensure_runtime_dirs(paths)
    warnings = write_acknowledged_hashes(paths, hashes, now)
    entry = ack_record(now, "config", hashes=hashes)
    signals.append_record(paths.acks_jsonl, entry)

    def sha(name: str) -> str:
        value = hashes[name]
        return "sha %s" % value[:8] if value else "missing"

    rulebook = ("adopted rulebook %s %s" % (version, sha("rulebook")) if version is not None
                else "adopted rulebook: none adopted")
    structuring = ", structuring.toml %s" % sha("structuring_toml") if hashes["structuring_toml"] else ""
    lines = ["configuration acknowledged at %s: desk.toml %s, positions.csv %s, %s%s" % (
        _clock(now), sha("desk_toml"), sha("positions_csv"), rulebook, structuring)]
    lines.extend("warning: %s" % warning for warning in warnings)
    payload = {"action": "config", "record": entry, "acknowledged_hashes": hashes, "rulebook_version": version,
               "path": paths.relative(paths.acks_jsonl), "warnings": warnings}
    return payload, lines


def do_connectors(paths: desk_paths.DeskPaths, now: datetime.datetime) -> Tuple[Dict[str, Any], List[str]]:
    desk_paths.ensure_runtime_dirs(paths)
    warnings = update_desk_state(paths, {CONNECTOR_KEY: now})
    entry = ack_record(now, "connectors")
    signals.append_record(paths.acks_jsonl, entry)
    lines = ["connector readback recorded at %s; desk_doctor re-asks after %d days"
             % (now.astimezone(datetime.timezone.utc).strftime("%H:%M UTC"), CONNECTOR_REASK_DAYS)]
    lines.extend("warning: %s" % warning for warning in warnings)
    payload = {"action": "connectors", "record": entry, "connector_readback_utc": now,
               "path": paths.relative(paths.acks_jsonl), "warnings": warnings}
    return payload, lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    now = args.now or state.utc_now()
    validate(parser, args, now)
    try:
        paths = desk_paths.for_root(args.desk_root)
    except desk_paths.DeskPathsError as exc:
        return fail(args, version, now, "desk_root", str(exc))
    try:
        if args.action == "list":
            payload, lines = do_list(paths)
        elif args.action == "show":
            payload, lines = do_show(paths, args.signal_id)
        elif args.action == "config":
            payload, lines = do_config(paths, now)
        elif args.action == "connectors":
            payload, lines = do_connectors(paths, now)
        else:
            payload, lines = do_signal_write(paths, args.action, args.signal_id, now, args.until_utc)
    except ToolError as exc:
        return fail(args, version, now, exc.code, exc.message)
    except state.LockBusy as exc:
        return fail(args, version, now, "lock_busy", str(exc))
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot read or write under the desk root: %s" % exc)
    if args.json:
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now)))
    else:
        print("\n".join(lines))
    return contract.EXIT_QUIET


if __name__ == "__main__":
    sys.exit(main())
