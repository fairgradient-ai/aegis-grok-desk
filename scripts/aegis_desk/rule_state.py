"""Rule state: the per-(group, rule) state machine behind the exit rules (plan section 8, the brief's "Rule state").

Standard library only; pure functions over plain JSON-ready dicts, plus :func:`load` / :func:`save` through
:mod:`aegis_desk.state` (``state/rule_state.json``, atomic, under the desk lock). The module never imports
``exit_rules``: the rules engine computes each rule's truth and hands this module the observation and the rule's
policy as plain values (:func:`step`), and the routine applies the resulting transitions (:func:`apply`).

The state dict
--------------
:func:`load` returns ``{"<subject>|<rule_id>": entry}``, the subject being the group id (``AG-...`` or
``ROW-n``), ``underlying:<SYM>`` or ``portfolio``. Every entry has exactly :data:`ENTRY_KEYS`: ``status``
(``armed | pending | fired | cooldown``), ``rule_sha`` (the per-rule canonical sha the entry was observed under),
``observations`` (the TRUE observations still confirming: ``[{as_of, value}]``), ``fired_at``, ``fired_value``,
``last_posted_at``, ``posts``, ``rearm_at``, ``first_observation_at`` and ``opened`` (the group's opened date).
Instants are ISO-8601 UTC strings, dates ISO strings, Decimals strings: an entry is the same before and after a
round trip through the file. On disk the file is ``{"schema_version": 1, "entries": {...}}``.

Transitions (:func:`step`, one observation of one rule on one subject)
---------------------------------------------------------------------
* An entry whose ``rule_sha`` differs from the rule's current sha (the rule changed) or that does not validate
  is started again (event ``reset``). An unseen (group, rule) whose first observation is UNKNOWN records nothing:
  it has not been seen yet. A first FALSE or TRUE records a new ``armed`` entry (event ``seen``).
* ``cooldown`` re-arms first (event ``rearmed``) when ``now >= rearm_at``, or, for ``hysteresis``, at the first
  observation whose value has retreated (:func:`retreated`); the observation is then processed from ``armed``.
* ``armed`` / ``pending`` + TRUE: the observation is recorded (:func:`add_observation`); the rule fires (event
  ``fired``) when it is confirmed (:func:`confirmed`), else it is (or stays) ``pending`` (event ``pending``).
  ``pending`` + FALSE: back to ``armed``, the streak cleared (event ``cleared``). UNKNOWN never moves a state.
* ``fired`` + TRUE: nothing (the signal of record stands, no new transition). ``fired`` + FALSE: ``cooldown``
  (event ``cooldown``) with ``rearm_at`` from :func:`rearm_instant`, and never earlier than the policy's
  ``cooldown_min`` after the entry's last post (``cooldown_min`` is the minimum between two posts of one (group,
  rule), so a rule cannot re-fire inside it). ``cooldown`` + TRUE before the re-arm: nothing (no second signal for
  the same condition inside the re-arm bound; the rules engine reports it as ``cooldown``, never as ``fired``).

Confirmations
-------------
Input class E never waits. M and G rules need ``confirmations`` TRUE observations: an observation is stored only
when its quote ``as_of`` is at least ``confirmation_gap_min`` after the last stored one (a re-run on the same quote,
or one stamped too soon, is not a new confirmation: the earlier stamp stands; an M or G TRUE observation without its
quote stamp is refused with ``ValueError``, because the run's clock would count a re-run as a confirmation), the
stored stamps are pruned to the
``confirmation_window_min`` of the newest, and the latest ``confirmations`` are kept. So the rule fires when the
latest ``confirmations`` observations have stamps pairwise at least the gap apart and all within the window of the
newest, or when a single observation suffices (:func:`single_observation`, the addendum's decision 17(i): the
session is not live, or the caller's ``next_run_utc`` is later than the session's close; ``None`` means another
observation is coming).

Re-arm (bounded to at most one trading day)
-------------------------------------------
``immediate``: ``rearm_at = now`` (the next observation re-arms). ``next_trading_day``: 00:00 ET of the next
trading day after today (the calendar's full closures skipped). ``hysteresis``: the first observation whose value
has retreated by ``hysteresis_pct`` percent of the threshold (``value <= T - |T| x pct / 100`` for a floor rule
``>=``/``>``, ``value >= T + |T| x pct / 100`` for a ceiling rule ``<=``/``<``, a FALSE value for a boolean or
``==`` rule), and never later than the ``next_trading_day`` instant, which is stored as its ``rearm_at``. Every mode
waits for the policy's optional ``cooldown_min`` (:data:`POLICY_OPTIONAL_KEYS`, 0 when absent) after the entry's
``last_posted_at``: the stored ``rearm_at`` is the later of the mode's instant and that minimum, and a hysteresis
retreat re-arms early only once it has passed. The cooldown's hold ends at the latest at the session open (09:30
ET) of the first trading day after the post's ET date, the calendar's closures skipped
(:func:`next_session_open`): a long ``cooldown_min`` never keeps an exit rule silent into the next session, which
is the one-trading-day bound in its safe direction. The ladder's re-posts of a standing signal still wait for the
full ``cooldown_min`` (:func:`may_post`).

First observation
-----------------
When a (group, rule) is first seen already TRUE and the group was opened before today (or its opened date is
unknown: no opened date is never "opened today"), ``first_observation_at`` is stamped; when that streak fires the
step says ``first_observation`` (the alert's sentence "already past this threshold at the desk's first
observation": a real condition is never silently suppressed, and the state then dedupes it like any other). A
FALSE before the fire, or a re-arm, clears the stamp.

Posts and adoption
------------------
:func:`record_post` stamps ``last_posted_at`` and counts ``posts``; :func:`may_post` applies the rule's
``cooldown_min`` between posts of one (group, rule). :func:`carry_forward` keeps the entries whose ``rule_sha``
equals the newly adopted per-rule sha, resets the others (it lists them in ``reset`` and leaves them out of the
state, exactly as :func:`step` starts a changed rule again: the next observation is the desk's first of the new
rule, so a condition already past the new threshold carries ``first_observation``) and drops the entries of rules
the new rulebook no longer holds. :func:`apply` writes the transitions of an evaluation into a new state dict (it
never changes its input) and re-arms untouched ``cooldown`` entries whose ``rearm_at`` has passed, exactly as the
next :func:`step` would.
"""

