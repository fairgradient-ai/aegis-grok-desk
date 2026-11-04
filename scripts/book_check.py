#!/usr/bin/env python3
"""book_check.py: the routine check: quotes, the book, the adopted exit rules, the posting rules, the message.

Usage::

    python3 scripts/book_check.py [--post] [--summary] [--watch-alive] [--next-run-utc ISO-8601-UTC]
        [--routine ID] [--scheduled-et HH:MM] [--fixture-set NAME] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

One run: the book through ``aegis_desk.book.load_book`` (one quotes request, graded in the session ``--now`` falls
in with the calendar's full closures), the rulebook ``rules/ADOPTED`` names (``exit_rules.load_adopted_rulebook``:
its bytes, version and per-rule shas must match the adoption), the evaluation (``exit_rules.evaluate`` with the
rule state of ``state/rule_state.json``, the positions rows for the ``#tags`` and opened dates, and
``--next-run-utc``: without it another observation is coming, so M and G rules wait for their confirmations; a
next run after the session's close lets one observation stand), the state transitions (``rule_state.apply``), the
posting rules, the message (``aegis_desk.alerts``), ``alert_lint`` and the heartbeat. Prints the message to post,
or the one word ``quiet`` when there is nothing to post.

Posting rules (plan section 11)
-------------------------------
The desk keeps the last verdict it recorded for every open group and for the portfolio section
(``state/desk_state.json["last_verdicts"]``). A group's message is posted as a NEW signal (``journal/signals.jsonl``,
``SIG-<ET date>-<NNN>``) when its verdict changed (EXIT_SIGNAL, WATCH or UNAVAILABLE began; a first sight of a
HOLD group is not a change) or when one of its rules newly fired (``RuleResult.new_signal``) more than that rule's
``cooldown_min`` after its last post. A rule that fired and cleared and is true again before it re-arms is
``cooldown`` (``exit_rules``): it sets no verdict, so a condition flapping across its threshold posts one signal,
then the HOLD notice once, then nothing until the rule re-arms (the next trading day, a hysteresis retreat, and
never inside ``cooldown_min`` of its last post, except that the hold ends at the next session's open;
``rule_state``). A group whose verdict became HOLD after a posted
verdict gets the one-line ``HOLD <trade> | ... none of your rules fires now; was <verdict>`` notice (UNAVAILABLE
ended, or the rule that fired is clear again); it is no signal. A standing UNAVAILABLE is re-posted on every run
under its signal id until the owner acknowledges it (``ack.py ack`` or ``acted``; a snooze holds it until its
``--until``): the banner is the message whenever the book is unavailable, UNAVAILABLE is a verdict, never silence
and never HOLD, and an acknowledged banner stays quiet until the verdict changes.
An unacknowledged EXIT_SIGNAL (the latest signal of its trade, neither ``ack`` nor ``acted``, not snoozed past now:
``ack.py``) whose group is still EXIT_SIGNAL is re-posted under its signal id on the ladder: on the next run of this
routine after its first post, then three runs, then seven runs after it (``signals.repost_due``; the runs are this
routine's heartbeats in ``state/heartbeat/history.jsonl`` after the first post, plus this run), and never sooner
than its primary rule's ``cooldown_min`` after the last post (the minimum minutes between two posts of one (group,
rule), ``rule_state.may_post``: the ladder's runs are counted only outside that cooldown, so at the default 240
minutes and two-hour routine spacing the first re-post comes on the second run; ``ack.py list`` names the earliest
instant). WATCH signals have no ladder. The portfolio section (underlying and portfolio rules) posts its WATCH message
on the same rules; it never makes a group UNAVAILABLE and never sets the exit code. Its verdict is WATCH (a rule
fired), UNAVAILABLE (none fired and one cannot be read: never HOLD) or HOLD; while it is UNAVAILABLE nothing is
posted for it and its verdict of record stands (the group banners carry the cause).

The message
-----------
In order: ``CONFIG CHANGED`` first whenever the SHA-256 of ``desk.toml``, ``positions.csv`` or the adopted
rulebook file differs from ``state/desk_state.json["acknowledged_hashes"]`` (a missing file or key is
unacknowledged; printed on every run until ``ack.py config --owner-tapped``; the run still evaluates), then the
``rulebook changed: N verdicts changed`` digest once after an adoption (``state/desk_state.json["rulebook_change"]``
= ``{version, sha256, previous_version, previous_sha256, adopted_utc, changed, snapshot}`` as ``rules_adopt.py``
records it, printed while ``version`` and ``sha256`` name the adopted file and no ``posted_utc`` is set; ``--post``
sets ``posted_utc``), then every posted group message in evaluation
order and the portfolio message, then the pre-close line with ``--summary`` (always printed: verdict counts, open
signals, data age, ``verify live quotes in your broker before acting``), then, with ``--watch-alive`` and nothing
else to post, the ``watch alive`` line when no linted message is in ``journal/alerts.jsonl`` since today's open
(from the open on; before it the run is quiet).
The parts are composed under ONE footer (``alerts.compose``). Every part is linted on its own against the numbers,
rule ids, identifiers and clocks its formatters emitted (``Alert.lint``, strict); a part that fails is replaced by
the fixed withheld banner (``alerts.withheld_text``: its kind, its trade id and the finding codes, never its text)
and named in ``payload.withheld`` and the warnings, so one bad part can never block the others (an EXIT SIGNAL
above all). The composed message is then linted against the strict emitted document (``payload.emitted``, the
``--emitted-file`` for ``alert_lint.py``); a failure there is exit 1 with the problems (error code ``lint_failed``),
never a posted message (with ``--post`` it is journaled with ``lint_ok`` false and the rule state is not consumed,
so the next run tries again). On a day without a session
(weekend, NYSE holiday, an owner-injected full closure) the run prints ``NYSE closed (<reason>); no evaluation``,
fetches nothing, evaluates nothing and exits 0.

``--post`` and the dry run
--------------------------
Without ``--post`` the run is a dry run: ``dry run`` is the first line (``payload.dry_run`` in JSON), the exit code
is the one the posted run would return, and none of the desk's records is written (no rule state, journal, desk
state or heartbeat; the quote cache and its request ledger belong to the market-data layer and are written by
every market tool, and the state layer moves a corrupt file aside whenever it reads one). With ``--post`` the run
holds ``state/.book_check.lock`` from reading the rule state to the last write (two overlapping routines cannot
post one transition twice; busy is exit 1 ``lock_busy``) and persists, in this order: the signals and alerts
journals (each posted part one ``alerts.jsonl`` record, under its signal id when it is one, and the day's
``journal/<ET date>.md``), the rule state with the posts recorded (``rule_state.record_post``), the desk state's
``last_verdicts`` (and ``rulebook_change.posted_utc``), then the heartbeat ``state/heartbeat/<routine>.json``
(routine id default ``book-check``; every ``--post`` run on an existing desk root is recorded, a failed one with its
exit code 1 or 20 and no message hash; ``--scheduled-et`` gives its lateness and the ``run N min late`` segment).

Exit 10 when the message contains an EXIT SIGNAL (a new one or a re-post), 20 when any group is UNAVAILABLE (and
none is EXIT SIGNAL: the banners are the message, or ``quiet`` once each is acknowledged), 0 quiet or informational
(WATCH, HOLD notices, CONFIG CHANGED, the digest, the pre-close and watch-alive lines, a closed day), 1 on an error (``desk_root``, ``config_invalid``,
``positions_missing``, ``positions_invalid``, ``calendar_invalid``, the rulebook's ``not_adopted``,
``adopted_invalid``, ``rules_missing``, ``rules_tampered``, ``render_failed``, ``lint_failed``, ``io_error``,
``lock_busy``; market data refused is 20 as in ``book.py``), 2 on usage. The underlying and portfolio sections
(``no_history``, ``unavailable``, WATCH) never change the exit code. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="book_check")

import contextlib  # noqa: E402
import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

from aegis_desk import (  # noqa: E402
    alert_lint,
    alerts,
    book,
    desk_config,
    desk_paths,
    exit_rules,
    heartbeat,
    market_clock,
    quote_cache,
    rule_state,
    signals,
    state,
)
from aegis_desk.version import release_version  # noqa: E402

TOOL = "book_check"
DESCRIPTION = ("the routine check: the book, the adopted exit rules, the rule state and the posting rules; prints the "
               "linted message to post (EXIT SIGNAL, WATCH, UNAVAILABLE, CONFIG CHANGED, pre-close, watch alive) or "
               "quiet; --post records it")
NETWORK_ACCESS = "GET https://sandbox.tradier.com/v1/markets/quotes only (through the quote cache); none with --offline"
DEFAULT_ROUTINE = "book-check"
RUN_LOCK_FILE = ".book_check.lock"
RUN_LOCK_TIMEOUT_S = 5.0
QUIET_LINE = "quiet"
DRY_RUN_LINE = "dry run"
CLOSED_DAY_LINE = "NYSE closed (%s); no evaluation"
HASH_NAMES: Tuple[str, ...] = ("desk_toml", "positions_csv", "rulebook")
"""The acknowledged files, in the order CONFIG CHANGED names them (``ack.py config`` writes the same names)."""
SHA256_RE = exit_rules.SHA256_RE  # anchored with \Z: a value with a trailing newline is no acknowledged hash
PORTFOLIO_KEY = alerts.PORTFOLIO_TRADE
LAST_VERDICTS_KEY = "last_verdicts"
RULEBOOK_CHANGE_KEY = "rulebook_change"
ACKNOWLEDGED_KEY = "acknowledged_hashes"
VERDICT_EXIT_SIGNAL = exit_rules.VERDICT_EXIT_SIGNAL
VERDICT_WATCH = exit_rules.VERDICT_WATCH
VERDICT_UNAVAILABLE = exit_rules.VERDICT_UNAVAILABLE
VERDICT_HOLD = exit_rules.VERDICT_HOLD
POSTED_VERDICTS: Tuple[str, ...] = (VERDICT_EXIT_SIGNAL, VERDICT_WATCH, VERDICT_UNAVAILABLE)
KIND_HOLD = "hold"
PART_KINDS: Tuple[str, ...] = (alerts.KIND_CONFIG_CHANGED, alerts.KIND_RULEBOOK_CHANGED, alerts.KIND_EXIT_SIGNAL,
                               alerts.KIND_WATCH, alerts.KIND_UNAVAILABLE, KIND_HOLD, alerts.KIND_PRE_CLOSE,
                               alerts.KIND_WATCH_ALIVE)
REASON_VERDICT_CHANGED = "verdict_changed"
REASON_NEW_RULE_FIRED = "new_rule_fired"
REASON_STILL_UNAVAILABLE = "still_unavailable"
REASON_LADDER = "ladder"
REASONS: Tuple[str, ...] = (REASON_VERDICT_CHANGED, REASON_NEW_RULE_FIRED, REASON_STILL_UNAVAILABLE, REASON_LADDER,
                            alerts.KIND_CONFIG_CHANGED, alerts.KIND_RULEBOOK_CHANGED, alerts.KIND_PRE_CLOSE,
                            alerts.KIND_WATCH_ALIVE)
SIGNAL_NEW = "new"
SIGNAL_REPOST = "repost"
HOLD_SENTENCE = "none of your rules fires now; was %s"
PORTFOLIO_HOLD_SENTENCE = "none of your portfolio or underlying rules fires now; was %s"
OUTPUT: Dict[str, Any] = {
    "dry_run": "boolean (no --post: nothing written; the exit code is the posted run's)",
    "posted": "boolean (--post and the records were written)",
    "quiet": "boolean (nothing to post: the output is the word quiet)",
    "closed_day": "boolean (no session today: nothing evaluated)",
    "message": "string | null: the composed message to post, exactly as printed (after the dry run line)",
    "parts": [{"kind": " | ".join(PART_KINDS), "reason": " | ".join(REASONS),
               "group_id": "string | portfolio | null (a desk message)", "trade_id": "string | null",
               "verdict": "EXIT_SIGNAL | WATCH | UNAVAILABLE | HOLD | null",
               "signal": "new | repost | null (no signal)", "signal_id": "SIG-YYYYMMDD-NNN | null (a dry run's new signal)"}],
    "lint": {"ok": "boolean", "problems": ["string"], "numbers_checked": "integer"},
    "emitted_numbers": ["the number strings the formatters printed"],
    "emitted": {"emitted_numbers": ["string"], "rule_ids": ["string"], "identifiers": ["string"],
                "clocks": ["HH:MM UTC | HH:MM ET"], "as_of": "ISO-8601 UTC | null (the footer's instant)",
                "note": "the strict emitted document: alert_lint.py --emitted-file (null keys when nothing is posted)"},
    "withheld": [{"group_id": "string | portfolio | null", "kind": "the part's kind",
                  "codes": ["the lint finding codes that withheld its text"]}],
    "config_changed": [{"name": "desk_toml | positions_csv | rulebook", "old": "acknowledged sha256 | null",
                        "new": "current sha256 | null"}],
    "rulebook": {"version": "X.Y.Z", "sha256": "hex of the adopted file", "adopted_utc": "ISO-8601 UTC"},
    "verdicts": [{"group_id": "string", "trade_id": "string | null", "structure": "string | null",
                  "verdict": "EXIT_SIGNAL | UNAVAILABLE | WATCH | HOLD", "previous": "the recorded verdict | null",
                  "quarantined": "boolean", "fired": ["rule id"], "unavailable": ["rule id"], "pending": ["rule id"],
                  "cooldown": ["rule id (true again before it re-arms: no verdict, no signal)"],
                  "suppressed": ["rule id"], "new_signals": ["rule id (newly fired this run)"]}],
    "portfolio": {"verdict": "WATCH | UNAVAILABLE | HOLD", "previous": "WATCH | HOLD | null", "fired": ["rule id"],
                  "unavailable": ["rule id"], "no_history": ["rule id"], "pending": ["rule id"],
                  "cooldown": ["rule id"], "new_signals": ["rule id"]},
    "counts": {"EXIT_SIGNAL": "n", "UNAVAILABLE": "n", "WATCH": "n", "HOLD": "n"},
    "open_signals": "integer: the open signals once this run's are posted",
    "signals": [{"signal_id": "SIG-YYYYMMDD-NNN | null", "trade_id": "string", "verdict": "string",
                 "kind": "new | repost"}],
    "routine": "the routine id",
    "heartbeat": "state/heartbeat/<routine>.json | null (dry run)",
    "written": ["desk-root relative paths written"],
    "rule_state_status": " | ".join(rule_state.LOAD_STATUSES) + " | null (closed day)",
    "session": "market_clock.SessionInfo.as_dict() (with the calendar's full closures)",
    "evaluation_utc": "ISO-8601 UTC (--now)",
    "next_run_utc": "ISO-8601 UTC | null",
    "single_observation": "boolean | null: one observation suffices this run (decision 17(i))",
    "quote_age_min": "integer | null: minutes from the newest quote to --now",
    "run_lateness_min": "integer | null (--scheduled-et)",
    "warnings": ["string"],
    "exit_code": "10 | 20 | 0",
}


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--post", action="store_true",
                        help="record the run: rule state, signal and alert journals, desk state, heartbeat")
    parser.add_argument("--summary", action="store_true", help="always print the pre-close line")
    parser.add_argument("--watch-alive", action="store_true",
                        help="print the watch-alive line when nothing has been posted since today's open")
    parser.add_argument("--next-run-utc", default=None, metavar="ISO-8601-UTC",
                        help="the routine's next run: after the session's close, one observation is enough")
    parser.add_argument("--routine", default=DEFAULT_ROUTINE, metavar="ID",
                        help="the routine id of the heartbeat and of the re-post ladder (default %s)" % DEFAULT_ROUTINE)
    parser.add_argument("--scheduled-et", default=None, metavar="HH:MM",
                        help="the routine's America/New_York slot, for its lateness")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/tradier/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any, now: datetime.datetime) -> None:
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    args.next_run = None
    if args.next_run_utc is not None:
        try:
            args.next_run = rule_state.check_next_run(contract.parse_utc_instant(args.next_run_utc, "--next-run-utc"), now)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        heartbeat.validate_routine_id(args.routine)
        if args.scheduled_et is not None:
            heartbeat.validate_scheduled_et(args.scheduled_et)
    except heartbeat.HeartbeatError as exc:
        parser.error(str(exc))


class Failure(Exception):
    """A run that ends with an error envelope: ``code``, ``message`` and the exit code."""

    def __init__(self, code: str, message: str, exit_code: int = contract.EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    status = contract.status_for(exit_code)
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, status, generated_at=now)))
    elif exit_code == contract.EXIT_UNAVAILABLE:
        print("UNAVAILABLE: %s" % message)
    else:
        print("error: %s" % message)
    return exit_code


def run_lock_path(paths: desk_paths.DeskPaths) -> str:
    """``state/.book_check.lock``: held by a ``--post`` run from reading the rule state to its last write."""
    return os.path.join(paths.state_dir, RUN_LOCK_FILE)


@contextlib.contextmanager
def _no_lock() -> Iterator[None]:
    yield


@dataclass
class Part:
    """One part of the message: its alert, what it is and why it is posted, and the signal it stands for."""

    alert: alerts.Alert
    kind: str
    reason: str
    group_id: Optional[str] = None
    trade_id: Optional[str] = None
    verdict: Optional[str] = None
    signal: Optional[str] = None
    signal_id: Optional[str] = None
    rule_keys: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "reason": self.reason, "group_id": self.group_id, "trade_id": self.trade_id,
                "verdict": self.verdict, "signal": self.signal, "signal_id": self.signal_id}


# --------------------------------------------------------------------------------------------
# Helpers over the book document and the journals
# --------------------------------------------------------------------------------------------


def _instant(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def book_as_of(document: Mapping[str, Any]) -> datetime.datetime:
    """The newest quote as-of of the book (the footer's instant), else its ``generated_at``."""
    stamps = [stamp for stamp in (_instant(leg.get("as_of")) for leg in document.get("legs") or []) if stamp]
    if stamps:
        return max(stamps)
    generated = _instant(document.get("generated_at"))
    if generated is None:
        raise ValueError("the book has neither a quote as-of nor generated_at")
    return generated


def _source_delay(document: Mapping[str, Any]) -> int:
    sources = document.get("sources") or []
    delay = (sources[0] if sources else {}).get("delay_min")
    return delay if isinstance(delay, int) and not isinstance(delay, bool) else 0


def hold_notice(group_id: str, trade_id: str, words: str, previous: str, document: Mapping[str, Any],
                rulebook: exit_rules.Rulebook, portfolio: bool = False) -> alerts.Alert:
    """``HOLD <trade> | <subject> | rules X.Y.Z, sha <8 hex>: none of your rules fires now; was <verdict> | not an
    instruction ...`` plus the footer: a group (or the portfolio section) back at HOLD after a posted verdict."""
    sentence = (PORTFOLIO_HOLD_SENTENCE if portfolio else HOLD_SENTENCE) % alerts.VERDICT_WORDS.get(previous, previous)
    run_info = {"rulebook_version": rulebook.rulebook_version, "rulebook_sha": rulebook.sha256, "quote_age_min": None,
                "run_lateness_min": None, "posted_at": None}
    return alerts.hold_text(trade_id, words, sentence, document, run_info, group_id)


def record_heartbeat(paths: desk_paths.DeskPaths, args: Any, version: str, now: datetime.datetime, exit_code: int,
                     message: Optional[str]) -> heartbeat.Heartbeat:
    """The run's heartbeat (``--post`` runs only): ``state/heartbeat/<routine>.json`` and ``history.jsonl``, the
    status the contract gives the exit code (``ok`` for a posted message, ``quiet`` for none), the hash of the
    posted message, the lateness against ``--scheduled-et``; started at ``now`` (``--now`` or the clock read once)."""
    status = contract.status_for(exit_code) if exit_code else (contract.STATUS_OK if message else contract.STATUS_QUIET)
    digest = heartbeat.sha256_hex(message.encode("utf-8")) if message else None
    finished = now if args.now is not None else max(now, state.utc_now())
    return heartbeat.write_heartbeat(paths, args.routine, now=now, exit_code=exit_code, status=status, started_utc=now,
                                     finished_utc=finished, message_sha256=digest, scheduled_et=args.scheduled_et,
                                     tool_version=version)


def _coded(result: alert_lint.LintResult) -> List[str]:
    """The lint's problems prefixed with their finding codes (``number_not_emitted: line 1, column 9: ...``)."""
    if result.findings:
        return ["%s: %s" % (finding.code, finding.message) for finding in result.findings]
    return list(result.problems)


def _ids(results: Sequence[Any]) -> List[str]:
    return [result.rule_id for result in results]


def _section_subject(section: exit_rules.SectionVerdict) -> str:
    return exit_rules.PORTFOLIO if section.scope == exit_rules.SCOPE_PORTFOLIO else "underlying:%s" % section.subject


# --------------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------------


class Check:
    """One run of the routine check (module notes)."""

    def __init__(self, args: Any, version: str, now: datetime.datetime, market: quote_cache.MarketData) -> None:
        self.args = args
        self.version = version
        self.now = now
        self.started = now
        self.market = market
        self.paths = market.paths
        self.warnings: List[str] = []
        self.written: List[str] = []
        self.withheld: List[Dict[str, Any]] = []
        self.message: Optional[str] = None

    # ---- inputs ---------------------------------------------------------------------------------

    def load(self) -> None:
        try:
            self.run = book.load_book(self.market)
        except book.BookInputError as exc:
            raise Failure(exc.code, exc.message) from None
        except OSError as exc:
            raise Failure("io_error", "cannot read the desk files or use the cache: %s" % exc) from None
        self.document = self.run.document
        self.session = self.run.session
        try:
            self.adopted, self.rulebook = exit_rules.load_adopted_rulebook(self.paths)
        except exit_rules.RulebookError as exc:
            raise Failure(exc.code, str(exc)) from None
        self.desk_state = self.read_desk_state()
        stamps = [stamp for stamp in (_instant(leg.get("as_of")) for leg in self.document["legs"]) if stamp]
        self.quote_age = quote_cache.age_minutes(self.now, max(stamps)) if stamps else None
        self.lateness, _reason = heartbeat.lateness(self.args.scheduled_et, self.started)
        self.run_info = {"rulebook_version": self.rulebook.rulebook_version, "rulebook_sha": self.rulebook.sha256,
                         "quote_age_min": self.quote_age, "run_lateness_min": self.lateness, "posted_at": self.now}

    def read_desk_state(self) -> Dict[str, Any]:
        document, status = state.read_json(self.paths.desk_state_json)
        if status == state.STATUS_CORRUPT:
            self.warnings.append("state/desk_state.json was corrupt and has been quarantined: the acknowledged hashes "
                                 "and the last verdicts are unknown")
            return {}
        if status == state.STATUS_OK and not isinstance(document, dict):
            self.warnings.append("state/desk_state.json is not a JSON object: it is read as empty")
            return {}
        return dict(document) if isinstance(document, dict) else {}

    def current_hashes(self) -> Dict[str, Optional[str]]:
        hashes = {"rulebook": self.rulebook.sha256, "desk_toml": self.market.cfg.sha256,
                  "positions_csv": self.run.positions.sha256}
        return {name: hashes[name] for name in HASH_NAMES}

    def config_changes(self) -> List[Dict[str, Optional[str]]]:
        acknowledged = self.desk_state.get(ACKNOWLEDGED_KEY)
        acknowledged = acknowledged if isinstance(acknowledged, dict) else {}
        changes = []
        for name, current in self.current_hashes().items():
            old = acknowledged.get(name)
            old = old if isinstance(old, str) and SHA256_RE.match(old) else None
            if old != current:
                changes.append({"name": name, "old": old, "new": current})
        return changes

    def rulebook_change(self) -> Optional[Dict[str, Any]]:
        record = self.desk_state.get(RULEBOOK_CHANGE_KEY)
        if record is None:
            return None
        if not isinstance(record, dict):
            self.warnings.append("state/desk_state.json rulebook_change is not an object: no digest printed")
            return None
        if record.get("version") != self.rulebook.rulebook_version or record.get("posted_utc") is not None:
            return None
        if record.get("sha256") is not None and record.get("sha256") != self.rulebook.sha256:
            return None  # a record of another file under the same version (rules_adopt names the bytes it adopted)
        previous, changed = record.get("previous_version"), record.get("changed")
        if previous is not None and not (isinstance(previous, str) and exit_rules.VERSION_RE.match(previous)):
            self.warnings.append("state/desk_state.json rulebook_change.previous_version is not X.Y.Z: no digest printed")
            return None
        if changed is not None and (isinstance(changed, bool) or not isinstance(changed, int) or changed < 0):
            self.warnings.append("state/desk_state.json rulebook_change.changed is not a count: no digest printed")
            return None
        return record

    # ---- evaluation and the posting rules ------------------------------------------------------

    def evaluate(self) -> None:
        self.entries, self.rule_state_status = rule_state.load_with_status(self.paths)
        if self.rule_state_status not in (rule_state.LOAD_OK, rule_state.LOAD_MISSING):
            self.warnings.append("state/rule_state.json was %s: the rules it held are observed afresh"
                                 % self.rule_state_status)
        self.evaluation = exit_rules.evaluate(self.rulebook, self.document, now=self.now, session=self.session,
                                              calendar=self.run.calendar, state=self.entries,
                                              next_run_utc=self.args.next_run, rows=self.run.positions.rows)
        self.warnings.extend(self.evaluation.warnings)
        self.new_entries = rule_state.apply(self.entries, self.evaluation.state_updates, self.now)
        try:
            self.rendered = alerts.render(self.evaluation, self.document, self.run_info)
        except ValueError as exc:
            raise Failure("render_failed", "the alert text cannot be built: %s" % exc) from None
        self.signal_records, status = signals.read_signals(self.paths)
        if status == signals.STATUS_PARTIAL:
            self.warnings.append("journal/signals.jsonl has lines that do not parse: they were skipped (the file is kept)")
        acks, status, _bad = signals.read_journal(self.paths.acks_jsonl)
        if status == signals.STATUS_PARTIAL:
            self.warnings.append("state/acks.jsonl has lines that do not parse: they were skipped (the file is kept)")
        self.open = signals.open_signals(self.paths, [item for item in acks if isinstance(item, dict)])
        history, _status, _bad = signals.read_journal(heartbeat.history_path(self.paths))
        self.history = [_instant(item.get("run_utc")) for item in history
                        if isinstance(item, dict) and item.get("routine_id") == self.args.routine]
        self.fired_now = {(update["subject"], update["rule_id"]) for update in self.evaluation.state_updates
                          if update.get("fired")}
        recorded = self.desk_state.get(LAST_VERDICTS_KEY)
        self.recorded: Dict[str, Any] = dict(recorded) if isinstance(recorded, dict) else {}

    def may_post(self, subject: str, result: Any) -> bool:
        entry = self.new_entries.get(rule_state.state_key(subject, result.rule_id))
        return rule_state.may_post(entry if isinstance(entry, Mapping) else None, self.now, int(result.cooldown_min))

    def newly_fired(self, subject: str, results: Sequence[Any]) -> List[Any]:
        return [result for result in results if (subject, result.rule_id) in self.fired_now
                and self.may_post(subject, result)]

    def latest_signal(self, trade_id: str) -> Optional[Dict[str, Any]]:
        latest = None
        for record in self.signal_records:
            if record.get("trade_id") == trade_id and signals.parse_signal_id(record.get("signal_id")) is not None:
                latest = record
        return latest

    def still_open(self, signal_id: str) -> bool:
        """True while the signal is open and not snoozed past now (an ``ack`` or ``acted`` record closes it)."""
        item = next((entry for entry in self.open if entry.signal_id == signal_id), None)
        if item is None:
            return False
        if item.ack_state == signals.ACK_STATE_SNOOZED:
            until = _instant(item.snoozed_until)
            return until is not None and self.now >= until
        return True

    def runs_since(self, first_post: datetime.datetime) -> int:
        """This routine's runs after ``first_post`` (its recorded heartbeats, then this run)."""
        return 1 + len([stamp for stamp in self.history if stamp is not None and first_post < stamp < self.now])

    def ladder_signal(self, trade_id: str, group: exit_rules.GroupVerdict) -> Optional[str]:
        """The open EXIT_SIGNAL of ``trade_id`` when the ladder says re-post it now, else ``None``."""
        item = next((entry for entry in self.open if entry.trade_id == trade_id), None)
        if item is None or item.verdict != VERDICT_EXIT_SIGNAL:
            return None
        if item.ack_state == signals.ACK_STATE_SNOOZED:
            until = _instant(item.snoozed_until)
            if until is None or self.now < until:
                return None
        if not signals.repost_due(item.posts, self.runs_since(item.posted_at)):
            return None
        primary = next((result for result in group.fired if result.verdict == VERDICT_EXIT_SIGNAL), None)
        if primary is None or not self.may_post(group.group_id, primary):
            return None
        return item.signal_id

    def group_words(self, group_id: str) -> str:
        for record in self.document["groups"]:
            if record.get("group_id") == group_id:
                structure = str(record.get("structure"))
                return "%s %s" % (record.get("underlying"), alerts.STRUCTURE_WORDS.get(structure, structure))
        return alerts.QUARANTINED_WORDS

    def plan(self) -> List[Part]:
        parts: List[Part] = []
        by_group = {alert.group_id: alert for alert in self.rendered if alert.group_id is not None}
        portfolio_alert = next((alert for alert in self.rendered if alert.group_id is None), None)
        self.verdicts_now: Dict[str, str] = {}
        self.verdict_rows: List[Dict[str, Any]] = []
        for group in self.evaluation.groups:
            group_id, current = group.group_id, group.verdict
            previous = self.recorded.get(group_id)
            self.verdicts_now[group_id] = current
            alert = by_group.get(group_id)
            trade_id = str(alert.signal_record["trade_id"]) if alert is not None and alert.signal_record else str(
                group.trade_id or group_id)
            newly = self.newly_fired(group_id, group.fired)
            self.verdict_rows.append({
                "group_id": group_id, "trade_id": group.trade_id, "structure": group.structure, "verdict": current,
                "previous": previous, "quarantined": group.quarantined, "fired": _ids(group.fired),
                "unavailable": _ids(group.unavailable), "pending": _ids(group.pending), "cooldown": _ids(group.cooldown),
                "suppressed": _ids(group.suppressed), "new_signals": _ids(newly)})
            keys = [rule_state.state_key(group_id, result.rule_id) for result in group.fired]
            changed = current != previous and not (previous is None and current == VERDICT_HOLD)
            if current in POSTED_VERDICTS and alert is not None:
                part = Part(alert, alert.kind, "", group_id, trade_id, current, rule_keys=keys)
                if changed:
                    part.reason, part.signal = REASON_VERDICT_CHANGED, SIGNAL_NEW
                elif newly:
                    part.reason, part.signal = REASON_NEW_RULE_FIRED, SIGNAL_NEW
                elif current == VERDICT_UNAVAILABLE:
                    latest = self.latest_signal(trade_id)
                    if latest is not None and latest.get("verdict") == VERDICT_UNAVAILABLE:
                        if not self.still_open(latest["signal_id"]):
                            continue  # acknowledged (or snoozed): quiet until the verdict changes
                        part.reason, part.signal, part.signal_id = (REASON_STILL_UNAVAILABLE, SIGNAL_REPOST,
                                                                    latest["signal_id"])
                    else:
                        part.reason, part.signal = REASON_VERDICT_CHANGED, SIGNAL_NEW
                elif current == VERDICT_EXIT_SIGNAL:
                    signal_id = self.ladder_signal(trade_id, group)
                    if signal_id is None:
                        continue
                    part.reason, part.signal, part.signal_id = REASON_LADDER, SIGNAL_REPOST, signal_id
                else:
                    continue
                parts.append(part)
            elif current == VERDICT_HOLD and changed and previous in POSTED_VERDICTS:
                notice = hold_notice(group_id, trade_id, self.group_words(group_id), previous, self.document,
                                     self.rulebook)
                parts.append(Part(notice, KIND_HOLD, REASON_VERDICT_CHANGED, group_id, trade_id, VERDICT_HOLD))
        parts.extend(self.plan_portfolio(portfolio_alert))
        return parts

    def plan_portfolio(self, alert: Optional[alerts.Alert]) -> List[Part]:
        sections = self.evaluation.sections
        fired = [(section, result) for section in sections for result in section.fired]
        newly = [result for section in sections for result in self.newly_fired(_section_subject(section), section.fired)]
        unreadable = any(section.unavailable for section in sections)
        current = VERDICT_WATCH if fired else (VERDICT_UNAVAILABLE if unreadable else VERDICT_HOLD)
        previous = self.recorded.get(PORTFOLIO_KEY)
        self.portfolio_row = {
            "verdict": current, "previous": previous, "fired": [result.rule_id for _section, result in fired],
            "unavailable": [result.rule_id for section in sections for result in section.unavailable],
            "no_history": [result.rule_id for section in sections for result in section.no_history],
            "pending": [result.rule_id for section in sections for result in section.pending],
            "cooldown": [result.rule_id for section in sections for result in section.cooldown],
            "new_signals": _ids(newly)}
        if current == VERDICT_UNAVAILABLE:
            # Never HOLD and nothing posted: the section's verdict of record stands while its rules cannot be read.
            if previous is not None:
                self.verdicts_now[PORTFOLIO_KEY] = previous
            return []
        self.verdicts_now[PORTFOLIO_KEY] = current
        changed = current != previous and not (previous is None and current == VERDICT_HOLD)
        if current == VERDICT_WATCH and alert is not None and (changed or newly):
            keys = [rule_state.state_key(_section_subject(section), result.rule_id) for section, result in fired]
            return [Part(alert, alert.kind, REASON_VERDICT_CHANGED if changed else REASON_NEW_RULE_FIRED, PORTFOLIO_KEY,
                         PORTFOLIO_KEY, current, SIGNAL_NEW, rule_keys=keys)]
        if current == VERDICT_HOLD and changed and previous in POSTED_VERDICTS:
            notice = hold_notice(PORTFOLIO_KEY, PORTFOLIO_KEY, alerts.PORTFOLIO_WORDS, previous, self.document,
                                 self.rulebook, portfolio=True)
            return [Part(notice, KIND_HOLD, REASON_VERDICT_CHANGED, PORTFOLIO_KEY, PORTFOLIO_KEY, VERDICT_HOLD)]
        return []

    def open_count(self, parts: Sequence[Part]) -> int:
        trades = {item.trade_id for item in self.open}
        trades.update(part.trade_id for part in parts if part.signal == SIGNAL_NEW and part.trade_id)
        return len(trades)

    def desk_parts(self, parts: List[Part]) -> Tuple[List[Part], List[Part]]:
        """``(leading, trailing)`` desk messages: CONFIG CHANGED and the digest first; the pre-close and watch-alive
        lines last."""
        leading: List[Part] = []
        self.changes = self.config_changes()
        if self.changes:
            alert = alerts.config_changed_text(self.changes, self.document, self.run_info)
            leading.append(Part(alert, alert.kind, alerts.KIND_CONFIG_CHANGED))
        self.digest = self.rulebook_change()
        if self.digest is not None:
            alert = alerts.rulebook_changed_text(self.digest.get("previous_version"), self.digest.get("changed"),
                                                 self.document, self.run_info)
            leading.append(Part(alert, alert.kind, alerts.KIND_RULEBOOK_CHANGED))
        trailing: List[Part] = []
        if self.args.summary:
            alert = alerts.pre_close_line(self.evaluation.verdict_counts(), self.open_count(parts), self.document,
                                          self.run_info)
            trailing.append(Part(alert, alert.kind, alerts.KIND_PRE_CLOSE))
        if self.args.watch_alive and not (leading or parts or trailing):
            since = self.session.open_utc  # today's open: before it, "nothing posted since the open" says nothing
            if since is not None and since <= self.now and not signals.posted_since(self.paths, since):
                alert = alerts.watch_alive_line(self.document, self.run_info)
                trailing.append(Part(alert, alert.kind, alerts.KIND_WATCH_ALIVE))
        return leading, trailing

    # ---- lint, persistence, output -------------------------------------------------------------

    def withhold(self, part: Part, result: alert_lint.LintResult) -> None:
        """Replace a part whose text failed the lint with the fixed withheld banner (module notes)."""
        codes = sorted({finding.code for finding in result.findings}) or ["a finding"]
        trade = part.trade_id if part.trade_id is not None else part.group_id
        record = part.alert.signal_record
        hold = part.kind == KIND_HOLD
        part.alert = alerts.withheld_text(alerts.KIND_WATCH if hold else part.kind, trade, codes, self.document,
                                          self.run_info, part.group_id, head="HOLD" if hold else None)
        if record is not None:  # the signal stands; its journaled reasons carry no unlinted text either
            sentence = alerts.WITHHELD_SENTENCE % ", ".join(codes)
            part.alert.signal_record = dict(record, reasons=[dict(item, text=sentence) for item in record["reasons"]])
        self.withheld.append({"group_id": part.group_id, "kind": part.kind, "codes": codes})
        self.warnings.append("the %s part for %s failed alert_lint (%s): its text was withheld and the withheld banner "
                             "posted in its place" % (part.kind, trade or "the desk", ", ".join(codes)))

    def lint(self, parts: Sequence[Part]) -> Tuple[Optional[alerts.Alert], alert_lint.LintResult]:
        if not parts:
            return None, alert_lint.LintResult(True, [])
        problems: List[str] = []
        checked = 0
        for part in parts:
            result = part.alert.lint()
            if not result.ok:
                self.withhold(part, result)
                result = part.alert.lint()
            checked += result.numbers_checked
            problems.extend("%s part: %s" % (part.kind, problem) for problem in _coded(result))
        composed = alerts.compose([part.alert for part in parts]) if len(parts) > 1 else parts[0].alert
        result = composed.lint()
        problems.extend("message: %s" % problem for problem in _coded(result))
        return composed, alert_lint.LintResult(not problems, problems, numbers_checked=checked + result.numbers_checked)

    def persist(self, parts: Sequence[Part]) -> None:
        for part in parts:
            if part.signal == SIGNAL_NEW:
                record = dict(part.alert.signal_record or {})
                record["signal_id"], record["posted_at"] = None, self.now
                stored = signals.append_signal(self.paths, record)
                part.signal_id = stored["signal_id"]
            signals.append_alert(self.paths, part.signal_id, part.alert.text, True, posted_at=self.now)
        if parts:
            self.written.extend([signals.alerts_path(self.paths), signals.day_journal_path(
                self.paths, signals.et_date_of(self.now))])
            if any(part.signal == SIGNAL_NEW for part in parts):
                self.written.append(signals.signals_path(self.paths))
        entries = self.new_entries
        for part in parts:
            for key in part.rule_keys:
                if key in entries:
                    entries = rule_state.record_post(entries, key, self.now)
        self.written.append(rule_state.save(self.paths, entries))
        with state.locked(self.paths.lock_path):
            document, status = state.read_json(self.paths.desk_state_json)
            document = dict(document) if status == state.STATUS_OK and isinstance(document, dict) else {}
            document[LAST_VERDICTS_KEY] = dict(self.verdicts_now)
            document["last_verdicts_utc"] = self.now
            if self.digest is not None and any(part.kind == alerts.KIND_RULEBOOK_CHANGED for part in parts):
                change = document.get(RULEBOOK_CHANGE_KEY)
                if isinstance(change, dict):
                    document[RULEBOOK_CHANGE_KEY] = dict(change, posted_utc=self.now)
            state.atomic_write_json(self.paths.desk_state_json, document)
        self.written.append(self.paths.desk_state_json)

    def write_heartbeat(self, exit_code: int) -> None:
        record_heartbeat(self.paths, self.args, self.version, self.now, exit_code, self.message)
        self.written.append(heartbeat.heartbeat_path(self.paths, self.args.routine))

    def payload(self, parts: Sequence[Part], lint: alert_lint.LintResult, exit_code: int, closed: bool,
                composed: Optional[alerts.Alert]) -> Dict[str, Any]:
        evaluation = getattr(self, "evaluation", None)
        return {
            "dry_run": not self.args.post, "posted": bool(self.args.post), "quiet": self.message is None and not closed,
            "closed_day": closed, "message": self.message, "parts": [part.as_dict() for part in parts],
            "lint": {"ok": lint.ok, "problems": list(lint.problems), "numbers_checked": lint.numbers_checked},
            "emitted_numbers": list(composed.emitted_numbers) if composed is not None else [],
            "emitted": composed.emitted_document() if composed is not None else {
                "emitted_numbers": [], "rule_ids": [], "identifiers": [], "clocks": [], "as_of": None},
            "withheld": list(self.withheld),
            "config_changed": list(getattr(self, "changes", [])),
            "rulebook": {"version": self.rulebook.rulebook_version, "sha256": self.rulebook.sha256,
                         "adopted_utc": self.adopted.adopted_utc},
            "verdicts": list(getattr(self, "verdict_rows", [])),
            "portfolio": getattr(self, "portfolio_row", None),
            "counts": evaluation.verdict_counts() if evaluation is not None else {
                verdict: 0 for verdict in exit_rules.GROUP_VERDICTS},
            "open_signals": self.open_count(parts) if evaluation is not None else 0,
            "signals": [{"signal_id": part.signal_id, "trade_id": part.trade_id, "verdict": part.verdict,
                         "kind": part.signal} for part in parts if part.signal is not None],
            "routine": self.args.routine,
            "heartbeat": (self.paths.relative(heartbeat.heartbeat_path(self.paths, self.args.routine))
                          if self.args.post else None),
            "written": [self.paths.relative(path) for path in self.written],
            "rule_state_status": getattr(self, "rule_state_status", None),
            "session": self.session.as_dict(), "evaluation_utc": self.now, "next_run_utc": self.args.next_run,
            "single_observation": evaluation.single_observation if evaluation is not None else None,
            "quote_age_min": self.quote_age, "run_lateness_min": self.lateness, "warnings": list(self.warnings),
            "exit_code": exit_code,
        }

    def emit(self, payload: Dict[str, Any], lines: List[str], exit_code: int) -> None:
        if self.args.json:
            status = contract.status_for(exit_code) if exit_code else (
                contract.STATUS_OK if self.message else contract.STATUS_QUIET)
            envelope = contract.envelope(TOOL, self.version, payload, source=quote_cache.source_label(self.market.cfg),
                                         delay_min=self.market.cfg.market_data.feed_delay_min, generated_at=self.now,
                                         status=status)
            print(contract.json_dumps(envelope))
        else:
            print("\n".join(([DRY_RUN_LINE] if not self.args.post else []) + lines))

    def run_check(self) -> int:
        self.load()
        if not self.session.trading_day:
            name = self.session.holiday_name or (
                "weekend" if self.session.session == market_clock.SESSION_WEEKEND else "closed")
            if self.args.post:
                self.write_heartbeat(contract.EXIT_QUIET)
            self.emit(self.payload([], alert_lint.LintResult(True, []), contract.EXIT_QUIET, True, None),
                      [CLOSED_DAY_LINE % name], contract.EXIT_QUIET)
            return contract.EXIT_QUIET
        lock = state.locked(run_lock_path(self.paths), timeout_s=RUN_LOCK_TIMEOUT_S) if self.args.post else _no_lock()
        with lock:
            self.evaluate()
            groups = self.plan()
            leading, trailing = self.desk_parts(groups)
            parts = leading + groups + trailing
            composed, lint = self.lint(parts)
            if not lint.ok:
                if self.args.post and composed is not None:
                    signals.append_alert(self.paths, None, composed.text, False, posted_at=self.now)
                raise Failure("lint_failed", "the message failed alert_lint and was not posted: %s"
                              % "; ".join(lint.problems))
            self.message = composed.text if composed is not None else None
            if any(part.kind == alerts.KIND_EXIT_SIGNAL for part in parts):
                exit_code = contract.EXIT_FIRED
            elif any(group.verdict == VERDICT_UNAVAILABLE for group in self.evaluation.groups):
                exit_code = contract.EXIT_UNAVAILABLE
            else:
                exit_code = contract.EXIT_QUIET
            if self.args.post:
                self.persist(parts)
                self.write_heartbeat(exit_code)
        self.emit(self.payload(parts, lint, exit_code, False, composed),
                  self.message.splitlines() if self.message is not None else [QUIET_LINE], exit_code)
        return exit_code


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
        return fail(args, version, now, "desk_root", str(exc), contract.EXIT_ERROR)
    try:
        try:
            market = quote_cache.open_market_data(args.desk_root, now=now, offline=args.offline,
                                                  fixture_set=args.fixture_set, timeout_s=args.timeout,
                                                  user_agent=user_agent(version))
        except desk_paths.DeskPathsError as exc:
            raise Failure("desk_root", str(exc)) from None
        except desk_config.DeskConfigError as exc:
            raise Failure("config_invalid", str(exc)) from None
        except market_clock.TableExpiredError as exc:
            raise Failure("holiday_table_expired", "holiday table expired: %s" % exc, contract.EXIT_UNAVAILABLE) from None
        except quote_cache.MarketDataRefused as exc:
            raise Failure(exc.status, exc.message, contract.EXIT_UNAVAILABLE) from None
        except OSError as exc:
            raise Failure("io_error", "cannot prepare the desk runtime directories: %s" % exc) from None
        return Check(args, version, now, market).run_check()
    except Failure as exc:
        failure = exc
    except state.LockBusy as exc:
        failure = Failure("lock_busy", str(exc))
    except (OSError, signals.SignalJournalError) as exc:
        failure = Failure("io_error", "cannot read or write under the desk root: %s" % exc)
    if args.post:
        try:
            record_heartbeat(paths, args, version, now, failure.exit_code, None)
        except (OSError, heartbeat.HeartbeatError):
            pass  # the failure being reported is what matters; a heartbeat that cannot be written adds nothing
    return fail(args, version, now, failure.code, failure.message, failure.exit_code)


if __name__ == "__main__":
    sys.exit(main())
