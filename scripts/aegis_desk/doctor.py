"""The desk doctor's checks: release pin, interpreter, holiday table, token, sandbox, machine role, broker-looking
environment names, desk files, market status, connector readback, heartbeats and the engine selftest. Standard
library only.

New for Aegis Desk (plan sections 9 and 10; PR 2d decision D2-12). ``scripts/desk_doctor.py`` runs these checks
and writes ``state/doctor.json``; this module holds each check as a function of its inputs, so every reason is
tested through the seams the decision names (the environment mapping, whether ``/workspace`` exists, the local UTC
offset, the checkout answer of ``check_grok_template.checkout_state``, the find-spec and token loaders, the sandbox
client) and nothing here imports a gate script, reads the wall clock or writes a file.

Checks
------
Each check is a :class:`Check`: its number and id (:data:`CHECKS`), a status (``PASS``, ``WARN`` or ``FAIL``) and a
one-line reason that never carries a secret.

1. ``release_pin`` (:func:`release_pin_check`): the ``checkout_state`` answer; pinned is PASS, ``development
   checkout (no tag)`` is WARN, every other reason (a dirty tree, HEAD off the tag, no checkout, git missing, git's
   dubious-ownership refusal) is FAIL with that reason; a failing ``check_grok_template.run`` adds
   ``template/grok-bot.json: <first error>`` and FAILs.
2. ``interpreter``: ``sys.version_info`` at or above the floor 3.9 and every name of :data:`DESK_STDLIB_MODULES`
   found by ``importlib.util.find_spec`` (top-level names only: a dotted name would import its parent). The tuple
   is every standard-library module the desk tools load (a test reads the tools' imports by AST and fails on one
   missing here).
3. ``holiday_table``: ``market_clock.table_status`` of the ET date; anything but ``ok`` FAILs (60 days before the
   table expires).
4. ``token_source``: ``tradier_client.load_token`` with ``desk.toml``'s ``token_env`` and ``token_file``; PASS names
   the source, ``no_token`` and ``token_file_mode`` FAIL with the client's message (sources, never the value);
   ``--offline`` reads nothing (WARN).
5. ``sandbox``: a ``production`` host FAILs with no request; ``--offline`` WARNs; otherwise :func:`verify_sandbox`
   asks the sandbox clock once (``GET /v1/markets/clock``): an answer PASSes, 401 or 403 FAIL ``not a sandbox
   token: remove and rotate``, any other failure WARNs with its status.
6. ``machine_role`` (:func:`machine_role`): ``owner_private`` only when ``AEGIS_MACHINE_ROLE`` is exactly
   ``owner_private`` AND ``/workspace`` does not exist, else ``shared`` (``desk.toml`` cannot set it); always PASS.
7. ``broker_env``: environment variable NAMES only (values are never read); a name split on ``_``, ``-`` and its
   camel-case humps (``schwabToken`` is ``SCHWAB``, ``TOKEN``) and upper-cased FAILs when a part is in
   :data:`BROKER_NAME_PARTS`, starts with a broker's name of :data:`BROKER_NAME_PREFIXES` (``SCHWABAPIKEY``), or two
   adjacent parts are in :data:`BROKER_NAME_PAIRS` (``E_TRADE``, ``TD_AMERITRADE``, ``IB_GATEWAY``, and a Tradier
   name that says live, production, brokerage or trading: such a token can trade); the configured ``token_env`` is
   excluded. Generic words match whole parts only, so ``GCM_INTERACTIVE``, ``BORDER_COLOR``, ``RECORDER_PATH`` and
   ``SORTORDER`` pass.
8. ``desk_files``: ``desk.toml`` (``desk_config``; a ``production`` market-data host FAILs here too, with no request,
   so the quick doctor of the pre-open routine agrees with check 5), ``positions.csv`` (file errors FAIL, quarantined
   groups WARN),
   the adopted rulebook (``exit_rules.load_adopted_rulebook``: ``<code>: <first problem>`` FAILs),
   ``research/calendar.toml`` (missing WARNs, unusable FAILs) and, only when the file exists, ``structuring.toml``
   (``structuring_config``: ``structuring.toml loads (sha <8 hex>)`` PASSes, the reader's error FAILs; a missing file
   adds no part, the structuring criteria being optional); the worst status wins and the reason joins the parts.
9. ``market_status``: the session and the America/New_York offset at the run instant, and the local offset at that
   instant; WARN when the local offset is neither UTC nor America/New_York's.
10. ``connector_readback``: ``state/desk_state.json["connector_readback_utc"]`` missing, unreadable, after the run
    instant or older than 30 days WARNs ``re-confirm connected apps and MCP servers``.
11. ``heartbeats``: the cadence profile's slots of the ET date (``routines.audit_day``); WARN names each slot with
    no heartbeat 45 minutes after it, otherwise PASS with the latest heartbeat's age per routine.
12. ``selftest``: five engine sub-checks (:data:`SELFTEST_NAMES`): the BSM golden of record, an implied-volatility
    round trip, the 2026 DST transitions, the vendored TOML reader and the alert lint (a known-good message passes
    and the same message with nothing emitted fails); any failure FAILs ``engine selftest: <which>``.

Modes (:data:`MODE_CHECKS`): ``full`` runs 1-11, ``quick`` 2, 3, 4, 8 and 9, ``selftest`` 2 and 12 (no desk file).
The verdict is FAIL when any check FAILs, else PASS (warnings allowed). :func:`doctor_record` is the
``state/doctor.json`` document the tool writes in the full and quick modes.
"""