from __future__ import annotations

import copy
import datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import market_clock
from . import state as state_io
from .desk_paths import DeskPaths
from .market_clock import SessionInfo

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

SCHEMA_VERSION: int = 1
KEY_SEPARATOR: str = "|"

STATUS_ARMED: str = "armed"
STATUS_PENDING: str = "pending"
STATUS_FIRED: str = "fired"
STATUS_COOLDOWN: str = "cooldown"
STATUSES: Tuple[str, ...] = (STATUS_ARMED, STATUS_PENDING, STATUS_FIRED, STATUS_COOLDOWN)

REARM_NEXT_TRADING_DAY: str = "next_trading_day"
REARM_IMMEDIATE: str = "immediate"
REARM_HYSTERESIS: str = "hysteresis"
REARM_MODES: Tuple[str, ...] = (REARM_NEXT_TRADING_DAY, REARM_IMMEDIATE, REARM_HYSTERESIS)

TRUTH_TRUE: str = "true"
TRUTH_FALSE: str = "false"
TRUTH_UNKNOWN: str = "unknown"
TRUTHS: Tuple[str, ...] = (TRUTH_TRUE, TRUTH_FALSE, TRUTH_UNKNOWN)

INPUT_CLASS_E: str = "E"
INPUT_CLASSES: Tuple[str, ...] = ("E", "M", "G")

EVENT_RESET: str = "reset"
EVENT_SEEN: str = "seen"
EVENT_REARMED: str = "rearmed"
EVENT_PENDING: str = "pending"
EVENT_FIRED: str = "fired"
EVENT_CLEARED: str = "cleared"
EVENT_COOLDOWN: str = "cooldown"
EVENTS: Tuple[str, ...] = (EVENT_RESET, EVENT_SEEN, EVENT_REARMED, EVENT_PENDING, EVENT_FIRED, EVENT_CLEARED,
                           EVENT_COOLDOWN)

ENTRY_KEYS: Tuple[str, ...] = ("status", "rule_sha", "observations", "fired_at", "fired_value", "last_posted_at",
                               "posts", "rearm_at", "first_observation_at", "opened")
