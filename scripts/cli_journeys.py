#!/usr/bin/env python3
"""Run the published command journeys as subprocesses in a scrubbed environment. Stdlib only.

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased
changes, commit 75a4c79 (scripts/cli_journeys.py). The reference drove its commands against a
loopback fake exchange; Aegis Desk journeys run with no server at all until the GET-only
market-data client lands, and then with its own loopback fixture.

A journey is one command the documentation tells a Bot to run, executed exactly as a Bot would
run it: as a subprocess from the repository root, in an environment that carries only ``PATH``,
``HOME`` and the Python UTF-8 switches (no proxy variables, no market-data token, nothing that
could reach a network), with its exit code and JSON output checked against the contract.
Every journey's output is also screened for order-shaped text (``FORBIDDEN_OUTPUT``): an
adversarial input must never produce an instruction to trade.

Journeys in this release: the three repository gates in ``--json`` mode. The ``Journey``
record is ready for tool journeys: ``argv`` names a script under ``scripts/`` and its
arguments, ``expected_exit`` the exit code, ``expected_json`` keys that must be present (value
``ANY``) or equal, and ``desk_root`` an optional fixture desk directory (repository-relative)
passed to the tool as ``--desk-root`` for offline runs.

Usage: ``python3 scripts/cli_journeys.py [--root DIR] [--json]``. ``--root`` is the tree the
gates are run against (default: this repository); the scripts always come from this checkout.
Exit 0 when every journey passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
TIMEOUT_SECONDS = 60
ANY = object()
FORBIDDEN_OUTPUT: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"\b(?:place|submit|cancel|replace)_order\b"),
    re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\s+/v\d+/accounts\b"),
)


@dataclasses.dataclass(frozen=True)
class Journey:
    name: str
    argv: Tuple[str, ...]
    expected_exit: int = 0
    expected_json: Optional[Dict[str, Any]] = None
    desk_root: Optional[str] = None


JOURNEYS: Tuple[Journey, ...] = (
    Journey("check_manifests --json", ("check_manifests.py", ".", "--json"), 0, {"ok": True, "version": ANY}),
    Journey("check_no_write_paths --json", ("check_no_write_paths.py", ".", "--json"), 0, {"ok": True, "scanned": ANY}),
    Journey("check_grok_template --json", ("check_grok_template.py", ".", "--json"), 0, {"ok": True, "status": ANY}),
)


def scrubbed_environment() -> Dict[str, str]:
    """Only what the interpreter needs; no proxies, tokens or locale surprises."""
    env = {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    for key in ("PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def run_journey(journey: Journey, root: str) -> Dict[str, Any]:
    script = os.path.join(SCRIPTS, journey.argv[0])
    argv = [sys.executable, script] + list(journey.argv[1:])
    if journey.desk_root is not None:
        argv += ["--desk-root", os.path.join(ROOT, journey.desk_root)]
    problems: List[str] = []
    try:
        proc = subprocess.run(argv, cwd=root, env=scrubbed_environment(), capture_output=True,
                              text=True, timeout=TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": journey.name, "ok": False, "exit": None, "problems": [f"could not run: {exc}"]}
    if proc.returncode != journey.expected_exit:
        problems.append(f"exit {proc.returncode}, expected {journey.expected_exit}; "
                        f"stderr: {proc.stderr.strip()[:400]}; stdout: {proc.stdout.strip()[:400]}")
    payload: Any = None
    if journey.expected_json is not None:
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            problems.append(f"stdout is not JSON: {exc}; stdout: {proc.stdout.strip()[:400]}")
        else:
            if not isinstance(payload, dict):
                problems.append("JSON output must be an object")
            else:
                for key, expected in journey.expected_json.items():
                    if key not in payload:
                        problems.append(f"JSON key '{key}' missing")
                    elif expected is not ANY and payload[key] != expected:
                        problems.append(f"JSON key '{key}' is {payload[key]!r}, expected {expected!r}")
    for pattern in FORBIDDEN_OUTPUT:
        if pattern.search(proc.stdout) or pattern.search(proc.stderr):
            problems.append(f"output contains order-shaped text matching /{pattern.pattern}/")
    return {"name": journey.name, "ok": not problems, "exit": proc.returncode, "problems": problems}


def run(root: str) -> Dict[str, Any]:
    results = [run_journey(journey, root) for journey in JOURNEYS]
    return {"ok": all(r["ok"] for r in results), "root": root, "journeys": results}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=ROOT, help="tree the gate journeys run against (default: this repository)")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of prose")
    args = parser.parse_args(argv)
    result = run(os.path.abspath(args.root))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for item in result["journeys"]:
            if not item["ok"]:
                print(f"{item['name']}: FAIL")
                for problem in item["problems"]:
                    print(f"  {problem}")
        if result["ok"]:
            names = ", ".join(j.name for j in JOURNEYS)
            print(f"ok: {len(JOURNEYS)} command journeys passed in a scrubbed environment ({names}); "
                  "no order-shaped output")
        else:
            failed = sum(1 for item in result["journeys"] if not item["ok"])
            print(f"{failed} journey(s) failed")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
