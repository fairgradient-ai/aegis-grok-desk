#!/usr/bin/env python3
"""routine.py: the single reviewed routine entry point: one manifest routine, its steps in-process, one linted message.

Usage::

    python3 scripts/routine.py ROUTINE [--slot HH:MM] [--allow-dirty] [--fixture-set NAME] [--json] [--schema]
        [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

Every Grok routine runs exactly this command (``routines/manifest.toml``'s ``prompt``, with ``--slot`` filled in) and
posts what it prints, verbatim. ``ROUTINE`` is a routine id of the manifest (``aegis_desk.routines``); an unknown id,
a ``--slot`` the routine does not have, ``--fixture-set`` without ``--offline`` and a desk root that is the desk
checkout or lies inside it are usage errors (exit 2). Without ``--desk-root`` the desk root is
:data:`DEFAULT_DESK_ROOT` (``/workspace/aegis-desk``), never the current directory: runtime files written into the
checkout would make it dirty, and the next run would refuse to start.

The run, under the routine's own lock ``state/.routine.<id>.lock`` (never ``state/.lock``, which the steps take
themselves; busy after :data:`LOCK_TIMEOUT_S` seconds is exit 1 ``INCIDENT | <id> | another run of this routine holds
its lock; this run did nothing`` and nothing is written):

1. The pin check: ``check_grok_template.checkout_state(<this checkout>, <plugin.json version>, run=CHECKOUT_RUN)``
   (:data:`CHECKOUT_RUN` is the test seam; ``None`` runs git's four literal read-only commands). A checkout that is
   not the clean commit of tag ``v<version>`` runs no step: exit 20 with ``INCIDENT | <id> | the desk checkout is not
   the pinned release v<version>: <reason>; no step ran | details in incidents/<stamp>-dirty-checkout.md``.
   ``--allow-dirty`` (development only; no manifest prompt carries it) runs anyway: the message then starts with
   ``development run: the checkout is not verified (--allow-dirty)`` (a quiet run prints that line, then ``quiet``)
   and the run record logs it with the checkout's state. The step tools are imported only once the pin check passed
   (or ``--allow-dirty`` was given), by :func:`step_modules`: a modified step module's code never runs on a refused
   checkout. Run as a script, this tool neither writes bytecode into the checkout nor reads any cached there
   (``sys.dont_write_bytecode``, ``sys.pycache_prefix`` pointed nowhere), so what runs is the checkout's source, the
   bytes the pin check verified, and the release checkout stays exactly as cloned.
2. The slot: ``--slot``, else the latest slot at or before the run instant in America/New_York over the union of
   the routine's profiles (never the nearest: a 10:05 run that starts 31 minutes late is still the 10:05 run), else
   the day's first slot. The chain is the slot's ``slot_chain`` entry, else ``chain``.
3. Closed days: a routine whose ``days`` exclude the ET weekday, or a ``trading_days_only`` routine on an NYSE holiday
   or an owner-injected full closure of ``research/calendar.toml``, runs no step and prints ``quiet``. So does
   ``final-call`` (:data:`EARLY_CLOSE_QUIET_ROUTINES`) on an early-close day: the 07:30 Morning Desk Sheet carries
   that day's early-close line (addendum O5).
4. The steps, in chain order and each exactly once, in-process: ``STEP_MODULES[tool].main(argv)`` (literal imports,
   no computed import) with stdout and stderr captured, ``SystemExit`` mapped to its code, any other exception to
   exit 1 ``step_crashed`` and stdout that is not one JSON envelope to exit 1 ``bad_envelope``. ``argv`` is the step's
   own tokens, then ``--json --desk-root <root> --timeout <s>``, ``--now`` and ``--offline`` when the routine got
   them, ``--fixture-set`` for the tools that declare it (``routines.FIXTURE_SET_TOOLS``), and for ``book_check``
   ``--routine <id> --scheduled-et <slot> --next-run-utc <ISO>``: the first slot of the routine (union of profiles)
   later than both the run instant plus five minutes and the slot itself, today or on a later day of its ``days``
   that is a trading day. Steps never short-circuit. Each step's envelope is copied to
   ``reports/routines/<ET date>/steps/<slug>.json`` (the tool and its flags joined with ``-``: ``csv_recon``,
   ``expiry_checklist-week``, ``book_check-post-summary``; the latest run of a step on an ET date replaces it).

The message (PR 2d decision D2-3)
---------------------------------
``post`` names the tools whose text may be posted and ``posts`` the policy: ``always`` posts the text of every post
step, ``transitions`` posts ``book_check``'s message whenever it is non-null (``book_check`` applied the posting rules
itself) and ``fired`` posts the text of each post step that exited 10 or 20 (none: the run is quiet). A step's text is
its ``payload.message``; an exit-20 error envelope of a post step is the part ``UNAVAILABLE: <its message>``, the line
the tool prints in prose. In order: the development header; then, for ``always`` and ``fired``, one line per failed
step, ``INCIDENT | <id> <slot> ET | <tool> failed (<error code>); the other steps ran; details in incidents/<file>``
(for ``transitions`` only a failed ``book_check`` gets that line: a failed ``trigger_levels`` refresh at 10:05 is an
incident file only, addendum O8); then each part in chain order. A strict part (``payload.emitted``: the
``Alert.emitted_document()`` shape) is linted against its own document as printed, its footer line dropped and its
numbers, rule ids, identifiers and clocks merged (first occurrence wins). A text part (``payload.message`` and
``payload.as_of``) goes through ``alerts.fmt_reason`` and is linted with a footer at its ``as_of``. A part that fails
its own lint is replaced by ``WITHHELD | <tool>: the text failed the alert lint (<codes>) and is withheld; <tool>.py
shows it`` and recorded in an incident file (never its text); its step's exit code stands. The header and every
INCIDENT line go through ``alerts.fmt_reason`` too. One footer ends the message (``alerts.fmt_footer`` at the newest
part's as-of, or the run instant when there is no part, with that part's envelope delay). The whole message is then
linted (``alert_lint.check``, strict); when it fails, only ``INCIDENT | <id> | the routine message failed the alert
lint (<codes>) and is withheld`` and the footer are printed and the exit code is 1. No part and no INCIDENT line is
``quiet`` (the exit code still follows the steps: ``book_check`` prints ``quiet`` and exits 20 once each UNAVAILABLE
banner is acknowledged). Human mode prints only the final text; ``--json`` prints only the envelope (``payload.emitted`` is the
strict emitted document of the printed text).

Exit codes (decision D2-5): 2 usage; 20 the pin refusal; otherwise 1 when a counted step exited 1 or 2, crashed or
printed no envelope, when the routine lock is busy or when the message failed the lint (also ``manifest_invalid``,
``desk_root`` and ``io_error`` before the lock); else 10 when a posted step exited 10; else 20 when a counted step
exited 20; else 0. Every step counts, except that under ``transitions`` only the post step decides (O8). A doctor FAIL
in preopen is 20; an EDITS_PENDING ``csv_recon`` there (10) is not posted and does not make preopen 10.

Heartbeat, journal and records (decision D2-4)
----------------------------------------------
A ``book_check --post`` step writes the routine's heartbeat (``state/heartbeat/<id>.json`` and ``history.jsonl``,
with ``--scheduled-et`` the slot) and journals every posted part itself, so this tool writes neither for it: a second
heartbeat per run would speed up the re-post ladder. Whenever no step wrote this routine's heartbeat (every other
routine; a book-check run with no step, or whose ``book_check`` stopped before its heartbeat), this tool writes it
(``heartbeat.write_heartbeat``: the exit code, ``ok`` or ``quiet``, the hash of the printed text, the slot) and
journals a non-quiet message (``signals.append_alert``). Incident files are ``incidents/<stamp>-<id>-<slug>.md``
(``-withheld`` and ``-message`` for the lint, ``<stamp>-dirty-checkout.md`` for the pin; ``stamp`` is
``state.quarantine_stamp`` of the run instant), written atomically. The run record
``reports/routines/<ET date>/<id>-<HHMM of the slot>.json`` (one per slot, so the 10:05 development run's record
survives the 12:05 run) = ``{schema 1, routine, slot, run_utc, development, checkout {pinned, reason}, steps [{tool,
argv, exit_code, status, error_code, artifact}], withheld, incidents, message_sha256, exit_code}`` is written at the
end of every run that held the lock. An unexpected error during the run is caught: ``incidents/<stamp>-<id>-crash.md``
holds its traceback, the routine's heartbeat records exit 1, and the linted notice ``INCIDENT | <id> | the routine did
not run (routine_crashed): ...`` (``io_error`` for an operating-system error) is printed, exit 1. A notice names the
routine only by a manifest id (``routine`` otherwise) and falls back to a fixed line when even its fallback fails the
lint. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    # Neither write bytecode into the release checkout nor read any cached there: the source runs, the bytes the pin
    # check verifies (a planted .pyc is never loaded; the prefix names a path that can hold no file).
    sys.dont_write_bytecode = True
    sys.pycache_prefix = os.path.join(os.devnull, "aegis-desk-no-bytecode")

from aegis_desk import contract  # noqa: E402

contract.python_floor_check(tool="routine")

import contextlib  # noqa: E402
import datetime  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import traceback  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

from aegis_desk import (  # noqa: E402
    alert_lint,
    alerts,
    calendar_events,
    desk_paths,
    heartbeat,
    market_clock,
    routines,
    signals,
    state,
    version,
)
from aegis_desk.version import release_version  # noqa: E402

import check_grok_template  # noqa: E402  (the release pin: checkout_state is a safe export of the gate)

TOOL = "routine"
DESCRIPTION = ("the single routine entry point: checks the release pin, runs one manifest routine's steps in-process "
               "with JSON captured, composes and lints the one message the Bot posts verbatim, and records the "
               "heartbeat, the journal, the step copies and the run record")
NETWORK_ACCESS = ("GET https://sandbox.tradier.com/v1/markets/... only, through the step tools' quote cache; none "
                  "with --offline")
STEP_MODULES: Dict[str, Any] = {}
"""The step tools by name, filled by :func:`step_modules` (``set(step_modules()) == set(routines.STEP_TOOLS)``)."""
DEFAULT_DESK_ROOT = "/workspace/aegis-desk"
DESK_ROOT_HELP = ("desk directory (desk.toml, positions.csv, state/); default: %s, never the current directory; a desk "
                  "root inside this checkout, or the checkout itself, is refused. --offline runs and fixture runs must "
                  "pass it explicitly" % DEFAULT_DESK_ROOT)
CHECKOUT_RUN: Optional[Callable[[str, List[str]], Any]] = None
"""The ``run`` seam of ``check_grok_template.checkout_state``; ``None`` is git itself."""
LOCK_FILE = ".routine.%s.lock"
LOCK_TIMEOUT_S = 5.0
RUN_RECORD_SCHEMA = 1
RUNS_SUBDIR = "routines"
STEPS_SUBDIR = "steps"
NEXT_RUN_LEAD_MIN = 5
NEXT_RUN_SEARCH_DAYS = 14
EARLY_CLOSE_QUIET_ROUTINES: Tuple[str, ...] = ("final-call",)
"""Routines that run no step on an early-close day (addendum O5: the Morning Desk Sheet carries the early close)."""
POSTED_EXIT_CODES: Tuple[int, ...] = (contract.EXIT_FIRED, contract.EXIT_UNAVAILABLE)
COUNTED_EXIT_CODES: Tuple[int, ...] = (contract.EXIT_QUIET, contract.EXIT_FIRED, contract.EXIT_UNAVAILABLE)
POLICY_ALWAYS = "always"
POLICY_TRANSITIONS = "transitions"
POLICY_FIRED = "fired"
STEP_CRASHED = "step_crashed"
BAD_ENVELOPE = "bad_envelope"
KIND_STRICT = "strict"
KIND_TEXT = "text"
KIND_UNAVAILABLE = "unavailable"
QUIET_LINE = "quiet"
DEVELOPMENT_LINE = "development run: the checkout is not verified (--allow-dirty)"
STEP_FAILED_LINE = "INCIDENT | %s %s ET | %s failed (%s); the other steps ran; details in %s"
WITHHELD_LINE = "WITHHELD | %s: the text failed the alert lint (%s) and is withheld; %s.py shows it"
MESSAGE_WITHHELD_LINE = "INCIDENT | %s | the routine message failed the alert lint (%s) and is withheld"
DIRTY_LINE = ("INCIDENT | %s | the desk checkout is not the pinned release v%s: %s; no step ran | details in %s")
LOCK_BUSY_LINE = "INCIDENT | %s | another run of this routine holds its lock; this run did nothing"
NOT_RUN_LINE = "INCIDENT | %s | the routine did not run (%s): %s"
NOTICE_WITHHELD_LINE = "INCIDENT | %s | the routine's notice failed the alert lint (%s); routine.py --json shows it"
NOTICE_FIXED_LINE = "INCIDENT | routine | a notice failed the alert lint; routine.py --json shows it"
"""The last resort when even the fallback notice fails its lint: nothing interpolated."""
ROUTINE_CRASHED = "routine_crashed"
CRASH_MESSAGE = "an unexpected error stopped the run; details in %s"
HISTORY_WARNING = "%s cannot be read (%s): this run's heartbeat is routine.py's"
UNAVAILABLE_LINE = "UNAVAILABLE: %s"
EMITTED_INVALID = "emitted_document_invalid"
RUN_RECORD_KEYS: Tuple[str, ...] = ("schema", "routine", "slot", "run_utc", "development", "checkout", "steps",
                                    "withheld", "incidents", "message_sha256", "exit_code")
STEP_RECORD_KEYS: Tuple[str, ...] = ("tool", "argv", "exit_code", "status", "error_code", "artifact")
PAYLOAD_KEYS: Tuple[str, ...] = ("routine", "slot", "et_date", "run_utc", "lateness_min", "next_run_utc",
                                 "development", "checkout", "closed_day", "early_close", "quiet", "message", "parts",
                                 "steps", "withheld", "incidents", "lint", "emitted", "heartbeat", "run_record",
                                 "written", "warnings", "message_sha256", "exit_code")
STEP_OUTPUT: Dict[str, Any] = {
    "tool": " | ".join(routines.STEP_TOOLS), "step": "the step's slug (tool and flags joined with -)",
    "argv": ["the exact argv main() received"], "exit_code": "0 | 1 | 2 | 10 | 20",
    "status": "the envelope status | error (crashed, no envelope)",
    "error_code": "the error envelope's code | step_crashed | bad_envelope | exit_<n> | null",
    "artifact": "reports/routines/<ET date>/steps/<slug>.json | null (not written)",
}
OUTPUT: Dict[str, Any] = {
    "routine": "the manifest routine id",
    "slot": "HH:MM (America/New_York)",
    "et_date": "YYYY-MM-DD (America/New_York date of the run instant)",
    "run_utc": "ISO-8601 UTC (--now or the clock read once)",
    "lateness_min": "integer (heartbeat.lateness of the slot; 0 before it)",
    "next_run_utc": "ISO-8601 UTC | null: the --next-run-utc book_check received (null without a book_check step)",
    "development": "boolean (--allow-dirty)",
    "checkout": {"pinned": "boolean", "reason": "check_grok_template.checkout_state's reason"},
    "closed_day": "boolean (no step: the routine does not run on this ET date)",
    "early_close": "boolean (no step: final-call on an early-close day)",
    "quiet": "boolean (nothing to post: the output is the word quiet)",
    "message": "string | null: the text the Bot posts, exactly as printed in prose",
    "parts": [{"tool": "string", "step": "slug", "kind": "%s | %s | %s" % (KIND_STRICT, KIND_TEXT, KIND_UNAVAILABLE),
               "exit_code": "the step's", "withheld": "boolean"}],
    "steps": [STEP_OUTPUT],
    "withheld": [{"tool": "string", "step": "slug", "codes": ["alert_lint finding codes"]}],
    "incidents": ["incidents/<stamp>-... .md written by this run"],
    "lint": {"ok": "boolean (the composed message's lint)", "problems": ["string"], "numbers_checked": "integer"},
    "emitted": {"emitted_numbers": ["string"], "rule_ids": ["string"], "identifiers": ["string"],
                "clocks": ["HH:MM UTC | HH:MM ET"], "as_of": "ISO-8601 UTC | null",
                "note": "the strict emitted document of the printed message (alert_lint.py --emitted-file)"},
    "heartbeat": "state/heartbeat/<routine>.json (whoever wrote it) | null",
    "run_record": "reports/routines/<ET date>/<routine>-<HHMM of the slot>.json | null (not written)",
    "written": ["desk-root relative paths this tool wrote (the steps' own writes are in their copies)"],
    "warnings": ["string"],
    "message_sha256": "sha256 hex of the printed message | null (quiet)",
    "exit_code": "0 | 1 | 10 | 20",
}
UTC = datetime.timezone.utc


# --------------------------------------------------------------------------------------------
# Paths, slugs and command lines
# --------------------------------------------------------------------------------------------


def lock_path(paths: desk_paths.DeskPaths, routine_id: str) -> str:
    """``state/.routine.<id>.lock``: held around the whole run (never ``state/.lock``: ``state.locked`` is not
    re-entrant and the steps take that one themselves)."""
    return os.path.join(paths.state_dir, LOCK_FILE % routine_id)


def runs_dir(paths: desk_paths.DeskPaths, et_date: datetime.date) -> str:
    return os.path.join(paths.reports_dir, RUNS_SUBDIR, et_date.isoformat())


def run_record_path(paths: desk_paths.DeskPaths, et_date: datetime.date, routine_id: str, slot: str) -> str:
    """``reports/routines/<ET date>/<id>-<HHMM>.json``: one record per slot."""
    return os.path.join(runs_dir(paths, et_date), "%s-%s.json" % (routine_id, slot.replace(":", "")))


def step_modules() -> Dict[str, Any]:
    """The step tools by name, imported here (literal import statements, never a computed import) only when a run is
    about to execute its steps: after the pin check passed or ``--allow-dirty`` was given (module notes, step 1)."""
    if not STEP_MODULES:
        import assignment_watch
        import book
        import book_check
        import csv_recon
        import desk_doctor
        import eod_summary
        import expiry_checklist
        import trigger_levels

        STEP_MODULES.update({
            "assignment_watch": assignment_watch, "book": book, "book_check": book_check, "csv_recon": csv_recon,
            "desk_doctor": desk_doctor, "eod_summary": eod_summary, "expiry_checklist": expiry_checklist,
            "trigger_levels": trigger_levels,
        })
    return STEP_MODULES


def step_artifact_path(paths: desk_paths.DeskPaths, et_date: datetime.date, slug: str) -> str:
    """``reports/routines/<ET date>/steps/<slug>.json``."""
    return os.path.join(runs_dir(paths, et_date), STEPS_SUBDIR, "%s.json" % slug)


def step_slug(step: str) -> str:
    """The tool and its flag tokens joined with ``-``, leading dashes stripped (``book_check-post-summary``)."""
    tokens = routines.step_tokens(step)
    return "-".join([tokens[0]] + [token.lstrip("-") for token in tokens[1:]])


def step_argv(step: str, *, desk_root: str, timeout: float, now: Optional[datetime.datetime], offline: bool,
              fixture_set: Optional[str], routine_id: str, slot: str,
              next_run: Optional[datetime.datetime]) -> List[str]:
    """The ``argv`` a step's ``main`` receives (module notes, step 4)."""
    tokens = routines.step_tokens(step)
    tool = tokens[0]
    argv = list(tokens[1:]) + ["--json", "--desk-root", desk_root, "--timeout", str(timeout)]
    if now is not None:
        argv += ["--now", contract.format_utc(now)]
    if offline:
        argv.append("--offline")
    if fixture_set is not None and tool in routines.FIXTURE_SET_TOOLS and not any(
            token in routines.NO_FIXTURE_SET_FLAGS for token in tokens):
        argv += ["--fixture-set", fixture_set]
    if tool == routines.BOOK_CHECK_TOOL:
        argv += ["--routine", routine_id, "--scheduled-et", slot]
        if next_run is not None:
            argv += ["--next-run-utc", contract.format_utc(next_run)]
    return argv