from __future__ import annotations

import datetime
import importlib.util
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import (
    alert_lint,
    alerts,
    bsm,
    calendar_events,
    contract,
    desk_config,
    exit_rules,
    heartbeat,
    iv,
    market_clock,
    positions_csv,
    routines,
    signals,
    state,
    structuring_config,
    toml_compat,
    tradier_client,
)
from .desk_paths import DeskPaths
from .grouping import group_positions

PASS: str = "PASS"
WARN: str = "WARN"
FAIL: str = "FAIL"
STATUSES: Tuple[str, ...] = (PASS, WARN, FAIL)
CHECKS: Tuple[Tuple[int, str], ...] = (
    (1, "release_pin"), (2, "interpreter"), (3, "holiday_table"), (4, "token_source"), (5, "sandbox"),
    (6, "machine_role"), (7, "broker_env"), (8, "desk_files"), (9, "market_status"), (10, "connector_readback"),
    (11, "heartbeats"), (12, "selftest"),
)
CHECK_IDS: Dict[int, str] = dict(CHECKS)
MODE_FULL: str = "full"
MODE_QUICK: str = "quick"
MODE_SELFTEST: str = "selftest"
MODES: Tuple[str, ...] = (MODE_FULL, MODE_QUICK, MODE_SELFTEST)
MODE_CHECKS: Dict[str, Tuple[int, ...]] = {MODE_FULL: tuple(range(1, 12)), MODE_QUICK: (2, 3, 4, 8, 9),
                                           MODE_SELFTEST: (2, 12)}
RECORD_MODES: Tuple[str, ...] = (MODE_FULL, MODE_QUICK)
"""The modes that write ``state/doctor.json`` (only when ``desk.toml`` exists at the desk root)."""
DOCTOR_FILE: str = "doctor.json"
RECORD_SCHEMA: int = 1

DESK_STDLIB_MODULES: Tuple[str, ...] = (
    "argparse", "collections", "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal", "errno", "fcntl",
    "functools", "hashlib", "http", "importlib", "io", "json", "math", "numbers", "os", "re", "socket", "ssl", "stat",
    "string", "subprocess", "sys", "tempfile", "time", "traceback", "typing", "unicodedata", "urllib",
)
"""Every top-level standard-library module the desk tools load (``subprocess`` through ``check_grok_template``, the
pin check; ``traceback`` for ``routine.py``'s incident files)."""

