"""The routine manifest model: ``routines/manifest.toml``, its structural rules, the weekly budget, the Grok Bot
Auto-review texts of ``routines/autoreview.toml`` and the day audit of the heartbeat history. Standard library only.

New for Aegis Desk (plan sections 9, 10 and 11). One module reads the manifest for every consumer:
``scripts/routine.py`` (the single reviewed routine entry point), ``scripts/routine_audit.py`` (``--budget``,
``--rules-text`` and the day audit), ``scripts/desk_doctor.py`` (the heartbeat check), ``scripts/eod_summary.py``
(``N of N routines ran``) and the gate ``scripts/check_manifests.py``, which adds the checks that need the tree
under test (owners, tool parsers). Nothing here runs a tool, reads the wall clock or writes a file.

The manifest (schema 1)
-----------------------
Top-level keys ``schema_version`` (1), ``profiles`` and ``routine`` and nothing else. ``[profiles.<name>]`` names a
cadence profile of ``desk.toml`` (``lean``, ``active``) with a non-empty ``description`` and an optional ``additive``
flag: an additive profile is the other profile plus slots of its own. One ``[[routine]]`` per ``routine.py`` id,
with exactly these keys (``slot_chain`` and ``depends_on`` optional, every other key required; any other key is
refused, ``et_early_close`` included: on an early-close day ``final-call`` runs no step and the 07:30 Morning Desk
Sheet carries the early-close line):

* ``id`` (:data:`aegis_desk.heartbeat.ROUTINE_ID_RE`, unique), ``owner`` (an ``agents/<owner>.md`` stem; the gate
  checks the file), ``stage`` (a positive integer);
* ``profiles`` (declared profile names) and ``et`` (one non-empty, sorted, unique list of ``HH:MM`` America/New_York
  slots per listed profile, and no other key); an additive profile's slots contain every other listed profile's
  slots, and a routine of a non-additive profile also lists every additive one;
* ``days`` (unique members of :data:`DAYS`) and ``trading_days_only`` (a boolean: such a routine runs no step on an
  NYSE holiday or an owner-injected full closure);
* ``chain`` (the steps ``routine.py`` runs in order) and ``slot_chain`` (``{"HH:MM": [steps]}``, the chain of one
  slot instead; every key a slot of the routine). A step is ``<tool> [tokens]``: every token matches
  :data:`CHAIN_TOKEN_RE` (no pipe, redirection, quote or separator can appear), the tool is one of
  :data:`STEP_TOOLS`, no token is a flag ``routine.py`` sets itself (:data:`ROUTINE_OWNED_FLAGS`, also in the
  ``--flag=value`` spelling) or a flag no routine may carry (:data:`FORBIDDEN_CHAIN_FLAGS`), ``ack`` appears
  nowhere (its text fails the alert lint, and ``eod_summary --brief`` reads the same journals), ``routine_audit`` is
  never a step (``eod_summary`` calls :func:`audit_day` itself), and a chain holds at most one ``book_check`` step,
  which carries ``--post``;
* ``depends_on`` (``{routine, min_gap_min}``): another routine of the manifest, with ``min_gap_min`` an integer of at
  least :data:`MIN_GAP_MIN`, and every slot of the dependant at least that many minutes after the dependency's
  latest slot;
* ``post`` (the tools whose text is posted: non-empty, unique, each a step of the routine's chains) and ``posts``
  (:data:`POSTS`; ``transitions`` means ``post = ["book_check"]`` and a ``book_check`` step in every chain);
* ``quiet_rule`` and ``approval_boundary`` (non-empty strings) and ``prompt`` (exactly
  ``PROMPT_TEMPLATE % id``).

No weekday slot falls in the delay window ``[08:00, 10:00)`` ET (:data:`DELAY_WINDOW_ET`, half open): the first
delayed quotes of the session are not in yet. A Saturday-only slot (``post-expiry`` 09:30) is outside the rule.
:func:`load_manifest` and :func:`parse_manifest_text` collect every problem and raise one :class:`ManifestError`
(a ``ValueError``) holding them all; the skeleton (``schema_version`` only, zero routines) is valid.

Budget and Auto-review texts
----------------------------
:func:`budget` is the regular runs per week of a profile: the sum over its routines of slots x days (holidays not
deducted). ``routines/autoreview.toml`` holds the three Auto-review texts (plan section 10, layer 6); the allow
text carries the literal :data:`ROUTINE_IDS_PLACEHOLDER`, which :func:`rules_text` replaces with the manifest's ids,
comma-separated, in manifest order. :func:`rules_text_problems` names a text longer than ``limit_chars``
(:data:`ALLOW_LIMIT_CHARS`) and an allow text that does not name every routine id.

The day audit
-------------
:func:`audit_day` reads heartbeat history records (``state/heartbeat/history.jsonl``, as
``aegis_desk.signals.read_journal`` returns them) against the slots :func:`day_slots` gives the cadence profile on
one ET date: routines of that profile whose ``days`` include the weekday and, for ``trading_days_only`` routines,
only on a trading day (``market_clock.is_trading_day`` with ``extra_holidays``). An early-close day expects the
regular slots: they all fire, ``final-call`` with a quiet heartbeat. A record counts for a slot once per distinct
``(routine_id, ET date of run_utc, scheduled_et)``, so ``book_check``'s heartbeat and ``routine.py``'s for one slot
never count twice, and a record without ``scheduled_et`` (a hand run) maps to no slot. The earliest record of a slot
gives its start, its lateness (the record's own ``lateness_min``), its signed offset from the slot (whole minutes,
floor; negative before the slot) and its exit code and status.

* ``expected``: the slots due at or before ``now`` plus any slot that already has a record (a run before its slot);
  with ``through_et`` (the running routine's own slot), exactly the slots at or before it. ``exclude`` drops the
  named routines: the 17:10 EOD brief counts the other routines only (``exclude=("eod-brief",)``).
* ``ran``: the expected slots with a record; ``late``: those whose lateness exceeds :data:`LATE_AFTER_MIN` minutes
  (the routine prompt's 20-minute mark); ``missed``: expected slots without a record once ``now`` is at least
  :data:`MISSED_AFTER_MIN` minutes past the slot (the doctor's dead-man rule); ``pending`` (a property): the rest.

:func:`timezone_offset_suspected` names a constant offset of about 60 or 120 minutes (either sign) across every run
of the day, the signature of a scheduler set to the wrong time zone.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from . import contract, desk_config, heartbeat, market_clock, state, toml_compat, version

MANIFEST_REL: str = "routines/manifest.toml"
AUTOREVIEW_REL: str = "routines/autoreview.toml"
SCHEMA_VERSION: int = 1
DAYS: Tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
"""Day names in ``datetime.date.weekday()`` order."""
WEEKDAYS: Tuple[str, ...] = DAYS[:5]
POSTS: Tuple[str, ...] = ("always", "transitions", "fired")
STEP_TOOLS: Tuple[str, ...] = ("assignment_watch", "book", "book_check", "csv_recon", "desk_doctor", "eod_summary",
                               "expiry_checklist", "trigger_levels")
"""The tools a chain may run; ``routine.py`` imports exactly these (``STEP_MODULES``)."""
FIXTURE_SET_TOOLS: FrozenSet[str] = frozenset({"book_check", "book", "csv_recon", "expiry_checklist",
                                               "assignment_watch", "trigger_levels"})
"""The step tools whose parser declares ``--fixture-set`` (``routine.py`` passes its own through to them)."""
NO_FIXTURE_SET_FLAGS: Tuple[str, ...] = ("--from-snapshot",)
"""A step carrying one of these reads a saved record, never quotes: ``routine.py`` passes it no ``--fixture-set``
(``trigger_levels --from-snapshot`` refuses the pair)."""
REFUSED_STEP_TOOLS: Dict[str, str] = {
    "ack": "its text fails the alert lint (IS07) and eod_summary --brief reads the same journals",
    "routine_audit": "eod_summary calls routines.audit_day itself; routine_audit.py is an owner and Bot command",
}
ROUTINE_OWNED_FLAGS: Tuple[str, ...] = ("--json", "--schema", "--timeout", "--desk-root", "--offline", "--now",
                                        "--fixture-set", "--routine", "--scheduled-et", "--next-run-utc", "-h",
                                        "--help")
"""Flags ``routine.py`` sets (or that would change the step's output shape): never written in the manifest."""
FORBIDDEN_CHAIN_FLAGS: Tuple[str, ...] = ("--owner-tapped", "--allow-rth", "--diff", "--allow-dirty")
"""Flags no routine may carry: owner authority, owner-typed cell text, an unverified checkout."""
CHAIN_TOKEN_RE = re.compile(r"^[A-Za-z0-9_:.=-]+$")
BOOK_CHECK_TOOL: str = "book_check"
BOOK_CHECK_POST_FLAG: str = "--post"
MIN_GAP_MIN: int = 20
DELAY_WINDOW_ET: Tuple[str, str] = ("08:00", "10:00")
"""Half open: a weekday slot at 08:00 is inside, 10:00 outside."""
ALLOW_LIMIT_CHARS: int = 1000
ROUTINE_IDS_PLACEHOLDER: str = "{routine_ids}"
PROMPT_TEMPLATE: str = ("Run python3 /workspace/aegis-grok-desk/scripts/routine.py %s --slot {slot} and post its "
                        "output verbatim.")
MISSED_AFTER_MIN: int = 45
LATE_AFTER_MIN: int = 20
TZ_OFFSETS_MIN: Tuple[int, ...] = (-120, -60, 60, 120)
TZ_OFFSET_TOLERANCE_MIN: int = 10
RULE_KEYS: Tuple[str, ...] = ("allow", "ask_first_writes", "ask_first_owner_authority")
"""The keys of :func:`rules_text`, in the order SETUP.md lists the rules."""
AUTOREVIEW_TABLES: Tuple[str, ...] = ("allow_routines", "ask_first_writes", "ask_first_owner_authority")
"""The ``routines/autoreview.toml`` table of each of :data:`RULE_KEYS`."""
TOP_KEYS: Tuple[str, ...] = ("schema_version", "profiles", "routine")
PROFILE_KEYS: Tuple[str, ...] = ("description", "additive")
ROUTINE_KEYS: Tuple[str, ...] = ("id", "owner", "stage", "profiles", "et", "days", "trading_days_only", "chain",
                                 "slot_chain", "depends_on", "post", "posts", "quiet_rule", "approval_boundary",
                                 "prompt")
OPTIONAL_ROUTINE_KEYS: Tuple[str, ...] = ("slot_chain", "depends_on")
DEPENDS_KEYS: Tuple[str, ...] = ("routine", "min_gap_min")
AUTOREVIEW_TOP_KEYS: Tuple[str, ...] = ("schema_version", "limit_chars") + AUTOREVIEW_TABLES
AGENT_STEM_RE = re.compile(r"^[a-z][a-z0-9-]*$")
UTC = datetime.timezone.utc


class ManifestError(ValueError):
    """``routines/manifest.toml`` (or ``routines/autoreview.toml``) breaks the schema; ``problems`` lists every defect."""

    def __init__(self, path: str, problems: Sequence[str]) -> None:
        self.path = path
        self.problems = list(problems)
        super().__init__("%s: %s" % (path, "; ".join(self.problems)))


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    additive: bool


@dataclass(frozen=True)
class Dependency:
    routine: str
    min_gap_min: int


@dataclass(frozen=True)
class Routine:
    """One ``[[routine]]`` (module notes)."""

    id: str
    owner: str
    stage: int
    profiles: Tuple[str, ...]
    et: Dict[str, Tuple[str, ...]]
    days: Tuple[str, ...]
    trading_days_only: bool
    chain: Tuple[str, ...]
    slot_chain: Dict[str, Tuple[str, ...]]
    depends_on: Optional[Dependency]
    post: Tuple[str, ...]
    posts: str
    quiet_rule: str
    approval_boundary: str
    prompt: str

    def slots(self, profile: Optional[str] = None) -> Tuple[str, ...]:
        """The ``HH:MM`` slots of ``profile`` (``()`` when the routine is not in it), or the sorted union of every
        listed profile's slots when ``profile`` is ``None``."""
        if profile is None:
            return tuple(sorted({slot for name in self.profiles for slot in self.et.get(name, ())}))
        return tuple(self.et.get(profile, ()))

    def chain_for(self, slot: str) -> Tuple[str, ...]:
        """The steps of ``slot``: its ``slot_chain`` entry, else ``chain``. ``ValueError`` for a slot it lacks."""
        if slot not in self.slots():
            raise ValueError("%r is not a slot of routine %s (%s)" % (slot, self.id, ", ".join(self.slots())))
        return self.slot_chain.get(slot, self.chain)

    def chains(self) -> Tuple[Tuple[str, ...], ...]:
        """``chain`` and every ``slot_chain`` entry (in slot order)."""
        return (self.chain,) + tuple(self.slot_chain[slot] for slot in sorted(self.slot_chain))

    def tools(self) -> Set[str]:
        """Every tool any of the routine's chains runs."""
        return {step_tool(step) for steps in self.chains() for step in steps}

    def runs_on(self, et_date: datetime.date, extra_holidays: market_clock.ExtraHolidays = ()) -> bool:
        """True when ``et_date``'s weekday is in ``days`` and, for ``trading_days_only``, it is a trading day."""
        day = _as_date(et_date)
        if DAYS[day.weekday()] not in self.days:
            return False
        return not self.trading_days_only or market_clock.is_trading_day(day, extra_holidays)


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    profiles: Dict[str, Profile]
    routines: Tuple[Routine, ...]
    path: str
    sha256: str

    @property
    def routine_ids(self) -> Tuple[str, ...]:
        return tuple(routine.id for routine in self.routines)

    def by_id(self, routine_id: str) -> Optional[Routine]:
        for routine in self.routines:
            if routine.id == routine_id:
                return routine
        return None

    def routines_for(self, profile: str) -> Tuple[Routine, ...]:
        _check_profile(self, profile)
        return tuple(routine for routine in self.routines if profile in routine.profiles)


@dataclass(frozen=True)
class Autoreview:
    schema_version: int
    limit_chars: int
    allow_template: str
    ask_first_writes: str
    ask_first_owner_authority: str
    path: str
    sha256: str


@dataclass(frozen=True)
class Slot:
    routine_id: str
    scheduled_et: str
    scheduled_utc: datetime.datetime


@dataclass(frozen=True)
class SlotAudit:
    """One expected slot and the earliest history record of it (``records`` 0: none)."""

    routine_id: str
    scheduled_et: str
    scheduled_utc: datetime.datetime
    records: int
    started_utc: Optional[datetime.datetime]
    lateness_min: Optional[int]
    offset_min: Optional[int]
    exit_code: Optional[int]
    status: Optional[str]

    @property
    def ran(self) -> bool:
        return self.records > 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "routine_id": self.routine_id,
            "scheduled_et": self.scheduled_et,
            "scheduled_utc": state.format_utc(self.scheduled_utc),
            "records": self.records,
            "started_utc": None if self.started_utc is None else state.format_utc(self.started_utc),
            "lateness_min": self.lateness_min,
            "offset_min": self.offset_min,
            "exit_code": self.exit_code,
            "status": self.status,
        }


