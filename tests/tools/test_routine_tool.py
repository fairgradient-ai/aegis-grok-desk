"""``scripts/routine.py`` run in-process against copied fixture desk roots: the single reviewed routine entry point (PR
2d decisions D2-2 to D2-6, with the addendum's O4, O5 and O8). The registry (literal imports, ``STEP_MODULES`` equal to
``routines.STEP_TOOLS``), one in-process JSON run per step with its stdout and stderr captured and ``SystemExit``
handled, the step copies and the run record under ``reports/routines/<ET date>/``, the composition (strict and text
parts, the development header, the INCIDENT, UNAVAILABLE and WITHHELD lines, one footer), the per-part and the
whole-message lint, the posting policies ``always``, ``transitions`` and ``fired``, who writes the heartbeat and the
journal, the exit codes, the slot and next-run rules, closed and early-close days, the routine's own lock, the
desk-root refusal and the pin check through ``CHECKOUT_RUN``; then the chains of ``routines/manifest.toml`` on the
fixture roots."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import ast
import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest import mock

from aegis_desk import alert_lint, contract, desk_paths, heartbeat, routines, signals, state, version

import ack as ack_tool
import routine as tool  # the tool scripts/routine.py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
ALERT_FIXTURES = os.path.join(ROOT, "tests", "fixtures", "alerts")
ROUTINE_SOURCE = os.path.join(ROOT, "scripts", "routine.py")
RELEASE = version.release_version()
UTC = datetime.timezone.utc
REGULAR_NOW = "2026-11-03T19:50:00Z"
PREOPEN_NOW = "2026-11-03T12:31:00Z"
EOD_NOW = "2026-11-03T21:35:00Z"
BRIEF_NOW = "2026-11-03T22:10:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
EXPIRY_QUOTES = "2026-11-20T19:31:12Z"
POST_EXPIRY_NOW = "2026-11-21T14:30:00Z"
SATURDAY_NOW = "2026-11-21T15:06:00Z"
THANKSGIVING_NOW = "2026-11-26T15:06:00Z"
EARLY_CLOSE_NOW = "2026-11-27T20:20:00Z"
HEAD = "971186c676d34b0a5d4e35c691d7888632132b8a"
OTHER = "0123456789abcdef0123456789abcdef01234567"
DEV_HEADER = "development run: the checkout is not verified (--allow-dirty)"
GIT_LISTS = [["rev-parse", "--is-inside-work-tree"], ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
             ["rev-parse", "--verify", "--quiet", "refs/tags/v%s^{commit}" % RELEASE],
             ["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"]]
CLOSURE_ENTRY = '\n[[closures]]\ndate = %s\nkind = "full"\nsource = "owner-entered (test)"\n'


class Answer:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def git_answers(tag: Optional[str], status: bytes = b"",
                calls: Optional[List[Tuple[str, List[str]]]] = None) -> Callable[[str, List[str]], Answer]:
    """A ``CHECKOUT_RUN`` seam answering the pin check's four literal argument lists (``tag`` None: no tag)."""
    def run(root: str, args: List[str]) -> Answer:
        if calls is not None:
            calls.append((root, list(args)))
        if args == GIT_LISTS[0]:
            return Answer(0, b"true\n")
        if args == GIT_LISTS[1]:
            return Answer(0, (HEAD + "\n").encode())
        if args == GIT_LISTS[2]:
            return Answer(0, (tag + "\n").encode()) if tag else Answer(1)
        if args == GIT_LISTS[3]:
            return Answer(0, status)
        raise AssertionError("unexpected git arguments %r" % (args,))
    return run