TEMPLATE_LABEL: str = "template/grok-bot.json"
DEVELOPMENT_REASON: str = "development checkout (no tag)"
TOKEN_OFFLINE_REASON: str = "not checked offline (offline runs load no credential)"
NO_CONFIG_TOKEN_REASON: str = "not checked: desk.toml cannot be used (see desk_files)"
SANDBOX_PRODUCTION_REASON: str = "production host is outside the guarantee before the market-data proxy"
SANDBOX_OFFLINE_REASON: str = "not verified offline"
SANDBOX_NO_TOKEN_REASON: str = "sandbox not verified: no token (see token_source)"
SANDBOX_NO_CONFIG_REASON: str = "not verified: desk.toml cannot be used (see desk_files)"
SANDBOX_AUTH_REASON: str = "not a sandbox token: remove and rotate"
MACHINE_ROLE_ENV: str = "AEGIS_MACHINE_ROLE"
ROLE_SHARED: str = "shared"
ROLE_OWNER_PRIVATE: str = "owner_private"
WORKSPACE_DIR: str = "/workspace"
BROKER_NAME_PARTS = frozenset({
    "SCHWAB", "IBKR", "ROBINHOOD", "ETRADE", "TASTY", "TASTYTRADE", "TASTYWORKS", "ALPACA", "SNAPTRADE", "FIDELITY",
    "WEBULL", "TRADESTATION", "BROKER", "BROKERAGE", "ORDER", "ORDERS", "INTERACTIVEBROKERS",
})
BROKER_NAME_PREFIXES: Tuple[str, ...] = (
    "SCHWAB", "IBKR", "ROBINHOOD", "ETRADE", "TASTYTRADE", "TASTYWORKS", "ALPACA", "SNAPTRADE", "FIDELITY", "WEBULL",
    "TRADESTATION", "INTERACTIVEBROKERS", "AMERITRADE", "TDAMERITRADE", "COINBASE",
)
"""Brokers' own names: a part that starts with one is broker-looking (``SCHWABAPIKEY``, ``IBKRTOKEN``)."""
BROKER_NAME_PAIRS = frozenset({
    ("INTERACTIVE", "BROKERS"), ("ACCOUNT", "ID"), ("TRADIER", "ACCOUNT"), ("E", "TRADE"), ("TD", "AMERITRADE"),
    ("IB", "GATEWAY"), ("TRADIER", "LIVE"), ("TRADIER", "PROD"), ("TRADIER", "PRODUCTION"), ("TRADIER", "BROKERAGE"),
    ("TRADIER", "TRADING"),
})
CONNECTOR_KEY: str = "connector_readback_utc"
CONNECTOR_MAX_AGE_DAYS: int = 30
CONNECTOR_REASON: str = "re-confirm connected apps and MCP servers"
HEARTBEAT_GRACE_MIN: int = routines.MISSED_AFTER_MIN

# The golden of record of tests/engine/test_golden_price.py (_BASE: S=162.06, K=170.163, tau=1.553, r=0.05, q=0.052,
# sigma=0.226; the call is 13.494231234329277, validated there against QuantLib's analytic European engine).
SELFTEST_BSM_ARGS: Tuple[Any, ...] = ("CALL", 162.06, 170.163, 1.553, 0.05, 0.052, 0.226)
SELFTEST_BSM_GOLDEN: float = 13.494231234329277
SELFTEST_BSM_TOLERANCE: float = 1e-12
SELFTEST_IV_TOLERANCE: float = 1e-10
SELFTEST_IV_SIGMA_TOLERANCE: float = 1e-9
SELFTEST_DST: Tuple[Tuple[datetime.datetime, str], ...] = (
    (datetime.datetime(2026, 3, 8, 6, 59, 59, tzinfo=datetime.timezone.utc), "2026-03-08T01:59:59-05:00"),
    (datetime.datetime(2026, 3, 8, 7, 0, 0, tzinfo=datetime.timezone.utc), "2026-03-08T03:00:00-04:00"),
    (datetime.datetime(2026, 11, 1, 5, 59, 59, tzinfo=datetime.timezone.utc), "2026-11-01T01:59:59-04:00"),
    (datetime.datetime(2026, 11, 1, 6, 0, 0, tzinfo=datetime.timezone.utc), "2026-11-01T01:00:00-05:00"),
)
SELFTEST_TOML: Tuple[str, Dict[str, Any]] = ('a = 1.5\n[b]\nc = "x"\n', {"a": Decimal("1.5"), "b": {"c": "x"}})
SELFTEST_AS_OF: datetime.datetime = datetime.datetime(2026, 11, 3, 19, 31, tzinfo=datetime.timezone.utc)
SELFTEST_NAMES: Tuple[str, ...] = ("bsm golden", "iv round trip", "DST transitions", "TOML reader", "alert lint")


@dataclass(frozen=True)
class Check:
    """One check's result (module notes): ``number`` and ``id`` from :data:`CHECKS`, ``status`` and ``reason``."""

    number: int
    id: str
    status: str
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "number": self.number, "status": self.status, "reason": self.reason}


