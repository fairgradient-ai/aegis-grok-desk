#!/usr/bin/env python3
"""desk_doctor.py: is this machine, checkout and desk root fit to run the desk? Twelve checks, one verdict.

Usage::

    python3 scripts/desk_doctor.py [--quick | --selftest] [--json] [--schema] [--timeout S] [--desk-root DIR]
        [--offline] [--now ISO-8601-UTC]

The checks are ``aegis_desk.doctor``'s (plan sections 9 and 10; PR 2d decision D2-12), each PASS, WARN or FAIL with a
one-line reason: 1 ``release_pin`` (this checkout is the clean commit of tag ``v<version>``:
``check_grok_template.checkout_state``, then the template hashes, ``check_grok_template.run``; a development checkout
WARNs), 2 ``interpreter``, 3 ``holiday_table``, 4 ``token_source`` (names the source, never the value), 5 ``sandbox``
(ONE ``GET /v1/markets/clock`` on the sandbox host; a 401 or 403 is ``not a sandbox token: remove and rotate``; a
``production`` host FAILs with no request), 6 ``machine_role``, 7 ``broker_env`` (variable NAMES only),
8 ``desk_files`` (``desk.toml``, ``positions.csv``, the adopted rulebook, ``research/calendar.toml``),
9 ``market_status``, 10 ``connector_readback``, 11 ``heartbeats`` (the cadence profile's slots of today in
``routines/manifest.toml`` against ``state/heartbeat/history.jsonl``) and 12 ``selftest`` (the engine subset).

Modes: the default (``full``) runs checks 1-11; ``--quick`` runs 2, 3, 4, 8 and 9 (the pre-open routine's step);
``--selftest`` runs 2 and 12 and reads no desk file. ``--offline`` sends no request and reads no credential
(checks 4 and 5 WARN). The verdict is FAIL when any check FAILs, else PASS (warnings allowed).

The record: the full and quick modes write ``state/doctor.json`` = ``{schema 1, run_utc, mode, verdict, checks
[{id, number, status, reason}], machine_role, tool_version}`` atomically, and only when ``desk.toml`` exists at the
desk root (the Morning Desk Sheet reads it). The selftest writes nothing. Nothing else is written, except what the
state layer's readers already do to a corrupt record (``state/desk_state.json`` or a heartbeat is moved aside).

Exit codes: 0 PASS; 20 FAIL (a success envelope with status ``unavailable`` and every check in the payload); 1 when
the doctor itself failed (``desk_root``, ``io_error``); 2 on usage. ``--json`` payload: ``{mode, verdict, checks,
counts {pass, warn, fail}, warned, failed, machine_role, release, record, written}``.

Test seams (module attributes, ``None`` meaning the real thing): ``CHECKOUT_RUN`` (the ``run`` of
``checkout_state``), ``TEMPLATE_RUN`` (in place of ``check_grok_template.run``), ``ENVIRON`` (the mapping checks 6 and 7 read, ``os.environ``), ``WORKSPACE_EXISTS`` (whether
``/workspace`` exists), ``LOCAL_OFFSET_S`` (the local UTC offset at the run instant, ``time.localtime``) and
``SANDBOX_CLIENT`` (``(token, timeout_s, user_agent) -> client`` for check 5's one request).
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="desk_doctor")

import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence  # noqa: E402

from aegis_desk import calendar_events, desk_config, desk_paths, doctor, market_clock, routines, state, tradier_client, version  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

import check_grok_template  # noqa: E402

TOOL = "desk_doctor"
DESCRIPTION = ("the desk doctor: release pin, interpreter, holiday table, token source, sandbox, machine role, "
               "broker-looking environment names, desk files, market status, connector readback, heartbeats and the "
               "engine selftest; PASS / WARN / FAIL per check, one verdict, state/doctor.json")
NETWORK_ACCESS = ("GET https://sandbox.tradier.com/v1/markets/clock once (full mode only); none with --offline, "
                  "--quick or --selftest")
CHECK_OUTPUT: Dict[str, Any] = {"id": " | ".join(name for _number, name in doctor.CHECKS), "number": "integer 1-12",
                                "status": " | ".join(doctor.STATUSES), "reason": "string (one line, never a secret)"}
OUTPUT: Dict[str, Any] = {
    "mode": " | ".join(doctor.MODES),
    "verdict": "PASS | FAIL (FAIL when any check FAILs)",
    "checks": [CHECK_OUTPUT],
    "counts": {"pass": "integer", "warn": "integer", "fail": "integer"},
    "warned": ["check id"],
    "failed": ["check id"],
    "machine_role": "%s | %s" % (doctor.ROLE_SHARED, doctor.ROLE_OWNER_PRIVATE),
    "release": "the plugin.json version",
    "record": "state/doctor.json | null (selftest, or no desk.toml at the desk root)",
    "written": ["desk-root relative paths written"],
}
CHECKOUT_RUN: Optional[Callable[[str, List[str]], Any]] = None
TEMPLATE_RUN: Optional[Callable[[str], Mapping[str, Any]]] = None
"""The template check's test seam; ``None`` is ``check_grok_template.run`` itself."""
ENVIRON: Optional[Mapping[str, str]] = None
WORKSPACE_EXISTS: Optional[bool] = None
LOCAL_OFFSET_S: Optional[int] = None
SANDBOX_CLIENT: Optional[Callable[[Any, float, str], Any]] = None