POLICY_KEYS: Tuple[str, ...] = ("input_class", "confirmations", "confirmation_gap_min", "confirmation_window_min",
                                "rearm", "hysteresis_pct", "op", "threshold")
POLICY_OPTIONAL_KEYS: Tuple[str, ...] = ("cooldown_min",)
"""Policy keys :func:`step` reads when present: ``cooldown_min`` (minutes, 0 when absent) holds a re-arm back."""
UPDATE_KEYS: Tuple[str, ...] = ("key", "subject", "rule_id", "rule_sha", "before", "after", "event", "events", "truth",
                                "as_of", "value", "fired", "first_observation", "entry")
FLOOR_OPS: Tuple[str, ...] = (">=", ">")
CEILING_OPS: Tuple[str, ...] = ("<=", "<")

LOAD_OK: str = "ok"
LOAD_MISSING: str = state_io.STATUS_MISSING
LOAD_CORRUPT: str = state_io.STATUS_CORRUPT
LOAD_MALFORMED: str = "malformed"
LOAD_PARTIAL: str = "partial"
LOAD_STATUSES: Tuple[str, ...] = (LOAD_OK, LOAD_MISSING, LOAD_CORRUPT, LOAD_MALFORMED, LOAD_PARTIAL)

_HUNDRED = Decimal(100)


# --------------------------------------------------------------------------------------------
# Keys, values, instants
# --------------------------------------------------------------------------------------------


def state_key(subject: str, rule_id: str) -> str:
    """``"<subject>|<rule_id>"``; neither part may contain the separator."""
    if not subject or not rule_id or KEY_SEPARATOR in subject or KEY_SEPARATOR in rule_id:
        raise ValueError("a state key needs a subject and a rule id without %r: %r, %r" % (KEY_SEPARATOR, subject, rule_id))
    return subject + KEY_SEPARATOR + rule_id


def split_key(key: str) -> Tuple[str, str]:
    """``(subject, rule_id)`` of a state key."""
    subject, separator, rule_id = str(key).rpartition(KEY_SEPARATOR)
    if not separator or not subject or not rule_id:
        raise ValueError("not a rule-state key: %r" % (key,))
    return subject, rule_id


def iso_instant(moment: Optional[datetime.datetime]) -> Optional[str]:
    """An aware instant as the state file stores it (ISO-8601 UTC); ``None`` stays ``None``."""
    if moment is None:
        return None
    return state_io.format_utc(moment)


def parse_instant(text: Any) -> Optional[datetime.datetime]:
    """The aware instant of a stored string, or ``None`` (absent, not a string, naive or malformed)."""
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(UTC)


def _parse_date(text: Any) -> Optional[datetime.date]:
    if not isinstance(text, str):
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def jsonable(value: Any) -> Any:
    """A metric value as the state stores it: Decimal -> string, bool / int / string / None as they are."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    raise ValueError("a rule-state value must be a Decimal, an int, a bool, a string or None, got %r" % (value,))


def _decimal_value(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return result if result.is_finite() else None
    return None


def _require_aware(moment: Any, what: str) -> datetime.datetime:
    if not isinstance(moment, datetime.datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("%s must be an aware datetime, got %r" % (what, moment))
    return moment.astimezone(UTC)


# --------------------------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------------------------


def new_entry(rule_sha: str, opened: Optional[datetime.date]) -> Dict[str, Any]:
    """A fresh ``armed`` entry with exactly :data:`ENTRY_KEYS`."""
    return {
        "status": STATUS_ARMED,
        "rule_sha": rule_sha,
        "observations": [],
        "fired_at": None,
        "fired_value": None,
        "last_posted_at": None,
        "posts": 0,
        "rearm_at": None,
        "first_observation_at": None,
        "opened": opened.isoformat() if opened is not None else None,
    }


def entry_problem(entry: Any) -> Optional[str]:
    """Why ``entry`` is not a usable state entry, or ``None`` when it is."""
    if not isinstance(entry, Mapping):
        return "an entry must be an object"
    if tuple(sorted(entry)) != tuple(sorted(ENTRY_KEYS)):
        return "an entry must have exactly the keys %s" % ", ".join(ENTRY_KEYS)
    if entry["status"] not in STATUSES:
        return "status %r is not one of %s" % (entry["status"], ", ".join(STATUSES))
    if not isinstance(entry["rule_sha"], str) or not entry["rule_sha"]:
        return "rule_sha must be a non-empty string"
    posts = entry["posts"]
    if isinstance(posts, bool) or not isinstance(posts, int) or posts < 0:
        return "posts must be a non-negative integer"
    for key in ("fired_at", "last_posted_at", "rearm_at", "first_observation_at"):
        if entry[key] is not None and parse_instant(entry[key]) is None:
            return "%s must be an ISO-8601 instant with a zone or null" % key
    if entry["opened"] is not None and _parse_date(entry["opened"]) is None:
        return "opened must be an ISO date or null"
    if entry["status"] == STATUS_COOLDOWN and entry["rearm_at"] is None:
        return "a cooldown entry needs rearm_at"
    observations = entry["observations"]
    if not isinstance(observations, list):
        return "observations must be a list"
    for item in observations:
        if not (isinstance(item, Mapping) and set(item) == {"as_of", "value"} and parse_instant(item["as_of"]) is not None):
            return "every observation must be {as_of, value} with an ISO-8601 instant"
    return None


# --------------------------------------------------------------------------------------------
# Policy pieces
# --------------------------------------------------------------------------------------------


def single_observation(session: SessionInfo, next_run_utc: Optional[datetime.datetime]) -> bool:
    """Decision 17(i): one observation suffices when the session is not live, or when the next run comes after the
    session's close; ``next_run_utc`` ``None`` means another observation is coming (module notes)."""
    if not session.is_live:
        return True
    if next_run_utc is None or session.close_utc is None:
        return False
    return _require_aware(next_run_utc, "next_run_utc") > session.close_utc