def run_main(argv: Sequence[str]) -> Tuple[Any, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = tool.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


def instant(text: str) -> datetime.datetime:
    return contract.parse_utc_instant(text)


def footer(at: str) -> str:
    return alert_lint.footer_line(instant(at))


def stamp(now: str) -> str:
    return state.quarantine_stamp(instant(now))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def alert_fixture(name: str) -> Tuple[str, Dict[str, Any]]:
    with open(os.path.join(ALERT_FIXTURES, name + ".txt"), encoding="utf-8") as handle:
        text = handle.read()
    with open(os.path.join(ALERT_FIXTURES, name + ".emitted.json"), encoding="utf-8") as handle:
        return text, json.load(handle)


def step_envelope(tool_name: str, payload: Dict[str, Any], status: str, at: str) -> Dict[str, Any]:
    return contract.envelope(tool_name, RELEASE, payload, source="alpaca:paper", delay_min=15,
                             generated_at=instant(at), status=status)


def fake_main(envelope: Optional[Dict[str, Any]] = None, code: int = 0, raw: Optional[str] = None,
              calls: Optional[List[List[str]]] = None, stderr_text: str = "",
              error: Optional[BaseException] = None) -> Callable[..., int]:
    """A step's ``main``: prints ``envelope`` (or ``raw``), writes ``stderr_text``, then raises ``error`` or returns."""
    def main(argv: Optional[Sequence[str]] = None) -> int:
        if calls is not None:
            calls.append(list(argv or []))
        if stderr_text:
            sys.stderr.write(stderr_text)
        if raw is not None:
            print(raw)
        elif envelope is not None:
            print(contract.json_dumps(envelope))
        if error is not None:
            raise error
        return code
    return main


class RoutineCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-routine-tool-")
        self.base = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.base, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        self.git_calls: List[Tuple[str, List[str]]] = []
        self.patch(tool, "CHECKOUT_RUN", git_answers(None, calls=self.git_calls))
        self.patch(tool.step_modules()["desk_doctor"], "LOCAL_OFFSET_S", 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def patch(self, owner: Any, name: str, value: Any) -> None:
        patcher = mock.patch.object(owner, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def argv(self, routine_id: str, slot: Optional[str], now: str, fixture_set: Optional[str] = None,
             allow_dirty: bool = True, json_mode: bool = True) -> List[str]:
        argv = [routine_id] + (["--slot", slot] if slot else []) + (["--allow-dirty"] if allow_dirty else [])
        if fixture_set:
            argv += ["--fixture-set", fixture_set]
        argv += ["--offline", "--desk-root", self.root, "--now", now]
        return argv + (["--json"] if json_mode else [])

    def run_json(self, routine_id: str, slot: Optional[str], now: str, fixture_set: Optional[str] = None,
                 allow_dirty: bool = True) -> Tuple[Any, Dict[str, Any]]:
        code, out, err = run_main(self.argv(routine_id, slot, now, fixture_set, allow_dirty))
        self.assertEqual("", err)
        self.assertEqual([], order_shaped(out))
        body = json.loads(out)
        self.assertEqual((tool.TOOL, contract.MODE), (body["tool"], body["mode"]))
        if "payload" in body:
            self.assertEqual(sorted(tool.PAYLOAD_KEYS), sorted(body["payload"]))
            self.assert_linted(body["payload"])
            self.assertEqual(body["payload"]["exit_code"], code)
        return code, body

    def run_text(self, routine_id: str, slot: Optional[str], now: str, fixture_set: Optional[str] = None,
                 allow_dirty: bool = True) -> Tuple[Any, str]:
        code, out, err = run_main(self.argv(routine_id, slot, now, fixture_set, allow_dirty, json_mode=False))
        self.assertEqual("", err)
        self.assertEqual([], order_shaped(out))
        return code, out

    def assert_linted(self, payload: Dict[str, Any]) -> None:
        """The printed message passes the strict lint of the emitted document the payload carries."""
        if payload["message"] is None:
            return
        result = alert_lint.load_emitted(payload["emitted"]).check(payload["message"])
        self.assertTrue(result.ok, result.problems)

    def usage(self, argv: Sequence[str]) -> Dict[str, Any]:
        code, out, _err = run_main(argv)
        self.assertEqual(2, code)
        body = json.loads(out)
        self.assertEqual(("usage", "invalid_arguments"), (body["status"], body["error"]["code"]))
        return body

    def path(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def exists(self, rel: str) -> bool:
        return os.path.exists(self.path(rel))

    def read_json(self, rel: str) -> Any:
        with open(self.path(rel), encoding="utf-8") as handle:
            return json.load(handle)

    def read_text(self, rel: str) -> str:
        with open(self.path(rel), encoding="utf-8") as handle:
            return handle.read()

    def remove(self, rel: str) -> None:
        os.remove(self.path(rel))

    def append(self, rel: str, text: str) -> None:
        with open(self.path(rel), "a", encoding="utf-8") as handle:
            handle.write(text)

    def files_under(self, rel: str) -> List[str]:
        found: List[str] = []
        for folder, _dirs, files in os.walk(self.path(rel)):
            found.extend(os.path.relpath(os.path.join(folder, name), self.root) for name in files)
        return sorted(found)

    def history(self) -> List[Dict[str, Any]]:
        records, _status, _bad = signals.read_journal(heartbeat.history_path(self.paths))
        return records

    def beats(self) -> List[Tuple[Any, ...]]:
        return [(b["routine_id"], b["exit_code"], b["status"], b["scheduled_et"], b["message_sha256"])
                for b in self.history()]

    def alerts_journal(self) -> List[Dict[str, Any]]:
        records, _status, _bad = signals.read_journal(signals.alerts_path(self.paths))
        return records

    def artifact(self, step: Dict[str, Any]) -> Dict[str, Any]:
        return self.read_json(step["artifact"])

    def ack_config(self, now: str) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ack_tool.main(["config", "--owner-tapped", "--offline", "--desk-root", self.root, "--now", now,
                                  "--json"])
        self.assertEqual(0, code, buffer.getvalue())


# --------------------------------------------------------------------------------------------
# The registry and the step command lines
# --------------------------------------------------------------------------------------------


class RegistryTest(unittest.TestCase):
    def test_step_modules_are_exactly_the_manifest_step_tools(self) -> None:
        self.assertEqual(set(routines.STEP_TOOLS), set(tool.step_modules()))
        for name, module in tool.step_modules().items():
            self.assertEqual(name, module.__name__)
            self.assertTrue(callable(module.main) and callable(module.build_parser), name)
        self.assertNotIn("ack", tool.step_modules())
        self.assertNotIn("routine_audit", tool.step_modules())

    def test_every_step_of_the_manifest_resolves_to_a_registered_tool(self) -> None:
        for routine in routines.load_manifest().routines:
            for steps in routine.chains():
                for step in steps:
                    self.assertIn(routines.step_tool(step), tool.step_modules(), step)

    def test_the_registry_is_built_from_literal_imports(self) -> None:
        with open(ROUTINE_SOURCE, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Name):
                self.assertNotEqual("__import__", node.id)
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("import_module", "run_git"))
        self.assertEqual([], sorted(set(routines.STEP_TOOLS) - imported))
        self.assertNotIn("importlib", imported)
        self.assertNotIn("subprocess", imported)
        self.assertIn("check_grok_template", imported)
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                top.update(alias.name for alias in node.names)
        self.assertEqual([], sorted(set(routines.STEP_TOOLS) & top), "a step tool is imported only after the pin check")
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "step_modules")
        inner = {alias.name for node in ast.walk(function) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(set(routines.STEP_TOOLS), inner)

    def test_the_fixture_set_tools_are_the_step_tools_whose_parser_declares_it(self) -> None:
        declares = set()
        for name, module in tool.step_modules().items():
            options = [flag for action in module.build_parser(RELEASE)._actions for flag in action.option_strings]
            if "--fixture-set" in options:
                declares.add(name)
        self.assertEqual(set(routines.FIXTURE_SET_TOOLS), declares)

    def test_the_step_command_line(self) -> None:
        now = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=UTC)
        next_run = datetime.datetime(2026, 11, 23, 15, 5, tzinfo=UTC)
        self.assertEqual(["--post", "--summary", "--json", "--desk-root", "/desk", "--timeout", "10.0", "--now",
                          "2026-11-20T19:50:00+00:00", "--offline", "--fixture-set", "expiry_day", "--routine",
                          "book-check", "--scheduled-et", "15:35", "--next-run-utc", "2026-11-23T15:05:00+00:00"],
                         tool.step_argv("book_check --post --summary", desk_root="/desk", timeout=10.0, now=now,
                                        offline=True, fixture_set="expiry_day", routine_id="book-check", slot="15:35",
                                        next_run=next_run))
        self.assertEqual(["--quick", "--json", "--desk-root", "/desk", "--timeout", "4.5"],
                         tool.step_argv("desk_doctor --quick", desk_root="/desk", timeout=4.5, now=None, offline=False,
                                        fixture_set="pre_open", routine_id="preopen", slot="07:30", next_run=None))
        self.assertEqual(["--brief", "--json", "--desk-root", "/desk", "--timeout", "10.0", "--offline"],
                         tool.step_argv("eod_summary --brief", desk_root="/desk", timeout=10.0, now=None, offline=True,
                                        fixture_set="regular", routine_id="eod-brief", slot="17:10", next_run=None))
        self.assertEqual(["--from-snapshot", "--json", "--desk-root", "/desk", "--timeout", "10.0", "--offline"],
                         tool.step_argv("trigger_levels --from-snapshot", desk_root="/desk", timeout=10.0, now=None,
                                        offline=True, fixture_set="after_close", routine_id="eod", slot="16:35",
                                        next_run=None), "a step that reads a saved record takes no fixture set")

    def test_the_step_slug(self) -> None:
        for step, slug in (("csv_recon", "csv_recon"), ("expiry_checklist --week", "expiry_checklist-week"),
                           ("desk_doctor --quick", "desk_doctor-quick"), ("eod_summary --brief", "eod_summary-brief"),
                           ("book_check --post --summary", "book_check-post-summary"),
                           ("book_check --post --watch-alive", "book_check-post-watch-alive"),
                           ("book --snapshot --report", "book-snapshot-report")):
            self.assertEqual(slug, tool.step_slug(step))

    def test_the_paths(self) -> None:
        paths = desk_paths.for_root(ROOT)
        day = datetime.date(2026, 11, 20)
        self.assertEqual(os.path.join(ROOT, "state", ".routine.final-call.lock"), tool.lock_path(paths, "final-call"))
        self.assertNotEqual(paths.lock_path, tool.lock_path(paths, "final-call"))
        self.assertEqual(os.path.join(ROOT, "reports", "routines", "2026-11-20", "final-call-1520.json"),
                         tool.run_record_path(paths, day, "final-call", "15:20"))
        self.assertEqual(os.path.join(ROOT, "reports", "routines", "2026-11-20", "steps", "csv_recon.json"),
                         tool.step_artifact_path(paths, day, "csv_recon"))
        self.assertEqual((5.0, "/workspace/aegis-desk", 1), (tool.LOCK_TIMEOUT_S, tool.DEFAULT_DESK_ROOT,
                                                             tool.RUN_RECORD_SCHEMA))
        self.assertEqual(("schema", "routine", "slot", "run_utc", "development", "checkout", "steps", "withheld",
                          "incidents", "message_sha256", "exit_code"), tool.RUN_RECORD_KEYS)


# --------------------------------------------------------------------------------------------
# Schema, usage and the desk root
# --------------------------------------------------------------------------------------------


class SchemaAndUsageTest(RoutineCopy):
    def test_schema_describes_the_payload_and_reads_no_desk(self) -> None:
        code, out, err = run_main(["--schema"])
        self.assertEqual((0, ""), (code, err))
        body = json.loads(out)
        self.assertEqual("routine", body["tool"])
        self.assertEqual(sorted(tool.PAYLOAD_KEYS), sorted(body["envelope"]["payload"]))
        self.assertEqual(["0", "1", "10", "2", "20"], sorted(body["exit_codes"]))
        self.assertEqual([], order_shaped(out))

    def test_an_unknown_routine_id_is_usage(self) -> None:
        body = self.usage(["nope", "--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertIn("nope", body["error"]["message"])
        self.assertIn("book-check", body["error"]["message"])
        self.assertFalse(self.exists("state"))

    def test_a_missing_routine_id_is_usage(self) -> None:
        self.usage(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])

    def test_a_slot_the_routine_lacks_is_usage(self) -> None:
        for routine_id, slot in (("book-check", "09:00"), ("preopen", "7:30"), ("final-call", "12:20")):
            with self.subTest(routine=routine_id, slot=slot):
                body = self.usage([routine_id, "--slot", slot, "--json", "--offline", "--desk-root", self.root,
                                   "--now", REGULAR_NOW])
                self.assertIn(slot, body["error"]["message"])
        self.assertFalse(self.exists("state"))

    def test_fixture_set_needs_offline(self) -> None:
        self.usage(["final-call", "--slot", "15:20", "--fixture-set", "expiry_day", "--desk-root", self.root, "--now",
                    EXPIRY_DAY_NOW, "--json"])
        self.assertFalse(self.exists("state"))

    def test_a_desk_root_in_the_checkout_is_refused(self) -> None:
        link = os.path.join(self.base, "link")
        os.symlink(FIXTURE_DESK, link)
        for root in (FIXTURE_DESK, ROOT, link):
            with self.subTest(root=root):
                body = self.usage(["final-call", "--slot", "15:20", "--allow-dirty", "--fixture-set", "expiry_day",
                                   "--offline", "--desk-root", root, "--now", EXPIRY_DAY_NOW, "--json"])
                self.assertIn("checkout", body["error"]["message"])
        self.assertFalse(os.path.exists(os.path.join(FIXTURE_DESK, "state")))
        self.assertFalse(os.path.exists(os.path.join(FIXTURE_DESK, "reports")))

    def test_usage_without_json_goes_to_stderr(self) -> None:
        code, out, err = run_main(["nope", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual((2, ""), (code, out))
        self.assertIn("nope", err)


class DeskRootHelpTest(unittest.TestCase):
    """The --desk-root help (and so docs/TOOLS.md) names routine.py's own default, never the current directory (PR 3
    decision P13)."""

    def help_of(self, flag: str) -> str:
        actions = [action for action in tool.build_parser(RELEASE)._actions if flag in action.option_strings]
        self.assertEqual(1, len(actions), flag)
        return actions[0].help or ""

    def test_the_help_names_the_routine_default(self) -> None:
        text = self.help_of("--desk-root")
        self.assertIn("default: %s" % tool.DEFAULT_DESK_ROOT, text)
        self.assertEqual("/workspace/aegis-desk", tool.DEFAULT_DESK_ROOT)
        self.assertIn("never the current directory", text)
        self.assertNotIn("default: the current directory", text)
        self.assertIn("inside this checkout", text)
        self.assertIn("--offline runs and fixture runs must pass it explicitly", text)

    def test_other_tools_keep_the_shared_help(self) -> None:
        parser = ack_tool.build_parser(RELEASE)
        shared = [action.help for action in parser._actions if "--desk-root" in action.option_strings][0]
        self.assertIn("default: the current directory", shared)


class DefaultDeskRootTest(RoutineCopy):
    def test_without_desk_root_the_default_is_used_never_the_current_directory(self) -> None:
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        elsewhere = os.path.join(self.base, "elsewhere")
        os.makedirs(elsewhere)
        os.chdir(elsewhere)
        absent = os.path.join(self.base, "absent")
        argv = ["book-check", "--slot", "10:05", "--allow-dirty", "--now", SATURDAY_NOW, "--json"]
        with mock.patch.object(tool, "DEFAULT_DESK_ROOT", absent):
            code, out, err = run_main(argv)
        self.assertEqual((1, ""), (code, err))
        body = json.loads(out)
        self.assertEqual(("error", "desk_root"), (body["status"], body["error"]["code"]))
        self.assertIn(absent, body["error"]["message"])
        with mock.patch.object(tool, "DEFAULT_DESK_ROOT", self.root):
            code, out, err = run_main(argv)
        self.assertEqual((0, ""), (code, err))
        self.assertTrue(json.loads(out)["payload"]["quiet"])
        self.assertTrue(self.exists("state/heartbeat/book-check.json"))
        self.assertEqual([], os.listdir(elsewhere))

    def test_a_default_in_the_checkout_is_refused(self) -> None:
        with mock.patch.object(tool, "DEFAULT_DESK_ROOT", FIXTURE_DESK):
            self.usage(["book-check", "--slot", "10:05", "--allow-dirty", "--now", SATURDAY_NOW, "--json"])
        self.assertFalse(os.path.exists(os.path.join(FIXTURE_DESK, "state")))


# --------------------------------------------------------------------------------------------
# The pin check
# --------------------------------------------------------------------------------------------


class PinTest(RoutineCopy):
    def refused(self, answers: Callable[[str, List[str]], Answer], reason: str, routine_id: str = "final-call",
                slot: str = "15:20") -> Dict[str, Any]:
        self.patch(tool, "CHECKOUT_RUN", answers)
        code, body = self.run_json(routine_id, slot, EXPIRY_DAY_NOW, "expiry_day", allow_dirty=False)
        incident = "incidents/%s-dirty-checkout.md" % stamp(EXPIRY_DAY_NOW)
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        payload = body["payload"]
        self.assertEqual("INCIDENT | %s | the desk checkout is not the pinned release v%s: %s; no step ran | details "
                         "in %s\n%s" % (routine_id, RELEASE, reason, incident, footer(EXPIRY_DAY_NOW)), payload["message"])
        self.assertEqual(([], [incident], False), (payload["steps"], payload["incidents"], payload["development"]))
        self.assertEqual({"pinned": False, "reason": reason}, payload["checkout"])
        self.assertIn(reason, self.read_text(incident))
        self.assertFalse(self.exists("reports/routines/2026-11-20/steps"))
        self.assertEqual([], self.files_under("data"))
        record = self.read_json("reports/routines/2026-11-20/%s-%s.json" % (routine_id, slot.replace(":", "")))
        self.assertEqual((20, False, [], [incident]), (record["exit_code"], record["development"], record["steps"],
                                                      record["incidents"]))
        self.assertEqual([(routine_id, 20, "unavailable", slot, sha(payload["message"]))], self.beats())
        self.assertEqual([(None, payload["message"])], [(r["signal_id"], r["text"]) for r in self.alerts_journal()])
        return payload

    def test_a_development_checkout_is_refused(self) -> None:
        self.refused(git_answers(None), "development checkout (no tag)")

    def test_a_dirty_release_checkout_is_refused(self) -> None:
        self.refused(git_answers(HEAD, b" M scripts/routine.py\n?? notes.txt\n"),
                     "the checkout has 2 uncommitted or untracked path(s)")

    def test_a_checkout_off_the_tag_is_refused(self) -> None:
        self.refused(git_answers(OTHER), "HEAD 971186c6 is not the commit of v%s (01234567)" % RELEASE)

    def test_a_refused_book_check_writes_the_routine_heartbeat(self) -> None:
        self.refused(git_answers(None), "development checkout (no tag)", "book-check", "15:35")

    def test_a_refused_checkout_imports_no_step_tool(self) -> None:
        loader = mock.Mock(side_effect=AssertionError("a step tool was imported on a refused checkout"))
        self.patch(tool, "step_modules", loader)
        self.patch(tool, "CHECKOUT_RUN", git_answers(None))
        code, body = self.run_json("eod", "16:35", EOD_NOW, "after_close", allow_dirty=False)
        self.assertEqual((20, 0), (code, loader.call_count))
        self.assertIn("the desk checkout is not the pinned release", body["payload"]["message"])

    def test_a_pinned_quiet_run_prints_only_quiet(self) -> None:
        self.patch(tool, "CHECKOUT_RUN", git_answers(HEAD))
        code, out = self.run_text("final-call", "15:20", REGULAR_NOW, "regular", allow_dirty=False)
        self.assertEqual((0, "quiet\n"), (code, out))

    def test_a_pinned_checkout_runs_without_the_development_header(self) -> None:
        self.patch(tool, "CHECKOUT_RUN", git_answers(HEAD, calls=self.git_calls))
        code, body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day", allow_dirty=False)
        payload = body["payload"]
        self.assertEqual(10, code)
        self.assertTrue(payload["message"].startswith("FINAL CALL Fri 2026-11-20"), payload["message"])
        self.assertEqual((False, {"pinned": True, "reason": "release v%s at 971186c6, clean" % RELEASE}),
                         (payload["development"], payload["checkout"]))
        self.assertEqual([(version.repository_root(), args) for args in GIT_LISTS], self.git_calls)

    def test_allow_dirty_runs_a_dirty_checkout_and_the_run_record_logs_it(self) -> None:
        self.patch(tool, "CHECKOUT_RUN", git_answers(HEAD, b" M scripts/routine.py\n"))
        code, body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        self.assertEqual(10, code)
        self.assertTrue(body["payload"]["message"].startswith(DEV_HEADER + "\nFINAL CALL"))
        record = self.read_json("reports/routines/2026-11-20/final-call-1520.json")
        self.assertEqual((True, {"pinned": False, "reason": "the checkout has 1 uncommitted or untracked path(s)"}),
                         (record["development"], record["checkout"]))


# --------------------------------------------------------------------------------------------
# The routine's own lock
# --------------------------------------------------------------------------------------------


class LockTest(RoutineCopy):
    def test_a_busy_routine_lock_does_nothing(self) -> None:
        self.patch(tool, "LOCK_TIMEOUT_S", 0.0)
        with state.locked(tool.lock_path(self.paths, "final-call")):
            code, out = self.run_text("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
            json_code, json_out, _err = run_main(self.argv("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day"))
        text = ("INCIDENT | final-call | another run of this routine holds its lock; this run did nothing\n"
                + footer(EXPIRY_DAY_NOW))
        self.assertEqual((1, text + "\n"), (code, out))
        self.assertTrue(alert_lint.check(text, [], identifiers=[], clocks=["19:50 UTC"],
                                         as_of=instant(EXPIRY_DAY_NOW)).ok)
        body = json.loads(json_out)
        self.assertEqual((1, "error", "lock_busy"), (json_code, body["status"], body["error"]["code"]))
        for top in ("state/heartbeat", "reports", "journal", "incidents", "data", "levels"):
            self.assertEqual([], self.files_under(top), top)

    def test_another_routine_holding_its_own_lock_does_not_block(self) -> None:
        self.patch(tool, "LOCK_TIMEOUT_S", 0.0)
        with state.locked(tool.lock_path(self.paths, "book-check")):
            code, _body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        self.assertEqual(10, code)


# --------------------------------------------------------------------------------------------
# posts = transitions (book-check)
# --------------------------------------------------------------------------------------------


class TransitionsTest(RoutineCopy):
    def test_book_check_1405_on_the_regular_set(self) -> None:
        code, body = self.run_json("book-check", "14:05", REGULAR_NOW, "regular")
        payload = body["payload"]
        self.assertEqual((0, "ok"), (code, body["status"]))
        step = payload["steps"][0]
        self.assertEqual(("book_check", 0), (step["tool"], step["exit_code"]))
        self.assertTrue(payload["lint"]["ok"])
        for piece in ("CONFIG CHANGED | desk.toml: not acknowledged -> sha f08a18dc",
                      "WATCH AG-20261020-01 | WMT covered call | your rule DTE21", "run 45 min late"):
            self.assertIn(piece, payload["message"])
        self.assertEqual(["--post", "--json", "--desk-root", self.root, "--timeout", "10.0", "--now",
                          "2026-11-03T19:50:00+00:00", "--offline", "--fixture-set", "regular", "--routine",
                          "book-check", "--scheduled-et", "14:05", "--next-run-utc", "2026-11-03T20:05:00+00:00"],
                         step["argv"])
        own = self.artifact(step)["payload"]["message"]
        self.assertEqual(DEV_HEADER + "\n" + own, payload["message"])
        self.assertEqual("state/heartbeat/book-check.json", payload["heartbeat"])
        self.assertEqual([("book-check", 0, "ok", "14:05", sha(own))], self.beats())
        self.assertEqual(45, self.history()[0]["lateness_min"])
        texts = [record["text"] for record in self.alerts_journal()]
        self.assertEqual(4, len(texts))
        self.assertNotIn(payload["message"], texts)
        self.assertEqual([], [text for text in texts if text.startswith(DEV_HEADER)])

    def test_book_check_1535_on_expiry_day_after_ack_config(self) -> None:
        self.ack_config("2026-11-20T19:49:00Z")
        code, body = self.run_json("book-check", "15:35", EXPIRY_DAY_NOW, "expiry_day")
        payload = body["payload"]
        self.assertEqual((10, "fired", True), (code, body["status"], payload["development"]))
        step = payload["steps"][0]
        self.assertEqual(("book_check", 10), (step["tool"], step["exit_code"]))
        self.assertTrue(payload["lint"]["ok"])
        self.assertEqual("state/heartbeat/book-check.json", payload["heartbeat"])
        self.assertEqual(["--post", "--summary"], step["argv"][:2])
        self.assertEqual("2026-11-23T15:05:00+00:00", step["argv"][step["argv"].index("--next-run-utc") + 1])
        self.assertEqual("2026-11-23T15:05:00+00:00", payload["next_run_utc"])
        for piece in ("EXIT SIGNAL AG-20261020-01 | WMT covered call | your rule TP80",
                      "pre-close | verdicts: 2 EXIT SIGNAL, 0 UNAVAILABLE, 1 WATCH, 0 HOLD | open signals: 4"):
            self.assertIn(piece, payload["message"])
        own = self.artifact(step)["payload"]
        self.assertEqual(DEV_HEADER + "\n" + own["message"], payload["message"])
        self.assertTrue(own["single_observation"])
        self.assertEqual(["book_check"], [part["tool"] for part in payload["parts"]])
        self.assertEqual("reports/routines/2026-11-20/steps/book_check-post-summary.json", step["artifact"])

    def test_book_check_1005_refreshes_the_levels_and_posts_book_check_only(self) -> None:
        code, body = self.run_json("book-check", "10:05", REGULAR_NOW, "regular")
        payload = body["payload"]
        self.assertEqual(0, code)
        self.assertEqual([("book_check", 0), ("trigger_levels", 0)],
                         [(step["tool"], step["exit_code"]) for step in payload["steps"]])
        self.assertEqual(["--json", "--desk-root", self.root, "--timeout", "10.0", "--now", "2026-11-03T19:50:00+00:00",
                          "--offline", "--fixture-set", "regular"], payload["steps"][1]["argv"])
        self.assertTrue(self.exists("levels/2026-11-03.md"))
        self.assertNotIn("THRESHOLD LEVELS", payload["message"])
        self.assertEqual(DEV_HEADER + "\n" + self.artifact(payload["steps"][0])["payload"]["message"],
                         payload["message"])
        self.assertEqual(["reports/routines/2026-11-03/steps/book_check-post.json",
                          "reports/routines/2026-11-03/steps/trigger_levels.json"],
                         [step["artifact"] for step in payload["steps"]])

    def test_book_check_1205_carries_watch_alive(self) -> None:
        code, body = self.run_json("book-check", "12:05", REGULAR_NOW, "regular")
        step = body["payload"]["steps"][0]
        self.assertEqual((0, ["--post", "--watch-alive"]), (code, step["argv"][:2]))
        self.assertEqual("reports/routines/2026-11-03/steps/book_check-post-watch-alive.json", step["artifact"])

    def test_a_failed_levels_refresh_is_an_incident_file_only(self) -> None:
        self.patch(tool.step_modules()["trigger_levels"], "main", fake_main(error=RuntimeError("levels probe")))
        code, body = self.run_json("book-check", "10:05", REGULAR_NOW, "regular")
        payload = body["payload"]
        incident = "incidents/%s-book-check-trigger_levels.md" % stamp(REGULAR_NOW)
        self.assertEqual(0, code)
        self.assertEqual((1, "error", "step_crashed"),
                         tuple(payload["steps"][1][key] for key in ("exit_code", "status", "error_code")))
        self.assertEqual([incident], payload["incidents"])
        text = self.read_text(incident)
        self.assertIn("Traceback", text)
        self.assertIn("RuntimeError: levels probe", text)
        self.assertNotIn("INCIDENT", payload["message"])
        self.assertEqual(DEV_HEADER + "\n" + self.artifact(payload["steps"][0])["payload"]["message"],
                         payload["message"])
        crashed = self.artifact(payload["steps"][1])
        self.assertEqual(("error", "step_crashed"), (crashed["status"], crashed["error"]["code"]))
        # O8: the 17:10 brief names it (the file's routine and step, never its contents)
        code, body = self.run_json("eod-brief", "17:10", "2026-11-03T22:10:00Z")
        self.assertIn("**incidents today** 1 (book-check trigger_levels); details in incidents/",
                      body["payload"]["message"].split("\n"))
        self.assertNotIn("levels probe", body["payload"]["message"])

    def test_a_failed_book_check_is_an_incident_line(self) -> None:
        self.remove("rules/ADOPTED")
        code, body = self.run_json("book-check", "14:05", REGULAR_NOW, "regular")
        payload = body["payload"]
        incident = "incidents/%s-book-check-book_check-post.md" % stamp(REGULAR_NOW)
        self.assertEqual((1, "error"), (code, body["status"]))
        self.assertEqual((1, "not_adopted"), (payload["steps"][0]["exit_code"], payload["steps"][0]["error_code"]))
        self.assertEqual("\n".join([DEV_HEADER, "INCIDENT | book-check 14:05 ET | book_check failed (not_adopted); the "
                                   "other steps ran; details in %s" % incident, footer(REGULAR_NOW)]),
                         payload["message"])
        self.assertEqual([("book-check", 1, "error", "14:05", None)], self.beats())  # book_check's own heartbeat


# --------------------------------------------------------------------------------------------
# posts = fired (final-call, post-expiry)
# --------------------------------------------------------------------------------------------


class FiredTest(RoutineCopy):
    def test_final_call_on_expiry_day(self) -> None:
        code, body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        payload = body["payload"]
        self.assertEqual((10, "fired"), (code, body["status"]))
        self.assertEqual([10, 0, 10], [step["exit_code"] for step in payload["steps"]])
        self.assertTrue(payload["lint"]["ok"])
        self.assertEqual("state/heartbeat/final-call.json", payload["heartbeat"])
        final, tomorrow, watch = [self.artifact(step)["payload"]["message"] for step in payload["steps"]]
        self.assertEqual("\n".join([DEV_HEADER, final, watch, footer(EXPIRY_QUOTES)]), payload["message"])
        self.assertIn("FINAL CALL Fri 2026-11-20: 2 contracts on their last trading day; act by 16:00 ET (21:00 UTC)",
                      payload["message"])
        self.assertIn("assignment watch Fri 2026-11-20 (session open): FIRED", payload["message"])
        self.assertNotIn(tomorrow.splitlines()[0], payload["message"])
        self.assertEqual(["expiry_checklist", "assignment_watch"], [part["tool"] for part in payload["parts"]])
        self.assertEqual([("final-call", 10, "fired", "15:20", sha(payload["message"]))], self.beats())
        self.assertEqual([(None, payload["message"], True)],
                         [(r["signal_id"], r["text"], r["lint_ok"]) for r in self.alerts_journal()])
        self.assertIn(payload["message"], self.read_text("journal/2026-11-20.md"))

    def test_human_mode_prints_only_the_message(self) -> None:
        code, out = self.run_text("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        _code, body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        self.assertEqual((10, body["payload"]["message"] + "\n"), (code, out))

    def test_final_call_on_an_ordinary_day_is_quiet(self) -> None:
        code, out = self.run_text("final-call", "15:20", REGULAR_NOW, "regular")
        self.assertEqual((0, DEV_HEADER + "\nquiet\n"), (code, out))
        self.assertEqual([("final-call", 0, "quiet", "15:20", None)], self.beats())
        self.assertEqual([], self.alerts_journal())
        code, body = self.run_json("final-call", "15:20", REGULAR_NOW, "regular")
        payload = body["payload"]
        self.assertEqual((0, "quiet", True, None, []), (code, body["status"], payload["quiet"], payload["message"],
                                                        payload["parts"]))
        self.assertEqual([0, 0, 0], [step["exit_code"] for step in payload["steps"]])

    def test_post_expiry_posts_the_inferred_outcomes(self) -> None:
        code, body = self.run_json("post-expiry", "09:30", POST_EXPIRY_NOW, "post_expiry")
        payload = body["payload"]
        self.assertEqual((10, "fired"), (code, body["status"]))
        self.assertEqual([("csv_recon", 10)], [(step["tool"], step["exit_code"]) for step in payload["steps"]])
        recon = self.artifact(payload["steps"][0])["payload"]["message"]
        self.assertTrue(recon.startswith("EDITS_PENDING (2026-11-21 ET): 2 expired legs, 3 proposed edits"), recon)
        self.assertEqual("\n".join([DEV_HEADER, recon, footer(POST_EXPIRY_NOW)]), payload["message"])
        self.assertEqual([("post-expiry", 10, "fired", "09:30", sha(payload["message"]))], self.beats())


# --------------------------------------------------------------------------------------------
# posts = always (eod; preopen and eod-brief in ChainsTest)
# --------------------------------------------------------------------------------------------


class AlwaysTest(RoutineCopy):
    def test_eod_posts_the_threshold_sheet(self) -> None:
        code, body = self.run_json("eod", "16:35", EOD_NOW, "after_close")
        payload = body["payload"]
        self.assertEqual((0, "ok"), (code, body["status"]))
        self.assertEqual([("book", 0), ("trigger_levels", 0), ("csv_recon", 0)],
                         [(step["tool"], step["exit_code"]) for step in payload["steps"]])
        self.assertEqual(DEV_HEADER + "\n" + self.artifact(payload["steps"][1])["payload"]["message"],
                         payload["message"])
        # D1-1: the levels are the snapshot's (data/book/<D>.json, the book the 17:10 brief reads), never a second build
        levels = payload["steps"][1]
        self.assertEqual(["--from-snapshot"], [token for token in levels["argv"] if token.startswith("--f")])
        self.assertEqual(("file", "data/book/2026-11-03.json"),
                         tuple(self.artifact(levels)["payload"]["book"][key] for key in ("origin", "path")))
        self.assertEqual(["reports/routines/2026-11-03/steps/book-snapshot-report.json",
                          "reports/routines/2026-11-03/steps/trigger_levels-from-snapshot.json",
                          "reports/routines/2026-11-03/steps/csv_recon.json"],
                         [step["artifact"] for step in payload["steps"]])
        for rel in ("data/book/2026-11-03.json", "levels/2026-11-03.md", "data/positions/2026-11-03.recon.json"):
            self.assertTrue(self.exists(rel), rel)
        self.assertEqual([("eod", 0, "ok", "16:35", sha(payload["message"]))], self.beats())

    def test_a_failed_post_step_is_an_incident_line_and_the_chain_goes_on(self) -> None:
        self.remove("rules/ADOPTED")
        code, body = self.run_json("eod", "16:35", EOD_NOW, "after_close")
        payload = body["payload"]
        incident = "incidents/%s-eod-trigger_levels-from-snapshot.md" % stamp(EOD_NOW)
        self.assertEqual((1, "error"), (code, body["status"]))
        self.assertEqual([0, 1, 0], [step["exit_code"] for step in payload["steps"]])
        self.assertEqual("\n".join([DEV_HEADER, "INCIDENT | eod 16:35 ET | trigger_levels failed (not_adopted); the "
                                   "other steps ran; details in %s" % incident, footer(EOD_NOW)]), payload["message"])
        self.assertEqual([incident], payload["incidents"])
        self.assertIn("not_adopted", self.read_text(incident))
        self.assertEqual([("eod", 1, "error", "16:35", sha(payload["message"]))], self.beats())

    def test_an_unavailable_error_envelope_is_posted_as_unavailable(self) -> None:
        missing = "book snapshot data/book/2026-11-03.json missing"
        envelope = contract.error_envelope("trigger_levels", RELEASE, "snapshot_missing", missing, "unavailable",
                                           generated_at=instant(EOD_NOW))
        self.patch(tool.step_modules()["trigger_levels"], "main", fake_main(envelope, 20))
        code, body = self.run_json("eod", "16:35", EOD_NOW, "after_close")
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        self.assertEqual("\n".join([DEV_HEADER, "UNAVAILABLE: " + missing, footer(EOD_NOW)]),
                         body["payload"]["message"])
        self.assertEqual([], body["payload"]["incidents"])


# --------------------------------------------------------------------------------------------
# The lint: a withheld part, a withheld message
# --------------------------------------------------------------------------------------------


class LintTest(RoutineCopy):
    def test_a_strict_part_that_fails_its_lint_is_withheld(self) -> None:
        text, document = alert_fixture("fail_imperative_start")
        envelope = step_envelope("book_check", {"message": text.rstrip("\n"), "emitted": document}, "fired",
                                 EXPIRY_DAY_NOW)
        self.patch(tool.step_modules()["book_check"], "main", fake_main(envelope, 10))
        code, body = self.run_json("book-check", "15:35", EXPIRY_DAY_NOW, "expiry_day")
        payload = body["payload"]
        incident = "incidents/%s-book-check-book_check-post-summary-withheld.md" % stamp(EXPIRY_DAY_NOW)
        self.assertEqual((10, "fired"), (code, body["status"]))
        self.assertEqual("\n".join([DEV_HEADER, "WITHHELD | book_check: the text failed the alert lint "
                                   "(imperative_start) and is withheld; book_check.py shows it",
                                   alert_lint.footer_line(alert_lint.load_emitted(document).as_of)]), payload["message"])
        self.assertEqual([{"tool": "book_check", "step": "book_check-post-summary", "codes": ["imperative_start"]}],
                         payload["withheld"])
        self.assertEqual([incident], payload["incidents"])
        self.assertIn("imperative_start", self.read_text(incident))
        self.assertEqual([], order_shaped(self.read_text(incident)))
        self.assertEqual([("book-check", 10, "fired", "15:35", sha(payload["message"]))], self.beats())

    def test_a_text_part_that_fails_its_lint_is_withheld(self) -> None:
        text, document = alert_fixture("fail_you_should")
        lines = text.rstrip("\n").split("\n")[:-1]
        envelope = step_envelope("csv_recon", {"message": "\n".join(lines), "as_of": document["as_of"]}, "fired",
                                 POST_EXPIRY_NOW)
        self.patch(tool.step_modules()["csv_recon"], "main", fake_main(envelope, 10))
        code, body = self.run_json("post-expiry", "09:30", POST_EXPIRY_NOW, "post_expiry")
        payload = body["payload"]
        self.assertEqual(10, code)
        self.assertEqual("\n".join([DEV_HEADER, "WITHHELD | csv_recon: the text failed the alert lint "
                                   "(forbidden_language) and is withheld; csv_recon.py shows it",
                                   footer(document["as_of"])]), payload["message"])
        self.assertEqual([{"tool": "csv_recon", "step": "csv_recon", "codes": ["forbidden_language"]}],
                         payload["withheld"])

    def test_a_message_that_fails_as_a_whole_is_withheld_whole(self) -> None:
        text, document = alert_fixture("pass_watch")
        lenient = dict(document, identifiers=None, clocks=None)
        self.assertTrue(alert_lint.load_emitted(lenient).check(text).ok)
        envelope = step_envelope("book_check", {"message": text.rstrip("\n"), "emitted": lenient}, "ok", REGULAR_NOW)
        self.patch(tool.step_modules()["book_check"], "main", fake_main(envelope, 0))
        code, body = self.run_json("book-check", "14:05", REGULAR_NOW, "regular")
        payload = body["payload"]
        incident = "incidents/%s-book-check-message.md" % stamp(REGULAR_NOW)
        self.assertEqual((1, "error"), (code, body["status"]))
        self.assertEqual("INCIDENT | book-check | the routine message failed the alert lint (identifier_not_emitted) "
                         "and is withheld\n" + footer("2026-11-03T19:31:12Z"), payload["message"])
        self.assertFalse(payload["lint"]["ok"])
        self.assertEqual([incident], payload["incidents"])
        self.assertIn("identifier_not_emitted", self.read_text(incident))
        self.assertEqual([("book-check", 1, "error", "14:05", sha(payload["message"]))], self.beats())


# --------------------------------------------------------------------------------------------
# One in-process run per step: capture, crashes, SystemExit, bad envelopes
# --------------------------------------------------------------------------------------------


class StepCaptureTest(RoutineCopy):
    def good(self) -> Dict[str, Any]:
        return step_envelope("csv_recon", {"message": "CLEAN (2026-11-21 ET): 0 expired legs",
                                           "as_of": "2026-11-21T14:30:00+00:00"}, "quiet", POST_EXPIRY_NOW)

    def test_a_step_that_prints_no_envelope(self) -> None:
        self.patch(tool.step_modules()["csv_recon"], "main", fake_main(raw="not an envelope", code=0))
        code, body = self.run_json("post-expiry", "09:30", POST_EXPIRY_NOW, "post_expiry")
        payload = body["payload"]
        step = payload["steps"][0]
        incident = "incidents/%s-post-expiry-csv_recon.md" % stamp(POST_EXPIRY_NOW)
        self.assertEqual(1, code)
        self.assertEqual((1, "error", "bad_envelope"), (step["exit_code"], step["status"], step["error_code"]))
        self.assertEqual("\n".join([DEV_HEADER, "INCIDENT | post-expiry 09:30 ET | csv_recon failed (bad_envelope); "
                                   "the other steps ran; details in %s" % incident, footer(POST_EXPIRY_NOW)]),
                         payload["message"])
        copy = self.artifact(step)
        self.assertEqual(("error", "bad_envelope"), (copy["status"], copy["error"]["code"]))

    def test_system_exit_codes(self) -> None:
        for raised, expected in ((SystemExit(None), 0), (SystemExit(0), 0), (SystemExit("stopped"), 1),
                                 (SystemExit(20), 20)):
            with self.subTest(raised=raised):
                with mock.patch.object(tool.step_modules()["csv_recon"], "main", fake_main(self.good(), error=raised)):
                    code, body = self.run_json("post-expiry", "09:30", POST_EXPIRY_NOW, "post_expiry")
                self.assertEqual(expected, body["payload"]["steps"][0]["exit_code"])
                self.assertEqual(1 if expected == 1 else expected, code)

    def test_a_usage_error_inside_a_step(self) -> None:
        usage = contract.error_envelope("csv_recon", RELEASE, "invalid_arguments", "unrecognized arguments: --nope",
                                        "usage", generated_at=instant(POST_EXPIRY_NOW))
        self.patch(tool.step_modules()["csv_recon"], "main", fake_main(usage, error=SystemExit(2)))
        code, body = self.run_json("post-expiry", "09:30", POST_EXPIRY_NOW, "post_expiry")
        step = body["payload"]["steps"][0]
        self.assertEqual((1, 2, "usage", "invalid_arguments"), (code, step["exit_code"], step["status"],
                                                                step["error_code"]))
        self.assertIn("csv_recon failed (invalid_arguments)", body["payload"]["message"])

    def test_step_output_is_captured_and_stderr_goes_to_the_incident(self) -> None:
        self.patch(tool.step_modules()["csv_recon"], "main", fake_main(raw="", code=1, stderr_text="a note on stderr\n"))
        code, out = self.run_text("post-expiry", "09:30", POST_EXPIRY_NOW, "post_expiry")
        self.assertEqual(1, code)
        self.assertTrue(out.startswith(DEV_HEADER + "\nINCIDENT | post-expiry 09:30 ET | csv_recon failed "
                                                    "(bad_envelope)"), out)
        self.assertIn("a note on stderr", self.read_text("incidents/%s-post-expiry-csv_recon.md"
                                                         % stamp(POST_EXPIRY_NOW)))

    def test_a_step_runs_once_and_steps_never_short_circuit(self) -> None:
        calls: List[List[str]] = []
        self.patch(tool.step_modules()["expiry_checklist"], "main", fake_main(raw="", code=1, calls=calls))
        code, body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        self.assertEqual(1, code)
        self.assertEqual(2, len(calls))
        self.assertEqual(["--final-call", "--tomorrow"], [argv[0] for argv in calls])
        self.assertEqual([1, 1, 10], [step["exit_code"] for step in body["payload"]["steps"]])
        self.assertEqual(["incidents/%s-final-call-expiry_checklist-final-call.md" % stamp(EXPIRY_DAY_NOW),
                          "incidents/%s-final-call-expiry_checklist-tomorrow.md" % stamp(EXPIRY_DAY_NOW)],
                         body["payload"]["incidents"])
        self.assertIn("assignment watch Fri 2026-11-20 (session open): FIRED", body["payload"]["message"])


class ExitCodeTest(RoutineCopy):
    def text_envelope(self, tool_name: str, message: str, status: str, at: str) -> Dict[str, Any]:
        return step_envelope(tool_name, {"message": message, "as_of": contract.format_utc(instant(at))}, status, at)

    def test_a_posted_10_wins_over_a_posted_20(self) -> None:
        envelope = self.text_envelope("expiry_checklist", "EXPIRY WEEK 2026-11-16..2026-11-22: 1 contract unavailable",
                                      "unavailable", EXPIRY_DAY_NOW)
        self.patch(tool.step_modules()["expiry_checklist"], "main", fake_main(envelope, 20))
        code, body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        self.assertEqual([20, 20, 10], [step["exit_code"] for step in body["payload"]["steps"]])
        self.assertEqual(["expiry_checklist", "expiry_checklist", "assignment_watch"],
                         [part["tool"] for part in body["payload"]["parts"]])
        self.assertEqual((10, "fired"), (code, body["status"]))

    def test_a_step_that_is_not_posted_never_makes_the_routine_10_but_its_20_counts(self) -> None:
        recon = self.text_envelope("csv_recon", "EDITS_PENDING (2026-11-03 ET): 0 expired legs, 1 proposed edit",
                                   "fired", EOD_NOW)
        self.patch(tool.step_modules()["csv_recon"], "main", fake_main(recon, 10))
        code, body = self.run_json("eod", "16:35", EOD_NOW, "after_close")
        self.assertEqual(([0, 0, 10], 0), ([step["exit_code"] for step in body["payload"]["steps"]], code))
        self.assertNotIn("EDITS_PENDING", body["payload"]["message"])
        unavailable = step_envelope("book", {"warnings": []}, "unavailable", EOD_NOW)
        self.patch(tool.step_modules()["book"], "main", fake_main(unavailable, 20))
        code, body = self.run_json("eod", "16:35", EOD_NOW, "after_close")
        self.assertEqual(([20, 0, 10], 20), ([step["exit_code"] for step in body["payload"]["steps"]], code))
        self.assertTrue(body["payload"]["message"].startswith(DEV_HEADER + "\nTHRESHOLD LEVELS 2026-11-03"))

    def test_under_transitions_only_book_check_decides(self) -> None:
        levels = step_envelope("trigger_levels", {"message": None, "warnings": ["levels not written: busy"]},
                               "unavailable", REGULAR_NOW)
        self.patch(tool.step_modules()["trigger_levels"], "main", fake_main(levels, 20))
        code, body = self.run_json("book-check", "10:05", REGULAR_NOW, "regular")
        self.assertEqual(([0, 20], 0, []), ([step["exit_code"] for step in body["payload"]["steps"]], code,
                                            body["payload"]["incidents"]))
        self.assertEqual(DEV_HEADER + "\n" + self.artifact(body["payload"]["steps"][0])["payload"]["message"],
                         body["payload"]["message"])


    def test_a_quiet_run_keeps_a_counted_20(self) -> None:
        acknowledged = step_envelope("book_check", {"message": None, "emitted": None, "quiet": True}, "unavailable",
                                     REGULAR_NOW)
        self.patch(tool.step_modules()["book_check"], "main", fake_main(acknowledged, 20))
        code, out = self.run_text("book-check", "14:05", REGULAR_NOW, "regular")
        self.assertEqual((20, DEV_HEADER + "\nquiet\n"), (code, out))
        code, body = self.run_json("book-check", "14:05", REGULAR_NOW, "regular")
        self.assertEqual((20, "unavailable", True, None), (code, body["status"], body["payload"]["quiet"],
                                                           body["payload"]["message"]))
        self.assertEqual([("book-check", 20, "unavailable", "14:05", None)] * 2, self.beats())


class LockDisciplineTest(RoutineCopy):
    def test_the_routine_lock_is_held_around_a_step_and_the_desk_lock_is_free(self) -> None:
        seen: List[Tuple[bool, bool]] = []

        def probe(argv: Optional[Sequence[str]] = None) -> int:
            def free(path: str) -> bool:
                try:
                    with state.locked(path, timeout_s=0.0):
                        return True
                except state.LockBusy:
                    return False
            seen.append((free(self.paths.lock_path), free(tool.lock_path(self.paths, "post-expiry"))))
            print(contract.json_dumps(step_envelope("csv_recon", {"message": None}, "quiet", POST_EXPIRY_NOW)))
            return 0

        self.patch(tool.step_modules()["csv_recon"], "main", probe)
        code, body = self.run_json("post-expiry", "09:30", POST_EXPIRY_NOW, "post_expiry")
        self.assertEqual([(True, False)], seen)
        self.assertEqual((0, True), (code, body["payload"]["quiet"]))
        with state.locked(tool.lock_path(self.paths, "post-expiry"), timeout_s=0.0):
            pass  # released at the end of the run


class NoticeTest(RoutineCopy):
    def test_a_notice_that_fails_its_lint_falls_back_to_the_fixed_line(self) -> None:
        text, _document = alert_fixture("fail_you_should")
        line = "INCIDENT | final-call | " + text.split("\n")[0]
        printed, result, document = tool.notice(line, "final-call", instant(EXPIRY_DAY_NOW))
        self.assertEqual("INCIDENT | final-call | the routine's notice failed the alert lint (forbidden_language); "
                         "routine.py --json shows it\n" + footer(EXPIRY_DAY_NOW), printed)
        self.assertTrue(result.ok)
        self.assertTrue(alert_lint.load_emitted(document).check(printed).ok)

    def test_an_invalid_manifest_is_exit_1_with_a_linted_notice(self) -> None:
        error = routines.ManifestError("routines/manifest.toml", ["schema_version must be 1 (found 2)"])
        self.patch(tool.routines, "load_manifest", mock.Mock(side_effect=error))
        code, out = self.run_text("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        # the manifest could not validate the id, so the notice names no routine
        printed = ("INCIDENT | routine | the routine did not run (manifest_invalid): routines/manifest.toml: "
                   "schema_version must be 1 (found 2)\n" + footer(EXPIRY_DAY_NOW))
        self.assertEqual((1, printed + "\n"), (code, out))
        code, out, _err = run_main(self.argv("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day"))
        body = json.loads(out)
        self.assertEqual((1, "error", "manifest_invalid"), (code, body["status"], body["error"]["code"]))
        self.assertFalse(self.exists("state"))

    def test_a_missing_default_desk_root_in_prose(self) -> None:
        absent = os.path.join(self.base, "absent")
        with mock.patch.object(tool, "DEFAULT_DESK_ROOT", absent):
            code, out, err = run_main(["final-call", "--slot", "15:20", "--allow-dirty", "--now", SATURDAY_NOW])
        self.assertEqual((1, ""), (code, err))
        self.assertTrue(out.startswith("INCIDENT | final-call | the routine did not run (desk_root): "), out)
        self.assertTrue(out.endswith(footer(SATURDAY_NOW) + "\n"), out)

    def test_a_broken_calendar_is_a_warning_and_the_steps_report_it(self) -> None:
        self.append("research/calendar.toml", "\n[[closures]]\ndate = 2026-13-40\n")
        code, body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        payload = body["payload"]
        self.assertEqual(1, code)
        self.assertEqual(1, len(payload["warnings"]))
        self.assertTrue(payload["warnings"][0].startswith("research/calendar.toml cannot be used"), payload["warnings"])
        self.assertEqual(["calendar_invalid"] * 3, [step["error_code"] for step in payload["steps"]])


# --------------------------------------------------------------------------------------------
# Closed days and the early close
# --------------------------------------------------------------------------------------------


class ClosedDayTest(RoutineCopy):
    def closed(self, routine_id: str, slot: str, now: str, early: bool = False) -> None:
        code, body = self.run_json(routine_id, slot, now)
        payload = body["payload"]
        self.assertEqual((0, "quiet", True, None, []), (code, body["status"], payload["quiet"], payload["message"],
                                                        payload["steps"]))
        self.assertEqual((not early, early), (payload["closed_day"], payload["early_close"]))
        self.assertEqual([(routine_id, 0, "quiet", slot, None)], self.beats())
        self.assertEqual("state/heartbeat/%s.json" % routine_id, payload["heartbeat"])
        self.assertEqual([], self.files_under("data"))
        self.assertEqual([], self.alerts_journal())
        day = instant(now).astimezone(datetime.timezone(datetime.timedelta(hours=-5))).date().isoformat()
        self.assertFalse(self.exists("reports/routines/%s/steps" % day))
        record = self.read_json("reports/routines/%s/%s-%s.json" % (day, routine_id, slot.replace(":", "")))
        self.assertEqual(([], 0, None), (record["steps"], record["exit_code"], record["message_sha256"]))
        code, out = self.run_text(routine_id, slot, now)
        self.assertEqual((0, DEV_HEADER + "\nquiet\n"), (code, out), "a development run says so even when quiet")

    def test_thanksgiving_is_a_closed_day(self) -> None:
        self.closed("book-check", "10:05", THANKSGIVING_NOW)

    def test_a_weekday_routine_on_saturday_runs_no_step(self) -> None:
        self.closed("preopen", "07:30", "2026-11-21T12:31:00Z")

    def test_an_owner_injected_closure_is_a_closed_day(self) -> None:
        self.append("research/calendar.toml", CLOSURE_ENTRY % "2026-11-03")
        self.closed("final-call", "15:20", REGULAR_NOW)

    def test_final_call_on_an_early_close_day_runs_no_step(self) -> None:
        self.closed("final-call", "15:20", EARLY_CLOSE_NOW, early=True)

    def test_book_check_still_runs_on_an_early_close_day(self) -> None:
        calls: List[List[str]] = []
        quiet = step_envelope("book_check", {"message": None, "emitted": None}, "quiet", "2026-11-27T15:06:00Z")
        self.patch(tool.step_modules()["book_check"], "main", fake_main(quiet, 0, calls=calls))
        code, body = self.run_json("book-check", "10:05", "2026-11-27T15:06:00Z")
        self.assertEqual((0, False, False, 1), (code, body["payload"]["early_close"], body["payload"]["closed_day"],
                                                len(calls)))
        self.assertEqual("2026-11-27T16:05:00+00:00", calls[0][calls[0].index("--next-run-utc") + 1])


# --------------------------------------------------------------------------------------------
# Slots and the next run
# --------------------------------------------------------------------------------------------


class SlotTest(RoutineCopy):
    def test_the_slot_is_the_latest_passed_never_the_nearest(self) -> None:
        manifest = routines.load_manifest()
        book_check = manifest.by_id("book-check")
        for now, slot in (("2026-11-03T15:36:00Z", "10:05"), (REGULAR_NOW, "14:35"), ("2026-11-03T12:00:00Z", "10:05"),
                          ("2026-11-03T22:00:00Z", "15:35")):
            self.assertEqual(slot, tool.resolve_slot(book_check, instant(now)), now)
        self.assertEqual("07:30", tool.resolve_slot(manifest.by_id("preopen"), instant(PREOPEN_NOW)))

    def test_the_next_run(self) -> None:
        book_check = routines.load_manifest().by_id("book-check")
        closure = {datetime.date(2026, 11, 23): "Owner closure"}
        for day, slot, now, extra, expected in (
                ("2026-11-20", "15:35", EXPIRY_DAY_NOW, {}, "2026-11-23T15:05:00Z"),
                ("2026-11-03", "14:05", REGULAR_NOW, {}, "2026-11-03T20:05:00Z"),
                ("2026-11-03", "14:05", "2026-11-03T19:31:00Z", {}, "2026-11-03T20:05:00Z"),
                ("2026-11-03", "10:05", "2026-11-03T15:06:00Z", {}, "2026-11-03T16:05:00Z"),
                ("2026-11-03", "15:05", "2026-11-03T20:02:00Z", {}, "2026-11-03T20:35:00Z"),
                ("2026-11-25", "15:35", "2026-11-25T20:36:00Z", {}, "2026-11-27T15:05:00Z"),
                ("2026-11-20", "15:35", EXPIRY_DAY_NOW, closure, "2026-11-24T15:05:00Z")):
            with self.subTest(day=day, slot=slot, now=now):
                self.assertEqual(instant(expected), tool.next_run_utc(
                    book_check, datetime.date.fromisoformat(day), slot, instant(now), extra))

    def test_without_slot_the_routine_takes_the_latest_passed_slot(self) -> None:
        code, body = self.run_json("book-check", None, REGULAR_NOW, "regular")
        payload = body["payload"]
        self.assertEqual((0, "14:35", 15), (code, payload["slot"], payload["lateness_min"]))
        argv = payload["steps"][0]["argv"]
        self.assertEqual("14:35", argv[argv.index("--scheduled-et") + 1])


# --------------------------------------------------------------------------------------------
# The run record and the step copies
# --------------------------------------------------------------------------------------------


class RecordsTest(RoutineCopy):
    def test_the_run_record(self) -> None:
        code, body = self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        payload = body["payload"]
        self.assertEqual(10, code)
        self.assertEqual("reports/routines/2026-11-20/final-call-1520.json", payload["run_record"])
        record = self.read_json(payload["run_record"])
        self.assertEqual(sorted(tool.RUN_RECORD_KEYS), sorted(record))
        self.assertEqual({"schema": 1, "routine": "final-call", "slot": "15:20", "run_utc": "2026-11-20T19:50:00+00:00",
                          "development": True, "checkout": {"pinned": False, "reason": "development checkout (no tag)"},
                          "withheld": [], "incidents": [], "message_sha256": sha(payload["message"]), "exit_code": 10},
                         {key: value for key, value in record.items() if key != "steps"})
        self.assertEqual([{key: step[key] for key in tool.STEP_RECORD_KEYS} for step in payload["steps"]],
                         record["steps"])
        self.assertEqual(["reports/routines/2026-11-20/steps/expiry_checklist-final-call.json",
                          "reports/routines/2026-11-20/steps/expiry_checklist-tomorrow.json",
                          "reports/routines/2026-11-20/steps/assignment_watch.json"],
                         [step["artifact"] for step in record["steps"]])
        for step in record["steps"]:
            copy = self.read_json(step["artifact"])
            self.assertEqual((step["tool"], step["status"]), (copy["tool"], copy["status"]))
        self.assertEqual(sha(payload["message"]), payload["message_sha256"])
        for rel in payload["written"]:
            self.assertTrue(self.exists(rel), rel)
        self.assertIn(payload["run_record"], payload["written"])

    def test_the_latest_run_of_a_step_replaces_its_copy(self) -> None:
        self.run_json("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        self.run_json("final-call", "15:20", "2026-11-20T19:55:00Z", "expiry_day")
        self.assertEqual(["reports/routines/2026-11-20/final-call-1520.json",
                          "reports/routines/2026-11-20/steps/assignment_watch.json",
                          "reports/routines/2026-11-20/steps/expiry_checklist-final-call.json",
                          "reports/routines/2026-11-20/steps/expiry_checklist-tomorrow.json"],
                         self.files_under("reports/routines"))
        self.assertEqual("2026-11-20T19:55:00+00:00",
                         self.read_json("reports/routines/2026-11-20/final-call-1520.json")["run_utc"])
        self.assertEqual("2026-11-20T19:55:00+00:00",
                         self.read_json("reports/routines/2026-11-20/steps/assignment_watch.json")["generated_at"])


# --------------------------------------------------------------------------------------------
# The chains that end in eod_summary (preopen, eod-brief)
# --------------------------------------------------------------------------------------------


class SummaryChainsTest(RoutineCopy):
    def summary_part(self, payload: Dict[str, Any], code: int, step: Dict[str, Any]) -> None:
        """The message is eod_summary's text (always), and the exit code follows D2-5 from the steps."""
        summary = self.artifact(step)
        message = summary.get("payload", {}).get("message") if isinstance(summary.get("payload"), dict) else None
        if step["exit_code"] in (1, 2):
            self.assertEqual(1, code)
            self.assertIn("INCIDENT | %s" % payload["routine"], payload["message"])
            return
        self.assertEqual(max([0] + [s["exit_code"] for s in payload["steps"] if s["exit_code"] == 20]), code)
        self.assertIsNotNone(message)
        self.assertEqual(DEV_HEADER + "\n" + message, payload["message"])
        self.assertEqual(["eod_summary"], [part["tool"] for part in payload["parts"]])

    def test_preopen(self) -> None:
        code, body = self.run_json("preopen", "07:30", PREOPEN_NOW, "pre_open")
        payload = body["payload"]
        steps = payload["steps"]
        self.assertEqual(["desk_doctor", "csv_recon", "expiry_checklist", "eod_summary"],
                         [step["tool"] for step in steps])
        self.assertEqual([0, 0, 0], [step["exit_code"] for step in steps[:3]])
        self.assertEqual(["--quick", "--json", "--desk-root", self.root, "--timeout", "10.0", "--now",
                          "2026-11-03T12:31:00+00:00", "--offline"], steps[0]["argv"])
        self.assertNotIn("--fixture-set", steps[3]["argv"])
        for rel in ("state/doctor.json", "data/positions/2026-11-03.recon.json", "reports/expiry/2026-11-03.week.json"):
            self.assertTrue(self.exists(rel), rel)
        self.summary_part(payload, code, steps[3])
        self.assertEqual([("preopen", code, contract.status_for(code) if code else "ok", "07:30",
                           sha(payload["message"]))], self.beats())

    def test_eod_brief_after_eod(self) -> None:
        self.run_json("eod", "16:35", EOD_NOW, "after_close")
        code, body = self.run_json("eod-brief", "17:10", BRIEF_NOW, "after_close")
        payload = body["payload"]
        steps = payload["steps"]
        self.assertEqual([("eod_summary", "--brief")], [(step["tool"], step["argv"][0]) for step in steps])
        self.assertNotIn("--fixture-set", steps[0]["argv"])
        self.assertEqual("reports/routines/2026-11-03/steps/eod_summary-brief.json", steps[0]["artifact"])
        self.summary_part(payload, code, steps[0])
        beats = [beat for beat in self.beats() if beat[0] == "eod-brief"]
        self.assertEqual([("eod-brief", code, contract.status_for(code) if code else "ok", "17:10",
                           sha(payload["message"]))], beats)


class ReviewFixTest(RoutineCopy):
    """The run record per slot, the notice that names no unvalidated id, the crash handler, the approval boundary."""

    def test_each_slot_keeps_its_own_run_record(self) -> None:
        quiet = step_envelope("book_check", {"message": None, "emitted": None}, "quiet", REGULAR_NOW)
        self.patch(tool.step_modules()["book_check"], "main", fake_main(quiet, 0))
        self.patch(tool.step_modules()["trigger_levels"], "main", fake_main(step_envelope(
            "trigger_levels", {"message": None}, "ok", REGULAR_NOW), 0))
        self.run_json("book-check", "10:05", "2026-11-03T15:06:00Z", "regular")
        self.run_json("book-check", "12:05", "2026-11-03T17:06:00Z", "regular")
        self.assertEqual(["reports/routines/2026-11-03/book-check-1005.json",
                          "reports/routines/2026-11-03/book-check-1205.json"],
                         [rel for rel in self.files_under("reports/routines") if "/steps/" not in rel])
        self.assertEqual(["10:05", "12:05"], [self.read_json("reports/routines/2026-11-03/book-check-%s.json" % hhmm)
                                             ["slot"] for hhmm in ("1005", "1205")])

    def test_an_invalid_manifest_never_echoes_the_routine_id(self) -> None:
        error = routines.ManifestError("routines/manifest.toml", ["schema_version must be 1 (found 2)"])
        self.patch(tool.routines, "load_manifest", mock.Mock(side_effect=error))
        # ids that read as trade instructions (spelled in parts: this file carries no order-shaped text)
        for routine_id in ("se" + "ll-everything", "bu" + "y-now", "ro" + "ll-to-dec"):
            code, out = self.run_text(routine_id, None, EXPIRY_DAY_NOW)
            text = out.rstrip("\n")
            self.assertEqual((1, "INCIDENT | routine | the routine did not run (manifest_invalid): routines/manifest.toml: "
                                 "schema_version must be 1 (found 2)\n" + footer(EXPIRY_DAY_NOW)), (code, text))
            self.assertTrue(alert_lint.check(text, ["2", "1"], identifiers=[], clocks=["19:50 UTC"],
                                             as_of=instant(EXPIRY_DAY_NOW)).ok)

    def test_a_fallback_that_fails_its_lint_is_the_fixed_line(self) -> None:
        printed, result, document = tool.notice("INCIDENT | x | you sho" + "uld look", "se" + "ll-everything",
                                                instant(EXPIRY_DAY_NOW))
        self.assertEqual(tool.NOTICE_FIXED_LINE + "\n" + footer(EXPIRY_DAY_NOW), printed)
        self.assertNotIn("se" + "ll", printed)
        self.assertTrue(result.ok)
        self.assertTrue(alert_lint.load_emitted(document).check(printed).ok)

    def test_an_unreadable_history_journal_is_a_warning_not_a_crash(self) -> None:
        os.makedirs(self.path("state/heartbeat/history.jsonl"))
        code, body = self.run_json("book-check", "14:05", REGULAR_NOW, "regular")
        payload = body["payload"]
        self.assertIn(code, (0, 1, 20))
        self.assertTrue(any(warning.startswith("state/heartbeat/history.jsonl cannot be read")
                            for warning in payload["warnings"]), payload["warnings"])

    def test_an_unexpected_error_writes_a_crash_incident_and_a_linted_notice(self) -> None:
        self.patch(tool.Run, "compose", mock.Mock(side_effect=RuntimeError("compose probe")))
        code, out = self.run_text("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day")
        incident = "incidents/%s-final-call-crash.md" % stamp(EXPIRY_DAY_NOW)
        self.assertEqual(1, code)
        self.assertEqual("INCIDENT | final-call | the routine did not run (routine_crashed): an unexpected error stopped "
                         "the run; details in %s\n%s\n" % (incident, footer(EXPIRY_DAY_NOW)), out)
        self.assertIn("RuntimeError: compose probe", self.read_text(incident))
        self.assertNotIn("compose probe", out)
        self.assertEqual([("final-call", 1, "error", "15:20", None)], self.beats())
        code, json_out, _err = run_main(self.argv("final-call", "15:20", "2026-11-20T19:51:00Z", "expiry_day"))
        body = json.loads(json_out)
        self.assertEqual((1, "error", "routine_crashed"), (code, body["status"], body["error"]["code"]))

    def test_every_routine_writes_only_under_its_approval_boundary(self) -> None:
        manifest = routines.load_manifest()
        cases = (("preopen", "07:30", PREOPEN_NOW, "pre_open"),
                 ("book-check", "10:05", "2026-11-03T15:06:00Z", "regular"),
                 ("final-call", "15:20", EXPIRY_DAY_NOW, "expiry_day"), ("eod", "16:35", EOD_NOW, "after_close"),
                 ("eod-brief", "17:10", BRIEF_NOW, "after_close"),
                 ("post-expiry", "09:30", POST_EXPIRY_NOW, "post_expiry"))
        self.assertEqual(set(manifest.routine_ids), {case[0] for case in cases})
        for routine_id, slot, now, fixture_set in cases:
            with self.subTest(routine=routine_id):
                self.tearDown()
                self.setUp()
                before = self.snapshot()
                self.run_json(routine_id, slot, now, fixture_set)
                after = self.snapshot()
                tops = sorted({rel.split("/")[0] for rel in after if after[rel] != before.get(rel)})
                boundary = manifest.by_id(routine_id).approval_boundary
                self.assertTrue(tops, routine_id)
                self.assertEqual([], [top for top in tops if "%s/" % top not in boundary], (routine_id, boundary))

    def snapshot(self) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(folder, name)
                with open(path, "rb") as handle:
                    found[os.path.relpath(path, self.root).replace(os.sep, "/")] = hashlib.sha256(
                        handle.read()).hexdigest()
        return found


if __name__ == "__main__":
    unittest.main()