def check(number: int, status: str, reason: str) -> Check:
    """The :class:`Check` of check ``number``; ``ValueError`` for an unknown number or status."""
    if number not in CHECK_IDS:
        raise ValueError("unknown doctor check number %r" % (number,))
    if status not in STATUSES:
        raise ValueError("a doctor check's status is one of %s, got %r" % (", ".join(STATUSES), status))
    return Check(number, CHECK_IDS[number], status, " ".join(str(reason).split()))


def worst(statuses: Iterable[str]) -> str:
    """FAIL over WARN over PASS (PASS for none)."""
    found = set(statuses)
    for status in (FAIL, WARN):
        if status in found:
            return status
    return PASS


def verdict(checks: Iterable[Check]) -> str:
    """FAIL when any check FAILs, else PASS (warnings allowed)."""
    return FAIL if any(item.status == FAIL for item in checks) else PASS


# --------------------------------------------------------------------------------------------
# 1-3: release pin, interpreter, holiday table
# --------------------------------------------------------------------------------------------


def release_pin_check(checkout: Mapping[str, Any], template: Optional[Mapping[str, Any]] = None) -> Check:
    """Check 1 from a ``check_grok_template.checkout_state`` answer and a ``check_grok_template.run`` result."""
    reason = str(checkout.get("reason") or "the checkout could not be verified")
    if checkout.get("pinned"):
        status = PASS
    elif reason == DEVELOPMENT_REASON:
        status = WARN
    else:
        status = FAIL
    parts = [reason]
    if template is not None and not template.get("ok"):
        errors = [str(error) for error in template.get("errors") or []]
        first = errors[0] if errors else "the template check failed"
        parts.append(first if first.startswith(TEMPLATE_LABEL + ":") else "%s: %s" % (TEMPLATE_LABEL, first))
        status = FAIL
    return check(1, status, "; ".join(parts))