def resolve_slot(routine: routines.Routine, now: datetime.datetime) -> str:
    """The latest slot at or before ``now`` (America/New_York) over the union of the routine's profiles; the day's
    first slot when none has passed yet. Never the nearest slot."""
    slots = routine.slots()
    local = market_clock.to_et(now)
    minute = local.hour * 60 + local.minute
    passed = [slot for slot in slots if routines.slot_minutes(slot) <= minute]
    return passed[-1] if passed else slots[0]


def next_run_utc(routine: routines.Routine, et_date: datetime.date, slot: str, now: datetime.datetime,
                 extra_holidays: market_clock.ExtraHolidays = ()) -> Optional[datetime.datetime]:
    """The routine's next run for ``book_check --next-run-utc``: the first slot (union of profiles) later than both
    ``now`` plus :data:`NEXT_RUN_LEAD_MIN` minutes and the current slot's instant, searched today, then on the
    following days of the routine's ``days`` that are trading days (``extra_holidays``: the calendar's full
    closures). ``None`` only when no such day falls within :data:`NEXT_RUN_SEARCH_DAYS` days."""
    floor = max(now + datetime.timedelta(minutes=NEXT_RUN_LEAD_MIN), routines.slot_instant(et_date, slot))
    for offset in range(NEXT_RUN_SEARCH_DAYS + 1):
        day = et_date + datetime.timedelta(days=offset)
        if offset and (routines.DAYS[day.weekday()] not in routine.days
                       or not market_clock.is_trading_day(day, extra_holidays)):
            continue
        for candidate in routine.slots():
            moment = routines.slot_instant(day, candidate)
            if moment > floor:
                return moment
    return None