@dataclass(frozen=True)
class DayAudit:
    """The audit of one ET date (module notes): every field a tuple of :class:`SlotAudit` in slot order."""

    expected: Tuple[SlotAudit, ...]
    ran: Tuple[SlotAudit, ...]
    late: Tuple[SlotAudit, ...]
    missed: Tuple[SlotAudit, ...]

    @property
    def pending(self) -> Tuple[SlotAudit, ...]:
        """Expected slots without a record whose 45-minute grace has not passed yet."""
        return tuple(slot for slot in self.expected if slot not in self.ran and slot not in self.missed)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "expected": [slot.as_dict() for slot in self.expected],
            "ran": [slot.as_dict() for slot in self.ran],
            "late": [slot.as_dict() for slot in self.late],
            "missed": [slot.as_dict() for slot in self.missed],
            "pending": [slot.as_dict() for slot in self.pending],
        }


# --------------------------------------------------------------------------------------------
# Paths and small helpers
# --------------------------------------------------------------------------------------------


def manifest_path(root: Optional[str] = None) -> str:
    """``<root>/routines/manifest.toml``; the root defaults to this checkout."""
    base = version.repository_root() if root is None else root
    return os.path.join(base, *MANIFEST_REL.split("/"))


def autoreview_path(root: Optional[str] = None) -> str:
    """``<root>/routines/autoreview.toml``; the root defaults to this checkout."""
    base = version.repository_root() if root is None else root
    return os.path.join(base, *AUTOREVIEW_REL.split("/"))