def interpreter_check(version_info: Optional[Sequence[int]] = None,
                      find_spec: Optional[Callable[[str], Any]] = None,
                      modules: Sequence[str] = DESK_STDLIB_MODULES) -> Check:
    """Check 2: the interpreter is at or above the floor and every desk standard-library module is found."""
    running = tuple(sys.version_info[:3] if version_info is None else version_info)
    finder = importlib.util.find_spec if find_spec is None else find_spec
    shown = ".".join(str(part) for part in running)
    floor = ".".join(str(part) for part in contract.PYTHON_FLOOR)
    if running[:2] < tuple(contract.PYTHON_FLOOR):
        return check(2, FAIL, "Python %s is below the desk's floor %s" % (shown, floor))
    missing: List[str] = []
    for name in modules:
        try:
            found = finder(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    if missing:
        return check(2, FAIL, "missing standard-library module(s): %s" % ", ".join(missing))
    return check(2, PASS, "Python %s (floor %s); %d desk standard-library modules found" % (shown, floor, len(modules)))


def holiday_table_check(et_date: datetime.date) -> Check:
    """Check 3: the NYSE holiday table covers ``et_date`` with more than 60 days to spare."""
    status = market_clock.table_status(et_date)
    expires = market_clock.TABLE_EXPIRES.isoformat()
    if status == market_clock.STATUS_EXPIRED:
        return check(3, FAIL, "holiday table expired %s" % expires)
    if status == market_clock.STATUS_EXPIRING_SOON:
        return check(3, FAIL, "holiday table expires %s: update the release" % expires)
    years = market_clock.TABLE_YEARS
    return check(3, PASS, "holiday table covers %d-%d (expires %s)" % (years[0], years[-1], expires))


# --------------------------------------------------------------------------------------------
# 4-5: token and sandbox
# --------------------------------------------------------------------------------------------


def token_source_check(cfg: Optional[desk_config.DeskConfig], offline: bool,
                       load: Optional[Callable[[str, Optional[str]], Any]] = None) -> Tuple[Check, Any]:
    """Check 4 and the token it found (``None`` unless it PASSes)."""
    if offline:
        return check(4, WARN, TOKEN_OFFLINE_REASON), None
    if cfg is None:
        return check(4, WARN, NO_CONFIG_TOKEN_REASON), None
    loader = tradier_client.load_token if load is None else load
    try:
        token = loader(cfg.market_data.token_env, cfg.market_data.token_file)
    except tradier_client.TradierError as exc:
        return check(4, FAIL, exc.message), None
    if token.source == "env":
        return check(4, PASS, "token from environment variable %s" % cfg.market_data.token_env), token
    return check(4, PASS, "token from file %s (no group or other permission bits)" % cfg.market_data.token_file), token


def verify_sandbox(client: Any) -> Check:
    """Check 5's one request: ``client.clock()`` (``GET /v1/markets/clock``)."""
    try:
        answer = client.clock()
    except tradier_client.TradierError as exc:
        if exc.status == "auth":
            return check(5, FAIL, SANDBOX_AUTH_REASON)
        return check(5, WARN, "sandbox not verified: %s" % exc.status)
    return check(5, PASS, "sandbox clock answered (%s)" % answer.state)


def sandbox_check(cfg: Optional[desk_config.DeskConfig], token: Any, offline: bool,
                  client_factory: Callable[[Any], Any]) -> Check:
    """Check 5: refuse production, skip offline, else :func:`verify_sandbox` on ``client_factory(token)``."""
    if cfg is None:
        return check(5, WARN, SANDBOX_NO_CONFIG_REASON)
    if cfg.market_data.host != "sandbox":
        return check(5, FAIL, SANDBOX_PRODUCTION_REASON)
    if offline:
        return check(5, WARN, SANDBOX_OFFLINE_REASON)
    if token is None:
        return check(5, WARN, SANDBOX_NO_TOKEN_REASON)
    return verify_sandbox(client_factory(token))


# --------------------------------------------------------------------------------------------
# 6-7: machine role and broker-looking names
# --------------------------------------------------------------------------------------------


def machine_role(environ: Mapping[str, str], workspace_exists: bool) -> str:
    """``owner_private`` only when the environment says so AND ``/workspace`` does not exist; else ``shared``."""
    if environ.get(MACHINE_ROLE_ENV) == ROLE_OWNER_PRIVATE and not workspace_exists:
        return ROLE_OWNER_PRIVATE
    return ROLE_SHARED


def machine_role_check(role: str) -> Check:
    """Check 6: the role is reported, never failed."""
    return check(6, PASS, "machine role: %s" % role)


_HUMP_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _name_parts(name: str) -> List[str]:
    """``name`` split on ``_`` and ``-`` and at its camel-case humps, upper-cased (``tdAmeritradeKey`` is ``TD``,
    ``AMERITRADE``, ``KEY``)."""
    return [hump.upper() for piece in name.replace("-", "_").split("_") for hump in _HUMP_RE.findall(piece)]


def broker_env_names(names: Iterable[str], token_env: Optional[str]) -> List[str]:
    """The broker-looking names among ``names`` (module notes, check 7), sorted; ``token_env`` is excluded."""
    flagged = []
    for name in names:
        if name == token_env:
            continue
        parts = _name_parts(name)
        if any(part in BROKER_NAME_PARTS or part.startswith(BROKER_NAME_PREFIXES) for part in parts) or any(
                pair in BROKER_NAME_PAIRS for pair in zip(parts, parts[1:])):
            flagged.append(name)
    return sorted(set(flagged))


def broker_env_check(environ: Mapping[str, str], token_env: Optional[str]) -> Check:
    """Check 7 over the NAMES of ``environ`` (the values are never read)."""
    names = list(environ.keys())
    flagged = broker_env_names(names, token_env)
    if flagged:
        return check(7, FAIL, "broker-looking environment variable(s): %s" % ", ".join(flagged))
    return check(7, PASS, "no broker-looking environment variable name (%d names checked)" % len(names))


# --------------------------------------------------------------------------------------------
# 8: desk files
# --------------------------------------------------------------------------------------------


def desk_files_check(paths: DeskPaths, et_date: datetime.date) -> Tuple[Check, Optional[desk_config.DeskConfig],
                                                                         Optional[calendar_events.CalendarFile]]:
    """Check 8 and what it loaded: ``(check, desk config or None, calendar or None)``."""
    parts: List[Tuple[str, str]] = []
    cfg: Optional[desk_config.DeskConfig] = None
    try:
        cfg = desk_config.load_desk_config(paths.desk_toml)
        if cfg.market_data.host != "sandbox":
            parts.append((FAIL, "desk.toml host %s: %s" % (cfg.market_data.host, SANDBOX_PRODUCTION_REASON)))
        else:
            parts.append((PASS, "desk.toml loads (sha %s)" % cfg.sha256[:8]))
    except desk_config.DeskConfigError as exc:
        parts.append((FAIL, str(exc)))
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=et_date)
    except FileNotFoundError:
        parts.append((FAIL, "positions.csv missing"))
    except OSError as exc:
        parts.append((FAIL, "positions.csv cannot be read: %s" % (exc.strerror or exc)))
    else:
        if positions.file_errors:
            parts.append((FAIL, "positions.csv cannot be used: %s" % positions.file_errors[0]))
        else:
            groups = group_positions(positions.rows)
            quarantined = sorted(set(positions.quarantined) | {group.group_id for group in groups
                                                               if group.is_quarantined})
            counted = len({group.group_id for group in groups} | set(positions.quarantined))
            text = "positions.csv parses: %d rows in %d groups" % (positions.row_count, counted)
            if quarantined:
                parts.append((WARN, "%s, %d quarantined (%s)" % (text, len(quarantined), ", ".join(quarantined))))
            else:
                parts.append((PASS, text + ", none quarantined"))
    try:
        _adopted, rulebook = exit_rules.load_adopted_rulebook(paths)
        parts.append((PASS, "rules %s, sha %s" % (rulebook.rulebook_version, rulebook.sha256[:8])))
    except exit_rules.RulebookError as exc:
        parts.append((FAIL, "%s: %s" % (exc.code, exc.problems[0] if exc.problems else exc)))
    except OSError as exc:
        parts.append((FAIL, "the adopted rulebook cannot be read: %s" % (exc.strerror or exc)))
    cal: Optional[calendar_events.CalendarFile] = None
    try:
        cal = calendar_events.load_calendar_if_present(paths.calendar_toml)
        parts.append((WARN, "research/calendar.toml missing") if cal is None else (PASS, "research/calendar.toml loads"))
    except calendar_events.CalendarError as exc:
        parts.append((FAIL, str(exc)))
    except OSError as exc:
        parts.append((FAIL, "research/calendar.toml cannot be read: %s" % (exc.strerror or exc)))
    try:
        criteria = structuring_config.load_structuring_config_if_present(paths.structuring_toml)
        if criteria is not None:
            parts.append((PASS, "structuring.toml loads (sha %s)" % criteria.sha256[:8]))
    except structuring_config.StructuringConfigError as exc:
        parts.append((FAIL, str(exc)))
    return check(8, worst(status for status, _text in parts), "; ".join(text for _status, text in parts)), cfg, cal