def _inside(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or lies inside it, as written or once symbolic links are resolved."""
    for candidate in {os.path.normpath(os.path.abspath(path)), os.path.realpath(path)}:
        for base in {os.path.normpath(os.path.abspath(root)), os.path.realpath(root)}:
            if os.path.commonpath([candidate, base]) == base:
                return True
    return False


def _exit_code(value: Any) -> int:
    """``SystemExit.code`` (or a returned value) as an exit code: an int as is, ``None`` 0, anything else 1."""
    if value is None:
        return contract.EXIT_QUIET
    if isinstance(value, bool) or not isinstance(value, int):
        return contract.EXIT_ERROR
    return value


def run_step(module: Any, argv: Sequence[str]) -> Tuple[int, str, str, Optional[str]]:
    """One in-process run of a step tool's ``main(argv)``: ``(exit code, stdout, stderr, traceback or None)``.
    ``SystemExit`` gives its code; any other exception gives exit 1 and its traceback (the step's bug is an incident
    of the run, never the routine's crash)."""
    out, err = io.StringIO(), io.StringIO()
    crash: Optional[str] = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _exit_code(module.main(list(argv)))
    except SystemExit as exc:
        code = _exit_code(exc.code)
    except Exception:  # noqa: BLE001 - every step failure is recorded, the other steps still run
        code, crash = contract.EXIT_ERROR, traceback.format_exc()
    return code, out.getvalue(), err.getvalue(), crash


def _coded(result: alert_lint.LintResult) -> List[str]:
    if result.findings:
        return ["%s: %s" % (finding.code, finding.message) for finding in result.findings]
    return list(result.problems)


def _codes(result: alert_lint.LintResult) -> List[str]:
    return sorted({finding.code for finding in result.findings}) or [alert_lint.CODE_EMPTY]


def _instant(value: Any) -> Optional[datetime.datetime]:
    if not isinstance(value, str):
        return None
    try:
        return contract.parse_utc_instant(value, "as_of")
    except ValueError:
        return None


def _delay(envelope: Optional[Mapping[str, Any]]) -> int:
    value = envelope.get("delay_min") if isinstance(envelope, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return alert_lint.FOOTER_DELAY_MIN
    return value


def _add_unique(into: List[str], items: Optional[Sequence[str]]) -> None:
    for item in items or []:
        if item not in into:
            into.append(item)


# --------------------------------------------------------------------------------------------
# Steps, parts and the message
# --------------------------------------------------------------------------------------------


@dataclass
class StepRun:
    """One step's run: its command line, exit code, envelope (``None``: crashed or no JSON) and copy."""

    step: str
    tool: str
    slug: str
    argv: List[str]
    exit_code: int
    envelope: Optional[Dict[str, Any]]
    stderr: str
    crash: Optional[str]
    status: str
    error_code: Optional[str]
    failed: bool
    artifact: Optional[str] = None

    def record(self) -> Dict[str, Any]:
        return {"tool": self.tool, "argv": list(self.argv), "exit_code": self.exit_code, "status": self.status,
                "error_code": self.error_code, "artifact": self.artifact}

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.record(), step=self.slug)


@dataclass
class Part:
    """One posted part: its body lines (footer dropped) and the numbers, ids and clocks they carry."""

    step: StepRun
    kind: str
    body: str
    as_of: datetime.datetime
    delay_min: int
    emitted_numbers: List[str] = field(default_factory=list)
    rule_ids: List[str] = field(default_factory=list)
    identifiers: List[str] = field(default_factory=list)
    clocks: List[str] = field(default_factory=list)
    withheld: Optional[List[str]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"tool": self.step.tool, "step": self.step.slug, "kind": self.kind, "exit_code": self.step.exit_code,
                "withheld": self.withheld is not None}


class Message:
    """The lines of one message and everything its formatters emitted (numbers, rule ids, identifiers, clocks)."""

    def __init__(self) -> None:
        self.lines: List[str] = []
        self.emitted: List[str] = []
        self.rule_ids: List[str] = []
        self.identifiers: List[str] = []
        self.clocks: List[str] = []

    def add_line(self, text: str) -> None:
        """A sentence the desk wrote (the header, an INCIDENT, WITHHELD or UNAVAILABLE line)."""
        self.lines.append(alerts.fmt_reason(text, self.emitted, self.rule_ids, self.identifiers, self.clocks))

    def add_part(self, part: Part) -> None:
        if part.withheld is not None:
            self.add_line(part.body)
            return
        self.lines.append(part.body)
        _add_unique(self.emitted, part.emitted_numbers)
        _add_unique(self.rule_ids, part.rule_ids)
        _add_unique(self.identifiers, part.identifiers)
        _add_unique(self.clocks, part.clocks)

    def finish(self, as_of: datetime.datetime, delay_min: int) -> Tuple[str, alert_lint.LintResult, Dict[str, Any]]:
        """The text with its one footer, the strict lint of it and its emitted document."""
        footer = alerts.fmt_footer(as_of, delay_min, self.emitted, self.clocks)
        text = "\n".join(self.lines + [footer])
        rule_ids = list(self.rule_ids) or None
        result = alert_lint.check(text, self.emitted, rule_ids=rule_ids, identifiers=self.identifiers,
                                  clocks=self.clocks, as_of=as_of)
        document = {"emitted_numbers": list(self.emitted), "rule_ids": list(self.rule_ids),
                    "identifiers": list(self.identifiers), "clocks": list(self.clocks),
                    "as_of": as_of.astimezone(UTC).isoformat()}
        return text, result, document


def notice(line: str, routine_id: str, as_of: datetime.datetime) -> Tuple[str, alert_lint.LintResult, Dict[str, Any]]:
    """A one-line desk notice with the footer, linted; the fixed fallback line when the notice itself fails."""
    message = Message()
    message.add_line(line)
    text, result, document = message.finish(as_of, alert_lint.FOOTER_DELAY_MIN)
    if result.ok:
        return text, result, document
    fallback = Message()
    fallback.add_line(NOTICE_WITHHELD_LINE % (routine_id, ", ".join(_codes(result))))
    text, result, document = fallback.finish(as_of, alert_lint.FOOTER_DELAY_MIN)
    if result.ok:
        return text, result, document
    fixed = Message()
    fixed.add_line(NOTICE_FIXED_LINE)
    return fixed.finish(as_of, alert_lint.FOOTER_DELAY_MIN)


EMPTY_EMITTED: Dict[str, Any] = {"emitted_numbers": [], "rule_ids": [], "identifiers": [], "clocks": [], "as_of": None}


# --------------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------------


class Run:
    """One run of one routine, under its lock (module notes)."""

    def __init__(self, args: Any, release: str, routine: routines.Routine, paths: desk_paths.DeskPaths,
                 start: datetime.datetime) -> None:
        self.args = args
        self.release = release
        self.routine = routine
        self.paths = paths
        self.start = start
        self.et_date = market_clock.to_et(start).date()
        self.slot = args.slot if args.slot is not None else resolve_slot(routine, start)
        self.lateness, _reason = heartbeat.lateness(self.slot, start)
        self.stamp = state.quarantine_stamp(start)
        self.warnings: List[str] = []
        self.written: List[str] = []
        self.incidents: List[str] = []
        self.withheld: List[Dict[str, Any]] = []
        self.steps: List[StepRun] = []
        self.parts: List[Part] = []
        self.next_run: Optional[datetime.datetime] = None
        self.history_before: Optional[int] = None
        self.checkout: Dict[str, Any] = {"pinned": False, "reason": ""}
        self.closed_day = False
        self.early_close = False
        self.newest: Optional[Part] = None

    # ---- inputs ---------------------------------------------------------------------------------

    def extra_closures(self) -> Dict[datetime.date, str]:
        try:
            cal = calendar_events.load_calendar_if_present(self.paths.calendar_toml)
        except calendar_events.CalendarError as exc:
            self.warnings.append("research/calendar.toml cannot be used (%s): the NYSE table alone decides the "
                                 "trading days of this run" % exc.reason)
            return {}
        return calendar_events.extra_closures(cal)

    def pin(self) -> Dict[str, Any]:
        try:
            found = check_grok_template.checkout_state(version.repository_root(), self.release, run=CHECKOUT_RUN)
        except ValueError as exc:
            return {"pinned": False, "reason": "the release cannot be checked: %s" % exc}
        return {"pinned": bool(found.get("pinned")), "reason": str(found.get("reason"))}

    def history_count(self) -> Optional[int]:
        """This routine's records in ``state/heartbeat/history.jsonl``; ``None`` (with a warning) when it cannot be
        read: then no step's heartbeat can be told apart, and this tool writes the routine's own."""
        path = heartbeat.history_path(self.paths)
        try:
            records, _status, _bad = signals.read_journal(path)
        except OSError as exc:
            warning = HISTORY_WARNING % (self.paths.relative(path), exc.strerror or exc.__class__.__name__)
            if warning not in self.warnings:
                self.warnings.append(warning)
            return None
        return sum(1 for record in records if isinstance(record, dict) and record.get("routine_id") == self.routine.id)

    # ---- writes ---------------------------------------------------------------------------------

    def write_json(self, path: str, document: Any) -> Optional[str]:
        try:
            state.atomic_write_json(path, document)
        except OSError as exc:
            self.warnings.append("%s not written: %s" % (self.paths.relative(path), exc.strerror or exc))
            return None
        rel = self.paths.relative(path)
        self.written.append(rel)
        return rel

    def write_incident(self, name: str, title: str, lines: Sequence[str]) -> str:
        path = os.path.join(self.paths.incidents_dir, "%s-%s.md" % (self.stamp, name))
        rel = self.paths.relative(path)
        body = ["# Incident %s: %s" % (self.stamp, title), "",
                "- routine: %s" % self.routine.id, "- slot: %s ET" % self.slot,
                "- run_utc: %s" % state.format_utc(self.start), "- release: %s" % self.release] + list(lines)
        try:
            state.atomic_write_text(path, "\n".join(body) + "\n")
        except OSError as exc:
            self.warnings.append("%s not written: %s" % (rel, exc.strerror or exc))
        else:
            self.written.append(rel)
        self.incidents.append(rel)
        return rel

    # ---- steps ----------------------------------------------------------------------------------

    def run_one(self, step: str) -> StepRun:
        tool = routines.step_tool(step)
        slug = step_slug(step)
        argv = step_argv(step, desk_root=self.paths.root, timeout=self.args.timeout, now=self.args.now,
                         offline=self.args.offline, fixture_set=self.args.fixture_set, routine_id=self.routine.id,
                         slot=self.slot, next_run=self.next_run)
        code, stdout, stderr, crash = run_step(step_modules()[tool], argv)
        envelope: Optional[Dict[str, Any]] = None
        if crash is None:
            try:
                parsed = json.loads(stdout)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and isinstance(parsed.get("status"), str):
                envelope = parsed
        error_code: Optional[str] = None
        status = contract.STATUS_ERROR
        if crash is not None:
            error_code = STEP_CRASHED
        elif envelope is None:
            code, error_code = contract.EXIT_ERROR, BAD_ENVELOPE
        else:
            status = envelope["status"]
            error = envelope.get("error")
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                error_code = error["code"]
        failed = envelope is None or code not in COUNTED_EXIT_CODES
        if failed and error_code is None:
            error_code = "exit_%d" % code
        run = StepRun(step, tool, slug, argv, code, envelope, stderr, crash, status, error_code, failed)
        copy = envelope
        if copy is None:
            what = "the step raised an exception" if crash is not None else "the step printed no JSON envelope"
            copy = contract.error_envelope(tool, self.release, error_code or BAD_ENVELOPE, what, contract.STATUS_ERROR,
                                           generated_at=self.start)
        run.artifact = self.write_json(step_artifact_path(self.paths, self.et_date, slug), copy)
        return run

    def step_incident(self, run: StepRun) -> str:
        error = run.envelope.get("error") if isinstance(run.envelope, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        lines = ["- step: %s" % run.step, "- argv: %s" % " ".join(run.argv), "- exit code: %d" % run.exit_code,
                 "- error code: %s" % run.error_code, "- error message: %s" % (message or "none"),
                 "- step copy: %s" % (run.artifact or "not written"), "", "## stderr", "", "```",
                 run.stderr.rstrip("\n") or "(empty)", "```"]
        if run.crash is not None:
            lines += ["", "## traceback", "", "```", run.crash.rstrip("\n"), "```"]
        return self.write_incident("%s-%s" % (self.routine.id, run.slug),
                                   "%s %s ET, %s failed (%s)" % (self.routine.id, self.slot, run.tool, run.error_code),
                                   lines)

    # ---- parts ----------------------------------------------------------------------------------

    def selected(self, run: StepRun) -> bool:
        if run.failed or run.tool not in self.routine.post:
            return False
        return self.routine.posts != POLICY_FIRED or run.exit_code in POSTED_EXIT_CODES

    def part_of(self, run: StepRun) -> Optional[Part]:
        envelope = run.envelope or {}
        payload = envelope.get("payload")
        delay = _delay(envelope)
        generated = _instant(envelope.get("generated_at")) or self.start
        if isinstance(payload, dict) and isinstance(payload.get("message"), str) and payload["message"].strip():
            text = payload["message"].rstrip("\n")
            if isinstance(payload.get("emitted"), dict):
                return self.strict_part(run, text, payload["emitted"], generated, delay)
            return self.text_part(run, KIND_TEXT, text, _instant(payload.get("as_of")) or generated, delay)
        error = envelope.get("error")
        if envelope.get("status") == contract.STATUS_UNAVAILABLE and isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return self.text_part(run, KIND_UNAVAILABLE, UNAVAILABLE_LINE % message.strip(), generated, delay)
        return None

    def strict_part(self, run: StepRun, text: str, emitted: Dict[str, Any], generated: datetime.datetime,
                    delay: int) -> Part:
        try:
            document = alert_lint.load_emitted(emitted)
        except ValueError as exc:
            part = Part(run, KIND_STRICT, "", generated, delay)
            self.withhold(part, [EMITTED_INVALID], ["%s: %s" % (EMITTED_INVALID, exc)])
            return part
        lines = text.split("\n")
        if len(lines) > 1 and alert_lint.FOOTER_RE.match(lines[-1]):
            lines = lines[:-1]
        part = Part(run, KIND_STRICT, "\n".join(lines), document.as_of or generated, delay,
                    list(document.emitted_numbers), list(document.rule_ids or []), list(document.identifiers or []),
                    list(document.clocks or []))
        result = document.check(text)
        if not result.ok:
            self.withhold(part, _codes(result), _coded(result))
        return part

    def text_part(self, run: StepRun, kind: str, text: str, as_of: datetime.datetime, delay: int) -> Part:
        part = Part(run, kind, "", as_of, delay)
        part.body = alerts.fmt_reason(text, part.emitted_numbers, part.rule_ids, part.identifiers, part.clocks)
        footer = alerts.fmt_footer(as_of, delay, part.emitted_numbers, part.clocks)
        result = alert_lint.check(part.body + "\n" + footer, part.emitted_numbers, rule_ids=part.rule_ids or None,
                                  identifiers=part.identifiers, clocks=part.clocks, as_of=as_of)
        if not result.ok:
            self.withhold(part, _codes(result), _coded(result))
        return part

    def withhold(self, part: Part, codes: List[str], problems: List[str]) -> None:
        """Replace a part that failed its own lint with the WITHHELD line; record it (never its text)."""
        run = part.step
        part.withheld = codes
        part.body = WITHHELD_LINE % (run.tool, ", ".join(codes), run.tool)
        part.emitted_numbers, part.rule_ids, part.identifiers, part.clocks = [], [], [], []
        self.withheld.append({"tool": run.tool, "step": run.slug, "codes": list(codes)})
        lines = ["- step: %s" % run.step, "- finding codes: %s" % ", ".join(codes), "- step copy: %s"
                 % (run.artifact or "not written"), "", "## findings", ""] + ["- %s" % problem for problem in problems]
        lines += ["", "The text is not copied here; %s.py shows it." % run.tool]
        self.write_incident("%s-%s-withheld" % (self.routine.id, run.slug),
                            "%s %s ET, the %s text was withheld" % (self.routine.id, self.slot, run.tool), lines)

    # ---- outcomes -------------------------------------------------------------------------------

    def execute(self) -> Tuple[int, Optional[str], alert_lint.LintResult, Dict[str, Any]]:
        extra = self.extra_closures()
        self.checkout = self.pin()
        if not self.checkout["pinned"] and not self.args.allow_dirty:
            return self.refuse()
        if not self.routine.runs_on(self.et_date, extra):
            self.closed_day = True
            return self.quiet()
        if self.routine.id in EARLY_CLOSE_QUIET_ROUTINES and market_clock.is_early_close(self.et_date, extra):
            self.early_close = True
            return self.quiet()
        chain = self.routine.chain_for(self.slot)
        if any(routines.step_tool(step) == routines.BOOK_CHECK_TOOL for step in chain):
            self.next_run = next_run_utc(self.routine, self.et_date, self.slot, self.start, extra)
            if self.next_run is None:
                self.warnings.append("no next run of %s within %d days: book_check runs without --next-run-utc"
                                     % (self.routine.id, NEXT_RUN_SEARCH_DAYS))
            self.history_before = self.history_count()
        for step in chain:
            self.steps.append(self.run_one(step))
        return self.compose()

    def quiet(self, code: int = contract.EXIT_QUIET) -> Tuple[int, Optional[str], alert_lint.LintResult,
                                                            Dict[str, Any]]:
        return code, None, alert_lint.LintResult(True, []), dict(EMPTY_EMITTED)

    def exit_code(self, counted: Sequence[StepRun]) -> int:
        """Decision D2-5 over the counted steps and the posted parts (the lint is the caller's)."""
        if any(run.failed for run in counted):
            return contract.EXIT_ERROR
        if any(part.step.exit_code == contract.EXIT_FIRED for part in self.parts):
            return contract.EXIT_FIRED
        if any(run.exit_code == contract.EXIT_UNAVAILABLE for run in counted):
            return contract.EXIT_UNAVAILABLE
        return contract.EXIT_QUIET

    def refuse(self) -> Tuple[int, Optional[str], alert_lint.LintResult, Dict[str, Any]]:
        reason = self.checkout["reason"]
        path = os.path.join(self.paths.incidents_dir, "%s-dirty-checkout.md" % self.stamp)
        rel = self.paths.relative(path)
        body = ["# Incident %s: the desk checkout is not the pinned release" % self.stamp, "",
                "- routine: %s" % self.routine.id, "- slot: %s ET" % self.slot,
                "- run_utc: %s" % state.format_utc(self.start), "- release: v%s" % self.release,
                "- checkout: %s" % reason, "- no step ran", "",
                "The routine runs only from the clean commit of tag v%s (SETUP); --allow-dirty is for development "
                "runs only." % self.release]
        try:
            state.atomic_write_text(path, "\n".join(body) + "\n")
        except OSError as exc:
            self.warnings.append("%s not written: %s" % (rel, exc.strerror or exc))
        else:
            self.written.append(rel)
        self.incidents.append(rel)
        text, result, document = notice(DIRTY_LINE % (self.routine.id, self.release, reason, rel), self.routine.id,
                                        self.start)
        return contract.EXIT_UNAVAILABLE, text, result, document

    def compose(self) -> Tuple[int, Optional[str], alert_lint.LintResult, Dict[str, Any]]:
        message = Message()
        if self.args.allow_dirty:
            message.add_line(DEVELOPMENT_LINE)
        transitions = self.routine.posts == POLICY_TRANSITIONS
        counted = [run for run in self.steps if not transitions or run.tool in self.routine.post]
        incident_lines = 0
        for run in self.steps:
            if not run.failed:
                continue
            rel = self.step_incident(run)
            if not transitions or run.tool in self.routine.post:
                message.add_line(STEP_FAILED_LINE % (self.routine.id, self.slot, run.tool, run.error_code, rel))
                incident_lines += 1
        for run in self.steps:
            if self.selected(run):
                part = self.part_of(run)
                if part is not None:
                    self.parts.append(part)
        if not self.parts and not incident_lines:
            return self.quiet(self.exit_code(counted))  # quiet text, but a counted 20 (an acknowledged banner) stands
        for part in self.parts:
            message.add_part(part)
        if self.parts:
            self.newest = max(self.parts, key=lambda item: item.as_of)
            as_of, delay = self.newest.as_of, self.newest.delay_min
        else:
            as_of, delay = self.start, alert_lint.FOOTER_DELAY_MIN
        text, result, document = message.finish(as_of, delay)
        if not result.ok:
            codes = _codes(result)
            self.write_incident("%s-message" % self.routine.id, "%s %s ET, the routine message was withheld"
                                % (self.routine.id, self.slot),
                                ["- finding codes: %s" % ", ".join(codes),
                                 "- parts: %s" % (", ".join(part.step.slug for part in self.parts) or "none"), "",
                                 "## findings", ""] + ["- %s" % problem for problem in _coded(result)]
                                + ["", "The text is not copied here; the step copies hold each part."])
            withheld_text, _ok, withheld_document = notice(MESSAGE_WITHHELD_LINE % (self.routine.id, ", ".join(codes)),
                                                           self.routine.id, as_of)
            return contract.EXIT_ERROR, withheld_text, result, withheld_document
        return self.exit_code(counted), text, result, document

    # ---- the heartbeat, the journal and the run record -----------------------------------------

    def owns_heartbeat(self) -> bool:
        """True unless a ``book_check --post`` step wrote this routine's heartbeat during the run (and whenever the
        history cannot be read, before or after the steps)."""
        if self.history_before is None:
            return True
        after = self.history_count()
        return after is None or after == self.history_before

    def crashed(self, trace: str) -> str:
        """An unexpected error stopped the run: the crash incident (its traceback) and the routine's heartbeat (exit
        1); the incident's desk-root relative path."""
        rel = self.write_incident("%s-crash" % self.routine.id, "%s %s ET, an unexpected error stopped the run"
                                  % (self.routine.id, self.slot), ["", "## traceback", "", "```", trace.rstrip("\n"),
                                                                   "```"])
        finished = self.start if self.args.now is not None else max(self.start, state.utc_now())
        try:
            heartbeat.write_heartbeat(self.paths, self.routine.id, now=self.start, exit_code=contract.EXIT_ERROR,
                                      status=contract.STATUS_ERROR, started_utc=self.start, finished_utc=finished,
                                      message_sha256=None, scheduled_et=self.slot, tool_version=self.release)
        except (OSError, heartbeat.HeartbeatError) as exc:
            self.warnings.append("the heartbeat was not written: %s" % exc)
        return rel

    def record(self, code: int, text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """Write the heartbeat and the journal (when this tool owns them) and the run record; ``(heartbeat path,
        run record path)`` relative to the desk root, ``None`` for one not written."""
        digest = heartbeat.sha256_hex(text.encode("utf-8")) if text is not None else None
        beat: Optional[str] = self.paths.relative(heartbeat.heartbeat_path(self.paths, self.routine.id))
        if self.owns_heartbeat():
            status = contract.status_for(code) if code else (contract.STATUS_OK if text else contract.STATUS_QUIET)
            finished = self.start if self.args.now is not None else max(self.start, state.utc_now())
            try:
                heartbeat.write_heartbeat(self.paths, self.routine.id, now=self.start, exit_code=code, status=status,
                                          started_utc=self.start, finished_utc=finished, message_sha256=digest,
                                          scheduled_et=self.slot, tool_version=self.release)
            except (OSError, heartbeat.HeartbeatError) as exc:
                self.warnings.append("the heartbeat was not written: %s" % exc)
                beat = None
            else:
                self.written.append(beat)
            if text is not None:
                try:
                    signals.append_alert(self.paths, None, text, True, posted_at=self.start)
                except (OSError, ValueError, signals.SignalJournalError) as exc:
                    self.warnings.append("the journal was not appended: %s" % exc)
                else:
                    for path in (signals.alerts_path(self.paths), signals.day_journal_path(self.paths, self.et_date)):
                        self.written.append(self.paths.relative(path))
        record = {"schema": RUN_RECORD_SCHEMA, "routine": self.routine.id, "slot": self.slot, "run_utc": self.start,
                  "development": bool(self.args.allow_dirty), "checkout": dict(self.checkout),
                  "steps": [run.record() for run in self.steps], "withheld": list(self.withheld),
                  "incidents": list(self.incidents), "message_sha256": digest, "exit_code": code}
        rel = self.write_json(run_record_path(self.paths, self.et_date, self.routine.id, self.slot), record)
        return beat, rel

    def payload(self, code: int, text: Optional[str], result: alert_lint.LintResult, document: Dict[str, Any],
                beat: Optional[str], record: Optional[str]) -> Dict[str, Any]:
        return {
            "routine": self.routine.id, "slot": self.slot, "et_date": self.et_date.isoformat(),
            "run_utc": self.start, "lateness_min": self.lateness, "next_run_utc": self.next_run,
            "development": bool(self.args.allow_dirty), "checkout": dict(self.checkout),
            "closed_day": self.closed_day, "early_close": self.early_close, "quiet": text is None, "message": text,
            "parts": [part.as_dict() for part in self.parts], "steps": [run.as_dict() for run in self.steps],
            "withheld": list(self.withheld), "incidents": list(self.incidents),
            "lint": {"ok": result.ok, "problems": _coded(result), "numbers_checked": result.numbers_checked},
            "emitted": document, "heartbeat": beat, "run_record": record, "written": list(self.written),
            "warnings": list(self.warnings),
            "message_sha256": heartbeat.sha256_hex(text.encode("utf-8")) if text is not None else None,
            "exit_code": code,
        }

    def source(self) -> Tuple[Optional[str], Optional[int]]:
        """The newest part's envelope source and delay (the envelope's own), else none."""
        if self.newest is None or self.newest.step.envelope is None:
            return None, None
        envelope = self.newest.step.envelope
        source = envelope.get("source")
        delay = envelope.get("delay_min")
        return (source if isinstance(source, str) else None,
                delay if isinstance(delay, int) and not isinstance(delay, bool) and delay >= 0 else None)


# --------------------------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------------------------


def build_parser(release: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=release, description=DESCRIPTION)
    parser.add_argument("routine_id", nargs="?", default=None, metavar="ROUTINE",
                        help="a routine id of routines/manifest.toml (preopen, book-check, final-call, ...)")
    parser.add_argument("--slot", default=None, metavar="HH:MM",
                        help="the routine's America/New_York slot (the manifest prompt passes it); default: the latest "
                             "slot at or before now")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="development only: run although the checkout is not the clean release tag (logged in "
                             "the run record and printed first)")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set passed to the steps that read one (--offline only)")
    # The shared --desk-root help says the default is the current directory; this tool's default is its own.
    for action in parser._actions:
        if "--desk-root" in action.option_strings:
            action.help = DESK_ROOT_HELP
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any, manifest: routines.Manifest) -> routines.Routine:
    ids = ", ".join(manifest.routine_ids) or "none"
    if args.routine_id is None:
        parser.error("a routine id is required (the manifest names %s)" % ids)
    routine = manifest.by_id(args.routine_id)
    if routine is None:
        parser.error("unknown routine %r (the manifest names %s)" % (args.routine_id, ids))
    if args.slot is not None and args.slot not in routine.slots():
        parser.error("--slot %s is not a slot of routine %s (%s)" % (args.slot, routine.id, ", ".join(routine.slots())))
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    if not args.desk_root_explicit:
        args.desk_root = DEFAULT_DESK_ROOT
    checkout = version.repository_root()
    if _inside(args.desk_root, checkout):
        parser.error("desk root %s is the desk checkout %s or inside it: the routine's runtime files would make the "
                     "checkout dirty (use a desk root outside it; the default is %s)"
                     % (args.desk_root, checkout, DEFAULT_DESK_ROOT))
    return routine


def fail(args: Any, release: str, start: datetime.datetime, routine_id: Optional[str], code: str, message: str,
         known_ids: Sequence[str] = ()) -> int:
    """An error before or around the routine's run (``manifest_invalid``, ``desk_root``, ``io_error``, ``lock_busy``,
    ``routine_crashed``): the error envelope with ``--json``, else the linted INCIDENT notice the Bot posts. The notice
    names the routine only by an id of the manifest (``known_ids``), else ``routine``: an id the manifest never
    validated is never echoed."""
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, release, code, message, contract.STATUS_ERROR,
                                                          generated_at=start)))
        return contract.EXIT_ERROR
    name = routine_id if isinstance(routine_id, str) and routine_id in known_ids else TOOL
    if code == "lock_busy":
        line = LOCK_BUSY_LINE % name
    else:
        line = NOT_RUN_LINE % (name, code, message)
    print(notice(line, name, start)[0])
    return contract.EXIT_ERROR