def step_tokens(step: str) -> Tuple[str, ...]:
    """The whitespace-separated tokens of a chain step (the tool first)."""
    return tuple(step.split())


def step_tool(step: str) -> str:
    """The tool a chain step runs (its first token)."""
    tokens = step_tokens(step)
    return tokens[0] if tokens else ""


def slot_minutes(slot: str) -> int:
    """Minutes after midnight of an ``HH:MM`` slot; ``ValueError`` for any other spelling."""
    match = heartbeat.SCHEDULED_ET_RE.match(slot) if isinstance(slot, str) else None
    if match is None:
        raise ValueError("a slot is HH:MM on the 24-hour clock (America/New_York), got %r" % (slot,))
    return int(match.group(1)) * 60 + int(match.group(2))


def slot_instant(et_date: datetime.date, slot: str) -> datetime.datetime:
    """The UTC instant of ``slot`` (America/New_York) on ``et_date``."""
    minutes = slot_minutes(slot)
    day = _as_date(et_date)
    return market_clock.from_et(datetime.datetime(day.year, day.month, day.day, minutes // 60, minutes % 60))


def _as_date(value: Any) -> datetime.date:
    if isinstance(value, datetime.datetime) or not isinstance(value, datetime.date):
        raise ValueError("et_date must be a datetime.date, got %r" % (value,))
    return value


def _instant(value: Any, what: str) -> datetime.datetime:
    if not isinstance(value, datetime.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("%s must be an aware UTC datetime, got %r" % (what, value))
    return value.astimezone(UTC)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _check_profile(manifest: Manifest, profile: Any) -> str:
    if profile in manifest.profiles or profile in desk_config.CADENCE_PROFILES:
        return profile
    raise ValueError("unknown profile %r (the manifest declares %s)" % (profile, ", ".join(manifest.profiles) or "none"))


# --------------------------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------------------------


class _Builder:
    def __init__(self, document: Dict[str, Any]) -> None:
        self.document = document
        self.problems: List[str] = []
        self.profiles: Dict[str, Profile] = {}
        self.seen_ids: List[str] = []

    def add(self, message: str) -> None:
        self.problems.append(message)

    def top(self) -> int:
        for key in self.document:
            if key not in TOP_KEYS:
                self.add("unknown top-level key %r (allowed: %s)" % (key, ", ".join(TOP_KEYS)))
        schema = self.document.get("schema_version")
        if not _is_int(schema) or schema != SCHEMA_VERSION:
            self.add("schema_version must be %d (found %r)" % (SCHEMA_VERSION, schema))
        return schema if _is_int(schema) else 0

    def read_profiles(self) -> None:
        raw = self.document.get("profiles", {})
        if not isinstance(raw, dict):
            self.add("[profiles] must be a table of profile tables")
            return
        for name, table in raw.items():
            if name not in desk_config.CADENCE_PROFILES:
                self.add("profile %r is not a cadence profile (%s)" % (name, ", ".join(desk_config.CADENCE_PROFILES)))
                continue
            if not isinstance(table, dict):
                self.add("profile %s must be a table" % name)
                continue
            for key in table:
                if key not in PROFILE_KEYS:
                    self.add("profile %s: unknown key %r" % (name, key))
            description = table.get("description")
            if not _nonblank(description):
                self.add("profile %s: description must be a non-empty string" % name)
            additive = table.get("additive", False)
            if not isinstance(additive, bool):
                self.add("profile %s: additive must be true or false" % name)
                additive = False
            self.profiles[name] = Profile(name, description if isinstance(description, str) else "", additive)

    def steps(self, label: str, where: str, raw: Any) -> Optional[Tuple[str, ...]]:
        if not _string_list(raw) or not raw:
            self.add("%s: %s must be a non-empty list of steps" % (label, where))
            return None
        before = len(self.problems)
        book_checks = 0
        for step in raw:
            if not step.strip():
                self.add("%s: empty step in %s" % (label, where))
                continue
            tokens = step_tokens(step)
            for token in tokens:
                if not CHAIN_TOKEN_RE.match(token):
                    self.add("%s: step %r: token %r must match %s" % (label, step, token, CHAIN_TOKEN_RE.pattern))
            tool = tokens[0]
            if "ack" in tokens:
                self.add("%s: step %r: ack is not a step tool: %s" % (label, step, REFUSED_STEP_TOOLS["ack"]))
            elif tool in REFUSED_STEP_TOOLS:
                self.add("%s: step %r: %s is not a step tool: %s" % (label, step, tool, REFUSED_STEP_TOOLS[tool]))
            elif tool not in STEP_TOOLS:
                self.add("%s: step %r: %s is not a step tool (%s)" % (label, step, tool, ", ".join(STEP_TOOLS)))
            for token in tokens[1:]:
                flag = token.split("=", 1)[0]
                if flag in ROUTINE_OWNED_FLAGS:
                    self.add("%s: step %r: %s is set by routine.py, never by the manifest" % (label, step, flag))
                elif flag in FORBIDDEN_CHAIN_FLAGS:
                    self.add("%s: step %r: %s is never allowed in a routine chain" % (label, step, flag))
            if tool == BOOK_CHECK_TOOL:
                book_checks += 1
                if BOOK_CHECK_POST_FLAG not in tokens[1:]:
                    self.add("%s: step %r: the book_check step must carry --post" % (label, step))
        if book_checks > 1:
            self.add("%s: at most one book_check step per chain (%s has %d)" % (label, where, book_checks))
        if len(self.problems) != before:
            return None
        return tuple(raw)

    def slot_lists(self, label: str, listed: Sequence[str], raw: Any) -> Optional[Dict[str, Tuple[str, ...]]]:
        if not isinstance(raw, dict):
            self.add("%s: et must be a table of slot lists, one per listed profile" % label)
            return None
        before = len(self.problems)
        if set(raw) != set(listed):
            self.add("%s: et must hold one slot list per listed profile (et has %s; profiles %s)"
                     % (label, ", ".join(sorted(raw)) or "none", ", ".join(listed) or "none"))
        out: Dict[str, Tuple[str, ...]] = {}
        for name in listed:
            if name not in raw:
                continue
            slots = raw[name]
            if not _string_list(slots) or not slots:
                self.add("%s: et.%s must be a non-empty list of HH:MM slots" % (label, name))
                continue
            bad = [slot for slot in slots if not heartbeat.SCHEDULED_ET_RE.match(slot)]
            for slot in bad:
                self.add("%s: et.%s slot %r is not HH:MM (24-hour clock, America/New_York)" % (label, name, slot))
            if bad:
                continue
            if list(slots) != sorted(set(slots)):
                self.add("%s: et.%s slots must be sorted and unique (found %s)" % (label, name, ", ".join(slots)))
                continue
            out[name] = tuple(slots)
        return out if len(self.problems) == before else None

    def routine(self, index: int, raw: Any) -> Optional[Routine]:
        if not isinstance(raw, dict):
            self.add("routine #%d must be a [[routine]] table" % (index + 1))
            return None
        routine_id = raw.get("id")
        label = "routine %s" % routine_id if isinstance(routine_id, str) and routine_id else "routine #%d" % (index + 1)
        before = len(self.problems)
        for key in raw:
            if key not in ROUTINE_KEYS:
                hint = ""
                if key == "et_early_close":
                    hint = " (there is no early-close slot: on an early-close day final-call runs no step)"
                self.add("%s: unknown key %r%s" % (label, key, hint))
        for key in ROUTINE_KEYS:
            if key not in OPTIONAL_ROUTINE_KEYS and key not in raw:
                self.add("%s: missing key %r" % (label, key))
        if "id" in raw:
            if not isinstance(routine_id, str) or not heartbeat.ROUTINE_ID_RE.match(routine_id):
                self.add("%s: routine id must match %s (found %r)" % (label, heartbeat.ROUTINE_ID_RE.pattern, routine_id))
            elif routine_id in self.seen_ids:
                self.add("duplicate routine id %r" % routine_id)
            else:
                self.seen_ids.append(routine_id)
        owner = raw.get("owner")
        if "owner" in raw and (not isinstance(owner, str) or not AGENT_STEM_RE.match(owner)):
            self.add("%s: owner %r must be an agent file stem (agents/<owner>.md)" % (label, owner))
        stage = raw.get("stage")
        if "stage" in raw and (not _is_int(stage) or stage < 1):
            self.add("%s: stage must be a positive integer (found %r)" % (label, stage))
        listed = raw.get("profiles")
        profiles_ok = _string_list(listed) and bool(listed) and len(set(listed)) == len(listed)
        if "profiles" in raw and not profiles_ok:
            self.add("%s: profiles must be a non-empty list of unique profile names" % label)
        if profiles_ok:
            for name in listed:
                if name not in self.profiles:
                    self.add("%s: profile %r is not declared under [profiles]" % (label, name))
        et = self.slot_lists(label, listed if profiles_ok else [], raw.get("et")) if "et" in raw else None
        days = raw.get("days")
        if "days" in raw:
            if not _string_list(days) or not days:
                self.add("%s: days must be a non-empty list of day names" % label)
            else:
                for day in days:
                    if day not in DAYS:
                        self.add("%s: day %r is not one of %s" % (label, day, ", ".join(DAYS)))
                if len(set(days)) != len(days):
                    self.add("%s: days must be unique" % label)
        trading = raw.get("trading_days_only")
        if "trading_days_only" in raw and not isinstance(trading, bool):
            self.add("%s: trading_days_only must be true or false" % label)
        chain = self.steps(label, "chain", raw.get("chain")) if "chain" in raw else None
        union: List[str] = sorted({slot for slots in (et or {}).values() for slot in slots})
        slot_chain: Dict[str, Tuple[str, ...]] = {}
        raw_slot_chain = raw.get("slot_chain", {})
        if not isinstance(raw_slot_chain, dict):
            self.add("%s: slot_chain must be a table of slot -> steps" % label)
        else:
            for slot, steps in raw_slot_chain.items():
                if et is not None and slot not in union:
                    self.add("%s: slot_chain key %r is not a slot of the routine" % (label, slot))
                parsed = self.steps(label, "slot_chain.%s" % slot, steps)
                if parsed is not None:
                    slot_chain[slot] = parsed
        depends: Optional[Dependency] = None
        if "depends_on" in raw:
            depends = self.dependency(label, raw["depends_on"])
        post = raw.get("post")
        post_ok = _string_list(post) and bool(post) and len(set(post)) == len(post)
        if "post" in raw and not post_ok:
            self.add("%s: post must be a non-empty list of unique tool names" % label)
        chains = ([chain] if chain is not None else []) + list(slot_chain.values())
        tools = {step_tool(step) for steps in chains for step in steps}
        if post_ok and chain is not None:
            for tool in post:
                if tool not in tools:
                    self.add("%s: post tool %r is not a step of its chains" % (label, tool))
        posts = raw.get("posts")
        if "posts" in raw:
            if posts not in POSTS:
                self.add("%s: posts %r is not one of %s" % (label, posts, ", ".join(POSTS)))
            elif posts == "transitions":
                if post != [BOOK_CHECK_TOOL]:
                    self.add('%s: posts \'transitions\' requires post = ["book_check"] (book_check applies the posting '
                             'rules itself)' % label)
                elif any(BOOK_CHECK_TOOL not in {step_tool(step) for step in steps} for steps in chains):
                    self.add("%s: posts 'transitions' requires a book_check step in every chain" % label)
        for key in ("quiet_rule", "approval_boundary"):
            if key in raw and not _nonblank(raw[key]):
                self.add("%s: %s must be a non-empty string" % (label, key))
        if isinstance(routine_id, str) and "prompt" in raw and raw["prompt"] != PROMPT_TEMPLATE % routine_id:
            self.add("%s: prompt must be exactly %r" % (label, PROMPT_TEMPLATE % routine_id))
        if et is not None and _string_list(days) and any(day in WEEKDAYS for day in days):
            low, high = (slot_minutes(bound) for bound in DELAY_WINDOW_ET)
            for slot in union:
                if low <= slot_minutes(slot) < high:
                    self.add("%s: slot %s falls in the delay window %s-%s ET on a weekday (the session's first delayed "
                             "quotes are not in yet)" % (label, slot, DELAY_WINDOW_ET[0], DELAY_WINDOW_ET[1]))
        if et is not None and profiles_ok:
            self.additive(label, listed, et)
        if len(self.problems) != before:
            return None
        return Routine(id=routine_id, owner=owner, stage=stage, profiles=tuple(listed), et=dict(et or {}),
                       days=tuple(days), trading_days_only=trading, chain=chain or (), slot_chain=slot_chain,
                       depends_on=depends, post=tuple(post), posts=posts, quiet_rule=raw["quiet_rule"],
                       approval_boundary=raw["approval_boundary"], prompt=raw["prompt"])

    def dependency(self, label: str, raw: Any) -> Optional[Dependency]:
        if not isinstance(raw, dict):
            self.add("%s: depends_on must be a table {routine, min_gap_min}" % label)
            return None
        for key in raw:
            if key not in DEPENDS_KEYS:
                self.add("%s: unknown depends_on key %r" % (label, key))
        target = raw.get("routine")
        gap = raw.get("min_gap_min")
        ok = True
        if not isinstance(target, str) or not target:
            self.add("%s: depends_on.routine must name a routine" % label)
            ok = False
        if not _is_int(gap) or gap < MIN_GAP_MIN:
            self.add("%s: depends_on.min_gap_min must be an integer of at least %d (found %r)" % (label, MIN_GAP_MIN, gap))
            ok = False
        return Dependency(target, gap) if ok else None

    def additive(self, label: str, listed: Sequence[str], et: Dict[str, Tuple[str, ...]]) -> None:
        additive = [name for name in listed if name in self.profiles and self.profiles[name].additive]
        plain = [name for name in listed if name in self.profiles and not self.profiles[name].additive]
        for big in additive:
            for small in plain:
                missing = sorted(set(et.get(small, ())) - set(et.get(big, ())))
                if missing:
                    self.add("%s: additive profile %s must contain %s's slots (missing %s)"
                             % (label, big, small, ", ".join(missing)))
        if plain:
            for name, profile in self.profiles.items():
                if profile.additive and name not in listed:
                    self.add("%s: a routine of profile %s must also list the additive profile %s" % (label, plain[0], name))

    def dependencies(self, built: Sequence[Routine]) -> None:
        by_id = {routine.id: routine for routine in built}
        for routine in built:
            depends = routine.depends_on
            if depends is None:
                continue
            label = "routine %s" % routine.id
            if depends.routine == routine.id:
                self.add("%s: depends_on.routine must differ from the routine itself" % label)
                continue
            if depends.routine not in self.seen_ids:
                self.add("%s: depends_on.routine %r is not a routine of this manifest" % (label, depends.routine))
                continue
            target = by_id.get(depends.routine)
            if target is None:
                continue  # the dependency has problems of its own, already reported
            latest = max(target.slots(), key=slot_minutes)
            for slot in routine.slots():
                gap = slot_minutes(slot) - slot_minutes(latest)
                if gap < depends.min_gap_min:
                    where = "%d min after" % gap if gap >= 0 else "%d min before" % -gap
                    self.add("%s: slot %s is %s %s's latest slot %s (depends_on.min_gap_min %d)"
                             % (label, slot, where, target.id, latest, depends.min_gap_min))


def parse_manifest_text(text: str, path: str = "<text>", sha256: Optional[str] = None) -> Manifest:
    """The :class:`Manifest` of ``text`` (module notes); :class:`ManifestError` with every problem otherwise."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if sha256 is None else sha256
    try:
        document = toml_compat.loads(text)
    except toml_compat.TomlError as exc:
        raise ManifestError(path, ["not valid TOML: %s" % exc]) from None
    builder = _Builder(document)
    schema = builder.top()
    builder.read_profiles()
    raw_routines = document.get("routine", [])
    built: List[Routine] = []
    if not isinstance(raw_routines, list):
        builder.add("routine must be an array of [[routine]] tables")
    else:
        for index, raw in enumerate(raw_routines):
            routine = builder.routine(index, raw)
            if routine is not None:
                built.append(routine)
    builder.dependencies(built)
    if builder.problems:
        raise ManifestError(path, builder.problems)
    return Manifest(schema_version=schema, profiles=dict(builder.profiles), routines=tuple(built), path=path,
                    sha256=digest)


def _read_file(path: str) -> Tuple[str, str]:
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        raise ManifestError(path, ["missing"]) from None
    except OSError as exc:
        raise ManifestError(path, ["cannot be read: %s" % (exc.strerror or exc)]) from None
    try:
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()
    except UnicodeDecodeError:
        raise ManifestError(path, ["not valid UTF-8"]) from None


def load_manifest(path: Optional[str] = None) -> Manifest:
    """Read and check the manifest: this checkout's ``routines/manifest.toml`` by default, a repository root's when
    ``path`` is a directory, else the file ``path``. :class:`ManifestError` lists every problem."""
    if path is None:
        path = manifest_path()
    elif not isinstance(path, str):
        raise TypeError("load_manifest() takes a path string or None, got %s" % type(path).__name__)
    elif os.path.isdir(path):
        path = manifest_path(path)
    text, digest = _read_file(path)
    return parse_manifest_text(text, path, digest)


def budget(manifest: Manifest, profile: str) -> int:
    """Regular runs per week of ``profile``: the sum over its routines of slots x days."""
    return sum(len(routine.slots(profile)) * len(routine.days) for routine in manifest.routines_for(profile))


# --------------------------------------------------------------------------------------------
# Auto-review texts
# --------------------------------------------------------------------------------------------


def parse_autoreview_text(text: str, path: str = "<text>", sha256: Optional[str] = None) -> Autoreview:
    """The :class:`Autoreview` of ``text``; :class:`ManifestError` with every problem otherwise."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if sha256 is None else sha256
    try:
        document = toml_compat.loads(text)
    except toml_compat.TomlError as exc:
        raise ManifestError(path, ["not valid TOML: %s" % exc]) from None
    problems: List[str] = []
    for key in document:
        if key not in AUTOREVIEW_TOP_KEYS:
            problems.append("unknown top-level key %r (allowed: %s)" % (key, ", ".join(AUTOREVIEW_TOP_KEYS)))
    schema = document.get("schema_version")
    if not _is_int(schema) or schema != SCHEMA_VERSION:
        problems.append("schema_version must be %d (found %r)" % (SCHEMA_VERSION, schema))
    limit = document.get("limit_chars")
    if not _is_int(limit) or limit != ALLOW_LIMIT_CHARS:
        problems.append("limit_chars must be %d (found %r)" % (ALLOW_LIMIT_CHARS, limit))
    texts: Dict[str, str] = {}
    for table_name in AUTOREVIEW_TABLES:
        table = document.get(table_name, {})
        if not isinstance(table, dict):
            problems.append("[%s] must be a table with one key, text" % table_name)
            continue
        for key in table:
            if key != "text":
                problems.append("unknown key %r in [%s]" % (key, table_name))
        value = table.get("text")
        if not _nonblank(value):
            problems.append("[%s].text must be a non-empty string" % table_name)
            continue
        texts[table_name] = value
    allow = texts.get("allow_routines")
    if allow is not None and ROUTINE_IDS_PLACEHOLDER not in allow:
        problems.append("[allow_routines].text must contain %s" % ROUTINE_IDS_PLACEHOLDER)
    if problems:
        raise ManifestError(path, problems)
    return Autoreview(schema_version=schema, limit_chars=limit, allow_template=texts["allow_routines"],
                      ask_first_writes=texts["ask_first_writes"],
                      ask_first_owner_authority=texts["ask_first_owner_authority"], path=path, sha256=digest)


def load_autoreview(path: Optional[str] = None) -> Autoreview:
    """Read and check ``routines/autoreview.toml`` (this checkout's by default, a root's for a directory)."""
    if path is None:
        path = autoreview_path()
    elif not isinstance(path, str):
        raise TypeError("load_autoreview() takes a path string or None, got %s" % type(path).__name__)
    elif os.path.isdir(path):
        path = autoreview_path(path)
    text, digest = _read_file(path)
    return parse_autoreview_text(text, path, digest)


def rules_text(manifest: Manifest, autoreview: Autoreview) -> Dict[str, str]:
    """The three Auto-review texts, rendered: ``{allow, ask_first_writes, ask_first_owner_authority}``."""
    return {
        "allow": autoreview.allow_template.replace(ROUTINE_IDS_PLACEHOLDER, ", ".join(manifest.routine_ids)),
        "ask_first_writes": autoreview.ask_first_writes,
        "ask_first_owner_authority": autoreview.ask_first_owner_authority,
    }


def rules_text_problems(manifest: Manifest, autoreview: Autoreview) -> List[str]:
    """Why the rendered texts cannot be pasted: a text over ``limit_chars``, a routine id the allow text omits."""
    texts = rules_text(manifest, autoreview)
    problems: List[str] = []
    for key, table_name in zip(RULE_KEYS, AUTOREVIEW_TABLES):
        if len(texts[key]) > autoreview.limit_chars:
            problems.append("[%s] renders to %d characters (limit %d)" % (table_name, len(texts[key]),
                                                                        autoreview.limit_chars))
    for routine_id in manifest.routine_ids:
        if not re.search(r"(?<![a-z0-9-])%s(?![a-z0-9-])" % re.escape(routine_id), texts["allow"]):
            problems.append("[allow_routines] does not name routine %s" % routine_id)
    return problems


# --------------------------------------------------------------------------------------------
# The day audit
# --------------------------------------------------------------------------------------------


def day_slots(manifest: Manifest, profile: str, et_date: datetime.date,
              extra_holidays: market_clock.ExtraHolidays = ()) -> Tuple[Slot, ...]:
    """Every slot ``profile`` runs on ``et_date`` (module notes), in time order, then manifest order."""
    day = _as_date(et_date)
    ordered: List[Tuple[int, int, Slot]] = []
    for order, routine in enumerate(manifest.routines_for(profile)):
        if not routine.runs_on(day, extra_holidays):
            continue
        for slot in routine.slots(profile):
            ordered.append((slot_minutes(slot), order, Slot(routine.id, slot, slot_instant(day, slot))))
    return tuple(item[2] for item in sorted(ordered, key=lambda item: (item[0], item[1])))


def _record_start(record: Any, et_date: datetime.date) -> Optional[Tuple[str, str, datetime.datetime, Dict[str, Any]]]:
    if not isinstance(record, dict):
        return None
    routine_id = record.get("routine_id")
    slot = record.get("scheduled_et")
    if not isinstance(routine_id, str) or not isinstance(slot, str) or not heartbeat.SCHEDULED_ET_RE.match(slot):
        return None
    try:
        run_utc = contract.parse_utc_instant(record.get("run_utc"), "run_utc")
    except ValueError:
        return None
    if market_clock.to_et(run_utc).date() != et_date:
        return None
    started = run_utc
    if isinstance(record.get("started_utc"), str):
        try:
            started = contract.parse_utc_instant(record["started_utc"], "started_utc")
        except ValueError:
            started = run_utc
    return routine_id, slot, started, record


def _slot_audit(slot: Slot, found: Sequence[Tuple[datetime.datetime, Dict[str, Any]]]) -> SlotAudit:
    if not found:
        return SlotAudit(slot.routine_id, slot.scheduled_et, slot.scheduled_utc, 0, None, None, None, None, None)
    started, record = min(found, key=lambda item: item[0])
    offset = int((started - slot.scheduled_utc).total_seconds() // 60)
    lateness = record.get("lateness_min")
    if not _is_int(lateness) or lateness < 0:
        lateness = max(offset, 0)
    exit_code = record.get("exit_code")
    status = record.get("status")
    return SlotAudit(slot.routine_id, slot.scheduled_et, slot.scheduled_utc, len(found), started, lateness, offset,
                     exit_code if _is_int(exit_code) else None, status if isinstance(status, str) else None)


def audit_day(manifest: Manifest, profile: str, history_records: Iterable[Any], et_date: datetime.date,
              now: datetime.datetime, extra_holidays: market_clock.ExtraHolidays = (), *,
              exclude: Iterable[str] = (), through_et: Optional[str] = None) -> DayAudit:
    """The :class:`DayAudit` of ``et_date`` at ``now`` (module notes).

    ``history_records`` are heartbeat records (dicts; anything else, or a record without a readable ``run_utc``
    or ``scheduled_et``, is ignored). ``exclude`` names routines left out (the running routine itself);
    ``through_et`` limits the expected slots to those at or before that ``HH:MM`` slot.
    """
    moment = _instant(now, "now")
    day = _as_date(et_date)
    cut = None if through_et is None else slot_minutes(through_et)
    excluded = set(exclude)
    index: Dict[Tuple[str, str], List[Tuple[datetime.datetime, Dict[str, Any]]]] = {}
    for record in history_records:
        parsed = _record_start(record, day)
        if parsed is not None:
            routine_id, slot, started, document = parsed
            index.setdefault((routine_id, slot), []).append((started, document))
    grace = datetime.timedelta(minutes=MISSED_AFTER_MIN)
    expected: List[SlotAudit] = []
    ran: List[SlotAudit] = []
    late: List[SlotAudit] = []
    missed: List[SlotAudit] = []
    for slot in day_slots(manifest, profile, day, extra_holidays):
        if slot.routine_id in excluded:
            continue
        found = index.get((slot.routine_id, slot.scheduled_et), [])
        if cut is not None:
            if slot_minutes(slot.scheduled_et) > cut:
                continue
        elif slot.scheduled_utc > moment and not found:
            continue
        audit = _slot_audit(slot, found)
        expected.append(audit)
        if audit.ran:
            ran.append(audit)
            if audit.lateness_min is not None and audit.lateness_min > LATE_AFTER_MIN:
                late.append(audit)
        elif moment >= slot.scheduled_utc + grace:
            missed.append(audit)
    return DayAudit(tuple(expected), tuple(ran), tuple(late), tuple(missed))


def timezone_offset_suspected(audit: DayAudit) -> Optional[int]:
    """The offset (-120, -60, 60 or 120 minutes) every run of the day sits near, within 10 minutes, when there
    are at least two runs; ``None`` otherwise."""
    offsets = [slot.offset_min for slot in audit.ran if slot.offset_min is not None]
    if len(offsets) < 2:
        return None
    for candidate in TZ_OFFSETS_MIN:
        if all(abs(offset - candidate) <= TZ_OFFSET_TOLERANCE_MIN for offset in offsets):
            return candidate
    return None


__all__ = [
    "ALLOW_LIMIT_CHARS",
    "AUTOREVIEW_REL",
    "AUTOREVIEW_TABLES",
    "Autoreview",
    "BOOK_CHECK_POST_FLAG",
    "BOOK_CHECK_TOOL",
    "CHAIN_TOKEN_RE",
    "DAYS",
    "DELAY_WINDOW_ET",
    "DayAudit",
    "Dependency",
    "FIXTURE_SET_TOOLS",
    "NO_FIXTURE_SET_FLAGS",
    "FORBIDDEN_CHAIN_FLAGS",
    "LATE_AFTER_MIN",
    "MANIFEST_REL",
    "MIN_GAP_MIN",
    "MISSED_AFTER_MIN",
    "Manifest",
    "ManifestError",
    "POSTS",
    "PROMPT_TEMPLATE",
    "Profile",
    "REFUSED_STEP_TOOLS",
    "ROUTINE_IDS_PLACEHOLDER",
    "ROUTINE_OWNED_FLAGS",
    "RULE_KEYS",
    "Routine",
    "SCHEMA_VERSION",
    "STEP_TOOLS",
    "Slot",
    "SlotAudit",
    "WEEKDAYS",
    "audit_day",
    "autoreview_path",
    "budget",
    "day_slots",
    "load_autoreview",
    "load_manifest",
    "manifest_path",
    "parse_autoreview_text",
    "parse_manifest_text",
    "rules_text",
    "rules_text_problems",
    "slot_instant",
    "slot_minutes",
    "step_tokens",
    "step_tool",
    "timezone_offset_suspected",
]