# --------------------------------------------------------------------------------------------
# 9-11: market status, connector readback, heartbeats
# --------------------------------------------------------------------------------------------


def format_offset(offset: Any) -> str:
    """``+HH:MM`` for a UTC offset in seconds (or a ``timedelta``)."""
    seconds = int(offset.total_seconds()) if isinstance(offset, datetime.timedelta) else int(offset)
    sign = "-" if seconds < 0 else "+"
    minutes = abs(seconds) // 60
    return "%s%02d:%02d" % (sign, minutes // 60, minutes % 60)


def market_status_check(now: datetime.datetime, local_offset_s: int,
                        extra_holidays: market_clock.ExtraHolidays = ()) -> Check:
    """Check 9: the session at ``now``, America/New_York's offset and the local offset at ``now``."""
    et_offset = market_clock.et_offset(now)
    clock = market_clock.to_et(now).strftime("%H:%M")
    try:
        session = "session %s at %s ET" % (market_clock.session_at(now, extra_holidays=extra_holidays).session, clock)
    except market_clock.TableExpiredError:
        return check(9, WARN, "session unknown at %s ET: the holiday table expired" % clock)
    local = int(local_offset_s)
    if local not in (0, int(et_offset.total_seconds())):
        return check(9, WARN, "local timezone offset %s is neither UTC nor America/New_York (%s); %s"
                     % (format_offset(local), format_offset(et_offset), session))
    return check(9, PASS, "%s; America/New_York offset %s; local offset %s"
                 % (session, format_offset(et_offset), format_offset(local)))


def connector_readback_check(paths: DeskPaths, now: datetime.datetime) -> Check:
    """Check 10: the owner re-confirmed the connected apps and MCP servers within the last 30 days."""
    document, status = state.read_json(paths.desk_state_json)
    where = paths.relative(paths.desk_state_json)
    value = document.get(CONNECTOR_KEY) if status == state.STATUS_OK and isinstance(document, dict) else None
    if value is None:
        return check(10, WARN, "%s (%s records no %s)" % (CONNECTOR_REASON, where, CONNECTOR_KEY))
    try:
        stamp = contract.parse_utc_instant(value, CONNECTOR_KEY)
    except ValueError:
        return check(10, WARN, "%s (%s %s is not an ISO-8601 UTC instant)" % (CONNECTOR_REASON, where, CONNECTOR_KEY))
    age = now - stamp
    if age < datetime.timedelta(0):
        return check(10, WARN, "%s (%s %s is after the run instant)" % (CONNECTOR_REASON, CONNECTOR_KEY, value))
    shown = stamp.date().isoformat()
    if age > datetime.timedelta(days=CONNECTOR_MAX_AGE_DAYS):
        return check(10, WARN, "%s (last confirmed %s, %d days ago)" % (CONNECTOR_REASON, shown, age.days))
    return check(10, PASS, "connected apps and MCP servers confirmed %s (%d days ago)" % (shown, age.days))


def heartbeats_check(paths: DeskPaths, manifest: routines.Manifest, profile: str, now: datetime.datetime,
                     extra_holidays: market_clock.ExtraHolidays = ()) -> Check:
    """Check 11: every slot of ``profile`` due on the ET date of ``now`` has a heartbeat within 45 minutes."""
    et_date = market_clock.to_et(now).date()
    records, status, bad = signals.read_journal(heartbeat.history_path(paths))
    audit = routines.audit_day(manifest, profile, records, et_date, now, extra_holidays)
    if audit.missed:
        return check(11, WARN, "; ".join("%s %s ET: no heartbeat %d min after the slot"
                                         % (slot.routine_id, slot.scheduled_et, HEARTBEAT_GRACE_MIN)
                                         for slot in audit.missed))
    if audit.expected:
        head = "%d of %d due slots ran today (profile %s)" % (len(audit.ran), len(audit.expected), profile)
    else:
        head = "no slot due yet today (profile %s)" % profile
    ages = []
    for record in heartbeat.list_heartbeats(paths):
        if record.get("read_status") != state.STATUS_OK:
            ages.append("%s unreadable" % record.get("routine_id"))
            continue
        try:
            ages.append("%s %d min ago" % (record["routine_id"], heartbeat.heartbeat_age_min(record, now)))
        except heartbeat.HeartbeatError:
            ages.append("%s unreadable" % record.get("routine_id"))
    tail = "latest heartbeats: %s" % ", ".join(ages) if ages else "no heartbeat recorded yet"
    if status == signals.STATUS_PARTIAL and bad:
        tail += "; %s: %d unreadable line(s) skipped" % (paths.relative(heartbeat.history_path(paths)), len(bad))
    return check(11, PASS, "%s; %s" % (head, tail))


# --------------------------------------------------------------------------------------------
# 12: the engine selftest
# --------------------------------------------------------------------------------------------


def _bsm_golden() -> bool:
    return abs(bsm.price(*SELFTEST_BSM_ARGS) - SELFTEST_BSM_GOLDEN) <= SELFTEST_BSM_TOLERANCE


def _iv_round_trip() -> bool:
    right, spot, strike, tau, rate, dividend, sigma = SELFTEST_BSM_ARGS
    value = bsm.price(*SELFTEST_BSM_ARGS)
    found = iv.implied_vol(value, right, spot, strike, tau, rate, dividend)
    if found.status != iv.STATUS_OK or found.sigma is None:
        return False
    repriced = bsm.price(right, spot, strike, tau, rate, dividend, found.sigma)
    return abs(repriced - value) <= SELFTEST_IV_TOLERANCE and abs(found.sigma - sigma) <= SELFTEST_IV_SIGMA_TOLERANCE


def _dst_transitions() -> bool:
    return all(market_clock.to_et(moment).isoformat() == expected for moment, expected in SELFTEST_DST)


def _toml_reader() -> bool:
    text, expected = SELFTEST_TOML
    return toml_compat.loads(text) == expected


def selftest_message(emitted: List[str]) -> str:
    """The embedded known-good message: alerts formatters for every number, the fixed footer at a fixed instant."""
    line = "doctor selftest | %s open groups | unrealized %s (liquidation), %s (mid) | data age: %s" % (
        alerts.fmt_count(3, emitted), alerts.fmt_money(Decimal("4034.00"), emitted),
        alerts.fmt_money(Decimal("4097.00"), emitted), alerts.fmt_minutes(19, emitted))
    return line + "\n" + alert_lint.footer_line(SELFTEST_AS_OF, alert_lint.FOOTER_DELAY_MIN)


def _alert_lint() -> bool:
    emitted: List[str] = []
    text = selftest_message(emitted)
    good = alert_lint.check(text, emitted, identifiers=[], clocks=[SELFTEST_AS_OF.strftime("%H:%M UTC")],
                            as_of=SELFTEST_AS_OF)
    unemitted = alert_lint.check(text, [], as_of=SELFTEST_AS_OF)
    return bool(good.ok) and not unemitted.ok


_SELFTESTS: Tuple[Tuple[str, Callable[[], bool]], ...] = (
    ("bsm golden", _bsm_golden), ("iv round trip", _iv_round_trip), ("DST transitions", _dst_transitions),
    ("TOML reader", _toml_reader), ("alert lint", _alert_lint),
)


def selftest_results() -> List[Tuple[str, bool]]:
    """``(name, passed)`` per sub-check, in :data:`SELFTEST_NAMES` order; an exception is a failure."""
    results: List[Tuple[str, bool]] = []
    for name, probe in _SELFTESTS:
        try:
            passed = bool(probe())
        except Exception:  # noqa: BLE001 - a broken engine is the finding, never a crash of the doctor
            passed = False
        results.append((name, passed))
    return results


def selftest_check(results: Optional[Sequence[Tuple[str, bool]]] = None) -> Check:
    """Check 12 from :func:`selftest_results`."""
    found = list(selftest_results() if results is None else results)
    failed = [name for name, passed in found if not passed]
    if failed:
        return check(12, FAIL, "engine selftest: %s" % ", ".join(failed))
    return check(12, PASS, "engine selftest: %d of %d sub-checks passed (%s)"
                 % (len(found), len(found), ", ".join(name for name, _passed in found)))


# --------------------------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------------------------


def doctor_path(paths: DeskPaths) -> str:
    """``state/doctor.json`` under the desk root."""
    return os.path.join(paths.state_dir, DOCTOR_FILE)


def doctor_record(mode: str, now: datetime.datetime, checks: Sequence[Check], role: str,
                  tool_version: str) -> Dict[str, Any]:
    """The ``state/doctor.json`` document (schema 1)."""
    if mode not in MODES:
        raise ValueError("unknown doctor mode %r" % (mode,))
    return {"schema": RECORD_SCHEMA, "run_utc": state.format_utc(now), "mode": mode, "verdict": verdict(checks),
            "checks": [item.as_dict() for item in checks], "machine_role": role, "tool_version": tool_version}


__all__ = [
    "BROKER_NAME_PAIRS",
    "BROKER_NAME_PARTS",
    "BROKER_NAME_PREFIXES",
    "CHECKS",
    "CHECK_IDS",
    "CONNECTOR_KEY",
    "CONNECTOR_MAX_AGE_DAYS",
    "CONNECTOR_REASON",
    "Check",
    "DESK_STDLIB_MODULES",
    "DOCTOR_FILE",
    "FAIL",
    "HEARTBEAT_GRACE_MIN",
    "MACHINE_ROLE_ENV",
    "MODES",
    "MODE_CHECKS",
    "MODE_FULL",
    "MODE_QUICK",
    "MODE_SELFTEST",
    "PASS",
    "RECORD_MODES",
    "RECORD_SCHEMA",
    "ROLE_OWNER_PRIVATE",
    "ROLE_SHARED",
    "SANDBOX_AUTH_REASON",
    "SANDBOX_OFFLINE_REASON",
    "SANDBOX_PRODUCTION_REASON",
    "SELFTEST_BSM_ARGS",
    "SELFTEST_BSM_GOLDEN",
    "SELFTEST_NAMES",
    "STATUSES",
    "TOKEN_OFFLINE_REASON",
    "WARN",
    "WORKSPACE_DIR",
    "broker_env_check",
    "broker_env_names",
    "check",
    "connector_readback_check",
    "desk_files_check",
    "doctor_path",
    "doctor_record",
    "format_offset",
    "heartbeats_check",
    "holiday_table_check",
    "interpreter_check",
    "machine_role",
    "machine_role_check",
    "market_status_check",
    "release_pin_check",
    "sandbox_check",
    "selftest_check",
    "selftest_message",
    "selftest_results",
    "token_source_check",
    "verdict",
    "verify_sandbox",
    "worst",
]