def check_next_run(next_run_utc: datetime.datetime, now: datetime.datetime) -> datetime.datetime:
    """The caller's ``--next-run-utc``, which must be later than the run's instant ``now`` (a next run in the past
    would promise an observation that is never coming); ``ValueError`` otherwise. Every tool that takes the flag
    (``book_check``, ``exit_check``, ``rules_diff``) checks it here, so they refuse the same values."""
    next_run = _require_aware(next_run_utc, "--next-run-utc")
    if next_run <= _require_aware(now, "now"):
        raise ValueError("--next-run-utc must be later than the run instant %s" % state_io.format_utc(now))
    return next_run


def next_trading_midnight(today: datetime.date, extra_holidays: market_clock.ExtraHolidays = ()) -> datetime.datetime:
    """00:00 America/New_York of the first trading day after ``today``, as an aware UTC instant."""
    day = market_clock.next_trading_day(today, extra_holidays)
    return market_clock.from_et(datetime.datetime(day.year, day.month, day.day, 0, 0))


def next_session_open(moment: datetime.datetime, extra_holidays: market_clock.ExtraHolidays = ()) -> datetime.datetime:
    """09:30 America/New_York of the first trading day after ``moment``'s ET date, as an aware UTC instant: the
    latest end of a cooldown's hold (module notes)."""
    day = market_clock.next_trading_day(market_clock.to_et(_require_aware(moment, "moment")).date(), extra_holidays)
    return market_clock.from_et(datetime.datetime.combine(day, market_clock.REGULAR_OPEN_ET))


def rearm_instant(rearm: str, now: datetime.datetime, today: datetime.date,
                  extra_holidays: market_clock.ExtraHolidays = ()) -> datetime.datetime:
    """The ``rearm_at`` a ``fired -> cooldown`` transition stores (module notes): ``now`` for ``immediate``, the next
    trading day's 00:00 ET for ``next_trading_day`` and, as the one-trading-day bound, for ``hysteresis``."""
    if rearm not in REARM_MODES:
        raise ValueError("rearm must be one of %s, got %r" % (", ".join(REARM_MODES), rearm))
    now = _require_aware(now, "now")
    if rearm == REARM_IMMEDIATE:
        return now
    return next_trading_midnight(today, extra_holidays)


def retreated(value: Any, op: str, threshold: Optional[Decimal], hysteresis_pct: Decimal) -> bool:
    """True when ``value`` has retreated from ``threshold`` by ``hysteresis_pct`` percent of it (module notes)."""
    if op in FLOOR_OPS or op in CEILING_OPS:
        number = _decimal_value(value)
        if number is None or threshold is None:
            return False
        margin = abs(threshold) * Decimal(hysteresis_pct) / _HUNDRED
        if op in FLOOR_OPS:
            return number <= threshold - margin
        return number >= threshold + margin
    return value is False