def main(argv: Optional[Sequence[str]] = None) -> int:
    release = release_version()
    parser = build_parser(release)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    start = args.now or state.utc_now()
    try:
        manifest = routines.load_manifest()
    except routines.ManifestError as exc:
        return fail(args, release, start, args.routine_id, "manifest_invalid", str(exc))
    routine = validate(parser, args, manifest)
    known = manifest.routine_ids
    try:
        paths = desk_paths.for_root(args.desk_root)
    except desk_paths.DeskPathsError as exc:
        return fail(args, release, start, routine.id, "desk_root", str(exc), known)
    try:
        desk_paths.ensure_runtime_dirs(paths)
    except OSError as exc:
        return fail(args, release, start, routine.id, "io_error", "cannot prepare the desk runtime directories: %s"
                    % (exc.strerror or exc), known)
    run = Run(args, release, routine, paths, start)
    try:
        with state.locked(lock_path(paths, routine.id), timeout_s=LOCK_TIMEOUT_S):
            try:
                code, text, result, document = run.execute()
                beat, record = run.record(code, text)
            except Exception as exc:  # noqa: BLE001 - the run's own bug: recorded, noticed, never a bare traceback
                rel = run.crashed(traceback.format_exc())
                error = "io_error" if isinstance(exc, OSError) else ROUTINE_CRASHED
                return fail(args, release, start, routine.id, error, CRASH_MESSAGE % rel, known)
    except state.LockBusy:
        return fail(args, release, start, routine.id, "lock_busy", LOCK_BUSY_LINE % routine.id, known)
    if args.json:
        source, delay = run.source()
        status = contract.status_for(code) if code else (contract.STATUS_OK if text else contract.STATUS_QUIET)
        envelope = contract.envelope(TOOL, release, run.payload(code, text, result, document, beat, record),
                                     source=source, delay_min=delay, generated_at=start, status=status)
        print(contract.json_dumps(envelope))
    elif text is not None:
        print(text)
    else:
        print(DEVELOPMENT_LINE + "\n" + QUIET_LINE if args.allow_dirty else QUIET_LINE)
    return code


if __name__ == "__main__":
    sys.exit(main())