def user_agent(release: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (release, TOOL)


def build_parser(release: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=release, description=DESCRIPTION)
    parser.add_argument("--quick", action="store_true",
                        help="checks 2, 3, 4, 8 and 9 only (the pre-open routine's step)")
    parser.add_argument("--selftest", action="store_true",
                        help="checks 2 and 12 only: the interpreter and the engine selftest; reads no desk file")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> str:
    if args.quick and args.selftest:
        parser.error("--quick and --selftest are separate modes; give one")
    if args.selftest:
        return doctor.MODE_SELFTEST
    return doctor.MODE_QUICK if args.quick else doctor.MODE_FULL


def fail(args: Any, release: str, now: datetime.datetime, code: str, message: str) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, release, code, message, contract.STATUS_ERROR,
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return contract.EXIT_ERROR


def release_pin() -> doctor.Check:
    """Check 1: the pin check (``CHECKOUT_RUN`` is its seam), then the template hashes."""
    root = version.repository_root()
    try:
        checkout: Mapping[str, Any] = check_grok_template.checkout_state(root, release_version(), run=CHECKOUT_RUN)
    except ValueError as exc:
        checkout = {"pinned": False, "reason": "the release cannot be checked: %s" % exc}
    template = check_grok_template.run(root) if TEMPLATE_RUN is None else TEMPLATE_RUN(root)
    return doctor.release_pin_check(checkout, template)


def sandbox_client(timeout_s: float, release: str) -> Callable[[Any], Any]:
    factory = SANDBOX_CLIENT
    if factory is None:
        return lambda token: tradier_client.TradierClient("sandbox", token, timeout_s=timeout_s,
                                                          user_agent=user_agent(release))
    return lambda token: factory(token, timeout_s, user_agent(release))


def heartbeats(paths: desk_paths.DeskPaths, cfg: Optional[desk_config.DeskConfig], now: datetime.datetime,
               extra: Dict[datetime.date, str]) -> doctor.Check:
    """Check 11 with this checkout's manifest and the desk's cadence profile."""
    if cfg is None:
        return doctor.check(11, doctor.WARN, "not checked: desk.toml cannot be used (see desk_files)")
    try:
        manifest = routines.load_manifest()
    except routines.ManifestError as exc:
        return doctor.check(11, doctor.FAIL, "%s: %s" % (routines.MANIFEST_REL, "; ".join(exc.problems)))
    return doctor.heartbeats_check(paths, manifest, cfg.desk.cadence_profile, now, extra)


def run_checks(args: Any, mode: str, now: datetime.datetime, release: str,
               paths: Optional[desk_paths.DeskPaths]) -> List[doctor.Check]:
    numbers = doctor.MODE_CHECKS[mode]
    environ: Mapping[str, str] = os.environ if ENVIRON is None else ENVIRON
    et_date = market_clock.to_et(now).date()
    cfg: Optional[desk_config.DeskConfig] = None
    cal: Optional[calendar_events.CalendarFile] = None
    files: Optional[doctor.Check] = None
    if paths is not None:
        files, cfg, cal = doctor.desk_files_check(paths, et_date)
    extra = calendar_events.extra_closures(cal)
    token: Any = None
    found: Dict[int, doctor.Check] = {}
    for number in numbers:
        if number == 1:
            found[1] = release_pin()
        elif number == 2:
            found[2] = doctor.interpreter_check()
        elif number == 3:
            found[3] = doctor.holiday_table_check(et_date)
        elif number == 4:
            found[4], token = doctor.token_source_check(cfg, args.offline)
        elif number == 5:
            found[5] = doctor.sandbox_check(cfg, token, args.offline, sandbox_client(args.timeout, release))
        elif number == 6:
            found[6] = doctor.machine_role_check(machine_role(environ))
        elif number == 7:
            token_env = cfg.market_data.token_env if cfg is not None else desk_config.DEFAULT_TOKEN_ENV
            found[7] = doctor.broker_env_check(environ, token_env)
        elif number == 8 and files is not None:
            found[8] = files
        elif number == 9:
            offset = LOCAL_OFFSET_S
            if offset is None:
                offset = time.localtime(now.timestamp()).tm_gmtoff
            found[9] = doctor.market_status_check(now, offset, extra)
        elif number == 10 and paths is not None:
            found[10] = doctor.connector_readback_check(paths, now)
        elif number == 11 and paths is not None:
            found[11] = heartbeats(paths, cfg, now, extra)
        elif number == 12:
            found[12] = doctor.selftest_check()
    return [found[number] for number in numbers]


def machine_role(environ: Mapping[str, str]) -> str:
    exists = os.path.exists(doctor.WORKSPACE_DIR) if WORKSPACE_EXISTS is None else WORKSPACE_EXISTS
    return doctor.machine_role(environ, exists)


def counted(checks: Sequence[doctor.Check]) -> Dict[str, int]:
    return {status.lower(): sum(1 for item in checks if item.status == status) for status in doctor.STATUSES}


def prose(mode: str, now: datetime.datetime, release: str, checks: Sequence[doctor.Check], result: str,
          written: Optional[str], note: str) -> List[str]:
    lines = ["desk doctor, %s check at %s UTC (release %s)" % (mode, now.strftime("%Y-%m-%d %H:%M"), release)]
    lines.extend("[%s] %d %s: %s" % (item.status, item.number, item.id, item.reason) for item in checks)
    warned = [item.id for item in checks if item.status == doctor.WARN]
    failed = [item.id for item in checks if item.status == doctor.FAIL]
    tail = "%s written" % written if written is not None else note
    lines.append("verdict: %s (%d checks; warnings: %s; failures: %s); %s"
                 % (result, len(checks), ", ".join(warned) or "none", ", ".join(failed) or "none", tail))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    release = release_version()
    parser = build_parser(release)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    mode = validate(parser, args)
    now = args.now or state.utc_now()
    paths: Optional[desk_paths.DeskPaths] = None
    if mode != doctor.MODE_SELFTEST:
        try:
            paths = desk_paths.for_root(args.desk_root)
        except desk_paths.DeskPathsError as exc:
            return fail(args, release, now, "desk_root", str(exc))
    try:
        checks = run_checks(args, mode, now, release, paths)
    except OSError as exc:
        return fail(args, release, now, "io_error", "cannot read the desk files: %s" % exc)
    result = doctor.verdict(checks)
    role = machine_role(os.environ if ENVIRON is None else ENVIRON)
    written: Optional[str] = None
    note = "nothing written (selftest)"
    if paths is not None and mode in doctor.RECORD_MODES:
        note = "%s not written (no desk.toml at the desk root)" % paths.relative(doctor.doctor_path(paths))
        if os.path.isfile(paths.desk_toml):
            record_file = doctor.doctor_path(paths)
            try:
                state.atomic_write_json(record_file, doctor.doctor_record(mode, now, checks, role, release))
            except OSError as exc:
                return fail(args, release, now, "io_error", "cannot write %s: %s"
                            % (paths.relative(record_file), exc.strerror or exc))
            written = paths.relative(record_file)
    exit_code = contract.EXIT_QUIET if result == doctor.PASS else contract.EXIT_UNAVAILABLE
    if args.json:
        payload = {"mode": mode, "verdict": result, "checks": [item.as_dict() for item in checks],
                   "counts": counted(checks),
                   "warned": [item.id for item in checks if item.status == doctor.WARN],
                   "failed": [item.id for item in checks if item.status == doctor.FAIL],
                   "machine_role": role, "release": release, "record": written,
                   "written": [] if written is None else [written]}
        print(contract.json_dumps(contract.envelope(TOOL, release, payload, generated_at=now,
                                                    status=contract.STATUS_OK if exit_code == 0
                                                    else contract.STATUS_UNAVAILABLE)))
        return exit_code
    print("\n".join(prose(mode, now, release, checks, result, written, note)))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