def add_observation(observations: Sequence[Mapping[str, Any]], as_of: datetime.datetime, value: Any, gap_min: int,
                    window_min: int, keep: int) -> List[Dict[str, Any]]:
    """The stored observations after a TRUE observation stamped ``as_of`` (module notes, "Confirmations")."""
    stamped: List[Tuple[datetime.datetime, Dict[str, Any]]] = []
    for item in observations:
        moment = parse_instant(item.get("as_of")) if isinstance(item, Mapping) else None
        if moment is not None:
            stamped.append((moment, {"as_of": iso_instant(moment), "value": item.get("value")}))
    stamped.sort(key=lambda pair: pair[0])
    as_of = _require_aware(as_of, "as_of")
    newest = stamped[-1][0] if stamped else None
    if newest is None or as_of >= newest + datetime.timedelta(minutes=gap_min):
        stamped.append((as_of, {"as_of": iso_instant(as_of), "value": jsonable(value)}))
    newest = stamped[-1][0]
    window = datetime.timedelta(minutes=window_min)
    kept = [item for moment, item in stamped if newest - moment <= window]
    return kept[-max(1, keep):]


def confirmed(observations: Sequence[Mapping[str, Any]], policy: Mapping[str, Any], single: bool) -> bool:
    """True when the stored observations confirm the rule (module notes)."""
    if single or policy["input_class"] == INPUT_CLASS_E:
        return True
    return len(observations) >= int(policy["confirmations"])


def _check_policy(policy: Mapping[str, Any]) -> None:
    missing = [key for key in POLICY_KEYS if key not in policy]
    if missing:
        raise ValueError("the rule policy lacks %s" % ", ".join(missing))
    if policy["input_class"] not in INPUT_CLASSES:
        raise ValueError("input_class must be one of %s, got %r" % (", ".join(INPUT_CLASSES), policy["input_class"]))
    if policy["rearm"] not in REARM_MODES:
        raise ValueError("rearm must be one of %s, got %r" % (", ".join(REARM_MODES), policy["rearm"]))
    for key in ("confirmations", "confirmation_gap_min", "confirmation_window_min"):
        value = policy[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("%s must be a positive integer, got %r" % (key, value))
    cooldown = policy.get("cooldown_min", 0)
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
        raise ValueError("cooldown_min must be a non-negative integer, got %r" % (cooldown,))


def _post_cooldown_end(entry: Mapping[str, Any], policy: Mapping[str, Any],
                       extra_holidays: market_clock.ExtraHolidays = ()) -> Optional[datetime.datetime]:
    """The end of the cooldown's hold: ``cooldown_min`` after the entry's last post, at the latest the next
    session's open (:func:`next_session_open`); ``None``: never posted, or no cooldown."""
    last = parse_instant(entry.get("last_posted_at"))
    minutes = int(policy.get("cooldown_min", 0))
    if last is None or minutes <= 0:
        return None
    return min(last + datetime.timedelta(minutes=minutes), next_session_open(last, extra_holidays))


# --------------------------------------------------------------------------------------------
# One step
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One observation's transition (module notes). ``entry`` is the new entry (``None`` when nothing is recorded:
    an unseen rule observed UNKNOWN); ``before`` / ``after`` the statuses (``None`` for no entry); ``events`` in
    order; ``fired`` when this step moved the rule into ``fired``; ``first_observation`` when that fire belongs to
    a streak TRUE since the desk first saw the (group, rule) on a position opened before today."""

    entry: Optional[Dict[str, Any]]
    before: Optional[str]
    after: Optional[str]
    events: Tuple[str, ...]
    fired: bool
    first_observation: bool

    @property
    def event(self) -> Optional[str]:
        """The last event, or ``None`` when nothing moved."""
        return self.events[-1] if self.events else None


def step(entry: Optional[Mapping[str, Any]], *, truth: str, as_of: Optional[datetime.datetime], value: Any,
         policy: Mapping[str, Any], now: datetime.datetime, today: datetime.date, single_observation: bool,
         opened: Optional[datetime.date], rule_sha: str, extra_holidays: market_clock.ExtraHolidays = ()) -> Step:
    """The transition of ``entry`` under one observation (module notes). ``truth`` is one of :data:`TRUTHS`,
    ``as_of`` the observation's quote stamp (``now`` only for an E rule, which reads no quote; an M or G TRUE
    observation without one is ``ValueError``), ``value`` the rule's metric value, ``policy`` the rule's
    :data:`POLICY_KEYS` (and :data:`POLICY_OPTIONAL_KEYS`), ``today`` the ET date, ``single_observation`` the outcome
    of :func:`single_observation`, ``opened`` the group's opened date and ``rule_sha`` the rule's current sha.
    Pure: ``entry`` is never changed."""
    if truth not in TRUTHS:
        raise ValueError("truth must be one of %s, got %r" % (", ".join(TRUTHS), truth))
    _check_policy(policy)
    now = _require_aware(now, "now")
    if truth == TRUTH_TRUE and as_of is None and policy["input_class"] != INPUT_CLASS_E:
        raise ValueError("an input-class %s TRUE observation needs its quote stamp: the run's clock would count a "
                         "re-run on the same quote as a confirmation" % policy["input_class"])
    stamp = _require_aware(as_of, "as_of") if as_of is not None else now
    jsonable(value)
    events: List[str] = []
    current: Optional[Dict[str, Any]] = None
    if entry is not None:
        if entry_problem(entry) is None and entry["rule_sha"] == rule_sha:
            current = copy.deepcopy(dict(entry))
        else:
            events.append(EVENT_RESET)
    before = current["status"] if current is not None else None
    if current is None:
        if truth == TRUTH_UNKNOWN:
            return Step(None, None, None, tuple(events), False, False)
        current = new_entry(rule_sha, opened)
        events.append(EVENT_SEEN)
        if truth == TRUTH_TRUE and (opened is None or opened < today):
            current["first_observation_at"] = iso_instant(now)
    elif opened is not None:
        current["opened"] = opened.isoformat()
    if current["status"] == STATUS_COOLDOWN:
        rearm_at = parse_instant(current["rearm_at"])
        threshold = _decimal_value(policy["threshold"])
        held_until = _post_cooldown_end(current, policy, extra_holidays)
        if (rearm_at is not None and now >= rearm_at) or (
                policy["rearm"] == REARM_HYSTERESIS and (held_until is None or now >= held_until)
                and retreated(value, policy["op"], threshold, Decimal(policy["hysteresis_pct"]))):
            current.update(status=STATUS_ARMED, observations=[], rearm_at=None, first_observation_at=None)
            events.append(EVENT_REARMED)
    fired = False
    first = False
    status = current["status"]
    if truth == TRUTH_TRUE and status in (STATUS_ARMED, STATUS_PENDING):
        observations = add_observation(current["observations"], stamp, value, int(policy["confirmation_gap_min"]),
                                       int(policy["confirmation_window_min"]), int(policy["confirmations"]))
        current["observations"] = observations
        if confirmed(observations, policy, single_observation):
            first = current["first_observation_at"] is not None
            current.update(status=STATUS_FIRED, fired_at=iso_instant(now), fired_value=jsonable(value))
            events.append(EVENT_FIRED)
            fired = True
        elif status == STATUS_ARMED:
            current["status"] = STATUS_PENDING
            events.append(EVENT_PENDING)
    elif truth == TRUTH_FALSE:
        if status == STATUS_PENDING:
            current.update(status=STATUS_ARMED, observations=[], first_observation_at=None)
            events.append(EVENT_CLEARED)
        elif status == STATUS_ARMED:
            current["observations"] = []
        elif status == STATUS_FIRED:
            rearm = rearm_instant(policy["rearm"], now, today, extra_holidays)
            held_until = _post_cooldown_end(current, policy, extra_holidays)
            if held_until is not None and held_until > rearm:
                rearm = held_until
            current.update(status=STATUS_COOLDOWN, observations=[], rearm_at=iso_instant(rearm))
            events.append(EVENT_COOLDOWN)
    return Step(current, before, current["status"], tuple(events), fired, first)


def update_record(subject: str, rule_id: str, result: Step, *, truth: str, as_of: Optional[datetime.datetime],
                  value: Any) -> Dict[str, Any]:
    """The JSON-ready transition record :func:`apply` consumes (exactly :data:`UPDATE_KEYS`)."""
    entry = copy.deepcopy(result.entry) if result.entry is not None else None
    return {
        "key": state_key(subject, rule_id),
        "subject": subject,
        "rule_id": rule_id,
        "rule_sha": entry["rule_sha"] if entry is not None else None,
        "before": result.before,
        "after": result.after,
        "event": result.event,
        "events": list(result.events),
        "truth": truth,
        "as_of": iso_instant(as_of),
        "value": jsonable(value),
        "fired": result.fired,
        "first_observation": result.first_observation,
        "entry": entry,
    }


def apply(state: Mapping[str, Any], updates: Iterable[Mapping[str, Any]], now: datetime.datetime) -> Dict[str, Any]:
    """A new state dict: ``state`` with every update's entry written (an update without an entry records nothing),
    and every untouched ``cooldown`` entry whose ``rearm_at`` is at or before ``now`` re-armed (module notes).
    ``state`` is never changed."""
    now = _require_aware(now, "now")
    out: Dict[str, Any] = copy.deepcopy(dict(state))
    touched = set()
    for update in updates:
        if not isinstance(update, Mapping) or "key" not in update or "entry" not in update:
            raise ValueError("a rule-state update needs key and entry: %r" % (update,))
        split_key(update["key"])
        entry = update["entry"]
        if entry is None:
            continue
        problem = entry_problem(entry)
        if problem is not None:
            raise ValueError("update %s: %s" % (update["key"], problem))
        out[update["key"]] = copy.deepcopy(dict(entry))
        touched.add(update["key"])
    for key, entry in out.items():
        if key in touched or not isinstance(entry, dict) or entry.get("status") != STATUS_COOLDOWN:
            continue
        rearm_at = parse_instant(entry.get("rearm_at"))
        if rearm_at is not None and now >= rearm_at:
            entry.update(status=STATUS_ARMED, observations=[], rearm_at=None, first_observation_at=None)
    return out


@dataclass(frozen=True)
class CarryForward:
    """:func:`carry_forward`'s outcome: the new state and the keys kept, reset and dropped (each sorted)."""

    state: Dict[str, Any]
    kept: List[str]
    reset: List[str]
    dropped: List[str]


def carry_forward(state: Mapping[str, Any], rules_sha: Mapping[str, str]) -> CarryForward:
    """Carry the state across an adoption (module notes): same per-rule sha kept; a changed rule reset (named in
    ``reset`` and left out of the state, so its next observation is a first observation exactly as after
    :func:`step`'s own reset); rules no longer held dropped. ``state`` is never changed."""
    out: Dict[str, Any] = {}
    kept: List[str] = []
    reset: List[str] = []
    dropped: List[str] = []
    for key in sorted(state):
        entry = state[key]
        try:
            _, rule_id = split_key(key)
        except ValueError:
            dropped.append(key)
            continue
        if rule_id not in rules_sha:
            dropped.append(key)
            continue
        if isinstance(entry, Mapping) and entry_problem(entry) is None and entry["rule_sha"] == rules_sha[rule_id]:
            out[key] = copy.deepcopy(dict(entry))
            kept.append(key)
            continue
        reset.append(key)
    return CarryForward(out, kept, reset, dropped)


def record_post(state: Mapping[str, Any], key: str, now: datetime.datetime) -> Dict[str, Any]:
    """A new state dict with ``key``'s post recorded (``last_posted_at = now``, ``posts + 1``); ``KeyError`` when the
    state has no such entry."""
    now = _require_aware(now, "now")
    if key not in state:
        raise KeyError(key)
    out: Dict[str, Any] = copy.deepcopy(dict(state))
    entry = out[key]
    entry["last_posted_at"] = iso_instant(now)
    entry["posts"] = int(entry.get("posts") or 0) + 1
    return out


def may_post(entry: Optional[Mapping[str, Any]], now: datetime.datetime, cooldown_min: int) -> bool:
    """True when ``cooldown_min`` minutes have passed since the entry's last post (or it never posted)."""
    now = _require_aware(now, "now")
    if entry is None:
        return True
    last = parse_instant(entry.get("last_posted_at"))
    if last is None:
        return True
    return now - last >= datetime.timedelta(minutes=int(cooldown_min))


# --------------------------------------------------------------------------------------------
# The state file
# --------------------------------------------------------------------------------------------


def document(entries: Mapping[str, Any]) -> Dict[str, Any]:
    """The on-disk shape: ``{"schema_version": 1, "entries": {...}}``."""
    return {"schema_version": SCHEMA_VERSION, "entries": copy.deepcopy(dict(entries))}


def load_with_status(paths: DeskPaths) -> Tuple[Dict[str, Any], str]:
    """``(entries, status)`` of ``state/rule_state.json``: ``ok``; ``missing`` (no file: every rule starts armed);
    ``corrupt`` (not JSON: ``state.read_json`` quarantined it); ``malformed`` (JSON of another shape); ``partial``
    (some entries did not validate and were dropped, so those rules start armed again). A corrupt, malformed or
    dropped entry is never guessed at: it is observed afresh, and first-observation semantics keep a real condition
    from being silently suppressed."""
    raw, status = state_io.read_json(paths.rule_state_json)
    if status != state_io.STATUS_OK:
        return {}, status
    if not (isinstance(raw, dict) and raw.get("schema_version") == SCHEMA_VERSION and isinstance(raw.get("entries"), dict)
            and set(raw) == {"schema_version", "entries"}):
        return {}, LOAD_MALFORMED
    entries: Dict[str, Any] = {}
    dropped = False
    for key, entry in raw["entries"].items():
        try:
            split_key(key)
        except ValueError:
            dropped = True
            continue
        if entry_problem(entry) is not None:
            dropped = True
            continue
        entries[key] = entry
    return entries, (LOAD_PARTIAL if dropped else LOAD_OK)


def load(paths: DeskPaths) -> Dict[str, Any]:
    """The state dict (module notes); :func:`load_with_status` says how it was read."""
    return load_with_status(paths)[0]


def save(paths: DeskPaths, entries: Mapping[str, Any]) -> str:
    """Write ``entries`` to ``state/rule_state.json`` atomically under the desk lock (``state.LockBusy`` when another
    routine holds it; the lock is not re-entrant, so the caller must not hold it); returns the path."""
    for key, entry in entries.items():
        split_key(key)
        problem = entry_problem(entry)
        if problem is not None:
            raise ValueError("entry %s: %s" % (key, problem))
    with state_io.locked(paths.lock_path):
        state_io.atomic_write_json(paths.rule_state_json, document(entries))
    return paths.rule_state_json


__all__ = [
    "CEILING_OPS",
    "ENTRY_KEYS",
    "EVENTS",
    "EVENT_CLEARED",
    "EVENT_COOLDOWN",
    "EVENT_FIRED",
    "EVENT_PENDING",
    "EVENT_REARMED",
    "EVENT_RESET",
    "EVENT_SEEN",
    "FLOOR_OPS",
    "INPUT_CLASSES",
    "INPUT_CLASS_E",
    "KEY_SEPARATOR",
    "LOAD_CORRUPT",
    "LOAD_MALFORMED",
    "LOAD_MISSING",
    "LOAD_OK",
    "LOAD_PARTIAL",
    "LOAD_STATUSES",
    "POLICY_KEYS",
    "POLICY_OPTIONAL_KEYS",
    "REARM_HYSTERESIS",
    "REARM_IMMEDIATE",
    "REARM_MODES",
    "REARM_NEXT_TRADING_DAY",
    "SCHEMA_VERSION",
    "STATUSES",
    "STATUS_ARMED",
    "STATUS_COOLDOWN",
    "STATUS_FIRED",
    "STATUS_PENDING",
    "TRUTHS",
    "TRUTH_FALSE",
    "TRUTH_TRUE",
    "TRUTH_UNKNOWN",
    "UPDATE_KEYS",
    "CarryForward",
    "Step",
    "add_observation",
    "apply",
    "carry_forward",
    "check_next_run",
    "confirmed",
    "document",
    "entry_problem",
    "iso_instant",
    "jsonable",
    "load",
    "load_with_status",
    "may_post",
    "new_entry",
    "next_session_open",
    "next_trading_midnight",
    "parse_instant",
    "rearm_instant",
    "record_post",
    "retreated",
    "save",
    "single_observation",
    "split_key",
    "state_key",
    "step",
    "update_record",
]
